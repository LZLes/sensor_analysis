"""
SolidStateView — the Solid-State mode's full Qt view.

Fit logic (nernstian_lod_fit, ideal_slope_in_signal_unit,
render_solid_cal_png, _cpdf_from_autodetect_windows, _load_solid_sample_data)
is imported directly from modes/solid_state.py — none of it is touched;
this view calls the same pure functions the Streamlit render() function
calls, just driven by AppState instead of st.session_state.

Tab layout mirrors AmperometryView's 4-step pipeline (Import → Time Series
& Windows → Calibration Results → Export) plus Compare Files — see that
file's docstring and the "Streamline Amperometry/Solid-State/CV" plan for
the reasoning. Solid-State has no dilution calculator (that's Amperometry-
only), so its Time Series & Windows tab is channel assignment + calibration
windows only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.calibration_table import _default_solid_cpdf
from core.constants import PAL, fmt
from core.numeric import _eff_t_start, to_num
from core.shared_tabs import _amp_label
from macos_app.ui.app_state import AppState
from macos_app.ui.dialogs.export_options_dialog import ExportOptionsDialog
from macos_app.ui.theme import plot_theme
from macos_app.ui.undo_commands import FilesListCommand, TableEditCommand
from macos_app.ui.widgets.autodetect_panel import AutodetectPanel
from macos_app.ui.widgets.collapsible import make_collapsible
from macos_app.ui.widgets.comparison_view import ComparisonView
from macos_app.ui.widgets.editable_table_view import EditableTableView
from macos_app.ui.widgets.import_panel import ImportPanel
from macos_app.ui.widgets.plot_view import PlotView
from macos_app.ui.widgets.timeseries_panel import TimeSeriesPanel, _FileChannelEditor
from modes.solid_state import (
    _cpdf_from_autodetect_windows,
    _load_solid_sample_data,
    ideal_slope_in_signal_unit,
    nernstian_lod_fit,
    render_solid_cal_png,
)

_FILES_KEY = "solid_files"
_UNIT_KEY = "solid_unit"
_CONC_UNIT_KEY = "solid_conc_unit"
_CPDF_COLUMNS = ["Label", "Concentration", "t_start", "t_end", "avg_duration", "Reading_mV"]


def _compute_file_fit(frec: dict, app_state: AppState) -> dict | None:
    """Comparison-tab adapter (see comparison_view.py): one independent
    Nernstian fit for this file alone, using its own calibration table and
    first channel — not mixed with any other file's channels, unlike the
    Calibration Results tab's cross-file channel selection."""
    channels = frec.get("channels", [])
    if not channels:
        return None
    ch = channels[0]
    cpdf = frec["cpdf"].copy()
    if cpdf.empty:
        return None
    rejected = ~(cpdf["Concentration"].astype(float) > 0)
    cpdf = cpdf[~rejected].reset_index(drop=True)
    if cpdf.empty:
        return None

    df = frec["df"]
    t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    e_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)

    readings = []
    for _, row in cpdf.iterrows():
        if pd.notna(row.get("Reading_mV")):
            readings.append(float(row["Reading_mV"]))
            continue
        ets = _eff_t_start(row)
        if ets is None or pd.isna(row.get("t_end")):
            readings.append(np.nan)
            continue
        mask = (t_arr >= ets) & (t_arr <= row["t_end"])
        pts = e_arr[mask]
        pts = pts[~np.isnan(pts)]
        readings.append(float(np.mean(pts)) if pts.size > 0 else np.nan)

    log_conc = np.log10(cpdf["Concentration"].astype(float).to_numpy())
    potential = np.array(readings, dtype=float)
    valid = ~np.isnan(potential)
    if valid.sum() < 2:
        return None

    lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
    nern = lod_fit["nernstian_segment"]
    if nern is None:
        return None
    x_valid = log_conc[valid]
    curve_x = np.linspace(float(x_valid.min()), float(x_valid.max()), 100)
    curve_y = nern["slope"] * curve_x + nern["intercept"]

    conc_unit, signal_unit = app_state.get_field(_CONC_UNIT_KEY), app_state.get_field(_UNIT_KEY)
    return {
        "x": x_valid.tolist(), "y": potential[valid].tolist(), "curve_x": curve_x.tolist(), "curve_y": curve_y.tolist(),
        "stats": {
            "File": frec["filename"], "Channel": ch["name"],
            f"Sensitivity ({signal_unit}/decade)": fmt(nern["slope"]),
            "R²": f"{nern['r2']:.4f}",
            f"LOD ({conc_unit})": fmt(lod_fit.get("lod_conc")),
        },
    }


