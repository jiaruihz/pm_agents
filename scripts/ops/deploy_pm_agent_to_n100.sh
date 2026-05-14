#!/usr/bin/env bash
set -euo pipefail

REMOTE="${REMOTE:-jiarui@192.168.0.200}"
REMOTE_DIR="${REMOTE_DIR:-/home/rui/projects/pm_agent}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

RSYNC_ARGS=(
  -az
  --delete
  --exclude ".git/"
  --exclude ".venv/"
  --exclude ".venv-md-docx/"
  --exclude ".env"
  --exclude ".pytest_cache/"
  --exclude "__pycache__/"
  --exclude "runtime/"
  --exclude "node_modules/"
  --exclude "chatgpt-web-bot/node_modules/"
  --exclude "research.db"
)

if [[ "$DRY_RUN" == "1" ]]; then
  RSYNC_ARGS+=(--dry-run)
fi

ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"
rsync "${RSYNC_ARGS[@]}" "$PROJECT_DIR/" "$REMOTE:$REMOTE_DIR/"

cat <<EOF
deployed pm_agent
  local:  $PROJECT_DIR
  remote: $REMOTE:$REMOTE_DIR
  dry_run: $DRY_RUN

Next remote setup, if needed:
  cd $REMOTE_DIR
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
EOF
