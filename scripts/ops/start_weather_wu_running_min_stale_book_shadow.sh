#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "${WEATHER_WU_LOW_STALE_BOOK_TMUX_SOCKET:-}")"
TMUX_SESSION="${WEATHER_WU_LOW_STALE_BOOK_TMUX_SESSION:-weather_wu_running_min_stale_book_shadow}"
TARGET_DATE="${WEATHER_WU_LOW_STALE_BOOK_TARGET_DATE:-}"
if [[ -n "$TARGET_DATE" ]]; then
  MARKET_DATE="$TARGET_DATE"
else
  MARKET_DATE="$("$PROJECT_DIR/.venv/bin/python" - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
print(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
PY
)"
fi
CITIES="${WEATHER_WU_LOW_STALE_BOOK_CITIES:-Seoul Tokyo}"
SOURCES="${WEATHER_WU_LOW_STALE_BOOK_SOURCES:-amos_runway jma_amedas}"
if [[ -n "${WEATHER_WU_LOW_STALE_BOOK_EVENT_SLUGS:-}" ]]; then
  MARKET_SLUGS="$WEATHER_WU_LOW_STALE_BOOK_EVENT_SLUGS"
else
  MARKET_SLUGS="$("$PROJECT_DIR/.venv/bin/python" - "$MARKET_DATE" "$CITIES" <<'PY'
import sys
from datetime import date

d = date.fromisoformat(sys.argv[1])
city_values = sys.argv[2].split()
city_slug = {
    "Seoul": "seoul",
    "Tokyo": "tokyo",
}
date_part = f"{d.strftime('%B').lower()}-{d.day}-{d.year}"
print(" ".join(f"{city}=lowest-temperature-in-{city_slug.get(city, city.lower())}-on-{date_part}" for city in city_values))
PY
)"
fi
INTERVAL_SECONDS="${WEATHER_WU_LOW_STALE_BOOK_INTERVAL_SECONDS:-30}"
FOLLOW_MINUTES="${WEATHER_WU_LOW_STALE_BOOK_FOLLOW_MINUTES:-1440}"
OUTPUT_DIR="${WEATHER_WU_LOW_STALE_BOOK_OUTPUT_DIR:-$RUNTIME_ROOT/output/wu_running_min_stale_book_shadow}"
MARKET_PROXY="${WEATHER_WU_LOW_STALE_BOOK_MARKET_PROXY:-${WEATHER_DATA_FEED_MARKET_PROXY:-${WEATHER_PREDICT_MARKET_PROXY:-http://127.0.0.1:7890}}}"
STALE_NO_ASK_MAX="${WEATHER_WU_LOW_STALE_BOOK_PREVIOUS_NO_ASK_MAX:-0.92}"
LOCK_YES_ASK_MAX="${WEATHER_WU_LOW_STALE_BOOK_LOCK_YES_ASK_MAX:-0.93}"
LOCK_NEXT_NO_ASK_MAX="${WEATHER_WU_LOW_STALE_BOOK_LOCK_NEXT_NO_ASK_MAX:-0.93}"
LOG_FILE="$RUNTIME_ROOT/loop/wu_running_min_stale_book_shadow.log"

mkdir -p "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_fast_source_stale_book_observer.py"
  --loop
  --output-dir "$OUTPUT_DIR"
  --signal-basis official-running-extreme
  --extreme-kind min
  --fresh-scope all
  --gamma-market-index
  --interval-seconds "$INTERVAL_SECONDS"
  --follow-minutes "$FOLLOW_MINUTES"
  --stale-no-ask-max "$STALE_NO_ASK_MAX"
  --lock-yes-ask-max "$LOCK_YES_ASK_MAX"
  --lock-next-no-ask-max "$LOCK_NEXT_NO_ASK_MAX"
)
cmd+=(--cities)
read -r -a city_args <<< "$CITIES"
cmd+=("${city_args[@]}")
cmd+=(--sources)
read -r -a source_args <<< "$SOURCES"
cmd+=("${source_args[@]}")
cmd+=(--floor-cities)
read -r -a slug_args <<< "$MARKET_SLUGS"
for slug_arg in "${slug_args[@]}"; do
  cmd+=(--gamma-event-slug "$slug_arg")
done
if [[ -n "$TARGET_DATE" ]]; then
  cmd+=(--target-date "$TARGET_DATE")
fi
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi

tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$PROJECT_DIR' && exec $(printf '%q ' "${cmd[@]}") >> '$LOG_FILE' 2>&1"

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "target_date=${TARGET_DATE:-auto_today}"
echo "cities=$CITIES"
echo "sources=$SOURCES"
echo "market_slugs=$MARKET_SLUGS"
echo "interval_seconds=$INTERVAL_SECONDS"
echo "follow_minutes=$FOLLOW_MINUTES"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "previous_no_ask_max=$STALE_NO_ASK_MAX"
echo "lock_yes_ask_max=$LOCK_YES_ASK_MAX"
echo "lock_next_no_ask_max=$LOCK_NEXT_NO_ASK_MAX"
echo "market_proxy=$([[ -n "$MARKET_PROXY" ]] && echo configured || echo direct)"
