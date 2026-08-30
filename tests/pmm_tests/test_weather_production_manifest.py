from pathlib import Path

import pytest
import yaml

from scripts.ops import weather_production_manifest as manifest
from src.strategies.runtime.production import (
    WeatherManagedRuntimeSpec,
    WeatherProductionReleaseSpec,
    WeatherProductionSpec,
    load_production_spec,
)

ROOT = Path(__file__).resolve().parents[2]


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
        market_proxy_state_path=tmp_path / "market_proxy_state.json",
        market_proxy_default_url="http://127.0.0.1:7897",
    )


def test_committed_production_spec_owns_jrs_canonical_db():
    spec = load_production_spec()

    assert spec.canonical_db_path == Path("/Volumes/jrs/pm_agents/runtime/weather.db")
    assert spec.operational_repo_root == Path("/Users/deepsleep/projects/pm_agents")
    assert spec.production_release_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases"
    )
    assert spec.canonical_refresh_checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/control_plane/"
        "588d33fcf89a19bc68a765129444bd2d59500c21"
    )
    assert spec.compatibility_db_paths == (Path("runtime/weather.db"),)
    assert spec.research_artifact_root == Path(
        "/Volumes/jrs-archive/pm_agents/research/artifact_store"
    )
    release_ids = [release.release_id for release in spec.releases]
    assert len(release_ids) == len(set(release_ids))
    assert spec.release("control_plane").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/control_plane/"
        "588d33fcf89a19bc68a765129444bd2d59500c21"
    )
    assert len(spec.release("control_plane").expected_repo_sha) == 40
    assert spec.release("core_carry_runtime").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/core_carry_runtime/"
        "264cf84d6a1b70994682253de815aba9b944d9a2"
    )


def test_production_spec_rejects_release_outside_managed_root(tmp_path):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    raw["production_releases"][0]["checkout_root"] = "/tmp/unmanaged-release"
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="production release checkout_root must equal"):
        load_production_spec(candidate)


def test_nested_project_worktree_is_included_in_lifecycle_audit(tmp_path, monkeypatch):
    spec = production_spec(tmp_path)
    nested = spec.operational_repo_root / ".worktrees/research"
    monkeypatch.setattr(
        manifest,
        "run_command",
        lambda *args, **kwargs: __import__("subprocess").CompletedProcess(
            args=[], returncode=0, stdout=f"worktree {nested}\nHEAD abc\n", stderr=""
        ),
    )

    rows = manifest.inspect_persistent_worktrees(spec)

    assert rows == [{"root": str(nested), "registered": False, "exists": False}]


def test_old_sha_under_managed_release_root_is_included_in_lifecycle_audit(
    tmp_path, monkeypatch
):
    base = production_spec(tmp_path)
    release_root = tmp_path / "releases"
    spec = WeatherProductionSpec(
        **{**base.__dict__, "production_release_root": release_root}
    )
    old_sha = release_root / "collector" / ("a" * 40)
    monkeypatch.setattr(
        manifest,
        "run_command",
        lambda *args, **kwargs: __import__("subprocess").CompletedProcess(
            args=[], returncode=0, stdout=f"worktree {old_sha}\nHEAD abc\n", stderr=""
        ),
    )

    rows = manifest.inspect_persistent_worktrees(spec)

    assert rows == [{"root": str(old_sha), "registered": False, "exists": False}]


def test_manifest_reports_runtime_health_contract_mismatch(tmp_path, monkeypatch):
    health = tmp_path / "feed/collector_health.json"
    health.parent.mkdir(parents=True)
    health.write_text('{"daily_payload_budget_bytes": 2000000000}', encoding="utf-8")
    runtime = WeatherManagedRuntimeSpec(
        instance_id="collector",
        tmux_session="weather_collector",
        role="collector",
        execution_mode="collector",
        health_path=health,
        expected_health_fields=(("daily_payload_budget_bytes", 3000000000),),
    )
    spec = WeatherProductionSpec(
        **{**production_spec(tmp_path).__dict__, "managed_runtimes": (runtime,)}
    )
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[],
        launchctl_rows=[],
        db_route=manifest.inspect_db_route(spec, repo_root=tmp_path / "repo"),
        db_consumers={},
    )

    finding = next(
        item
        for item in payload["findings"]
        if item["kind"] == "runtime_health_contract_mismatch"
    )
    assert finding["severity"] == "critical"
    mismatch = finding["detail"]["runtimes"][0]["mismatches"][0]
    assert mismatch["expected"] == 3000000000
    assert mismatch["observed"] == 2000000000


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
    assert result["read_probe"] == {"readable": True, "error": None}


