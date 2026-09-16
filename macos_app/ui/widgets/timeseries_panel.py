"""
TimeSeriesPanel — Qt rebuild of core/shared_tabs.py's _render_timeseries_tab.

Shared between Amperometry and Solid-State, generic over files_key/unit_key
exactly like the Streamlit version. Reuses core.numeric.to_num/smooth_signal,
core.shared_tabs.render_ts_png (PNG export — pure, no Streamlit calls despite
living in a file that imports streamlit at module level; see
macos_app/requirements-macos.txt for the resulting dependency note), and
this app's own macos_app.ui.theme.plot_theme() (not core/constants.py's
st.context.theme-based _plot_theme()).

_ChannelRow/_FileChannelEditor live here (moved from import_panel.py) since
channel assignment is now part of the combined "Time Series & Windows" step
in each mode view rather than a separate Import-tab gate — see
amperometry_view.py/solid_state_view.py's _build_windows_tab.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.constants import PAL
from core.numeric import _eff_t_start, smooth_signal, to_num
from core.shared_tabs import _amp_label, render_ts_png
from macos_app.ui.app_state import AppState
from macos_app.ui.dialogs.export_options_dialog import ExportOptionsDialog
from macos_app.ui.undo_commands import SetFieldCommand
from macos_app.ui.widgets.collapsible import make_collapsible
from macos_app.ui.widgets.plot_view import PlotView

_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]
_MAX_CHANNELS = 8


class _ChannelRow(QWidget):
    """One channel's (name, time col, signal col) mapping."""

    def __init__(self, columns: list[str], index: int, preset: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.name_edit = QLineEdit(preset.get("name", f"Channel {index + 1}") if preset else f"Channel {index + 1}", self)
        self.time_combo = QComboBox(self)
        self.time_combo.addItems(columns)
        self.signal_combo = QComboBox(self)
        self.signal_combo.addItems(columns)

        def col_idx(col: str | None, fallback_idx: int) -> int:
            if col is not None and col in columns:
                return columns.index(col)
            return min(fallback_idx, len(columns) - 1) if columns else 0

        self.time_combo.setCurrentIndex(col_idx(preset.get("tc") if preset else None, index * 2))
        self.signal_combo.setCurrentIndex(col_idx(preset.get("ic") if preset else None, index * 2 + 1))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.name_edit, 2)
        row.addWidget(self.time_combo, 3)
        row.addWidget(self.signal_combo, 3)

    def channel(self) -> dict:
        return {"name": self.name_edit.text(), "tc": self.time_combo.currentText(), "ic": self.signal_combo.currentText()}


