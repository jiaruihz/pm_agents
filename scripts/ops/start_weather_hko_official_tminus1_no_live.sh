#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${WEATHER_HKO_LOCK_TMUX_SESSION:-weather_hko_official_tminus1_no_live}"
TARGET_DATE="${WEATHER_HKO_LOCK_TARGET_DATE:-}"
INTERVAL_SEC="${WEATHER_HKO_LOCK_INTERVAL_SEC:-30}"
SHARES="${WEATHER_HKO_LOCK_SHARES:-5}"
MAX_SHARES_PER_MARKET="${WEATHER_HKO_LOCK_MAX_SHARES_PER_MARKET:-5}"
MAX_NO_ASK="${WEATHER_HKO_LOCK_MAX_NO_ASK:-0.93}"
MAX_SOURCE_DETECT_AGE_MIN="${WEATHER_HKO_LOCK_MAX_SOURCE_DETECT_AGE_MIN:-5}"
MAX_SOURCE_OBSERVATION_LAG_MIN="${WEATHER_HKO_LOCK_MAX_SOURCE_OBSERVATION_LAG_MIN:-30}"
FOK_IMMEDIATE_RETRIES="${WEATHER_HKO_LOCK_FOK_IMMEDIATE_RETRIES:-2}"
LIVE="${WEATHER_HKO_LOCK_LIVE:-0}"
MARKET_PROXY="${WEATHER_HKO_LOCK_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
OUTPUT_DIR="${WEATHER_HKO_LOCK_OUTPUT_DIR:-$RUNTIME_ROOT/output/hko_official_tminus1_no_live}"
LOG_FILE="$RUNTIME_ROOT/loop/hko_official_tminus1_no_live.log"
PID_FILE="$RUNTIME_ROOT/loop/hko_official_tminus1_no_live.pid"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR" "$RUNTIME_ROOT/loop"
cmd=(
  "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_hko_official_tminus1_no_live.py"
  --loop --output-dir "$OUTPUT_DIR" --interval-sec "$INTERVAL_SEC" --shares "$SHARES"
  --max-shares-per-market "$MAX_SHARES_PER_MARKET" --max-no-ask "$MAX_NO_ASK"
  --fok-immediate-retries "$FOK_IMMEDIATE_RETRIES"
  --max-source-detect-age-min "$MAX_SOURCE_DETECT_AGE_MIN" --max-source-observation-lag-min "$MAX_SOURCE_OBSERVATION_LAG_MIN"
)
if [[ "$LIVE" == "1" ]]; then cmd+=(--live --confirm-live); fi
if [[ -n "$TARGET_DATE" ]]; then cmd+=(--target-date "$TARGET_DATE"); fi
if [[ -n "$MARKET_PROXY" ]]; then cmd+=(--market-proxy "$MARKET_PROXY"); fi

if [[ -f "$PID_FILE" ]]; then
  old="$(cat "$PID_FILE" || true)"
  [[ "$old" == tmux:* ]] && weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "${old#tmux:}" 2>/dev/null || true
fi
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
pkill -f "$PROJECT_DIR/scripts/ops/weather_hko_official_tminus1_no_live.py --loop" 2>/dev/null || true
printf -v quoted_cmd '%q ' "${cmd[@]}"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" "cd $(printf '%q' "$PROJECT_DIR") && set -a && { [ ! -f .env ] || . ./.env || true; } && set +a && exec $quoted_cmd >> $(printf '%q' "$LOG_FILE") 2>&1"
echo "tmux:$TMUX_SESSION" > "$PID_FILE"
echo "started hko_official_tminus1_no_live socket=$TMUX_SOCKET session=$TMUX_SESSION mode=$([[ "$LIVE" == "1" ]] && echo live || echo shadow) shares=$SHARES market_cap=$MAX_SHARES_PER_MARKET max_no_ask=$MAX_NO_ASK"
