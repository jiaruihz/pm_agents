#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="${WEATHER_FAST_PREV_NO_TMUX_SOCKET:-weather-jrs}"
TMUX_SESSION="${WEATHER_FAST_PREV_NO_TMUX_SESSION:-weather_fast_source_prev_no_trial}"
SCREEN_SESSION="${WEATHER_FAST_PREV_NO_SCREEN_SESSION:-weather_fast_source_prev_no_trial}"
TARGET_DATE="${WEATHER_FAST_PREV_NO_TARGET_DATE:-}"
INTERVAL_SEC="${WEATHER_FAST_PREV_NO_INTERVAL_SEC:-30}"
BURST_INTERVAL_SEC="${WEATHER_FAST_PREV_NO_BURST_INTERVAL_SEC:-10}"
SOURCES="${WEATHER_FAST_PREV_NO_SOURCES:-jma_amedas singapore_mss fmi amos_runway mgm ims_lod noaa_madis_hfmetar}"
LIVE_CITIES="${WEATHER_FAST_PREV_NO_LIVE_CITIES:-Helsinki Busan Singapore Tokyo}"
SHADOW_CITIES="${WEATHER_FAST_PREV_NO_SHADOW_CITIES:-Seoul TelAviv Ankara Istanbul Atlanta Miami SanFrancisco}"
MAX_SOURCE_AGE_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_AGE_MIN:-15}"
MAX_SOURCE_DETECT_AGE_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_DETECT_AGE_MIN:-5}"
MAX_SOURCE_OBSERVATION_LAG_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_OBSERVATION_LAG_MIN:-15}"
NEXT_METAR_WINDOW_MIN="${WEATHER_FAST_PREV_NO_NEXT_METAR_WINDOW_MIN:-20}"
BOOK_TIMEOUT_SEC="${WEATHER_FAST_PREV_NO_BOOK_TIMEOUT_SEC:-5}"
POST_ONLY_IMMEDIATE_REPRICES="${WEATHER_FAST_PREV_NO_POST_ONLY_IMMEDIATE_REPRICES:-${WEATHER_FAST_PREV_NO_FOK_IMMEDIATE_RETRIES:-2}}"
TAKER_SHARES="${WEATHER_FAST_PREV_NO_TAKER_SHARES:-10}"
MAKER_SHARES="${WEATHER_FAST_PREV_NO_MAKER_SHARES:-5}"
OUTPUT_DIR="${WEATHER_FAST_PREV_NO_OUTPUT_DIR:-$RUNTIME_ROOT/output/fast_source_prev_no_trial}"
MARKET_PROXY="${WEATHER_FAST_PREV_NO_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
ENABLE_LIVE="${WEATHER_FAST_PREV_NO_LIVE:-1}"
CONFIRM_LIVE="${WEATHER_FAST_PREV_NO_CONFIRM_LIVE:-1}"
ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS="${WEATHER_FAST_PREV_NO_ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS:-1}"
LOG_FILE="$RUNTIME_ROOT/loop/fast_source_prev_no_trial.log"
PID_FILE="$RUNTIME_ROOT/loop/fast_source_prev_no_trial.pid"
START_MODE="${WEATHER_FAST_PREV_NO_START_MODE:-tmux}"

mkdir -p "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_prev_no_trial.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --interval-sec "$INTERVAL_SEC"
  --burst-interval-sec "$BURST_INTERVAL_SEC"
  --max-source-age-min "$MAX_SOURCE_AGE_MIN"
  --max-source-detect-age-min "$MAX_SOURCE_DETECT_AGE_MIN"
  --max-source-observation-lag-min "$MAX_SOURCE_OBSERVATION_LAG_MIN"
  --next-metar-window-min "$NEXT_METAR_WINDOW_MIN"
  --book-timeout-sec "$BOOK_TIMEOUT_SEC"
  --post-only-immediate-reprices "$POST_ONLY_IMMEDIATE_REPRICES"
  --taker-shares "$TAKER_SHARES"
  --maker-shares "$MAKER_SHARES"
  --sources
)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
cmd+=(--live-cities)
read -r -a live_city_args <<< "$LIVE_CITIES"
cmd+=("${live_city_args[@]}")
cmd+=(--shadow-cities)
read -r -a shadow_city_args <<< "$SHADOW_CITIES"
cmd+=("${shadow_city_args[@]}")
if [[ -n "$TARGET_DATE" ]]; then
  cmd+=(--target-date "$TARGET_DATE")
fi
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi
if [[ "$ENABLE_LIVE" == "1" ]]; then
  cmd+=(--live)
fi
if [[ "$CONFIRM_LIVE" == "1" ]]; then
  cmd+=(--confirm-live)
fi
if [[ "$ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS" == "1" ]]; then
  cmd+=(--acknowledge-historical-share-cap-incidents)
fi

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" == screen:* ]]; then
    screen -S "${old_pid#screen:}" -X quit 2>/dev/null || true
  elif [[ "$old_pid" == tmux:* ]]; then
    tmux -L "$TMUX_SOCKET" kill-session -t "${old_pid#tmux:}" 2>/dev/null || true
  elif [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 1
  fi
fi
tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
screen -S "$SCREEN_SESSION" -X quit 2>/dev/null || true
pkill -f "$PROJECT_DIR/scripts/ops/weather_fast_source_prev_no_trial.py --loop" 2>/dev/null || true
printf -v quoted_cmd '%q ' "${cmd[@]}"
if [[ "$START_MODE" == "tmux" ]]; then
  tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
    "cd $(printf '%q' "$PROJECT_DIR") && set -a && [[ -f .env ]] && source .env || true && set +a && exec $quoted_cmd >> $(printf '%q' "$LOG_FILE") 2>&1"
  echo "tmux:$TMUX_SESSION" > "$PID_FILE"
else
  screen -dmS "$SCREEN_SESSION" sh -c "cd $(printf '%q' "$PROJECT_DIR") && set -a && { [ ! -f .env ] || . ./.env || true; } && set +a && exec $quoted_cmd >> $(printf '%q' "$LOG_FILE") 2>&1"
  echo "screen:$SCREEN_SESSION" > "$PID_FILE"
fi

echo "started fast_source_prev_no_trial mode=$START_MODE"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "interval_sec=$INTERVAL_SEC"
echo "burst_interval_sec=$BURST_INTERVAL_SEC"
echo "sources=$SOURCES"
echo "live_cities=$LIVE_CITIES"
echo "shadow_cities=$SHADOW_CITIES"
echo "city_trade_policy=scripts/ops/weather_fast_source_city_policy.py"
echo "max_source_age_min=$MAX_SOURCE_AGE_MIN"
echo "max_source_detect_age_min=$MAX_SOURCE_DETECT_AGE_MIN"
echo "max_source_observation_lag_min=$MAX_SOURCE_OBSERVATION_LAG_MIN"
echo "next_metar_window_min=$NEXT_METAR_WINDOW_MIN"
echo "post_only_immediate_reprices=$POST_ONLY_IMMEDIATE_REPRICES"
echo "execution_split=taker_${TAKER_SHARES}_shares+maker_${MAKER_SHARES}_shares"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "pid_file=$PID_FILE"
echo "live=$ENABLE_LIVE confirm_live=$CONFIRM_LIVE"
echo "acknowledge_historical_share_cap_incidents=$ACKNOWLEDGE_HISTORICAL_SHARE_CAP_INCIDENTS"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
