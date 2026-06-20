from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scripts.ops import weather_theta_current_yes_tiny_live as live


def _record(city: str, bracket: str, ask: float = 0.78, event_date: str = "2026-06-16") -> dict:
    metar_defaults = {
        "LA": {
            "metar_current_max_f": 70.0,
            "metar_latest_temp_f": 68.0,
            "metar_latest_ts_utc": f"{event_date}T20:20:00+00:00",
        },
        "NYC": {
            "metar_current_max_f": 80.0,
            "metar_latest_temp_f": 79.0,
            "metar_latest_ts_utc": f"{event_date}T20:20:00+00:00",
        },
        "Helsinki": {
            "metar_current_max_f": 68.0,
            "metar_latest_temp_f": 66.2,
            "metar_latest_ts_utc": f"{event_date}T13:10:00+00:00",
        },
        "Tokyo": {
            "metar_current_max_f": 68.0,
            "metar_latest_temp_f": 66.2,
            "metar_latest_ts_utc": f"{event_date}T04:00:00+00:00",
        },
    }
    return {
        "city": city,
        "event_date": event_date,
        "market_id": f"{city}-{event_date}-{bracket}",
        "condition_id": f"{city}-{event_date}-{bracket}",
        "bracket": bracket,
        "question": f"Will {city} hit {bracket} C?",
        "yes_best_ask": ask,
        "yes_ask_size": 20,
        "no_best_ask": 0.90,
        "no_ask_size": 20,
        "yes_token_id": f"yes-{city}-{event_date}-{bracket}",
        "event_slug": f"{city.lower()}-{event_date}-{bracket}",
        "metar_obs_count_today": 10,
        "metar_source": "test_snapshot_metar",
        **metar_defaults.get(
            city,
            {
                "metar_current_max_f": 70.0,
                "metar_latest_temp_f": 68.0,
                "metar_latest_ts_utc": f"{event_date}T04:00:00+00:00",
            },
        ),
    }


def test_parse_label_keeps_positive_fahrenheit_ranges():
    parsed = live.parse_label("74-75", "Will the highest temperature in New York City be between 74-75°F?")

    assert parsed == {"low": 74.0, "high": 75.0, "bottom": False, "top": False, "label": "74-75"}
    assert live.bracket_contains(parsed, 74)
    assert live.bracket_contains(parsed, 75)


def test_aviationweather_obs_parses_cloud_cover(monkeypatch):
    def fake_fetch_json(_url, _params):
        return [
            {
                "reportTime": "2026-06-18T06:00:00.000Z",
                "temp": 27,
                "dewp": 23,
                "relh": 78.8,
                "wdir": 70,
                "wspd": 6,
                "cover": "FEW",
                "rawOb": "ZSPD 180600Z 07003MPS 020V120 9999 FEW015 27/23 Q1008 NOSIG",
            }
        ]

    monkeypatch.setattr(live, "fetch_json", fake_fetch_json)

    rows = live.aviationweather_obs("ZSPD", ZoneInfo("Asia/Shanghai"), datetime(2026, 6, 18).date())

    assert rows[0]["sky"] == live.SKY_CODE["FEW"]
    assert rows[0]["drct"] == 70


def test_snapshot_freshness_uses_file_mtime_when_generation_finishes_late(tmp_path):
    snapshot_path = tmp_path / "snapshot_20260619_2138.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    logical_ts = datetime(2026, 6, 19, 13, 38, 43, tzinfo=timezone.utc)
    mtime = datetime(2026, 6, 19, 14, 18, 29, tzinfo=timezone.utc)
    os.utime(snapshot_path, (mtime.timestamp(), mtime.timestamp()))

    freshness_ts, source = live.snapshot_freshness_time({"ts_utc": logical_ts.isoformat()}, snapshot_path)

    assert freshness_ts == mtime
    assert source == "file_mtime_utc"


def test_fresh_taker_quote_uses_peak_forming_min_edge(monkeypatch):
    monkeypatch.setattr(
        live,
        "fetch_json",
        lambda *_args, **_kwargs: {
            "asks": [{"price": "0.740", "size": "10"}],
            "bids": [{"price": "0.720", "size": "10"}],
        },
    )
    args = argparse.Namespace(
        max_taker_cushion=0.02,
        cross_tick_buffer=0.001,
        max_order_notional=5.0,
        peak_forming_min_edge=0.02,
    )

    quote = live.fresh_taker_quote(
        {
            "token_id": "yes-token",
            "p_yes_win": 0.766,
            "yes_current_ask": 0.740,
            "entry_profile": "peak_forming_micro",
        },
        args,
    )

    assert quote["status"] == "accepted"
    assert quote["required_quote_edge"] == 0.02
    assert round(quote["edge_at_limit"], 3) == 0.025


