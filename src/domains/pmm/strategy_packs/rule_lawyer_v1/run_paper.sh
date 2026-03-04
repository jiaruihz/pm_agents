#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

PARAMS_FILE="${1:-src/domains/pmm/strategy_packs/rule_lawyer_v1/params.example.json}"
if [[ ! -f "$PARAMS_FILE" ]]; then
  echo "Params file not found: $PARAMS_FILE" >&2
  exit 1
fi

PY_BIN=".venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi

# Note: this strategy pack runs in research domain.
exec "$PY_BIN" -m src.domains.research.cli pap-run
