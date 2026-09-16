#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
source "$ROOT/scripts/ops/weather_market_proxy_env.sh"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
PM_RUNTIME_ROOT="${WEATHER_PM_RUNTIME_ROOT:-$(weather_production_path "$ROOT" pm_runtime_root)}"
CORE_RUNTIME="${CORE_CARRY_EVENT_SHADOW_CORE_RUNTIME:-$PM_RUNTIME_ROOT/weather_edge_v1/current_yes_core_carry_tiny_live_v2}"
OUTPUT_DIR="${CORE_CARRY_EVENT_SHADOW_OUTPUT_DIR:-$PM_RUNTIME_ROOT/weather_edge_v1/current_yes_core_carry_event_rescore_shadow_v1}"
SESSION="${CORE_CARRY_EVENT_SHADOW_TMUX_SESSION:-weather_current_yes_core_carry_event_rescore_shadow_v1}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$PM_RUNTIME_ROOT")"
MARKET_PROXY="$(weather_resolve_market_proxy "$ROOT")"
LOG_FILE="$OUTPUT_DIR/runner.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"

session_cmd="cd '$ROOT' && export PYTHONPATH='$ROOT' && exec '$PY' -u scripts/ops/weather_current_yes_core_carry_event_rescore_shadow_v1.py \
    --core-runtime '$CORE_RUNTIME' \
    --output-dir '$OUTPUT_DIR' \
    --market-proxy '$MARKET_PROXY' \
    --book-timeout-sec 5 \
    --max-books-per-run 40 \
    --max-books-per-utc-day 2000 \
    --interval-seconds 15 >> '$LOG_FILE' 2>&1"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$SESSION" "$session_cmd"

echo "started tmux_socket=$TMUX_SOCKET session=$SESSION output=$OUTPUT_DIR log=$LOG_FILE"