def test_fresh_taker_quote_rejects_peak_forming_edge_below_required(monkeypatch):
    monkeypatch.setattr(
        live,
        "fetch_json",
        lambda *_args, **_kwargs: {
            "asks": [{"price": "0.740", "size": "10"}],
            "bids": [{"price": "0.720", "size": "10"}],
        },
    )
    args = argparse.Namespace(
        max_taker_cushion=0.02,
        cross_tick_buffer=0.001,
        max_order_notional=5.0,
        peak_forming_min_edge=0.02,
    )

    quote = live.fresh_taker_quote(
        {
            "token_id": "yes-token",
            "p_yes_win": 0.750358,
            "yes_current_ask": 0.740,
            "entry_profile": "peak_forming_micro",
        },
        args,
    )

    assert quote["status"] == "rejected"
    assert quote["reason"] == "fresh_edge_below_required"
    assert quote["required_quote_edge"] == 0.02


def test_fresh_taker_quote_accepts_thin_depth_when_edge_passes(monkeypatch):
    monkeypatch.setattr(
        live,
        "fetch_json",
        lambda *_args, **_kwargs: {
            "asks": [{"price": "0.740", "size": "1"}],
            "bids": [{"price": "0.720", "size": "10"}],
        },
    )
    args = argparse.Namespace(
        max_taker_cushion=0.02,
        cross_tick_buffer=0.001,
        max_order_notional=5.0,
        peak_forming_min_edge=0.02,
    )

    quote = live.fresh_taker_quote(
        {
            "token_id": "yes-token",
            "p_yes_win": 0.80,
            "yes_current_ask": 0.740,
            "entry_profile": "peak_forming_micro",
        },
        args,
    )

    assert quote["status"] == "accepted"
    assert quote["fresh_available_notional"] == 0.74
    assert quote["edge_at_limit"] >= 0.02


def test_order_expiry_tracks_next_observation_clock():
    fields = live.order_expiry_fields({"obs": {"minutes_to_next_obs": 19.6}})

    assert fields["expiry_policy"] == "observation_clock_next_obs_plus_5m_max_90m_v1"
    assert fields["order_ttl_min"] == 24.6
    assert fields["expires_at_utc"]


def test_order_expiry_falls_back_when_clock_missing():
    fields = live.order_expiry_fields({"obs": {}})

    assert fields["expiry_policy"] == "fixed_60m_missing_observation_clock_v1"
    assert fields["order_ttl_min"] == 60.0


def test_build_current_rows_reports_missing_local_date_market_when_snapshot_rolls_ahead():
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")
    snapshot_ts = datetime(2026, 6, 17, 20, 30, tzinfo=timezone.utc)

    rows, audits = live.build_current_rows(
        {"ts_utc": snapshot_ts.isoformat()},
        [_record("LA", "74-75"), _record("LA", "76-77")],
        {"LA": station},
        snapshot_ts,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=1,
    )

    assert rows.empty
    assert audits[0]["status"] == "no_current_local_date_market"
    assert audits[0]["local_date"] == "2026-06-17"
    assert audits[0]["available_target_dates"] == ["2026-06-16"]
    assert audits[0]["nearest_target_date_delta_days"] == -1


def test_load_stations_supplements_live_source_profiles_without_watchlist_city():
    stations = live.load_stations()

    assert stations["Chicago"].icao == "KORD"
    assert stations["Chicago"].timezone_name == "America/Chicago"
    assert stations["PanamaCity"].icao == "MPMG"
    assert "MexicoCity" not in stations


def test_build_current_rows_reports_station_map_missing_for_watchlist_city():
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)

    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [_record("MexicoCity", "24-25", event_date="2026-06-18")],
        {},
        now,
        source_profiles=live.load_source_profiles(),
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert rows.empty
    assert audits[0]["status"] == "station_map_missing"
    assert audits[0]["reason"] == "source_profile_not_live_eligible"


