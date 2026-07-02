#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
UID_NUM="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
DATA_FEED_LABEL="com.pm-agents.weather-data-feed"
SHADOW_LABEL="com.pm-agents.regime-routed-no-shadow"
LOW_PRICE_LABEL="com.pm-agents.low-price-yes-lottery-live"
DATA_FEED_PLIST="$LAUNCH_DIR/$DATA_FEED_LABEL.plist"
SHADOW_PLIST="$LAUNCH_DIR/$SHADOW_LABEL.plist"
LOW_PRICE_PLIST="$LAUNCH_DIR/$LOW_PRICE_LABEL.plist"
DATA_FEED_RUNTIME="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$HOME/projects/weather_data_feed_service_runtime}"
DATA_FEED_SNAPSHOT_DIR="$DATA_FEED_RUNTIME/targeted_output/paper_snapshots"
DATA_FEED_OBS="$DATA_FEED_RUNTIME/output/observations/latest.json"
REGIME_RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1"
DATA_FEED_SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-$HOME/projects/weather_data_feed_service}"
LOW_PRICE_RUNTIME="$PROJECT_DIR/runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1"

usage() {
  cat <<'EOF'
Usage: scripts/ops/mac_weather_stack.sh <command>

Commands:
  install-launchagents   Write Mac LaunchAgent plists for data-feed and strategy shadow
  start                  Start data-feed + strategy shadow
  stop                   Stop data-feed + strategy shadow
  restart                Restart data-feed + strategy shadow
  status                 Print process, proxy, snapshot, observation, and strategy state
  verify                 Fail if required Mac shadow stack evidence is missing/stale
  proxy-status           Print mihomo selected proxy and N100 reverse tunnel status
  proxy-failover         Probe Gamma and switch to a healthy configured 1x node
  start-live --confirm-live
                         Start real regime-routed NO tiny-live runner explicitly
  stop-live              Stop real regime-routed NO tiny-live runner
  start-low-price-live --confirm-live
                         Start real low-price YES lottery tiny-live runner explicitly
  stop-low-price-live    Stop real low-price YES lottery tiny-live runner

Notes:
  - "start" does not place live orders. It starts data-feed and shadow only.
  - Real orders require "start-live --confirm-live" or
    "start-low-price-live --confirm-live".
EOF
}

write_launchagents() {
  mkdir -p "$LAUNCH_DIR" "$DATA_FEED_RUNTIME/loop" "$REGIME_RUNTIME" "$LOW_PRICE_RUNTIME"
  cat >"$DATA_FEED_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DATA_FEED_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>MAC_WEATHER_DATA_FEED_LOOP_CHILD=1</string>
    <string>$PROJECT_DIR/scripts/ops/start_mac_weather_data_feed_loop.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DATA_FEED_RUNTIME/loop/data_feed_launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DATA_FEED_RUNTIME/loop/data_feed_launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
</dict>
</plist>
EOF
  cat >"$SHADOW_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SHADOW_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>MAC_REGIME_ROUTED_NO_SHADOW_CHILD=1</string>
    <string>$PROJECT_DIR/scripts/ops/start_mac_regime_routed_no_shadow_loop.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REGIME_RUNTIME/shadow_launchd.out.log</string>
  <key>StandardErrorPath</key><string>$REGIME_RUNTIME/shadow_launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
</dict>
</plist>
EOF
  cat >"$LOW_PRICE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LOW_PRICE_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>LOW_PRICE_YES_LOTTERY_LOOP_CHILD=1</string>
    <string>LOW_PRICE_YES_LOTTERY_LIVE=1</string>
    <string>LOW_PRICE_YES_LOTTERY_CONFIRM_LIVE=1</string>
    <string>LOW_PRICE_YES_LOTTERY_NOTIONAL=1.5</string>
    <string>LOW_PRICE_YES_LOTTERY_INTERVAL_SECONDS=300</string>
    <string>$PROJECT_DIR/scripts/ops/start_low_price_yes_lottery_tiny_live.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOW_PRICE_RUNTIME/low_price_launchd.out.log</string>
  <key>StandardErrorPath</key><string>$LOW_PRICE_RUNTIME/low_price_launchd.err.log</string>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
</dict>
</plist>
EOF
}

