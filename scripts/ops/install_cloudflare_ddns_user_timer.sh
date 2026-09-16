#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
ENV_DIR="${HOME}/.config/pm_agent"
ENV_FILE="${ENV_DIR}/cloudflare_ddns.env"
SERVICE_FILE="${SYSTEMD_DIR}/pm-agent-cloudflare-ddns.service"
TIMER_FILE="${SYSTEMD_DIR}/pm-agent-cloudflare-ddns.timer"

mkdir -p "${SYSTEMD_DIR}" "${ENV_DIR}" "${HOME}/.cache/pm_agent"

if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  cat >"${ENV_FILE}" <<'EOF'
# Create a Cloudflare API token with:
# Zone -> DNS -> Edit, Zone -> Zone -> Read
# scoped only to weekendleague.party.
CF_API_TOKEN=
CF_ZONE_NAME=weekendleague.party
CF_RECORD_NAME=weather.weekendleague.party
CF_DDNS_TTL=300
CF_DDNS_PROXIED=false
EOF
fi
chmod 600 "${ENV_FILE}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Update Cloudflare DDNS record for pm_agent LDM host

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=${ROOT}/.venv/bin/python ${ROOT}/scripts/ops/cloudflare_ddns_update.py
EOF

cat >"${TIMER_FILE}" <<'EOF'
[Unit]
Description=Run pm_agent Cloudflare DDNS updater

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now pm-agent-cloudflare-ddns.timer
systemctl --user list-timers --no-pager pm-agent-cloudflare-ddns.timer

echo "Installed ${SERVICE_FILE}"
echo "Installed ${TIMER_FILE}"
echo "Edit ${ENV_FILE} and set CF_API_TOKEN, then run:"
echo "  systemctl --user start pm-agent-cloudflare-ddns.service"
