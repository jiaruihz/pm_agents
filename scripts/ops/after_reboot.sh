#!/usr/bin/env bash
# Controller-only post-login/reboot audit and explicit recovery wrapper.
#
# No arguments are read-only.  Mutating recovery requires --apply; restoring
# live runtimes additionally requires --confirm-live.
#
# Usage:
#   scripts/ops/after_reboot.sh
#   scripts/ops/after_reboot.sh --apply [--confirm-live]
#   scripts/ops/after_reboot.sh --recover-jrs-context --apply [--confirm-live]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

APPLY=0
CONFIRM_LIVE=0
RECOVER_JRS=0
REASON="explicit post-login recovery"

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --confirm-live) CONFIRM_LIVE=1 ;;
    --recover-jrs-context) RECOVER_JRS=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

PY=".venv/bin/python"
CTL=("scripts/ops/weather_production_ctl.py")

"$PY" scripts/ops/weather_production_manifest.py --strict >/dev/null || true
"$PY" "${CTL[@]}" health || true
"$PY" "${CTL[@]}" plan

if [[ $APPLY -ne 1 ]]; then
  printf '%s\n' "[after_reboot] read-only audit complete; no process was started or stopped"
  exit 0
fi

ARGS=(--apply --reason "$REASON")
if [[ $CONFIRM_LIVE -eq 1 ]]; then
  ARGS+=(--confirm-live)
fi

if [[ $RECOVER_JRS -eq 1 ]]; then
  "$PY" "${CTL[@]}" recover-jrs-context "${ARGS[@]}"
else
  "$PY" "${CTL[@]}" reconcile "${ARGS[@]}"
fi

"$PY" "${CTL[@]}" health
