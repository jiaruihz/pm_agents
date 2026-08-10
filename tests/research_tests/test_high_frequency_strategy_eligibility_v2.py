from datetime import datetime, timezone
import json

from scripts.analysis.forecast_quality.research_high_frequency_strategy_eligibility_v2 import (
    ReferenceEvent,
    annotate_market_brackets,
    annotate_settlement_labels,
    build_event_comparison,
    first_cross_rows,
    iter_jsonl,
    load_reference_events,
    parse_bracket_contains,
    persistent_cross_rows,
)


def utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=timezone.utc)


def test_event_comparison_uses_later_report_not_another_route_copy() -> None:
    previous = ReferenceEvent("Test", "aviationweather_metar", utc(10, 0), utc(10, 5), 20.0, 20, "2026-07-15", 20)
    following = ReferenceEvent("Test", "aviationweather_metar", utc(10, 30), utc(10, 36), 21.0, 21, "2026-07-15", 21)
    fast = [{
        "city": "Test", "source": "fast", "obs_ts": utc(10, 20), "detect_ts": utc(10, 21),
        "target_date": "2026-07-15", "market_unit": "C", "temp_unit": 20.7, "temp_round": 21, "age_min": 1.0,
    }]

    rows = build_event_comparison(fast, {"Test": [previous, following]}, 90.0)

    assert len(rows) == 1
    assert rows[0]["next_metar_report_ts_utc"] == utc(10, 30).isoformat()
    assert rows[0]["fast_implies_cross"] is True
    assert rows[0]["next_metar_crossed"] is True


def test_first_cross_deduplicates_repeated_poll_of_same_decision() -> None:
    base = {
        "city": "Test", "target_date": "2026-07-15", "fast_source": "fast",
        "previous_market_bracket": "20", "fast_temp_round": 21,
        "fast_implies_market_cross": True, "next_metar_market_crossed": True,
    }
    rows = first_cross_rows([
        {**base, "fast_detect_ts_utc": utc(10, 22).isoformat()},
        {**base, "fast_detect_ts_utc": utc(10, 21).isoformat()},
    ])

    assert len(rows) == 1
    assert rows[0]["fast_detect_ts_utc"] == utc(10, 21).isoformat()


def test_persistent_cross_requires_two_distinct_observations_and_strong_latest() -> None:
    base = {
        "city": "Test", "target_date": "2026-07-15", "fast_source": "fast",
        "previous_market_bracket": "20", "previous_market_bracket_upper": 20,
        "fast_implies_market_cross": True, "next_metar_market_crossed": True,
        "next_metar_report_clock_distance_min": 10.0,
    }
    rows = persistent_cross_rows([
        {
            **base, "fast_obs_ts_utc": utc(10, 10).isoformat(),
            "fast_detect_ts_utc": utc(10, 11).isoformat(), "fast_temp_unit": 20.5,
        },
        {
            **base, "fast_obs_ts_utc": utc(10, 20).isoformat(),
            "fast_detect_ts_utc": utc(10, 21).isoformat(), "fast_temp_unit": 20.7,
        },
    ])

    assert len(rows) == 1
    assert rows[0]["confirmation_first_margin"] == 0.5
    assert rows[0]["confirmation_latest_margin"] == 0.7


def test_persistent_cross_blocks_outside_next_metar_clock_window() -> None:
    base = {
        "city": "Test", "target_date": "2026-07-15", "fast_source": "fast",
        "previous_market_bracket": "20", "previous_market_bracket_upper": 20,
        "fast_implies_market_cross": True, "next_metar_market_crossed": True,
        "next_metar_report_clock_distance_min": 21.0,
    }

    rows = persistent_cross_rows([
        {
            **base, "fast_obs_ts_utc": utc(10, 10).isoformat(),
            "fast_detect_ts_utc": utc(10, 11).isoformat(), "fast_temp_unit": 20.5,
        },
        {
            **base, "fast_obs_ts_utc": utc(10, 20).isoformat(),
            "fast_detect_ts_utc": utc(10, 21).isoformat(), "fast_temp_unit": 20.7,
        },
    ])

    assert rows == []


def test_market_cross_uses_two_degree_range_upper_edge() -> None:
    rows = [{
        "city": "Test", "target_date": "2026-07-15",
        "previous_metar_running_max_round": 90,
        "fast_temp_round": 91, "next_metar_temp_round": 91,
    }]

    annotate_market_brackets(rows, {("Test", "2026-07-15"): ["88-89", "90-91", "92-93"]})

    assert rows[0]["previous_market_bracket"] == "90-91"
    assert rows[0]["previous_market_bracket_upper"] == 91
    assert rows[0]["fast_implies_market_cross"] is False


def test_exact_range_and_open_ended_brackets() -> None:
    assert parse_bracket_contains("98-99", 99)
    assert parse_bracket_contains("30+", 31)
    assert parse_bracket_contains("27", 27)
    assert not parse_bracket_contains("98-99", 100)


def test_jsonl_reader_ignores_incomplete_trailing_record(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"ok": 1}\n{"partial":')

    assert list(iter_jsonl(path)) == [{"ok": 1}]


def test_reference_events_read_dated_source_event_shards(tmp_path) -> None:
    root = tmp_path / "source_events"
    shard = root / "2026-07-15" / "sources.jsonl"
    shard.parent.mkdir(parents=True)
    shard.write_text(json.dumps({
        "city": "Test",
        "source": "aviationweather_metar",
        "source_report_ts_utc": "2026-07-15T10:00:00Z",
        "local_detect_ts_utc": "2026-07-15T10:01:00Z",
        "temp_c": 20.0,
    }) + "\n")

    awc, synoptic = load_reference_events(
        root,
        {"Test": {"timezone_name": "UTC", "unit": "C"}},
    )

    assert len(awc["Test"]) == 1
    assert synoptic == {}


def test_settlement_label_uses_final_winning_bracket() -> None:
    rows = [{"city": "Test", "target_date": "2026-07-15", "previous_metar_running_max_round": 20}]

    annotate_settlement_labels(rows, {("Test", "2026-07-15"): "21"})

    assert rows[0]["settlement_crossed_previous_max"] is True
