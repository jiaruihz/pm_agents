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

ensure_linux_node() {
  local node_path npm_path nvm_bin
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    # Non-interactive WSL shells do not source nvm automatically.
    # Prefer the user's configured Linux node over Windows node/npm on /mnt/c.
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh"
    nvm use --silent 20 >/dev/null 2>&1 || nvm use --silent node >/dev/null 2>&1 || true
  fi

  node_path="$(command -v node 2>/dev/null || true)"
  npm_path="$(command -v npm 2>/dev/null || true)"

  if [[ "$node_path" == /mnt/* || "$node_path" == *.exe || "$npm_path" == /mnt/* || "$npm_path" == *.cmd ]]; then
    nvm_bin="$(find "$HOME/.nvm/versions/node" -maxdepth 3 -type f -name node -printf '%h\n' 2>/dev/null | sort -V | tail -1 || true)"
    if [[ -n "$nvm_bin" ]]; then
      export PATH="$nvm_bin:$PATH"
    fi
  fi

  node_path="$(command -v node 2>/dev/null || true)"
  npm_path="$(command -v npm 2>/dev/null || true)"
  if [[ -z "$node_path" || -z "$npm_path" || "$node_path" == /mnt/* || "$npm_path" == /mnt/* ]]; then
    err "Linux node/npm not found for frontend startup. Install nodejs in WSL or install/use nvm under WSL."
    exit 1
  fi
}

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
    rows = c.execute('SELECT run_id, state, execution_mode, (SELECT COUNT(*) FROM fills f JOIN orders o ON f.execution_id=o.execution_id WHERE o.run_id=runs.run_id AND f.status IN (\"filled\", \"simulated\")) AS fills FROM runs').fetchall()
    if not rows:
        print('  [DB] schema present but no runs')
    for r in rows[:25]:
        print(f'  [DB] {r[\"run_id\"][:12]}  {r[\"state\"]:8s} {r[\"execution_mode\"]:18s} fills={r[\"fills\"]}')
    if len(rows) > 25:
        print(f'  [DB] ... {len(rows) - 25} more run(s)')
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
  make -f Makefile.weather db-canonical-rebuild >/dev/null

  SNAP_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_snapshot_replay_trades.csv"
  PAPER_CSV="$REPO_ROOT/runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv"

  if [[ -f "$SNAP_CSV" || -f "$PAPER_CSV" ]]; then
    log "  Migrating legacy research CSVs into canonical DB"
    make -f Makefile.weather migrate-legacy-research >"$LOG_DIR/migrate_legacy_research.log" 2>&1 || {
      err "legacy research migration failed — see $LOG_DIR/migrate_legacy_research.log"
      exit 1
    }
  else
    warn "  research CSVs missing (run scripts/ops/sync_weather_remote.sh first)"
  fi

  if [[ -d "$REPO_ROOT/runtime/weather_edge_v1/live_cycle" || -d "$REPO_ROOT/runtime/weather_edge_v1/remote_pm_agent/live_cycle" ]]; then
    log "  Migrating live-cycle lineage into canonical DB"
    make -f Makefile.weather migrate-live-cycle >"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
      err "live-cycle migration failed — see $LOG_DIR/migrate_live_cycle.log"
      exit 1
    }
  else
    warn "  live_cycle directories missing"
  fi

  log "  Precomputing metrics cache for all runs"
  make -f Makefile.weather metrics-refresh >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "metrics-refresh failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }

  log "  Syncing real CLOB fills from Polymarket activity API"
  "$VENV/python" -m weather_dashboard.ingest.clob_fill_sync \
    --db-path "$DB_PATH" >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || {
    warn "clob-fill-sync failed (non-fatal) — see $LOG_DIR/migrate_live_cycle.log"
  }

  log "  Re-computing metrics after CLOB fill sync"
  make -f Makefile.weather metrics-refresh >>"$LOG_DIR/migrate_live_cycle.log" 2>&1 || true
fi

show_status

# ---- 2. Start main-app BFF (strategy_dashboard_server, port 8011) ----
BFF_PORT="${WEATHER_BFF_PORT:-8011}"
if [[ $START_API -eq 1 ]]; then
  if ss -tln 2>/dev/null | grep -q ":$BFF_PORT "; then
    warn "Port $BFF_PORT already in use — assuming BFF already running"
  else
    log "Starting BFF on :$BFF_PORT (logs: $LOG_DIR/bff.log)"
    ARTIFACTS_DIR="$REPO_ROOT/src/strategies/pmm/backtest/.artifacts"
    setsid nohup "$VENV/python" -m src.interfaces.web.strategy_dashboard_server \
      --host 127.0.0.1 --port "$BFF_PORT" \
      --artifacts-dir "$ARTIFACTS_DIR" \
      --runtime-dir "$REPO_ROOT/runtime" \
      >"$LOG_DIR/bff.log" 2>&1 &
    echo $! > "$LOG_DIR/bff.pid"
    sleep 2
    if ss -tln 2>/dev/null | grep -q ":$BFF_PORT "; then
      log "  BFF healthy on :$BFF_PORT"
    else
      warn "  BFF may not have started — check $LOG_DIR/bff.log"
    fi
  fi
fi

# ---- 3. Start weather API ----
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
  ensure_linux_node
  if [[ ! -d "$FE_DIR/node_modules" ]]; then
    log "Installing frontend deps (first run)"
    (cd "$FE_DIR" && npm install >"$LOG_DIR/npm_install.log" 2>&1)
  fi
  if ss -tln 2>/dev/null | grep -q ":$FE_PORT "; then
    warn "Port $FE_PORT already in use — assuming FE already running"
  else
    log "Starting frontend on :$FE_PORT (logs: $LOG_DIR/fe.log)"
    (cd "$FE_DIR" && setsid nohup npm run dev -- --host 0.0.0.0 --port "$FE_PORT" \
        >"$LOG_DIR/fe.log" 2>&1 &
     echo $! > "$LOG_DIR/fe.pid")
    sleep 5
    # Detect actual port Vite chose (it may increment if $FE_PORT was busy)
    ACTUAL_FE_PORT="$(grep -oE 'localhost:[0-9]+' "$LOG_DIR/fe.log" | head -1 | cut -d: -f2 || echo $FE_PORT)"
    if [[ -n "$ACTUAL_FE_PORT" && "$ACTUAL_FE_PORT" != "$FE_PORT" ]]; then
      warn "Vite started on :$ACTUAL_FE_PORT (port $FE_PORT was busy)"
      FE_PORT="$ACTUAL_FE_PORT"
    fi
  fi
fi

# ---- 4. Windows port-forwarding (WSL2 -> Windows host) ----
setup_windows_portproxy() {
  local wsl_ip
  wsl_ip="$(ip addr show eth0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)"
  if [[ -z "$wsl_ip" ]]; then
    warn "Could not determine WSL IP — skipping Windows port-proxy setup"
    return
  fi
  log "Setting up Windows port-proxy: WSL IP=$wsl_ip"

  # Build one-liner netsh commands (avoid multi-line PS quoting issues in bash)
  local del_api="netsh interface portproxy delete v4tov4 listenport=${API_PORT} listenaddress=0.0.0.0"
  local add_api="netsh interface portproxy add v4tov4 listenport=${API_PORT} listenaddress=0.0.0.0 connectport=${API_PORT} connectaddress=${wsl_ip}"
  local del_fe="netsh interface portproxy delete v4tov4 listenport=${FE_PORT} listenaddress=0.0.0.0"
  local add_fe="netsh interface portproxy add v4tov4 listenport=${FE_PORT} listenaddress=0.0.0.0 connectport=${FE_PORT} connectaddress=${wsl_ip}"

  if powershell.exe -NoProfile -NonInteractive -Command "${del_api}; ${add_api}; ${del_fe}; ${add_fe}; netsh interface portproxy show all" 2>/dev/null; then
    log "  Port-proxy OK"
  else
    warn "  Port-proxy setup requires admin PowerShell. Run manually:"
    warn "    ${add_api}"
    warn "    ${add_fe}"
  fi
}

if [[ $START_API -eq 1 || $START_FE -eq 1 ]]; then
  setup_windows_portproxy
fi

log "Done. Open:"
log "  Dashboard: http://localhost:$FE_PORT/weather/runs"
log "  Live mon:  http://localhost:$FE_PORT/weather/live"
log "  API docs:  http://localhost:$API_PORT/docs"
log ""
log "Stop:"
log "  pkill -F $LOG_DIR/api.pid 2>/dev/null; pkill -F $LOG_DIR/fe.pid 2>/dev/null"
