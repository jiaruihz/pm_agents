#!/usr/bin/env bash
set -euo pipefail

# Exact production contract for the original broad T-1 NO stale-book
# collector.  Keep this separate from experimental active-bracket launchers so
# a controller recovery cannot inherit their changing defaults.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
PAPER_SNAPSHOT_DIR="${WEATHER_STRATEGY_PAPER_SNAPSHOT_DIR:-$(weather_production_path "$PROJECT_DIR" strategy_paper_snapshot_dir)}"
MARKET_BOOK_BATCH_ROOT="${WEATHER_MARKET_BOOK_BATCH_ROOT:-$(weather_production_path "$PROJECT_DIR" market_books_root)/batches}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="weather_fast_source_stale_book"
OUTPUT_DIR="$RUNTIME_ROOT/output/fast_source_stale_book"
LOG_FILE="$RUNTIME_ROOT/loop/fast_source_stale_book_observer.log"
PY="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$TMUX_SESSION" 2>/dev/null; then
  echo "already running session=$TMUX_SESSION socket=$TMUX_SOCKET"
  exit 0
fi

cmd=(
  env
  "WEATHER_STRATEGY_PAPER_SNAPSHOT_DIR=$PAPER_SNAPSHOT_DIR"
  "WEATHER_MARKET_BOOK_BATCH_ROOT=$MARKET_BOOK_BATCH_ROOT"
  "$PY"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --interval-seconds 60
  --follow-minutes 20
  --fresh-scope t_minus_1_no
  --sources
  amos_runway
  noaa_madis_hfmetar
  singapore_mss
  jma_amedas
  hko_obs
  cowin_obs
  fmi
  mgm
  ims_lod
  --market-proxy http://127.0.0.1:7890
)

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q ' "${cmd[@]}") >> $(printf '%q' "$LOG_FILE") 2>&1"

echo "started session=$TMUX_SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR mode=telemetry_only_no_orders"
