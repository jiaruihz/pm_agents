from __future__ import annotations

from scripts.analysis.live_performance.weather_cross_no_accuracy_attribution_v1 import (
    block_bootstrap_delta,
    dedupe_event_rows,
)


def test_dedupe_event_rows_keeps_first_condition_expression() -> None:
    rows = [
        {
            "status": "cross_candidate",
            "condition_id": "c1",
            "city": "Tokyo",
            "target_date": "2026-08-11",
            "t_minus_1_no_bracket_c": 30,
            "source_detect_ts_utc": "2026-08-11T01:01:00Z",
            "source_market_temp": 31.0,
        },
        {
            "status": "cross_candidate",
            "condition_id": "c1",
            "city": "Tokyo",
            "target_date": "2026-08-11",
            "t_minus_1_no_bracket_c": 30,
            "source_detect_ts_utc": "2026-08-11T01:00:00Z",
            "source_market_temp": 30.8,
        },
    ]

    result = dedupe_event_rows(rows, "2026-08-01", "2026-08-11")

    assert len(result) == 1
    assert result[0]["_detected_at_utc"] == "2026-08-11T01:00:00+00:00"
    assert abs(result[0]["_cross_margin_native"] - 0.8) < 1e-9


def test_block_bootstrap_delta_reports_observed_change() -> None:
    earlier = [
        {"target_date": "2026-08-01", "correct": 0},
        {"target_date": "2026-08-02", "correct": 1},
    ]
    later = [
        {"target_date": "2026-08-03", "correct": 1},
        {"target_date": "2026-08-04", "correct": 1},
    ]

    result = block_bootstrap_delta(earlier, later, iterations=100, seed=7)

    assert result["delta"] == 0.5
    assert result["iterations"] == 100
