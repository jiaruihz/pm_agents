from __future__ import annotations

from datetime import datetime, timezone

from weather_data_feed import (
    ForecastFetchResult,
    city_local_datetime,
    city_local_date,
    city_scan_dates,
    build_taf_signal,
    build_vertical_profile_signal,
    fetch_open_meteo_historical_forecast,
    fetch_open_meteo_multi_model,
    fetch_open_meteo_previous_runs,
    fetch_open_meteo_single_run,
    forecast_enrichment_records,
    index_forecast_enrichment,
    load_city_configs,
    load_source_profiles,
    local_settle_utc,
    market_snapshot_record,
    normalize_snapshot_record,
    parse_market_event_date,
    target_day_hourly_summary,
)
from weather_data_feed import forecast_sources
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


def test_market_event_date_parses_slug_and_question():
    assert (
        parse_market_event_date(
            {"event_slug": "highest-temperature-in-cape-town-on-june-28-2026", "target_date": "2026-06-28"}
        )
        == "2026-06-28"
    )
    assert (
        parse_market_event_date({"question": "Will the highest temperature in Cape Town be 15°C on June 28?"}, target_year=2026)
        == "2026-06-28"
    )


def test_legacy_strategy_imports_reexport_data_module():
    from src.strategies.weather_edge_v1.official_observation_feed.source_registry import (
        DEFAULT_RESEARCH_REGISTRY_JSON,
        DEFAULT_SOURCE_PROFILES_JSON,
        load_source_profiles as legacy_load_source_profiles,
    )
    from src.strategies.weather_edge_v1.tools.official_observation_clock import (
        city_timezone_name as legacy_city_timezone_name,
    )

    assert DEFAULT_RESEARCH_REGISTRY_JSON.name == "2026-06-14-settlement-source-registry-v0.json"
    assert DEFAULT_SOURCE_PROFILES_JSON.name == "source_profiles.json"
    assert "weather_data_feed" in str(DEFAULT_SOURCE_PROFILES_JSON)
    assert legacy_load_source_profiles()["Shanghai"] == load_source_profiles()["Shanghai"]
    assert legacy_city_timezone_name("Helsinki") == "Europe/Helsinki"


def test_open_meteo_multi_model_parser_tracks_model_spread_and_hash(monkeypatch):
    payload = {
        "latitude": 31.14,
        "longitude": 121.81,
        "timezone": "Asia/Shanghai",
        "utc_offset_seconds": 28800,
        "daily": {
            "time": ["2026-07-07"],
            "temperature_2m_max_ecmwf_ifs025": [95.0],
            "temperature_2m_max_gfs_seamless": [97.5],
            "temperature_2m_max_ncep_hrrr_conus": [96.0],
        },
        "hourly": {
            "time": ["2026-07-07T13:00", "2026-07-07T14:00"],
            "temperature_2m_ecmwf_ifs025": [94.0, 95.0],
            "temperature_2m_gfs_seamless": [96.0, 97.5],
        },
    }

    class Response:
        def json(self):
            return payload

    monkeypatch.setattr(forecast_sources, "_http_get", lambda *_args, **_kwargs: Response())

    result = fetch_open_meteo_multi_model(31.14, 121.81, forecast_days=1)

    assert result.status == "ok"
    day = result.payload["daily"]["2026-07-07"]
    assert day["models"]["ECMWF"] == 95.0
    assert day["models"]["GFS"] == 97.5
    assert day["model_spread"] == 2.5
    assert set(result.payload["hourly_values_hash_by_model"]) == {"ECMWF", "GFS"}


