"""
Assay (96-well microplate) mode API — Import → Layout → Standard Curve →
Results & Export → Normalise. The first four match modes/assay.py's tabs;
the plate-layout tools (select wells → standards/blank/samples with subject +
timepoint labels), per-sample summaries and normalisation are web-only and
live in web_app/api/assay_layout.py as pure functions.

The pure pieces of modes/assay.py are imported unmodified (parse_plate_csv,
_plate_get, _well_rc, _fit_4pl, _4pl_inv, render_assay_curve). The standard-
curve fit and back-calculation live inline in that module's Streamlit
render() (not importable), so they're reimplemented here as pure functions
with the same rules: the FIRST standards row is the blank, its mean is
subtracted from every well, and the blank is excluded from the fit itself.
_plate_fig is likewise rebuilt here because the original reads the
Streamlit theme.
"""

from __future__ import annotations

import os
import warnings as warnings_mod
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core.constants import _SAMPLE_DATA_DIR, PAL, fmt
from core.numeric import lin_reg
from modes.assay import _4pl_inv, _PLATE_ROWS, _fit_4pl, _plate_get, _well_rc, parse_plate_csv, render_assay_curve
from web_app.api import assay_layout as lay
from web_app.api.common import ExportFmt, ExportStyle, df_records, export_media_type, figure_json, png_response, records_to_df
from web_app.deps import get_session
from web_app.session import SessionData

router = APIRouter(prefix="/api/assay", tags=["assay"])

_STD_COLUMNS = ["Label", "Conc", "S1", "S2", "S3"]
_SAMPLE_COLUMNS = lay.SAMPLE_COLUMNS
FIT_TYPES = ["Linear", "Quadratic", "4-Parameter Logistic (4PL)"]
SAMPLE_PLATE = os.path.join(_SAMPLE_DATA_DIR, "assay_plate.csv")


# -- pure helpers ------------------------------------------------------------------
def empty_plate() -> pd.DataFrame:
    return pd.DataFrame(np.full((8, 12), np.nan),
                        index=pd.Index(_PLATE_ROWS, name="Row"),
                        columns=pd.Index(range(1, 13), name="Col"))


def _norm_well(w) -> str:
    return "" if w is None or (isinstance(w, float) and np.isnan(w)) else str(w).strip().upper()


def std_wells_map(std_df: pd.DataFrame) -> dict:
    """{well: {set, conc, label, is_blank}} — row 0 is the blank."""
    m: dict = {}
    for i, (_, r) in enumerate(std_df.iterrows()):
        for s_idx, s_col in [(1, "S1"), (2, "S2"), (3, "S3")]:
            w = _norm_well(r.get(s_col))
            if w and _well_rc(w):
                conc = pd.to_numeric(r.get("Conc"), errors="coerce")
                m[w] = {"set": s_idx, "conc": None if pd.isna(conc) else float(conc),
                        "label": str(r.get("Label", "")), "is_blank": i == 0}
    return m


def sample_meta(sample_df: pd.DataFrame) -> dict:
    """{well: {Label, Subject, Timepoint}} for valid sample wells."""
    return {r["Well"]: {"Label": r["Label"], "Subject": r["Subject"], "Timepoint": r["Timepoint"]}
            for r in lay.normalize_sample_df(sample_df).to_dict(orient="records") if _well_rc(r["Well"])}


def sample_map(sample_df: pd.DataFrame) -> dict:
    return {w: (m["Label"] or w) for w, m in sample_meta(sample_df).items()}


def layout_problems(std_df: pd.DataFrame, sample_df: pd.DataFrame) -> list[str]:
    """Human-readable warnings about the layout tables (bad/duplicate wells)."""
    problems = []
    seen: dict[str, str] = {}
    for i, (_, r) in enumerate(std_df.iterrows()):
        for s_col in ("S1", "S2", "S3"):
            w = _norm_well(r.get(s_col))
            if not w:
                continue
            label = r.get("Label")
            label = "" if label is None or (isinstance(label, float) and np.isnan(label)) else label
            where = f"standards row {i + 1} ({label}) {s_col}"
            if not _well_rc(w):
                problems.append(f"'{w}' in {where} is not a valid well (A1–H12).")
            elif w in seen:
                problems.append(f"{w} is used twice: {seen[w]} and {where}.")
            else:
                seen[w] = where
    for _, r in sample_df.iterrows():
        w = _norm_well(r.get("Well"))
        if not w:
            continue
        if not _well_rc(w):
            problems.append(f"Sample well '{w}' is not a valid well (A1–H12).")
        elif w in seen:
            problems.append(f"Sample well {w} is already used by {seen[w]}.")
    if len(std_df):
        c0 = pd.to_numeric(std_df["Conc"].iloc[0], errors="coerce") if "Conc" in std_df else np.nan
        if pd.notna(c0) and c0 != 0:
            problems.append(f"The first standards row is used as the blank but has concentration {c0:g}, "
                            "not 0. Mark the blank wells with Mark as blank.")
    return problems


