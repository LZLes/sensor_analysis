"""
Assay (96-well microplate) mode API — Import → Layout → Standard Curve →
Results → Normalise & Export. The maths lives in core/analysis/assay.py
(curve fit, back-calculation, QC) and core/analysis/assay_layout.py (layout
editing, grouping, normalisation); this module holds session handling,
figures and endpoints.

Plates. A session can hold several plates (web-only). The ACTIVE plate lives
in the flat SessionData fields that mirror the Streamlit app's keys
(assay_plate, assay_std_df, assay_sample_df, assay_std_res) plus the
web-only assay_excluded / assay_plate_name / assay_plate_id /
assay_std_source; `plates(session)` returns every plate with the active one
taken from those fields (without writing to the session, so the autosave
thread can call it safely); endpoints that change the list of plates store
it back with `_store_plates`. Every endpoint except Results/Normalise acts on
the active plate; those two pool all plates, grouping replicates by
Subject × Timepoint. A plate without standards can use another plate's
curve (assay_std_source); its own blank is subtracted when it has one.

Streamlit compatibility. The session bundle's flat assay_* keys describe the
active plate in the Streamlit schema (standards columns S1–S3 only), so the
Streamlit app opens it. The web-only keys assay_plates / assay_active /
assay_norm / assay_readout carry everything else; Streamlit ignores them.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core.analysis import assay_layout as lay
from core.analysis.assay import (  # noqa: F401  (re-exported for tests)
    _PLATE_ROWS,
    FIT_TYPES,
    READOUT_UNITS,
    SAMPLE_PLATE,
    _as_float_array,
    _as_float_matrix,
    _norm_well,
    _plate_get,
    blank_mean,
    compute_standard_curve,
    effective_plate,
    empty_plate,
    fit_equation,
    fit_eval,
    layout_problems,
    parse_plate_csv,
    readout_text,
    render_assay_curve,
    sample_map,
    sample_meta,
    sample_results,
    standards_table,
    std_wells_map,
    well_layout,
)
from core.constants import PAL, fmt
from core.serialization import _jsonify, _plate_df_from_csv, _plate_df_to_csv
from web_app import storage
from web_app.api.common import (
    ExportFmt,
    ExportStyle,
    df_records,
    export_media_type,
    figure_json,
    png_response,
    records_to_df,
)
from web_app.deps import get_session
from web_app.history import tracked
from web_app.modespec import ModeSpec
from web_app.session import (
    SessionData,
    default_assay_norm,
    default_assay_readout,
    default_assay_sample_df,
    default_assay_std_df,
)

router = APIRouter(prefix="/api/assay", tags=["assay"])

_SAMPLE_COLUMNS = lay.SAMPLE_COLUMNS
# The per-plate fields; together they describe the active plate.
PLATE_FIELDS = ("assay_plate", "assay_std_df", "assay_sample_df", "assay_std_res",
                "assay_excluded", "assay_plate_name", "assay_plate_id", "assay_std_source")
UNDO_FIELDS = PLATE_FIELDS + ("assay_plates", "assay_active", "assay_norm", "assay_readout",
                              "assay_sig_unit", "assay_conc_unit")


def _std_cols(std_df: pd.DataFrame) -> list[str]:
    return ["Label", "Conc", *lay.rep_cols(std_df)]


def _empty_std_df() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series([], dtype=object) for c in lay.STD_COLUMNS})


# -- plates --------------------------------------------------------------------------
def _active_index(session: SessionData) -> int:
    return min(max(session.assay_active, 0), max(len(session.assay_plates) - 1, 0))


def plates(session: SessionData) -> list[dict]:
    """Every plate, as dicts of PLATE_FIELDS: a new list whose active entry
    is built from the session fields. Read-only — never writes the session."""
    ps = list(session.assay_plates) or [{}]
    ps[_active_index(session)] = {f: getattr(session, f) for f in PLATE_FIELDS}
    return ps


def _store_plates(session: SessionData, ps: list[dict], active: int) -> None:
    """Make `ps` the session's plates and load plate `active` into the fields."""
    session.assay_plates = ps
    session.assay_active = active
    for f in PLATE_FIELDS:
        setattr(session, f, ps[active][f])


def _activate(session: SessionData, index: int) -> None:
    ps = plates(session)
    if not 0 <= index < len(ps):
        raise HTTPException(status_code=404, detail="No such plate.")
    _store_plates(session, ps, index)


def _plate_by_id(ps: list[dict], plate_id: str | None) -> dict | None:
    return next((p for p in ps if p["assay_plate_id"] == plate_id), None) if plate_id else None


def _curve_for(ps: list[dict], p: dict) -> tuple[dict | None, str | None]:
    """(standard-curve result, name of the plate it came from if borrowed)."""
    src = _plate_by_id(ps, p["assay_std_source"])
    if p["assay_std_source"]:
        return (src["assay_std_res"], src["assay_plate_name"]) if src else (None, None)
    return p["assay_std_res"], None


def _plate_rows(ps: list[dict], p: dict) -> tuple[list[dict], str | None]:
    """Per-well results for one plate, each row tagged with the plate name."""
    if p["assay_plate"] is None:
        return [], None
    res, src_name = _curve_for(ps, p)
    if res is None:
        return [], f"{p['assay_plate_name']}: no standard curve yet — it is left out of the results."
    sdf = p["assay_sample_df"]
    blank = blank_mean(effective_plate(p["assay_plate"], p["assay_excluded"]), p["assay_std_df"]) if src_name else None
    rows = sample_results(p["assay_plate"], res, sample_map(sdf), sample_meta(sdf), excluded=p["assay_excluded"],
                          std_wells=set(std_wells_map(p["assay_std_df"])), blank=blank)
    for r in rows:
        r["Plate"] = p["assay_plate_name"]
    return rows, None


def _all_rows(session: SessionData) -> tuple[list[dict], list[str]]:
    ps = plates(session)
    rows, notes = [], []
    for p in ps:
        rs, note = _plate_rows(ps, p)
        rows += rs
        if note and len(ps) > 1:
            notes.append(note)
    return rows, notes


