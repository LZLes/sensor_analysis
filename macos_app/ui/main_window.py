"""
MainWindow — the real app shell (supersedes Phase 1's SpikeWindow).

One instance per open session/window (see main.py's open_new_window()):
owns one AppState, contributes its QUndoStack to the process-wide
QUndoGroup so Edit > Undo/Redo always targets the frontmost window, and
provides the native-app chrome Streamlit had no equivalent for: a real
menu bar with keyboard shortcuts, and drag-and-drop file import.

Mode panels (Amperometry/Solid-State/Cyclic Voltammetry/Assay) are
placeholders here — they're built out mode-by-mode in Phases 4-7. This
phase's job is the shell they'll plug into.
"""

from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoGroup
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from macos_app.persistence import build_session_bundle, apply_session_bundle
from macos_app.ui.app_state import AppState
from macos_app.ui.modes.amperometry_view import AmperometryView
from macos_app.ui.modes.assay_view import AssayView
from macos_app.ui.modes.cyclic_voltammetry_view import CyclicVoltammetryView
from macos_app.ui.modes.solid_state_view import SolidStateView
from macos_app.ui.settings import Settings

_MODES = ["Amperometry", "Solid-State", "Cyclic Voltammetry", "Assay"]

_IMPORTABLE_SUFFIXES = (".csv", ".txt", ".pssession")


def _placeholder_page(mode: str) -> QWidget:
    label = QLabel(f"{mode}\n\n(view built in a later phase)")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class MainWindow(QMainWindow):
    def __init__(
        self,
        app_state: AppState,
        settings: Settings,
        undo_group: QUndoGroup,
        on_new_window: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self._settings = settings
        self._undo_group = undo_group
        self._on_new_window = on_new_window

        self.setWindowTitle("Sensor Calibration Studio")
        self.resize(1100, 720)
        self.setAcceptDrops(True)

        undo_group.addStack(app_state.undo_stack)

        self._build_menu_bar()
        self._build_central_widget()
        self.setStatusBar(QStatusBar(self))

        app_state.mode_changed.connect(self._on_mode_changed)

    # -- menu bar / shortcuts --------------------------------------------------
    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        new_window_action = QAction("New Window", self)
        new_window_action.setShortcut(QKeySequence.StandardKey.New)
        new_window_action.triggered.connect(self._on_new_window)
        file_menu.addAction(new_window_action)

        open_action = QAction("Open…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._prompt_open_files)
        file_menu.addAction(open_action)

        self._recent_menu = QMenu("Open Recent", self)
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        file_menu.addMenu(self._recent_menu)

        file_menu.addSeparator()

        export_session_action = QAction("Export Session…", self)
        export_session_action.setShortcut(QKeySequence.StandardKey.Save)
        export_session_action.triggered.connect(self._export_session)
        file_menu.addAction(export_session_action)

        import_session_action = QAction("Import Session…", self)
        import_session_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        import_session_action.triggered.connect(self._import_session)
        file_menu.addAction(import_session_action)

        file_menu.addSeparator()

        close_action = QAction("Close Window", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        edit_menu = menu_bar.addMenu("&Edit")
        undo_action = self._undo_group.createUndoAction(self, "Undo")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        redo_action = self._undo_group.createRedoAction(self, "Redo")
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = self._settings.recent_files()
        if not recents:
            empty = QAction("No Recent Files", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for path in recents:
            action = QAction(path, self)
            action.triggered.connect(lambda checked=False, p=path: self._handle_incoming_files([p]))
            self._recent_menu.addAction(action)

    def _prompt_open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import sensor data",
            "",
            "Sensor data (*.csv *.txt *.pssession);;All files (*)",
        )
        if paths:
            self._handle_incoming_files(paths)

    def _export_session(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Session", "session.json", "JSON (*.json)")
        if not path:
            return
        bundle = build_session_bundle(self.app_state)
        with open(path, "w") as f:
            json.dump(bundle, f)
        self._settings.add_recent_file(path)
        self.statusBar().showMessage(f"Session exported to {path}", 5000)

    def _import_session(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Session", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path) as f:
                bundle = json.load(f)
            apply_session_bundle(self.app_state, bundle)
        except Exception as exc:  # noqa: BLE001 - surface any parse/apply failure to the user
            QMessageBox.warning(self, "Import Session", f"Could not import session: {exc}")
            return
        self._settings.add_recent_file(path)
        self.statusBar().showMessage(f"Session imported from {path}", 5000)

    def _handle_incoming_files(self, paths: list[str]) -> None:
        """Common landing point for both the Open... dialog and drag-and-drop.
        Forwards to the active mode's view if it knows how to import files
        (see SolidStateView.import_files); modes without a real view yet
        just get Recent Files recorded."""
        for path in paths:
            self._settings.add_recent_file(path)
        current_page = self._stack.currentWidget()
        if hasattr(current_page, "import_files"):
            current_page.import_files(paths)
        names = ", ".join(p.split("/")[-1] for p in paths)
        self.statusBar().showMessage(f"Importing: {names}", 5000)

    # -- mode switcher ------------------------------------------------------
    def _build_central_widget(self) -> None:
        central = QWidget(self)
        layout = QHBoxLayout(central)

        self._mode_list = QListWidget(central)
        self._mode_list.setFixedWidth(180)
        for mode in _MODES:
            QListWidgetItem(mode, self._mode_list)
        self._mode_list.currentRowChanged.connect(self._on_mode_list_row_changed)

        self._stack = QStackedWidget(central)
        for mode in _MODES:
            if mode == "Amperometry":
                self._stack.addWidget(AmperometryView(self.app_state, central))
            elif mode == "Solid-State":
                self._stack.addWidget(SolidStateView(self.app_state, central))
            elif mode == "Cyclic Voltammetry":
                self._stack.addWidget(CyclicVoltammetryView(self.app_state, central))
            elif mode == "Assay":
                self._stack.addWidget(AssayView(self.app_state, central))
            else:
                self._stack.addWidget(_placeholder_page(mode))

        layout.addWidget(self._mode_list)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._mode_list.setCurrentRow(_MODES.index(self.app_state.data.mode))

    def _on_mode_list_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self._stack.setCurrentIndex(row)
        self.app_state.set_field("mode", _MODES[row])

    def _on_mode_changed(self, mode: str) -> None:
        """Sync the sidebar to an AppState mode change that didn't originate
        from the sidebar itself (e.g. undo/redo, or a future menu action).
        Signals are blocked during the programmatic update so this doesn't
        loop back into _on_mode_list_row_changed and double-fire set_field."""
        if mode not in _MODES:
            return
        idx = _MODES.index(mode)
        if self._mode_list.currentRow() == idx:
            return
        self._mode_list.blockSignals(True)
        self._mode_list.setCurrentRow(idx)
        self._mode_list.blockSignals(False)
        self._stack.setCurrentIndex(idx)

    # -- drag-and-drop --------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(_IMPORTABLE_SUFFIXES)
        ]
        if paths:
            self._handle_incoming_files(paths)
            event.acceptProposedAction()
