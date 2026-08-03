#!/usr/bin/env bash

# One permission-bearing tmux server for every long-running process that reads
# or writes the external JRS runtime. macOS grants external-volume access to
# the tmux server process context, so the same session name elsewhere is not
# equivalent.
WEATHER_JRS_TMUX_SOCKET_CANONICAL="weather-data-feed-jrs"
WEATHER_JRS_TMUX_BIN_CANONICAL="/opt/homebrew/Cellar/tmux/3.6b/bin/tmux"
WEATHER_JRS_TMUX_BIN_CANONICAL_SHA256="74e47d00267734a47daffd0c6d92e6841c671ee88b54aab6c13d239ba8741584"

weather_jrs_tmux_bin() {
  if [[ -n "${WEATHER_JRS_TMUX_BIN:-}" ]]; then
    if [[ "${WEATHER_JRS_TMUX_TEST_OVERRIDE:-0}" != "1" ]]; then
      echo "refusing JRS tmux binary override outside tests: $WEATHER_JRS_TMUX_BIN" >&2
      return 1
    fi
    if [[ ! -x "$WEATHER_JRS_TMUX_BIN" ]]; then
      echo "configured WEATHER_JRS_TMUX_BIN is not executable: $WEATHER_JRS_TMUX_BIN" >&2
      return 1
    fi
    printf '%s\n' "$WEATHER_JRS_TMUX_BIN"
    return 0
  fi
  if [[ ! -x "$WEATHER_JRS_TMUX_BIN_CANONICAL" ]]; then
    echo "pinned Full-Disk-Access JRS tmux binary is missing: $WEATHER_JRS_TMUX_BIN_CANONICAL" >&2
    return 1
  fi
  local actual_sha256
  actual_sha256="$(
    shasum -a 256 "$WEATHER_JRS_TMUX_BIN_CANONICAL" | awk '{print $1}'
  )"
  if [[ "$actual_sha256" != "$WEATHER_JRS_TMUX_BIN_CANONICAL_SHA256" ]]; then
    echo "pinned JRS tmux binary changed; grant Full Disk Access and update the reviewed pin before use: path=$WEATHER_JRS_TMUX_BIN_CANONICAL expected_sha256=$WEATHER_JRS_TMUX_BIN_CANONICAL_SHA256 actual_sha256=$actual_sha256" >&2
    return 1
  fi
  printf '%s\n' "$WEATHER_JRS_TMUX_BIN_CANONICAL"
}

weather_jrs_tmux_socket() {
  local requested="${1:-$WEATHER_JRS_TMUX_SOCKET_CANONICAL}"
  if [[ "$requested" != "$WEATHER_JRS_TMUX_SOCKET_CANONICAL" ]]; then
    echo "refusing non-canonical JRS tmux socket: $requested (required: $WEATHER_JRS_TMUX_SOCKET_CANONICAL)" >&2
    return 1
  fi
  printf '%s\n' "$requested"
}

weather_jrs_tmux() {
  local socket
  local tmux_bin
  local command_name="${2:-}"

  socket="$(weather_jrs_tmux_socket "${1:-}")" || return 1
  shift
  tmux_bin="$(weather_jrs_tmux_bin)" || return 1
  case "$command_name" in
    new-session|new-window|kill-session|kill-server|start-server)
      case "${WEATHER_JRS_TMUX_MUTATION_AUTHORITY:-}" in
        controller|helper-internal|bounded-oneshot) ;;
        *)
          echo "refusing persistent JRS tmux mutation outside production controller: command=$command_name" >&2
          return 1
          ;;
      esac
      ;;
  esac
  # Business entrypoints are attach-only.  ``-N`` prevents tmux from
  # starting a new server if the canonical permission host is absent or dies
  # between the caller's health check and this command.  Only the production
  # controller may create/recreate the canonical server.
  "$tmux_bin" -N -L "$socket" "$@"
}

