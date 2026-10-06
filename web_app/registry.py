"""The analysis modes the web app serves, in nav-bar order. Adding a mode is
one line here plus its own files (see web_app/modespec.py)."""

from __future__ import annotations

from web_app.api import amperometry, assay, cyclic_voltammetry, solid_state
from web_app.modespec import ModeSpec

MODES: list[ModeSpec] = [
    amperometry.MODE,
    solid_state.MODE,
    cyclic_voltammetry.MODE,
    assay.MODE,
]


def get_mode(mode_id: str) -> ModeSpec | None:
    return next((m for m in MODES if m.id == mode_id), None)
