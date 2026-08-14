from pathlib import Path

import pytest

from scripts.ops import weather_market_proxy_ctl as ctl
from scripts.ops import weather_market_proxy as shared
from src.strategies.runtime.production import load_production_spec


def test_proxy_url_validation():
    assert ctl.validate_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"
    with pytest.raises(ValueError):
        ctl.validate_proxy_url("http://127.0.0.1")


def test_shared_proxy_resolves_named_default_route_not_legacy_endpoint_state(tmp_path, monkeypatch):
    state = tmp_path / "market_proxy.json"
    state.write_text('{"proxy_url":"http://127.0.0.1:17897"}\n', encoding="utf-8")
    spec = __import__("dataclasses").replace(
        load_production_spec(),
        market_proxy_state_path=state,
        market_proxy_default_url="http://127.0.0.1:27897",
    )
    monkeypatch.setattr(shared, "load_production_spec", lambda: spec)

    assert shared.market_proxy_url(None, env={"WEATHER_PREDICT_MARKET_PROXY": "http://127.0.0.1:9999"}) == "http://127.0.0.1:7896"
    assert shared.market_proxy_url(None, env={"WEATHER_DATA_FEED_MARKET_PROXY": "http://127.0.0.1:8888"}) == "http://127.0.0.1:8888"
    assert shared.market_proxy_url(
        None,
        env={"WEATHER_DATA_FEED_MARKET_PROXY": "http://127.0.0.1:8888"},
        default="http://127.0.0.1:7777",
        route_key="stable",
    ) == "http://127.0.0.1:7896"
    assert shared.market_proxy_url(
        "http://127.0.0.1:6666",
        route_key="stable",
    ) == "http://127.0.0.1:6666"


def test_control_state_surfaces_legacy_endpoint_drift_without_using_it(tmp_path, monkeypatch):
    state = tmp_path / "market_proxy.json"
    state.write_text('{"proxy_url":"http://127.0.0.1:7897"}\n', encoding="utf-8")
    spec = __import__("dataclasses").replace(
        load_production_spec(), market_proxy_state_path=state
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)

    result = ctl.read_state()

    assert result["proxy_url"] == "http://127.0.0.1:7896"
    assert result["legacy_proxy_url"] == "http://127.0.0.1:7897"
    assert result["legacy_state_drift"] is True


def test_proxy_probe_requires_gamma_and_clob(monkeypatch):
    statuses = iter(("200", "503", '{"blocked": false, "country": "HK"}'))

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self):
            self.stdout = next(statuses)

    monkeypatch.setattr(ctl.subprocess, "run", lambda *args, **kwargs: Result())

    result = ctl.probe("http://127.0.0.1:17897")

    assert result["ok"] is False
    assert [row["name"] for row in result["checks"]] == ["gamma", "clob", "geoblock"]
    assert result["checks"][1]["http_status"] == "503"


def test_proxy_probe_rejects_restricted_trading_region(monkeypatch):
    statuses = iter(("200", "200", '{"blocked": true, "country": "SG"}'))

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self):
            self.stdout = next(statuses)

    monkeypatch.setattr(ctl.subprocess, "run", lambda *args, **kwargs: Result())

    result = ctl.probe("http://127.0.0.1:17897")

    assert result["ok"] is False
    geoblock = result["checks"][-1]
    assert geoblock["blocked"] is True
    assert geoblock["trading_allowed"] is False
    assert geoblock["country"] == "SG"


def test_proxy_consumers_come_only_from_production_manifest():
    expected = {
        item.instance_id
        for item in load_production_spec().managed_runtimes
        if item.uses_market_proxy
    }
    assert expected
    assert {item.instance_id for item in ctl.consumers()} == expected
    assert ctl.consumers()[0].instance_id == "weather_market_books"


def test_route_status_reports_all_named_groups(monkeypatch):
    monkeypatch.setattr(
        ctl,
        "controller_json",
        lambda path: {
            "proxies": {
                "Allblue 加速器": {
                    "type": "Selector",
                    "now": "node-a",
                    "all": ["node-a", "node-b"],
                },
                "PM-STABLE": {
                    "type": "Fallback",
                    "now": "TAG-LOCAL",
                    "all": ["TAG-LOCAL", "Allblue 加速器"],
                },
            }
        },
    )
    monkeypatch.setattr(
        ctl,
        "probe",
        lambda proxy_url, timeout=8.0: {"ok": True, "checks": []},
    )

    result = ctl.route_status()

    assert result["reachable"] is True
    assert result["healthy"] is True
    assert {row["route_key"] for row in result["routes"]} == {"default", "allblue", "stable"}
    stable = next(row for row in result["routes"] if row["route_key"] == "stable")
    assert stable["current_node"] == "TAG-LOCAL"


