"""
Entry point for the local (localhost) web app.

Run from the repo root with:  python -m web_app.main
(Requires requirements-web.txt. The fit maths, parsers and export builders
come from core/, shared with the Streamlit app; Streamlit itself isn't needed.)

Starts a Uvicorn server bound to localhost only and opens the default
browser to it. No auth — this is a single-user local tool, not a hosted
service; binding to 127.0.0.1 (not 0.0.0.0) keeps it off the network.

Single instance: the running server's port/pid is recorded in a small state
file, so launching again (e.g. double-clicking the macOS app twice) just
reopens the browser on the existing server instead of starting a second one.

Environment variables:
  WEB_APP_PORT=8000             preferred port (the next free one is used if taken)
  WEB_APP_NO_BROWSER=1          don't open a browser tab automatically
  WEB_APP_IDLE_SHUTDOWN_MIN=15  exit after this many minutes with no open tab
                                (set by the macOS launcher; off by default)
  SCS_DATA_DIR=/path            where state, autosaves and templates live
                                (default: see web_app/storage.py)

The modes it serves come from web_app/registry.py; nothing here names one.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from contextlib import asynccontextmanager

import plotly.offline as pyo
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from web_app import autosave, history, storage
from web_app.api import session, shell
from web_app.registry import MODES, get_mode
from web_app.session import SESSION_COOKIE_NAME, store

APP_ID = "sensor-calibration-studio"
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")



def _state_file() -> str:
    return str(storage.data_dir() / "server.json")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    autosave.rotate()
    task = asyncio.create_task(autosave.run_forever())
    try:
        yield
    finally:
        task.cancel()
        try:
            autosave.save_now()
        except Exception as exc:  # noqa: BLE001
            print(f"Final autosave failed: {exc!r}", flush=True)


app = FastAPI(title="Sensor Calibration Studio (local)", lifespan=_lifespan)

# Binding to 127.0.0.1 keeps other machines out, but not other *websites*
# open in the same browser. Two checks close that gap:
#  * Host allowlist — defeats DNS rebinding (an attacker's domain re-pointed
#    at 127.0.0.1 would otherwise be "same origin" and could read data).
#  * Origin check on state-changing requests — a cross-site form/fetch POST
#    (which skips CORS preflight) can't trigger anything, e.g. /api/app/quit.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def _local_only(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in _ALLOWED_HOSTS:
        return PlainTextResponse("Forbidden host", status_code=403)
    origin = request.headers.get("origin")
    if request.method not in _SAFE_METHODS and origin is not None:
        origin_host = urllib.parse.urlsplit(origin).hostname or ""
        if origin_host not in _ALLOWED_HOSTS:
            return PlainTextResponse("Cross-site request blocked", status_code=403)
    return await call_next(request)


# Mode-scoped requests: /api/<mode>/… and /api/history/<mode>/…
_MODE_IDS = {m.id for m in MODES}


@app.middleware("http")
async def _undo_and_autosave(request: Request, call_next):
    """Tags mode responses with that mode's undo/redo counts (X-Undo:
    "<undo>,<redo>"), and marks the session changed for autosave after any
    successful state-changing request."""
    response = await call_next(request)
    parts = request.url.path.split("/")
    if len(parts) < 3 or parts[1] != "api":
        return response
    mode_id = parts[3] if parts[2] == "history" and len(parts) > 3 else parts[2]
    sess = store.peek(request.cookies.get(SESSION_COOKIE_NAME))
    if sess is None:
        return response
    if mode_id in _MODE_IDS and get_mode(mode_id).undo_fields:
        undo, redo = history.counts(sess, mode_id)
        response.headers["X-Undo"] = f"{undo},{redo}"
        response.headers["X-Undo-Mode"] = mode_id
    changed = mode_id in _MODE_IDS or request.url.path in ("/api/session/import", "/api/autosave/restore")
    if changed and request.method not in _SAFE_METHODS and response.status_code < 400:
        autosave.mark_dirty(sess)
    return response


# Routers are registered before the catch-all static mount below —
# Starlette matches routes in registration order, so /api/* always resolves
# to these handlers rather than falling through to StaticFiles.
for _mode in MODES:
    app.include_router(_mode.router)
app.include_router(session.router)
app.include_router(shell.router)

_server: uvicorn.Server | None = None
_last_heartbeat = time.monotonic()


@app.get("/api/app/info")
def app_info() -> dict:
    return {"app": APP_ID, "pid": os.getpid()}


@app.post("/api/app/heartbeat")
def heartbeat() -> dict:
    """Open pages ping this periodically; drives the idle auto-shutdown."""
    global _last_heartbeat
    _last_heartbeat = time.monotonic()
    return {"ok": True}


@app.post("/api/app/quit")
def quit_app() -> dict:
    """The page's Quit button — there's no terminal to Ctrl-C when the
    server was started from the macOS app."""
    try:
        autosave.save_now()
    except Exception as exc:  # noqa: BLE001
        print(f"Autosave on quit failed: {exc!r}", flush=True)
    if _server is not None:
        threading.Timer(0.3, lambda: setattr(_server, "should_exit", True)).start()
    return {"ok": True}


@app.get("/js/plotly.min.js")
def plotly_js() -> PlainTextResponse:
    # Served from the installed plotly package rather than a CDN or a
    # committed multi-MB bundle: works offline and always matches the
    # installed plotly version.
    return PlainTextResponse(pyo.get_plotlyjs(), media_type="application/javascript",
                             headers={"Cache-Control": "max-age=86400"})


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


# -- Port selection + single instance ----------------------------------------------------
def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def _pick_port(preferred: int, attempts: int = 20) -> int:
    for port in range(preferred, preferred + attempts):
        if _port_is_free(port):
            return port
    raise SystemExit(f"No free port found in {preferred}–{preferred + attempts - 1}; set WEB_APP_PORT.")


def _running_instance_url() -> str | None:
    """URL of an already-running instance of this app, if there is one."""
    try:
        with open(_state_file(), encoding="utf-8") as fh:
            port = int(json.load(fh)["port"])
        url = f"http://{HOST}:{port}"
        with urllib.request.urlopen(f"{url}/api/app/info", timeout=1.5) as resp:
            if json.load(resp).get("app") == APP_ID:
                return url
    except Exception:  # noqa: BLE001 — missing/stale state file or nothing listening
        pass
    return None


def _write_state(port: int) -> None:
    with open(_state_file(), "w", encoding="utf-8") as fh:
        json.dump({"port": port, "pid": os.getpid()}, fh)


def _clear_state() -> None:
    try:
        with open(_state_file(), encoding="utf-8") as fh:
            if json.load(fh).get("pid") != os.getpid():
                return  # another instance owns it now
        os.remove(_state_file())
    except Exception:  # noqa: BLE001
        pass


def _idle_watchdog(minutes: float) -> None:
    while _server is None or not _server.should_exit:
        time.sleep(15)
        if time.monotonic() - _last_heartbeat > minutes * 60 and _server is not None:
            print(f"No open tab for {minutes:g} min — shutting down.", flush=True)
            _server.should_exit = True


def main() -> int:
    global _server, _last_heartbeat
    open_browser = os.environ.get("WEB_APP_NO_BROWSER") != "1"

    existing = _running_instance_url()
    if existing:
        print(f"Sensor Calibration Studio is already running at {existing} — opening that instead.\n"
              "(If it's the installed Mac app, it runs its own bundled copy of the code; quit it "
              "first to run this checkout.)", flush=True)
        if open_browser:
            webbrowser.open(existing)
        return 0

    port = _pick_port(int(os.environ.get("WEB_APP_PORT", DEFAULT_PORT)))
    url = f"http://{HOST}:{port}"
    _write_state(port)
    print(f"Sensor Calibration Studio running at {url}  (Ctrl-C or the Quit button to stop)", flush=True)

    _server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    idle_min = float(os.environ.get("WEB_APP_IDLE_SHUTDOWN_MIN", "0") or 0)
    if idle_min > 0:
        _last_heartbeat = time.monotonic() + 60  # grace period for the first page load
        threading.Thread(target=_idle_watchdog, args=(idle_min,), daemon=True).start()
    try:
        _server.run()
    finally:
        _clear_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