class _FileChannelEditor(QGroupBox):
    """One file's full channel mapping — N channels, each a _ChannelRow."""

    def __init__(self, filename: str, df: pd.DataFrame, preset_channels: list[dict], signal_col_label: str, parent=None) -> None:
        super().__init__(filename, parent)
        self.df = df
        self._columns = list(df.columns)
        self._preset_channels = preset_channels
        self._signal_col_label = signal_col_label
        self._rows: list[_ChannelRow] = []

        outer = QVBoxLayout(self)

        meta_label = QLabel(f"{len(df):,} rows, {len(df.columns)} columns", self)
        outer.addWidget(meta_label)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Number of channels", self))
        self._n_channels_spin = QSpinBox(self)
        self._n_channels_spin.setRange(1, _MAX_CHANNELS)
        default_n = len(preset_channels) if preset_channels else max(1, len(self._columns) // 2)
        self._n_channels_spin.setValue(min(_MAX_CHANNELS, default_n))
        self._n_channels_spin.valueChanged.connect(self._set_row_count)
        spin_row.addWidget(self._n_channels_spin)
        spin_row.addStretch(1)
        outer.addLayout(spin_row)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Channel name</b>", self), 2)
        header.addWidget(QLabel("<b>Time column</b>", self), 3)
        header.addWidget(QLabel(f"<b>{signal_col_label} column</b>", self), 3)
        outer.addLayout(header)

        self._rows_container = QVBoxLayout()
        outer.addLayout(self._rows_container)

        self._set_row_count(self._n_channels_spin.value())

    def _set_row_count(self, n: int) -> None:
        while len(self._rows) < n:
            i = len(self._rows)
            preset = self._preset_channels[i] if i < len(self._preset_channels) else None
            row = _ChannelRow(self._columns, i, preset, self)
            self._rows.append(row)
            self._rows_container.addWidget(row)
        while len(self._rows) > n:
            row = self._rows.pop()
            self._rows_container.removeWidget(row)
            row.deleteLater()

    def channels(self) -> list[dict]:
        return [row.channel() for row in self._rows]


class TimeSeriesPanel(QWidget):
    channels_changed = Signal()  # emitted whenever refresh() runs — lets mode
    # views (e.g. Amperometry's "add average trace" checkbox) react to the
    # channel-visibility checklist without reaching into a private attribute.

    def __init__(self, app_state: AppState, files_key: str, unit_key: str, signal_axis_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._files_key = files_key
        self._unit_key = unit_key
        self._signal_axis_label = signal_axis_label

        outer = QVBoxLayout(self)

        smooth_group = QGroupBox("Signal smoothing", self)
        smooth_outer = QVBoxLayout(smooth_group)
        smooth_content = QWidget(self)
        smooth_form = QFormLayout(smooth_content)
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
        smooth_outer.addWidget(smooth_content)
        make_collapsible(smooth_group, smooth_content, expanded=False)
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

        self._plot_view = PlotView(self, show_range_slider=True)
        outer.addWidget(self._plot_view, 1)

        export_row = QHBoxLayout()
        png_btn = QPushButton("Export PNG", self)
        png_btn.clicked.connect(self._export_png)
        export_row.addWidget(png_btn)
        export_row.addStretch(1)
        outer.addLayout(export_row)

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
        self.channels_changed.emit()

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

        self._plot_view.clear()
        for fi, ci, fn, df, ch in combos:
            label = _amp_label(fn, ch["name"], multi_file)
            if label not in visible:
                continue
            t = to_num(df[ch["tc"]])
            raw = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            smoothed = smooth_signal(raw, method, window, polyorder)
            color = PAL[(fi if multi_file else ci) % len(PAL)]
            dash = _DASHES[ci % len(_DASHES)] if multi_file else "solid"
            if method != "None":
                self._plot_view.add_series(t, raw, name=f"{label} (raw)", color=color, width=1,
                                            dash=dash, opacity=0.35, legend=False, hover=False)
            self._plot_view.add_series(t, smoothed, name=label, color=color, width=1.5, dash=dash)

        for frec in files:
            for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
                ets = _eff_t_start(row)
                if ets is not None and pd.notna(row.get("t_end")):
                    color = "rgba(255,165,0,0.22)" if row.get("Baseline") else "rgba(100,160,255,0.15)"
                    label = f"{frec['filename']}: {row['Label']}" if multi_file else str(row["Label"])
                    self._plot_view.add_region(ets, row["t_end"], color=color, text=label)

        # Auto-detect preview: candidate edges not yet applied to the
        # calibration table (see autodetect_panel.py) — thin dotted lines,
        # distinct from the shaded calibration windows above.
        edges_by_file = self._app_state.data.ts_ui.get(self._files_key, {}).get("autodetect_edges", {})
        for frec in files:
            for edge in edges_by_file.get(frec["filename"], []):
                self._plot_view.add_vline(edge, color="#e91e63", dash="dot", text="detected")

        self._plot_view.set_labels(
            "Time (s)", f"{self._signal_axis_label} ({self._app_state.get_field(self._unit_key)})"
        )
        self._plot_view.set_y_range(
            auto=self._y_auto.isChecked(), y_min=self._y_min.value(), y_max=self._y_max.value()
        )
        self._plot_view.finish()

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

        def render(opts: dict) -> bytes:
            return render_ts_png(
                files, self._app_state.get_field(self._unit_key), self.visible_labels(),
                dpi=opts["dpi"], fmt=opts["fmt"], figsize=opts["figsize"], style=opts["style"],
                smooth_method=self._smooth_method.currentText(),
                smooth_window=self._smooth_window.value(),
                smooth_polyorder=self._smooth_polyorder.value(),
            )

        opts = ExportOptionsDialog.get_options(self, "Export time series", render)
        if opts is None:
            return None
        png_bytes = render(opts)
        ext = opts["fmt"]
        path, _ = QFileDialog.getSaveFileName(self, "Export time series", f"time_series.{ext}", f"{ext.upper()} (*.{ext})")
        if path:
            with open(path, "wb") as f:
                f.write(png_bytes)
        return png_bytes, path
