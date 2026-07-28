#!/usr/bin/env bash
set -euo pipefail

# Canonical JRS entrypoint for the generic T-1 NO stale-book shadow observer.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${WEATHER_STALE_BOOK_TMUX_SESSION:-weather_fast_source_stale_book}"
TARGET_DATE="${WEATHER_STALE_BOOK_TARGET_DATE:-}"
INTERVAL_SECONDS="${WEATHER_STALE_BOOK_INTERVAL_SECONDS:-60}"
FOLLOW_MINUTES="${WEATHER_STALE_BOOK_FOLLOW_MINUTES:-20}"
SOURCES="${WEATHER_STALE_BOOK_SOURCES:-amos_runway noaa_madis_hfmetar singapore_mss jma_amedas hko_obs cowin_obs fmi mgm ims_lod}"
OUTPUT_DIR="${WEATHER_STALE_BOOK_OUTPUT_DIR:-$RUNTIME_ROOT/output/fast_source_stale_book}"
MARKET_PROXY="${WEATHER_STALE_BOOK_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
FRESH_SCOPE="${WEATHER_STALE_BOOK_FRESH_SCOPE:-t_minus_1_no}"
LOG_FILE="$RUNTIME_ROOT/loop/fast_source_stale_book_observer.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --interval-seconds "$INTERVAL_SECONDS"
  --follow-minutes "$FOLLOW_MINUTES"
  --fresh-scope "$FRESH_SCOPE"
  --sources
)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
if [[ -n "$TARGET_DATE" ]]; then
  cmd+=(--target-date "$TARGET_DATE")
fi
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "follow_minutes=$FOLLOW_MINUTES"
echo "sources=$SOURCES"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "fresh_scope=$FRESH_SCOPE"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
