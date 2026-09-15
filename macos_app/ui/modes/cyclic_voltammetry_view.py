"""
CyclicVoltammetryView — Cyclic Voltammetry mode's Qt view (Phase 6).

Unlike Amperometry/Solid-State, CV doesn't reuse ImportPanel/TimeSeriesPanel/
AutodetectPanel here either — modes/cyclic_voltammetry.py's own docstring/
comment notes it parses CSVs inline rather than sharing core/shared_tabs.py,
calling that "pre-existing duplication, not part of scope" to fix. This view
mirrors that same self-contained structure rather than unifying something
the original app deliberately didn't.

Reuses find_cv_peaks (the one module-level pure function in
modes/cyclic_voltammetry.py) and core.numeric.lin_reg directly, unmodified.

Scope note (same honest-cut pattern as Phases 4-5): _render_cv_plot,
_render_sr_analysis, and _cv_pub_settings in modes/cyclic_voltammetry.py are
closures defined inside render(), not importable standalone functions —
unlike the other modes' render_*_png builders — so this pass covers the
interactive Plotly charts and CSV exports (the data), and defers the
publication-quality static (SVG/PNG/PDF/TIFF) export panel and interactive-
HTML downloads to a follow-up that reimplements those closures as real
functions (which would also let modes/cyclic_voltammetry.py reuse them).
Also deferred: per-item "Isolate" solo buttons (Time Series' panel uses a
plain checklist for the same reason) and the duplicate-scan-rate warning.
"""

from __future__ import annotations

import io
import re
from collections import Counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.constants import PAL
from core.numeric import lin_reg, to_num
from core.parsing import parse_potentiostat_csv
from macos_app.ui.app_state import AppState
from macos_app.ui.theme import plot_theme
from macos_app.ui.undo_commands import SetFieldCommand
from macos_app.ui.widgets.plot_view import PlotView
from modes.cyclic_voltammetry import find_cv_peaks

_FILES_KEY = "cv_runs"
_MAX_CHANNELS = 8


def _dedup_cols(cols: list[str]) -> list[str]:
    """Same dedup logic as modes/cyclic_voltammetry.py's closure of the same
    name (reimplemented here since it's a closure, not importable) —
    'Potential (V), Current, Potential (V), ...' ->
    'Potential (V) [scan 1], Current [scan 1], Potential (V) [scan 2], ...'."""
    cnt = Counter(cols)
    seen: dict[str, int] = {}
    out = []
    for c in cols:
        if cnt[c] > 1:
            seen[c] = seen.get(c, 0) + 1
            out.append(f"{c} [scan {seen[c]}]")
        else:
            out.append(c)
    return out


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


