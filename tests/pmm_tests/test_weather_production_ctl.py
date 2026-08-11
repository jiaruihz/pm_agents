import json
import os
import subprocess
from pathlib import Path

from scripts.ops import weather_production_ctl as ctl
from src.strategies.runtime.production import (
    WeatherManagedRuntimeSpec,
    WeatherProductionSpec,
    load_production_spec,
)


ROOT = Path(__file__).resolve().parents[2]


def production_spec(
    tmp_path: Path, runtimes: tuple[WeatherManagedRuntimeSpec, ...]
) -> WeatherProductionSpec:
    return WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path,
        canonical_tmux_socket="weather-data-feed-jrs",
        canonical_tmux_binary=tmp_path / "tmux",
        market_proxy_state_path=tmp_path / "market_proxy.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=runtimes,
    )


def observed(*sessions: str) -> dict:
    return {
        "generated_at_utc": "2026-08-02T16:00:00Z",
        "status": "healthy",
        "findings": [],
        "tmux_sessions": [
            {
                "session": session,
                "panes": [
                    {
                        "pane_current_path": "/prod",
                        "pane_start_command": "python runner.py --live --confirm-live",
                    }
                ],
            }
            for session in sessions
        ],
    }


def test_committed_production_spec_declares_current_live_control_plane():
    spec = load_production_spec()
    by_id = {item.instance_id: item for item in spec.managed_runtimes}

    assert spec.production_storage_root == Path("/Volumes/jrs")
    assert spec.production_storage_volume_uuid == "93F5EA71-2AAB-4A45-9A21-18220BBE4614"
    assert spec.archive_storage_root == Path("/Volumes/jrs-archive")
    assert spec.archive_storage_volume_uuid == "748B704B-E074-40C1-A5F0-AC9205B52E9F"
    assert spec.research_artifact_root == Path(
        "/Volumes/jrs-archive/pm_agents/research/artifact_store"
    )
    assert by_id["current_yes_core_carry_tiny_live_v2"].expected_live is True
    assert by_id["current_yes_core_carry_tiny_live_v2"].recovery_policy == "guarded_live"
    assert by_id["current_yes_core_carry_tiny_live_v2"].release_id == "core_carry_runtime"
    assert by_id["current_yes_core_carry_tiny_live_v2"].resolved_restart_script() == Path(
        "/Users/deepsleep/projects/pm_agents_core_carry_prod/scripts/ops/"
        "start_weather_current_yes_core_carry_tiny_live_v2.sh"
    )
    assert by_id["current_yes_core_carry_tiny_live_v2"].live_order_path == Path(
        "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
        "current_yes_core_carry_tiny_live_v2/live_orders.jsonl"
    )
    assert by_id["weather_dashboard_api"].health_format == "http_json"
    assert by_id["weather_dashboard_api"].health_url == "http://127.0.0.1:8000/health"
    assert by_id["fast_source_prev_no_trial_v1"].dependencies == (
        "weather_data_feed_jrs",
        "weather_live_cross_observations",
        "weather_knmi_open_data_jrs",
    )
    assert "weather_canonical_refresh" in spec.allowed_unmanaged_sessions
    assert by_id["weather_knmi_open_data_jrs"].checkout_root == Path(
        "/Users/deepsleep/projects/pm_agents_knmi_recovery"
    )
    assert by_id["weather_knmi_open_data_jrs"].resolved_restart_script() == Path(
        "/Users/deepsleep/projects/pm_agents/scripts/ops/"
        "start_mac_knmi_open_data_jrs_tmux.sh"
    )
    assert by_id["weather_knmi_open_data_jrs"].resolved_start_script() == Path(
        "/Users/deepsleep/projects/pm_agents/scripts/ops/"
        "start_mac_knmi_open_data_jrs_tmux.sh"
    )
    assert by_id["weather_knmi_open_data_jrs"].max_health_age_sec == 900
    assert by_id["weather_city_probability_runtime_v3"].max_health_age_sec == 1200
    assert by_id["weather_city_probability_runtime_v3"].accepted_health_statuses == ("ok",)
    proposal_shadow = by_id["polymarket_weather_proposal_reward_shadow_v1"]
    assert proposal_shadow.release_id == "proposal_reward"
    assert proposal_shadow.execution_mode == "shadow"
    assert proposal_shadow.expected_live is False
    assert proposal_shadow.health_path == Path(
        "/Volumes/jrs/weather_data_feed_service_runtime/output/"
        "proposal_reward_shadow_v1/latest.json"
    )
    assert proposal_shadow.dependencies == ("weather_jrs_context_keeper",)
    assert by_id[
        "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1"
    ].accepted_health_statuses == ("ok",)
    observation_checkout = Path(
        "/Users/deepsleep/projects/pm_agents_fast_observation_prod"
    )
    assert by_id[
        "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1"
    ].checkout_root == observation_checkout
    assert by_id["weather_fast_source_stale_book"].checkout_root == observation_checkout
    assert by_id["weather_fast_source_stale_book"].health_path == Path(
        "/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_stale_book/health.json"
    )
    assert by_id["weather_source_event_ladder_repricing_shadow"].checkout_root == observation_checkout
    assert by_id["weather_source_event_ladder_repricing_shadow"].health_path == Path(
        "/Volumes/jrs/weather_data_feed_service_runtime/output/"
        "source_event_ladder_repricing_shadow/health.json"
    )
    assert by_id["weather_current_yes_heat_death_shadow_v1"].max_health_age_sec == 2400
    assert by_id["weather_knmi_first_seen_ladder_v1"].checkout_root == observation_checkout
    assert by_id["weather_helsinki_pre_cross_active_ladder_shadow"].checkout_root == Path(
        "/Users/deepsleep/projects/pm_agents_market_books_prod"
    )
    assert by_id[
        "weather_helsinki_pre_cross_active_ladder_shadow"
    ].resolved_start_script() == Path(
        "/Users/deepsleep/projects/pm_agents_market_books_prod/scripts/ops/"
        "start_weather_helsinki_pre_cross_active_ladder_shadow.sh"
    )


