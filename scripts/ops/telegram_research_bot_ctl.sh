#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="runtime/telegram_research_bot.pid"
LOG_FILE="runtime/logs/telegram_research_bot.log"

mkdir -p runtime/logs

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env: $name" >&2
    exit 1
  fi
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  ps -p "$pid" > /dev/null 2>&1
}

start_bot() {
  require_env TG_RESEARCH_BOT_TOKEN
  require_env TG_RESEARCH_ALLOWED_CHAT_IDS
  if is_running; then
    echo "telegram_research_bot already running: pid=$(cat "$PID_FILE")"
    return 0
  fi
  rm -f "$PID_FILE"
  setsid env PYTHONPATH="." ./.venv/bin/python scripts/ops/telegram_research_bot.py >> "$LOG_FILE" 2>&1 < /dev/null &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  sleep 1
  if ps -p "$pid" > /dev/null 2>&1; then
    echo "telegram_research_bot started: pid=$pid log=$LOG_FILE"
    return 0
  fi
  echo "telegram_research_bot failed to start; check $LOG_FILE" >&2
  exit 1
}

stop_bot() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "telegram_research_bot is not running"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  sleep 1
  if ps -p "$pid" > /dev/null 2>&1; then
    echo "telegram_research_bot still running, sending SIGKILL: pid=$pid"
    kill -9 "$pid"
  fi
  rm -f "$PID_FILE"
  echo "telegram_research_bot stopped"
}

status_bot() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    echo "telegram_research_bot RUNNING pid=$pid"
    ps -p "$pid" -o pid=,etime=,cmd=
    return 0
  fi
  echo "telegram_research_bot STOPPED"
  return 1
}

logs_bot() {
  tail -n "${1:-80}" "$LOG_FILE"
}

case "${1:-status}" in
  start)
    start_bot
    ;;
  stop)
    stop_bot
    ;;
  restart)
    stop_bot || true
    start_bot
    ;;
  status)
    status_bot
    ;;
  logs)
    logs_bot "${2:-80}"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs [lines]}" >&2
    exit 1
    ;;
esac
