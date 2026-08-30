#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PRODUCTION_SPEC="${WEATHER_PRODUCTION_CONFIG:?controller must inject WEATHER_PRODUCTION_CONFIG}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
source "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh"

RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
TMUX_SESSION="${WEATHER_CROSS_NO_V2_METAR_TMUX_SESSION:-weather_cross_no_v2_metar_v1}"
OUTPUT_DIR="${WEATHER_CROSS_NO_V2_METAR_OUTPUT_DIR:-$RUNTIME_ROOT/output/cross_no_v2_metar_v1}"
EVIDENCE_DB="${WEATHER_CROSS_NO_V2_METAR_EVIDENCE_DB:?WEATHER_CROSS_NO_V2_METAR_EVIDENCE_DB is required}"
MARKET_BOOKS_LATEST="${WEATHER_CROSS_NO_V2_METAR_MARKET_BOOKS_LATEST:-$RUNTIME_ROOT/market_books/latest.json}"
PAUSE_FILE="${WEATHER_CROSS_NO_V2_METAR_PAUSE_FILE:-$OUTPUT_DIR/PAUSE}"
INTERVAL_SEC="${WEATHER_CROSS_NO_V2_METAR_INTERVAL_SEC:-0.5}"
MAX_SOURCE_AGE_SEC="${WEATHER_CROSS_NO_V2_METAR_MAX_SOURCE_AGE_SEC:-30}"
BOOK_TIMEOUT_SEC="${WEATHER_CROSS_NO_V2_METAR_BOOK_TIMEOUT_SEC:-5}"
STOP_AFTER_SEC="${WEATHER_CROSS_NO_V2_METAR_STOP_AFTER_SEC:-86400}"
ENABLE_LIVE="${WEATHER_CROSS_NO_V2_METAR_LIVE:-0}"
CONFIRM_LIVE="${WEATHER_CROSS_NO_V2_METAR_CONFIRM_LIVE:-0}"
ALLOW_CLOCK_OVERRIDE="${WEATHER_CROSS_NO_V2_METAR_ALLOW_CLOCK_INVALID_SAME_BOOT_MONOTONIC_PROBE:-0}"
LOG_FILE="${WEATHER_CROSS_NO_V2_METAR_LOG_FILE:-$RUNTIME_ROOT/loop/cross_no_v2_metar_v1.log}"
PID_FILE="${WEATHER_CROSS_NO_V2_METAR_PID_FILE:-$RUNTIME_ROOT/loop/cross_no_v2_metar_v1.pid}"
MARKET_PROXY="$(weather_resolve_market_proxy "$PROJECT_DIR")"
CODE_IDENTITY="$(git -C "$PROJECT_DIR" rev-parse HEAD)"

[[ -f "$EVIDENCE_DB" ]] || { echo "missing evidence DB: $EVIDENCE_DB" >&2; exit 1; }
[[ -f "$MARKET_BOOKS_LATEST" ]] || { echo "missing market books: $MARKET_BOOKS_LATEST" >&2; exit 1; }
if [[ "$ENABLE_LIVE" == "1" && "$CONFIRM_LIVE" != "1" ]]; then
  echo "live mode requires WEATHER_CROSS_NO_V2_METAR_CONFIRM_LIVE=1" >&2
  exit 1
fi

# A live entrypoint must prove that it is the exact immutable release and
# launch environment registered in the operational production contract.  The
# controller remains the only authority allowed to supply this environment.
"$PROJECT_DIR/.venv/bin/python" - \
  "$PROJECT_DIR" "$PRODUCTION_SPEC" "$CODE_IDENTITY" \
  "$EVIDENCE_DB" "$MARKET_BOOKS_LATEST" "$OUTPUT_DIR" "$PAUSE_FILE" \
  "$INTERVAL_SEC" "$MAX_SOURCE_AGE_SEC" "$BOOK_TIMEOUT_SEC" "$STOP_AFTER_SEC" \
  "$ENABLE_LIVE" "$CONFIRM_LIVE" "$ALLOW_CLOCK_OVERRIDE" <<'PY'