def test_every_business_runtime_has_controller_start_contract():
    spec = load_production_spec()
    business = [
        item
        for item in spec.managed_runtimes
        if item.instance_id != "weather_jrs_context_keeper"
    ]

    assert business
    assert all(item.recovery_policy != "manual" for item in business)
    assert all(item.checkout_root is not None for item in business)
    assert all(item.release_id is not None for item in business)
    assert all(item.resolved_start_script() is not None for item in business)
    assert all(item.health_path is not None or item.health_url is not None for item in business)
    assert all(item.resolved_start_script().is_file() for item in business)
    run_capture = next(
        item
        for item in business
        if item.instance_id == "weather_forecast_run_capture_v1"
    )
    assert run_capture.execution_mode == "collector"
    assert run_capture.recovery_policy == "safe"


def test_current_migrated_runtimes_remain_non_live_and_dormant_d1_is_not_desired():
    spec = load_production_spec()
    migrated_ids = {
        "low_price_yes_integrated_tail_shadow_v2",
        "low_price_yes_lottery_shadow_v1",
        "metar_reversal_false_fade_reheat_shadow_v1",
        "regime_routed_no_shadow_v1",
        "tmax_distribution_edge_first_lock_no_current_yes_shadow_v1",
        "weather_current_yes_heat_death_shadow_v1",
        "weather_fast_source_stale_book",
        "weather_market_books",
        "weather_helsinki_pre_cross_active_ladder_shadow",
        "weather_korea_first_seen_state_v1",
        "weather_runtime_monitor",
        "weather_source_event_ladder_repricing_shadow",
        "weather_tokyo_current_break_active_ladder_shadow_v1",
    }
    migrated = [
        item for item in spec.managed_runtimes if item.instance_id in migrated_ids
    ]
    actual_ids = {item.instance_id for item in spec.managed_runtimes}

    assert {
        "d1_multisource_consensus_shadow_v1",
        "europe_d1_distance2_dual_no_shadow_v1",
    }.isdisjoint(actual_ids)
    assert len(migrated) == len(migrated_ids)
    assert all(item.expected_live is False for item in migrated)
    assert all(item.recovery_policy == "safe" for item in migrated)


def test_health_checks_session_freshness_status_and_live_flags(tmp_path):
    health_path = tmp_path / "latest.json"
    health_path.write_text(
        json.dumps({"status": "ok", "live_enabled": True}), encoding="utf-8"
    )
    os.utime(health_path, (1000.0, 1000.0))
    runtime = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live_session",
        role="strategy",
        execution_mode="live",
        checkout_root=Path("/prod"),
        health_path=health_path,
        max_health_age_sec=60,
        accepted_health_statuses=("ok",),
        expected_live=True,
        recovery_policy="guarded_live",
    )

    report = ctl.evaluate_production_health(
        production_spec(tmp_path, (runtime,)),
        observed("live_session"),
        now_epoch=1030.0,
    )

    assert report["status"] == "healthy"
    assert report["runtimes"][0]["health_age_sec"] == 30.0
    assert report["runtimes"][0]["issues"] == []


