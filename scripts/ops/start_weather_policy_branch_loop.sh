#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
EXECUTION_POLICY="${WEATHER_BRANCH_EXECUTION_POLICY:-maker_queue_v1}"
SOURCE_POLICY="${WEATHER_BRANCH_SOURCE_POLICY:-mid_price_core_v1}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
PID_FILE="$LOG_DIR/policy_branch_${EXECUTION_POLICY}.pid"
OUT_FILE="$LOG_DIR/policy_branch_${EXECUTION_POLICY}.out"
mkdir -p "$LOG_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running policy=$EXECUTION_POLICY pid=$old_pid"
    exit 0
  fi
fi

cd "$PROJECT_DIR"
WEATHER_BRANCH_EXECUTION_POLICY="$EXECUTION_POLICY" \
WEATHER_BRANCH_SOURCE_POLICY="$SOURCE_POLICY" \
nohup scripts/ops/weather_policy_branch_loop.sh >"$OUT_FILE" 2>&1 &
pid="$!"
echo "$pid" >"$PID_FILE"
echo "started policy=$EXECUTION_POLICY source=$SOURCE_POLICY pid=$pid"
