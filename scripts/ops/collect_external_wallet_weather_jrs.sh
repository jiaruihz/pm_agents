#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 WALLET" >&2
  exit 2
fi

WALLET="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
ARCHIVE_STORAGE_ROOT="$("$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_production_path.py" archive_storage_root)"
JRS_ROOT="${EXTERNAL_WALLET_WEATHER_JRS_ROOT:-$ARCHIVE_STORAGE_ROOT/pm_agents/research/external_wallet_weather}"
WORKERS="${EXTERNAL_WALLET_WEATHER_WORKERS:-8}"
if [[ ! "$WALLET" =~ ^0x[0-9a-f]{40}$ ]]; then
  echo "invalid wallet: $WALLET" >&2
  exit 2
fi
if [[ ! "$WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid worker count: $WORKERS" >&2
  exit 2
fi

END_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SNAPSHOT_ID="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$END_UTC" +%Y%m%dT%H%M%SZ)"
OUTPUT="$JRS_ROOT/raw/wallet=$WALLET/snapshot=$SNAPSHOT_ID"
SESSION="external_wallet_collect_${WALLET:2:8}_${SNAPSHOT_ID:9:6}"
JOB_DIR="$JRS_ROOT/jobs/$WALLET/collect_$SNAPSHOT_ID"

printf -v JOB_COMMAND \
  'set -eu; cd %q; %q %q --wallet %q --output %q --workers %q --end %q; export WEATHER_JRS_IMPORT_CONTEXT=1; %q %q --source %q --jrs-root %q --wallet %q' \
  "$PROJECT_DIR" \
  "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/analysis/wallet_weather/collect_external_wallet_weather_history_v1.py" \
  "$WALLET" \
  "$OUTPUT" \
  "$WORKERS" \
  "$END_UTC" \
  "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/analysis/wallet_weather/persist_external_wallet_weather_jrs_v1.py" \
  "$OUTPUT" \
  "$JRS_ROOT" \
  "$WALLET"

weather_jrs_tmux_run_oneshot \
  "$JRS_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"

echo "wallet=$WALLET snapshot_id=$SNAPSHOT_ID output=$OUTPUT"
