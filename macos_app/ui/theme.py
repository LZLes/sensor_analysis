"""
Native dark-mode query for this app's own Plotly figures.

Deliberately NOT touching core/constants.py's _plot_theme() (which reads
st.context.theme.type — a Streamlit-only API) per the non-negotiable
constraint: this produces the same output shape independently, using Qt's
own color-scheme query, so downstream Plotly figure-building code (copied
from the same patterns as core/constants.py's callers) is unaffected.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, Qt


def _is_dark_mode() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is None:
        # Qt < 6.5 — no colorScheme() API; default to light rather than guess.
        return False
    return scheme() == Qt.ColorScheme.Dark


def plot_theme() -> dict:
    """Same shape as core/constants.py's _plot_theme(), independently sourced."""
    is_dark = _is_dark_mode()
    return dict(
        template=   "plotly_dark" if is_dark else "plotly_white",
        grid=       "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.12)",
        axisline=   "rgba(255,255,255,0.2)" if is_dark else "rgba(0,0,0,0.25)",
        spike=      "#888" if is_dark else "#555",
        annot_font= "#e0e0e0" if is_dark else "#222",
    )
