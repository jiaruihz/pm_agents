#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 WALLET SNAPSHOT_ID" >&2
  exit 2
fi

WALLET="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
SNAPSHOT_ID="$2"
JRS_ROOT="${EXTERNAL_WALLET_WEATHER_JRS_ROOT:-/Volumes/jrs/pm_agents/research/external_wallet_weather}"

if [[ ! "$WALLET" =~ ^0x[0-9a-f]{40}$ ]]; then
  echo "invalid wallet: $WALLET" >&2
  exit 2
fi
if [[ ! "$SNAPSHOT_ID" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "invalid snapshot id: $SNAPSHOT_ID" >&2
  exit 2
fi

SOURCE="$JRS_ROOT/raw/wallet=$WALLET/snapshot=$SNAPSHOT_ID"
OUTPUT="$SOURCE/analysis/full_ladder_history_v1"
SESSION="external_wallet_replay_${WALLET:2:8}_${SNAPSHOT_ID:9:6}"
JOB_DIR="$JRS_ROOT/jobs/$WALLET/full_ladder_history_v1"
printf -v JOB_COMMAND \
  'cd %q; %q %q --source %q --output %q --wallet %q --snapshot-id %q' \
  "$PROJECT_DIR" \
  "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/analysis/wallet_weather/research_external_wallet_full_ladder_history_v1.py" \
  "$SOURCE" \
  "$OUTPUT" \
  "$WALLET" \
  "$SNAPSHOT_ID"

weather_jrs_tmux_run_oneshot \
  "$JRS_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"
