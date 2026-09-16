#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
  echo "usage: $0 SOURCE_SNAPSHOT_DIR [WALLET]" >&2
  exit 2
fi

SOURCE_DIR="$1"
WALLET="${2:-}"
ARCHIVE_STORAGE_ROOT="$("$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_production_path.py" archive_storage_root)"
JRS_ROOT="${EXTERNAL_WALLET_WEATHER_JRS_ROOT:-$ARCHIVE_STORAGE_ROOT/pm_agents/research/external_wallet_weather}"

if [[ ! -f "$SOURCE_DIR/manifest.json" ]]; then
  echo "missing source manifest: $SOURCE_DIR/manifest.json" >&2
  exit 2
fi

if [[ -z "$WALLET" ]]; then
  WALLET="$(
    "$PROJECT_DIR/.venv/bin/python" -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["wallet"])' \
      "$SOURCE_DIR/manifest.json"
  )"
fi
WALLET="$(printf '%s' "$WALLET" | tr '[:upper:]' '[:lower:]')"
if [[ ! "$WALLET" =~ ^0x[0-9a-f]{40}$ ]]; then
  echo "invalid wallet: $WALLET" >&2
  exit 2
fi

SNAPSHOT_ID="$(
  "$PROJECT_DIR/.venv/bin/python" -c \
    'import datetime,json,sys; value=json.load(open(sys.argv[1]))["coverage"]["query_end_utc"]; print(datetime.datetime.fromisoformat(value).astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))' \
    "$SOURCE_DIR/manifest.json"
)"
SESSION="external_wallet_import_${WALLET:2:8}_${SNAPSHOT_ID:9:6}"
JOB_DIR="$JRS_ROOT/jobs/$WALLET"
printf -v JOB_COMMAND \
  'cd %q; export WEATHER_JRS_IMPORT_CONTEXT=1; %q %q --source %q --jrs-root %q --wallet %q' \
  "$PROJECT_DIR" \
  "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/analysis/wallet_weather/persist_external_wallet_weather_jrs_v1.py" \
  "$SOURCE_DIR" \
  "$JRS_ROOT" \
  "$WALLET"

weather_jrs_tmux_run_oneshot \
  "$JRS_ROOT" \
  "$SESSION" \
  "$JOB_DIR" \
  "$JOB_COMMAND"
