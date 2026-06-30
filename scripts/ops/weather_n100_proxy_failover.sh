#!/usr/bin/env bash
# Configure remote N100 proxy envs, optionally using this Mac's local proxy
# through an SSH reverse tunnel when the native proxy is unhealthy.

set -euo pipefail

N100_REMOTE="${N100_REMOTE:-jiarui@192.168.0.200}"
REMOTE_ENV_DIRS="${REMOTE_ENV_DIRS:-/home/jiarui/projects/weather_data_feed_service,/home/jiarui/projects/pm_agent}"

PRIMARY_PROXY="${PRIMARY_PROXY:-http://127.0.0.1:10809}"
FALLBACK_PROXY="${FALLBACK_PROXY:-http://127.0.0.1:18089}"
APPLY_PROXY="${APPLY_PROXY:-}"
REMOTE_PROXY_ENV_KEYS="${REMOTE_PROXY_ENV_KEYS:-HTTP_PROXY,HTTPS_PROXY,ALL_PROXY,http_proxy,https_proxy,all_proxy,WEATHER_DATA_FEED_MARKET_PROXY,WEATHER_PREDICT_PROXY}"
REMOTE_NO_PROXY_VALUE="${REMOTE_NO_PROXY_VALUE:-127.0.0.1,localhost,192.168.0.0/16}"
LOCAL_PROXY_HOST="${LOCAL_PROXY_HOST:-127.0.0.1}"
LOCAL_PROXY_PORT="${LOCAL_PROXY_PORT:-7897}"
REMOTE_FALLBACK_BIND="${REMOTE_FALLBACK_BIND:-127.0.0.1:18089}"
SSH_CONTROL_SOCKET="${SSH_CONTROL_SOCKET:-/tmp/n100-weather-proxy-tunnel.ctl}"
LOCAL_MIHOMO_SOCKET="${LOCAL_MIHOMO_SOCKET:-/tmp/verge/verge-mihomo.sock}"
LOCAL_REMOTE_DATA_NODE="${LOCAL_REMOTE_DATA_NODE:-🇯🇵 日本 01丨1x JP}"
LOCAL_REMOTE_DATA_GROUPS="${LOCAL_REMOTE_DATA_GROUPS:-♻️ 手动切换,🐟 漏网之鱼,🌏 国外网站,🎬 韩国媒体,🔎 Google,🧲 OpenAI,🧲 Claude}"
SKIP_LOCAL_SELECTOR_UPDATE="${SKIP_LOCAL_SELECTOR_UPDATE:-0}"

HEALTH_URL="${HEALTH_URL:-https://gamma-api.polymarket.com/events?slug=highest-temperature-in-nyc-on-june-29-2026}"
CURL_TIMEOUT_SEC="${CURL_TIMEOUT_SEC:-20}"
CHECK_ONLY=0
FORCE_FALLBACK=0

log() { printf '\033[1;36m[proxy-failover]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[proxy-failover]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[proxy-failover]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: scripts/ops/weather_n100_proxy_failover.sh [--check-only] [--force-fallback] [--apply-proxy PROXY]

Environment:
  N100_REMOTE=$N100_REMOTE
  PRIMARY_PROXY=$PRIMARY_PROXY
  FALLBACK_PROXY=$FALLBACK_PROXY
  APPLY_PROXY=$APPLY_PROXY
  REMOTE_ENV_DIRS=$REMOTE_ENV_DIRS
  REMOTE_PROXY_ENV_KEYS=$REMOTE_PROXY_ENV_KEYS
  REMOTE_NO_PROXY_VALUE=$REMOTE_NO_PROXY_VALUE
  LOCAL_PROXY_HOST=$LOCAL_PROXY_HOST
  LOCAL_PROXY_PORT=$LOCAL_PROXY_PORT
  REMOTE_FALLBACK_BIND=$REMOTE_FALLBACK_BIND
  LOCAL_REMOTE_DATA_NODE=$LOCAL_REMOTE_DATA_NODE
  LOCAL_REMOTE_DATA_GROUPS=$LOCAL_REMOTE_DATA_GROUPS
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --check-only) CHECK_ONLY=1 ;;
    --force-fallback) FORCE_FALLBACK=1 ;;
    --apply-proxy)
      [[ "$#" -ge 2 ]] || die "--apply-proxy requires a proxy value"
      APPLY_PROXY="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

remote_curl_check() {
  local proxy="$1"
  ssh "$N100_REMOTE" "timeout '$CURL_TIMEOUT_SEC' curl -sS -x '$proxy' -o /tmp/weather_proxy_health.\$\$ -w 'status=%{http_code} bytes=%{size_download} time=%{time_total} err=%{errormsg}\n' '$HEALTH_URL'" 2>&1
}

remote_proxy_healthy() {
  local proxy="$1"
  local out
  out="$(remote_curl_check "$proxy" || true)"
  printf '%s\n' "$out"
  grep -Eq 'status=(200|204|301|302|304) ' <<<"$out"
}

ensure_reverse_tunnel() {
  if ssh -S "$SSH_CONTROL_SOCKET" -O check "$N100_REMOTE" >/dev/null 2>&1; then
    log "reverse tunnel already running: $REMOTE_FALLBACK_BIND -> $LOCAL_PROXY_HOST:$LOCAL_PROXY_PORT"
    return 0
  fi

  rm -f "$SSH_CONTROL_SOCKET"
  log "starting reverse tunnel: $N100_REMOTE $REMOTE_FALLBACK_BIND -> $LOCAL_PROXY_HOST:$LOCAL_PROXY_PORT"
  ssh -fN -M -S "$SSH_CONTROL_SOCKET" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -R "$REMOTE_FALLBACK_BIND:$LOCAL_PROXY_HOST:$LOCAL_PROXY_PORT" \
    "$N100_REMOTE"
}

