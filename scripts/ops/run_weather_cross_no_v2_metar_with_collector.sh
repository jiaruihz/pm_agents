#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PYTHON_BIN="${WEATHER_CROSS_NO_V2_METAR_PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
EVIDENCE_DB="${WEATHER_CROSS_NO_V2_METAR_EVIDENCE_DB:?missing evidence DB}"
COLLECTOR_RUNTIME_ROOT="${WEATHER_CROSS_NO_V2_METAR_COLLECTOR_RUNTIME_ROOT:?missing collector runtime root}"
COLLECTOR_LOG="${WEATHER_CROSS_NO_V2_METAR_COLLECTOR_LOG:?missing collector log}"
SOURCE_ENV_FILE="${WEATHER_CROSS_NO_V2_METAR_SOURCE_ENV_FILE:?missing paid source env file}"
UNIVERSE_CONFIG="${WEATHER_CROSS_NO_V2_METAR_UNIVERSE_CONFIG:?missing universe config}"
MARKET_BOOKS_LATEST="${WEATHER_CROSS_NO_V2_METAR_MARKET_BOOKS_LATEST:?missing market books}"
OUTPUT_DIR="${WEATHER_CROSS_NO_V2_METAR_OUTPUT_DIR:?missing output dir}"
CAPTURE_DEMANDS_JSONL="${WEATHER_CROSS_NO_V2_METAR_CAPTURE_DEMANDS_JSONL:?missing capture demands journal}"
PAUSE_FILE="${WEATHER_CROSS_NO_V2_METAR_PAUSE_FILE:?missing pause file}"
STOP_AFTER_SEC="${WEATHER_CROSS_NO_V2_METAR_STOP_AFTER_SEC:?missing stop-after seconds}"
CODE_IDENTITY="${WEATHER_CROSS_NO_V2_METAR_CODE_IDENTITY:?missing code identity}"

collector_pid=""
runner_pid=""
cleanup() {
  if [[ -n "$runner_pid" ]]; then
    kill -TERM "$runner_pid" 2>/dev/null || true
    wait "$runner_pid" 2>/dev/null || true
  fi
  if [[ -n "$collector_pid" ]]; then
    kill -TERM "$collector_pid" 2>/dev/null || true
    wait "$collector_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

mkdir -p "$COLLECTOR_RUNTIME_ROOT" "$(dirname "$COLLECTOR_LOG")" "$OUTPUT_DIR"
if [[ ! -f "$PAUSE_FILE" ]]; then
  if [[ -f "$EVIDENCE_DB" ]]; then
    echo "refusing to append a fresh live collector run to existing evidence DB: $EVIDENCE_DB" >&2
    exit 1
  fi
  set -a
  source "$SOURCE_ENV_FILE"
  set +a
  "$PYTHON_BIN" -m us_fast_weather_lab.cli smoke \
    --runtime-root "$COLLECTOR_RUNTIME_ROOT" \
    --reports-root "$COLLECTOR_RUNTIME_ROOT/reports" \
    --vantage-id MAC_MINI_POLYMARKET_METARWS_48H_V1 \
    --duration-sec "$STOP_AFTER_SEC" \
    --enable-metar-ws --disable-wis2 --disable-awc \
    --station-universe-config "$UNIVERSE_CONFIG" \
    >>"$COLLECTOR_LOG" 2>&1 &
  collector_pid=$!

  for _ in {1..120}; do
    [[ -f "$EVIDENCE_DB" ]] && break
    kill -0 "$collector_pid" 2>/dev/null || {
      echo "paid METAR.ws collector exited before creating evidence DB" >&2
      exit 1
    }
    sleep 0.5
  done
  [[ -f "$EVIDENCE_DB" ]] || { echo "paid METAR.ws collector did not create evidence DB" >&2; exit 1; }
fi

runner=(
  "$PYTHON_BIN" "$PROJECT_DIR/scripts/ops/weather_cross_no_v2_metar.py"
  --loop
  --evidence-db "$EVIDENCE_DB"
  --market-books-latest "$MARKET_BOOKS_LATEST"
  --universe-config "$UNIVERSE_CONFIG"
  --capture-demands-jsonl "$CAPTURE_DEMANDS_JSONL"
  --output-dir "$OUTPUT_DIR"
  --pause-file "$PAUSE_FILE"
  --interval-sec "${WEATHER_CROSS_NO_V2_METAR_INTERVAL_SEC:-0.5}"
  --max-source-age-sec "${WEATHER_CROSS_NO_V2_METAR_MAX_SOURCE_AGE_SEC:-30}"
  --book-timeout-sec "${WEATHER_CROSS_NO_V2_METAR_BOOK_TIMEOUT_SEC:-5}"
  --stop-after-sec "$STOP_AFTER_SEC"
  --official-fee-rate 0.05
  --code-identity "$CODE_IDENTITY"
)
if [[ -n "${WEATHER_MARKET_PROXY_URL:-}" ]]; then
  runner+=(--market-proxy "$WEATHER_MARKET_PROXY_URL")
fi
if [[ "${WEATHER_CROSS_NO_V2_METAR_LIVE:-0}" == "1" ]]; then
  runner+=(--live)
fi
if [[ "${WEATHER_CROSS_NO_V2_METAR_CONFIRM_LIVE:-0}" == "1" ]]; then
  runner+=(--confirm-live)
fi
if [[ "${WEATHER_CROSS_NO_V2_METAR_ALLOW_CLOCK_INVALID_SAME_BOOT_MONOTONIC_PROBE:-0}" == "1" ]]; then
  runner+=(--allow-clock-invalid-same-boot-monotonic-probe)
fi

"${runner[@]}" &
runner_pid=$!
wait "$runner_pid"
runner_pid=""
