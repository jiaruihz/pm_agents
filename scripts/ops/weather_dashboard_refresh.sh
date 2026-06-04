#!/usr/bin/env bash
# scripts/ops/weather_dashboard_refresh.sh
#
# Full weather dashboard data refresh pipeline:
#   1. sync — pull latest data from n100 pm_agent runtime
#   2. ingest — scan live_cycle JSONL into canonical weather.db
#   3. clob-fill-sync — pull real CLOB fills from Polymarket activity API
#   4. fact-build — rebuild fact_trades / fact_signal_candidates from canonical DB
#   5. metrics-refresh — recompute all run metrics and persist to DB cache
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

TS="$(date -Is)"
log "Weather dashboard refresh — $TS"
log "DB: $DB_PATH"

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
log "Step 2/5: Ingesting live_cycle JSONL into $DB_PATH"
make -f Makefile.weather migrate-live-cycle 2>&1 | tee "$LOG_DIR/ingest.log" || {
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
log "Step 4/5: Rebuilding fact_trades and fact_signal_candidates"
"$VENV/python" scripts/analysis/build_weather_fact_trades.py \
  --db-path "$DB_PATH" \
  --parquet-path "$REPO_ROOT/runtime/weather_edge_v1/market_data/research/fact_trades.parquet" \
  2>&1 | tee "$LOG_DIR/fact_trades.log" || {
  err "fact_trades build failed — check $LOG_DIR/fact_trades.log"
  exit 1
}
"$VENV/python" scripts/analysis/build_weather_signal_candidates.py \
  --db-path "$DB_PATH" \
  --parquet-path "$REPO_ROOT/runtime/weather_edge_v1/market_data/research/fact_signal_candidates.parquet" \
  --decision-hts-min 22 --decision-hts-max 24 \
  2>&1 | tee "$LOG_DIR/fact_signal_candidates.log" || {
  err "fact_signal_candidates build failed — check $LOG_DIR/fact_signal_candidates.log"
  exit 1
}

# ---- Step 5: Metrics refresh ----
log ""
log "Step 5/5: Recomputing metrics cache for all runs"
make -f Makefile.weather metrics-refresh 2>&1 | tee "$LOG_DIR/metrics_refresh.log" || {
  warn "metrics-refresh failed (non-fatal) — check $LOG_DIR/metrics_refresh.log"
}

# ---- Summary ----
log ""
log "Refresh complete at $(date -Is)"
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
