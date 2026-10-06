"""
Autosave: the web app keeps sessions only in memory, so a crash, a closed
terminal or Quit would lose unsaved work. This writes the most recently
changed session's bundle (the same JSON as Export session) to
<data dir>/autosave.json a few seconds after each change, and again on
shutdown.

On startup the previous run's autosave is moved to autosave-previous.json;
the page offers to restore it into a fresh session (GET /api/autosave),
and it stays there until restored, discarded, or replaced by the next
startup's rotation.

Changes are detected in main.py's middleware: any successful non-GET
/api/<mode>/ or /api/session/import request marks the session dirty.
Requests can run in a thread pool while the bundle is built, so a save can
in rare cases catch a half-applied change; the next save corrects it.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from web_app import storage
from web_app.session import SessionData

INTERVAL_S = 5.0

_dirty: SessionData | None = None
_last_saved_at: float | None = None


def current_path() -> Path:
    return storage.data_dir() / "autosave.json"


def previous_path() -> Path:
    return storage.data_dir() / "autosave-previous.json"


def rotate() -> None:
    """At startup: last run's autosave becomes the one offered for restore."""
    cur = current_path()
    if cur.exists():
        cur.replace(previous_path())


def mark_dirty(session: SessionData) -> None:
    global _dirty
    _dirty = session


def save_now() -> None:
    """Write the dirty session, if any (blocking)."""
    global _dirty, _last_saved_at
    session = _dirty
    if session is None:
        return
    _dirty = None
    from web_app.api.session import build_bundle, summaries  # late: avoids an import cycle via the registry
    if not summaries(session):
        return  # nothing worth keeping (e.g. everything was removed)
    payload = {"kind": "sensor-studio-autosave", "saved_at": time.strftime("%Y-%m-%d %H:%M"),
               "summary": summaries(session), "bundle": build_bundle(session)}
    storage.write_json_atomic(current_path(), payload)
    _last_saved_at = time.time()


async def run_forever() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        if _dirty is not None:
            try:
                await run_in_threadpool(save_now)
            except Exception as exc:  # noqa: BLE001 — never let autosave take the server down
                print(f"Autosave failed: {exc!r}", flush=True)


def previous_info() -> dict | None:
    path = previous_path()
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "sensor-studio-autosave":
        return None
    return payload