def test_health_supports_explicit_mtime_heartbeat(tmp_path):
    health_path = tmp_path / "runner.log"
    health_path.write_text("not json\n", encoding="utf-8")
    os.utime(health_path, (1000.0, 1000.0))
    runtime = WeatherManagedRuntimeSpec(
        instance_id="collector",
        tmux_session="collector",
        role="collector",
        execution_mode="collector",
        health_path=health_path,
        health_format="mtime",
        max_health_age_sec=60,
        recovery_policy="safe",
    )

    report = ctl.evaluate_production_health(
        production_spec(tmp_path, (runtime,)),
        observed("collector"),
        now_epoch=1030.0,
    )

    assert report["status"] == "healthy"
    assert report["runtimes"][0]["health_format"] == "mtime"
    assert report["runtimes"][0]["health_age_sec"] == 30.0


def test_health_supports_db_backed_http_json(tmp_path, monkeypatch):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="api",
        tmux_session="api",
        role="dashboard_api",
        execution_mode="read_only_api",
        health_url="http://127.0.0.1:8000/health",
        health_format="http_json",
        accepted_health_statuses=("ok",),
        recovery_policy="safe",
    )
    monkeypatch.setattr(ctl, "_read_http_json", lambda _url: ({"status": "critical"}, None))

    report = ctl.evaluate_production_health(
        production_spec(tmp_path, (runtime,)), observed("api"), now_epoch=1030.0
    )

    assert report["status"] == "critical"
    assert report["runtimes"][0]["issues"] == ["health_status_unaccepted"]


def test_health_propagates_missing_dependency_to_live_runtime(tmp_path):
    feed = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed_session",
        role="data_feed",
        execution_mode="collector",
        recovery_policy="safe",
    )
    live = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live_session",
        role="strategy",
        execution_mode="live",
        dependencies=("feed",),
        expected_live=True,
        recovery_policy="guarded_live",
    )

    report = ctl.evaluate_production_health(
        production_spec(tmp_path, (feed, live)),
        observed("live_session"),
        now_epoch=1000.0,
    )
    rows = {row["instance_id"]: row for row in report["runtimes"]}

    assert report["status"] == "critical"
    assert rows["feed"]["issues"] == ["tmux_session_missing"]
    assert rows["live"]["issues"] == ["dependency_unhealthy:feed"]


