"""
AmperometryView — the Amperometry mode's Qt view (Phase 5).

Reuses Phase 4's ImportPanel/TimeSeriesPanel/AutodetectPanel unchanged
(both modes share the exact same Import/Time-Series UI shape in the
Streamlit app too), and calls modes/amperometry.py's fit/PNG functions
directly (piecewise_fit, _apply_effective_concentration, render_cal_png) —
none of them touched.

Scope note (like Phase 4's ImportPanel note): the Streamlit version also
has a "Quick-fill: common calibration protocols" preset expander, an
"Averaging window details" results expander, interactive-HTML downloads,
and a publication-quality (SVG/PDF/TIFF, DPI/size) export panel. This pass
covers the mode's defining features versus Solid-State — baseline
subtraction, segmented-linear fits, the channel-average trace, and the
effective-concentration dilution calculator — plus PNG export; the
remaining UI is straightforward follow-up on the same patterns already
established (ImportPanel, PlotView, EditableTableView).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.calibration_table import _baseline_keep_mask, _default_cpdf
from core.constants import AVG_COLOR, PAL, fmt
from core.numeric import _eff_t_start, smooth_signal, to_num
from core.shared_tabs import _amp_label
from macos_app.ui.app_state import AppState
from macos_app.ui.theme import plot_theme
from macos_app.ui.undo_commands import SetFieldCommand, TableEditCommand
from macos_app.ui.widgets.autodetect_panel import AutodetectPanel
from macos_app.ui.widgets.comparison_view import ComparisonView
from macos_app.ui.widgets.editable_table_view import EditableTableView
from macos_app.ui.widgets.import_panel import ImportPanel
from macos_app.ui.widgets.plot_view import PlotView
from macos_app.ui.widgets.timeseries_panel import TimeSeriesPanel
from modes.amperometry import (
    _apply_effective_concentration,
    _cpdf_from_autodetect_windows,
    _load_sample_data,
    piecewise_fit,
    render_cal_png,
)

_FILES_KEY = "amp_files"
_UNIT_KEY = "cur_unit"
_CONC_UNIT_KEY = "conc_unit"
_CPDF_COLUMNS = ["Label", "Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration", "Baseline"]


def _compute_file_fit(frec: dict, app_state: AppState) -> dict | None:
    """Comparison-tab adapter (see comparison_view.py): one independent
    linear fit for this file alone, using its own calibration table and
    first channel — not mixed with any other file's channels, unlike the
    Calibration Curve tab's cross-file "Channels to analyse" selector.
    Deliberately simple (always Linear, first channel only) since this is
    a quick side-by-side comparison, not the full analysis workbench."""
    channels = frec.get("channels", [])
    if not channels:
        return None
    ch = channels[0]
    cpdf = frec["cpdf"].dropna(subset=["t_end"]).reset_index(drop=True)
    if cpdf.empty:
        return None

    base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
    base_idx = int(base_rows.index[0]) if len(base_rows) else 0

    df = frec["df"]
    t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
    i_arr = smooth_signal(i_arr, app_state.data.smooth_method, app_state.data.smooth_window, app_state.data.smooth_polyorder)

    avgs = []
    for _, row in cpdf.iterrows():
        ets = _eff_t_start(row)
        if ets is None:
            avgs.append(np.nan)
            continue
        mask = (t_arr >= ets) & (t_arr <= row["t_end"])
        pts = i_arr[mask]
        pts = pts[~np.isnan(pts)]
        avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)

    base_val = avgs[base_idx]
    if np.isnan(base_val):
        return None
    delta_i = [(v - base_val) if not np.isnan(v) else np.nan for v in avgs]

    keep = _baseline_keep_mask(cpdf["Baseline"].tolist())
    x = np.asarray(cpdf["Concentration"].values, dtype=float)[keep]
    y = np.asarray(delta_i, dtype=float)[keep]
    fit_result = piecewise_fit(x, y, 1)
    if not fit_result["segments"]:
        return None
    seg = fit_result["segments"][0]
    curve_x = np.linspace(seg["xr"][0], seg["xr"][1], 100)
    curve_y = seg["slope"] * curve_x + seg["intercept"]

    conc_unit, cur_unit = app_state.get_field(_CONC_UNIT_KEY), app_state.get_field(_UNIT_KEY)
    return {
        "x": x.tolist(), "y": y.tolist(), "curve_x": curve_x.tolist(), "curve_y": curve_y.tolist(),
        "stats": {
            "File": frec["filename"], "Channel": ch["name"],
            f"Sensitivity ({cur_unit}/{conc_unit})": fmt(seg["slope"]),
            "R²": f"{seg['r2']:.4f}",
        },
    }


class AmperometryView(QWidget):
    def __init__(self, app_state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._active_file_index = 0
        self._last_cal_results: dict | None = None

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        outer.addWidget(tabs)

        # -- Tab 1: Import & Configure -----------------------------------
        self._import_panel = ImportPanel(
            app_state, _FILES_KEY, "Current", _UNIT_KEY, _CONC_UNIT_KEY,
            seed_cpdf_fn=_default_cpdf,
            sample_loader_fn=_load_sample_data,
            sample_caption="Two synthetic amperometric runs (2 channels each) with a ready-made calibration table.",
        )
        tabs.addTab(self._import_panel, "① Import & Configure")

        # -- Tab 2: Time Series -------------------------------------------
        self._timeseries_panel = TimeSeriesPanel(app_state, _FILES_KEY, _UNIT_KEY, "Current")
        tabs.addTab(self._timeseries_panel, "② Time Series")

        # -- Tab 3: Calibration Curve ---------------------------------------
        cal_tab = QWidget(self)
        cal_layout = QVBoxLayout(cal_tab)

        dataset_row = QHBoxLayout()
        dataset_row.addWidget(QLabel("Dataset:", self))
        self._dataset_combo = QComboBox(self)
        self._dataset_combo.currentIndexChanged.connect(self._on_dataset_changed)
        dataset_row.addWidget(self._dataset_combo, 1)
        cal_layout.addLayout(dataset_row)

        settings_group = QGroupBox("Analysis Settings", self)
        settings_form = QFormLayout(settings_group)
        self._channels_list = QListWidget(self)
        self._channels_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self._channels_list.setMaximumHeight(80)
        self._channels_list.itemSelectionChanged.connect(self._on_channel_selection_changed)
        settings_form.addRow("Channels to analyse", self._channels_list)
        self._fit_type_combo = QComboBox(self)
        self._fit_type_combo.addItems(["Linear", "Segmented Linear"])
        self._fit_type_combo.currentTextChanged.connect(self._on_fit_type_changed)
        settings_form.addRow("Fit type", self._fit_type_combo)
        self._n_seg_spin = QSpinBox(self)
        self._n_seg_spin.setRange(2, 4)
        self._n_seg_spin.setValue(2)
        self._n_seg_spin.setEnabled(False)
        settings_form.addRow("Segments", self._n_seg_spin)
        self._show_avg_checkbox = QCheckBox("Add channel average trace", self)
        self._show_avg_checkbox.setEnabled(False)
        settings_form.addRow(self._show_avg_checkbox)
        cal_layout.addWidget(settings_group)

        self._autodetect_panel = AutodetectPanel(app_state, _FILES_KEY, _cpdf_from_autodetect_windows, has_baseline=True)
        self._autodetect_panel.edges_detected.connect(self._timeseries_panel.refresh)
        cal_layout.addWidget(self._autodetect_panel)

        cal_layout.addWidget(QLabel("Calibration Points", self))
        self._cal_table = EditableTableView(
            _default_cpdf(),
            editable_columns=set(_CPDF_COLUMNS),
            row_defaults={"Label": "New", "Concentration": 0.0, "Spike Vol": np.nan, "Stock Conc": np.nan,
                          "t_start": 0.0, "t_end": 60.0, "avg_duration": np.nan, "Baseline": False},
        )
        self._cal_table.row_committed.connect(self._commit_active_cpdf)
        cal_layout.addWidget(self._cal_table)

        effconc_group = QGroupBox("Effective concentration calculator (serial dilution)", self)
        effconc_form = QFormLayout(effconc_group)
        self._initial_volume_spin = QDoubleSpinBox(self)
        self._initial_volume_spin.setRange(0.0, 1e9)
        self._initial_volume_spin.setDecimals(5)
        self._initial_volume_spin.setValue(app_state.data.initial_volume)
        effconc_form.addRow("Initial volume", self._initial_volume_spin)
        self._vol_unit_edit = QLineEdit(app_state.data.vol_unit, self)
        effconc_form.addRow("Volume unit", self._vol_unit_edit)
        preview_btn = QPushButton("Preview: update Concentration & t start", self)
        preview_btn.clicked.connect(self._preview_effective_concentration)
        effconc_form.addRow(preview_btn)
        cal_layout.addWidget(effconc_group)

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

        tabs.addTab(cal_tab, "③ Calibration Curve")

        # -- Tab 4: Export --------------------------------------------------
        export_tab = QWidget(self)
        export_layout = QVBoxLayout(export_tab)
        csv_btn = QPushButton("Export calibration summary CSV", self)
        csv_btn.clicked.connect(self._export_csv)
        export_layout.addWidget(csv_btn)
        png_btn = QPushButton("Export calibration curve PNG", self)
        png_btn.clicked.connect(self._export_curve_png)
        export_layout.addWidget(png_btn)
        export_layout.addStretch(1)
        tabs.addTab(export_tab, "④ Export")

        # -- Tab 5: Comparison (Phase 8 QoL enhancement) -------------------
        comparison_tab = ComparisonView(
            app_state, _FILES_KEY, _compute_file_fit,
            x_label=f"Concentration ({app_state.get_field(_CONC_UNIT_KEY)})",
            y_label=f"ΔI ({app_state.get_field(_UNIT_KEY)})",
        )
        tabs.addTab(comparison_tab, "⑤ Compare Files")

        app_state.files_changed.connect(self._on_files_changed)
        app_state.cpdf_changed.connect(self._on_cpdf_changed)
        self._refresh_dataset_list()

    def import_files(self, paths: list[str]) -> None:
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
        self._refresh_channels_list()
        self._on_dataset_changed(self._dataset_combo.currentIndex())

    def _refresh_channels_list(self) -> None:
        """"Channels to analyse" spans every loaded file (matching
        modes/amperometry.py's _cal_combo_lookup, built over all of
        SS.amp_files) — independent of which file the Dataset selector has
        active for editing its calibration table below."""
        multi_file = len(self._files()) > 1
        self._channels_list.clear()
        for frec in self._files():
            for ch in frec.get("channels", []):
                label = _amp_label(frec["filename"], ch["name"], multi_file)
                item = QListWidgetItem(label, self._channels_list)
                item.setSelected(True)

    def _on_dataset_changed(self, index: int) -> None:
        if index < 0:
            return
        self._active_file_index = index
        frec = self._active_frec()
        if frec is None:
            return
        self._autodetect_panel.set_active_file(frec, self._active_file_index)
        self._load_active_cpdf_into_table()

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

    def _on_fit_type_changed(self, text: str) -> None:
        self._n_seg_spin.setEnabled(text == "Segmented Linear")

    def _on_channel_selection_changed(self) -> None:
        self._show_avg_checkbox.setEnabled(len(self._channels_list.selectedItems()) >= 2)

    # -- effective concentration calculator ----------------------------------
    def _preview_effective_concentration(self) -> None:
        frec = self._active_frec()
        if frec is None:
            return
        self._app_state.set_field("initial_volume", self._initial_volume_spin.value())
        self._app_state.set_field("vol_unit", self._vol_unit_edit.text())
        current_df = self._cal_table.dataframe()
        new_cpdf = _apply_effective_concentration(current_df, self._initial_volume_spin.value())
        cmd = TableEditCommand(self._app_state, _FILES_KEY, self._active_file_index, new_cpdf, text="Preview effective concentration")
        self._app_state.undo_stack.push(cmd)
        self._cal_status.setText("Concentration / t start updated above.")

    # -- Compute (mirrors modes.amperometry.render()'s _do_compute_calibration
    #    closure, driven by AppState instead of st.session_state) ------------
    def _compute_calibration(self) -> None:
        selected_labels = [item.text() for item in self._channels_list.selectedItems()]
        if not selected_labels:
            self._cal_status.setText("Select at least one channel to analyse above.")
            return

        multi_file = len(self._files()) > 1
        combo_lookup = {
            _amp_label(frec["filename"], ch["name"], multi_file): (frec, ch)
            for frec in self._files() for ch in frec["channels"]
        }
        fit_type = self._fit_type_combo.currentText()
        n_seg = self._n_seg_spin.value() if fit_type == "Segmented Linear" else 1
        show_avg = self._show_avg_checkbox.isChecked() and len(selected_labels) >= 2

        results: dict[str, dict] = {}
        warnings: list[str] = []
        for ch_name in selected_labels:
            frec, ch = combo_lookup[ch_name]
            cpdf = frec["cpdf"].dropna(subset=["t_end"]).reset_index(drop=True)
            if cpdf.empty:
                warnings.append(f"{ch_name}: no valid calibration rows.")
                continue

            base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
            base_idx = int(base_rows.index[0]) if len(base_rows) else 0
            if len(base_rows) == 0:
                warnings.append(f"{ch_name}: no baseline row marked — using the first row as baseline.")

            df = frec["df"]
            t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
            i_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            i_arr = smooth_signal(i_arr, self._app_state.data.smooth_method, self._app_state.data.smooth_window, self._app_state.data.smooth_polyorder)

            avgs, sigs, n_pts, t_starts_used = [], [], [], []
            for _, row in cpdf.iterrows():
                ets = _eff_t_start(row)
                t_starts_used.append(ets)
                if ets is None:
                    avgs.append(np.nan); sigs.append(np.nan); n_pts.append(0)
                    continue
                mask = (t_arr >= ets) & (t_arr <= row["t_end"])
                pts = i_arr[mask]
                pts = pts[~np.isnan(pts)]
                n_pts.append(int(pts.size))
                avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
                sigs.append(float(np.std(pts, ddof=1)) if pts.size >= 2 else np.nan)

            base_val = avgs[base_idx]
            sigma_bl = sigs[base_idx]
            if np.isnan(base_val):
                warnings.append(f"{ch_name}: baseline window has no data points — ΔI cannot be computed.")
                continue
            delta_i = [(v - base_val) if not np.isnan(v) else np.nan for v in avgs]

            results[ch_name] = dict(
                concs=cpdf["Concentration"].values.astype(float),
                labels=cpdf["Label"].values,
                avgs=avgs, sigs=sigs, delta_i=delta_i,
                sigma_bl=float(sigma_bl), is_average=False,
                n_pts=n_pts, t_starts_used=t_starts_used,
                t_ends=cpdf["t_end"].tolist(), baselines=cpdf["Baseline"].tolist(),
            )

        avg_chs = [c for c in selected_labels if c in results]
        if show_avg and len(avg_chs) >= 2:
            all_di = np.array([results[c]["delta_i"] for c in avg_chs], dtype=float)
            all_avgs = np.array([results[c]["avgs"] for c in avg_chs], dtype=float)
            all_sigma = [results[c]["sigma_bl"] for c in avg_chs]
            n_ch = len(avg_chs)
            avg_delta_i = np.nanmean(all_di, axis=0)
            std_across_ch = np.nanstd(all_di, axis=0, ddof=1)
            avg_avgs = np.nanmean(all_avgs, axis=0)
            valid_s = [s for s in all_sigma if np.isfinite(s)]
            sigma_bl_avg = (np.sqrt(sum(s ** 2 for s in valid_s)) / n_ch) if valid_s else np.nan
            results["Channel Average"] = dict(
                concs=results[avg_chs[0]]["concs"], labels=results[avg_chs[0]]["labels"],
                avgs=avg_avgs.tolist(), sigs=std_across_ch.tolist(), delta_i=avg_delta_i.tolist(),
                sigma_bl=float(sigma_bl_avg), is_average=True, baselines=results[avg_chs[0]]["baselines"],
            )

        if warnings:
            self._cal_status.setText(" | ".join(warnings))
        if not results:
            self._app_state.set_field("cal_results", None)
            return
        self._app_state.set_field("cal_results", dict(results=results, fit_type=fit_type, n_seg=n_seg))
        if not warnings:
            self._cal_status.setText("Calibration computed — results below.")
        self._render_calibration_curve(results, fit_type, n_seg)

    def _render_calibration_curve(self, res_map: dict, fit_type: str, n_seg: int) -> None:
        fig = go.Figure()
        stat_rows = []
        conc_unit = self._app_state.get_field(_CONC_UNIT_KEY)
        cur_unit = self._app_state.get_field(_UNIT_KEY)

        for j, (ch_name, res) in enumerate(res_map.items()):
            is_avg = res.get("is_average", False)
            col = AVG_COLOR if is_avg else PAL[j % len(PAL)]
            keep = _baseline_keep_mask(res.get("baselines", [False] * len(res["concs"])))
            x = np.asarray(res["concs"], dtype=float)[keep]
            y = np.array(res["delta_i"], float)[keep]
            labels_plot = np.asarray(res["labels"], dtype=object)[keep]
            sigs_plot = np.asarray(res["sigs"], dtype=float)[keep]
            marker_sym = "diamond" if is_avg else "circle"

            fig.add_trace(go.Scatter(
                x=x, y=y, name=ch_name, mode="markers+text",
                text=labels_plot, textposition="top center",
                marker=dict(color=col, size=10, symbol=marker_sym),
                error_y=dict(type="data", array=[float(s) if (s and not np.isnan(s)) else 0.0 for s in sigs_plot],
                             visible=is_avg, color=col),
            ))

            pf = piecewise_fit(x, y, int(n_seg) if fit_type == "Segmented Linear" else 1)
            segs, breakpoints = pf["segments"], pf["breakpoints"]
            for k, seg in enumerate(segs):
                xp = np.linspace(seg["xr"][0], seg["xr"][1], 300)
                yp = seg["slope"] * xp + seg["intercept"]
                lbl = ch_name + (f" seg {k + 1}" if len(segs) > 1 else "")
                fig.add_trace(go.Scatter(x=xp, y=yp, name=f"{lbl} fit", mode="lines", showlegend=False,
                                          line=dict(color=col, dash="dot" if is_avg else "dash", width=2)))

                sigma, sens, intcpt = res["sigma_bl"], seg["slope"], seg["intercept"]
                lod_val = ((3.3 * abs(sigma) - intcpt) / sens) if sens else np.nan
                loq_val = ((10.0 * abs(sigma) - intcpt) / sens) if sens else np.nan
                stat_rows.append({
                    "Channel": ch_name,
                    "Segment": (f"{seg['xr'][0]:.3g}–{seg['xr'][1]:.3g} {conc_unit}" if len(segs) > 1 else "Full range"),
                    f"Sensitivity ({cur_unit}/{conc_unit})": fmt(sens),
                    "R²": f"{seg['r2']:.4f}",
                    f"LOD ({conc_unit})": fmt(lod_val),
                    f"LOQ ({conc_unit})": fmt(loq_val),
                })

            for bp in breakpoints:
                fig.add_vline(x=bp, line_dash="dot", line_color=col, annotation_text=f"{bp:.3g} {conc_unit}")

        theme = plot_theme()
        fig.update_layout(
            xaxis_title=f"Concentration ({conc_unit})", yaxis_title=f"ΔI ({cur_unit})",
            hovermode="closest", height=480, template=theme["template"],
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        self._cal_plot.set_figure(fig)
        self._last_cal_results = dict(results=res_map, fit_type=fit_type, n_seg=n_seg)

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
        results = self._app_state.data.cal_results
        if not results:
            self._cal_status.setText("Run calibration analysis first.")
            return
        conc_unit = self._app_state.get_field(_CONC_UNIT_KEY)
        cur_unit = self._app_state.get_field(_UNIT_KEY)
        rows = []
        for ch_name, res in results["results"].items():
            for lbl, conc, avg, sig, di in zip(res["labels"], res["concs"], res["avgs"], res["sigs"], res["delta_i"]):
                rows.append({
                    "Channel": ch_name, "Label": lbl, f"Concentration ({conc_unit})": conc,
                    f"Avg Current ({cur_unit})": avg, f"SD ({cur_unit})": sig, f"ΔI ({cur_unit})": di,
                })
        path, _ = QFileDialog.getSaveFileName(self, "Export calibration CSV", "calibration_data.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    def _export_curve_png(self) -> None:
        results = self._app_state.data.cal_results
        if not results:
            self._cal_status.setText("Run calibration analysis first.")
            return
        png_bytes = render_cal_png(
            results["results"], results["fit_type"], int(results["n_seg"]),
            self._app_state.get_field(_CONC_UNIT_KEY), self._app_state.get_field(_UNIT_KEY),
        )
        path, _ = QFileDialog.getSaveFileName(self, "Export calibration curve PNG", "calibration_curve.png", "PNG image (*.png)")
        if path:
            with open(path, "wb") as f:
                f.write(png_bytes)