def test_open_meteo_single_run_records_pit_run_metadata(monkeypatch):
    calls = []
    payload = {
        "latitude": 31.14,
        "longitude": 121.81,
        "timezone": "Asia/Shanghai",
        "utc_offset_seconds": 28800,
        "hourly": {
            "time": ["2026-07-07T13:00", "2026-07-07T14:00"],
            "temperature_2m": [95.0, 96.0],
        },
    }

    class Response:
        def json(self):
            return payload

    def fake_http_get(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return Response()

    monkeypatch.setattr(forecast_sources, "_http_get", fake_http_get)

    result = fetch_open_meteo_single_run(
        31.14,
        121.81,
        model="ecmwf_ifs025",
        run="2026-07-07T00:00",
        forecast_days=2,
        timezone_name="Asia/Shanghai",
        target_date="2026-07-07",
        decision_time_utc="2026-07-07T06:00:00+00:00",
    )

    assert calls[0][0] == forecast_sources.OPEN_METEO_SINGLE_RUN_API
    assert calls[0][1]["run"] == "2026-07-07T00:00"
    assert calls[0][1]["models"] == "ecmwf_ifs025"
    assert result.source_key == "open_meteo_single_run"
    assert result.payload["endpoint_kind"] == "single_run"
    assert result.payload["issue_time_utc"] == "2026-07-07T00:00"
    assert result.payload["decision_time_utc"] == "2026-07-07T06:00:00+00:00"
    assert result.payload["target_day_hourly"]["forecast_max"] == 96.0
    assert result.metadata["model_min_date"] == "2024-03-01"


def test_open_meteo_previous_runs_and_historical_forecast_are_explicit_fallbacks(monkeypatch):
    calls = []
    payload = {
        "timezone": "UTC",
        "hourly": {
            "time": ["2026-07-07T12:00"],
            "temperature_2m": [90.0],
        },
    }

    class Response:
        def json(self):
            return payload

    def fake_http_get(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return Response()

    monkeypatch.setattr(forecast_sources, "_http_get", fake_http_get)

    previous = fetch_open_meteo_previous_runs(
        40.77,
        -73.87,
        model="gfs_seamless",
        forecast_days=2,
        target_date="2026-07-07",
    )
    historical = fetch_open_meteo_historical_forecast(
        40.77,
        -73.87,
        model="gfs_seamless",
        start_date="2026-07-07",
        end_date="2026-07-07",
        target_date="2026-07-07",
    )

    assert calls[0][0] == forecast_sources.OPEN_METEO_FORECAST_API
    assert calls[0][1]["previous_runs"] == "true"
    assert previous.payload["endpoint_kind"] == "previous_runs"
    assert calls[1][0] == forecast_sources.OPEN_METEO_HISTORICAL_FORECAST_API
    assert historical.payload["endpoint_kind"] == "historical_forecast"
    assert historical.metadata["pit_exact"] is False


def test_weather_context_vertical_profile_and_hourly_peak_features():
    hourly = {
        "time": ["2026-07-07T12:00", "2026-07-07T13:00", "2026-07-07T14:00"],
        "temperature_2m": [90.0, 93.0, 92.0],
        "cape": [50.0, 800.0, 400.0],
        "convective_inhibition": [-5.0, -60.0, -10.0],
        "lifted_index": [1.0, -2.0, 0.0],
        "boundary_layer_height": [500.0, 1600.0, 1200.0],
        "wind_speed_10m": [5.0, 6.0, 5.0],
        "wind_direction_10m": [180.0, 180.0, 180.0],
        "wind_speed_180m": [14.0, 15.0, 14.0],
        "wind_direction_180m": [270.0, 270.0, 270.0],
    }

    summary = target_day_hourly_summary(hourly, "2026-07-07")
    signal = build_vertical_profile_signal(
        hourly,
        target_date="2026-07-07",
        local_hour=12,
        first_peak_hour=summary["first_peak_hour_local"],
        last_peak_hour=summary["last_peak_hour_local"],
    )

    assert summary["forecast_max"] == 93.0
    assert summary["first_peak_hour_local"] == 13
    assert signal["available"] is True
    assert signal["suppression_risk"] == "high"
    assert signal["trigger_risk"] == "high"
    assert signal["mixing_strength"] == "strong"
    assert signal["heating_setup"] == "suppressed"


def test_taf_signal_extracts_peak_window_cloud_rain_and_wind_shift():
    taf_payload = {
        "issue_time": "2026-07-07T06:00:00Z",
        "valid_time_from": "2026-07-07T06:00:00Z",
        "valid_time_to": "2026-07-08T06:00:00Z",
        "raw_taf": "TAF ZSPD 070000Z 0700/0800 18008KT BKN030 TEMPO 0704/0708 TSRA BKN020 FM070500 35010KT SCT030",
    }

    signal = build_taf_signal(
        taf_payload,
        target_date="2026-07-07",
        utc_offset_seconds=28800,
        first_peak_hour=12,
        last_peak_hour=13,
    )

    assert signal["available"] is True
    assert signal["suppression_level"] == "high"
    assert signal["disruption_level"] == "high"
    assert signal["low_ceiling_ft"] == 2000
    assert signal["wind_shift"] is True


def test_forecast_enrichment_cache_indexes_latest_city_date_record():
    payload = {
        "records": [
            {
                "city": "Shanghai",
                "target_date": "2026-07-07",
                "snapshot_ts_utc": "2026-07-07T01:00:00Z",
                "status": "partial",
            },
            {
                "city": "Shanghai",
                "target_date": "2026-07-07",
                "snapshot_ts_utc": "2026-07-07T02:00:00Z",
                "status": "ok",
                "open_meteo_multi_model": {"target_date": {"model_spread": 3.4}},
            },
        ]
    }

    rows = forecast_enrichment_records(payload)
    indexed = index_forecast_enrichment(payload)

    assert rows[0]["schema_version"] == "weather_forecast_enrichment_v1"
    assert indexed[("Shanghai", "2026-07-07")]["status"] == "ok"
    assert indexed[("Shanghai", "2026-07-07")]["open_meteo_multi_model"]["target_date"]["model_spread"] == 3.4
