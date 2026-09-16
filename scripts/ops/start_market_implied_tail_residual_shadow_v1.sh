#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SESSION="market_implied_tail_residual_shadow_v1"
OUTPUT_DIR="$RUNTIME_ROOT/output/market_implied_tail_residual_shadow_v1"
LOG_FILE="$RUNTIME_ROOT/loop/market_implied_tail_residual_shadow_v1.log"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && exec '$PY' '$ROOT/scripts/ops/market_implied_tail_residual_shadow_v1.py' loop --market-books '$RUNTIME_ROOT/market_books/latest.json' --ladder-snapshot '$RUNTIME_ROOT/market_ladder_snapshots/latest.json' --output-dir '$OUTPUT_DIR' --interval-seconds 60 >> '$LOG_FILE' 2>&1"
echo "started session=$SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR mode=shadow_zero_notional"
