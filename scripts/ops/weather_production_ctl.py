#!/usr/bin/env python3
"""Declarative control plane for the current Mac weather production stack.

The default commands are read-only. ``reconcile --apply`` only starts missing
runtimes declared in production.yaml; it never stops extra processes. Live
recovery requires both an explicit reason and ``--confirm-live``.
``stop --apply`` stops one exact ``safe`` non-live runtime and never acts on a
live or manual-recovery runtime.
``restart --apply`` may synthesize an exact-session stop followed by the
registered start contract only for ``safe`` non-live runtimes; live runtimes
require an explicit restart contract.

``recover-jrs-context --apply`` is the bounded exception for a failed canonical
JRS permission host. It persists every existing pane to the internal disk,
proves that a fresh temporary tmux server can write JRS before touching the old
server, rebuilds only the canonical server, restores the exact pane commands,
and then uses the normal desired-state reconcile path for sessions that were
already missing. It cannot grant or repair macOS TCC permissions.

A JRS probe failure is observed across controller cycles, not immediately
promoted to a stack-wide outage. The controller keeps already-running runtimes
running until ten consecutive failed cycles span ten minutes. Only then does
the permission host become critical. ``recover-jrs-context`` remains a single
bounded recovery transaction for that latter case.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_production_manifest as manifest_tool  # noqa: E402
from src.strategies.runtime.production import (  # noqa: E402
    WeatherManagedRuntimeSpec,
    WeatherProductionSpec,
    health_contract_mismatches,
    load_production_spec,
)


def _read_json(
    path: Path, *, canonical_spec: WeatherProductionSpec | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "health_path_missing"
    except (OSError, json.JSONDecodeError) as exc:
        if canonical_spec is not None and isinstance(exc, OSError):
            result = _run_tmux_checked(
                canonical_spec,
                canonical_spec.canonical_tmux_socket,
                "weather_controller_health_read",
                f"/bin/cat {shlex.quote(str(path))}",
                timeout_sec=15,
            )
            if result.returncode == 0:
                try:
                    value = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return None, "health_payload_invalid_json"
                if isinstance(value, dict):
                    return value, None
        return None, f"health_path_unreadable:{type(exc).__name__}"
    if not isinstance(value, dict):
        return None, "health_payload_not_object"
    return value, None


def _read_http_json(url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return None, f"health_url_unreadable:{type(exc).__name__}"
    if not isinstance(value, dict):
        return None, "health_payload_not_object"
    return value, None


def _pane_text(row: Mapping[str, Any] | None) -> str:
    if not row:
        return ""
    return "\n".join(
        f"{pane.get('pane_current_path') or ''} {pane.get('pane_start_command') or ''}"
        for pane in row.get("panes", [])
        if isinstance(pane, Mapping)
    )


def without_allowed_unmanaged_sessions(
    spec: WeatherProductionSpec, observed: Mapping[str, Any]
) -> dict[str, Any]:
    allowed_unmanaged = set(spec.allowed_unmanaged_sessions)
    return {
        **observed,
        "tmux_sessions": [
            row
            for row in observed.get("tmux_sessions", [])
            if str(row.get("session") or "") not in allowed_unmanaged
        ],
    }


def evaluate_production_health(
    spec: WeatherProductionSpec,
    observed: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now_epoch = time.time() if now_epoch is None else now_epoch
    session_rows = {
        str(row.get("session")): row
        for row in observed.get("tmux_sessions", [])
        if isinstance(row, Mapping) and row.get("session")
    }
    runtime_rows: list[dict[str, Any]] = []
    by_instance: dict[str, dict[str, Any]] = {}

    for runtime in spec.managed_runtimes:
        session_row = session_rows.get(runtime.tmux_session)
        issues: list[str] = []
        health_age_sec: float | None = None
        health_payload: dict[str, Any] | None = None
        contract_mismatches: list[dict[str, Any]] = []
        if runtime.desired_state == "paused":
            if session_row is not None:
                issues.append("tmux_session_present_while_paused")
            row = {
                "instance_id": runtime.instance_id,
                "tmux_session": runtime.tmux_session,
                "role": runtime.role,
                "execution_mode": runtime.execution_mode,
                "desired_state": runtime.desired_state,
                "present": session_row is not None,
                "status": "critical" if issues else "paused",
                "issues": issues,
                "health_path": str(runtime.health_path) if runtime.health_path else None,
                "health_url": runtime.health_url,
                "health_format": runtime.health_format,
                "health_age_sec": None,
                "health_generated_at_utc": None,
                "health_contract_mismatches": [],
                "checkout_root": str(runtime.checkout_root) if runtime.checkout_root else None,
                "start_script": (
                    str(runtime.resolved_start_script())
                    if runtime.resolved_start_script()
                    else None
                ),
                "recovery_policy": runtime.recovery_policy,
                "expected_live": runtime.expected_live,
                "dependencies": list(runtime.dependencies),
            }
            runtime_rows.append(row)
            by_instance[runtime.instance_id] = row
            continue
        if session_row is None:
            issues.append("tmux_session_missing")
        pane_text = _pane_text(session_row)
        if runtime.checkout_root is not None and session_row is not None:
            current_paths = {
                str(Path(str(pane.get("pane_current_path"))).resolve(strict=False))
                for pane in session_row.get("panes", [])
                if isinstance(pane, Mapping) and pane.get("pane_current_path")
            }
            expected_checkout_root = str(
                runtime.checkout_root.resolve(strict=False)
            )
            if expected_checkout_root not in current_paths:
                issues.append("checkout_root_mismatch")
        if runtime.expected_live and session_row is not None:
            if "--live" not in pane_text or "--confirm-live" not in pane_text:
                issues.append("live_flags_missing")
        if runtime.health_format == "http_json":
            health_payload, health_error = _read_http_json(str(runtime.health_url))
            if health_error:
                issues.append(health_error)
            elif runtime.accepted_health_statuses:
                status = str(health_payload.get("status") or "")
                if status not in runtime.accepted_health_statuses:
                    issues.append("health_status_unaccepted")
        elif runtime.health_path is not None:
            if not runtime.health_path.is_file():
                issues.append("health_artifact_missing")
            elif runtime.health_format == "mtime":
                health_payload = {}
                health_age_sec = max(
                    0.0, now_epoch - runtime.health_path.stat().st_mtime
                )
            else:
                health_payload, health_error = _read_json(
                    runtime.health_path, canonical_spec=spec
                )
                if health_error:
                    issues.append(health_error)
                else:
                    health_age_sec = max(
                        0.0, now_epoch - runtime.health_path.stat().st_mtime
                    )
            if health_age_sec is not None:
                if (
                    runtime.max_health_age_sec is not None
                    and health_age_sec > runtime.max_health_age_sec
                ):
                    issues.append("health_artifact_stale")
                if runtime.health_format == "json" and runtime.accepted_health_statuses:
                    status = str(health_payload.get("status") or "")
                    if status not in runtime.accepted_health_statuses:
                        issues.append("health_status_unaccepted")
                if runtime.health_format == "json" and runtime.expected_live and health_payload.get("live_enabled") is not True:
                    issues.append("health_live_not_enabled")
        if health_payload is not None and runtime.expected_health_fields:
            contract_mismatches = health_contract_mismatches(runtime, health_payload)
            if contract_mismatches:
                issues.append("health_contract_mismatch")
        row = {
            "instance_id": runtime.instance_id,
            "tmux_session": runtime.tmux_session,
            "role": runtime.role,
            "execution_mode": runtime.execution_mode,
            "desired_state": runtime.desired_state,
            "present": session_row is not None,
            "status": "critical" if issues else "healthy",
            "issues": issues,
            "health_path": str(runtime.health_path) if runtime.health_path else None,
            "health_url": runtime.health_url,
            "health_format": runtime.health_format,
            "health_age_sec": (
                round(health_age_sec, 3) if health_age_sec is not None else None
            ),
            "health_generated_at_utc": (
                health_payload.get("generated_at_utc") if health_payload else None
            ),
            "health_contract_mismatches": contract_mismatches,
            "checkout_root": str(runtime.checkout_root) if runtime.checkout_root else None,
            "start_script": (
                str(runtime.resolved_start_script())
                if runtime.resolved_start_script()
                else None
            ),
            "recovery_policy": runtime.recovery_policy,
            "expected_live": runtime.expected_live,
            "dependencies": list(runtime.dependencies),
            "startup_grace_sec": runtime.startup_grace_sec,
        }
        runtime_rows.append(row)
        by_instance[runtime.instance_id] = row

    for runtime in spec.managed_runtimes:
        row = by_instance[runtime.instance_id]
        if runtime.desired_state == "paused":
            continue
        unhealthy_dependencies = [
            dependency
            for dependency in runtime.dependencies
            if by_instance[dependency]["status"] != "healthy"
        ]
        if unhealthy_dependencies:
            row["issues"].append(
                "dependency_unhealthy:" + ",".join(unhealthy_dependencies)
            )
            row["status"] = "critical"

    desired_sessions = {item.tmux_session for item in spec.managed_runtimes}
    extra_sessions = sorted(
        {
            session
            for session in session_rows
            if not session.startswith("weather_reliability_worker_")
        }
        - desired_sessions
        - set(spec.allowed_unmanaged_sessions)
    )
    critical_findings = [
        row
        for row in observed.get("findings", [])
        if isinstance(row, Mapping) and row.get("severity") == "critical"
    ]
    critical_runtimes = [
        row["instance_id"] for row in runtime_rows if row["status"] == "critical"
    ]
    if critical_findings or critical_runtimes:
        overall = "critical"
    elif observed.get("status") == "warning" or extra_sessions:
        overall = "warning"
    else:
        overall = "healthy"
    return {
        "controller_version": "weather_production_ctl_v1",
        "generated_at_utc": observed.get("generated_at_utc"),
        "status": overall,
        "manifest_status": observed.get("status"),
        "critical_manifest_findings": critical_findings,
        "critical_runtimes": critical_runtimes,
        "extra_sessions": extra_sessions,
        "allowed_unmanaged_sessions": list(spec.allowed_unmanaged_sessions),
        "runtimes": runtime_rows,
    }


JRS_CONTEXT_HEALTH_TIMEOUT_SEC = 15
JRS_CONTEXT_HEALTH_ATTEMPTS = 3
JRS_CONTEXT_HEALTH_RETRY_DELAY_SEC = 0.25
JRS_CONTEXT_FAILURE_STREAK_THRESHOLD = 10
JRS_CONTEXT_FAILURE_DURATION_SEC = 10 * 60
JRS_CONTEXT_FAILURE_MAX_GAP_SEC = 3 * 60
JRS_CONTEXT_FAILURE_STATE_PATH = (
    Path.home()
    / "Library/Application Support/pm_agents/production_reliability"
    / "jrs_context_failure_state.json"
)
DATA_FEED_SEMANTIC_TIMEOUT_SEC = 25


def _load_jrs_context_failure_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_jrs_context_failure_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def collect_jrs_context_health(
    spec: WeatherProductionSpec,
    *,
    now_epoch: float | None = None,
    state_path: Path = JRS_CONTEXT_FAILURE_STATE_PATH,
) -> dict[str, Any]:
    """Run the canonical helper's effective write probe, not a session check."""

    helper = ROOT / "scripts/ops/weather_jrs_tmux_env.sh"
    command = (
        f"source {str(helper)!r}; "
        "weather_jrs_tmux_write_probe "
        f"{spec.canonical_tmux_socket!r} {str(spec.data_feed_runtime_root)!r}"
    )
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, JRS_CONTEXT_HEALTH_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=JRS_CONTEXT_HEALTH_TIMEOUT_SEC,
                check=False,
            )
            attempts.append(
                {
                    "attempt": attempt,
                    "returncode": result.returncode,
                    "output": result.stdout[-2000:].strip(),
                }
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "returncode": (
                        124 if isinstance(exc, subprocess.TimeoutExpired) else None
                    ),
                    "output": f"jrs_context_probe_failed:{type(exc).__name__}",
                }
            )
        if attempt < JRS_CONTEXT_HEALTH_ATTEMPTS:
            time.sleep(JRS_CONTEXT_HEALTH_RETRY_DELAY_SEC)

    now_epoch = time.time() if now_epoch is None else now_epoch
    successful = [row for row in attempts if row["returncode"] == 0]
    if successful:
        # Any successful effective write proves this is not a continuous JRS
        # outage. Reset the cross-cycle streak and keep all runtimes running.
        state = {
            "last_healthy_epoch": now_epoch,
            "failure_streak": 0,
            "first_failure_epoch": None,
            "last_failure_epoch": None,
        }
        _save_jrs_context_failure_state(state_path, state)
        status = "healthy"
        failure_streak = 0
        failure_duration_sec = 0.0
    else:
        previous = _load_jrs_context_failure_state(state_path)
        try:
            previous_streak = int(previous.get("failure_streak") or 0)
            previous_first_failure = float(previous.get("first_failure_epoch"))
            previous_last_failure = float(previous.get("last_failure_epoch"))
        except (TypeError, ValueError):
            previous_streak = 0
            previous_first_failure = now_epoch
            previous_last_failure = 0.0
        if (
            previous_streak <= 0
            or now_epoch - previous_last_failure > JRS_CONTEXT_FAILURE_MAX_GAP_SEC
        ):
            failure_streak = 1
            first_failure_epoch = now_epoch
        else:
            failure_streak = previous_streak + 1
            first_failure_epoch = previous_first_failure
        failure_duration_sec = max(0.0, now_epoch - first_failure_epoch)
        _save_jrs_context_failure_state(
            state_path,
            {
                "first_failure_epoch": first_failure_epoch,
                "last_failure_epoch": now_epoch,
                "failure_streak": failure_streak,
            },
        )
        status = (
            "critical"
            if (
                failure_streak >= JRS_CONTEXT_FAILURE_STREAK_THRESHOLD
                and failure_duration_sec >= JRS_CONTEXT_FAILURE_DURATION_SEC
            )
            else "observing"
        )
    latest = attempts[-1]
    return {
        "status": status,
        "returncode": latest["returncode"],
        "socket": spec.canonical_tmux_socket,
        "runtime_root": str(spec.data_feed_runtime_root),
        "output": latest["output"],
        "attempt_count": len(attempts),
        "successful_attempt_count": len(successful),
        "attempts": attempts,
        "failure_streak": failure_streak,
        "failure_duration_sec": round(failure_duration_sec, 3),
        "failure_streak_threshold": JRS_CONTEXT_FAILURE_STREAK_THRESHOLD,
        "failure_duration_threshold_sec": JRS_CONTEXT_FAILURE_DURATION_SEC,
        "failure_state_path": str(state_path),
    }


