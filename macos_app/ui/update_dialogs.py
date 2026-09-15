"""
Update-check dialog helpers — shared between main.py's silent startup
check and MainWindow's explicit Help > Check for Updates... action, so
the two call sites don't duplicate QMessageBox construction.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from macos_app.update_check import UpdateInfo
from macos_app.version import APP_VERSION


def show_update_available_dialog(info: UpdateInfo, parent: QWidget | None = None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("Update Available")
    box.setText(f"Sensor Calibration Studio {info.version} is available (you have {APP_VERSION}).")
    if info.notes:
        box.setInformativeText(info.notes[:500])
    download_btn = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is download_btn:
        QDesktopServices.openUrl(QUrl(info.url))


def show_up_to_date_dialog(parent: QWidget | None = None) -> None:
    QMessageBox.information(parent, "No Updates", f"You're up to date (version {APP_VERSION}).")


def show_check_failed_dialog(message: str, parent: QWidget | None = None) -> None:
    QMessageBox.warning(parent, "Update Check Failed", f"Couldn't check for updates: {message}")
