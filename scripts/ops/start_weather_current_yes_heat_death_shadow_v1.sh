#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$ROOT" data_feed_runtime_root)}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-$(weather_production_path "$ROOT" pm_runtime_root)}"
FEATURE_STORE_ROOT="${WEATHER_FEATURE_STORE_DIR:-$(weather_production_path "$ROOT" feature_store_root)}"
SNAPSHOT_DIR="${CURRENT_YES_HEAT_DEATH_SNAPSHOT_DIR:-$(weather_production_path "$ROOT" strategy_paper_snapshot_dir)}"
OBSERVATION_CACHE="${CURRENT_YES_HEAT_DEATH_OBSERVATION_CACHE:-$(weather_production_path "$ROOT" observation_cache_path)}"
FORECAST_CURVE_DIR="${CURRENT_YES_HEAT_DEATH_FORECAST_CURVE_DIR:-$(weather_production_path "$ROOT" forecast_hourly_curve_dir)}"
OUTPUT_DIR="${CURRENT_YES_HEAT_DEATH_OUTPUT_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/current_yes_heat_death_shadow_v1}"
SESSION="${CURRENT_YES_HEAT_DEATH_TMUX_SESSION:-weather_current_yes_heat_death_shadow_v1}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
LOG_FILE="$OUTPUT_DIR/runner.log"

mkdir -p "$OUTPUT_DIR"
if pgrep -f "weather_current_yes_heat_death_shadow_v1.py loop" >/dev/null 2>&1; then
  echo "current-YES heat-death shadow already running"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_heat_death_shadow_v1.py loop \
    --snapshot-dir '$SNAPSHOT_DIR' \
    --observation-cache '$OBSERVATION_CACHE' \
    --forecast-curve-dir '$FORECAST_CURVE_DIR' \
    --output-dir '$OUTPUT_DIR' \
    --feature-store '$FEATURE_STORE_ROOT' \
    --interval-seconds 30 >> '$LOG_FILE' 2>&1"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
