#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
APPLY=0
INCLUDE_BACKUPS=0
INCLUDE_STRATEGY_RUNTIME=0
VOLUME=""

usage() {
  cat <<'EOF'
Usage: scripts/ops/prepare_mac_weather_external_runtime.sh --volume /Volumes/WeatherRuntime [--apply] [--include-backups] [--include-strategy-runtime]

Creates an external-disk runtime layout and, with --apply, moves high-write
weather runtime directories to the external disk and replaces them with symlinks.

Dry-run is the default. It prints planned actions without moving files.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --volume) VOLUME="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --include-backups) INCLUDE_BACKUPS=1; shift ;;
    --include-strategy-runtime) INCLUDE_STRATEGY_RUNTIME=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$VOLUME" ]]; then
  usage >&2
  exit 2
fi
if [[ ! -d "$VOLUME" ]]; then
  echo "missing volume directory: $VOLUME" >&2
  exit 1
fi

timestamp="$(date +%Y%m%dT%H%M%S)"

move_to_external() {
  local src="$1"
  local dst="$2"
  echo "PLAN $src -> $dst"
  if [[ "$APPLY" != "1" ]]; then
    return
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ -L "$src" ]]; then
    echo "SKIP already symlink: $src"
    return
  fi
  if [[ -e "$src" ]]; then
    mkdir -p "$dst"
    rsync -a "$src/" "$dst/"
  elif [[ -e "$dst" ]]; then
    echo "USING existing destination: $dst"
  else
    mkdir -p "$dst"
  fi
  if [[ -e "$src" && ! -L "$src" ]]; then
    mv "$src" "$src.pre_external_$timestamp"
  fi
  ln -s "$dst" "$src"
}

mkdir -p "$VOLUME/pm_agents"

move_to_external "$HOME/projects/weather_data_feed_service_runtime" "$VOLUME/weather_data_feed_service_runtime"
move_to_external "$PROJECT_DIR/runtime/weather_edge_v1/market_data" "$VOLUME/pm_agents/runtime/weather_edge_v1/market_data"

if [[ "$INCLUDE_STRATEGY_RUNTIME" == "1" ]]; then
  move_to_external "$PROJECT_DIR/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1" "$VOLUME/pm_agents/runtime/weather_edge_v1/regime_routed_no_tiny_live_v1"
fi

if [[ "$INCLUDE_BACKUPS" == "1" ]]; then
  move_to_external "$PROJECT_DIR/runtime/n100_emergency_backup" "$VOLUME/pm_agents/runtime/n100_emergency_backup"
fi

if [[ "$APPLY" == "1" ]]; then
  echo "APPLY_DONE"
else
  echo "DRY_RUN_ONLY pass --apply to move and symlink"
fi
