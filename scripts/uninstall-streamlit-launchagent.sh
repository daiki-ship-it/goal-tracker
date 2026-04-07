#!/usr/bin/env bash
# Removes the Streamlit LaunchAgent installed by install-streamlit-launchagent.sh
set -euo pipefail

LABEL="com.goaltracker.streamlit"
PLIST_NAME="${LABEL}.plist"
DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
GUI_DOMAIN="gui/$(id -u)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is for macOS only." >&2
  exit 1
fi

if [[ -f "${DEST}" ]]; then
  launchctl bootout "${GUI_DOMAIN}" "${DEST}" 2>/dev/null || true
  rm -f "${DEST}"
  echo "Removed ${DEST}"
else
  echo "No plist at ${DEST} (nothing to do)."
fi
