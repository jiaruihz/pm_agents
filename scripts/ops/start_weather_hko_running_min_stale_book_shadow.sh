#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="${WEATHER_HKO_LOW_STALE_BOOK_TMUX_SESSION:-weather_hko_running_min_stale_book_shadow}"
TARGET_DATE="${WEATHER_HKO_LOW_STALE_BOOK_TARGET_DATE:-}"
MARKET_DATE="${TARGET_DATE:-$("$PROJECT_DIR/.venv/bin/python" - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
print(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
PY
)}"
MARKET_SLUG="${WEATHER_HKO_LOW_STALE_BOOK_EVENT_SLUG:-$("$PROJECT_DIR/.venv/bin/python" - "$MARKET_DATE" <<'PY'
import sys
from datetime import date
d = date.fromisoformat(sys.argv[1])
print(f"lowest-temperature-in-hong-kong-on-{d.strftime('%B').lower()}-{d.day}-{d.year}")
PY
)}"
INTERVAL_SECONDS="${WEATHER_HKO_LOW_STALE_BOOK_INTERVAL_SECONDS:-30}"
FOLLOW_MINUTES="${WEATHER_HKO_LOW_STALE_BOOK_FOLLOW_MINUTES:-1440}"
OUTPUT_DIR="${WEATHER_HKO_LOW_STALE_BOOK_OUTPUT_DIR:-$RUNTIME_ROOT/output/hko_running_min_stale_book_shadow}"
MARKET_PROXY="${WEATHER_HKO_LOW_STALE_BOOK_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
STALE_NO_ASK_MAX="${WEATHER_HKO_LOW_STALE_BOOK_PREVIOUS_NO_ASK_MAX:-0.92}"
LOCK_YES_ASK_MAX="${WEATHER_HKO_LOW_STALE_BOOK_LOCK_YES_ASK_MAX:-0.93}"
LOCK_NEXT_NO_ASK_MAX="${WEATHER_HKO_LOW_STALE_BOOK_LOCK_NEXT_NO_ASK_MAX:-0.93}"
LOG_FILE="$RUNTIME_ROOT/loop/hko_running_min_stale_book_shadow.log"

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --signal-basis official-running-extreme
  --extreme-kind min
  --cities HongKong
  --sources hko_obs
  --floor-cities HongKong
  --fresh-scope all
  --gamma-market-index
  --gamma-event-slug "HongKong=$MARKET_SLUG"
  --interval-seconds "$INTERVAL_SECONDS"
  --follow-minutes "$FOLLOW_MINUTES"
  --stale-no-ask-max "$STALE_NO_ASK_MAX"
  --lock-yes-ask-max "$LOCK_YES_ASK_MAX"
  --lock-next-no-ask-max "$LOCK_NEXT_NO_ASK_MAX"
)
if [[ -n "$TARGET_DATE" ]]; then
  cmd+=(--target-date "$TARGET_DATE")
fi
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "market_slug=$MARKET_SLUG"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "follow_minutes=$FOLLOW_MINUTES"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "previous_no_ask_max=$STALE_NO_ASK_MAX"
echo "lock_yes_ask_max=$LOCK_YES_ASK_MAX"
echo "lock_next_no_ask_max=$LOCK_NEXT_NO_ASK_MAX"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
