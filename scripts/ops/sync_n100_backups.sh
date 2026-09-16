#!/usr/bin/env bash
# scripts/ops/sync_n100_backups.sh
#
# Pull N100 weather-predict tar backups to local for disaster recovery.
# These backups are produced on N100 by scripts/ops/backup_data.sh and live
# in /home/jiarui/weather-predict-backups/. They are the only way to recover
# weather-predict data if N100 disk dies.
#
# Volume: ~14 MB today, grows ~4 MB/day. Safe to run weekly.
#
# Usage:
#   scripts/ops/sync_n100_backups.sh              # sync all
#   scripts/ops/sync_n100_backups.sh --dry-run    # preview

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

N100_REMOTE="${N100_REMOTE:-jiarui@192.168.0.200}"
N100_BACKUP_DIR="${N100_BACKUP_DIR:-/home/jiarui/weather-predict-backups}"
LOCAL_BACKUP_DIR="$REPO_ROOT/runtime/_backups_n100"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

RSYNC_FLAGS=(-az --info=stats1,progress2)
[[ "$DRY_RUN" == "1" ]] && RSYNC_FLAGS+=(--dry-run)

N100_SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10)

log()  { printf '\033[1;36m[backup-sync]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[backup-sync]\033[0m %s\n' "$*"; }

log "date: $(date -Is)"
log "source: $N100_REMOTE:$N100_BACKUP_DIR"
log "dest:   $LOCAL_BACKUP_DIR"

mkdir -p "$LOCAL_BACKUP_DIR"

if ! ssh "${N100_SSH_OPTS[@]}" "$N100_REMOTE" "test -d $N100_BACKUP_DIR" 2>/dev/null; then
  warn "n100 not reachable or backup dir missing — skipping"
  warn "  host: $N100_REMOTE, dir: $N100_BACKUP_DIR"
  exit 0
fi

rsync "${RSYNC_FLAGS[@]}" -e "ssh ${N100_SSH_OPTS[*]}" \
  "$N100_REMOTE:$N100_BACKUP_DIR/" "$LOCAL_BACKUP_DIR/"

log "local backup mirror: $(find "$LOCAL_BACKUP_DIR" -type f | wc -l) files, $(du -sh "$LOCAL_BACKUP_DIR" 2>/dev/null | cut -f1)"

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run complete; skipping local sha256 verification"
  exit 0
fi

# ---- Verify sha256 of the latest backup (if .sha256 sidecar present) ----
latest_tar="$(ls -t "$LOCAL_BACKUP_DIR"/*.tar.zst 2>/dev/null | head -1 || true)"
if [[ -n "$latest_tar" && -f "${latest_tar}.sha256" ]]; then
  log "verifying sha256 of $(basename "$latest_tar")"
  expected_hash="$(awk '{print $1; exit}' "${latest_tar}.sha256")"
  if [[ -z "$expected_hash" ]]; then
    warn "sha256 sidecar is empty: ${latest_tar}.sha256"
    exit 1
  fi
  (cd "$LOCAL_BACKUP_DIR" && printf '%s  %s\n' "$expected_hash" "$(basename "$latest_tar")" | sha256sum -c -) || {
    warn "sha256 verification FAILED for $(basename "$latest_tar")"
    exit 1
  }
  log "sha256 OK"
fi

log "done."
