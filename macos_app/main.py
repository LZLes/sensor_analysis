"""
Entry point for the native macOS app.

Run from the repo root with:  python -m macos_app.main
(Requires macos_app/requirements-macos.txt installed.)

This does not touch app.py, modes/, or core/'s Streamlit-facing behavior —
see the "Non-negotiable constraint" section of the migration plan.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QUndoGroup
from PySide6.QtWidgets import QApplication

from macos_app.ui.app_state import AppState
from macos_app.ui.main_window import MainWindow
from macos_app.ui.settings import Settings
from macos_app.ui.update_dialogs import show_update_available_dialog
from macos_app.update_check import UpdateChecker, UpdateInfo

_STARTUP_UPDATE_CHECK_DELAY_MS = 2000  # let the first window finish rendering first


class AppController:
    """Owns process-wide singletons (the undo group and Recent Files
    settings are legitimately cross-window) and the list of open windows.
    A plain object rather than a QObject — nothing here needs Qt signals of
    its own, it just needs to outlive the windows it creates."""

    def __init__(self) -> None:
        self.settings = Settings()
        self.undo_group = QUndoGroup()
        self._windows: list[MainWindow] = []
        self._startup_update_checker: UpdateChecker | None = None

    def open_new_window(self) -> MainWindow:
        app_state = AppState()
        window = MainWindow(
            app_state=app_state,
            settings=self.settings,
            undo_group=self.undo_group,
            on_new_window=self.open_new_window,
        )
        window.destroyed.connect(lambda: self._forget_window(window))
        self._windows.append(window)
        window.show()
        return window

    def _forget_window(self, window: MainWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def check_for_updates_on_startup(self) -> None:
        """Silent background check: only interrupts the user if an update
        actually exists. No dialog on "already up to date" or a network
        error (e.g. offline) — that's what the Help menu's explicit
        "Check for Updates..." action is for. Keeps the checker alive on
        self (not a local variable) since QNetworkAccessManager's async
        reply would otherwise risk the checker being garbage-collected
        before the request completes."""
        self._startup_update_checker = UpdateChecker()
        self._startup_update_checker.update_available.connect(self._on_startup_update_available)
        self._startup_update_checker.check()

    def _on_startup_update_available(self, info: UpdateInfo) -> None:
        show_update_available_dialog(info, parent=self._windows[-1] if self._windows else None)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Sensor Calibration Studio")

    controller = AppController()
    controller.open_new_window()
    QTimer.singleShot(_STARTUP_UPDATE_CHECK_DELAY_MS, controller.check_for_updates_on_startup)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
