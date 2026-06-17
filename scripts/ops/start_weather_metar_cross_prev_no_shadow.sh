#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/metar_cross_prev_no_shadow"
PID_FILE="$LOG_DIR/loop.pid"
OUT_FILE="$LOG_DIR/loop.out"
PY="$PROJECT_DIR/.venv/bin/python"
TMUX_SESSION="${METAR_CROSS_TMUX_SESSION:-weather_metar_cross_prev_no_shadow}"

mkdir -p "$LOG_DIR"
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$OUT_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

METAR_CROSS_CITIES="${METAR_CROSS_CITIES:-Shanghai Tokyo}"
METAR_CROSS_INTERVAL_SEC="${METAR_CROSS_INTERVAL_SEC:-20}"
METAR_CROSS_MAX_ASK="${METAR_CROSS_MAX_ASK:-0.995}"
METAR_CROSS_INCLUDE_STATION_DIFF="${METAR_CROSS_INCLUDE_STATION_DIFF:-0}"

args=(
  "$PROJECT_DIR/scripts/ops/weather_metar_cross_prev_no_shadow.py"
  loop
  --interval-sec "$METAR_CROSS_INTERVAL_SEC"
  --max-ask "$METAR_CROSS_MAX_ASK"
  --cities $METAR_CROSS_CITIES
)
if [[ "$METAR_CROSS_INCLUDE_STATION_DIFF" == "1" ]]; then
  args+=(--include-station-diff)
fi

if command -v tmux >/dev/null 2>&1 && [[ "${METAR_CROSS_START_MODE:-tmux}" == "tmux" ]]; then
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already running tmux=$TMUX_SESSION log=$OUT_FILE"
    exit 0
  fi
  printf -v quoted_args '%q ' "${args[@]}"
  tmux new-session -d -s "$TMUX_SESSION" "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q' "$PY") -u $quoted_args >>$(printf '%q' "$OUT_FILE") 2>&1"
  echo "tmux:$TMUX_SESSION" > "$PID_FILE"
  echo "started metar-cross prev-NO shadow tmux=$TMUX_SESSION log=$OUT_FILE cities=$METAR_CROSS_CITIES"
  exit 0
fi

nohup "$PY" -u "${args[@]}" >"$OUT_FILE" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PID_FILE"
echo "started metar-cross prev-NO shadow pid=$pid log=$OUT_FILE cities=$METAR_CROSS_CITIES"
