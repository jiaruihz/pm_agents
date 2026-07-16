#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TMUX_SOCKET="${WEATHER_DATA_FEED_TMUX_SOCKET:-weather-jrs}"
SESSION="${WEATHER_LIVE_PATROL_TMUX_SESSION:-weather_live_runtime_patrol}"
RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/live_runtime_patrol"
LOG_FILE="$RUNTIME/tmux.log"

mkdir -p "$RUNTIME"
if tmux -L "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION"
  exit 0
fi

tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && exec '$PROJECT_DIR/scripts/ops/run_weather_live_runtime_patrol_launchd.sh' >> '$LOG_FILE' 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