def compute_standard_curve(plate: pd.DataFrame, std_df: pd.DataFrame, fit_type: str) -> tuple[dict, list[str]]:
    """Port of modes/assay.py's Compute-standard-curve block. Raises
    ValueError with a user-facing message when the curve can't be fitted."""
    warnings: list[str] = []
    if len(std_df) == 0 or pd.isna(pd.to_numeric(std_df["Conc"].iloc[0], errors="coerce")):
        raise ValueError("The first row (the Blank) needs a Concentration value on the Standards tab.")
    sdf = std_df.copy()
    sdf["Conc"] = pd.to_numeric(sdf["Conc"], errors="coerce")
    sdf = sdf.dropna(subset=["Conc"]).reset_index(drop=True)
    if len(sdf) < 3:
        raise ValueError("Need the blank plus at least 2 non-blank concentration levels to fit a curve.")

    raw = np.array([[_plate_get(plate, _norm_well(r.get(sc))) for sc in ("S1", "S2", "S3")]
                    for _, r in sdf.iterrows()], dtype=float)
    if np.all(np.isnan(raw[0])):
        raise ValueError("Blank row has no valid signal. Check its well addresses on the Standards tab.")
    blank = float(np.nanmean(raw[0]))

    delta = raw - blank
    with warnings_mod.catch_warnings():
        warnings_mod.simplefilter("ignore", RuntimeWarning)  # all-NaN levels / single replicates
        means = np.nanmean(delta, axis=1)
        sds = np.nanstd(delta, axis=1, ddof=1)
    concs = sdf["Conc"].to_numpy(dtype=float)
    ok = np.isfinite(concs) & np.isfinite(means)
    ok[0] = False  # the blank is the reference, not an independent data point
    missing = [str(sdf["Label"].iloc[i]) for i in range(1, len(sdf)) if not np.isfinite(means[i])]
    if missing:
        warnings.append(f"No signal found for: {', '.join(missing)} — excluded from the fit.")
    if ok.sum() < 2:
        raise ValueError("Fewer than 2 non-blank standards have signal — check the well addresses.")

    fit: dict | None = None
    if fit_type == "Linear":
        lr = lin_reg(concs[ok], means[ok])
        fit = dict(type="linear", **lr) if lr else None
    elif fit_type == "Quadratic":
        if ok.sum() < 3:
            raise ValueError("Quadratic fit needs at least 3 non-blank standards with signal.")
        coefs = np.polyfit(concs[ok], means[ok], 2)
        yp = np.polyval(coefs, concs[ok])
        sst = float(np.sum((means[ok] - means[ok].mean()) ** 2))
        r2 = 1 - float(np.sum((means[ok] - yp) ** 2)) / sst if sst > 0 else 0.0
        fit = dict(type="quad", a=float(coefs[0]), b=float(coefs[1]), c=float(coefs[2]), r2=r2)
    elif fit_type == FIT_TYPES[2]:
        fit = _fit_4pl(concs[ok], means[ok])
        if fit is None:
            warnings.append("4PL did not converge — fell back to Linear.")
            lr = lin_reg(concs[ok], means[ok])
            fit = dict(type="linear", **lr) if lr else None
    else:
        raise ValueError(f"Unknown fit type {fit_type!r}")
    if fit is None:
        raise ValueError("Regression failed — not enough valid data points.")
    fit = {k: (float(v) if isinstance(v, (np.floating, float, int)) and k != "type" else v) for k, v in fit.items()}

    def _clean(a):
        return [[None if not np.isfinite(v) else float(v) for v in row] for row in a] if np.ndim(a) == 2 else \
               [None if not np.isfinite(v) else float(v) for v in a]

    res = dict(
        fit=fit, fit_label=fit_type,
        concs=concs.tolist(), labels=[str(x) for x in sdf["Label"].tolist()],
        means=_clean(means), sds=_clean(sds), raw_arr=_clean(raw), delta_arr=_clean(delta),
        blank_mean=blank, std_df=sdf.to_dict(orient="records"),
    )
    return res, warnings


