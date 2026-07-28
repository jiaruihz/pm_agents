#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${D1_MULTISOURCE_CONSENSUS_TMUX_SESSION:-d1_multisource_consensus_shadow_v1}"
OUTPUT_DIR="$RUNTIME_ROOT/output/d1_multisource_consensus_shadow_v1"
LOG_FILE="$OUTPUT_DIR/runner.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec '$PROJECT_DIR/.venv/bin/python' -u \
  '$PROJECT_DIR/scripts/ops/d1_multisource_consensus_shadow_v1.py' \
  --loop --dedup --interval-sec 60 \
  --policy '$PROJECT_DIR/configs/weather/d1_multisource_consensus_shadow_v1.json' \
  --output-dir '$OUTPUT_DIR' >> '$LOG_FILE' 2>&1"

weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION"
echo "started $TMUX_SESSION"
echo "mode=zero_notional_shadow"
echo "output=$OUTPUT_DIR"
echo "log=$LOG_FILE"
