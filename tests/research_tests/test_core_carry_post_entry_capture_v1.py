from __future__ import annotations

from scripts.analysis.reheat_risk.research_core_carry_post_entry_capture_v1 import (
    official_weather_fee_per_share,
    walk_sell_ladder,
)
from scripts.ops.weather_current_yes_core_carry_post_entry_capture_shadow_v1 import (
    entry_is_active,
    evaluate_event,
)
from datetime import datetime, timezone


def test_walk_sell_ladder_uses_depth_and_exit_fee() -> None:
    result = walk_sell_ladder(
        [{"price": 0.90, "size": 4}, {"price": 0.89, "size": 8}],
        10,
    )
    expected_principal = 4 * 0.90 + 6 * 0.89
    expected_fee = 4 * official_weather_fee_per_share(0.90) + 6 * official_weather_fee_per_share(0.89)
    assert result["executable"] is True
    assert result["principal"] == expected_principal
    assert result["fee"] == expected_fee
    assert result["net_proceeds_per_share"] == (expected_principal - expected_fee) / 10


def test_walk_sell_ladder_fails_closed_on_insufficient_depth() -> None:
    result = walk_sell_ladder([{"price": 0.90, "size": 9.9}], 10)
    assert result["executable"] is False
    assert result["net_proceeds_per_share"] is None


def test_shadow_exit_requires_fee_adjusted_gain_floor() -> None:
    entry = {
        "city": "Lucknow",
        "target_date": "2026-07-31",
        "current_bracket": "32",
        "current_condition_id": "condition",
        "current_yes_token_id": "token",
        "created_at_utc": "2026-07-31T09:06:00Z",
        "source_report_ts_utc": "2026-07-31T08:30:00Z",
        "taker_ladder": {"effective_cost_per_share": 0.8564},
    }
    event = {
        "source_report_ts_utc": "2026-07-31T10:00:00Z",
        "as_of_ts_utc": "2026-07-31T10:31:00Z",
        "raw_metar": "METAR TEST",
    }
    book = {
        "status": "ok",
        "fetched_at_utc": "2026-07-31T10:31:01Z",
        "bid": 0.90,
        "ask": 0.93,
        "bids": [{"price": 0.90, "size": 10}],
    }
    row = evaluate_event(entry, event, "event", book, quantity=10, gain_floor=0.03)
    assert row["net_gain_per_share"] > 0.03
    assert row["would_exit"] is True
    assert row["zero_notional"] is True
    assert row["no_order_placed"] is True
    assert row["execution_calls"] == 0


def test_shadow_does_not_exit_on_top_bid_without_full_depth() -> None:
    entry = {
        "city": "X",
        "target_date": "2026-08-01",
        "current_bracket": "30",
        "current_condition_id": "condition",
        "current_yes_token_id": "token",
        "taker_ladder": {"effective_cost_per_share": 0.85},
    }
    event = {"source_report_ts_utc": "2026-08-01T10:00:00Z"}
    book = {
        "status": "ok",
        "bid": 0.95,
        "ask": 0.96,
        "bids": [{"price": 0.95, "size": 5}],
    }
    row = evaluate_event(entry, event, "event", book, quantity=10, gain_floor=0.03)
    assert row["would_exit"] is False


def test_active_entry_uses_city_local_target_date() -> None:
    now = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    assert entry_is_active(
        {"target_date": "2026-08-07", "timezone": "Asia/Manila"}, now
    )
    assert not entry_is_active(
        {"target_date": "2026-08-07", "timezone": "Pacific/Auckland"}, now
    )
