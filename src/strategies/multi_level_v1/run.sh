#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ -z "${PMM_TOKEN_IDS:-}" ]]; then
  echo "PMM_TOKEN_IDS is required, e.g. YES_TOKEN,NO_TOKEN" >&2
  exit 1
fi

PARAMS_FILE="${1:-src/strategies/multi_level_v1/params.example.json}"
if [[ ! -f "$PARAMS_FILE" ]]; then
  echo "Params file not found: $PARAMS_FILE" >&2
  exit 1
fi

export PMM_EXECUTION_MODE="${PMM_EXECUTION_MODE:-paper}"
export PMM_STRATEGY_KEY="multi_level_v1"
export PMM_QUOTE_LEVELS="${PMM_QUOTE_LEVELS:-3}"
export PMM_LEVEL_SPREAD_STEP="${PMM_LEVEL_SPREAD_STEP:-0.005}"
export PMM_LEVEL_SIZE_DECAY="${PMM_LEVEL_SIZE_DECAY:-0.6}"
export PMM_STRATEGY_PARAMS_JSON="$(cat "$PARAMS_FILE")"

PY_BIN=".venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi
exec "$PY_BIN" -u -m src.strategies.pmm.main
