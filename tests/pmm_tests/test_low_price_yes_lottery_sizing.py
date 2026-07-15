from argparse import Namespace

import pytest

from scripts.ops.low_price_yes_lottery_tiny_live import (
    annotate_runtime_decision,
    choose_lifecycle_action,
    enforce_live_safety_args,
    load_fresh_snapshot_candidates,
    refresh_lifecycle_thesis,
    shares_for_price_tier_6_8_10,
    shares_for_sizing_policy,
    sizing_shadow,
)


def test_shadow_would_live_decision_records_fresh_best_ask():
    row = annotate_runtime_decision(
        {
            "decision_status": "planned",
            "fresh_best_ask": 0.11,
            "fresh_best_ask_size": 17.0,
            "maker_limit_price": 0.101,
            "planned_shares": 5.0,
            "planned_notional_usd": 0.505,
        },
        live_enabled=False,
    )

    assert row["runtime_execution_mode"] == "shadow"
    assert row["would_live_entry"] is True
    assert row["would_live_best_ask"] == 0.11
    assert row["would_live_best_ask_size"] == 17.0
    assert row["would_live_maker_limit_price"] == 0.101


def test_price_tier_6_8_10_boundaries():
    assert shares_for_price_tier_6_8_10(price=0.05, min_shares=5.0) == 6.0
    assert shares_for_price_tier_6_8_10(price=0.08, min_shares=5.0) == 6.0
    assert shares_for_price_tier_6_8_10(price=0.0801, min_shares=5.0) == 8.0
    assert shares_for_price_tier_6_8_10(price=0.14, min_shares=5.0) == 8.0
    assert shares_for_price_tier_6_8_10(price=0.1401, min_shares=5.0) == 10.0
    assert shares_for_price_tier_6_8_10(price=0.20, min_shares=5.0) == 10.0


def test_live_sizing_policy_keeps_fixed_cash_as_explicit_alternative():
    row = {"edge": 0.25, "p_cal_no_city_ev": 0.40}
    assert (
        shares_for_sizing_policy(
            policy="price_tier_6_8_10_shares",
            price=0.17,
            row=row,
            order_notional_usd=0.8,
            min_shares=5.0,
        )
        == 10.0
    )
    assert (
        shares_for_sizing_policy(
            policy="fixed_cash_order_notional",
            price=0.17,
            row=row,
            order_notional_usd=0.8,
            min_shares=5.0,
        )
        == 5.0
    )


def test_shadow_sizing_variants_include_live_and_counterfactuals():
    args = Namespace(
        order_notional_usd=0.8,
        min_order_shares=5.0,
        taker_fee_rate=0.05,
        maker_rebate_rate=0.0,
        sizing_policy="price_tier_6_8_10_shares",
    )
    shadow = sizing_shadow(0.17, 0.32, {"edge": 0.15, "p_cal_no_city_ev": 0.55}, args)

    assert shadow["live_selected"]["policy"] == "price_tier_6_8_10_shares"
    assert shadow["live_selected"]["shares"] == 10.0
    assert shadow["fixed_cash_0p80"]["shares"] == 4.705882
    assert shadow["fixed_8_shares"]["shares"] == 8.0
    assert shadow["price_tier_6_8_10_shares"]["shares"] == 10.0
    assert shadow["quality_price_tier_5_8_12_shares"]["shares"] == 12.0


def test_debug_distance_override_cannot_run_live():
    args = Namespace(
        live=True,
        confirm_live=True,
        allow_settled=False,
        allow_dist_le0=True,
        allow_dist_lt0=False,
    )

    with pytest.raises(RuntimeError, match="allow-dist-le0"):
        enforce_live_safety_args(args)


def lifecycle_args(**overrides):
    base = dict(
        min_order_shares=5.0,
        min_ask=0.05,
        max_ask=0.20,
        min_edge=0.20,
        min_fee_adjusted_edge=0.15,
        min_decision_hours_to_settle=22.0,
        max_decision_hours_to_settle=24.0,
        taker_fee_rate=0.05,
        maker_rebate_rate=0.0,
        maker_lifecycle_allow_taker_fallback=True,
        maker_lifecycle_taker_ttl_min=30.0,
        maker_lifecycle_refresh_ttl_min=15.0,
        maker_lifecycle_spread_cap=0.01,
        maker_lifecycle_taker_max_premium=0.0,
        maker_lifecycle_reprice_cushion=0.01,
        maker_lifecycle_downshift_min=0.01,
        maker_lifecycle_min_reprice_improvement=0.001,
    )
    base.update(overrides)
    return Namespace(**base)


