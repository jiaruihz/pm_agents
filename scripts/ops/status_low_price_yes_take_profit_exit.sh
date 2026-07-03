#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="runtime/weather_edge_v1/low_price_yes_take_profit_exit_v1"
SUMMARY_FILE="$RUNTIME_DIR/latest_summary.json"
DECISIONS_FILE="$RUNTIME_DIR/exit_decisions.jsonl"
PLAN_FILE="$RUNTIME_DIR/trade_plans.jsonl"
LIVE_FILE="runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders.jsonl"
CANCEL_FILE="runtime/weather_edge_v1/live/low_price_yes_take_profit_exit_v1_orders_cancels.jsonl"

pids="$(pgrep -f 'scripts/ops/low_price_yes_take_profit_exit_v1.py loop' || true)"
if [[ -n "$pids" ]]; then
  echo "process=running pids=$(echo "$pids" | tr '\n' ',' | sed 's/,$//')"
else
  echo "process=not_running"
fi

if [[ -f "$SUMMARY_FILE" ]]; then
  echo "summary=$SUMMARY_FILE"
  python3 - <<'PY' "$SUMMARY_FILE"
import json, sys
data = json.load(open(sys.argv[1]))
for key in [
    "generated_at_utc",
    "status",
    "entry_submitted_buy_tokens",
    "position_rows_matched_to_entry_tokens",
    "decision_count",
    "planned_count",
    "live_enabled",
    "take_profit_bid",
    "maker_ttl_seconds",
    "enable_taker_fallback",
    "status_counts",
    "blocker_counts",
    "role_counts",
]:
    if key in data:
        print(f"{key}={data[key]}")
executor = data.get("executor_result") or {}
if executor:
    for key in [
        "plans_read",
        "paper_written",
        "live_written",
        "live_errors",
        "cancel_expired_checked",
        "expired_seen",
        "cancel_attempted",
        "cancel_written",
        "cancel_errors",
    ]:
        if key in executor:
            print(f"executor_{key}={executor[key]}")
PY
else
  echo "summary=missing"
fi

if [[ -f "$DECISIONS_FILE" ]]; then
  echo "decisions=$DECISIONS_FILE"
  python3 - <<'PY' "$DECISIONS_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("decision_status", "")) for row in rows)
blockers = collections.Counter(str(row.get("blocker", "")) for row in rows if row.get("blocker"))
roles = collections.Counter(str(row.get("planned_role", "")) for row in rows if row.get("planned_role"))
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"decision_rows={len(rows)}")
print(f"decision_first_created={min(created) if created else ''}")
print(f"decision_last_created={max(created) if created else ''}")
print(f"decision_status_counts={dict(statuses)}")
print(f"decision_blocker_counts={dict(blockers)}")
print(f"decision_role_counts={dict(roles)}")
PY
else
  echo "decisions=missing"
fi

if [[ -f "$PLAN_FILE" ]]; then
  echo "plans=$PLAN_FILE"
  python3 - <<'PY' "$PLAN_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
roles = collections.Counter(str(row.get("child_order_role", "")) for row in rows)
print(f"plan_rows={len(rows)}")
print(f"plan_role_counts={dict(roles)}")
PY
else
  echo "plans=missing"
fi

if [[ -f "$LIVE_FILE" ]]; then
  echo "live=$LIVE_FILE"
  python3 - <<'PY' "$LIVE_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("status", "")) for row in rows)
roles = collections.Counter(str(row.get("child_order_role", "")) for row in rows)
place = collections.Counter(str(((row.get("exchange_response") or {}).get("place") or {}).get("status", "")) for row in rows)
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"live_rows={len(rows)}")
print(f"live_first_created={min(created) if created else ''}")
print(f"live_last_created={max(created) if created else ''}")
print(f"live_status_counts={dict(statuses)}")
print(f"live_role_counts={dict(roles)}")
print(f"live_place_status_counts={dict(place)}")
PY
else
  echo "live=missing"
fi

if [[ -f "$CANCEL_FILE" ]]; then
  echo "cancels=$CANCEL_FILE"
  python3 - <<'PY' "$CANCEL_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("status", "")) for row in rows)
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"cancel_rows={len(rows)}")
print(f"cancel_first_created={min(created) if created else ''}")
print(f"cancel_last_created={max(created) if created else ''}")
print(f"cancel_status_counts={dict(statuses)}")
PY
else
  echo "cancels=missing"
fi