def _plates_summary(session: SessionData) -> list[dict]:
    ps = plates(session)
    out = []
    for i, p in enumerate(ps):
        src = _plate_by_id(ps, p["assay_std_source"])
        out.append({"index": i, "id": p["assay_plate_id"], "name": p["assay_plate_name"],
                    "has_data": p["assay_plate"] is not None, "has_curve": _curve_for(ps, p)[0] is not None,
                    "own_curve": p["assay_std_res"] is not None, "std_source": p["assay_std_source"],
                    "source_name": src["assay_plate_name"] if src else None})
    return out


# -- figures -------------------------------------------------------------------------
_SET_COLS = {1: "rgba(70,130,220,0.85)", 2: "rgba(50,180,110,0.85)", 3: "rgba(220,80,80,0.85)"}
_BLANK_COL = "rgba(255,152,0,0.90)"
_SAMPLE_COL = "rgba(150,95,210,0.75)"
_DATA_COL = "rgba(120,130,145,0.55)"
_EMPTY_COL = "rgba(200,205,212,0.35)"
_EXCL_COL = "rgba(255,255,255,0.9)"


def plate_figure(plate: pd.DataFrame | None, std_wells: dict, samples: dict, conc_unit: str, sig_unit: str,
                 results: dict | None = None, excluded=()) -> go.Figure:
    """96-well plate map. With `results` ({well: result row}) the sample wells
    show back-calculated concentration instead of raw signal."""
    excluded = set(excluded)
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
            if well in excluded:
                col, text = _EXCL_COL, "excl."
                hov += "<br><i>Excluded from calculations</i>"
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
        for si in range(da.shape[1] if da.ndim == 2 else 0):
            ry = da[:, si]
            vr = np.isfinite(ry) & np.isfinite(cx)
            if vr.any():
                fig.add_trace(go.Scatter(x=cx[vr], y=ry[vr], name=f"Set {si + 1}", mode="markers",
                                         marker=dict(symbol="circle-open", size=9, color=PAL[si % len(PAL)],
                                                     line=dict(width=1.5))))
    fig.add_trace(go.Scatter(
        x=cx[vm], y=my[vm], name="Mean ± SD", mode="markers", text=labels[vm],
        hovertemplate="%{text}<br>%{x} " + conc_unit + "<br>ΔSignal %{y:.4g}<extra></extra>",
        marker=dict(size=11, color="#4c96d7", line=dict(width=1.5, color="white")),
        error_y=dict(type="data", array=np.nan_to_num(sy[vm]).tolist(), visible=True, color="#4c96d7", thickness=1.5, width=5),
    ))
    xp = np.linspace(max(0.0, float(cx[vm].min())), float(cx[vm].max()), 400)
    fig.add_trace(go.Scatter(x=xp, y=fit_eval(fit, xp), name="Fit", mode="lines",
                             line=dict(color="#ff9230", dash="dash", width=2.5)))
    qc = res.get("qc") or {}
    for key, name, dash in (("lod", "LOD", "dot"), ("loq", "LOQ", "dashdot")):
        if qc.get(key) is not None and qc[key] <= float(cx[vm].max()):
            fig.add_vline(x=qc[key], line=dict(color="#999", dash=dash, width=1),
                          annotation_text=name, annotation_position="top")
    fig.update_layout(
        xaxis_title=f"Concentration ({conc_unit})", yaxis_title=f"ΔSignal ({sig_unit})",
        height=480, template="plotly_white", hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        annotations=[*fig.layout.annotations,
                     dict(text=fit_equation(fit), xref="paper", yref="paper", x=0.02, y=0.98,
                          xanchor="left", yanchor="top", showarrow=False, font=dict(size=12),
                          bgcolor="rgba(255,255,255,0.85)", bordercolor="#999", borderwidth=1, borderpad=6)],
    )
    return fig


def _nan_none(v):
    return None if v is None or not np.isfinite(v) else float(v)


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


# -- state ---------------------------------------------------------------------------
def _plate_grid(plate: pd.DataFrame | None) -> list[list[float | None]] | None:
    if plate is None:
        return None
    return [[None if not np.isfinite(v) else float(v) for v in plate.loc[r].to_numpy(dtype=float)] for r in _PLATE_ROWS]


