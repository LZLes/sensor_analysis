"""
Settings — Tier-1 persistence (the QSettings-backed equivalent of the
Streamlit app's browser-localStorage "Save" button) plus the cross-window
Recent Files MRU list.

Unlike the localStorage tier, QSettings reads are synchronous, so there's
no need for the app.py lines 63-80 async-retry-with-time.sleep/st.rerun()
hack this app replaces — settings are just available at startup.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

_ORG = "SensorCalibrationStudio"
_APP = "SensorCalibrationStudio"
_RECENT_FILES_KEY = "recent_files"
_CFG_KEY = "sensor_config"
_MAX_RECENT_FILES = 10


class Settings:
    """Thin wrapper so callers don't sprinkle QSettings key strings
    throughout the UI code."""

    def __init__(self) -> None:
        self._qs = QSettings(_ORG, _APP)

    # -- Tier 1: settings-only config (units, smoothing params — no raw data) --
    def load_cfg_dict(self) -> dict | None:
        raw = self._qs.value(_CFG_KEY, None)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def save_cfg_dict(self, cfg: dict) -> None:
        self._qs.setValue(_CFG_KEY, json.dumps(cfg))

    # -- Recent Files MRU, shared across windows ---------------------------------
    def recent_files(self) -> list[str]:
        raw = self._qs.value(_RECENT_FILES_KEY, [])
        return list(raw) if raw else []

    def add_recent_file(self, path: str) -> None:
        files = [f for f in self.recent_files() if f != path]
        files.insert(0, path)
        self._qs.setValue(_RECENT_FILES_KEY, files[:_MAX_RECENT_FILES])

    def clear_recent_files(self) -> None:
        self._qs.setValue(_RECENT_FILES_KEY, [])
