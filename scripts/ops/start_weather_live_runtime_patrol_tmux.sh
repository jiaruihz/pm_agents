#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
SESSION="${WEATHER_LIVE_PATROL_TMUX_SESSION:-weather_live_runtime_patrol}"
RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/live_runtime_patrol"
LOG_FILE="$RUNTIME/tmux.log"

mkdir -p "$RUNTIME"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && exec '$PROJECT_DIR/scripts/ops/run_weather_live_runtime_patrol_launchd.sh' >> '$LOG_FILE' 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
