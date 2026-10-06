#!/bin/bash
# Starts the bundled web-app server (or reuses a running one) and prints its
# URL. Called by the app's AppleScript wrapper (packaging/app.applescript);
# lives at Contents/Resources/start-server.sh inside the built app.
RES="$(cd "$(dirname "$0")" && pwd)"
PY="$RES/venv/bin/python"
LOG="$HOME/Library/Logs/Sensor Calibration Studio.log"
STATE="$HOME/Library/Application Support/Sensor Calibration Studio/server.json"
IDLE_SHUTDOWN_MIN="${IDLE_SHUTDOWN_MIN:-15}"

running_url() {
  local port
  port="$(sed -n 's/.*"port": *\([0-9][0-9]*\).*/\1/p' "$STATE" 2>/dev/null)"
  [ -n "$port" ] || return 1
  /usr/bin/curl -fs --max-time 2 "http://127.0.0.1:$port/api/app/info" | grep -q sensor-calibration-studio || return 1
  echo "http://127.0.0.1:$port"
}

# Already running: just bring up a browser tab.
if URL="$(running_url)"; then
  [ "${WEB_APP_NO_BROWSER:-0}" = 1 ] || /usr/bin/open "$URL"
  echo "$URL"
  exit 0
fi

[ -x "$PY" ] || { echo "Python environment missing at $PY — rebuild the app." >&2; exit 1; }
mkdir -p "$(dirname "$LOG")"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ]; then mv -f "$LOG" "$LOG.old"; fi
echo "=== $(date) — launching from $RES" >> "$LOG"

cd "$RES/app" || exit 1
WEB_APP_IDLE_SHUTDOWN_MIN="$IDLE_SHUTDOWN_MIN" nohup "$PY" -m web_app.main >> "$LOG" 2>&1 < /dev/null &
PID=$!

# Wait up to ~20 s for it to answer; fail fast if it crashes.
for _ in $(seq 1 80); do
  if URL="$(running_url)"; then echo "$URL"; exit 0; fi
  kill -0 "$PID" 2>/dev/null || { echo "Server exited during startup." >&2; exit 1; }
  sleep 0.25
done
echo "Server did not respond within 20 s." >&2
exit 1
