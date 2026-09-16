#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
TMUX_SESSION="weather_dashboard_api"
PY="$PROJECT_DIR/.venv/bin/python"
PORT="${WEATHER_DASHBOARD_API_PORT:-8000}"

[[ -x "$PY" ]] || { echo "missing project python: $PY" >&2; exit 1; }
CANONICAL_DB_PATH="$(
  PYTHONPATH="$PROJECT_DIR" "$PY" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().canonical_db_path)'
)"
OPERATIONAL_PROJECT_DIR="$(
  PYTHONPATH="$PROJECT_DIR" "$PY" -c \
    'from src.strategies.runtime.production import load_production_spec; print(load_production_spec().operational_repo_root)'
)"
"$PY" - "$CANONICAL_DB_PATH" <<'PY'
from pathlib import Path
import sys

db_path = Path(sys.argv[1])
if not db_path.is_absolute() or not db_path.is_file():
    raise SystemExit(f"canonical dashboard DB missing or non-absolute: {db_path}")
PY
LOG_DIR="$OPERATIONAL_PROJECT_DIR/runtime/_dashboard_logs"
mkdir -p "$LOG_DIR"

command="cd $(printf '%q' "$PROJECT_DIR") && exec env WEATHER_DB_PATH=$(printf '%q' "$CANONICAL_DB_PATH") $(printf '%q' "$PY") -m uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port $(printf '%q' "$PORT") >>$(printf '%q' "$LOG_DIR/api.controller.log") 2>>$(printf '%q' "$LOG_DIR/api.controller.err")"
weather_jrs_tmux_guarded_replace_session "$TMUX_SOCKET" "$TMUX_SESSION" "$command"
echo "started session=$TMUX_SESSION socket=$TMUX_SOCKET port=$PORT"
