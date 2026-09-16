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
    control_plane_release = spec.release("control_plane")
    canonical_refresh_release = spec.release(spec.canonical_refresh_release_id)
    assert spec.canonical_refresh_release_id == "canonical_refresh"
    assert (
        spec.canonical_refresh_checkout_root
        == canonical_refresh_release.checkout_root
    )
    assert spec.compatibility_db_paths == (Path("runtime/weather.db"),)
    assert spec.research_artifact_root == Path(
        "/Volumes/jrs-archive/pm_agents/research/artifact_store"
    )
    release_ids = [release.release_id for release in spec.releases]
    assert len(release_ids) == len(set(release_ids))
    assert control_plane_release.checkout_root == (
        spec.production_release_root
        / control_plane_release.release_id
        / control_plane_release.expected_repo_sha
    )
    assert len(control_plane_release.expected_repo_sha) == 40
    assert control_plane_release.runtime_bindings == (
        "operational_venv",
        "operational_env",
    )
    assert spec.release("core_carry_runtime").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/core_carry_runtime/"
        "444b7aef30fbd9bce06be8810ffd1a3c1db11f36"
    )
    assert spec.release("core_carry_runtime").runtime_bindings == (
        "operational_venv",
        "canonical_db",
        "operational_env",
    )
    assert spec.release("fast_source_runtime").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/fast_source_runtime/"
        "559d285ca9f111915ea4a80d2535bc40bb6c5722"
    )
    assert spec.release("fast_source_runtime").runtime_bindings == (
        "operational_venv",
        "canonical_db",
        "operational_env",
    )
    assert spec.release("core_carry_market_state_shadow").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/"
        "core_carry_market_state_shadow/"
        "a2cfc619a4c292ab3514895fecbc40a256356333"
    )
    assert spec.release("city_probability_runtime").checkout_root == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/city_probability_runtime/"
        "51c904bdfd220c83b097044cffa6b48ed2398f3b"
    )
    assert spec.release("city_probability_runtime").runtime_bindings == (
        "operational_venv",
        "canonical_db",
    )
    assert dict(spec.release("knmi").runtime_binding_sources)[
        "operational_env"
    ] == Path(
        "/Users/deepsleep/.local/share/pm_agents/releases/control_plane/"
        "588d33fcf89a19bc68a765129444bd2d59500c21/.env.knmi"
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


def test_production_spec_rejects_release_binding_without_operational_venv(tmp_path):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    raw["production_releases"][0]["runtime_bindings"] = ["canonical_db"]
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must include operational_venv"):
        load_production_spec(candidate)


def test_production_spec_rejects_refresh_release_shared_with_managed_runtime(
    tmp_path,
):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    shared = next(
        runtime
        for runtime in raw["managed_runtimes"]
        if runtime.get("release_id")
    )
    raw["canonical_refresh_release_id"] = shared["release_id"]
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must be a dedicated release"):
        load_production_spec(candidate)


def test_production_spec_rejects_freshness_budget_shorter_than_three_heartbeats(
    tmp_path,
):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    near = next(
        runtime
        for runtime in raw["managed_runtimes"]
        if runtime["instance_id"]
        == "current_yes_core_carry_near_core_ws_controller"
    )
    near["freshness_contracts"][0]["consumer_max_age_sec"] = 3
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="shorter than the heartbeat budget"):
        load_production_spec(candidate)


def test_production_spec_requires_cadence_on_freshness_producer(tmp_path):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    producer = next(
        runtime
        for runtime in raw["managed_runtimes"]
        if runtime["instance_id"] == "weather_market_books"
    )
    producer.pop("health_publish_interval_sec")
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must declare health_publish_interval_sec"):
        load_production_spec(candidate)


