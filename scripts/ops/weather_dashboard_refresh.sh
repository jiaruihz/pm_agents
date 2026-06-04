#!/usr/bin/env bash
# scripts/ops/weather_dashboard_refresh.sh
#
# Full weather dashboard data refresh pipeline:
#   1. sync — pull latest data from n100 pm_agent runtime
#   2. ingest — scan live_cycle JSONL into canonical weather.db
#   3. clob-fill-sync — pull real CLOB fills from Polymarket activity API
#   4. metrics-refresh — recompute all run metrics and persist to DB cache
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
  log "Step 1/4: Syncing data from n100"
  SYNC_FLAGS=(--live-only)
  [[ "$DRY" == "1" ]] && SYNC_FLAGS+=(--dry-run)
  "$SCRIPT_DIR/sync_weather_remote.sh" "${SYNC_FLAGS[@]}" 2>&1 | tee "$LOG_DIR/sync.log"
else
  log "Step 1/4: Skipping sync (--no-sync)"
fi

if [[ "$INGEST" == "0" ]]; then
  log "Done (sync-only mode)."
  exit 0
fi

# ---- Step 2: Ingest live_cycle JSONL ----
log ""
log "Step 2/4: Ingesting live_cycle JSONL into $DB_PATH"
make -f Makefile.weather migrate-live-cycle 2>&1 | tee "$LOG_DIR/ingest.log" || {
  err "Ingest failed — check $LOG_DIR/ingest.log"
  exit 1
}

# ---- Step 3: CLOB fill sync ----
if [[ "$CLOB" == "1" ]]; then
  log ""
  log "Step 3/4: Syncing real CLOB fills from Polymarket activity API"
  "$VENV/python" -m weather_dashboard.ingest.clob_fill_sync \
    --db-path "$DB_PATH" 2>&1 | tee "$LOG_DIR/clob_fill_sync.log" || {
    err "CLOB fill sync failed; live_real fills are incomplete — check $LOG_DIR/clob_fill_sync.log"
    exit 1
  }
else
  log "Step 3/4: Skipping CLOB fill sync (--no-clob)"
fi

# ---- Step 4: Metrics refresh ----
log ""
log "Step 4/4: Recomputing metrics cache for all runs"
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
" 2>/dev/null || true
fi
