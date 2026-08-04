#!/usr/bin/env bash
# scripts/weather_dashboard/run_stack.sh
#
# Canonical weather DB rebuild/status compatibility entrypoint.
#
# Usage:
#   scripts/weather_dashboard/run_stack.sh                 # controller/status only; no process mutation
#   scripts/weather_dashboard/run_stack.sh --rebuild       # explicitly rebuild facts; starts no service
#   scripts/weather_dashboard/run_stack.sh --no-rebuild    # compatibility alias for --status
#   scripts/weather_dashboard/run_stack.sh --recreate-db   # explicitly delete and recreate weather.db
#   scripts/weather_dashboard/run_stack.sh --status        # just show current DB / process status
#
# API/process recovery belongs exclusively to weather_production_ctl.py.
# Frontend production lifecycle belongs to com.pm-agents.weather-fe.

set -euo pipefail

# ---- Resolve paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv/bin"
DB_PATH=""
FE_DIR="$REPO_ROOT/frontend/strategy_dashboard"
API_PORT="${WEATHER_API_PORT:-8000}"
FE_PORT="${WEATHER_FE_PORT:-5174}"
LOG_DIR="$REPO_ROOT/runtime/_dashboard_logs"

REBUILD=0
RECREATE_DB=0
STATUS_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --rebuild)    REBUILD=1 ;;
    --no-rebuild) STATUS_ONLY=1; REBUILD=0 ;;
    --recreate-db) RECREATE_DB=1; REBUILD=1 ;;
    --api-only|--fe-only)
      echo "$arg is retired: API is controller-managed and FE is LaunchAgent-managed" >&2
      exit 2 ;;
    --status)     STATUS_ONLY=1; REBUILD=0 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# Recreating the DB is always a rebuild, regardless of argument order.
if [[ $RECREATE_DB -eq 1 ]]; then
  REBUILD=1
fi

log() { printf '\033[1;36m[run_stack]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[run_stack]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[run_stack]\033[0m %s\n' "$*" >&2; }

pid_listening_on_port() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null \
      | awk -v port=":$port" '$4 ~ port {print $0}' \
      | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' \
      | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u
  fi
}

port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -q ":$port "
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
  else
    return 1
  fi
}

file_size() {
  local path="$1"
  [[ -f "$path" ]] || {
    echo "absent"
    return
  }
  stat -c '%s bytes' "$path" 2>/dev/null || stat -f '%z bytes' "$path"
}

# ---- Preflight ----
if [[ ! -x "$VENV/python" ]]; then
  err "venv not found at $VENV — create with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Mutations always target the physical canonical declared by production.yaml.
# Deleting the compatibility symlink and then initializing through its old path
# would create a split repo-local DB.
DB_PATH="$($VENV/python -c 'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().canonical_db_path)')"

init_canonical_db() {
  "$VENV/python" -c "from weather_dashboard.db.apply_schema_canonical import init_db_canonical; init_db_canonical('$DB_PATH')"
}

rebuild_canonical_db() {
  rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
  init_canonical_db
}

