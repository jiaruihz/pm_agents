#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-/Volumes/jrs/pm_agents/runtime}"
OUTPUT_DIR="$PM_RUNTIME_ROOT/weather_edge_v1/runtime_log_maintainer"
SESSION="weather_runtime_log_maintainer"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$SOCKET" "$OUTPUT_DIR"
if weather_jrs_tmux "$SOCKET" has-session -t "=$SESSION" 2>/dev/null; then
  echo "already running session=$SESSION socket=$SOCKET"
  exit 0
fi

cmd=(
  "$PY" "$PROJECT_DIR/scripts/ops/weather_runtime_log_maintainer.py"
  --root "$RUNTIME_ROOT"
  --root "$PM_RUNTIME_ROOT"
  --health-path "$OUTPUT_DIR/latest_summary.json"
  --max-mib 64
  --retain-mib 8
  --interval-seconds 300
)

weather_jrs_tmux "$SOCKET" new-session -d -s "$SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q ' "${cmd[@]}")"
echo "started session=$SESSION socket=$SOCKET health=$OUTPUT_DIR/latest_summary.json"
