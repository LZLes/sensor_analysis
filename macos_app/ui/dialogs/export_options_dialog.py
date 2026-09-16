"""
ExportOptionsDialog — publication-quality export options, the macOS app's
equivalent of the Streamlit app's "publication-quality export" panel
(modes/amperometry.py's render() — Format/DPI/Style/Size controls around
render_cal_png/render_ts_png/render_solid_cal_png).

Those pure PNG builders (in core/shared_tabs.py, modes/amperometry.py,
modes/solid_state.py) already accept dpi/fmt/figsize/style kwargs — this
dialog is only a UI to choose them, wired in by each mode view's export
actions rather than hardcoding png/150dpi/default as they did before.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
)

_FORMATS = ["PNG", "SVG", "PDF", "TIFF"]
_DPI_CHOICES = [150, 300, 600]
_STYLES = ["Default", "Origin", "Minimal"]


class ExportOptionsDialog(QDialog):
    def __init__(self, parent=None, title: str = "Export options") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)

        form = QFormLayout(self)

        self._format_combo = QComboBox(self)
        self._format_combo.addItems(_FORMATS)
        self._format_combo.currentTextChanged.connect(self._on_format_changed)
        form.addRow("Format", self._format_combo)

        self._dpi_combo = QComboBox(self)
        self._dpi_combo.addItems([str(d) for d in _DPI_CHOICES])
        self._dpi_combo.setCurrentText("300")
        form.addRow("DPI", self._dpi_combo)

        self._style_combo = QComboBox(self)
        self._style_combo.addItems(_STYLES)
        form.addRow("Style", self._style_combo)

        self._width_spin = QDoubleSpinBox(self)
        self._width_spin.setRange(0.0, 100.0)
        self._width_spin.setDecimals(2)
        self._width_spin.setSpecialValueText("Auto")
        self._width_spin.setSuffix(" in")
        form.addRow("Figure width", self._width_spin)

        self._height_spin = QDoubleSpinBox(self)
        self._height_spin.setRange(0.0, 100.0)
        self._height_spin.setDecimals(2)
        self._height_spin.setSpecialValueText("Auto")
        self._height_spin.setSuffix(" in")
        form.addRow("Figure height", self._height_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._on_format_changed(self._format_combo.currentText())

    def _on_format_changed(self, fmt: str) -> None:
        # DPI only affects raster output — SVG/PDF are vector, matching the
        # Streamlit publication panel's own DPI-disable-for-SVG/PDF logic.
        self._dpi_combo.setEnabled(fmt not in ("SVG", "PDF"))

    def options(self) -> dict:
        width = self._width_spin.value()
        height = self._height_spin.value()
        figsize = (width, height) if width > 0 and height > 0 else None
        return {
            "fmt": self._format_combo.currentText().lower(),
            "dpi": int(self._dpi_combo.currentText()),
            "style": self._style_combo.currentText().lower(),
            "figsize": figsize,
        }

    @classmethod
    def get_options(cls, parent=None, title: str = "Export options") -> dict | None:
        """Convenience entry point mirroring QInputDialog.getText's pattern —
        returns None if the user cancelled, the chosen kwargs dict otherwise."""
        dialog = cls(parent, title)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.options()
