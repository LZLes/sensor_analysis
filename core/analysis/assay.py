"""Assay (96-well microplate) maths, shared by modes/assay.py (Streamlit) and
web_app/api/assay.py:

  * plate parsing, the 4PL fit and inverse, and the standard-curve export
    figure (originally modes/assay.py's own helpers);
  * the standard-curve fit and back-calculation. modes/assay.py runs these
    inline in its Streamlit render(); the versions here follow the same rules:
    the FIRST standards row is the blank, its mean is subtracted from every
    well, and the blank is excluded from the fit itself;
  * per-well layout/result tables used by the web app.

Plate-layout editing, grouping and normalisation live in assay_layout.py.
"""

from __future__ import annotations

import io
import os
import re
import warnings as warnings_mod

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from core.analysis import assay_layout as lay
from core.constants import _SAMPLE_DATA_DIR, PAL, fmt
from core.numeric import lin_reg
from core.plotting import _MINIMAL_RC, _ORIGIN_RC, _apply_spine_style

_PLATE_ROWS = list("ABCDEFGH")


def _well_rc(well: str) -> tuple[int, int] | None:
    """'A1' → (0, 0), 'H12' → (7, 11). None if invalid."""
    w = well.strip().upper()
    if not w or w[0] not in _PLATE_ROWS:
        return None
    try:
        c = int(w[1:]) - 1
    except ValueError:
        return None
    return (_PLATE_ROWS.index(w[0]), c) if 0 <= c < 12 else None


def _plate_get(plate_df: pd.DataFrame | None, well: str) -> float:
    rc = _well_rc(well)
    if rc is None or plate_df is None:
        return np.nan
    try:
        return float(plate_df.iat[rc[0], rc[1]])  # type: ignore[arg-type]
    except Exception:
        return np.nan


def _is_plate_num(s: str) -> bool:
    try:
        float(s.strip().replace(",", "."))
        return True
    except ValueError:
        return False


def parse_plate_csv(raw: str) -> pd.DataFrame:
    """
    Parse a microplate reader export into an 8×12 DataFrame (index A–H, cols 1–12).
    Handles TECAN/Synergy/generic grid formats (tab, comma, semicolon delimited).
    Only 96-well plates are supported — raises rather than silently returning
    a truncated subset if the file looks like a larger (e.g. 384-well) plate.
    """
    import re as _re
    row_re      = _re.compile(r'^\s*([A-Ha-h])(?:[,;\t]|\s)')
    oversize_re = _re.compile(r'^\s*([I-Pi-p])(?:[,;\t]|\s)')
    grid: dict[str, list[float]] = {}
    oversize_rows: set[str] = set()
    for line in raw.splitlines():
        m = row_re.match(line)
        if not m:
            om = oversize_re.match(line)
            if om:
                parts_o = _re.split(r'[,;\t]+', line.strip())
                nums_o  = sum(1 for p in parts_o[1:] if _is_plate_num(p))
                if nums_o >= 3:   # looks like a real data row, not a stray label
                    oversize_rows.add(om.group(1).upper())
            continue
        letter = m.group(1).upper()
        # Split on ONE delimiter (not a run of them) so an empty well keeps
        # its column position — "A,0.1,,0.3" is A1=0.1, A2=empty, A3=0.3,
        # not A2=0.3. Semicolon before comma: semicolon-delimited exports
        # use comma decimals.
        stripped = line.strip()
        delim = next((d for d in ("\t", ";", ",") if d in stripped), None)
        parts = stripped.split(delim) if delim else stripped.split()
        cells = parts[1:]
        while cells and not _is_plate_num(cells[-1]):   # trailing delimiters / row labels
            cells.pop()
        nums: list[float] = []
        for p in cells:
            try:
                nums.append(float(p.strip().replace(",", ".")))
            except ValueError:
                nums.append(np.nan)   # blank / "OVER" / text: keep the slot
        if any(np.isfinite(nums)):
            if len(nums) > 12:
                raise ValueError(
                    f"Row {letter} has {len(nums)} numeric columns — this parser "
                    "only supports 96-well plates (columns 1-12). 384-well "
                    "plates aren't supported yet."
                )
            grid[letter] = nums
    if oversize_rows:
        raise ValueError(
            f"Found row(s) beyond H ({', '.join(sorted(oversize_rows))}) — this "
            "parser only supports 96-well plates (rows A-H). 384-well plates "
            "aren't supported yet."
        )
    if not grid:
        raise ValueError(
            "No plate rows found — expected rows labeled A–H. "
            "Check the file has a standard grid layout."
        )
    data = {}
    for r in _PLATE_ROWS:
        row_vals = (grid.get(r, []) + [np.nan] * 12)[:12]
        data[r] = row_vals
    df = pd.DataFrame(data, index=range(1, 13)).T
    df.index   = pd.Index(_PLATE_ROWS, name="Row")
    df.columns = pd.Index(range(1, 13), name="Col")
    return df


