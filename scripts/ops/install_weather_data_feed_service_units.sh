#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
retired entrypoint: N100 systemd data-feed installation is outside the current production contract

Current Mac production is managed by weather_production_ctl.py and production.yaml.
Do not reinstall these historical units:
  weather-data-feed-snapshot.service
  weather-data-feed-full-snapshot.service
  weather-data-feed-observations.service
  weather-data-feed-source-events.service
  weather-data-feed-daily.service
EOF

exit 2
