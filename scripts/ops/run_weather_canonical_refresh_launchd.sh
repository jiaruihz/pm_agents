#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
LOCK_DIR="$RUNTIME_DIR/refresh.lock"

mkdir -p "$RUNTIME_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "canonical refresh already running; skipping"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR"
DB_PATH="$PROJECT_DIR/runtime/weather.db"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi
MARKET_PROXY="${WEATHER_DATA_FEED_MARKET_PROXY:-http://127.0.0.1:7897}"
export HTTP_PROXY="$MARKET_PROXY" HTTPS_PROXY="$MARKET_PROXY" ALL_PROXY="$MARKET_PROXY"
export http_proxy="$MARKET_PROXY" https_proxy="$MARKET_PROXY" all_proxy="$MARKET_PROXY"

# Keep this post-trade path deliberately small: production-declared live
# instances only, then order -> fill -> fact -> gate.  Registration is the
# ownership boundary, so future execution modules do not require another
# hard-coded path here.
# The full dashboard refresh also rebuilds every signal candidate and can take
# many minutes, which is unnecessary for closing live execution lineage.
"$PROJECT_DIR/.venv/bin/python" -c \
  "from weather_dashboard.db.apply_schema_canonical import init_db_canonical; init_db_canonical('$DB_PATH')"
"$PROJECT_DIR/.venv/bin/python" -m weather_dashboard.cli.ingest_strategy_runtime_orders \
  --db-path "$DB_PATH" \
  --active-live-only \
  --project-root "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" -m weather_dashboard.cli.check_strategy_runtime_order_coverage \
  --db-path "$DB_PATH" \
  --active-live-only \
  --project-root "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" scripts/ops/reconcile_weather_order_execution_aliases.py \
  --db-path "$DB_PATH" \
  --apply \
  --json-out "$PROJECT_DIR/runtime/_dashboard_logs/order_execution_aliases.json"
clob_synced=0
for attempt in 1 2 3; do
  if "$PROJECT_DIR/.venv/bin/python" -m weather_dashboard.ingest.clob_fill_sync \
    --db-path "$DB_PATH" \
    --require-authenticated \
    --lookback-hours 72; then
    clob_synced=1
    break
  fi
  echo "clob fill sync attempt $attempt failed" >&2
  if [[ "$attempt" -lt 3 ]]; then
    sleep 10
  fi
done
if [[ "$clob_synced" != "1" ]]; then
  echo "clob fill sync failed after 3 attempts" >&2
  exit 1
fi
"$PROJECT_DIR/.venv/bin/python" scripts/etl/build_weather_fact_trades.py \
  --db-path "$DB_PATH" --no-parquet
"$PROJECT_DIR/.venv/bin/python" scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py \
  --db "$DB_PATH" \
  --json-out "$PROJECT_DIR/runtime/_dashboard_logs/clob_fill_coverage_gate.json"
"$PROJECT_DIR/.venv/bin/python" scripts/analysis/execution_quality/weather_execution_module_compare.py \
  --db "$DB_PATH" \
  --active-live-only \
  --json-out "$PROJECT_DIR/runtime/weather_edge_v1/execution_module_compare/latest.json" \
  >/dev/null
