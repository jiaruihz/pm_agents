#!/usr/bin/env bash
set -euo pipefail

API_URL="${GLM_BASE_URL:-https://open.bigmodel.cn/api/paas/v4/chat/completions}"
MODEL="${GLM_MODEL:-glm-4.6}"
API_KEY="${GLM:-${GLM_API_KEY:-}}"
STREAM="${GLM_STREAM:-true}"
SYSTEM_PROMPT="${GLM_SYSTEM_PROMPT:-You are a helpful AI assistant.}"
USER_PROMPT="${1:-Hello, please introduce yourself.}"

if [[ -z "${API_KEY}" ]]; then
  echo "Missing API key. Set GLM (recommended) or GLM_API_KEY." >&2
  exit 1
fi

BODY="$(
python3 - "${MODEL}" "${SYSTEM_PROMPT}" "${USER_PROMPT}" "${STREAM}" <<'PY'
import json
import sys

model, system_prompt, user_prompt, stream_value = sys.argv[1:5]
stream = stream_value.strip().lower() in {"1", "true", "yes", "on"}
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "temperature": 1.0,
    "stream": stream,
}
print(json.dumps(payload, ensure_ascii=False))
PY
)"

if [[ "${STREAM,,}" == "true" || "${STREAM}" == "1" ]]; then
  curl -sS -N -X POST "${API_URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -d "${BODY}"
else
  curl -sS -X POST "${API_URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -d "${BODY}"
fi
