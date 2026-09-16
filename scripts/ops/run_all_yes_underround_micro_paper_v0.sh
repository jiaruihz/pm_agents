#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$ROOT}"
DATA_PROJECT_DIR="${DATA_PROJECT_DIR:-$PROJECT_DIR}"
if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [[ -x "$DATA_PROJECT_DIR/.venv/bin/python" ]]; then
  PY="$DATA_PROJECT_DIR/.venv/bin/python"
else
  PY="python3"
fi

RUN_DIR="${RUN_DIR:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/all_yes_underround_paper_v0}"
DB_PATH="${DB_PATH:-$DATA_PROJECT_DIR/runtime/weather.db}"
GATE_PATH="${GATE_PATH:-$DATA_PROJECT_DIR/runtime/_dashboard_logs/clob_fill_coverage_gate.json}"
STATION_BASIS_GATE_PATH="${STATION_BASIS_GATE_PATH:-$DATA_PROJECT_DIR/runtime/weather_edge_v1/station_basis_shadow_v1/live_prep_gate.json}"
WEATHER_PREDICT_DIR="${WEATHER_PREDICT_DIR:-$PROJECT_DIR}"
MICRO_SNAPSHOT_DIR="${MICRO_SNAPSHOT_DIR:-$RUN_DIR/micro_orderbook_snapshots}"
MICRO_SUMMARY_PATH="${MICRO_SUMMARY_PATH:-$RUN_DIR/micro_snapshot_summary.json}"
SCAN_JSON_PATH="${SCAN_JSON_PATH:-$RUN_DIR/latest_scan.json}"
SCAN_MD_PATH="${SCAN_MD_PATH:-$RUN_DIR/latest_scan.md}"
OBSERVATION_SCAN_JSON_PATH="${OBSERVATION_SCAN_JSON_PATH:-$RUN_DIR/latest_observation_scan.json}"
OBSERVATION_SCAN_MD_PATH="${OBSERVATION_SCAN_MD_PATH:-$RUN_DIR/latest_observation_scan.md}"
OBSERVATION_MIN_UNDERROUND="${OBSERVATION_MIN_UNDERROUND:-0.01}"
OBSERVATION_FORMAL_MIN_UNDERROUND="${OBSERVATION_FORMAL_MIN_UNDERROUND:-0.02}"
MICRO_DATES="${MICRO_DATES:-}"
MICRO_DAYS_FORWARD="${MICRO_DAYS_FORWARD:-1}"
MICRO_CITIES="${MICRO_CITIES:-}"
MICRO_CITY_POOL="${MICRO_CITY_POOL:-all}"
MICRO_EVENT_CONCURRENCY="${MICRO_EVENT_CONCURRENCY:-12}"
MICRO_ORDERBOOK_CONCURRENCY="${MICRO_ORDERBOOK_CONCURRENCY:-80}"
MICRO_ORDERBOOK_TIMEOUT_SECONDS="${MICRO_ORDERBOOK_TIMEOUT_SECONDS:-4}"
MICRO_PROXY="${MICRO_PROXY:-}"
SNAPSHOT_SERVICE_NAME="${SNAPSHOT_SERVICE_NAME:-}"
SNAPSHOT_SERVICE_WAIT_SECONDS="${SNAPSHOT_SERVICE_WAIT_SECONDS:-120}"
SNAPSHOT_SERVICE_POLL_SECONDS="${SNAPSHOT_SERVICE_POLL_SECONDS:-5}"
MAX_SNAPSHOT_AGE_SECONDS="${MAX_SNAPSHOT_AGE_SECONDS:-180}"
MIN_FILE_STABLE_SECONDS="${MIN_FILE_STABLE_SECONDS:-0}"
MIN_SNAPSHOT_ROWS="${MIN_SNAPSHOT_ROWS:-100}"

cd "$PROJECT_DIR"
mkdir -p "$RUN_DIR"

