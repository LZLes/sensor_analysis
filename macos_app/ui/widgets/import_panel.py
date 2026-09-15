"""
ImportPanel — Qt rebuild of core/shared_tabs.py's _render_import_tab.

Shared between Amperometry and Solid-State (same as the Streamlit version),
parameterized by files_key/unit_key/etc. Reuses core.parsing.parse_with_options
(the pure sibling of _parse_one_file added for exactly this purpose) and
whichever seed_cpdf_fn the calling mode passes in.

Supports an arbitrary number of channels per file (name / time-col /
signal-col per channel, same shape as the Streamlit grid) via a "Number of
channels" spinner that adds/removes rows, defaulting new rows to the same
paired-column guess Streamlit used (columns 2i / 2i+1) when there's no
auto-detected or previously-configured channel to seed from.
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.parsing import parse_with_options
from macos_app.ui.app_state import AppState
from macos_app.ui.undo_commands import SetFieldCommand

_IMPORTABLE_SUFFIXES = (".csv", ".txt", ".pssession")
_MAX_CHANNELS = 8


class _ChannelRow(QWidget):
    """One channel's (name, time col, signal col) mapping."""

    def __init__(self, columns: list[str], index: int, preset: dict | None, signal_col_label: str, parent=None) -> None:
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
            row = _ChannelRow(self._columns, i, preset, self._signal_col_label, self)
            self._rows.append(row)
            self._rows_container.addWidget(row)
        while len(self._rows) > n:
            row = self._rows.pop()
            self._rows_container.removeWidget(row)
            row.deleteLater()

    def channels(self) -> list[dict]:
        return [row.channel() for row in self._rows]


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
        self._pending: list[dict] = []  # [{filename, df}] awaiting Apply
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
        existing_by_name = {f["filename"]: f for f in self._app_state.files_for(self._files_key)}
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
            # Re-importing a file that's already configured keeps its existing
            # channel mapping as the preset (same as Streamlit's _preset_chs),
            # so re-uploading to change delimiter/etc doesn't lose channel edits.
            preset_channels = existing_by_name[filename]["channels"] if filename in existing_by_name else (auto_channels or [])
            self._add_parsed_file(filename, df, preset_channels)

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import sensor data", "", "Sensor data (*.csv *.txt *.pssession);;All files (*)"
        )
        if paths:
            self.add_files(paths)

    def _add_parsed_file(self, filename: str, df: pd.DataFrame, preset_channels: list[dict]) -> None:
        editor = _FileChannelEditor(filename, df, preset_channels, self._signal_col_label, self)
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
            channels = editor.channels()
            cpdf = existing_by_name[filename]["cpdf"] if filename in existing_by_name else self._seed_cpdf_fn()
            new_files.append({"filename": filename, "df": entry["df"], "channels": channels, "cpdf": cpdf})

        cmd = SetFieldCommand(self._app_state, self._files_key, new_files, text="Apply channel configuration")
        self._app_state.undo_stack.push(cmd)
        self._app_state.notify_files_changed(self._files_key)
        n_channels = sum(len(f["channels"]) for f in new_files)
        self._status_label.setText(f"{len(new_files)} file(s), {n_channels} channel(s) applied.")
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
