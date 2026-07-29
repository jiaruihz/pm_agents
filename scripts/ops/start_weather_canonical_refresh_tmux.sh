#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "$PROJECT_DIR/scripts/ops/weather_jrs_tmux_env.sh"
RUNTIME_ROOT="${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}"
TMUX_SOCKET="$(weather_jrs_tmux_start_socket "$RUNTIME_ROOT")"
SESSION="weather_canonical_refresh"
LOG_DIR="$PROJECT_DIR/runtime/weather_edge_v1/canonical_refresh"
LOG_FILE="$LOG_DIR/tmux.log"
STATUS_FILE="$LOG_DIR/last_exit_status"
STATUS_BRIDGE="$(mktemp "${TMPDIR:-/tmp}/weather-canonical-refresh-status.XXXXXX")"
cleanup() { rm -f "$STATUS_BRIDGE"; }
trap cleanup EXIT INT TERM

weather_jrs_tmux_mkdir "$TMUX_SOCKET" "$LOG_DIR"
if weather_jrs_tmux "$TMUX_SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "canonical refresh already running in tmux; skipping"
  exit 0
fi

printf -v session_cmd \
  'set +e; mkdir -p %q; rm -f %q; cd %q; export WEATHER_DATA_FEED_RUNTIME_ROOT=%q; %q >> %q 2>&1; rc=$?; printf "%%s\n" "$rc" > %q; exit "$rc"' \
  "$LOG_DIR" "$STATUS_FILE" "$PROJECT_DIR" "$RUNTIME_ROOT" \
  "$PROJECT_DIR/scripts/ops/run_weather_canonical_refresh_launchd.sh" "$LOG_FILE" "$STATUS_FILE"
weather_jrs_tmux "$TMUX_SOCKET" new-session -d -s "$SESSION" "$session_cmd"
echo "started tmux_socket=$TMUX_SOCKET session=$SESSION log=$LOG_FILE"

while weather_jrs_tmux "$TMUX_SOCKET" has-session -t "=$SESSION" 2>/dev/null; do
  sleep 1
done
printf -v status_bridge_cmd \
  'set -eu; test -s %q; tr -d "[:space:]" < %q > %q' \
  "$STATUS_FILE" "$STATUS_FILE" "$STATUS_BRIDGE"
if ! weather_jrs_tmux "$TMUX_SOCKET" run-shell "$status_bridge_cmd"; then
  echo "canonical refresh exited without status: session=$SESSION" >&2
  exit 1
fi
rc="$(<"$STATUS_BRIDGE")"
if [[ ! "$rc" =~ ^[0-9]+$ ]]; then
  echo "invalid canonical refresh exit status: $rc" >&2
  exit 1
fi
if [[ "$rc" != "0" ]]; then
  echo "canonical refresh failed: returncode=$rc log=$LOG_FILE" >&2
  exit "$rc"
fi
echo "canonical refresh completed: returncode=0"