if [[ -n "$SNAPSHOT_SERVICE_NAME" ]]; then
  service_status="$(systemctl --user is-active "$SNAPSHOT_SERVICE_NAME" 2>/dev/null || true)"
  case "$service_status" in
    active|activating|reloading|deactivating)
      wait_start_epoch="$(date +%s)"
      wait_deadline=$((wait_start_epoch + SNAPSHOT_SERVICE_WAIT_SECONDS))
      waited_seconds=0
      while [[ "$SNAPSHOT_SERVICE_WAIT_SECONDS" -gt 0 && "$(date +%s)" -lt "$wait_deadline" ]]; do
        sleep "$SNAPSHOT_SERVICE_POLL_SECONDS"
        waited_seconds=$(( $(date +%s) - wait_start_epoch ))
        service_status="$(systemctl --user is-active "$SNAPSHOT_SERVICE_NAME" 2>/dev/null || true)"
        case "$service_status" in
          active|activating|reloading|deactivating)
            ;;
          *)
            break
            ;;
        esac
      done
      case "$service_status" in
        active|activating|reloading|deactivating)
          ;;
        *)
          echo "snapshot service ${SNAPSHOT_SERVICE_NAME} cleared after ${waited_seconds}s status=${service_status}; continuing micro capture"
          ;;
      esac
      case "$service_status" in
        active|activating|reloading|deactivating)
      "$PY" - "$RUN_DIR" "$SNAPSHOT_SERVICE_NAME" "$service_status" "$waited_seconds" "$SNAPSHOT_SERVICE_WAIT_SECONDS" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

run_dir = Path(sys.argv[1])
service_name = sys.argv[2]
service_status = sys.argv[3]
waited_seconds = int(float(sys.argv[4]))
wait_limit_seconds = int(float(sys.argv[5]))
run_dir.mkdir(parents=True, exist_ok=True)
result = {
    "command": "micro_paper_cycle",
    "executed_cycle": False,
    "fresh": False,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "live_now": False,
    "reason": "snapshot_service_running",
    "snapshot_service_name": service_name,
    "snapshot_service_running": True,
    "snapshot_service_status": service_status,
    "snapshot_service_waited_seconds": waited_seconds,
    "snapshot_service_wait_limit_seconds": wait_limit_seconds,
    "verdict": "MICRO_SNAPSHOT_SKIP_CYCLE",
}
(run_dir / "fresh_cycle.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2, sort_keys=True))
PY
      "$PY" scripts/ops/all_yes_underround_live_plan_v0.py plan \
        --scan-json "$SCAN_JSON_PATH" \
        --run-dir "$RUN_DIR" \
        --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_to_executor_plans_v0.py convert \
        --plan-json "$RUN_DIR/latest_live_plan.json" \
        --out-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
        --summary-out "$RUN_DIR/executor_trade_plan_summary.json" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_executor_state_v0.py check \
        --plan-json "$RUN_DIR/latest_live_plan.json" \
        --run-dir "$RUN_DIR" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_basket_executor_v0.py check \
        --plan-json "$RUN_DIR/latest_live_plan.json" \
        --executor-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
        --run-dir "$RUN_DIR" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_fok_executor_v0.py check \
        --plan-json "$RUN_DIR/latest_live_plan.json" \
        --executor-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
        --run-dir "$RUN_DIR" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_paper_exec_v0.py monitor \
        --db-path "$DB_PATH" \
        --gate-path "$GATE_PATH" \
        --run-dir "$RUN_DIR" \
        --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" >/dev/null || true
      "$PY" scripts/ops/all_yes_underround_observation_v0.py monitor \
        --run-dir "$RUN_DIR" >/dev/null || true
      exit 0
      ;;
      esac
      ;;
  esac
fi

micro_args=(
  --weather-predict-dir "$WEATHER_PREDICT_DIR"
  --snapshot-dir "$MICRO_SNAPSHOT_DIR"
  --days-forward "$MICRO_DAYS_FORWARD"
  --city-pool "$MICRO_CITY_POOL"
  --event-concurrency "$MICRO_EVENT_CONCURRENCY"
  --orderbook-concurrency "$MICRO_ORDERBOOK_CONCURRENCY"
  --orderbook-timeout-seconds "$MICRO_ORDERBOOK_TIMEOUT_SECONDS"
  --summary-out "$MICRO_SUMMARY_PATH"
)
if [[ -n "$MICRO_PROXY" ]]; then
  micro_args+=(--proxy "$MICRO_PROXY")
fi
if [[ -n "$MICRO_DATES" ]]; then
  micro_args+=(--dates "$MICRO_DATES")
fi
if [[ -n "$MICRO_CITIES" ]]; then
  micro_args+=(--cities "$MICRO_CITIES")
fi

"$PY" scripts/ops/all_yes_underround_micro_snapshot_v0.py "${micro_args[@]}"

snapshot_path="$("$PY" - "$MICRO_SUMMARY_PATH" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1]))["out"])
PY
)"

