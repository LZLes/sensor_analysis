"""Shared constants and small formatting/theme helpers used across all modes."""

import os

import numpy as np
import streamlit as st


PAL = [
    "#4c96d7", "#ff9230", "#2ecc71", "#e05c5c",
    "#b39ddb", "#f0a050", "#f48fb1", "#6d8ea0",
]
AVG_COLOR = "#555555"   # dark charcoal for channel-average — readable on both white and dark backgrounds

# sensor_analysis/ (parent of this core/ package), not core/ itself — used by
# both Amperometry's and Solid-State's "Load sample data" buttons.
_SAMPLE_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data"
)


def _plot_theme() -> dict:
    """Plotly styling that adapts to the user's actual Streamlit theme (light/dark)."""
    is_dark = st.context.theme.type != "light"   # None (unknown) treated as dark, today's default
    return dict(
        template   = "plotly_dark" if is_dark else "plotly_white",
        grid       = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.12)",
        axisline   = "rgba(255,255,255,0.2)" if is_dark else "rgba(0,0,0,0.25)",
        spike      = "#888" if is_dark else "#555",
        annot_font = "#e0e0e0" if is_dark else "#222",
    )


_MIME = {"png": "image/png", "svg": "image/svg+xml",
         "pdf": "application/pdf", "tiff": "image/tiff"}


def fmt(val, p: int = 4) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:.{p}g}"
