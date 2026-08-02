from argparse import Namespace

import pytest

from scripts.ops.low_price_yes_lottery_tiny_live import (
    annotate_runtime_decision,
    choose_lifecycle_action,
    enforce_live_safety_args,
    execute_non_live_shared_entry_plans,
    load_fresh_snapshot_candidates,
    normalize_snapshot_candidate,
    refresh_lifecycle_thesis,
    shares_for_price_tier_6_8_10,
    shares_for_sizing_policy,
    sizing_shadow,
    submitted_natural_key,
    validate_candidate,
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


def test_non_live_plan_uses_shared_order_runtime_and_dedupes(tmp_path, monkeypatch):
    from scripts.ops import low_price_yes_lottery_tiny_live as runner

    monkeypatch.setattr(runner, "SHARED_EXECUTION_JOURNAL_OUT", tmp_path / "execution.jsonl")
    plan = runner.build_plan(
        {
            "signal_id": "signal-1",
            "city": "Atlanta",
            "target_date": "2026-08-03",
            "condition_id": "condition-1",
            "market_slug": "atlanta-2026-08-03",
            "event_id": "event-1",
            "question": "Atlanta temperature",
            "bracket": "92-93",
            "token_id": "token-1",
            "limit_price": 0.101,
            "maker_limit_price": 0.101,
            "taker_limit_price": 0.11,
            "planned_shares": 5.0,
            "planned_notional_usd": 0.505,
            "model_p_yes": 0.30,
            "fresh_best_bid": 0.10,
            "fresh_best_ask": 0.11,
            "fresh_spread": 0.01,
            "fee_adjusted_edge": 0.19,
            "sizing_policy": "fixed_5_shares",
        },
        live_enabled=False,
    )

    first = execute_non_live_shared_entry_plans([plan], generated_at_utc="2026-08-02T10:00:00Z")
    second = execute_non_live_shared_entry_plans([plan], generated_at_utc="2026-08-02T10:01:00Z")

    assert first["submitted"] == first["venue_calls"] == 1
    assert first["authority"] == "shared_order_runtime"
    assert second["deduped"] == 1
    assert second["venue_calls"] == 0


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


def test_snapshot_normalization_materializes_hot_tail_distance():
    cold = normalize_snapshot_candidate(
        {
            "city": "Atlanta",
            "event_date": "2026-07-14",
            "bracket": "90-91",
            "unit": "F",
            "forecast_max_native": 91.2,
        },
        source_path=__import__("pathlib").Path("snapshot.json"),
    )
    hot = normalize_snapshot_candidate(
        {
            "city": "Atlanta",
            "event_date": "2026-07-14",
            "bracket": "92-93",
            "unit": "F",
            "forecast_max_native": 91.2,
        },
        source_path=__import__("pathlib").Path("snapshot.json"),
    )

    assert cold["bracket_distance_available"] is True
    assert cold["forecast_to_bracket_low_native"] == -1.2
    assert hot["forecast_to_bracket_low_native"] == 0.8


def test_cold_snapshot_candidate_is_blocked_before_token_or_book_fetch():
    args = Namespace(
        min_ask=0.05,
        max_ask=0.20,
        min_edge=0.20,
        max_taker_cushion=0.01,
        min_fee_adjusted_edge=0.15,
        order_notional_usd=0.8,
        sizing_policy="fixed_5_shares",
        min_order_shares=5.0,
        max_decision_snapshot_age_hours=0.5,
        min_decision_hours_to_settle=22.0,
        max_decision_hours_to_settle=24.0,
        allow_dist_le0=False,
        allow_dist_lt0=False,
    )
    row = normalize_snapshot_candidate(
        {
            "probability_status": "ok",
            "side": "BUY_YES",
            "city": "Atlanta",
            "event_date": "2026-07-14",
            "bracket": "90-91",
            "unit": "F",
            "forecast_max_native": 91.2,
            "snapshot_ts_utc": "2026-07-14T15:15:00Z",
            "hours_to_settle": 23.0,
            "entry_price": 0.08,
            "model_prob": 0.40,
            "edge": 0.32,
            "condition_id": "cold",
        },
        source_path=__import__("pathlib").Path("snapshot.json"),
    )

    decision = validate_candidate(row, args, cache={}, submitted_signal_ids=set(), submitted_natural_keys=set(), tail_telemetry_resources=None)

    assert decision["decision_status"] == "blocked"
    assert decision["blocker"] == "dist_lt0_cold_or_inside_forecast_tail_v1"


def test_head_a_dedupe_key_is_city_date_not_exact_bracket():
    first = submitted_natural_key(
        {"target_date": "2026-07-14", "city": "Atlanta", "bracket": "90-91", "condition_id": "a"}
    )
    revised = submitted_natural_key(
        {"target_date": "2026-07-14", "city": "Atlanta", "bracket": "92-93", "condition_id": "b"}
    )

    assert first == revised == "2026-07-14|Atlanta|BUY_YES"


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