def attach_jrs_context_health(
    health: dict[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    health["jrs_context_health"] = dict(context)
    context_status = context.get("status")
    if context_status in {"healthy", "observing"}:
        return health
    if context_status == "critical":
        health["status"] = "critical"
    elif health.get("status") == "healthy":
        health["status"] = "warning"
    for row in health.get("runtimes", []):
        if row["instance_id"] == "weather_jrs_context_keeper":
            row["issues"] = list(row.get("issues") or []) + [
                "jrs_write_probe_failed"
                if context_status == "critical"
                else "jrs_write_probe_degraded"
            ]
            row["status"] = "critical" if context_status == "critical" else "warning"
        else:
            row["issues"] = list(row.get("issues") or []) + [
                "jrs_context_unhealthy"
                if context_status == "critical"
                else "jrs_context_degraded"
            ]
            if row.get("status") == "healthy":
                row["status"] = "warning"
    health["critical_runtimes"] = [
        row["instance_id"]
        for row in health.get("runtimes", [])
        if row.get("status") == "critical"
    ]
    return health


def build_plan(
    spec: WeatherProductionSpec, health: Mapping[str, Any]
) -> list[dict[str, Any]]:
    specs = {item.instance_id: item for item in spec.managed_runtimes}
    actions: list[dict[str, Any]] = []
    jrs_context_healthy = (
        (health.get("jrs_context_health") or {}).get("status") != "critical"
    )
    for row in health.get("runtimes", []):
        if row.get("desired_state") == "paused":
            actions.append(
                {
                    "instance_id": row["instance_id"],
                    "action": "inspect" if row.get("present") else "none",
                    "reason": (
                        "tmux_session_present_while_paused"
                        if row.get("present")
                        else "desired_state_paused"
                    ),
                    "recovery_policy": row["recovery_policy"],
                    "expected_live": row["expected_live"],
                    "start_script": row["start_script"],
                }
            )
            continue
        if row.get("present"):
            action = "inspect" if row.get("status") == "critical" else "none"
            reason = ",".join(row.get("issues") or []) or "healthy"
        elif not jrs_context_healthy:
            action = "manual_recovery_required"
            reason = "jrs_context_unhealthy"
        else:
            runtime = specs[str(row["instance_id"])]
            if runtime.recovery_policy == "manual":
                action = "manual_recovery_required"
            elif runtime.resolved_start_script() is None:
                action = "start_contract_missing"
            else:
                action = "start"
            reason = "tmux_session_missing"
        actions.append(
            {
                "instance_id": row["instance_id"],
                "action": action,
                "reason": reason,
                "recovery_policy": row["recovery_policy"],
                "expected_live": row["expected_live"],
                "start_script": row["start_script"],
            }
        )
    return actions


def _tmux(spec: WeatherProductionSpec, *args: str) -> subprocess.CompletedProcess[str]:
    return _tmux_on_socket(spec, spec.canonical_tmux_socket, *args)


def _tmux_on_socket(
    spec: WeatherProductionSpec, socket: str, *args: str
) -> subprocess.CompletedProcess[str]:
    tmux_env = os.environ.copy()
    # A newly-created permission host must not inherit the interactive zsh as
    # tmux's default shell.  Production checked sessions and start contracts
    # are bash-based, and /bin/bash is the separately audited FDA shell on this
    # host.  Pinning it here also makes prospective and canonical hosts use the
    # same responsible-process chain.
    tmux_env["SHELL"] = "/bin/bash"
    return subprocess.run(
        [str(spec.canonical_tmux_binary), "-L", socket, *args],
        cwd=ROOT,
        env=tmux_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )


def _run_tmux_checked(
    spec: WeatherProductionSpec,
    socket: str,
    session: str,
    command: str,
    *,
    timeout_sec: float,
) -> subprocess.CompletedProcess[str]:
    """Run one detached command without tmux run-shell and bridge its status."""

    # Health may be requested concurrently (for example directly and through
    # run_stack). A fixed helper session name makes the second probe fail with
    # "duplicate session", which then looks like invalid producer JSON.
    session_name = f"{session}_{os.getpid()}_{time.monotonic_ns()}"
    with tempfile.TemporaryDirectory(prefix=f"weather-{session}-") as bridge_dir:
        status_path = Path(bridge_dir) / "status"
        output_path = Path(bridge_dir) / "output"
        wrapped = (
            "set +e; "
            f"{{ {command}; }} > {shlex.quote(str(output_path))} 2>&1; "
            "rc=$?; "
            f"printf '%s\\n' \"$rc\" > {shlex.quote(str(status_path))}; "
            "exit \"$rc\""
        )
        started = _tmux_on_socket(
            spec, socket, "new-session", "-d", "-s", session_name, wrapped
        )
        if started.returncode != 0:
            return started
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            present = _tmux_on_socket(
                spec, socket, "has-session", "-t", f"={session_name}"
            )
            if present.returncode != 0:
                break
            time.sleep(0.25)
        else:
            return subprocess.CompletedProcess(
                ["tmux", "-L", socket, session], 124, "migration command timed out"
            )
        try:
            returncode = int(status_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError):
            returncode = 1
        try:
            output = output_path.read_text(encoding="utf-8")
        except OSError:
            output = "migration command exited without output bridge"
        return subprocess.CompletedProcess(
            ["tmux", "-L", socket, session_name], returncode, output
        )


def persist_recovery_manifest(
    observed: Mapping[str, Any], *, directory: Path | None = None
) -> Path:
    """Atomically save recovery state on the internal disk before disruption."""

    target_dir = directory or ROOT / "runtime/ops/weather_jrs_recovery"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = target_dir / f"prechange-{stamp}-{os.getpid()}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(observed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def collect_prospective_jrs_context_health(
    spec: WeatherProductionSpec,
) -> dict[str, Any]:
    """Prove a newly spawned tmux parent can write JRS before killing production."""

    socket = f"{spec.canonical_tmux_socket}-recovery-probe-{os.getpid()}"
    session = "weather_jrs_recovery_probe"
    probe_dirs = (
        spec.data_feed_runtime_root / "loop",
        spec.canonical_db_path.parent,
    )
    started = _tmux_on_socket(
        spec,
        socket,
        "new-session",
        "-d",
        "-s",
        session,
        "while :; do sleep 3600; done",
    )
    output = started.stdout[-2000:].strip()
    probe_returncode = started.returncode
    try:
        if started.returncode == 0:
            commands = ["umask 077"]
            for index, probe_dir in enumerate(probe_dirs):
                probe_path = probe_dir / (
                    f".prospective_tmux_probe_{os.getpid()}_{index}"
                )
                commands.extend(
                    (
                        f"mkdir -p {shlex.quote(str(probe_dir))}",
                        f"printf 'probe\\n' > {shlex.quote(str(probe_path))}",
                        f"rm -f {shlex.quote(str(probe_path))}",
                    )
                )
            commands.append(
                "dd "
                f"if={shlex.quote(str(spec.canonical_db_path))} "
                "of=/dev/null bs=1 count=1 2>/dev/null"
            )
            with tempfile.TemporaryDirectory(
                prefix="weather-jrs-prospective-probe-"
            ) as bridge_dir:
                status_path = Path(bridge_dir) / "status"
                output_path = Path(bridge_dir) / "output"
                # Preserve the first failing JRS operation long enough to
                # write its status. ``set -e`` previously exited before the
                # bridge file was created and hid the useful TCC errno.
                command = " && ".join(commands)
                session_command = (
                    "set +e; "
                    f"{{ {command}; }} > {shlex.quote(str(output_path))} 2>&1; "
                    "rc=$?; "
                    f"printf '%s\\n' \"$rc\" > {shlex.quote(str(status_path))}; "
                    "exit \"$rc\""
                )
                probed = _tmux_on_socket(
                    spec,
                    socket,
                    "new-session",
                    "-d",
                    "-s",
                    f"{session}_io",
                    session_command,
                )
                probe_returncode = probed.returncode
                output = probed.stdout[-2000:].strip()
                deadline = time.monotonic() + 15.0
                while probe_returncode == 0 and time.monotonic() < deadline:
                    present = _tmux_on_socket(
                        spec, socket, "has-session", "-t", f"={session}_io"
                    )
                    if present.returncode != 0:
                        break
                    time.sleep(0.1)
                else:
                    if probe_returncode == 0:
                        probe_returncode = 124
                        output = "prospective JRS probe timed out"
                if probe_returncode == 0:
                    try:
                        probe_returncode = int(status_path.read_text().strip())
                    except (FileNotFoundError, OSError, ValueError):
                        probe_returncode = 1
                        output = "prospective JRS probe exited without status"
                    else:
                        try:
                            output = output_path.read_text(encoding="utf-8").strip()
                        except OSError:
                            pass
    finally:
        _tmux_on_socket(spec, socket, "kill-server")
    return {
        "status": "healthy" if probe_returncode == 0 else "critical",
        "returncode": probe_returncode,
        "socket": socket,
        "runtime_roots": [str(path) for path in probe_dirs],
        "canonical_db_path": str(spec.canonical_db_path),
        "output": output,
    }


def _pane_restore_rows(observed: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in observed.get("tmux_sessions", []):
        if not isinstance(session, Mapping):
            continue
        name = str(session.get("session") or "")
        if not name or name == "weather_jrs_context_keeper":
            continue
        panes = []
        for pane in session.get("panes", []):
            if not isinstance(pane, Mapping):
                continue
            command = str(pane.get("pane_start_command") or "").strip()
            cwd = str(pane.get("pane_current_path") or ROOT)
            if not command:
                raise RuntimeError(f"missing pane_start_command for {name}")
            decoded = shlex.split(command)
            if len(decoded) == 1:
                command = decoded[0]
            panes.append({"cwd": cwd, "command": command})
        if not panes:
            raise RuntimeError(f"no restorable panes for {name}")
        rows.append({"session": name, "panes": panes})
    return rows


def recover_jrs_context(
    spec: WeatherProductionSpec,
    before: Mapping[str, Any],
    *,
    confirm_live: bool,
) -> list[dict[str, Any]]:
    """Rebuild the canonical permission host and restore the saved topology."""

    if not confirm_live:
        raise RuntimeError("recover-jrs-context requires --confirm-live")
    allowed_unmanaged = set(spec.allowed_unmanaged_sessions)
    restore_rows = [
        row
        for row in _pane_restore_rows(before)
        if row["session"] not in allowed_unmanaged
    ]
    actions: list[dict[str, Any]] = []
    prospective = collect_prospective_jrs_context_health(spec)
    actions.append({"action": "prospective_jrs_write_probe", **prospective})
    if prospective["status"] != "healthy":
        raise RuntimeError(
            "prospective tmux host cannot write JRS; canonical server preserved: "
            f"{prospective['output']}"
        )
    existing_server = _tmux(spec, "list-sessions")
    existing_output = existing_server.stdout[-1000:].strip()
    server_absent = existing_server.returncode != 0 and any(
        marker in existing_output.lower()
        for marker in ("no server running", "failed to connect to server")
    )
    if existing_server.returncode != 0 and not server_absent:
        raise RuntimeError(
            "cannot determine canonical tmux server state; server preserved: "
            f"{existing_output}"
        )
    if server_absent:
        actions.append(
            {
                "action": "kill_server",
                "returncode": 0,
                "output": "canonical server already absent",
                "skipped": True,
            }
        )
    else:
        killed = _tmux(spec, "kill-server")
        actions.append(
            {
                "action": "kill_server",
                "returncode": killed.returncode,
                "output": killed.stdout[-1000:].strip(),
            }
        )
        if killed.returncode != 0:
            raise RuntimeError(
                "failed to stop canonical tmux server; recovery aborted: "
                f"{killed.stdout[-1000:].strip()}"
            )
    started: subprocess.CompletedProcess[str] | None = None
    for _ in range(10):
        started = _tmux(
            spec,
            "new-session",
            "-d",
            "-s",
            "weather_jrs_context_keeper",
            "while :; do sleep 3600; done",
        )
        if started.returncode == 0:
            break
        time.sleep(0.2)
    assert started is not None
    if started.returncode != 0:
        raise RuntimeError(f"failed to start canonical tmux host: {started.stdout}")
    probe = collect_jrs_context_health(spec)
    actions.append({"action": "jrs_write_probe", **probe})
    if probe["status"] != "healthy":
        raise RuntimeError(f"new canonical tmux host cannot write JRS: {probe['output']}")
    for row in restore_rows:
        session = row["session"]
        for index, pane in enumerate(row["panes"]):
            verb = "new-session" if index == 0 else "new-window"
            target_args = ["-s", session] if index == 0 else ["-t", session]
            result = _tmux(
                spec,
                verb,
                "-d",
                *target_args,
                "-c",
                pane["cwd"],
                pane["command"],
            )
            actions.append(
                {
                    "action": "restore_pane",
                    "session": session,
                    "pane_index": index,
                    "returncode": result.returncode,
                    "output": result.stdout[-1000:].strip(),
                }
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"failed restoring {session} pane {index}: {result.stdout}"
                )
    return actions


def migrate_production_storage(
    spec: WeatherProductionSpec,
    before: Mapping[str, Any],
    *,
    staging_root: Path,
    confirm_live: bool,
) -> list[dict[str, Any]]:
    """Atomically move the production mount contract to the pinned NVMe UUID."""

    if not confirm_live:
        raise RuntimeError("migrate-production-storage requires --confirm-live")
    if (
        not spec.production_storage_volume_uuid
        or not spec.archive_storage_volume_uuid
    ):
        raise RuntimeError("production and archive volume UUIDs must be pinned")
    source = manifest_tool.inspect_volume_identity(spec.production_storage_root)
    target = manifest_tool.inspect_volume_identity(staging_root)
    if source.get("volume_uuid") != spec.archive_storage_volume_uuid:
        raise RuntimeError(
            "source volume identity mismatch: "
            f"expected={spec.archive_storage_volume_uuid} observed={source.get('volume_uuid')}"
        )
    if target.get("volume_uuid") != spec.production_storage_volume_uuid:
        raise RuntimeError(
            "staging volume identity mismatch: "
            f"expected={spec.production_storage_volume_uuid} observed={target.get('volume_uuid')}"
        )
    restore_rows = [
        row
        for row in _pane_restore_rows(before)
        if row["session"] not in set(spec.allowed_unmanaged_sessions)
    ]
    active_allowed = sorted(
        {
            str(row.get("session"))
            for row in before.get("tmux_sessions", [])
            if isinstance(row, Mapping)
            and row.get("session") in set(spec.allowed_unmanaged_sessions)
        }
    )
    if active_allowed:
        raise RuntimeError(
            "bounded one-shot sessions must finish before storage migration: "
            + ",".join(active_allowed)
        )
    # Reuse the already-proven canonical permission parent. macOS may deny a
    # fresh tmux parent even when the existing server still has both volumes;
    # the controller therefore stops/restores sessions without killing it.
    socket = spec.canonical_tmux_socket
    actions: list[dict[str, Any]] = []
    if True:
        probe_command = " && ".join(
            (
                f"test \"$(/usr/sbin/diskutil info -plist {shlex.quote(str(spec.production_storage_root))} | /usr/bin/plutil -extract VolumeUUID raw -)\" = {shlex.quote(spec.archive_storage_volume_uuid)}",
                f"test \"$(/usr/sbin/diskutil info -plist {shlex.quote(str(staging_root))} | /usr/bin/plutil -extract VolumeUUID raw -)\" = {shlex.quote(spec.production_storage_volume_uuid)}",
                f"test -r {shlex.quote(str(spec.canonical_db_path))}",
                f"touch {shlex.quote(str(staging_root / '.weather_storage_migration_probe'))}",
                f"rm -f {shlex.quote(str(staging_root / '.weather_storage_migration_probe'))}",
            )
        )
        probe = _run_tmux_checked(
            spec,
            socket,
            "weather_storage_migration_probe",
            probe_command,
            timeout_sec=30,
        )
        actions.append(
            {
                "action": "canonical_dual_volume_probe",
                "returncode": probe.returncode,
                "output": probe.stdout[-2000:].strip(),
            }
        )
        if probe.returncode != 0:
            raise RuntimeError(f"canonical dual-volume probe failed: {probe.stdout}")

        for row in restore_rows:
            killed = _tmux(spec, "kill-session", "-t", f"={row['session']}")
            actions.append(
                {
                    "action": "stop_session_for_storage_cutover",
                    "session": row["session"],
                    "returncode": killed.returncode,
                    "output": killed.stdout[-2000:].strip(),
                }
            )
            if killed.returncode != 0:
                raise RuntimeError(
                    f"failed to stop {row['session']}: {killed.stdout}"
                )

        target_feed_path = staging_root / spec.data_feed_runtime_root.relative_to(
            spec.production_storage_root
        )
        target_loop_path = target_feed_path / "loop"
        source_runtime = shlex.quote(str(spec.pm_runtime_root) + "/")
        target_runtime_path = staging_root / spec.pm_runtime_root.relative_to(
            spec.production_storage_root
        )
        target_runtime = shlex.quote(str(target_runtime_path) + "/")
        source_db = shlex.quote(str(spec.canonical_db_path))
        target_db = staging_root / spec.canonical_db_path.relative_to(
            spec.production_storage_root
        )
        target_db_tmp = target_db.with_name(target_db.name + ".migration.tmp")
        source_output = spec.data_feed_runtime_root / "output"
        target_output = staging_root / source_output.relative_to(
            spec.production_storage_root
        )
        source_current_yes = (
            spec.pm_runtime_root
            / "weather_edge_v1/current_yes_core_carry_tiny_live_v2"
        )
        target_current_yes = staging_root / source_current_yes.relative_to(
            spec.production_storage_root
        )
        mutable_feed_command = (
            f"for src in {shlex.quote(str(source_output))}/*; do "
            "test -d \"$src\" || continue; "
            f"dst={shlex.quote(str(target_output))}/\"${{src##*/}}\"; "
            "mkdir -p \"$dst\"; "
            "/usr/bin/rsync -aE --exclude='*/' \"$src/\" \"$dst/\"; "
            "done"
        )
        current_day_command = (
            "for day in \"$(date +%Y-%m-%d)\" \"$(date -v-1d +%Y-%m-%d)\"; do "
            f"for src in {shlex.quote(str(source_output))}/*/\"$day\" "
            f"{shlex.quote(str(spec.resolved_market_books_root() / 'batches'))}/\"$day\" "
            f"{shlex.quote(str(spec.forecast_hourly_curve_dir()))}/\"$day\"; do "
            "test -d \"$src\" || continue; "
            f"rel=\"${{src#{str(spec.production_storage_root)}/}}\"; "
            f"dst={shlex.quote(str(staging_root))}/\"$rel\"; "
            "mkdir -p \"$(dirname \"$dst\")\"; "
            "/usr/bin/rsync -aE \"$src/\" \"$dst/\"; "
            "done; "
            "stamp=\"${day//-/}\"; "
            f"for src in {shlex.quote(str(spec.strategy_paper_snapshot_dir()))}/snapshot_\"$stamp\"* "
            f"{shlex.quote(str(spec.resolved_market_ladder_snapshot_root()))}/*\"$stamp\"*; do "
            "test -f \"$src\" || continue; "
            f"rel=\"${{src#{str(spec.production_storage_root)}/}}\"; "
            f"dst={shlex.quote(str(staging_root))}/\"$rel\"; "
            "mkdir -p \"$(dirname \"$dst\")\"; "
            "/usr/bin/rsync -aE \"$src\" \"$dst\"; "
            "done; done"
        )
        final_command = " && ".join(
            (
                "set -eu",
                f"mkdir -p {shlex.quote(str(target_feed_path))} {shlex.quote(str(target_loop_path))} {shlex.quote(str(target_runtime_path))}",
                mutable_feed_command,
                current_day_command,
                f"mkdir -p {shlex.quote(str(target_current_yes))}",
                f"/usr/bin/rsync -aE {shlex.quote(str(source_current_yes) + '/')} {shlex.quote(str(target_current_yes) + '/')}",
                f"/usr/bin/rsync -aE --exclude='*/' --exclude='weather.db' --exclude='weather.db-wal' --exclude='weather.db-shm' {source_runtime} {target_runtime}",
                f"rm -f {shlex.quote(str(target_db_tmp))}",
                f"/usr/bin/sqlite3 {source_db} \".timeout 30000\" \".backup '{str(target_db_tmp).replace("'", "''")}'\"",
                f"test \"$(/usr/bin/sqlite3 {shlex.quote(str(target_db_tmp))} 'PRAGMA quick_check;')\" = ok",
                f"mv -f {shlex.quote(str(target_db_tmp))} {shlex.quote(str(target_db))}",
                "/bin/sync",
                f"/usr/sbin/diskutil rename {shlex.quote(str(spec.production_storage_root))} {shlex.quote(spec.archive_storage_root.name)}",
                f"/usr/sbin/diskutil rename {shlex.quote(str(staging_root))} {shlex.quote(spec.production_storage_root.name)}",
                f"test \"$(/usr/sbin/diskutil info -plist {shlex.quote(str(spec.production_storage_root))} | /usr/bin/plutil -extract VolumeUUID raw -)\" = {shlex.quote(spec.production_storage_volume_uuid)}",
                f"test \"$(/usr/sbin/diskutil info -plist {shlex.quote(str(spec.archive_storage_root))} | /usr/bin/plutil -extract VolumeUUID raw -)\" = {shlex.quote(spec.archive_storage_volume_uuid)}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'pm_agents/research'))} {shlex.quote(str(spec.production_storage_root / 'pm_agents/research'))}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'pm_agents/archive'))} {shlex.quote(str(spec.production_storage_root / 'pm_agents/archive'))}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'pm_agents/backups'))} {shlex.quote(str(spec.production_storage_root / 'pm_agents/backups'))}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'weather_data_feed_service_runtime/history'))} {shlex.quote(str(spec.production_storage_root / 'weather_data_feed_service_runtime/history'))}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'weather_data_feed_service_runtime/migration_archive'))} {shlex.quote(str(spec.production_storage_root / 'weather_data_feed_service_runtime/migration_archive'))}",
                f"ln -s {shlex.quote(str(spec.archive_storage_root / 'weather_data_feed_service_runtime/research'))} {shlex.quote(str(spec.production_storage_root / 'weather_data_feed_service_runtime/research'))}",
            )
        )
        migrated = _run_tmux_checked(
            spec,
            socket,
            "weather_storage_migration_cutover",
            final_command,
            timeout_sec=3600,
        )
        actions.append(
            {
                "action": "copy_verify_and_swap_mounts",
                "returncode": migrated.returncode,
                "output": migrated.stdout[-4000:].strip(),
            }
        )
        if migrated.returncode != 0:
            raise RuntimeError(
                "storage cutover failed; canonical permission host preserved at "
                f"socket={socket}: {migrated.stdout}"
            )
    probe = collect_jrs_context_health(spec)
    actions.append({"action": "new_production_volume_probe", **probe})
    if probe["status"] != "healthy":
        raise RuntimeError(f"new production volume probe failed: {probe['output']}")
    for row in restore_rows:
        for index, pane in enumerate(row["panes"]):
            result = _tmux(
                spec,
                "new-session" if index == 0 else "new-window",
                "-d",
                *(["-s", row["session"]] if index == 0 else ["-t", row["session"]]),
                "-c",
                pane["cwd"],
                pane["command"],
            )
            actions.append(
                {
                    "action": "restore_pane",
                    "session": row["session"],
                    "pane_index": index,
                    "returncode": result.returncode,
                    "output": result.stdout[-1000:].strip(),
                }
            )
            if result.returncode != 0:
                raise RuntimeError(f"failed restoring {row['session']} pane {index}: {result.stdout}")
    return actions


def summarize_data_feed_semantics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the broad legacy feed doctor into production-relevant health."""

    critical_reasons: list[str] = []
    warnings: list[str] = []
    expected_ok = {
        "live_cross_observation_state": "status",
        "snapshot_parity": "status",
        "snapshot_source_model": "status",
    }
    for component, field in expected_ok.items():
        row = payload.get(component)
        if not isinstance(row, Mapping) or row.get(field) != "ok":
            critical_reasons.append(f"{component}_not_ok")
    observation_cache = payload.get("observation_cache")
    observation_status = (
        observation_cache.get("status")
        if isinstance(observation_cache, Mapping)
        else None
    )
    if observation_status == "warn":
        inactive_count = int(observation_cache.get("inactive_invalid_record_count") or 0)
        warnings.append(f"observation_cache_inactive_stale:{inactive_count}")
    elif observation_status != "ok":
        critical_reasons.append("observation_cache_not_ok")
    orderbooks = payload.get("orderbook_snapshots")
    if not isinstance(orderbooks, Mapping):
        critical_reasons.append("orderbook_snapshots_missing")
    elif orderbooks.get("missing") or orderbooks.get("stale"):
        critical_reasons.append("orderbook_snapshots_stale_or_missing")
    source_model = payload.get("snapshot_source_model")
    if isinstance(source_model, Mapping) and source_model.get("fallback_detected"):
        critical_reasons.append("forecast_source_fallback_detected")

    curves = payload.get("forecast_hourly_curves")
    curve_status = curves.get("status") if isinstance(curves, Mapping) else None
    if curve_status == "incomplete_city_target_coverage":
        examples = curves.get("missing_city_target_examples") or []
        labels = [
            f"{row.get('city')}@{row.get('target_date')}"
            for row in examples
            if isinstance(row, Mapping)
        ]
        warnings.append(
            "forecast_hourly_curves_incomplete:"
            + (",".join(labels) if labels else str(curves.get("missing_city_target_count") or "unknown"))
        )
    elif curve_status != "ok":
        critical_reasons.append(f"forecast_hourly_curves:{curve_status or 'missing'}")

    book_coverage = payload.get("snapshot_orderbook_coverage")
    book_status = (
        book_coverage.get("status") if isinstance(book_coverage, Mapping) else None
    )
    if book_status == "incomplete" and book_coverage.get("target_ok_count", 0) > 0:
        warnings.append(
            "snapshot_orderbook_coverage_incomplete:"
            f"{book_coverage.get('target_incomplete_count', 0)}/"
            f"{book_coverage.get('target_count', 0)}"
        )
    elif book_status != "ok":
        critical_reasons.append(f"snapshot_orderbook_coverage:{book_status or 'missing'}")

    coverage = payload.get("snapshot_city_state_coverage")
    if isinstance(coverage, Mapping) and coverage.get("status") != "ok":
        trading = coverage.get("missing_required_trading_cities") or []
        missing = coverage.get("missing_record_cities") or []
        warnings.append(
            "snapshot_city_state_coverage:"
            + (
                ",".join(map(str, trading))
                if trading
                else ",".join(map(str, missing))
                if missing
                else str(coverage.get("status"))
            )
        )
    if critical_reasons:
        status = "critical"
    elif warnings:
        status = "warning"
    else:
        status = "healthy"
    return {
        "status": status,
        "critical_reasons": critical_reasons,
        "warnings": warnings,
        # This producer is disabled in the current data-feed command; the
        # active replacement is weather_live_cross_observations.
        "ignored_legacy_checks": ["fast_observation_state"],
        "checked_at_utc": payload.get("checked_at_utc"),
    }


def collect_data_feed_semantics(
    spec: WeatherProductionSpec | None = None,
) -> dict[str, Any]:
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/ops/weather_data_feed_prod_health_check.py"),
    ]
    try:
        if spec is None:
            result = subprocess.run(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=DATA_FEED_SEMANTIC_TIMEOUT_SEC,
                check=False,
            )
        else:
            result = _run_tmux_checked(
                spec,
                spec.canonical_tmux_socket,
                "weather_controller_feed_health",
                shlex.join(command),
                timeout_sec=DATA_FEED_SEMANTIC_TIMEOUT_SEC,
            )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("data-feed health payload is not an object")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        detail = type(exc).__name__
        if isinstance(exc, subprocess.TimeoutExpired):
            detail += f":{DATA_FEED_SEMANTIC_TIMEOUT_SEC}s"
        return {
            "status": "critical",
            "critical_reasons": [f"data_feed_health_command_failed:{detail}"],
            "warnings": [],
            "ignored_legacy_checks": [],
            "checked_at_utc": None,
        }
    return summarize_data_feed_semantics(payload)


def attach_semantic_health(
    health: dict[str, Any], semantic: Mapping[str, Any]
) -> dict[str, Any]:
    health["data_feed_semantic_health"] = dict(semantic)
    if semantic.get("status") == "critical":
        health["status"] = "critical"
    elif semantic.get("status") == "warning" and health.get("status") == "healthy":
        health["status"] = "warning"
    return health


def _print_human(payload: Mapping[str, Any], *, include_plan: bool = False) -> None:
    print(f"weather production: {str(payload.get('status')).upper()}")
    print(f"manifest: {payload.get('manifest_status')}")
    context = payload.get("jrs_context_health") or {}
    print(f"jrs_context: {context.get('status', 'unknown')}")
    for row in payload.get("runtimes", []):
        marker = {
            "healthy": "OK",
            "warning": "WARNING",
            "critical": "CRITICAL",
        }.get(str(row.get("status")), "UNKNOWN")
        age = (
            f" age={row['health_age_sec']:.0f}s"
            if row.get("health_age_sec") is not None
            else ""
        )
        issues = ",".join(row.get("issues") or [])
        suffix = f" issues={issues}" if issues else ""
        print(f"[{marker}] {row['instance_id']} session={row['tmux_session']}{age}{suffix}")
    if payload.get("extra_sessions"):
        print("extra_sessions=" + ",".join(payload["extra_sessions"]))
    semantic = payload.get("data_feed_semantic_health") or {}
    print(f"data_feed_semantics: {semantic.get('status', 'unknown')}")
    for warning in semantic.get("warnings", []):
        print(f"[WARNING] {warning}")
    for reason in semantic.get("critical_reasons", []):
        print(f"[CRITICAL] {reason}")
    for row in payload.get("recovery_preflight", []):
        if row.get("status") == "error":
            print(
                f"[PREFLIGHT ERROR] {row.get('instance_id')} "
                f"reason={row.get('reason')}"
            )
    if include_plan:
        for action in payload.get("plan", []):
            if action["action"] != "none":
                print(
                    f"[PLAN] {action['instance_id']} action={action['action']} "
                    f"reason={action['reason']}"
                )


def _ensure_release_runtime_bindings(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
) -> dict[str, Any] | None:
    """Provision declared non-Git release bindings before stopping a runtime."""
    if runtime.release_id is None:
        return None
    release = spec.release(runtime.release_id)
    try:
        checkout_resolved = release.checkout_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": f"checkout_runtime_binding_root_unreadable:{release.checkout_root}",
        }
    missing: list[tuple[str, Path, Path]] = []
    for binding, target, source in spec.release_runtime_binding_paths(release):
        relative_target = target.relative_to(release.checkout_root)
        cursor = release.checkout_root
        for part in relative_target.parts[:-1]:
            cursor /= part
            if os.path.lexists(cursor) and (
                cursor.is_symlink() or not cursor.is_dir()
            ):
                return {
                    "instance_id": runtime.instance_id,
                    "status": "error",
                    "reason": (
                        f"checkout_runtime_binding_parent_unsafe:{binding}:{cursor}"
                    ),
                }
        try:
            parent_resolved = target.parent.resolve(strict=False)
        except (OSError, RuntimeError):
            parent_resolved = None
        if parent_resolved is None or not parent_resolved.is_relative_to(
            checkout_resolved
        ):
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": (
                    f"checkout_runtime_binding_parent_escape:{binding}:{target.parent}"
                ),
            }
        if binding == "operational_venv":
            python = source / "bin/python"
            source_healthy = (
                source.is_dir() and python.is_file() and os.access(python, os.X_OK)
            )
        else:
            source_healthy = source.is_file() and os.access(source, os.R_OK)
        if not source_healthy:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": (
                    f"checkout_runtime_binding_source_unhealthy:{binding}:{source}"
                ),
            }

        if not os.path.lexists(target):
            missing.append((binding, target, source))
            continue
        if not target.is_symlink():
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": f"checkout_runtime_binding_conflict:{binding}:{target}",
            }
        try:
            observed = target.resolve(strict=True)
            expected = source.resolve(strict=True)
        except (OSError, RuntimeError):
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": f"checkout_runtime_binding_unreadable:{binding}:{target}",
            }
        if observed != expected:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": (
                    f"checkout_runtime_binding_mismatch:{binding}:"
                    f"expected={expected}:observed={observed}"
                ),
            }

    for binding, target, source in missing:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(
                source,
                target_is_directory=binding == "operational_venv",
            )
        except OSError as exc:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": (
                    f"checkout_runtime_binding_create_failed:{binding}:"
                    f"{type(exc).__name__}:{target}"
                ),
            }
    return None


def _checkout_start_preflight(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
) -> dict[str, Any] | None:
    """Validate a git production checkout before an existing session is stopped."""
    checkout = runtime.checkout_root
    script = runtime.resolved_start_script()
    if script is not None:
        if not script.is_file():
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": f"start_script_missing:{script}",
            }
        if not os.access(script, os.X_OK):
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": f"start_script_not_executable:{script}",
            }
    if checkout is None:
        return None
    if not (checkout / ".git").exists():
        if runtime.release_id:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": f"checkout_release_missing:{checkout}",
            }
        return None
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": "checkout_git_identity_unreadable",
        }
    observed_sha = head.stdout.strip()
    if runtime.release_id:
        expected_sha = spec.release(runtime.release_id).expected_repo_sha
        if observed_sha != expected_sha:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": (
                    "checkout_release_sha_mismatch:"
                    f"expected={expected_sha}:observed={observed_sha}"
                ),
            }
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": "checkout_cleanliness_unreadable",
        }
    if status.stdout.strip():
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": "checkout_dirty_tracked",
        }
    if script is not None and script.exists() and runtime.release_id is not None:
        release = spec.release(runtime.release_id)
        if "operational_env" not in release.runtime_bindings:
            try:
                script_text = script.read_text(encoding="utf-8")
            except OSError:
                script_text = ""
            env_path = checkout / ".env"
            if ".env" in script_text and not env_path.exists():
                return {
                    "instance_id": runtime.instance_id,
                    "status": "error",
                    "reason": f"checkout_bootstrap_missing_env:{env_path}",
                }
    binding_error = _ensure_release_runtime_bindings(spec, runtime)
    if binding_error is not None:
        return binding_error
    python = checkout / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": f"checkout_bootstrap_missing_venv:{python}",
        }
    return None


def collect_recovery_preflight(
    spec: WeatherProductionSpec,
    instance_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate every runtime that a recovery transaction may need to start.

    This is intentionally completed before ``recover-jrs-context`` can kill the
    canonical server. Existing JSON health is used as a schema witness when a
    runtime declares expected health fields; age is ignored for this check.
    """

    rows: list[dict[str, Any]] = []
    for runtime in spec.managed_runtimes:
        if runtime.desired_state != "running":
            continue
        if instance_ids is not None and runtime.instance_id not in instance_ids:
            continue
        if runtime.recovery_policy == "manual":
            rows.append(
                {
                    "instance_id": runtime.instance_id,
                    "status": "skipped",
                    "reason": "manual_recovery_contract",
                }
            )
            continue
        error = _checkout_start_preflight(spec, runtime)
        if error is not None:
            rows.append(error)
            continue
        if (
            runtime.expected_health_fields
            and runtime.health_format == "json"
            and runtime.health_path is not None
            and runtime.health_path.exists()
        ):
            payload, health_error = _read_json(
                runtime.health_path, canonical_spec=spec
            )
            if health_error:
                rows.append(
                    {
                        "instance_id": runtime.instance_id,
                        "status": "error",
                        "reason": f"health_contract_probe_failed:{health_error}",
                    }
                )
                continue
            assert payload is not None
            mismatches = health_contract_mismatches(runtime, payload)
            if mismatches:
                rows.append(
                    {
                        "instance_id": runtime.instance_id,
                        "status": "error",
                        "reason": "health_contract_incompatible_with_last_artifact",
                        "mismatches": mismatches,
                    }
                )
                continue
        rows.append(
            {
                "instance_id": runtime.instance_id,
                "status": "passed",
                "reason": "release_start_contract_ready",
            }
        )
    return rows


MARKET_PROXY_RUNTIME_ENV_KEYS = (
    "WEATHER_DATA_FEED_MARKET_PROXY",
    "WEATHER_PREDICT_MARKET_PROXY",
    "WEATHER_PREDICT_PROXY",
    "POLYMARKET_PROXY_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _runtime_launch_env(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
    *,
    confirm_live: bool,
) -> dict[str, str]:
    env = os.environ.copy()
    env["WEATHER_JRS_TMUX_MUTATION_AUTHORITY"] = "controller"
    production_config = str(
        ROOT / "src/strategies/runtime/production.yaml"
    )
    env["WEATHER_PRODUCTION_CONFIG"] = production_config
    env.update(dict(runtime.launch_environment))
    update_environment = _tmux(
        spec,
        "show-options",
        "-gv",
        "update-environment",
    )
    if update_environment.returncode != 0:
        raise RuntimeError(
            "failed to inspect canonical tmux update-environment: "
            f"{update_environment.stdout[-500:].strip()}"
        )
    update_keys = shlex.split(update_environment.stdout.strip())
    required_update_keys = [
        "WEATHER_PRODUCTION_CONFIG",
        *dict(runtime.launch_environment),
    ]
    if any(key not in update_keys for key in required_update_keys):
        update_keys.extend(key for key in required_update_keys if key not in update_keys)
        update_result = _tmux(
            spec,
            "set-option",
            "-g",
            "update-environment",
            " ".join(update_keys),
        )
        if update_result.returncode != 0:
            raise RuntimeError(
                "failed to register canonical tmux production contract environment: "
                f"{update_result.stdout[-500:].strip()}"
            )
    pinned_config = _tmux(
        spec,
        "set-environment",
        "-g",
        "WEATHER_PRODUCTION_CONFIG",
        production_config,
    )
    if pinned_config.returncode != 0:
        raise RuntimeError(
            "failed to pin canonical tmux production contract: "
            f"{pinned_config.stdout[-500:].strip()}"
        )
    if runtime.uses_market_proxy:
        proxy_url = spec.market_proxy_default_url
        for key in MARKET_PROXY_RUNTIME_ENV_KEYS:
            env[key] = proxy_url
            pinned = _tmux(spec, "set-environment", "-g", key, proxy_url)
            if pinned.returncode != 0:
                raise RuntimeError(
                    f"failed to pin canonical tmux proxy environment: {key}: "
                    f"{pinned.stdout[-500:].strip()}"
                )
    if confirm_live:
        env["WEATHER_STRATEGY_CONFIRM_LIVE"] = "1"
    return env


def _run_start(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
    *,
    confirm_live: bool,
    launch_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if runtime.desired_state != "running":
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "desired_state_paused"}
    script = runtime.resolved_start_script()
    if script is None:
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "start_contract_missing"}
    if runtime.recovery_policy == "manual":
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "manual_recovery_required"}
    if runtime.expected_live and not confirm_live:
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "confirm_live_required"}
    if not script.exists():
        return {"instance_id": runtime.instance_id, "status": "error", "reason": f"start_script_missing:{script}"}
    preflight_error = _checkout_start_preflight(spec, runtime)
    if preflight_error is not None:
        return preflight_error
    try:
        env = launch_env or _runtime_launch_env(
            spec, runtime, confirm_live=confirm_live
        )
    except RuntimeError as exc:
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": str(exc),
        }
    started_at_epoch = time.time()
    result = subprocess.run(
        [str(script)],
        cwd=str(runtime.checkout_root or ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode == 0:
        status = "started"
    else:
        session_present = _tmux(
            spec, "has-session", "-t", f"={runtime.tmux_session}"
        ).returncode == 0
        status = "warming" if session_present else "error"
    return {
        "instance_id": runtime.instance_id,
        "status": status,
        "returncode": result.returncode,
        "output": result.stdout[-2000:].strip(),
        "started_at_epoch": started_at_epoch,
        "startup_grace_sec": runtime.startup_grace_sec,
        "session_present_after_start": (
            True if result.returncode == 0 else session_present
        ),
    }


def _startup_artifact_refreshed(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
    started_at_epoch: float,
) -> bool:
    if runtime.health_path is None:
        return True
    try:
        mtime_ns = runtime.health_path.stat().st_mtime_ns
    except OSError:
        python = spec.operational_repo_root / ".venv/bin/python"
        command = shlex.join(
            [
                str(python),
                "-c",
                "import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)",
                str(runtime.health_path),
            ]
        )
        result = _run_tmux_checked(
            spec,
            spec.canonical_tmux_socket,
            "weather_controller_health_mtime",
            command,
            timeout_sec=15,
        )
        if result.returncode != 0:
            return False
        try:
            mtime_ns = int(result.stdout.strip())
        except ValueError:
            return False
    return mtime_ns > int(started_at_epoch * 1_000_000_000)


def wait_for_startup_convergence(
    spec: WeatherProductionSpec,
    actions: list[dict[str, Any]],
    *,
    poll_interval_sec: float = 1.0,
) -> None:
    """Turn launcher success into verified runtime freshness.

    A launcher may return non-zero after creating a healthy tmux session, or it
    may return zero before the first health artifact is published. Both remain
    ``warming`` until the runtime is present, healthy, and has produced
    post-start evidence within its declared grace period.
    """

    runtimes = {runtime.instance_id: runtime for runtime in spec.managed_runtimes}
    pending: dict[str, dict[str, Any]] = {}
    now = time.monotonic()
    for action in actions:
        if action.get("status") not in {"started", "warming"}:
            continue
        runtime = runtimes[str(action["instance_id"])]
        grace = max(0.0, float(runtime.startup_grace_sec))
        action["status"] = "warming"
        action["converged"] = False
        pending[runtime.instance_id] = {
            "action": action,
            "runtime": runtime,
            "deadline": now + grace,
        }

    while pending:
        snapshot = manifest_tool.collect_manifest(spec)
        report = evaluate_production_health(spec, snapshot)
        by_instance = {
            str(row["instance_id"]): row for row in report.get("runtimes", [])
        }
        current = time.monotonic()
        for instance_id, state in list(pending.items()):
            action = state["action"]
            runtime = state["runtime"]
            row = by_instance[instance_id]
            if not row.get("present"):
                action["status"] = "error"
                action["reason"] = "session_exited_during_startup"
                action["final_issues"] = list(row.get("issues") or [])
                pending.pop(instance_id)
                continue
            artifact_refreshed = _startup_artifact_refreshed(
                spec, runtime, float(action["started_at_epoch"])
            )
            if row.get("status") == "healthy" and artifact_refreshed:
                action["status"] = "started"
                action["converged"] = True
                action["final_issues"] = []
                pending.pop(instance_id)
                continue
            if current >= float(state["deadline"]):
                action["status"] = "error"
                action["reason"] = "startup_convergence_timeout"
                action["artifact_refreshed"] = artifact_refreshed
                action["final_issues"] = list(row.get("issues") or [])
                pending.pop(instance_id)
        if pending:
            next_deadline = min(float(state["deadline"]) for state in pending.values())
            time.sleep(max(0.0, min(poll_interval_sec, next_deadline - time.monotonic())))


def _run_restart(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
    *,
    confirm_live: bool,
) -> dict[str, Any]:
    script = runtime.resolved_restart_script()
    if script is None:
        if runtime.expected_live or runtime.recovery_policy != "safe":
            return {
                "instance_id": runtime.instance_id,
                "status": "blocked",
                "reason": "explicit_restart_contract_required",
            }
        if runtime.resolved_start_script() is None:
            return {
                "instance_id": runtime.instance_id,
                "status": "blocked",
                "reason": "start_contract_missing",
            }
        preflight_error = _checkout_start_preflight(spec, runtime)
        if preflight_error is not None:
            return preflight_error
        try:
            launch_env = _runtime_launch_env(
                spec, runtime, confirm_live=False
            )
        except RuntimeError as exc:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": str(exc),
            }
        stopped, session_was_missing = _stop_registered_session(
            spec, runtime.tmux_session
        )
        if stopped.returncode != 0 and not session_was_missing:
            return {
                "instance_id": runtime.instance_id,
                "status": "error",
                "reason": "stop_before_restart_failed",
                "returncode": stopped.returncode,
                "output": stopped.stdout[-2000:].strip(),
            }
        started = _run_start(
            spec,
            runtime,
            confirm_live=False,
            launch_env=launch_env,
        )
        return {
            **started,
            "status": (
                "restarted" if started.get("status") == "started" else started.get("status")
            ),
            "restart_mode": (
                "controller_registered_start_missing_session"
                if session_was_missing
                else "controller_stop_then_registered_start"
            ),
        }
    preflight_error = _checkout_start_preflight(spec, runtime)
    if preflight_error is not None:
        return preflight_error
    if runtime.expected_live and not confirm_live:
        return {
            "instance_id": runtime.instance_id,
            "status": "blocked",
            "reason": "confirm_live_required",
        }
    if not script.exists():
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": f"restart_script_missing:{script}",
        }
    try:
        launch_env = _runtime_launch_env(
            spec, runtime, confirm_live=confirm_live
        )
    except RuntimeError as exc:
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": str(exc),
        }
    result = subprocess.run(
        [str(script)],
        cwd=str(runtime.checkout_root or ROOT),
        env=launch_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "instance_id": runtime.instance_id,
        "status": "restarted" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "output": result.stdout[-2000:].strip(),
    }


