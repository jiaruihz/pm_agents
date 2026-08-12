#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 WALLET_FILE" >&2
  exit 2
fi

WALLET_FILE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
if [[ ! -f "$WALLET_FILE" ]]; then
  echo "wallet file not found: $WALLET_FILE" >&2
  exit 2
fi

ARCHIVE_STORAGE_ROOT="$("$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_production_path.py" archive_storage_root)"
JRS_ROOT="${EXTERNAL_WALLET_WEATHER_JRS_ROOT:-$ARCHIVE_STORAGE_ROOT/pm_agents/research/external_wallet_weather}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
SESSION="external_wallet_batch_replay_${RUN_ID:9:6}"
JOB_DIR="$JRS_ROOT/jobs/batch_full_ladder_$RUN_ID"
OUTPUT="$JOB_DIR/batch_manifest.json"

printf -v JOB_COMMAND \
  'set -eu; cd %q; %q %q --wallet-file %q --jrs-root %q --output %q' \
  "$PROJECT_DIR" \
  "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/analysis/wallet_weather/run_external_wallet_full_ladder_batch_v1.py" \
  "$WALLET_FILE" \
  "$JRS_ROOT" \
  "$OUTPUT"

weather_jrs_tmux_run_oneshot \
  "$JRS_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"

echo "batch_manifest=$OUTPUT"
