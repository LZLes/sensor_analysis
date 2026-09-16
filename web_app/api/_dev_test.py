"""
Phase-1 feasibility endpoint — not part of any mode's real API surface.

Proves the whole pipeline end to end before building out per-mode routers:
modes/solid_state.py's _load_solid_sample_data (unmodified) -> a Plotly
figure built from core.numeric.to_num'd data -> JSON over the wire ->
Plotly.js rendering it in a real browser (verified with Playwright, not
just "the request didn't 500").
"""

from __future__ import annotations

import json

import plotly.graph_objects as go
from fastapi import APIRouter

from core.numeric import to_num
from modes.solid_state import _load_solid_sample_data

router = APIRouter(prefix="/api/_test", tags=["_test"])


@router.get("/figure")
def get_test_figure() -> dict:
    files = _load_solid_sample_data()
    if not files:
        return {"error": "sample data missing"}
    frec = files[0]
    ch = frec["channels"][0]
    t = to_num(frec["df"][ch["tc"]])
    v = to_num(frec["df"][ch["ic"]])

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=v, mode="lines", name=ch["name"]))
    fig.update_layout(
        title=f"{frec['filename']} — {ch['name']} (Phase 1 feasibility check)",
        xaxis_title="Time (s)", yaxis_title="Potential (mV)",
        template="plotly_white",
    )
    # json.loads(fig.to_json()) rather than fig.to_dict(): to_json() runs
    # Plotly's own numpy-safe encoder, so int64/float64 in the traces never
    # reach FastAPI's default JSON encoder (which would choke on them).
    return json.loads(fig.to_json())
