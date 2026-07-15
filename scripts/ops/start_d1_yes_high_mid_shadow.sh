#!/usr/bin/env bash
# Start the zero-notional d1_yes_high_mid shadow runner in a dedicated tmux
# session (mirrors the JRS-volume tmux pattern used for the data feed; avoids
# the macOS LaunchAgent "Operation not permitted" issue on external volumes).
#
# Idempotent: re-running reuses the existing session if alive.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SESSION="d1_yes_high_mid_shadow_v1"
PY="$PROJECT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
INTERVAL_SECONDS="${D1_YES_HIGH_MID_INTERVAL_SECONDS:-600}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "already running: tmux session $SESSION"
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && '$PY' -u scripts/ops/d1_yes_high_mid_shadow_v1.py loop \
     --interval-seconds $INTERVAL_SECONDS \
     2>&1 | tee -a '$PROJECT_DIR/runtime/weather_edge_v1/d1_yes_high_mid_shadow_v1/shadow_loop.log'"

echo "started tmux session $SESSION interval=${INTERVAL_SECONDS}s"
