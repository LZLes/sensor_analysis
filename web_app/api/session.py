"""
Tier 2 persistence (Export/Import JSON) — same bundle SHAPE as
core/persistence.py's _build_session_bundle/_apply_session_bundle (same keys
and nesting), so a session exported here opens in the Streamlit app and vice
versa, for all four modes. core/persistence.py's own functions can't be
called directly since they read/write st.session_state; only its pure pieces
(_jsonify, _plate_df_to_csv/_plate_df_from_csv) and core/calibration_table.py's
record parsers are reused, with the dict-building/applying done against
SessionData instead.
"""

from __future__ import annotations

import copy
import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from core.calibration_table import _cpdf_from_records, _solid_cpdf_from_records
from core.persistence import _jsonify, _plate_df_from_csv, _plate_df_to_csv
from web_app.deps import get_session
from web_app.session import (SessionData, default_assay_norm, default_assay_readout, default_assay_sample_df,
                              default_assay_std_df)

router = APIRouter(prefix="/api/session", tags=["session"])

_UNIT_FIELDS = [
    "conc_unit", "solid_conc_unit", "cur_unit", "solid_unit",
    "volt_unit", "cv_cur_unit", "cv_sr_unit", "vol_unit",
    "assay_sig_unit", "assay_conc_unit",
]


def _build_bundle(session: SessionData) -> dict:
    d = {field: getattr(session, field) for field in _UNIT_FIELDS}
    d["initial_volume"] = session.initial_volume
    d["smooth_method"] = session.smooth_method
    d["smooth_window"] = session.smooth_window
    d["smooth_polyorder"] = session.smooth_polyorder

    d["amp_files"] = [
        {"filename": f["filename"], "csv": f["df"].to_csv(index=False), "channels": f["channels"],
         "cpdf": f["cpdf"].to_dict(orient="records")}
        for f in session.amp_files
    ]
    d["solid_files"] = [
        {"filename": f["filename"], "csv": f["df"].to_csv(index=False), "channels": f["channels"],
         "cpdf": f["cpdf"].to_dict(orient="records")}
        for f in session.solid_files
    ]
    d["cv_runs"] = [
        {"scan_rate": r["scan_rate"], "label": r["label"], "filename": r["filename"],
         "csv": r["df"].to_csv(index=False), "channels": r["channels"], "peaks": r["peaks"]}
        for r in session.cv_runs
    ]
    d["assay_plate"] = _plate_df_to_csv(session.assay_plate)
    d["assay_std_df"] = _jsonify(session.assay_std_df.to_dict(orient="records"))
    d["assay_sample_df"] = _jsonify(session.assay_sample_df.to_dict(orient="records"))
    d["assay_std_res"] = _jsonify(session.assay_std_res)
    # Web-only extras: the Streamlit app ignores keys it doesn't know.
    d["assay_norm"] = _jsonify(session.assay_norm)
    d["assay_readout"] = dict(session.assay_readout)
    return d


@router.get("/export")
def export_session(session: SessionData = Depends(get_session)) -> Response:
    bundle = _build_bundle(session)
    return Response(
        content=json.dumps(bundle),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="session.json"'},
    )


def _apply_bundle(session: SessionData, d: dict) -> None:
    for field in _UNIT_FIELDS:
        if field in d:
            setattr(session, field, d[field])
    if "initial_volume" in d:
        session.initial_volume = float(d["initial_volume"])
    if "smooth_method" in d:
        session.smooth_method = d["smooth_method"]
    if "smooth_window" in d:
        session.smooth_window = int(d["smooth_window"])
    if "smooth_polyorder" in d:
        session.smooth_polyorder = int(d["smooth_polyorder"])

    if "amp_files" in d:
        session.amp_files = [
            {"filename": f["filename"], "df": pd.read_csv(io.StringIO(f["csv"])),
             "channels": f["channels"], "cpdf": _cpdf_from_records(f.get("cpdf"))}
            for f in d["amp_files"]
        ]
    if "solid_files" in d:
        session.solid_files = [
            {"filename": f["filename"], "df": pd.read_csv(io.StringIO(f["csv"])),
             "channels": f["channels"], "cpdf": _solid_cpdf_from_records(f.get("cpdf"))}
            for f in d["solid_files"]
        ]
    if "cv_runs" in d:
        session.cv_runs = [
            {"scan_rate": float(r["scan_rate"]), "label": r["label"], "filename": r["filename"],
             "df": pd.read_csv(io.StringIO(r["csv"])), "channels": r["channels"], "peaks": r.get("peaks", {})}
            for r in d["cv_runs"]
        ]

    if "assay_plate" in d:
        session.assay_plate = _plate_df_from_csv(d["assay_plate"])
    if "assay_std_df" in d:
        session.assay_std_df = pd.DataFrame(d["assay_std_df"]) if d["assay_std_df"] else default_assay_std_df()
    if "assay_sample_df" in d:
        session.assay_sample_df = pd.DataFrame(d["assay_sample_df"]) if d["assay_sample_df"] else default_assay_sample_df()
    if "assay_std_res" in d:
        session.assay_std_res = d["assay_std_res"]
    if "assay_plate" in d or "assay_norm" in d:
        norm = d.get("assay_norm") or {}
        session.assay_norm = {**default_assay_norm(), **{k: norm[k] for k in ("area_unit", "vol_unit", "rows") if k in norm}}
        if not isinstance(session.assay_norm["rows"], list):
            raise ValueError("assay_norm.rows must be a list")
    if "assay_plate" in d or "assay_readout" in d:
        session.assay_readout = {**default_assay_readout(), **(d.get("assay_readout") or {})}

    # Autodetect previews belong to the previous file set.
    session.ts_ui = {}

    # Stale results tied to the previous file set — the user re-runs
    # Compute Calibration explicitly, same "compute on explicit action"
    # model the rest of this app uses.
    session.cal_results = None
    session.solid_cal_results = None


@router.post("/import")
async def import_session(file: UploadFile = File(...), session: SessionData = Depends(get_session)) -> dict:
    raw = await file.read()
    try:
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not parse session file: {exc}") from exc

    if not isinstance(d, dict):
        raise HTTPException(status_code=400, detail="Not a session file (expected a JSON object).")

    # Apply to a copy and swap in only on success, so a malformed/partial
    # bundle can't leave the live session half-overwritten.
    staged = copy.deepcopy(session)
    try:
        _apply_bundle(staged, d)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Session file is incomplete or malformed: {exc!r}") from exc
    session.__dict__.update(staged.__dict__)

    return {"amp_files": len(session.amp_files), "solid_files": len(session.solid_files),
            "cv_runs": len(session.cv_runs), "assay_plate": session.assay_plate is not None}


@router.get("/ping")
def ping(session: SessionData = Depends(get_session)) -> dict:
    """Called once on page load before any mode initializes, so the session
    cookie exists before the modes fire their parallel /state requests."""
    return {"ok": True}
