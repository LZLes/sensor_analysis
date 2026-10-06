"""Endpoints of the app shell that work the same for every mode, driven by
the registry: the mode list the page boots from, undo/redo, printable
reports and autosave restore."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from web_app import autosave, history, report
from web_app.api.session import import_into, summaries
from web_app.deps import get_session
from web_app.modespec import ModeSpec
from web_app.registry import MODES, get_mode
from web_app.session import SessionData

router = APIRouter(tags=["shell"])


def _mode(mode_id: str) -> ModeSpec:
    mode = get_mode(mode_id)
    if mode is None:
        raise HTTPException(status_code=404, detail=f"Unknown mode {mode_id!r}.")
    return mode


@router.get("/api/app/modes")
def list_modes() -> dict:
    return {"modes": [m.public() for m in MODES]}


# -- Undo / redo ------------------------------------------------------------------------
@router.get("/api/history/{mode_id}")
def history_counts(mode_id: str, session: SessionData = Depends(get_session)) -> dict:
    _mode(mode_id)
    undo, redo = history.counts(session, mode_id)
    return {"undo": undo, "redo": redo}


@router.post("/api/history/{mode_id}/{direction}")
def history_step(mode_id: str, direction: str, session: SessionData = Depends(get_session)) -> dict:
    mode = _mode(mode_id)
    if direction not in ("undo", "redo"):
        raise HTTPException(status_code=404, detail="Use undo or redo.")
    if not mode.undo_fields or not history.step(session, mode_id, mode.undo_fields, direction):
        raise HTTPException(status_code=400, detail=f"Nothing to {direction}.")
    return mode.state(session)


# -- Report -------------------------------------------------------------------------------
@router.get("/api/report/{mode_id}", response_class=HTMLResponse)
def mode_report(mode_id: str, session: SessionData = Depends(get_session)) -> HTMLResponse:
    mode = _mode(mode_id)
    if mode.report is None:
        raise HTTPException(status_code=404, detail=f"{mode.label} has no report.")
    return HTMLResponse(report.render(mode.label, mode.report(session)))


# -- Autosave -------------------------------------------------------------------------------
@router.get("/api/autosave")
def autosave_info(session: SessionData = Depends(get_session)) -> dict:
    """The previous run's autosave, offered only while this session is empty."""
    info = autosave.previous_info()
    if info is None or summaries(session):
        return {"available": False}
    return {"available": True, "saved_at": info.get("saved_at", ""), "summary": info.get("summary", [])}


@router.post("/api/autosave/restore")
def autosave_restore(session: SessionData = Depends(get_session)) -> dict:
    info = autosave.previous_info()
    if info is None:
        raise HTTPException(status_code=404, detail="No previous session to restore.")
    loaded = import_into(session, info.get("bundle") or {})
    autosave.previous_path().unlink(missing_ok=True)
    return {"loaded": loaded}


@router.post("/api/autosave/discard")
def autosave_discard() -> dict:
    autosave.previous_path().unlink(missing_ok=True)
    return {"ok": True}
