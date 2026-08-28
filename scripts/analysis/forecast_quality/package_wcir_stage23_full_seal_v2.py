#!/usr/bin/env python3
"""Build and verify strict WCIR full-seal-v2 and bounded-canary packages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import locale
import platform
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.package_wcir_stage23_rev2_closure import (
    _root_entries,
    entry,
    write_directory_manifest,
    write_package,
)


REVIEW = ROOT / "reviews/wcir_next_print"
SEAL = REVIEW / "stage_02_03_rev2_full_seal_v2"
CANARY = REVIEW / "collector_clock_amendment_v2_canary"
PRIOR = REVIEW / "stage_02_03_rev2_closure"
STAGE2 = REVIEW / "stage_02_rev2"
STAGE3 = REVIEW / "stage_03_rev2"
EVENTS = REVIEW / "stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_text(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def write_freezes(code_files: tuple[str, ...]) -> None:
    dependencies = run_text(str(ROOT / ".venv/bin/python"), "-m", "pip", "freeze") + "\n"
    head = run_text("git", "rev-parse", "HEAD")
    production_yaml = ROOT / "src/strategies/runtime/production.yaml"
    committed_production = subprocess.run(
        ["git", "show", f"{head}:src/strategies/runtime/production.yaml"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    production_match = hashlib.sha256(committed_production).hexdigest() == sha256(production_yaml)
    manifest_check = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/ops/weather_production_manifest.py", "--strict"],
        cwd=ROOT,
        capture_output=True,
    )
    if manifest_check.returncode != 0:
        raise RuntimeError("production manifest strict postcheck failed")
    freeze = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit_sha": head,
        "branch": run_text("git", "branch", "--show-current"),
        "code_and_test_identities": [
            {"path": relative, "size_bytes": (ROOT / relative).stat().st_size, "sha256": sha256(ROOT / relative)}
            for relative in code_files
        ],
        "environment": {
            "python": platform.python_version(),
            "python_executable": str(ROOT / ".venv/bin/python"),
            "os": platform.platform(),
            "machine": platform.machine(),
            "locale": locale.setlocale(locale.LC_ALL, None),
            "timezone": "Asia/Shanghai (CST +0800)",
            "pip_freeze_sha256": hashlib.sha256(dependencies.encode()).hexdigest(),
        },
        "production_boundary": {
            "production_yaml_sha256": sha256(production_yaml),
            "production_yaml_matches_code_commit": production_match,
            "production_config_modified_by_this_work": False,
            "production_deployment_performed": False,
            "production_manifest_strict_postcheck": "healthy",
            "production_manifest_stdout_sha256": hashlib.sha256(manifest_check.stdout).hexdigest(),
        },
    }
    if not production_match:
        raise RuntimeError("production.yaml differs from frozen code commit")
    for root in (SEAL, CANARY):
        (root / "PYTHON_ENVIRONMENT.txt").write_text(dependencies, encoding="utf-8")
        (root / "FINAL_CODE_AND_ENVIRONMENT_FREEZE.json").write_text(
            json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    canary = json.loads((CANARY / "NETWORK_CANARY_RESULTS.json").read_text())
    zero = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_channel": "PUBLIC_MARKET_READ_ONLY",
        "authentication_used": canary["network"]["authentication_used"],
        "order_or_user_channel_used": canary["network"]["order_or_user_channel_used"],
        "production_consumer_joined": canary["production_consumer_joined"],
        "production_config_modified": canary["production_config_modified"],
        "daemon_started": canary["daemon_started"],
        "orders": canary["orders"],
        "fills": canary["fills"],
        "notional_usd": canary["notional_usd"],
        "production_yaml_sha256": sha256(production_yaml),
        "production_yaml_matches_code_commit": production_match,
        "status": "PASS",
    }
    if any((zero["authentication_used"], zero["order_or_user_channel_used"], zero["production_consumer_joined"], zero["production_config_modified"], zero["daemon_started"], zero["orders"], zero["fills"], zero["notional_usd"])):
        raise RuntimeError(f"zero-notional isolation failure: {zero}")
    (CANARY / "ZERO_NOTIONAL_AND_ISOLATION_AUDIT.json").write_text(
        json.dumps(zero, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (SEAL / "ZERO_NOTIONAL_AUDIT.json").write_text(
        json.dumps({**zero, "network_channel": "NO_NETWORK_USED_BY_OFFLINE_FULL_SEAL_BUILDER"}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def selected_root_entries(root: Path, prefix: str) -> list[dict[str, object]]:
    return _root_entries(root, prefix)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp")
    args = parser.parse_args()
    stamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    code_files = (
        "scripts/analysis/forecast_quality/wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/wcir_stage23_full_seal_v2.py",
        "scripts/analysis/forecast_quality/wcir_collector_clock_shadow.py",
        "scripts/analysis/forecast_quality/wcir_isolated_network_canary.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_full_seal_v2.py",
        "tests/research_tests/test_wcir_stage23_rev2_closure.py",
        "tests/research_tests/test_wcir_stage23_full_seal_v2.py",
    )
    write_freezes(code_files)
    write_directory_manifest(SEAL)
    write_directory_manifest(CANARY)
    seal_entries = selected_root_entries(SEAL, "stage_02_03_rev2_full_seal_v2")
    seal_entries += selected_root_entries(PRIOR, "prior_full_evidence_seal")
    seal_entries += selected_root_entries(STAGE2, "immutable_stage_02_rev2")
    seal_entries += selected_root_entries(STAGE3, "immutable_stage_03_rev2")
    seal_entries.append(entry(EVENTS, "immutable_stage_03/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"))
    seal_entries += [entry(ROOT / relative, relative) for relative in code_files]

    canary_entries = selected_root_entries(CANARY, "collector_clock_amendment_v2_canary")
    canary_entries += [entry(ROOT / relative, relative) for relative in code_files[2:]]
    seal_out = SEAL / f"wcir-stage23-rev2-full-evidence-seal-v2-{stamp}.zip"
    canary_out = CANARY / f"wcir-collector-clock-v2-bounded-network-canary-{stamp}.zip"
    result = {
        "full_evidence_seal": write_package(seal_out, seal_entries, "wcir_stage23_rev2_full_evidence_seal_v2"),
        "bounded_network_canary": write_package(canary_out, canary_entries, "wcir_collector_clock_v2_bounded_public_read_only_canary"),
    }
    (SEAL / "FULL_REVIEW_PACKAGE.sha256").write_text(
        f"{result['full_evidence_seal']['sha256']}  {seal_out.name}\n", encoding="utf-8"
    )
    (CANARY / "FULL_REVIEW_PACKAGE.sha256").write_text(
        f"{result['bounded_network_canary']['sha256']}  {canary_out.name}\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
