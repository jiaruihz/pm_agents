#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
RUNTIME_DIR="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1}"
LOG_FILE="$RUNTIME_DIR/candidate_shadow_loop.log"
PY="$PROJECT_DIR/.venv/bin/python"
SESSION="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_SESSION:-tmax_distribution_edge_candidate_shadow_v1}"

INTERVAL_SEC="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_INTERVAL_SEC:-900}"
SOURCE="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_SOURCE:-$PROJECT_DIR/docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv}"
ASK_FLOORS="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_ASK_FLOORS:-0.05 0.10 0.20 0.25 0.40}"
DIAGNOSTIC_DAILY_CAPS="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_DIAGNOSTIC_DAILY_CAPS:-10 15 20}"
DEFAULT_ASK_FLOOR="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_DEFAULT_ASK_FLOOR:-0.20}"
FIXED_SHARES="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_FIXED_SHARES:-5}"
EXCLUDE_TREND3H_FLAT="${TMAX_DISTRIBUTION_EDGE_CANDIDATE_EXCLUDE_TREND3H_FLAT:-1}"

mkdir -p "$RUNTIME_DIR"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

args=()
for floor in $ASK_FLOORS; do
  args+=(--ask-floor "$floor")
done
for cap in $DIAGNOSTIC_DAILY_CAPS; do
  args+=(--diagnostic-daily-cap-usd "$cap")
done
if [[ "$EXCLUDE_TREND3H_FLAT" == "1" ]]; then
  args+=(--exclude-trend3h-flat)
fi

runner_cmd=(
  "$PY" "-u" "scripts/ops/tmax_distribution_edge_candidate_shadow_v1.py" "loop"
  "--runtime-dir" "$RUNTIME_DIR"
  "--source" "$SOURCE"
  "--fixed-shares" "$FIXED_SHARES"
  "--default-ask-floor" "$DEFAULT_ASK_FLOOR"
  "--interval-seconds" "$INTERVAL_SEC"
  "${args[@]}"
)
printf -v quoted_runner_cmd '%q ' "${runner_cmd[@]}"
bootstrap="cd $(printf '%q' "$PROJECT_DIR") && exec $quoted_runner_cmd >> $(printf '%q' "$LOG_FILE") 2>&1"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "already running tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
  exit 0
fi
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$bootstrap")"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"
