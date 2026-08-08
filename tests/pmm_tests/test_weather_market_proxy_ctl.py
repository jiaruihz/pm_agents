from pathlib import Path

import pytest

from scripts.ops import weather_market_proxy_ctl as ctl
from src.strategies.runtime.production import load_production_spec


def test_proxy_url_validation():
    assert ctl.validate_proxy_url("http://127.0.0.1:7897") == "http://127.0.0.1:7897"
    with pytest.raises(ValueError):
        ctl.validate_proxy_url("http://127.0.0.1")


def test_proxy_consumers_come_only_from_production_manifest():
    expected = {
        item.instance_id
        for item in load_production_spec().managed_runtimes
        if item.uses_market_proxy
    }
    assert expected
    assert {item.instance_id for item in ctl.consumers()} == expected
    assert ctl.consumers()[0].instance_id == "weather_market_books"


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


def test_publish_health_is_atomic_and_machine_readable(tmp_path, monkeypatch):
    spec = __import__("dataclasses").replace(load_production_spec(), data_feed_runtime_root=tmp_path)
    monkeypatch.setattr(ctl, "load_production_spec", lambda: spec)
    path = ctl.publish_health({"probe": {"ok": True}})
    payload = __import__("json").loads(path.read_text())
    assert payload["schema_version"] == "weather_market_proxy_health_v1"
    assert payload["probe"]["ok"] is True