weather_jrs_tmux_exec_checked() (
  local socket="$1"
  local label="$2"
  local command="$3"
  local bridge_dir
  local status_bridge
  local session
  local session_command
  local rc
  local wait_count=0

  socket="$(weather_jrs_tmux_socket "$socket")" || return 1
  bridge_dir="$(mktemp -d "${TMPDIR:-/tmp}/weather_jrs_${label}.XXXXXX")" || return 1
  status_bridge="$bridge_dir/status"
  session="weather_jrs_${label}_$$_${RANDOM}"
  trap 'rm -f "$status_bridge"; rmdir "$bridge_dir" 2>/dev/null || true' EXIT INT TERM

  printf -v session_command \
    'set +e; %s; rc=$?; printf "%%s\n" "$rc" > %q; exit "$rc"' \
    "$command" "$status_bridge"
  if ! WEATHER_JRS_TMUX_MUTATION_AUTHORITY=helper-internal \
    weather_jrs_tmux "$socket" new-session -d -s "$session" "$session_command"; then
    echo "failed to start checked JRS tmux command: label=$label" >&2
    return 1
  fi
  while weather_jrs_tmux "$socket" has-session -t "=$session" 2>/dev/null; do
    wait_count=$((wait_count + 1))
    if [[ "$wait_count" -ge 300 ]]; then
      echo "checked JRS tmux command timed out: label=$label" >&2
      WEATHER_JRS_TMUX_MUTATION_AUTHORITY=helper-internal \
        weather_jrs_tmux "$socket" kill-session -t "=$session" 2>/dev/null || true
      return 1
    fi
    sleep 0.1
  done
  if [[ ! -s "$status_bridge" ]]; then
    echo "checked JRS tmux command exited without status: label=$label" >&2
    return 1
  fi
  rc="$(tr -d '[:space:]' < "$status_bridge")"
  if [[ ! "$rc" =~ ^[0-9]+$ || "$rc" -gt 255 ]]; then
    echo "invalid checked JRS tmux command status: label=$label status=$rc" >&2
    return 1
  fi
  return "$rc"
)

weather_jrs_tmux_write_probe() {
  local socket="$1"
  local runtime_root="$2"
  local probe_dir="$runtime_root/loop"
  local probe_path="$probe_dir/.jrs_tmux_context_probe_$$"
  local quoted_dir
  local quoted_probe

  socket="$(weather_jrs_tmux_socket "$socket")" || return 1
  quoted_dir="$(printf '%q' "$probe_dir")"
  quoted_probe="$(printf '%q' "$probe_path")"
  if ! weather_jrs_tmux_exec_checked "$socket" "write_probe" \
    "set -eu; umask 077; mkdir -p $quoted_dir; printf 'probe\\n' > $quoted_probe; rm -f $quoted_probe"; then
    echo "JRS tmux context cannot write $runtime_root: socket=$socket" >&2
    return 1
  fi
}

weather_jrs_tmux_start_socket() {
  local runtime_root="${1:-${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}}"
  local socket

  socket="$(weather_jrs_tmux_socket)" || return 1
  weather_jrs_tmux_write_probe "$socket" "$runtime_root" || return 1
  printf '%s\n' "$socket"
}

weather_jrs_tmux_mkdir() {
  local socket="$1"
  shift
  local command="set -eu; mkdir -p"
  local target_path

  socket="$(weather_jrs_tmux_socket "$socket")" || return 1
  if [[ "$#" -eq 0 ]]; then
    echo "weather_jrs_tmux_mkdir requires at least one path" >&2
    return 1
  fi
  for target_path in "$@"; do
    command+=" $(printf '%q' "$target_path")"
  done
  weather_jrs_tmux_exec_checked "$socket" "mkdir" "$command"
}

