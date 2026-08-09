from __future__ import annotations

from weather_data_feed.observation_cache import index_observation_cache
from weather_data_feed.physical_features import observation_clock_features
from weather_feature_layer.builders import build_weather_state_frame


def _row(*, fetched: str, report: str, current: float, cached_age: float) -> dict[str, object]:
    return {
        "city": "London",
        "target_date": "2026-07-26",
        "status": "ok",
        "source": "aviationweather_metar",
        "station": "EGLL",
        "fetched_at_utc": fetched,
        "last_obs_utc": report,
        "current_temp_c": current,
        "running_max_c": current,
        "age_min": cached_age,
        "cadence_min": 30.0,
    }


def test_observation_index_uses_latest_pit_capture_not_input_order() -> None:
    cache = {
        "decision_as_of_utc": "2026-07-26T12:38:10Z",
        "records": [
            _row(
                fetched="2026-07-26T12:33:00Z",
                report="2026-07-26T12:20:00Z",
                current=26.0,
                cached_age=13.0,
            ),
            # The previous UTC day's final capture was appended after today's
            # history in the live runner and used to win by last-write order.
            _row(
                fetched="2026-07-25T23:58:00Z",
                report="2026-07-25T23:50:00Z",
                current=20.0,
                cached_age=8.0,
            ),
        ],
    }

    selected = index_observation_cache(cache)[("London", "2026-07-26")]

    assert selected["current_temp_c"] == 26.0
    assert selected["last_obs_utc"] == "2026-07-26T12:20:00Z"
    assert selected["obs_age_minutes"] == 18.0 + 10.0 / 60.0
    assert selected["obs_age_clock_source"] == "decision_asof_minus_source_report"


def test_weather_state_recomputes_age_at_decision_clock() -> None:
    frame = build_weather_state_frame(
        [
            {
                "city": "London",
                "target_date": "2026-07-26",
                "snapshot_ts_utc": "2026-07-26T12:38:10Z",
                "ts_local": "2026-07-26T13:38:10+01:00",
                "unit": "C",
            }
        ],
        {
            "records": [
                _row(
                    fetched="2026-07-25T23:58:00Z",
                    report="2026-07-25T23:50:00Z",
                    current=20.0,
                    cached_age=8.0,
                )
            ]
        },
        as_of_ts_utc="2026-07-26T12:38:10Z",
    )

    row = frame.iloc[0]
    assert row["obs_age_minutes"] == 768.0 + 10.0 / 60.0
    assert row["station_gap_state"] == "beyond_expected_cadence"


def test_weather_state_excludes_future_source_report_even_without_capture_clock() -> None:
    frame = build_weather_state_frame(
        [
            {
                "city": "London",
                "target_date": "2026-07-26",
                "snapshot_ts_utc": "2026-07-26T12:38:10Z",
                "ts_local": "2026-07-26T13:38:10+01:00",
                "unit": "C",
            }
        ],
        {
            "records": [
                _row(
                    fetched="",
                    report="2026-07-26T12:40:00Z",
                    current=27.0,
                    cached_age=0.0,
                )
            ]
        },
        as_of_ts_utc="2026-07-26T12:38:10Z",
    )

    assert frame.empty


def test_weather_state_tuple_index_preserves_key_metadata() -> None:
    frame = build_weather_state_frame(
        [
            {
                "city": "London",
                "target_date": "2026-07-26",
                "snapshot_ts_utc": "2026-07-26T12:38:10Z",
                "ts_local": "2026-07-26T13:38:10+01:00",
                "unit": "C",
            }
        ],
        {
            ("London", "2026-07-26"): {
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "EGLL",
                "fetched_at_utc": "2026-07-26T12:33:00Z",
                "last_obs_utc": "2026-07-26T12:20:00Z",
                "current_temp_c": 26.0,
                "running_max_c": 26.0,
            }
        },
        as_of_ts_utc="2026-07-26T12:38:10Z",
    )

    assert len(frame) == 1
    assert frame.iloc[0]["current_temp_c"] == 26.0


def test_physical_clock_recomputes_age_instead_of_reusing_cached_age() -> None:
    features = observation_clock_features(
        {
            "source_report_ts_utc": "2026-07-25T23:50:00Z",
            "decision_snapshot_ts_utc": "2026-07-26T12:38:10Z",
            "obs_age_minutes": 8.0,
            "expected_report_cadence": 30.0,
        }
    )

    assert features["obs_age_minutes"] == 768.0 + 10.0 / 60.0
