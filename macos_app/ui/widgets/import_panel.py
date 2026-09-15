"""
ImportPanel — Qt rebuild of core/shared_tabs.py's _render_import_tab.

Shared between Amperometry and Solid-State (same as the Streamlit version),
parameterized by files_key/unit_key/etc. Reuses core.parsing.parse_with_options
(the pure sibling of _parse_one_file added for exactly this purpose) and
whichever seed_cpdf_fn the calling mode passes in.

Scope note: the Streamlit version lets each file define an arbitrary number
of channels via a dynamic per-row grid (name / time-col / signal-col,
repeated N times). This first pass supports one channel per file (auto-
detected where possible, editable name/time-col/signal-col) rather than
that full dynamic N-channel grid — covers the common case (and the sample
data) while the vertical slice validates the rest of the architecture;
multi-channel-per-file mapping is a straightforward follow-up on the same
pattern once this is in place.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.parsing import parse_with_options
from macos_app.ui.app_state import AppState
from macos_app.ui.undo_commands import SetFieldCommand

_IMPORTABLE_SUFFIXES = (".csv", ".txt", ".pssession")


class _FileChannelEditor(QGroupBox):
    """One file's channel mapping (name / time col / signal col)."""

    def __init__(self, filename: str, df: pd.DataFrame, auto_channel: dict | None, signal_col_label: str, parent=None) -> None:
        super().__init__(filename, parent)
        self.df = df
        cols = list(df.columns)

        self.name_edit = QLineEdit(auto_channel["name"] if auto_channel else "Channel 1", self)
        self.time_combo = QComboBox(self)
        self.time_combo.addItems(cols)
        self.signal_combo = QComboBox(self)
        self.signal_combo.addItems(cols)
        if auto_channel:
            self.time_combo.setCurrentText(auto_channel.get("tc", cols[0]))
            self.signal_combo.setCurrentText(auto_channel.get("ic", cols[-1]))
        elif len(cols) >= 2:
            self.time_combo.setCurrentIndex(0)
            self.signal_combo.setCurrentIndex(1)

        form = QFormLayout(self)
        form.addRow("Channel name", self.name_edit)
        form.addRow("Time column", self.time_combo)
        form.addRow(f"{signal_col_label} column", self.signal_combo)

    def channel(self) -> dict:
        return {"name": self.name_edit.text(), "tc": self.time_combo.currentText(), "ic": self.signal_combo.currentText()}


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
        self._pending: list[dict] = []  # [{filename, df, editor}] awaiting Apply
        self._editors: dict[str, _FileChannelEditor] = {}

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

        self._files_area = QScrollArea(self)
        self._files_area.setWidgetResizable(True)
        self._files_container = QWidget(self._files_area)
        self._files_layout = QVBoxLayout(self._files_container)
        self._files_area.setWidget(self._files_container)
        outer.addWidget(self._files_area, 1)

        apply_btn = QPushButton("Apply Channel Configuration", self)
        apply_btn.clicked.connect(self._apply_configuration)
        outer.addWidget(apply_btn)

        units_group = QGroupBox("Units", self)
        units_form = QFormLayout(units_group)
        self._conc_unit_edit = QLineEdit(app_state.get_field(conc_unit_key), self)
        self._conc_unit_edit.editingFinished.connect(self._commit_conc_unit)
        self._signal_unit_edit = QLineEdit(app_state.get_field(unit_key), self)
        self._signal_unit_edit.editingFinished.connect(self._commit_signal_unit)
        units_form.addRow("Concentration unit", self._conc_unit_edit)
        units_form.addRow(f"{signal_col_label} unit", self._signal_unit_edit)
        outer.addWidget(units_group)

        self._status_label = QLabel("", self)
        outer.addWidget(self._status_label)

    # -- entry points ---------------------------------------------------------
    def add_files(self, paths: list[str]) -> None:
        """Public entry point for both the Browse dialog and drag-and-drop
        (forwarded from MainWindow.dropEvent)."""
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
            self._add_parsed_file(filename, df, auto_channels[0] if auto_channels else None)

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import sensor data", "", "Sensor data (*.csv *.txt *.pssession);;All files (*)"
        )
        if paths:
            self.add_files(paths)

    def _add_parsed_file(self, filename: str, df: pd.DataFrame, auto_channel: dict | None) -> None:
        editor = _FileChannelEditor(filename, df, auto_channel, self._signal_col_label, self)
        self._editors[filename] = editor
        self._files_layout.addWidget(editor)
        self._pending.append({"filename": filename, "df": df})

    def _load_sample(self) -> None:
        if self._sample_loader_fn is None:
            return
        sample_files = self._sample_loader_fn()
        if sample_files is None:
            self._status_label.setText("Sample data files are missing from this deployment.")
            return
        cmd = SetFieldCommand(self._app_state, self._files_key, sample_files, text="Load sample data")
        self._app_state.undo_stack.push(cmd)
        self._app_state.notify_files_changed(self._files_key)
        self._status_label.setText(f"Sample data loaded ({len(sample_files)} file(s)).")

    def _apply_configuration(self) -> None:
        if not self._pending:
            return
        existing_by_name = {f["filename"]: f for f in self._app_state.files_for(self._files_key)}
        new_files = []
        for entry in self._pending:
            filename = entry["filename"]
            editor = self._editors[filename]
            channel = editor.channel()
            cpdf = existing_by_name[filename]["cpdf"] if filename in existing_by_name else self._seed_cpdf_fn()
            new_files.append({"filename": filename, "df": entry["df"], "channels": [channel], "cpdf": cpdf})

        cmd = SetFieldCommand(self._app_state, self._files_key, new_files, text="Apply channel configuration")
        self._app_state.undo_stack.push(cmd)
        self._app_state.notify_files_changed(self._files_key)
        self._status_label.setText(f"{len(new_files)} file(s) applied.")
        self._pending.clear()

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
