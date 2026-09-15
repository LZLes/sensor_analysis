#!/usr/bin/env bash
# Build, ad-hoc sign, and package Sensor Calibration Studio as a .dmg.
#
# Must run on macOS — this repo was developed in a Linux sandbox with no
# access to a Mac, so this script has never actually been executed.
# Treat the first run as the Phase 0 feasibility spike the migration plan
# called for: confirm the .app actually launches and renders a chart
# (see pyinstaller.spec's docstring) before trusting it for distribution.
#
# Ad-hoc signed only — no Apple Developer account needed or used. This
# satisfies Apple Silicon's "every executable must be signed" requirement
# so the app runs at all, but it is NOT the same as proper Developer ID
# signing + notarization: Gatekeeper will still show an "unidentified
# developer" / "could not verify" warning on first launch on another
# Mac. Users get past it with right-click > Open, or System Settings >
# Privacy & Security > Open Anyway — a one-time step per machine. If you
# later get a paid Apple Developer account, replace the `codesign --sign -`
# call below with `codesign --sign "Developer ID Application: ..."` and
# add `xcrun notarytool submit` + `xcrun stapler staple` steps after it.
#
# Usage:
#   python3 -m pip install -r macos_app/requirements-macos.txt \
#                          -r macos_app/packaging/requirements-build.txt
#   ./macos_app/packaging/build_macos.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

APP_NAME="Sensor Calibration Studio"
APP_FILE="SensorCalibrationStudio.app"
VERSION="$(python3 -c 'from macos_app.version import APP_VERSION; print(APP_VERSION)')"
DIST_DIR="$REPO_ROOT/dist"
BUILD_DIR="$REPO_ROOT/build"
DMG_NAME="SensorCalibrationStudio-${VERSION}.dmg"

echo "==> Building ${APP_NAME} ${VERSION}"

echo "==> Running PyInstaller"
pyinstaller macos_app/packaging/pyinstaller.spec \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR" \
    --noconfirm

APP_PATH="$DIST_DIR/$APP_FILE"
if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: expected bundle not found at $APP_PATH" >&2
  exit 1
fi

echo "==> Bundle size before cleanup:"
du -sh "$APP_PATH"

# Strip unused Qt plugin categories PyInstaller's PySide6 hooks bundle
# unconditionally (see pyinstaller.spec's comment on why this has to be a
# post-build step, not an Analysis()-level exclude). Every name here is a
# standalone plugin loaded dynamically via QPluginLoader — confirmed via
# `grep -rl` over macos_app/ that nothing here imports the corresponding
# Qt module (SQL, Qt3D, Bluetooth, positioning/sensors, multimedia,
# serial/CAN bus, text-to-speech, geoservices, the Designer/Help/QML
# tooling plugins) — deleting the .so/.dylib itself just means Qt can't
# offer that unused functionality; nothing in this app calls into it.
# Deliberately NOT touching Qt/lib/*.dylib (linked libraries) — see the
# spec file's comment on why that's a real risk this script won't take
# blind. Run BEFORE signing: codesign must be the last step, since
# removing files after signing invalidates the signature.
UNUSED_PLUGIN_DIRS=(
  sqldrivers sceneparsers assetimporters renderers canbus bluetooth nfc
  positioning position sensors serialport texttospeech virtualkeyboard
  geoservices multimedia designer qthelp labs qmllint qmltooling
  scxmldatamodel vectorimageformats renderplugins geometryloaders webview
)
echo "==> Removing unused Qt plugin categories"
for plugin_dir in "${UNUSED_PLUGIN_DIRS[@]}"; do
  find "$APP_PATH" -type d -path "*/Qt/plugins/${plugin_dir}" -print -exec rm -rf {} + 2>/dev/null || true
done

echo "==> Bundle size after cleanup:"
du -sh "$APP_PATH"

echo "==> Ad-hoc signing (no Apple Developer account — see this script's"
echo "    header comment for what this does and doesn't give you)"
codesign --deep --force --sign - "$APP_PATH"
codesign --verify --verbose "$APP_PATH"

echo "==> Bundle size:"
du -sh "$APP_PATH"

echo "==> Creating $DMG_NAME"
DMG_STAGING="$BUILD_DIR/dmg_staging"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP_PATH" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"

DMG_PATH="$DIST_DIR/$DMG_NAME"
rm -f "$DMG_PATH"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_STAGING" -ov -format UDZO "$DMG_PATH"

echo
echo "==> Done: $DMG_PATH"
echo
echo "This is ad-hoc signed, not notarized — first launch on another Mac"
echo "will show Gatekeeper's warning. See this script's header for the"
echo "workaround and the upgrade path to real Developer ID signing."
