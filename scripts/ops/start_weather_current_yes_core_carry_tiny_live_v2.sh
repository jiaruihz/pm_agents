#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
source "$ROOT/scripts/ops/weather_market_proxy_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$ROOT" data_feed_runtime_root)}"
SNAPSHOT_DIR="${CURRENT_YES_CORE_CARRY_SNAPSHOT_DIR:-$(weather_production_path "$ROOT" strategy_paper_snapshot_dir)}"
OBSERVATION_CACHE="${CURRENT_YES_CORE_CARRY_OBSERVATION_CACHE:-$(weather_production_path "$ROOT" observation_cache_path)}"
FORECAST_CURVE_DIR="${CURRENT_YES_CORE_CARRY_FORECAST_CURVE_DIR:-$(weather_production_path "$ROOT" forecast_hourly_curve_dir)}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-/Volumes/jrs/pm_agents/runtime}"
OUTPUT_DIR="${CURRENT_YES_CORE_CARRY_TINY_LIVE_V2_OUTPUT_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/current_yes_core_carry_tiny_live_v2}"
SESSION="${CURRENT_YES_CORE_CARRY_TINY_LIVE_V2_TMUX_SESSION:-weather_current_yes_core_carry_tiny_live_v2}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$PM_RUNTIME_ROOT")"
LOG_FILE="$OUTPUT_DIR/runner.log"
MARKET_PROXY="$(weather_resolve_market_proxy "$ROOT")"
NEAR_CORE_WS_CONTROL_MANIFEST="${CURRENT_YES_CORE_CARRY_NEAR_CORE_WS_CONTROL_MANIFEST:-$OUTPUT_DIR/near_core_ws_control_readiness.json}"

if [[ ! -f "$NEAR_CORE_WS_CONTROL_MANIFEST" ]]; then
  echo "near-Core live readiness manifest missing: $NEAR_CORE_WS_CONTROL_MANIFEST" >&2
  exit 1
fi

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"
PREFLIGHT="$("$PY" -c \
  'import json; from scripts.ops.weather_current_yes_core_carry_tiny_live_v2 import DEPLOYMENT_METADATA, assert_runtime_contract; assert_runtime_contract(); print(json.dumps(DEPLOYMENT_METADATA, sort_keys=True))')"
echo "deployment preflight passed: $PREFLIGHT"

session_cmd="cd '$ROOT' && export PYTHONPATH='$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py loop \
    --snapshot-dir '$SNAPSHOT_DIR' \
    --observation-cache '$OBSERVATION_CACHE' \
    --forecast-curve-dir '$FORECAST_CURVE_DIR' \
    --output-dir '$OUTPUT_DIR' \
    --taker-shares 10 \
    --maker-shares 5 \
    --pullback-maker-shares 0 \
    --maker-refresh-sec 15 \
    --order-ttl-min 15 \
    --max-city-days-per-bj-day 10 \
    --max-daily-cost-usd 100 \
    --near-core-maker-probe-enabled \
    --confirm-near-core-maker-probe-live \
    --near-core-max-city-days-per-bj-day 0 \
    --near-core-max-daily-cost-usd 30 \
    --near-core-ws-control-manifest '$NEAR_CORE_WS_CONTROL_MANIFEST' \
    --interval-seconds 15 \
    --book-proxy '$MARKET_PROXY' \
    --live --confirm-live >> '$LOG_FILE' 2>&1"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$SESSION" "$session_cmd"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
