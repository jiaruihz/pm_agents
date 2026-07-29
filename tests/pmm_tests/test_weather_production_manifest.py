from pathlib import Path

from scripts.ops import weather_production_manifest as manifest
from src.strategies.runtime.production import (
    WeatherProductionSpec,
    load_production_spec,
)


def production_spec(tmp_path: Path) -> WeatherProductionSpec:
    return WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path / "repo",
        canonical_db_path=tmp_path / "jrs/weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path / "jrs",
        canonical_tmux_socket="weather-data-feed-jrs",
        canonical_tmux_binary=tmp_path / "tmux",
    )


def test_committed_production_spec_owns_jrs_canonical_db():
    spec = load_production_spec()

    assert spec.canonical_db_path == Path("/Volumes/jrs/pm_agents/runtime/weather.db")
    assert spec.operational_repo_root == Path("/Users/deepsleep/projects/pm_agents")
    assert spec.compatibility_db_paths == (Path("runtime/weather.db"),)


def test_db_route_detects_distinct_local_and_jrs_files(tmp_path):
    spec = production_spec(tmp_path)
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("jrs", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.write_text("local", encoding="utf-8")

    result = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")

    assert result["status"] == "split"
    assert result["distinct_existing_paths"] == [str(local)]


def test_db_route_accepts_repo_compatibility_symlink_to_jrs(tmp_path):
    spec = production_spec(tmp_path)
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("jrs", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)

    result = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")

    assert result["status"] == "healthy"
    assert result["linked_compatibility_paths"] == [str(local)]


def test_process_parser_and_execution_mode_are_present_state_based():
    rows = manifest.parse_process_table(
        " 14098 1 Tue Jul 28 21:36:02 2026 "
        "/repo/.venv/bin/python scripts/ops/weather_fast.py --live --confirm-live\n"
        " 62952 1 Wed Jul 29 00:38:02 2026 python collector.py\n"
    )

    assert [row["pid"] for row in rows] == [14098, 62952]
    assert manifest.classify_execution_mode(rows[0]["command"]) == "live_confirmed"
    assert manifest.classify_execution_mode("python weather.py --live") == "live_unconfirmed"
    assert manifest.classify_execution_mode("python weather_shadow.py") == "zero_notional_shadow"


def test_lsof_parser_keeps_physical_db_consumers():
    consumers = manifest.parse_lsof_db_consumers(
        "p14098\ncpython3.12\nn/Volumes/jrs/pm_agents/runtime/weather.db\n"
        "p62952\ncpython3.12\nn/Users/me/repo/runtime/weather.db\n"
        "n/Users/me/repo/runtime/weather.db-wal\n"
    )

    assert consumers[14098]["paths"] == ["/Volumes/jrs/pm_agents/runtime/weather.db"]
    assert consumers[62952]["paths"] == [
        "/Users/me/repo/runtime/weather.db",
        "/Users/me/repo/runtime/weather.db-wal",
    ]


def test_redaction_hides_secret_arguments():
    command = "python weather.py --api-token abc --city Tokyo"

    assert manifest.redact_command(command) == "python weather.py --api-token '<redacted>' --city Tokyo"


def test_manifest_flags_noncanonical_db_consumers(tmp_path, monkeypatch):
    spec = production_spec(tmp_path)
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.write_text("split", encoding="utf-8")
    db_route = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[],
        launchctl_rows=[],
        db_route=db_route,
        db_consumers={
            7: {
                "pid": 7,
                "command": "python",
                "paths": [str(local), str(local) + "-wal"],
            }
        },
    )

    assert payload["status"] == "critical"
    assert "db_consumer_outside_canonical" in {
        item["kind"] for item in payload["findings"]
    }


def test_manifest_reports_registry_and_launch_agent_drift(tmp_path, monkeypatch):
    spec = production_spec(tmp_path)
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    db_route = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[
            {"session": "weather_unregistered", "panes": [], "process_pids": []}
        ],
        launchctl_rows=[
            {
                "label": "com.pm-agents.weather-canonical-refresh",
                "pid": None,
                "last_exit_status": 1,
            }
        ],
        db_route=db_route,
        db_consumers={},
    )

    findings = {item["kind"]: item for item in payload["findings"]}
    assert findings["tmux_sessions_missing_from_instance_registry"]["severity"] == "warning"
    assert findings["launch_agent_last_exit_nonzero"]["severity"] == "critical"
