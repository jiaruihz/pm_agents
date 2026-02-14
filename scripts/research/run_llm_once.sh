#!/usr/bin/env bash
set -euo pipefail

# DeepSeek (OpenAI-compatible)
: "${LLM_API_KEY:?Set LLM_API_KEY in your environment or .env}"
: "${LLM_BASE_URL:=https://api.deepseek.com}"
: "${LLM_MODEL:=deepseek-chat}"
export LLM_API_KEY LLM_BASE_URL LLM_MODEL

# If your proxy MITMs TLS, keep these to avoid cert errors.
export PYTHONHTTPSVERIFY=0
export CURL_CA_BUNDLE=""

.venv/bin/pmr parse --llm --batch 1