weather_jrs_tmux_run_oneshot() (
  local runtime_root="$1"
  local session="$2"
  local job_dir="$3"
  local job_command="$4"
  local socket
  local log_file="$job_dir/tmux.log"
  local status_file="$job_dir/last_exit_status"
  local status_bridge
  local session_command
  local status_bridge_command
  local rc

  if [[ -z "$session" || ! "$session" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "invalid JRS tmux session name: $session" >&2
    return 1
  fi

  socket="$(weather_jrs_tmux_start_socket "$runtime_root")" || return 1
  weather_jrs_tmux_mkdir "$socket" "$job_dir" || return 1
  if weather_jrs_tmux "$socket" has-session -t "=$session" 2>/dev/null; then
    echo "JRS tmux one-shot already running; skipping: session=$session"
    return 0
  fi

  status_bridge="$(
    mktemp "${TMPDIR:-/tmp}/weather-external-${session}-status.XXXXXX"
  )" || return 1
  trap 'rm -f "$status_bridge"' EXIT INT TERM

  printf -v session_command \
    'set +e; mkdir -p %q; rm -f %q; %s >> %q 2>&1; rc=$?; printf "%%s\n" "$rc" > %q; exit "$rc"' \
    "$job_dir" "$status_file" "$job_command" "$log_file" "$status_file"
  WEATHER_JRS_TMUX_MUTATION_AUTHORITY=bounded-oneshot \
    weather_jrs_tmux "$socket" new-session -d -s "$session" "$session_command"
  echo "started JRS tmux one-shot: socket=$socket session=$session log=$log_file"

  while weather_jrs_tmux "$socket" has-session -t "=$session" 2>/dev/null; do
    sleep 1
  done

  printf -v status_bridge_command \
    'set -eu; test -s %q; tr -d "[:space:]" < %q > %q' \
    "$status_file" "$status_file" "$status_bridge"
  if ! weather_jrs_tmux_exec_checked "$socket" "status_bridge" "$status_bridge_command"; then
    echo "JRS tmux one-shot exited without status: session=$session" >&2
    return 1
  fi

  rc="$(<"$status_bridge")"
  if [[ ! "$rc" =~ ^[0-9]+$ || "$rc" -gt 255 ]]; then
    echo "invalid JRS tmux one-shot exit status: session=$session status=$rc" >&2
    return 1
  fi
  if [[ "$rc" != "0" ]]; then
    echo "JRS tmux one-shot failed: session=$session returncode=$rc log=$log_file" >&2
    return "$rc"
  fi
  echo "completed JRS tmux one-shot: session=$session returncode=0"
)

weather_jrs_tmux_guarded_replace_session() {
  local socket="$1"
  local session="$2"
  local command="$3"
  local startup_wait_sec="${4:-1}"
  local before_sessions
  local after_sessions
  local peer

  socket="$(weather_jrs_tmux_socket "$socket")" || return 1
  if [[ -z "$session" || ! "$session" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "invalid JRS tmux session name: $session" >&2
    return 1
  fi

  before_sessions="$(
    weather_jrs_tmux "$socket" list-sessions -F '#{session_name}' 2>/dev/null || true
  )"
  weather_jrs_tmux "$socket" kill-session -t "=$session" 2>/dev/null || true
  weather_jrs_tmux "$socket" new-session -d -s "$session" "$command"
  sleep "$startup_wait_sec"

  if ! weather_jrs_tmux "$socket" has-session -t "=$session" 2>/dev/null; then
    echo "JRS tmux target session exited during startup: $session" >&2
    return 1
  fi

  after_sessions="$(
    weather_jrs_tmux "$socket" list-sessions -F '#{session_name}' 2>/dev/null || true
  )"
  while IFS= read -r peer; do
    [[ -z "$peer" || "$peer" == "$session" ]] && continue
    if ! grep -Fxq "$peer" <<<"$after_sessions"; then
      echo "unrelated JRS tmux session disappeared while replacing $session: $peer" >&2
      return 1
    fi
  done <<<"$before_sessions"
}
