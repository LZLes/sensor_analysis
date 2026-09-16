"""
Entry point for the local (localhost) web app.

Run from the repo root with:  python -m web_app.main
(Requires requirements-web.txt installed, in addition to requirements.txt
since core/ and modes/*.py are imported directly here — same reuse-via-
import model macos_app/ used, per .claude/CLAUDE.md's non-negotiable
constraint: this never edits app.py, modes/*.py, or core/*.py's existing
behavior.)

Starts a Uvicorn server bound to localhost only and opens the default
browser to it. No auth — this is a single-user local tool, not a hosted
service; binding to 127.0.0.1 (not 0.0.0.0) keeps it off the network.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

import plotly.offline as pyo
import uvicorn
from fastapi import FastAPI
from fastapi import Response
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from web_app.api import _dev_test, amperometry, cyclic_voltammetry, session, solid_state

HOST = "127.0.0.1"
PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Sensor Calibration Studio (local)")

# Routers are registered before the catch-all static mount below —
# Starlette matches routes in registration order, so /api/* always resolves
# to these handlers rather than falling through to StaticFiles.
app.include_router(_dev_test.router)
app.include_router(solid_state.router)
app.include_router(amperometry.router)
app.include_router(cyclic_voltammetry.router)
app.include_router(session.router)


@app.get("/js/plotly.min.js")
def plotly_js() -> PlainTextResponse:
    # Served from the installed plotly package (plotly.offline.get_plotlyjs())
    # rather than committing a multi-MB minified bundle to the repo — stays
    # in sync with whatever plotly version requirements-web.txt pins, and
    # matches the offline-capability reasoning macos_app/ui/widgets/plot_view.py's
    # include_plotlyjs=True already established (no CDN fetch at chart-render time).
    return PlainTextResponse(pyo.get_plotlyjs(), media_type="application/javascript")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> int:
    if os.environ.get("WEB_APP_NO_BROWSER") != "1":
        threading.Timer(1.0, _open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
