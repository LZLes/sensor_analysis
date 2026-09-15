"""
PlotView — embeds an interactive Plotly figure in a Qt widget.

Chosen over PyQtGraph so the existing app's figure-construction logic
(scroll-zoom, custom rangeslider, unified hover w/ spike lines,
add_vrect calibration-window shading, add_vline auto-detect markers)
can be reused as plain plotly.graph_objects code, same as the
Streamlit app's st.plotly_chart calls, without hand-rebuilding those
interactions in a native charting toolkit.

plotly.js is embedded directly in the generated HTML (include_plotlyjs=True)
so the app works fully offline — there is no CDN fetch at render time.
"""

from __future__ import annotations

import plotly.graph_objects as go
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget


class PlotView(QWidget):
    """A QWidget that renders a plotly.graph_objects.Figure via QWebEngineView."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._web_view = QWebEngineView(self)
        # about:blank avoids a transient "no URL" state before the first setHtml.
        self._web_view.setUrl(QUrl("about:blank"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._web_view)

    def set_figure(self, fig: go.Figure) -> None:
        """Render (or re-render) the given figure. Full re-render per call —
        matches the existing app's model of rebuilding the figure on each
        user action rather than incremental trace patching."""
        html = fig.to_html(include_plotlyjs=True, full_html=True)
        # baseUrl left empty: figure HTML is self-contained, no relative assets.
        self._web_view.setHtml(html)
