from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scripts.ops.weather_station_basis_shadow import (
    aviationweather_metar_day,
    parse_label,
    parse_iem_asos_temperature_obs,
    summarize_temperature_obs,
)


def test_parse_label_keeps_positive_fahrenheit_ranges():
    parsed = parse_label("74-75", "Will the highest temperature in New York City be between 74-75°F?")

    assert parsed == {"low": 74.0, "high": 75.0, "bottom": False, "top": False}


def test_parse_iem_asos_temperature_obs_filters_local_day():
    tz = ZoneInfo("Asia/Kuala_Lumpur")
    text = "\n".join(
        [
            "station,valid,tmpc",
            "WMKK,2026-06-13 15:30,27.00",
            "WMKK,2026-06-13 16:00,28.00",
            "WMKK,2026-06-14 06:45,25.00",
            "WMKK,2026-06-14 17:00,26.00",
            "",
        ]
    )

    obs = parse_iem_asos_temperature_obs(text, tz, date(2026, 6, 14))

    assert [(dt.isoformat(), temp) for dt, temp in obs] == [
        ("2026-06-13T16:00:00+00:00", 28.0),
        ("2026-06-14T06:45:00+00:00", 25.0),
    ]


def test_summarize_temperature_obs_marks_fresh_iem_source():
    obs = [
        (datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc), 28.0),
        (datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc), 30.0),
        (datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc), 31.0),
        (datetime(2026, 6, 14, 3, 0, tzinfo=timezone.utc), 32.0),
        (datetime(2026, 6, 14, 4, 0, tzinfo=timezone.utc), 32.0),
        (datetime(2026, 6, 14, 6, 45, tzinfo=timezone.utc), 25.0),
    ]

    result = summarize_temperature_obs(
        obs,
        source="iem_asos",
        now_dt=datetime(2026, 6, 14, 7, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert result["source"] == "iem_asos"
    assert result["running_max_c"] == 32.0
    assert result["current_temp_c"] == 25.0
    assert result["decline_c"] == 7.0
    assert result["age_min"] == 15.0


def test_aviationweather_metar_day_keeps_only_requested_local_day(monkeypatch):
    now = datetime.now(timezone.utc)
    tz = ZoneInfo("Asia/Kuala_Lumpur")
    local_date = now.astimezone(tz).date()
    prev_local = datetime.combine(local_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc) - timedelta(minutes=30)
    today_rows = [
        (now - timedelta(minutes=50), 28),
        (now - timedelta(minutes=45), 29),
        (now - timedelta(minutes=40), 30),
        (now - timedelta(minutes=35), 31),
        (now - timedelta(minutes=30), 32),
        (now - timedelta(minutes=25), 25),
    ]

    def fake_fetch_json(_url, _params):
        return [{"reportTime": (prev_local - timezone.utc.utcoffset(prev_local)).isoformat(), "temp": 27}] + [
            {"reportTime": ts.isoformat(), "temp": temp}
            for ts, temp in today_rows
        ]

    monkeypatch.setattr("scripts.ops.weather_station_basis_shadow.fetch_json", fake_fetch_json)

    result = aviationweather_metar_day("WMKK", tz, local_date)

    assert result["n_obs"] == 6
    assert result["running_max_c"] == 32.0
