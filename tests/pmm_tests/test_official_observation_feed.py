from __future__ import annotations

import json

from src.strategies.weather_edge_v1.official_observation_feed.market_brackets import (
    bracket_contains,
    parse_label_dict,
    parse_market_bracket,
)
from src.strategies.weather_edge_v1.official_observation_feed.source_registry import (
    load_source_profiles,
)


def test_market_bracket_parser_keeps_positive_ranges_and_tails():
    parsed = parse_market_bracket("74-75", "Will the highest temperature be between 74-75°F?")
    assert parsed is not None
    assert parsed.low == 74.0
    assert parsed.high == 75.0
    assert parsed.contains(74)
    assert parsed.contains(75)
    assert not parsed.contains(76)

    triple = parse_market_bracket("100-101", "Will the highest temperature be between 100-101°F?")
    assert triple is not None
    assert triple.low == 100.0
    assert triple.high == 101.0
    assert triple.contains(100.5)
    assert not triple.contains(99)

    top = parse_label_dict("94+", "Will the highest temperature be 94°F or higher?")
    assert top == {"low": 94.0, "high": None, "bottom": False, "top": True, "label": "94+"}
    assert bracket_contains(top, 96)

    bottom = parse_label_dict("72", "Will the highest temperature be 72°F or below?")
    assert bottom == {"low": None, "high": 72.0, "bottom": True, "top": False, "label": "72"}
    assert bracket_contains(bottom, 71)


def test_source_registry_marks_confirmed_and_blocked_profiles(tmp_path):
    registry = {
        "registry": [
            {
                "city": "Paris",
                "unit": "C",
                "settlement_source_class": "official_station_diff_confirmed",
                "official_station_or_feed": "LFPB",
                "mapping_rule": "whole-degree official station max",
            },
            {
                "city": "Seoul",
                "unit": "C",
                "settlement_source_class": "blocked_unresolved_settlement_basis",
                "official_station_or_feed": "unknown_effective_source",
                "mapping_rule": "unresolved",
                "downstream_action": "exclude until root cause is found",
            },
        ]
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    profiles = load_source_profiles(path)

    assert profiles["Paris"].official_station_or_feed == "LFPB"
    assert profiles["Paris"].primary_source == "aviationweather_metar"
    assert profiles["Paris"].fallback_sources == ("iem_asos",)
    assert profiles["Paris"].live_eligible
    assert not profiles["Seoul"].live_eligible
    assert profiles["Seoul"].blocked_reason == "exclude until root cause is found"


def test_default_source_profiles_cover_current_city_universe():
    profiles = load_source_profiles()

    assert len(profiles) == 52
    assert sum(profile.live_eligible for profile in profiles.values()) == 41
    assert sum(profile.primary_source == "aviationweather_metar" for profile in profiles.values()) == 41
    assert not [profile.city for profile in profiles.values() if profile.timezone_name == "UTC"]

    assert profiles["Austin"].primary_source == "synopticdata_timeseries"
    assert profiles["Dallas"].primary_source == "synopticdata_timeseries"
    assert profiles["Houston"].primary_source == "synopticdata_timeseries"
    assert profiles["Denver"].primary_source == "aviationweather_metar"
    assert profiles["Paris"].official_station_or_feed == "LFPB"
    assert profiles["Paris"].live_eligible
    assert profiles["MexicoCity"].primary_source == "aviationweather_metar"
    assert not profiles["MexicoCity"].live_eligible
    assert profiles["Seoul"].settlement_source_class == "default_source_watchlist"
    assert profiles["Seoul"].primary_source == "aviationweather_metar"
    assert not profiles["Seoul"].live_eligible
    assert profiles["Shenzhen"].settlement_source_class == "default_source_watchlist"
    assert profiles["Shenzhen"].primary_source == "aviationweather_metar"
    assert not profiles["Shenzhen"].live_eligible
    assert profiles["HongKong"].official_station_or_feed == "HKO"
    assert not profiles["HongKong"].live_eligible
    assert profiles["Moscow"].settlement_source_class == "non_wu_source_by_rules"
    assert profiles["Moscow"].official_station_or_feed.endswith("site=UUWW")
    assert profiles["Moscow"].alignment_days == 62
    assert profiles["Moscow"].alignment_matches == 62
    assert profiles["Moscow"].primary_source == "synopticdata_timeseries"
    assert not profiles["Moscow"].live_eligible
    assert profiles["Boston"].timezone_name == "America/New_York"
    assert not profiles["Boston"].live_eligible