class SolidStateView(QWidget):
    def __init__(self, app_state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._active_file_index = 0
        self._channel_editor: _FileChannelEditor | None = None

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        outer.addWidget(tabs)

        # -- Tab 1: Import -----------------------------------------------
        self._import_panel = ImportPanel(
            app_state, _FILES_KEY, "Potential", _UNIT_KEY, _CONC_UNIT_KEY,
            seed_cpdf_fn=_default_solid_cpdf,
            sample_loader_fn=_load_solid_sample_data,
            sample_caption="A synthetic potentiometric (ISE-style) run with a two-regime response.",
        )
        tabs.addTab(self._import_panel, "① Import")

        # -- Tab 2: Time Series & Windows -----------------------------------
        self._timeseries_panel = TimeSeriesPanel(app_state, _FILES_KEY, _UNIT_KEY, "Potential")
        windows_tab = self._build_windows_tab(app_state)
        tabs.addTab(windows_tab, "② Time Series & Windows")

        # -- Tab 3: Calibration Results ---------------------------------------
        cal_tab = QWidget(self)
        cal_layout = QVBoxLayout(cal_tab)

        settings_group = QGroupBox("Analysis Settings", self)
        settings_form = QFormLayout(settings_group)
        settings_form.addRow(QLabel(
            "Channels analysed = the checked traces in ② Time Series & Windows.", self))
        self._ion_charge = QSpinBox(self)
        self._ion_charge.setRange(1, 4)
        self._ion_charge.setValue(1)
        settings_form.addRow("Ion charge |z|", self._ion_charge)
        self._lab_temp = QDoubleSpinBox(self)
        self._lab_temp.setRange(-20.0, 100.0)
        self._lab_temp.setValue(25.0)
        self._lab_temp.setSingleStep(0.5)
        settings_form.addRow("Lab temperature (°C)", self._lab_temp)
        cal_layout.addWidget(settings_group)

        compute_btn = QPushButton("Compute Calibration", self)
        compute_btn.clicked.connect(self._compute_calibration)
        cal_layout.addWidget(compute_btn)

        self._cal_status = QLabel("", self)
        cal_layout.addWidget(self._cal_status)

        self._cal_plot = PlotView(self)
        cal_layout.addWidget(self._cal_plot, 1)

        self._stats_table = QTableWidget(self)
        self._stats_table.setMaximumHeight(160)
        cal_layout.addWidget(self._stats_table)

        tabs.addTab(cal_tab, "③ Calibration Results")

        # -- Tab 4: Export --------------------------------------------------
        export_tab = QWidget(self)
        export_layout = QVBoxLayout(export_tab)
        csv_btn = QPushButton("Export calibration summary CSV", self)
        csv_btn.clicked.connect(self._export_csv)
        export_layout.addWidget(csv_btn)
        png_btn = QPushButton("Export calibration curve…", self)
        png_btn.clicked.connect(self._export_curve_png)
        export_layout.addWidget(png_btn)
        export_layout.addStretch(1)
        tabs.addTab(export_tab, "④ Export")

        # -- Tab 5: Comparison (cross-file overlay) -------------------
        comparison_tab = ComparisonView(
            app_state, _FILES_KEY, _compute_file_fit,
            x_label=f"log₁₀(Concentration [{app_state.get_field(_CONC_UNIT_KEY)}])",
            y_label=f"Potential ({app_state.get_field(_UNIT_KEY)})",
        )
        tabs.addTab(comparison_tab, "⑤ Compare Files")

        app_state.files_changed.connect(self._on_files_changed)
        app_state.cpdf_changed.connect(self._on_cpdf_changed)
        self._refresh_dataset_list()

    # -- Tab 2 construction ---------------------------------------------------
    def _build_windows_tab(self, app_state: AppState) -> QWidget:
        tab = QWidget(self)
        outer = QVBoxLayout(tab)

        dataset_row = QHBoxLayout()
        dataset_row.addWidget(QLabel("Dataset:", self))
        self._dataset_combo = QComboBox(self)
        self._dataset_combo.currentIndexChanged.connect(self._on_dataset_changed)
        dataset_row.addWidget(self._dataset_combo, 1)
        outer.addLayout(dataset_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._timeseries_panel)

        side_scroll = QScrollArea(self)
        side_scroll.setWidgetResizable(True)
        side_container = QWidget(side_scroll)
        side_layout = QVBoxLayout(side_container)

        # -- Channel assignment (collapsed by default) -----------------------
        chan_group = QGroupBox("Channel assignment", self)
        chan_outer = QVBoxLayout(chan_group)
        self._chan_content = QWidget(self)
        self._chan_content_layout = QVBoxLayout(self._chan_content)
        self._chan_content_layout.setContentsMargins(0, 0, 0, 0)
        apply_chan_btn = QPushButton("Apply channel assignment", self)
        apply_chan_btn.clicked.connect(self._apply_channel_assignment)
        self._chan_content_layout.addWidget(apply_chan_btn)
        chan_outer.addWidget(self._chan_content)
        make_collapsible(chan_group, self._chan_content, expanded=False)
        side_layout.addWidget(chan_group)

        # -- Calibration windows (expanded by default) -----------------------
        windows_group = QGroupBox("Calibration windows", self)
        windows_outer = QVBoxLayout(windows_group)
        windows_content = QWidget(self)
        windows_content_layout = QVBoxLayout(windows_content)
        windows_content_layout.setContentsMargins(0, 0, 0, 0)
        self._autodetect_panel = AutodetectPanel(app_state, _FILES_KEY, _cpdf_from_autodetect_windows, has_baseline=False)
        self._autodetect_panel.edges_detected.connect(self._timeseries_panel.refresh)
        windows_content_layout.addWidget(self._autodetect_panel)
        windows_content_layout.addWidget(QLabel("Calibration Points", self))
        self._cal_table = EditableTableView(
            _default_solid_cpdf(),
            editable_columns=set(_CPDF_COLUMNS),
            row_defaults={"Label": "New", "Concentration": 1.0, "t_start": 0.0, "t_end": 60.0, "avg_duration": np.nan, "Reading_mV": np.nan},
        )
        self._cal_table.row_committed.connect(self._commit_active_cpdf)
        windows_content_layout.addWidget(self._cal_table)
        windows_outer.addWidget(windows_content)
        make_collapsible(windows_group, windows_content, expanded=True)
        side_layout.addWidget(windows_group)

        side_layout.addStretch(1)
        side_scroll.setWidget(side_container)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)
        return tab

    def import_files(self, paths: list[str]) -> None:
        """Entry point for MainWindow's Open.../drag-and-drop, forwarded here
        when this mode is active."""
        self._import_panel.add_files(paths)

    # -- shared plumbing across tabs ---------------------------------------
    def _files(self) -> list[dict]:
        return self._app_state.files_for(_FILES_KEY)

    def _active_frec(self) -> dict | None:
        files = self._files()
        if not files or not (0 <= self._active_file_index < len(files)):
            return None
        return files[self._active_file_index]

    def _on_files_changed(self, files_key: str) -> None:
        if files_key != _FILES_KEY:
            return
        self._active_file_index = 0
        self._refresh_dataset_list()
        self._timeseries_panel.refresh()

    def _on_cpdf_changed(self, files_key: str, file_index: int) -> None:
        if files_key != _FILES_KEY:
            return
        self._timeseries_panel.refresh()
        if file_index == self._active_file_index:
            self._load_active_cpdf_into_table()

    def _refresh_dataset_list(self) -> None:
        files = self._files()
        self._dataset_combo.blockSignals(True)
        self._dataset_combo.clear()
        self._dataset_combo.addItems([f["filename"] for f in files])
        self._dataset_combo.blockSignals(False)
        if files:
            self._dataset_combo.setCurrentIndex(min(self._active_file_index, len(files) - 1))
        self._on_dataset_changed(self._dataset_combo.currentIndex())

    def _on_dataset_changed(self, index: int) -> None:
        self._active_file_index = index
        frec = self._active_frec()
        self._rebuild_channel_editor(frec)
        if frec is None:
            return
        self._autodetect_panel.set_active_file(frec, self._active_file_index)
        self._load_active_cpdf_into_table()

    def _rebuild_channel_editor(self, frec: dict | None) -> None:
        if self._channel_editor is not None:
            self._chan_content_layout.removeWidget(self._channel_editor)
            self._channel_editor.deleteLater()
            self._channel_editor = None
        if frec is None:
            return
        self._channel_editor = _FileChannelEditor(frec["filename"], frec["df"], frec["channels"], "Potential", self)
        self._chan_content_layout.insertWidget(0, self._channel_editor)

    def _apply_channel_assignment(self) -> None:
        if self._channel_editor is None:
            return
        frec = self._active_frec()
        if frec is None:
            return
        new_files = list(self._files())
        new_files[self._active_file_index] = {**frec, "channels": self._channel_editor.channels()}
        cmd = FilesListCommand(self._app_state, _FILES_KEY, new_files, text="Apply channel assignment")
        self._app_state.undo_stack.push(cmd)

    def _load_active_cpdf_into_table(self) -> None:
        frec = self._active_frec()
        if frec is None:
            return
        self._cal_table.set_dataframe(frec["cpdf"])

    def _commit_active_cpdf(self) -> None:
        frec = self._active_frec()
        if frec is None:
            return
        new_cpdf = self._cal_table.dataframe()
        cmd = TableEditCommand(self._app_state, _FILES_KEY, self._active_file_index, new_cpdf, text="Edit calibration table")
        self._app_state.undo_stack.push(cmd)

    # -- Nernstian fit (mirrors modes.solid_state.render()'s
    #    _do_compute_calibration_solid closure, driven by AppState) --------
    def _compute_calibration(self) -> None:
        selected_labels = self._timeseries_panel.visible_labels()
        if not selected_labels:
            self._cal_status.setText("Check at least one channel in ② Time Series & Windows.")
            return

        multi_file = len(self._files()) > 1
        combo_lookup = {
            _amp_label(frec["filename"], ch["name"], multi_file): (frec, ch)
            for frec in self._files() for ch in frec["channels"]
        }
        signal_unit = self._app_state.get_field(_UNIT_KEY)
        ion_charge = self._ion_charge.value()
        lab_temp = self._lab_temp.value()

        results: dict[str, dict] = {}
        warnings: list[str] = []
        for ch_name in selected_labels:
            frec, ch = combo_lookup[ch_name]
            cpdf = frec["cpdf"].copy()
            if cpdf.empty:
                warnings.append(f"{ch_name}: calibration table is empty.")
                continue
            rejected = ~(cpdf["Concentration"].astype(float) > 0)
            if rejected.any():
                warnings.append(f"{ch_name}: {int(rejected.sum())} row(s) with Concentration ≤ 0 excluded.")
                cpdf = cpdf[~rejected].reset_index(drop=True)
            if cpdf.empty:
                warnings.append(f"{ch_name}: no valid calibration rows.")
                continue

            df = frec["df"]
            t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
            e_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)

            readings = []
            for _, row in cpdf.iterrows():
                if pd.notna(row.get("Reading_mV")):
                    readings.append(float(row["Reading_mV"]))
                    continue
                ets = _eff_t_start(row)
                if ets is None or pd.isna(row.get("t_end")):
                    readings.append(np.nan)
                    continue
                mask = (t_arr >= ets) & (t_arr <= row["t_end"])
                pts = e_arr[mask]
                pts = pts[~np.isnan(pts)]
                readings.append(float(np.mean(pts)) if pts.size > 0 else np.nan)

            log_conc = np.log10(cpdf["Concentration"].astype(float).to_numpy())
            potential = np.array(readings, dtype=float)
            valid = ~np.isnan(potential)
            if valid.sum() < 2:
                warnings.append(f"{ch_name}: fewer than 2 valid readings.")
                continue

            lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
            nernst_seg = lod_fit["nernstian_segment"]
            ideal = ideal_slope_in_signal_unit(lab_temp, ion_charge, signal_unit)

            results[ch_name] = dict(
                concs=cpdf["Concentration"].astype(float).tolist(),
                labels=cpdf["Label"].tolist(),
                valid_mask=valid.tolist(),
                log_conc=log_conc[valid].tolist(),
                potential_mv=potential[valid].tolist(),
                low_segment=lod_fit["low_segment"],
                nernstian_segment=nernst_seg,
                lod_log10=lod_fit["lod_log10"],
                lod_conc=lod_fit["lod_conc"],
                sensitivity_mv_per_decade=nernst_seg["slope"] if nernst_seg else None,
                pct_of_ideal_nernstian=(
                    100.0 * abs(nernst_seg["slope"]) / ideal if (nernst_seg and ideal) else None
                ),
                ideal_slope_mv_per_decade=ideal,
                signal_unit=signal_unit,
                is_average=False,
            )

        if warnings:
            self._cal_status.setText(" | ".join(warnings))
        if not results:
            self._app_state.set_field("solid_cal_results", None)
            return
        self._app_state.set_field("solid_cal_results", dict(results=results))
        if not warnings:
            self._cal_status.setText("Calibration computed — results below.")
        self._render_calibration_curve(results)

    def _render_calibration_curve(self, res_map: dict) -> None:
        fig = go.Figure()
        stat_rows = []
        for j, (ch_name, res) in enumerate(res_map.items()):
            col = PAL[j % len(PAL)]
            x = np.asarray(res["log_conc"], dtype=float)
            y = np.asarray(res["potential_mv"], dtype=float)
            vmask = res.get("valid_mask", [True] * len(res["labels"]))
            labels_plot = np.asarray(res["labels"], dtype=object)[vmask]

            fig.add_trace(go.Scatter(x=x, y=y, name=ch_name, mode="markers+text",
                                      text=labels_plot, textposition="top center",
                                      marker=dict(color=col, size=10)))

            low, nern = res.get("low_segment"), res.get("nernstian_segment")
            lod_log10 = res.get("lod_log10")
            has_lod = lod_log10 is not None and np.isfinite(lod_log10)
            for seg, seg_name, dash in [(low, "low", "dot"), (nern, "Nernstian", "dash")]:
                if seg is None:
                    continue
                if seg_name == "low" and has_lod:
                    x0, x1 = float(np.min(x)), lod_log10
                elif seg_name == "Nernstian" and has_lod:
                    x0, x1 = lod_log10, float(np.max(x))
                else:
                    x0, x1 = float(np.min(x)), float(np.max(x))
                if x1 <= x0:
                    x0, x1 = float(np.min(x)), float(np.max(x))
                xp = np.linspace(x0, x1, 200)
                yp = seg["slope"] * xp + seg["intercept"]
                fig.add_trace(go.Scatter(x=xp, y=yp, name=f"{ch_name} {seg_name} fit", mode="lines",
                                          showlegend=False, line=dict(color=col, dash=dash, width=2)))

            if has_lod:
                fig.add_vline(x=lod_log10, line=dict(color=col, dash="dashdot", width=1.2),
                              annotation_text=f"{ch_name} LOD", annotation_position="top")

            ideal = res.get("ideal_slope_mv_per_decade")
            sunit = res.get("signal_unit", self._app_state.get_field(_UNIT_KEY))
            stat_rows.append({
                "Channel": ch_name,
                f"Sensitivity ({sunit}/decade)": fmt(res.get("sensitivity_mv_per_decade")),
                "% of ideal Nernstian": (
                    f"{res['pct_of_ideal_nernstian']:.1f}%" if res.get("pct_of_ideal_nernstian") is not None
                    else ("—" if ideal is not None else "unit not recognized")
                ),
                f"Ideal ({sunit}/decade)": fmt(ideal),
                "R² (Nernstian)": (f"{nern['r2']:.4f}" if nern else "—"),
                f"LOD ({self._app_state.get_field(_CONC_UNIT_KEY)})": fmt(res.get("lod_conc")),
            })

        theme = plot_theme()
        fig.update_layout(
            xaxis_title=f"log₁₀(Concentration [{self._app_state.get_field(_CONC_UNIT_KEY)}])",
            yaxis_title=f"Potential ({self._app_state.get_field(_UNIT_KEY)})",
            hovermode="closest", height=480, template=theme["template"],
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        self._cal_plot.set_figure(fig)
        self._last_cal_figure = fig

        self._stats_table.clear()
        if stat_rows:
            columns = list(stat_rows[0].keys())
            self._stats_table.setColumnCount(len(columns))
            self._stats_table.setHorizontalHeaderLabels(columns)
            self._stats_table.setRowCount(len(stat_rows))
            for r, row in enumerate(stat_rows):
                for c, col in enumerate(columns):
                    self._stats_table.setItem(r, c, QTableWidgetItem(str(row[col])))

    # -- export ---------------------------------------------------------------
    def _export_csv(self) -> None:
        results = self._app_state.data.solid_cal_results
        if not results:
            self._cal_status.setText("Run calibration analysis first.")
            return
        rows = []
        for ch_name, res in results["results"].items():
            for lbl, conc in zip(res["labels"], res["concs"]):
                rows.append({"Channel": ch_name, "Label": lbl, f"Concentration ({self._app_state.get_field(_CONC_UNIT_KEY)})": conc})
        path, _ = QFileDialog.getSaveFileName(self, "Export calibration CSV", "solid_state_calibration_data.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    def _export_curve_png(self) -> None:
        results = self._app_state.data.solid_cal_results
        if not results:
            self._cal_status.setText("Run calibration analysis first.")
            return
        opts = ExportOptionsDialog.get_options(self, "Export calibration curve")
        if opts is None:
            return
        png_bytes = render_solid_cal_png(
            results["results"], self._app_state.get_field(_CONC_UNIT_KEY), self._app_state.get_field(_UNIT_KEY),
            dpi=opts["dpi"], fmt=opts["fmt"], figsize=opts["figsize"], style=opts["style"],
        )
        ext = opts["fmt"]
        path, _ = QFileDialog.getSaveFileName(self, "Export calibration curve", f"solid_state_calibration_curve.{ext}", f"{ext.upper()} (*.{ext})")
        if path:
            with open(path, "wb") as f:
                f.write(png_bytes)