def test_db_route_rejects_metadata_only_access(tmp_path, monkeypatch):
    spec = production_spec(tmp_path)
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("jrs", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    monkeypatch.setattr(
        manifest,
        "probe_file_readable",
        lambda _path: {"readable": False, "error": "Operation not permitted"},
    )

    result = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")

    assert result["status"] == "inaccessible"
    assert result["read_probe"]["readable"] is False


def test_db_route_retries_jrs_read_in_canonical_context(tmp_path, monkeypatch):
    spec = WeatherProductionSpec(
        **{
            **production_spec(tmp_path).__dict__,
            "production_storage_root": tmp_path / "jrs",
        }
    )
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("jrs", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    monkeypatch.setattr(
        manifest,
        "probe_file_readable",
        lambda _path: {"readable": False, "error": "Operation not permitted"},
    )
    monkeypatch.setattr(
        manifest,
        "probe_file_readable_via_canonical_context",
        lambda _spec, _path: {
            "readable": True,
            "error": None,
            "read_context": "canonical_jrs_tmux",
        },
    )

    result = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")

    assert result["status"] == "healthy"
    assert result["read_probe"]["read_context"] == "canonical_jrs_tmux"


def test_process_parser_and_execution_mode_are_present_state_based():
    rows = manifest.parse_process_table(
        " 14098 1 Tue Jul 28 21:36:02 2026 "
        "/repo/.venv/bin/python scripts/ops/weather_fast.py --live --confirm-live\n"
        " 62952 1 Wed Jul 29 00:38:02 2026 python collector.py\n"
    )

    assert [row["pid"] for row in rows] == [14098, 62952]
    assert manifest.classify_execution_mode(rows[0]["command"]) == "live_confirmed"
    assert (
        manifest.classify_execution_mode("python weather.py --live")
        == "live_unconfirmed"
    )
    assert (
        manifest.classify_execution_mode("python weather_shadow.py")
        == "zero_notional_shadow"
    )


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

    assert (
        manifest.redact_command(command)
        == "python weather.py --api-token '<redacted>' --city Tokyo"
    )


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
    monkeypatch.setattr(
        manifest,
        "inspect_persistent_worktrees",
        lambda _spec: [
            {
                "root": "/Users/deepsleep/projects/pm_agents_old_fix",
                "registered": False,
                "exists": True,
            }
        ],
    )

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
    assert (
        findings["tmux_sessions_missing_from_instance_registry"]["severity"]
        == "warning"
    )
    assert findings["launch_agent_last_exit_nonzero"]["severity"] == "critical"
    assert findings["unregistered_persistent_worktrees"]["severity"] == "warning"


def test_manifest_reports_managed_session_without_controller_contract(
    tmp_path, monkeypatch
):
    base = production_spec(tmp_path)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="collector",
        tmux_session="weather_collector",
        role="collector",
        execution_mode="collector",
        release_id="collector_release",
    )
    spec = WeatherProductionSpec(**{**base.__dict__, "managed_runtimes": (runtime,)})
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    db_route = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[
            {
                "session": "weather_collector",
                "panes": [],
                "process_pids": [],
                "production_config_path": None,
            }
        ],
        launchctl_rows=[],
        db_route=db_route,
        db_consumers={},
    )

    finding = next(
        item
        for item in payload["findings"]
        if item["kind"] == "managed_session_production_config_not_injected"
    )
    assert finding["severity"] == "warning"
    assert finding["detail"]["sessions"][0]["instance_id"] == "collector"


def test_manifest_warns_when_declared_production_checkout_is_dirty(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "prod"
    spec = WeatherProductionSpec(
        **{
            **production_spec(tmp_path).__dict__,
            "canonical_refresh_checkout_root": checkout,
        }
    )
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    db_route = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])

    def fake_git_metadata(root, cache):
        payload = {
            "root": str(root),
            "head": "abc123",
            "branch": "HEAD",
            "dirty_tracked": True,
        }
        cache[str(root)] = payload
        return payload

    monkeypatch.setattr(manifest, "git_metadata", fake_git_metadata)

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[],
        launchctl_rows=[],
        db_route=db_route,
        db_consumers={},
    )

    findings = {item["kind"]: item for item in payload["findings"]}
    assert payload["status"] == "warning"
    assert findings["production_checkouts_dirty"] == {
        "severity": "warning",
        "kind": "production_checkouts_dirty",
        "message": "registered or running production checkouts contain tracked changes",
        "detail": {
            "checkouts": [
                {
                    "root": str(checkout),
                    "head": "abc123",
                    "branch": "HEAD",
                    "dirty_tracked": True,
                }
            ]
        },
    }


