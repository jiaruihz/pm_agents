#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
INTERVAL_SEC="${INTERVAL_SEC:-1800}"
INITIAL_DELAY_SEC="${INITIAL_DELAY_SEC:-180}"
PROFILE="${WEATHER_BLEND_PROFILE:-all}"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/live_cycle"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

echo "weather_blended_shadow_paper_loop started at $(date -Is), profile=${PROFILE}, initial_delay=${INITIAL_DELAY_SEC}s, interval=${INTERVAL_SEC}s"
sleep "$INITIAL_DELAY_SEC"

while true; do
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="$LOG_DIR/blended_shadow_paper_${PROFILE}_${ts}.log"
  extra_args=()
  if [[ "${WEATHER_BLEND_NO_SYNC:-0}" == "1" ]]; then
    extra_args+=(--no-sync)
  fi
  if [[ "${WEATHER_BLEND_DRY_RUN:-0}" == "1" ]]; then
    extra_args+=(--dry-run)
  fi
  echo "[$(date -Is)] blended shadow/paper start: profile=${PROFILE} ts=${ts}" | tee -a "$LOG_DIR/blended_shadow_paper_${PROFILE}.log"
  if .venv/bin/python scripts/ops/weather_blended_shadow_paper.py \
      --profile "$PROFILE" \
      "${extra_args[@]}" \
      >"$log" 2>&1; then
    echo "[$(date -Is)] blended shadow/paper ok: profile=${PROFILE} ts=${ts} log=$log" | tee -a "$LOG_DIR/blended_shadow_paper_${PROFILE}.log"
  else
    rc=$?
    echo "[$(date -Is)] blended shadow/paper failed rc=$rc: profile=${PROFILE} ts=${ts} log=$log" | tee -a "$LOG_DIR/blended_shadow_paper_${PROFILE}.log"
  fi
  sleep "$INTERVAL_SEC"
done
