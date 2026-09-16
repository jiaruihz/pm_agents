from __future__ import annotations

import json
from datetime import datetime, timezone

from weather_data_feed.forecast_hourly_curves import (
    FORECAST_HOURLY_CURVE_SCHEMA_VERSION,
    build_curve_row,
    build_hourly_curve,
    summarize_source_models,
    write_forecast_hourly_curve_capture,
)


def _row(
    snapshot_ts_utc: str,
    values_hash: str = "hash-a",
    *,
    detected_at_utc: str | None = None,
) -> dict:
    curve = build_hourly_curve(
        ["2026-07-11T00:00", "2026-07-11T01:00", "2026-07-11T02:00"],
        [70.0, None, "72.125"],
    )
    return build_curve_row(
        snapshot_ts_utc=snapshot_ts_utc,
        city="Testville",
        target_date="2026-07-11",
        forecast_source="open_meteo_live_ecmwf",
        forecast_model="ecmwf",
        forecast_assigned_model="ecmwf",
        forecast_values_hash=values_hash,
        hourly_curve=curve,
        forecast_max_f=72.125,
        forecast_peak_hour_local=2,
        forecast_peak_time_local="2026-07-11T02:00",
        forecast_peak_hour_utc=18,
        forecast_peak_time_utc="2026-07-10T18:00:00Z",
        forecast_timezone="Asia/Shanghai",
        forecast_timezone_abbreviation="CST",
        forecast_utc_offset_seconds=28800,
        forecast_generationtime_ms=1.2,
        forecast_model_fallback_reason=None,
        forecast_detected_at_utc=detected_at_utc,
    )


def test_hourly_curve_preserves_shortwave_radiation() -> None:
    curve = build_hourly_curve(
        ["2026-08-12T10:00", "2026-08-12T11:00"],
        [65.0, 67.0],
        shortwave_radiation_wm2=[125.0, 250.0],
    )

    assert curve[0]["shortwave_radiation_wm2"] == 125.0
    assert curve[1]["shortwave_radiation_wm2"] == 250.0


def test_curve_capture_is_immutable_and_preserves_exact_hash_first_seen(tmp_path) -> None:
    first = write_forecast_hourly_curve_capture(
        tmp_path,
        [_row("2026-07-11T01:00:00Z", detected_at_utc="2026-07-11T01:00:03Z")],
        available_at_utc=datetime(2026, 7, 11, 1, 0, 5, tzinfo=timezone.utc),
    )
    second = write_forecast_hourly_curve_capture(
        tmp_path,
        [_row("2026-07-11T02:00:00Z", detected_at_utc="2026-07-11T02:00:03Z")],
        available_at_utc=datetime(2026, 7, 11, 2, 0, 5, tzinfo=timezone.utc),
    )

    assert first != second
    assert first.exists() and second.exists()
    row = json.loads(second.read_text(encoding="utf-8"))
    assert row["schema_version"] == FORECAST_HOURLY_CURVE_SCHEMA_VERSION
    assert row["forecast_first_seen_utc"] == "2026-07-11T01:00:03.000000Z"
    assert row["forecast_first_seen_source"] == "historical_capture_first_seen"
    assert row["forecast_detected_at_utc"] == "2026-07-11T02:00:03Z"
    assert row["snapshot_ts_utc"] <= row["forecast_detected_at_utc"]
    assert row["forecast_first_seen_utc"] <= row["available_at_utc"]
    assert row["event_kind"] == "forecast_curve"
    assert row["pit_lineage_class"] == "collector_exact"
    assert row["material_state_change"] is False
    assert row["information_event_status"] == "non_material_duplicate_state"
    assert len(row["information_event_id"]) == 64
    assert row["forecast_run_ts_utc"] is None
    assert row["forecast_run_lineage_status"] == "source_response_does_not_expose_run_timestamp"
    assert row["forecast_model_fallback"] is False
    assert len(row["batch_capture_id"]) == 64
    assert row["source_capture_lineage_schema_version"] == "weather_source_capture_lineage_v1"
    assert row["producer_build_lineage_status"] in {"known", "unavailable"}
    assert row["hourly_curve"] == [
        {"time_local": "2026-07-11T00:00", "temperature_f": 70.0},
        {"time_local": "2026-07-11T02:00", "temperature_f": 72.125},
    ]


