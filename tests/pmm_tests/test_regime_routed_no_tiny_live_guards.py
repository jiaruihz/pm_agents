from __future__ import annotations

import importlib
import sys

import pandas as pd

from scripts.ops import regime_routed_no_tiny_live as live


def test_display_path_accepts_external_runtime_root(tmp_path):
    path = tmp_path / "trade_plans.jsonl"
    assert live.display_path(path) == str(path)


def test_live_runner_import_closure_excludes_training_labels():
    sys.modules.pop("weather_feature_layer.labels", None)
    importlib.reload(live)
    assert "weather_feature_layer.labels" not in sys.modules


def test_current_local_day_filter_drops_tomorrow_markets():
    records = pd.DataFrame(
        [
            {
                "city": "CapeTown",
                "target_date": "2026-06-27",
                "market_local_date": "2026-06-27",
                "city_local_date_at_snapshot": "2026-06-27",
                "event_slug": "highest-temperature-in-cape-town-on-june-27-2026",
            },
            {
                "city": "CapeTown",
                "target_date": "2026-06-28",
                "market_local_date": "2026-06-28",
                "city_local_date_at_snapshot": "2026-06-27",
                "event_slug": "highest-temperature-in-cape-town-on-june-28-2026",
            },
        ]
    )

    kept, meta = live.filter_current_local_day_records(records)

    assert kept["target_date"].tolist() == ["2026-06-27"]
    assert meta["dropped_rows"] == 1
    assert meta["dropped_target_dates"] == ["2026-06-28"]


def test_market_record_date_ok_rejects_mismatched_slug():
    assert live.market_record_date_ok(
        {
            "target_date": "2026-06-27",
            "event_slug": "highest-temperature-in-cape-town-on-june-27-2026",
            "question": "Will the highest temperature in Cape Town be 15°C on June 27?",
        }
    )
    assert not live.market_record_date_ok(
        {
            "target_date": "2026-06-27",
            "event_slug": "highest-temperature-in-cape-town-on-june-28-2026",
            "question": "Will the highest temperature in Cape Town be 15°C on June 28?",
        }
    )


def test_current_no_runway_state_requires_fresh_running_high():
    assert live.current_no_runway_state_ok(
        {
            "route_leg": "runway_current_no",
            "expression": "current_bracket_no",
            "running_max_state": "fresh_running_high",
            "intraday_state": "active_warming",
        }
    )
    assert not live.current_no_runway_state_ok(
        {
            "route_leg": "runway_current_no",
            "expression": "current_bracket_no",
            "running_max_state": "mature_fade",
            "intraday_state": "false_fade_risk",
        }
    )
    assert live.current_no_runway_state_ok(
        {
            "route_leg": "capped_d2_no",
            "expression": "d2_no",
            "running_max_state": "mature_fade",
            "intraday_state": "false_fade_risk",
        }
    )


def test_tail_diagnostic_route_legs_are_shadow_only():
    assert live.is_shadow_only_route_leg("false_fade_reheat_current_no")
    assert live.is_shadow_only_route_leg("cheap_stale_tail_current_no")
    assert not live.is_shadow_only_route_leg("fresh_runway_current_no")
    assert not live.is_shadow_only_route_leg("capped_d2_no")


def test_live_order_size_clamps_to_top_ask_without_rounding_up():
    assert live.clamp_order_shares_to_top_ask(14.68, 13.0) == 13.0
    assert live.clamp_order_shares_to_top_ask(4.9, 100.0) == 4.9


def test_build_plan_uses_clamped_live_order_size_and_preserves_soft_target():
    row = pd.Series(
        {
            "ask": 0.17,
            "bid": 0.16,
            "soft_notional_usd": 2.4956,
            "soft_shares": 14.68,
            "live_order_shares": 13.0,
            "live_order_notional_usd": 2.21,
            "live_order_clamped_by_top_ask": True,
            "row_risk_soft_v1": 0.49912,
            "base_notional_usd": 5.0,
            "soft_balanced": 1.0,
            "legacy_soft_balanced_notional_usd": 5.0,
            "route_price_cap": 0.55,
            "route_price_ok": True,
            "market_date_match_ok": True,
            "city": "Tokyo",
            "target_date": "2026-06-30",
            "decision_snapshot_ts_utc": "2026-06-30T05:00:00+00:00",
            "expression": "current_bracket_no",
            "route_leg": "fresh_runway_current_no",
            "bracket": "27",
            "token_id": "tok-no",
            "market_id": "mkt",
            "event_slug": "highest-temperature-in-tokyo-on-june-30-2026",
            "question": "Will the highest temperature in Tokyo be 27°C on June 30?",
            "market_event_date": "2026-06-30",
        }
    )

    plan = live.build_plan(row, live_enabled=False, ttl_min=30.0)

    assert plan["size"] == 13.0
    assert plan["notional"] == 2.21
    assert plan["order_notional_cap"] == 2.21
    assert plan["soft_shares"] == 14.68
    assert plan["soft_notional_usd"] == 2.4956
    assert plan["live_order_clamped_by_top_ask"] is True
