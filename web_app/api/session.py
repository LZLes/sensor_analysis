"""
Tier 2 persistence (Export/Import JSON) — reuses core/persistence.py's
bundle SHAPE (same keys/nesting _build_session_bundle/_apply_session_bundle
use) so a session exported here opens in the Streamlit app, and vice versa.
core/persistence.py's own functions can't be called directly — they read
and write st.session_state via `SS = st.session_state`, the exact
Streamlit-coupling problem macos_app/persistence.py already solved the
same way for the macOS app: only the pure pieces
(_cpdf_from_records/_solid_cpdf_from_records from core/calibration_table.py)
are reused; the dict-building/applying logic is reimplemented here against
SessionData instead of SS.

Assay isn't in scope for web_app/ (see session.py's SessionData docstring)
— exporting simply omits assay_* keys (core/persistence.py's own
_apply_session_bundle guards every key with `if "..." in d`, so importing
this bundle into the Streamlit app just leaves Assay untouched), and
importing a Streamlit-exported bundle that does have assay_* keys just
ignores them.
"""

from __future__ import annotations

import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from core.calibration_table import _cpdf_from_records, _solid_cpdf_from_records
from web_app.deps import get_session
from web_app.session import SessionData

router = APIRouter(prefix="/api/session", tags=["session"])

_UNIT_FIELDS = [
    "conc_unit", "solid_conc_unit", "cur_unit", "solid_unit",
    "volt_unit", "cv_cur_unit", "cv_sr_unit", "vol_unit",
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
    return d


@router.get("/export")
def export_session(session: SessionData = Depends(get_session)) -> Response:
    bundle = _build_bundle(session)
    return Response(
        content=json.dumps(bundle),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="session.json"'},
    )


@router.post("/import")
async def import_session(file: UploadFile = File(...), session: SessionData = Depends(get_session)) -> dict:
    raw = await file.read()
    try:
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not parse session file: {exc}") from exc

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

    # Stale results tied to the previous file set — the user re-runs
    # Compute Calibration explicitly, same "compute on explicit action"
    # model the rest of this app uses.
    session.cal_results = None
    session.solid_cal_results = None

    return {"amp_files": len(session.amp_files), "solid_files": len(session.solid_files), "cv_runs": len(session.cv_runs)}
