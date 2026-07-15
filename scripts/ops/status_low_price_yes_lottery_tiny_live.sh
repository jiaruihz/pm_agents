#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="${LOW_PRICE_YES_LOTTERY_RUNTIME_DIR:-runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/loop.pid"
SUMMARY_FILE="$RUNTIME_DIR/latest_summary.json"
SHADOW_FILE="$RUNTIME_DIR/shadow_decisions.jsonl"
WOULD_LIVE_FILE="$RUNTIME_DIR/would_live_entries.jsonl"
LIVE_FILE="runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"

if [[ -s "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "process=running pid=$pid"
  else
    pids="$(pgrep -f 'scripts/ops/low_price_yes_lottery_tiny_live.py loop' || true)"
    if [[ -n "$pids" ]]; then
      echo "process=running pids=$(echo "$pids" | tr '\n' ',' | sed 's/,$//') stale_pid=$pid"
    else
      echo "process=stale_pid pid=$pid"
    fi
  fi
else
  pids="$(pgrep -f 'scripts/ops/low_price_yes_lottery_tiny_live.py loop' || true)"
  if [[ -n "$pids" ]]; then
    echo "process=running pids=$(echo "$pids" | tr '\n' ',' | sed 's/,$//')"
  else
    echo "process=not_running"
  fi
fi

if [[ -f "$SUMMARY_FILE" ]]; then
  echo "summary=$SUMMARY_FILE"
  python3 - <<'PY' "$SUMMARY_FILE"
import json, sys
data = json.load(open(sys.argv[1]))
for key in [
    "generated_at_utc",
    "status",
    "effective_min_event_date",
    "decision_count",
    "planned_count",
    "would_live_entry_count",
    "blocked_count",
    "planned_notional_usd",
    "sizing_policy",
    "live_enabled",
    "live_requested",
    "tail_telemetry_status_counts",
    "tail_telemetry_model_artifact",
]:
    if key in data:
        print(f"{key}={data[key]}")
print(f"blocker_counts={data.get('blocker_counts', {})}")
executor = data.get("executor_result") or {}
if executor:
    print(f"executor_live_written={executor.get('live_written')}")
    print(f"executor_live_errors={executor.get('live_errors')}")
PY
else
  echo "summary=missing"
fi

if [[ -f "$SHADOW_FILE" ]]; then
  echo "shadow=$SHADOW_FILE"
  python3 - <<'PY' "$SHADOW_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("decision_status", "")) for row in rows)
blockers = collections.Counter(str(row.get("blocker", "")) for row in rows if row.get("blocker"))
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"shadow_rows={len(rows)}")
print(f"shadow_first_created={min(created) if created else ''}")
print(f"shadow_last_created={max(created) if created else ''}")
print(f"decision_status_counts={dict(statuses)}")
print(f"blocker_counts={dict(blockers)}")
PY
else
  echo "shadow=missing"
fi

if [[ -f "$WOULD_LIVE_FILE" ]]; then
  echo "would_live=$WOULD_LIVE_FILE"
  python3 - <<'PY' "$WOULD_LIVE_FILE"
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
print(f"would_live_rows={len(rows)}")
for row in rows[-5:]:
    print(
        "  "
        f"{row.get('created_at_utc','')} "
        f"{row.get('target_date','')} {row.get('city','')} {row.get('bracket','')} "
        f"ask={row.get('would_live_best_ask')} "
        f"ask_size={row.get('would_live_best_ask_size')} "
        f"maker={row.get('would_live_maker_limit_price')} "
        f"shares={row.get('would_live_shares')} "
        f"notional={row.get('would_live_notional_usd')}"
    )
PY
else
  echo "would_live=missing (no eligible shadow entry yet)"
fi

if [[ -f "$LIVE_FILE" ]]; then
  echo "live=$LIVE_FILE"
  python3 - <<'PY' "$LIVE_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("status", "")) for row in rows)
sizing = collections.Counter(str(row.get("sizing_mode") or row.get("live_sizing_policy") or "unknown") for row in rows)
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"live_rows={len(rows)}")
print(f"live_first_created={min(created) if created else ''}")
print(f"live_last_created={max(created) if created else ''}")
print(f"live_status_counts={dict(statuses)}")
print(f"live_sizing_counts={dict(sizing)}")
print("recent_live_orders=")
for row in rows[-5:]:
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = response.get("place") if isinstance(response.get("place"), dict) else {}
    order_id = row.get("order_id") or place.get("orderID") or place.get("order_id") or ""
    order_id_short = str(order_id)[:10] if order_id else ""
    print(
        "  "
        f"{row.get('created_at_utc','')} "
        f"{row.get('target_date','')} {row.get('city','')} {row.get('bracket','')} "
        f"status={row.get('status','')} "
        f"sizing={row.get('sizing_mode') or row.get('live_sizing_policy') or 'unknown'} "
        f"price={row.get('limit_price') or row.get('posted_price') or row.get('price')} "
        f"shares={row.get('fixed_order_shares') or row.get('size')} "
        f"notional={row.get('notional') or row.get('posted_notional')} "
        f"clob_status={place.get('status','')} "
        f"order={order_id_short}"
    )
PY
else
  echo "live=missing"
fi
