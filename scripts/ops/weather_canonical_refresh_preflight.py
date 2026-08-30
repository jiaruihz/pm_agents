#!/usr/bin/env python3
"""Fail-closed preflight for bounded canonical refresh stages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.data_products import (  # noqa: E402
    production_data_product_registry,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402


PREFLIGHT_SCHEMA_VERSION = "weather_canonical_refresh_preflight_v1"


def _run_json(
    argv: Sequence[str], *, timeout: float
) -> tuple[int, dict[str, Any], str]:
    try:
        result = subprocess.run(
            list(argv),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, {}, f"{type(exc).__name__}: {exc}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return result.returncode, {}, (
            f"invalid_json:{exc}; stderr={result.stderr[-1000:]}"
        )
    if not isinstance(payload, dict):
        return result.returncode, {}, "JSON command output was not an object"
    return result.returncode, payload, result.stderr[-1000:]


def _jrs_write_probe(socket: str, runtime_root: Path) -> dict[str, Any]:
    helper = ROOT / "scripts/ops/weather_jrs_tmux_env.sh"
    command = (
        f"source {str(helper)!r}; "
        f"weather_jrs_tmux_write_probe {socket!r} {str(runtime_root)!r}"
    )
    try:
        result = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "critical", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "healthy" if result.returncode == 0 else "critical",
        "returncode": result.returncode,
        "output": result.stdout[-2000:].strip(),
        "socket": socket,
        "runtime_root": str(runtime_root),
    }


def evaluate_preflight(
    *,
    manifest: Mapping[str, Any],
    db_path: Path,
    canonical_db_path: Path,
    jrs_probe: Mapping[str, Any],
    feed_health: Mapping[str, Any],
    registry_audit: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    migration_blockers: list[dict[str, str]] = []
    try:
        db_same_file = (
            db_path.is_file()
            and canonical_db_path.is_file()
            and os.path.samefile(db_path, canonical_db_path)
        )
    except OSError:
        db_same_file = False
    if not db_same_file:
        failures.append(
            {"kind": "canonical_db_identity_mismatch", "scope": "all_writes"}
        )
    if (manifest.get("db_route") or {}).get("status") != "healthy":
        failures.append({"kind": "manifest_db_route_unhealthy", "scope": "all_writes"})
    if jrs_probe.get("status") != "healthy":
        failures.append({"kind": "canonical_jrs_write_probe_failed", "scope": "all_writes"})
    if manifest.get("status") != "healthy":
        migration_blockers.append(
            {"kind": "production_manifest_not_healthy", "scope": "migration_only"}
        )
    if feed_health.get("status") != "ok":
        migration_blockers.append(
            {"kind": "data_feed_semantic_health_not_ok", "scope": "migration_only"}
        )
    if registry_audit.get("ok") is not True:
        migration_blockers.append(
            {"kind": "data_product_registry_invalid", "scope": "migration_only"}
        )
    live_allowed = not failures
    migration_allowed = live_allowed and not migration_blockers
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": (
            "ready"
            if migration_allowed
            else "migration_blocked"
            if live_allowed
            else "all_writes_blocked"
        ),
        "live_accounting_allowed": live_allowed,
        "migration_allowed": migration_allowed,
        "failures": failures,
        "migration_blockers": migration_blockers,
        "db_identity": {
            "requested_path": str(db_path),
            "canonical_path": str(canonical_db_path),
            "same_file": db_same_file,
        },
        "manifest_status": manifest.get("status"),
        "db_route_status": (manifest.get("db_route") or {}).get("status"),
        "jrs_probe": dict(jrs_probe),
        "data_feed_status": feed_health.get("status"),
        "registry_build_id": registry_audit.get("registry_build_id"),
    }


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest-report", type=Path, required=True)
    parser.add_argument("--feed-health-report", type=Path, required=True)
    parser.add_argument("--production-spec", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = load_production_spec(args.production_spec)
    registry = production_data_product_registry(spec)
    manifest_command = [
        sys.executable,
        str(ROOT / "scripts/ops/weather_production_manifest.py"),
        "--strict",
    ]
    if args.production_spec:
        manifest_command.extend(["--production-spec", str(args.production_spec)])
    manifest_code, manifest, manifest_error = _run_json(
        manifest_command,
        timeout=40,
    )
    _write_atomic(args.manifest_report, manifest or {"error": manifest_error})
    feed_code, feed_health, feed_error = _run_json(
        [
            sys.executable,
            str(ROOT / "scripts/ops/weather_data_feed_prod_health_check.py"),
        ],
        timeout=45,
    )
    jrs_probe = _jrs_write_probe(
        spec.canonical_tmux_socket, spec.data_feed_runtime_root
    )
    if not manifest:
        manifest = {"status": "critical", "db_route": {"status": "unknown"}}
    if not feed_health:
        feed_health = {"status": "fail", "error": feed_error}
    _write_atomic(args.feed_health_report, feed_health)
    report = evaluate_preflight(
        manifest=manifest,
        db_path=args.db,
        canonical_db_path=spec.canonical_db_path,
        jrs_probe=jrs_probe,
        feed_health=feed_health,
        registry_audit=registry.audit(),
    )
    report.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "manifest_command_exit": manifest_code,
            "manifest_command_error": manifest_error or None,
            "data_feed_command_exit": feed_code,
            "data_feed_command_error": feed_error or None,
        }
    )
    _write_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["live_accounting_allowed"]:
        return 20
    return 0 if report["migration_allowed"] else 10


if __name__ == "__main__":
    raise SystemExit(main())
