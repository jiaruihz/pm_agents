#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_SRC_DIR="$ROOT_DIR/deploy/systemd/user"
UNIT_DST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RUNTIME_ROOT="$HOME/projects/weather_data_feed_service_runtime"

mkdir -p "$UNIT_DST_DIR" "$RUNTIME_ROOT/output/logs" "$RUNTIME_ROOT/cache"

for unit in \
  weather-data-feed-snapshot.service \
  weather-data-feed-snapshot.timer \
  weather-data-feed-full-snapshot.service \
  weather-data-feed-full-snapshot.timer \
  weather-data-feed-observations.service \
  weather-data-feed-observations.timer \
  weather-data-feed-daily.service \
  weather-data-feed-daily.timer
do
  install -m 0644 "$UNIT_SRC_DIR/$unit" "$UNIT_DST_DIR/$unit"
done

systemctl --user daemon-reload

cat <<EOF
Installed weather data feed units into $UNIT_DST_DIR

Expected optional environment file:
  $HOME/projects/weather_data_feed_service/.env

Next manual steps:
  systemctl --user enable --now weather-data-feed-snapshot.timer
  # Enable full snapshot only after parity validation against weather-predict.
  systemctl --user enable --now weather-data-feed-full-snapshot.timer
  systemctl --user enable --now weather-data-feed-observations.timer
  systemctl --user enable --now weather-data-feed-daily.timer
  systemctl --user status weather-data-feed-snapshot.timer weather-data-feed-full-snapshot.timer weather-data-feed-observations.timer weather-data-feed-daily.timer

Runtime root:
  $RUNTIME_ROOT
EOF
