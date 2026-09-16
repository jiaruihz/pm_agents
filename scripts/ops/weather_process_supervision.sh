#!/usr/bin/env bash

# Run one producer child with a wall-clock deadline. The caller remains
# responsible for set -e handling and logging the returned status.
weather_run_with_timeout() {
  local timeout_seconds="$1"
  shift
  if [[ ! "$timeout_seconds" =~ ^[0-9]+$ ]] || (( timeout_seconds < 1 )); then
    echo "invalid weather child timeout: $timeout_seconds" >&2
    return 2
  fi

  "$@" &
  local child_pid=$!
  local deadline=$((SECONDS + timeout_seconds))
  while kill -0 "$child_pid" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "weather child timeout after ${timeout_seconds}s: pid=$child_pid command=$1" >&2
      kill -TERM "$child_pid" 2>/dev/null || true
      local grace_deadline=$((SECONDS + 5))
      while kill -0 "$child_pid" 2>/dev/null && (( SECONDS < grace_deadline )); do
        sleep 1
      done
      kill -KILL "$child_pid" 2>/dev/null || true
      wait "$child_pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
  done
  wait "$child_pid"
}

# Start a supervised child without blocking the producer loop. The caller polls
# WEATHER_ASYNC_PID and waits it once kill -0 reports completion.
weather_start_with_timeout_async() {
  local timeout_seconds="$1"
  shift
  weather_run_with_timeout "$timeout_seconds" "$@" &
  WEATHER_ASYNC_PID=$!
}