def test_new_hash_without_detected_time_first_seen_equals_available(tmp_path) -> None:
    snapshot = "2026-07-11T01:00:00Z"
    available = datetime(2026, 7, 11, 1, 0, 5, tzinfo=timezone.utc)
    path = write_forecast_hourly_curve_capture(
        tmp_path,
        [_row(snapshot, values_hash="hash-without-detected")],
        available_at_utc=available,
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["forecast_first_seen_utc"] == "2026-07-11T01:00:05.000000Z"
    assert row["forecast_first_seen_source"] == "current_capture_available_at"
    assert row["snapshot_ts_utc"] <= row["forecast_first_seen_utc"] <= row["available_at_utc"]


def test_legacy_snapshot_time_is_not_reused_as_first_seen(tmp_path) -> None:
    legacy_dir = tmp_path / "forecast_hourly_curves" / "2026-07-11"
    legacy_dir.mkdir(parents=True)
    legacy = {
        **_row("2026-07-11T00:00:00Z", values_hash="legacy-hash"),
        "schema_version": "forecast_hourly_curve_v1",
    }
    (legacy_dir / "forecast_hourly_curves_legacy.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    path = write_forecast_hourly_curve_capture(
        tmp_path,
        [_row("2026-07-11T01:00:00Z", values_hash="legacy-hash")],
        available_at_utc=datetime(2026, 7, 11, 1, 0, 5, tzinfo=timezone.utc),
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["forecast_first_seen_utc"] == "2026-07-11T01:00:05.000000Z"
    assert row["forecast_first_seen_utc"] != legacy["snapshot_ts_utc"]


def test_first_seen_scan_is_bounded_by_target_date(tmp_path) -> None:
    from weather_data_feed.forecast_hourly_curves import _load_first_seen

    old_dir = tmp_path / "forecast_hourly_curves" / "2026-01-01"
    old_dir.mkdir(parents=True)
    (old_dir / "forecast_hourly_curves_old.jsonl").write_text(
        '{"target_date":"2026-07-11","city":"Old","forecast_source":"x",'
        '"forecast_model":"gfs","forecast_values_hash":"old","available_at_utc":"2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    first_seen = _load_first_seen(
        tmp_path / "forecast_hourly_curves",
        target_dates={"2026-07-11"},
    )

    assert first_seen == {}


def test_curve_row_marks_model_fallback_explicitly() -> None:
    curve = build_hourly_curve(["2026-07-11T00:00"], [70.0])
    fallback = build_curve_row(
        snapshot_ts_utc="2026-07-11T01:00:00Z",
        city="Testville",
        target_date="2026-07-11",
        forecast_source="open_meteo_live_gfs",
        forecast_model="gfs",
        forecast_assigned_model="ecmwf",
        forecast_values_hash="hash-a",
        hourly_curve=curve,
        forecast_max_f=70.0,
        forecast_peak_hour_local=0,
        forecast_peak_time_local="2026-07-11T00:00",
        forecast_peak_hour_utc=16,
        forecast_peak_time_utc="2026-07-10T16:00:00Z",
        forecast_timezone="Asia/Shanghai",
        forecast_timezone_abbreviation="CST",
        forecast_utc_offset_seconds=28800,
        forecast_generationtime_ms=1.2,
        forecast_model_fallback_reason="forecast_fetch_unavailable",
    )

    assert fallback["forecast_model_fallback"] is True
    assert fallback["forecast_model_fallback_reason"] == "forecast_fetch_unavailable"

    summary = summarize_source_models([fallback], expected_city_target_count=2)
    assert summary == {
        "schema_version": "forecast_source_model_summary_v1",
        "grain": "city_target_forecast",
        "expected_city_target_count": 2,
        "captured_city_target_count": 1,
        "assigned_model_counts": {"ecmwf": 1},
        "actual_model_counts": {"gfs": 1},
        "fallback_count": 1,
        "fallback_reason_counts": {"forecast_fetch_unavailable": 1},
        "missing_count": 1,
    }
