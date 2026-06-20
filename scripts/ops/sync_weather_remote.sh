#!/usr/bin/env bash
# scripts/ops/sync_weather_remote.sh
#
# Sync weather data from remote sources:
#   1. weather-predict or weather_data_feed_service runtime
#      → runtime/weather_edge_v1/market_data/
#      (paper snapshots, orderbook snapshots, research CSVs, cache)
#   2. n100 pm_agent runtime → runtime/weather_edge_v1/remote_pm_agent/
#      (live_cycle JSONL, signals, plans, live orders, paper orders)
#
# Usage:
#   scripts/ops/sync_weather_remote.sh              # sync both
#   scripts/ops/sync_weather_remote.sh --dry-run    # preview only
#   scripts/ops/sync_weather_remote.sh --market-only  # only market data
#   scripts/ops/sync_weather_remote.sh --live-only    # only n100
#   scripts/ops/sync_weather_remote.sh --market-source=weather-data-feed

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ---- Config: market data source ----
WEATHER_REMOTE="${WEATHER_REMOTE:-jiarui@192.168.0.200}"
WEATHER_MARKET_SOURCE="${WEATHER_MARKET_SOURCE:-weather-predict}"
WEATHER_PREDICT_REMOTE_DIR="${WEATHER_PREDICT_REMOTE_DIR:-~/projects/weather-predict}"
WEATHER_DATA_FEED_RUNTIME_DIR="${WEATHER_DATA_FEED_RUNTIME_DIR:-~/projects/weather_data_feed_service_runtime}"
WEATHER_REMOTE_DIR="${WEATHER_REMOTE_DIR:-}"
SSH_KEY="${WEATHER_SSH_KEY:-$HOME/.ssh/id_ed25519_weather_deploy}"
MARKET_LOCAL="$REPO_ROOT/runtime/weather_edge_v1/market_data"

# ---- Config: n100 pm_agent runtime source ----
N100_REMOTE="${N100_REMOTE:-jiarui@192.168.0.200}"
N100_REMOTE_DIR="${N100_REMOTE_DIR:-/home/jiarui/projects/pm_agent/runtime/weather_edge_v1}"
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
    --market-source=*) WEATHER_MARKET_SOURCE="${arg#*=}" ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

case "$WEATHER_MARKET_SOURCE" in
  weather-predict|legacy)
    WEATHER_MARKET_SOURCE="weather-predict"
    WEATHER_REMOTE_DIR="${WEATHER_REMOTE_DIR:-$WEATHER_PREDICT_REMOTE_DIR}"
    MARKET_OPTIONAL_OUTPUTS=0
    ;;
  weather-data-feed|weather_data_feed|weather_data_feed_service|data-feed)
    WEATHER_MARKET_SOURCE="weather-data-feed"
    WEATHER_REMOTE_DIR="${WEATHER_REMOTE_DIR:-$WEATHER_DATA_FEED_RUNTIME_DIR}"
    MARKET_OPTIONAL_OUTPUTS=1
    ;;
  *)
    echo "Unknown WEATHER_MARKET_SOURCE: $WEATHER_MARKET_SOURCE" >&2
    echo "Expected weather-predict or weather-data-feed" >&2
    exit 2
    ;;
esac

RSYNC_FLAGS=(-az)
if rsync --help 2>/dev/null | grep -q -- '--info='; then
  RSYNC_FLAGS+=(--info=stats1,progress2)
else
  RSYNC_FLAGS+=(--stats --progress)
fi
[[ "$DRY_RUN" == "1" ]] && RSYNC_FLAGS+=(--dry-run)

WEATHER_SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes)
N100_SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)

log()  { printf '\033[1;36m[sync]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sync]\033[0m %s\n' "$*"; }
iso_now() {
  date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z'
}

