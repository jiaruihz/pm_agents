#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LABEL="com.pm-agents.weather-canonical-refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
INTERVAL="${WEATHER_CANONICAL_REFRESH_INTERVAL_SECONDS:-300}"

mkdir -p "$HOME/Library/LaunchAgents" "$RUNTIME_DIR"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>StandardOutPath</key><string>$RUNTIME_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$RUNTIME_DIR/launchd.err.log</string>
</dict></plist>
EOF

plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "installed label=$LABEL interval_seconds=$INTERVAL plist=$PLIST"
