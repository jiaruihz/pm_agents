#!/usr/bin/env python3
"""Host-level weather/crypto reliability supervision.

This is deliberately a bounded one-shot observer, not another process manager.
It runs from the Mac internal disk so JRS/tmux failure cannot take the observer
down with the workloads.  Repairs, when explicitly enabled, are delegated to
the canonical weather controller or crypto runtime reconciler.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402


DEFAULT_STATE_ROOT = (
    Path.home()
    / "Library/Application Support/pm_agents/production_reliability"
)
DEFAULT_CRYPTO_ROOT = Path("/Users/deepsleep/projects/crypto_quant")
DEFAULT_CRYPTO_RUNTIME_ROOT = Path("/Volumes/jrs/crypto_quant/runtime/pm5m")
DEFAULT_CRYPTO_RAW_ROOT = Path(
    "/Volumes/jrs/crypto_quant/data/raw/polymarket/crypto_updown_5m"
)
CRITICAL_DEBOUNCE_CYCLES = 2
WARNING_DEBOUNCE_CYCLES = 3
REPAIR_COOLDOWN_SECONDS = 15 * 60
MAX_REPAIR_ATTEMPTS = 3


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float


Runner = Callable[[Sequence[str], Path | None, float], CommandResult]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def run_command(
    command: Sequence[str], cwd: Path | None = None, timeout: float = 30
) -> CommandResult:
    started = time.monotonic()
    try:
        result = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_sec=round(time.monotonic() - started, 3),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(
            returncode=124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            stdout="",
            stderr=f"{type(exc).__name__}:{exc}",
            elapsed_sec=round(time.monotonic() - started, 3),
        )


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def issue(
    key: str,
    severity: str,
    detail: str,
    *,
    component: str,
    repair: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "severity": severity,
        "component": component,
        "detail": detail[:1000],
    }
    if repair:
        row["repair"] = dict(repair)
    return row


def parse_json_output(result: CommandResult) -> dict[str, Any] | None:
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def collect_weather(runner: Runner = run_command) -> dict[str, Any]:
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/ops/weather_production_ctl.py"),
        "health",
        "--json",
    ]
    result = runner(command, ROOT, 45)
    payload = parse_json_output(result)
    findings: list[dict[str, Any]] = []
    if payload is None:
        findings.append(
            issue(
                "weather.controller.unreadable",
                "critical",
                f"controller health failed rc={result.returncode} error={result.stderr[-500:]}",
                component="weather-controller",
            )
        )
        return {
            "status": "critical",
            "elapsed_sec": result.elapsed_sec,
            "findings": findings,
            "health": None,
        }

    manifest_status = payload.get("manifest_status")
    if manifest_status != "healthy":
        details = payload.get("critical_manifest_findings") or []
        findings.append(
            issue(
                "weather.manifest.degraded",
                "critical" if manifest_status == "critical" or details else "warning",
                f"manifest_status={manifest_status} findings={details[:5]}",
                component="weather-manifest",
            )
        )

    jrs = payload.get("jrs_context_health") or {}
    if jrs.get("status") != "healthy":
        findings.append(
            issue(
                "weather.jrs_context.unhealthy",
                "critical",
                f"status={jrs.get('status')} rc={jrs.get('returncode')} output={str(jrs.get('output') or '')[-500:]}",
                component="jrs-context",
            )
        )

    semantics = payload.get("data_feed_semantic_health") or {}
    semantic_status = semantics.get("status")
    if semantic_status == "critical":
        findings.append(
            issue(
                "weather.data_feed.semantic_critical",
                "critical",
                ",".join(map(str, semantics.get("critical_reasons") or ["unknown"])),
                component="weather-data",
            )
        )
    elif semantic_status == "warning":
        findings.append(
            issue(
                "weather.data_feed.semantic_warning",
                "warning",
                ",".join(map(str, semantics.get("warnings") or ["unknown"])),
                component="weather-data",
            )
        )

    critical_roles = {
        "infrastructure",
        "dashboard_api",
        "data_feed",
        "collector",
        "source_collector",
        "market_collector",
        "probability_runtime",
        "strategy",
    }
    runtime_rows = payload.get("runtimes") or []
    for row in runtime_rows:
        if not isinstance(row, dict) or row.get("status") == "healthy":
            continue
        instance_id = str(row.get("instance_id") or "unknown")
        severity = (
            "critical"
            if row.get("expected_live") or row.get("role") in critical_roles
            else "warning"
        )
        direct_issues = [
            str(value)
            for value in (row.get("issues") or [])
            if not str(value).startswith("dependency_unhealthy:")
        ]
        repair = None
        if (
            direct_issues
            and row.get("recovery_policy") == "safe"
            and not row.get("expected_live")
        ):
            repair = {"kind": "weather_restart", "target": instance_id}
        findings.append(
            issue(
                f"weather.runtime.{instance_id}",
                severity,
                ",".join(map(str, row.get("issues") or ["unhealthy"])),
                component="weather-runtime",
                repair=repair,
            )
        )

    status = "critical" if any(row["severity"] == "critical" for row in findings) else (
        "warning" if findings else "healthy"
    )
    return {
        "status": status,
        "elapsed_sec": result.elapsed_sec,
        "controller_status": payload.get("status"),
        "runtime_count": len(runtime_rows),
        "findings": findings,
        "health": payload,
    }


def disk_volume_uuid(path: Path, runner: Runner = run_command) -> tuple[str | None, str]:
    result = runner(["/usr/sbin/diskutil", "info", "-plist", str(path)], None, 10)
    if result.returncode != 0:
        return None, result.stderr[-500:]
    try:
        payload = plistlib.loads(result.stdout.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError) as exc:
        return None, f"{type(exc).__name__}:{exc}"
    return str(payload.get("VolumeUUID") or "").upper() or None, ""


def collect_storage(runner: Runner = run_command) -> dict[str, Any]:
    spec = load_production_spec()
    path = spec.production_storage_root
    findings: list[dict[str, Any]] = []
    if not path.is_dir():
        findings.append(
            issue(
                "storage.jrs.missing",
                "critical",
                f"production storage is not mounted: {path}",
                component="storage",
            )
        )
        return {"status": "critical", "path": str(path), "findings": findings}

    observed_uuid, uuid_error = disk_volume_uuid(path, runner)
    expected_uuid = spec.production_storage_volume_uuid
    if observed_uuid != expected_uuid:
        findings.append(
            issue(
                "storage.jrs.uuid_mismatch",
                "critical",
                f"expected={expected_uuid} observed={observed_uuid} error={uuid_error}",
                component="storage",
            )
        )

    usage = shutil.disk_usage(path)
    free_pct = 100.0 * usage.free / usage.total if usage.total else 0.0
    if free_pct < 5:
        findings.append(
            issue(
                "storage.jrs.free_space_critical",
                "critical",
                f"free={usage.free} total={usage.total} free_pct={free_pct:.2f}",
                component="storage",
            )
        )
    elif free_pct < 10:
        findings.append(
            issue(
                "storage.jrs.free_space_warning",
                "warning",
                f"free={usage.free} total={usage.total} free_pct={free_pct:.2f}",
                component="storage",
            )
        )
    status = "critical" if any(row["severity"] == "critical" for row in findings) else (
        "warning" if findings else "healthy"
    )
    return {
        "status": status,
        "path": str(path),
        "expected_uuid": expected_uuid,
        "observed_uuid": observed_uuid,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_pct": round(free_pct, 3),
        "findings": findings,
    }


def _curl_probe(name: str, url: str, proxy_url: str, runner: Runner) -> dict[str, Any]:
    command = [
        "/usr/bin/curl",
        "-sS",
        "--proxy",
        proxy_url,
        "--connect-timeout",
        "5",
        "--max-time",
        "10",
        "--retry",
        "1",
        "--retry-all-errors",
        "--retry-delay",
        "0",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code} %{time_total}",
        url,
    ]
    result = runner(command, None, 10)
    fields = result.stdout.strip().split()
    http_code = fields[0] if fields else "000"
    try:
        latency = float(fields[1]) if len(fields) > 1 else None
    except ValueError:
        latency = None
    ok = result.returncode == 0 and http_code.startswith("2")
    return {
        "name": name,
        "ok": ok,
        "http_code": http_code,
        "latency_sec": latency,
        "error": result.stderr[-300:],
    }


def collect_network(runner: Runner = run_command) -> dict[str, Any]:
    proxy_url = load_production_spec().market_proxy_default_url
    probes = [
        _curl_probe(
            "open_meteo",
            "https://api.open-meteo.com/v1/forecast?latitude=35&longitude=139&hourly=temperature_2m&forecast_days=1",
            proxy_url,
            runner,
        ),
    ]
    findings = [
        issue(
            f"network.weather.{row['name']}",
            "critical",
            f"http={row['http_code']} latency={row['latency_sec']} error={row['error']}",
            component="network",
        )
        for row in probes
        if not row["ok"]
    ]
    return {
        "status": "critical" if findings else "healthy",
        "proxy_url": proxy_url,
        "probes": probes,
        "findings": findings,
    }


def maintain_market_proxy_control(runner: Runner = run_command) -> dict[str, Any]:
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/ops/weather_market_proxy_ctl.py"),
        "maintain",
        "--apply",
        "--confirm-live",
        "--trigger",
        "production_reliability_supervisor",
        "--reason",
        "automatic default route recovery",
    ]
    result = runner(command, ROOT, 120)
    payload = parse_json_output(result)
    findings: list[dict[str, Any]] = []
    if payload is None:
        findings.append(
            issue(
                "network.weather.market_proxy_control_unreadable",
                "critical",
                f"rc={result.returncode} error={result.stderr[-500:]}",
                component="network-control",
            )
        )
        return {
            "status": "critical",
            "elapsed_sec": result.elapsed_sec,
            "findings": findings,
        }
    maintenance = payload.get("maintenance") or {}
    route_control = payload.get("route_control") or {}
    probe = payload.get("probe") or {}
    acceptable = {
        "healthy",
        "recovered_before_switch",
        "switched",
        "already_running",
    }
    healthy = (
        result.returncode == 0
        and maintenance.get("status") in acceptable
        and route_control.get("healthy") is True
        and probe.get("ok") is True
    )
    if not healthy:
        findings.append(
            issue(
                "network.weather.market_proxy_control_degraded",
                "critical",
                (
                    f"rc={result.returncode} maintenance={maintenance.get('status')} "
                    f"route_healthy={route_control.get('healthy')} probe_ok={probe.get('ok')}"
                ),
                component="network-control",
            )
        )
    return {
        "status": "healthy" if healthy else "critical",
        "elapsed_sec": result.elapsed_sec,
        "maintenance": maintenance,
        "route_control": {
            "healthy": route_control.get("healthy"),
            "all_routes_healthy": route_control.get("all_routes_healthy"),
            "reachable": route_control.get("reachable"),
        },
        "probe": probe,
        "health_artifact": payload.get("health_artifact"),
        "findings": findings,
    }


def collect_weather_execution_semantics() -> dict[str, Any]:
    from scripts.ops.weather_execution_semantic_health import (
        collect_probes,
        summarize_probes,
    )

    summary = summarize_probes(collect_probes())
    controller_owned_kinds = {
        "missing_summary",
        "summary_parse_error",
        "stale_summary",
        "aging_summary",
    }
    findings: list[dict[str, Any]] = []
    for probe in summary.get("probes") or []:
        for alert in probe.get("alerts") or []:
            if alert.get("kind") in controller_owned_kinds:
                continue
            findings.append(
                issue(
                    (
                        "weather.execution."
                        f"{alert.get('strategy_instance')}.{alert.get('kind')}"
                    ),
                    str(alert.get("severity") or "warning"),
                    str(alert.get("message") or alert.get("kind") or "semantic failure"),
                    component="weather-execution",
                )
            )
    status = "critical" if any(row["severity"] == "critical" for row in findings) else (
        "warning" if findings else "healthy"
    )
    return {
        "status": status,
        "probe_count": len(summary.get("probes") or []),
        "semantic_summary": summary,
        "findings": findings,
    }


def _file_age(path: Path, now_epoch: float) -> float | None:
    try:
        return max(0.0, now_epoch - path.stat().st_mtime)
    except OSError:
        return None


def _fresh_json_check(
    key: str,
    path: Path,
    max_age_sec: float,
    now_epoch: float,
    *,
    required_fields: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    age = _file_age(path, now_epoch)
    payload = read_json(path)
    detail = {"path": str(path), "age_sec": round(age, 3) if age is not None else None}
    reasons: list[str] = []
    if age is None:
        reasons.append("missing")
    elif age > max_age_sec:
        reasons.append(f"stale:{age:.1f}>{max_age_sec:.1f}")
    for field, expected in (required_fields or {}).items():
        if payload.get(field) != expected:
            reasons.append(f"{field}={payload.get(field)!r} expected={expected!r}")
    if not reasons:
        return detail, None
    return detail, issue(
        key,
        "critical",
        f"{path}: " + ",".join(reasons),
        component="crypto-data",
    )


def collect_crypto(
    runner: Runner = run_command,
    *,
    crypto_root: Path = DEFAULT_CRYPTO_ROOT,
    runtime_root: Path = DEFAULT_CRYPTO_RUNTIME_ROOT,
    raw_root: Path = DEFAULT_CRYPTO_RAW_ROOT,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now_epoch = time.time() if now_epoch is None else now_epoch
    registry_path = crypto_root / "configs/pm5m-runtime.json"
    registry = read_json(registry_path)
    findings: list[dict[str, Any]] = []
    if not registry.get("services"):
        findings.append(
            issue(
                "crypto.registry.unreadable",
                "critical",
                f"missing or invalid registry: {registry_path}",
                component="crypto-runtime",
            )
        )
        return {"status": "critical", "findings": findings}

    result = runner(["/bin/launchctl", "list"], None, 15)
    launch_rows: dict[str, tuple[str, str]] = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3:
                launch_rows[fields[2]] = (fields[0], fields[1])
    else:
        findings.append(
            issue(
                "crypto.launchctl.unreadable",
                "critical",
                result.stderr[-500:],
                component="crypto-runtime",
            )
        )

    started_services = [
        row
        for row in registry["services"]
        if row.get("lifecycle") in {"active", "evidence"}
    ]
    missing: list[str] = []
    exited: list[str] = []
    for service in started_services:
        for label in service.get("labels") or []:
            launch = launch_rows.get(label)
            if launch is None:
                missing.append(label)
            elif launch[0] == "-":
                exited.append(f"{label}:last_exit={launch[1]}")
    if missing or exited:
        findings.append(
            issue(
                "crypto.runtime.incomplete",
                "critical",
                f"missing={missing} exited={exited}",
                component="crypto-runtime",
                repair={"kind": "crypto_reconcile", "target": "pm5m-retained"},
            )
        )

    artifacts: list[dict[str, Any]] = []
    fixed_checks = [
        (
            "crypto.data.settlement",
            runtime_root / "settlement-service.status.json",
            240.0,
            None,
        ),
        (
            "crypto.data.context",
            runtime_root / "future-context.status.json",
            30.0,
            None,
        ),
    ]
    for key, path, max_age, required in fixed_checks:
        artifact, finding = _fresh_json_check(
            key, path, max_age, now_epoch, required_fields=required
        )
        artifacts.append(artifact)
        if finding:
            findings.append(finding)

    collector_paths = list(raw_root.glob("*/*/collector.status.json")) if raw_root.is_dir() else []
    for symbol in ("btc", "eth"):
        matching = []
        for path in collector_paths:
            payload = read_json(path)
            if str(payload.get("symbol") or "").lower() == symbol:
                matching.append(path)
        latest = max(matching, key=lambda path: path.stat().st_mtime) if matching else (
            raw_root / f"missing-{symbol}-collector.status.json"
        )
        artifact, finding = _fresh_json_check(
            f"crypto.data.collector.{symbol}",
            latest,
            30.0,
            now_epoch,
            required_fields={"connected": True, "transport_warm": True},
        )
        artifact["symbol"] = symbol
        artifacts.append(artifact)
        if finding:
            findings.append(finding)

    status = "critical" if any(row["severity"] == "critical" for row in findings) else (
        "warning" if findings else "healthy"
    )
    expected_labels = sum(len(row.get("labels") or []) for row in started_services)
    return {
        "status": status,
        "registry_profile_id": registry.get("profile_id"),
        "expected_labels": expected_labels,
        "running_labels": expected_labels - len(missing) - len(exited),
        "artifacts": artifacts,
        "findings": findings,
    }


def update_incident_state(
    previous: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    previous_issues = previous.get("issues") if isinstance(previous.get("issues"), dict) else {}
    observed = {str(row["key"]): row for row in findings}
    next_issues: dict[str, Any] = {}
    transitions: list[dict[str, Any]] = []
    now_text = iso_utc(now)

    for key, finding in observed.items():
        old = previous_issues.get(key) if isinstance(previous_issues.get(key), dict) else {}
        consecutive = int(old.get("consecutive_cycles") or 0) + 1
        threshold = (
            CRITICAL_DEBOUNCE_CYCLES
            if finding.get("severity") == "critical"
            else WARNING_DEBOUNCE_CYCLES
        )
        active = bool(old.get("active")) or consecutive >= threshold
        row = {
            **finding,
            "first_seen_utc": old.get("first_seen_utc") or now_text,
            "last_seen_utc": now_text,
            "consecutive_cycles": consecutive,
            "active": active,
            "repair_attempts": int(old.get("repair_attempts") or 0),
            "last_repair_epoch": int(old.get("last_repair_epoch") or 0),
            "last_repair": old.get("last_repair"),
        }
        next_issues[key] = row
        if active and not old.get("active"):
            transitions.append(
                {
                    "event": "opened",
                    "key": key,
                    "severity": finding.get("severity"),
                    "at_utc": now_text,
                    "impact_started_utc": row["first_seen_utc"],
                    "detail": finding.get("detail"),
                }
            )

    for key, old in previous_issues.items():
        if key in observed or not isinstance(old, dict) or not old.get("active"):
            continue
        transitions.append(
            {
                "event": "resolved",
                "key": key,
                "severity": old.get("severity"),
                "at_utc": now_text,
                "impact_started_utc": old.get("first_seen_utc"),
                "impact_ended_utc": now_text,
                "detail": old.get("detail"),
            }
        )

    return {
        "schema_version": "production_reliability_state_v1",
        "updated_at_utc": now_text,
        "issues": next_issues,
    }, transitions


def eligible_repair(issue_state: Mapping[str, Any], now_epoch: int) -> bool:
    if not issue_state.get("active") or not issue_state.get("repair"):
        return False
    if int(issue_state.get("repair_attempts") or 0) >= MAX_REPAIR_ATTEMPTS:
        return False
    return now_epoch - int(issue_state.get("last_repair_epoch") or 0) >= REPAIR_COOLDOWN_SECONDS


def apply_one_safe_repair(
    state: dict[str, Any],
    *,
    runtime_storage_ok: bool,
    runner: Runner = run_command,
    crypto_root: Path = DEFAULT_CRYPTO_ROOT,
) -> dict[str, Any] | None:
    now_epoch = int(time.time())
    candidates = [
        row
        for row in state.get("issues", {}).values()
        if isinstance(row, dict) and eligible_repair(row, now_epoch)
    ]
    candidates.sort(key=lambda row: (row.get("first_seen_utc") or "", row.get("key") or ""))
    for row in candidates:
        repair = row.get("repair") or {}
        kind = repair.get("kind")
        if not runtime_storage_ok:
            continue
        if kind == "weather_restart":
            command = [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/ops/weather_production_ctl.py"),
                "restart",
                "--apply",
                "--json",
                "--instance",
                str(repair.get("target")),
                "--reason",
                f"production reliability supervisor: {row.get('key')}",
            ]
            result = runner(command, ROOT, 150)
        elif kind == "crypto_reconcile":
            command = [str(crypto_root / "scripts/pm5m_shared_runtime.sh"), "reconcile"]
            result = runner(command, crypto_root, 180)
        else:
            continue
        action = {
            "at_utc": iso_utc(),
            "issue_key": row.get("key"),
            "kind": kind,
            "target": repair.get("target"),
            "returncode": result.returncode,
            "elapsed_sec": result.elapsed_sec,
            "output": (result.stdout + "\n" + result.stderr)[-2000:].strip(),
        }
        row["repair_attempts"] = int(row.get("repair_attempts") or 0) + 1
        row["last_repair_epoch"] = now_epoch
        row["last_repair"] = action
        return action
    return None


def render_notification(transitions: Sequence[Mapping[str, Any]]) -> str:
    opened = [row for row in transitions if row.get("event") == "opened"]
    resolved = [row for row in transitions if row.get("event") == "resolved"]
    repairs = [row for row in transitions if row.get("event") == "repair_attempt"]
    lines = ["【生产链路稳定性告警】"]
    for row in opened:
        lines.append(
            f"- 新增 {str(row.get('severity')).upper()} {row.get('key')}: {row.get('detail')}"
        )
        lines.append(f"  影响从 {row.get('impact_started_utc')} 开始")
    for row in resolved:
        lines.append(f"- 已恢复 {row.get('key')}")
        lines.append(
            f"  影响窗口 {row.get('impact_started_utc')} → {row.get('impact_ended_utc')}"
        )
    for row in repairs:
        lines.append(
            f"- 自动恢复 {row.get('key')}: rc={row.get('returncode')} {row.get('detail')}"
        )
    return "\n".join(lines)[:3900]


def notify_telegram(transitions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not transitions:
        return {"status": "not_needed"}
    try:
        from src.platform.notification.telegram import send_telegram_message_sync

        response = send_telegram_message_sync(render_notification(transitions))
        return {"status": "sent", "response_ok": bool(response.get("ok", True))}
    except Exception as exc:  # notification failure must not hide the monitored fault
        return {"status": "failed", "error": f"{type(exc).__name__}:{exc}"[:500]}


def send_heartbeat(url: str, runner: Runner = run_command) -> dict[str, Any]:
    if not url:
        return {"status": "disabled"}
    result = runner(
        [
            "/usr/bin/curl",
            "-fsS",
            "--connect-timeout",
            "3",
            "--max-time",
            "8",
            "-o",
            "/dev/null",
            url,
        ],
        None,
        10,
    )
    return {
        "status": "sent" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "elapsed_sec": result.elapsed_sec,
        "error": result.stderr[-500:],
    }


def build_snapshot(
    *,
    runner: Runner = run_command,
    crypto_root: Path = DEFAULT_CRYPTO_ROOT,
    crypto_runtime_root: Path = DEFAULT_CRYPTO_RUNTIME_ROOT,
    crypto_raw_root: Path = DEFAULT_CRYPTO_RAW_ROOT,
    maintain_weather_route: bool = False,
) -> dict[str, Any]:
    weather = collect_weather(runner)
    storage = collect_storage(runner)
    jrs_healthy = (
        storage["status"] == "healthy"
        and (weather.get("health") or {}).get("jrs_context_health", {}).get("status")
        == "healthy"
    )
    market_proxy = (
        maintain_market_proxy_control(runner)
        if maintain_weather_route and jrs_healthy
        else {
            "status": "skipped",
            "reason": (
                "maintenance_disabled"
                if not maintain_weather_route
                else "jrs_or_storage_unhealthy"
            ),
            "findings": [],
        }
    )
    network = collect_network(runner)
    weather_execution = collect_weather_execution_semantics()
    crypto = collect_crypto(
        runner,
        crypto_root=crypto_root,
        runtime_root=crypto_runtime_root,
        raw_root=crypto_raw_root,
    )
    sections = {
        "weather": weather,
        "weather_execution": weather_execution,
        "storage": storage,
        "market_proxy": market_proxy,
        "network": network,
        "crypto": crypto,
    }
    findings = [row for section in sections.values() for row in section.get("findings", [])]
    status = "critical" if any(row["severity"] == "critical" for row in findings) else (
        "warning" if findings else "healthy"
    )
    return {
        "schema_version": "production_reliability_snapshot_v1",
        "generated_at_utc": iso_utc(),
        "status": status,
        "findings": findings,
        "sections": sections,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--crypto-root", type=Path, default=DEFAULT_CRYPTO_ROOT)
    parser.add_argument("--crypto-runtime-root", type=Path, default=DEFAULT_CRYPTO_RUNTIME_ROOT)
    parser.add_argument("--crypto-raw-root", type=Path, default=DEFAULT_CRYPTO_RAW_ROOT)
    parser.add_argument("--apply-safe", action="store_true")
    parser.add_argument("--maintain-weather-route", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument(
        "--heartbeat-url",
        default=os.getenv("PRODUCTION_RELIABILITY_HEARTBEAT_URL", ""),
        help="optional external dead-man-switch ping URL",
    )
    parser.add_argument("--fail-on-degraded", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.state_root.mkdir(parents=True, exist_ok=True)
    lock_path = args.state_root / "supervisor.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return 0

        snapshot = build_snapshot(
            crypto_root=args.crypto_root,
            crypto_runtime_root=args.crypto_runtime_root,
            crypto_raw_root=args.crypto_raw_root,
            maintain_weather_route=bool(args.maintain_weather_route),
        )
        state_path = args.state_root / "state.json"
        previous = read_json(state_path, {})
        state, transitions = update_incident_state(previous, snapshot["findings"], utc_now())
        jrs_healthy = (
            snapshot["sections"]["storage"]["status"] == "healthy"
            and (snapshot["sections"]["weather"].get("health") or {})
            .get("jrs_context_health", {})
            .get("status")
            == "healthy"
        )
        action = None
        if args.apply_safe:
            action = apply_one_safe_repair(
                state,
                runtime_storage_ok=jrs_healthy,
                crypto_root=args.crypto_root,
            )
        pending_notifications = (
            list(previous.get("pending_notifications") or []) if args.notify else []
        )
        pending_notifications.extend(transitions)
        if action:
            pending_notifications.append(
                {
                    "event": "repair_attempt",
                    "key": action.get("issue_key"),
                    "returncode": action.get("returncode"),
                    "detail": f"kind={action.get('kind')} target={action.get('target')}",
                    "at_utc": action.get("at_utc"),
                }
            )
        notification = (
            notify_telegram(pending_notifications)
            if args.notify
            else {"status": "disabled"}
        )
        if args.notify and notification.get("status") != "sent":
            state["pending_notifications"] = pending_notifications[-50:]
        else:
            state["pending_notifications"] = []
        heartbeat = send_heartbeat(str(args.heartbeat_url))
        latest = {
            **snapshot,
            "active_incidents": [
                row for row in state["issues"].values() if row.get("active")
            ],
            "pending_incidents": [
                row for row in state["issues"].values() if not row.get("active")
            ],
            "transitions": transitions,
            "safe_repair_enabled": bool(args.apply_safe),
            "repair_action": action,
            "notification": notification,
            "heartbeat": heartbeat,
        }
        atomic_write_json(state_path, state)
        atomic_write_json(args.state_root / "latest.json", latest)
        for transition in transitions:
            append_jsonl(args.state_root / "events.jsonl", transition)
        if action:
            append_jsonl(args.state_root / "repair_actions.jsonl", action)
        if notification.get("status") == "failed":
            append_jsonl(
                args.state_root / "notification_failures.jsonl",
                {"at_utc": iso_utc(), **notification, "transitions": transitions},
            )
        print(json.dumps(latest, ensure_ascii=False, indent=2))
        return 1 if args.fail_on_degraded and snapshot["status"] != "healthy" else 0


if __name__ == "__main__":
    raise SystemExit(main())