def test_plan_only_starts_missing_runtime_with_recovery_contract(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed_session",
        role="data_feed",
        execution_mode="collector",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    report = ctl.evaluate_production_health(spec, observed(), now_epoch=1000.0)

    plan = ctl.build_plan(spec, report)

    assert plan == [
        {
            "instance_id": "feed",
            "action": "start",
            "reason": "tmux_session_missing",
            "recovery_policy": "safe",
            "expected_live": False,
            "start_script": str(tmp_path / "start.sh"),
        }
    ]


def test_failed_jrs_context_blocks_all_missing_starts(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed_session",
        role="data_feed",
        execution_mode="collector",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        health_path=Path("/Volumes/jrs/feed/latest.json"),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    report = ctl.evaluate_production_health(spec, observed(), now_epoch=1000.0)
    report = ctl.attach_jrs_context_health(
        report, {"status": "critical", "returncode": 1}
    )

    assert report["status"] == "critical"
    assert report["runtimes"][0]["issues"][-1] == "jrs_context_unhealthy"
    assert ctl.build_plan(spec, report)[0] == {
        "instance_id": "feed",
        "action": "manual_recovery_required",
        "reason": "jrs_context_unhealthy",
        "recovery_policy": "safe",
        "expected_live": False,
        "start_script": str(tmp_path / "start.sh"),
    }


def test_failed_jrs_context_marks_keeper_critical(tmp_path):
    keeper = WeatherManagedRuntimeSpec(
        instance_id="weather_jrs_context_keeper",
        tmux_session="weather_jrs_context_keeper",
        role="infrastructure",
        execution_mode="infrastructure",
        recovery_policy="manual",
    )
    spec = production_spec(tmp_path, (keeper,))
    report = ctl.evaluate_production_health(
        spec, observed("weather_jrs_context_keeper"), now_epoch=1000.0
    )
    report = ctl.attach_jrs_context_health(
        report, {"status": "critical", "returncode": 1}
    )

    assert report["runtimes"][0]["status"] == "critical"
    assert report["runtimes"][0]["issues"] == ["jrs_write_probe_failed"]


def test_failed_jrs_context_marks_same_server_manual_runtime_critical(tmp_path):
    shadow = WeatherManagedRuntimeSpec(
        instance_id="shadow",
        tmux_session="shadow",
        role="shadow",
        execution_mode="shadow",
        recovery_policy="manual",
    )
    spec = production_spec(tmp_path, (shadow,))
    report = ctl.evaluate_production_health(
        spec, observed("shadow"), now_epoch=1000.0
    )
    report = ctl.attach_jrs_context_health(
        report, {"status": "critical", "returncode": 1}
    )

    assert report["runtimes"][0]["issues"] == ["jrs_context_unhealthy"]
    assert report["runtimes"][0]["status"] == "critical"


def test_start_order_respects_runtime_dependencies(tmp_path):
    feed = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed",
        role="collector",
        execution_mode="collector",
        recovery_policy="safe",
    )
    market = WeatherManagedRuntimeSpec(
        instance_id="market",
        tmux_session="market",
        role="collector",
        execution_mode="collector",
        dependencies=("feed",),
        recovery_policy="safe",
    )
    model = WeatherManagedRuntimeSpec(
        instance_id="model",
        tmux_session="model",
        role="shadow",
        execution_mode="shadow",
        dependencies=("market",),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (model, market, feed))
    plan = [
        {"instance_id": "model", "action": "start"},
        {"instance_id": "market", "action": "start"},
        {"instance_id": "feed", "action": "start"},
    ]

    assert [
        row["instance_id"] for row in ctl._ordered_start_items(spec, plan)
    ] == ["feed", "market", "model"]


def test_recovery_requires_live_confirmation(tmp_path):
    spec = production_spec(tmp_path, ())

    try:
        ctl.recover_jrs_context(spec, observed("existing"), confirm_live=False)
    except RuntimeError as exc:
        assert str(exc) == "recover-jrs-context requires --confirm-live"
    else:
        raise AssertionError("expected live confirmation failure")


def test_storage_migration_requires_live_confirmation(tmp_path):
    spec = production_spec(tmp_path, ())

    try:
        ctl.migrate_production_storage(
            spec,
            observed("existing"),
            staging_root=tmp_path / "staging",
            confirm_live=False,
        )
    except RuntimeError as exc:
        assert str(exc) == "migrate-production-storage requires --confirm-live"
    else:
        raise AssertionError("expected live confirmation failure")


def test_storage_migration_fails_before_tmux_on_wrong_volume(monkeypatch, tmp_path):
    base = production_spec(tmp_path, ())
    spec = WeatherProductionSpec(
        **{
            **base.__dict__,
            "production_storage_volume_uuid": "NEW",
            "archive_storage_volume_uuid": "OLD",
        }
    )
    monkeypatch.setattr(
        ctl.manifest_tool,
        "inspect_volume_identity",
        lambda path: {"volume_uuid": "WRONG"},
    )
    tmux_calls = []
    monkeypatch.setattr(
        ctl, "_tmux_on_socket", lambda *args, **kwargs: tmux_calls.append(args)
    )

    try:
        ctl.migrate_production_storage(
            spec,
            observed("existing"),
            staging_root=tmp_path / "staging",
            confirm_live=True,
        )
    except RuntimeError as exc:
        assert "source volume identity mismatch" in str(exc)
    else:
        raise AssertionError("expected volume identity failure")

    assert tmux_calls == []


def test_recovery_retries_canonical_server_start(monkeypatch, tmp_path):
    spec = production_spec(tmp_path, ())
    calls = []

    class Result:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout

    def fake_tmux(_spec, *args):
        calls.append(args)
        if args == ("kill-server",):
            return Result()
        if args[0] == "new-session":
            attempts = sum(1 for call in calls if call[0] == "new-session")
            return Result(returncode=1 if attempts == 1 else 0)
        raise AssertionError(args)

    monkeypatch.setattr(ctl, "_tmux", fake_tmux)
    monkeypatch.setattr(
        ctl,
        "collect_prospective_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0},
    )
    monkeypatch.setattr(
        ctl,
        "collect_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0},
    )
    monkeypatch.setattr(ctl.time, "sleep", lambda _seconds: None)

    actions = ctl.recover_jrs_context(
        spec,
        {"tmux_sessions": []},
        confirm_live=True,
    )

    assert sum(1 for call in calls if call[0] == "new-session") == 2
    assert actions[-1]["action"] == "jrs_write_probe"


def test_failed_prospective_probe_preserves_canonical_server(monkeypatch, tmp_path):
    spec = production_spec(tmp_path, ())
    canonical_calls = []
    monkeypatch.setattr(
        ctl,
        "collect_prospective_jrs_context_health",
        lambda _spec: {
            "status": "critical",
            "returncode": 1,
            "output": "Operation not permitted",
        },
    )
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda _spec, *args: canonical_calls.append(args),
    )

    try:
        ctl.recover_jrs_context(
            spec,
            observed("live"),
            confirm_live=True,
        )
    except RuntimeError as exc:
        assert "canonical server preserved" in str(exc)
    else:
        raise AssertionError("expected prospective permission failure")

    assert canonical_calls == []


