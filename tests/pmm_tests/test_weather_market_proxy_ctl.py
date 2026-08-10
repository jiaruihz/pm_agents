from pathlib import Path

import pytest

from scripts.ops import weather_market_proxy_ctl as ctl
from scripts.ops import weather_market_proxy as shared
from src.strategies.runtime.production import (
    WeatherMarketProxyFailoverSpec,
    load_production_spec,
)


def test_proxy_url_validation():
    assert ctl.validate_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"
    with pytest.raises(ValueError):
        ctl.validate_proxy_url("http://127.0.0.1")


def test_shared_proxy_resolves_controller_state_not_legacy_aliases(tmp_path, monkeypatch):
    state = tmp_path / "market_proxy.json"
    state.write_text('{"proxy_url":"http://127.0.0.1:17897"}\n', encoding="utf-8")
    spec = __import__("dataclasses").replace(
        load_production_spec(),
        market_proxy_state_path=state,
        market_proxy_default_url="http://127.0.0.1:27897",
    )
    monkeypatch.setattr(shared, "load_production_spec", lambda: spec)

    assert shared.market_proxy_url(None, env={"WEATHER_PREDICT_MARKET_PROXY": "http://127.0.0.1:9999"}) == "http://127.0.0.1:17897"
    assert shared.market_proxy_url(None, env={"WEATHER_DATA_FEED_MARKET_PROXY": "http://127.0.0.1:8888"}) == "http://127.0.0.1:8888"


def test_proxy_probe_requires_gamma_and_clob(monkeypatch):
    statuses = iter(("200", "503"))

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self):
            self.stdout = next(statuses)

    monkeypatch.setattr(ctl.subprocess, "run", lambda *args, **kwargs: Result())

    result = ctl.probe("http://127.0.0.1:17897")

    assert result["ok"] is False
    assert [row["name"] for row in result["checks"]] == ["gamma", "clob"]
    assert result["checks"][1]["http_status"] == "503"


def test_proxy_consumers_come_only_from_production_manifest():
    expected = {
        item.instance_id
        for item in load_production_spec().managed_runtimes
        if item.uses_market_proxy
    }
    assert expected
    assert {item.instance_id for item in ctl.consumers()} == expected
    assert ctl.consumers()[0].instance_id == "weather_market_books"


def failover_spec(tmp_path: Path) -> WeatherMarketProxyFailoverSpec:
    return WeatherMarketProxyFailoverSpec(
        enabled=True,
        controller_url="http://127.0.0.1:19097",
        group="market-group",
        controller_secret_env="TEST_PROXY_SECRET",
        controller_secret_keychain_service="test.weather.proxy",
        controller_secret_keychain_account="tester",
        state_path=tmp_path / "config/node.json",
        audit_path=tmp_path / "output/node.jsonl",
        lock_path=tmp_path / "config/node.lock",
        probe_timeout_sec=0.1,
        failure_confirmations=2,
        settle_sec=0.0,
    )


def test_node_control_status_reports_current_group(monkeypatch):
    monkeypatch.setattr(
        ctl,
        "controller_json",
        lambda path, **kwargs: {
            "proxies": {
                "🙂 TAGSS": {
                    "type": "Selector",
                    "now": "node-a",
                    "all": ["node-a", "node-b"],
                }
            }
        },
    )

    result = ctl.node_control_status()

    assert result["reachable"] is True
    assert result["current_node"] == "node-a"
    assert result["candidate_count"] == 2


def test_recover_node_switches_to_first_fully_healthy_candidate(tmp_path, monkeypatch):
    spec = __import__("dataclasses").replace(
        load_production_spec(),
        data_feed_runtime_root=tmp_path,
        market_proxy_state_path=tmp_path / "config/market_proxy.json",
        market_proxy_failover=failover_spec(tmp_path),
    )
    spec.market_proxy_state_path.parent.mkdir(parents=True)
    spec.market_proxy_state_path.write_text(
        '{"proxy_url":"http://127.0.0.1:17897"}\n', encoding="utf-8"
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)
    failures = iter(((True, [{"ok": False}]), (True, [{"ok": False}])))
    monkeypatch.setattr(ctl, "confirmed_proxy_failure", lambda proxy: next(failures))
    monkeypatch.setattr(
        ctl,
        "node_control_status",
        lambda: {
            "configured": True,
            "enabled": True,
            "reachable": True,
            "current_node": "node-a",
            "candidates": ["node-a", "node-b", "node-c"],
        },
    )
    selected = {"node": "node-a"}
    switches = []

    def switch(node):
        selected["node"] = node
        switches.append(node)

    monkeypatch.setattr(ctl, "switch_node", switch)
    monkeypatch.setattr(
        ctl,
        "probe",
        lambda proxy, timeout=8.0: {"ok": selected["node"] == "node-b", "checks": []},
    )

    result = ctl.recover_node(apply=True, reason="test", trigger="unit")

    assert result["status"] == "switched"
    assert result["before_node"] == "node-a"
    assert result["selected_node"] == "node-b"
    assert switches == ["node-b"]
    state = __import__("json").loads(spec.market_proxy_failover.state_path.read_text())
    assert state["selected_node"] == "node-b"
    assert spec.market_proxy_failover.audit_path.read_text().count("\n") == 1


