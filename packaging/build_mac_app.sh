#!/bin/bash
# Builds "Sensor Calibration Studio.app" — a self-contained macOS app for the
# local web app (web_app/) — and installs it into /Applications.
#
# The app is a small stay-open AppleScript applet (packaging/app.applescript)
# that starts the server (packaging/start-server.sh) and stays in the Dock
# while it runs. It carries its own copy of the code (web_app/, core/,
# modes/, sample_data/) and its own Python virtualenv under
# Contents/Resources, so it keeps working if this repo folder moves and never
# reads from ~/Documents at launch (which macOS privacy protection would block
# or prompt for). It uses this Mac's installed Python, so it's built for THIS
# machine — not a redistributable binary.
#
# After changing the code, re-run this script to update the installed app
# (the virtualenv is reused when requirements haven't changed, so it's quick).
#
# Usage:
#   ./packaging/build_mac_app.sh                      # install to /Applications
#   ./packaging/build_mac_app.sh --dest ~/Applications
#   ./packaging/build_mac_app.sh --python /path/to/python3.12
set -euo pipefail

APP_NAME="Sensor Calibration Studio"
BUNDLE_ID="com.lzles.sensorcalibrationstudio"
VERSION="1.0.0"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$REPO/packaging"
DEST="/Applications"
PYTHON="$(command -v python3)"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
TARGET="$DEST/$APP_NAME.app"
RES="$TARGET/Contents/Resources"

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
echo "Building $TARGET"
echo "  using $("$PYTHON" -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if pgrep -f "$TARGET/Contents/MacOS" >/dev/null 2>&1; then
  echo "  quitting the running copy first"
  osascript -e "tell application \"$TARGET\" to quit" >/dev/null 2>&1 || true
  sleep 2
fi

# -- 1. Keep an existing venv if its requirements + Python version still match --------
REQ_HASH="$(shasum < "$REPO/requirements-web.txt" | cut -c1-16)-$("$PYTHON" -c 'import sys; print(sys.version.split()[0])')"
KEEP="$DEST/.scs-venv-keep"
rm -rf "$KEEP"
if [ -f "$RES/venv/.req-hash" ] && [ "$(cat "$RES/venv/.req-hash")" = "$REQ_HASH" ] \
   && "$RES/venv/bin/python" -c "import fastapi" 2>/dev/null; then
  mv "$RES/venv" "$KEEP"
fi

# -- 2. Applet ------------------------------------------------------------------------------
rm -rf "$TARGET"
osacompile -s -o "$TARGET" "$PKG/app.applescript" 2>/dev/null
if [ -d "$KEEP" ]; then
  mv "$KEEP" "$RES/venv"
  echo "  reusing existing Python environment"
fi

# Icon: replace the default applet icon (and drop the asset catalog that
# would otherwise take precedence over it).
cp "$PKG/AppIcon.icns" "$RES/applet.icns"
rm -f "$RES/Assets.car"
PLIST="$TARGET/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$PLIST" >/dev/null 2>&1 || true
plutil -replace CFBundleIdentifier -string "$BUNDLE_ID" "$PLIST"
plutil -replace CFBundleName -string "$APP_NAME" "$PLIST"
plutil -replace CFBundleDisplayName -string "$APP_NAME" "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace NSHighResolutionCapable -bool true "$PLIST"

# -- 3. App code + server start script ------------------------------------------------------
mkdir -p "$RES/app"
for d in web_app core modes sample_data; do
  rsync -a --exclude '__pycache__' --exclude '*.pyc' "$REPO/$d" "$RES/app/"
done
install -m 755 "$PKG/start-server.sh" "$RES/start-server.sh"

# -- 4. Python environment (created in place: venvs aren't relocatable) ----------------------
if [ ! -d "$RES/venv" ]; then
  echo "  creating Python environment (first build takes a few minutes) ..."
  "$PYTHON" -m venv "$RES/venv"
  "$RES/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$RES/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REPO/requirements-web.txt"
  echo "$REQ_HASH" > "$RES/venv/.req-hash"
fi
(cd "$RES/app" && "$RES/venv/bin/python" -c "import web_app.main") \
  || { echo "The bundled app failed to import — see the error above." >&2; exit 1; }
"$RES/venv/bin/python" -m compileall -q "$RES/app" >/dev/null || true   # faster first launch

# -- 5. Re-sign (ad hoc) — editing Info.plist/resources invalidated the applet's seal --------
codesign --force --deep --sign - "$TARGET" 2>/dev/null
touch "$TARGET"   # nudge Finder/Dock to pick up the icon

echo
echo "Installed: $TARGET  ($(du -sh "$TARGET" | cut -f1))"
echo "Open it from Applications, Launchpad or Spotlight. Logs: ~/Library/Logs/$APP_NAME.log"
