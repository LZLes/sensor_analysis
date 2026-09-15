"""
PandasTableModel — QAbstractTableModel over a pandas.DataFrame.

Replaces st.data_editor. Kept as an in-house model rather than pulling in
qtpandas (unmaintained, PyQt4-era) — this is ~100 lines and covers exactly
what the app's calibration/standards/sample tables need: per-column
editability, typed cells, and a plain DataFrame in/out so callers
(amperometry/solid_state cpdf editors, assay's standards/sample tables)
can keep using core/calibration_table.py's existing DataFrame builders
unchanged.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class PandasTableModel(QAbstractTableModel):
    def __init__(
        self,
        df: pd.DataFrame,
        editable_columns: set[str] | None = None,
        column_casters: dict[str, Callable[[str], Any]] | None = None,
        parent=None,
    ) -> None:
        """editable_columns: column names the user may edit (default: all).
        column_casters: optional str->value converter per column (e.g. float
        for numeric columns, bool for a checkbox-backed column) — falls back
        to the existing cell's type if not given."""
        super().__init__(parent)
        self._df = df.reset_index(drop=True)
        self._editable_columns = editable_columns
        self._casters = column_casters or {}

    # -- read -----------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col = self._df.columns[index.column()]
        value = self._df.iat[index.row(), index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return "" if pd.isna(value) else str(value)
        if role == Qt.ItemDataRole.CheckStateRole and pd.api.types.is_bool_dtype(self._df[col]):
            return Qt.CheckState.Checked if bool(value) else Qt.CheckState.Unchecked
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)

    # -- write ------------------------------------------------------------------
    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        col = self._df.columns[index.column()]
        if self._editable_columns is None or col in self._editable_columns:
            base |= Qt.ItemFlag.ItemIsEditable
            if pd.api.types.is_bool_dtype(self._df[col]):
                base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False
        col = self._df.columns[index.column()]
        if role == Qt.ItemDataRole.CheckStateRole:
            self._df.iat[index.row(), index.column()] = value == Qt.CheckState.Checked.value
            self.dataChanged.emit(index, index, [role])
            return True
        if role != Qt.ItemDataRole.EditRole:
            return False
        caster = self._casters.get(col) or self._default_caster(col)
        try:
            cast_value = caster(value)
        except (TypeError, ValueError):
            return False
        self._df.iat[index.row(), index.column()] = cast_value
        self.dataChanged.emit(index, index, [role])
        return True

    def _default_caster(self, col: str) -> Callable[[Any], Any]:
        """No explicit caster given for this column — infer one from its
        current dtype so e.g. typing "10" into a float64 column doesn't
        crash pandas' strict setitem (LossySetitemError) with a raw str."""
        dtype = self._df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            return lambda v: int(float(v))
        if pd.api.types.is_float_dtype(dtype):
            return float
        if pd.api.types.is_bool_dtype(dtype):
            return lambda v: str(v).strip().lower() in ("1", "true", "yes", "y")
        return str

    # -- row add/remove (editable_table_view.py's +/- toolbar) ----------------
    def add_row(self, defaults: dict | None = None) -> None:
        n = len(self._df)
        self.beginInsertRows(QModelIndex(), n, n)
        row = {c: (defaults or {}).get(c, "") for c in self._df.columns}
        self._df.loc[n] = row
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        if not (0 <= row < len(self._df)):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._df = self._df.drop(self._df.index[row]).reset_index(drop=True)
        self.endRemoveRows()

    # -- access -----------------------------------------------------------------
    def dataframe(self) -> pd.DataFrame:
        return self._df.copy()

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self.endResetModel()
