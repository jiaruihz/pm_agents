#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$(weather_production_path "$PROJECT_DIR" data_feed_runtime_root)}"
SOURCE_DIR="${WEATHER_KNMI_LADDER_SOURCE_DIR:-/Users/deepsleep/projects/pm_agents_knmi_first_seen_prod}"
SESSION="${WEATHER_KNMI_LADDER_SESSION:-weather_knmi_first_seen_ladder_v1}"
SOCKET="$(weather_jrs_tmux_start_socket)"
PY="${WEATHER_KNMI_LADDER_PYTHON:-/Users/deepsleep/projects/pm_agents/.venv/bin/python}"
SOURCE="${WEATHER_KNMI_LADDER_SOURCE:-$RUNTIME_ROOT/output/knmi_open_data/knmi_observations.jsonl}"
PAPER="${WEATHER_KNMI_LADDER_PAPER_SNAPSHOTS:-$(weather_production_path "$PROJECT_DIR" strategy_paper_snapshot_dir)}"
ORDERBOOKS="${WEATHER_KNMI_LADDER_ORDERBOOK_SNAPSHOTS:-$(weather_production_path "$PROJECT_DIR" market_books_root)/batches}"
OUTPUT="${WEATHER_KNMI_LADDER_OUTPUT:-$RUNTIME_ROOT/output/knmi_first_seen_ladder_v1}"
MARKET_PROXY="${WEATHER_KNMI_LADDER_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-http://127.0.0.1:7890}}"
ACTION="${1:-start}"

if [[ "$ACTION" == "status" ]]; then
  weather_jrs_tmux "$SOCKET" list-panes -t "=$SESSION" -F '#{session_name} #{pane_pid} #{pane_start_command}'
  exit 0
fi
if [[ "$ACTION" == "stop" ]]; then
  weather_jrs_tmux "$SOCKET" kill-session -t "=$SESSION"
  echo "stopped session=$SESSION"
  exit 0
fi
if [[ "$ACTION" != "start" ]]; then
  echo "usage: $0 [start|stop|status]" >&2
  exit 2
fi

weather_jrs_tmux_write_probe "$SOCKET" "$RUNTIME_ROOT"
weather_jrs_tmux_mkdir "$SOCKET" "$OUTPUT" "$OUTPUT/snapshots" "$RUNTIME_ROOT/loop"

cmd=(
  "$PY" -u "$SOURCE_DIR/scripts/ops/weather_knmi_first_seen_ladder_shadow_v1.py"
  --knmi-observations "$SOURCE"
  --paper-snapshots "$PAPER"
  --orderbook-snapshots "$ORDERBOOKS"
  --output-dir "$OUTPUT"
  --state "$OUTPUT/state.json"
  --market-proxy "$MARKET_PROXY"
  --max-workers 16
  --top-n 50
  --max-new-event-age-seconds 90
  --bootstrap-at-end
  --loop
  --interval-seconds 1
)

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "missing clean KNMI first-seen checkout: $SOURCE_DIR" >&2
  exit 1
fi
if [[ ! -x "$PY" ]]; then
  echo "missing KNMI first-seen python: $PY" >&2
  exit 1
fi

weather_jrs_tmux "$SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$SOCKET" new-session -d -s "$SESSION" \
  "cd '$SOURCE_DIR' && export PYTHONPATH='$SOURCE_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$OUTPUT/forward.log' 2>&1"

sleep 1
weather_jrs_tmux "$SOCKET" has-session -t "=$SESSION"
echo "started KNMI first-seen full-ladder zero-notional collector: socket=$SOCKET session=$SESSION"
echo "source_dir=$SOURCE_DIR"
echo "output=$OUTPUT"
