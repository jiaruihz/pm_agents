#!/usr/bin/env bash
# Dedicated full-ladder (all cities x all brackets) orderbook snapshot loop.
#
# Runs ONLY the `snapshot-full` step of the data-feed service, as its own session
# on the canonical permission-bearing JRS tmux server, writing to a dedicated output-root
# on the JRS runtime volume.  It deliberately does NOT touch the running
# data-feed loop, so the high-frequency observations the live fast-source / hko
# heads depend on are unaffected.
#
# Consumed by scripts/ops/d1_yes_high_mid_shadow_v1.py via the full_ladder_output
# orderbook dir (preferred over the narrow targeted feed when fresh).
#
# Idempotent: re-running reuses the existing tmux session if alive.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$PROJECT_DIR}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
OUTPUT_ROOT="${WEATHER_FULL_LADDER_OUTPUT_ROOT:-$RUNTIME_ROOT/full_ladder_output}"
CACHE_ROOT="${WEATHER_FULL_LADDER_CACHE_ROOT:-$RUNTIME_ROOT/cache}"
INTERVAL_SEC="${WEATHER_FULL_LADDER_INTERVAL_SEC:-1200}"
ORDERBOOK_BUDGET_SEC="${WEATHER_FULL_LADDER_ORDERBOOK_BUDGET_SEC:-900}"
ORDERBOOK_WORKERS="${WEATHER_FULL_LADDER_ORDERBOOK_WORKERS:-2}"
FAILOVER_LOG="${WEATHER_MARKET_PROXY_FAILOVER_LOG:-$RUNTIME_ROOT/loop/market_proxy_failover.jsonl}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_FULL_LADDER_TMUX_SESSION:-weather_full_ladder_capture}"
LOG_FILE="$OUTPUT_ROOT/full_ladder_capture.log"

if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "already running: tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  exit 0
fi

weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "export WEATHER_DATA_FEED_SERVICE_DIR='$SERVICE_DIR' \
     WEATHER_FULL_LADDER_OUTPUT_ROOT='$OUTPUT_ROOT' \
     WEATHER_FULL_LADDER_CACHE_ROOT='$CACHE_ROOT' \
     WEATHER_FULL_LADDER_INTERVAL_SEC='$INTERVAL_SEC' \
     WEATHER_FULL_LADDER_ORDERBOOK_BUDGET_SEC='$ORDERBOOK_BUDGET_SEC' \
     WEATHER_FULL_LADDER_ORDERBOOK_WORKERS='$ORDERBOOK_WORKERS' \
     WEATHER_MARKET_PROXY_FAILOVER_LOG='$FAILOVER_LOG' \
     WEATHER_FULL_LADDER_LOG_FILE='$LOG_FILE' \
   && exec bash '$PROJECT_DIR/scripts/ops/_full_ladder_capture_loop_body.sh'"

sleep 1
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "started tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
  echo "  output_root=$OUTPUT_ROOT interval=${INTERVAL_SEC}s budget=${ORDERBOOK_BUDGET_SEC}s workers=$ORDERBOOK_WORKERS"
  echo "  log=$LOG_FILE"
else
  echo "failed to start $TMUX_SESSION; inspect $LOG_FILE" >&2
  exit 1
fi
