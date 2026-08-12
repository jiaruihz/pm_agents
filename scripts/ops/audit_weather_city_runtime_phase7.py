#!/usr/bin/env python3
"""Audit the five-city WCIR cutover without changing production state."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402


FRAMEWORK_ID = "weather_city_intraday_runtime_v1"
STRATEGY_FAMILY = "weather.city_intraday_probability"
RUNTIME_ID = "weather_city_probability_runtime_v3"
EXPECTED_CITIES = ("Amsterdam", "Busan", "Helsinki", "Seoul", "Tokyo")
COVERAGE_ONLY_CITIES = ("Seoul",)
FORBIDDEN_EXECUTION_PATTERNS = {
    "direct_clob_client": re.compile(r"\bClobClient\b|\bpy_clob_client(?:_v2)?\b"),
    "legacy_order_executor": re.compile(r"weather_order_executor"),
}


def utc_text(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            yield value


def code_authority_audit(repo_root: Path) -> dict[str, Any]:
    roots = (
        repo_root / "weather_city_runtime",
        repo_root / "src/strategies/weather_city_probability_shadow",
    )
    files = [
        repo_root / "scripts/ops/weather_city_probability_runtime_v3.py",
        repo_root / "scripts/ops/weather_city_execution_handoff_v1.py",
        repo_root / "scripts/ops/start_weather_city_probability_runtime_v3.sh",
    ]
    for directory in roots:
        files.extend(sorted(directory.rglob("*.py")))
    findings: list[dict[str, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for kind, pattern in FORBIDDEN_EXECUTION_PATTERNS.items():
            if pattern.search(text):
                findings.append({"kind": kind, "path": str(path.relative_to(repo_root))})
    return {
        "files_scanned": len(files),
        "forbidden_execution_findings": findings,
        "pass": not findings,
    }


def config_audit(config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("profiles") or []
    declared_cities = sorted({str(row.get("city") or "") for row in profiles})
    contract_cities = sorted(
        str(city)
        for city in (config.get("decision_contract_output") or {}).get("cities", [])
    )
    coverage_profiles = {
        str(row.get("city")): row
        for row in profiles
        if str(row.get("adapter")) == "observation_coverage_v1"
    }
    errors: list[str] = []
    if config.get("framework_id") != FRAMEWORK_ID:
        errors.append("framework_id_mismatch")
    if config.get("strategy_family") != STRATEGY_FAMILY:
        errors.append("strategy_family_mismatch")
    if config.get("execution_mode") != "zero_notional_shadow":
        errors.append("execution_mode_not_zero_notional_shadow")
    if int(config.get("orders_submitted") or 0) != 0:
        errors.append("config_orders_submitted_nonzero")
    if declared_cities != list(EXPECTED_CITIES):
        errors.append("profile_city_set_mismatch")
    if contract_cities != list(EXPECTED_CITIES):
        errors.append("decision_contract_city_set_mismatch")
    for city in COVERAGE_ONLY_CITIES:
        profile = coverage_profiles.get(city)
        if profile is None:
            errors.append(f"{city}:missing_coverage_profile")
            continue
        if profile.get("emit_paper_intents") is not False:
            errors.append(f"{city}:coverage_profile_may_emit_intent")
        if not profile.get("blocker_reason"):
            errors.append(f"{city}:coverage_profile_missing_blocker")
    legacy = config.get("legacy_runtime") or {}
    if legacy.get("lifecycle_status") != "deprecated_read_only":
        errors.append("legacy_runtime_not_deprecated_read_only")
    return {
        "declared_cities": declared_cities,
        "decision_contract_cities": contract_cities,
        "coverage_only_cities": sorted(coverage_profiles),
        "profile_count": len(profiles),
        "errors": errors,
        "pass": not errors,
    }


def production_audit(production_path: Path) -> dict[str, Any]:
    spec = load_production_spec(production_path)
    matches = [row for row in spec.managed_runtimes if row.instance_id == RUNTIME_ID]
    errors: list[str] = []
    if len(matches) != 1:
        errors.append(f"managed_runtime_count={len(matches)}")
        runtime = None
    else:
        runtime = matches[0]
        if runtime.execution_mode != "shadow":
            errors.append("managed_runtime_not_shadow")
        if runtime.expected_live:
            errors.append("managed_runtime_expected_live")
        if runtime.role != "probability_runtime":
            errors.append("managed_runtime_role_mismatch")
    return {
        "host_role": spec.host_role,
        "managed_runtime": (
            {
                "instance_id": runtime.instance_id,
                "execution_mode": runtime.execution_mode,
                "expected_live": runtime.expected_live,
                "checkout_root": str(runtime.checkout_root),
                "start_script": str(runtime.start_script),
            }
            if runtime is not None
            else None
        ),
        "errors": errors,
        "pass": not errors,
    }


def raw_runtime_audit(runtime_dir: Path) -> dict[str, Any]:
    bundles = list(iter_jsonl(runtime_dir / "decision_bundles.jsonl"))
    blockers = list(iter_jsonl(runtime_dir / "checkpoint_blockers.jsonl"))
    intents = list(iter_jsonl(runtime_dir / "trade_intents.jsonl"))
    handoffs = list(iter_jsonl(runtime_dir / "execution_handoffs.jsonl"))
    latest = read_json(runtime_dir / "latest_summary.json")
    candidate_rows = [row.get("signal_candidate") or {} for row in bundles]
    candidate_ids = [str(row.get("candidate_id") or "") for row in candidate_rows]
    errors: list[str] = []
    if latest.get("execution_mode") != "zero_notional_shadow":
        errors.append("latest_execution_mode_not_shadow")
    if int(latest.get("orders_submitted") or 0) != 0:
        errors.append("latest_orders_submitted_nonzero")
    invalid_intents = [
        str(row.get("intent_id") or index)
        for index, row in enumerate(intents)
        if row.get("mode") != "zero_notional"
        or float(row.get("requested_size") or 0.0) != 0.0
    ]
    if invalid_intents:
        errors.append(f"nonzero_or_nonshadow_intents={len(invalid_intents)}")
    invalid_handoffs = [
        index
        for index, row in enumerate(handoffs)
        if (row.get("status") or row.get("execution_action")) != "record_only"
        or row.get("execution_intent") is not None
        or int(row.get("venue_calls") or 0) != 0
    ]
    if invalid_handoffs:
        errors.append(f"side_effect_handoffs={len(invalid_handoffs)}")
    forbidden_files = [
        name
        for name in ("live_orders.jsonl", "paper_orders.jsonl", "plans.jsonl", "fills.jsonl")
        if (runtime_dir / name).exists()
    ]
    if forbidden_files:
        errors.append("execution_files_present")
    return {
        "latest_generated_at_utc": latest.get("generated_at_utc"),
        "bundle_rows": len(bundles),
        "unique_candidates": len({value for value in candidate_ids if value}),
        "candidate_rows_by_city": dict(
            sorted(Counter(str(row.get("city") or "missing") for row in candidate_rows).items())
        ),
        "candidate_status": dict(
            sorted(Counter(str(row.get("candidate_status") or "missing") for row in candidate_rows).items())
        ),
        "blocker_rows": len(blockers),
        "blocker_rows_by_city": dict(
            sorted(Counter(str(row.get("city") or "missing") for row in blockers).items())
        ),
        "intent_rows": len(intents),
        "invalid_intent_ids": invalid_intents,
        "handoff_rows": len(handoffs),
        "invalid_handoff_rows": invalid_handoffs,
        "forbidden_execution_files": forbidden_files,
        "errors": errors,
        "pass": not errors,
    }


def legacy_audit(config: dict[str, Any], active_runtime: Path) -> dict[str, Any]:
    legacy = config.get("legacy_runtime") or {}
    legacy_root = Path(str(legacy.get("runtime_dir") or ""))
    journals = [str(value) for value in legacy.get("journals") or []]
    active_files = [
        active_runtime / name
        for name in ("decision_bundles.jsonl", "checkpoint_blockers.jsonl")
        if (active_runtime / name).is_file()
    ]
    active_birth = min(
        getattr(path.stat(), "st_birthtime", path.stat().st_mtime)
        for path in active_files
    )
    rows: dict[str, int] = {}
    mtimes: dict[str, str] = {}
    written_after_cutover: list[str] = []
    for name in journals:
        path = legacy_root / name
        rows[name] = sum(1 for _ in iter_jsonl(path))
        mtimes[name] = utc_text(path.stat().st_mtime)
        if path.stat().st_mtime > active_birth:
            written_after_cutover.append(name)
    return {
        "lifecycle_status": legacy.get("lifecycle_status"),
        "runtime_dir": str(legacy_root),
        "active_journal_birth_utc": utc_text(active_birth),
        "rows": rows,
        "journal_mtime_utc": mtimes,
        "journals_written_after_active_cutover": written_after_cutover,
        "pass": not written_after_cutover,
    }


def manifest_audit(manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {"status": "not_supplied", "pass": False}
    manifest = read_json(manifest_path)
    processes = [
        row
        for row in manifest.get("processes") or []
        if "weather_city_probability_runtime_v3.py" in str(row.get("command") or "")
    ]
    errors: list[str] = []
    if manifest.get("status") != "healthy":
        errors.append("manifest_not_healthy")
    if (manifest.get("db_route") or {}).get("status") != "healthy":
        errors.append("canonical_db_route_not_healthy")
    if len(processes) != 1:
        errors.append(f"wcir_process_count={len(processes)}")
    for row in processes:
        command = str(row.get("command") or "")
        if "--live" in command or "--confirm-live" in command:
            errors.append("wcir_process_has_live_flag")
    return {
        "status": manifest.get("status"),
        "generated_at_utc": manifest.get("generated_at_utc"),
        "db_route_status": (manifest.get("db_route") or {}).get("status"),
        "processes": [
            {
                "pid": row.get("pid"),
                "command": row.get("command"),
                "checkout": row.get("checkout"),
            }
            for row in processes
        ],
        "errors": errors,
        "pass": not errors,
    }


def build_audit(
    *,
    repo_root: Path,
    config_path: Path,
    production_path: Path,
    runtime_dir: Path,
    manifest_path: Path | None,
) -> dict[str, Any]:
    config = read_json(config_path)
    sections = {
        "config": config_audit(config),
        "production": production_audit(production_path),
        "code_authority": code_authority_audit(repo_root),
        "raw_runtime": raw_runtime_audit(runtime_dir),
        "legacy_read_only": legacy_audit(config, runtime_dir),
        "manifest": manifest_audit(manifest_path),
    }
    report: dict[str, Any] = {
        "schema_version": "weather_city_runtime_phase7_audit_v1",
        "framework_id": FRAMEWORK_ID,
        "framework_name": "Weather City Intraday Runtime",
        "strategy_family": STRATEGY_FAMILY,
        "expected_cities": list(EXPECTED_CITIES),
        "sections": sections,
        "pass": all(bool(section.get("pass")) for section in sections.values()),
        "phase5_live_migration": "not_applicable_to_current_wcir_shadow",
        "execution_impact": {
            "plans_created": 0,
            "orders_created": 0,
            "fills_created": 0,
            "notional_usd": 0.0,
        },
    }
    report["report_hash"] = canonical_json_hash(report)
    return report


def markdown(report: dict[str, Any]) -> str:
    raw = report["sections"]["raw_runtime"]
    legacy = report["sections"]["legacy_read_only"]
    lines = [
        "# WCIR Phase 7 audit",
        "",
        f"Result: **{'PASS' if report['pass'] else 'FAIL'}**",
        "",
        f"Framework: {report['framework_name']} (`{report['framework_id']}`)",
        "",
        f"Cities: {', '.join(report['expected_cities'])}",
        "",
        f"Candidates: {raw['unique_candidates']} unique / {raw['bundle_rows']} journal rows",
        "",
        f"Checkpoint blockers: {raw['blocker_rows']}",
        "",
        f"Trade intents: {raw['intent_rows']} (all zero-notional)",
        "",
        f"Execution handoffs: {raw['handoff_rows']} (all record-only)",
        "",
        f"Legacy rows: `{json.dumps(legacy['rows'], sort_keys=True)}`",
        "",
        "Plans/orders/fills/notional created by this audit: 0/0/0/$0.",
        "",
        f"Report hash: `{report['report_hash']}`",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo-root", type=Path, default=ROOT)
    value.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/weather/city_probability_runtime_v3.json",
    )
    value.add_argument(
        "--production",
        type=Path,
        default=ROOT / "src/strategies/runtime/production.yaml",
    )
    value.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(
            "/Volumes/jrs/weather_data_feed_service_runtime/output/"
            "city_probability_runtime_v3"
        ),
    )
    value.add_argument("--manifest", type=Path)
    value.add_argument("--json-out", type=Path)
    value.add_argument("--markdown-out", type=Path)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_audit(
        repo_root=args.repo_root,
        config_path=args.config,
        production_path=args.production,
        runtime_dir=args.runtime_dir,
        manifest_path=args.manifest,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown(report), encoding="utf-8")
    print(payload, end="")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
