#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUNTIME_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
LOCK_DIR="$RUNTIME_DIR/refresh.lock"

mkdir -p "$RUNTIME_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "canonical refresh already running; skipping"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR"
scripts/ops/weather_dashboard_refresh.sh --no-sync
