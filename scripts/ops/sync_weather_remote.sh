#!/usr/bin/env bash
# scripts/ops/sync_weather_remote.sh
#
# Sync weather data from two remote sources:
#   1. weather-predict machine → runtime/weather_edge_v1/market_data/
#      (paper snapshots, orderbook snapshots, research CSVs, cache)
#   2. n100 pm_agent runtime → runtime/weather_edge_v1/remote_pm_agent/
#      (live_cycle JSONL, signals, plans, live orders, paper orders)
#
# Usage:
#   scripts/ops/sync_weather_remote.sh              # sync both
#   scripts/ops/sync_weather_remote.sh --dry-run    # preview only
#   scripts/ops/sync_weather_remote.sh --market-only  # only weather-predict
#   scripts/ops/sync_weather_remote.sh --live-only    # only n100

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ---- Config: weather-predict machine (market data source) ----
WEATHER_REMOTE="${WEATHER_REMOTE:-jiarui@192.168.0.200}"
WEATHER_REMOTE_DIR="${WEATHER_REMOTE_DIR:-~/projects/weather-predict}"
SSH_KEY="${WEATHER_SSH_KEY:-$HOME/.ssh/id_ed25519_weather_deploy}"
MARKET_LOCAL="$REPO_ROOT/runtime/weather_edge_v1/market_data"

# ---- Config: n100 pm_agent runtime source ----
N100_REMOTE="${N100_REMOTE:-jiarui@192.168.0.200}"
N100_REMOTE_DIR="${N100_REMOTE_DIR:-/home/rui/projects/pm_agent/runtime/weather_edge_v1}"
N100_LOCAL="$REPO_ROOT/runtime/weather_edge_v1/remote_pm_agent"

# ---- Flags ----
DRY_RUN=0
SYNC_MARKET=1
SYNC_LIVE=1

for arg in "$@"; do
  case "$arg" in
    --dry-run)     DRY_RUN=1 ;;
    --market-only) SYNC_LIVE=0 ;;
    --live-only)   SYNC_MARKET=0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

RSYNC_FLAGS=(-az --info=stats1,progress2)
[[ "$DRY_RUN" == "1" ]] && RSYNC_FLAGS+=(--dry-run)

WEATHER_SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes)
N100_SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)

log()  { printf '\033[1;36m[sync]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sync]\033[0m %s\n' "$*"; }

# ---- Block 1: market_data from weather-predict machine ----
sync_market() {
  log "=== Syncing market_data from $WEATHER_REMOTE:$WEATHER_REMOTE_DIR ==="

  _sync_dir() {
    local remote_subdir="$1" local_subdir="$2"
    mkdir -p "$MARKET_LOCAL/$local_subdir"
    rsync "${RSYNC_FLAGS[@]}" -e "ssh ${WEATHER_SSH_OPTS[*]}" \
      "$WEATHER_REMOTE:$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
  }

  _sync_glob() {
    local remote_subdir="$1" pattern="$2" local_subdir="$3"
    mkdir -p "$MARKET_LOCAL/$local_subdir"
    rsync "${RSYNC_FLAGS[@]}" -e "ssh ${WEATHER_SSH_OPTS[*]}" \
      --include="$pattern" --exclude='*' \
      "$WEATHER_REMOTE:$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
  }

  _sync_dir  "output/paper_snapshots"    "paper_snapshots"
  _sync_dir  "output/orderbook_snapshots" "orderbook_snapshots"
  _sync_dir  "output/paper_trades"       "paper_trades"
  _sync_dir  "output/research"           "research"
  _sync_dir  "cache/pm_history"          "cache/pm_history"
  _sync_dir  "cache/wu_obs"              "cache/wu_obs"
  _sync_glob "cache" "iem_v2_*.csv"      "cache/iem"

  log "market_data: $(find "$MARKET_LOCAL" -type f | wc -l) files, $(du -sh "$MARKET_LOCAL" 2>/dev/null | cut -f1)"
}

# ---- Block 2: pm_agent runtime (live_cycle/signals/plans/live/paper) from n100 ----
sync_live() {
  log "=== Syncing pm_agent runtime from $N100_REMOTE:$N100_REMOTE_DIR ==="

  if ! ssh "${N100_SSH_OPTS[@]}" "$N100_REMOTE" "test -d $N100_REMOTE_DIR" 2>/dev/null; then
    warn "n100 not reachable or remote dir missing — skipping live sync"
    warn "  host: $N100_REMOTE, dir: $N100_REMOTE_DIR"
    return 0
  fi

  for subdir in live_cycle signals plans live paper; do
    mkdir -p "$N100_LOCAL/$subdir"
    log "  syncing $subdir/"
    rsync "${RSYNC_FLAGS[@]}" -e "ssh ${N100_SSH_OPTS[*]}" \
      --exclude="*.pid" \
      --exclude="*.out" \
      "$N100_REMOTE:$N100_REMOTE_DIR/$subdir/" "$N100_LOCAL/$subdir/" || {
      warn "  $subdir sync failed (non-fatal)"
    }
  done

  log "remote_pm_agent: $(find "$N100_LOCAL" -type f | wc -l) files, $(du -sh "$N100_LOCAL" 2>/dev/null | cut -f1)"
}

echo "date: $(date -Is)"
[[ "$SYNC_MARKET" == "1" ]] && sync_market
[[ "$SYNC_LIVE" == "1" ]]   && sync_live
echo "done."
