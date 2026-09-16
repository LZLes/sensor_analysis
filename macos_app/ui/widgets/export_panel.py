"""
ExportPanel — the export-options controls and live preview from
macos_app/ui/dialogs/export_options_dialog.py, built directly into a tab
instead of behind a popup.

A dedicated "Export" tab's whole purpose is exporting, so a modal dialog
on top of it is a redundant extra step (open dialog -> pick options ->
close dialog -> browse for a save path) that also hides the tab underneath
while it's open. This widget puts the same Format/DPI/Style/Size controls
and live preview permanently in view, re-rendering as you change them, so
"Export…" is the only click left — it just opens the save-path browser.

Each mode's Export tab passes one or more `ExportTarget`s (what to render,
and its default filename); a picker combo appears only when there's more
than one (Cyclic Voltammetry's Export tab offers five different plots —
Amperometry/Solid-State's offer only their one calibration curve, where
the combo would be pure clutter). `refresh_preview()` is meant to be wired
to the tab becoming current (QTabWidget.currentChanged), since a target's
underlying data (e.g. calibration results) can change while this tab isn't
visible and pyqtgraph/matplotlib never re-render on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from macos_app.ui.dialogs.export_options_dialog import _PREVIEW_DEBOUNCE_MS, _ExportControls, _PreviewPane


@dataclass
class ExportTarget:
    label: str
    render_fn: Callable[[dict], bytes]  # opts -> bytes; may raise if there's nothing to export yet
    default_filename: str  # no extension — the chosen format supplies it


class ExportPanel(QWidget):
    def __init__(self, targets: list[ExportTarget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not targets:
            raise ValueError("ExportPanel needs at least one ExportTarget")
        self._targets = targets

        root = QVBoxLayout(self)

        top_form = QFormLayout()
        top_form.setContentsMargins(0, 0, 0, 0)
        self._target_combo: QComboBox | None = None
        if len(targets) > 1:
            self._target_combo = QComboBox(self)
            self._target_combo.addItems([t.label for t in targets])
            self._target_combo.currentIndexChanged.connect(self._schedule_preview)
            top_form.addRow("Plot", self._target_combo)
        root.addLayout(top_form)

        body = QHBoxLayout()
        self._controls = _ExportControls(self)
        controls_col = QVBoxLayout()
        controls_col.addWidget(self._controls)

        self._export_btn = QPushButton("Export…", self)
        self._export_btn.clicked.connect(self._do_export)
        controls_col.addWidget(self._export_btn)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        controls_col.addWidget(self._status_label)
        controls_col.addStretch(1)
        body.addLayout(controls_col)

        preview_col = QVBoxLayout()
        preview_col.addWidget(QLabel("Preview", self))
        self._preview = _PreviewPane(self)
        preview_col.addWidget(self._preview, 1)
        body.addLayout(preview_col, 1)
        root.addLayout(body, 1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._controls.changed.connect(self._schedule_preview)
        self._schedule_preview()

    def _current_target(self) -> ExportTarget:
        index = self._target_combo.currentIndex() if self._target_combo is not None else 0
        return self._targets[max(0, index)]

    # -- live preview -----------------------------------------------------------
    def _schedule_preview(self, *_args) -> None:
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    def refresh_preview(self) -> None:
        """Re-render immediately — wire to the host tab becoming current,
        since underlying data can change while this panel isn't visible."""
        self._update_preview()

    def _update_preview(self) -> None:
        preview_opts = dict(self._controls.options(), fmt="png")
        try:
            png_bytes = self._current_target().render_fn(preview_opts)
        except Exception as exc:  # noqa: BLE001 - surface any render failure in the preview itself
            self._preview.show_error(str(exc))
            return
        self._preview.show_png_bytes(png_bytes)

    # -- export -----------------------------------------------------------------
    def _do_export(self) -> None:
        target = self._current_target()
        opts = self._controls.options()
        try:
            png_bytes = target.render_fn(opts)
        except Exception as exc:  # noqa: BLE001 - same failures the preview already surfaces
            self._status_label.setText(str(exc))
            return
        ext = opts["fmt"]
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {target.label}", f"{target.default_filename}.{ext}", f"{ext.upper()} (*.{ext})"
        )
        if not path:
            return
        with open(path, "wb") as f:
            f.write(png_bytes)
        self._status_label.setText(f"Saved to {path}")