def test_prospective_probe_uses_ephemeral_session_not_run_shell(monkeypatch, tmp_path):
    spec = production_spec(tmp_path, ())
    spec.canonical_db_path.write_bytes(b"x")
    calls = []

    def fake_tmux_on_socket(_spec, _socket, *args):
        calls.append(args)
        if args[0] == "new-session" and str(args[3]).endswith("_io"):
            completed = subprocess.run(
                ["/bin/sh", "-c", str(args[-1])],
                capture_output=True,
                text=True,
                check=False,
            )
            return completed
        if args[0] == "has-session":
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ctl, "_tmux_on_socket", fake_tmux_on_socket)

    report = ctl.collect_prospective_jrs_context_health(spec)

    assert report["status"] == "healthy"
    assert not any(args[0] == "run-shell" for args in calls)


def test_tmux_new_host_uses_audited_bash_default_shell(monkeypatch, tmp_path):
    spec = production_spec(tmp_path, ())
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ctl.subprocess, "run", fake_run)

    ctl._tmux_on_socket(spec, "probe", "new-session", "-d", "true")

    assert captured["env"]["SHELL"] == "/bin/bash"
    assert captured["command"][:3] == [str(spec.canonical_tmux_binary), "-L", "probe"]


def test_checked_tmux_sessions_use_unique_names(monkeypatch, tmp_path):
    spec = production_spec(tmp_path, ())
    session_names = []

    def fake_tmux_on_socket(_spec, _socket, *args):
        if args[0] == "new-session":
            session_names.append(str(args[3]))
            completed = subprocess.run(
                ["/bin/sh", "-c", str(args[-1])],
                capture_output=True,
                text=True,
                check=False,
            )
            return subprocess.CompletedProcess(args, 0, completed.stdout, completed.stderr)
        if args[0] == "has-session":
            return subprocess.CompletedProcess(args, 1, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(ctl, "_tmux_on_socket", fake_tmux_on_socket)

    first = ctl._run_tmux_checked(spec, "probe", "health", "printf first", timeout_sec=1)
    second = ctl._run_tmux_checked(spec, "probe", "health", "printf second", timeout_sec=1)

    assert first.stdout == "first"
    assert second.stdout == "second"
    assert session_names[0].startswith("health_")
    assert session_names[1].startswith("health_")
    assert session_names[0] != session_names[1]


def test_prospective_probe_reports_first_io_failure(monkeypatch, tmp_path):
    base = production_spec(tmp_path, ())
    base.canonical_db_path.write_bytes(b"x")
    spec = WeatherProductionSpec(
        **{
            **base.__dict__,
            "data_feed_runtime_root": Path("/dev/null/blocked"),
        }
    )

    def fake_tmux_on_socket(_spec, _socket, *args):
        if args[0] == "new-session" and str(args[3]).endswith("_io"):
            completed = subprocess.run(
                ["/bin/sh", "-c", str(args[-1])],
                capture_output=True,
                text=True,
                check=False,
            )
            return subprocess.CompletedProcess(
                args, 0, completed.stdout, completed.stderr
            )
        if args[0] == "has-session":
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ctl, "_tmux_on_socket", fake_tmux_on_socket)

    report = ctl.collect_prospective_jrs_context_health(spec)

    assert report["status"] == "critical"
    assert report["returncode"] != 0
    assert "Not a directory" in report["output"]


def test_persist_recovery_manifest_is_complete(tmp_path):
    snapshot = observed("one", "two")

    target = ctl.persist_recovery_manifest(snapshot, directory=tmp_path)

    assert target.parent == tmp_path
    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert list(tmp_path.glob("*.tmp")) == []


def test_restore_rows_preserve_every_session_and_pane():
    snapshot = observed("one")
    snapshot["tmux_sessions"][0]["panes"].append(
        {"pane_current_path": "/prod2", "pane_start_command": "python b.py"}
    )

    assert ctl._pane_restore_rows(snapshot) == [
        {
            "session": "one",
            "panes": [
                {
                    "cwd": "/prod",
                    "command": "python runner.py --live --confirm-live",
                },
                {"cwd": "/prod2", "command": "python b.py"},
            ],
        }
    ]


def test_restore_rows_decode_tmux_quoted_start_command():
    snapshot = observed("one")
    snapshot["tmux_sessions"][0]["panes"][0]["pane_start_command"] = (
        '"cd \'/tmp\' && exec sleep 60"'
    )

    assert ctl._pane_restore_rows(snapshot)[0]["panes"][0]["command"] == (
        "cd '/tmp' && exec sleep 60"
    )