def _fit_4pl(x: np.ndarray, y: np.ndarray) -> dict | None:
    """4-parameter logistic: y = d + (a − d) / (1 + (x/c)^b)."""
    from scipy.optimize import curve_fit as _cf

    def _model(xv, a, b, c, d):
        return d + (a - d) / (1.0 + (np.asarray(xv) / c) ** b)

    xpos = x[x > 0]
    c0 = float(np.median(xpos)) if xpos.size else 1.0
    try:
        popt, _ = _cf(_model, x, y,
                       p0=[float(y.min()), 1.0, c0, float(y.max())],
                       maxfev=10000,
                       bounds=([-np.inf, 0.01, 1e-12, -np.inf],
                               [ np.inf, 10.0,  np.inf,  np.inf]))
        yp = _model(x, *popt)
        ss_res = float(np.sum((y - yp) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return dict(type="4pl", a=popt[0], b=popt[1], c=popt[2], d=popt[3], r2=r2)
    except Exception:
        return None


def _4pl_inv(y_val: float, p: dict) -> float:
    a, b, c, d = p["a"], p["b"], p["c"], p["d"]
    try:
        ratio = (a - d) / (float(y_val) - d)
        return float(c * (ratio - 1.0) ** (1.0 / b)) if ratio > 0 else np.nan
    except Exception:
        return np.nan


def render_assay_curve(res: dict, show_reps: bool, conc_unit: str, sig_unit: str,
                       dpi: int = 150, fmt: str = "png",
                       figsize: tuple | None = None, style: str = "default") -> bytes:
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11
    fit  = res["fit"]
    cx   = np.array(res["concs"], float)
    my   = np.array(res["means"], float)
    sy   = np.array(res["sds"],   float)
    darr = np.array(res["delta_arr"], float)
    vm   = np.isfinite(my) & np.isfinite(cx)
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (7, 5))
        if show_reps:
            for si in range(darr.shape[1] if darr.ndim == 2 else 0):
                rc = PAL[si % len(PAL)]
                ry = darr[:, si]
                vr = np.isfinite(ry) & np.isfinite(cx)
                if vr.any():
                    ax.scatter(cx[vr], ry[vr], color=rc, s=22, alpha=0.6,
                               marker="o", facecolors="none", linewidths=1.2,
                               zorder=3, label=f"Set {si + 1}")
        ax.errorbar(cx[vm], my[vm], yerr=sy[vm], fmt="o", color="#4c96d7",
                    capsize=4, markersize=7, linewidth=1.4, elinewidth=1.2,
                    zorder=4, label="Mean")
        xp = np.linspace(max(0.0, cx[vm].min()), cx[vm].max(), 400)
        if fit["type"] == "linear":
            yp   = fit["slope"] * xp + fit["intercept"]
            b    = fit["intercept"]
            _eq  = (f"y = {fit['slope']:.3g}x {'+ ' if b >= 0 else '− '}{abs(b):.3g}"
                    f"\nR² = {fit['r2']:.4f}")
        elif fit["type"] == "quad":
            yp  = fit["a"]*xp**2 + fit["b"]*xp + fit["c"]
            _eq = (f"y = {fit['a']:.3g}x² + {fit['b']:.3g}x + {fit['c']:.3g}"
                   f"\nR² = {fit['r2']:.4f}")
        else:
            yp  = fit["d"] + (fit["a"] - fit["d"]) / (1 + (xp / fit["c"]) ** fit["b"])
            _eq = (f"4PL  a={fit['a']:.3g}  b={fit['b']:.3g}\n"
                   f"c={fit['c']:.3g}  d={fit['d']:.3g}  R²={fit['r2']:.4f}")
        ax.plot(xp, yp, "--", color="#ff9230", linewidth=2, label="Fit")
        ax.set_xlabel(f"Concentration ({conc_unit})", fontsize=_lfs)
        ax.set_ylabel(f"ΔSignal ({sig_unit})", fontsize=_lfs)
        ax.legend(fontsize=7, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        ax.text(0.5, -0.22, _eq, transform=ax.transAxes, fontsize=7,
                va="top", ha="center", family="monospace",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          alpha=0.88, edgecolor="#cccccc", linewidth=0.8))
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# -- Standard curve, back-calculation and per-well tables -----------------------------
FIT_TYPES = ["Linear", "Quadratic", "4-Parameter Logistic (4PL)"]
SAMPLE_PLATE = os.path.join(_SAMPLE_DATA_DIR, "assay_plate.csv")
READOUT_UNITS = {"absorbance": "Abs", "fluorescence": "RFU", "luminescence": "RLU"}
# Back-calculated standards outside this recovery window are flagged.
RECOVERY_OK = (80.0, 120.0)


def empty_plate() -> pd.DataFrame:
    return pd.DataFrame(np.full((8, 12), np.nan),
                        index=pd.Index(_PLATE_ROWS, name="Row"),
                        columns=pd.Index(range(1, 13), name="Col"))


def _norm_well(w) -> str:
    return "" if w is None or (isinstance(w, float) and np.isnan(w)) else str(w).strip().upper()


def effective_plate(plate: pd.DataFrame | None, excluded) -> pd.DataFrame | None:
    """The plate with excluded wells blanked (NaN), so every downstream
    calculation simply sees no signal there."""
    if plate is None or not excluded:
        return plate
    out = plate.copy()
    for w in excluded:
        rc = _well_rc(str(w))
        if rc:
            out.iat[rc[0], rc[1]] = np.nan
    return out


def std_wells_map(std_df: pd.DataFrame) -> dict:
    """{well: {set, conc, label, is_blank}} — row 0 is the blank."""
    m: dict = {}
    cols = lay.rep_cols(std_df)
    for i, (_, r) in enumerate(std_df.iterrows()):
        for s_idx, s_col in enumerate(cols, start=1):
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
        for s_col in lay.rep_cols(std_df):
            w = _norm_well(r.get(s_col))
            if not w:
                continue
            label = lay.txt(r.get("Label"))
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


def blank_mean(plate: pd.DataFrame | None, std_df: pd.DataFrame) -> float:
    """Mean signal of the blank wells (row 0), NaN if there are none with data."""
    if plate is None or not len(std_df):
        return np.nan
    vals = np.array([_plate_get(plate, _norm_well(std_df.iloc[0].get(c))) for c in lay.rep_cols(std_df)], dtype=float)
    return float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan


def compute_standard_curve(plate: pd.DataFrame, std_df: pd.DataFrame, fit_type: str) -> tuple[dict, list[str]]:
    """Port of modes/assay.py's Compute-standard-curve block, for any number
    of replicate columns. Pass effective_plate(...) to leave wells out.
    Raises ValueError with a user-facing message when the curve can't be
    fitted. The result carries curve QC (see curve_qc) under "qc"."""
    warnings: list[str] = []
    if len(std_df) == 0 or pd.isna(pd.to_numeric(std_df["Conc"].iloc[0], errors="coerce")):
        raise ValueError("The first row (the Blank) needs a Concentration value on the Standards tab.")
    cols = lay.rep_cols(std_df)
    sdf = std_df.copy()
    sdf["Conc"] = pd.to_numeric(sdf["Conc"], errors="coerce")
    sdf = sdf.dropna(subset=["Conc"]).reset_index(drop=True)
    if len(sdf) < 3:
        raise ValueError("Need the blank plus at least 2 non-blank concentration levels to fit a curve.")

    raw = np.array([[_plate_get(plate, _norm_well(r.get(sc))) for sc in cols]
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

    res = dict(
        fit=fit, fit_label=fit_type,
        concs=concs.tolist(), labels=[str(x) for x in sdf["Label"].tolist()],
        means=_clean(means), sds=_clean(sds), raw_arr=_clean(raw), delta_arr=_clean(delta),
        blank_mean=blank, std_df=sdf.to_dict(orient="records"),
    )
    res["qc"] = curve_qc(res)
    bad = [res["labels"][k] for k, r in enumerate(res["qc"]["recovery"]) if r is not None and not _recovery_ok(r)]
    if bad:
        warnings.append(f"Back-calculated recovery outside {RECOVERY_OK[0]:g}–{RECOVERY_OK[1]:g}% for: "
                        f"{', '.join(bad)}. Consider another fit type or excluding an outlier well.")
    return res, warnings


def _clean(a):
    """NaN -> None, numpy -> float, for JSON."""
    if np.ndim(a) == 2:
        return [[None if not np.isfinite(v) else float(v) for v in row] for row in a]
    return [None if not np.isfinite(v) else float(v) for v in a]


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


def _recovery_ok(r: float) -> bool:
    return RECOVERY_OK[0] <= r <= RECOVERY_OK[1]


def curve_qc(res: dict) -> dict:
    """Curve quality figures:
      * blank_sd: SD of the blank replicates (needs ≥ 2);
      * lod / loq: the concentrations whose ΔSignal is 3 / 10 blank SDs from
        the blank (in the curve's direction, so decreasing curves work);
      * recovery: back-calculated ÷ nominal concentration per level, in %
        (None for the blank and levels without signal)."""
    fit = res["fit"]
    raw = _as_float_matrix(res["raw_arr"])
    concs = _as_float_array(res["concs"])
    means = _as_float_array(res["means"])
    blank_reps = raw[0][np.isfinite(raw[0])] if raw.size else np.array([])
    sd = float(np.std(blank_reps, ddof=1)) if blank_reps.size >= 2 else np.nan
    stds = concs[1:][np.isfinite(concs[1:])]
    lo, hi = (float(stds.min()), float(stds.max())) if stds.size else (0.0, 1.0)
    direction = np.sign(float(fit_eval(fit, np.array([hi]))[0] - fit_eval(fit, np.array([lo]))[0])) or 1.0

    def limit(k: float):
        if not np.isfinite(sd):
            return None
        c = back_calc(direction * k * sd, fit)
        return float(c) if np.isfinite(c) and c >= 0 else None

    recovery = [None]
    for k in range(1, len(concs)):
        bc = back_calc(means[k], fit) if np.isfinite(means[k]) else np.nan
        recovery.append(float(bc / concs[k] * 100) if np.isfinite(bc) and concs[k] else None)
    return {"blank_sd": sd if np.isfinite(sd) else None, "n_blank": int(blank_reps.size),
            "lod": limit(3), "loq": limit(10), "recovery": recovery, "increasing": bool(direction > 0)}


def _conc_flags(conc: float, c_min: float, c_max: float, qc: dict) -> str:
    if not np.isfinite(conc):
        return "undefined"
    flags = []
    if conc < c_min - 1e-9:
        flags.append("< range")
    elif conc > c_max + 1e-9:
        flags.append("> range")
    if qc.get("lod") is not None and conc < qc["lod"]:
        flags.append("< LOD")
    elif qc.get("loq") is not None and conc < qc["loq"]:
        flags.append("< LOQ")
    return ", ".join(flags)


def sample_results(plate: pd.DataFrame, res: dict, samples: dict, meta: dict | None = None, *,
                   excluded=(), std_wells: set[str] | None = None, blank: float | None = None) -> list[dict]:
    """One row per non-standard well with signal: back-calculated conc + flags
    (range, LOD/LOQ). Excluded wells are listed with Excluded=True and no
    concentration. `std_wells` (this plate's standards) defaults to the
    curve's own; `blank` (this plate's blank mean) to the curve's."""
    meta = meta or {}
    fit = res["fit"]
    blank = float(res["blank_mean"]) if blank is None or not np.isfinite(blank) else float(blank)
    concs = _as_float_array(res["concs"])
    c_min, c_max = float(np.nanmin(concs)), float(np.nanmax(concs))
    qc = res.get("qc") or {}
    if std_wells is None:
        std_wells = {_norm_well(r.get(sc)) for r in res["std_df"] for sc in r if re.fullmatch(r"S\d+", sc)} - {""}
    excluded = set(excluded)
    rows = []
    for row_lbl in _PLATE_ROWS:
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            if well in std_wells:
                continue
            sig = _plate_get(plate, well)
            if not np.isfinite(sig):
                continue
            m = meta.get(well, {})
            row = {"Well": well, "Label": samples.get(well, ""), "Subject": m.get("Subject", ""),
                   "Timepoint": m.get("Timepoint", ""), "Signal": sig, "ΔSignal": sig - blank}
            if well in excluded:
                rows.append({**row, "Conc": None, "Flag": "excluded", "Excluded": True})
                continue
            conc = back_calc(sig - blank, fit)
            rows.append({**row, "Conc": conc if np.isfinite(conc) else None,
                         "Flag": _conc_flags(conc, c_min, c_max, qc), "Excluded": False})
    return rows


def standards_table(res: dict, sig_unit: str, conc_unit: str, excluded=()) -> list[dict]:
    """The Streamlit app's "Intermediate values" table: raw, Δ, mean, SD, CV
    per level, plus back-calculated recovery. Excluded wells show "excl."."""
    rows = []
    means, sds = _as_float_array(res["means"]), _as_float_array(res["sds"])
    raw_arr, delta_arr = _as_float_matrix(res["raw_arr"]), _as_float_matrix(res["delta_arr"])
    n_sets = raw_arr.shape[1] if raw_arr.ndim == 2 else 0
    recovery = (res.get("qc") or {}).get("recovery") or [None] * len(res["labels"])
    excluded = set(excluded)
    for k, label in enumerate(res["labels"]):
        n_reps = int(np.isfinite(delta_arr[k]).sum())
        sd = sds[k]
        mean = means[k]
        wells = [_norm_well(res["std_df"][k].get(f"S{s + 1}")) for s in range(n_sets)]

        def cell(arr, s):
            return "excl." if wells[s] in excluded else fmt(arr[k, s])
        # The blank's mean ΔSignal is 0 by construction, so a CV there is meaningless.
        cv = abs(sd / mean) * 100 if k > 0 and np.isfinite(sd) and np.isfinite(mean) and mean != 0 else np.nan
        rec = recovery[k] if k < len(recovery) else None
        rows.append({
            "Label": label + (" (blank)" if k == 0 else ""),
            f"Conc ({conc_unit})": f"{res['concs'][k]:.5g}",
            **{f"S{s + 1} raw": cell(raw_arr, s) for s in range(n_sets)},
            **{f"S{s + 1} Δ": cell(delta_arr, s) for s in range(n_sets)},
            f"Mean Δ ({sig_unit})": fmt(mean),
            f"SD ({sig_unit})": fmt(sd) if np.isfinite(sd) else ("n=1" if n_reps == 1 else "—"),
            "CV (%)": fmt(cv, 3),
            "Back-calc conc": fmt(back_calc(mean, res["fit"])) if k > 0 and np.isfinite(mean) else "—",
            "Recovery (%)": "—" if rec is None else (f"{rec:.1f}" + ("" if _recovery_ok(rec) else " ⚠")),
        })
    return rows


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


def well_layout(plate: pd.DataFrame | None, std_df: pd.DataFrame, sample_df: pd.DataFrame,
                excluded=()) -> list[dict]:
    """One entry per well (A1…H12) for the Layout tab's clickable plate."""
    stds = std_wells_map(std_df)
    meta = sample_meta(sample_df)
    excluded = set(excluded)
    out = []
    for ri, row_lbl in enumerate(_PLATE_ROWS):
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            v = _plate_get(plate, well)
            cell = {"well": well, "value": float(v) if np.isfinite(v) else None,
                    "role": "data" if np.isfinite(v) else "empty", "excluded": well in excluded}
            if well in stds:
                info = stds[well]
                cell.update(role="blank" if info["is_blank"] else "std", set=info["set"], conc=info["conc"], label=info["label"])
            elif well in meta:
                cell.update(role="sample", label=meta[well]["Label"], subject=meta[well]["Subject"],
                            timepoint=meta[well]["Timepoint"])
            out.append(cell)
    return out