bootout_label() {
  local label="$1"
  launchctl bootout "gui/$UID_NUM" "$LAUNCH_DIR/$label.plist" >/dev/null 2>&1 || true
}

bootstrap_label() {
  local label="$1"
  launchctl bootstrap "gui/$UID_NUM" "$LAUNCH_DIR/$label.plist"
}

print_proxy_status() {
  python3 - "$DATA_FEED_SERVICE_DIR/.env" <<'PY'
import json, os, subprocess, sys, urllib.request

def read_env(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out

env_file = sys.argv[1]
env = read_env(env_file)
market_proxy = (
    os.environ.get("WEATHER_DATA_FEED_MARKET_PROXY")
    or os.environ.get("WEATHER_PREDICT_MARKET_PROXY")
    or env.get("WEATHER_DATA_FEED_MARKET_PROXY")
    or env.get("WEATHER_PREDICT_MARKET_PROXY")
    or "unset"
)
print("market_proxy=" + market_proxy)
try:
    with urllib.request.urlopen("http://127.0.0.1:9090/proxies", timeout=5) as r:
        proxies = json.load(r)["proxies"]
    print("mihomo_TAGSS=" + str(proxies.get("🙂 TAGSS", {}).get("now")))
except Exception as exc:
    print(f"mihomo_TAGSS=ERROR:{type(exc).__name__}:{exc}")
try:
    out = subprocess.check_output(["pgrep", "-fl", "127\\.0\\.0\\.1:18089:"], text=True)
    print("n100_reverse_tunnel=present")
    print(out.strip())
except Exception:
    print("n100_reverse_tunnel=missing")
PY
}

print_json_state() {
  python3 - "$DATA_FEED_SNAPSHOT_DIR" "$DATA_FEED_OBS" "$REGIME_RUNTIME/latest_summary.json" <<'PY'
import glob, json, os, sys
from datetime import datetime, timezone

snapshot_dir, obs_path, summary_path = sys.argv[1:]
now = datetime.now(timezone.utc)

snapshots = sorted(glob.glob(os.path.join(snapshot_dir, "snapshot_*.json")))
print("latest_snapshot=" + (snapshots[-1] if snapshots else "NONE"))
if snapshots:
    with open(snapshots[-1]) as f:
        payload = json.load(f)
    ts = payload.get("ts_utc")
    age = None
    if ts:
        age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60
    print(f"snapshot_rows={len(payload.get('records', []))}")
    print(f"snapshot_ts_utc={ts}")
    print(f"snapshot_age_min={age:.1f}" if age is not None else "snapshot_age_min=unknown")
if os.path.exists(obs_path):
    with open(obs_path) as f:
        obs = json.load(f)
    print("obs_summary=" + json.dumps(obs.get("summary", {}), sort_keys=True))
else:
    print("obs_summary=missing")
if os.path.exists(summary_path):
    with open(summary_path) as f:
        summary = json.load(f)
    keys = ["generated_at_utc", "live_enabled", "routed_candidates", "execution_eligible", "plans_written", "skip_reasons"]
    print("strategy_summary=" + json.dumps({k: summary.get(k) for k in keys}, sort_keys=True))
else:
    print("strategy_summary=missing")
PY
}

status() {
  echo "== launchctl =="
  launchctl list | grep -E 'weather-data-feed|regime-routed-no-shadow|low-price-yes-lottery-live|weather-api|weather-fe' || true
  echo "== proxy =="
  print_proxy_status
  echo "== data/strategy =="
  print_json_state
  echo "== live runner =="
  if [[ -f "$REGIME_RUNTIME/loop.pid" ]]; then
    local pid
    pid="$(cat "$REGIME_RUNTIME/loop.pid" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "live_runner=running pid=$pid"
    else
      echo "live_runner=stale_pid"
    fi
  else
    echo "live_runner=not_running"
  fi
  echo "== low price YES lottery live runner =="
  "$PROJECT_DIR/scripts/ops/status_low_price_yes_lottery_tiny_live.sh" || true
}

verify() {
  status
  python3 - "$DATA_FEED_SNAPSHOT_DIR" "$DATA_FEED_OBS" "$REGIME_RUNTIME/latest_summary.json" <<'PY'
import glob, json, os, sys
from datetime import datetime, timezone
snapshot_dir, obs_path, summary_path = sys.argv[1:]
now = datetime.now(timezone.utc)
errors = []
snapshots = sorted(glob.glob(os.path.join(snapshot_dir, "snapshot_*.json")))
if not snapshots:
    errors.append("missing targeted snapshot")
else:
    with open(snapshots[-1]) as f:
        s = json.load(f)
    rows = len(s.get("records", []))
    ts = s.get("ts_utc")
    age = 9999
    if ts:
        age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60
    if rows < 100:
        errors.append(f"snapshot rows too low: {rows}")
    if age > 30:
        errors.append(f"snapshot stale: {age:.1f} min")
if not os.path.exists(obs_path):
    errors.append("missing observation cache")
else:
    with open(obs_path) as f:
        obs = json.load(f)
    summary = obs.get("summary", {})
    if int(summary.get("ok", 0)) < 30:
        errors.append(f"observation ok too low: {summary}")
if not os.path.exists(summary_path):
    errors.append("missing strategy latest_summary")
else:
    with open(summary_path) as f:
        st = json.load(f)
    if st.get("live_enabled") is not False:
        errors.append("shadow verify expected live_enabled=false")
if errors:
    print("VERIFY_FAIL " + "; ".join(errors))
    raise SystemExit(1)
print("VERIFY_OK")
PY
}

start_stack() {
  write_launchagents
  bootout_label "$LOW_PRICE_LABEL"
  bootout_label "$DATA_FEED_LABEL"
  bootout_label "$SHADOW_LABEL"
  bootstrap_label "$DATA_FEED_LABEL"
  bootstrap_label "$SHADOW_LABEL"
  status
}

stop_stack() {
  bootout_label "$LOW_PRICE_LABEL"
  bootout_label "$SHADOW_LABEL"
  bootout_label "$DATA_FEED_LABEL"
  status
}

start_live() {
  if [[ "${1:-}" != "--confirm-live" ]]; then
    echo "refusing live start: pass --confirm-live" >&2
    exit 2
  fi
  REGIME_ROUTED_NO_SNAPSHOT_DIR="$DATA_FEED_SNAPSHOT_DIR" \
  REGIME_ROUTED_NO_OBSERVATION_CACHE="$DATA_FEED_OBS" \
  REGIME_ROUTED_NO_BASE_NOTIONAL="${REGIME_ROUTED_NO_BASE_NOTIONAL:-9}" \
  REGIME_ROUTED_NO_DAILY_GROSS_CAP="${REGIME_ROUTED_NO_DAILY_GROSS_CAP:-9}" \
  REGIME_ROUTED_NO_MAX_ORDERS="${REGIME_ROUTED_NO_MAX_ORDERS:-1}" \
  "$PROJECT_DIR/scripts/ops/start_regime_routed_no_tiny_live.sh"
}

start_low_price_live() {
  if [[ "${1:-}" != "--confirm-live" ]]; then
    echo "refusing low-price live start: pass --confirm-live" >&2
    exit 2
  fi
  write_launchagents
  bootout_label "$LOW_PRICE_LABEL"
  bootstrap_label "$LOW_PRICE_LABEL"
  "$PROJECT_DIR/scripts/ops/status_low_price_yes_lottery_tiny_live.sh"
}

case "${1:-}" in
  install-launchagents) write_launchagents ;;
  start) start_stack ;;
  stop) stop_stack ;;
  restart) stop_stack; start_stack ;;
  status) status ;;
  verify) verify ;;
  proxy-status) print_proxy_status ;;
  proxy-failover) "$PROJECT_DIR/scripts/ops/weather_market_proxy_failover.py" ;;
  start-live) shift; start_live "${1:-}" ;;
  stop-live) "$PROJECT_DIR/scripts/ops/stop_regime_routed_no_tiny_live.sh" ;;
  start-low-price-live) shift; start_low_price_live "${1:-}" ;;
  stop-low-price-live) bootout_label "$LOW_PRICE_LABEL"; "$PROJECT_DIR/scripts/ops/stop_low_price_yes_lottery_tiny_live.sh" ;;
  -h|--help|help|"") usage ;;
  *) usage; exit 2 ;;
esac
