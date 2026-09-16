"""
ExportOptionsDialog — publication-quality export options, the macOS app's
equivalent of the Streamlit app's "publication-quality export" panel
(modes/amperometry.py's render() — Format/DPI/Style/Size controls around
render_cal_png/render_ts_png/render_solid_cal_png).

Those pure PNG builders (in core/shared_tabs.py, modes/amperometry.py,
modes/solid_state.py) already accept dpi/fmt/figsize/style kwargs — this
module is only a UI to choose them, wired in by each mode view's export
actions rather than hardcoding png/150dpi/default as they did before.

Two widgets share the same controls (`_ExportControls`) and live-preview
pane (`_PreviewPane`, always rasterized as PNG regardless of the chosen
export format — SVG/PDF previews would need a separate decoder this app
has no other use for, and the layout/style/size these controls affect
looks the same either way):
- `ExportOptionsDialog`, a popup, for a secondary export action embedded in
  a busy tab (e.g. TimeSeriesPanel's own "Export PNG" button) where a modal
  step doesn't get in the way of anything else.
- `macos_app/ui/widgets/export_panel.py`'s `ExportPanel`, built directly
  into a tab whose sole purpose IS exporting, so there's no popup step at
  all — see that module's docstring.

Callers pass render_fn: dict -> bytes, the same closure they'll use to
build the real export bytes. The preview re-renders on every control
change (debounced).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_FORMATS = ["PNG", "SVG", "PDF", "TIFF"]
_DPI_CHOICES = [150, 300, 600]
_STYLES = ["Default", "Origin", "Minimal"]
_PREVIEW_DEBOUNCE_MS = 250
_PREVIEW_MIN_SIZE = (380, 300)


class _ExportControls(QWidget):
    """Format/DPI/Style/figure-size form, shared by the popup dialog and
    the inline export panel. Emits `changed` on any control edit."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

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

        self._format_combo.currentTextChanged.connect(lambda _t: self.changed.emit())
        self._dpi_combo.currentTextChanged.connect(lambda _t: self.changed.emit())
        self._style_combo.currentTextChanged.connect(lambda _t: self.changed.emit())
        self._width_spin.valueChanged.connect(lambda _v: self.changed.emit())
        self._height_spin.valueChanged.connect(lambda _v: self.changed.emit())

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


class _PreviewPane(QLabel):
    """Scales its pixmap to fill the available space, keeping aspect ratio,
    and re-scales on resize instead of re-rendering from bytes each time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setMinimumSize(*_PREVIEW_MIN_SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("QLabel { border: 1px solid palette(mid); }")
        self.setText("Rendering preview…")

    def show_png_bytes(self, png_bytes: bytes) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(png_bytes, "PNG")
        self._pixmap = pixmap
        self._apply_scaled()

    def show_error(self, message: str) -> None:
        self._pixmap = None
        self.setText(f"Preview unavailable:\n{message}")

    def _apply_scaled(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        scaled = self._pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_scaled()


class ExportOptionsDialog(QDialog):
    def __init__(self, parent=None, title: str = "Export options", render_fn: Callable[[dict], bytes] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._render_fn = render_fn

        root = QHBoxLayout(self)

        self._controls = _ExportControls(self)
        controls_col = QVBoxLayout()
        controls_col.addWidget(self._controls)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        controls_col.addWidget(buttons)
        controls_col.addStretch(1)
        root.addLayout(controls_col)

        if render_fn is not None:
            preview_col = QVBoxLayout()
            preview_col.addWidget(QLabel("Preview", self))
            self._preview = _PreviewPane(self)
            preview_col.addWidget(self._preview, 1)
            root.addLayout(preview_col, 1)

            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._update_preview)
            self._controls.changed.connect(lambda: self._preview_timer.start(_PREVIEW_DEBOUNCE_MS))
            self._preview_timer.start(0)

    def options(self) -> dict:
        return self._controls.options()

    def _update_preview(self) -> None:
        if self._render_fn is None:
            return
        preview_opts = dict(self.options(), fmt="png")
        try:
            png_bytes = self._render_fn(preview_opts)
        except Exception as exc:  # noqa: BLE001 - surface any render failure in the preview itself
            self._preview.show_error(str(exc))
            return
        self._preview.show_png_bytes(png_bytes)

    @classmethod
    def get_options(cls, parent=None, title: str = "Export options", render_fn: Callable[[dict], bytes] | None = None) -> dict | None:
        """Convenience entry point mirroring QInputDialog.getText's pattern —
        returns None if the user cancelled, the chosen kwargs dict otherwise.
        Pass render_fn (opts -> bytes) to show a live preview; it's typically
        the same closure the caller uses to build the real export bytes."""
        dialog = cls(parent, title, render_fn)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.options()
