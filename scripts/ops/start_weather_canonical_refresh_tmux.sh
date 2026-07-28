#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
SESSION="weather_canonical_refresh"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
LOG_FILE="$LOG_DIR/tmux.log"

mkdir -p "$LOG_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "canonical refresh already running in tmux; skipping"
  exit 0
fi

printf -v session_cmd \
  'set -eu; cd %q; export WEATHER_DATA_FEED_RUNTIME_ROOT=%q; exec %q >> %q 2>&1' \
  "$PROJECT_DIR" "$RUNTIME_ROOT" \
  "$PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh" "$LOG_FILE"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "$session_cmd"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
