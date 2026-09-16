"""
PlotView — native Qt chart widget (pyqtgraph) used by every interactive
plot in the app: time series, calibration curves, CV traces/scan-rate
plots, and cross-file comparison.

Replaces the earlier Plotly-figure-in-QWebEngineView approach. That design
reused the Streamlit app's plotly.graph_objects code directly, but it had
two real problems: it looked like an embedded web page (browser chrome,
Plotly's own fonts/toolbar, a white canvas that never matched the app's
theme) instead of a native control, and QWebEngineView.setHtml() silently
drops any document over ~2MB — a full_html Plotly export with plotly.js
embedded is routinely 4MB+, so every plot came up blank until a prior fix
switched to loading a temp file by URL instead.

pyqtgraph paints straight onto a QWidget with QPainter, so it has neither
problem: transparent background that follows the app's own light/dark
theme (see macos_app/ui/theme.py's plot_theme()), no size ceiling, and
native scroll-zoom/pan for free from PlotWidget's ViewBox.

Callers no longer build a plotly.graph_objects.Figure. Instead: clear(),
then one add_series()/add_vline()/add_hline()/add_region() call per trace/
annotation (a deliberately narrow builder API — only the shapes the app's
plot call sites actually use), then set_labels()/set_y_range(), then
finish() to autorange and sync the optional range-slider overview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from macos_app.ui.theme import plot_theme

pg.setConfigOptions(antialias=True)

_DASH_STYLES: dict[str, Qt.PenStyle] = {
    "solid": Qt.PenStyle.SolidLine,
    "dash": Qt.PenStyle.DashLine,
    "dot": Qt.PenStyle.DotLine,
    "dashdot": Qt.PenStyle.DashDotLine,
    "longdash": Qt.PenStyle.DashLine,
    "longdashdot": Qt.PenStyle.DashDotLine,
}

_SYMBOLS: dict[str, str] = {
    "circle": "o", "diamond": "d", "square": "s", "cross": "x", "x": "x",
    "triangle-up": "t1", "triangle-down": "t", "star": "star",
}

_RGBA_RE = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")

_RANGE_SLIDER_HEIGHT = 60
_MIN_PLOT_HEIGHT = 260


def _qcolor(spec: str, opacity: float = 1.0) -> QColor:
    m = _RGBA_RE.match(spec.strip()) if isinstance(spec, str) else None
    if m:
        r, g, b = (int(float(v)) for v in m.groups()[:3])
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        color = QColor(r, g, b)
        color.setAlphaF(max(0.0, min(1.0, a * opacity)))
        return color
    color = QColor(spec)
    if opacity < 1.0:
        color.setAlphaF(opacity)
    return color


@dataclass
class _HoverSeries:
    x: np.ndarray
    y: np.ndarray
    name: str
    color: QColor


@dataclass
class _PlotState:
    hover_series: list[_HoverSeries] = field(default_factory=list)
    text_items: list[pg.TextItem] = field(default_factory=list)


class PlotView(QWidget):
    """A QWidget hosting a native pyqtgraph chart, optionally with a
    range-slider overview strip below it (time series only)."""

    def __init__(self, parent: QWidget | None = None, *, show_range_slider: bool = False) -> None:
        super().__init__(parent)
        self._state = _PlotState()
        self._show_range_slider = show_range_slider

        self._plot_widget = pg.PlotWidget(self)
        self._plot = self._plot_widget.getPlotItem()
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._legend = self._plot.addLegend(offset=(10, 10))
        self._view_box = self._plot.getViewBox()

        self._crosshair = pg.InfiniteLine(angle=90, movable=False)
        self._crosshair.setVisible(False)
        self._plot.addItem(self._crosshair, ignoreBounds=True)
        self._hover_label = pg.TextItem(anchor=(0, 0))
        self._hover_label.setVisible(False)
        self._plot.addItem(self._hover_label, ignoreBounds=True)
        self._hover_proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse_moved
        )

        # A splitter/layout with several siblings otherwise happily
        # compresses this widget toward zero — pyqtgraph's own sizeHint is a
        # generous 600x480, but only a floor like this stops sibling
        # widgets' natural (non-stretch) sizes from winning that fight and
        # squeezing the plot down to an unreadable sliver.
        self.setMinimumHeight(_MIN_PLOT_HEIGHT + (_RANGE_SLIDER_HEIGHT if show_range_slider else 0))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot_widget, 1)

        self._overview_widget: pg.PlotWidget | None = None
        self._overview_region: pg.LinearRegionItem | None = None
        if show_range_slider:
            self._overview_widget = pg.PlotWidget(self)
            self._overview_widget.setFixedHeight(_RANGE_SLIDER_HEIGHT)
            self._overview_widget.getPlotItem().hideAxis("left")
            self._overview_widget.getPlotItem().showGrid(x=True, y=False, alpha=0.2)
            self._overview_region = pg.LinearRegionItem(movable=True)
            self._overview_widget.addItem(self._overview_region)
            self._overview_region.sigRegionChanged.connect(self._on_overview_region_changed)
            self._view_box.sigXRangeChanged.connect(self._on_main_range_changed)
            layout.addWidget(self._overview_widget)

        self.apply_theme()

    # -- theme -----------------------------------------------------------------
    def apply_theme(self) -> None:
        theme = plot_theme()
        self._plot_widget.setBackground(None)
        axis_pen = pg.mkPen(_qcolor(theme["axisline"]))
        text_color = theme["annot_font"]
        for axis_name in ("bottom", "left"):
            axis = self._plot.getAxis(axis_name)
            axis.setPen(axis_pen)
            axis.setTextPen(text_color)
        self._plot.getViewBox().setBackgroundColor(None)
        self._crosshair.setPen(pg.mkPen(_qcolor(theme["spike"]), style=Qt.PenStyle.DotLine))
        self._hover_label.setColor(text_color)
        if self._overview_widget is not None:
            self._overview_widget.setBackground(None)
            self._overview_widget.getPlotItem().getAxis("bottom").setPen(axis_pen)
            self._overview_widget.getPlotItem().getAxis("bottom").setTextPen(text_color)
            self._overview_region.setBrush(_qcolor("rgba(120,150,200,0.25)"))

    # -- building ----------------------------------------------------------------
    def clear(self) -> None:
        self._plot.clear()
        self._legend.clear()
        self._state = _PlotState()
        self._plot.addItem(self._crosshair, ignoreBounds=True)
        self._plot.addItem(self._hover_label, ignoreBounds=True)
        self._crosshair.setVisible(False)
        self._hover_label.setVisible(False)
        if self._overview_widget is not None:
            self._overview_widget.clear()
            self._overview_region = pg.LinearRegionItem(movable=True)
            self._overview_widget.addItem(self._overview_region)
            self._overview_region.sigRegionChanged.connect(self._on_overview_region_changed)
        self.apply_theme()

    def add_series(
        self, x, y, *,
        name: str | None = None,
        color: str = "#1f77b4",
        width: float = 1.5,
        dash: str = "solid",
        show_line: bool = True,
        symbol: str | None = None,
        size: float = 8,
        opacity: float = 1.0,
        legend: bool = True,
        hover: bool = True,
        text: list[str] | None = None,
        error_y=None,
    ) -> None:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        qcolor = _qcolor(color, opacity)
        pen = pg.mkPen(qcolor, width=width, style=_DASH_STYLES.get(dash, Qt.PenStyle.SolidLine)) if show_line else None
        pg_symbol = _SYMBOLS.get(symbol) if symbol else None
        item = pg.PlotDataItem(
            x=x, y=y, pen=pen, connect="finite",
            symbol=pg_symbol, symbolBrush=qcolor if pg_symbol else None,
            symbolPen=qcolor if pg_symbol else None, symbolSize=size,
        )
        self._plot.addItem(item)
        if legend and name:
            self._legend.addItem(item, name)

        if error_y is not None:
            err = np.asarray(error_y, dtype=float)
            bar = pg.ErrorBarItem(x=x, y=y, top=err, bottom=err, pen=pg.mkPen(qcolor))
            self._plot.addItem(bar)

        if text is not None:
            for xi, yi, label in zip(x, y, text):
                if not label or not np.isfinite(xi) or not np.isfinite(yi):
                    continue
                ti = pg.TextItem(str(label), color=qcolor, anchor=(0.5, 1.3))
                ti.setPos(xi, yi)
                self._plot.addItem(ti)
                self._state.text_items.append(ti)

        if hover and name:
            self._state.hover_series.append(_HoverSeries(x=x, y=y, name=name, color=qcolor))

        if self._overview_widget is not None:
            overview_pen = pg.mkPen(qcolor, width=1)
            self._overview_widget.addItem(pg.PlotDataItem(x=x, y=y, pen=overview_pen, connect="finite"))

    def add_vline(self, x: float, *, color: str = "#888888", dash: str = "dot", text: str | None = None) -> None:
        line = pg.InfiniteLine(
            pos=x, angle=90, movable=False,
            pen=pg.mkPen(_qcolor(color), style=_DASH_STYLES.get(dash, Qt.PenStyle.DotLine)),
            label=text, labelOpts=dict(position=0.95, color=_qcolor(color)) if text else None,
        )
        self._plot.addItem(line, ignoreBounds=True)

    def add_hline(self, y: float, *, color: str = "#888888", dash: str = "dash") -> None:
        line = pg.InfiniteLine(
            pos=y, angle=0, movable=False,
            pen=pg.mkPen(_qcolor(color), style=_DASH_STYLES.get(dash, Qt.PenStyle.DashLine)),
        )
        self._plot.addItem(line, ignoreBounds=True)

    def add_region(self, x0: float, x1: float, *, color: str = "rgba(100,160,255,0.15)", text: str | None = None) -> None:
        region = pg.LinearRegionItem(values=(x0, x1), movable=False, brush=_qcolor(color))
        for ln in region.lines:
            ln.setPen(QPen(Qt.PenStyle.NoPen))
        self._plot.addItem(region)
        if text:
            label = pg.TextItem(text, color=_qcolor(color, 1.0), anchor=(0, 1))
            label.setPos(x0, 0)
            self._plot.addItem(label, ignoreBounds=True)
            self._state.text_items.append(label)

    def set_labels(self, x_title: str | None = None, y_title: str | None = None) -> None:
        if x_title is not None:
            self._plot.setLabel("bottom", x_title)
        if y_title is not None:
            self._plot.setLabel("left", y_title)

    def set_y_range(self, auto: bool = True, y_min: float | None = None, y_max: float | None = None) -> None:
        if auto:
            self._view_box.enableAutoRange(axis="y")
        else:
            self._view_box.setYRange(y_min, y_max, padding=0)

    def finish(self) -> None:
        """Call once after building a figure: reposition the top-of-region
        text labels now that the real y-range is known, and (re)sync the
        range-slider overview to the full data extent."""
        y_top = None
        if self._state.text_items:
            _, y_range = self._view_box.viewRange()
            y_top = y_range[1]
        for item in self._state.text_items:
            if isinstance(item, pg.TextItem) and item.pos().y() == 0 and y_top is not None:
                item.setPos(item.pos().x(), y_top)
        if self._overview_widget is not None and self._overview_region is not None:
            self._overview_widget.getPlotItem().enableAutoRange()
            x_range, _ = self._view_box.viewRange()
            self._overview_region.blockSignals(True)
            self._overview_region.setRegion(x_range)
            self._overview_region.blockSignals(False)

    # -- range-slider linkage -----------------------------------------------
    def _on_overview_region_changed(self) -> None:
        if self._overview_region is None:
            return
        self._view_box.setXRange(*self._overview_region.getRegion(), padding=0)

    def _on_main_range_changed(self, _vb, x_range) -> None:
        if self._overview_region is None:
            return
        self._overview_region.blockSignals(True)
        self._overview_region.setRegion(x_range)
        self._overview_region.blockSignals(False)

    # -- unified hover ------------------------------------------------------
    def _on_mouse_moved(self, evt) -> None:
        pos = evt[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            self._crosshair.setVisible(False)
            self._hover_label.setVisible(False)
            return
        point: QPointF = self._view_box.mapSceneToView(pos)
        x = point.x()
        if not self._state.hover_series:
            self._crosshair.setVisible(False)
            self._hover_label.setVisible(False)
            return
        self._crosshair.setPos(x)
        self._crosshair.setVisible(True)

        lines = [f"x = {x:.4g}"]
        for series in self._state.hover_series:
            if series.x.size == 0:
                continue
            idx = int(np.nanargmin(np.abs(series.x - x)))
            yi = series.y[idx]
            if np.isfinite(yi):
                lines.append(f"{series.name}: {yi:.4g}")
        self._hover_label.setHtml("<br>".join(lines))
        (x_min, x_max), (y_min, y_max) = self._view_box.viewRange()
        label_x = x if x < (x_min + x_max) / 2 else x
        self._hover_label.setPos(label_x, y_max)
        self._hover_label.setAnchor((0, 0) if x < (x_min + x_max) / 2 else (1, 0))
        self._hover_label.setVisible(True)
