#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PY="$PROJECT_DIR/.venv/bin/python"
SYNC=1
START_DATE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-sync) SYNC=0; shift ;;
    --start-date) START_DATE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -x "$PY" ]] || PY="python3"
if [[ -z "$START_DATE" ]]; then
  START_DATE="$($PY -c 'from datetime import date,timedelta; print(date.today()-timedelta(days=1))')"
fi

cd "$PROJECT_DIR"
if [[ "$SYNC" == "1" ]]; then
  scripts/ops/sync_weather_remote.sh --market-source=mac-weather-data-feed --market-only
fi

"$PY" scripts/ops/backfill_weather_pm_history.py \
  --start-date "$START_DATE" --end-date "$START_DATE"
"$PY" -m weather_dashboard.ingest.pm_history_settlements \
  --db-path runtime/weather.db --start-date "$START_DATE" --end-date "$START_DATE"
"$PY" scripts/etl/build_weather_signal_candidates.py \
  --db-path runtime/weather.db --incremental-start-date "$START_DATE" \
  --snapshot-lookback-days 2 --no-parquet
"$PY" scripts/ops/weather_analysis_freshness_monitor.py
