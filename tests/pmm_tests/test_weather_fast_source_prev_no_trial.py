import json
from datetime import datetime, timedelta, timezone

import scripts.ops.weather_fast_source_prev_no_trial as runner

from scripts.ops.weather_fast_source_prev_no_trial import (
    build_parser,
    exact_share_maker_intent,
    metar_report_clocks,
    next_metar_burst_cities,
    next_metar_window_status,
    opportunity_journal_rows,
    resolve_share_cap_pause,
    source_temp_in_market_unit,
    source_cross_confirmation,
)
from scripts.ops.weather_fast_source_city_policy import CITY_POLICIES
from scripts.ops.weather_fast_source_execution import (
    GTD_SECURITY_THRESHOLD_SEC,
    audit_order_share_caps,
    exact_share_taker_intent,
    spent_market_shares,
    submit_fok_with_immediate_retries,
    submit_marketable_gtc,
    submit_post_only_gtd,
)
from scripts.ops.weather_fast_source_stale_book_observer import MarketToken
from weather_data_feed.fast_event_source_policy import load_fast_event_source_profiles
from weather_data_feed.source_event_incremental_state import (
    metar_report_clocks_from_state,
    metar_running_max_from_state,
    refresh_source_event_state,
)


def evaluate(
    temp: float,
    obs_ts: str,
    state: dict,
    *,
    city="Busan",
    source="amos_runway",
    metar_running_max=34,
    candidate_no_bracket=34,
):
    policy = CITY_POLICIES[(city, source)]
    return source_cross_confirmation(
        city=city,
        source=source,
        target_date="2026-07-11",
        source_market_temp=temp,
        source_market_value=int(temp + 0.5),
        source_obs_ts_utc=obs_ts,
        metar_running_max_value=metar_running_max,
        candidate_no_bracket=candidate_no_bracket,
        policy=policy,
        state=state,
    )


def test_persistent_sources_accept_exactly_half_degree_as_first_print():
    result = evaluate(34.5, "2026-07-11T04:46:00+00:00", {})

    assert result["confirmed"] is False
    assert result["blocker"] == "source_cross_persistence_not_met"
    assert result["qualifying_distinct_observations"] == 1


def test_fahrenheit_source_temperature_is_compared_in_market_units():
    assert source_temp_in_market_unit(31.0, "F") == 87.8
    assert source_temp_in_market_unit(31.0, "C") == 31.0


def test_persistent_sources_wait_for_a_second_distinct_observation():
    state = {}
    first = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    repeated_poll = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)

    assert first["blocker"] == "source_cross_persistence_not_met"
    assert repeated_poll["qualifying_distinct_observations"] == 1
    assert repeated_poll["confirmed"] is False


def test_persistent_policy_does_not_reuse_legacy_persistence_state():
    state = {
        "Busan|2026-07-11|amos_runway|34": {
            "last_source_obs_ts_utc": "2026-07-11T04:45:00+00:00",
            "last_observation_qualified": True,
            "qualifying_distinct_observations": 1,
        }
    }

    result = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 1
    assert result["confirmed"] is False


