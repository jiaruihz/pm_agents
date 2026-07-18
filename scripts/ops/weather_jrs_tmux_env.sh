#!/usr/bin/env bash

# One permission-bearing tmux server for every long-running process that reads
# or writes the external JRS runtime. macOS grants external-volume access to
# the tmux server process context, so the same session name elsewhere is not
# equivalent.
WEATHER_JRS_TMUX_SOCKET_CANONICAL="weather-data-feed-jrs"

weather_jrs_tmux_socket() {
  local requested="${1:-${WEATHER_JRS_TMUX_SOCKET:-$WEATHER_JRS_TMUX_SOCKET_CANONICAL}}"
  if [[ "$requested" != "$WEATHER_JRS_TMUX_SOCKET_CANONICAL" ]]; then
    echo "refusing non-canonical JRS tmux socket: $requested (required: $WEATHER_JRS_TMUX_SOCKET_CANONICAL)" >&2
    return 1
  fi
  printf '%s\n' "$requested"
}

weather_jrs_tmux_write_probe() {
  local socket="$1"
  local runtime_root="$2"
  local tmux_bin="${3:-tmux}"
  local probe_dir="$runtime_root/loop"
  local probe_path="$probe_dir/.jrs_tmux_context_probe_$$"
  local probe_session="weather_jrs_context_probe_$$"
  local quoted_probe

  mkdir -p "$probe_dir"
  quoted_probe="$(printf '%q' "$probe_path")"
  "$tmux_bin" -L "$socket" kill-session -t "$probe_session" 2>/dev/null || true
  "$tmux_bin" -L "$socket" new-session -d -s "$probe_session" \
    "umask 077; printf 'probe\\n' > $quoted_probe"

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -f "$probe_path" ]]; then
      rm -f "$probe_path"
      "$tmux_bin" -L "$socket" kill-session -t "$probe_session" 2>/dev/null || true
      return 0
    fi
    sleep 0.1
  done

  "$tmux_bin" -L "$socket" kill-session -t "$probe_session" 2>/dev/null || true
  echo "JRS tmux context cannot write $runtime_root: socket=$socket" >&2
  return 1
}

weather_jrs_tmux_start_socket() {
  local requested="${1:-}"
  local runtime_root="${2:-${WEATHER_DATA_FEED_RUNTIME_ROOT:-/Volumes/jrs/weather_data_feed_service_runtime}}"
  local tmux_bin="${3:-tmux}"
  local socket

  socket="$(weather_jrs_tmux_socket "$requested")" || return 1
  weather_jrs_tmux_write_probe "$socket" "$runtime_root" "$tmux_bin" || return 1
  printf '%s\n' "$socket"
}
