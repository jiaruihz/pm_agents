#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="${WEATHER_CANONICAL_REFRESH_RUNTIME_DIR:-$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh}"
LOCK_DIR="$RUNTIME_DIR/refresh.lock"
source "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh"

mkdir -p "$RUNTIME_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "canonical refresh already running; skipping"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR"

# Immutable releases deliberately do not carry mutable credentials.  Resolve
# the production-declared operational root before authenticated fill sync and
# load its local environment without copying secrets into the release.
OPERATIONAL_PROJECT_DIR="$(
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().operational_repo_root)'
)"
if [[ -f "$OPERATIONAL_PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$OPERATIONAL_PROJECT_DIR/.env"
  set +a
fi
# Resolve the same mutable control-plane state as every market consumer.  A
# proxy switch must also move fill reconciliation; keeping a local default here
# would create a second route that the controller cannot change or audit.
PROXY_CONTROL_ROOT="$OPERATIONAL_PROJECT_DIR"
MARKET_PROXY="$(weather_resolve_market_proxy "$PROXY_CONTROL_ROOT")"
weather_export_market_proxy_env "$MARKET_PROXY"

# Keep this bounded path additive: install schema/view migrations, append the
# production-declared WCIR shadow journal, then close live execution lineage as
# order -> fill -> fact -> gate.  It must not run the full dashboard rebuild.
CANONICAL_DB_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().canonical_db_path)'
)"
# A release checkout is immutable code, not a data root.  Using
# "$PROJECT_DIR/runtime/weather.db" here silently created a split database in
# every new control-plane release before the later identity check could run.
# Bind every writer to the production-declared physical canonical path from
# the first schema operation onward and fail closed if that file is missing.
DB_PATH="$CANONICAL_DB_PATH"
"$PROJECT_DIR/.venv/bin/python" - "$DB_PATH" <<'PY'
from pathlib import Path
import sys

db_path = Path(sys.argv[1])
if not db_path.is_absolute():
    raise SystemExit(f"canonical DB path must be absolute: {db_path}")
if not db_path.is_file():
    raise SystemExit(f"canonical DB file missing: {db_path}")
PY
WCIR_BUNDLES_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().data_feed_output_root() / "city_probability_runtime_v3" / "decision_bundles.jsonl")'
)"
DATA_FEED_RUNTIME_ROOT="$(
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().data_feed_runtime_root)'
)"
PM_RUNTIME_ROOT="$(
  PYTHONPATH="$PROJECT_DIR" "$PROJECT_DIR/.venv/bin/python" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().pm_runtime_root)'
)"
SIGNAL_CLOCK_ADJUSTMENT_JOURNAL="$PM_RUNTIME_ROOT/weather_edge_v1/signal_clock_adjustments.jsonl"
EXECUTION_PUBLIC_BOOKS_ROOT="$DATA_FEED_RUNTIME_ROOT/market_books/ws_incremental/execution_evidence_v1/public_books"
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
"$PROJECT_DIR/.venv/bin/python" scripts/ops/reconcile_weather_signal_clocks.py \
  --db-path "$DB_PATH" \
  --expected-db "$CANONICAL_DB_PATH" \
  --journal "$SIGNAL_CLOCK_ADJUSTMENT_JOURNAL" \
  --report "$RUNTIME_DIR/signal_clock_reconciliation.json" \
  --apply
if [[ -d "$EXECUTION_PUBLIC_BOOKS_ROOT" ]]; then
  "$PROJECT_DIR/.venv/bin/python" scripts/ops/materialize_weather_execution_evidence.py \
    --db-path "$DB_PATH" \
    --public-books-root "$EXECUTION_PUBLIC_BOOKS_ROOT" \
    --fill-date "$(date -u +%F)" \
    --fill-date "$(date -u -v-1d +%F)" \
    --max-unlinked-fills 500 \
    --max-book-age-sec 120 \
    --report "$RUNTIME_DIR/execution_evidence_materialization.json" \
    --apply
fi
"$PROJECT_DIR/.venv/bin/python" scripts/etl/build_weather_fact_trades.py \
  --db-path "$DB_PATH" --incremental --no-parquet
"$PROJECT_DIR/.venv/bin/python" scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py \
  --db "$DB_PATH" \
  --json-out "$PROJECT_DIR/runtime/_dashboard_logs/clob_fill_coverage_gate.json"

# Candidate replay is append-only and zero-notional.  Keep it after the live
# fill/fact/gate chain so a concurrently appended shadow journal can fail and
# retry without delaying post-trade accounting.
if [[ ! -s "$WCIR_BUNDLES_PATH" ]]; then
  echo "WCIR decision bundle journal missing or empty: $WCIR_BUNDLES_PATH" >&2
  exit 1
fi
"$PROJECT_DIR/.venv/bin/python" scripts/ops/materialize_weather_city_runtime_canonical_v1.py \
  --bundles "$WCIR_BUNDLES_PATH" \
  --db "$DB_PATH" \
  --expected-db "$CANONICAL_DB_PATH" \
  --state "$RUNTIME_DIR/wcir_candidate_materialization_state.json" \
  --max-new-rows 5000 \
  --max-new-bytes 67108864 \
  --max-settlement-updates 5000 \
  --apply \
  --report "$RUNTIME_DIR/wcir_candidate_materialization.json"
