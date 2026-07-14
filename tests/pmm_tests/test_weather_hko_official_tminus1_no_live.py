import json

from scripts.ops.weather_hko_official_tminus1_no_live import first_seen_hko_crosses


def test_hko_crosses_use_official_floor_and_first_detection(tmp_path):
    path = tmp_path / "high_frequency.jsonl"
    rows = [
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 31.9,
            "observation_time_utc": "2026-07-14T02:00:00Z",
            "local_detect_ts_utc": "2026-07-14T02:03:00Z",
        },
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 31.9,
            "observation_time_utc": "2026-07-14T02:00:00Z",
            "local_detect_ts_utc": "2026-07-14T02:02:00Z",
        },
        {
            "city": "HongKong",
            "source": "hko_obs",
            "station": "HK Observatory",
            "target_date": "2026-07-14",
            "temp_c": 32.0,
            "observation_time_utc": "2026-07-14T03:00:00Z",
            "local_detect_ts_utc": "2026-07-14T03:02:00Z",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    crosses = first_seen_hko_crosses(path, "2026-07-14")

    assert [row["source_floor_bracket_c"] for row in crosses] == [31, 32]
    assert crosses[0]["source_detect_ts_utc"] == "2026-07-14T02:02:00Z"
    assert crosses[1]["t_minus_1_no_bracket_c"] == 31