def _as_float_array(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def _as_float_matrix(rows) -> np.ndarray:
    return np.array([[np.nan if v is None else v for v in row] for row in rows], dtype=float)


def fit_eval(fit: dict, x: np.ndarray) -> np.ndarray:
    if fit["type"] == "linear":
        return fit["slope"] * x + fit["intercept"]
    if fit["type"] == "quad":
        return fit["a"] * x ** 2 + fit["b"] * x + fit["c"]
    with np.errstate(all="ignore"):
        return fit["d"] + (fit["a"] - fit["d"]) / (1 + (x / fit["c"]) ** fit["b"])


def fit_equation(fit: dict) -> str:
    if fit["type"] == "linear":
        b = fit["intercept"]
        return f"y = {fit['slope']:.4g}x {'+' if b >= 0 else '−'} {abs(b):.4g}    R² = {fit['r2']:.4f}"
    if fit["type"] == "quad":
        return f"y = {fit['a']:.4g}x² + {fit['b']:.4g}x + {fit['c']:.4g}    R² = {fit['r2']:.4f}"
    return (f"4PL: a = {fit['a']:.4g}, b = {fit['b']:.4g}, c (EC50) = {fit['c']:.4g}, "
            f"d = {fit['d']:.4g}    R² = {fit['r2']:.4f}")


def back_calc(dy: float, fit: dict) -> float:
    """ΔSignal → concentration. Same branch logic as modes/assay.py's _back_calc."""
    if not np.isfinite(dy):
        return np.nan
    ft = fit["type"]
    if ft == "linear":
        s = fit["slope"]
        return float((dy - fit["intercept"]) / s) if s != 0 else np.nan
    if ft == "quad":
        a, b, c = fit["a"], fit["b"], fit["c"] - dy
        if abs(a) <= 1e-9 * max(abs(b), 1e-12):
            return float(-c / b) if b != 0 else np.nan
        disc = b ** 2 - 4 * a * c
        if disc < 0:
            return np.nan
        roots = [(-b + np.sqrt(disc)) / (2 * a), (-b - np.sqrt(disc)) / (2 * a)]
        pos = [r for r in roots if r >= -1e-9]
        if a < 0 and len(pos) == 2:
            return np.nan  # ambiguous: both roots are physically possible
        return float(min(pos)) if pos else np.nan
    return _4pl_inv(dy, fit)


def sample_results(plate: pd.DataFrame, res: dict, samples: dict, meta: dict | None = None) -> list[dict]:
    """One row per non-standard well with signal: back-calculated conc + range flag."""
    meta = meta or {}
    fit = res["fit"]
    blank = float(res["blank_mean"])
    concs = _as_float_array(res["concs"])
    c_min, c_max = float(np.nanmin(concs)), float(np.nanmax(concs))
    std_wells = {_norm_well(r.get(sc)) for r in res["std_df"] for sc in ("S1", "S2", "S3")} - {""}
    rows = []
    for row_lbl in _PLATE_ROWS:
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            if well in std_wells:
                continue
            sig = _plate_get(plate, well)
            if not np.isfinite(sig):
                continue
            dy = sig - blank
            conc = back_calc(dy, fit)
            if not np.isfinite(conc):
                flag = "undefined"
            elif conc < c_min - 1e-9:
                flag = "< range"
            elif conc > c_max + 1e-9:
                flag = "> range"
            else:
                flag = ""
            m = meta.get(well, {})
            rows.append({"Well": well, "Label": samples.get(well, ""), "Subject": m.get("Subject", ""),
                         "Timepoint": m.get("Timepoint", ""), "Signal": sig, "ΔSignal": dy,
                         "Conc": conc if np.isfinite(conc) else None, "Flag": flag})
    return rows


# -- figures -----------------------------------------------------------------------
_SET_COLS = {1: "rgba(70,130,220,0.85)", 2: "rgba(50,180,110,0.85)", 3: "rgba(220,80,80,0.85)"}
_BLANK_COL = "rgba(255,152,0,0.90)"
_SAMPLE_COL = "rgba(150,95,210,0.75)"
_DATA_COL = "rgba(120,130,145,0.55)"
_EMPTY_COL = "rgba(200,205,212,0.35)"


def plate_figure(plate: pd.DataFrame | None, std_wells: dict, samples: dict, conc_unit: str, sig_unit: str,
                 results: dict | None = None) -> go.Figure:
    """96-well plate map. With `results` ({well: result row}) the sample wells
    show back-calculated concentration instead of raw signal."""
    xs, ys, txts, hovs, cols = [], [], [], [], []
    for ri, row_lbl in enumerate(_PLATE_ROWS):
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            val = _plate_get(plate, well)
            has = np.isfinite(val)
            val_s = f"{val:.4g}" if has else "—"
            text = val_s if has else ""
            hov = f"<b>{well}</b><br>Signal: {val_s} {sig_unit}"
            if well in std_wells:
                info = std_wells[well]
                col = _BLANK_COL if info["is_blank"] else _SET_COLS.get(info["set"], _SET_COLS[1])
                hov += f"<br>Standard: {info['label']} ({info['conc']} {conc_unit})<br>Set {info['set']}"
            elif results is not None and well in results:
                r = results[well]
                col = _SAMPLE_COL
                conc_s = fmt(r["Conc"]) if r["Conc"] is not None else "—"
                text = conc_s
                hov += f"<br>Sample: {r['Label'] or well}<br>Conc: {conc_s} {conc_unit} {r['Flag']}"
            elif well in samples:
                col = _SAMPLE_COL
                hov += f"<br>Sample: {samples[well]}"
            else:
                col = _DATA_COL if has else _EMPTY_COL
            xs.append(ci + 1)
            ys.append(7 - ri)
            txts.append(text)
            hovs.append(hov)
            cols.append(col)

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="markers+text", text=txts, textposition="middle center",
        textfont=dict(size=9, color="#1a1a1a"), hovertext=hovs, hoverinfo="text",
        marker=dict(color=cols, size=38, line=dict(width=1, color="rgba(0,0,0,0.15)")),
        showlegend=False,
    ))
    for name, col in [("Blank", _BLANK_COL), ("Set 1", _SET_COLS[1]), ("Set 2", _SET_COLS[2]),
                      ("Set 3", _SET_COLS[3]), ("Sample", _SAMPLE_COL), ("Unassigned", _DATA_COL)]:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(color=col, size=11)))
    fig.update_layout(
        height=430, template="plotly_white", margin=dict(l=40, r=20, t=20, b=60),
        xaxis=dict(tickmode="array", tickvals=list(range(1, 13)), side="top",
                   showgrid=False, zeroline=False, range=[0.4, 12.6], fixedrange=True),
        yaxis=dict(tickmode="array", tickvals=list(range(8)), ticktext=list(reversed(_PLATE_ROWS)),
                   showgrid=False, zeroline=False, range=[-0.6, 7.6], fixedrange=True),
        legend=dict(orientation="h", x=0, y=-0.06, xanchor="left"),
    )
    return fig


def curve_figure(res: dict, show_reps: bool, conc_unit: str, sig_unit: str) -> go.Figure:
    fit = res["fit"]
    cx = _as_float_array(res["concs"])
    my = _as_float_array(res["means"])
    sy = _as_float_array(res["sds"])
    da = _as_float_matrix(res["delta_arr"])
    labels = np.array(res["labels"], dtype=object)
    vm = np.isfinite(my) & np.isfinite(cx)

    fig = go.Figure()
    if show_reps:
        for si in range(3):
            ry = da[:, si]
            vr = np.isfinite(ry) & np.isfinite(cx)
            if vr.any():
                fig.add_trace(go.Scatter(x=cx[vr], y=ry[vr], name=f"Set {si + 1}", mode="markers",
                                         marker=dict(symbol="circle-open", size=9, color=PAL[si], line=dict(width=1.5))))
    fig.add_trace(go.Scatter(
        x=cx[vm], y=my[vm], name="Mean ± SD", mode="markers", text=labels[vm],
        hovertemplate="%{text}<br>%{x} " + conc_unit + "<br>ΔSignal %{y:.4g}<extra></extra>",
        marker=dict(size=11, color="#4c96d7", line=dict(width=1.5, color="white")),
        error_y=dict(type="data", array=np.nan_to_num(sy[vm]).tolist(), visible=True, color="#4c96d7", thickness=1.5, width=5),
    ))
    xp = np.linspace(max(0.0, float(cx[vm].min())), float(cx[vm].max()), 400)
    fig.add_trace(go.Scatter(x=xp, y=fit_eval(fit, xp), name="Fit", mode="lines",
                             line=dict(color="#ff9230", dash="dash", width=2.5)))
    fig.update_layout(
        xaxis_title=f"Concentration ({conc_unit})", yaxis_title=f"ΔSignal ({sig_unit})",
        height=480, template="plotly_white", hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        annotations=[dict(text=fit_equation(fit), xref="paper", yref="paper", x=0.02, y=0.98,
                          xanchor="left", yanchor="top", showarrow=False, font=dict(size=12),
                          bgcolor="rgba(255,255,255,0.85)", bordercolor="#999", borderwidth=1, borderpad=6)],
    )
    return fig


