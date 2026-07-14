import json
from datetime import datetime, timezone

from scripts.ops.weather_fast_source_prev_no_trial import (
    metar_report_clocks,
    next_metar_burst_cities,
    next_metar_window_status,
    source_cross_confirmation,
    submit_fok_with_immediate_retries,
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
    return source_cross_confirmation(
        city=city,
        source=source,
        target_date="2026-07-11",
        source_temp_c=temp,
        source_obs_ts_utc=obs_ts,
        metar_running_max_c=metar_running_max,
        candidate_no_bracket_c=candidate_no_bracket,
        state=state,
    )


def test_persistent_sources_accept_exactly_half_degree_as_first_print():
    result = evaluate(34.5, "2026-07-11T04:46:00+00:00", {})

    assert result["confirmed"] is False
    assert result["blocker"] == "source_cross_persistence_not_met"
    assert result["qualifying_distinct_observations"] == 1


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
    assert result["policy"] == "two_above_half_latest_above_seven_candidate_v3"
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

    assert result["policy"] == "two_above_half_latest_above_seven_candidate_v3"
    assert result["confirmed"] is False


def test_other_sources_keep_existing_arithmetic_round_policy():
    result = source_cross_confirmation(
        city="Tokyo",
        source="jma_amedas",
        target_date="2026-07-11",
        source_temp_c=21.5,
        source_obs_ts_utc="2026-07-11T15:00:00+00:00",
        metar_running_max_c=21,
        candidate_no_bracket_c=21,
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


def test_definitive_fok_rejection_retries_immediately_with_a_fresh_book():
    place_calls = []
    fetch_calls = []

    def place(row):
        place_calls.append(dict(row))
        if len(place_calls) == 1:
            raise RuntimeError("FOK order couldn't be fully filled")
        return {"order_id": "retry-order-id", "place": {"status": "matched"}}

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
            "best_ask": 0.73,
            "ask_size": 76.4,
            "limit_price": 0.75,
        },
        place=place,
        fetch_book_fn=fetch_book,
        market_proxy="proxy",
        book_timeout_sec=5.0,
        max_no_ask=0.94,
        limit_price_cushion=0.02,
        immediate_retries=2,
    )

    assert result["live_submit_status"] == "submitted"
    assert len(place_calls) == 2
    assert len(fetch_calls) == 1
    assert result["order_row"]["best_ask"] == 0.87
    assert result["order_row"]["limit_price"] == 0.89
    assert [attempt["status"] for attempt in result["attempts"]] == ["submit_failed", "submitted"]


def test_ambiguous_submit_error_is_not_retried():
    place_calls = []

    def place(row):
        place_calls.append(dict(row))
        raise TimeoutError("network timeout")

    result = submit_fok_with_immediate_retries(
        {"token_id": "30-no-token", "size": 10.0, "best_ask": 0.73, "ask_size": 76.4, "limit_price": 0.75},
        place=place,
        fetch_book_fn=lambda *_args, **_kwargs: {},
        market_proxy="proxy",
        book_timeout_sec=5.0,
        max_no_ask=0.94,
        limit_price_cushion=0.02,
        immediate_retries=2,
    )

    assert result["live_submit_status"] == "submit_failed"
    assert len(place_calls) == 1


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

    clock = metar_report_clocks(path, "2026-07-13")["Busan"]

    assert clock["routine_metar_cadence_min"] == 60.0
    assert clock["latest_routine_metar_report_ts_utc"] == "2026-07-13T05:00:00+00:00"
    assert clock["next_expected_metar_report_ts_utc"] == "2026-07-13T06:00:00+00:00"


def test_next_metar_execution_window_covers_twenty_minutes_before_and_after():
    clock = {"next_expected_metar_report_ts_utc": "2026-07-13T06:00:00+00:00"}

    too_early = next_metar_window_status(clock, datetime(2026, 7, 13, 5, 18, tzinfo=timezone.utc), window_min=20)
    at_open = next_metar_window_status(clock, datetime(2026, 7, 13, 5, 40, tzinfo=timezone.utc), window_min=20)
    after_due = next_metar_window_status(clock, datetime(2026, 7, 13, 6, 10, tzinfo=timezone.utc), window_min=20)
    too_late = next_metar_window_status(clock, datetime(2026, 7, 13, 6, 21, tzinfo=timezone.utc), window_min=20)

    assert too_early["next_metar_window_eligible"] is False
    assert too_early["minutes_to_next_expected_metar"] == 42.0
    assert at_open["next_metar_window_eligible"] is True
    assert after_due["next_metar_window_eligible"] is True
    assert too_late["next_metar_window_eligible"] is False


def test_next_metar_burst_cities_only_includes_eligible_configured_rows():
    rows = [
        {"city": "Busan", "next_metar_window_eligible": True},
        {"city": "Singapore", "next_metar_window_eligible": False},
        {"city": "Busan", "next_metar_window_eligible": True},
        {"status": "source_missing"},
    ]

    assert next_metar_burst_cities(rows) == ["Busan"]
