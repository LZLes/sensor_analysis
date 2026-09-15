"""
EditableTableView — QTableView + a "+/– Row" toolbar, standing in for
st.data_editor's built-in row-add/delete affordance.

Emits row_committed after an edit or row add/remove finishes, rather than
propagating every keystroke — callers (mode views) connect this to an
undo_commands.TableEditCommand push, matching the "compute once on
explicit action" model the app uses instead of Streamlit's per-keystroke
rerun.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableView, QVBoxLayout, QWidget

from macos_app.ui.widgets.pandas_table_model import PandasTableModel


class EditableTableView(QWidget):
    row_committed = Signal()  # fires after add/remove row, or when the user finishes editing a cell

    def __init__(
        self,
        df: pd.DataFrame,
        editable_columns: set[str] | None = None,
        column_casters: dict | None = None,
        row_defaults: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._row_defaults = row_defaults or {}

        self._model = PandasTableModel(df, editable_columns, column_casters, parent=self)
        self._view = QTableView(self)
        self._view.setModel(self._model)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._model.dataChanged.connect(lambda *_: self.row_committed.emit())

        add_btn = QPushButton("+ Row", self)
        remove_btn = QPushButton("− Row", self)
        add_btn.clicked.connect(self._add_row)
        remove_btn.clicked.connect(self._remove_selected_row)

        toolbar = QHBoxLayout()
        toolbar.addWidget(add_btn)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self._view)

    def _add_row(self) -> None:
        self._model.add_row(self._row_defaults)
        self.row_committed.emit()

    def _remove_selected_row(self) -> None:
        rows = {i.row() for i in self._view.selectionModel().selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._model.remove_row(row)
        if rows:
            self.row_committed.emit()

    def dataframe(self) -> pd.DataFrame:
        return self._model.dataframe()

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self._model.set_dataframe(df)
