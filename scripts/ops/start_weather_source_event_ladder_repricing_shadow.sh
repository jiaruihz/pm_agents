#!/usr/bin/env bash
set -euo pipefail

# Dedicated zero-notional collector for post-cross ladder repricing research.
# It deliberately uses a separate tmux session/output tree from the existing
# T-1 NO observer and never passes an explicit target date, so the underlying
# observer routes every city through its own local market date.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="${WEATHER_EVENT_LADDER_TMUX_SOCKET:-weather-jrs}"
TMUX_SESSION="${WEATHER_EVENT_LADDER_TMUX_SESSION:-weather_source_event_ladder_repricing_shadow}"
OUTPUT_DIR="${WEATHER_EVENT_LADDER_OUTPUT_DIR:-$RUNTIME_ROOT/output/source_event_ladder_repricing_shadow}"
INTERVAL_SECONDS="${WEATHER_EVENT_LADDER_INTERVAL_SECONDS:-30}"
FOLLOW_MINUTES="${WEATHER_EVENT_LADDER_FOLLOW_MINUTES:-90}"
SOURCES="${WEATHER_EVENT_LADDER_SOURCES:-amos_runway noaa_madis_hfmetar singapore_mss jma_amedas hko_obs cowin_obs fmi mgm ims_lod}"
MARKET_PROXY="${WEATHER_EVENT_LADDER_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
LOG_FILE="$RUNTIME_ROOT/loop/source_event_ladder_repricing_shadow.log"

mkdir -p "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --interval-seconds "$INTERVAL_SECONDS"
  --follow-minutes "$FOLLOW_MINUTES"
  --fresh-scope all
  --sources
)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi

tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "target_date=per_city_local"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "follow_minutes=$FOLLOW_MINUTES"
echo "sources=$SOURCES"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "fresh_scope=all"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