def test_production_spec_rejects_incomplete_live_runtime_contract(tmp_path):
    raw = yaml.safe_load(
        (ROOT / "src/strategies/runtime/production.yaml").read_text(encoding="utf-8")
    )
    core = next(
        runtime
        for runtime in raw["managed_runtimes"]
        if runtime["instance_id"] == "current_yes_core_carry_tiny_live_v2"
    )
    core.pop("restart_script")
    candidate = tmp_path / "production.yaml"
    candidate.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="live runtime must declare execution_mode=live"):
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


def test_paused_runtime_does_not_require_active_health_artifact(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="paused_live_probe",
        tmux_session="paused_live_probe",
        role="strategy",
        execution_mode="live",
        desired_state="paused",
        health_path=tmp_path / "missing_health.json",
        expected_health_fields=(("live_enabled", True),),
        expected_live=True,
        recovery_policy="guarded_live",
    )
    spec = WeatherProductionSpec(
        **{**production_spec(tmp_path).__dict__, "managed_runtimes": (runtime,)}
    )

    assert manifest.inspect_runtime_health_contracts(spec) == []


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


def test_inspect_tmux_includes_weather_pane_executable_in_process_pids(
    tmp_path, monkeypatch
):
    spec = production_spec(tmp_path)
    spec.canonical_tmux_binary.write_text("", encoding="utf-8")
    pane_pid = 101
    processes = [
        {
            "pid": pane_pid,
            "ppid": 1,
            "started": "Sun Sep  6 21:52:42 2026",
            "command": "python weather_controller.py --live --confirm-live",
        }
    ]

    def fake_run_command(args, **_kwargs):
        if "list-panes" in args:
            return __import__("subprocess").CompletedProcess(
                args,
                0,
                "weather_controller\t101\t/repo\tpython weather_controller.py\n",
                "",
            )
        return __import__("subprocess").CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(manifest, "run_command", fake_run_command)

    rows = manifest.inspect_tmux(spec, processes)

    assert rows[0]["process_pids"] == [pane_pid]


def test_runtime_evidence_uses_tmux_owned_health_not_shared_output_summary(tmp_path):
    shared_output = tmp_path / "shared"
    shared_output.mkdir()
    controller_health = shared_output / "controller_readiness.json"
    controller_sha = "a" * 40
    live_sha = "b" * 40
    controller_health.write_text(
        '{"status":"ready","deployed_repo_sha":"' + controller_sha + '"}',
        encoding="utf-8",
    )
    (shared_output / "latest_summary.json").write_text(
        '{"status":"ok","deployed_repo_sha":"' + live_sha + '"}',
        encoding="utf-8",
    )
    controller_checkout = tmp_path / "releases/controller" / controller_sha
    live_checkout = tmp_path / "releases/live" / live_sha
    controller = WeatherManagedRuntimeSpec(
        instance_id="controller",
        tmux_session="weather_controller",
        role="execution_control",
        execution_mode="live",
        release_id="controller",
        health_path=controller_health,
    )
    live = WeatherManagedRuntimeSpec(
        instance_id="live_runner",
        tmux_session="weather_live_runner",
        role="strategy",
        execution_mode="live",
        release_id="live",
        health_path=shared_output / "latest_summary.json",
    )
    spec = WeatherProductionSpec(
        **{**production_spec(tmp_path).__dict__, "managed_runtimes": (controller, live)}
    )
    releases_by_id = {
        "controller": WeatherProductionReleaseSpec(
            release_id="controller",
            checkout_root=controller_checkout,
            expected_repo_sha=controller_sha,
        ),
        "live": WeatherProductionReleaseSpec(
            release_id="live",
            checkout_root=live_checkout,
            expected_repo_sha=live_sha,
        ),
    }
    tokens = ["python", "weather_controller.py", "--output-dir", str(shared_output)]

    summary, ownership = manifest.runtime_evidence_for_process(
        pid=101,
        tokens=tokens,
        checkout=controller_checkout,
        pid_owners={101: (controller,)},
        releases_by_id=releases_by_id,
        spec=spec,
    )

    assert ownership["status"] == "resolved"
    assert ownership["instance_id"] == "controller"
    assert summary["path"] == str(controller_health)
    assert summary["deployed_repo_sha"] == controller_sha

    controller_health.unlink()
    missing_summary, missing_ownership = manifest.runtime_evidence_for_process(
        pid=101,
        tokens=tokens,
        checkout=controller_checkout,
        pid_owners={101: (controller,)},
        releases_by_id=releases_by_id,
        spec=spec,
    )

    assert missing_summary == {}
    assert missing_ownership == {
        "instance_id": "controller",
        "tmux_session": "weather_controller",
        "release_id": "controller",
        "status": "unreadable",
        "health_path": str(controller_health),
        "evidence_source": "managed_runtime_health_path",
    }


