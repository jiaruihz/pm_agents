from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_data_feed_prod_health_check import (
    check_snapshot_duplicates,
    check_telemetry,
    overall_status,
)


def test_prod_health_check_flags_snapshot_duplicates_and_staleness(tmp_path):
    snapshot = tmp_path / "snapshot_20260619_1200.json"
    row = {
        "city": "Shanghai",
        "target_date": "2026-06-19",
        "market_local_date": "2026-06-19",
        "city_local_date_at_snapshot": "2026-06-19",
        "snapshot_ts_utc": "2026-06-19T10:00:00Z",
        "token_id": "token-1",
        "bracket": "30",
    }
    snapshot.write_text(
        json.dumps(
            {
                "schema_version": "v3_cross_section_forecast_peak_clock",
                "data_feed_schema_version": "weather_data_feed_snapshot_v1",
                "records": [row, dict(row)],
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_duplicates(
        snapshot,
        now_utc=datetime(2026, 6, 19, 11, 0, tzinfo=timezone.utc),
        max_age_min=45,
    )

    assert report["duplicate_record_count"] == 1
    assert report["snapshot_stale"] is True
    assert report["snapshot_age_min"] == 60.0


def test_prod_health_check_flags_duplicate_telemetry_run_ids(tmp_path):
    telemetry = tmp_path / "forward_telemetry.jsonl"
    row = {
        "record_type": "theta_current_yes_forward_telemetry",
        "created_at_utc": "2026-06-19T10:00:00Z",
        "strategy_instance": "theta_current_yes_fade_confirmed_tiny_live_v1",
        "city": "Shanghai",
        "target_date": "2026-06-19",
        "decision_status": "planned",
        "telemetry_run_id": "same-run",
    }
    telemetry.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    report = check_telemetry(telemetry, tail_rows=10)

    assert report["duplicate_telemetry_run_id_count"] == 1
    assert report["duplicate_decision_count"] == 1
    assert report["missing_required_fields"] == {}


def test_prod_health_overall_status_warns_on_stale_but_fails_on_structural_errors():
    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": True},
        "telemetry": [{"parse_error_count": 0, "duplicate_telemetry_run_id_count": 0, "duplicate_decision_count": 0}],
        "live_orders": {"parse_error_count": 0, "duplicate_order_id_count": 0, "duplicate_strategy_city_token_count": 0},
        "summaries": [],
    }
    assert overall_status(sections) == "warn"

    sections["snapshot_duplicates"]["duplicate_record_count"] = 1
    assert overall_status(sections) == "fail"
