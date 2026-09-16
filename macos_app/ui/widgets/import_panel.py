"""
ImportPanel — Qt rebuild of core/shared_tabs.py's _render_import_tab.

Shared between Amperometry and Solid-State, parameterized by
files_key/unit_key/etc. Reuses core.parsing.parse_with_options (the pure
sibling of _parse_one_file added for exactly this purpose) and whichever
seed_cpdf_fn the calling mode passes in.

Deliberately lightweight: files are added with their auto-detected channel
guess (or a single-channel fallback) and are immediately usable — there is
no "Apply Channel Configuration" gate here. Fine-tuning channel names/time
column/signal column happens directly in the "Time Series & Windows" step
(see macos_app/ui/widgets/timeseries_panel.py's _FileChannelEditor) once the
user can see the trace, rather than being forced before it's even visible.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.parsing import parse_with_options
from macos_app.ui.app_state import AppState
from macos_app.ui.undo_commands import FilesListCommand, SetFieldCommand

_IMPORTABLE_SUFFIXES = (".csv", ".txt", ".pssession")


def _fallback_channels(df: pd.DataFrame) -> list[dict]:
    """Single-channel guess used when parse_with_options can't auto-detect
    any channels at all (e.g. an unrecognized plain CSV) — better than
    landing with zero channels and nothing to plot."""
    columns = list(df.columns)
    if not columns:
        return []
    return [{"name": "Channel 1", "tc": columns[0], "ic": columns[1] if len(columns) > 1 else columns[0]}]


class ImportPanel(QWidget):
    def __init__(
        self,
        app_state: AppState,
        files_key: str,
        signal_col_label: str,
        unit_key: str,
        conc_unit_key: str,
        seed_cpdf_fn: Callable[[], pd.DataFrame],
        sample_loader_fn: Callable[[], list[dict] | None] | None = None,
        sample_caption: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._files_key = files_key
        self._signal_col_label = signal_col_label
        self._unit_key = unit_key
        self._conc_unit_key = conc_unit_key
        self._seed_cpdf_fn = seed_cpdf_fn
        self._sample_loader_fn = sample_loader_fn

        outer = QVBoxLayout(self)

        if sample_loader_fn is not None:
            sample_row = QHBoxLayout()
            sample_btn = QPushButton("Load sample data", self)
            sample_btn.clicked.connect(self._load_sample)
            sample_row.addWidget(sample_btn)
            sample_row.addWidget(QLabel(sample_caption, self), 1)
            outer.addLayout(sample_row)

        browse_row = QHBoxLayout()
        browse_btn = QPushButton("Browse Files…", self)
        browse_btn.clicked.connect(self._browse_files)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch(1)
        outer.addLayout(browse_row)

        units_group = QGroupBox("Units", self)
        units_form = QFormLayout(units_group)
        self._conc_unit_edit = QLineEdit(app_state.get_field(conc_unit_key), self)
        self._conc_unit_edit.editingFinished.connect(self._commit_conc_unit)
        self._signal_unit_edit = QLineEdit(app_state.get_field(unit_key), self)
        self._signal_unit_edit.editingFinished.connect(self._commit_signal_unit)
        units_form.addRow("Concentration unit", self._conc_unit_edit)
        units_form.addRow(f"{signal_col_label} unit", self._signal_unit_edit)
        outer.addWidget(units_group)

        outer.addWidget(QLabel("Loaded files — fine-tune channel assignment in the Time Series & Windows tab.", self))
        self._files_list = QListWidget(self)
        outer.addWidget(self._files_list, 1)

        self._status_label = QLabel("", self)
        outer.addWidget(self._status_label)

        app_state.setting_changed.connect(self._on_setting_changed)
        app_state.files_changed.connect(self._on_files_changed)
        self._refresh_files_list()

    def _on_setting_changed(self, field_name: str) -> None:
        """Keep the unit fields in sync with AppState when they change from
        outside this panel's own editingFinished handlers — a session
        Import, or undo/redo. setText() doesn't itself fire editingFinished,
        so this can't loop back into _commit_conc_unit/_commit_signal_unit."""
        if field_name == self._conc_unit_key:
            self._conc_unit_edit.setText(self._app_state.get_field(self._conc_unit_key))
        elif field_name == self._unit_key:
            self._signal_unit_edit.setText(self._app_state.get_field(self._unit_key))

    def _on_files_changed(self, files_key: str) -> None:
        if files_key == self._files_key:
            self._refresh_files_list()

    def _refresh_files_list(self) -> None:
        self._files_list.clear()
        for frec in self._app_state.files_for(self._files_key):
            n_ch = len(frec.get("channels", []))
            label = f"{frec['filename']} — {n_ch} channel{'s' if n_ch != 1 else ''}"
            QListWidgetItem(label, self._files_list)

    # -- entry points ---------------------------------------------------------
    def add_files(self, paths: list[str]) -> None:
        """Public entry point for both the Browse dialog and drag-and-drop
        (forwarded from MainWindow.dropEvent). Parses, auto-detects channels,
        and commits straight to AppState — no separate "Apply" step."""
        existing = list(self._app_state.files_for(self._files_key))
        by_name = {f["filename"]: f for f in existing}
        order = [f["filename"] for f in existing]
        added = 0
        for path in paths:
            if not path.lower().endswith(_IMPORTABLE_SUFFIXES):
                continue
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                filename = path.rsplit("/", 1)[-1]
                df, auto_channels = parse_with_options(filename, raw, fmt="standard", delimiter="auto")
            except Exception as exc:  # noqa: BLE001 - surface any parse failure to the status label
                self._status_label.setText(f"Parse error in {path}: {exc}")
                continue
            if filename in by_name:
                # Re-importing keeps the existing channel mapping and
                # calibration table, only refreshing the parsed data.
                channels = by_name[filename]["channels"]
                cpdf = by_name[filename]["cpdf"]
            else:
                channels = auto_channels or _fallback_channels(df)
                cpdf = self._seed_cpdf_fn()
                order.append(filename)
            by_name[filename] = {"filename": filename, "df": df, "channels": channels, "cpdf": cpdf}
            added += 1
        if added == 0:
            return
        new_files = [by_name[name] for name in order]
        cmd = FilesListCommand(self._app_state, self._files_key, new_files, text="Import files")
        self._app_state.undo_stack.push(cmd)  # push() calls redo(), which already fires files_changed
        n_channels = sum(len(f["channels"]) for f in new_files)
        self._status_label.setText(f"{len(new_files)} file(s), {n_channels} channel(s) loaded.")

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import sensor data", "", "Sensor data (*.csv *.txt *.pssession);;All files (*)"
        )
        if paths:
            self.add_files(paths)

    def _load_sample(self) -> None:
        if self._sample_loader_fn is None:
            return
        sample_files = self._sample_loader_fn()
        if sample_files is None:
            self._status_label.setText("Sample data files are missing from this deployment.")
            return
        cmd = FilesListCommand(self._app_state, self._files_key, sample_files, text="Load sample data")
        self._app_state.undo_stack.push(cmd)  # push() calls redo(), which already fires files_changed
        self._status_label.setText(f"Sample data loaded ({len(sample_files)} file(s)).")

    def _commit_conc_unit(self) -> None:
        value = self._conc_unit_edit.text()
        if value != self._app_state.get_field(self._conc_unit_key):
            self._app_state.undo_stack.push(
                SetFieldCommand(self._app_state, self._conc_unit_key, value, text="Set concentration unit")
            )

    def _commit_signal_unit(self) -> None:
        value = self._signal_unit_edit.text()
        if value != self._app_state.get_field(self._unit_key):
            self._app_state.undo_stack.push(
                SetFieldCommand(self._app_state, self._unit_key, value, text=f"Set {self._signal_col_label.lower()} unit")
            )
