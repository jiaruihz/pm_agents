from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from scripts.ops import weather_theta_current_yes_tiny_live as live


def _record(city: str, bracket: str, ask: float = 0.78) -> dict:
    return {
        "city": city,
        "event_date": "2026-06-16",
        "market_id": f"{city}-{bracket}",
        "condition_id": f"{city}-{bracket}",
        "bracket": bracket,
        "question": f"Will {city} hit {bracket} C?",
        "yes_best_ask": ask,
        "yes_ask_size": 20,
        "no_best_ask": 0.90,
        "no_ask_size": 20,
        "yes_token_id": f"yes-{city}-{bracket}",
        "event_slug": f"{city.lower()}-{bracket}",
    }


def test_parse_label_keeps_positive_fahrenheit_ranges():
    parsed = live.parse_label("74-75", "Will the highest temperature in New York City be between 74-75°F?")

    assert parsed == {"low": 74.0, "high": 75.0, "bottom": False, "top": False, "label": "74-75"}
    assert live.bracket_contains(parsed, 74)
    assert live.bracket_contains(parsed, 75)


def test_build_current_rows_requires_target_date_to_match_station_local_date():
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
    assert audits[0]["status"] == "target_date_not_local_date"
    assert audits[0]["target_date"] == "2026-06-16"
    assert audits[0]["local_date"] == "2026-06-17"


def test_helsinki_uses_iana_dst_for_live_hour_gate():
    station = live.Station("Helsinki", "EFHK", "C", 2, "Europe/Helsinki")
    snapshot_ts = datetime(2026, 6, 16, 13, 18, tzinfo=timezone.utc)

    rows, audits = live.build_current_rows(
        {"ts_utc": snapshot_ts.isoformat()},
        [_record("Helsinki", "20"), _record("Helsinki", "21")],
        {"Helsinki": station},
        snapshot_ts,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=1,
    )

    assert rows.empty
    assert audits[0]["status"] == "outside_hour"
    assert audits[0]["hour_local"] == 16
    assert audits[0]["timezone"] == "Europe/Helsinki"


def test_other_dst_cities_use_city_timezone_mapping_without_explicit_tz():
    station = live.Station("NYC", "KNYC", "F", -5, None)
    snapshot_ts = datetime(2026, 6, 16, 20, 30, tzinfo=timezone.utc)

    rows, audits = live.build_current_rows(
        {"ts_utc": snapshot_ts.isoformat()},
        [_record("NYC", "80"), _record("NYC", "81")],
        {"NYC": station},
        snapshot_ts,
        max_obs_age_min=20,
        pre_update_blackout_min=6,
        min_gap_to_next_bracket_c=1,
    )

    assert rows.empty
    assert audits[0]["status"] == "outside_hour"
    assert audits[0]["hour_local"] == 16
    assert audits[0]["timezone"] == "America/New_York"


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


def test_first_rule_rejects_missing_or_far_ahead_forecast_peak():
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
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": -2.0}, args) == "snapshot_rule_forecast_peak_too_far_ahead"
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": None}, args) == "snapshot_rule_missing_forecast_peak"


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
    assert live.first_rule_reject_reason({**base, "forecast_peak_delta_hours_local": -1.25}, args) == "snapshot_rule_peak_forming_forecast_peak_ahead"
    assert live.first_rule_reject_reason({**base, "yes_current_ask": 0.98}, args) == "snapshot_rule_peak_forming_ask_gt_max"


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
    monkeypatch.setattr(live, "load_model_artifact", lambda: {})
    monkeypatch.setattr(live, "score_rows", lambda rows, artifact: [0.9] * len(rows))
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
    assert rows[0]["obs_source"] == "test_metar"
    assert rows[0]["minutes_since_running_max"] == 40.0
    assert rows[0]["fresh_best_ask"] == 0.79
    assert rows[0]["forecast_peak_hour_local"] == 13
    assert rows[0]["forecast_peak_delta_hours_local"] == 0.16666666666666607
    assert rows[0]["forecast_peak_fetch_status"] == "snapshot_native"
