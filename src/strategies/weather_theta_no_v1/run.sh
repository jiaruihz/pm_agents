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
  echo "PMM_TOKEN_IDS is required, e.g. NO_TOKEN_ID" >&2
  exit 1
fi

PARAMS_FILE="${1:-src/strategies/weather_theta_no_v1/params.example.json}"
if [[ ! -f "$PARAMS_FILE" ]]; then
  echo "Params file not found: $PARAMS_FILE" >&2
  exit 1
fi

export PMM_EXECUTION_MODE="${PMM_EXECUTION_MODE:-paper}"
export PMM_STRATEGY_KEY="weather_theta_no_v1"
export PMM_STRATEGY_PARAMS_JSON="$(cat "$PARAMS_FILE")"

PY_BIN=".venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi
exec "$PY_BIN" -u -m src.domains.pmm.main
