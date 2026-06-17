from __future__ import annotations

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


def test_build_current_rows_vetoes_one_c_gap_to_next_bracket(monkeypatch):
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
        min_gap_to_next_bracket_c=1,
    )

    assert rows.empty
    assert audits[0]["status"] == "too_close_to_next_bracket"
    assert audits[0]["gap_running_to_d1_low_c"] == 1.0
