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
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
STAGE_REPORT_DIR="$RUNTIME_DIR/stages"
MAX_WAL_BYTES="${WEATHER_CANONICAL_REFRESH_MAX_WAL_BYTES:-536870912}"
MAX_WAL_GROWTH_BYTES="${WEATHER_CANONICAL_REFRESH_MAX_WAL_GROWTH_BYTES:-134217728}"

# Immutable releases deliberately do not carry mutable credentials.  Resolve
# the production-declared operational root before authenticated fill sync and
# load its local environment without copying secrets into the release.
BUNDLED_PRODUCTION_SPEC="$PROJECT_DIR/src/strategies/runtime/production.yaml"
OPERATIONAL_PROJECT_DIR="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from pathlib import Path; import sys; from src.strategies.runtime.production import load_production_spec; print(load_production_spec(Path(sys.argv[1])).operational_repo_root)' \
    "$BUNDLED_PRODUCTION_SPEC"
)"
if [[ -f "$OPERATIONAL_PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$OPERATIONAL_PROJECT_DIR/.env"
  set +a
fi
PRODUCTION_SPEC_SOURCE="$OPERATIONAL_PROJECT_DIR/src/strategies/runtime/production.yaml"
if [[ ! -f "$PRODUCTION_SPEC_SOURCE" ]]; then
  echo "operational production spec missing: $PRODUCTION_SPEC_SOURCE" >&2
  exit 1
fi
# The immutable checkout owns code, while the operational repository owns the
# mutable production topology and current release pins.  Freeze one stable
# topology for the whole bounded run so nested loaders cannot mix revisions.
PRODUCTION_SPEC_PATH="$RUNTIME_DIR/production_spec_snapshot.yaml"
PRODUCTION_SPEC_REPORT="$RUNTIME_DIR/production_spec_snapshot.json"
"$PYTHON_BIN" scripts/ops/weather_production_spec_snapshot.py capture \
  --source "$PRODUCTION_SPEC_SOURCE" \
  --snapshot "$PRODUCTION_SPEC_PATH" \
  --report "$PRODUCTION_SPEC_REPORT" >/dev/null
PRODUCTION_SPEC_SHA256="$(
  "$PYTHON_BIN" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sha256"])' \
    "$PRODUCTION_SPEC_REPORT"
)"
export WEATHER_PRODUCTION_CONFIG="$PRODUCTION_SPEC_PATH"

assert_production_spec_unchanged() {
  "$PYTHON_BIN" scripts/ops/weather_production_spec_snapshot.py verify \
    --source "$PRODUCTION_SPEC_SOURCE" \
    --expected-sha256 "$PRODUCTION_SPEC_SHA256" >/dev/null
}

DASHBOARD_LOG_DIR="$OPERATIONAL_PROJECT_DIR/runtime/_dashboard_logs"
mkdir -p "$DASHBOARD_LOG_DIR"
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
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
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
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).physical_path("wcir_decision_bundles"))'
)"
WCIR_CURSOR_STATE_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).cursor_state_path("wcir_decision_bundles"))'
)"
D1_DISTANCE2_BUNDLES_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).physical_path("europe_d1_distance2_decision_bundles"))'
)"
D1_DISTANCE2_CURSOR_STATE_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).cursor_state_path("europe_d1_distance2_decision_bundles"))'
)"
D1_DISTANCE2_INTENTS_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).physical_path("europe_d1_distance2_trade_intents"))'
)"
D1_DISTANCE2_INTENT_CURSOR_STATE_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.data_products import production_data_product_registry; from src.strategies.runtime.production import load_production_spec; spec = load_production_spec(); print(production_data_product_registry(spec).cursor_state_path("europe_d1_distance2_trade_intents"))'
)"
DATA_FEED_RUNTIME_ROOT="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().data_feed_runtime_root)'
)"
PM_RUNTIME_ROOT="$(
  PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().pm_runtime_root)'
)"
SIGNAL_CLOCK_ADJUSTMENT_JOURNAL="$PM_RUNTIME_ROOT/weather_edge_v1/signal_clock_adjustments.jsonl"
EXECUTION_PUBLIC_BOOKS_ROOT="$DATA_FEED_RUNTIME_ROOT/market_books/ws_incremental/execution_evidence_v1/public_books"
D1_PMH_DIR="$OPERATIONAL_PROJECT_DIR/runtime/weather_edge_v1/market_data/cache/pm_history"
D1_SETTLEMENT_START_DATE="$(date -u -v-14d +%F)"
D1_SETTLEMENT_END_DATE="$(date -u -v-1d +%F)"

