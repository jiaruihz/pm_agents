#!/usr/bin/env bash
set -euo pipefail

# Zero-notional Seoul/Busan AMOS first-seen state and active-ladder collector.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_KOREA_FIRST_SEEN_TMUX_SESSION:-weather_korea_first_seen_state_v1}"
OUTPUT_DIR="${WEATHER_KOREA_FIRST_SEEN_OUTPUT_DIR:-$RUNTIME_ROOT/output/korea_first_seen_state_v1}"
SOURCE_JSONL="${WEATHER_KOREA_FIRST_SEEN_SOURCE_JSONL:-$RUNTIME_ROOT/output/live_cross_observations/high_frequency_observations.jsonl}"
FORECAST_ROOT="${WEATHER_KOREA_FIRST_SEEN_FORECAST_ROOT:-$RUNTIME_ROOT/targeted_output/forecast_hourly_curves}"
CONFIG="${WEATHER_KOREA_FIRST_SEEN_CONFIG:-$PROJECT_DIR/configs/weather/korea_first_seen_collector_v1.json}"
MARKET_PROXY="${WEATHER_KOREA_FIRST_SEEN_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-http://127.0.0.1:7890}}"
INTERVAL_SECONDS="${WEATHER_KOREA_FIRST_SEEN_INTERVAL_SECONDS:-5}"
LOG_FILE="$RUNTIME_ROOT/loop/korea_first_seen_state_v1.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_korea_first_seen_collector.py"
  --loop
  --config "$CONFIG"
  --source-jsonl "$SOURCE_JSONL"
  --forecast-root "$FORECAST_ROOT"
  --output-dir "$OUTPUT_DIR"
  --market-proxy "$MARKET_PROXY"
  --interval-seconds "$INTERVAL_SECONDS"
)

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on canonical JRS tmux socket $TMUX_SOCKET"
echo "mode=zero_notional_research_collector"
echo "source_jsonl=$SOURCE_JSONL"
echo "forecast_root=$FORECAST_ROOT"
echo "output_dir=$OUTPUT_DIR"
echo "config=$CONFIG"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "log=$LOG_FILE"
