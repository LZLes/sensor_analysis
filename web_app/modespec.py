"""
ModeSpec: everything the web app's shell needs to know about one analysis
mode. Each router module (web_app/api/<mode>.py) defines a module-level
`MODE = ModeSpec(...)`, and web_app/registry.py lists them. The shell uses
the spec to mount the router, build the nav bar, load the mode's HTML
fragment and scripts, write/read its part of the session bundle, keep its
undo history, autosave it and build its printable report. Nothing outside
the mode's own files hardcodes a mode.

Adding a mode (see .claude/CLAUDE.md, "Adding a mode"):
  1. web_app/api/<id>.py with `router`, a `_state(session)` and `MODE`.
  2. web_app/static/modes/<id>.html (the mode's <section> body) and
     web_app/static/js/<id>.js, which calls registerMode("<id>", {...}).
  3. One line in web_app/registry.py.
Session state for a new mode goes in `session.extra["<id>"]` (see
SessionData.mode_state) unless it must round-trip with the Streamlit app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter

from web_app.session import SessionData

# One printable-report block. kind: "text" (value: str), "kv" (value: dict),
# "table" (value: list[dict]), "image" (value: PNG bytes), "note" (value: str).
ReportSection = tuple[str, str, Any]   # (kind, title, value)


@dataclass(frozen=True)
class ModeSpec:
    id: str                    # url prefix /api/<id>, DOM id mode-<id>, files modes/<id>.html + js/<id>.js
    label: str                 # nav-bar text
    router: APIRouter
    state: Callable[[SessionData], dict]
    # Session bundle (Export/Import JSON, autosave). Keys must match
    # core/persistence.py where the Streamlit app has the same data.
    export_bundle: Callable[[SessionData], dict]
    apply_bundle: Callable[[SessionData, dict], None]
    # Short description of the data the mode holds ("2 files"), or None
    # when it holds none — used by the import toast and the restore banner.
    summary: Callable[[SessionData], str | None]
    # SessionData attributes an undo snapshot covers (web_app/history.py).
    undo_fields: tuple[str, ...] = ()
    report: Callable[[SessionData], list[ReportSection]] | None = None
    scripts: tuple[str, ...] = ()  # shared scripts to load before js/<id>.js, e.g. "trace_mode.js"

    def public(self) -> dict:
        """What the page needs to boot the mode."""
        return {"id": self.id, "label": self.label, "scripts": list(self.scripts),
                "report": self.report is not None, "undo": bool(self.undo_fields)}