# -- tables ------------------------------------------------------------------------
def standards_table(res: dict, sig_unit: str, conc_unit: str) -> list[dict]:
    """The Streamlit app's "Intermediate values" table: raw, Δ, mean, SD, CV per level."""
    rows = []
    means, sds = _as_float_array(res["means"]), _as_float_array(res["sds"])
    raw_arr, delta_arr = _as_float_matrix(res["raw_arr"]), _as_float_matrix(res["delta_arr"])
    for k, label in enumerate(res["labels"]):
        n_reps = int(np.isfinite(delta_arr[k]).sum())
        sd = sds[k]
        mean = means[k]
        # The blank's mean ΔSignal is 0 by construction, so a CV there is meaningless.
        cv = abs(sd / mean) * 100 if k > 0 and np.isfinite(sd) and np.isfinite(mean) and mean != 0 else np.nan
        rows.append({
            "Label": label + (" (blank)" if k == 0 else ""),
            f"Conc ({conc_unit})": f"{res['concs'][k]:.5g}",
            **{f"S{s + 1} raw": fmt(raw_arr[k, s]) for s in range(3)},
            **{f"S{s + 1} Δ": fmt(delta_arr[k, s]) for s in range(3)},
            f"Mean Δ ({sig_unit})": fmt(mean),
            f"SD ({sig_unit})": fmt(sd) if np.isfinite(sd) else ("n=1" if n_reps == 1 else "—"),
            "CV (%)": fmt(cv, 3),
            "Back-calc conc": fmt(back_calc(mean, res["fit"])) if k > 0 and np.isfinite(mean) else "—",
        })
    return rows


# -- state ---------------------------------------------------------------------------
def _plate_grid(plate: pd.DataFrame | None) -> list[list[float | None]] | None:
    if plate is None:
        return None
    return [[None if not np.isfinite(v) else float(v) for v in plate.loc[r].to_numpy(dtype=float)] for r in _PLATE_ROWS]


def _results_by_well(session: SessionData) -> dict | None:
    if session.assay_std_res is None or session.assay_plate is None:
        return None
    rows = sample_results(session.assay_plate, session.assay_std_res, sample_map(session.assay_sample_df))
    return {r["Well"]: r for r in rows}


READOUT_UNITS = {"absorbance": "Abs", "fluorescence": "RFU", "luminescence": "RLU"}


def readout_text(r: dict) -> str:
    """'Absorbance, 450 nm' / 'Fluorescence, Ex 485 / Em 528 nm' / 'Luminescence'."""
    kind = (r.get("type") or "").strip()
    name = kind.capitalize() if kind else "Signal"
    if kind == "absorbance" and lay.txt(r.get("wavelength")):
        return f"{name}, {lay.txt(r['wavelength'])} nm"
    if kind == "fluorescence" and (lay.txt(r.get("ex")) or lay.txt(r.get("em"))):
        parts = [f"Ex {lay.txt(r['ex'])}" if lay.txt(r.get("ex")) else "", f"Em {lay.txt(r['em'])}" if lay.txt(r.get("em")) else ""]
        return f"{name}, {' / '.join(p for p in parts if p)} nm"
    return name


def well_layout(plate: pd.DataFrame | None, std_df: pd.DataFrame, sample_df: pd.DataFrame) -> list[dict]:
    """One entry per well (A1…H12) for the Layout tab's clickable plate."""
    stds = std_wells_map(std_df)
    meta = sample_meta(sample_df)
    out = []
    for ri, row_lbl in enumerate(_PLATE_ROWS):
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            v = _plate_get(plate, well)
            cell = {"well": well, "value": float(v) if np.isfinite(v) else None, "role": "data" if np.isfinite(v) else "empty"}
            if well in stds:
                info = stds[well]
                cell.update(role="blank" if info["is_blank"] else "std", set=info["set"], conc=info["conc"], label=info["label"])
            elif well in meta:
                cell.update(role="sample", label=meta[well]["Label"], subject=meta[well]["Subject"],
                            timepoint=meta[well]["Timepoint"])
            out.append(cell)
    return out


def _state(session: SessionData) -> dict:
    stds = std_wells_map(session.assay_std_df)
    samples = sample_map(session.assay_sample_df)
    plate = session.assay_plate
    res = session.assay_std_res
    sdf = lay.normalize_sample_df(session.assay_sample_df)
    return {
        "sig_unit": session.assay_sig_unit,
        "conc_unit": session.assay_conc_unit,
        "readout": session.assay_readout,
        "readout_text": readout_text(session.assay_readout),
        "wells": well_layout(plate, session.assay_std_df, session.assay_sample_df),
        "subjects": lay.ordered_unique(x for x in sdf["Subject"] if x),
        "timepoints": lay.ordered_unique(x for x in sdf["Timepoint"] if x),
        "plate": _plate_grid(plate),
        "n_wells": int(plate.notna().sum().sum()) if plate is not None else 0,
        "std_rows": df_records(session.assay_std_df.reindex(columns=_STD_COLUMNS)),
        "sample_rows": df_records(sdf),
        "layout_problems": layout_problems(session.assay_std_df, session.assay_sample_df),
        "plate_figure": figure_json(plate_figure(plate, stds, samples, session.assay_conc_unit,
                                                 session.assay_sig_unit)) if plate is not None else None,
        "has_result": res is not None,
        "fit_types": FIT_TYPES,
        "fit_label": (res or {}).get("fit_label"),
    }


