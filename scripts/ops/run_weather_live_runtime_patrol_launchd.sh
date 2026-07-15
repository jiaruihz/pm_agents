#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOCAL_RUNTIME="${WEATHER_LIVE_PATROL_LOCAL_RUNTIME:-$PROJECT_DIR/runtime/weather_edge_v1/live_runtime_patrol}"

cd "$PROJECT_DIR"
set -a
[[ ! -f .env ]] || source .env
set +a
mkdir -p "$LOCAL_RUNTIME"

exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_live_runtime_patrol.py" \
  --loop \
  --interval-sec 60 \
  --lookback-min 5 \
  --stop-runner-on-submit-failure \
  --telegram \
  --output "$LOCAL_RUNTIME/latest.json" \
  --notification-state "$LOCAL_RUNTIME/notification_state.json"