def test_runtime_evidence_keeps_legacy_only_when_no_registered_output_owner(tmp_path):
    shared_output = tmp_path / "shared"
    shared_output.mkdir()
    (shared_output / "latest_summary.json").write_text(
        '{"status":"ok","deployed_repo_sha":"live"}', encoding="utf-8"
    )
    runtime = WeatherManagedRuntimeSpec(
        instance_id="registered",
        tmux_session="weather_registered",
        role="strategy",
        execution_mode="live",
        health_path=shared_output / "registered_health.json",
    )
    spec = WeatherProductionSpec(
        **{**production_spec(tmp_path).__dict__, "managed_runtimes": (runtime,)}
    )
    shared_tokens = ["python", "weather_legacy.py", "--output-dir", str(shared_output)]

    blocked_summary, blocked_ownership = manifest.runtime_evidence_for_process(
        pid=77,
        tokens=shared_tokens,
        checkout=None,
        pid_owners={},
        releases_by_id={},
        spec=spec,
    )
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "latest_summary.json").write_text(
        '{"status":"ok","deployed_repo_sha":"legacy"}', encoding="utf-8"
    )
    legacy_summary, legacy_ownership = manifest.runtime_evidence_for_process(
        pid=78,
        tokens=["python", "weather_legacy.py", "--output-dir", str(legacy_dir)],
        checkout=None,
        pid_owners={},
        releases_by_id={},
        spec=spec,
    )

    assert blocked_summary == {}
    assert blocked_ownership["status"] == "unknown"
    assert blocked_ownership["instance_ids"] == ["registered"]
    assert legacy_summary["deployed_repo_sha"] == "legacy"
    assert legacy_ownership["status"] == "legacy"

    ambiguous_runtime = WeatherManagedRuntimeSpec(
        instance_id="also_registered",
        tmux_session="weather_also_registered",
        role="strategy",
        execution_mode="shadow",
    )
    ambiguous_summary, ambiguous_ownership = manifest.runtime_evidence_for_process(
        pid=79,
        tokens=shared_tokens,
        checkout=None,
        pid_owners={79: (runtime, ambiguous_runtime)},
        releases_by_id={},
        spec=spec,
    )

    assert ambiguous_summary == {}
    assert ambiguous_ownership["status"] == "ambiguous"


