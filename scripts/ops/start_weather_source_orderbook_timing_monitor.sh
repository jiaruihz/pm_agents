#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/source_orderbook_timing"
PID_FILE="$LOG_DIR/loop.pid"
OUT_FILE="$LOG_DIR/loop.out"
PY="$PROJECT_DIR/.venv/bin/python"
TMUX_SESSION="${TIMING_MONITOR_TMUX_SESSION:-weather_source_orderbook_timing_monitor}"

mkdir -p "$LOG_DIR"
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ "$old_pid" == tmux:* ]] && command -v tmux >/dev/null 2>&1 && tmux has-session -t "${old_pid#tmux:}" 2>/dev/null; then
    echo "already running tmux=${old_pid#tmux:} log=$OUT_FILE"
    exit 0
  fi
  if [[ -n "$old_pid" ]] && [[ "$old_pid" != tmux:* ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already running pid=$old_pid log=$OUT_FILE"
    exit 0
  fi
fi

if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

TIMING_MONITOR_CITIES="${TIMING_MONITOR_CITIES:-Shanghai Tokyo}"
TIMING_MONITOR_SOURCES="${TIMING_MONITOR_SOURCES:-source_profiles checkwx_html}"
TIMING_MONITOR_BASE_INTERVAL_SEC="${TIMING_MONITOR_BASE_INTERVAL_SEC:-60}"
TIMING_MONITOR_BURST_INTERVAL_SEC="${TIMING_MONITOR_BURST_INTERVAL_SEC:-3}"
TIMING_MONITOR_BURST_WINDOW_MIN="${TIMING_MONITOR_BURST_WINDOW_MIN:-8}"
TIMING_MONITOR_BRACKET_RADIUS="${TIMING_MONITOR_BRACKET_RADIUS:-2}"
TIMING_MONITOR_INCLUDE_STATION_DIFF="${TIMING_MONITOR_INCLUDE_STATION_DIFF:-0}"

args=(
  "$PROJECT_DIR/scripts/ops/weather_source_orderbook_timing_monitor.py"
  loop
  --base-interval-sec "$TIMING_MONITOR_BASE_INTERVAL_SEC"
  --burst-interval-sec "$TIMING_MONITOR_BURST_INTERVAL_SEC"
  --burst-window-min "$TIMING_MONITOR_BURST_WINDOW_MIN"
  --bracket-radius "$TIMING_MONITOR_BRACKET_RADIUS"
  --cities $TIMING_MONITOR_CITIES
  --sources $TIMING_MONITOR_SOURCES
)
if [[ "$TIMING_MONITOR_INCLUDE_STATION_DIFF" == "1" ]]; then
  args+=(--include-station-diff)
fi

if command -v tmux >/dev/null 2>&1 && [[ "${TIMING_MONITOR_START_MODE:-tmux}" == "tmux" ]]; then
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "already running tmux=$TMUX_SESSION log=$OUT_FILE"
    exit 0
  fi
  printf -v quoted_args '%q ' "${args[@]}"
  tmux new-session -d -s "$TMUX_SESSION" "cd $(printf '%q' "$PROJECT_DIR") && exec $(printf '%q' "$PY") -u $quoted_args >>$(printf '%q' "$OUT_FILE") 2>&1"
  echo "tmux:$TMUX_SESSION" > "$PID_FILE"
  echo "started timing monitor tmux=$TMUX_SESSION log=$OUT_FILE cities=$TIMING_MONITOR_CITIES sources=$TIMING_MONITOR_SOURCES"
  exit 0
fi

nohup "$PY" -u "${args[@]}" >"$OUT_FILE" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PID_FILE"
echo "started timing monitor pid=$pid log=$OUT_FILE cities=$TIMING_MONITOR_CITIES sources=$TIMING_MONITOR_SOURCES"