# ---- Block 1: market_data from configured producer ----
sync_market() {
  log "=== Syncing market_data source=$WEATHER_MARKET_SOURCE from $WEATHER_REMOTE:$WEATHER_REMOTE_DIR ==="

  _remote_dir_exists() {
    local remote_subdir="$1"
    if [[ "$WEATHER_REMOTE" == "local" || "$WEATHER_REMOTE" == "localhost" || "$WEATHER_REMOTE" == "127.0.0.1" ]]; then
      [[ -d "$WEATHER_REMOTE_DIR/$remote_subdir" ]]
    else
      ssh "${WEATHER_SSH_OPTS[@]}" "$WEATHER_REMOTE" "test -d $WEATHER_REMOTE_DIR/$remote_subdir" 2>/dev/null
    fi
  }

  _sync_dir() {
    local remote_subdir="$1" local_subdir="$2"
    mkdir -p "$MARKET_LOCAL/$local_subdir"
    if [[ "$WEATHER_REMOTE" == "local" || "$WEATHER_REMOTE" == "localhost" || "$WEATHER_REMOTE" == "127.0.0.1" ]]; then
      rsync "${RSYNC_FLAGS[@]}" \
        "$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
    else
      rsync "${RSYNC_FLAGS[@]}" -e "ssh ${WEATHER_SSH_OPTS[*]}" \
        "$WEATHER_REMOTE:$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
    fi
  }

  _sync_dir_optional() {
    local remote_subdir="$1" local_subdir="$2"
    if _remote_dir_exists "$remote_subdir"; then
      _sync_dir "$remote_subdir" "$local_subdir"
    else
      warn "  missing optional market dir: $WEATHER_REMOTE_DIR/$remote_subdir"
    fi
  }

  _sync_glob() {
    local remote_subdir="$1" pattern="$2" local_subdir="$3"
    mkdir -p "$MARKET_LOCAL/$local_subdir"
    if [[ "$WEATHER_REMOTE" == "local" || "$WEATHER_REMOTE" == "localhost" || "$WEATHER_REMOTE" == "127.0.0.1" ]]; then
      rsync "${RSYNC_FLAGS[@]}" \
        --include="$pattern" --exclude='*' \
        "$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
    else
      rsync "${RSYNC_FLAGS[@]}" -e "ssh ${WEATHER_SSH_OPTS[*]}" \
        --include="$pattern" --exclude='*' \
        "$WEATHER_REMOTE:$WEATHER_REMOTE_DIR/$remote_subdir/" "$MARKET_LOCAL/$local_subdir/"
    fi
  }

  # ---- Output ----
  _sync_dir  "output/paper_snapshots"    "paper_snapshots"
  _sync_dir  "output/orderbook_snapshots" "orderbook_snapshots"
  if [[ "$MARKET_OPTIONAL_OUTPUTS" == "1" ]]; then
    _sync_dir_optional "output/paper_trades" "paper_trades"
    _sync_dir_optional "output/research"     "research"
  else
    _sync_dir "output/paper_trades" "paper_trades"
    _sync_dir "output/research"     "research"
  fi
  _sync_dir  "output/logs"               "logs"

  # ---- Cache: observation / settlement (large, already used) ----
  _sync_dir  "cache/pm_history"          "cache/pm_history"
  _sync_dir  "cache/wu_obs"              "cache/wu_obs"
  _sync_glob "cache" "iem_v2_*.csv"      "cache/iem"

  # ---- Cache: weather model forecast (probability model inputs) ----
  # GFS (primary), ECMWF (secondary), plus regional models for coverage.
  _sync_glob "cache" "gfs_v4_*.json"     "cache/gfs_v4"
  _sync_glob "cache" "gfs_daily_*.json"  "cache/gfs_daily"
  _sync_glob "cache" "ecmwf_v4_*.json"   "cache/ecmwf_v4"
  _sync_glob "cache" "jma_v5_*.json"     "cache/jma_v5"
  _sync_glob "cache" "hrrr_v5_*.json"    "cache/hrrr_v5"
  _sync_glob "cache" "icon_eu_v5_*.json" "cache/icon_eu_v5"
  _sync_glob "cache" "arome_v5_*.json"   "cache/arome_v5"

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

  for subdir in live_cycle signals plans live paper range_rv_shadow_v0 theta_current_yes_tiny_live_v1; do
    mkdir -p "$N100_LOCAL/$subdir"
    log "  syncing $subdir/"
    rsync "${RSYNC_FLAGS[@]}" -e "ssh ${N100_SSH_OPTS[*]}" \
      --exclude="*.pid" \
      --exclude="*.out" \
      "$N100_REMOTE:$N100_REMOTE_DIR/$subdir/" "$N100_LOCAL/$subdir/" || {
      warn "  $subdir sync failed (non-fatal)"
    }
  done

  # Persisted CLOB fill cache is replayed by clob_fill_sync during local DB rebuild.
  # Do not rsync this file directly onto the local canonical cache: local recovery
  # can contain more partial fills than the N100 cache, and overwriting it would
  # make the next fact rebuild regress live_real coverage.
  mkdir -p "$REPO_ROOT/runtime/weather_edge_v1"
  log "  syncing clob_fills.jsonl (staging only; local cache is canonical)"
  local remote_clob_cache="$N100_LOCAL/clob_fills.remote.jsonl"
  local local_clob_cache="$REPO_ROOT/runtime/weather_edge_v1/clob_fills.jsonl"
  rsync "${RSYNC_FLAGS[@]}" -e "ssh ${N100_SSH_OPTS[*]}" \
    "$N100_REMOTE:/home/jiarui/projects/pm_agent/runtime/weather_edge_v1/clob_fills.jsonl" \
    "$remote_clob_cache" || {
    warn "  clob_fills.jsonl sync failed (non-fatal)"
  }
  if [[ "$DRY_RUN" == "0" && -f "$remote_clob_cache" ]]; then
    if [[ ! -f "$local_clob_cache" ]]; then
      cp "$remote_clob_cache" "$local_clob_cache"
      log "  initialized local clob_fills.jsonl from remote staging"
    else
      local_count="$(wc -l < "$local_clob_cache" | tr -d ' ')"
      remote_count="$(wc -l < "$remote_clob_cache" | tr -d ' ')"
      log "  staged remote clob_fills.jsonl: remote_rows=$remote_count local_rows=$local_count"
      if [[ "$remote_count" -gt "$local_count" ]]; then
        warn "  remote clob_fills.jsonl has more rows than local; not auto-merging. Run scripts/ops/rebuild_clob_fill_cache_from_activity.py --replace before fact rebuild."
      fi
    fi
  fi

  # ---- pm_agent runtime/logs (live cycle process logs, useful for debugging) ----
  mkdir -p "$N100_LOCAL/logs"
  log "  syncing logs/ (pm_agent process logs)"
  rsync "${RSYNC_FLAGS[@]}" -e "ssh ${N100_SSH_OPTS[*]}" \
    --exclude="*.pid" \
    "$N100_REMOTE:/home/jiarui/projects/pm_agent/runtime/logs/" "$N100_LOCAL/logs/" || {
    warn "  logs sync failed (non-fatal)"
  }

  log "remote_pm_agent: $(find "$N100_LOCAL" -type f | wc -l) files, $(du -sh "$N100_LOCAL" 2>/dev/null | cut -f1)"
}

echo "date: $(iso_now)"
[[ "$SYNC_MARKET" == "1" ]] && sync_market
[[ "$SYNC_LIVE" == "1" ]]   && sync_live
echo "done."