run_refresh_stage() {
  local stage="$1"
  local max_seconds="$2"
  local stage_rc=0
  shift 2
  assert_production_spec_unchanged
  "$PYTHON_BIN" scripts/ops/weather_canonical_refresh_stage.py \
    --stage "$stage" \
    --db "$DB_PATH" \
    --report "$STAGE_REPORT_DIR/$stage.json" \
    --max-seconds "$max_seconds" \
    --max-wal-bytes "$MAX_WAL_BYTES" \
    --max-wal-growth-bytes "$MAX_WAL_GROWTH_BYTES" \
    -- "$@" || stage_rc="$?"
  assert_production_spec_unchanged
  return "$stage_rc"
}

preflight_rc=0
assert_production_spec_unchanged
"$PYTHON_BIN" scripts/ops/weather_canonical_refresh_preflight.py \
  --db "$DB_PATH" \
  --report "$RUNTIME_DIR/preflight.json" \
  --manifest-report "$RUNTIME_DIR/production_manifest.json" \
  --feed-health-report "$RUNTIME_DIR/data_feed_health.json" \
  --production-spec "$PRODUCTION_SPEC_PATH" || preflight_rc="$?"
assert_production_spec_unchanged
case "$preflight_rc" in
  0) migration_allowed=1 ;;
  10)
    migration_allowed=0
    echo "canonical migration stages blocked by preflight; live accounting continues" >&2
    ;;
  20)
    echo "canonical refresh all writes blocked by preflight" >&2
    exit 20
    ;;
  *)
    echo "canonical refresh preflight crashed: rc=$preflight_rc" >&2
    exit "$preflight_rc"
    ;;
esac

if [[ "$migration_allowed" == "1" ]]; then
  run_refresh_stage schema 120 \
    "$PYTHON_BIN" -c \
    "from weather_dashboard.db.apply_schema_canonical import init_db_canonical; init_db_canonical('$DB_PATH')"
fi

run_refresh_stage ingest_runtime_orders 180 \
  "$PYTHON_BIN" -m weather_dashboard.cli.ingest_strategy_runtime_orders \
  --db-path "$DB_PATH" \
  --require-existing-schema \
  --active-live-only \
  --report-dir "$RUNTIME_DIR/order_migration_reports" \
  --project-root "$PROJECT_DIR"
run_refresh_stage order_coverage_before_fills 120 \
  "$PYTHON_BIN" -m weather_dashboard.cli.check_strategy_runtime_order_coverage \
  --db-path "$DB_PATH" \
  --active-live-only \
  --project-root "$PROJECT_DIR"
run_refresh_stage reconcile_execution_aliases 180 \
  "$PYTHON_BIN" scripts/ops/reconcile_weather_order_execution_aliases.py \
  --db-path "$DB_PATH" \
  --apply \
  --json-out "$DASHBOARD_LOG_DIR/order_execution_aliases.json"
clob_synced=0
for attempt in 1 2 3; do
  if run_refresh_stage "clob_fill_sync_attempt_$attempt" 240 \
    "$PYTHON_BIN" -m weather_dashboard.ingest.clob_fill_sync \
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
run_refresh_stage reconcile_signal_clocks 180 \
  "$PYTHON_BIN" scripts/ops/reconcile_weather_signal_clocks.py \
  --db-path "$DB_PATH" \
  --expected-db "$CANONICAL_DB_PATH" \
  --journal "$SIGNAL_CLOCK_ADJUSTMENT_JOURNAL" \
  --report "$RUNTIME_DIR/signal_clock_reconciliation.json" \
  --apply
if [[ -d "$EXECUTION_PUBLIC_BOOKS_ROOT" ]]; then
  run_refresh_stage materialize_execution_evidence 300 \
    "$PYTHON_BIN" scripts/ops/materialize_weather_execution_evidence.py \
    --db-path "$DB_PATH" \
    --public-books-root "$EXECUTION_PUBLIC_BOOKS_ROOT" \
    --fill-date "$(date -u +%F)" \
    --fill-date "$(date -u -v-1d +%F)" \
    --max-unlinked-fills 500 \
    --max-book-age-sec 120 \
    --report "$RUNTIME_DIR/execution_evidence_materialization.json" \
    --apply
