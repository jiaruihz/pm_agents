#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${THETA_HIGHER_NO_CARRY_TMUX_SESSION:-weather_theta_higher_no_carry_shadow_v1}"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/theta_higher_no_carry_shadow_v1"
PID_FILE="$RUNTIME_DIR/loop.pid"
LOG_FILE="$RUNTIME_DIR/loop.log"
mkdir -p "$RUNTIME_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if [[ "$old_pid" == "tmux:$TMUX_SESSION" ]] && weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already_running tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
    exit 0
  elif [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already_running pid=$old_pid"
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
MIN_DECLINE_C="${MIN_DECLINE_C:-0.5}"
MIN_NO_ASK="${MIN_NO_ASK:-0.75}"
MAX_NO_ASK="${MAX_NO_ASK:-0.97}"
MIN_AVAILABLE_NOTIONAL="${MIN_AVAILABLE_NOTIONAL:-5}"
MIN_GAP_TO_NEXT_BRACKET_C="${MIN_GAP_TO_NEXT_BRACKET_C:-0}"
MAX_SNAPSHOT_AGE_MIN="${MAX_SNAPSHOT_AGE_MIN:-45}"
MAX_OBS_AGE_MIN="${MAX_OBS_AGE_MIN:-20}"
PRE_METAR_UPDATE_BLACKOUT_MIN="${PRE_METAR_UPDATE_BLACKOUT_MIN:-6}"
MIN_LOCAL_HOUR="${MIN_LOCAL_HOUR:-13}"
MAX_LOCAL_HOUR="${MAX_LOCAL_HOUR:-17}"
SHADOW_NOTIONAL="${SHADOW_NOTIONAL:-5}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"

args=(
  scripts/ops/weather_theta_higher_no_carry_shadow.py
  loop
  --min-decline-c "$MIN_DECLINE_C"
  --min-no-ask "$MIN_NO_ASK"
  --max-no-ask "$MAX_NO_ASK"
  --min-available-notional "$MIN_AVAILABLE_NOTIONAL"
  --min-gap-to-next-bracket-c "$MIN_GAP_TO_NEXT_BRACKET_C"
  --max-snapshot-age-min "$MAX_SNAPSHOT_AGE_MIN"
  --max-obs-age-min "$MAX_OBS_AGE_MIN"
  --pre-metar-update-blackout-min "$PRE_METAR_UPDATE_BLACKOUT_MIN"
  --min-local-hour "$MIN_LOCAL_HOUR"
  --max-local-hour "$MAX_LOCAL_HOUR"
  --shadow-notional "$SHADOW_NOTIONAL"
  --interval-seconds "$INTERVAL_SECONDS"
)
if [[ -n "${MAX_CURRENT_YES_ASK:-}" ]]; then
  args+=(--max-current-yes-ask "$MAX_CURRENT_YES_ASK")
fi

printf -v quoted_args '%q ' "$PYTHON_BIN" "${args[@]}"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$ROOT") && exec $quoted_args >> $(printf '%q' "$LOG_FILE") 2>&1"
echo "tmux:$TMUX_SESSION" >"$PID_FILE"
echo "started tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION log=$LOG_FILE"
