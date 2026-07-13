import json
from datetime import datetime, timezone

from scripts.ops.weather_fast_source_prev_no_trial import (
    metar_report_clocks,
    next_metar_window_status,
    source_cross_confirmation,
)


def evaluate(temp: float, obs_ts: str, state: dict, *, city="Busan", source="amos_runway"):
    return source_cross_confirmation(
        city=city,
        source=source,
        target_date="2026-07-11",
        source_temp_c=temp,
        source_obs_ts_utc=obs_ts,
        metar_running_max_c=34,
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
    assert result["policy"] == "two_above_half_latest_above_seven_v2"
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

    assert result["policy"] == "two_above_half_latest_above_seven_v2"
    assert result["confirmed"] is False


def test_other_sources_keep_existing_arithmetic_round_policy():
    result = source_cross_confirmation(
        city="Tokyo",
        source="jma_amedas",
        target_date="2026-07-11",
        source_temp_c=21.5,
        source_obs_ts_utc="2026-07-11T15:00:00+00:00",
        metar_running_max_c=21,
        state={},
    )

    assert result["policy"] == "arithmetic_round_v1"
    assert result["confirmed"] is True


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