def test_manifest_detects_real_sha_mismatch_from_tmux_owned_health(tmp_path, monkeypatch):
    shared_output = tmp_path / "shared"
    shared_output.mkdir()
    controller_sha = "a" * 40
    live_sha = "b" * 40
    controller_health = shared_output / "controller_readiness.json"
    controller_health.write_text(
        '{"status":"ready","deployed_repo_sha":"' + controller_sha + '"}',
        encoding="utf-8",
    )
    (shared_output / "latest_summary.json").write_text(
        '{"status":"ok","deployed_repo_sha":"' + live_sha + '"}',
        encoding="utf-8",
    )
    controller_checkout = tmp_path / "releases/controller" / controller_sha
    live_checkout = tmp_path / "releases/live" / live_sha
    controller = WeatherManagedRuntimeSpec(
        instance_id="controller",
        tmux_session="weather_controller",
        role="execution_control",
        execution_mode="live",
        release_id="controller",
        health_path=controller_health,
    )
    live = WeatherManagedRuntimeSpec(
        instance_id="live_runner",
        tmux_session="weather_live_runner",
        role="strategy",
        execution_mode="live",
        release_id="live",
        health_path=shared_output / "latest_summary.json",
    )
    releases = (
        WeatherProductionReleaseSpec(
            release_id="controller",
            checkout_root=controller_checkout,
            expected_repo_sha=controller_sha,
        ),
        WeatherProductionReleaseSpec(
            release_id="live",
            checkout_root=live_checkout,
            expected_repo_sha=live_sha,
        ),
    )
    spec = WeatherProductionSpec(
        **{
            **production_spec(tmp_path).__dict__,
            "managed_runtimes": (controller, live),
            "releases": releases,
        }
    )
    process_rows = [
        {
            "pid": 101,
            "ppid": 1,
            "started": "Sun Sep  6 21:52:42 2026",
            "command": f"python weather_controller.py --output-dir {shared_output} --live --confirm-live",
        },
        {
            "pid": 102,
            "ppid": 1,
            "started": "Sun Sep  6 21:52:42 2026",
            "command": f"python weather_runner.py --output-dir {shared_output} --live --confirm-live",
        },
    ]
    checkouts = {101: controller_checkout, 102: live_checkout}
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])
    monkeypatch.setattr(
        manifest, "inspect_release_runtime_bindings", lambda _spec, _release: []
    )
    monkeypatch.setattr(
        manifest,
        "checkout_for_process",
        lambda row, **_kwargs: checkouts[int(row["pid"])],
    )

    def fake_git_metadata(root, cache):
        root = Path(root)
        expected = next(
            (release.expected_repo_sha for release in releases if root == release.checkout_root),
            "c" * 40,
        )
        payload = {
            "root": str(root),
            "head": expected,
            "branch": "HEAD",
            "dirty_tracked": False,
            "git_common_dir": str(tmp_path / ".git"),
        }
        cache[str(root)] = payload
        return payload

    monkeypatch.setattr(manifest, "git_metadata", fake_git_metadata)
    tmux_rows = [
        {"session": "weather_controller", "panes": [{"pane_pid": 101}], "process_pids": []},
        {"session": "weather_live_runner", "panes": [{"pane_pid": 102}], "process_pids": []},
    ]
    kwargs = {
        "spec": spec,
        "processes": process_rows,
        "tmux_rows": tmux_rows,
        "launchctl_rows": [],
        "db_route": {"status": "healthy"},
        "db_consumers": {},
    }

    healthy_payload = manifest.build_manifest(**kwargs)
    assert not [
        item for item in healthy_payload["findings"] if item["kind"] == "process_release_sha_mismatch"
    ]
    observed = {row["pid"]: row for row in healthy_payload["processes"]}
    assert observed[101]["runtime_summary"]["path"] == str(controller_health)
    assert observed[102]["runtime_summary"]["path"] == str(shared_output / "latest_summary.json")

    controller_health.write_text(
        '{"status":"ready","deployed_repo_sha":"' + live_sha + '"}',
        encoding="utf-8",
    )
    mismatch_payload = manifest.build_manifest(**kwargs)
    mismatch = next(
        item
        for item in mismatch_payload["findings"]
        if item["kind"] == "process_release_sha_mismatch"
    )
    assert mismatch["detail"]["pid"] == 101
    assert mismatch["detail"]["deployed_repo_sha"] == live_sha


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
            {"session": "weather_unregistered", "panes": [], "process_pids": []},
            {
                "session": "weather_reliability_worker_123_456",
                "panes": [],
                "process_pids": [],
            },
            {
                "session": "weather_controller_health_read_123_456",
                "panes": [],
                "process_pids": [],
            },
        ],
        launchctl_rows=[
            {
                "label": "com.pm-agents.weather-canonical-refresh",
                "pid": None,
                "last_exit_status": 1,
            },
            {
                "label": "com.pm-agents.tag-proxy-route-maintain",
                "pid": None,
                "last_exit_status": 1,
            },
        ],
        db_route=db_route,
        db_consumers={},
    )

    findings = {item["kind"]: item for item in payload["findings"]}
    assert (
        findings["tmux_sessions_missing_from_instance_registry"]["severity"]
        == "warning"
    )
    assert findings["tmux_sessions_missing_from_instance_registry"]["detail"][
        "sessions"
    ] == ["weather_unregistered"]
    assert findings["launch_agent_last_exit_nonzero"]["severity"] == "critical"
    assert findings["launch_agent_last_exit_nonzero"]["detail"]["jobs"] == [
        {
            "label": "com.pm-agents.weather-canonical-refresh",
            "pid": None,
            "last_exit_status": 1,
        }
    ]
    assert (
        findings["host_auxiliary_launch_agent_last_exit_nonzero"]["severity"]
        == "warning"
    )
    assert findings["host_auxiliary_launch_agent_last_exit_nonzero"]["detail"][
        "jobs"
    ] == [
        {
            "label": "com.pm-agents.tag-proxy-route-maintain",
            "pid": None,
            "last_exit_status": 1,
        }
    ]
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