"$PY" scripts/ops/all_yes_underround_fresh_paper_cycle_v0.py \
  --snapshot-path "$snapshot_path" \
  --run-dir "$RUN_DIR" \
  --db-path "$DB_PATH" \
  --gate-path "$GATE_PATH" \
  --station-basis-gate-path "$STATION_BASIS_GATE_PATH" \
  --scan-json "$SCAN_JSON_PATH" \
  --scan-md "$SCAN_MD_PATH" \
  --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" \
  --min-file-stable-seconds "$MIN_FILE_STABLE_SECONDS" \
  --min-snapshot-rows "$MIN_SNAPSHOT_ROWS"

"$PY" scripts/analysis/market_structure_edge/research_all_yes_underround_live_prep_v0.py \
  --snapshot-path "$snapshot_path" \
  --db-path "$DB_PATH" \
  --gate-path "$GATE_PATH" \
  --station-basis-gate-path "$STATION_BASIS_GATE_PATH" \
  --paper-gate-path "$RUN_DIR/live_prep_gate.json" \
  --paper-monitor-path "$RUN_DIR/monitor.json" \
  --fresh-cycle-path "$RUN_DIR/fresh_cycle.json" \
  --out-json "$OBSERVATION_SCAN_JSON_PATH" \
  --out-md "$OBSERVATION_SCAN_MD_PATH" \
  --min-underround "$OBSERVATION_MIN_UNDERROUND" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_observation_v0.py cycle \
  --scan-json "$OBSERVATION_SCAN_JSON_PATH" \
  --run-dir "$RUN_DIR" \
  --min-underround "$OBSERVATION_MIN_UNDERROUND" \
  --formal-min-underround "$OBSERVATION_FORMAL_MIN_UNDERROUND" \
  --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_observation_v0.py monitor \
  --run-dir "$RUN_DIR" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_live_plan_v0.py plan \
  --scan-json "$SCAN_JSON_PATH" \
  --run-dir "$RUN_DIR" \
  --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_to_executor_plans_v0.py convert \
  --plan-json "$RUN_DIR/latest_live_plan.json" \
  --out-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
  --summary-out "$RUN_DIR/executor_trade_plan_summary.json" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_executor_state_v0.py check \
  --plan-json "$RUN_DIR/latest_live_plan.json" \
  --run-dir "$RUN_DIR" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_basket_executor_v0.py check \
  --plan-json "$RUN_DIR/latest_live_plan.json" \
  --executor-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
  --run-dir "$RUN_DIR" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_fok_executor_v0.py check \
  --plan-json "$RUN_DIR/latest_live_plan.json" \
  --executor-jsonl "$RUN_DIR/executor_trade_plans.jsonl" \
  --run-dir "$RUN_DIR" >/dev/null || true

"$PY" scripts/ops/all_yes_underround_paper_exec_v0.py monitor \
  --db-path "$DB_PATH" \
  --gate-path "$GATE_PATH" \
  --run-dir "$RUN_DIR" \
  --max-snapshot-age-seconds "$MAX_SNAPSHOT_AGE_SECONDS" >/dev/null || true
