#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${EUROPE_D1_DISTANCE2_LINEAGE_SESSION:-europe_d1_distance2_lineage_adapter_v1}"
OUTPUT_DIR="$RUNTIME_ROOT/output/europe_d1_distance2_lineage_adapter_v1"
SOURCE_BASKETS="$RUNTIME_ROOT/output/europe_d1_distance2_dual_no_shadow_v1/baskets.jsonl"
LOG_FILE="$OUTPUT_DIR/runner.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec '$PROJECT_DIR/.venv/bin/python' -u \
  '$PROJECT_DIR/scripts/ops/weather_europe_d1_distance2_lineage_adapter_v1.py' \
  --loop --interval-sec 60 \
  --source-repo-sha '0ed49db4602ab7410207070dc0e03da6a11d6c57' \
  --source-baskets '$SOURCE_BASKETS' \
  --config '$PROJECT_DIR/configs/weather/europe_d1_distance2_dual_no_shadow_v1.json' \
  --output-dir '$OUTPUT_DIR' >> '$LOG_FILE' 2>&1"

weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION"
echo "started $TMUX_SESSION"
echo "mode=zero_notional_adapter"
echo "source_release=0ed49db4602ab7410207070dc0e03da6a11d6c57"
echo "output=$OUTPUT_DIR"
