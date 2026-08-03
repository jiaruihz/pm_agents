#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"

VOLUME="${WEATHER_JRS_VOLUME:-/Volumes/jrs}"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-$VOLUME/weather_data_feed_service_runtime}"
SERVICE_DIR="${WEATHER_DATA_FEED_SERVICE_DIR:-/Users/deepsleep/projects/pm_agents_knmi_recovery}"
PYTHON_BIN="${KNMI_PYTHON_BIN:-/Users/deepsleep/projects/pm_agents_prod/.venv/bin/python}"
ENV_FILE="${KNMI_ENV_FILE:-/Users/deepsleep/projects/pm_agents_prod/.env.knmi}"
OUTPUT_DIR="${KNMI_OPEN_DATA_OUTPUT_DIR:-$RUNTIME_ROOT/output/knmi_open_data}"
LOG_FILE="${KNMI_OPEN_DATA_LOG_FILE:-$RUNTIME_ROOT/loop/knmi_open_data.log}"
STATUS_PATH="${KNMI_OPEN_DATA_STATUS_PATH:-$OUTPUT_DIR/supervisor_status.json}"
CLIENT_ID="${KNMI_OPEN_DATA_CLIENT_ID:-pm-agents-knmi-schiphol-prod-v1}"
TIMEOUT_SEC="${KNMI_OPEN_DATA_TIMEOUT_SEC:-15}"
CONSISTENCY_RETRIES="${KNMI_OPEN_DATA_CONSISTENCY_RETRIES:-6}"
TMUX_SESSION="${KNMI_OPEN_DATA_TMUX_SESSION:-weather_knmi_open_data_jrs}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket)"
SUPERVISOR="$SERVICE_DIR/scripts/ops/weather_knmi_notification_supervisor.py"

if [[ ! -d "$VOLUME" ]]; then
  echo "missing mounted volume: $VOLUME" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing KNMI env file: $ENV_FILE" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "missing KNMI python: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -f "$SUPERVISOR" ]]; then
  echo "missing KNMI supervisor: $SUPERVISOR" >&2
  exit 1
fi

weather_jrs_tmux "$TMUX_SOCKET" run-shell \
  "mkdir -p '$OUTPUT_DIR' '$RUNTIME_ROOT/loop' && probe='$OUTPUT_DIR/.write_probe' && : > \"\$probe\" && rm \"\$probe\""

weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
  "cd '$SERVICE_DIR' && set -a && source '$ENV_FILE' && set +a && exec '$PYTHON_BIN' -u '$SUPERVISOR' --status-path '$STATUS_PATH' -- '$PYTHON_BIN' -u -m weather_data_feed_service.knmi_notification --output-dir '$OUTPUT_DIR' --client-id '$CLIENT_ID' --timeout-sec '$TIMEOUT_SEC' --consistency-retries '$CONSISTENCY_RETRIES' >> '$LOG_FILE' 2>&1"

sleep 1
if ! weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "failed to start $TMUX_SESSION; inspect $LOG_FILE" >&2
  exit 1
fi

echo "started $TMUX_SESSION on tmux socket $TMUX_SOCKET"
echo "service_dir=$SERVICE_DIR"
echo "python=$PYTHON_BIN"
echo "output_dir=$OUTPUT_DIR"
echo "log=$LOG_FILE"
echo "status=$STATUS_PATH"
