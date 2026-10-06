"""
SessionStore — in-memory, cookie-keyed session state for web_app/.

One SessionData per browser session (a new browser with no cookie gets its
own). No auth: the cookie is purely for statefulness on a single-user
localhost tool, not a security boundary. Lost on server restart by design —
Export/Import JSON (web_app/api/session.py) is the durability mechanism,
same as it already is for the Streamlit app.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

SESSION_COOKIE_NAME = "sensor_session"

# Cookie ids we mint are token_urlsafe(24) — anything else the browser sends
# is ignored and a fresh id is issued instead.
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def default_assay_std_df() -> pd.DataFrame:
    """Same default standards layout core/state.py seeds the Streamlit app with."""
    return pd.DataFrame({
        "Label": ["Blank", "Std 2", "Std 3", "Std 4", "Std 5", "Std 6", "Std 7", "Std 8"],
        "Conc": [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        "S1": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
        "S2": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"],
        "S3": ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
    })


def default_assay_sample_df() -> pd.DataFrame:
    # Streamlit's default has only Well/Label; Subject/Timepoint are extra
    # columns it carries along untouched (see core/analysis/assay_layout.py).
    return pd.DataFrame({c: pd.Series([], dtype=str) for c in ("Well", "Label", "Subject", "Timepoint")})


def default_assay_norm() -> dict:
    """Web-only normalisation inputs (per sample dilution/volume/area)."""
    return {"area_unit": "cm²", "vol_unit": "µL", "rows": []}


def default_assay_readout() -> dict:
    """Web-only description of how the plate was read; shown in exports."""
    return {"type": "absorbance", "wavelength": "450", "ex": "", "em": ""}


@dataclass
class SessionData:
    """Field names mirror core/state.py's session-state keys so the
    Export/Import JSON bundle stays byte-compatible with the Streamlit app."""

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

    # Assay (96-well microplate)
    assay_plate: pd.DataFrame | None = None
    assay_sig_unit: str = "Abs"
    assay_conc_unit: str = "µM"
    assay_std_df: pd.DataFrame = field(default_factory=default_assay_std_df)
    assay_sample_df: pd.DataFrame = field(default_factory=default_assay_sample_df)
    assay_std_res: dict | None = None
    assay_norm: dict = field(default_factory=default_assay_norm)
    assay_readout: dict = field(default_factory=default_assay_readout)
    # Web-only, per plate (the fields above from assay_plate to assay_std_res
    # plus these three describe the ACTIVE plate; see web_app/api/assay.py):
    assay_excluded: list[str] = field(default_factory=list)   # wells left out of every calculation
    assay_plate_name: str = "Plate 1"
    assay_plate_id: str = "p1"
    assay_std_source: str | None = None   # another plate's id whose standard curve this plate uses
    # Every plate, as dicts of the per-plate fields; the entry at assay_active
    # is refreshed from the fields above whenever the list is read.
    assay_plates: list[dict] = field(default_factory=list)
    assay_active: int = 0

    # Per-file-list UI state (autodetect preview edges etc.), namespaced by
    # files_key ("amp_files"/"solid_files").
    ts_ui: dict[str, dict] = field(default_factory=dict)

    # State for modes that don't share keys with the Streamlit app, keyed by
    # mode id (see mode_state). Included in deep copies and the bundle only
    # through each mode's own export/apply hooks.
    extra: dict[str, Any] = field(default_factory=dict)

    # Undo/redo stacks per mode id (web_app/history.py). Not session data:
    # never exported, and reset when a session file is imported.
    history: dict[str, dict] = field(default_factory=dict, repr=False)

    def mode_state(self, mode_id: str, factory: Callable[[], Any]) -> Any:
        """A mode's own state object, created by `factory` on first use."""
        if mode_id not in self.extra:
            self.extra[mode_id] = factory()
        return self.extra[mode_id]


class SessionStore:
    """Deliberately not thread-safe beyond dict's own atomicity — uvicorn's
    default single-worker/async model means requests interleave
    cooperatively, not across real threads, for this single-user tool."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, SessionData]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        # An unknown-but-well-formed id (e.g. the server restarted while the
        # tab stayed open) is reused rather than replaced, so several requests
        # fired in parallel by the page all land in the same fresh session
        # instead of each minting — and then overwriting — its own cookie.
        new_id = session_id if (session_id and _VALID_ID.match(session_id)) else secrets.token_urlsafe(24)
        data = SessionData()
        self._sessions[new_id] = data
        return new_id, data

    def peek(self, session_id: str | None) -> SessionData | None:
        """The session for an id, without creating one."""
        return self._sessions.get(session_id) if session_id else None


store = SessionStore()
