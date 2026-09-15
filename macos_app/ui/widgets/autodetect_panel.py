"""
AutodetectPanel — Qt rebuild of core/shared_tabs.py's _render_autodetect_expander.

Detects candidate step-transition times from the active file's own trace
via core.step_detection (pure, unchanged), previews them (the caller wires
edges_detected to TimeSeriesPanel.refresh() so the dotted preview lines
show up there), and rebuilds the calibration table via build_cpdf_fn only
once the user clicks Apply.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.numeric import smooth_signal, to_num
from core.step_detection import detect_step_edges, edges_to_windows
from macos_app.ui.app_state import AppState
from macos_app.ui.undo_commands import TableEditCommand


class AutodetectPanel(QGroupBox):
    edges_detected = Signal()  # caller (mode view) connects this to TimeSeriesPanel.refresh()

    def __init__(
        self,
        app_state: AppState,
        files_key: str,
        build_cpdf_fn: Callable[[list[tuple[str, float, float]]], "pd.DataFrame"],
        has_baseline: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Auto-detect step times from trace", parent)
        self._app_state = app_state
        self._files_key = files_key
        self._build_cpdf_fn = build_cpdf_fn
        self._has_baseline = has_baseline
        self._active_frec: dict | None = None
        self._active_file_index: int | None = None

        form = QFormLayout(self)

        self._channel_combo = QComboBox(self)
        form.addRow("Channel to analyse", self._channel_combo)

        self._sensitivity = QDoubleSpinBox(self)
        self._sensitivity.setRange(0.3, 3.0)
        self._sensitivity.setSingleStep(0.1)
        self._sensitivity.setValue(1.0)
        form.addRow("Sensitivity", self._sensitivity)

        self._min_gap = QDoubleSpinBox(self)
        self._min_gap.setRange(1.0, 1e6)
        self._min_gap.setValue(30.0)
        form.addRow("Min. seconds between steps", self._min_gap)

        self._max_steps = QSpinBox(self)
        self._max_steps.setRange(0, 1000)
        form.addRow("Expected step count (0 = keep all)", self._max_steps)

        self._include_baseline = QCheckBox("Leading Baseline row (0 → first step)", self)
        self._include_baseline.setChecked(True)
        self._include_baseline.setVisible(has_baseline)
        form.addRow(self._include_baseline)

        detect_btn = QPushButton("Detect", self)
        detect_btn.clicked.connect(self._run_detect)
        form.addRow(detect_btn)

        self._result_label = QLabel("", self)
        form.addRow(self._result_label)

        apply_btn = QPushButton("Apply detected edges — replaces the table below", self)
        apply_btn.clicked.connect(self._apply_edges)
        form.addRow(apply_btn)

    def set_active_file(self, frec: dict, file_index: int) -> None:
        self._active_frec = frec
        self._active_file_index = file_index
        self._channel_combo.clear()
        self._channel_combo.addItems([c["name"] for c in frec.get("channels", [])])

    def _run_detect(self) -> None:
        if self._active_frec is None or not self._active_frec.get("channels"):
            return
        ch_name = self._channel_combo.currentText()
        ch = next((c for c in self._active_frec["channels"] if c["name"] == ch_name), None)
        if ch is None:
            return
        t_arr = to_num(self._active_frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
        i_arr = to_num(self._active_frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
        i_arr = smooth_signal(i_arr, self._app_state.data.smooth_method, self._app_state.data.smooth_window, self._app_state.data.smooth_polyorder)
        edges = detect_step_edges(
            t_arr, i_arr,
            min_step_seconds=self._min_gap.value(),
            sensitivity=self._sensitivity.value(),
            max_edges=self._max_steps.value() or None,
        )
        bucket = self._app_state.data.ts_ui.setdefault(self._files_key, {}).setdefault("autodetect_edges", {})
        bucket[self._active_frec["filename"]] = edges
        if not edges:
            self._result_label.setText("No clear step transitions found — try lowering sensitivity.")
        else:
            self._result_label.setText("Detected times (s): " + ", ".join(f"{e:.4g}" for e in edges))
        self.edges_detected.emit()

    def _apply_edges(self) -> None:
        if self._active_frec is None or self._active_file_index is None:
            return
        edges = self._app_state.data.ts_ui.get(self._files_key, {}).get("autodetect_edges", {}).get(self._active_frec["filename"], [])
        if not edges:
            return
        ch_name = self._channel_combo.currentText()
        ch = next((c for c in self._active_frec["channels"] if c["name"] == ch_name), None)
        if ch is None:
            return
        t_arr = to_num(self._active_frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
        trace_end = float(np.nanmax(t_arr)) if t_arr.size else 0.0
        windows = edges_to_windows(edges, trace_end, self._include_baseline.isChecked() if self._has_baseline else False)
        new_cpdf = self._build_cpdf_fn(windows)
        cmd = TableEditCommand(self._app_state, self._files_key, self._active_file_index, new_cpdf, text="Apply detected edges")
        self._app_state.undo_stack.push(cmd)
        self._result_label.setText(f"Applied {len(windows)} row(s) from detected edges.")
