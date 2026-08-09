from __future__ import annotations

import sqlite3
import subprocess
import hashlib
import json
from pathlib import Path

from scripts.ops import weather_storage_identity_audit as storage_audit
from scripts.ops.weather_storage_identity_audit import (
    build_report,
    sqlite_schema_fingerprint,
)
from src.strategies.runtime.production import (
    WeatherManagedRuntimeSpec,
    WeatherProductionSpec,
)
from weather_dashboard.db.connection import DB_PATH


def _spec(tmp_path: Path, canonical: Path, compatibility: Path) -> WeatherProductionSpec:
    return WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=canonical,
        compatibility_db_paths=(compatibility,),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=canonical.parent,
        canonical_tmux_socket="test",
        canonical_tmux_binary=Path("/usr/bin/tmux"),
        market_proxy_state_path=tmp_path / "feed/output/market_proxy/state.json",
        market_proxy_default_url="http://127.0.0.1:7891",
        managed_runtimes=(
            WeatherManagedRuntimeSpec(
                instance_id="live",
                tmux_session="live",
                role="strategy",
                execution_mode="live",
                expected_live=True,
                live_order_path=tmp_path / "feed/output/live/orders.jsonl",
                recovery_policy="guarded_live",
            ),
        ),
    )


def test_storage_audit_distinguishes_alias_and_distinct_weather_db(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    canonical = tmp_path / "jrs/weather.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = runtime / "weather.db"
    compatibility.symlink_to(canonical)
    distinct = tmp_path / "feed/weather.db"
    distinct.parent.mkdir()
    with sqlite3.connect(distinct) as conn:
        conn.execute("CREATE TABLE other(id INTEGER PRIMARY KEY)")

    report = build_report(_spec(tmp_path, canonical, compatibility))

    by_path = {item["path"]: item for item in report["databases"]}
    assert by_path[str(compatibility)]["classification"] == "compatibility_alias"
    assert by_path[str(distinct)]["classification"] == "distinct_canonical_name"
    assert report["status"] == "critical"


def test_schema_probe_has_process_level_timeout(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "blocked.db"
    database.touch()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(storage_audit.subprocess, "run", timeout)

    fingerprint, error = sqlite_schema_fingerprint(database, timeout_sec=0.01)

    assert fingerprint is None
    assert error == "schema_probe_timeout_after_0.01s"


def test_storage_audit_reuses_schema_probe_for_same_inode(tmp_path: Path, monkeypatch) -> None:
    canonical = tmp_path / "runtime/physical.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = tmp_path / "runtime/weather-link.db"
    compatibility.symlink_to(canonical)
    calls: list[Path] = []

    def fingerprint(path: Path, **kwargs):
        calls.append(path)
        return "fingerprint", None

    monkeypatch.setattr(storage_audit, "sqlite_schema_fingerprint", fingerprint)

    report = build_report(_spec(tmp_path, canonical, compatibility))

    assert calls == [canonical]
    by_path = {item["path"]: item for item in report["databases"]}
    assert by_path[str(canonical)]["schema_identity_reused"] is True
    assert by_path[str(compatibility)]["schema_identity_reused"] is True


def test_production_spec_is_single_authority_for_live_journals(tmp_path: Path) -> None:
    canonical = tmp_path / "runtime/physical.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = tmp_path / "runtime/weather-link.db"
    compatibility.symlink_to(canonical)
    journal = tmp_path / "feed/output/live/orders.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"schema_version":"orders_v1","order_id":"1"}\n', encoding="utf-8")

    spec = _spec(tmp_path, canonical, compatibility)
    report = build_report(spec)

    assert spec.active_live_order_paths() == (journal,)
    assert report["active_live_order_journals"][0]["schema_versions"] == {"orders_v1": 1}


def test_storage_audit_preserves_legacy_unversioned_prefix_without_warning(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "runtime/physical.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = tmp_path / "runtime/weather-link.db"
    compatibility.symlink_to(canonical)
    journal = tmp_path / "feed/output/live/orders.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        '{"record_type":"order","order_id":"legacy"}\n'
        '{"schema_version":"orders_v1","record_type":"order","order_id":"current"}\n',
        encoding="utf-8",
    )

    report = build_report(_spec(tmp_path, canonical, compatibility))

    journal_report = report["active_live_order_journals"][0]
    assert journal_report["legacy_unversioned_prefix_rows"] == 1
    assert journal_report["missing_schema_after_versioned"] == 0
    findings = [item for item in report["findings"] if item["path"] == str(journal)]
    assert findings == [
        {
            "severity": "info",
            "kind": "live_order_legacy_unversioned_prefix",
            "path": str(journal),
            "row_count": 1,
            "message": "append-only legacy prefix is preserved; current tail is versioned",
        }
    ]


def test_storage_audit_allows_distinct_schemas_for_distinct_record_types(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "runtime/physical.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = tmp_path / "runtime/weather-link.db"
    compatibility.symlink_to(canonical)
    journal = tmp_path / "feed/output/live/orders.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        '{"schema_version":"orders_v2","record_type":"order","order_id":"1"}\n'
        '{"schema_version":"exit_v1","event_type":"exit","order_id":"1"}\n',
        encoding="utf-8",
    )

    report = build_report(_spec(tmp_path, canonical, compatibility))

    assert report["active_live_order_journals"][0][
        "schema_versions_by_record_type"
    ] == {"order": {"orders_v2": 1}, "exit": {"exit_v1": 1}}
    assert not any(
        item["kind"] == "live_order_schema_version_drift"
        for item in report["findings"]
    )


def test_storage_audit_verifies_recoverable_quarantine(tmp_path: Path) -> None:
    canonical = tmp_path / "runtime/physical.db"
    canonical.parent.mkdir()
    with sqlite3.connect(canonical) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY)")
    compatibility = tmp_path / "runtime/weather-link.db"
    compatibility.symlink_to(canonical)
    quarantine = tmp_path / "runtime/_legacy/storage_quarantine"
    quarantine.mkdir(parents=True)
    retired = quarantine / "retired.db"
    retired.write_bytes(b"retired")
    retired.chmod(0o444)
    digest = hashlib.sha256(retired.read_bytes()).hexdigest()
    (quarantine / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "weather_storage_quarantine_v1",
                "quarantined_at_utc": "2026-08-06T00:00:00Z",
                "files": [
                    {
                        "original_path": "runtime/retired.db",
                        "quarantine_path": "runtime/_legacy/storage_quarantine/retired.db",
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_report(_spec(tmp_path, canonical, compatibility))

    assert report["storage_quarantine"]["exists"] is True
    assert report["storage_quarantine"]["files"][0]["actual_sha256"] == digest
    assert not any(
        item["kind"].startswith("storage_quarantine_")
        for item in report["findings"]
    )


def test_dashboard_connection_default_never_points_to_parallel_dashboard_db() -> None:
    assert Path(DB_PATH).name == "weather.db"


def test_dashboard_and_health_entrypoints_have_no_parallel_production_lists() -> None:
    root = Path(__file__).resolve().parents[2]
    run_stack = (root / "scripts/weather_dashboard/run_stack.sh").read_text(encoding="utf-8")
    health = (root / "scripts/ops/weather_data_feed_prod_health_check.py").read_text(encoding="utf-8")
    installer = (root / "scripts/ops/install_weather_dashboard_user_services.sh").read_text(encoding="utf-8")

    for forbidden in ("uvicorn", "nohup", "npm run dev", "START_API", "START_FE"):
        assert forbidden not in run_stack
    assert "load_production_spec().canonical_db_path" in run_stack
    assert 'DB_PATH="$REPO_ROOT/runtime/weather.db"' not in run_stack
    for forbidden in (
        "weather-predict/output",
        "low_price_yes_lottery_tiny_live_v1_orders.jsonl",
        "late_window_residual_split_v1/live_orders.jsonl",
        "d1_yes_high_mid_live_v1/live_orders.jsonl",
    ):
        assert forbidden not in health
    assert "ExecStart=" not in installer
    assert "weather_production_ctl.py" in installer
