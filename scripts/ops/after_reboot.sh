#!/usr/bin/env bash
# Controller-only post-login/reboot audit and explicit recovery wrapper.
#
# No arguments are read-only.  Mutating recovery requires --apply; restoring
# live runtimes additionally requires --confirm-live.
#
# Usage:
#   scripts/ops/after_reboot.sh
#   scripts/ops/after_reboot.sh --apply [--confirm-live]
#   scripts/ops/after_reboot.sh --apply --confirm-live [--restore-manifest PATH]
#   scripts/ops/after_reboot.sh --recover-jrs-context --apply --confirm-live [--restore-manifest PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

APPLY=0
CONFIRM_LIVE=0
RECOVERY_MODE="auto"
RESTORE_MANIFEST=""
REASON="explicit post-login recovery"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --confirm-live) CONFIRM_LIVE=1 ;;
    --recover-jrs-context) RECOVERY_MODE="recover-jrs-context" ;;
    --reconcile) RECOVERY_MODE="reconcile" ;;
    --restore-manifest)
      shift
      [[ $# -gt 0 ]] || { echo "--restore-manifest requires a path" >&2; exit 2; }
      RESTORE_MANIFEST="$1"
      ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

PY=".venv/bin/python"
CTL=("scripts/ops/weather_production_ctl.py")

run_status_allow_critical() {
  local critical_rc="$1"
  shift
  local rc
  set +e
  "$@"
  rc=$?
  set -e
  if [[ $rc -ne 0 && $rc -ne $critical_rc ]]; then
    return "$rc"
  fi
}

AUDIT_MANIFEST="$(mktemp /tmp/weather-after-reboot.XXXXXX.json)"
trap 'rm -f "$AUDIT_MANIFEST"' EXIT INT TERM

# A missing post-reboot stack is expected to make these read-only commands
# report critical (manifest=1, controller=2).  Preserve real invocation errors,
# but do not let the expected critical status prevent explicit recovery.
run_status_allow_critical 1 "$PY" scripts/ops/weather_production_manifest.py \
  --strict --json-out "$AUDIT_MANIFEST" >/dev/null
run_status_allow_critical 2 "$PY" "${CTL[@]}" health
run_status_allow_critical 2 "$PY" "${CTL[@]}" plan

if [[ $APPLY -ne 1 ]]; then
  printf '%s\n' "[after_reboot] read-only audit complete; no process was started or stopped"
  exit 0
fi

ARGS=(--apply --reason "$REASON")
if [[ $CONFIRM_LIVE -eq 1 ]]; then
  ARGS+=(--confirm-live)
fi

if [[ "$RECOVERY_MODE" == "auto" ]]; then
  SESSION_COUNT="$(
    "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); observed={str(r.get("session")) for r in d.get("tmux_sessions", [])}; managed=set(d.get("desired", {}).get("managed_runtime_sessions", [])); print(len(observed & managed))' \
      "$AUDIT_MANIFEST"
  )"
  if [[ "$SESSION_COUNT" == "0" ]]; then
    RECOVERY_MODE="recover-jrs-context"
  else
    set +e
    source scripts/ops/weather_jrs_tmux_env.sh
    weather_jrs_tmux_write_probe \
      weather-data-feed-jrs \
      /Volumes/jrs/weather_data_feed_service_runtime >/dev/null 2>&1
    JRS_PROBE_RC=$?
    set -e
    if [[ $JRS_PROBE_RC -ne 0 ]]; then
      echo "[after_reboot] canonical server has managed sessions but its JRS write probe failed; refusing auto-route, inspect first or explicitly use --recover-jrs-context" >&2
      exit 2
    fi
    RECOVERY_MODE="reconcile"
  fi
  printf '%s\n' "[after_reboot] selected mode=$RECOVERY_MODE observed_sessions=$SESSION_COUNT"
fi

if [[ -n "$RESTORE_MANIFEST" && ! -r "$RESTORE_MANIFEST" ]]; then
  echo "restore manifest is not readable: $RESTORE_MANIFEST" >&2
  exit 2
fi

if [[ "$RECOVERY_MODE" == "recover-jrs-context" ]]; then
  if [[ -n "$RESTORE_MANIFEST" ]]; then
    ARGS+=(--restore-manifest "$RESTORE_MANIFEST")
  fi
  "$PY" "${CTL[@]}" recover-jrs-context "${ARGS[@]}"
else
  if [[ -n "$RESTORE_MANIFEST" ]]; then
    echo "--restore-manifest is only valid for recover-jrs-context" >&2
    exit 2
  fi
  "$PY" "${CTL[@]}" reconcile "${ARGS[@]}"
fi

"$PY" "${CTL[@]}" health
