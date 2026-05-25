#!/usr/bin/env bash
# scripts/ops/after_reboot.sh
#
# 机器重启后一键恢复所有常驻服务。
#
# Usage:
#   scripts/ops/after_reboot.sh              # 完整恢复（dashboard + Telegram + N100检查）
#   scripts/ops/after_reboot.sh --dashboard  # 仅启 dashboard
#   scripts/ops/after_reboot.sh --n100       # 仅检查 N100
#   scripts/ops/after_reboot.sh --status     # 仅看状态，不启动任何服务

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

DO_DASHBOARD=1
DO_TELEGRAM=1
DO_N100=1
STATUS_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --dashboard)  DO_TELEGRAM=0; DO_N100=0 ;;
    --n100)       DO_DASHBOARD=0; DO_TELEGRAM=0 ;;
    --status)     STATUS_ONLY=1; DO_DASHBOARD=0; DO_TELEGRAM=0; DO_N100=0 ;;
    -h|--help)    sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m[after_reboot]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[after_reboot]\033[0m ✓ %s\n' "$*"; }
warn() { printf '\033[1;33m[after_reboot]\033[0m ⚠ %s\n' "$*"; }
err()  { printf '\033[1;31m[after_reboot]\033[0m ✗ %s\n' "$*" >&2; }

# ── 当前状态概览 ─────────────────────────────────────────────────
log "当前状态概览"
PYTHONPATH=. .venv/bin/python scripts/ops/process_status.py 2>/dev/null || warn "process_status.py 未能运行"
echo

if [[ $STATUS_ONLY -eq 1 ]]; then
  exit 0
fi

# ── 1. Weather Dashboard（API + 前端） ────────────────────────────
if [[ $DO_DASHBOARD -eq 1 ]]; then
  log "启动 Weather Dashboard..."
  if ss -tln 2>/dev/null | grep -q ':8000 '; then
    ok "API :8000 已在运行，跳过"
  else
    scripts/weather_dashboard/run_stack.sh --no-rebuild
  fi
  echo
fi

# ── 2. Telegram Research Bot ──────────────────────────────────────
if [[ $DO_TELEGRAM -eq 1 ]]; then
  log "启动 Telegram Research Bot..."
  if [[ -f runtime/telegram_research_bot.pid ]] && kill -0 "$(cat runtime/telegram_research_bot.pid 2>/dev/null)" 2>/dev/null; then
    ok "Telegram Bot 已在运行（pid=$(cat runtime/telegram_research_bot.pid)），跳过"
  else
    if [[ -z "${TG_RESEARCH_BOT_TOKEN:-}" ]]; then
      # 尝试从 .env 加载
      if [[ -f .env ]]; then
        # shellcheck disable=SC1091
        set -a; source .env; set +a
      fi
    fi
    if [[ -z "${TG_RESEARCH_BOT_TOKEN:-}" ]]; then
      warn "TG_RESEARCH_BOT_TOKEN 未设置，跳过 Telegram Bot（手动启动：export TG_RESEARCH_BOT_TOKEN=... && scripts/ops/telegram_research_bot_ctl.sh start）"
    else
      scripts/ops/telegram_research_bot_ctl.sh start
      ok "Telegram Bot 已启动"
    fi
  fi
  echo
fi

# ── 3. N100 实盘健康检查 ──────────────────────────────────────────
if [[ $DO_N100 -eq 1 ]]; then
  log "检查 N100 实盘状态..."
  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes 192.168.0.200 exit 2>/dev/null; then
    warn "无法连接 N100（192.168.0.200），跳过实盘检查"
  else
    ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_status.py status --json' 2>/dev/null \
      | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    paused = d.get('paused', False)
    reason = d.get('pause_reason', '')
    print(f'  live paused: {paused}' + (f'  reason: {reason}' if paused else ''))
    print(f'  last_cycle: {d.get(\"last_cycle_at\", \"unknown\")}')
except:
    print('  (status parse failed)')
" || true
    echo
    log "N100 doctor 检查（快速）..."
    ssh 192.168.0.200 'cd /home/jiarui/projects/pm_agent && .venv/bin/python scripts/ops/weather_live_doctor.py --http-timeout 6 --sync-dry-run 2>&1 | tail -5' || warn "doctor 检查失败，请手动查看 N100 状态"
  fi
  echo
fi

# ── 完成 ──────────────────────────────────────────────────────────
log "完成。常用入口："
log "  Dashboard:  http://localhost:5174/weather/runs"
log "  Live 监控:  http://localhost:5174/weather/live"
log "  API docs:   http://localhost:8000/docs"
log ""
log "停止 Dashboard:"
log "  pkill -F runtime/_dashboard_logs/api.pid 2>/dev/null"
log "  pkill -F runtime/_dashboard_logs/fe.pid 2>/dev/null"
