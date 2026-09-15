"""
TimeSeriesPanel — Qt rebuild of core/shared_tabs.py's _render_timeseries_tab.

Shared between Amperometry and Solid-State, generic over files_key/unit_key
exactly like the Streamlit version. Reuses core.numeric.to_num/smooth_signal,
core.shared_tabs.render_ts_png (PNG export — pure, no Streamlit calls despite
living in a file that imports streamlit at module level; see
macos_app/requirements-macos.txt for the resulting dependency note), and
this app's own macos_app.ui.theme.plot_theme() (not core/constants.py's
st.context.theme-based _plot_theme()).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.numeric import _eff_t_start, smooth_signal, to_num
from core.shared_tabs import _amp_label, render_ts_png
from macos_app.ui.app_state import AppState
from macos_app.ui.theme import plot_theme
from macos_app.ui.undo_commands import SetFieldCommand
from macos_app.ui.widgets.plot_view import PlotView

_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]
_PAL = [
    "#4c96d7", "#ff9230", "#2ecc71", "#e05c5c",
    "#b39ddb", "#f0a050", "#f48fb1", "#6d8ea0",
]


class TimeSeriesPanel(QWidget):
    def __init__(self, app_state: AppState, files_key: str, unit_key: str, signal_axis_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._files_key = files_key
        self._unit_key = unit_key
        self._signal_axis_label = signal_axis_label

        outer = QVBoxLayout(self)

        smooth_group = QGroupBox("Signal smoothing", self)
        smooth_form = QFormLayout(smooth_group)
        self._smooth_method = QComboBox(self)
        self._smooth_method.addItems(["None", "Moving average", "Savitzky-Golay"])
        self._smooth_method.setCurrentText(app_state.data.smooth_method)
        self._smooth_method.currentTextChanged.connect(self._on_smoothing_changed)
        self._smooth_window = QSpinBox(self)
        self._smooth_window.setRange(3, 9999)
        self._smooth_window.setSingleStep(2)
        self._smooth_window.setValue(app_state.data.smooth_window)
        self._smooth_window.valueChanged.connect(self._on_smoothing_changed)
        self._smooth_polyorder = QSpinBox(self)
        self._smooth_polyorder.setRange(1, 5)
        self._smooth_polyorder.setValue(app_state.data.smooth_polyorder)
        self._smooth_polyorder.valueChanged.connect(self._on_smoothing_changed)
        smooth_form.addRow("Method", self._smooth_method)
        smooth_form.addRow("Window (samples)", self._smooth_window)
        smooth_form.addRow("Polynomial order", self._smooth_polyorder)
        outer.addWidget(smooth_group)

        self._channel_list = QListWidget(self)
        self._channel_list.setMaximumHeight(120)
        self._channel_list.itemChanged.connect(lambda _item: self.refresh())
        outer.addWidget(self._channel_list)

        y_group = QGroupBox("Y-axis range", self)
        y_row = QHBoxLayout(y_group)
        self._y_auto = QCheckBox("Auto-scale", self)
        self._y_auto.setChecked(app_state.data.ts_y_auto)
        self._y_auto.toggled.connect(self._on_y_range_changed)
        self._y_min = QDoubleSpinBox(self)
        self._y_min.setRange(-1e12, 1e12)
        self._y_min.setDecimals(6)
        self._y_max = QDoubleSpinBox(self)
        self._y_max.setRange(-1e12, 1e12)
        self._y_max.setDecimals(6)
        self._y_min.valueChanged.connect(self._on_y_range_changed)
        self._y_max.valueChanged.connect(self._on_y_range_changed)
        y_row.addWidget(self._y_auto)
        y_row.addWidget(self._y_min)
        y_row.addWidget(self._y_max)
        outer.addWidget(y_group)

        self._plot_view = PlotView(self)
        outer.addWidget(self._plot_view, 1)

        export_row = QHBoxLayout()
        png_btn = QPushButton("Export PNG", self)
        png_btn.clicked.connect(self._export_png)
        export_row.addWidget(png_btn)
        export_row.addStretch(1)
        outer.addLayout(export_row)

        self._last_figure: go.Figure | None = None

    # -- data plumbing ----------------------------------------------------------
    def _combos(self) -> list[tuple[int, int, str, pd.DataFrame, dict]]:
        files = self._app_state.files_for(self._files_key)
        return [
            (fi, ci, frec["filename"], frec["df"], ch)
            for fi, frec in enumerate(files)
            for ci, ch in enumerate(frec["channels"])
        ]

    def refresh(self) -> None:
        """Rebuild the channel-visibility list (if the file set changed) and
        the figure. Safe to call any time files/cpdf/smoothing/y-range
        change — this app recomputes the whole figure per action rather
        than patching traces, same model the Streamlit version used."""
        files = self._app_state.files_for(self._files_key)
        multi_file = len(files) > 1
        combos = self._combos()
        all_labels = [_amp_label(fn, ch["name"], multi_file) for _, _, fn, _, ch in combos]

        existing_labels = {self._channel_list.item(i).text() for i in range(self._channel_list.count())}
        if set(all_labels) != existing_labels:
            self._channel_list.blockSignals(True)
            self._channel_list.clear()
            for label in all_labels:
                item = QListWidgetItem(label, self._channel_list)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            self._channel_list.blockSignals(False)

        self._render_figure()

    def visible_labels(self) -> list[str]:
        return [
            self._channel_list.item(i).text()
            for i in range(self._channel_list.count())
            if self._channel_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _render_figure(self) -> None:
        files = self._app_state.files_for(self._files_key)
        if not files:
            return
        multi_file = len(files) > 1
        combos = self._combos()
        visible = set(self.visible_labels())
        method = self._smooth_method.currentText()
        window = self._smooth_window.value()
        polyorder = self._smooth_polyorder.value()

        fig = go.Figure()
        for fi, ci, fn, df, ch in combos:
            label = _amp_label(fn, ch["name"], multi_file)
            if label not in visible:
                continue
            t = to_num(df[ch["tc"]])
            raw = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            smoothed = smooth_signal(raw, method, window, polyorder)
            color = _PAL[(fi if multi_file else ci) % len(_PAL)]
            dash = _DASHES[ci % len(_DASHES)] if multi_file else "solid"
            if method != "None":
                fig.add_trace(go.Scatter(x=t, y=raw, name=f"{label} (raw)", mode="lines",
                                          opacity=0.35, line=dict(color=color, width=1, dash=dash),
                                          showlegend=False))
            fig.add_trace(go.Scatter(x=t, y=smoothed, name=label, mode="lines",
                                      line=dict(color=color, width=1.5, dash=dash)))

        theme = plot_theme()
        for frec in files:
            for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
                ets = _eff_t_start(row)
                if ets is not None and pd.notna(row.get("t_end")):
                    color = "rgba(255,165,0,0.22)" if row.get("Baseline") else "rgba(100,160,255,0.15)"
                    label = f"{frec['filename']}: {row['Label']}" if multi_file else str(row["Label"])
                    fig.add_vrect(x0=ets, x1=row["t_end"], fillcolor=color, layer="below", line_width=0,
                                  annotation_text=label, annotation_position="top left",
                                  annotation=dict(font_size=10, font_color=theme["annot_font"]))

        # Auto-detect preview: candidate edges not yet applied to the
        # calibration table (see autodetect_panel.py) — thin dotted lines,
        # distinct from the shaded calibration windows above.
        edges_by_file = self._app_state.data.ts_ui.get(self._files_key, {}).get("autodetect_edges", {})
        for frec in files:
            for edge in edges_by_file.get(frec["filename"], []):
                fig.add_vline(x=edge, line_dash="dot", line_color="#e91e63", opacity=0.55,
                              annotation_text="detected", annotation_position="bottom",
                              annotation=dict(font_size=9, font_color="#e91e63"))

        y_range_kwargs = {}
        if not self._y_auto.isChecked():
            y_range_kwargs["range"] = [self._y_min.value(), self._y_max.value()]

        fig.update_layout(
            xaxis_title="Time (s)",
            yaxis_title=f"{self._signal_axis_label} ({self._app_state.get_field(self._unit_key)})",
            hovermode="x unified",
            height=520,
            template=theme["template"],
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=True,
            xaxis=dict(
                rangeslider=dict(visible=True, thickness=0.05),
                showspikes=True, spikemode="across", spikesnap="cursor",
                spikecolor=theme["spike"], spikethickness=1, spikedash="dot",
                showgrid=True, gridcolor=theme["grid"], linecolor=theme["axisline"],
            ),
            yaxis=dict(
                showspikes=True, spikemode="across",
                spikecolor=theme["spike"], spikethickness=1, spikedash="dot",
                showgrid=True, gridcolor=theme["grid"], linecolor=theme["axisline"],
                **y_range_kwargs,
            ),
        )
        self._plot_view.set_figure(fig)
        self._last_figure = fig

    # -- control handlers ---------------------------------------------------
    def _on_smoothing_changed(self, *_args) -> None:
        stack = self._app_state.undo_stack
        stack.push(SetFieldCommand(self._app_state, "smooth_method", self._smooth_method.currentText()))
        stack.push(SetFieldCommand(self._app_state, "smooth_window", self._smooth_window.value()))
        stack.push(SetFieldCommand(self._app_state, "smooth_polyorder", self._smooth_polyorder.value()))
        self._render_figure()

    def _on_y_range_changed(self, *_args) -> None:
        self._app_state.set_field("ts_y_auto", self._y_auto.isChecked())
        self._app_state.set_field("ts_y_min", self._y_min.value())
        self._app_state.set_field("ts_y_max", self._y_max.value())
        self._render_figure()

    def _export_png(self) -> tuple[bytes, str] | None:
        files = self._app_state.files_for(self._files_key)
        if not files:
            return None
        png_bytes = render_ts_png(
            files, self._app_state.get_field(self._unit_key), self.visible_labels(),
            smooth_method=self._smooth_method.currentText(),
            smooth_window=self._smooth_window.value(),
            smooth_polyorder=self._smooth_polyorder.value(),
        )
        path, _ = QFileDialog.getSaveFileName(self, "Export time series PNG", "time_series.png", "PNG image (*.png)")
        if path:
            with open(path, "wb") as f:
                f.write(png_bytes)
        return png_bytes, path
