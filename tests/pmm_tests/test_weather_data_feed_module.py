from __future__ import annotations

from datetime import datetime, timezone

from weather_data_feed import (
    city_local_datetime,
    city_local_date,
    city_scan_dates,
    load_city_configs,
    load_source_profiles,
    local_settle_utc,
    market_snapshot_record,
    normalize_snapshot_record,
)
from weather_data_feed.city_calendar import station_timezone, timezone_label
from weather_data_feed.market_brackets import bracket_contains, parse_label_dict
from weather_data_feed.snapshot_protocol import validate_snapshot_record


class StationStub:
    city = "Helsinki"
    utc_offset = 2
    timezone_name = "Europe/Helsinki"


def test_city_calendar_uses_iana_timezone_and_dst():
    now = "2026-06-16T13:18:54Z"

    assert city_local_date("Helsinki", now).isoformat() == "2026-06-16"
    assert city_local_datetime("Helsinki", now).hour == 16
    assert datetime(2026, 6, 16, 13, 18, 54, tzinfo=timezone.utc).astimezone(station_timezone(StationStub())).hour == 16
    assert timezone_label(station_timezone(StationStub())) == "Europe/Helsinki"


def test_local_settle_utc_uses_dst_not_static_offset():
    assert local_settle_utc("Helsinki", "2026-06-16").isoformat() == "2026-06-16T19:00:00+00:00"
    assert local_settle_utc("NYC", "2026-06-19").isoformat() == "2026-06-20T02:00:00+00:00"


def test_city_scan_dates_are_per_city_not_machine_date():
    now = "2026-06-19T05:01:23Z"

    assert city_scan_dates("LA", now) == ["2026-06-18", "2026-06-19"]
    assert city_scan_dates("Denver", now) == ["2026-06-18", "2026-06-19"]
    assert city_scan_dates("NYC", now) == ["2026-06-19", "2026-06-20"]
    assert city_scan_dates("Shanghai", now) == ["2026-06-19", "2026-06-20"]


def test_source_profiles_live_in_data_module_and_cover_universe():
    profiles = load_source_profiles()

    assert len(profiles) == 52
    assert sum(profile.live_eligible for profile in profiles.values()) == 41
    assert profiles["Shanghai"].timezone_name == "Asia/Shanghai"
    assert profiles["Shanghai"].official_station_or_feed == "ZSPD"
    assert profiles["Helsinki"].timezone_name == "Europe/Helsinki"
    assert not [profile.city for profile in profiles.values() if profile.timezone_name == "UTC"]


def test_source_policy_still_builds_live_configs():
    configs = load_city_configs(include_station_diff=False, only_cities={"Shanghai", "HongKong"})

    assert [cfg.city for cfg in configs] == ["Shanghai"]
    assert configs[0].official_icao == "ZSPD"
    assert configs[0].live_observation_source == "aviationweather_metar"


def test_market_bracket_helpers_are_data_module_public_api():
    parsed = parse_label_dict("94+", "Will the highest temperature be 94°F or higher?")

    assert parsed == {"low": 94.0, "high": None, "bottom": False, "top": True, "label": "94+"}
    assert bracket_contains(parsed, 96)


def test_snapshot_protocol_normalizes_legacy_aliases_and_dates():
    row = {
        "city": "LA",
        "event_date": "2026-06-19",
        "ts_utc": "2026-06-19T05:01:23Z",
        "clob_token_id": "token-1",
        "outcome": "72-73",
    }

    normalized = normalize_snapshot_record(row)

    assert normalized["target_date"] == "2026-06-19"
    assert normalized["market_local_date"] == "2026-06-19"
    assert normalized["city_local_date_at_snapshot"] == "2026-06-18"
    assert normalized["snapshot_ts_utc"] == "2026-06-19T05:01:23Z"
    assert normalized["token_id"] == "token-1"
    assert normalized["bracket"] == "72-73"
    validate_snapshot_record(normalized)

    record = market_snapshot_record(row)
    assert record.city == "LA"
    assert record.target_date == "2026-06-19"
    assert record.city_local_date_at_snapshot == "2026-06-18"


def test_legacy_strategy_imports_reexport_data_module():
    from src.strategies.weather_edge_v1.official_observation_feed.source_registry import (
        DEFAULT_RESEARCH_REGISTRY_JSON,
        DEFAULT_SOURCE_PROFILES_JSON,
        load_source_profiles as legacy_load_source_profiles,
    )
    from src.strategies.weather_edge_v1.tools.official_observation_clock import (
        city_timezone_name as legacy_city_timezone_name,
    )

    assert DEFAULT_RESEARCH_REGISTRY_JSON.exists()
    assert DEFAULT_SOURCE_PROFILES_JSON.name == "source_profiles.json"
    assert "weather_data_feed" in str(DEFAULT_SOURCE_PROFILES_JSON)
    assert legacy_load_source_profiles()["Shanghai"] == load_source_profiles()["Shanghai"]
    assert legacy_city_timezone_name("Helsinki") == "Europe/Helsinki"