refresh_canonical_db() {
  init_canonical_db
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

show_status() {
  log "Repo:        $REPO_ROOT"
  log "DB path:     $DB_PATH ($(file_size "$DB_PATH"))"
  if [[ -f "$DB_PATH" ]]; then
    "$VENV/python" -c "
import sqlite3
try:
    c = sqlite3.connect('file:$DB_PATH?mode=ro', uri=True, timeout=1.0)
    c.execute('PRAGMA query_only=ON')
    c.execute('PRAGMA busy_timeout=1000')
    c.execute('SELECT 1').fetchone()
    print('  [DB] readable (read-only probe)')
    c.close()
except Exception as e:
    print(f'  [DB] error: {e}')
" 2>&1 || true
  fi
  log "API port:    $API_PORT  $(port_listening "$API_PORT" && echo '(listening)' || echo '(idle)')"
  log "FE port:     $FE_PORT  $(port_listening "$FE_PORT" && echo '(listening)' || echo '(idle)')"
}

if [[ $STATUS_ONLY -eq 1 ]]; then
  show_status
  "$VENV/python" scripts/ops/weather_production_ctl.py health
  exit 0
fi

if [[ $REBUILD -ne 1 ]]; then
  show_status
  "$VENV/python" scripts/ops/weather_production_ctl.py health
  exit 0
fi

mkdir -p "$LOG_DIR"
"$VENV/python" scripts/ops/weather_production_manifest.py --strict >/dev/null

# ---- 1. Refresh DB (idempotent — ingest is content-addressable) ----
# Default is non-destructive.  Use --recreate-db only when a clean local
# dashboard DB is intentionally needed.
if [[ $REBUILD -eq 1 ]]; then
  if [[ $RECREATE_DB -eq 1 ]]; then
    log "Recreating DB at $DB_PATH (--recreate-db)"
    rebuild_canonical_db >/dev/null
  else
    log "Refreshing DB at $DB_PATH (non-destructive; use --recreate-db to delete first)"
    refresh_canonical_db >/dev/null
  fi

  SNAP_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv"
  PAPER_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv"

  if [[ -f "$SNAP_CSV" || -f "$PAPER_CSV" ]]; then
    log "  Migrating legacy research CSVs into canonical DB"
    {
      init_canonical_db
      "$VENV/python" -m weather_dashboard.cli.ingest_legacy_research --db-path "$DB_PATH"
    } >"$LOG_DIR/migrate_legacy_research.log" 2>&1 || {
      err "legacy research migration failed — see $LOG_DIR/migrate_legacy_research.log"
      exit 1
    }
  else
    warn "  research CSVs missing (run scripts/ops/sync_weather_remote.sh first)"
  fi

  if [[ -d "$REPO_ROOT/runtime/weather_edge_v1/live_cycle" || -d "$REPO_ROOT/runtime/weather_edge_v1/remote_pm_agent/live_cycle" ]]; then
    log "  Migrating live-cycle lineage into canonical DB"
    {
      init_canonical_db
      "$VENV/python" -m weather_dashboard.cli.ingest_live_cycle --db-path "$DB_PATH"
    } >"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
      err "live-cycle migration failed — see $LOG_DIR/migrate_live_cycle.log"
      exit 1
    }
  else
    warn "  live_cycle directories missing"
  fi

  log "  Migrating strategy-local runtime orders into canonical DB"
  {
    init_canonical_db
    "$VENV/python" -m weather_dashboard.cli.ingest_strategy_runtime_orders \
      --db-path "$DB_PATH" --active-live-only --project-root "$REPO_ROOT"
    "$VENV/python" -m weather_dashboard.cli.check_strategy_runtime_order_coverage \
      --db-path "$DB_PATH" --active-live-only --project-root "$REPO_ROOT"
  } >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "strategy runtime order migration failed — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  log "  Ingesting pm_history -> settlements (authoritative)"
  "$VENV/python" -m weather_dashboard.ingest.pm_history_settlements \
    --db-path "$DB_PATH" >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "pm_history_settlements failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }

  log "  Backfilling missing CLOB settlements from Polymarket API"
  "$VENV/python" -m weather_dashboard.ingest.backfill_missing_settlements \
    --db-path "$DB_PATH" >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "backfill_missing_settlements failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }

  log "  Syncing real CLOB fills from Polymarket activity API"
  "$VENV/python" -m weather_dashboard.ingest.clob_fill_sync \
    --db-path "$DB_PATH" >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "clob-fill-sync failed; live_real fills are incomplete — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  log "  Consolidating fragmented strategy_config rows -> config_aliases"
  "$VENV/python" -m weather_dashboard.db.consolidate_configs \
    --db-path "$DB_PATH" >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "consolidate_configs failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }

  log "  Syncing strategy definitions, config ownership, and order-instance lineage"
  "$VENV/python" scripts/ops/weather_strategy_launcher.py \
    --db-path "$DB_PATH" sync >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "strategy management sync failed — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  # ---- 1b. Build fact_trades BEFORE metrics (metrics reads from fact_trades) ----
  log "  Building fact_trades (唯一派生层)..."
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
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "fact_trades build failed — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  # ---- 1c. Build fact_signal_candidates (机会粒度，对齐 universe→paper→live) ----
  FIRST_SEEN_RAW_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
  FIRST_SEEN_SOURCE_EVENTS="${WEATHER_FIRST_SEEN_SOURCE_EVENTS_PATH:-$FIRST_SEEN_RAW_ROOT/output/source_events/sources.jsonl}"
  FIRST_SEEN_FORECAST_CURVES="${WEATHER_FIRST_SEEN_FORECAST_CURVES_PATH:-$FIRST_SEEN_RAW_ROOT/targeted_output/forecast_hourly_curves}"
  FIRST_SEEN_FORECAST_ENRICHMENT="${WEATHER_FIRST_SEEN_FORECAST_ENRICHMENT_PATH:-$FIRST_SEEN_RAW_ROOT/output/forecast_enrichment/forecast_enrichment.jsonl}"
  FIRST_SEEN_PAPER_SNAPSHOTS="${WEATHER_FIRST_SEEN_PAPER_SNAPSHOTS_PATH:-$FIRST_SEEN_RAW_ROOT/targeted_output/paper_snapshots}"
  FIRST_SEEN_FEATURE_STORE="${WEATHER_FIRST_SEEN_FEATURE_STORE_PATH:-$REPO_ROOT/runtime/weather_edge_v1/feature_store}"
  log "  Materializing first-seen information events (data/signal lineage only)..."
  "$VENV/python" scripts/etl/materialize_weather_information_events.py \
    --db "$DB_PATH" \
    --source-events "$FIRST_SEEN_SOURCE_EVENTS" \
    --forecast-curves "$FIRST_SEEN_FORECAST_CURVES" \
    --forecast-enrichment "$FIRST_SEEN_FORECAST_ENRICHMENT" \
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "first-seen information event materialization failed"
    exit 1
  }

  log "  Building fact_signal_candidates (机会粒度候选表)..."
  "$VENV/python" scripts/etl/build_weather_signal_candidates.py \
    --db-path "$DB_PATH" \
    "${SIGNAL_PARQUET_ARGS[@]}" \
    --decision-hts-min 22 --decision-hts-max 24 \
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "fact_signal_candidates build failed — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  log "  Building first-seen PIT checkpoints and candidate v2 denominator..."
  "$VENV/python" scripts/etl/materialize_weather_first_seen_pipeline.py \
    --db "$DB_PATH" \
    --paper-snapshots "$FIRST_SEEN_PAPER_SNAPSHOTS" \
    --feature-store "$FIRST_SEEN_FEATURE_STORE" \
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "first-seen checkpoint/candidate build failed — see $LOG_DIR/migrate_live_cycle.log"
    exit 1
  }

  log "  Checking CLOB fill coverage gate..."
  "$VENV/python" scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py \
    --db "$DB_PATH" \
    --json-out "$LOG_DIR/clob_fill_coverage_gate.json" \
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    err "CLOB fill coverage gate failed — see $LOG_DIR/clob_fill_coverage_gate.json"
    exit 1
  }

  log "  Re-computing metrics after fill+settlement+fact_trades rebuild"
  refresh_metrics >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || true

  log "  Refreshing strategy runtime registry"
  "$VENV/python" scripts/ops/refresh_weather_strategy_runtime_registry.py \
    --db-path "$DB_PATH" \
    --json-out "$LOG_DIR/strategy_runtime_registry_refresh.json" \
    >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "strategy runtime registry refresh failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }
fi

show_status
log "Rebuild completed. No API, frontend, collector, or strategy process was started or stopped."
