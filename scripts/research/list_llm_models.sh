#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
API_KEY="${LLM_API_KEY:-}"

if [[ -z "${API_KEY}" ]]; then
  echo "LLM_API_KEY is required." >&2
  exit 1
fi

curl -sS "${BASE_URL}/v1/models" \
  -H "Authorization: Bearer ${API_KEY}"
