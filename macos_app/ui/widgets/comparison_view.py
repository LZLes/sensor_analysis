"""
ComparisonView — cross-file overlay/comparison chart (Phase 8, the QoL
enhancement beyond Streamlit parity).

Note on scope vs. what already exists: since the Phase 5 bug fix, each
mode's own "Channels to analyse" selector already spans every loaded file,
so its Calibration Curve chart already overlays multiple files' channels
on one plot. What that view can't do is show each FILE's own independent
fit cleanly labeled by filename — mixing files' channels into one result
set is exactly the point of that workbench, but it means two files' curves
aren't presented as "File A vs File B," they're presented as whichever
channels you picked. ComparisonView fills that specific gap: pick 2+ files
from the mode's file list, and for each one, run a single fit over that
file's own primary channel + calibration table (via the mode-specific
compute_fn passed in) and overlay the results with the filename as the
legend label, plus a compact stats table for direct side-by-side reading.

Generic over which mode's per-file compute logic it uses (same
files_key/compute_fn parameterization pattern as ImportPanel/
TimeSeriesPanel/AutodetectPanel) — Amperometry and Solid-State each
supply their own compute_fn (see amperometry_view.py/solid_state_view.py),
since their fit math and calibration-table schemas differ.

Scoped to same-window/same-session comparison, per the migration plan —
comparing across separate windows isn't supported (would need a session-
merge mechanism this app doesn't otherwise need).
"""

from __future__ import annotations

from typing import Callable

import plotly.graph_objects as go
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from core.constants import PAL
from macos_app.ui.app_state import AppState
from macos_app.ui.theme import plot_theme
from macos_app.ui.widgets.plot_view import PlotView

FileFitFn = Callable[[dict, AppState], "dict | None"]
# compute_fn(file_record, app_state) -> {
#     "x": list[float], "y": list[float],                # calibration points for this file
#     "curve_x": list[float], "curve_y": list[float],     # fit line to draw
#     "stats": dict,                                      # one row for the comparison table
# } | None (file has no valid fit yet, e.g. empty calibration table)


class ComparisonView(QWidget):
    def __init__(self, app_state: AppState, files_key: str, compute_fn: FileFitFn,
                 x_label: str, y_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._files_key = files_key
        self._compute_fn = compute_fn
        self._x_label = x_label
        self._y_label = y_label

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Compare files independently: each selected file gets its own fit "
            "(its own calibration table, not mixed with other files' channels), "
            "overlaid here by filename.", self,
        ))

        self._file_list = QListWidget(self)
        self._file_list.setMaximumHeight(110)
        self._file_list.itemChanged.connect(lambda _item: self.refresh())
        layout.addWidget(self._file_list)

        refresh_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh comparison", self)
        refresh_btn.clicked.connect(self.refresh)
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch(1)
        layout.addLayout(refresh_row)

        self._plot_view = PlotView(self)
        layout.addWidget(self._plot_view, 1)

        self._stats_table = QTableWidget(self)
        self._stats_table.setMaximumHeight(160)
        layout.addWidget(self._stats_table)

        app_state.files_changed.connect(self._on_files_changed)
        app_state.cpdf_changed.connect(self._on_cpdf_changed)
        self.refresh_file_list()

    def _on_files_changed(self, files_key: str) -> None:
        if files_key == self._files_key:
            self.refresh_file_list()

    def _on_cpdf_changed(self, files_key: str, _file_index: int) -> None:
        if files_key == self._files_key:
            self.refresh()

    def refresh_file_list(self) -> None:
        from PySide6.QtCore import Qt
        # Defaults every file to checked on any file-list change (import,
        # apply, sample load) rather than trying to preserve a prior partial
        # selection — "preserve checked, default new ones unchecked" sounds
        # nicer but actually surprises users: a newly-added file silently
        # not appearing in the comparison. Matches TimeSeriesPanel's
        # all-checked-by-default pattern; users narrow it down manually.
        files = self._app_state.files_for(self._files_key)
        self._file_list.blockSignals(True)
        self._file_list.clear()
        for frec in files:
            item = QListWidgetItem(frec["filename"], self._file_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
        self._file_list.blockSignals(False)
        self.refresh()

    def _checked_filenames(self) -> set[str]:
        from PySide6.QtCore import Qt
        return {
            self._file_list.item(i).text()
            for i in range(self._file_list.count())
            if self._file_list.item(i).checkState() == Qt.CheckState.Checked
        }

    def refresh(self) -> None:
        files = self._app_state.files_for(self._files_key)
        checked = self._checked_filenames()
        selected_files = [f for f in files if f["filename"] in checked]

        fig = go.Figure()
        stat_rows = []
        for fi, frec in enumerate(selected_files):
            result = self._compute_fn(frec, self._app_state)
            if result is None:
                continue
            color = PAL[fi % len(PAL)]
            fig.add_trace(go.Scatter(x=result["x"], y=result["y"], name=frec["filename"], mode="markers",
                                      marker=dict(color=color, size=9)))
            fig.add_trace(go.Scatter(x=result["curve_x"], y=result["curve_y"], name=f"{frec['filename']} fit",
                                      mode="lines", showlegend=False, line=dict(color=color, dash="dash", width=2)))
            stat_rows.append(result["stats"])

        theme = plot_theme()
        fig.update_layout(
            xaxis_title=self._x_label, yaxis_title=self._y_label,
            height=420, template=theme["template"],
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        self._plot_view.set_figure(fig)

        self._stats_table.clear()
        if stat_rows:
            cols = list(stat_rows[0].keys())
            self._stats_table.setColumnCount(len(cols))
            self._stats_table.setHorizontalHeaderLabels(cols)
            self._stats_table.setRowCount(len(stat_rows))
            for r, row in enumerate(stat_rows):
                for c, col in enumerate(cols):
                    self._stats_table.setItem(r, c, QTableWidgetItem(str(row.get(col, ""))))
        else:
            self._stats_table.setColumnCount(0)
            self._stats_table.setRowCount(0)