def test_recovery_does_not_replay_allowed_unmanaged_oneshot(monkeypatch, tmp_path):
    spec = WeatherProductionSpec(
        **{
            **production_spec(tmp_path, ()).__dict__,
            "allowed_unmanaged_sessions": ("weather_canonical_refresh",),
        }
    )
    snapshot = observed("weather_canonical_refresh")
    calls = []

    class Result:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(
        ctl,
        "collect_prospective_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0},
    )
    monkeypatch.setattr(
        ctl,
        "collect_jrs_context_health",
        lambda _spec: {"status": "healthy", "returncode": 0},
    )
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda _spec, *args: calls.append(args) or Result(),
    )

    ctl.recover_jrs_context(spec, snapshot, confirm_live=True)

    assert not any(
        "weather_canonical_refresh" in call
        for args in calls
        for call in args
    )
    baseline = ctl.without_allowed_unmanaged_sessions(spec, snapshot)
    assert baseline["tmux_sessions"] == []


def test_manual_runtime_never_becomes_automatic_start(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="legacy_shadow",
        tmux_session="legacy_shadow",
        role="shadow",
        execution_mode="shadow",
        recovery_policy="manual",
    )
    spec = production_spec(tmp_path, (runtime,))
    report = ctl.evaluate_production_health(spec, observed(), now_epoch=1000.0)

    assert ctl.build_plan(spec, report)[0]["action"] == "manual_recovery_required"


def test_live_recovery_is_blocked_without_explicit_confirmation(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live",
        role="strategy",
        execution_mode="live",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        expected_live=True,
        recovery_policy="guarded_live",
    )

    result = ctl._run_start(runtime, confirm_live=False)

    assert result == {
        "instance_id": "live",
        "status": "blocked",
        "reason": "confirm_live_required",
    }


def test_controller_injects_tmux_mutation_authority_for_start(tmp_path):
    marker = tmp_path / "authority.txt"
    config_marker = tmp_path / "production-config.txt"
    script = tmp_path / "start.sh"
    script.write_text(
        f"#!/bin/sh\nprintf '%s' \"$WEATHER_JRS_TMUX_MUTATION_AUTHORITY\" > {marker}\n"
        f"printf '%s' \"$WEATHER_PRODUCTION_CONFIG\" > {config_marker}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="shadow",
        tmux_session="shadow",
        role="shadow",
        execution_mode="shadow",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        recovery_policy="safe",
    )

    result = ctl._run_start(runtime, confirm_live=False)

    assert result["status"] == "started"
    assert marker.read_text(encoding="utf-8") == "controller"
    assert config_marker.read_text(encoding="utf-8") == str(
        ROOT / "src/strategies/runtime/production.yaml"
    )


def test_restart_requires_explicit_contract(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed",
        role="collector",
        execution_mode="collector",
        checkout_root=tmp_path,
        recovery_policy="safe",
    )

    assert ctl._run_restart(
        production_spec(tmp_path, (runtime,)), runtime, confirm_live=False
    ) == {
        "instance_id": "feed",
        "status": "blocked",
        "reason": "start_contract_missing",
    }


def test_restart_runs_registered_contract(tmp_path):
    marker = tmp_path / "authority.txt"
    script = tmp_path / "restart.sh"
    script.write_text(
        f"#!/bin/sh\nprintf '%s' \"$WEATHER_JRS_TMUX_MUTATION_AUTHORITY\" > {marker}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="feed",
        tmux_session="feed",
        role="collector",
        execution_mode="collector",
        checkout_root=tmp_path,
        restart_script=Path("restart.sh"),
        recovery_policy="safe",
    )

    result = ctl._run_restart(
        production_spec(tmp_path, (runtime,)), runtime, confirm_live=False
    )

    assert result["status"] == "restarted"
    assert result["returncode"] == 0
    assert marker.read_text(encoding="utf-8") == "controller"


