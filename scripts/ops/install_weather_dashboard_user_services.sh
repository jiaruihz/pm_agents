#!/usr/bin/env bash
# Retired Linux/systemd installer retained as a fail-closed compatibility stub.
#
# The current Mac API is production.yaml:weather_dashboard_api and is managed
# only by weather_production_ctl.py in the canonical JRS context.  The frontend
# is the separate com.pm-agents.weather-fe LaunchAgent.

set -euo pipefail

cat >&2 <<'EOF'
This installer is retired and made no changes.
Use:
  .venv/bin/python scripts/ops/weather_production_ctl.py health
  .venv/bin/python scripts/ops/weather_production_ctl.py plan
  .venv/bin/python scripts/ops/weather_production_ctl.py reconcile --apply
Live recovery additionally requires --confirm-live.
EOF
exit 2
