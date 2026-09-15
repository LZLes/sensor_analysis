# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Sensor Calibration Studio (macOS).

Build with (from the repo root, on an actual Mac):
    pyinstaller macos_app/packaging/pyinstaller.spec --distpath dist --workpath build --noconfirm
or just run macos_app/packaging/build_macos.sh, which does this plus
ad-hoc signing and .dmg creation.

Requires macos_app/requirements-macos.txt AND
macos_app/packaging/requirements-build.txt installed.

IMPORTANT — this has never been run on a real Mac (written and reasoned
through in a Linux sandbox with no macOS/PyInstaller-for-macOS available
to test against). Treat the first build as the Phase 0 feasibility spike
the migration plan called for: confirm the app actually launches, and
specifically that QtWebEngine renders a Plotly chart rather than a blank
QWebEngineView (the collect_all("PySide6") call below exists to prevent
that — see the comment on it), before trusting this for real distribution.
Also validate the resulting bundle size (QtWebEngine's Chromium runtime
was flagged early in the migration plan as the single biggest packaging-
size driver in the whole app).
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent.parent  # SPECPATH is macos_app/packaging
sys.path.insert(0, str(REPO_ROOT))

from macos_app.version import APP_VERSION  # noqa: E402

APP_NAME = "Sensor Calibration Studio"
APP_FILE_NAME = "SensorCalibrationStudio"
BUNDLE_ID = "com.lzles.sensorcalibrationstudio"

# collect_all pulls in PySide6's Qt plugins/translations/QtWebEngine
# resources (the QtWebEngineProcess helper binary, icudtl.dat, *.pak
# locale/resource files) that PyInstaller's default import-scanning misses
# — this is the single most common cause of a PySide6+QtWebEngine app that
# launches fine but shows a permanently blank web view once packaged.
#
# Bundle-size note: collect_all("PySide6") is indiscriminate — it grabs
# every Qt module PySide6 ships, including several this app never imports
# (SQL drivers, Qt3D, Bluetooth, multimedia, the whole QML/Quick stack —
# this app's charts are Plotly HTML/JS in a plain QWebEngineView, not
# QML). Filtering that list at the Analysis() level here was tried and
# measured ineffective: pyinstaller-hooks-contrib's own PySide6 hooks
# re-collect the same broad plugin set independently (triggered by this
# app's real `import PySide6.QtWidgets` etc.), regardless of what's
# filtered out of this variable — only ~14MB of ~1.1GB moved. The
# reliable fix is a POST-BUILD cleanup instead: build_macos.sh deletes
# specific unused Qt/plugins/* subdirectories (standalone .so/.dylib
# files, dynamically loaded via QPluginLoader — safe to remove without
# touching anything link-time) after PyInstaller finishes and before
# codesigning. Qt/lib/*.dylib files are deliberately left alone even
# though several also look unused from this app's perspective — those
# are linked libraries, and removing one that QtWebEngineCore secretly
# depends on internally would break the app outright; that risk isn't
# worth taking blind from a sandbox with no way to verify the real
# dependency graph. See build_macos.sh for the actual cleanup + its
# measured effect on the real build.
pyside6_datas, pyside6_binaries, pyside6_hidden = collect_all("PySide6")

a = Analysis(
    [str(REPO_ROOT / "macos_app" / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=pyside6_binaries,
    datas=pyside6_datas + [
        (str(REPO_ROOT / "sample_data"), "sample_data"),
    ],
    hiddenimports=pyside6_hidden + [
        # modes/*.py and core/*.py are imported dynamically enough
        # (mode views import specific functions, not whole packages via a
        # pattern PyInstaller's static analysis always catches) that it's
        # worth listing them explicitly rather than debugging a
        # ModuleNotFoundError in a packaged build.
        #
        # Deliberately NOT listing core.drive or core.ai_insights: neither
        # is imported anywhere in macos_app/ (Google Drive Cloud Sessions
        # was dropped per the migration plan; Ollama AI Insights was
        # flagged as a cheap follow-up but never actually built into any
        # mode view). Pulling either in here would drag google-auth/
        # google-api-python-client or ollama into the bundle for a feature
        # that doesn't exist yet — confirmed while testing this spec file:
        # including core.drive here made PyInstaller's analysis import
        # google-auth's cryptography dependency, which crashed with a
        # PyO3 panic in the environment this was tested in (the exact
        # failure mode core/drive.py's own docstring warns about).
        "modes.amperometry", "modes.solid_state", "modes.cyclic_voltammetry", "modes.assay",
        "core.parsing", "core.persistence", "core.calibration_table", "core.numeric",
        "core.plotting", "core.constants", "core.step_detection", "core.shared_tabs",
    ],
    hookspath=[],
    excludes=[
        # streamlit itself is a required import — see
        # macos_app/requirements-macos.txt's note (core/shared_tabs.py etc.
        # do `import streamlit as st` at module level even though this app
        # never calls into it) — but its own server/CLI entry points and
        # browser-facing static assets are dead weight here.
        "streamlit.web.cli",
        # cryptography (and Google's OAuth stack, which is what actually
        # depends on it in this codebase — core/drive.py, unused by
        # macos_app/) is pulled in transitively by something in streamlit's
        # own dependency tree even with core.drive excluded from
        # hiddenimports above. macos_app/ never touches anything Drive/
        # crypto-related, so excluding it outright is correct regardless of
        # environment — it also legitimately shrinks the bundle. Found
        # because PyInstaller's hook-cryptography.py crashed analysis with
        # a PyO3 panic in the sandbox this spec was tested in (the exact
        # broken-crypto-backend failure mode core/drive.py's own docstring
        # warns about); excluding avoids relying on that hook at all.
        "cryptography", "google.auth", "googleapiclient", "google_auth_httplib2",
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=APP_FILE_NAME,
    debug=False,
    strip=False,
    upx=False,  # UPX-compressed Qt frameworks are a common source of subtle, hard-to-debug runtime breakage
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_FILE_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_FILE_NAME}.app",
    icon=None,  # add macos_app/packaging/AppIcon.icns here once a real icon is designed
    bundle_identifier=BUNDLE_ID,
    version=APP_VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "© LZLes",
    },
)
