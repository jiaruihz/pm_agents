#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="${LOW_PRICE_YES_LOTTERY_RUNTIME_DIR:-runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/loop.pid"
SUMMARY_FILE="$RUNTIME_DIR/latest_summary.json"
SHADOW_FILE="$RUNTIME_DIR/shadow_decisions.jsonl"
LIVE_FILE="runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"

if [[ -s "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "process=running pid=$pid"
  else
    echo "process=stale_pid pid=$pid"
  fi
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
    "effective_min_event_date",
    "decision_count",
    "planned_count",
    "blocked_count",
    "planned_notional_usd",
    "live_enabled",
    "live_requested",
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

if [[ -f "$LIVE_FILE" ]]; then
  echo "live=$LIVE_FILE"
  python3 - <<'PY' "$LIVE_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("status", "")) for row in rows)
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"live_rows={len(rows)}")
print(f"live_first_created={min(created) if created else ''}")
print(f"live_last_created={max(created) if created else ''}")
print(f"live_status_counts={dict(statuses)}")
PY
else
  echo "live=missing"
fi
