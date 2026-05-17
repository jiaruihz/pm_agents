#!/usr/bin/env bash
# scripts/weather_dashboard/run_stack.sh
#
# One-shot bring-up for the weather strategy dashboard stack.
#
# Usage:
#   scripts/weather_dashboard/run_stack.sh                 # full bring-up (rebuild DB + API + FE)
#   scripts/weather_dashboard/run_stack.sh --no-rebuild    # skip DB rebuild (just start API + FE)
#   scripts/weather_dashboard/run_stack.sh --api-only      # start only API
#   scripts/weather_dashboard/run_stack.sh --fe-only       # start only FE
#   scripts/weather_dashboard/run_stack.sh --status        # just show current DB / process status
#
# Idempotent. Designed for any agent (Claude, Codex, MiniMax) or human operator
# to spin up the stack without prior context.

set -euo pipefail

# ---- Resolve paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv/bin"
DB_PATH="$REPO_ROOT/runtime/weather.db"
FE_DIR="$REPO_ROOT/frontend/strategy_dashboard"
API_PORT="${WEATHER_API_PORT:-8000}"
FE_PORT="${WEATHER_FE_PORT:-5173}"
LOG_DIR="$REPO_ROOT/runtime/_dashboard_logs"
mkdir -p "$LOG_DIR"

REBUILD=1
START_API=1
START_FE=1
STATUS_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --no-rebuild) REBUILD=0 ;;
    --api-only)   START_FE=0 ;;
    --fe-only)    START_API=0; REBUILD=0 ;;
    --status)     STATUS_ONLY=1; REBUILD=0; START_API=0; START_FE=0 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;36m[run_stack]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[run_stack]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[run_stack]\033[0m %s\n' "$*" >&2; }

# ---- Preflight ----
if [[ ! -x "$VENV/python" ]]; then
  err "venv not found at $VENV — create with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

show_status() {
  log "Repo:        $REPO_ROOT"
  log "DB path:     $DB_PATH ($([[ -f $DB_PATH ]] && stat -c '%s bytes' "$DB_PATH" || echo 'absent'))"
  if [[ -f "$DB_PATH" ]]; then
    "$VENV/python" -c "
from weather_dashboard.db.connection import get_conn
try:
    c = get_conn('$DB_PATH')
    rows = c.execute('SELECT run_id, state, execution_mode, (SELECT COUNT(*) FROM fills f JOIN orders o ON f.order_id=o.order_id WHERE o.run_id=runs.run_id AND f.status=\"filled\") AS fills FROM runs').fetchall()
    if not rows:
        print('  [DB] schema present but no runs')
    for r in rows:
        print(f'  [DB] {r[\"run_id\"][:12]}  {r[\"state\"]:8s} {r[\"execution_mode\"]:18s} fills={r[\"fills\"]}')
except Exception as e:
    print(f'  [DB] error: {e}')
" 2>&1 || true
  fi
  log "API port:    $API_PORT  $(ss -tln 2>/dev/null | grep -q ":$API_PORT " && echo '(listening)' || echo '(idle)')"
  log "FE port:     $FE_PORT  $(ss -tln 2>/dev/null | grep -q ":$FE_PORT " && echo '(listening)' || echo '(idle)')"
}

if [[ $STATUS_ONLY -eq 1 ]]; then
  show_status
  exit 0
fi

# ---- 1. Rebuild DB (idempotent — ingest is content-addressable) ----
if [[ $REBUILD -eq 1 ]]; then
  log "Rebuilding DB at $DB_PATH"
  rm -f "$DB_PATH"
  make -f Makefile.weather db-init >/dev/null

  SNAP_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv"
  PAPER_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv"

  if [[ -f "$SNAP_CSV" ]]; then
    log "  Ingesting snapshot_replay CSV ($(stat -c '%s' "$SNAP_CSV") bytes)"
    make -f Makefile.weather ingest-real >"$LOG_DIR/ingest_snapshot.log" 2>&1 || {
      err "snapshot ingest failed — see $LOG_DIR/ingest_snapshot.log"
      exit 1
    }
  else
    warn "  snapshot_replay CSV missing: $SNAP_CSV (run scripts/ops/sync_weather_remote.sh first)"
  fi

  if [[ -f "$PAPER_CSV" ]]; then
    log "  Ingesting paper_ledger CSV ($(stat -c '%s' "$PAPER_CSV") bytes)"
    make -f Makefile.weather ingest-paper >"$LOG_DIR/ingest_paper.log" 2>&1 || {
      err "paper ingest failed — see $LOG_DIR/ingest_paper.log"
      exit 1
    }
  else
    warn "  paper_ledger CSV missing: $PAPER_CSV"
  fi
fi

show_status

# ---- 2. Start API ----
if [[ $START_API -eq 1 ]]; then
  if ss -tln 2>/dev/null | grep -q ":$API_PORT "; then
    warn "Port $API_PORT already in use — assuming API is already running"
  else
    log "Starting API on :$API_PORT (logs: $LOG_DIR/api.log)"
    WEATHER_DB_PATH="$DB_PATH" \
      nohup "$VENV/uvicorn" weather_dashboard.api.app:app \
        --host 0.0.0.0 --port "$API_PORT" --reload \
        >"$LOG_DIR/api.log" 2>&1 &
    echo $! > "$LOG_DIR/api.pid"
    sleep 2
    if ! ss -tln 2>/dev/null | grep -q ":$API_PORT "; then
      err "API failed to start — tail of log:"
      tail -20 "$LOG_DIR/api.log" >&2
      exit 1
    fi
    log "  API healthy: http://localhost:$API_PORT/health"
  fi
fi

# ---- 3. Start frontend ----
if [[ $START_FE -eq 1 ]]; then
  if [[ ! -d "$FE_DIR/node_modules" ]]; then
    log "Installing frontend deps (first run)"
    (cd "$FE_DIR" && npm install >"$LOG_DIR/npm_install.log" 2>&1)
  fi
  if ss -tln 2>/dev/null | grep -q ":$FE_PORT "; then
    warn "Port $FE_PORT already in use — assuming FE already running"
  else
    log "Starting frontend on :$FE_PORT (logs: $LOG_DIR/fe.log)"
    (cd "$FE_DIR" && nohup npm run dev -- --host 0.0.0.0 --port "$FE_PORT" \
        >"$LOG_DIR/fe.log" 2>&1 &
     echo $! > "$LOG_DIR/fe.pid")
    sleep 3
  fi
fi

log "Done. Open:"
log "  Dashboard: http://localhost:$FE_PORT/weather/runs"
log "  Live mon:  http://localhost:$FE_PORT/weather/live"
log "  API docs:  http://localhost:$API_PORT/docs"
log ""
log "Stop:"
log "  pkill -F $LOG_DIR/api.pid 2>/dev/null; pkill -F $LOG_DIR/fe.pid 2>/dev/null"
