#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
LOG_DIR="${ROOT}/runtime/_dashboard_logs"
API_PORT="${WEATHER_DASHBOARD_API_PORT:-8000}"

mkdir -p "${SYSTEMD_DIR}" "${LOG_DIR}"

cat >"${SYSTEMD_DIR}/pm-agent-weather-dashboard.service" <<EOF
[Unit]
Description=pm_agent weather dashboard API and static frontend
After=network-online.target

[Service]
WorkingDirectory=${ROOT}
Environment=WEATHER_DB_PATH=${ROOT}/runtime/weather.db
ExecStartPre=${ROOT}/.venv/bin/python ${ROOT}/scripts/ops/refresh_weather_strategy_runtime_registry.py --db-path ${ROOT}/runtime/weather.db --json-out ${LOG_DIR}/strategy_runtime_registry_refresh.json
ExecStart=${ROOT}/.venv/bin/python -m uvicorn weather_dashboard.api.app:app --host 127.0.0.1 --port ${API_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat >"${SYSTEMD_DIR}/pm-agent-weather-dashboard-refresh.service" <<EOF
[Unit]
Description=Refresh pm_agent weather strategy runtime registry

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
Environment=WEATHER_DB_PATH=${ROOT}/runtime/weather.db
ExecStart=${ROOT}/.venv/bin/python ${ROOT}/scripts/ops/refresh_weather_strategy_runtime_registry.py --db-path ${ROOT}/runtime/weather.db --json-out ${LOG_DIR}/strategy_runtime_registry_refresh.json
EOF

cat >"${SYSTEMD_DIR}/pm-agent-weather-dashboard-refresh.timer" <<'EOF'
[Unit]
Description=Run pm_agent weather dashboard registry refresh

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=20s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now pm-agent-weather-dashboard.service
systemctl --user enable --now pm-agent-weather-dashboard-refresh.timer
systemctl --user status pm-agent-weather-dashboard.service --no-pager -l | sed -n '1,40p'
systemctl --user list-timers --no-pager pm-agent-weather-dashboard-refresh.timer

echo "Weather dashboard API/static frontend: http://127.0.0.1:${API_PORT}/weather/runtime"
