#!/usr/bin/env bash
set -euo pipefail

REMOTE="${WEATHER_REMOTE:-jiarui@192.168.0.200}"
REMOTE_DIR="${WEATHER_REMOTE_DIR:-~/projects/weather-predict}"
LOCAL_ROOT="${WEATHER_LOCAL_ROOT:-runtime/weather_edge_v1/market_data}"
SSH_KEY="${WEATHER_SSH_KEY:-$HOME/.ssh/id_ed25519_weather_deploy}"
RSYNC_FLAGS=(-az --info=stats1,progress2)

if [[ "${1:-}" == "--dry-run" ]]; then
  RSYNC_FLAGS+=("--dry-run")
fi

SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes)

sync_dir() {
  local remote_subdir="$1"
  local local_subdir="$2"
  mkdir -p "$LOCAL_ROOT/$local_subdir"
  rsync "${RSYNC_FLAGS[@]}" -e "ssh ${SSH_OPTS[*]}" \
    "$REMOTE:$REMOTE_DIR/$remote_subdir/" "$LOCAL_ROOT/$local_subdir/"
}

sync_glob() {
  local remote_subdir="$1"
  local pattern="$2"
  local local_subdir="$3"
  mkdir -p "$LOCAL_ROOT/$local_subdir"
  rsync "${RSYNC_FLAGS[@]}" -e "ssh ${SSH_OPTS[*]}" \
    --include="$pattern" --exclude='*' \
    "$REMOTE:$REMOTE_DIR/$remote_subdir/" "$LOCAL_ROOT/$local_subdir/"
}

echo "remote=$REMOTE:$REMOTE_DIR"
echo "local=$LOCAL_ROOT"
date -Is

sync_dir "output/paper_snapshots" "paper_snapshots"
sync_dir "output/paper_trades" "paper_trades"
sync_dir "output/research" "research"
sync_dir "cache/pm_history" "cache/pm_history"
sync_dir "cache/wu_obs" "cache/wu_obs"
sync_glob "cache" "iem_v2_*.csv" "cache/iem"

echo
echo "local summary:"
find "$LOCAL_ROOT" -maxdepth 2 -type f | wc -l
du -sh "$LOCAL_ROOT" || true
