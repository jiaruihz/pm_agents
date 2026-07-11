#!/usr/bin/env bash
# scripts/ops/weather_dashboard_refresh.sh
#
# Full weather dashboard data refresh pipeline:
#   1. sync — pull latest data from n100 pm_agent runtime
#   2. ingest — scan live_cycle JSONL into canonical weather.db
#   3. clob-fill-sync — pull real CLOB fills from Polymarket activity API
#   4. fact-build — rebuild fact_trades / fact_signal_candidates from canonical DB
#   5. clob-fill-coverage-gate — fail closed on mismatched/over-cap live fills
#   6. metrics-refresh — recompute all run metrics and persist to DB cache
#
# Usage:
#   scripts/ops/weather_dashboard_refresh.sh                  # full refresh
#   scripts/ops/weather_dashboard_refresh.sh --no-sync        # skip rsync (use cached data)
#   scripts/ops/weather_dashboard_refresh.sh --no-clob        # skip CLOB fill sync
#   scripts/ops/weather_dashboard_refresh.sh --sync-only      # just sync, don't ingest
#   scripts/ops/weather_dashboard_refresh.sh --dry-run        # sync dry-run + report

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv/bin"
DB_PATH="$REPO_ROOT/runtime/weather.db"
LOG_DIR="$REPO_ROOT/runtime/_dashboard_logs"
mkdir -p "$LOG_DIR"

SYNC=1
INGEST=1
CLOB=1
METRICS=1
DRY=0

