"""
Solid-State (potentiometric / Nernstian) mode API. Parsing, the Nernstian
LOD fit and PNG/SVG/PDF export use the same core/ functions as the Streamlit
app (core/analysis/solid_state.py, core/analysis/traces.py). Each endpoint mutates the
session in place and returns the new state.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core.analysis.solid_state import (
    _cpdf_from_autodetect_windows,
    _load_solid_sample_data,
    ideal_slope_in_signal_unit,
    nernstian_lod_fit,
    render_solid_cal_png,
)
from core.analysis.traces import _amp_label, render_ts_png, window_stats
from core.calibration_table import _default_solid_cpdf, _solid_cpdf_from_records
from core.constants import PAL, fmt
from core.numeric import _eff_t_start, smooth_signal, to_num
from core.parsing import parse_with_options
from core.step_detection import detect_step_edges, edges_to_windows
from web_app.api.common import (
    ExportFmt,
    ExportStyle,
    channels_fit_columns,
    df_records,
    export_media_type,
    figure_json,
    guess_channels,
    png_response,
    records_to_df,
    require_file_index,
)
from web_app.deps import get_session
from web_app.history import tracked
from web_app.modespec import ModeSpec
from web_app.session import SessionData

router = APIRouter(prefix="/api/solid_state", tags=["solid_state"])

# Session fields an undo step restores (web_app/history.py). Not the
# calibration results: those always reflect the last Compute, as ③ shows.
UNDO_FIELDS = ("solid_files",)

_FILES_KEY = "solid_files"
_CPDF_COLUMNS = ["Label", "Concentration", "t_start", "t_end", "avg_duration", "Reading_mV"]


# -- shared state shape --------------------------------------------------------
def _state(session: SessionData) -> dict:
    files = session.solid_files
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
        "signal_unit": session.solid_unit,
        "conc_unit": session.solid_conc_unit,
        "smooth_method": session.smooth_method,
        "smooth_window": session.smooth_window,
        "smooth_polyorder": session.smooth_polyorder,
        "cal_results": _cal_results_summary(session),
    }


def _cal_results_summary(session: SessionData) -> dict | None:
    if not session.solid_cal_results:
        return None
    return {"channels": list(session.solid_cal_results["results"].keys())}


@router.get("/state")
def get_state(session: SessionData = Depends(get_session)) -> dict:
    return _state(session)


# -- Import ---------------------------------------------------------------------
@router.post("/files")
@tracked("solid_state", UNDO_FIELDS)
async def upload_files(files: list[UploadFile] = File(...), session: SessionData = Depends(get_session)) -> dict:
    existing = list(session.solid_files)
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
            # Re-uploading a file keeps its calibration table, and its channel
            # mapping too as long as those columns still exist in the new data.
            prev_channels = by_name[filename]["channels"]
            channels = (prev_channels if channels_fit_columns(prev_channels, df.columns)
                        else (auto_channels or guess_channels(df)))
            cpdf = by_name[filename]["cpdf"]
        else:
            channels = auto_channels or guess_channels(df)
            cpdf = _default_solid_cpdf()
            order.append(filename)
        by_name[filename] = {"filename": filename, "df": df, "channels": channels, "cpdf": cpdf}
    session.solid_files = [by_name[name] for name in order]
    session.solid_cal_results = None
    return _state(session)


@router.delete("/files/{index}")
@tracked("solid_state", UNDO_FIELDS)
def remove_file(index: int, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.solid_files, index)
    session.solid_files = [f for i, f in enumerate(session.solid_files) if i != index]
    session.ts_ui.get(_FILES_KEY, {}).get("autodetect_edges", {}).pop(frec["filename"], None)
    session.solid_cal_results = None
    return _state(session)


@router.delete("/files")
@tracked("solid_state", UNDO_FIELDS)
def clear_files(session: SessionData = Depends(get_session)) -> dict:
    session.solid_files = []
    session.ts_ui.pop(_FILES_KEY, None)
    session.solid_cal_results = None
    return _state(session)



@router.post("/files/sample")
@tracked("solid_state", UNDO_FIELDS)
def load_sample(session: SessionData = Depends(get_session)) -> dict:
    sample_files = _load_solid_sample_data()
    if sample_files is None:
        raise HTTPException(status_code=404, detail="Sample data files are missing from this deployment.")
    session.solid_files = sample_files
    session.ts_ui.pop(_FILES_KEY, None)
    session.solid_cal_results = None
    return _state(session)


class UnitsBody(BaseModel):
    signal_unit: str | None = None
    conc_unit: str | None = None


@router.post("/units")
def set_units(body: UnitsBody, session: SessionData = Depends(get_session)) -> dict:
    if body.signal_unit is not None:
        session.solid_unit = body.signal_unit
    if body.conc_unit is not None:
        session.solid_conc_unit = body.conc_unit
    return _state(session)


# -- Channel assignment -----------------------------------------------------------
class ChannelsBody(BaseModel):
    channels: list[dict]


@router.post("/files/{index}/channels")
@tracked("solid_state", UNDO_FIELDS)
def set_channels(index: int, body: ChannelsBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.solid_files, index)
    if not body.channels:
        raise HTTPException(status_code=400, detail="At least one channel is required.")
    if not channels_fit_columns(body.channels, frec["df"].columns):
        raise HTTPException(status_code=400, detail="Channel assignment references a column that isn't in this file.")
    names = [str(c.get("name", "")).strip() for c in body.channels]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="Channel names must be non-empty and unique within a file.")
    session.solid_files[index] = {**frec, "channels": [{**c, "name": n} for c, n in zip(body.channels, names)]}
    return _state(session)


# -- Calibration table -------------------------------------------------------------
class TableBody(BaseModel):
    rows: list[dict]


@router.post("/files/{index}/table")
@tracked("solid_state", UNDO_FIELDS)
def set_table(index: int, body: TableBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.solid_files, index)
    session.solid_files[index] = {**frec, "cpdf": records_to_df(body.rows, _CPDF_COLUMNS, bool_cols=())}
    return _state(session)


# -- Autodetect -------------------------------------------------------------------
class AutodetectBody(BaseModel):
    channel: str
    sensitivity: float = 1.0
    min_gap: float = 30.0
    max_steps: int = 0


@router.post("/files/{index}/autodetect")
def autodetect(index: int, body: AutodetectBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.solid_files, index)
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


@router.post("/files/{index}/autodetect/apply")
@tracked("solid_state", UNDO_FIELDS)
def autodetect_apply(index: int, body: AutodetectApplyBody, session: SessionData = Depends(get_session)) -> dict:
    frec = require_file_index(session.solid_files, index)
    edges = session.ts_ui.get(_FILES_KEY, {}).get("autodetect_edges", {}).get(frec["filename"], [])
    if not edges:
        raise HTTPException(status_code=400, detail="No detected edges to apply — run autodetect first.")
    ch = next((c for c in frec["channels"] if c["name"] == body.channel), None)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel {body.channel!r}")
    t_arr = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    trace_end = float(np.nanmax(t_arr)) if t_arr.size else 0.0
    windows = edges_to_windows(edges, trace_end, include_leading_baseline=False)
    session.solid_files[index] = {**frec, "cpdf": _cpdf_from_autodetect_windows(windows)}
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

    files = session.solid_files
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

    # Draw each distinct window once — several files often share one
    # calibration schedule, and stacked duplicate labels are unreadable.
    drawn: set[tuple] = set()
    for frec in files:
        for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
            ets = _eff_t_start(row)
            if ets is None or pd.isna(row.get("t_end")):
                continue
            key = (round(float(ets), 6), round(float(row["t_end"]), 6), str(row["Label"]))
            if key in drawn:
                continue
            drawn.add(key)
            color = "rgba(100,160,255,0.15)"
            fig.add_vrect(x0=ets, x1=row["t_end"], fillcolor=color, layer="below", line_width=0,
                          annotation_text=str(row["Label"]), annotation_position="top left",
                          annotation_font_size=10)

    edges_by_file = session.ts_ui.get(_FILES_KEY, {}).get("autodetect_edges", {})
    for frec in files:
        for edge in edges_by_file.get(frec["filename"], []):
            fig.add_vline(x=edge, line_dash="dot", line_color="#e91e63", opacity=0.55,
                          annotation_text="detected", annotation_position="bottom")

    y_range = {} if body.y_auto else {"range": [body.y_min, body.y_max]}
    fig.update_layout(
        xaxis_title="Time (s)", yaxis_title=f"Potential ({session.solid_unit})",
        hovermode="x unified", height=480, template="plotly_white", showlegend=True,
        xaxis=dict(rangeslider=dict(visible=True, thickness=0.05)),
        yaxis=dict(**y_range),
    )
    return figure_json(fig)


# -- Compute calibration -------------------------------------------------------------
class ComputeBody(BaseModel):
    selected: list[str]
    ion_charge: int = 1
    lab_temp: float = 25.0


@router.post("/compute")
def compute(body: ComputeBody, session: SessionData = Depends(get_session)) -> dict:
    if not body.selected:
        raise HTTPException(status_code=400, detail="Select at least one channel to analyse.")

    files = session.solid_files
    multi = len(files) > 1
    combo_lookup = {
        _amp_label(frec["filename"], ch["name"], multi): (frec, ch)
        for frec in files for ch in frec["channels"]
    }
    signal_unit = session.solid_unit

    results: dict[str, dict] = {}
    warnings: list[str] = []
    for ch_name in body.selected:
        if ch_name not in combo_lookup:
            warnings.append(f"{ch_name}: not found.")
            continue
        frec, ch = combo_lookup[ch_name]
        cpdf = frec["cpdf"].copy()
        if cpdf.empty:
            warnings.append(f"{ch_name}: calibration table is empty.")
            continue
        rejected = ~(cpdf["Concentration"].astype(float) > 0)
        if rejected.any():
            warnings.append(f"{ch_name}: {int(rejected.sum())} row(s) with Concentration ≤ 0 excluded.")
            cpdf = cpdf[~rejected].reset_index(drop=True)
        if cpdf.empty:
            warnings.append(f"{ch_name}: no valid calibration rows.")
            continue

        t_arr = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
        e_arr = to_num(frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)

        readings, _ = window_stats(t_arr, e_arr, cpdf, override_col="Reading_mV")

        log_conc = np.log10(cpdf["Concentration"].astype(float).to_numpy())
        potential = np.array(readings, dtype=float)
        valid = ~np.isnan(potential)
        if valid.sum() < 2:
            warnings.append(f"{ch_name}: fewer than 2 valid readings.")
            continue

        lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
        nernst_seg = lod_fit["nernstian_segment"]
        ideal = ideal_slope_in_signal_unit(body.lab_temp, body.ion_charge, signal_unit)

        results[ch_name] = dict(
            concs=cpdf["Concentration"].astype(float).tolist(),
            labels=cpdf["Label"].tolist(),
            valid_mask=valid.tolist(),
            log_conc=log_conc[valid].tolist(),
            potential_mv=potential[valid].tolist(),
            low_segment=lod_fit["low_segment"],
            nernstian_segment=nernst_seg,
            lod_log10=lod_fit["lod_log10"],
            lod_conc=lod_fit["lod_conc"],
            sensitivity_mv_per_decade=nernst_seg["slope"] if nernst_seg else None,
            pct_of_ideal_nernstian=(
                100.0 * abs(nernst_seg["slope"]) / ideal if (nernst_seg and ideal) else None
            ),
            ideal_slope_mv_per_decade=ideal,
            signal_unit=signal_unit,
            is_average=False,
        )

    if not results:
        session.solid_cal_results = None
        return {"warnings": warnings, "figure": None, "stats": []}

    session.solid_cal_results = dict(results=results)
    fig, stat_rows = _render_calibration_curve(results, session)
    return {"warnings": warnings, "figure": figure_json(fig), "stats": stat_rows}


def _render_calibration_curve(res_map: dict, session: SessionData) -> tuple[go.Figure, list[dict]]:
    fig = go.Figure()
    stat_rows = []
    for j, (ch_name, res) in enumerate(res_map.items()):
        col = PAL[j % len(PAL)]
        x = np.asarray(res["log_conc"], dtype=float)
        y = np.asarray(res["potential_mv"], dtype=float)
        vmask = res.get("valid_mask", [True] * len(res["labels"]))
        labels_plot = np.asarray(res["labels"], dtype=object)[vmask]

        fig.add_trace(go.Scatter(x=x, y=y, name=ch_name, mode="markers+text",
                                  text=labels_plot, textposition="top center",
                                  marker=dict(color=col, size=10)))

        low, nern = res.get("low_segment"), res.get("nernstian_segment")
        lod_log10 = res.get("lod_log10")
        has_lod = lod_log10 is not None and np.isfinite(lod_log10)
        for seg, seg_name, dash in [(low, "low", "dot"), (nern, "Nernstian", "dash")]:
            if seg is None:
                continue
            if seg_name == "low" and has_lod:
                x0, x1 = float(np.min(x)), lod_log10
            elif seg_name == "Nernstian" and has_lod:
                x0, x1 = lod_log10, float(np.max(x))
            else:
                x0, x1 = float(np.min(x)), float(np.max(x))
            if x1 <= x0:
                x0, x1 = float(np.min(x)), float(np.max(x))
            xp = np.linspace(x0, x1, 200)
            yp = seg["slope"] * xp + seg["intercept"]
            fig.add_trace(go.Scatter(x=xp, y=yp, name=f"{ch_name} {seg_name} fit", mode="lines",
                                      showlegend=False, line=dict(color=col, dash=dash, width=2)))

        if has_lod:
            fig.add_vline(x=lod_log10, line=dict(color=col, dash="dashdot", width=1.2),
                          annotation_text=f"{ch_name} LOD", annotation_position="top")

        ideal = res.get("ideal_slope_mv_per_decade")
        sunit = res.get("signal_unit", session.solid_unit)
        stat_rows.append({
            "Channel": ch_name,
            f"Sensitivity ({sunit}/decade)": fmt(res.get("sensitivity_mv_per_decade")),
            "% of ideal Nernstian": (
                f"{res['pct_of_ideal_nernstian']:.1f}%" if res.get("pct_of_ideal_nernstian") is not None
                else ("—" if ideal is not None else "unit not recognized")
            ),
            f"Ideal ({sunit}/decade)": fmt(ideal),
            "R² (Nernstian)": (f"{nern['r2']:.4f}" if nern else "—"),
            f"LOD ({session.solid_conc_unit})": fmt(res.get("lod_conc")),
        })

    fig.update_layout(
        xaxis_title=f"log₁₀(Concentration [{session.solid_conc_unit}])",
        yaxis_title=f"Potential ({session.solid_unit})",
        hovermode="closest", height=480, template="plotly_white",
    )
    return fig, stat_rows


# -- Export -----------------------------------------------------------------------
class ExportCurveBody(BaseModel):
    fmt: ExportFmt = "png"
    dpi: int = Field(150, ge=50, le=1200)
    style: ExportStyle = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/curve")
def export_curve(body: ExportCurveBody, session: SessionData = Depends(get_session)):
    if not session.solid_cal_results:
        raise HTTPException(status_code=400, detail="Run calibration analysis first.")
    png_bytes = render_solid_cal_png(
        session.solid_cal_results["results"], session.solid_conc_unit, session.solid_unit,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
    )
    media_type = export_media_type(body.fmt)
    return png_response(png_bytes, f"solid_state_calibration_curve.{body.fmt}", media_type)


class ExportTimeseriesBody(BaseModel):
    visible: list[str]
    fmt: ExportFmt = "png"
    dpi: int = Field(150, ge=50, le=1200)
    style: ExportStyle = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/timeseries")
def export_timeseries(body: ExportTimeseriesBody, session: SessionData = Depends(get_session)):
    files = session.solid_files
    if not files:
        raise HTTPException(status_code=400, detail="No files loaded.")
    png_bytes = render_ts_png(
        files, session.solid_unit, body.visible,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
        smooth_method=session.smooth_method, smooth_window=session.smooth_window,
        smooth_polyorder=session.smooth_polyorder,
    )
    media_type = export_media_type(body.fmt)
    return png_response(png_bytes, f"solid_state_time_series.{body.fmt}", media_type)


@router.get("/export/csv")
def export_csv(session: SessionData = Depends(get_session)):
    if not session.solid_cal_results:
        raise HTTPException(status_code=400, detail="Run calibration analysis first.")
    rows = []
    for ch_name, res in session.solid_cal_results["results"].items():
        sunit = res.get("signal_unit", session.solid_unit)
        # log_conc/potential_mv only hold the valid rows — walk valid_mask to
        # line them back up with the full label/concentration lists.
        valid_vals = iter(zip(res["log_conc"], res["potential_mv"]))
        for lbl, conc, ok in zip(res["labels"], res["concs"], res.get("valid_mask", [True] * len(res["labels"]))):
            log_c, pot = next(valid_vals) if ok else (np.nan, np.nan)
            rows.append({
                "Channel": ch_name, "Label": lbl, f"Concentration ({session.solid_conc_unit})": conc,
                "log10(Concentration)": log_c, f"Potential ({sunit})": pot,
            })
    csv_text = pd.DataFrame(rows).to_csv(index=False)
    return png_response(csv_text.encode("utf-8"), "solid_state_calibration_data.csv", "text/csv")


# -- Comparison (cross-file overlay) -----------------------------------------------
def _compute_file_fit(frec: dict, session: SessionData) -> dict | None:
    """One independent Nernstian fit per file, first channel only — a quick
    side-by-side comparison, not the full multi-channel analysis workbench."""
    channels = frec.get("channels", [])
    if not channels:
        return None
    ch = channels[0]
    cpdf = frec["cpdf"].copy()
    if cpdf.empty:
        return None
    rejected = ~(cpdf["Concentration"].astype(float) > 0)
    cpdf = cpdf[~rejected].reset_index(drop=True)
    if cpdf.empty:
        return None

    df = frec["df"]
    t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    e_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)

    readings, _ = window_stats(t_arr, e_arr, cpdf, override_col="Reading_mV")

    log_conc = np.log10(cpdf["Concentration"].astype(float).to_numpy())
    potential = np.array(readings, dtype=float)
    valid = ~np.isnan(potential)
    if valid.sum() < 2:
        return None

    lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
    nern = lod_fit["nernstian_segment"]
    if nern is None:
        return None
    x_valid = log_conc[valid]
    curve_x = np.linspace(float(x_valid.min()), float(x_valid.max()), 100)
    curve_y = nern["slope"] * curve_x + nern["intercept"]

    conc_unit, signal_unit = session.solid_conc_unit, session.solid_unit
    return {
        "x": x_valid.tolist(), "y": potential[valid].tolist(), "curve_x": curve_x.tolist(), "curve_y": curve_y.tolist(),
        "stats": {
            "File": frec["filename"], "Channel": ch["name"],
            f"Sensitivity ({signal_unit}/decade)": fmt(nern["slope"]),
            "R²": f"{nern['r2']:.4f}",
            f"LOD ({conc_unit})": fmt(lod_fit.get("lod_conc")),
        },
    }


@router.get("/comparison")
def comparison(session: SessionData = Depends(get_session)) -> dict:
    fig = go.Figure()
    stat_rows = []
    for j, frec in enumerate(session.solid_files):
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
        xaxis_title=f"log₁₀(Concentration [{session.solid_conc_unit}])",
        yaxis_title=f"Potential ({session.solid_unit})",
        hovermode="closest", height=480, template="plotly_white",
    )
    return {"figure": figure_json(fig) if stat_rows else None, "stats": stat_rows}


# -- Mode registration -----------------------------------------------------------
# Bundle keys match core/persistence.py's _build_session_bundle.
def _export_bundle(session: SessionData) -> dict:
    return {
        "solid_conc_unit": session.solid_conc_unit, "solid_unit": session.solid_unit,
        "solid_files": [{"filename": f["filename"], "csv": f["df"].to_csv(index=False), "channels": f["channels"],
                         "cpdf": f["cpdf"].to_dict(orient="records")} for f in session.solid_files],
    }


def _apply_bundle(session: SessionData, d: dict) -> None:
    for key in ("solid_conc_unit", "solid_unit"):
        if key in d:
            setattr(session, key, d[key])
    if "solid_files" in d:
        session.solid_files = [
            {"filename": f["filename"], "df": pd.read_csv(io.StringIO(f["csv"])),
             "channels": f["channels"], "cpdf": _solid_cpdf_from_records(f.get("cpdf"))}
            for f in d["solid_files"]
        ]
    session.solid_cal_results = None  # stale: the user re-runs Compute explicitly


def _summary(session: SessionData) -> str | None:
    n = len(session.solid_files)
    return f"{n} solid-state file{'s' if n != 1 else ''}" if n else None


def _report(session: SessionData) -> list:
    sections: list = [("table", "Files", [
        {"File": f["filename"], "Rows": len(f["df"]), "Channels": ", ".join(c["name"] for c in f["channels"])}
        for f in session.solid_files])]
    for f in session.solid_files:
        sections.append(("table", f"{f['filename']} — calibration table", df_records(f["cpdf"])))
    cr = session.solid_cal_results
    if not cr:
        sections.append(("note", "Calibration", "Not computed yet — run ③ Calibration first."))
        return sections
    sections.append(("image", "Calibration curve", render_solid_cal_png(cr["results"], session.solid_conc_unit,
                                                                        session.solid_unit, dpi=150)))
    _, stat_rows = _render_calibration_curve(cr["results"], session)
    sections.append(("table", "Sensor statistics", stat_rows))
    return sections


MODE = ModeSpec(
    id="solid_state", label="Solid-State", router=router, state=_state,
    export_bundle=_export_bundle, apply_bundle=_apply_bundle, summary=_summary,
    undo_fields=UNDO_FIELDS, scripts=("trace_mode.js",), report=_report,
)