def test_persistent_sources_confirm_when_latest_print_reaches_seven_tenths():
    state = {}
    evaluate(34.5, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.7, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["confirmed"] is True
    assert result["policy"] == "persistent_candidate_margin_v5"
    assert result["blocker"] == ""


def test_latest_print_below_seven_tenths_does_not_confirm():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    result = evaluate(34.6, "2026-07-11T04:47:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 2
    assert result["confirmed"] is False
    assert result["blocker"] == "latest_source_cross_strength_not_met"


def test_busans_nonqualifying_print_resets_persistence():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    evaluate(34.4, "2026-07-11T04:47:00+00:00", state)
    result = evaluate(34.8, "2026-07-11T04:48:00+00:00", state)

    assert result["qualifying_distinct_observations"] == 1
    assert result["confirmed"] is False


def test_persistent_city_states_do_not_clear_each_other():
    state = {}
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state)
    evaluate(34.8, "2026-07-11T04:46:00+00:00", state, city="Helsinki", source="fmi")
    busan = evaluate(34.8, "2026-07-11T04:47:00+00:00", state)
    helsinki = evaluate(34.8, "2026-07-11T04:47:00+00:00", state, city="Helsinki", source="fmi")

    assert busan["confirmed"] is True
    assert helsinki["confirmed"] is True


def test_singapore_uses_persistent_confirmation_policy():
    result = evaluate(
        34.8,
        "2026-07-11T04:46:00+00:00",
        {},
        city="Singapore",
        source="singapore_mss",
    )

    assert result["policy"] == "persistent_candidate_margin_v5"
    assert result["confirmed"] is False


def test_other_sources_keep_existing_arithmetic_round_policy():
    result = source_cross_confirmation(
        city="Tokyo",
        source="jma_amedas",
        target_date="2026-07-11",
        source_market_temp=21.5,
        source_market_value=22,
        source_obs_ts_utc="2026-07-11T15:00:00+00:00",
        metar_running_max_value=21,
        candidate_no_bracket=21,
        policy=CITY_POLICIES[("Tokyo", "jma_amedas")],
        state={},
    )

    assert result["policy"] == "arithmetic_round_v1"
    assert result["confirmed"] is True


def test_persistent_confirmation_is_scoped_to_candidate_no_bracket():
    state = {}
    evaluate(
        29.6,
        "2026-07-14T00:41:00+00:00",
        state,
        metar_running_max=29,
        candidate_no_bracket=29,
    )
    evaluate(
        29.8,
        "2026-07-14T00:51:00+00:00",
        state,
        metar_running_max=29,
        candidate_no_bracket=29,
    )

    first_30_no_print = evaluate(
        30.6,
        "2026-07-14T00:58:00+00:00",
        state,
        metar_running_max=29,
        candidate_no_bracket=30,
    )
    confirmed_30_no = evaluate(
        30.7,
        "2026-07-14T00:59:00+00:00",
        state,
        metar_running_max=29,
        candidate_no_bracket=30,
    )

    assert first_30_no_print["qualifying_distinct_observations"] == 1
    assert first_30_no_print["confirmed"] is False
    assert first_30_no_print["threshold_c"] == 30.5
    assert first_30_no_print["strong_threshold_c"] == 30.7
    assert confirmed_30_no["qualifying_distinct_observations"] == 2
    assert confirmed_30_no["confirmed"] is True


def test_nonqualifying_observation_resets_other_bracket_streak():
    state = {}
    evaluate(
        32.5,
        "2026-07-15T05:24:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=32,
    )
    evaluate(
        31.9,
        "2026-07-15T05:39:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=31,
    )
    first_new_cross = evaluate(
        33.0,
        "2026-07-15T05:44:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=32,
    )
    confirmed = evaluate(
        32.9,
        "2026-07-15T05:49:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=32,
    )

    assert first_new_cross["qualifying_distinct_observations"] == 1
    assert first_new_cross["confirmed"] is False
    assert confirmed["qualifying_distinct_observations"] == 2
    assert confirmed["confirmed"] is True

    evaluate(
        32.1,
        "2026-07-15T05:58:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=31,
    )
    first_after_pullback = evaluate(
        32.5,
        "2026-07-15T06:04:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=32,
    )
    second_after_pullback = evaluate(
        32.8,
        "2026-07-15T06:08:00+00:00",
        state,
        city="Singapore",
        source="singapore_mss",
        metar_running_max=32,
        candidate_no_bracket=32,
    )

    assert first_after_pullback["qualifying_distinct_observations"] == 1
    assert first_after_pullback["confirmed"] is False
    assert second_after_pullback["qualifying_distinct_observations"] == 2
    assert second_after_pullback["confirmed"] is True


def test_out_of_order_observation_cannot_confirm_persistence():
    state = {}
    evaluate(34.8, "2026-07-11T04:47:00+00:00", state)
    result = evaluate(34.8, "2026-07-11T04:46:00+00:00", state)

    assert result["confirmed"] is False
    assert result["blocker"] == "source_observation_out_of_order"
    assert result["qualifying_distinct_observations"] == 1


def test_definitive_fok_rejection_retries_immediately_with_a_fresh_book():
    place_calls = []
    fetch_calls = []

    def place(row):
        place_calls.append(dict(row))
        if len(place_calls) == 1:
            raise RuntimeError("FOK order couldn't be fully filled")
        return {
            "order_id": "retry-order-id",
            "place": {
                "success": True,
                "status": "matched",
                "orderID": "retry-order-id",
                "takingAmount": "10",
                "makingAmount": "8.7",
            },
        }

    def fetch_book(token_id, **kwargs):
        fetch_calls.append((token_id, kwargs))
        return {
            "status": "ok",
            "summary": {"best_ask": 0.87, "ask_size": 35.6},
            "http_status": 200,
            "proxy_used": "proxy",
        }

    result = submit_fok_with_immediate_retries(
        {
            "token_id": "30-no-token",
            "size": 10.0,
            "desired_shares": 10.0,
            "best_ask": 0.73,
            "ask_size": 76.4,
            "limit_price": 0.75,
        },
        place=place,
        fetch_book_fn=fetch_book,
        market_proxy="proxy",
        book_timeout_sec=5.0,
        max_no_ask=0.94,
        immediate_retries=2,
    )

    assert result["live_submit_status"] == "submitted"
    assert len(place_calls) == 2
    assert len(fetch_calls) == 1
    assert result["order_row"]["best_ask"] == 0.87
    assert result["order_row"]["limit_price"] == 0.87
    assert result["actual_fill_shares"] == 10.0
    assert [attempt["status"] for attempt in result["attempts"]] == ["submit_failed", "submitted"]


def test_ambiguous_submit_error_is_not_retried():
    place_calls = []

    def place(row):
        place_calls.append(dict(row))
        raise TimeoutError("network timeout")

    result = submit_fok_with_immediate_retries(
        {"token_id": "30-no-token", "size": 10.0, "desired_shares": 10.0, "best_ask": 0.73, "ask_size": 76.4, "limit_price": 0.73},
        place=place,
        fetch_book_fn=lambda *_args, **_kwargs: {},
        market_proxy="proxy",
        book_timeout_sec=5.0,
        max_no_ask=0.94,
        immediate_retries=2,
    )

    assert result["live_submit_status"] == "submit_failed"
    assert len(place_calls) == 1


def test_fok_post_fill_invariant_marks_share_cap_violation():
    result = submit_fok_with_immediate_retries(
        {
            "token_id": "token",
            "size": 5.0,
            "desired_shares": 5.0,
            "max_shares_per_market": 5.0,
            "best_ask": 0.33,
            "ask_size": 20.0,
            "limit_price": 0.92,
        },
        place=lambda _row: {
            "order_id": "overfill",
            "place": {
                "success": True,
                "status": "matched",
                "orderID": "overfill",
                "takingAmount": "13.823528",
                "makingAmount": "4.599999",
            },
        },
        fetch_book_fn=lambda *_args, **_kwargs: {},
        market_proxy="",
        book_timeout_sec=5.0,
        max_no_ask=0.94,
        immediate_retries=0,
    )

    assert result["live_submit_status"] == "share_cap_violation"
    assert result["share_cap_check"]["share_cap_violation"] is True
    assert result["share_cap_check"]["share_cap_excess_shares"] == 8.823528
    assert result["error"] == "actual_fill_shares_exceeded_desired_or_market_cap"


def test_market_cap_uses_actual_exchange_fill_shares(tmp_path):
    path = tmp_path / "orders.jsonl"
    path.write_text(
        json.dumps(
            {
                "target_date": "2026-07-14",
                "token_id": "token",
                "size": 5.0,
                "actual_fill_shares": 5.037507,
                "live_submit_status": "submitted",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert spent_market_shares(path, target_date="2026-07-14", token_id="token") == 5.037507


def test_market_cap_reserves_all_shares_of_a_posted_maker_order(tmp_path):
    path = tmp_path / "orders.jsonl"
    path.write_text(
        json.dumps(
            {
                "target_date": "2026-07-15",
                "token_id": "token",
                "size": 5.0,
                "desired_shares": 5.0,
                "live_submit_status": "posted",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert spent_market_shares(path, target_date="2026-07-15", token_id="token") == 5.0


def test_low_ask_exact_share_intent_uses_post_only_gtd_not_fok_usdc_spend():
    now = datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc)

    intent = exact_share_maker_intent(
        best_ask=0.33,
        tick_size=0.01,
        desired_shares=5.0,
        now=now,
        effective_lifetime_sec=45,
    )

    assert intent["limit_price"] == 0.32
    assert intent["size"] == 5.0
    assert intent["desired_shares"] == 5.0
    assert intent["submitted_notional_usd"] == 1.6
    assert intent["order_type"] == "GTD"
    assert intent["post_only"] is True
    assert intent["share_cap_enforcement"] == "resting_post_only_signed_size_v1"
    assert intent["expiration"] == int(now.timestamp()) + GTD_SECURITY_THRESHOLD_SEC + 45
    assert intent["gtd_security_threshold_sec"] == 180


def test_exact_share_taker_intent_uses_marketable_gtc_not_fok_usdc_spend():
    intent = exact_share_taker_intent(best_ask=0.82, desired_shares=10.0)

    assert intent == {
        "limit_price": 0.82,
        "size": 10.0,
        "desired_shares": 10.0,
        "submitted_notional_usd": 8.2,
        "limit_price_policy": "fresh_best_ask_marketable_gtc_v1",
        "order_type": "GTC",
        "post_only": False,
        "expiration": 0,
        "share_cap_enforcement": "marketable_gtc_signed_size_v1",
    }


def test_marketable_gtc_accepts_exact_ten_share_immediate_match():
    calls = []

    def place(row):
        calls.append(dict(row))
        return {
            "order_id": "taker-order",
            "order_type": "GTC",
            "order_mode": "marketable_gtc_buy_shares",
            "post_only": False,
            "place": {
                "success": True,
                "status": "matched",
                "orderID": "taker-order",
                "takingAmount": "10",
                "makingAmount": "8.2",
            },
        }

    result = submit_marketable_gtc(
        {
            "token_id": "token",
            "best_ask": 0.82,
            "ask_size": 20.0,
            "limit_price": 0.82,
            "size": 10.0,
            "desired_shares": 10.0,
            "max_shares_per_market": 15.0,
            "share_cap_enforcement": "marketable_gtc_signed_size_v1",
        },
        place=place,
    )

    assert result["live_submit_status"] == "submitted"
    assert result["exchange_order_status"] == "matched"
    assert result["actual_fill_shares"] == 10.0
    assert result["actual_fill_cost_usd"] == 8.2
    assert result["share_cap_check"]["share_cap_violation"] is False
    assert calls[0]["size"] == 10.0


def test_marketable_gtc_partial_or_unfilled_remainder_stays_bounded_by_signed_shares():
    def place(_row):
        return {
            "order_id": "resting-taker-remainder",
            "order_type": "GTC",
            "order_mode": "marketable_gtc_buy_shares",
            "post_only": False,
            "place": {
                "success": True,
                "status": "live",
                "orderID": "resting-taker-remainder",
            },
        }

    result = submit_marketable_gtc(
        {
            "token_id": "token",
            "limit_price": 0.82,
            "size": 10.0,
            "desired_shares": 10.0,
            "max_shares_per_market": 15.0,
            "share_cap_enforcement": "marketable_gtc_signed_size_v1",
        },
        place=place,
    )

    assert result["live_submit_status"] == "submitted"
    assert result["exchange_order_status"] == "live"
    assert result["actual_fill_shares"] is None
    assert result["share_cap_check"]["effective_share_cap"] == 10.0


def test_all_live_fast_source_city_policies_use_fifteen_share_split_cap():
    live_policies = [policy for policy in CITY_POLICIES.values() if policy.default_mode == "live_trial"]

    assert live_policies
    assert {(policy.shares_per_trade, policy.max_shares_per_market) for policy in live_policies} == {(15.0, 15.0)}


def test_post_only_gtd_accepts_only_a_resting_order_and_reserves_exact_shares():
    calls = []

    def place(row):
        calls.append(dict(row))
        return {
            "order_id": "maker-order",
            "order_type": "GTD",
            "order_mode": "post_only_gtd_buy_shares",
            "post_only": True,
            "place": {
                "success": True,
                "status": "live",
                "orderID": "maker-order",
                "takingAmount": "5",
                "makingAmount": "1.6",
            },
        }

    result = submit_post_only_gtd(
        {
            "token_id": "token",
            "limit_price": 0.32,
            "size": 5.0,
            "desired_shares": 5.0,
            "max_shares_per_market": 5.0,
            "expiration": 123,
            "share_cap_enforcement": "resting_post_only_signed_size_v1",
        },
        place=place,
    )

    assert result["live_submit_status"] == "submitted"
    assert result["live_order_posted"] is True
    assert result["actual_fill_shares"] is None
    assert calls[0]["size"] == 5.0


def test_post_only_gtd_immediately_reprices_a_crossing_rejection():
    calls = []

    def place(row):
        calls.append(dict(row))
        if len(calls) == 1:
            raise RuntimeError("invalid post-only order: order crosses book")
        return {
            "order_id": "repriced-maker-order",
            "order_type": "GTD",
            "order_mode": "post_only_gtd_buy_shares",
            "post_only": True,
            "place": {"success": True, "status": "live", "orderID": "repriced-maker-order"},
        }

    def fetch_book(*_args, **_kwargs):
        return {
            "status": "ok",
            "summary": {"best_ask": 0.64, "ask_size": 20.0, "tick_size": 0.01},
            "http_status": 200,
            "proxy_used": "proxy",
        }

    result = submit_post_only_gtd(
        {
            "token_id": "token",
            "best_ask": 0.67,
            "ask_size": 20.0,
            "tick_size": 0.01,
            "limit_price": 0.66,
            "size": 10.0,
            "desired_shares": 10.0,
            "max_shares_per_market": 10.0,
            "effective_lifetime_sec": 45,
            "share_cap_enforcement": "resting_post_only_signed_size_v1",
        },
        place=place,
        fetch_book_fn=fetch_book,
        market_proxy="proxy",
        max_no_ask=0.94,
        immediate_reprices=2,
    )

    assert result["live_submit_status"] == "submitted"
    assert len(calls) == 2
    assert calls[1]["best_ask"] == 0.64
    assert calls[1]["limit_price"] == 0.63
    assert calls[1]["size"] == 10.0
    assert result["attempts"][0]["post_only_crossing"] is True


def test_post_only_gtd_reprice_stops_when_new_ask_exceeds_cap():
    calls = []

    def place(row):
        calls.append(dict(row))
        raise RuntimeError("invalid post-only order: order crosses book")

    def fetch_book(*_args, **_kwargs):
        return {"status": "ok", "summary": {"best_ask": 0.95, "ask_size": 20.0, "tick_size": 0.01}}

    result = submit_post_only_gtd(
        {
            "token_id": "token",
            "best_ask": 0.67,
            "ask_size": 20.0,
            "tick_size": 0.01,
            "limit_price": 0.66,
            "size": 10.0,
            "desired_shares": 10.0,
            "max_shares_per_market": 10.0,
            "effective_lifetime_sec": 45,
        },
        place=place,
        fetch_book_fn=fetch_book,
        max_no_ask=0.94,
        immediate_reprices=2,
    )

    assert result["live_submit_status"] == "submit_failed"
    assert len(calls) == 1
    assert result["attempts"][-1]["status"] == "reprice_blocked"
    assert result["attempts"][-1]["blockers"] == ["ask_above_max"]


def test_post_fill_share_cap_violation_is_visible_and_pauses_post_fix_orders(tmp_path):
    path = tmp_path / "orders.jsonl"
    old = {
        "target_date": "2026-07-11",
        "city": "Busan",
        "desired_shares": 5.0,
        "max_shares_per_market": 5.0,
        "actual_fill_shares": 13.823528,
        "live_submit_status": "submitted",
        "order_id": "old",
    }
    post_fix = {
        "target_date": "2026-07-15",
        "city": "Tokyo",
        "desired_shares": 5.0,
        "max_shares_per_market": 5.0,
        "actual_fill_shares": 5.01,
        "live_submit_status": "share_cap_violation",
        "share_cap_enforcement": "resting_post_only_signed_size_v1",
        "order_id": "new",
    }
    path.write_text(json.dumps(old) + "\n" + json.dumps(post_fix) + "\n", encoding="utf-8")

    audit = audit_order_share_caps(path)

    assert audit["share_cap_anomaly_count"] == 2
    assert audit["post_fix_share_cap_anomaly_count"] == 1
    assert audit["pause_required"] is True
    assert audit["post_fix_pause_required"] is True
    assert audit["share_cap_anomalies"][0]["share_cap_excess_shares"] == 8.823528


def test_historical_acknowledgement_does_not_clear_a_post_fix_pause():
    historical_only = {
        "share_cap_anomaly_count": 12,
        "post_fix_pause_required": False,
    }
    post_fix = {
        "share_cap_anomaly_count": 13,
        "post_fix_pause_required": True,
    }

    assert resolve_share_cap_pause({}, historical_only, historical_acknowledged=False) == (
        True,
        "historical_actual_fill_exceeded_desired_or_market_cap_requires_acknowledgement",
    )
    assert resolve_share_cap_pause({}, historical_only, historical_acknowledged=True) == (False, "")
    assert resolve_share_cap_pause({}, post_fix, historical_acknowledged=True) == (
        True,
        "post_fix_actual_fill_exceeded_desired_or_market_cap",
    )


def test_generic_live_chain_uses_city_policy_and_records_matched_fill(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    target_date = "2026-07-14"
    token = MarketToken(
        city="Tokyo",
        target_date=target_date,
        bracket="21",
        question="Will Tokyo be 21C?",
        event_slug="event",
        market_id="market",
        condition_id="0x" + "1" * 64,
        yes_token_id="yes",
        no_token_id="no",
    )
    monkeypatch.setattr(
        runner,
        "source_latest_by_city",
        lambda *_args, **_kwargs: {
            ("Tokyo", target_date): {
                "source": "jma_amedas",
                "temp_c": 21.5,
                "source_market_value": 22,
                "runway": "15R/33L",
                "primary_runway": "15L",
                "preferred_temperature_runway": "15R/33L",
                "is_preferred_temperature_runway": True,
                "source_obs_ts_utc": (now - timedelta(minutes=2)).isoformat(),
                "source_detect_ts_utc": (now - timedelta(minutes=1)).isoformat(),
            }
        },
    )
    monkeypatch.setattr(
        runner,
        "refresh_source_event_state",
        lambda *_args, **_kwargs: ({}, {"status": "ok", "lines_read": 0, "bytes_read": 0}),
    )
    monkeypatch.setattr(
        runner,
        "metar_running_max_from_state",
        lambda *_args, **_kwargs: {
            ("Tokyo", target_date): {
                "metar_running_max_market_value": 21,
                "metar_running_max_temp_c": 21.0,
                "latest_metar_temp_c": 21.0,
                "latest_metar_round_c": 21,
                "latest_report_ts_utc": (now - timedelta(minutes=10)).isoformat(),
                "latest_detect_ts_utc": (now - timedelta(minutes=9)).isoformat(),
            }
        },
    )
    monkeypatch.setattr(
        runner,
        "metar_report_clocks_from_state",
        lambda *_args, **_kwargs: {
            ("Tokyo", target_date): {"next_expected_metar_report_ts_utc": (now + timedelta(minutes=5)).isoformat()}
        },
    )
    monkeypatch.setattr(runner, "latest_paper_snapshot", lambda: tmp_path / "paper.json")
    monkeypatch.setattr(runner, "latest_orderbook_snapshot", lambda: tmp_path / "book.json")
    monkeypatch.setattr(runner, "build_market_index", lambda *_args, **_kwargs: {("Tokyo", target_date, "21"): token})
    monkeypatch.setattr(
        runner,
        "fetch_fresh_book",
        lambda *_args, **_kwargs: {"status": "ok", "summary": {"best_ask": 0.8, "ask_size": 10.0, "tick_size": 0.01}},
    )
    args = build_parser().parse_args(
        [
            "--target-date",
            target_date,
            "--output-dir",
            str(tmp_path / "out"),
            "--live-cities",
            "Tokyo",
            "--shadow-cities",
            "--live",
            "--confirm-live",
            "--no-prebuild-live-client",
        ]
    )
    taker_place = lambda _row: {
        "order_id": "taker-order",
        "order_type": "GTC",
        "order_mode": "marketable_gtc_buy_shares",
        "post_only": False,
        "place": {
            "success": True,
            "status": "matched",
            "orderID": "taker-order",
            "takingAmount": "10",
            "makingAmount": "8",
        },
    }
    maker_place = lambda _row: {
        "order_id": "maker-order",
        "order_type": "GTD",
        "order_mode": "post_only_gtd_buy_shares",
        "post_only": True,
        "place": {
            "success": True,
            "status": "live",
            "orderID": "maker-order",
            "takingAmount": "5",
            "makingAmount": "3.95",
        },
    }

    latest = runner.run_once(args, {"taker_place": taker_place, "maker_place": maker_place})
    orders = [json.loads(line) for line in (tmp_path / "out" / "orders.jsonl").read_text(encoding="utf-8").splitlines()]
    by_role = {order["child_order_role"]: order for order in orders}

    assert latest["live_orders_submitted"] == 2
    assert latest["city_policies"]["Tokyo"]["shares_per_trade"] == 15.0
    assert latest["caps"]["taker_shares"] == 10.0
    assert latest["caps"]["maker_shares"] == 5.0
    assert by_role["taker"]["limit_price"] == 0.8
    assert by_role["taker"]["actual_fill_shares"] == 10.0
    assert by_role["taker"]["post_only"] is False
    assert by_role["taker"]["order_type"] == "GTC"
    assert by_role["maker"]["limit_price"] == 0.79
    assert by_role["maker"]["actual_fill_shares"] is None
    assert by_role["maker"]["post_only"] is True
    assert by_role["maker"]["order_type"] == "GTD"
    for order in orders:
        assert order["source_runway"] == "15R/33L"
        assert order["source_primary_runway"] == "15L"
        assert order["source_is_preferred_temperature_runway"] is True
        assert order["live_submit_status"] == "submitted"
        assert order["live_order_posted"] is True
        assert order["t_minus_1_no_bracket"] == 21


def test_candidate_market_falls_back_to_gamma_when_snapshot_omits_bracket(monkeypatch):
    target_date = "2026-07-14"
    token = MarketToken(
        city="Istanbul",
        target_date=target_date,
        bracket="26",
        question="Will Istanbul be 26C?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )
    monkeypatch.setattr(
        runner,
        "augment_market_index_from_gamma",
        lambda index, **_kwargs: {**index, ("Istanbul", target_date, "26"): token},
    )

    resolved, index, resolution = runner.resolve_candidate_market(
        {},
        city="Istanbul",
        target_date=target_date,
        candidate=26,
        market_proxy="http://127.0.0.1:7890",
    )

    assert resolved == token
    assert index[("Istanbul", target_date, "26")] == token
    assert resolution == "gamma_fallback"


def test_candidate_market_rejects_value_inside_lower_bound_bucket(monkeypatch):
    target_date = "2026-07-14"
    lower_bucket = MarketToken(
        city="Helsinki",
        target_date=target_date,
        bracket="20",
        question="Will the highest temperature in Helsinki be 20C or below?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )
    index = {("Helsinki", target_date, "20"): lower_bucket}
    monkeypatch.setattr(runner, "augment_market_index_from_gamma", lambda current, **_kwargs: current)

    resolved, _index, resolution = runner.resolve_candidate_market(
        index,
        city="Helsinki",
        target_date=target_date,
        candidate=18,
        market_proxy="http://127.0.0.1:7890",
    )

    assert resolved is None
    assert resolution == "unresolved"


def test_candidate_market_accepts_lower_bound_boundary():
    token = MarketToken(
        city="Helsinki",
        target_date="2026-07-14",
        bracket="20",
        question="Will the highest temperature in Helsinki be 20C or below?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )

    assert runner.candidate_market_is_lockable(token, 20) is True
    assert runner.candidate_market_is_lockable(token, 19) is False


def test_range_candidate_uses_bracket_containing_metar_running_max():
    token = MarketToken(
        city="Miami",
        target_date="2026-07-15",
        bracket="86-87",
        question="Will the highest temperature in Miami be between 86-87F?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )
    index = {("Miami", "2026-07-15", "86-87"): token}

    resolved, _index, upper, resolution = runner.resolve_range_candidate_market(
        index,
        city="Miami",
        target_date="2026-07-15",
        metar_running_max_value=86,
        market_proxy="http://127.0.0.1:7890",
    )

    assert resolved == token
    assert upper == 87
    assert resolution == "paper_snapshot"


def test_range_candidate_rejects_open_top_bracket():
    token = MarketToken(
        city="Miami",
        target_date="2026-07-15",
        bracket="104+",
        question="Will the highest temperature in Miami be 104F or higher?",
        event_slug="event",
        market_id="market",
        condition_id="condition",
        yes_token_id="yes",
        no_token_id="no",
    )
    index = {("Miami", "2026-07-15", "104+"): token}

    resolved, _index, upper, resolution = runner.resolve_range_candidate_market(
        index,
        city="Miami",
        target_date="2026-07-15",
        metar_running_max_value=104,
        market_proxy="http://127.0.0.1:7890",
    )

    assert resolved is None
    assert upper is None
    assert resolution == "unsupported_open_top_bracket"


def test_metar_report_clock_uses_routine_reports_and_ignores_speci(tmp_path):
    path = tmp_path / "sources.jsonl"
    rows = [
        ("2026-07-13T03:00:00+00:00", "METAR RKPK 130300Z"),
        ("2026-07-13T04:00:00+00:00", "METAR RKPK 130400Z"),
        ("2026-07-13T04:27:00+00:00", "SPECI RKPK 130427Z"),
        ("2026-07-13T05:00:00+00:00", "METAR RKPK 130500Z"),
    ]
    path.write_text(
        "".join(
            json.dumps(
                {
                    "city": "Busan",
                    "target_date": "2026-07-13",
                    "source_report_ts_utc": report_ts,
                    "raw_metar": raw_metar,
                }
            )
            + "\n"
            for report_ts, raw_metar in rows
        ),
        encoding="utf-8",
    )

    clock = metar_report_clocks(path, {"Busan": "2026-07-13"})[("Busan", "2026-07-13")]

    assert clock["routine_metar_cadence_min"] == 60.0
    assert clock["latest_routine_metar_report_ts_utc"] == "2026-07-13T05:00:00+00:00"
    assert clock["next_expected_metar_report_ts_utc"] == "2026-07-13T06:00:00+00:00"


def test_incremental_metar_state_warm_starts_new_day_and_reads_only_appends(tmp_path):
    path = tmp_path / "sources.jsonl"
    profiles = load_fast_event_source_profiles()
    city_profiles = {"Tokyo": profiles[("Tokyo", "jma_amedas")]}

    def source_row(target_date, report_ts, temp_c, raw_metar):
        return {
            "city": "Tokyo",
            "target_date": target_date,
            "source": "aviationweather_metar",
            "source_report_ts_utc": report_ts,
            "local_detect_ts_utc": report_ts,
            "temp_c": temp_c,
            "raw_metar": raw_metar,
        }

    rows = [
        source_row("2026-07-12", "2026-07-12T13:30:00+00:00", 28.0, "METAR RJTT 121330Z"),
        source_row("2026-07-12", "2026-07-12T14:00:00+00:00", 28.0, "METAR RJTT 121400Z"),
        source_row("2026-07-12", "2026-07-12T14:30:00+00:00", 28.0, "METAR RJTT 121430Z"),
        source_row("2026-07-13", "2026-07-12T15:00:00+00:00", 29.0, "METAR RJTT 121500Z"),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    state, first_refresh = refresh_source_event_state(
        path,
        None,
        target_dates_by_city={"Tokyo": "2026-07-13"},
        city_profiles=city_profiles,
    )
    clocks = metar_report_clocks_from_state(
        state,
        {"Tokyo": "2026-07-13"},
        now=datetime(2026, 7, 12, 15, 17, tzinfo=timezone.utc),
    )
    clock = clocks[("Tokyo", "2026-07-13")]

    assert first_refresh["full_rebuild"] is True
    assert first_refresh["lines_read"] == 4
    assert clock["routine_metar_clock_source"] == "cross_day_warm_start"
    assert clock["routine_metar_cadence_min"] == 30.0
    assert clock["routine_metar_report_count"] == 1
    assert clock["next_expected_metar_report_ts_utc"] == "2026-07-12T15:30:00+00:00"
    assert metar_running_max_from_state(state, {"Tokyo": "2026-07-13"})[("Tokyo", "2026-07-13")][
        "metar_running_max_market_value"
    ] == 29

    appended = source_row("2026-07-13", "2026-07-12T15:30:00+00:00", 29.0, "METAR RJTT 121530Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(appended) + "\n")
    prior_offset = state["byte_offset"]
    state, second_refresh = refresh_source_event_state(
        path,
        state,
        target_dates_by_city={"Tokyo": "2026-07-13"},
        city_profiles=city_profiles,
    )

    assert second_refresh["full_rebuild"] is False
    assert second_refresh["lines_read"] == 1
    assert second_refresh["bytes_read"] == state["byte_offset"] - prior_offset


def test_opportunity_journal_only_writes_changes_or_heartbeat():
    now = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
    row = {
        "city": "Tokyo",
        "target_date": "2026-07-21",
        "status": "blocked",
        "blockers": ["missing_best_ask"],
    }

    first, state = opportunity_journal_rows([row], None, now=now, heartbeat_sec=300)
    unchanged, state = opportunity_journal_rows(
        [{**row, "ts_utc": (now + timedelta(seconds=10)).isoformat()}],
        state,
        now=now + timedelta(seconds=10),
        heartbeat_sec=300,
    )
    heartbeat, _state = opportunity_journal_rows(
        [row],
        state,
        now=now + timedelta(seconds=301),
        heartbeat_sec=300,
    )

    assert len(first) == 1
    assert unchanged == []
    assert len(heartbeat) == 1


def test_next_metar_execution_window_is_pre_report_deadline():
    clock = {"next_expected_metar_report_ts_utc": "2026-07-13T06:00:00+00:00"}

    too_early = next_metar_window_status(clock, datetime(2026, 7, 13, 5, 18, tzinfo=timezone.utc), window_min=20)
    at_open = next_metar_window_status(clock, datetime(2026, 7, 13, 5, 40, tzinfo=timezone.utc), window_min=20)
    after_due = next_metar_window_status(clock, datetime(2026, 7, 13, 6, 10, tzinfo=timezone.utc), window_min=20)
    too_late = next_metar_window_status(clock, datetime(2026, 7, 13, 6, 21, tzinfo=timezone.utc), window_min=20)

    assert too_early["next_metar_window_eligible"] is False
    assert too_early["minutes_to_next_expected_metar"] == 42.0
    assert at_open["next_metar_window_eligible"] is True
    assert after_due["next_metar_window_eligible"] is False
    assert after_due["next_metar_window_blocker"] == "outside_next_metar_execution_window"
    assert too_late["next_metar_window_eligible"] is False


def test_next_metar_burst_cities_only_includes_eligible_configured_rows():
    rows = [
        {"city": "Busan", "next_metar_window_eligible": True},
        {"city": "Singapore", "next_metar_window_eligible": False},
        {"city": "Busan", "next_metar_window_eligible": True},
        {"status": "source_missing"},
    ]

    assert next_metar_burst_cities(rows) == ["Busan"]
