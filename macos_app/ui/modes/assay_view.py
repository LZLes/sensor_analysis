"""
AssayView — Assay (microplate / 4PL) mode's Qt view (Phase 7).

Self-contained like Cyclic Voltammetry (Assay doesn't use core/shared_tabs.py
in the Streamlit app either). Reuses modes/assay.py's parse_plate_csv,
_fit_4pl, _4pl_inv, render_assay_curve, _well_rc, _plate_get, _PLATE_ROWS
directly, unmodified — all pure.

_plate_fig is the one exception: it's otherwise pure but calls
core.constants._plot_theme() internally (reads st.context.theme.type), so
like the other interactive-chart builders in this app it can't be imported
as-is (it would need a running Streamlit script context). _plate_figure()
below is new code covering the same visual content via
macos_app.ui.theme.plot_theme() instead — same pattern as every other
mode's interactive figures in this app.

Scope note: the Streamlit version's "Publication-quality export" panel
(SVG/PDF/TIFF, style/DPI/size picker) is deferred, same honest-cut level
as Amperometry/Solid-State's Compute tab — this covers plain 150dpi PNG
export via the unmodified render_assay_curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.constants import PAL, fmt
from core.numeric import lin_reg
from macos_app.ui.app_state import AppState
from macos_app.ui.theme import plot_theme
from macos_app.ui.undo_commands import SetFieldCommand
from macos_app.ui.widgets.editable_table_view import EditableTableView
from macos_app.ui.widgets.pandas_table_model import PandasTableModel
from macos_app.ui.widgets.plot_view import PlotView
from modes.assay import (
    _4pl_inv,
    _fit_4pl,
    _plate_get,
    _well_rc,
    _PLATE_ROWS,
    parse_plate_csv,
    render_assay_curve,
)

_STD_COLUMNS = ["Label", "Conc", "S1", "S2", "S3"]
_SAMPLE_COLUMNS = ["Well", "Label"]


def _empty_plate() -> pd.DataFrame:
    return pd.DataFrame(np.full((8, 12), np.nan), index=pd.Index(_PLATE_ROWS, name="Row"), columns=pd.Index(range(1, 13), name="Col"))


def _build_std_wells_map(std_df: pd.DataFrame) -> dict:
    m: dict = {}
    for i, row in std_df.iterrows():
        is_blank = i == 0
        for s_idx, s_col in [(1, "S1"), (2, "S2"), (3, "S3")]:
            w = str(row.get(s_col, "")).strip().upper()
            if w and _well_rc(w):
                m[w] = {"set": s_idx, "conc": float(row.get("Conc", 0) or 0), "label": str(row.get("Label", "")), "is_blank": is_blank}
    return m


def _build_sample_map(sample_df: pd.DataFrame) -> dict:
    m: dict = {}
    for _, row in sample_df.iterrows():
        w = str(row.get("Well", "")).strip().upper()
        if w:
            m[w] = str(row.get("Label", w))
    return m


def _plate_figure(plate_df: pd.DataFrame | None, std_wells: dict, sample_map: dict, conc_unit: str, sig_unit: str) -> go.Figure:
    """New code mirroring modes/assay.py's _plate_fig visual content — see
    this module's docstring for why it isn't imported directly."""
    set_cols = {1: "rgba(70,130,220,0.85)", 2: "rgba(50,200,120,0.85)", 3: "rgba(220,80,80,0.85)"}
    blank_col, sample_col, empty_col = "rgba(255,152,0,0.90)", "rgba(160,100,220,0.75)", "rgba(80,80,80,0.35)"

    xs, ys, txts, hovs, cols = [], [], [], [], []
    for ri, row_lbl in enumerate(_PLATE_ROWS):
        for ci in range(12):
            well = f"{row_lbl}{ci + 1}"
            val = _plate_get(plate_df, well)
            val_s = f"{val:.4g}" if np.isfinite(val) else "—"
            if well in std_wells:
                info = std_wells[well]
                col = blank_col if info["is_blank"] else set_cols.get(info["set"], set_cols[1])
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}<br>Std: {info['label']} ({info['conc']} {conc_unit})<br>Set {info['set']}")
            elif well in sample_map:
                col = sample_col
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}<br>Sample: {sample_map[well]}")
            else:
                col = empty_col
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}")
            xs.append(ci + 1)
            ys.append(7 - ri)
            txts.append(val_s if np.isfinite(val) else "")
            cols.append(col)

    fig = go.Figure(go.Scatter(x=xs, y=ys, mode="markers+text", text=txts, textposition="middle center",
                                textfont=dict(size=6.5, color="rgba(255,255,255,0.92)"), hovertext=hovs, hoverinfo="text",
                                marker=dict(color=cols, size=30, symbol="circle", line=dict(width=0.5, color="rgba(255,255,255,0.15)")),
                                showlegend=False))
    for ltxt, lcol in [("Blank", blank_col), ("Set 1", set_cols[1]), ("Set 2", set_cols[2]), ("Set 3", set_cols[3]), ("Sample", sample_col), ("—", empty_col)]:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(color=lcol, size=10, symbol="circle"), name=ltxt, showlegend=True))
    theme = plot_theme()
    fig.update_layout(
        height=345, template=theme["template"], paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(tickmode="array", tickvals=list(range(1, 13)), ticktext=[str(i) for i in range(1, 13)], showgrid=False, zeroline=False, range=[0.3, 12.7]),
        yaxis=dict(tickmode="array", tickvals=list(range(8)), ticktext=list(reversed(_PLATE_ROWS)), showgrid=False, zeroline=False, range=[-0.5, 7.5]),
        legend=dict(orientation="h", x=0, y=-0.12, xanchor="left", font=dict(size=9)),
        margin=dict(l=35, r=15, t=15, b=50),
    )
    return fig


class AssayView(QWidget):
    def __init__(self, app_state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        outer.addWidget(tabs)

        tabs.addTab(self._build_import_tab(), "① Import")
        tabs.addTab(self._build_standards_tab(), "② Standards")
        tabs.addTab(self._build_curve_tab(), "③ Standard Curve")
        tabs.addTab(self._build_results_tab(), "④ Results & Export")

        self._refresh_all()

    def import_files(self, paths: list[str]) -> None:
        if paths:
            self._load_plate_file(paths[0])

    # -- Tab 1: Import -----------------------------------------------------
    def _build_import_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        units_group = QGroupBox("Units", self)
        units_form = QFormLayout(units_group)
        self._sig_unit_edit = QLineEdit(self._app_state.data.assay_sig_unit, self)
        self._sig_unit_edit.editingFinished.connect(lambda: self._commit_unit("assay_sig_unit", self._sig_unit_edit))
        self._conc_unit_edit = QLineEdit(self._app_state.data.assay_conc_unit, self)
        self._conc_unit_edit.editingFinished.connect(lambda: self._commit_unit("assay_conc_unit", self._conc_unit_edit))
        units_form.addRow("Signal unit", self._sig_unit_edit)
        units_form.addRow("Concentration unit", self._conc_unit_edit)
        layout.addWidget(units_group)

        browse_btn = QPushButton("Browse Plate File…", self)
        browse_btn.clicked.connect(self._browse_plate_file)
        layout.addWidget(browse_btn)
        self._import_status = QLabel("", self)
        layout.addWidget(self._import_status)

        layout.addWidget(QLabel("Manual entry — rows A–H, columns 1–12", self))
        self._plate_model = PandasTableModel(_empty_plate().reset_index(), editable_columns=set(range(1, 13)), parent=self)
        self._plate_view = QTableView(self)
        self._plate_view.setModel(self._plate_model)
        layout.addWidget(self._plate_view)
        apply_btn = QPushButton("Apply manual values", self)
        apply_btn.clicked.connect(self._apply_manual_plate)
        layout.addWidget(apply_btn)

        layout.addWidget(QLabel("Plate map", self))
        self._import_plate_plot = PlotView(self)
        self._import_plate_plot.setMinimumHeight(300)
        layout.addWidget(self._import_plate_plot)
        return tab

    def _commit_unit(self, field: str, edit: QLineEdit) -> None:
        value = edit.text()
        if value != self._app_state.get_field(field):
            self._app_state.undo_stack.push(SetFieldCommand(self._app_state, field, value, text=f"Set {field}"))

    def _browse_plate_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import plate file", "", "Plate data (*.csv *.txt);;All files (*)")
        if path:
            self._load_plate_file(path)

    def _load_plate_file(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                raw = f.read().decode("utf-8", errors="replace")
            plate = parse_plate_csv(raw)
        except Exception as exc:  # noqa: BLE001
            self._import_status.setText(f"Parse error: {exc}")
            return
        cmd = SetFieldCommand(self._app_state, "assay_plate", plate, text="Import plate file")
        self._app_state.undo_stack.push(cmd)
        self._app_state.undo_stack.push(SetFieldCommand(self._app_state, "assay_std_res", None, text="Invalidate stale fit"))
        self._import_status.setText(f"Loaded — {int(plate.notna().sum().sum())} wells with data.")
        self._refresh_all()

    def _apply_manual_plate(self) -> None:
        edited = self._plate_model.dataframe()
        if "Row" in edited.columns:
            edited = edited.drop(columns=["Row"])
        edited.index = pd.Index(_PLATE_ROWS[:len(edited)], name="Row")
        edited.columns = pd.Index(range(1, len(edited.columns) + 1), name="Col")
        plate = edited.apply(pd.to_numeric, errors="coerce")
        cmd = SetFieldCommand(self._app_state, "assay_plate", plate, text="Apply manual plate values")
        self._app_state.undo_stack.push(cmd)
        self._app_state.undo_stack.push(SetFieldCommand(self._app_state, "assay_std_res", None, text="Invalidate stale fit"))
        self._import_status.setText("Plate values updated.")
        self._refresh_all()

    # -- Tab 2: Standards -----------------------------------------------------
    def _build_standards_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Standard concentrations & well positions (first row = Blank)", self))
        self._std_table = EditableTableView(
            self._app_state.data.assay_std_df, editable_columns=set(_STD_COLUMNS),
            row_defaults={"Label": "New", "Conc": 0.0, "S1": "", "S2": "", "S3": ""},
        )
        layout.addWidget(self._std_table)

        layout.addWidget(QLabel("Sample well labels (optional)", self))
        self._sample_table = EditableTableView(
            self._app_state.data.assay_sample_df, editable_columns=set(_SAMPLE_COLUMNS),
            row_defaults={"Well": "", "Label": ""},
        )
        layout.addWidget(self._sample_table)

        apply_btn = QPushButton("Apply layout", self)
        apply_btn.clicked.connect(self._apply_layout)
        layout.addWidget(apply_btn)
        self._layout_status = QLabel("", self)
        layout.addWidget(self._layout_status)

        layout.addWidget(QLabel("Layout preview", self))
        self._layout_plate_plot = PlotView(self)
        self._layout_plate_plot.setMinimumHeight(300)
        layout.addWidget(self._layout_plate_plot)
        return tab

    def _apply_layout(self) -> None:
        std_df = self._std_table.dataframe()
        sample_df = self._sample_table.dataframe()
        self._app_state.undo_stack.push(SetFieldCommand(self._app_state, "assay_std_df", std_df, text="Apply standards layout"))
        self._app_state.undo_stack.push(SetFieldCommand(self._app_state, "assay_sample_df", sample_df, text="Apply sample layout"))
        self._app_state.undo_stack.push(SetFieldCommand(self._app_state, "assay_std_res", None, text="Invalidate stale fit"))
        self._layout_status.setText("Layout saved — head to Standard Curve to fit the regression.")
        self._refresh_all()

    # -- Tab 3: Standard Curve -----------------------------------------------
    def _build_curve_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        settings_row = QHBoxLayout()
        self._fit_combo = QComboBox(self)
        self._fit_combo.addItems(["Linear", "Quadratic", "4-Parameter Logistic (4PL)"])
        settings_row.addWidget(QLabel("Fit type", self))
        settings_row.addWidget(self._fit_combo)
        self._show_reps_check = QCheckBox("Show individual replicates", self)
        self._show_reps_check.setChecked(True)
        settings_row.addWidget(self._show_reps_check)
        layout.addLayout(settings_row)

        compute_btn = QPushButton("Compute standard curve", self)
        compute_btn.clicked.connect(self._compute_standard_curve)
        layout.addWidget(compute_btn)
        self._curve_status = QLabel("", self)
        layout.addWidget(self._curve_status)

        self._curve_plot = PlotView(self)
        self._curve_plot.setMinimumHeight(340)
        layout.addWidget(self._curve_plot)

        layout.addWidget(QLabel("Standard summary", self))
        self._std_summary_table = QTableWidget(self)
        self._std_summary_table.setMaximumHeight(160)
        layout.addWidget(self._std_summary_table)

        export_btn = QPushButton("Export standard curve PNG", self)
        export_btn.clicked.connect(self._export_curve_png)
        layout.addWidget(export_btn)
        return tab

    def _compute_standard_curve(self) -> None:
        std_raw = self._app_state.data.assay_std_df
        plate = self._app_state.data.assay_plate
        if plate is None:
            self._curve_status.setText("Import plate data first.")
            return
        if len(std_raw) == 0 or pd.isna(std_raw["Conc"].iloc[0]):
            self._curve_status.setText("The first row (the Blank) needs a Concentration value filled in on the Standards tab.")
            return

        sdf = std_raw.dropna(subset=["Conc"]).reset_index(drop=True)
        if len(sdf) < 3:
            self._curve_status.setText("Need the blank plus at least 2 non-blank concentration levels to fit a curve.")
            return

        raw = np.array([[_plate_get(plate, str(row.get(sc, "")).strip().upper()) for sc in ["S1", "S2", "S3"]] for _, row in sdf.iterrows()], dtype=float)
        blank = float(np.nanmean(raw[0]))
        if not np.isfinite(blank):
            self._curve_status.setText("Blank row has no valid signal. Check well addresses in Standards.")
            return

        delta = raw - blank
        means = np.nanmean(delta, axis=1)
        sds = np.nanstd(delta, axis=1, ddof=1)
        concs = sdf["Conc"].values.astype(float)
        labels = sdf["Label"].values
        ok = np.isfinite(concs) & np.isfinite(means)
        ok[0] = False  # blank excluded from the fit itself, same as Amperometry's baseline point

        fit_label = self._fit_combo.currentText()
        fit: dict | None = None
        if fit_label == "Linear":
            lr = lin_reg(concs[ok], means[ok])
            if lr:
                fit = dict(type="linear", **lr)
        elif fit_label == "Quadratic":
            if ok.sum() >= 3:
                try:
                    coefs = np.polyfit(concs[ok], means[ok], 2)
                    yp = np.polyval(coefs, concs[ok])
                    sst = float(np.sum((means[ok] - means[ok].mean()) ** 2))
                    r2 = 1 - float(np.sum((means[ok] - yp) ** 2)) / sst if sst > 0 else 0.0
                    fit = dict(type="quad", a=float(coefs[0]), b=float(coefs[1]), c=float(coefs[2]), r2=r2)
                except Exception:  # noqa: BLE001
                    self._curve_status.setText("Quadratic fit failed.")
        else:
            fit = _fit_4pl(concs[ok], means[ok])
            if fit is None:
                lr = lin_reg(concs[ok], means[ok])
                if lr:
                    fit = dict(type="linear", **lr)
                self._curve_status.setText("4PL did not converge — fell back to Linear.")

        if fit is None:
            self._curve_status.setText("Regression failed — not enough valid data points.")
            return

        result = dict(fit=fit, concs=concs.tolist(), labels=labels.tolist(), means=means.tolist(), sds=sds.tolist(),
                      raw_arr=raw.tolist(), delta_arr=delta.tolist(), blank_mean=blank, std_df=sdf.to_dict(orient="records"))
        self._app_state.set_field("assay_std_res", result)
        if not self._curve_status.text().startswith("4PL"):
            self._curve_status.setText("Standard curve computed.")
        self._render_curve(result)

    def _render_curve(self, res: dict) -> None:
        fit = res["fit"]
        cx = np.array(res["concs"], float)
        my = np.array(res["means"], float)
        sy = np.array(res["sds"], float)
        da = np.array(res["delta_arr"], float)
        lb = np.array(res["labels"])
        vm = np.isfinite(my) & np.isfinite(cx)

        fig = go.Figure()
        if self._show_reps_check.isChecked():
            for si, col in enumerate([PAL[0], PAL[1], PAL[2]]):
                ry = da[:, si]
                vr = np.isfinite(ry) & np.isfinite(cx)
                if vr.any():
                    fig.add_trace(go.Scatter(x=cx[vr], y=ry[vr], name=f"Set {si + 1}", mode="markers",
                                              marker=dict(symbol="circle-open", size=9, color=col, line=dict(width=1.5))))
        fig.add_trace(go.Scatter(x=cx[vm], y=my[vm], name="Mean ± SD", mode="markers",
                                  marker=dict(symbol="circle", size=11, color="#4c96d7", line=dict(width=1.5, color="white")),
                                  error_y=dict(type="data", array=sy[vm].tolist(), visible=True, color="#4c96d7"),
                                  text=lb[vm], textposition="top center"))

        xp = np.linspace(max(0.0, float(cx[vm].min())), float(cx[vm].max()), 400)
        if fit["type"] == "linear":
            yp = fit["slope"] * xp + fit["intercept"]
            eq = f"y = {fit['slope']:.3g}x {'+ ' if fit['intercept'] >= 0 else '− '}{abs(fit['intercept']):.3g}   R² = {fit['r2']:.4f}"
        elif fit["type"] == "quad":
            yp = fit["a"] * xp ** 2 + fit["b"] * xp + fit["c"]
            eq = f"y = {fit['a']:.3g}x² + {fit['b']:.3g}x + {fit['c']:.3g}   R² = {fit['r2']:.4f}"
        else:
            yp = fit["d"] + (fit["a"] - fit["d"]) / (1 + (xp / fit["c"]) ** fit["b"])
            eq = f"4PL: a={fit['a']:.3g}  b={fit['b']:.3g}  c={fit['c']:.3g}  d={fit['d']:.3g}   R²={fit['r2']:.4f}"

        fig.add_trace(go.Scatter(x=xp, y=yp, name="Fit", mode="lines", line=dict(color="#ff9230", dash="dash", width=2.5)))
        theme = plot_theme()
        fig.update_layout(
            xaxis_title=f"Concentration ({self._app_state.data.assay_conc_unit})",
            yaxis_title=f"ΔSignal ({self._app_state.data.assay_sig_unit})",
            height=460, template=theme["template"], paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(text=eq, xref="paper", yref="paper", x=0.02, y=0.98, xanchor="left", yanchor="top",
                               font=dict(size=11, color=theme["annot_font"]), showarrow=False, bordercolor="#555", borderwidth=1, borderpad=6)],
        )
        self._curve_plot.set_figure(fig)

        rows = []
        for k in range(len(cx)):
            raw_k = np.array(res["raw_arr"][k], float)
            n_reps = int(np.isfinite(raw_k).sum())
            rows.append({
                "Label": lb[k], "Conc": f"{cx[k]:.5g}",
                "Set 1": fmt(raw_k[0]), "Set 2": fmt(raw_k[1]), "Set 3": fmt(raw_k[2]),
                "Mean Δ": fmt(my[k]),
                "SD": fmt(sy[k]) if np.isfinite(sy[k]) else ("n=1" if n_reps == 1 else "—"),
                "CV (%)": fmt(abs(sy[k] / my[k]) * 100 if np.isfinite(my[k]) and my[k] != 0 else np.nan, 2),
            })
        self._std_summary_table.clear()
        if rows:
            cols = list(rows[0].keys())
            self._std_summary_table.setColumnCount(len(cols))
            self._std_summary_table.setHorizontalHeaderLabels(cols)
            self._std_summary_table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, col in enumerate(cols):
                    self._std_summary_table.setItem(r, c, QTableWidgetItem(str(row[col])))

    def _export_curve_png(self) -> None:
        res = self._app_state.data.assay_std_res
        if not res:
            self._curve_status.setText("Compute the standard curve first.")
            return
        png_bytes = render_assay_curve(res, self._show_reps_check.isChecked(), self._app_state.data.assay_conc_unit, self._app_state.data.assay_sig_unit)
        path, _ = QFileDialog.getSaveFileName(self, "Export standard curve PNG", "standard_curve.png", "PNG image (*.png)")
        if path:
            with open(path, "wb") as f:
                f.write(png_bytes)

    # -- Tab 4: Results & Export --------------------------------------------
    def _build_results_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        compute_btn = QPushButton("Compute sample results", self)
        compute_btn.clicked.connect(self._compute_results)
        layout.addWidget(compute_btn)

        self._results_table = QTableWidget(self)
        layout.addWidget(self._results_table, 1)

        export_row = QHBoxLayout()
        results_csv_btn = QPushButton("Export results CSV", self)
        results_csv_btn.clicked.connect(self._export_results_csv)
        export_row.addWidget(results_csv_btn)
        std_csv_btn = QPushButton("Export standards CSV", self)
        std_csv_btn.clicked.connect(self._export_standards_csv)
        export_row.addWidget(std_csv_btn)
        layout.addLayout(export_row)

        layout.addWidget(QLabel("Results plate map", self))
        self._results_plate_plot = PlotView(self)
        self._results_plate_plot.setMinimumHeight(300)
        layout.addWidget(self._results_plate_plot)
        return tab

    def _back_calc(self, dy: float, fit: dict) -> float:
        """New code reimplementing modes/assay.py render()'s _back_calc
        closure (not importable — see this module's docstring)."""
        ft = fit["type"]
        if not np.isfinite(dy):
            return np.nan
        if ft == "linear":
            s = fit["slope"]
            return float((dy - fit["intercept"]) / s) if s != 0 else np.nan
        if ft == "quad":
            a, b, c = fit["a"], fit["b"], fit["c"] - dy
            if abs(a) <= 1e-9 * max(abs(b), 1e-12):
                return float(-c / b) if b != 0 else np.nan
            disc = b ** 2 - 4 * a * c
            if disc < 0:
                return np.nan
            r1 = (-b + np.sqrt(disc)) / (2 * a)
            r2 = (-b - np.sqrt(disc)) / (2 * a)
            pos = [r for r in [r1, r2] if r >= -1e-9]
            if a < 0 and len(pos) == 2:
                return np.nan
            return float(min(pos)) if pos else np.nan
        return _4pl_inv(dy, fit)

    def _compute_results(self) -> None:
        res = self._app_state.data.assay_std_res
        plate = self._app_state.data.assay_plate
        if res is None or plate is None:
            return
        fit = res["fit"]
        blank = float(res["blank_mean"])
        cx = np.array(res["concs"], float)
        c_min, c_max = float(cx.min()), float(cx.max())
        std_wells = {str(r.get(sc, "")).strip().upper() for r in res["std_df"] for sc in ["S1", "S2", "S3"] if str(r.get(sc, "")).strip()}
        sample_map = _build_sample_map(self._app_state.data.assay_sample_df)

        rows = []
        for row_lbl in _PLATE_ROWS:
            for ci in range(12):
                well = f"{row_lbl}{ci + 1}"
                if well in std_wells:
                    continue
                sig = _plate_get(plate, well)
                if not np.isfinite(sig):
                    continue
                dy = sig - blank
                conc = self._back_calc(dy, fit)
                flag = ""
                if np.isfinite(conc):
                    if conc < c_min - 1e-9:
                        flag = "< range"
                    elif conc > c_max + 1e-9:
                        flag = "> range"
                else:
                    flag = "undefined"
                rows.append({"Well": well, "Label": sample_map.get(well, ""), "Signal": fmt(sig), "ΔSignal": fmt(dy),
                             "Conc": fmt(conc) if np.isfinite(conc) else "—", "Flag": flag})

        self._results_rows = rows
        self._results_table.clear()
        if rows:
            cols = list(rows[0].keys())
            self._results_table.setColumnCount(len(cols))
            self._results_table.setHorizontalHeaderLabels(cols)
            self._results_table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, col in enumerate(cols):
                    self._results_table.setItem(r, c, QTableWidgetItem(str(row[col])))

        self._results_plate_plot.set_figure(_plate_figure(plate, _build_std_wells_map(self._app_state.data.assay_std_df), sample_map,
                                                            self._app_state.data.assay_conc_unit, self._app_state.data.assay_sig_unit))

    def _export_results_csv(self) -> None:
        rows = getattr(self, "_results_rows", None)
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results CSV", "assay_results.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    def _export_standards_csv(self) -> None:
        res = self._app_state.data.assay_std_res
        if not res:
            return
        rows = [{
            "Label": res["labels"][i], "Conc": res["concs"][i],
            "Set 1": res["raw_arr"][i][0], "Set 2": res["raw_arr"][i][1], "Set 3": res["raw_arr"][i][2],
            "Mean Δ": res["means"][i], "SD": res["sds"][i],
        } for i in range(len(res["concs"]))]
        path, _ = QFileDialog.getSaveFileName(self, "Export standards CSV", "standard_curve_data.csv", "CSV (*.csv)")
        if path:
            pd.DataFrame(rows).to_csv(path, index=False)

    # -- refresh plumbing -------------------------------------------------------
    def _refresh_all(self) -> None:
        plate = self._app_state.data.assay_plate
        display_plate = plate if plate is not None else _empty_plate()
        self._plate_model.set_dataframe(display_plate.reset_index())

        std_wells = _build_std_wells_map(self._app_state.data.assay_std_df)
        sample_map = _build_sample_map(self._app_state.data.assay_sample_df)
        conc_unit, sig_unit = self._app_state.data.assay_conc_unit, self._app_state.data.assay_sig_unit
        fig = _plate_figure(plate, std_wells, sample_map, conc_unit, sig_unit)
        self._import_plate_plot.set_figure(fig)
        self._layout_plate_plot.set_figure(fig)
