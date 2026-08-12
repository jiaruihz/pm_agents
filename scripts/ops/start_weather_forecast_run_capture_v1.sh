#!/usr/bin/env bash
set -euo pipefail

# Coverage-only exact-run D-1/D-2 forecast collector.  It does not create
# SignalCandidate, TradeIntent, order, fill, or any execution-side artifact.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_FORECAST_RUN_CAPTURE_TMUX_SESSION:-weather_forecast_run_capture_v1}"
OUTPUT_DIR="${WEATHER_FORECAST_RUN_CAPTURE_OUTPUT_DIR:-$RUNTIME_ROOT/output/forecast_run_capture}"
INTERVAL_SECONDS="${WEATHER_FORECAST_RUN_CAPTURE_INTERVAL_SECONDS:-1800}"
TIMEOUT_SECONDS="${WEATHER_FORECAST_RUN_CAPTURE_TIMEOUT_SECONDS:-60}"
MARKET_CAPTURE_DEMAND_OUTPUT="${WEATHER_FORECAST_RUN_CAPTURE_MARKET_DEMAND_OUTPUT:-$RUNTIME_ROOT/output/forecast_run_capture/market_capture_demands.jsonl}"
MARKET_CAPTURE_DEMAND_TTL_MIN="${WEATHER_FORECAST_RUN_CAPTURE_MARKET_DEMAND_TTL_MIN:-120}"
MARKET_CAPTURE_DEMAND_CITIES="${WEATHER_FORECAST_RUN_CAPTURE_MARKET_DEMAND_CITIES:-Amsterdam Busan Helsinki Tokyo}"
MARKET_CAPTURE_DEMAND_MAX_RUN_AGE_HOURS="${WEATHER_FORECAST_RUN_CAPTURE_MARKET_DEMAND_MAX_RUN_AGE_HOURS:-24}"
LOG_FILE="$RUNTIME_ROOT/loop/forecast_run_capture_v1.log"
PY="${PYTHON_BIN:-/Users/deepsleep/projects/pm_agents/.venv/bin/python}"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

loop_command="while true; do date -u +\"[forecast_run_capture] start_utc=%Y-%m-%dT%H:%M:%SZ\"; rc=0; for offset in 18 12 6 0; do '$PY' -u -m weather_data_feed_service.forecast_run_capture --output-dir '$OUTPUT_DIR' --candidate-latest-cycle --candidate-cycle-offset-hours \"\$offset\" --timeout-sec '$TIMEOUT_SECONDS' --market-capture-demand-output '$MARKET_CAPTURE_DEMAND_OUTPUT' --market-capture-demand-ttl-min '$MARKET_CAPTURE_DEMAND_TTL_MIN' --market-capture-demand-max-run-age-hours '$MARKET_CAPTURE_DEMAND_MAX_RUN_AGE_HOURS' --market-capture-demand-cities $MARKET_CAPTURE_DEMAND_CITIES || rc=\$?; done; date -u +\"[forecast_run_capture] done_utc=%Y-%m-%dT%H:%M:%SZ returncode=\$rc\"; sleep '$INTERVAL_SECONDS'; done"

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec /bin/bash -lc $(printf '%q' "$loop_command") >> '$LOG_FILE' 2>&1"

echo "started session=$TMUX_SESSION"
echo "mode=coverage_only_no_execution"
echo "output_dir=$OUTPUT_DIR"
echo "market_capture_demand_output=$MARKET_CAPTURE_DEMAND_OUTPUT"
echo "market_capture_demand_cities=$MARKET_CAPTURE_DEMAND_CITIES"
echo "market_capture_demand_max_run_age_hours=$MARKET_CAPTURE_DEMAND_MAX_RUN_AGE_HOURS"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "log=$LOG_FILE"
