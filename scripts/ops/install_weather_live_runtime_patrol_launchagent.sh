#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LABEL="com.pm-agents.weather-live-runtime-patrol"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/live_runtime_patrol"
UID_NUM="$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/scripts/ops/run_weather_live_runtime_patrol_launchd.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$RUNTIME/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$RUNTIME/launchd.err.log</string>
</dict>
</plist>
EOF

screen -S weather_live_runtime_patrol -X quit 2>/dev/null || true
pkill -f "$PROJECT_DIR/scripts/ops/weather_live_runtime_patrol.py --loop" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "installed $LABEL"
echo "health=$RUNTIME/latest.json"
echo "logs=$RUNTIME/launchd.out.log $RUNTIME/launchd.err.log"