def _state(session: SessionData) -> dict:
    stds = std_wells_map(session.assay_std_df)
    samples = sample_map(session.assay_sample_df)
    plate = session.assay_plate
    ps = plates(session)
    res, src_name = _curve_for(ps, ps[_active_index(session)])
    sdf = lay.normalize_sample_df(session.assay_sample_df)
    all_sdf = [lay.normalize_sample_df(p["assay_sample_df"]) for p in ps]
    return {
        "sig_unit": session.assay_sig_unit,
        "conc_unit": session.assay_conc_unit,
        "readout": session.assay_readout,
        "readout_text": readout_text(session.assay_readout),
        "plates": _plates_summary(session),
        "active": _active_index(session),
        "wells": well_layout(plate, session.assay_std_df, session.assay_sample_df, session.assay_excluded),
        "excluded": list(session.assay_excluded),
        "subjects": lay.ordered_unique(x for d in all_sdf for x in d["Subject"] if x),
        "timepoints": lay.ordered_unique(x for d in all_sdf for x in d["Timepoint"] if x),
        "plate": _plate_grid(plate),
        "n_wells": int(plate.notna().sum().sum()) if plate is not None else 0,
        "rep_cols": lay.rep_cols(session.assay_std_df),
        "max_reps": lay.MAX_REPS,
        "std_rows": df_records(session.assay_std_df.reindex(columns=_std_cols(session.assay_std_df))),
        "sample_rows": df_records(sdf),
        "layout_problems": layout_problems(session.assay_std_df, session.assay_sample_df),
        "plate_figure": figure_json(plate_figure(plate, stds, samples, session.assay_conc_unit, session.assay_sig_unit,
                                                 excluded=session.assay_excluded)) if plate is not None else None,
        "has_result": any(p["has_curve"] for p in _plates_summary(session)),
        "has_curve": res is not None,
        "curve_source": src_name,
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
    session.assay_excluded = []


@router.post("/plate/upload")
@tracked("assay", UNDO_FIELDS)
async def upload_plate(file: UploadFile = File(...), session: SessionData = Depends(get_session)) -> dict:
    raw = await file.read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
    try:
        plate = parse_plate_csv(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse {file.filename}: {exc}") from exc
    _set_plate(session, plate)
    return _state(session)


def _demo_layout(session: SessionData) -> None:
    """The sample plate is laid out to match the default standards table;
    subjects P01–P05 (one per row D–H), timepoints D0–D14 in duplicate
    across columns 1–8."""
    session.assay_std_df = default_assay_std_df()
    wells = [f"{r}{c}" for r in "DEFGH" for c in range(1, 9)]
    plan, _ = lay.plan_samples(wells, lay.parse_name_list("P01-P05"), ["D0", "D3", "D7", "D14"], 2, "rows", "subject")
    _, session.assay_sample_df = lay.assign_samples(session.assay_std_df, default_assay_sample_df(), plan, wells)


@router.post("/plate/sample")
@tracked("assay", UNDO_FIELDS)
def load_sample(session: SessionData = Depends(get_session)) -> dict:
    if not os.path.exists(SAMPLE_PLATE):
        raise HTTPException(status_code=404, detail="Sample plate file is missing from sample_data/.")
    with open(SAMPLE_PLATE, encoding="utf-8") as fh:
        _set_plate(session, parse_plate_csv(fh.read()))
    session.assay_std_source = None
    _demo_layout(session)
    session.assay_norm = default_assay_norm()
    return _state(session)


class PlateBody(BaseModel):
    grid: list[list[float | None]]


@router.post("/plate")
@tracked("assay", UNDO_FIELDS)
def set_plate(body: PlateBody, session: SessionData = Depends(get_session)) -> dict:
    if len(body.grid) != 8 or any(len(r) != 12 for r in body.grid):
        raise HTTPException(status_code=400, detail="Plate grid must be 8 rows × 12 columns.")
    plate = empty_plate()
    plate.iloc[:, :] = np.array([[np.nan if v is None else v for v in r] for r in body.grid], dtype=float)
    _set_plate(session, plate)
    return _state(session)


@router.delete("/plate")
@tracked("assay", UNDO_FIELDS)
def clear_plate(session: SessionData = Depends(get_session)) -> dict:
    session.assay_plate = None
    session.assay_std_res = None
    session.assay_excluded = []
    return _state(session)


class UnitsBody(BaseModel):
    sig_unit: str | None = None
    conc_unit: str | None = None


@router.post("/units")
@tracked("assay", UNDO_FIELDS)
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
@tracked("assay", UNDO_FIELDS)
def set_readout(body: ReadoutBody, session: SessionData = Depends(get_session)) -> dict:
    """Changing the readout kind also switches the signal unit to its usual
    one (Abs/RFU/RLU) — unless the user had typed a custom unit."""
    old_unit = READOUT_UNITS.get(session.assay_readout.get("type"))
    if body.type != session.assay_readout.get("type") and body.type in READOUT_UNITS \
            and session.assay_sig_unit in (old_unit, *READOUT_UNITS.values()):
        session.assay_sig_unit = READOUT_UNITS[body.type]
    session.assay_readout = body.model_dump()
    return _state(session)


# -- Plates ----------------------------------------------------------------------------
class NewPlateBody(BaseModel):
    name: str = ""
    copy_layout: bool = True     # the current plate's standards and blank
    copy_samples: bool = False   # …and its sample labels (same samples → pooled as replicates)


class RenameBody(BaseModel):
    name: str


class CurveSourceBody(BaseModel):
    source: str | None = None   # plate id, or None for the plate's own standards


@router.post("/plates")
@tracked("assay", UNDO_FIELDS)
def add_plate(body: NewPlateBody, session: SessionData = Depends(get_session)) -> dict:
    """A new, empty plate (made active). copy_layout reuses the current
    plate's standards and blank; copy_samples also its sample labels — only
    right when the plate holds the same samples, since Results pools wells
    with the same subject and timepoint as replicates."""
    ps = plates(session)
    ids = {p["assay_plate_id"] for p in ps}
    names = {p["assay_plate_name"] for p in ps}
    n = len(ps) + 1
    while f"p{n}" in ids:
        n += 1
    name = body.name.strip()
    if not name:
        k = len(ps) + 1
        while f"Plate {k}" in names:
            k += 1
        name = f"Plate {k}"
    elif name in names:
        raise HTTPException(status_code=400, detail=f"Another plate is already called {name!r}.")
    std = session.assay_std_df.copy() if body.copy_layout else _empty_std_df()
    samples = session.assay_sample_df.copy() if body.copy_samples else default_assay_sample_df()
    ps.append({"assay_plate": None, "assay_std_df": std, "assay_sample_df": samples, "assay_std_res": None,
               "assay_excluded": [], "assay_plate_name": name, "assay_plate_id": f"p{n}", "assay_std_source": None})
    _store_plates(session, ps, len(ps) - 1)
    return _state(session)


@router.post("/plates/{index}/activate")
def activate_plate(index: int, session: SessionData = Depends(get_session)) -> dict:
    # Not an undo step: switching plates doesn't change any data.
    _activate(session, index)
    return _state(session)


@router.post("/plates/{index}/rename")
@tracked("assay", UNDO_FIELDS)
def rename_plate(index: int, body: RenameBody, session: SessionData = Depends(get_session)) -> dict:
    ps = plates(session)
    if not 0 <= index < len(ps):
        raise HTTPException(status_code=404, detail="No such plate.")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A plate name can't be empty.")
    if any(p["assay_plate_name"] == name for i, p in enumerate(ps) if i != index):
        raise HTTPException(status_code=400, detail=f"Another plate is already called {name!r}.")
    ps[index] = {**ps[index], "assay_plate_name": name}
    _store_plates(session, ps, _active_index(session))
    return _state(session)


@router.delete("/plates/{index}")
@tracked("assay", UNDO_FIELDS)
def delete_plate(index: int, session: SessionData = Depends(get_session)) -> dict:
    ps = plates(session)
    if not 0 <= index < len(ps):
        raise HTTPException(status_code=404, detail="No such plate.")
    if len(ps) == 1:
        raise HTTPException(status_code=400, detail="The last plate can't be removed — use Clear on ① Import instead.")
    gone = ps.pop(index)
    ps = [{**p, "assay_std_source": None} if p["assay_std_source"] == gone["assay_plate_id"] else p for p in ps]
    active = _active_index(session)
    if index < active:
        active -= 1
    elif index == active:
        active = min(index, len(ps) - 1)
    _store_plates(session, ps, active)
    return _state(session)


@router.post("/plates/curve-source")
@tracked("assay", UNDO_FIELDS)
def set_curve_source(body: CurveSourceBody, session: SessionData = Depends(get_session)) -> dict:
    """Use another plate's standard curve for the active plate (or its own)."""
    if body.source:
        ps = plates(session)
        src = _plate_by_id(ps, body.source)
        if src is None or src["assay_plate_id"] == session.assay_plate_id:
            raise HTTPException(status_code=400, detail="Choose another plate.")
        if src["assay_std_source"]:
            raise HTTPException(status_code=400, detail=f"{src['assay_plate_name']} itself borrows a curve; "
                                                        "choose the plate the standards were run on.")
        session.assay_std_res = None
    session.assay_std_source = body.source or None
    return _state(session)


# -- Standards / layout ------------------------------------------------------------------
class LayoutBody(BaseModel):
    std_rows: list[dict]
    sample_rows: list[dict]


@router.post("/layout")
@tracked("assay", UNDO_FIELDS)
def set_layout(body: LayoutBody, session: SessionData = Depends(get_session)) -> dict:
    n = max([int(k[1:]) for r in body.std_rows for k in r if re.fullmatch(r"S\d+", k)] + [3])
    if n > lay.MAX_REPS:
        raise HTTPException(status_code=400, detail=f"At most {lay.MAX_REPS} replicate columns are supported.")
    cols = lay.std_columns(n)
    reps = cols[2:]
    std = records_to_df(body.std_rows, cols, text_cols=("Label", *reps))
    for c in reps:
        std[c] = std[c].map(_norm_well)
    samples = records_to_df(body.sample_rows, _SAMPLE_COLUMNS, text_cols=tuple(_SAMPLE_COLUMNS))
    session.assay_std_df = lay.trim_reps(std)
    session.assay_sample_df = lay.normalize_sample_df(samples)
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
@tracked("assay", UNDO_FIELDS)
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
            note = ""
            if reps > 3:
                note = " — the Streamlit app reads only the first 3 replicates of each level."
            return {"preview": prev, "message": f"{len(levels)} level(s) × {reps} replicate(s): "
                    + ", ".join(lay.fmt_conc(c) for c in concs) + f" {session.assay_conc_unit}" + note}
        std, samples, msg = lay.assign_standards(session.assay_std_df, session.assay_sample_df, wells,
                                                 body.direction, concs)
    except ValueError as exc:
        if body.preview:
            return {"preview": [], "message": str(exc), "error": True}
        _bad_request(exc)
    session.assay_std_source = None
    return _set_layout(session, std, samples, msg)


@router.post("/assign/blank")
@tracked("assay", UNDO_FIELDS)
def assign_blank(body: WellsBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        std, samples, msg = lay.assign_blank(session.assay_std_df, session.assay_sample_df, body.wells)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, msg)


@router.post("/assign/samples")
@tracked("assay", UNDO_FIELDS)
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
@tracked("assay", UNDO_FIELDS)
def clear_assignment(body: WellsBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        std, samples = lay.clear_wells(session.assay_std_df, session.assay_sample_df, body.wells)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, f"Cleared {len(lay.clean_wells(body.wells))} well(s).")


@router.post("/layout/paste")
@tracked("assay", UNDO_FIELDS)
def paste_layout(body: PasteLayoutBody, session: SessionData = Depends(get_session)) -> dict:
    try:
        cells = lay.parse_layout_grid(body.text)
        std, samples, msg = lay.apply_layout_grid(session.assay_std_df, cells, body.sep)
    except ValueError as exc:
        _bad_request(exc)
    return _set_layout(session, std, samples, msg)


# -- Excluding wells ----------------------------------------------------------------------
class ExcludeBody(BaseModel):
    wells: list[str]
    excluded: bool = True


def _refit(session: SessionData) -> list[str]:
    """Refit the active plate's own curve with its current fit type (after
    the data under it changed). Returns warnings; drops the fit on failure."""
    res = session.assay_std_res
    if res is None or session.assay_plate is None:
        return []
    try:
        new, warnings = compute_standard_curve(effective_plate(session.assay_plate, session.assay_excluded),
                                               session.assay_std_df, res.get("fit_label") or "Linear")
    except ValueError as exc:
        session.assay_std_res = None
        return [f"The standard curve can't be fitted any more: {exc}"]
    session.assay_std_res = new
    return warnings


@router.post("/exclude")
@tracked("assay", UNDO_FIELDS)
def set_excluded(body: ExcludeBody, session: SessionData = Depends(get_session)) -> dict:
    """Leave wells out of (or put them back into) every calculation: the
    standard curve, the blank and the sample means. Refits the curve."""
    try:
        wells = set(lay.clean_wells(body.wells))
    except ValueError as exc:
        _bad_request(exc)
    current = set(session.assay_excluded)
    current = current | wells if body.excluded else current - wells
    session.assay_excluded = lay.sort_wells(list(current))
    warnings = _refit(session)
    verb = "Excluded" if body.excluded else "Included"
    msg = f"{verb} {', '.join(lay.sort_wells(list(wells)))}."
    if session.assay_std_res is not None:
        msg += " Standard curve refitted."
    return {**_state(session), "message": msg, "warnings": warnings}


# -- Standard curve ----------------------------------------------------------------------
class ComputeBody(BaseModel):
    fit_type: str = "Linear"
    show_reps: bool = True


def _qc_display(res: dict, cu: str) -> dict:
    qc = res.get("qc") or {}
    rec = [r for r in qc.get("recovery", []) if r is not None]
    return {
        "lod": f"{qc['lod']:.4g} {cu}" if qc.get("lod") is not None else "—",
        "loq": f"{qc['loq']:.4g} {cu}" if qc.get("loq") is not None else "—",
        "blank_sd": fmt(qc["blank_sd"]) if qc.get("blank_sd") is not None else "—",
        "n_blank": qc.get("n_blank", 0),
        "recovery_range": f"{min(rec):.0f}–{max(rec):.0f}%" if rec else "—",
        "note": ("LOD/LOQ need at least 2 blank replicates." if qc.get("blank_sd") is None
                 else "LOD/LOQ can't be read off this fit: it doesn't pass near the blank. Try another fit type."
                 if qc.get("lod") is None else ""),
    }


def _curve_payload(session: SessionData, res: dict, show_reps: bool, warnings: list[str] | None = None,
                   source: str | None = None) -> dict:
    su, cu = session.assay_sig_unit, session.assay_conc_unit
    ps = plates(session)
    src = _plate_by_id(ps, session.assay_std_source)
    excluded = src["assay_excluded"] if src else session.assay_excluded
    return {
        "warnings": warnings or [],
        "figure": figure_json(curve_figure(res, show_reps, cu, su)),
        "equation": fit_equation(res["fit"]),
        "fit_label": res.get("fit_label"),
        "blank_mean": res["blank_mean"],
        "qc": _qc_display(res, cu),
        "standards": standards_table(res, su, cu, excluded),
        "source": source,
    }


@router.post("/compute")
@tracked("assay", UNDO_FIELDS)
def compute(body: ComputeBody, session: SessionData = Depends(get_session)) -> dict:
    if session.assay_plate is None:
        raise HTTPException(status_code=400, detail="Import plate data first.")
    if session.assay_std_source:
        raise HTTPException(status_code=400, detail="This plate uses another plate's standard curve. Switch to "
                                                    "that plate to refit, or choose “Its own standards”.")
    try:
        res, warnings = compute_standard_curve(effective_plate(session.assay_plate, session.assay_excluded),
                                               session.assay_std_df, body.fit_type)
    except ValueError as exc:
        session.assay_std_res = None
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.assay_std_res = res
    return _curve_payload(session, res, body.show_reps, warnings)


@router.get("/curve")
def get_curve(show_reps: bool = True, session: SessionData = Depends(get_session)) -> dict:
    """Re-render the stored curve (tab revisit, units change, imported session)."""
    ps = plates(session)
    res, src_name = _curve_for(ps, ps[_active_index(session)])
    if res is None:
        return {"figure": None, "source": src_name}
    return _curve_payload(session, res, show_reps, source=src_name)


# -- Results -------------------------------------------------------------------------------
def _require_rows(session: SessionData) -> tuple[list[dict], list[str]]:
    if not any(p["has_curve"] and p["has_data"] for p in _plates_summary(session)):
        raise HTTPException(status_code=400, detail="Compute the standard curve first.")
    return _all_rows(session)


def _groups_from(rows: list[dict]) -> list[dict]:
    return lay.group_results([r for r in rows if r["Label"]])


def _summary_rows_display(groups: list[dict], cu: str) -> list[dict]:
    return [{"Subject": g["Subject"], "Timepoint": g["Timepoint"], "n": g["n"],
             f"Mean ({cu})": fmt(g["Mean"]), f"SD ({cu})": fmt(g["SD"]), "CV (%)": fmt(g["CV"], 3),
             "Wells": g["Wells"], "Flag": g["Flag"]} for g in groups]


@router.get("/results")
def results(session: SessionData = Depends(get_session)) -> dict:
    rows, notes = _require_rows(session)
    multi = len(plates(session)) > 1
    active_rows = [r for r in rows if r["Plate"] == session.assay_plate_name]
    fig = plate_figure(session.assay_plate, std_wells_map(session.assay_std_df), sample_map(session.assay_sample_df),
                       session.assay_conc_unit, session.assay_sig_unit, results={r["Well"]: r for r in active_rows},
                       excluded=session.assay_excluded) if session.assay_plate is not None else None
    used = [r for r in rows if not r["Excluded"]]
    n_flagged = sum(1 for r in used if r["Flag"])
    groups = _groups_from(rows)
    cu = session.assay_conc_unit
    n_excl = len(rows) - len(used)
    return {
        "rows": [{**({"Plate": r["Plate"]} if multi else {}), "Well": r["Well"], "Label": r["Label"],
                  "Subject": r["Subject"], "Timepoint": r["Timepoint"], "Signal": fmt(r["Signal"]),
                  "ΔSignal": fmt(r["ΔSignal"]), "Conc": fmt(r["Conc"]) if r["Conc"] is not None else "—",
                  "Flag": r["Flag"], "Excluded": r["Excluded"]} for r in rows],
        "summary": f"{len(used)} sample well(s) ({len(groups)} labelled sample(s)"
                   + (f" across {len({r['Plate'] for r in rows})} plates" if multi else "") + "); "
                   f"{n_flagged} well(s) flagged" + (f"; {n_excl} excluded" if n_excl else "") + ".",
        "notes": notes,
        "groups": _summary_rows_display(groups, cu),
        "group_figure": figure_json(summary_figure(groups, "Mean", "SD", f"Concentration ({cu})")) if groups else None,
        "figure": figure_json(fig) if fig else None,
        "multi_plate": multi, "active_plate": session.assay_plate_name,
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


def _normalised(session: SessionData) -> tuple[list[dict], list[dict], list[dict], dict]:
    """(groups, inputs, normalised rows, units) over all plates."""
    rows, _ = _require_rows(session)
    groups = _groups_from(rows)
    norm = session.assay_norm
    inputs = _norm_inputs(session, groups)
    out, units = lay.normalise(groups, inputs, session.assay_conc_unit, norm["vol_unit"], norm["area_unit"])
    return groups, inputs, out, units


def _norm_payload(session: SessionData) -> dict:
    groups, inputs, out, units = _normalised(session)
    norm = session.assay_norm
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
@tracked("assay", UNDO_FIELDS)
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
    rows, _ = _require_rows(session)
    if not rows:
        raise HTTPException(status_code=400, detail="No sample wells found (every well with data is a standard).")
    su, cu = session.assay_sig_unit, session.assay_conc_unit
    multi = len(plates(session)) > 1
    df = pd.DataFrame([{**({"Plate": r["Plate"]} if multi else {}), "Well": r["Well"], "Label": r["Label"],
                        "Subject": r["Subject"], "Timepoint": r["Timepoint"],
                        f"Signal ({su})": r["Signal"], f"ΔSignal ({su})": r["ΔSignal"], f"Conc ({cu})": r["Conc"],
                        "Flag": r["Flag"], "Excluded": r["Excluded"]} for r in rows])
    return _csv_response(df, "assay_results.csv", session)


def _groups(session: SessionData) -> list[dict]:
    groups = _groups_from(_require_rows(session)[0])
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
    _, _, out, units = _normalised(session)
    norm = session.assay_norm
    cu, au, pu = session.assay_conc_unit, units["amount"], units["per_area"]
    df = pd.DataFrame([{"Subject": r["Subject"], "Timepoint": r["Timepoint"], "n": r["n"], "Dilution": r["Dilution"],
                        f"Volume ({norm['vol_unit']})": r["Volume"], f"Area ({norm['area_unit']})": r["Area"],
                        f"Sample conc ({cu})": r["SampleConc"], f"SD ({cu})": r["SampleSD"],
                        f"Amount ({au})": r["Amount"], f"Amount SD ({au})": r["AmountSD"],
                        f"Per area ({pu})": r["PerArea"], f"Per area SD ({pu})": r["PerAreaSD"], "Flag": r["Flag"]}
                       for r in out])
    return _csv_response(df, "assay_normalised.csv", session)


def _active_curve(session: SessionData) -> dict:
    ps = plates(session)
    res, _ = _curve_for(ps, ps[_active_index(session)])
    if res is None:
        raise HTTPException(status_code=400, detail="Compute the standard curve first.")
    return res


@router.get("/export/standards-csv")
def export_standards_csv(session: SessionData = Depends(get_session)):
    res = _active_curve(session)
    su, cu = session.assay_sig_unit, session.assay_conc_unit
    qc = res.get("qc") or {}
    rec = qc.get("recovery") or []
    raw = res["raw_arr"]
    rows = [{
        "Label": res["labels"][i], f"Conc ({cu})": res["concs"][i],
        **{f"Set {s + 1} ({su})": raw[i][s] for s in range(len(raw[i]))},
        f"Mean Δ ({su})": res["means"][i], f"SD ({su})": res["sds"][i],
        "Recovery (%)": rec[i] if i < len(rec) else None,
    } for i in range(len(res["concs"]))]
    lim = "".join(f"# {name}: {qc[k]:.6g} {cu}\n" for k, name in (("lod", "LOD"), ("loq", "LOQ")) if qc.get(k) is not None)
    return _csv_response(pd.DataFrame(rows), "standard_curve_data.csv", session,
                         f"# Fit: {fit_equation(res['fit'])}\n# Blank mean: {res['blank_mean']:.6g} {su}\n" + lim)


class ExportCurveBody(BaseModel):
    fmt: ExportFmt = "png"
    dpi: int = Field(300, ge=50, le=1200)
    style: ExportStyle = "default"
    figsize: tuple[float, float] | None = None
    show_reps: bool = True


def _curve_png(res: dict, session: SessionData, show_reps: bool = True, **kw) -> bytes:
    # render_assay_curve expects plain float arrays (NaN, not None).
    mpl_res = {**res, "concs": _as_float_array(res["concs"]).tolist(),
               "means": _as_float_array(res["means"]).tolist(), "sds": _as_float_array(res["sds"]).tolist(),
               "delta_arr": _as_float_matrix(res["delta_arr"]).tolist()}
    return render_assay_curve(mpl_res, show_reps, session.assay_conc_unit, session.assay_sig_unit, **kw)


@router.post("/export/curve")
def export_curve(body: ExportCurveBody, session: SessionData = Depends(get_session)):
    res = _active_curve(session)
    content = _curve_png(res, session, body.show_reps, dpi=body.dpi, fmt=body.fmt, figsize=body.figsize,
                         style=body.style)
    return png_response(content, f"standard_curve.{body.fmt}", export_media_type(body.fmt))


# -- Layout templates ------------------------------------------------------------------------
# A template is the reusable part of an experiment: plate layout (standards
# and sample labels), readout, units and the normalisation units — stored as
# JSON files in the app's data folder (web_app/storage.py).
class TemplateBody(BaseModel):
    name: str


def _template_dir():
    return storage.subdir("assay_templates")


def _template_path(name: str):
    slug = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")[:80]
    if not slug:
        raise HTTPException(status_code=400, detail="Give the template a name.")
    return _template_dir() / f"{slug}.json"


def _template_payload(session: SessionData, name: str) -> dict:
    return {
        "kind": "assay-layout-template", "version": 1, "name": name, "saved_at": time.strftime("%Y-%m-%d %H:%M"),
        "std_df": _jsonify(session.assay_std_df.reindex(columns=_std_cols(session.assay_std_df)).to_dict(orient="records")),
        "sample_df": _jsonify(lay.normalize_sample_df(session.assay_sample_df).to_dict(orient="records")),
        "readout": dict(session.assay_readout), "sig_unit": session.assay_sig_unit,
        "conc_unit": session.assay_conc_unit, "area_unit": session.assay_norm.get("area_unit", "cm²"),
        "vol_unit": session.assay_norm.get("vol_unit", "µL"),
        "fit_type": (session.assay_std_res or {}).get("fit_label"),
    }


def _read_template(path) -> dict:
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not read template: {exc}") from exc
    if not isinstance(t, dict) or t.get("kind") != "assay-layout-template":
        raise HTTPException(status_code=400, detail="Not an assay layout template.")
    return t


@router.get("/templates")
def list_templates() -> dict:
    out = []
    for path in sorted(_template_dir().glob("*.json")):
        try:
            t = _read_template(path)
        except HTTPException:
            continue
        out.append({"id": path.stem, "name": t.get("name") or path.stem, "saved_at": t.get("saved_at", ""),
                    "n_levels": len(t.get("std_df") or []), "n_samples": len(t.get("sample_df") or [])})
    return {"templates": out}


@router.post("/templates")
def save_template(body: TemplateBody, session: SessionData = Depends(get_session)) -> dict:
    name = body.name.strip()
    storage.write_json_atomic(_template_path(name), _template_payload(session, name))
    return {**list_templates(), "message": f"Saved template “{name}”."}


def _apply_template(session: SessionData, t: dict) -> None:
    rows = t.get("std_df") or []
    n = max([int(k[1:]) for r in rows for k in r if re.fullmatch(r"S\d+", k)] + [3])
    cols = lay.std_columns(min(n, lay.MAX_REPS))
    std = records_to_df(rows, cols, text_cols=("Label", *cols[2:]))
    session.assay_std_df = lay.trim_reps(std) if rows else _empty_std_df()
    session.assay_sample_df = lay.normalize_sample_df(pd.DataFrame(t.get("sample_df") or [], columns=_SAMPLE_COLUMNS))
    session.assay_std_res = None
    session.assay_std_source = None
    session.assay_excluded = []
    if isinstance(t.get("readout"), dict):
        session.assay_readout = {**default_assay_readout(), **t["readout"]}
    for key, attr in (("sig_unit", "assay_sig_unit"), ("conc_unit", "assay_conc_unit")):
        if t.get(key):
            setattr(session, attr, str(t[key]))
    session.assay_norm = {**session.assay_norm, **{k: str(t[k]) for k in ("area_unit", "vol_unit") if t.get(k)}}


@router.post("/templates/apply")
@tracked("assay", UNDO_FIELDS)
def apply_template(body: TemplateBody, session: SessionData = Depends(get_session)) -> dict:
    path = _template_path(body.name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No template called {body.name!r}.")
    t = _read_template(path)
    _apply_template(session, t)
    msg = f"Applied template “{t.get('name', body.name)}” to {session.assay_plate_name}."
    if t.get("fit_type"):
        msg += f" It was fitted with {t['fit_type']}."
    return {**_state(session), "message": msg, "fit_type": t.get("fit_type")}


@router.delete("/templates/{name}")
def delete_template(name: str) -> dict:
    _template_path(name).unlink(missing_ok=True)
    return list_templates()


@router.get("/templates/{name}/download")
def download_template(name: str):
    path = _template_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No template called {name!r}.")
    return png_response(path.read_bytes(), path.name, "application/json")


@router.post("/templates/upload")
async def upload_template(file: UploadFile = File(...)) -> dict:
    try:
        t = json.loads(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not read template: {exc}") from exc
    if not isinstance(t, dict) or t.get("kind") != "assay-layout-template":
        raise HTTPException(status_code=400, detail="Not an assay layout template.")
    name = str(t.get("name") or os.path.splitext(file.filename or "template")[0])
    storage.write_json_atomic(_template_path(name), {**t, "name": name})
    return {**list_templates(), "message": f"Added template “{name}”."}


# -- Mode registration ---------------------------------------------------------------------
def _plate_to_json(p: dict) -> dict:
    return {"id": p["assay_plate_id"], "name": p["assay_plate_name"], "plate": _plate_df_to_csv(p["assay_plate"]),
            "std_df": _jsonify(p["assay_std_df"].reindex(columns=_std_cols(p["assay_std_df"])).to_dict(orient="records")),
            "sample_df": _jsonify(lay.normalize_sample_df(p["assay_sample_df"]).to_dict(orient="records")),
            "std_res": _jsonify(p["assay_std_res"]), "excluded": list(p["assay_excluded"]),
            "std_source": p["assay_std_source"]}


def _plate_from_json(d: dict, i: int) -> dict:
    if not isinstance(d, dict):
        raise ValueError("assay_plates entries must be objects")
    std_rows = d.get("std_df")
    std = pd.DataFrame(std_rows) if std_rows else _empty_std_df()
    return {"assay_plate": _plate_df_from_csv(d.get("plate")), "assay_std_df": std,
            "assay_sample_df": lay.normalize_sample_df(pd.DataFrame(d.get("sample_df") or [], columns=_SAMPLE_COLUMNS)),
            "assay_std_res": d.get("std_res"), "assay_excluded": [str(w) for w in d.get("excluded") or []],
            "assay_plate_name": str(d.get("name") or f"Plate {i + 1}"), "assay_plate_id": str(d.get("id") or f"p{i + 1}"),
            "assay_std_source": d.get("std_source") or None}


def _export_bundle(session: SessionData) -> dict:
    ps = plates(session)
    std = session.assay_std_df
    # The flat keys are what the Streamlit app reads: the active plate, with
    # standards limited to its S1–S3 columns. A fit that used more replicates
    # or another plate's standards can't be expressed there, so it is left
    # for Streamlit to recompute.
    portable_fit = len(lay.rep_cols(std)) <= 3 and not session.assay_std_source
    return {
        "assay_sig_unit": session.assay_sig_unit, "assay_conc_unit": session.assay_conc_unit,
        "assay_plate": _plate_df_to_csv(session.assay_plate),
        # Never empty: Streamlit builds its table from these records and
        # needs the columns (an empty list gives it a table without them).
        "assay_std_df": _jsonify(std.reindex(columns=lay.STD_COLUMNS).to_dict(orient="records"))
        or [{"Label": "Blank", "Conc": 0.0, "S1": "", "S2": "", "S3": ""}],
        "assay_sample_df": _jsonify(session.assay_sample_df.to_dict(orient="records")),
        "assay_std_res": _jsonify(session.assay_std_res) if portable_fit else None,
        # Web-only extras: the Streamlit app ignores keys it doesn't know.
        "assay_norm": _jsonify(session.assay_norm),
        "assay_readout": dict(session.assay_readout),
        "assay_plates": [_plate_to_json(p) for p in ps],
        "assay_active": _active_index(session),
    }


def _apply_bundle(session: SessionData, d: dict) -> None:
    for key in ("assay_sig_unit", "assay_conc_unit"):
        if key in d:
            setattr(session, key, d[key])
    if d.get("assay_plates"):
        if not isinstance(d["assay_plates"], list):
            raise ValueError("assay_plates must be a list")
        ps = [_plate_from_json(p, i) for i, p in enumerate(d["assay_plates"])]
        _store_plates(session, ps, min(max(int(d.get("assay_active") or 0), 0), len(ps) - 1))
    else:
        # A Streamlit bundle (or an older web one): a single plate.
        if "assay_plate" in d:
            session.assay_plate = _plate_df_from_csv(d["assay_plate"])
        if "assay_std_df" in d:
            session.assay_std_df = pd.DataFrame(d["assay_std_df"]) if d["assay_std_df"] else default_assay_std_df()
        if "assay_sample_df" in d:
            session.assay_sample_df = (pd.DataFrame(d["assay_sample_df"]) if d["assay_sample_df"]
                                       else default_assay_sample_df())
        if "assay_std_res" in d:
            session.assay_std_res = d["assay_std_res"]
        if "assay_plate" in d:
            session.assay_plates, session.assay_active = [], 0
            session.assay_excluded, session.assay_std_source = [], None
            session.assay_plate_name, session.assay_plate_id = "Plate 1", "p1"
    if "assay_plate" in d or "assay_norm" in d:
        norm = d.get("assay_norm") or {}
        session.assay_norm = {**default_assay_norm(), **{k: norm[k] for k in ("area_unit", "vol_unit", "rows") if k in norm}}
        if not isinstance(session.assay_norm["rows"], list):
            raise ValueError("assay_norm.rows must be a list")
    if "assay_plate" in d or "assay_readout" in d:
        session.assay_readout = {**default_assay_readout(), **(d.get("assay_readout") or {})}


def _summary(session: SessionData) -> str | None:
    n = sum(1 for p in plates(session) if p["assay_plate"] is not None)
    return f"{n} assay plate{'s' if n != 1 else ''}" if n else None


def _layout_grid_table(p: dict) -> list[dict]:
    """The plate layout as an 8 × 12 table of short labels, for the report."""
    stds = std_wells_map(p["assay_std_df"])
    meta = sample_meta(p["assay_sample_df"])
    excluded = set(p["assay_excluded"])
    rows = []
    for r in _PLATE_ROWS:
        row = {"": r}
        for c in range(1, 13):
            w = f"{r}{c}"
            if w in stds:
                info = stds[w]
                text = "Blank" if info["is_blank"] else f"Std {info['conc']:g}" if info["conc"] is not None else "Std"
            elif w in meta:
                text = meta[w]["Label"] or w
            else:
                text = "·" if np.isfinite(_plate_get(p["assay_plate"], w)) else ""
            row[str(c)] = text + (" ✕" if w in excluded else "")
        rows.append(row)
    return rows


def _report(session: SessionData) -> list:
    ps = plates(session)
    cu, su = session.assay_conc_unit, session.assay_sig_unit
    sections: list = [("kv", "Assay", {"Readout": readout_text(session.assay_readout), "Signal unit": su,
                                        "Concentration unit": cu, "Plates": ", ".join(p["assay_plate_name"] for p in ps)})]
    for p in ps:
        if p["assay_plate"] is None:
            continue
        title = p["assay_plate_name"]
        sections.append(("table", f"{title} — layout", _layout_grid_table(p)))
        res, src = _curve_for(ps, p)
        if res is None:
            sections.append(("note", f"{title} — standard curve", "Not fitted yet."))
            continue
        if src:
            sections.append(("note", f"{title} — standard curve", f"Uses the standard curve from {src}."))
            continue
        q = _qc_display(res, cu)
        sections.append(("image", f"{title} — standard curve", _curve_png(res, session, dpi=150)))
        sections.append(("kv", f"{title} — fit and QC", {
            "Fit": res.get("fit_label") or "", "Equation": fit_equation(res["fit"]),
            "Blank mean": f"{res['blank_mean']:.4g} {su}", "Blank SD": f"{q['blank_sd']} (n = {q['n_blank']})",
            "LOD (3 SD)": q["lod"], "LOQ (10 SD)": q["loq"], "Recovery of standards": q["recovery_range"],
            "Excluded wells": ", ".join(p["assay_excluded"]) or "none"}))
        sections.append(("table", f"{title} — standards", standards_table(res, su, cu, p["assay_excluded"])))
    try:
        groups, _, out, units = _normalised(session)
    except HTTPException:
        return sections
    if groups:
        sections.append(("table", "Results per sample", _summary_rows_display(groups, cu)))
    if any(np.isfinite(r["PerArea"]) for r in out):
        sections.append(("table", "Normalised", [
            {"Subject": r["Subject"], "Timepoint": r["Timepoint"], "n": r["n"], "Dilution": fmt(r["Dilution"]),
             f"Sample conc ({cu})": fmt(r["SampleConc"]), f"Amount ({units['amount']})": fmt(r["Amount"]),
             f"Per area ({units['per_area']})": fmt(r["PerArea"]), f"SD ({units['per_area']})": fmt(r["PerAreaSD"]),
             "Flag": r["Flag"]} for r in out]))
    return sections


MODE = ModeSpec(
    id="assay", label="Assay (Microplate)", router=router, state=_state,
    export_bundle=_export_bundle, apply_bundle=_apply_bundle, summary=_summary,
    undo_fields=UNDO_FIELDS, report=_report,
)
