from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_fast_source_stale_book_observer import (
    bracket_from_question,
    bracket_lookup,
    build_market_index,
    market_date_has_tokens,
    relative_market_token,
    source_market_episode_key,
    source_latest_by_city,
    target_date_for_city,
    temperature_event_slug,
)
from weather_data_feed.fast_event_source_policy import (
    load_fast_event_source_profiles,
    market_value_from_temp_c,
)


def test_fast_event_profiles_are_calibration_only_and_unit_aware():
    profiles = load_fast_event_source_profiles()

    assert len(profiles) == 22
    assert len({profile.city for profile in profiles.values()}) == 21
    assert not any(profile.live_eligible for profile in profiles.values())
    assert profiles[("Atlanta", "noaa_madis_hfmetar")].market_unit == "F"
    assert profiles[("HongKong", "hko_obs")].bracket_rounding == "floor"
    assert profiles[("Shenzhen", "hko_obs")].source_basis_class == "cross_station_proxy"


def test_market_value_conversion_uses_profile_unit_and_rounding():
    profiles = load_fast_event_source_profiles()

    assert market_value_from_temp_c(30.0, profiles[("Atlanta", "noaa_madis_hfmetar")]) == 86
    assert market_value_from_temp_c(30.9, profiles[("HongKong", "hko_obs")]) == 30
    assert market_value_from_temp_c(30.5, profiles[("Tokyo", "jma_amedas")]) == 31


def test_target_date_routes_per_city_local_calendar():
    now = datetime(2026, 7, 13, 16, 49, tzinfo=timezone.utc)

    assert target_date_for_city("Atlanta", now) == "2026-07-13"
    assert target_date_for_city("Tokyo", now) == "2026-07-14"
    assert target_date_for_city("Atlanta", now, "2026-07-20") == "2026-07-20"


def test_source_latest_keeps_simultaneous_local_dates_and_market_units(tmp_path):
    profiles = load_fast_event_source_profiles()
    now = datetime(2026, 7, 13, 16, 49, tzinfo=timezone.utc)
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Atlanta",
                        "target_date": "2026-07-13",
                        "source": "noaa_madis_hfmetar",
                        "temp_c": 30.0,
                        "observation_time_utc": "2026-07-13T16:45:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:46:00Z",
                    },
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 30.5,
                        "observation_time_utc": "2026-07-13T16:40:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:41:00Z",
                    },
                    {
                        "city": "Atlanta",
                        "target_date": "2026-07-14",
                        "source": "noaa_madis_hfmetar",
                        "temp_c": 40.0,
                        "observation_time_utc": "2026-07-13T16:45:00Z",
                        "local_detect_ts_utc": "2026-07-13T16:46:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = source_latest_by_city(
        path,
        "",
        {"noaa_madis_hfmetar", "jma_amedas"},
        profiles,
        now,
    )

    assert set(rows) == {("Atlanta", "2026-07-13"), ("Tokyo", "2026-07-14")}
    assert rows[("Atlanta", "2026-07-13")]["source_market_value"] == 86
    assert rows[("Tokyo", "2026-07-14")]["source_market_value"] == 31


def test_source_latest_prefers_newer_observation_even_when_temperature_falls(tmp_path):
    profiles = load_fast_event_source_profiles()
    now = datetime(2026, 7, 14, 2, 30, tzinfo=timezone.utc)
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 33.0,
                        "observation_time_utc": "2026-07-14T02:10:00Z",
                        "local_detect_ts_utc": "2026-07-14T02:11:00Z",
                    },
                    {
                        "city": "Tokyo",
                        "target_date": "2026-07-14",
                        "source": "jma_amedas",
                        "temp_c": 32.0,
                        "observation_time_utc": "2026-07-14T02:20:00Z",
                        "local_detect_ts_utc": "2026-07-14T02:21:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = source_latest_by_city(path, "", {"jma_amedas"}, profiles, now)

    assert rows[("Tokyo", "2026-07-14")]["temp_c"] == 32.0
    assert rows[("Tokyo", "2026-07-14")]["source_obs_ts_utc"] == "2026-07-14T02:20:00+00:00"


def test_market_index_does_not_collide_across_target_dates(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {"city": "Atlanta", "target_date": "2026-07-13", "bracket": "86", "yes_token_id": "old"},
                    {"city": "Atlanta", "target_date": "2026-07-14", "bracket": "86", "yes_token_id": "new"},
                ]
            }
        ),
        encoding="utf-8",
    )

    index = build_market_index(path, {"2026-07-13", "2026-07-14"})

    assert index[("Atlanta", "2026-07-13", "86")].yes_token_id == "old"
    assert index[("Atlanta", "2026-07-14", "86")].yes_token_id == "new"


def test_market_ladder_uses_real_range_brackets_not_numeric_plus_minus_one(tmp_path):
    path = tmp_path / "paper.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "74-75"},
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "76-77"},
                    {"city": "SanFrancisco", "target_date": "2026-07-13", "bracket": "78-79"},
                ]
            }
        ),
        encoding="utf-8",
    )
    index = build_market_index(path, {"2026-07-13"})

    assert bracket_lookup(index, "SanFrancisco", "2026-07-13", 76).bracket == "76-77"
    assert bracket_lookup(index, "SanFrancisco", "2026-07-13", 77).bracket == "76-77"
    assert relative_market_token(index, "SanFrancisco", "2026-07-13", 77, -1).bracket == "74-75"
    assert relative_market_token(index, "SanFrancisco", "2026-07-13", 77, 1).bracket == "78-79"
    assert market_date_has_tokens(index, "SanFrancisco", "2026-07-13")
    assert not market_date_has_tokens(index, "SanFrancisco", "2026-07-14")


def test_event_key_deduplicates_repeated_source_reports_within_same_market_bracket():
    first = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "76-77")
    repeated = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "76-77")
    next_bracket = source_market_episode_key("SanFrancisco", "2026-07-13", "noaa_madis_hfmetar", "78-79")

    assert first == repeated
    assert first != next_bracket


def test_gamma_market_parsing_handles_fahrenheit_ranges_and_minimum_slugs():
    assert bracket_from_question("Will the highest temperature in SF be between 76-77°F?") == "76-77"
    assert bracket_from_question("Will the highest temperature in Atlanta be 95°F or higher?") == "95+"
    assert temperature_event_slug("HongKong", "2026-07-14", "min").startswith(
        "lowest-temperature-in-hong-kong-on-july-14-2026"
    )