def test_controller_can_restart_safe_non_live_runtime_from_start_contract(
    monkeypatch, tmp_path
):
    marker = tmp_path / "started.txt"
    script = tmp_path / "start.sh"
    script.write_text(f"#!/bin/sh\nprintf started > {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="shadow",
        tmux_session="shadow",
        role="shadow",
        execution_mode="shadow",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    calls = []
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda _spec, *args: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = ctl._run_restart(spec, runtime, confirm_live=False)

    assert calls == [("kill-session", "-t", "=shadow")]
    assert result["status"] == "restarted"
    assert result["restart_mode"] == "controller_stop_then_registered_start"
    assert marker.read_text(encoding="utf-8") == "started"


def test_controller_restart_preflights_git_checkout_before_stop(monkeypatch, tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    script = tmp_path / "start.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="shadow",
        tmux_session="shadow",
        role="shadow",
        execution_mode="shadow",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    calls = []
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda _spec, *args: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = ctl._run_restart(spec, runtime, confirm_live=False)

    assert calls == []
    assert result["status"] == "error"
    assert result["reason"].startswith("checkout_bootstrap_missing_venv:")


def test_controller_restart_starts_missing_safe_runtime(monkeypatch, tmp_path):
    marker = tmp_path / "started.txt"
    script = tmp_path / "start.sh"
    script.write_text(f"#!/bin/sh\nprintf started > {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    runtime = WeatherManagedRuntimeSpec(
        instance_id="collector",
        tmux_session="collector",
        role="collector",
        execution_mode="collector",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    monkeypatch.setattr(
        ctl,
        "_tmux",
        lambda _spec, *args: subprocess.CompletedProcess(
            args, 1, "can't find session: collector", ""
        ),
    )

    result = ctl._run_restart(spec, runtime, confirm_live=False)

    assert result["status"] == "restarted"
    assert result["restart_mode"] == "controller_registered_start_missing_session"
    assert marker.read_text(encoding="utf-8") == "started"


def test_controller_does_not_synthesize_live_restart_contract(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live",
        role="strategy",
        execution_mode="live",
        checkout_root=tmp_path,
        start_script=Path("start.sh"),
        expected_live=True,
        recovery_policy="guarded_live",
    )

    assert ctl._run_restart(
        production_spec(tmp_path, (runtime,)), runtime, confirm_live=True
    ) == {
        "instance_id": "live",
        "status": "blocked",
        "reason": "explicit_restart_contract_required",
    }


def test_controller_stops_exact_safe_non_live_runtime(tmp_path, monkeypatch):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="shadow",
        tmux_session="shadow_session",
        role="shadow",
        execution_mode="zero_notional_shadow",
        recovery_policy="safe",
    )
    spec = production_spec(tmp_path, (runtime,))
    calls = []

    def fake_tmux(_spec, *args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ctl, "_tmux", fake_tmux)

    assert ctl._run_stop(spec, runtime)["status"] == "stopped"
    assert calls == [("kill-session", "-t", "=shadow_session")]


def test_controller_refuses_stop_for_live_runtime(tmp_path):
    runtime = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live_session",
        role="strategy",
        execution_mode="live",
        expected_live=True,
        recovery_policy="guarded_live",
    )

    assert ctl._run_stop(production_spec(tmp_path, (runtime,)), runtime) == {
        "instance_id": "live",
        "status": "blocked",
        "reason": "confirm_live_required",
    }


def test_controller_stops_exact_confirmed_live_runtime(tmp_path, monkeypatch):
    restart = tmp_path / "restart.sh"
    restart.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime = WeatherManagedRuntimeSpec(
        instance_id="live",
        tmux_session="live_session",
        role="strategy",
        execution_mode="live",
        checkout_root=tmp_path,
        restart_script=Path("restart.sh"),
        expected_live=True,
        recovery_policy="guarded_live",
    )
    spec = production_spec(tmp_path, (runtime,))
    calls = []

    def fake_tmux(_spec, *args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ctl, "_tmux", fake_tmux)

    assert ctl._run_stop(spec, runtime, confirm_live=True)["status"] == "stopped"
    assert calls == [("kill-session", "-t", "=live_session")]


def test_data_feed_semantics_separates_coverage_warning_from_critical_chain():
    payload = {
        "checked_at_utc": "2026-08-02T16:00:00Z",
        "observation_cache": {"status": "ok"},
        "forecast_hourly_curves": {"status": "ok"},
        "live_cross_observation_state": {"status": "ok"},
        "snapshot_parity": {"status": "ok"},
        "snapshot_orderbook_coverage": {"status": "ok"},
        "snapshot_source_model": {"status": "ok", "fallback_detected": False},
        "orderbook_snapshots": {"missing": False, "stale": False},
        "snapshot_city_state_coverage": {
            "status": "missing_same_day_weather_state",
            "missing_required_trading_cities": ["Chengdu", "Guangzhou"],
        },
        "fast_observation_state": {"status": "stale"},
    }

    result = ctl.summarize_data_feed_semantics(payload)

    assert result["status"] == "warning"
    assert result["critical_reasons"] == []
    assert result["warnings"] == [
        "snapshot_city_state_coverage:Chengdu,Guangzhou"
    ]
    assert result["ignored_legacy_checks"] == ["fast_observation_state"]


def test_data_feed_semantics_makes_forecast_fallback_critical():
    payload = {
        "observation_cache": {"status": "ok"},
        "forecast_hourly_curves": {"status": "ok"},
        "live_cross_observation_state": {"status": "ok"},
        "snapshot_parity": {"status": "ok"},
        "snapshot_orderbook_coverage": {"status": "ok"},
        "snapshot_source_model": {"status": "ok", "fallback_detected": True},
        "orderbook_snapshots": {"missing": False, "stale": False},
        "snapshot_city_state_coverage": {"status": "ok"},
    }

    result = ctl.summarize_data_feed_semantics(payload)

    assert result["status"] == "critical"
    assert result["critical_reasons"] == ["forecast_source_fallback_detected"]


def test_data_feed_semantics_treats_inactive_observation_staleness_as_warning():
    payload = {
        "observation_cache": {
            "status": "warn",
            "inactive_invalid_record_count": 1,
        },
        "forecast_hourly_curves": {"status": "ok"},
        "live_cross_observation_state": {"status": "ok"},
        "snapshot_parity": {"status": "ok"},
        "snapshot_orderbook_coverage": {"status": "ok"},
        "snapshot_source_model": {"status": "ok", "fallback_detected": False},
        "orderbook_snapshots": {"missing": False, "stale": False},
        "snapshot_city_state_coverage": {"status": "ok"},
    }

    result = ctl.summarize_data_feed_semantics(payload)

    assert result["status"] == "warning"
    assert result["critical_reasons"] == []
    assert result["warnings"] == ["observation_cache_inactive_stale:1"]


def test_data_feed_semantics_treats_partial_fresh_coverage_as_warning():
    payload = {
        "observation_cache": {"status": "ok"},
        "forecast_hourly_curves": {
            "status": "incomplete_city_target_coverage",
            "missing_city_target_count": 1,
            "missing_city_target_examples": [
                {"city": "NYC", "target_date": "2026-08-02"}
            ],
        },
        "live_cross_observation_state": {"status": "ok"},
        "snapshot_parity": {"status": "ok"},
        "snapshot_orderbook_coverage": {
            "status": "incomplete",
            "target_count": 170,
            "target_ok_count": 166,
            "target_incomplete_count": 4,
        },
        "snapshot_source_model": {"status": "ok", "fallback_detected": False},
        "orderbook_snapshots": {"missing": False, "stale": False},
        "snapshot_city_state_coverage": {"status": "ok"},
    }

    result = ctl.summarize_data_feed_semantics(payload)

    assert result["status"] == "warning"
    assert result["critical_reasons"] == []
    assert result["warnings"] == [
        "forecast_hourly_curves_incomplete:NYC@2026-08-02",
        "snapshot_orderbook_coverage_incomplete:4/170",
    ]


def test_data_feed_semantic_health_is_bounded(monkeypatch):
    observed_timeout = None

    def fake_run(*_args, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd="health", timeout=observed_timeout)

    monkeypatch.setattr(ctl.subprocess, "run", fake_run)

    result = ctl.collect_data_feed_semantics()

    assert observed_timeout == ctl.DATA_FEED_SEMANTIC_TIMEOUT_SEC
    assert result["status"] == "critical"
    assert result["critical_reasons"] == [
        f"data_feed_health_command_failed:TimeoutExpired:{observed_timeout}s"
    ]


def test_read_json_falls_back_to_canonical_context(tmp_path, monkeypatch):
    spec = production_spec(tmp_path, ())
    path = tmp_path / "health.json"
    original_read_text = Path.read_text

    def denied_read_text(self, *args, **kwargs):
        if self == path:
            raise PermissionError("Operation not permitted")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied_read_text)
    monkeypatch.setattr(
        ctl,
        "_run_tmux_checked",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["tmux"], 0, '{"status":"ok"}'
        ),
    )

    payload, error = ctl._read_json(path, canonical_spec=spec)

    assert error is None
    assert payload == {"status": "ok"}


