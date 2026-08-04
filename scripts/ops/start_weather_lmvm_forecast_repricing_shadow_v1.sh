#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_LMVM_REPRICING_SESSION:-weather_lmvm_forecast_repricing_shadow_v1}"
OUTPUT_DIR="${WEATHER_LMVM_REPRICING_OUTPUT_DIR:-$RUNTIME_ROOT/output/lmvm_forecast_repricing_shadow_v1}"
PYTHON_BIN="${WEATHER_LMVM_REPRICING_PYTHON:-/Users/deepsleep/projects/pm_agents/.venv/bin/python}"
INTERVAL_SECONDS="${WEATHER_LMVM_REPRICING_INTERVAL_SECONDS:-60}"
FOLLOW_MINUTES="${WEATHER_LMVM_REPRICING_FOLLOW_MINUTES:-180}"
LOG_FILE="$RUNTIME_ROOT/loop/lmvm_forecast_repricing_shadow_v1.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
[[ -x "$PYTHON_BIN" ]] || { echo "missing LMVM Python: $PYTHON_BIN" >&2; exit 1; }

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$TMUX_SESSION" 2>/dev/null; then
  echo "already running: tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  exit 0
fi

cmd=(
  "$PYTHON_BIN" -u
  "$PROJECT_DIR/scripts/ops/weather_lmvm_forecast_repricing_shadow_v1.py"
  --snapshot-dir "$RUNTIME_ROOT/full_ladder_output/paper_snapshots"
  --snapshot-dir "$RUNTIME_ROOT/targeted_output/paper_snapshots"
  --output-dir "$OUTPUT_DIR"
  --state "$OUTPUT_DIR/state.json"
  --bootstrap-lookback-files 64
  --follow-minutes "$FOLLOW_MINUTES"
  --max-snapshot-age-seconds 5400
  --loop
  --interval-seconds "$INTERVAL_SECONDS"
)

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

sleep 1
weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$TMUX_SESSION"
echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "execution_mode=zero_notional_shadow"
echo "snapshot_dirs=$RUNTIME_ROOT/full_ladder_output/paper_snapshots,$RUNTIME_ROOT/targeted_output/paper_snapshots"
echo "output_dir=$OUTPUT_DIR"
echo "follow_minutes=$FOLLOW_MINUTES"
echo "log=$LOG_FILE"
