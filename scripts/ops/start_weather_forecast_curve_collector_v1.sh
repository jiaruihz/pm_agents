#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
source "$PROJECT_DIR/scripts/ops/weather_process_supervision.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_FORECAST_CURVE_TMUX_SESSION:-weather_forecast_curve_collector_v1}"
OUTPUT_ROOT="${WEATHER_DATA_FEED_TARGETED_OUTPUT_ROOT:-$RUNTIME_ROOT/targeted_output}"
STATUS_PATH="${WEATHER_FORECAST_CURVE_STATUS_PATH:-$RUNTIME_ROOT/output/forecast_curve_collector/latest.json}"
ENRICHMENT_OUTPUT="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OUTPUT_DIR:-$RUNTIME_ROOT/output/forecast_enrichment}"
INTERVAL_SEC="${WEATHER_FORECAST_CURVE_INTERVAL_SEC:-900}"
REFRESH_SEC="${WEATHER_FORECAST_CURVE_OPEN_METEO_REFRESH_SEC:-3600}"
ENRICHMENT_REFRESH_SEC="${WEATHER_DATA_FEED_FORECAST_ENRICHMENT_OPEN_METEO_REFRESH_SEC:-21600}"
TIMEOUT_SEC="${WEATHER_FORECAST_CURVE_TIMEOUT_SEC:-300}"
LOG_FILE="$RUNTIME_ROOT/loop/forecast_curve_collector_v1.log"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$(dirname "$STATUS_PATH")" "$ENRICHMENT_OUTPUT" "$OUTPUT_ROOT"

loop_command="source '$PROJECT_DIR/scripts/ops/weather_process_supervision.sh'; while true; do date -u +\"[forecast_curve_collector] start_utc=%Y-%m-%dT%H:%M:%SZ\"; WEATHER_DATA_FEED_OUTPUT_ROOT='$OUTPUT_ROOT' WEATHER_DATA_FEED_FORECAST_LIVE_REFRESH_SEC='$REFRESH_SEC' weather_run_with_timeout '$TIMEOUT_SEC' '$PY' -u -m weather_data_feed_service.forecast_curve_collector --output-root '$OUTPUT_ROOT' --status-path '$STATUS_PATH' || true; weather_run_with_timeout '$TIMEOUT_SEC' '$PY' -u -m weather_data_feed_service forecast-enrichment -- --output-dir '$ENRICHMENT_OUTPUT' --market-snapshot-dir '$OUTPUT_ROOT/paper_snapshots' --include-station-diff --include-research-cities --open-meteo-refresh-sec '$ENRICHMENT_REFRESH_SEC' --no-single-runs --max-workers 4 || true; date -u +\"[forecast_curve_collector] done_utc=%Y-%m-%dT%H:%M:%SZ\"; sleep '$INTERVAL_SEC'; done"

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec /bin/bash -lc $(printf '%q' "$loop_command") >> '$LOG_FILE' 2>&1"

echo "started session=$TMUX_SESSION"
echo "owner=forecast_curves_and_enrichment"
echo "interval_sec=$INTERVAL_SEC"
echo "status=$STATUS_PATH"
echo "log=$LOG_FILE"
