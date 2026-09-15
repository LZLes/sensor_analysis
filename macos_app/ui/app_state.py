"""
AppState — replaces Streamlit's st.session_state (SS) dict for the macOS app.

One AppState per open window (see main_window.py) — this is deliberately
NOT a singleton, so "New Window" (Phase 3) can hold an independent session
per window with no state leaking between them.

Field list mirrors core/state.py's init_session_state() defaults, kept
centralized here for the same reason core/state.py gives: switching modes
should never hit a missing attribute just because another mode's view
hasn't touched it yet.

Mutation model: callers should go through AppState's setter methods (not
raw attribute assignment) so every change (a) emits a signal so only the
interested panel updates, and (b) is undoable via the owned QUndoStack —
see undo_commands.py. Direct attribute reads are fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack


def _default_assay_std_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Label": ["Blank", "Std 2", "Std 3", "Std 4", "Std 5", "Std 6", "Std 7", "Std 8"],
        "Conc":  [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        "S1":    ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
        "S2":    ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"],
        "S3":    ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
    })


def _default_assay_sample_df() -> pd.DataFrame:
    return pd.DataFrame({"Well": pd.Series([], dtype=str), "Label": pd.Series([], dtype=str)})


@dataclass
class SessionData:
    """Plain-data fields, one per Streamlit SS key in core/state.py.
    Held on AppState rather than as loose QObject attributes so the whole
    thing can be swapped atomically on Import/undo-to-start."""

    df: pd.DataFrame | None = None
    channels: list[dict] = field(default_factory=list)
    amp_files: list[dict] = field(default_factory=list)        # [{filename, df, channels, cpdf, ...}]
    solid_files: list[dict] = field(default_factory=list)      # [{filename, df, channels, cpdf, ...}]
    solid_cal_results: dict | None = None
    cal_results: dict | None = None

    conc_unit: str = "mM"
    solid_conc_unit: str = "M"
    cur_unit: str = "µA"
    solid_unit: str = "mV"
    vol_unit: str = "µL"
    initial_volume: float = 1.0
    smooth_method: str = "None"
    smooth_window: int = 11
    smooth_polyorder: int = 2
    ts_y_auto: bool = True
    ts_y_min: float | None = None
    ts_y_max: float | None = None

    mode: str = "Amperometry"
    volt_unit: str = "V"
    cv_cur_unit: str = "µA"
    cv_sr_unit: str = "mV/s"
    cv_runs: list[dict] = field(default_factory=list)          # [{scan_rate, label, filename, df, channels, peaks}]

    assay_plate: Any = None
    assay_sig_unit: str = "Abs"
    assay_conc_unit: str = "µM"
    assay_std_res: dict | None = None
    assay_std_df: pd.DataFrame = field(default_factory=_default_assay_std_df)
    assay_sample_df: pd.DataFrame = field(default_factory=_default_assay_sample_df)

    # Per-file-list UI state that Streamlit namespaced dynamically as
    # f"{files_key}_..." (see core/shared_tabs.py) — kept here as a nested
    # dict keyed the same way, since Qt/AppState doesn't need dynamic
    # attribute names the way a rerun-and-rebuild model did.
    ts_ui: dict[str, dict] = field(default_factory=dict)       # ts_ui["amp"]["y_min"], etc.


class AppState(QObject):
    """Owns one SessionData + one QUndoStack for a single window/session."""

    # Coarse-grained signals — panels connect to what they need and re-read
    # the relevant SessionData field(s) themselves, rather than every field
    # getting its own Signal (that would be ~35 signals for little benefit
    # at this app's scale).
    files_changed = Signal(str)          # emits files_key, e.g. "amp_files" / "solid_files" / "cv_runs"
    cpdf_changed = Signal(str, int)      # emits files_key, file_index
    setting_changed = Signal(str)        # emits the SessionData field name that changed
    mode_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.data = SessionData()
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(100)

    # -- generic setters used by undo_commands.SetFieldCommand --------------
    def get_field(self, name: str) -> Any:
        return getattr(self.data, name)

    def set_field_silent(self, name: str, value: Any) -> None:
        """Set without emitting — used internally by undo/redo, which emit
        the appropriate signal themselves after the swap."""
        setattr(self.data, name, value)

    def set_field(self, name: str, value: Any) -> None:
        """Direct (non-undoable) set + signal emit — for setup/import paths
        where there's nothing meaningful to undo. Interactive edits should
        go through undo_commands.SetFieldCommand.push(...) instead."""
        setattr(self.data, name, value)
        if name == "mode":
            self.mode_changed.emit(value)
        else:
            self.setting_changed.emit(name)

    # -- per-file-list helpers ------------------------------------------------
    def files_for(self, files_key: str) -> list[dict]:
        return getattr(self.data, files_key)

    def notify_files_changed(self, files_key: str) -> None:
        self.files_changed.emit(files_key)

    def notify_cpdf_changed(self, files_key: str, file_index: int) -> None:
        self.cpdf_changed.emit(files_key, file_index)
