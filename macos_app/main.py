"""
Entry point for the native macOS app.

Run from the repo root with:  python -m macos_app.main
(Requires macos_app/requirements-macos.txt installed.)

This does not touch app.py, modes/, or core/'s Streamlit-facing behavior —
see the "Non-negotiable constraint" section of the migration plan.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QUndoGroup
from PySide6.QtWidgets import QApplication

from macos_app.ui.app_state import AppState
from macos_app.ui.main_window import MainWindow
from macos_app.ui.settings import Settings


class AppController:
    """Owns process-wide singletons (the undo group and Recent Files
    settings are legitimately cross-window) and the list of open windows.
    A plain object rather than a QObject — nothing here needs Qt signals of
    its own, it just needs to outlive the windows it creates."""

    def __init__(self) -> None:
        self.settings = Settings()
        self.undo_group = QUndoGroup()
        self._windows: list[MainWindow] = []

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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Sensor Calibration Studio")

    controller = AppController()
    controller.open_new_window()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
