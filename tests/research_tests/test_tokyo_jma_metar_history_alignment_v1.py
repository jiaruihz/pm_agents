from __future__ import annotations

from datetime import date, datetime, timezone

from scripts.analysis.market_structure_edge.research_tokyo_jma_metar_history_alignment_v1 import (
    build_alignment_rows,
    month_ranges,
    round_native_c,
)


UTC = timezone.utc


def test_month_ranges_cover_each_date_once() -> None:
    assert month_ranges(date(2024, 12, 30), date(2025, 2, 2)) == [
        (date(2024, 12, 30), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 2)),
    ]


def test_native_rounding_is_explicit_half_up() -> None:
    assert round_native_c(29.4) == 29
    assert round_native_c(29.5) == 30


def test_future_metar_label_is_strictly_after_jma_observation() -> None:
    jma = [
        {
            "observation_time_utc": "2026-07-01T01:10:00+00:00",
            "temp_c": "29.6",
        }
    ]
    metar = [
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            "temp_c": 29.0,
            "routine_clock": True,
            "local_date": "2026-07-01",
        },
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 10, tzinfo=UTC),
            "temp_c": 99.0,
            "routine_clock": False,
            "local_date": "2026-07-01",
        },
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 30, tzinfo=UTC),
            "temp_c": 30.0,
            "routine_clock": True,
            "local_date": "2026-07-01",
        },
    ]

    comparisons, horizons = build_alignment_rows(jma, metar)

    # Equal observation clocks do not reveal which archive was published first.
    assert comparisons[0]["prior_metar_temp_c"] == 29.0
    assert comparisons[0]["next_routine_metar_temp_c"] == 30.0
    row_30 = next(row for row in horizons if row["horizon_min"] == 30)
    assert row_30["future_metar_count"] == 1
    assert row_30["future_metar_max_c"] == 30.0