set_local_proxy_selectors() {
  if [[ "$SKIP_LOCAL_SELECTOR_UPDATE" == "1" ]]; then
    warn "SKIP_LOCAL_SELECTOR_UPDATE=1; not touching local proxy selectors"
    return 0
  fi
  if [[ ! -S "$LOCAL_MIHOMO_SOCKET" ]]; then
    warn "local Mihomo controller socket not found: $LOCAL_MIHOMO_SOCKET"
    warn "verify your local proxy is not using a high-multiplier node before enabling fallback"
    return 0
  fi

  python3 - "$LOCAL_MIHOMO_SOCKET" "$LOCAL_REMOTE_DATA_NODE" "$LOCAL_REMOTE_DATA_GROUPS" <<'PY'
import json
import subprocess
import sys
import urllib.parse

socket, node, groups_raw = sys.argv[1:4]
groups = [item.strip() for item in groups_raw.split(",") if item.strip()]
if not groups:
    raise SystemExit(0)

payload = subprocess.check_output(
    ["curl", "--unix-socket", socket, "-sS", "http://127.0.0.1/proxies"],
    text=True,
)
proxies = json.loads(payload).get("proxies", {})
if node not in proxies:
    raise SystemExit(f"local proxy node not found: {node}")

for group in groups:
    obj = proxies.get(group)
    if not obj or not obj.get("all"):
        print(f"skip non-selector group: {group}")
        continue
    if node not in (obj.get("all") or []):
        print(f"skip group without node: {group}")
        continue
    url = "http://127.0.0.1/proxies/" + urllib.parse.quote(group, safe="")
    body = json.dumps({"name": node}, ensure_ascii=False)
    subprocess.check_call(
        [
            "curl",
            "--unix-socket",
            socket,
            "-sS",
            "-X",
            "PUT",
            url,
            "-H",
            "Content-Type: application/json",
            "--data",
            body,
        ],
        stdout=subprocess.DEVNULL,
    )
    print(f"{group} => {node}")
PY
}

set_remote_proxy_envs() {
  local proxy="$1"
  ssh "$N100_REMOTE" "REMOTE_ENV_DIRS='$REMOTE_ENV_DIRS' PROXY_VALUE='$proxy' REMOTE_PROXY_ENV_KEYS='$REMOTE_PROXY_ENV_KEYS' REMOTE_NO_PROXY_VALUE='$REMOTE_NO_PROXY_VALUE' python3 - <<'PY'
import os
from datetime import datetime, timezone
from pathlib import Path

roots = [Path(item) for item in os.environ['REMOTE_ENV_DIRS'].split(',') if item.strip()]
proxy = os.environ['PROXY_VALUE']
proxy_keys = [item.strip() for item in os.environ['REMOTE_PROXY_ENV_KEYS'].split(',') if item.strip()]
no_proxy = os.environ['REMOTE_NO_PROXY_VALUE']
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
managed = {key: proxy for key in proxy_keys}
if no_proxy:
    managed['NO_PROXY'] = no_proxy
    managed['no_proxy'] = no_proxy

for root in roots:
    env_path = root / '.env'
    if not env_path.exists():
        print(f'skip missing env file: {env_path}')
        continue
    backup = env_path.with_name(env_path.name + '.bak.proxy_failover_' + stamp)
    backup.write_text(env_path.read_text(encoding='utf-8'), encoding='utf-8')
    seen = set()
    lines = []
    for raw in env_path.read_text(encoding='utf-8').splitlines():
        key = raw.split('=', 1)[0] if '=' in raw else ''
        if key in managed:
            lines.append(f'{key}={managed[key]}')
            seen.add(key)
        else:
            lines.append(raw)
    for key, value in managed.items():
        if key not in seen:
            lines.append(f'{key}={value}')
    env_path.write_text('\\n'.join(lines).rstrip() + '\\n', encoding='utf-8')
    print(f'updated {env_path}')
    print(f'backup {backup}')
PY"
}

if [[ -n "$APPLY_PROXY" ]]; then
  log "applying explicit proxy to remote envs: $APPLY_PROXY"
  set_remote_proxy_envs "$APPLY_PROXY"
  warn "remote .env files updated; restart only affected data-feed/strategy services after checking live order risk"
  exit 0
fi

log "checking primary proxy on N100: $PRIMARY_PROXY"
if [[ "$FORCE_FALLBACK" != "1" ]] && remote_proxy_healthy "$PRIMARY_PROXY"; then
  log "primary proxy is healthy; no fallback needed"
  exit 0
fi

warn "primary proxy failed health check"
set_local_proxy_selectors
ensure_reverse_tunnel

log "checking fallback proxy through Mac tunnel: $FALLBACK_PROXY"
if ! remote_proxy_healthy "$FALLBACK_PROXY"; then
  die "fallback proxy is not healthy; check local proxy at $LOCAL_PROXY_HOST:$LOCAL_PROXY_PORT"
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "check-only: fallback is available, no remote env change"
  exit 0
fi

set_remote_proxy_envs "$FALLBACK_PROXY"
warn "remote .env files updated; restart only the affected data-feed/strategy service after checking live order risk"
log "active fallback proxy is now $FALLBACK_PROXY"
