#!/usr/bin/env bash

# One permission-bearing tmux server for every long-running process that reads
# or writes the external JRS runtime. macOS grants external-volume access to
# the tmux server process context, so the same session name elsewhere is not
# equivalent.
WEATHER_JRS_TMUX_SOCKET_CANONICAL="weather-data-feed-jrs"

weather_jrs_tmux_bin() {
  if [[ -n "${WEATHER_JRS_TMUX_BIN:-}" ]]; then
    if [[ ! -x "$WEATHER_JRS_TMUX_BIN" ]]; then
      echo "configured WEATHER_JRS_TMUX_BIN is not executable: $WEATHER_JRS_TMUX_BIN" >&2
      return 1
    fi
    printf '%s\n' "$WEATHER_JRS_TMUX_BIN"
    return 0
  fi
  if [[ -x /opt/homebrew/bin/tmux ]]; then
    printf '%s\n' /opt/homebrew/bin/tmux
    return 0
  fi
  command -v tmux
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

  socket="$(weather_jrs_tmux_socket "${1:-}")" || return 1
  shift
  tmux_bin="$(weather_jrs_tmux_bin)" || return 1
  "$tmux_bin" -L "$socket" "$@"
}

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
  if ! weather_jrs_tmux "$socket" run-shell \
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
