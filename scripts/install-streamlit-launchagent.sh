#!/usr/bin/env bash
# Installs a LaunchAgent so Streamlit keeps running after Terminal closes (macOS).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.goaltracker.streamlit"
PLIST_NAME="${LABEL}.plist"
DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
LOG_DIR="${ROOT}/logs"
STDOUT_LOG="${LOG_DIR}/streamlit-launchd.log"
STDERR_LOG="${LOG_DIR}/streamlit-launchd.err"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is for macOS only." >&2
  exit 1
fi

if [[ -x "${ROOT}/.venv/bin/python3" ]]; then
  PYTHON="${ROOT}/.venv/bin/python3"
elif [[ -x "${ROOT}/venv/bin/python3" ]]; then
  PYTHON="${ROOT}/venv/bin/python3"
else
  PYTHON="$(command -v python3)"
fi

mkdir -p "${LOG_DIR}"
mkdir -p "${HOME}/Library/LaunchAgents"

GUI_DOMAIN="gui/$(id -u)"

if [[ -f "${DEST}" ]]; then
  launchctl bootout "${GUI_DOMAIN}" "${DEST}" 2>/dev/null || true
fi

# Paths with spaces are fine as separate plist string entries.
cat >"${DEST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>-m</string>
    <string>streamlit</string>
    <string>run</string>
    <string>${ROOT}/app.py</string>
    <string>--server.headless</string>
    <string>true</string>
    <string>--server.port</string>
    <string>8501</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${STDOUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${STDERR_LOG}</string>
</dict>
</plist>
EOF

launchctl bootstrap "${GUI_DOMAIN}" "${DEST}"

echo "Installed LaunchAgent: ${DEST}"
echo "Python: ${PYTHON}"
echo "Logs: ${STDOUT_LOG} / ${STDERR_LOG}"
echo "Open: http://localhost:8501"
echo ""
echo "Stop:   launchctl bootout ${GUI_DOMAIN} ${DEST}"
echo "Start:  launchctl bootstrap ${GUI_DOMAIN} ${DEST}"
echo "Restart: launchctl kickstart -k ${GUI_DOMAIN}/${LABEL}"
