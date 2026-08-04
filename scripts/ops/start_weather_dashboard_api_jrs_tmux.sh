#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="weather_dashboard_api"
PY="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/runtime/_dashboard_logs"
PORT="${WEATHER_DASHBOARD_API_PORT:-8000}"

mkdir -p "$LOG_DIR"
[[ -x "$PY" ]] || { echo "missing project python: $PY" >&2; exit 1; }

command="cd $(printf '%q' "$PROJECT_DIR") && exec env WEATHER_DB_PATH=$(printf '%q' "$PROJECT_DIR/runtime/weather.db") $(printf '%q' "$PY") -m uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port $(printf '%q' "$PORT") >>$(printf '%q' "$LOG_DIR/api.controller.log") 2>>$(printf '%q' "$LOG_DIR/api.controller.err")"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$TMUX_SESSION" "$command"
echo "started session=$TMUX_SESSION socket=$TMUX_SOCKET port=$PORT"