def test_build_current_rows_prefers_city_local_date_when_snapshot_has_multiple_market_dates(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-18T20:20:00+00:00",
            "timezone": "America/Los_Angeles",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 21.1,
            "current_temp_c": 20.0,
            "decline_c": 1.1,
            "tmpf_now": 68.0,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 18.0,
            "relh_now": 45.0,
            "sknt_now": 6.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [
            _record("LA", "68-69", event_date="2026-06-18"),
            _record("LA", "70-71", event_date="2026-06-18"),
            _record("LA", "72-73", event_date="2026-06-18"),
            _record("LA", "74-75", event_date="2026-06-19"),
        ],
        {"LA": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1


def test_build_current_rows_prefers_fast_observation_cache(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")
    observation_cache = {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": "2026-06-18T20:29:00+00:00",
        "records": [
            {
                "city": "LA",
                "target_date": "2026-06-18",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "timezone_name": "America/Los_Angeles",
                "last_obs_utc": "2026-06-18T20:20:00+00:00",
                "running_max_obs_utc": "2026-06-18T20:00:00+00:00",
                "n_obs": 10,
                "cadence_min": 30.0,
                "current_temp_c": 20.0,
                "running_max_c": 21.1,
                "decline_c": 1.1,
                "tmpf_now": 68.0,
            }
        ],
    }

    def fail_snapshot_metar(*_args, **_kwargs):
        raise AssertionError("snapshot METAR fallback should not be used when fast cache has the city/date")

    monkeypatch.setattr(live, "snapshot_metar_obs", fail_snapshot_metar)

    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [
            _record("LA", "68-69", event_date="2026-06-18"),
            _record("LA", "70-71", event_date="2026-06-18"),
            _record("LA", "72-73", event_date="2026-06-18"),
        ],
        {"LA": station},
        now,
        observation_cache=observation_cache,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["obs"]["source"] == "aviationweather_metar"
    assert rows.iloc[0]["running_value"] == 70


def test_build_current_rows_trusts_ok_observation_cache_with_low_obs_count(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")
    observation_cache = {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": "2026-06-18T20:29:00+00:00",
        "records": [
            {
                "city": "LA",
                "target_date": "2026-06-18",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "timezone_name": "America/Los_Angeles",
                "last_obs_utc": "2026-06-18T20:20:00+00:00",
                "running_max_obs_utc": "2026-06-18T20:20:00+00:00",
                "n_obs": 1,
                "cadence_min": 30.0,
                "current_temp_c": 20.0,
                "running_max_c": 20.0,
                "decline_c": 0.0,
            }
        ],
    }

    def fail_snapshot_metar(*_args, **_kwargs):
        raise AssertionError("ok observation cache should not fall back only because n_obs is low")

    monkeypatch.setattr(live, "snapshot_metar_obs", fail_snapshot_metar)

    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [
            _record("LA", "68-69", event_date="2026-06-18"),
            _record("LA", "70-71", event_date="2026-06-18"),
        ],
        {"LA": station},
        now,
        observation_cache=observation_cache,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["obs"]["source"] == "aviationweather_metar"


def test_build_current_rows_falls_back_when_fast_cache_fetch_failed(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")
    observation_cache = {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": "2026-06-18T20:29:00+00:00",
        "records": [
            {
                "city": "LA",
                "target_date": "2026-06-18",
                "status": "fetch_failed",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "error": "HTTP 429",
            }
        ],
    }

    def snapshot_ok(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "paper_snapshot_metar",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-18T20:20:00+00:00",
            "timezone": "America/Los_Angeles",
            "cadence_min": 60.0,
            "minutes_to_next_obs": 50.0,
            "running_max_c": 21.1,
            "running_max_obs_utc": "2026-06-18T20:00:00+00:00",
            "current_temp_c": 20.0,
            "decline_c": 1.1,
            "tmpf_now": 68.0,
        }

    monkeypatch.setattr(live, "snapshot_metar_obs", snapshot_ok)

    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [
            _record("LA", "68-69", event_date="2026-06-18"),
            _record("LA", "70-71", event_date="2026-06-18"),
            _record("LA", "72-73", event_date="2026-06-18"),
        ],
        {"LA": station},
        now,
        observation_cache=observation_cache,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["obs"]["source"] == "paper_snapshot_metar"


def test_observation_cache_uses_cadence_aware_staleness_limit():
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")
    now = datetime(2026, 6, 18, 20, 35, tzinfo=timezone.utc)
    observation_cache = {
        "schema_version": "weather_data_feed_observation_cache_v1",
        "generated_at_utc": "2026-06-18T20:35:00+00:00",
        "records": [
            {
                "city": "LA",
                "target_date": "2026-06-18",
                "status": "ok",
                "source": "aviationweather_metar",
                "station": "KLAX",
                "last_obs_utc": "2026-06-18T20:00:00+00:00",
                "running_max_obs_utc": "2026-06-18T20:00:00+00:00",
                "n_obs": 10,
                "cadence_min": 60.0,
                "current_temp_c": 20.0,
                "running_max_c": 21.1,
                "decline_c": 1.1,
            }
        ],
    }

    obs = live.observation_cache_obs(
        observation_cache,
        "LA",
        "2026-06-18",
        station,
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
    )

    assert obs["status"] == "ok"
    assert obs["effective_max_obs_age_min"] == 75.0


def test_build_current_rows_allows_explicit_market_local_date_mapping(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-18T20:20:00+00:00",
            "timezone": "America/Los_Angeles",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 21.1,
            "current_temp_c": 20.0,
            "decline_c": 1.1,
            "tmpf_now": 68.0,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 18.0,
            "relh_now": 45.0,
            "sknt_now": 6.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    mapped_current = {**_record("LA", "70-71", event_date="2026-06-19"), "market_local_date": "2026-06-18"}
    mapped_d1 = {**_record("LA", "72-73", event_date="2026-06-19"), "market_local_date": "2026-06-18"}

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [mapped_current, mapped_d1],
        {"LA": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["target_date"] == "2026-06-19"
    assert rows.iloc[0]["local_date"] == "2026-06-18"
    assert rows.iloc[0]["current_bracket"] == "70-71"


def test_build_current_rows_falls_back_to_market_prices_when_orderbook_missing(monkeypatch):
    now = datetime(2026, 6, 18, 20, 30, tzinfo=timezone.utc)
    station = live.Station("LA", "KLAX", "F", -8, "America/Los_Angeles")

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-18T20:20:00+00:00",
            "timezone": "America/Los_Angeles",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": (70.0 - 32.0) * 5.0 / 9.0,
            "current_temp_c": (68.0 - 32.0) * 5.0 / 9.0,
            "decline_c": 2.0 * 5.0 / 9.0,
            "tmpf_now": 68.0,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 18.0,
            "relh_now": 45.0,
            "sknt_now": 6.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    current = {
        **_record("LA", "70-71", event_date="2026-06-18"),
        "yes_best_ask": None,
        "yes_ask_size": None,
        "market_yes_price": 0.42,
    }
    d1 = {
        **_record("LA", "72-73", event_date="2026-06-18"),
        "no_best_ask": None,
        "no_ask_size": None,
        "market_yes_price": 0.18,
    }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [current, d1],
        {"LA": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["yes_current_ask"] == 0.42
    assert round(rows.iloc[0]["d1_no_ask"], 2) == 0.82
    assert rows.iloc[0]["snapshot_price_source"] == "market_yes_price_fallback"


def test_helsinki_uses_iana_dst_without_live_hour_gate(monkeypatch):
    station = live.Station("Helsinki", "EFHK", "C", 2, "Europe/Helsinki")
    snapshot_ts = datetime(2026, 6, 16, 13, 18, tzinfo=timezone.utc)

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T13:10:00+00:00",
            "timezone": "Europe/Helsinki",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 20.0,
            "current_temp_c": 19.0,
            "decline_c": 1.0,
            "tmpf_now": 66.2,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 16.2,
            "relh_now": 50.0,
            "sknt_now": 5.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": snapshot_ts.isoformat()},
        [_record("Helsinki", "20"), _record("Helsinki", "21")],
        {"Helsinki": station},
        snapshot_ts,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["decision_hour_local"] == 16
    assert rows.iloc[0]["timezone"] == "Europe/Helsinki"


def test_other_dst_cities_use_city_timezone_mapping_without_hour_gate(monkeypatch):
    station = live.Station("NYC", "KNYC", "F", -5, None)
    snapshot_ts = datetime(2026, 6, 16, 20, 30, tzinfo=timezone.utc)

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T20:20:00+00:00",
            "timezone": "America/New_York",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": (80.0 - 32.0) * 5.0 / 9.0,
            "current_temp_c": (79.0 - 32.0) * 5.0 / 9.0,
            "decline_c": 5.0 / 9.0,
            "tmpf_now": 79.0,
            "dwpf_now": 55.0,
            "dewpoint_depression_f": 24.0,
            "relh_now": 45.0,
            "sknt_now": 6.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": snapshot_ts.isoformat()},
        [_record("NYC", "80"), _record("NYC", "81")],
        {"NYC": station},
        snapshot_ts,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    assert rows.iloc[0]["decision_hour_local"] == 16
    assert rows.iloc[0]["timezone"] == "America/New_York"


def test_fetch_obs_blocks_pre_metar_update_blackout(monkeypatch):
    now = datetime(2026, 6, 16, 13, 18, tzinfo=timezone.utc)
    start = datetime(2026, 6, 16, 9, 50, tzinfo=timezone.utc)
    obs = [
        {
            "ts": start + timedelta(minutes=30 * i),
            "tmpc": 19.0,
            "dwpc": 10.0,
            "relh": 50.0,
            "sknt": 5.0,
            "sky": 1.0,
        }
        for i in range(8)
    ]
    assert obs[-1]["ts"] == datetime(2026, 6, 16, 13, 20, tzinfo=timezone.utc)

    def fake_obs(_icao, _tz, _local_date):
        return obs

    monkeypatch.setattr(live, "aviationweather_obs", fake_obs)
    station = live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")
    result = live.fetch_obs(
        station,
        now,
        max_obs_age_min=40,
        pre_update_blackout_min=6,
    )

    assert result["status"] == "pre_metar_update_blackout"
    assert result["last_obs_utc"] == "2026-06-16T12:50:00+00:00"
    assert result["minutes_to_next_obs"] == 2.0


def test_fetch_obs_reports_minutes_since_running_max(monkeypatch):
    now = datetime(2026, 6, 16, 13, 25, tzinfo=timezone.utc)
    obs = [
        {"ts": datetime(2026, 6, 16, 9, 50, tzinfo=timezone.utc), "tmpc": 18.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
        {"ts": datetime(2026, 6, 16, 10, 50, tzinfo=timezone.utc), "tmpc": 19.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
        {"ts": datetime(2026, 6, 16, 11, 50, tzinfo=timezone.utc), "tmpc": 20.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
        {"ts": datetime(2026, 6, 16, 12, 50, tzinfo=timezone.utc), "tmpc": 19.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
        {"ts": datetime(2026, 6, 16, 13, 20, tzinfo=timezone.utc), "tmpc": 19.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
        {"ts": datetime(2026, 6, 16, 13, 25, tzinfo=timezone.utc), "tmpc": 19.0, "dwpc": 9.0, "relh": 50.0, "sknt": 5.0, "sky": 1.0},
    ]

    monkeypatch.setattr(live, "aviationweather_obs", lambda *_args: obs)
    station = live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")
    result = live.fetch_obs(
        station,
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=0,
    )

    assert result["status"] == "ok"
    assert result["running_max_c"] == 20.0
    assert result["running_max_obs_utc"] == "2026-06-16T11:50:00+00:00"
    assert result["minutes_since_running_max"] == 95.0


def test_forecast_details_from_open_meteo_uses_earliest_peak_hour():
    payload = {
        "timezone": "Europe/Helsinki",
        "utc_offset_seconds": 10800,
        "hourly": {
            "time": [
                "2026-06-16T12:00",
                "2026-06-16T13:00",
                "2026-06-16T14:00",
                "2026-06-16T15:00",
            ],
            "temperature_2m": [68.0, 69.8, 69.8, 68.9],
        },
    }

    info = live.forecast_details_from_open_meteo(payload, source_model="ecmwf")

    assert info["forecast_max_f"] == 69.8
    assert info["forecast_peak_hour_local"] == 13
    assert info["forecast_peak_time_utc"] == "2026-06-16T10:00:00Z"
    assert info["forecast_values_hash"]
    assert info["forecast_peak_source"] == "open_meteo_live_ecmwf"


def test_build_current_rows_allows_one_c_gap_for_current_yes(monkeypatch):
    now = datetime(2026, 6, 16, 4, 10, tzinfo=timezone.utc)
    station = live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T04:00:00+00:00",
            "timezone": "Asia/Tokyo",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 20.0,
            "current_temp_c": 19.0,
            "decline_c": 1.0,
            "tmpf_now": 66.2,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 16.2,
            "relh_now": 50.0,
            "sknt_now": 5.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [_record("Tokyo", "20"), _record("Tokyo", "21")],
        {"Tokyo": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert len(rows) == 1
    assert rows.iloc[0]["current_bracket"] == "20"
    assert rows.iloc[0]["d1_no_bracket"] == "21"
    assert rows.iloc[0]["gap_running_to_d1_low_c"] == 1.0
    assert rows.iloc[0]["forecast_peak_fetch_status"] == "snapshot_missing_no_forecast_source"
    assert audits == []


def test_build_current_rows_fetches_peak_clock_when_snapshot_lacks_native_fields(monkeypatch):
    now = datetime(2026, 6, 16, 4, 10, tzinfo=timezone.utc)
    station = live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")
    rec20 = {
        **_record("Tokyo", "20"),
        "forecast_source": "open_meteo_live_gfs",
        "forecast_max_f": 69.8,
    }

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T04:00:00+00:00",
            "timezone": "Asia/Tokyo",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 20.0,
            "current_temp_c": 19.0,
            "decline_c": 1.0,
            "tmpf_now": 66.2,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 16.2,
            "relh_now": 50.0,
            "sknt_now": 5.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    def fake_forecast(city, target_date, model):
        assert (city, target_date, model) == ("Tokyo", "2026-06-16", "gfs")
        return {
            "forecast_max_f": 69.8,
            "forecast_peak_hour_local": 13,
            "forecast_peak_time_local": "2026-06-16T13:00",
            "forecast_peak_hour_utc": 4,
            "forecast_peak_time_utc": "2026-06-16T04:00:00Z",
            "forecast_hourly_count": 24,
            "forecast_values_hash": "hash-from-fallback",
            "forecast_peak_source": "open_meteo_live_gfs",
            "forecast_peak_fetch_status": "fetched",
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    monkeypatch.setattr(live, "fetch_live_forecast_peak_details", fake_forecast)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [rec20, _record("Tokyo", "21")],
        {"Tokyo": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=0,
    )

    assert audits == []
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["forecast_peak_hour_local"] == 13
    assert row["forecast_peak_delta_hours_local"] == 0.16666666666666607
    assert row["forecast_values_hash"] == "hash-from-fallback"
    assert row["forecast_peak_fetch_status"] == "fetched"


def test_first_rule_treats_forecast_peak_as_model_context_not_hard_gate():
    args = argparse.Namespace(
        min_available_notional=5.0,
        allow_missing_forecast_peak=False,
        min_forecast_peak_delta_hours=-1.999,
    )
    base = {
        "decline_c": 1.0,
        "yes_current_ask": 0.78,
        "p_yes_win": 0.9,
        "ev": 0.12,
        "available_notional_at_ask": 10.0,
        "token_id": "yes-token",
        "forecast_peak_delta_hours_local": 0.5,
    }

    assert live.first_rule_reject_reason(base, args) == "snapshot_rule_passed"
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": -2.0}, args) == "snapshot_rule_passed"
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": None}, args) == "snapshot_rule_passed"
    assert live.first_rule_reject_reason({**base, "available_notional_at_ask": 0.25}, args) == "snapshot_rule_passed"


def test_first_rule_allows_peak_forming_current_high_when_enabled():
    args = argparse.Namespace(
        min_available_notional=5.0,
        allow_missing_forecast_peak=False,
        min_forecast_peak_delta_hours=-1.999,
        enable_peak_forming_live=True,
        peak_forming_max_decline_c=0.25,
        peak_forming_min_ask=0.50,
        peak_forming_max_ask=0.97,
        peak_forming_min_p=0.60,
        peak_forming_min_edge=0.02,
        peak_forming_min_forecast_delta_hours=-1.0,
        entry_profile_mode="both",
    )
    base = {
        "decline_c": 0.0,
        "yes_current_ask": 0.66,
        "p_yes_win": 0.77,
        "ev": 0.11,
        "available_notional_at_ask": 10.0,
        "token_id": "yes-token",
        "forecast_peak_delta_hours_local": 0.0,
    }

    assert live.first_rule_reject_reason({**base}, argparse.Namespace(**{**vars(args), "enable_peak_forming_live": False})) == "snapshot_rule_decline_lt_0_5"
    assert live.classify_entry_profile(base, args) == ("snapshot_rule_passed", "peak_forming_micro")
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": -1.25}, args) == "snapshot_rule_passed"
    assert live.first_rule_reject_reason({**base, "yes_current_ask": 0.98}, args) == "snapshot_rule_peak_forming_ask_gt_max"


def test_peak_forming_metar_veto_only_blocks_too_fresh_running_max():
    args = argparse.Namespace(
        min_available_notional=5.0,
        allow_missing_forecast_peak=False,
        min_forecast_peak_delta_hours=-1.999,
        enable_peak_forming_live=True,
        peak_forming_max_decline_c=0.25,
        peak_forming_min_ask=0.50,
        peak_forming_max_ask=0.97,
        peak_forming_min_p=0.60,
        peak_forming_min_edge=0.02,
        peak_forming_min_forecast_delta_hours=-1.0,
        entry_profile_mode="both",
        disable_peak_forming_metar_veto=False,
        peak_forming_min_minutes_since_running_max=10.0,
        peak_forming_forecast_bust_margin_c=0.1,
        peak_forming_clear_sky_max_code=1.0,
        peak_forming_prior_cloud_min_code=3.0,
        peak_forming_cloud_clearing_min_drop=2.0,
        peak_forming_warming_trend_min_d_tmpf_3h=1.5,
    )
    row = {
        "unit": "C",
        "decline_c": 0.0,
        "yes_current_ask": 0.89,
        "p_yes_win": 0.943,
        "ev": 0.053,
        "available_notional_at_ask": 45.0,
        "token_id": "yes-token",
        "forecast_peak_delta_hours_local": 3.0,
        "forecast_max_native": 26.78,
        "running_max_c": 27.0,
        "minutes_since_running_max": 0.15,
        "sky_now": 1.0,
        "sky_1h": 3.0,
        "d_tmpf_3h": 1.8,
    }

    assert live.first_rule_reject_reason(row, args) == "snapshot_rule_peak_forming_fresh_running_max"
    assert live.first_rule_reject_reason({**row, "sky_now": live.np.nan, "sky_1h": live.np.nan}, args) == "snapshot_rule_peak_forming_fresh_running_max"
    assert live.first_rule_reject_reason({**row, "sky_now": live.np.nan, "sky_1h": live.np.nan, "forecast_max_native": 28.0}, args) == "snapshot_rule_peak_forming_fresh_running_max"
    assert live.first_rule_reject_reason({**row, "sky_now": live.np.nan, "sky_1h": live.np.nan, "forecast_max_native": 28.0, "d_tmpf_3h": 0.0}, args) == "snapshot_rule_peak_forming_fresh_running_max"
    assert live.classify_entry_profile(
        {**row, "minutes_since_running_max": 15.0},
        args,
    ) == ("snapshot_rule_passed", "peak_forming_micro")


def test_entry_profile_mode_splits_fade_and_peak_instances():
    args = argparse.Namespace(
        min_available_notional=5.0,
        allow_missing_forecast_peak=False,
        min_forecast_peak_delta_hours=-1.999,
        enable_peak_forming_live=True,
        peak_forming_max_decline_c=0.25,
        peak_forming_min_ask=0.50,
        peak_forming_max_ask=0.97,
        peak_forming_min_p=0.60,
        peak_forming_min_edge=0.02,
        peak_forming_min_forecast_delta_hours=-1.0,
        entry_profile_mode="both",
    )
    base = {
        "yes_current_ask": 0.70,
        "p_yes_win": 0.78,
        "ev": 0.08,
        "available_notional_at_ask": 10.0,
        "token_id": "yes-token",
        "forecast_peak_delta_hours_local": 0.0,
    }
    fade = {**base, "decline_c": 0.5}
    peak = {**base, "decline_c": 0.0}

    assert live.classify_entry_profile(fade, argparse.Namespace(**{**vars(args), "entry_profile_mode": "fade_confirmed"})) == (
        "snapshot_rule_passed",
        "fade_confirmed",
    )
    assert live.first_rule_reject_reason(peak, argparse.Namespace(**{**vars(args), "entry_profile_mode": "fade_confirmed"})) == "snapshot_rule_peak_forming_disabled"
    assert live.first_rule_reject_reason(fade, argparse.Namespace(**{**vars(args), "entry_profile_mode": "peak_forming_micro"})) == "snapshot_rule_fade_confirmed_disabled"
    assert live.classify_entry_profile(peak, argparse.Namespace(**{**vars(args), "entry_profile_mode": "peak_forming_micro"})) == (
        "snapshot_rule_passed",
        "peak_forming_micro",
    )


def test_split_instances_count_matching_legacy_profile_as_prior_risk():
    legacy_peak = {
        "strategy_instance": "theta_current_yes_tiny_live_v1",
        "entry_profile": "peak_forming_micro",
    }
    legacy_fade = {
        "strategy_instance": "theta_current_yes_tiny_live_v1",
        "entry_profile": "fade_confirmed",
    }

    assert live.prior_row_matches_strategy(legacy_peak, "theta_current_yes_peak_forming_micro_tiny_live_v1")
    assert not live.prior_row_matches_strategy(legacy_fade, "theta_current_yes_peak_forming_micro_tiny_live_v1")
    assert live.prior_row_matches_strategy(legacy_fade, "theta_current_yes_fade_confirmed_tiny_live_v1")
    assert not live.prior_row_matches_strategy(legacy_peak, "theta_current_yes_fade_confirmed_tiny_live_v1")


def test_split_instances_infer_missing_legacy_profile_from_shadow_fields():
    legacy_peak = {
        "strategy_instance": "theta_current_yes_tiny_live_v1",
        "shadow_decision": "tiny_live_peak_forming_micro_v1",
        "shadow_reason": "forecast_peak_current_high_micro_probe",
    }
    legacy_fade = {
        "strategy_instance": "theta_current_yes_tiny_live_v1",
        "combo": "current_yes_fade_confirmed_v9",
        "shadow_decision": "tiny_live_confirmed_v9",
    }

    assert live.inferred_entry_profile_for_row(legacy_peak) == "peak_forming_micro"
    assert live.inferred_entry_profile_for_row(legacy_fade) == "fade_confirmed"
    assert live.prior_row_matches_strategy(legacy_peak, "theta_current_yes_peak_forming_micro_tiny_live_v1")
    assert not live.prior_row_matches_strategy(legacy_peak, "theta_current_yes_fade_confirmed_tiny_live_v1")
    assert live.prior_row_matches_strategy(legacy_fade, "theta_current_yes_fade_confirmed_tiny_live_v1")
    assert not live.prior_row_matches_strategy(legacy_fade, "theta_current_yes_peak_forming_micro_tiny_live_v1")


def test_strategy_signal_key_matches_candidate_and_live_order_shapes():
    candidate = {
        "city": "Helsinki",
        "target_date": "2026-06-18",
        "current_bracket": "22",
        "token_id": "yes-token",
    }
    live_order = {
        "city": "Helsinki",
        "target_date": "2026-06-18",
        "bracket": "22",
        "token_id": "yes-token",
    }

    assert live.strategy_signal_key(candidate) == live.strategy_signal_key(live_order)


def test_probability_branch_scores_shadow_fade_specialist_until_enabled(monkeypatch):
    rows = [
        {"decline_c": 0.5, "yes_current_ask": 0.7},
        {"decline_c": 0.0, "yes_current_ask": 0.7},
    ]
    current = live.pd.DataFrame(rows)
    for col in live.MODEL_FEATURES:
        if col not in current.columns:
            current[col] = "Tokyo" if col == "city" else ("C" if col == "unit" else 0.0)
    base_artifact = {"artifact_type": "base"}
    fade_artifact = {"artifact_type": "fade"}

    def fake_score(frame, artifact):
        if artifact["artifact_type"] == "base":
            return live.np.asarray([0.8, 0.6])
        return live.np.asarray([0.9, 0.4])

    monkeypatch.setattr(live, "score_rows", fake_score)
    base_args = argparse.Namespace(fade_confirmed_model_mode="base")
    scored = live.apply_probability_branch_scores(current, args=base_args, base_artifact=base_artifact, fade_artifact=fade_artifact)
    assert scored["p_yes_win"].tolist() == [0.8, 0.6]
    assert scored["p_yes_win_fade_confirmed_specialist"].tolist() == [0.9, 0.4]
    assert scored["probability_branch"].tolist() == ["base_current_yes_model", "base_current_yes_model"]

    specialist_args = argparse.Namespace(fade_confirmed_model_mode="specialist")
    scored = live.apply_probability_branch_scores(current, args=specialist_args, base_artifact=base_artifact, fade_artifact=fade_artifact)
    assert scored["p_yes_win"].tolist() == [0.9, 0.6]
    assert scored["probability_branch"].tolist() == ["fade_confirmed_specialist_v1", "base_current_yes_model"]


def test_observation_epoch_key_uses_running_max_metar_timestamp():
    row = {
        "city": "Shanghai",
        "target_date": "2026-06-18",
        "current_bracket": "27",
        "obs": {"running_max_obs_utc": "2026-06-18T06:00:00+00:00"},
    }

    assert live.observation_epoch_key(row) == (
        "Shanghai",
        "2026-06-18",
        "27",
        "2026-06-18T06:00:00+00:00",
    )
    assert live.observation_epoch_key({**row, "obs": {"running_max_obs_utc": "2026-06-18T06:30:00+00:00"}}) != live.observation_epoch_key(row)


def test_build_current_rows_can_optionally_veto_gap_above_threshold(monkeypatch):
    now = datetime(2026, 6, 16, 4, 10, tzinfo=timezone.utc)
    station = live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T04:00:00+00:00",
            "timezone": "Asia/Tokyo",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 20.0,
            "current_temp_c": 19.0,
            "decline_c": 1.0,
            "tmpf_now": 66.2,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 16.2,
            "relh_now": 50.0,
            "sknt_now": 5.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    rows, audits = live.build_current_rows(
        {"ts_utc": now.isoformat()},
        [_record("Tokyo", "20"), _record("Tokyo", "21")],
        {"Tokyo": station},
        now,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=1.01,
    )

    assert rows.empty
    assert audits[0]["status"] == "too_close_to_next_bracket"
    assert audits[0]["gap_running_to_d1_low_c"] == 1.0


def test_run_once_writes_forward_telemetry_for_planned_candidate(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-06-16T04:10:00+00:00",
                "records": [
                    {
                        **_record("Tokyo", "20", 0.78),
                        "forecast_source": "open_meteo_live_gfs",
                        "forecast_peak_hour_local": 13,
                        "forecast_peak_time_local": "2026-06-16T13:00",
                        "forecast_values_hash": "hash-test",
                    },
                    _record("Tokyo", "21", 0.20),
                ],
            }
        ),
        encoding="utf-8",
    )
    plan_out = tmp_path / "plans.jsonl"
    summary_out = tmp_path / "summary.json"
    history_out = tmp_path / "history.jsonl"
    telemetry_out = tmp_path / "forward_telemetry.jsonl"

    monkeypatch.setattr(live, "PLAN_OUT", plan_out)
    monkeypatch.setattr(live, "SUMMARY_OUT", summary_out)
    monkeypatch.setattr(live, "HISTORY_OUT", history_out)
    monkeypatch.setattr(live, "FORWARD_TELEMETRY_OUT", telemetry_out)
    monkeypatch.setattr(live, "latest_snapshot", lambda: snapshot_path)
    monkeypatch.setattr(live, "snapshot_dir", lambda: tmp_path)
    monkeypatch.setattr(live, "load_stations", lambda: {"Tokyo": live.Station("Tokyo", "RJTT", "C", 9, "Asia/Tokyo")})
    monkeypatch.setattr(live, "load_source_profiles", lambda: {})
    monkeypatch.setattr(live, "load_model_artifact", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "score_rows", lambda rows, artifact: live.np.asarray([0.9] * len(rows)))
    monkeypatch.setattr(live, "prior_city_day_notional", lambda _instance: {})
    monkeypatch.setattr(
        live,
        "fresh_taker_quote",
        lambda row, args: {
            "status": "accepted",
            "best_bid": 0.77,
            "fresh_ask": 0.79,
            "fresh_ask_size": 10.0,
            "fresh_available_notional": 7.9,
            "max_taker_price": 0.80,
            "limit_price": 0.791,
            "edge_at_fresh_ask": 0.11,
            "edge_at_limit": 0.109,
            "expected_profit_usd": 0.689,
            "derived_min_edge_after_full_cushion": 0.03,
            "cushion_paid_vs_snapshot": 0.011,
        },
    )

    def fake_fetch_obs(*_args, **_kwargs):
        return {
            "status": "ok",
            "source": "test_metar",
            "n_obs": 10,
            "age_min": 10.0,
            "last_obs_utc": "2026-06-16T04:00:00+00:00",
            "timezone": "Asia/Tokyo",
            "cadence_min": 30.0,
            "minutes_to_next_obs": 20.0,
            "running_max_c": 20.0,
            "running_max_obs_utc": "2026-06-16T03:30:00+00:00",
            "minutes_since_running_max": 40.0,
            "current_temp_c": 19.0,
            "decline_c": 1.0,
            "tmpf_now": 66.2,
            "dwpf_now": 50.0,
            "dewpoint_depression_f": 16.2,
            "relh_now": 50.0,
            "sknt_now": 5.0,
            "sky_now": 1.0,
            "d_tmpf_1h": -1.0,
            "d_tmpf_3h": -2.0,
            "d_dwpf_3h": 0.0,
            "d_relh_3h": 0.0,
        }

    monkeypatch.setattr(live, "fetch_obs", fake_fetch_obs)
    args = argparse.Namespace(
        snapshot=str(snapshot_path),
        now_utc="2026-06-16T04:10:00+00:00",
        max_order_notional=5.0,
        max_city_day_notional=10.0,
        min_available_notional=5.0,
        max_taker_cushion=0.02,
        cross_tick_buffer=0.001,
        max_orders=20,
        max_snapshot_age_min=1_000_000.0,
        max_obs_age_min=20.0,
        pre_metar_update_blackout_min=6.0,
        min_gap_to_next_bracket_c=0.0,
        min_local_hour=13,
        max_local_hour=15,
        live=False,
        confirm_live=False,
        no_telegram=True,
    )

    result = live.run_once(args)
    rows = [json.loads(line) for line in telemetry_out.read_text(encoding="utf-8").splitlines()]

    assert result["forward_telemetry_rows"] == 1
    assert rows[0]["decision_status"] == "planned"
    assert rows[0]["city"] == "Tokyo"
    assert rows[0]["obs_source"] == "paper_snapshot_metar"
    assert rows[0]["minutes_since_running_max"] is None
    assert rows[0]["fresh_best_ask"] == 0.79
    assert rows[0]["forecast_peak_hour_local"] == 13
    assert rows[0]["forecast_peak_delta_hours_local"] == 0.16666666666666607
    assert rows[0]["forecast_peak_fetch_status"] == "snapshot_native"
