from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ops.weather_canonical_refresh_preflight import evaluate_preflight
from scripts.ops.weather_canonical_refresh_stage import run_stage
from scripts.ops.weather_production_spec_snapshot import (
    ProductionSpecSnapshotError,
    capture_production_spec_snapshot,
    verify_production_spec_source,
)
from src.strategies.runtime.production import load_production_spec


ROOT = Path(__file__).resolve().parents[2]


def _preflight(tmp_path: Path, **overrides):
    db = tmp_path / "weather.db"
    db.write_bytes(b"db")
    values = {
        "manifest": {"status": "healthy", "db_route": {"status": "healthy"}},
        "db_path": db,
        "canonical_db_path": db,
        "jrs_probe": {"status": "healthy"},
        "feed_health": {"status": "ok"},
        "registry_audit": {"ok": True, "registry_build_id": "registry"},
    }
    values.update(overrides)
    return evaluate_preflight(**values)


def test_preflight_scopes_migration_blockers_without_skipping_live_accounting(
    tmp_path: Path,
) -> None:
    ready = _preflight(tmp_path)
    assert ready["status"] == "ready"
    assert ready["live_accounting_allowed"] is True
    assert ready["migration_allowed"] is True

    migration_blocked = _preflight(
        tmp_path,
        manifest={"status": "critical", "db_route": {"status": "healthy"}},
        feed_health={"status": "fail"},
    )
    assert migration_blocked["status"] == "migration_blocked"
    assert migration_blocked["live_accounting_allowed"] is True
    assert migration_blocked["migration_allowed"] is False


def test_preflight_blocks_every_write_on_db_identity_or_jrs_failure(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other.db"
    other.write_bytes(b"other")
    mismatch = _preflight(tmp_path, canonical_db_path=other)
    assert mismatch["status"] == "all_writes_blocked"
    assert mismatch["live_accounting_allowed"] is False

    jrs = _preflight(tmp_path, jrs_probe={"status": "critical"})
    assert jrs["status"] == "all_writes_blocked"


def test_preflight_allows_only_explicitly_safe_manifest_warnings(
    tmp_path: Path,
) -> None:
    benign = _preflight(
        tmp_path,
        manifest={
            "status": "warning",
            "db_route": {"status": "healthy"},
            "findings": [
                {
                    "kind": "unregistered_persistent_worktrees",
                    "severity": "warning",
                }
            ],
        },
    )
    assert benign["migration_allowed"] is True
    assert benign["allowed_manifest_warnings"] == [
        "unregistered_persistent_worktrees"
    ]

    unknown = _preflight(
        tmp_path,
        manifest={
            "status": "warning",
            "db_route": {"status": "healthy"},
            "findings": [{"kind": "new_warning", "severity": "warning"}],
        },
    )
    assert unknown["migration_allowed"] is False
    assert unknown["allowed_manifest_warnings"] == []

    malformed = _preflight(
        tmp_path,
        manifest={
            "status": "warning",
            "db_route": {"status": "healthy"},
            "findings": [],
        },
    )
    assert malformed["migration_allowed"] is False


def test_stage_records_pass_timeout_and_wal_growth(tmp_path: Path) -> None:
    db = tmp_path / "weather.db"
    db.write_bytes(b"db")
    report = tmp_path / "reports" / "pass.json"
    code, payload = run_stage(
        stage="pass",
        command=[sys.executable, "-c", "print('ok')"],
        db_path=db,
        report_path=report,
        max_seconds=2,
        max_wal_bytes=1024,
        max_wal_growth_bytes=100,
    )
    assert code == 0 and payload["status"] == "passed"
    assert json.loads(report.read_text())["stdout_tail"].strip() == "ok"

    code, payload = run_stage(
        stage="timeout",
        command=[sys.executable, "-c", "import time; time.sleep(1)"],
        db_path=db,
        report_path=tmp_path / "reports" / "timeout.json",
        max_seconds=0.02,
        max_wal_bytes=1024,
        max_wal_growth_bytes=100,
    )
    assert code == 124 and payload["status"] == "timeout"

    wal = Path(str(db) + "-wal")
    code, payload = run_stage(
        stage="growth",
        command=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(wal)!r}).write_bytes(b'x' * 20)",
        ],
        db_path=db,
        report_path=tmp_path / "reports" / "growth.json",
        max_seconds=2,
        max_wal_bytes=1024,
        max_wal_growth_bytes=10,
    )
    assert code == 75 and payload["status"] == "wal_budget_exceeded"


def test_stage_does_not_start_when_absolute_wal_budget_is_already_breached(
    tmp_path: Path,
) -> None:
    db = tmp_path / "weather.db"
    db.write_bytes(b"db")
    Path(str(db) + "-wal").write_bytes(b"x" * 20)
    marker = tmp_path / "must-not-exist"
    code, payload = run_stage(
        stage="preexisting",
        command=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
        db_path=db,
        report_path=tmp_path / "reports" / "preexisting.json",
        max_seconds=2,
        max_wal_bytes=10,
        max_wal_growth_bytes=10,
    )
    assert code == 75
    assert payload["status"] == "blocked_preexisting_wal_budget"
    assert not marker.exists()


def test_production_spec_snapshot_is_stable_and_detects_later_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "src/strategies/runtime/production.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("version: test\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    snapshot = repo / "runtime/production_spec_snapshot.yaml"
    report = repo / "runtime/production_spec_snapshot.json"

    payload = capture_production_spec_snapshot(
        source=source,
        snapshot=snapshot,
        report=report,
    )
    assert payload["ok"] is True
    assert snapshot.read_text(encoding="utf-8") == "version: test\n"
    assert json.loads(report.read_text(encoding="utf-8"))["sha256"] == payload[
        "sha256"
    ]
    assert verify_production_spec_source(
        source=source,
        expected_sha256=payload["sha256"],
    )["ok"] is True

    source.write_text("version: changed\n", encoding="utf-8")
    with pytest.raises(
        ProductionSpecSnapshotError,
        match="changed during canonical refresh",
    ):
        verify_production_spec_source(
            source=source,
            expected_sha256=payload["sha256"],
        )


def test_explicit_bundled_spec_ignores_inherited_config(monkeypatch) -> None:
    monkeypatch.setenv("WEATHER_PRODUCTION_CONFIG", "/tmp/must-not-be-loaded.yaml")
    spec = load_production_spec(ROOT / "src/strategies/runtime/production.yaml")
    assert spec.operational_repo_root == Path("/Users/deepsleep/projects/pm_agents")


def test_launchd_refresh_uses_registry_preflight_and_stage_wrapper() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/ops/run_weather_canonical_refresh_launchd.sh"
    ).read_text(encoding="utf-8")
    assert 'physical_path("wcir_decision_bundles")' in script
    assert 'cursor_state_path("wcir_decision_bundles")' in script
    assert "weather_canonical_refresh_preflight.py" in script
    assert "weather_canonical_refresh_stage.py" in script
    assert "migration_allowed" in script
    assert "--require-existing-schema" in script
    assert 'BUNDLED_PRODUCTION_SPEC="$PROJECT_DIR/' in script
    assert "load_production_spec(Path(sys.argv[1]))" in script
    assert 'PRODUCTION_SPEC_SOURCE="$OPERATIONAL_PROJECT_DIR/' in script
    assert "weather_production_spec_snapshot.py capture" in script
    assert "weather_production_spec_snapshot.py verify" in script
    assert 'export WEATHER_PRODUCTION_CONFIG="$PRODUCTION_SPEC_PATH"' in script
    assert '--production-spec "$PRODUCTION_SPEC_PATH"' in script
