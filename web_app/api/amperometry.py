"""
Amperometry mode API — ports macos_app/ui/modes/amperometry_view.py's logic
to HTTP endpoints. Every fit/parse/export call below is the exact same
core/*.py or modes/amperometry.py function that view used, unmodified.

Differs from solid_state.py by: baseline subtraction, segmented-linear
fits, the channel-average trace, and the effective-concentration
(dilution) calculator — matching modes/amperometry.py's own module
docstring on what sets it apart from Solid-State.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.calibration_table import _baseline_keep_mask, _default_cpdf
from core.constants import AVG_COLOR, PAL, fmt
from core.numeric import _eff_t_start, smooth_signal, to_num
from core.parsing import parse_with_options
from core.shared_tabs import _amp_label, render_ts_png
from core.step_detection import detect_step_edges, edges_to_windows
from modes.amperometry import (
    _apply_effective_concentration,
    _cpdf_from_autodetect_windows,
    _load_sample_data,
    piecewise_fit,
    render_cal_png,
)
from web_app.api.common import df_records, figure_json, png_response, records_to_df, require_file_index
from web_app.deps import get_session
from web_app.session import SessionData

router = APIRouter(prefix="/api/amperometry", tags=["amperometry"])

_FILES_KEY = "amp_files"
_CPDF_COLUMNS = ["Label", "Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration", "Baseline"]


# -- shared state shape --------------------------------------------------------
def _state(session: SessionData) -> dict:
    files = session.amp_files
    multi = len(files) > 1
    channel_labels = [
        _amp_label(f["filename"], ch["name"], multi) for f in files for ch in f["channels"]
    ]
    return {
        "files": [
            {
                "filename": f["filename"],
                "n_rows": len(f["df"]),
                "columns": list(f["df"].columns),
                "channels": f["channels"],
                "cpdf": df_records(f["cpdf"]),
            }
            for f in files
        ],
        "channel_labels": channel_labels,
        "signal_unit": session.cur_unit,
        "conc_unit": session.conc_unit,
        "smooth_method": session.smooth_method,
        "smooth_window": session.smooth_window,
        "smooth_polyorder": session.smooth_polyorder,
        "initial_volume": session.initial_volume,
        "vol_unit": session.vol_unit,
        "cal_results": {"channels": list(session.cal_results["results"].keys())} if session.cal_results else None,
    }


@router.get("/state")
def get_state(session: SessionData = Depends(get_session)) -> dict:
    return _state(session)


# -- Import ---------------------------------------------------------------------
def _fallback_channels(df: pd.DataFrame) -> list[dict]:
    columns = list(df.columns)
    if not columns:
        return []
    return [{"name": "Channel 1", "tc": columns[0], "ic": columns[1] if len(columns) > 1 else columns[0]}]


@router.post("/files")
async def upload_files(files: list[UploadFile] = File(...), session: SessionData = Depends(get_session)) -> dict:
    existing = list(session.amp_files)
    by_name = {f["filename"]: f for f in existing}
    order = [f["filename"] for f in existing]
    for upload in files:
        raw = await upload.read()
        filename = upload.filename or "upload.csv"
        try:
            df, auto_channels = parse_with_options(filename, raw, fmt="standard", delimiter="auto")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Could not parse {filename}: {exc}") from exc
        if filename in by_name:
            channels = by_name[filename]["channels"]
            cpdf = by_name[filename]["cpdf"]
        else:
            channels = auto_channels or _fallback_channels(df)
            cpdf = _default_cpdf()
            order.append(filename)
        by_name[filename] = {"filename": filename, "df": df, "channels": channels, "cpdf": cpdf}
    session.amp_files = [by_name[name] for name in order]
    return _state(session)


@router.post("/files/sample")
def load_sample(session: SessionData = Depends(get_session)) -> dict:
    sample_files = _load_sample_data()
    if sample_files is None:
        raise HTTPException(status_code=404, detail="Sample data files are missing from this deployment.")
    session.amp_files = sample_files
    return _state(session)


class UnitsBody(BaseModel):
    signal_unit: str | None = None
    conc_unit: str | None = None


@router.post("/units")
def set_units(body: UnitsBody, session: SessionData = Depends(get_session)) -> dict:
    if body.signal_unit is not None:
        session.cur_unit = body.signal_unit
    if body.conc_unit is not None:
        session.conc_unit = body.conc_unit
    return _state(session)


# -- Channel assignment -----------------------------------------------------------
class ChannelsBody(BaseModel):
    channels: list[dict]


@router.post("/files/{index}/channels")
def set_channels(index: int, body: ChannelsBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.amp_files, index)
    session.amp_files[index] = {**frec, "channels": body.channels}
    return _state(session)


# -- Calibration table -------------------------------------------------------------
class TableBody(BaseModel):
    rows: list[dict]


@router.post("/files/{index}/table")
def set_table(index: int, body: TableBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.amp_files, index)
    session.amp_files[index] = {**frec, "cpdf": records_to_df(body.rows, _CPDF_COLUMNS)}
    return _state(session)


# -- Effective concentration (dilution) calculator ------------------------------------
class EffConcBody(BaseModel):
    initial_volume: float
    vol_unit: str


@router.post("/files/{index}/effective-concentration")
def apply_effective_concentration(index: int, body: EffConcBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.amp_files, index)
    session.initial_volume = body.initial_volume
    session.vol_unit = body.vol_unit
    new_cpdf = _apply_effective_concentration(frec["cpdf"], body.initial_volume)
    session.amp_files[index] = {**frec, "cpdf": new_cpdf}
    return _state(session)


# -- Autodetect -------------------------------------------------------------------
class AutodetectBody(BaseModel):
    channel: str
    sensitivity: float = 1.0
    min_gap: float = 30.0
    max_steps: int = 0


@router.post("/files/{index}/autodetect")
def autodetect(index: int, body: AutodetectBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.amp_files, index)
    ch = next((c for c in frec["channels"] if c["name"] == body.channel), None)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel {body.channel!r}")
    t_arr = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = to_num(frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = smooth_signal(i_arr, session.smooth_method, session.smooth_window, session.smooth_polyorder)
    edges = detect_step_edges(
        t_arr, i_arr, min_step_seconds=body.min_gap, sensitivity=body.sensitivity,
        max_edges=body.max_steps or None,
    )
    session.ts_ui.setdefault(_FILES_KEY, {}).setdefault("autodetect_edges", {})[frec["filename"]] = edges
    return {"edges": edges}


class AutodetectApplyBody(BaseModel):
    channel: str
    include_baseline: bool = True


@router.post("/files/{index}/autodetect/apply")
def autodetect_apply(index: int, body: AutodetectApplyBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.amp_files, index)
    edges = session.ts_ui.get(_FILES_KEY, {}).get("autodetect_edges", {}).get(frec["filename"], [])
    if not edges:
        raise HTTPException(status_code=400, detail="No detected edges to apply — run autodetect first.")
    ch = next((c for c in frec["channels"] if c["name"] == body.channel), None)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel {body.channel!r}")
    t_arr = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    trace_end = float(np.nanmax(t_arr)) if t_arr.size else 0.0
    windows = edges_to_windows(edges, trace_end, include_leading_baseline=body.include_baseline)
    session.amp_files[index] = {**frec, "cpdf": _cpdf_from_autodetect_windows(windows)}
    return _state(session)


# -- Time series figure -------------------------------------------------------------
_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]


class TimeseriesBody(BaseModel):
    visible: list[str]
    smooth_method: str = "None"
    smooth_window: int = 11
    smooth_polyorder: int = 2
    y_auto: bool = True
    y_min: float | None = None
    y_max: float | None = None


@router.post("/timeseries")
def timeseries_figure(body: TimeseriesBody, session: SessionData = Depends(get_session)) -> dict:
    session.smooth_method = body.smooth_method
    session.smooth_window = body.smooth_window
    session.smooth_polyorder = body.smooth_polyorder

    files = session.amp_files
    multi = len(files) > 1
    visible = set(body.visible)
    fig = go.Figure()
    for fi, frec in enumerate(files):
        for ci, ch in enumerate(frec["channels"]):
            label = _amp_label(frec["filename"], ch["name"], multi)
            if label not in visible:
                continue
            t = to_num(frec["df"][ch["tc"]])
            raw = to_num(frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            smoothed = smooth_signal(raw, body.smooth_method, body.smooth_window, body.smooth_polyorder)
            color = PAL[(fi if multi else ci) % len(PAL)]
            dash = _DASHES[ci % len(_DASHES)] if multi else "solid"
            if body.smooth_method != "None":
                fig.add_trace(go.Scatter(x=t, y=raw, name=f"{label} (raw)", mode="lines",
                                          opacity=0.35, line=dict(color=color, width=1, dash=dash), showlegend=False))
            fig.add_trace(go.Scatter(x=t, y=smoothed, name=label, mode="lines", line=dict(color=color, width=1.5, dash=dash)))

    for frec in files:
        for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
            ets = _eff_t_start(row)
            if ets is not None and pd.notna(row.get("t_end")):
                color = "rgba(255,165,0,0.22)" if row.get("Baseline") else "rgba(100,160,255,0.15)"
                label = f"{frec['filename']}: {row['Label']}" if multi else str(row["Label"])
                fig.add_vrect(x0=ets, x1=row["t_end"], fillcolor=color, layer="below", line_width=0,
                              annotation_text=label, annotation_position="top left")

    edges_by_file = session.ts_ui.get(_FILES_KEY, {}).get("autodetect_edges", {})
    for frec in files:
        for edge in edges_by_file.get(frec["filename"], []):
            fig.add_vline(x=edge, line_dash="dot", line_color="#e91e63", opacity=0.55,
                          annotation_text="detected", annotation_position="bottom")

    y_range = {} if body.y_auto else {"range": [body.y_min, body.y_max]}
    fig.update_layout(
        xaxis_title="Time (s)", yaxis_title=f"Current ({session.cur_unit})",
        hovermode="x unified", height=480, template="plotly_white", showlegend=True,
        xaxis=dict(rangeslider=dict(visible=True, thickness=0.05)),
        yaxis=dict(**y_range),
    )
    return figure_json(fig)


# -- Compute calibration -------------------------------------------------------------
class ComputeBody(BaseModel):
    selected: list[str]
    fit_type: str = "Linear"
    n_seg: int = 2
    show_avg: bool = False


@router.post("/compute")
def compute(body: ComputeBody, session: SessionData = Depends(get_session)) -> dict:
    if not body.selected:
        raise HTTPException(status_code=400, detail="Select at least one channel to analyse.")

    files = session.amp_files
    multi = len(files) > 1
    combo_lookup = {
        _amp_label(frec["filename"], ch["name"], multi): (frec, ch)
        for frec in files for ch in frec["channels"]
    }
    n_seg = body.n_seg if body.fit_type == "Segmented Linear" else 1
    show_avg = body.show_avg and len(body.selected) >= 2

    results: dict[str, dict] = {}
    warnings: list[str] = []
    for ch_name in body.selected:
        if ch_name not in combo_lookup:
            warnings.append(f"{ch_name}: not found.")
            continue
        frec, ch = combo_lookup[ch_name]
        cpdf = frec["cpdf"].dropna(subset=["t_end"]).reset_index(drop=True)
        if cpdf.empty:
            warnings.append(f"{ch_name}: no valid calibration rows.")
            continue

        base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
        base_idx = int(base_rows.index[0]) if len(base_rows) else 0
        if len(base_rows) == 0:
            warnings.append(f"{ch_name}: no baseline row marked — using the first row as baseline.")

        t_arr = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
        i_arr = to_num(frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
        i_arr = smooth_signal(i_arr, session.smooth_method, session.smooth_window, session.smooth_polyorder)

        avgs, sigs = [], []
        for _, row in cpdf.iterrows():
            ets = _eff_t_start(row)
            if ets is None:
                avgs.append(np.nan)
                sigs.append(np.nan)
                continue
            mask = (t_arr >= ets) & (t_arr <= row["t_end"])
            pts = i_arr[mask]
            pts = pts[~np.isnan(pts)]
            avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
            sigs.append(float(np.std(pts, ddof=1)) if pts.size >= 2 else np.nan)

        base_val = avgs[base_idx]
        sigma_bl = sigs[base_idx]
        if np.isnan(base_val):
            warnings.append(f"{ch_name}: baseline window has no data points — ΔI cannot be computed.")
            continue
        delta_i = [(v - base_val) if not np.isnan(v) else np.nan for v in avgs]

        results[ch_name] = dict(
            concs=cpdf["Concentration"].values.astype(float),
            labels=cpdf["Label"].values,
            avgs=avgs, sigs=sigs, delta_i=delta_i,
            sigma_bl=float(sigma_bl), is_average=False,
            baselines=cpdf["Baseline"].tolist(),
        )

    avg_chs = [c for c in body.selected if c in results]
    if show_avg and len(avg_chs) >= 2:
        all_di = np.array([results[c]["delta_i"] for c in avg_chs], dtype=float)
        all_avgs = np.array([results[c]["avgs"] for c in avg_chs], dtype=float)
        all_sigma = [results[c]["sigma_bl"] for c in avg_chs]
        n_ch = len(avg_chs)
        avg_delta_i = np.nanmean(all_di, axis=0)
        std_across_ch = np.nanstd(all_di, axis=0, ddof=1)
        avg_avgs = np.nanmean(all_avgs, axis=0)
        valid_s = [s for s in all_sigma if np.isfinite(s)]
        sigma_bl_avg = (np.sqrt(sum(s ** 2 for s in valid_s)) / n_ch) if valid_s else np.nan
        results["Channel Average"] = dict(
            concs=results[avg_chs[0]]["concs"], labels=results[avg_chs[0]]["labels"],
            avgs=avg_avgs.tolist(), sigs=std_across_ch.tolist(), delta_i=avg_delta_i.tolist(),
            sigma_bl=float(sigma_bl_avg), is_average=True, baselines=results[avg_chs[0]]["baselines"],
        )

    if not results:
        session.cal_results = None
        return {"warnings": warnings, "figure": None, "stats": []}

    session.cal_results = dict(results=results, fit_type=body.fit_type, n_seg=n_seg)
    fig, stat_rows = _render_calibration_curve(results, body.fit_type, n_seg, session)
    return {"warnings": warnings, "figure": figure_json(fig), "stats": stat_rows}


def _render_calibration_curve(res_map: dict, fit_type: str, n_seg: int, session: SessionData) -> tuple[go.Figure, list[dict]]:
    fig = go.Figure()
    stat_rows = []
    conc_unit, cur_unit = session.conc_unit, session.cur_unit

    for j, (ch_name, res) in enumerate(res_map.items()):
        is_avg = res.get("is_average", False)
        col = AVG_COLOR if is_avg else PAL[j % len(PAL)]
        keep = _baseline_keep_mask(res.get("baselines", [False] * len(res["concs"])))
        x = np.asarray(res["concs"], dtype=float)[keep]
        y = np.array(res["delta_i"], float)[keep]
        labels_plot = np.asarray(res["labels"], dtype=object)[keep]
        sigs_plot = np.asarray(res["sigs"], dtype=float)[keep]
        marker_sym = "diamond" if is_avg else "circle"

        fig.add_trace(go.Scatter(
            x=x, y=y, name=ch_name, mode="markers+text",
            text=labels_plot, textposition="top center",
            marker=dict(color=col, size=10, symbol=marker_sym),
            error_y=dict(type="data", array=[float(s) if (s and not np.isnan(s)) else 0.0 for s in sigs_plot],
                         visible=is_avg, color=col),
        ))

        pf = piecewise_fit(x, y, int(n_seg) if fit_type == "Segmented Linear" else 1)
        segs, breakpoints = pf["segments"], pf["breakpoints"]
        for k, seg in enumerate(segs):
            xp = np.linspace(seg["xr"][0], seg["xr"][1], 300)
            yp = seg["slope"] * xp + seg["intercept"]
            lbl = ch_name + (f" seg {k + 1}" if len(segs) > 1 else "")
            fig.add_trace(go.Scatter(x=xp, y=yp, name=f"{lbl} fit", mode="lines", showlegend=False,
                                      line=dict(color=col, dash="dot" if is_avg else "dash", width=2)))

            sigma, sens, intcpt = res["sigma_bl"], seg["slope"], seg["intercept"]
            lod_val = ((3.3 * abs(sigma) - intcpt) / sens) if sens else np.nan
            loq_val = ((10.0 * abs(sigma) - intcpt) / sens) if sens else np.nan
            stat_rows.append({
                "Channel": ch_name,
                "Segment": (f"{seg['xr'][0]:.3g}–{seg['xr'][1]:.3g} {conc_unit}" if len(segs) > 1 else "Full range"),
                f"Sensitivity ({cur_unit}/{conc_unit})": fmt(sens),
                "R²": f"{seg['r2']:.4f}",
                f"LOD ({conc_unit})": fmt(lod_val),
                f"LOQ ({conc_unit})": fmt(loq_val),
            })

        for bp in breakpoints:
            fig.add_vline(x=bp, line_dash="dot", line_color=col, annotation_text=f"{bp:.3g} {conc_unit}")

    fig.update_layout(
        xaxis_title=f"Concentration ({conc_unit})", yaxis_title=f"ΔI ({cur_unit})",
        hovermode="closest", height=480, template="plotly_white",
    )
    return fig, stat_rows


# -- Export -----------------------------------------------------------------------
class ExportCurveBody(BaseModel):
    fmt: str = "png"
    dpi: int = 150
    style: str = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/curve")
def export_curve(body: ExportCurveBody, session: SessionData = Depends(get_session)):
    if not session.cal_results:
        raise HTTPException(status_code=400, detail="Run calibration analysis first.")
    png_bytes = render_cal_png(
        session.cal_results["results"], session.cal_results["fit_type"], int(session.cal_results["n_seg"]),
        session.conc_unit, session.cur_unit,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
    )
    media_type = {"svg": "image/svg+xml", "pdf": "application/pdf", "tiff": "image/tiff"}.get(body.fmt, "image/png")
    return png_response(png_bytes, f"calibration_curve.{body.fmt}", media_type)


class ExportTimeseriesBody(BaseModel):
    visible: list[str]
    fmt: str = "png"
    dpi: int = 150
    style: str = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/timeseries")
def export_timeseries(body: ExportTimeseriesBody, session: SessionData = Depends(get_session)):
    files = session.amp_files
    if not files:
        raise HTTPException(status_code=400, detail="No files loaded.")
    png_bytes = render_ts_png(
        files, session.cur_unit, body.visible,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
        smooth_method=session.smooth_method, smooth_window=session.smooth_window,
        smooth_polyorder=session.smooth_polyorder,
    )
    media_type = {"svg": "image/svg+xml", "pdf": "application/pdf", "tiff": "image/tiff"}.get(body.fmt, "image/png")
    return png_response(png_bytes, f"time_series.{body.fmt}", media_type)


@router.get("/export/csv")
def export_csv(session: SessionData = Depends(get_session)):
    if not session.cal_results:
        raise HTTPException(status_code=400, detail="Run calibration analysis first.")
    rows = []
    for ch_name, res in session.cal_results["results"].items():
        for lbl, conc, avg, sig, di in zip(res["labels"], res["concs"], res["avgs"], res["sigs"], res["delta_i"]):
            rows.append({
                "Channel": ch_name, "Label": lbl, f"Concentration ({session.conc_unit})": conc,
                f"Avg Current ({session.cur_unit})": avg, f"SD ({session.cur_unit})": sig, f"ΔI ({session.cur_unit})": di,
            })
    csv_text = pd.DataFrame(rows).to_csv(index=False)
    return png_response(csv_text.encode("utf-8"), "calibration_data.csv", "text/csv")


# -- Comparison (cross-file overlay) -----------------------------------------------
def _compute_file_fit(frec: dict, session: SessionData) -> dict | None:
    """One independent linear fit per file, first channel only — a quick
    side-by-side comparison, not the full multi-channel analysis workbench
    (mirrors macos_app/ui/modes/amperometry_view.py's _compute_file_fit)."""
    channels = frec.get("channels", [])
    if not channels:
        return None
    ch = channels[0]
    cpdf = frec["cpdf"].dropna(subset=["t_end"]).reset_index(drop=True)
    if cpdf.empty:
        return None

    base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
    base_idx = int(base_rows.index[0]) if len(base_rows) else 0

    df = frec["df"]
    t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = smooth_signal(i_arr, session.smooth_method, session.smooth_window, session.smooth_polyorder)

    avgs = []
    for _, row in cpdf.iterrows():
        ets = _eff_t_start(row)
        if ets is None:
            avgs.append(np.nan)
            continue
        mask = (t_arr >= ets) & (t_arr <= row["t_end"])
        pts = i_arr[mask]
        pts = pts[~np.isnan(pts)]
        avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)

    base_val = avgs[base_idx]
    if np.isnan(base_val):
        return None
    delta_i = [(v - base_val) if not np.isnan(v) else np.nan for v in avgs]

    keep = _baseline_keep_mask(cpdf["Baseline"].tolist())
    x = np.asarray(cpdf["Concentration"].values, dtype=float)[keep]
    y = np.asarray(delta_i, dtype=float)[keep]
    fit_result = piecewise_fit(x, y, 1)
    if not fit_result["segments"]:
        return None
    seg = fit_result["segments"][0]
    curve_x = np.linspace(seg["xr"][0], seg["xr"][1], 100)
    curve_y = seg["slope"] * curve_x + seg["intercept"]

    conc_unit, cur_unit = session.conc_unit, session.cur_unit
    return {
        "x": x.tolist(), "y": y.tolist(), "curve_x": curve_x.tolist(), "curve_y": curve_y.tolist(),
        "stats": {
            "File": frec["filename"], "Channel": ch["name"],
            f"Sensitivity ({cur_unit}/{conc_unit})": fmt(seg["slope"]),
            "R²": f"{seg['r2']:.4f}",
        },
    }


@router.get("/comparison")
def comparison(session: SessionData = Depends(get_session)) -> dict:
    fig = go.Figure()
    stat_rows = []
    for j, frec in enumerate(session.amp_files):
        result = _compute_file_fit(frec, session)
        if result is None:
            continue
        col = PAL[j % len(PAL)]
        fig.add_trace(go.Scatter(x=result["x"], y=result["y"], mode="markers", name=frec["filename"],
                                  marker=dict(color=col, size=9)))
        fig.add_trace(go.Scatter(x=result["curve_x"], y=result["curve_y"], mode="lines", showlegend=False,
                                  line=dict(color=col, dash="dash", width=2)))
        stat_rows.append(result["stats"])

    fig.update_layout(
        xaxis_title=f"Concentration ({session.conc_unit})", yaxis_title=f"ΔI ({session.cur_unit})",
        hovermode="closest", height=480, template="plotly_white",
    )
    return {"figure": figure_json(fig) if stat_rows else None, "stats": stat_rows}
