#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
DATA_PROJECT_DIR="${DATA_PROJECT_DIR:-$PROJECT_DIR}"
RUN_DIR="${RUN_DIR:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
PID_FILE="$RUN_DIR/micro_loop.pid"
OUT_FILE="$RUN_DIR/micro_loop.out"

mkdir -p "$RUN_DIR"

if [[ -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid"
    exit 0
  fi
fi

cd "$PROJECT_DIR"
nohup bash scripts/ops/all_yes_underround_micro_paper_loop_v0.sh >"$OUT_FILE" 2>&1 < /dev/null &
pid="$!"
echo "$pid" > "$PID_FILE"
echo "started all-YES underround micro paper loop pid=$pid log=$OUT_FILE"
