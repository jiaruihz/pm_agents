#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LABEL="com.pm-agents.production-reliability"
PLIST="${PRODUCTION_RELIABILITY_PLIST_PATH:-$HOME/Library/LaunchAgents/$LABEL.plist}"
LOG_DIR="${PRODUCTION_RELIABILITY_LOG_DIR:-$HOME/Library/Logs/pm-agents/production-reliability}"
STATE_ROOT="${PRODUCTION_RELIABILITY_STATE_ROOT:-$HOME/Library/Application Support/pm_agents/production_reliability}"
INTERVAL="${PRODUCTION_RELIABILITY_INTERVAL_SECONDS:-60}"
APPLY_SAFE="${PRODUCTION_RELIABILITY_APPLY_SAFE:-0}"
RENDER_ONLY="${PRODUCTION_RELIABILITY_RENDER_ONLY:-0}"

if [[ "$APPLY_SAFE" == "1" ]]; then
  APPLY_SAFE_XML="    <string>--apply-safe</string>"
else
  APPLY_SAFE_XML=""
fi

mkdir -p "$(dirname "$PLIST")" "$LOG_DIR" "$STATE_ROOT"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/.venv/bin/python</string>
    <string>$PROJECT_DIR/scripts/ops/production_reliability_supervisor.py</string>
    <string>--state-root</string>
    <string>$STATE_ROOT</string>
    <string>--notify</string>
    <string>--maintain-weather-route</string>
$APPLY_SAFE_XML
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$INTERVAL</integer>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.err.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"
if [[ "$RENDER_ONLY" == "1" ]]; then
  echo "rendered label=$LABEL interval_seconds=$INTERVAL apply_safe=$APPLY_SAFE plist=$PLIST"
  exit 0
fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "installed label=$LABEL interval_seconds=$INTERVAL apply_safe=$APPLY_SAFE state_root=$STATE_ROOT"