for arg in "$@"; do
  case "$arg" in
    --no-sync)   SYNC=0 ;;
    --no-clob)   CLOB=0 ;;
    --sync-only) INGEST=0; CLOB=0; METRICS=0 ;;
    --dry-run)   DRY=1 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m[refresh]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[refresh]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[refresh]\033[0m %s\n' "$*" >&2; }

TS="$(date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
log "Weather dashboard refresh — $TS"
log "DB: $DB_PATH"
log ""
log "数据源契约（不要绕过）："
log "  N100 没有活跃 SQLite。生产数据 = N100 文件系统（output/ + runtime/weather_edge_v1/）"
log "  $DB_PATH = 本机从 N100 镜像独立重建的 DB。**不从 N100 拷贝 SQLite。**"
log "  详见 docs/WEATHER_DATA_CANONICAL_SOURCES.md"
log ""

init_canonical_db() {
  "$VENV/python" -c "from weather_dashboard.db.apply_schema_canonical import init_db_canonical; init_db_canonical('$DB_PATH')"
}

refresh_metrics() {
  "$VENV/python" -c "
from weather_dashboard.db.connection import get_conn
from weather_dashboard.metrics.save import save_all_metrics
conn = get_conn('$DB_PATH')
results = save_all_metrics(conn)
conn.close()
print(f'Updated metrics for {len(results)} run(s)')
"
}

has_parquet_engine() {
  "$VENV/python" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pyarrow') or importlib.util.find_spec('fastparquet') else 1)"
}

# ---- Step 1: Sync from n100 ----
if [[ "$SYNC" == "1" ]]; then
  log ""
  log "Step 1/5: Syncing data from n100"
  SYNC_FLAGS=(--live-only)
  [[ "$DRY" == "1" ]] && SYNC_FLAGS+=(--dry-run)
  "$SCRIPT_DIR/sync_weather_remote.sh" "${SYNC_FLAGS[@]}" 2>&1 | tee "$LOG_DIR/sync.log"
else
  log "Step 1/5: Skipping sync (--no-sync)"
fi

if [[ "$INGEST" == "0" ]]; then
  log "Done (sync-only mode)."
  exit 0
fi

# ---- Step 2: Ingest live_cycle JSONL ----
log ""
log "Step 2/5: Ingesting live_cycle and strategy-runtime JSONL into $DB_PATH"
{
  init_canonical_db
  "$VENV/python" -m weather_dashboard.cli.ingest_live_cycle --db-path "$DB_PATH"
  STRATEGY_RUNTIME_ARGS=(--db-path "$DB_PATH")
  STRATEGY_ORDER_FILES=()
  for order_file in \
    "$REPO_ROOT/runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl" \
    "$REPO_ROOT/runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders.jsonl" \
    "${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}/output/fast_source_prev_no_trial/orders.jsonl" \
    "${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}/output/hko_official_tminus1_no_live/orders.jsonl"
  do
    if [[ -f "$order_file" ]]; then
      STRATEGY_RUNTIME_ARGS+=(--order-file "$order_file")
      STRATEGY_ORDER_FILES+=("$order_file")
    fi
  done
  "$VENV/python" -m weather_dashboard.cli.ingest_strategy_runtime_orders "${STRATEGY_RUNTIME_ARGS[@]}"
  if (( ${#STRATEGY_ORDER_FILES[@]} > 0 )); then
    COVERAGE_ARGS=(--db-path "$DB_PATH")
    for order_file in "${STRATEGY_ORDER_FILES[@]}"; do
      COVERAGE_ARGS+=(--order-file "$order_file")
    done
    "$VENV/python" -m weather_dashboard.cli.check_strategy_runtime_order_coverage "${COVERAGE_ARGS[@]}"
  fi
} 2>&1 | tee "$LOG_DIR/ingest.log" || {
  err "Ingest failed — check $LOG_DIR/ingest.log"
  exit 1
}

# ---- Step 3: CLOB fill sync ----
if [[ "$CLOB" == "1" ]]; then
  log ""
  log "Step 3/5: Syncing real CLOB fills from Polymarket activity API"
  "$VENV/python" -m weather_dashboard.ingest.clob_fill_sync \
    --db-path "$DB_PATH" 2>&1 | tee "$LOG_DIR/clob_fill_sync.log" || {
    err "CLOB fill sync failed; live_real fills are incomplete — check $LOG_DIR/clob_fill_sync.log"
    exit 1
  }
else
  log "Step 3/5: Skipping CLOB fill sync (--no-clob)"
fi

# ---- Step 4: Fact table rebuild ----
log ""
log "Step 4/6: Rebuilding fact_trades and fact_signal_candidates"
FACT_PARQUET_ARGS=()
SIGNAL_PARQUET_ARGS=()
if has_parquet_engine; then
  FACT_PARQUET_ARGS=(--parquet-path "$REPO_ROOT/runtime/weather_edge_v1/market_data/research/fact_trades.parquet")
  SIGNAL_PARQUET_ARGS=(--parquet-path "$REPO_ROOT/runtime/weather_edge_v1/market_data/research/fact_signal_candidates.parquet")
else
  warn "No pyarrow/fastparquet in $VENV; skipping parquet export and writing SQLite fact tables only"
  FACT_PARQUET_ARGS=(--no-parquet)
  SIGNAL_PARQUET_ARGS=(--no-parquet)
fi
"$VENV/python" scripts/etl/build_weather_fact_trades.py \
  --db-path "$DB_PATH" \
  "${FACT_PARQUET_ARGS[@]}" \
  2>&1 | tee "$LOG_DIR/fact_trades.log" || {
  err "fact_trades build failed — check $LOG_DIR/fact_trades.log"
  exit 1
}
"$VENV/python" scripts/etl/build_weather_signal_candidates.py \
  --db-path "$DB_PATH" \
  "${SIGNAL_PARQUET_ARGS[@]}" \
  --decision-hts-min 22 --decision-hts-max 24 \
  2>&1 | tee "$LOG_DIR/fact_signal_candidates.log" || {
  err "fact_signal_candidates build failed — check $LOG_DIR/fact_signal_candidates.log"
  exit 1
}

# ---- Step 5: CLOB fill coverage gate ----
log ""
log "Step 5/6: Checking CLOB fill coverage gate"
"$VENV/python" scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py \
  --db "$DB_PATH" \
  --json-out "$LOG_DIR/clob_fill_coverage_gate.json" \
  2>&1 | tee "$LOG_DIR/clob_fill_coverage_gate.log" || {
  err "CLOB fill coverage gate failed — check $LOG_DIR/clob_fill_coverage_gate.json"
  exit 1
}

# ---- Step 6: Metrics refresh ----
log ""
log "Step 6/6: Recomputing metrics cache for all runs"
refresh_metrics 2>&1 | tee "$LOG_DIR/metrics_refresh.log" || {
  warn "metrics-refresh failed (non-fatal) — check $LOG_DIR/metrics_refresh.log"
}

# ---- Summary ----
log ""
log "Refresh complete at $(date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
if [[ -f "$DB_PATH" ]]; then
  DB_SIZE="$(stat -c '%s' "$DB_PATH" 2>/dev/null || echo '?')"
  log "DB size: $DB_SIZE bytes"
  "$VENV/python" -c "
from weather_dashboard.db.connection import get_conn
c = get_conn('$DB_PATH')
runs = c.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
fills = c.execute(\"SELECT COUNT(*) FROM fills WHERE status IN ('filled','simulated')\").fetchone()[0]
orders = c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
print(f'  runs={runs}  orders={orders}  fills={fills}')
try:
    rows = c.execute('SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class ORDER BY trade_class').fetchall()
    print('  fact_trades=' + ', '.join(f'{r[0]}:{r[1]}' for r in rows))
except Exception as exc:
    print(f'  fact_trades unavailable: {exc}')
" 2>/dev/null || true
fi
