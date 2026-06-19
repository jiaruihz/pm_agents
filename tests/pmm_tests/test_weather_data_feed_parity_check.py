from __future__ import annotations

import json

from scripts.ops.weather_data_feed_parity_check import check_snapshot


def test_weather_data_feed_parity_check_accepts_city_local_target_mismatch(tmp_path):
    snapshot = {
        "ts_utc": "2026-06-19T05:01:23Z",
        "schema_version": "test",
        "records": [
            {
                "city": "LA",
                "event_date": "2026-06-19",
                "target_date": "2026-06-19",
                "market_local_date": "2026-06-19",
                "city_local_date_at_snapshot": "2026-06-18",
                "bracket": "72-73",
            },
            {
                "city": "Shanghai",
                "target_date": "2026-06-19",
                "market_local_date": "2026-06-19",
                "city_local_date_at_snapshot": "2026-06-19",
                "bracket": "30",
            },
        ],
    }
    path = tmp_path / "snapshot_20260619_0501.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    report = check_snapshot(path)

    assert report["status"] == "ok"
    assert report["total_records"] == 2
    assert report["local_to_target_date_counts"]["2026-06-18->2026-06-19"] == 1


def test_weather_data_feed_parity_check_fails_missing_protocol_field(tmp_path):
    snapshot = {
        "ts_utc": "2026-06-19T05:01:23Z",
        "records": [{"city": "LA", "target_date": "2026-06-19"}],
    }
    path = tmp_path / "snapshot_bad.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    report = check_snapshot(path)

    assert report["status"] == "fail"
    assert report["missing_required_fields"]["market_local_date"] == 1
