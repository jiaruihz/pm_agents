from weather_data_feed.korea_amos_features import (
    aggregate_amos_observation,
    rolling_path_features,
)


def _row(
    ts: str,
    temp: float,
    *,
    runway: str = "15R/33L",
    preferred: bool = True,
    dewpoint: float = 24.0,
) -> dict:
    return {
        "city": "Seoul",
        "target_date": "2026-07-30",
        "source": "amos_runway",
        "station": "RKSI",
        "observation_time_utc": ts,
        "source_first_seen_at_utc": ts,
        "temp_c": temp,
        "dewpoint_c": dewpoint,
        "wind_speed": 7.0,
        "runway": runway,
        "is_preferred_temperature_runway": preferred,
        "raw_metar": "METAR RKSI 300300Z 25008KT 9999 -RA SCT010 BKN030 29/24 Q1008=",
        "metar_temp_c": 29.0,
    }


def test_aggregate_amos_observation_unifies_runways_and_physical_context() -> None:
    rows = [
        _row("2026-07-30T03:00:00+00:00", 29.4),
        _row(
            "2026-07-30T03:00:00+00:00",
            29.7,
            runway="16L/34R",
            preferred=False,
            dewpoint=24.2,
        ),
    ]
    state = aggregate_amos_observation(rows)
    assert state["feature_grain"] == "city_target_date_distinct_source_observation_first_seen"
    assert state["source_temp_c"] == 29.7
    assert state["preferred_runway_temp_c"] == 29.4
    assert state["runway_count"] == 2
    assert state["runway_temp_spread_c"] == 0.3
    assert state["relative_humidity_pct"] is not None
    assert state["precip_state"] == "rain_or_drizzle"
    assert state["cloud_layer_count"] == 2
    assert state["ceiling_ft_agl"] == 3000
    assert state["metar_wind_dir_deg"] == 250.0


def test_rolling_path_features_use_real_timestamp_windows() -> None:
    states = []
    for minute, temp in ((0, 28.0), (5, 28.4), (10, 28.8), (15, 28.6)):
        state = aggregate_amos_observation(
            [_row(f"2026-07-30T03:{minute:02d}:00+00:00", temp)]
        )
        states.append(state)
    features = rolling_path_features(
        states[-1],
        states[:-1],
        windows_minutes=(5, 15, 30, 60),
    )
    assert features["path_windows"]["5m"]["temp_delta_c"] == -0.2
    assert features["path_windows"]["15m"]["temp_delta_c"] == 0.6
    assert features["path_windows"]["30m"]["history_complete"] is False
    assert features["source_running_max_c"] == 28.8
    assert features["distance_below_source_running_max_c"] == 0.2
    assert features["minutes_since_source_running_max"] == 5.0