fi
run_refresh_stage build_fact_trades 300 \
  "$PYTHON_BIN" scripts/etl/build_weather_fact_trades.py \
  --db-path "$DB_PATH" --incremental --no-parquet
run_refresh_stage fill_coverage_gate 180 \
  "$PYTHON_BIN" scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py \
  --db "$DB_PATH" \
  --json-out "$DASHBOARD_LOG_DIR/clob_fill_coverage_gate.json"

# Candidate replay is append-only and zero-notional.  Keep it after the live
# fill/fact/gate chain so a concurrently appended shadow journal can fail and
# retry without delaying post-trade accounting.
if [[ "$migration_allowed" == "1" ]]; then
  # Keep the D-1 settlement join bounded and identity-exact. Current Gamma
  # cache rows carry condition_id/market_id; legacy rows may still use the
  # signal fallback inside the ingester. Missing files remain an explicit
  # evidence gap and never trigger a city/date inferred label.
  run_refresh_stage europe_d1_distance2_settlement_ingest 180 \
    "$PYTHON_BIN" -m weather_dashboard.ingest.pm_history_settlements \
    --db-path "$DB_PATH" \
    --pmh-dir "$D1_PMH_DIR" \
    --start-date "$D1_SETTLEMENT_START_DATE" \
    --end-date "$D1_SETTLEMENT_END_DATE" \
    --city Amsterdam --city Ankara --city Helsinki --city Istanbul \
    --city London --city Madrid --city Milan --city Munich --city Paris \
    --city Warsaw
  if [[ ! -s "$WCIR_BUNDLES_PATH" ]]; then
    echo "WCIR decision bundle journal missing or empty: $WCIR_BUNDLES_PATH" >&2
    exit 1
  fi
  run_refresh_stage wcir_candidate_materialization 300 \
    "$PYTHON_BIN" scripts/ops/materialize_weather_city_runtime_canonical_v1.py \
    --bundles "$WCIR_BUNDLES_PATH" \
    --db "$DB_PATH" \
    --expected-db "$CANONICAL_DB_PATH" \
    --state "$WCIR_CURSOR_STATE_PATH" \
    --max-new-rows 5000 \
    --max-new-bytes 67108864 \
    --max-settlement-updates 5000 \
    --apply \
    --report "$RUNTIME_DIR/wcir_candidate_materialization.json"
  if [[ ! -s "$D1_DISTANCE2_BUNDLES_PATH" ]]; then
    echo "Europe D-1 distance-2 decision bundle journal missing or empty: $D1_DISTANCE2_BUNDLES_PATH" >&2
    exit 1
  fi
  if [[ ! -s "$D1_DISTANCE2_INTENTS_PATH" ]]; then
    echo "Europe D-1 distance-2 TradeIntent journal missing or empty: $D1_DISTANCE2_INTENTS_PATH" >&2
    exit 1
  fi
  run_refresh_stage europe_d1_distance2_candidate_materialization 300 \
    "$PYTHON_BIN" scripts/ops/materialize_weather_city_runtime_canonical_v1.py \
    --bundles "$D1_DISTANCE2_BUNDLES_PATH" \
    --db "$DB_PATH" \
    --expected-db "$CANONICAL_DB_PATH" \
    --state "$D1_DISTANCE2_CURSOR_STATE_PATH" \
    --max-new-rows 5000 \
    --max-new-bytes 67108864 \
    --trade-intents "$D1_DISTANCE2_INTENTS_PATH" \
    --trade-intent-state "$D1_DISTANCE2_INTENT_CURSOR_STATE_PATH" \
    --max-new-intents 5000 \
    --max-new-intent-bytes 67108864 \
    --max-settlement-updates 5000 \
    --apply \
    --report "$RUNTIME_DIR/europe_d1_distance2_candidate_materialization.json"
  run_refresh_stage replay_artifact_verification 300 \
    "$PYTHON_BIN" scripts/ops/materialize_weather_replay_artifact_verifications.py \
    --db "$DB_PATH" \
    --expected-db "$CANONICAL_DB_PATH" \
    --max-cases 5000 \
    --commit-every 500 \
    --apply \
    --report "$RUNTIME_DIR/replay_artifact_verification.json"
fi
assert_production_spec_unchanged
