"""
Lightweight OTA-update check — deliberately not a full auto-updater
(Sparkle etc.). This app is ad-hoc signed (no Apple Developer account —
see the packaging notes), so Gatekeeper's "unidentified developer"/
"could not verify" prompt shows on every install regardless of whether
the download was automatic or manual. A full silent auto-installer would
buy little UX benefit over a "new version available, click to download"
notification, at a large integration cost: Sparkle is a native Cocoa
framework with no first-class Python/PySide6 bridge, so wiring it into a
PyInstaller-bundled app is substantial work for a benefit Gatekeeper
mostly cancels out anyway.

Checks GitHub's Releases API for the latest tagged release of this repo
and compares its version against APP_VERSION — ties the update mechanism
directly to how Phase 10 packaging actually ships releases (tag + GitHub
Release + .dmg asset), so no separate release-manifest file needs to be
authored/maintained by hand.

Uses QNetworkAccessManager (async, signal-based) rather than a Python
thread + requests/urllib: Qt's own event loop already drives this app, so
this adds no new dependency and never blocks the UI thread while waiting
on the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from macos_app.version import APP_VERSION

_REPO = "LZLes/sensor_analysis"
_RELEASES_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_RELEASES_PAGE_URL = f"https://github.com/{_REPO}/releases/latest"


def parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). Missing/non-numeric parts become 0
    so a malformed or non-semver tag never crashes the comparison — it
    just sorts as not-newer instead."""
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:3]) or (0,)


def is_newer(remote_version: str, local_version: str = APP_VERSION) -> bool:
    remote = parse_version(remote_version)
    local = parse_version(local_version)
    length = max(len(remote), len(local))
    remote = remote + (0,) * (length - len(remote))
    local = local + (0,) * (length - len(local))
    return remote > local


@dataclass
class UpdateInfo:
    version: str
    url: str
    notes: str


class UpdateChecker(QObject):
    """One-shot per .check() call — construct a fresh instance for each
    check rather than reusing one across many checks, since it holds no
    state between calls beyond the QNetworkAccessManager itself."""

    update_available = Signal(object)  # emits UpdateInfo
    no_update = Signal()
    check_failed = Signal(str)  # emits a human-readable error message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)

    def check(self) -> None:
        request = QNetworkRequest(QUrl(_RELEASES_API_URL))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"SensorCalibrationStudio/{APP_VERSION}")
        reply = self._manager.get(request)
        reply.finished.connect(lambda: self._on_finished(reply))

    def _on_finished(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.check_failed.emit(reply.errorString())
            return
        try:
            payload = bytes(reply.readAll().data()).decode("utf-8")
            info = parse_release_response(payload)
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            self.check_failed.emit(str(exc))
            return
        if info is not None:
            self.update_available.emit(info)
        else:
            self.no_update.emit()


def parse_release_response(payload: str) -> UpdateInfo | None:
    """Pure parsing/decision logic, separated from the QNetworkAccessManager
    plumbing above so it's testable without a live network call or a Qt
    event loop. Returns None if the latest release isn't newer than
    APP_VERSION; raises ValueError/KeyError on a malformed response."""
    data = json.loads(payload)
    tag = data.get("tag_name", "")
    if not tag:
        raise ValueError("release response has no tag_name")
    if not is_newer(tag):
        return None
    assets = data.get("assets", [])
    dmg_asset = next((a for a in assets if a.get("name", "").endswith(".dmg")), None)
    url = dmg_asset["browser_download_url"] if dmg_asset else data.get("html_url", _RELEASES_PAGE_URL)
    return UpdateInfo(version=tag, url=url, notes=data.get("body", ""))