def test_manifest_rejects_release_from_independent_clone(tmp_path, monkeypatch):
    base = production_spec(tmp_path)
    release_root = tmp_path / "releases"
    sha = "a" * 40
    checkout = release_root / "collector" / sha
    release = WeatherProductionReleaseSpec(
        release_id="collector",
        checkout_root=checkout,
        expected_repo_sha=sha,
    )
    spec = WeatherProductionSpec(
        **{
            **base.__dict__,
            "production_release_root": release_root,
            "releases": (release,),
        }
    )
    spec.canonical_db_path.parent.mkdir(parents=True)
    spec.canonical_db_path.write_text("canonical", encoding="utf-8")
    local = tmp_path / "repo/runtime/weather.db"
    local.parent.mkdir(parents=True)
    local.symlink_to(spec.canonical_db_path)
    db_route = manifest.inspect_db_route(spec, repo_root=tmp_path / "repo")
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])

    def fake_git_metadata(root, cache):
        root = Path(root)
        payload = {
            "root": str(root),
            "head": sha if root == checkout else "b" * 40,
            "branch": "HEAD",
            "dirty_tracked": False,
            "git_common_dir": (
                str(tmp_path / "foreign/.git")
                if root == checkout
                else str(spec.operational_repo_root / ".git")
            ),
        }
        cache[str(root)] = payload
        return payload

    monkeypatch.setattr(manifest, "git_metadata", fake_git_metadata)

    payload = manifest.build_manifest(
        spec=spec,
        processes=[],
        tmux_rows=[],
        launchctl_rows=[],
        db_route=db_route,
        db_consumers={},
    )

    finding = next(
        item
        for item in payload["findings"]
        if item["kind"] == "production_release_not_linked_worktree"
    )
    assert finding["severity"] == "critical"
    assert finding["detail"]["release_id"] == "collector"


def test_prechange_comparison_fails_when_existing_session_disappears():
    baseline = {
        "generated_at_utc": "2026-08-02T06:00:00Z",
        "tmux_sessions": [
            {"session": "weather_data_feed_jrs"},
            {"session": "weather_current_yes_core_carry_tiny_live_v2"},
        ],
    }
    current = {
        "status": "healthy",
        "findings": [],
        "tmux_sessions": [{"session": "weather_data_feed_jrs"}],
    }

    payload = manifest.compare_prechange_manifest(current, baseline)

    assert payload["status"] == "critical"
    assert payload["prechange_comparison"]["missing_sessions"] == [
        "weather_current_yes_core_carry_tiny_live_v2"
    ]
    assert payload["findings"][0]["kind"] == (
        "canonical_tmux_sessions_lost_since_prechange"
    )


def test_prechange_comparison_requires_explicit_allowance_for_intended_stop():
    baseline = {
        "generated_at_utc": "2026-08-02T06:00:00Z",
        "tmux_sessions": [{"session": "weather_shadow_v1"}],
    }
    current = {"status": "healthy", "findings": [], "tmux_sessions": []}

    payload = manifest.compare_prechange_manifest(
        current,
        baseline,
        allow_missing_sessions=["weather_shadow_v1"],
    )

    assert payload["status"] == "healthy"
    assert payload["prechange_comparison"]["missing_sessions"] == []


def test_prechange_comparison_ignores_bounded_reliability_worker_session():
    baseline = {
        "generated_at_utc": "2026-08-02T06:00:00Z",
        "tmux_sessions": [
            {"session": "weather_data_feed_jrs"},
            {"session": "weather_reliability_worker_123_456"},
        ],
    }
    current = {
        "status": "healthy",
        "findings": [],
        "tmux_sessions": [{"session": "weather_data_feed_jrs"}],
    }

    payload = manifest.compare_prechange_manifest(current, baseline)

    assert payload["status"] == "healthy"
    assert payload["prechange_comparison"]["baseline_sessions"] == [
        "weather_data_feed_jrs"
    ]
    assert payload["prechange_comparison"]["missing_sessions"] == []


def test_weather_prompts_and_analysis_skills_require_manifest_preflight():
    paths = [
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "skills/weather-fact-rebuild/SKILL.md",
        ROOT / "skills/weather-live-account-reconcile/SKILL.md",
        ROOT / "skills/weather-strategy-deploy/SKILL.md",
        ROOT / "skills/weather-strategy-exposure/SKILL.md",
        ROOT / "skills/weather-strategy-lineage/SKILL.md",
        ROOT / "skills/weather-strategy-performance/SKILL.md",
        ROOT / "skills/weather-strategy-research/SKILL.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "weather_production_manifest.py --strict" in text, path
