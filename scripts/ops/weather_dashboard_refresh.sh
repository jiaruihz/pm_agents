#!/usr/bin/env bash
# Compatibility wrapper for the single bounded canonical refresh entrypoint.
#
# Usage:
#   scripts/ops/weather_dashboard_refresh.sh
#   scripts/ops/weather_dashboard_refresh.sh --status
#
# Historical --no-sync/--no-clob/--sync-only pipelines are retired.  Current
# inputs and live journals are resolved by production.yaml inside the bounded
# canonical refresh job.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if [[ "${1:-}" == "--status" ]]; then
  .venv/bin/python scripts/ops/weather_production_ctl.py health
  exit $?
fi
if [[ $# -ne 0 ]]; then
  echo "retired refresh flags: use the canonical bounded one-shot with no arguments" >&2
  exit 2
fi

exec "$REPO_ROOT/scripts/ops/start_weather_canonical_refresh_tmux.sh"
