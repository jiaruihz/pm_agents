#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
PY="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ENV_FILE="${WEATHER_ENV_FILE:-$ROOT/.env}"
SESSION="weather_live_cross_observations"
OUTPUT_DIR="$RUNTIME_ROOT/output/live_cross_observations"
NOTIFY_PATH="$RUNTIME_ROOT/loop/live_cross_observation_notify.json"
LOG_FILE="$RUNTIME_ROOT/loop/live_cross_observations.log"
ACTION="${1:-start}"
if [[ ! -x "$PY" ]]; then
  echo "configured live-cross Python is not executable: $PY" >&2
  exit 1
fi
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"

if [[ "$ACTION" == "status" ]]; then
  weather_jrs_tmux "$TMUX_SOCKET" list-panes -t "=$SESSION" -F '#{session_name} #{pane_pid} #{pane_start_command}'
  exit 0
fi
if [[ "$ACTION" == "stop" ]]; then
  weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION"
  echo "stopped session=$SESSION"
  exit 0
fi
if [[ "$ACTION" != "start" ]]; then
  echo "usage: $0 [start|stop|status]" >&2
  exit 2
fi

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$RUNTIME_ROOT/loop" "$OUTPUT_DIR"
weather_jrs_tmux "$TMUX_SOCKET" kill-session -t "=$SESSION" 2>/dev/null || true
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" \
  "cd '$ROOT' && set -a; [[ -f '$ENV_FILE' ]] && source '$ENV_FILE' || true; set +a; exec '$PY' -u '$ROOT/scripts/ops/weather_live_cross_observations_loop.py' --output-dir '$OUTPUT_DIR' --notify-path '$NOTIFY_PATH' --base-loop-interval-sec 20 --max-workers 8 --timeout-sec 5 --sources jma_amedas singapore_mss fmi knmi amos_runway mgm ims_lod noaa_madis_hfmetar --cities Amsterdam Helsinki Busan Singapore Tokyo Seoul TelAviv Ankara Istanbul Atlanta Miami SanFrancisco --source-min-interval-sec jma_amedas=300 --source-min-interval-sec singapore_mss=20 --source-min-interval-sec fmi=60 --source-min-interval-sec knmi=300 --source-min-interval-sec amos_runway=20 --source-min-interval-sec mgm=300 --source-min-interval-sec ims_lod=300 --source-min-interval-sec noaa_madis_hfmetar=300 --source-window-min-interval-sec jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:2 --source-window-min-interval-sec fmi=1-6,11-16,21-26,31-36,41-46,51-56:2 --fast-lane amos_core=amos_runway:Busan,Seoul@5 >> '$LOG_FILE' 2>&1"
echo "started session=$SESSION socket=$TMUX_SOCKET output=$OUTPUT_DIR"
