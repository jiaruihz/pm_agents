#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
EXECUTION_POLICY="${WEATHER_BRANCH_EXECUTION_POLICY:-mid_price_core_v2}"
SOURCE_POLICY="${WEATHER_BRANCH_SOURCE_POLICY:-mid_price_core_v1}"
STRATEGY_INSTANCE="${WEATHER_BRANCH_STRATEGY_INSTANCE:-${EXECUTION_POLICY}_branch}"
TMUX_SESSION="${WEATHER_BRANCH_TMUX_SESSION:-weather_policy_branch_${STRATEGY_INSTANCE}}"
SOURCE_STRATEGY_INSTANCE="${WEATHER_BRANCH_SOURCE_STRATEGY_INSTANCE:-}"
ALLOWED_CITIES="${WEATHER_BRANCH_ALLOWED_CITIES:-${WEATHER_LIVE_ALLOWED_CITIES:-}}"
INITIAL_DELAY="${INITIAL_DELAY_SEC:-300}"
INTERVAL="${INTERVAL_SEC:-1800}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
PID_FILE="$LOG_DIR/policy_branch_${STRATEGY_INSTANCE}.pid"
OUT_FILE="$LOG_DIR/policy_branch_${STRATEGY_INSTANCE}.out"
mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" == "tmux:$TMUX_SESSION" ]] && weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already running policy=$EXECUTION_POLICY tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
    exit 0
  elif [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running policy=$EXECUTION_POLICY pid=$old_pid"
    exit 0
  fi
fi

launch_cmd=(env
  WEATHER_BRANCH_EXECUTION_POLICY="$EXECUTION_POLICY"
  WEATHER_BRANCH_SOURCE_POLICY="$SOURCE_POLICY"
  WEATHER_BRANCH_STRATEGY_INSTANCE="$STRATEGY_INSTANCE"
  WEATHER_BRANCH_SOURCE_STRATEGY_INSTANCE="$SOURCE_STRATEGY_INSTANCE"
  WEATHER_BRANCH_ALLOWED_CITIES="$ALLOWED_CITIES"
  INITIAL_DELAY_SEC="$INITIAL_DELAY"
  INTERVAL_SEC="$INTERVAL"
  scripts/ops/weather_policy_branch_loop.sh)
printf -v quoted_launch_cmd '%q ' "${launch_cmd[@]}"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $quoted_launch_cmd > $(printf '%q' "$OUT_FILE") 2>&1"
echo "tmux:$TMUX_SESSION" >"$PID_FILE"
echo "started instance=$STRATEGY_INSTANCE policy=$EXECUTION_POLICY source=$SOURCE_POLICY/${SOURCE_STRATEGY_INSTANCE:-*} tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
