#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${LOW_PRICE_YES_FACT_REFRESH_TMUX_SESSION:-low_price_yes_fact_refresh}"
cd "$ROOT"

RUNTIME_DIR="${LOW_PRICE_YES_FACT_REFRESH_RUNTIME_DIR:-runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/fact_refresh.pid"
LOG_FILE="$RUNTIME_DIR/fact_refresh.log"
LOCK_DIR="$RUNTIME_DIR/fact_refresh.lock"
mkdir -p "$RUNTIME_DIR" runtime/weather_edge_v1/market_data/paper_snapshots runtime/weather_edge_v1/market_data/cache

if [[ "${LOW_PRICE_YES_FACT_REFRESH_CHILD:-0}" != "1" && -s "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if [[ "$old_pid" == tmux:* ]] && weather_jrs_tmux "$TMUX_SOCKET" has-session -t "${old_pid#tmux:}" 2>/dev/null; then
    echo "already_running pid=$old_pid log=$LOG_FILE"
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

SNAPSHOT_SRC="${LOW_PRICE_YES_FACT_REFRESH_SNAPSHOT_SRC:-$HOME/projects/weather_data_feed_service_runtime/targeted_output/paper_snapshots}"
CACHE_SRC="${LOW_PRICE_YES_FACT_REFRESH_CACHE_SRC:-$HOME/projects/weather_data_feed_service_runtime/cache}"
SNAPSHOT_DST="${LOW_PRICE_YES_FACT_REFRESH_SNAPSHOT_DST:-runtime/weather_edge_v1/market_data/paper_snapshots}"
CACHE_DST="${LOW_PRICE_YES_FACT_REFRESH_CACHE_DST:-runtime/weather_edge_v1/market_data/cache}"
DB_PATH="${LOW_PRICE_YES_FACT_REFRESH_DB_PATH:-runtime/weather.db}"
INTERVAL_SECONDS="${LOW_PRICE_YES_FACT_REFRESH_INTERVAL_SECONDS:-600}"

if [[ "${LOW_PRICE_YES_FACT_REFRESH_CHILD:-0}" != "1" ]]; then
  launch_cmd=(env \
    LOW_PRICE_YES_FACT_REFRESH_CHILD=1 \
    PYTHON_BIN="$PYTHON_BIN" \
    LOW_PRICE_YES_FACT_REFRESH_SNAPSHOT_SRC="$SNAPSHOT_SRC" \
    LOW_PRICE_YES_FACT_REFRESH_CACHE_SRC="$CACHE_SRC" \
    LOW_PRICE_YES_FACT_REFRESH_SNAPSHOT_DST="$SNAPSHOT_DST" \
    LOW_PRICE_YES_FACT_REFRESH_CACHE_DST="$CACHE_DST" \
    LOW_PRICE_YES_FACT_REFRESH_DB_PATH="$DB_PATH" \
    LOW_PRICE_YES_FACT_REFRESH_INTERVAL_SECONDS="$INTERVAL_SECONDS" \
    "$0")
  printf -v quoted_launch_cmd '%q ' "${launch_cmd[@]}"
  weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
  weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
    "cd $(printf '%q' "$ROOT") && exec $quoted_launch_cmd >> $(printf '%q' "$LOG_FILE") 2>&1"
  echo "tmux:$TMUX_SESSION" >"$PID_FILE"
  echo "started low-price YES fact refresh socket=$TMUX_SOCKET session=$TMUX_SESSION log=$LOG_FILE interval=${INTERVAL_SECONDS}s"
  exit 0
fi

echo "$$" >"$PID_FILE"
trap 'rm -f "$PID_FILE"; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
date -u +"[low_price_yes_fact_refresh] loop_start_utc=%Y-%m-%dT%H:%M:%SZ pid=$$ interval=${INTERVAL_SECONDS}s"

while true; do
  date -u +"[low_price_yes_fact_refresh] cycle_start_utc=%Y-%m-%dT%H:%M:%SZ"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    {
      if [[ ! -d "$SNAPSHOT_SRC" ]]; then
        echo "[low_price_yes_fact_refresh] missing_snapshot_src=$SNAPSHOT_SRC"
      else
        rsync -a "$SNAPSHOT_SRC/" "$SNAPSHOT_DST/"
      fi
      if [[ -d "$CACHE_SRC" ]]; then
        rsync -a "$CACHE_SRC/" "$CACHE_DST/"
      fi
      "$PYTHON_BIN" scripts/etl/build_weather_signal_candidates.py \
        --db-path "$DB_PATH" \
        --decision-hts-min 22 \
        --decision-hts-max 24 \
        --no-parquet
    }
    rmdir "$LOCK_DIR" 2>/dev/null || true
  else
    echo "[low_price_yes_fact_refresh] skipped lock_held=$LOCK_DIR"
  fi
  sleep "$INTERVAL_SECONDS"
done