def test_fresh_snapshot_candidates_do_not_drop_d0_forecast_rows():
    args = Namespace(
        min_ask=0.05,
        max_ask=0.20,
        min_edge=0.20,
        min_decision_hours_to_settle=22.0,
        max_decision_hours_to_settle=24.0,
        min_event_date=None,
        max_event_date=None,
        max_candidates_per_run=80,
    )
    base = {
        "probability_status": "ok",
        "side": "BUY_YES",
        "city": "Atlanta",
        "snapshot_ts_utc": "2026-07-14T15:15:00Z",
        "bracket": "90-91",
        "entry_price": 0.08,
        "model_prob": 0.40,
        "edge": 0.32,
        "hours_to_settle": 23,
        "snapshot_ts_utc": "2026-07-14T15:15:00Z",
    }
    snapshot = {
        "path": __import__("pathlib").Path("snapshot.json"),
        "records": [
            {**base, "event_date": "2026-07-14", "condition_id": "d0"},
            {**base, "event_date": "2026-07-15", "condition_id": "d1"},
            {**base, "event_date": "2026-07-14", "condition_id": "too_late", "hours_to_settle": 10},
            {**base, "event_date": "2026-07-16", "condition_id": "too_early", "hours_to_settle": 30},
        ],
    }

    rows = load_fresh_snapshot_candidates(snapshot, args)

    assert [row["condition_id"] for row in rows] == ["d0", "d1"]


def test_lifecycle_refresh_rejects_stale_probability_when_fresh_edge_is_gone():
    order = {"model_p_yes_used": 0.42}
    fresh = {
        "probability_status": "ok",
        "side": "BUY_YES",
        "event_date": "2026-07-15",
        "city": "Atlanta",
        "snapshot_ts_utc": "2026-07-14T15:15:00Z",
        "bracket": "90-91",
        "forecast_max_native": 89.0,
        "model_p_yes": 0.12,
        "decision_hours_to_settle": 23.0,
    }

    _, reason = refresh_lifecycle_thesis(
        order,
        fresh_row=fresh,
        best_ask=0.10,
        args=lifecycle_args(),
    )

    assert reason == "fresh_weather_fee_edge_below_min"


def test_lifecycle_refresh_accepts_d0_forecast_thesis():
    order = {"model_p_yes_used": 0.32}
    fresh = {
        "probability_status": "ok",
        "side": "BUY_YES",
        "event_date": "2026-07-14",
        "city": "Atlanta",
        "snapshot_ts_utc": "2026-07-14T05:15:00Z",
        "bracket": "90-91",
        "forecast_max_native": 89.0,
        "model_p_yes": 0.40,
        "decision_hours_to_settle": 22.5,
    }

    refreshed, reason = refresh_lifecycle_thesis(
        order,
        fresh_row=fresh,
        best_ask=0.10,
        args=lifecycle_args(),
    )

    assert reason == ""
    assert refreshed["model_p_yes_used"] == 0.40


def test_lifecycle_refresh_rejects_order_far_outside_entry_window():
    _, reason = refresh_lifecycle_thesis(
        {"model_p_yes_used": 0.40},
        fresh_row={
            "probability_status": "ok",
            "side": "BUY_YES",
            "bracket": "90-91",
            "forecast_max_native": 89.0,
            "model_p_yes": 0.40,
            "decision_hours_to_settle": 10.0,
        },
        best_ask=0.10,
        args=lifecycle_args(),
    )

    assert reason == "fresh_weather_outside_entry_window"


def test_lifecycle_prefers_lower_repost_when_book_moves_down():
    action = choose_lifecycle_action(
        order={"posted_price": 0.059, "model_p_yes_used": 0.36, "quote_tick_size": 0.001},
        age_min=20.0,
        remaining_shares=10.0,
        asks=[(0.014, 20.0)],
        bids=[(0.010, 50.0)],
        args=lifecycle_args(),
    )

    assert action["decision_status"] == "planned"
    assert action["execution_action"] == "maker_lifecycle_repost_lower"
    assert action["maker_only"] is True
    assert action["limit_price"] < 0.059


def test_lifecycle_does_not_taker_above_source_price():
    action = choose_lifecycle_action(
        order={"posted_price": 0.151, "model_p_yes_used": 0.36, "quote_tick_size": 0.001},
        age_min=45.0,
        remaining_shares=5.3,
        asks=[(0.156, 20.0)],
        bids=[(0.151, 30.0)],
        args=lifecycle_args(),
    )

    assert action["decision_status"] == "planned"
    assert action["execution_action"] == "maker_lifecycle_reprice_maker"
    assert action["maker_only"] is True