def test_recover_node_restores_original_when_all_candidates_fail(tmp_path, monkeypatch):
    spec = __import__("dataclasses").replace(
        load_production_spec(),
        data_feed_runtime_root=tmp_path,
        market_proxy_state_path=tmp_path / "config/market_proxy.json",
        market_proxy_failover=failover_spec(tmp_path),
    )
    spec.market_proxy_state_path.parent.mkdir(parents=True)
    spec.market_proxy_state_path.write_text(
        '{"proxy_url":"http://127.0.0.1:17897"}\n', encoding="utf-8"
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)
    monkeypatch.setattr(
        ctl,
        "confirmed_proxy_failure",
        lambda proxy: (True, [{"ok": False}]),
    )
    monkeypatch.setattr(
        ctl,
        "node_control_status",
        lambda: {
            "configured": True,
            "enabled": True,
            "reachable": True,
            "current_node": "node-a",
            "candidates": ["node-a", "node-b", "node-c"],
        },
    )
    switches = []
    monkeypatch.setattr(ctl, "switch_node", lambda node: switches.append(node))
    monkeypatch.setattr(
        ctl,
        "probe",
        lambda proxy, timeout=8.0: {"ok": False, "checks": []},
    )

    with pytest.raises(RuntimeError, match="no healthy market proxy node"):
        ctl.recover_node(apply=True, reason="test", trigger="unit")

    assert switches == ["node-b", "node-c", "node-a"]
    state = __import__("json").loads(spec.market_proxy_failover.state_path.read_text())
    assert state["status"] == "failed"
    assert state["restored_original"] is True


def test_maintain_node_requires_live_confirmation(monkeypatch):
    monkeypatch.setattr(ctl.sys, "argv", [
        "weather_market_proxy_ctl.py", "maintain-node", "--apply", "--reason", "test"
    ])
    monkeypatch.setattr(
        ctl,
        "consumers",
        lambda: [type("Consumer", (), {"expected_live": True})()],
    )
    with pytest.raises(SystemExit, match="--confirm-live is required"):
        ctl.main()


def test_active_entrypoints_do_not_hardcode_old_proxy_port():
    root = Path(__file__).resolve().parents[2]
    names = (
        "_weather_market_books_loop_body.sh",
        "start_weather_current_yes_core_carry_tiny_live_v2.sh",
        "start_weather_fast_source_prev_no_trial.sh",
        "start_low_price_yes_integrated_tail_shadow_v2.sh",
        "start_weather_fast_source_stale_book_production.sh",
        "start_weather_helsinki_pre_cross_active_ladder_shadow.sh",
        "start_weather_tokyo_current_break_active_ladder_shadow_v1.sh",
    )
    for name in names:
        text = (root / "scripts/ops" / name).read_text(encoding="utf-8")
        assert "127.0.0.1:7890" not in text, name
        assert "weather_resolve_market_proxy" in text, name

    for path in (root / "scripts").glob("**/*"):
        if path.is_file() and path.suffix in {".py", ".sh"}:
            assert "127.0.0.1:7890" not in path.read_text(encoding="utf-8"), path


def test_chain_health_uses_artifact_freshness_only_for_live_and_primary_books(monkeypatch):
    payload = {
        "manifest_status": "healthy",
        "runtimes": [
            {"instance_id": "weather_market_books", "present": True, "status": "healthy", "health_age_sec": 1, "issues": []},
            {"instance_id": "current_yes_core_carry_tiny_live_v2", "present": True, "status": "healthy", "health_age_sec": 1, "issues": []},
            {"instance_id": "fast_source_prev_no_trial_v1", "present": True, "status": "healthy", "health_age_sec": 1, "issues": []},
            {"instance_id": "low_price_yes_lottery_shadow_v1", "present": True, "status": "critical", "health_age_sec": 9999, "issues": ["health_artifact_stale"]},
        ],
    }
    class Result:
        stdout = __import__("json").dumps(payload)
    monkeypatch.setattr(ctl.subprocess, "run", lambda *a, **k: Result())
    result = ctl.chain_health()
    assert result["blocking_consumers"] == {}
    assert result["consumer_health"]["low_price_yes_lottery_shadow_v1"]["verification_mode"] == "process_and_proxy_binding"
    assert result["full_runtime_health"]["weather_market_books"]["health_trigger"] == "process_presence"
    assert result["full_runtime_count"] == 4


def test_publish_health_is_atomic_and_machine_readable(tmp_path, monkeypatch):
    spec = __import__("dataclasses").replace(load_production_spec(), data_feed_runtime_root=tmp_path)
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)
    path = ctl.publish_health({"probe": {"ok": True}})
    payload = __import__("json").loads(path.read_text())
    assert payload["schema_version"] == "weather_market_proxy_health_v1"
    assert payload["probe"]["ok"] is True


def test_runtime_monitor_refreshes_proxy_health_matrix():
    root = Path(__file__).resolve().parents[2]
    text = (root / "scripts/ops/start_weather_runtime_monitor.sh").read_text(encoding="utf-8")
    assert 'weather_market_proxy_ctl.py" maintain-node' in text
    assert "--apply --confirm-live" in text
    assert "weather_runtime_monitor" in text
    assert "market_proxy_health_failed_utc" in text
    assert 'CONTROL_ROOT="$PROJECT_DIR"' in text
    assert 'WEATHER_RUNTIME_MONITOR_INTERVAL_SECONDS:-60' in text