def test_gateway_overlay_targets_active_allblue_enhancements(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (tmp_path / "profiles.yaml").write_text(
        "current: active\n"
        "items:\n"
        "  - uid: active\n"
        "    type: remote\n"
        "    name: Allblue 加速器\n"
        "    option:\n"
        "      merge: merge-id\n"
        "      proxies: proxy-id\n"
        "      groups: group-id\n",
        encoding="utf-8",
    )
    (profiles / "merge-id.yaml").write_text("profile:\n  store-selected: true\n", encoding="utf-8")
    for name in ("proxy-id", "group-id"):
        (profiles / f"{name}.yaml").write_text(
            "prepend: []\nappend: []\ndelete: []\n", encoding="utf-8"
        )
    spec = __import__("dataclasses").replace(
        load_production_spec(), market_proxy_gateway_config_root=tmp_path
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)

    plan, outputs = ctl.gateway_overlay_files()

    assert plan["active_profile_name"] == "Allblue 加速器"
    assert plan["stable_proxy_url"] == "http://127.0.0.1:7896"
    by_name = {path.name: value.decode("utf-8") for path, value in outputs}
    assert "pm-stable-in" in by_name["merge-id.yaml"]
    assert "TAG-LOCAL" in by_name["proxy-id.yaml"]
    assert "PM-STABLE" in by_name["group-id.yaml"]
    assert "Allblue 加速器" in by_name["group-id.yaml"]


def test_gateway_overlay_apply_requires_network_confirmation(monkeypatch):
    monkeypatch.setattr(
        ctl.sys,
        "argv",
        ["weather_market_proxy_ctl.py", "gateway-overlay", "--apply"],
    )

    with pytest.raises(SystemExit, match="--confirm-network-change is required"):
        ctl.main()


def test_default_route_maintenance_switches_to_verified_candidate(tmp_path, monkeypatch):
    spec = __import__("dataclasses").replace(
        load_production_spec(), data_feed_runtime_root=tmp_path
    )
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)
    payload = {
        "proxies": {
            "Allblue 加速器": {
                "type": "Selector",
                "now": "node-a",
                "all": ["node-a", "node-b", "node-c", "DIRECT"],
            },
            "node-b": {"history": [{"delay": 500}]},
            "node-c": {"history": [{"delay": 200}]},
        }
    }
    monkeypatch.setattr(ctl, "controller_json", lambda path, **kwargs: payload)
    selected = {"node": "node-a"}
    switches = []

    def switch(group, node):
        assert group == "Allblue 加速器"
        selected["node"] = node
        switches.append(node)

    monkeypatch.setattr(ctl, "switch_group_node", switch)
    monkeypatch.setattr(ctl.time, "sleep", lambda seconds: None)
    calls = {"count": 0}

    def fake_probe(proxy_url, timeout=8.0):
        calls["count"] += 1
        return {
            "ok": calls["count"] > 3 and selected["node"] == "node-c",
            "checks": [],
        }

    monkeypatch.setattr(ctl, "probe", fake_probe)

    result = ctl.maintain_default_route(
        apply=True,
        reason="test",
        trigger="unit",
    )

    assert result["status"] == "switched"
    assert result["selected_node"] == "node-c"
    assert switches == ["node-c"]
    audit = tmp_path / "output/market_proxy_control/route_switches.jsonl"
    assert audit.exists()
    assert '"selected_node": "node-c"' in audit.read_text(encoding="utf-8")


def test_route_maintenance_apply_requires_live_confirmation(monkeypatch):
    monkeypatch.setattr(
        ctl.sys,
        "argv",
        [
            "weather_market_proxy_ctl.py",
            "maintain",
            "--apply",
            "--reason",
            "test",
        ],
    )
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
            if path.name == "tag_proxy_route_ctl.py":
                # Host-level TAG node selection intentionally probes TAG's
                # canonical local ingress; it is not a weather consumer.
                continue
            assert "127.0.0.1:7890" not in path.read_text(encoding="utf-8"), path

    fast_source = (
        root / "scripts/ops/start_weather_fast_source_prev_no_trial.sh"
    ).read_text(encoding="utf-8")
    assert fast_source.index("source .env") < fast_source.index(
        "weather_export_market_proxy_env %q"
    )


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


def test_reliability_supervisor_owns_proxy_health_maintenance():
    root = Path(__file__).resolve().parents[2]
    text = (root / "scripts/ops/production_reliability_supervisor.py").read_text(
        encoding="utf-8"
    )
    installer = (
        root / "scripts/ops/install_production_reliability_launchagent.sh"
    ).read_text(encoding="utf-8")
    assert '"maintain"' in text
    assert '"--apply"' in text
    assert '"--confirm-live"' in text
    assert '"production_reliability_supervisor"' in text
    assert "--maintain-weather-route" in installer