class _CVChannelRow(QWidget):
    def __init__(self, columns: list[str], index: int, preset: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.name_edit = QLineEdit(preset.get("name", f"CH{index + 1}") if preset else f"CH{index + 1}", self)
        self.voltage_combo = QComboBox(self)
        self.voltage_combo.addItems(columns)
        self.current_list = QListWidget(self)
        self.current_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.current_list.addItems(columns)
        self.current_list.setMaximumHeight(60)

        def col_idx(col: str | None, fallback_idx: int) -> int:
            if col is not None and col in columns:
                return columns.index(col)
            return min(fallback_idx, len(columns) - 1) if columns else 0

        self.voltage_combo.setCurrentIndex(col_idx(preset.get("vc") if preset else None, index * 2))
        default_ic = preset.get("ic") if preset else None
        default_idx = col_idx(default_ic, index * 2 + 1)
        if 0 <= default_idx < self.current_list.count():
            self.current_list.item(default_idx).setSelected(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.name_edit, 2)
        row.addWidget(self.voltage_combo, 2)
        row.addWidget(self.current_list, 3)

    def channel(self) -> dict:
        ic_cols = [item.text() for item in self.current_list.selectedItems()]
        return {"name": self.name_edit.text(), "vc": self.voltage_combo.currentText(), "ic_cols": ic_cols}


class CyclicVoltammetryView(QWidget):
    def __init__(self, app_state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._pending_paths: list[str] = []
        self._pending_df0: pd.DataFrame | None = None
        self._channel_rows: list[_CVChannelRow] = []

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        outer.addWidget(tabs)

        tabs.addTab(self._build_import_tab(), "① Import")
        tabs.addTab(self._build_plot_tab(), "② CV Plot")
        tabs.addTab(self._build_peak_tab(), "③ Peak Analysis")
        tabs.addTab(self._build_scan_rate_tab(), "④ Scan Rate Analysis")
        tabs.addTab(self._build_export_tab(), "⑤ Export")

        app_state.files_changed.connect(self._on_files_changed)
        self._refresh_loaded_runs_table()

    def import_files(self, paths: list[str]) -> None:
        self._load_files(paths)

    def _runs(self) -> list[dict]:
        return self._app_state.data.cv_runs

    # -- Tab 1: Import ---------------------------------------------------------
    def _build_import_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        units_group = QGroupBox("Units", self)
        units_form = QFormLayout(units_group)
        self._volt_unit_edit = QLineEdit(self._app_state.data.volt_unit, self)
        self._volt_unit_edit.editingFinished.connect(lambda: self._commit_unit("volt_unit", self._volt_unit_edit))
        self._cur_unit_edit = QLineEdit(self._app_state.data.cv_cur_unit, self)
        self._cur_unit_edit.editingFinished.connect(lambda: self._commit_unit("cv_cur_unit", self._cur_unit_edit))
        self._sr_unit_edit = QLineEdit(self._app_state.data.cv_sr_unit, self)
        self._sr_unit_edit.editingFinished.connect(lambda: self._commit_unit("cv_sr_unit", self._sr_unit_edit))
        units_form.addRow("Potential unit", self._volt_unit_edit)
        units_form.addRow("Current unit", self._cur_unit_edit)
        units_form.addRow("Scan rate unit", self._sr_unit_edit)
        layout.addWidget(units_group)

        browse_row = QHBoxLayout()
        browse_btn = QPushButton("Browse CV Files…", self)
        browse_btn.clicked.connect(self._browse_files)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch(1)
        layout.addLayout(browse_row)

        layout.addWidget(QLabel("Assign a scan rate to each file:", self))
        self._sr_table = QTableWidget(self)
        self._sr_table.setColumnCount(2)
        self._sr_table.setHorizontalHeaderLabels(["File", "Scan rate"])
        self._sr_table.setMaximumHeight(140)
        layout.addWidget(self._sr_table)

        format_group = QGroupBox("Column Mapping", self)
        format_form = QFormLayout(format_group)
        self._fmt_combo = QComboBox(self)
        self._fmt_combo.addItems(["Standard CSV", "Multi-channel instrument"])
        self._fmt_combo.currentTextChanged.connect(self._on_format_changed)
        format_form.addRow("File format", self._fmt_combo)
        self._delim_combo = QComboBox(self)
        self._delim_combo.addItems(["Auto-detect", "Comma", "Tab", "Semicolon", "Space"])
        self._delim_combo.currentTextChanged.connect(self._on_format_changed)
        format_form.addRow("Delimiter", self._delim_combo)
        self._skip_spin = QSpinBox(self)
        self._skip_spin.setRange(0, 50)
        self._skip_spin.valueChanged.connect(self._on_format_changed)
        format_form.addRow("Rows to skip", self._skip_spin)
        layout.addWidget(format_group)

        self._n_channels_spin = QSpinBox(self)
        self._n_channels_spin.setRange(1, _MAX_CHANNELS)
        self._n_channels_spin.valueChanged.connect(self._set_channel_row_count)
        channels_header = QHBoxLayout()
        channels_header.addWidget(QLabel("Number of channels", self))
        channels_header.addWidget(self._n_channels_spin)
        channels_header.addStretch(1)
        layout.addLayout(channels_header)

        col_header = QHBoxLayout()
        col_header.addWidget(QLabel("<b>Name</b>", self), 2)
        col_header.addWidget(QLabel("<b>Voltage col</b>", self), 2)
        col_header.addWidget(QLabel("<b>Current col(s) — multi = averaged</b>", self), 3)
        layout.addLayout(col_header)
        self._channel_rows_layout = QVBoxLayout()
        layout.addLayout(self._channel_rows_layout)

        load_btn = QPushButton("Load All Files", self)
        load_btn.clicked.connect(self._load_all_files)
        layout.addWidget(load_btn)

        self._import_status = QLabel("", self)
        layout.addWidget(self._import_status)

        layout.addWidget(QLabel("Loaded Runs", self))
        self._runs_table = QTableWidget(self)
        self._runs_table.setColumnCount(4)
        self._runs_table.setHorizontalHeaderLabels(["Scan rate", "File", "Rows", "Channels"])
        layout.addWidget(self._runs_table, 1)

        return tab

    def _commit_unit(self, field: str, edit: QLineEdit) -> None:
        value = edit.text()
        if value != self._app_state.get_field(field):
            self._app_state.undo_stack.push(SetFieldCommand(self._app_state, field, value, text=f"Set {field}"))

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Import CV files", "", "CV data (*.csv *.txt);;All files (*)")
        if paths:
            self._load_files(paths)

    def _load_files(self, paths: list[str]) -> None:
        self._pending_paths = paths
        self._sr_table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            filename = path.rsplit("/", 1)[-1]
            self._sr_table.setItem(row, 0, QTableWidgetItem(filename))
            nums = re.findall(r"\d+\.?\d*", filename.rsplit(".", 1)[0])
            default_sr = float(nums[-1]) if nums else 10.0
            self._sr_table.setItem(row, 1, QTableWidgetItem(f"{max(default_sr, 0.001):g}"))
        if paths:
            self._preview_first_file()

    def _on_format_changed(self, *_args) -> None:
        if self._pending_paths:
            self._preview_first_file()

    def _current_delimiter(self) -> str:
        return {"Auto-detect": "auto", "Comma": "comma", "Tab": "tab", "Semicolon": "semicolon", "Space": "space"}[self._delim_combo.currentText()]

    def _preview_first_file(self) -> None:
        try:
            raw = _read_text(self._pending_paths[0])
            skip = self._skip_spin.value()
            delim_map = {"auto": None, "comma": ",", "tab": "\t", "semicolon": ";", "space": r"\s+"}
            d = delim_map[self._current_delimiter()]
            if d is None:
                lines = raw.splitlines()
                sniff = lines[skip] if skip < len(lines) else (lines[0] if lines else "")
                d = next((c for c in [",", "\t", ";"] if c in sniff), r"\s+")

            if self._fmt_combo.currentText().startswith("Multi"):
                df0, auto_channels = parse_potentiostat_csv(raw, d, mode="cv")
                df0.columns = _dedup_cols(list(df0.columns))
                auto_channels = [ch for ch in auto_channels if ch.get("vc") in df0.columns and ch.get("ic") in df0.columns]
            else:
                engine = "python" if d == r"\s+" else "c"
                df0 = pd.read_csv(io.StringIO(raw), sep=d, skiprows=skip, engine=engine, skipinitialspace=True)
                df0.columns = _dedup_cols([c.lstrip("﻿").strip() for c in df0.columns])
                auto_channels = []
        except Exception as exc:  # noqa: BLE001
            self._import_status.setText(f"Could not parse {self._pending_paths[0]}: {exc}")
            return

        self._pending_df0 = df0
        columns = list(df0.columns)
        default_n = len(auto_channels) if auto_channels else max(1, len(columns) // 2)
        self._n_channels_spin.blockSignals(True)
        self._n_channels_spin.setValue(min(_MAX_CHANNELS, default_n))
        self._n_channels_spin.blockSignals(False)
        self._auto_channels = auto_channels
        self._columns = columns
        self._set_channel_row_count(self._n_channels_spin.value())

    def _set_channel_row_count(self, n: int) -> None:
        if self._pending_df0 is None:
            return
        while len(self._channel_rows) < n:
            i = len(self._channel_rows)
            preset = self._auto_channels[i] if i < len(self._auto_channels) else None
            row = _CVChannelRow(self._columns, i, preset, self)
            self._channel_rows.append(row)
            self._channel_rows_layout.addWidget(row)
        while len(self._channel_rows) > n:
            row = self._channel_rows.pop()
            self._channel_rows_layout.removeWidget(row)
            row.deleteLater()

    def _load_all_files(self) -> None:
        if not self._pending_paths or self._pending_df0 is None:
            self._import_status.setText("Preview a file first (select files above).")
            return
        channel_specs = [row.channel() for row in self._channel_rows]
        delim_map = {"auto": None, "comma": ",", "tab": "\t", "semicolon": ";", "space": r"\s+"}
        d = delim_map[self._current_delimiter()]
        skip = self._skip_spin.value()
        is_multi = self._fmt_combo.currentText().startswith("Multi")

        sr_by_row = {}
        for row in range(self._sr_table.rowCount()):
            filename = self._sr_table.item(row, 0).text()
            try:
                sr_by_row[filename] = float(self._sr_table.item(row, 1).text())
            except ValueError:
                sr_by_row[filename] = 10.0

        existing_peaks = {(r["filename"], r["scan_rate"]): r["peaks"] for r in self._runs()}
        sr_unit = self._app_state.get_field("cv_sr_unit")
        new_runs, errors = [], []
        for path in self._pending_paths:
            filename = path.rsplit("/", 1)[-1]
            try:
                raw = _read_text(path)
                dd = d
                if dd is None:
                    lines = raw.splitlines()
                    sniff = lines[skip] if skip < len(lines) else (lines[0] if lines else "")
                    dd = next((c for c in [",", "\t", ";"] if c in sniff), r"\s+")
                if is_multi:
                    df_run, _ = parse_potentiostat_csv(raw, dd, mode="cv")
                    df_run.columns = _dedup_cols(list(df_run.columns))
                else:
                    engine = "python" if dd == r"\s+" else "c"
                    df_run = pd.read_csv(io.StringIO(raw), sep=dd, skiprows=skip, engine=engine, skipinitialspace=True)
                    df_run.columns = _dedup_cols([c.lstrip("﻿").strip() for c in df_run.columns])

                channels = []
                for spec in channel_specs:
                    ic_cols = spec["ic_cols"]
                    if not ic_cols:
                        continue
                    if len(ic_cols) == 1:
                        ic_col = ic_cols[0]
                    else:
                        ic_arrs = [to_num(df_run[c]).to_numpy(dtype=float, na_value=np.nan) for c in ic_cols if c in df_run.columns]
                        if not ic_arrs:
                            continue
                        max_len = max(len(a) for a in ic_arrs)
                        mat = np.full((len(ic_arrs), max_len), np.nan)
                        for j, arr in enumerate(ic_arrs):
                            mat[j, :len(arr)] = arr
                        ic_col = f"__avg_{spec['name']}_ic"
                        df_run[ic_col] = np.nanmean(mat, axis=0)
                    channels.append({"name": spec["name"], "vc": spec["vc"], "ic": ic_col, "is_avg": len(ic_cols) > 1})

                sr_val = sr_by_row.get(filename, 10.0)
                new_runs.append({
                    "scan_rate": sr_val, "label": f"{sr_val:g} {sr_unit}", "filename": filename,
                    "df": df_run, "channels": channels,
                    "peaks": existing_peaks.get((filename, sr_val), {}),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{filename}: {exc}")

        new_runs.sort(key=lambda r: r["scan_rate"])
        cmd = SetFieldCommand(self._app_state, _FILES_KEY, new_runs, text="Load CV files")
        self._app_state.undo_stack.push(cmd)
        self._app_state.notify_files_changed(_FILES_KEY)
        msg = f"Loaded {len(new_runs)} file(s)."
        if errors:
            msg += " Errors: " + "; ".join(errors)
        self._import_status.setText(msg)

    def _on_files_changed(self, files_key: str) -> None:
        if files_key != _FILES_KEY:
            return
        self._refresh_loaded_runs_table()
        self._refresh_plot_selectors()
        self._refresh_peak_channel_list()
        self._refresh_scan_rate_channel_list()

    def _refresh_loaded_runs_table(self) -> None:
        runs = self._runs()
        self._runs_table.setRowCount(len(runs))
        for row, r in enumerate(runs):
            self._runs_table.setItem(row, 0, QTableWidgetItem(f"{r['scan_rate']:g}"))
            self._runs_table.setItem(row, 1, QTableWidgetItem(r["filename"]))
            self._runs_table.setItem(row, 2, QTableWidgetItem(str(len(r["df"]))))
            ch_summary = ", ".join(c["name"] + (" ⏢" if c.get("is_avg") else "") for c in r["channels"])
            self._runs_table.setItem(row, 3, QTableWidgetItem(ch_summary))

    # -- Tab 2: CV Plot -----------------------------------------------------
    def _build_plot_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Scan rates", self))
        self._plot_sr_list = QListWidget(self)
        self._plot_sr_list.setMaximumHeight(90)
        self._plot_sr_list.itemChanged.connect(lambda _i: self._render_cv_plot())
        layout.addWidget(self._plot_sr_list)
        layout.addWidget(QLabel("Channels", self))
        self._plot_ch_list = QListWidget(self)
        self._plot_ch_list.setMaximumHeight(90)
        self._plot_ch_list.itemChanged.connect(lambda _i: self._render_cv_plot())
        layout.addWidget(self._plot_ch_list)
        self._cv_plot_view = PlotView(self)
        layout.addWidget(self._cv_plot_view, 1)
        return tab

    def _refresh_plot_selectors(self) -> None:
        runs = self._runs()
        all_srs = [r["label"] for r in runs]
        all_chs = list(dict.fromkeys(c["name"] for r in runs for c in r["channels"]))
        for widget, values in [(self._plot_sr_list, all_srs), (self._plot_ch_list, all_chs)]:
            widget.blockSignals(True)
            widget.clear()
            for v in values:
                item = QListWidgetItem(v, widget)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            widget.blockSignals(False)
        self._render_cv_plot()

    def _checked_labels(self, widget: QListWidget) -> list[str]:
        return [widget.item(i).text() for i in range(widget.count()) if widget.item(i).checkState() == Qt.CheckState.Checked]

    def _render_cv_plot(self) -> None:
        runs = self._runs()
        if not runs:
            return
        vis_srs = set(self._checked_labels(self._plot_sr_list))
        vis_chs = set(self._checked_labels(self._plot_ch_list))
        vis_runs = [r for r in runs if r["label"] in vis_srs]
        n_vis = len(vis_runs)

        fig = go.Figure()
        for rank, run in enumerate(vis_runs):
            opacity = 0.30 + 0.70 * (rank / max(1, n_vis - 1))
            for ci, ch in enumerate(run["channels"]):
                if ch["name"] not in vis_chs:
                    continue
                col = PAL[ci % len(PAL)]
                v = to_num(run["df"][ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                i = to_num(run["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                fig.add_trace(go.Scatter(x=v, y=i, name=run["label"], legendgroup=ch["name"],
                                          legendgrouptitle=dict(text=ch["name"]), mode="lines",
                                          opacity=opacity, line=dict(color=col, width=1.8)))
                for pt_key, sym in [("anodic", "triangle-up"), ("cathodic", "triangle-down")]:
                    for p in run["peaks"].get(ch["name"], {}).get(pt_key, []):
                        fig.add_trace(go.Scatter(x=[p["Ep"]], y=[p["Ip"]], mode="markers", showlegend=False,
                                                  legendgroup=ch["name"], opacity=opacity,
                                                  marker=dict(symbol=sym, size=10, color=col)))

        theme = plot_theme()
        fig.add_hline(y=0, line=dict(color=theme["axisline"], width=1, dash="dash"))
        fig.update_layout(
            xaxis_title=f"Potential ({self._app_state.get_field('volt_unit')})",
            yaxis_title=f"Current ({self._app_state.get_field('cv_cur_unit')})",
            height=520, template=theme["template"],
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="v", x=1.02, y=1, xanchor="left", groupclick="toggleitem"),
        )
        self._cv_plot_view.set_figure(fig)

    # -- Tab 3: Peak Analysis -------------------------------------------------
    def _build_peak_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        params_form = QFormLayout()
        self._prom_spin = QDoubleSpinBox(self)
        self._prom_spin.setRange(0.0, 1e12)
        self._prom_spin.setDecimals(5)
        params_form.addRow("Prominence", self._prom_spin)
        self._dist_spin = QSpinBox(self)
        self._dist_spin.setRange(1, 100000)
        self._dist_spin.setValue(10)
        params_form.addRow("Min distance (points)", self._dist_spin)
        self._width_spin = QSpinBox(self)
        self._width_spin.setRange(0, 100000)
        params_form.addRow("Min width (points)", self._width_spin)
        self._height_spin = QDoubleSpinBox(self)
        self._height_spin.setRange(0.0, 1e12)
        self._height_spin.setDecimals(5)
        params_form.addRow("Min |Ip|", self._height_spin)
        layout.addLayout(params_form)

        layout.addWidget(QLabel("Channels", self))
        self._peak_ch_list = QListWidget(self)
        self._peak_ch_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._peak_ch_list.setMaximumHeight(90)
        layout.addWidget(self._peak_ch_list)

        find_btn = QPushButton("Find Peaks in All Runs", self)
        find_btn.clicked.connect(self._find_peaks)
        layout.addWidget(find_btn)
        self._peak_status = QLabel("", self)
        layout.addWidget(self._peak_status)

        layout.addWidget(QLabel("All detected peaks", self))
        self._peaks_table = QTableWidget(self)
        layout.addWidget(self._peaks_table, 1)
        return tab

    def _refresh_peak_channel_list(self) -> None:
        runs = self._runs()
        all_chs = list(dict.fromkeys(c["name"] for r in runs for c in r["channels"]))
        self._peak_ch_list.clear()
        for ch in all_chs:
            item = QListWidgetItem(ch, self._peak_ch_list)
            item.setSelected(True)
        all_i = []
        for r in runs:
            for ch in r["channels"]:
                arr = to_num(r["df"][ch["ic"]]).dropna().to_numpy(float)
                if len(arr):
                    all_i.extend(arr.tolist())
        auto_prom = float(np.ptp(all_i)) * 0.05 if all_i else 0.1
        self._prom_spin.setValue(round(auto_prom, 4))

    def _find_peaks(self) -> None:
        selected = {item.text() for item in self._peak_ch_list.selectedItems()}
        if not selected:
            self._peak_status.setText("Select at least one channel.")
            return
        width = self._width_spin.value() or None
        height = self._height_spin.value() or None
        new_runs = []
        for run in self._runs():
            new_run = dict(run)
            peaks = {}
            for ch in run["channels"]:
                if ch["name"] not in selected:
                    continue
                v = to_num(run["df"][ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                i = to_num(run["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                peaks[ch["name"]] = find_cv_peaks(v, i, self._prom_spin.value(), self._dist_spin.value(), width, height)
            new_run["peaks"] = peaks
            new_runs.append(new_run)
        cmd = SetFieldCommand(self._app_state, _FILES_KEY, new_runs, text="Find CV peaks")
        self._app_state.undo_stack.push(cmd)
        self._app_state.notify_files_changed(_FILES_KEY)
        self._peak_status.setText(f"Peaks found in {len(new_runs)} run(s).")
        self._render_cv_plot()
        self._refresh_peaks_table()
        self._refresh_scan_rate_channel_list()

    def _refresh_peaks_table(self) -> None:
        rows = []
        for r in self._runs():
            for ch_name, pk in r["peaks"].items():
                for p in pk.get("anodic", []):
                    rows.append((r["scan_rate"], ch_name, "Anodic", p["Ep"], p["Ip"]))
                for p in pk.get("cathodic", []):
                    rows.append((r["scan_rate"], ch_name, "Cathodic", p["Ep"], p["Ip"]))
        self._peaks_table.setColumnCount(5)
        self._peaks_table.setHorizontalHeaderLabels(["Scan rate", "Channel", "Type", "Ep", "Ip"])
        self._peaks_table.setRowCount(len(rows))
        for row, (sr, ch, kind, ep, ip) in enumerate(rows):
            for col, val in enumerate((f"{sr:g}", ch, kind, f"{ep:.4g}", f"{ip:.4g}")):
                self._peaks_table.setItem(row, col, QTableWidgetItem(val))

    # -- Tab 4: Scan Rate Analysis --------------------------------------------
    def _build_scan_rate_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Channels", self))
        self._sr_ch_list = QListWidget(self)
        self._sr_ch_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._sr_ch_list.setMaximumHeight(90)
        self._sr_ch_list.itemSelectionChanged.connect(self._render_scan_rate_plots)
        layout.addWidget(self._sr_ch_list)

        layout.addWidget(QLabel("Peak Current vs Scan Rate", self))
        self._ip_nu_plot = PlotView(self)
        self._ip_nu_plot.setMinimumHeight(300)
        layout.addWidget(self._ip_nu_plot)

        layout.addWidget(QLabel("Randles–Ševčík Plot (Ip vs √ν)", self))
        self._ip_sqrt_plot = PlotView(self)
        self._ip_sqrt_plot.setMinimumHeight(300)
        layout.addWidget(self._ip_sqrt_plot)

        self._sr_stats_table = QTableWidget(self)
        self._sr_stats_table.setMaximumHeight(140)
        layout.addWidget(self._sr_stats_table)

        export_btn = QPushButton("Export scan-rate analysis CSV", self)
        export_btn.clicked.connect(self._export_scan_rate_csv)
        layout.addWidget(export_btn)
        return tab

    def _refresh_scan_rate_channel_list(self) -> None:
        runs = self._runs()
        all_chs = [ch for ch in dict.fromkeys(c["name"] for r in runs for c in r["channels"])
                   if any(r["peaks"].get(ch) for r in runs)]
        self._sr_ch_list.clear()
        for ch in all_chs:
            item = QListWidgetItem(ch, self._sr_ch_list)
            item.setSelected(True)
        self._render_scan_rate_plots()

    def _scan_rate_data(self) -> dict[str, pd.DataFrame]:
        def main_peak(lst):
            return max(lst, key=lambda p: abs(p["Ip"])) if lst else None

        selected = [item.text() for item in self._sr_ch_list.selectedItems()]
        ch_data = {}
        for ch_name in selected:
            rows = []
            for run in self._runs():
                pk = run["peaks"].get(ch_name, {})
                pa, pc = main_peak(pk.get("anodic", [])), main_peak(pk.get("cathodic", []))
                epa = pa["Ep"] if pa else np.nan
                epc = pc["Ep"] if pc else np.nan
                rows.append({
                    "scan_rate": run["scan_rate"], "label": run["label"],
                    "Ip_a": pa["Ip"] if pa else np.nan, "Ep_a": epa,
                    "Ip_c": pc["Ip"] if pc else np.nan, "Ep_c": epc,
                    "delta_Ep": abs(epa - epc) if np.isfinite(epa) and np.isfinite(epc) else np.nan,
                })
            ch_data[ch_name] = pd.DataFrame(rows).sort_values("scan_rate").reset_index(drop=True)
        return ch_data

    def _render_scan_rate_plots(self) -> None:
        ch_data = self._scan_rate_data()
        if not ch_data:
            return
        cur_unit = self._app_state.get_field("cv_cur_unit")
        sr_unit = self._app_state.get_field("cv_sr_unit")
        theme = plot_theme()
        common_layout = dict(template=theme["template"], paper_bgcolor="rgba(0,0,0,0)",
                              plot_bgcolor="rgba(0,0,0,0)", height=340)

        fig_ip_nu = go.Figure()
        fig_ip_sqrt = go.Figure()
        stat_rows = []
        for ci, (ch_name, d) in enumerate(ch_data.items()):
            col = PAL[ci % len(PAL)]
            nu = d["scan_rate"].values
            snu = np.sqrt(nu)
            for pt, ipcol, sym, dash in [("anodic", "Ip_a", "triangle-up", "solid"), ("cathodic", "Ip_c", "triangle-down", "dash")]:
                ip = d[ipcol].values
                valid = np.isfinite(ip)
                if not valid.any():
                    continue
                lbl = f"{ch_name} ({pt})"
                fig_ip_nu.add_trace(go.Scatter(x=nu[valid], y=ip[valid], name=lbl, mode="markers+lines",
                                                marker=dict(symbol=sym, size=9, color=col), line=dict(color=col, dash=dash)))
                fig_ip_sqrt.add_trace(go.Scatter(x=snu[valid], y=ip[valid], name=lbl, mode="markers",
                                                  marker=dict(symbol=sym, size=9, color=col)))
                fit = lin_reg(snu[valid], ip[valid])
                if fit:
                    xf = np.linspace(snu[valid].min(), snu[valid].max(), 200)
                    fig_ip_sqrt.add_trace(go.Scatter(x=xf, y=fit["slope"] * xf + fit["intercept"],
                                                      name=f"{lbl} fit (R²={fit['r2']:.3f})", mode="lines",
                                                      line=dict(color=col, dash="dot", width=2)))
                    stat_rows.append({"Channel": ch_name, "Peak": pt, "Slope": f"{fit['slope']:.4g}",
                                       "R²": f"{fit['r2']:.4f}", "N runs": int(valid.sum())})

        fig_ip_nu.update_layout(**common_layout, xaxis_title=f"Scan rate ν ({sr_unit})", yaxis_title=f"Ip ({cur_unit})")
        fig_ip_sqrt.update_layout(**common_layout, xaxis_title=f"√ν", yaxis_title=f"Ip ({cur_unit})")
        self._ip_nu_plot.set_figure(fig_ip_nu)
        self._ip_sqrt_plot.set_figure(fig_ip_sqrt)

        self._sr_stats_table.clear()
        if stat_rows:
            cols = list(stat_rows[0].keys())
            self._sr_stats_table.setColumnCount(len(cols))
            self._sr_stats_table.setHorizontalHeaderLabels(cols)
            self._sr_stats_table.setRowCount(len(stat_rows))
            for r, row in enumerate(stat_rows):
                for c, col in enumerate(cols):
                    self._sr_stats_table.setItem(r, c, QTableWidgetItem(str(row[col])))

    def _export_scan_rate_csv(self) -> None:
        ch_data = self._scan_rate_data()
        rows = []
        for ch_name, d in ch_data.items():
            for _, row in d.iterrows():
                rows.append({"Channel": ch_name, "Scan rate": row["scan_rate"], "Ip_a": row["Ip_a"],
                             "Ep_a": row["Ep_a"], "Ip_c": row["Ip_c"], "Ep_c": row["Ep_c"], "delta_Ep": row["delta_Ep"]})
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export scan rate analysis CSV", "cv_scan_rate_analysis.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    # -- Tab 5: Export ----------------------------------------------------------
    def _build_export_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        peaks_btn = QPushButton("Export all peaks CSV", self)
        peaks_btn.clicked.connect(self._export_peaks_csv)
        layout.addWidget(peaks_btn)
        raw_btn = QPushButton("Export raw data (all runs, one CSV each)", self)
        raw_btn.clicked.connect(self._export_raw_data)
        layout.addWidget(raw_btn)
        layout.addStretch(1)
        return tab

    def _export_peaks_csv(self) -> None:
        rows = []
        for r in self._runs():
            for ch_name, pk in r["peaks"].items():
                for p in pk.get("anodic", []):
                    rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Anodic", "Ep": p["Ep"], "Ip": p["Ip"]})
                for p in pk.get("cathodic", []):
                    rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Cathodic", "Ep": p["Ep"], "Ip": p["Ip"]})
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export peaks CSV", "cv_peaks_all.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    def _export_raw_data(self) -> None:
        if not self._runs():
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose folder for raw CV data CSVs")
        if not directory:
            return
        for r in self._runs():
            safe = r["label"].replace("/", "per").replace(" ", "_")
            r["df"].to_csv(f"{directory}/cv_raw_{safe}.csv", index=False)
