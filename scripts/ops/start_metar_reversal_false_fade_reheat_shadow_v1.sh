#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${METAR_REVERSAL_TMUX_SESSION:-metar_reversal_false_fade_reheat_shadow_v1}"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1"
LOG_FILE="$RUNTIME_DIR/shadow_tmux.log"
SNAPSHOT_DIR="${METAR_REVERSAL_SNAPSHOT_DIR:-$RUNTIME_ROOT/targeted_output/paper_snapshots}"
OBSERVATION_CACHE="${METAR_REVERSAL_OBSERVATION_CACHE:-$RUNTIME_ROOT/output/observations/latest.json}"
INTERVAL_SECONDS="${METAR_REVERSAL_INTERVAL_SECONDS:-300}"

mkdir -p "$RUNTIME_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q' "$PROJECT_DIR/.venv/bin/python") -u scripts/ops/metar_reversal_false_fade_reheat_shadow_v1.py loop --snapshot-dir $(printf '%q' "$SNAPSHOT_DIR") --observation-cache $(printf '%q' "$OBSERVATION_CACHE") --interval-seconds $(printf '%q' "$INTERVAL_SECONDS") >> $(printf '%q' "$LOG_FILE") 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION log=$LOG_FILE"