def test_data_feed_semantics_uses_canonical_context(tmp_path, monkeypatch):
    spec = production_spec(tmp_path, ())
    payload = {
        "checked_at_utc": "2026-08-06T00:00:00Z",
        "observation_cache": {"status": "ok"},
        "forecast_hourly_curves": {"status": "ok"},
        "live_cross_observation_state": {"status": "ok"},
        "snapshot_parity": {"status": "ok"},
        "snapshot_orderbook_coverage": {"status": "ok"},
        "snapshot_source_model": {"status": "ok", "fallback_detected": False},
        "orderbook_snapshots": {"missing": False, "stale": False},
        "snapshot_city_state_coverage": {"status": "ok"},
    }
    monkeypatch.setattr(
        ctl,
        "_run_tmux_checked",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["tmux"], 0, json.dumps(payload)
        ),
    )

    result = ctl.collect_data_feed_semantics(spec)

    assert result["status"] == "healthy"


def test_jrs_context_health_timeout_returns_critical(tmp_path, monkeypatch):
    def fake_run(*_args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=kwargs["timeout"])

    monkeypatch.setattr(ctl.subprocess, "run", fake_run)
    spec = production_spec(tmp_path, ())

    result = ctl.collect_jrs_context_health(spec)

    assert result["status"] == "critical"
    assert result["returncode"] == 124
    assert result["output"] == "jrs_context_probe_failed:TimeoutExpired"
