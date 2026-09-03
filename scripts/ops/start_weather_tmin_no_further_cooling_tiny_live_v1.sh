#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
source "$ROOT/scripts/ops/weather_market_proxy_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-/Volumes/jrs/pm_agents/runtime}"
SOURCE="${WEATHER_TMIN_V1_SOURCE_CANDIDATES:-/Volumes/jrs/weather_data_feed_service_runtime/output/tmin_no_further_cooling_shadow_v1/signal_candidates.jsonl}"
OUTPUT="${WEATHER_TMIN_V1_TINY_LIVE_OUTPUT_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/tmin_no_further_cooling_tiny_live_v1}"
SESSION="${WEATHER_TMIN_V1_TINY_LIVE_SESSION:-weather_tmin_no_further_cooling_tiny_live_v1}"
SOCKET="$(weather_jrs_tmux_start_socket "$PM_RUNTIME_ROOT")"
PROXY="$(weather_resolve_market_proxy "$ROOT")"
LOG="$OUTPUT/runner.log"

weather_jrs_tmux_mkdir "$SOCKET" "$OUTPUT"
cmd="cd '$ROOT' && export PYTHONPATH='$ROOT' && exec '$PY' -u scripts/ops/weather_tmin_no_further_cooling_tiny_live_v1.py loop \
  --source-candidates '$SOURCE' \
  --output-dir '$OUTPUT' \
  --shares 5 \
  --max-candidate-age-sec 180 \
  --order-ttl-min 15 \
  --max-daily-cost-usd 35 \
  --interval-seconds 10 \
  --market-proxy '$PROXY' \
  --live --confirm-live >> '$LOG' 2>&1"
weather_jrs_tmux_guarded_replace_session "$SOCKET" "$SESSION" "$cmd"
echo "started tmux_socket=$SOCKET session=$SESSION output=$OUTPUT mode=tiny_live shares=5"