import subprocess
import sys
from pathlib import Path

import yaml

(
    project_dir,
    production_spec,
    code_identity,
    evidence_db,
    market_books_latest,
    output_dir,
    pause_file,
    interval_sec,
    max_source_age_sec,
    book_timeout_sec,
    stop_after_sec,
    enable_live,
    confirm_live,
    allow_clock_override,
) = sys.argv[1:]

payload = yaml.safe_load(Path(production_spec).read_text(encoding="utf-8"))
releases = {
    str(row.get("release_id")): row
    for row in payload.get("production_releases", [])
    if isinstance(row, dict)
}
runtimes = {
    str(row.get("instance_id")): row
    for row in payload.get("managed_runtimes", [])
    if isinstance(row, dict)
}
release = releases.get("cross_no_v2_metar")
runtime = runtimes.get("cross_no_v2_metar_v1")
if release is None or runtime is None:
    raise SystemExit("cross_no_v2_metar is not registered in production contract")
expected_sha = str(release.get("expected_repo_sha") or "")
observed_sha = subprocess.check_output(
    ["git", "-C", project_dir, "rev-parse", "HEAD"], text=True
).strip()
if code_identity != observed_sha or observed_sha != expected_sha:
    raise SystemExit("loaded release SHA does not match production contract")
if Path(project_dir).resolve() != Path(str(release.get("checkout_root") or "")).resolve():
    raise SystemExit("loaded checkout root does not match production contract")
if runtime.get("release_id") != "cross_no_v2_metar":
    raise SystemExit("runtime release_id mismatch")
if runtime.get("execution_mode") != "live" or runtime.get("expected_live") is not True:
    raise SystemExit("runtime is not registered as expected live")
if runtime.get("start_script") != "scripts/ops/start_weather_cross_no_v2_metar.sh":
    raise SystemExit("runtime start_script mismatch")

actual = {
    "WEATHER_CROSS_NO_V2_METAR_EVIDENCE_DB": evidence_db,
    "WEATHER_CROSS_NO_V2_METAR_MARKET_BOOKS_LATEST": market_books_latest,
    "WEATHER_CROSS_NO_V2_METAR_OUTPUT_DIR": output_dir,
    "WEATHER_CROSS_NO_V2_METAR_PAUSE_FILE": pause_file,
    "WEATHER_CROSS_NO_V2_METAR_INTERVAL_SEC": interval_sec,
    "WEATHER_CROSS_NO_V2_METAR_MAX_SOURCE_AGE_SEC": max_source_age_sec,
    "WEATHER_CROSS_NO_V2_METAR_BOOK_TIMEOUT_SEC": book_timeout_sec,
    "WEATHER_CROSS_NO_V2_METAR_STOP_AFTER_SEC": stop_after_sec,
    "WEATHER_CROSS_NO_V2_METAR_LIVE": enable_live,
    "WEATHER_CROSS_NO_V2_METAR_CONFIRM_LIVE": confirm_live,
    "WEATHER_CROSS_NO_V2_METAR_ALLOW_CLOCK_INVALID_SAME_BOOT_MONOTONIC_PROBE": allow_clock_override,
}
registered = runtime.get("launch_environment") or {}
for key, value in actual.items():
    if str(registered.get(key, "")) != str(value):
        raise SystemExit(f"launch environment mismatch: {key}")
PY

PRESTART_MANIFEST="$(mktemp /tmp/cross_no_v2_metar_prestart.XXXXXX.json)"
trap 'rm -f "$PRESTART_MANIFEST"' EXIT
set +e
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/ops/weather_production_manifest.py" \
  --production-spec "$PRODUCTION_SPEC" \
  --allow-missing-session "$TMUX_SESSION" \
  --json-out "$PRESTART_MANIFEST" \
  --strict >/dev/null