@router.get("/state")
def get_state(session: SessionData = Depends(get_session)) -> dict:
    return _state(session)


# -- Import --------------------------------------------------------------------------
def _set_plate(session: SessionData, plate: pd.DataFrame) -> None:
    session.assay_plate = plate
    session.assay_std_res = None  # stale fit


@router.post("/plate/upload")
async def upload_plate(file: UploadFile = File(...), session: SessionData = Depends(get_session)) -> dict:
    raw = await file.read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
    try:
        plate = parse_plate_csv(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse {file.filename}: {exc}") from exc
    _set_plate(session, plate)
    return _state(session)


@router.post("/plate/sample")
def load_sample(session: SessionData = Depends(get_session)) -> dict:
    if not os.path.exists(SAMPLE_PLATE):
        raise HTTPException(status_code=404, detail="Sample plate file is missing from sample_data/.")
    with open(SAMPLE_PLATE, encoding="utf-8") as fh:
        _set_plate(session, parse_plate_csv(fh.read()))
    # The sample plate is laid out to match the default standards table.
    from web_app.session import default_assay_norm, default_assay_sample_df, default_assay_std_df
    session.assay_std_df = default_assay_std_df()
    # Demo labelling: subjects P01–P05 (one per row D–H), timepoints D0–D14
    # in duplicate across columns 1–8.
    wells = [f"{r}{c}" for r in "DEFGH" for c in range(1, 9)]
    plan, _ = lay.plan_samples(wells, lay.parse_name_list("P01-P05"), ["D0", "D3", "D7", "D14"], 2, "rows", "subject")
    _, session.assay_sample_df = lay.assign_samples(session.assay_std_df, default_assay_sample_df(), plan, wells)
    session.assay_norm = default_assay_norm()
    return _state(session)


class PlateBody(BaseModel):
    grid: list[list[float | None]]


@router.post("/plate")
def set_plate(body: PlateBody, session: SessionData = Depends(get_session)) -> dict:
    if len(body.grid) != 8 or any(len(r) != 12 for r in body.grid):
        raise HTTPException(status_code=400, detail="Plate grid must be 8 rows × 12 columns.")
    plate = empty_plate()
    plate.iloc[:, :] = np.array([[np.nan if v is None else v for v in r] for r in body.grid], dtype=float)
    _set_plate(session, plate)
    return _state(session)


@router.delete("/plate")
def clear_plate(session: SessionData = Depends(get_session)) -> dict:
    session.assay_plate = None
    session.assay_std_res = None
    return _state(session)


class UnitsBody(BaseModel):
    sig_unit: str | None = None
    conc_unit: str | None = None


@router.post("/units")
def set_units(body: UnitsBody, session: SessionData = Depends(get_session)) -> dict:
    if body.sig_unit is not None:
        session.assay_sig_unit = body.sig_unit
    if body.conc_unit is not None:
        session.assay_conc_unit = body.conc_unit
    return _state(session)


class ReadoutBody(BaseModel):
    type: Literal["absorbance", "fluorescence", "luminescence", "other"] = "absorbance"
    wavelength: str = ""
    ex: str = ""
    em: str = ""


@router.post("/readout")
def set_readout(body: ReadoutBody, session: SessionData = Depends(get_session)) -> dict:
    """Changing the readout kind also switches the signal unit to its usual
    one (Abs/RFU/RLU) — unless the user had typed a custom unit."""
    old_unit = READOUT_UNITS.get(session.assay_readout.get("type"))
    if body.type != session.assay_readout.get("type") and body.type in READOUT_UNITS \
            and session.assay_sig_unit in (old_unit, *READOUT_UNITS.values()):
        session.assay_sig_unit = READOUT_UNITS[body.type]
    session.assay_readout = body.model_dump()
    return _state(session)


# -- Standards / layout ------------------------------------------------------------------
class LayoutBody(BaseModel):
    std_rows: list[dict]
    sample_rows: list[dict]


@router.post("/layout")
def set_layout(body: LayoutBody, session: SessionData = Depends(get_session)) -> dict:
    std = records_to_df(body.std_rows, _STD_COLUMNS, text_cols=("Label", "S1", "S2", "S3"))
    for c in ("S1", "S2", "S3"):
        std[c] = std[c].map(_norm_well)
    samples = records_to_df(body.sample_rows, _SAMPLE_COLUMNS, text_cols=tuple(_SAMPLE_COLUMNS))
    samples = lay.normalize_sample_df(samples)
    session.assay_std_df = std
    session.assay_sample_df = samples
    session.assay_std_res = None
    return _state(session)


class AssignStandardsBody(BaseModel):
    wells: list[str]
    direction: Literal["across", "down"] = "across"
    mode: Literal["serial", "list"] = "serial"
    top: float | None = None
    factor: float | None = None
    include_blank: bool = True
    lowest_first: bool = False
    concs_text: str = ""
    preview: bool = False


class AssignSamplesBody(BaseModel):
    wells: list[str]
    subjects: str = ""
    timepoints: str = ""
    replicates: int = Field(1, ge=1, le=96)
    order: Literal["rows", "columns"] = "rows"
    nesting: Literal["subject", "timepoint"] = "subject"
    preview: bool = False


class WellsBody(BaseModel):
    wells: list[str]


class PasteLayoutBody(BaseModel):
    text: str
    sep: str = "auto"


def _set_layout(session: SessionData, std: pd.DataFrame, samples: pd.DataFrame, message: str) -> dict:
    session.assay_std_df = std
    session.assay_sample_df = samples
    session.assay_std_res = None
    return {**_state(session), "message": message}


def _bad_request(exc: ValueError):
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _standards_concs(body: AssignStandardsBody, n_levels: int) -> list[float]:
    if body.mode == "list":
        return lay.parse_conc_list(body.concs_text)
    return lay.serial_concs(n_levels, float(body.top if body.top is not None else np.nan),
                            float(body.factor if body.factor is not None else np.nan),
                            body.include_blank, body.lowest_first)


@router.post("/assign/standards")
def assign_standards(body: AssignStandardsBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        wells = lay.clean_wells(body.wells)
        levels = lay.group_levels(wells, body.direction)
        concs = _standards_concs(body, len(levels))
        if body.preview:
            if len(concs) != len(levels):
                raise ValueError(f"{len(levels)} level(s) selected but {len(concs)} concentration(s) given.")
            prev = [{"well": w, "role": "blank" if c == 0 else "std", "text": "Blank" if c == 0 else lay.fmt_conc(c),
                     "sub": f"S{i + 1}"} for c, lv in zip(concs, levels) for i, w in enumerate(lv)]
            reps = max(len(lv) for lv in levels)
            return {"preview": prev, "message": f"{len(levels)} level(s) × {reps} replicate(s): "
                    + ", ".join(lay.fmt_conc(c) for c in concs) + f" {session.assay_conc_unit}"
                    + ("" if reps <= 3 else " — at most 3 replicates per level are supported.")}
        std, samples, msg = lay.assign_standards(session.assay_std_df, session.assay_sample_df, wells,
                                                 body.direction, concs)
    except ValueError as exc:
        if body.preview:
            return {"preview": [], "message": str(exc), "error": True}
        _bad_request(exc)
    return _set_layout(session, std, samples, msg)


@router.post("/assign/blank")
def assign_blank(body: WellsBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        std, samples, msg = lay.assign_blank(session.assay_std_df, session.assay_sample_df, body.wells)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, msg)


@router.post("/assign/samples")
def assign_samples(body: AssignSamplesBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        plan, msg = lay.plan_samples(body.wells, lay.parse_name_list(body.subjects), lay.parse_name_list(body.timepoints),
                                     body.replicates, body.order, body.nesting)
        if body.preview:
            return {"preview": [{"well": p["Well"], "role": "sample", "text": p["Subject"], "sub": p["Timepoint"],
                                 "subject": p["Subject"]} for p in plan], "message": msg}
        std, samples = lay.assign_samples(session.assay_std_df, session.assay_sample_df, plan, body.wells)
    except ValueError as exc:
        if body.preview:
            return {"preview": [], "message": str(exc), "error": True}
        _bad_request(exc)
    return _set_layout(session, std, samples, f"Labelled {len(plan)} well(s). " + msg)


@router.post("/assign/clear")
def clear_assignment(body: WellsBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        std, samples = lay.clear_wells(session.assay_std_df, session.assay_sample_df, body.wells)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, f"Cleared {len(lay.clean_wells(body.wells))} well(s).")


@router.post("/layout/paste")
def paste_layout(body: PasteLayoutBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        cells = lay.parse_layout_grid(body.text)
        std, samples, msg = lay.apply_layout_grid(session.assay_std_df, cells, body.sep)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, msg)


# -- Standard curve ----------------------------------------------------------------------
class ComputeBody(BaseModel):
    fit_type: str = "Linear"
    show_reps: bool = True


def _curve_payload(session: SessionData, show_reps: bool, warnings: list[str] | None = None) -> dict:
    res = session.assay_std_res
    return {
        "warnings": warnings or [],
        "figure": figure_json(curve_figure(res, show_reps, session.assay_conc_unit, session.assay_sig_unit)),
        "equation": fit_equation(res["fit"]),
        "fit_label": res.get("fit_label"),
        "blank_mean": res["blank_mean"],
        "standards": standards_table(res, session.assay_sig_unit, session.assay_conc_unit),
    }


@router.post("/compute")
def compute(body: ComputeBody, session: SessionData = Depends(get_session)) -> dict:
    if session.assay_plate is None:
        raise HTTPException(status_code=400, detail="Import plate data first.")
    try:
        res, warnings = compute_standard_curve(session.assay_plate, session.assay_std_df, body.fit_type)
    except ValueError as exc:
        session.assay_std_res = None
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.assay_std_res = res
    return _curve_payload(session, body.show_reps, warnings)


@router.get("/curve")
def get_curve(show_reps: bool = True, session: SessionData = Depends(get_session)) -> dict:
    """Re-render the stored curve (tab revisit, units change, imported session)."""
    if session.assay_std_res is None:
        return {"figure": None}
    return _curve_payload(session, show_reps)


# -- Results -------------------------------------------------------------------------------
def _require_result(session: SessionData) -> dict:
    if session.assay_std_res is None or session.assay_plate is None:
        raise HTTPException(status_code=400, detail="Compute the standard curve first.")
    return session.assay_std_res


def _well_rows(session: SessionData) -> list[dict]:
    res = _require_result(session)
    return sample_results(session.assay_plate, res, sample_map(session.assay_sample_df),
                          sample_meta(session.assay_sample_df))


def summary_figure(groups: list[dict], y: str, sd: str, y_title: str) -> go.Figure:
    """Mean ± SD per sample. With timepoints: one line per subject across
    timepoints (in layout order); otherwise one bar per sample."""
    fig = go.Figure()
    tps = lay.ordered_unique(g["Timepoint"] for g in groups if g["Timepoint"])
    if tps:
        for i, subj in enumerate(lay.ordered_unique(g["Subject"] for g in groups)):
            gs = sorted((g for g in groups if g["Subject"] == subj and g["Timepoint"]),
                        key=lambda g: tps.index(g["Timepoint"]))
            if not gs:
                continue
            fig.add_trace(go.Scatter(
                x=[g["Timepoint"] for g in gs], y=[_nan_none(g[y]) for g in gs], name=subj or "(no subject)",
                mode="lines+markers", line=dict(color=PAL[i % len(PAL)], width=2), marker=dict(size=8),
                error_y=dict(type="data", array=[_nan_none(g[sd]) or 0 for g in gs], visible=True, thickness=1.2, width=4),
            ))
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=tps, title="Timepoint")
    else:
        fig.add_trace(go.Bar(x=[g["Subject"] for g in groups], y=[_nan_none(g[y]) for g in groups],
                             marker_color="#4c96d7",
                             error_y=dict(type="data", array=[_nan_none(g[sd]) or 0 for g in groups], visible=True)))
        fig.update_xaxes(type="category", title="Sample")
    fig.update_layout(yaxis_title=y_title, height=420, template="plotly_white", hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40))
    return fig


def _nan_none(v):
    return None if v is None or not np.isfinite(v) else float(v)


def _summary_rows_display(groups: list[dict], cu: str) -> list[dict]:
    return [{"Subject": g["Subject"], "Timepoint": g["Timepoint"], "n": g["n"],
             f"Mean ({cu})": fmt(g["Mean"]), f"SD ({cu})": fmt(g["SD"]), "CV (%)": fmt(g["CV"], 3),
             "Wells": g["Wells"], "Flag": g["Flag"]} for g in groups]


@router.get("/results")
def results(session: SessionData = Depends(get_session)) -> dict:
    rows = _well_rows(session)
    samples = sample_map(session.assay_sample_df)
    by_well = {r["Well"]: r for r in rows}
    fig = plate_figure(session.assay_plate, std_wells_map(session.assay_std_df), samples,
                       session.assay_conc_unit, session.assay_sig_unit, results=by_well)
    n_flagged = sum(1 for r in rows if r["Flag"])
    groups = lay.group_results([r for r in rows if r["Well"] in samples])
    cu = session.assay_conc_unit
    return {
        "rows": [{**r, "Signal": fmt(r["Signal"]), "ΔSignal": fmt(r["ΔSignal"]),
                  "Conc": fmt(r["Conc"]) if r["Conc"] is not None else "—"} for r in rows],
        "summary": f"{len(rows)} sample well(s) ({len(groups)} labelled sample(s)); "
                   f"{n_flagged} well(s) outside the standard range or undefined.",
        "groups": _summary_rows_display(groups, cu),
        "group_figure": figure_json(summary_figure(groups, "Mean", "SD", f"Concentration ({cu})")) if groups else None,
        "figure": figure_json(fig),
        "sig_unit": session.assay_sig_unit, "conc_unit": cu,
    }


# -- Normalise -----------------------------------------------------------------------------
class NormaliseBody(BaseModel):
    area_unit: str = "cm²"
    vol_unit: str = "µL"
    rows: list[dict] = []


def _norm_inputs(session: SessionData, groups: list[dict]) -> list[dict]:
    """The stored per-sample inputs, one row per current sample (new samples
    get blank inputs; inputs for samples no longer on the plate are kept in
    the session but not shown)."""
    stored = {(lay.txt(r.get("Subject")), lay.txt(r.get("Timepoint"))): r for r in session.assay_norm.get("rows", [])}
    out = []
    for g in groups:
        r = stored.get((g["Subject"], g["Timepoint"]), {})
        out.append({"Subject": g["Subject"], "Timepoint": g["Timepoint"],
                    **{c: _nan_none(lay._num(r.get(c))) for c in ("Dilution", "Volume", "Area")}})
    return out


def _norm_payload(session: SessionData) -> dict:
    rows = _well_rows(session)
    samples = sample_map(session.assay_sample_df)
    groups = lay.group_results([r for r in rows if r["Well"] in samples])
    norm = session.assay_norm
    inputs = _norm_inputs(session, groups)
    out, units = lay.normalise(groups, inputs, session.assay_conc_unit, norm["vol_unit"], norm["area_unit"])
    cu = session.assay_conc_unit
    table = []
    for r, g in zip(out, groups):
        row = {"Subject": r["Subject"], "Timepoint": r["Timepoint"], "n": r["n"],
               f"Well conc ({cu})": fmt(g["Mean"]), "Dilution": fmt(r["Dilution"]),
               f"Sample conc ({cu})": fmt(r["SampleConc"]), f"SD ({cu})": fmt(r["SampleSD"])}
        if np.isfinite(r["Amount"]) or units["needs_volume"] or any(np.isfinite(x["Amount"]) for x in out):
            row[f"Amount ({units['amount']})"] = fmt(r["Amount"])
        row[f"Per area ({units['per_area']})"] = fmt(r["PerArea"])
        row[f"SD per area ({units['per_area']})"] = fmt(r["PerAreaSD"])
        row["Flag"] = r["Flag"]
        table.append(row)
    has_area = any(np.isfinite(r["PerArea"]) for r in out)
    fig = summary_figure(out, "PerArea", "PerAreaSD", f"Per area ({units['per_area']})") if has_area else None
    notes = []
    if units["needs_volume"]:
        notes.append("Some samples have a volume and others don't: per-area values are only given where the "
                     "volume is filled in, so all are in the same units.")
    if not has_area and groups:
        notes.append("Enter an area for each sample to get per-area values.")
    return {"area_unit": norm["area_unit"], "vol_unit": norm["vol_unit"], "conc_unit": cu, "units": units,
            "inputs": inputs, "table": table, "figure": figure_json(fig) if fig else None, "notes": notes,
            "n_samples": len(groups)}


@router.get("/normalise")
def get_normalise(session: SessionData = Depends(get_session)) -> dict:
    return _norm_payload(session)


@router.post("/normalise")
def set_normalise(body: NormaliseBody, session: SessionData = Depends(get_session)) -> dict:
    df = records_to_df(body.rows, lay.NORM_COLUMNS, text_cols=("Subject", "Timepoint"))
    bad = [f"{r.Subject} {r.Timepoint}".strip() for r in df.itertuples() if np.isfinite(r.Area) and r.Area <= 0]
    if bad:
        raise HTTPException(status_code=400, detail=f"Area must be positive (check {', '.join(bad)}).")
    # Merge into the stored rows so inputs for samples not currently shown survive.
    stored = {(lay.txt(r.get("Subject")), lay.txt(r.get("Timepoint"))): r for r in session.assay_norm.get("rows", [])}
    for rec in df_records(df):
        stored[(rec["Subject"], rec["Timepoint"])] = rec
    session.assay_norm = {"area_unit": body.area_unit.strip() or "cm²", "vol_unit": body.vol_unit.strip() or "µL",
                          "rows": list(stored.values())}
    return _norm_payload(session)


# -- Export --------------------------------------------------------------------------------
def _csv_response(df: pd.DataFrame, filename: str, session: SessionData, extra: str = "") -> object:
    text = df.to_csv(index=False) + f"\n# Readout: {readout_text(session.assay_readout)}\n" + extra
    return png_response(text.encode("utf-8"), filename, "text/csv")


@router.get("/export/results-csv")
def export_results_csv(session: SessionData = Depends(get_session)):
    rows = _well_rows(session)
    if not rows:
        raise HTTPException(status_code=400, detail="No sample wells found (every well with data is a standard).")
    su, cu = session.assay_sig_unit, session.assay_conc_unit
    df = pd.DataFrame([{"Well": r["Well"], "Label": r["Label"], "Subject": r["Subject"], "Timepoint": r["Timepoint"],
                        f"Signal ({su})": r["Signal"], f"ΔSignal ({su})": r["ΔSignal"], f"Conc ({cu})": r["Conc"],
                        "Flag": r["Flag"]} for r in rows])
    return _csv_response(df, "assay_results.csv", session)


def _groups(session: SessionData) -> list[dict]:
    samples = sample_map(session.assay_sample_df)
    groups = lay.group_results([r for r in _well_rows(session) if r["Well"] in samples])
    if not groups:
        raise HTTPException(status_code=400, detail="No labelled samples — label sample wells on ② Layout first.")
    return groups


@router.get("/export/summary-csv")
def export_summary_csv(wide: bool = False, session: SessionData = Depends(get_session)):
    """Per-sample mean/SD/CV. wide=true: subjects × timepoints of the mean
    (the shape GraphPad Prism / Excel charts want)."""
    groups = _groups(session)
    cu = session.assay_conc_unit
    if wide:
        tps = lay.ordered_unique(g["Timepoint"] for g in groups)
        subjects = lay.ordered_unique(g["Subject"] for g in groups)
        means = {(g["Subject"], g["Timepoint"]): g["Mean"] for g in groups}
        df = pd.DataFrame([{"Subject": s, **{(t or "Mean"): means.get((s, t), np.nan) for t in tps}} for s in subjects])
        return _csv_response(df, "assay_summary_wide.csv", session, f"# Values: mean concentration ({cu})\n")
    df = pd.DataFrame([{"Subject": g["Subject"], "Timepoint": g["Timepoint"], "n": g["n"], f"Mean ({cu})": g["Mean"],
                        f"SD ({cu})": g["SD"], "CV (%)": g["CV"], "Wells": g["Wells"], "Flag": g["Flag"]} for g in groups])
    return _csv_response(df, "assay_summary.csv", session)


@router.get("/export/normalised-csv")
def export_normalised_csv(session: SessionData = Depends(get_session)):
    _groups(session)
    p = _norm_payload(session)
    out, units = lay.normalise(lay.group_results([r for r in _well_rows(session) if r["Well"] in sample_map(session.assay_sample_df)]),
                               p["inputs"], p["conc_unit"], p["vol_unit"], p["area_unit"])
    cu, au, pu = p["conc_unit"], units["amount"], units["per_area"]
    df = pd.DataFrame([{"Subject": r["Subject"], "Timepoint": r["Timepoint"], "n": r["n"], "Dilution": r["Dilution"],
                        f"Volume ({p['vol_unit']})": r["Volume"], f"Area ({p['area_unit']})": r["Area"],
                        f"Sample conc ({cu})": r["SampleConc"], f"SD ({cu})": r["SampleSD"],
                        f"Amount ({au})": r["Amount"], f"Amount SD ({au})": r["AmountSD"],
                        f"Per area ({pu})": r["PerArea"], f"Per area SD ({pu})": r["PerAreaSD"], "Flag": r["Flag"]}
                       for r in out])
    return _csv_response(df, "assay_normalised.csv", session)


@router.get("/export/standards-csv")
def export_standards_csv(session: SessionData = Depends(get_session)):
    res = _require_result(session)
    su, cu = session.assay_sig_unit, session.assay_conc_unit
    rows = [{
        "Label": res["labels"][i], f"Conc ({cu})": res["concs"][i],
        f"Set 1 ({su})": res["raw_arr"][i][0], f"Set 2 ({su})": res["raw_arr"][i][1], f"Set 3 ({su})": res["raw_arr"][i][2],
        f"Mean Δ ({su})": res["means"][i], f"SD ({su})": res["sds"][i],
    } for i in range(len(res["concs"]))]
    return _csv_response(pd.DataFrame(rows), "standard_curve_data.csv", session,
                         f"# Fit: {fit_equation(res['fit'])}\n# Blank mean: {res['blank_mean']:.6g} {su}\n")


class ExportCurveBody(BaseModel):
    fmt: ExportFmt = "png"
    dpi: int = Field(300, ge=50, le=1200)
    style: ExportStyle = "default"
    figsize: tuple[float, float] | None = None
    show_reps: bool = True


@router.post("/export/curve")
def export_curve(body: ExportCurveBody, session: SessionData = Depends(get_session)):
    res = _require_result(session)
    media_type = export_media_type(body.fmt)
    # render_assay_curve expects plain float arrays (NaN, not None).
    mpl_res = {**res, "concs": _as_float_array(res["concs"]).tolist(),
               "means": _as_float_array(res["means"]).tolist(), "sds": _as_float_array(res["sds"]).tolist(),
               "delta_arr": _as_float_matrix(res["delta_arr"]).tolist()}
    content = render_assay_curve(mpl_res, body.show_reps, session.assay_conc_unit, session.assay_sig_unit,
                                 dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style)
    return png_response(content, f"standard_curve.{body.fmt}", media_type)
