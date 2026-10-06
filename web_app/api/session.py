"""
Tier 2 persistence (Export/Import JSON) — same bundle SHAPE as
core/persistence.py's _build_session_bundle/_apply_session_bundle (same keys
and nesting), so a session exported here opens in the Streamlit app and vice
versa, for all four modes. core/persistence.py's own functions can't be
called directly since they read/write st.session_state.

Each mode writes and reads its own keys (ModeSpec.export_bundle /
apply_bundle, in web_app/api/<mode>.py); this module adds the settings
shared across modes and does the file handling. The autosave
(web_app/autosave.py) uses the same bundle.
"""

from __future__ import annotations

import copy
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from web_app.deps import get_session
from web_app.registry import MODES
from web_app.session import SessionData

router = APIRouter(prefix="/api/session", tags=["session"])

# Settings shared by the trace modes (Amperometry and Solid-State).
_COMMON_FIELDS = {"smooth_method": str, "smooth_window": int, "smooth_polyorder": int}


def build_bundle(session: SessionData) -> dict:
    d = {field: getattr(session, field) for field in _COMMON_FIELDS}
    for mode in MODES:
        d.update(mode.export_bundle(session))
    return d


def apply_bundle(session: SessionData, d: dict) -> None:
    for field, cast in _COMMON_FIELDS.items():
        if field in d:
            setattr(session, field, cast(d[field]))
    for mode in MODES:
        mode.apply_bundle(session, d)
    # Autodetect previews belong to the previous file set.
    session.ts_ui = {}


def import_into(session: SessionData, d: dict) -> list[str]:
    """Apply a bundle to a copy and swap it in only on success, so a
    malformed/partial bundle can't leave the live session half-overwritten.
    Clears undo history. Returns what was loaded, one phrase per mode."""
    if not isinstance(d, dict):
        raise HTTPException(status_code=400, detail="Not a session file (expected a JSON object).")
    history, session.history = session.history, {}
    try:
        staged = copy.deepcopy(session)
    finally:
        session.history = history
    try:
        apply_bundle(staged, d)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Session file is incomplete or malformed: {exc!r}") from exc
    session.__dict__.update(staged.__dict__)
    return summaries(session)


def summaries(session: SessionData) -> list[str]:
    return [s for s in (mode.summary(session) for mode in MODES) if s]


@router.get("/export")
def export_session(session: SessionData = Depends(get_session)) -> Response:
    return Response(
        content=json.dumps(build_bundle(session)),
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
    loaded = import_into(session, d)
    return {"loaded": loaded,
            # Counts kept for callers of the original API.
            "amp_files": len(session.amp_files), "solid_files": len(session.solid_files),
            "cv_runs": len(session.cv_runs), "assay_plate": session.assay_plate is not None}


@router.get("/ping")
def ping(session: SessionData = Depends(get_session)) -> dict:
    """Called once on page load before any mode initializes, so the session
    cookie exists before the modes fire their parallel /state requests."""
    return {"ok": True, "empty": not summaries(session)}
