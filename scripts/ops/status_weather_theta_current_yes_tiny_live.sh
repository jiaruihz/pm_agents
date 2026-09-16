#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_DIR="${THETA_CURRENT_YES_RUNTIME_DIR:-runtime/weather_edge_v1/theta_current_yes_tiny_live_v1}"
PID_FILE="$RUNTIME_DIR/loop.pid"
SUMMARY_FILE="$RUNTIME_DIR/latest_summary.json"
TELEMETRY_FILE="$RUNTIME_DIR/forward_telemetry.jsonl"

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
keys = [
    "generated_at_utc",
    "status",
    "snapshot_ts_utc",
    "snapshot_age_min",
    "current_rows",
    "candidate_rows",
    "plans",
    "live_enabled",
    "forward_telemetry_rows",
]
for key in keys:
    if key in data:
        print(f"{key}={data[key]}")
PY
else
  echo "summary=missing"
fi

if [[ -f "$TELEMETRY_FILE" ]]; then
  echo "telemetry=$TELEMETRY_FILE"
  python3 - <<'PY' "$TELEMETRY_FILE"
import collections, json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
statuses = collections.Counter(str(row.get("decision_status", "")) for row in rows)
peak_statuses = collections.Counter(str(row.get("forecast_peak_fetch_status", "")) for row in rows)
created = [str(row.get("created_at_utc", "")) for row in rows if row.get("created_at_utc")]
print(f"telemetry_rows={len(rows)}")
print(f"telemetry_first_created={min(created) if created else ''}")
print(f"telemetry_last_created={max(created) if created else ''}")
print(f"decision_status_counts={dict(statuses)}")
print(f"forecast_peak_fetch_status_counts={dict(peak_statuses)}")
PY
else
  echo "telemetry=missing"
fi
