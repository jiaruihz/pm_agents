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