def _run_stop(
    spec: WeatherProductionSpec,
    runtime: WeatherManagedRuntimeSpec,
    *,
    confirm_live: bool = False,
) -> dict[str, Any]:
    """Stop one exact registered tmux session through controller authority."""

    is_live = runtime.expected_live or runtime.execution_mode == "live"
    if is_live and not confirm_live:
        return {
            "instance_id": runtime.instance_id,
            "status": "blocked",
            "reason": "confirm_live_required",
        }
    if is_live and runtime.recovery_policy != "guarded_live":
        return {
            "instance_id": runtime.instance_id,
            "status": "blocked",
            "reason": "guarded_live_recovery_policy_required",
        }
    if is_live and runtime.resolved_restart_script() is None:
        return {
            "instance_id": runtime.instance_id,
            "status": "blocked",
            "reason": "explicit_restart_contract_required",
        }
    if not is_live and runtime.recovery_policy != "safe":
        return {
            "instance_id": runtime.instance_id,
            "status": "blocked",
            "reason": "safe_recovery_policy_required",
        }
    stopped, _ = _stop_registered_session(spec, runtime.tmux_session)
    if stopped.returncode != 0:
        return {
            "instance_id": runtime.instance_id,
            "status": "error",
            "reason": "stop_failed",
            "returncode": stopped.returncode,
            "output": stopped.stdout[-2000:].strip(),
        }
    return {
        "instance_id": runtime.instance_id,
        "status": "stopped",
        "tmux_session": runtime.tmux_session,
    }


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_registered_session(
    spec: WeatherProductionSpec,
    session: str,
    *,
    quiescence_timeout_sec: float = 10.0,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Kill one exact session and wait for its observed pane processes to exit."""

    panes = _tmux(spec, "list-panes", "-t", f"={session}", "-F", "#{pane_pid}")
    pane_pids = {
        int(value)
        for value in panes.stdout.splitlines()
        if value.strip().isdigit()
    }
    stopped = _tmux(spec, "kill-session", "-t", f"={session}")
    session_was_missing = (
        stopped.returncode != 0 and "can't find session" in stopped.stdout.lower()
    )
    if stopped.returncode != 0 or not pane_pids:
        return stopped, session_was_missing
    deadline = time.monotonic() + max(0.0, quiescence_timeout_sec)
    alive = {pid for pid in pane_pids if _pid_is_alive(pid)}
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = {pid for pid in alive if _pid_is_alive(pid)}
    if alive:
        return (
            subprocess.CompletedProcess(
                stopped.args,
                1,
                "session removed but pane processes remained alive: "
                + ",".join(str(pid) for pid in sorted(alive)),
                "",
            ),
            False,
        )
    return stopped, False


def _ordered_start_items(
    spec: WeatherProductionSpec, plan: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Order missing runtimes so producers start before their consumers."""

    specs = {item.instance_id: item for item in spec.managed_runtimes}
    remaining = {
        str(item["instance_id"]): item
        for item in plan
        if item.get("action") == "start"
    }
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [
            instance_id
            for instance_id in remaining
            if not (set(specs[instance_id].dependencies) & set(remaining))
        ]
        if not ready:
            raise RuntimeError(
                "cyclic recovery dependencies: " + ",".join(sorted(remaining))
            )
        for instance_id in ready:
            ordered.append(remaining.pop(instance_id))
    return ordered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-spec", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    health = sub.add_parser("health")
    health.add_argument("--json", action="store_true")
    plan = sub.add_parser("plan")
    plan.add_argument("--json", action="store_true")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--json", action="store_true")
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--confirm-live", action="store_true")
    reconcile.add_argument("--reason")
    restart = sub.add_parser("restart")
    restart.add_argument("--json", action="store_true")
    restart.add_argument("--apply", action="store_true")
    restart.add_argument("--instance", required=True)
    restart.add_argument("--confirm-live", action="store_true")
    restart.add_argument("--reason")
    stop = sub.add_parser("stop")
    stop.add_argument("--json", action="store_true")
    stop.add_argument("--apply", action="store_true")
    stop.add_argument("--instance", required=True)
    stop.add_argument("--confirm-live", action="store_true")
    stop.add_argument("--reason")
    recover = sub.add_parser("recover-jrs-context")
    recover.add_argument("--json", action="store_true")
    recover.add_argument("--apply", action="store_true")
    recover.add_argument("--confirm-live", action="store_true")
    recover.add_argument("--reason")
    recover.add_argument(
        "--restore-manifest",
        type=Path,
        help="saved pre-change manifest used only when retrying an interrupted recovery",
    )
    migrate = sub.add_parser("migrate-production-storage")
    migrate.add_argument("--json", action="store_true")
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--confirm-live", action="store_true")
    migrate.add_argument("--reason")
    migrate.add_argument("--staging-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = load_production_spec(args.production_spec)
    before = manifest_tool.collect_manifest(spec)
    health = evaluate_production_health(spec, before)
    health = attach_jrs_context_health(health, collect_jrs_context_health(spec))
    health = attach_semantic_health(health, collect_data_feed_semantics(spec))
    health["plan"] = build_plan(spec, health)
    health["command"] = args.command
    health["apply"] = bool(getattr(args, "apply", False))
    if args.command == "migrate-production-storage":
        health["staging_root"] = str(args.staging_root)
        if args.apply:
            if not args.reason:
                raise SystemExit("migrate-production-storage --apply requires --reason")
            recovery_manifest_path = persist_recovery_manifest(before)
            try:
                actions = migrate_production_storage(
                    spec,
                    before,
                    staging_root=args.staging_root,
                    confirm_live=bool(args.confirm_live),
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"{exc}; recovery_manifest={recovery_manifest_path}"
                ) from exc
            time.sleep(3)
            after = manifest_tool.collect_manifest(spec)
            after = manifest_tool.compare_prechange_manifest(
                after, without_allowed_unmanaged_sessions(spec, before)
            )
            health = evaluate_production_health(spec, after)
            health = attach_jrs_context_health(
                health, collect_jrs_context_health(spec)
            )
            health = attach_semantic_health(
                health, collect_data_feed_semantics(spec)
            )
            health.update(
                {
                    "command": args.command,
                    "apply": True,
                    "reason": args.reason,
                    "staging_root": str(args.staging_root),
                    "actions": actions,
                    "recovery_manifest": str(recovery_manifest_path),
                }
            )
            health["plan"] = build_plan(spec, health)
    if args.command == "stop":
        specs = {item.instance_id: item for item in spec.managed_runtimes}
        runtime = specs.get(args.instance)
        if runtime is None:
            raise SystemExit(f"unknown managed runtime: {args.instance}")
        health["target_instance"] = args.instance
        if args.apply:
            if not args.reason:
                raise SystemExit("stop --apply requires --reason")
            if (health.get("jrs_context_health") or {}).get("status") == "critical":
                raise SystemExit("stop blocked: jrs_context_unhealthy")
            action = _run_stop(
                spec,
                runtime,
                confirm_live=bool(args.confirm_live),
            )
            time.sleep(2)
            after = manifest_tool.collect_manifest(spec)
            after = manifest_tool.compare_prechange_manifest(
                after,
                before,
                allow_missing_sessions={runtime.tmux_session},
            )
            health = evaluate_production_health(spec, after)
            health = attach_jrs_context_health(
                health, collect_jrs_context_health(spec)
            )
            health = attach_semantic_health(
                health, collect_data_feed_semantics(spec)
            )
            health.update(
                {
                    "command": args.command,
                    "apply": True,
                    "reason": args.reason,
                    "target_instance": args.instance,
                    "actions": [action],
                }
            )
            health["plan"] = build_plan(spec, health)
            if action.get("status") in {"blocked", "error"}:
                health["status"] = "critical"
    if args.command == "restart":
        specs = {item.instance_id: item for item in spec.managed_runtimes}
        runtime = specs.get(args.instance)
        if runtime is None:
            raise SystemExit(f"unknown managed runtime: {args.instance}")
        health["target_instance"] = args.instance
        health["restart_script"] = (
            str(runtime.resolved_restart_script())
            if runtime.resolved_restart_script()
            else None
        )
        health["restart_mode"] = (
            "explicit_restart_script"
            if runtime.resolved_restart_script()
            else (
                "controller_stop_then_registered_start"
                if runtime.recovery_policy == "safe" and not runtime.expected_live
                else "blocked_without_explicit_restart_contract"
            )
        )
        if args.apply:
            if not args.reason:
                raise SystemExit("restart --apply requires --reason")
            if (health.get("jrs_context_health") or {}).get("status") == "critical":
                raise SystemExit("restart blocked: jrs_context_unhealthy")
            target_health = next(
                row
                for row in health.get("runtimes", [])
                if row.get("instance_id") == runtime.instance_id
            )
            dependency_issues = [
                issue
                for issue in target_health.get("issues", [])
                if str(issue).startswith("dependency_unhealthy:")
            ]
            if dependency_issues:
                raise SystemExit(
                    "restart blocked: " + ",".join(dependency_issues)
                )
            action = _run_restart(
                spec, runtime, confirm_live=bool(args.confirm_live)
            )
            time.sleep(2)
            after = manifest_tool.collect_manifest(spec)
            after = manifest_tool.compare_prechange_manifest(after, before)
            health = evaluate_production_health(spec, after)
            health = attach_jrs_context_health(
                health, collect_jrs_context_health(spec)
            )
            health = attach_semantic_health(
                health, collect_data_feed_semantics(spec)
            )
            health.update(
                {
                    "command": args.command,
                    "apply": True,
                    "reason": args.reason,
                    "target_instance": args.instance,
                    "actions": [action],
                }
            )
            health["plan"] = build_plan(spec, health)
            if action.get("status") in {"blocked", "error"}:
                health["status"] = "critical"
    if args.command in {"reconcile", "recover-jrs-context"} and args.apply:
        if not args.reason:
            raise SystemExit(f"{args.command} --apply requires --reason")
        specs = {item.instance_id: item for item in spec.managed_runtimes}
        actions: list[dict[str, Any]] = []
        preflight_ids = (
            None
            if args.command == "recover-jrs-context"
            else {
                str(item["instance_id"])
                for item in health["plan"]
                if item.get("action") == "start"
            }
        )
        recovery_preflight = collect_recovery_preflight(spec, preflight_ids)
        health["recovery_preflight"] = recovery_preflight
        if any(row.get("status") == "error" for row in recovery_preflight):
            health["status"] = "critical"
            health["reason"] = args.reason
            health["actions"] = []
            if args.json:
                print(json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_human(health, include_plan=True)
            return 2
        recovery_started_at_epoch = time.time()
        if args.command == "recover-jrs-context":
            recovery_before = before
            recovery_manifest_path: Path
            if args.restore_manifest is not None:
                recovery_manifest_path = args.restore_manifest.resolve()
                recovery_before = json.loads(
                    recovery_manifest_path.read_text(encoding="utf-8")
                )
            else:
                recovery_manifest_path = persist_recovery_manifest(before)
            health["recovery_manifest"] = str(recovery_manifest_path)
            try:
                actions.extend(
                    recover_jrs_context(
                        spec,
                        recovery_before,
                        confirm_live=bool(args.confirm_live),
                    )
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"{exc}; recovery_manifest={recovery_manifest_path}"
                ) from exc
            time.sleep(2)
            interim = manifest_tool.collect_manifest(spec)
            interim_health = evaluate_production_health(spec, interim)
            interim_health = attach_jrs_context_health(
                interim_health, collect_jrs_context_health(spec)
            )
            health["plan"] = build_plan(spec, interim_health)
        for item in _ordered_start_items(spec, health["plan"]):
            actions.append(
                _run_start(
                    spec,
                    specs[item["instance_id"]],
                    confirm_live=bool(args.confirm_live),
                )
            )
        if args.command == "recover-jrs-context":
            runtime_action_ids = {
                str(action.get("instance_id"))
                for action in actions
                if action.get("instance_id")
            }
            for runtime in spec.managed_runtimes:
                if (
                    runtime.desired_state != "running"
                    or runtime.recovery_policy == "manual"
                    or runtime.instance_id in runtime_action_ids
                ):
                    continue
                actions.append(
                    {
                        "action": "verify_restored_runtime",
                        "instance_id": runtime.instance_id,
                        "status": "warming",
                        "started_at_epoch": recovery_started_at_epoch,
                        "startup_grace_sec": runtime.startup_grace_sec,
                        "output": "restored from saved pane topology",
                    }
                )
        wait_for_startup_convergence(spec, actions)
        after = manifest_tool.collect_manifest(spec)
        comparison_before = (
            recovery_before
            if args.command == "recover-jrs-context"
            else before
        )
        if args.command == "recover-jrs-context":
            comparison_before = without_allowed_unmanaged_sessions(
                spec, comparison_before
            )
        after = manifest_tool.compare_prechange_manifest(after, comparison_before)
        health = evaluate_production_health(spec, after)
        health = attach_jrs_context_health(
            health, collect_jrs_context_health(spec)
        )
        health = attach_semantic_health(
            health, collect_data_feed_semantics(spec)
        )
        health["command"] = args.command
        health["apply"] = True
        health["reason"] = args.reason
        health["actions"] = actions
        health["recovery_preflight"] = recovery_preflight
        if args.command == "recover-jrs-context":
            health["recovery_manifest"] = str(recovery_manifest_path)
        health["plan"] = build_plan(spec, health)
        if any(row.get("status") in {"blocked", "error"} for row in actions):
            health["status"] = "critical"
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(health, include_plan=args.command != "health")
    return 2 if health["status"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
