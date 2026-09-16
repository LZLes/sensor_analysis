"""
SessionStore — in-memory, cookie-keyed session state for web_app/.

One SessionData per browser session (a new tab with no cookie gets its own),
mirroring what macos_app/ui/app_state.py's AppState gave per window, without
needing Qt's window/undo-group machinery. No auth: the cookie is purely for
statefulness on a single-user localhost tool, not a security boundary.
Lost on server restart by design — Export/Import JSON (web_app/api/session.py)
is the durability mechanism, same as it already is for the Streamlit app.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

SESSION_COOKIE_NAME = "sensor_session"


@dataclass
class SessionData:
    """Mirrors macos_app/ui/app_state.py's SessionData fields (already a
    plain, Qt-independent dataclass) minus the assay_* fields — Assay isn't
    in scope for web_app/, and macos_app/ won't exist to round-trip them
    with once it's removed."""

    amp_files: list[dict] = field(default_factory=list)
    solid_files: list[dict] = field(default_factory=list)
    cv_runs: list[dict] = field(default_factory=list)
    cal_results: dict | None = None
    solid_cal_results: dict | None = None

    conc_unit: str = "mM"
    solid_conc_unit: str = "M"
    cur_unit: str = "µA"
    solid_unit: str = "mV"
    vol_unit: str = "µL"
    initial_volume: float = 1.0
    smooth_method: str = "None"
    smooth_window: int = 11
    smooth_polyorder: int = 2

    volt_unit: str = "V"
    cv_cur_unit: str = "µA"
    cv_sr_unit: str = "mV/s"

    # Per-file-list UI state (autodetect preview edges etc.), namespaced by
    # files_key the same way macos_app/ui/app_state.py's ts_ui was.
    ts_ui: dict[str, dict] = field(default_factory=dict)


class SessionStore:
    """Deliberately not thread-safe beyond dict's own atomicity — uvicorn's
    default single-worker/async model means requests interleave
    cooperatively, not across real threads, for this single-user tool."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, SessionData]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        new_id = secrets.token_urlsafe(24)
        data = SessionData()
        self._sessions[new_id] = data
        return new_id, data

    def replace(self, session_id: str, data: SessionData) -> None:
        self._sessions[session_id] = data


store = SessionStore()
