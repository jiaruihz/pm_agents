#!/usr/bin/env python3
"""Declarative control plane for the current Mac weather production stack.

The default commands are read-only. ``reconcile --apply`` only starts missing
runtimes declared in production.yaml; it never stops extra processes. Live
recovery requires both an explicit reason and ``--confirm-live``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_production_manifest as manifest_tool  # noqa: E402
from src.strategies.runtime.production import (  # noqa: E402
    WeatherManagedRuntimeSpec,
    WeatherProductionSpec,
    load_production_spec,
)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "health_path_missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"health_path_unreadable:{type(exc).__name__}"
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
        if session_row is None:
            issues.append("tmux_session_missing")
        pane_text = _pane_text(session_row)
        if runtime.checkout_root is not None and session_row is not None:
            current_paths = {
                str(pane.get("pane_current_path") or "")
                for pane in session_row.get("panes", [])
                if isinstance(pane, Mapping)
            }
            if str(runtime.checkout_root) not in current_paths:
                issues.append("checkout_root_mismatch")
        if runtime.expected_live and session_row is not None:
            if "--live" not in pane_text or "--confirm-live" not in pane_text:
                issues.append("live_flags_missing")
        if runtime.health_path is not None:
            health_payload, health_error = _read_json(runtime.health_path)
            if health_error:
                issues.append(health_error)
            else:
                health_age_sec = max(
                    0.0, now_epoch - runtime.health_path.stat().st_mtime
                )
                if (
                    runtime.max_health_age_sec is not None
                    and health_age_sec > runtime.max_health_age_sec
                ):
                    issues.append("health_artifact_stale")
                if runtime.accepted_health_statuses:
                    status = str(health_payload.get("status") or "")
                    if status not in runtime.accepted_health_statuses:
                        issues.append("health_status_unaccepted")
                if runtime.expected_live and health_payload.get("live_enabled") is not True:
                    issues.append("health_live_not_enabled")
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
            "health_age_sec": (
                round(health_age_sec, 3) if health_age_sec is not None else None
            ),
            "health_generated_at_utc": (
                health_payload.get("generated_at_utc") if health_payload else None
            ),
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

    for runtime in spec.managed_runtimes:
        row = by_instance[runtime.instance_id]
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
        set(session_rows)
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


def build_plan(
    spec: WeatherProductionSpec, health: Mapping[str, Any]
) -> list[dict[str, Any]]:
    specs = {item.instance_id: item for item in spec.managed_runtimes}
    actions: list[dict[str, Any]] = []
    for row in health.get("runtimes", []):
        if row.get("present"):
            action = "inspect" if row.get("status") == "critical" else "none"
            reason = ",".join(row.get("issues") or []) or "healthy"
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


def _print_human(payload: Mapping[str, Any], *, include_plan: bool = False) -> None:
    print(f"weather production: {str(payload.get('status')).upper()}")
    print(f"manifest: {payload.get('manifest_status')}")
    for row in payload.get("runtimes", []):
        marker = "OK" if row["status"] == "healthy" else "CRITICAL"
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
    if include_plan:
        for action in payload.get("plan", []):
            if action["action"] != "none":
                print(
                    f"[PLAN] {action['instance_id']} action={action['action']} "
                    f"reason={action['reason']}"
                )


def _run_start(runtime: WeatherManagedRuntimeSpec, *, confirm_live: bool) -> dict[str, Any]:
    script = runtime.resolved_start_script()
    if script is None:
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "start_contract_missing"}
    if runtime.recovery_policy == "manual":
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "manual_recovery_required"}
    if runtime.expected_live and not confirm_live:
        return {"instance_id": runtime.instance_id, "status": "blocked", "reason": "confirm_live_required"}
    if not script.exists():
        return {"instance_id": runtime.instance_id, "status": "error", "reason": f"start_script_missing:{script}"}
    env = os.environ.copy()
    if confirm_live:
        env["WEATHER_STRATEGY_CONFIRM_LIVE"] = "1"
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
    return {
        "instance_id": runtime.instance_id,
        "status": "started" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "output": result.stdout[-2000:].strip(),
    }


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = load_production_spec(args.production_spec)
    before = manifest_tool.collect_manifest(spec)
    health = evaluate_production_health(spec, before)
    health["plan"] = build_plan(spec, health)
    health["command"] = args.command
    health["apply"] = bool(getattr(args, "apply", False))
    if args.command == "reconcile" and args.apply:
        if not args.reason:
            raise SystemExit("reconcile --apply requires --reason")
        specs = {item.instance_id: item for item in spec.managed_runtimes}
        actions: list[dict[str, Any]] = []
        for item in health["plan"]:
            if item["action"] != "start":
                continue
            actions.append(
                _run_start(
                    specs[item["instance_id"]],
                    confirm_live=bool(args.confirm_live),
                )
            )
        after = manifest_tool.collect_manifest(spec)
        after = manifest_tool.compare_prechange_manifest(after, before)
        health = evaluate_production_health(spec, after)
        health["command"] = args.command
        health["apply"] = True
        health["reason"] = args.reason
        health["actions"] = actions
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
