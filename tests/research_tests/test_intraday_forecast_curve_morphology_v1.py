from scripts.analysis.reheat_risk import (
    research_intraday_forecast_curve_morphology_v1 as study,
)


def test_detects_overnight_peak_with_afternoon_lobe() -> None:
    values = [
        86.0,
        82.9,
        80.2,
        78.4,
        76.9,
        75.8,
        75.1,
        74.7,
        74.5,
        74.4,
        74.6,
        75.4,
        77.5,
        80.2,
        82.6,
        84.7,
        85.2,
        85.6,
        84.7,
        82.8,
        80.4,
        77.8,
        76.5,
        75.7,
    ]

    result = study.curve_morphology(list(enumerate(values)))

    assert result["curve_shape"] == "overnight_peak_afternoon_lobe"
    assert result["global_peak_hour"] == 0
    assert result["afternoon_peak_hour"] == 17
    assert abs(result["afternoon_lobe_gap_f"] - 0.4) < 1e-9


def test_detects_canonical_afternoon_single_peak() -> None:
    values = [
        55.0,
        54.0,
        53.0,
        52.0,
        51.0,
        50.0,
        51.0,
        53.0,
        56.0,
        60.0,
        64.0,
        68.0,
        72.0,
        75.0,
        78.0,
        80.0,
        78.0,
        75.0,
        71.0,
        67.0,
        63.0,
        60.0,
        58.0,
        56.0,
    ]

    result = study.curve_morphology(list(enumerate(values)))

    assert result["curve_shape"] == "canonical_afternoon_single"
    assert result["global_peak_hour"] == 15


def test_decision_relative_alias_uses_future_exit_boundary() -> None:
    values = [
        86.0,
        82.9,
        80.2,
        78.4,
        76.9,
        75.8,
        75.1,
        74.7,
        74.5,
        74.4,
        74.6,
        75.4,
        77.5,
        80.2,
        82.6,
        84.7,
        85.2,
        85.6,
        84.7,
        82.8,
        80.4,
        77.8,
        76.5,
        75.7,
    ]
    record = {
        "city": "Chengdu",
        "target_date": "2026-07-27",
        "checkpoint_key": "Chengdu|2026-07-27|16",
        "decision_snapshot_ts_utc": "2026-07-27T08:44:37Z",
        "decision_hour_local_float": 16.73,
        "current_bracket": "29",
        "d1_bracket": "30",
        "unit": "C",
        "hourly_curve": [
            {
                "time_local": f"2026-07-27T{hour:02d}:00",
                "temperature_f": temperature,
            }
            for hour, temperature in enumerate(values)
        ],
    }

    result = study.add_live_curve_fields(record)

    assert result["peak_clock_alias"] is True
    assert result["current_exit_threshold_native"] == 29.5
    assert result["future_curve_max_native"] > 29.5


def test_decision_relative_alias_excludes_past_current_hour_point() -> None:
    values = [
        70.0,
        69.0,
        68.0,
        67.0,
        66.0,
        65.0,
        66.0,
        68.0,
        72.0,
        76.0,
        80.0,
        84.0,
        88.0,
        86.0,
        84.0,
        83.5,
        80.0,
        78.0,
        76.0,
        74.0,
        73.0,
        72.0,
        71.0,
        70.0,
    ]
    record = {
        "city": "Example",
        "target_date": "2026-07-27",
        "checkpoint_key": "Example|2026-07-27|15",
        "decision_snapshot_ts_utc": "2026-07-27T07:44:00Z",
        "decision_hour_local_float": 15.73,
        "current_bracket": "29",
        "d1_bracket": "30",
        "unit": "C",
        "hourly_curve": [
            {
                "time_local": f"2026-07-27T{hour:02d}:00",
                "temperature_f": temperature,
            }
            for hour, temperature in enumerate(values)
        ],
    }

    result = study.add_live_curve_fields(record)

    assert result["future_curve_start_hour_local"] == 16
    assert result["future_curve_max_native"] < 29.5
    assert result["peak_clock_alias"] is False
