#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="${WEATHER_FAST_PREV_NO_TMUX_SOCKET:-weather-jrs}"
TMUX_SESSION="${WEATHER_FAST_PREV_NO_TMUX_SESSION:-weather_fast_source_prev_no_trial}"
TARGET_DATE="${WEATHER_FAST_PREV_NO_TARGET_DATE:-}"
INTERVAL_SEC="${WEATHER_FAST_PREV_NO_INTERVAL_SEC:-30}"
SOURCES="${WEATHER_FAST_PREV_NO_SOURCES:-jma_amedas singapore_mss fmi amos_runway noaa_madis_hfmetar}"
LIVE_CITIES="${WEATHER_FAST_PREV_NO_LIVE_CITIES:-Helsinki}"
SHADOW_CITIES="${WEATHER_FAST_PREV_NO_SHADOW_CITIES:-Tokyo Singapore Busan LA Dallas Houston SanFrancisco}"
MAX_SHARES_PER_TRADE="${WEATHER_FAST_PREV_NO_MAX_SHARES_PER_TRADE:-10}"
MAX_SHARES_PER_MARKET="${WEATHER_FAST_PREV_NO_MAX_SHARES_PER_MARKET:-10}"
MAX_SHARES_PER_TRADE_BY_CITY="${WEATHER_FAST_PREV_NO_MAX_SHARES_PER_TRADE_BY_CITY:-}"
MAX_SHARES_PER_MARKET_BY_CITY="${WEATHER_FAST_PREV_NO_MAX_SHARES_PER_MARKET_BY_CITY:-}"
MAX_NO_ASK="${WEATHER_FAST_PREV_NO_MAX_NO_ASK:-0.92}"
MAX_SOURCE_AGE_MIN="${WEATHER_FAST_PREV_NO_MAX_SOURCE_AGE_MIN:-15}"
BOOK_TIMEOUT_SEC="${WEATHER_FAST_PREV_NO_BOOK_TIMEOUT_SEC:-5}"
OUTPUT_DIR="${WEATHER_FAST_PREV_NO_OUTPUT_DIR:-$RUNTIME_ROOT/output/fast_source_prev_no_trial}"
MARKET_PROXY="${WEATHER_FAST_PREV_NO_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
ENABLE_LIVE="${WEATHER_FAST_PREV_NO_LIVE:-1}"
CONFIRM_LIVE="${WEATHER_FAST_PREV_NO_CONFIRM_LIVE:-1}"
LOG_FILE="$RUNTIME_ROOT/loop/fast_source_prev_no_trial.log"

mkdir -p "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_prev_no_trial.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --interval-sec "$INTERVAL_SEC"
  --max-shares-per-trade "$MAX_SHARES_PER_TRADE"
  --max-shares-per-market "$MAX_SHARES_PER_MARKET"
  --max-no-ask "$MAX_NO_ASK"
  --max-source-age-min "$MAX_SOURCE_AGE_MIN"
  --book-timeout-sec "$BOOK_TIMEOUT_SEC"
  --sources
)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
if [[ -n "$MAX_SHARES_PER_TRADE_BY_CITY" ]]; then
  cmd+=(--max-shares-per-trade-by-city "$MAX_SHARES_PER_TRADE_BY_CITY")
fi
if [[ -n "$MAX_SHARES_PER_MARKET_BY_CITY" ]]; then
  cmd+=(--max-shares-per-market-by-city "$MAX_SHARES_PER_MARKET_BY_CITY")
fi
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

tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && set -a && [[ -f .env ]] && source .env || true && set +a && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "interval_sec=$INTERVAL_SEC"
echo "sources=$SOURCES"
echo "live_cities=$LIVE_CITIES"
echo "shadow_cities=$SHADOW_CITIES"
echo "max_shares_per_trade=$MAX_SHARES_PER_TRADE"
echo "max_shares_per_market=$MAX_SHARES_PER_MARKET"
echo "max_shares_per_trade_by_city=${MAX_SHARES_PER_TRADE_BY_CITY:-none}"
echo "max_shares_per_market_by_city=${MAX_SHARES_PER_MARKET_BY_CITY:-none}"
echo "max_no_ask=$MAX_NO_ASK"
echo "max_source_age_min=$MAX_SOURCE_AGE_MIN"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "live=$ENABLE_LIVE confirm_live=$CONFIRM_LIVE"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