MANIFEST_RC=$?
set -e
if [[ "$MANIFEST_RC" -ne 0 ]]; then
  # Immediately before this process exists, its own health file is allowed to
  # be unreadable. No other production critical is tolerated.
  "$PROJECT_DIR/.venv/bin/python" - "$PRESTART_MANIFEST" "$PROJECT_DIR" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from scripts.ops.weather_cross_no_v2_metar import validate_isolated_prestart_manifest

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
try:
    validate_isolated_prestart_manifest(payload)
except ValueError as exc:
    raise SystemExit(str(exc)) from exc
PY
fi

cmd=(
  "$PROJECT_DIR/.venv/bin/python"
  "$PROJECT_DIR/scripts/ops/weather_cross_no_v2_metar.py"
  --loop
  --evidence-db "$EVIDENCE_DB"
  --market-books-latest "$MARKET_BOOKS_LATEST"
  --output-dir "$OUTPUT_DIR"
  --pause-file "$PAUSE_FILE"
  --interval-sec "$INTERVAL_SEC"
  --max-source-age-sec "$MAX_SOURCE_AGE_SEC"
  --book-timeout-sec "$BOOK_TIMEOUT_SEC"
  --stop-after-sec "$STOP_AFTER_SEC"
  --official-fee-rate 0.05
  --code-identity "$CODE_IDENTITY"
)
if [[ -n "$MARKET_PROXY" ]]; then
  cmd+=(--market-proxy "$MARKET_PROXY")
fi
if [[ "$ENABLE_LIVE" == "1" ]]; then
  cmd+=(--live)
fi
if [[ "$CONFIRM_LIVE" == "1" ]]; then
  cmd+=(--confirm-live)
fi
if [[ "$ALLOW_CLOCK_OVERRIDE" == "1" ]]; then
  cmd+=(--allow-clock-invalid-same-boot-monotonic-probe)
fi

printf -v quoted_cmd '%q ' "${cmd[@]}"
printf -v session_cmd \
  'set -eu; mkdir -p %q %q; cd %q; set -a; [[ -f .env ]] && source .env || true; set +a; source %q; weather_export_market_proxy_env %q; export PYTHONPATH=%q; exec %s >> %q 2>&1' \
  "$RUNTIME_ROOT/loop" "$OUTPUT_DIR" "$PROJECT_DIR" \
  "$PROJECT_DIR/scripts/ops/weather_market_proxy_env.sh" "$MARKET_PROXY" \
  "$PROJECT_DIR" "$quoted_cmd" "$LOG_FILE"

weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$TMUX_SESSION" "$session_cmd"
printf -v marker_command "printf 'tmux:%%s\\n' %q > %q" "$TMUX_SESSION" "$PID_FILE"
weather_jrs_tmux_exec_checked "$TMUX_SOCKET" "cross_no_v2_metar_pid_marker" "$marker_command"

for _ in {1..30}; do
  if [[ -f "$OUTPUT_DIR/latest.json" ]] \
    && "$PROJECT_DIR/.venv/bin/python" - "$OUTPUT_DIR/latest.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if payload.get("status") == "ok" else 1)
PY
  then
    break
  fi
  sleep 1
done

"$PROJECT_DIR/.venv/bin/python" - "$OUTPUT_DIR/latest.json" "$ENABLE_LIVE" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload.get("status") == "ok", payload
assert bool(payload.get("live_enabled")) is (sys.argv[2] == "1"), payload
assert payload.get("shares_per_order") == 5.0, payload
assert payload.get("max_orders_per_utc_day") == 5, payload
assert payload.get("max_daily_principal_usd") == 25.0, payload
PY

echo "started cross_no_v2_metar_v1 tmux_socket=$TMUX_SOCKET session=$TMUX_SESSION"
echo "execution=5_share_taker_only max_orders_per_utc_day=5 max_daily_principal_usd=25"
echo "live=$ENABLE_LIVE confirm_live=$CONFIRM_LIVE clock_override=$ALLOW_CLOCK_OVERRIDE"
echo "evidence_db=$EVIDENCE_DB output_dir=$OUTPUT_DIR pause_file=$PAUSE_FILE"