def test_manifest_rejects_declared_release_runtime_binding_conflict(
    tmp_path, monkeypatch
):
    base = production_spec(tmp_path)
    operational = base.operational_repo_root
    source_python = operational / ".venv/bin/python"
    source_python.parent.mkdir(parents=True)
    source_python.write_text("#!/bin/sh\n", encoding="utf-8")
    source_python.chmod(0o755)
    (operational / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    base.canonical_db_path.parent.mkdir(parents=True)
    base.canonical_db_path.write_text("canonical", encoding="utf-8")
    compatibility = operational / "runtime/weather.db"
    compatibility.parent.mkdir(parents=True)
    compatibility.symlink_to(base.canonical_db_path)

    release_root = tmp_path / "releases"
    sha = "a" * 40
    checkout = release_root / "collector" / sha
    checkout.mkdir(parents=True)
    (checkout / ".venv").symlink_to(operational / ".venv", target_is_directory=True)
    (checkout / ".env").symlink_to(operational / ".env")
    local_db = checkout / "runtime/weather.db"
    local_db.parent.mkdir()
    local_db.write_text("split", encoding="utf-8")
    release = WeatherProductionReleaseSpec(
        release_id="collector",
        checkout_root=checkout,
        expected_repo_sha=sha,
        runtime_bindings=(
            "operational_venv",
            "canonical_db",
            "operational_env",
        ),
    )
    spec = WeatherProductionSpec(
        **{
            **base.__dict__,
            "production_release_root": release_root,
            "releases": (release,),
        }
    )
    db_route = manifest.inspect_db_route(spec, repo_root=operational)
    monkeypatch.setattr(manifest, "load_instance_specs", lambda: [])
    monkeypatch.setattr(manifest, "inspect_persistent_worktrees", lambda _spec: [])

    def fake_git_metadata(root, cache):
        payload = {
            "root": str(root),
            "head": sha,
            "branch": "HEAD",
            "dirty_tracked": False,
            "git_common_dir": str(operational / ".git"),
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
        if item["kind"] == "production_release_runtime_binding_mismatch"
    )
    assert finding["severity"] == "critical"
    assert finding["detail"]["release_id"] == "collector"
    assert finding["detail"]["bindings"] == [
        {
            "binding": "canonical_db",
            "target": str(local_db),
            "expected_source": str(base.canonical_db_path),
            "source_healthy": True,
            "target_is_symlink": False,
            "status": "target_not_symlink",
        }
    ]


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
