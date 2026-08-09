from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_data_feed_prod_health_check import (
    check_forecast_hourly_curves,
    check_fast_observation_state,
    check_observation_cache,
    check_snapshot_orderbook_coverage,
    check_live_orders,
    check_snapshot_city_state_coverage,
    check_snapshot_duplicates,
    check_snapshot_source_model,
    check_telemetry,
    overall_status,
)
from weather_data_feed.forecast_hourly_curves import build_curve_row, write_forecast_hourly_curve_capture


def _history_rows(cache: dict, batch_id: str) -> list[dict]:
    generated = cache["generated_at_utc"]
    return [
        {
            **row,
            "record_type": "weather_observation_cache_record",
            "producer": "weather_data_feed_service.observations",
            "producer_build_id": "test-build",
            "batch_capture_id": batch_id,
            "observation_history_id": f"{batch_id}:{index}",
            "available_at_utc": generated,
            "ingested_at_utc": generated,
            "observation_cache_generated_at_utc": generated,
        }
        for index, row in enumerate(cache["records"])
    ]


def _write_observation_history(
    history,
    cache: dict,
    *,
    prefix_rows: list[dict] | None = None,
) -> None:
    latest_rows = _history_rows(cache, "latest-batch")
    all_rows = [*(prefix_rows or []), *latest_rows]
    history.write_text(
        "".join(json.dumps(item) + "\n" for item in all_rows), encoding="utf-8"
    )
    day_path = history.parent / cache["generated_at_utc"][:10] / history.name
    day_path.parent.mkdir(parents=True)
    day_path.write_text(
        "".join(json.dumps(item) + "\n" for item in latest_rows), encoding="utf-8"
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


def test_prod_health_check_fails_incomplete_snapshot_orderbook_coverage(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "records": [],
                "orderbook_enrichment_summary": {
                    "status": "incomplete",
                    "scope": "strategy_live",
                    "budget_sec": 120,
                    "spent_sec": 120.2,
                    "target_count": 100,
                    "target_ok_count": 90,
                    "target_incomplete_count": 10,
                    "target_status_counts": {"ok": 90, "orderbook_budget_exhausted": 10},
                },
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_orderbook_coverage(snapshot)

    assert report["status"] == "incomplete"
    assert report["target_incomplete_count"] == 10


def test_prod_health_check_fails_stale_fast_observation_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"updated_at_utc": "2026-07-18T03:30:00Z"}), encoding="utf-8")
    report = check_fast_observation_state(
        state,
        now_utc=datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc),
        max_age_min=3,
    )
    assert report["status"] == "stale"
    assert report["age_min"] == 30.0

    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": False},
        "fast_observation_state": report,
        "telemetry": [],
        "live_orders": {},
        "summaries": [],
    }
    assert overall_status(sections) == "fail"


def test_prod_health_check_uses_live_cross_as_active_fast_route():
    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": False},
        "fast_observation_state": {"status": "stale"},
        "live_cross_observation_state": {"status": "ok"},
        "telemetry": [],
        "live_orders": {},
        "summaries": [],
    }

    assert overall_status(sections) == "ok"


def test_prod_health_check_fails_recent_running_max_regression(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    now = datetime(2026, 7, 19, 10, 20, tzinfo=timezone.utc)
    row = {
        "city": "Singapore",
        "target_date": "2026-07-19",
        "station": "WSSS",
        "status": "ok",
        "current_temp_c": 31.0,
        "running_max_c": 31.0,
        "age_min": 5.0,
    }
    cache_payload = {"generated_at_utc": "2026-07-19T10:19:00Z", "records": [row]}
    cache.write_text(json.dumps(cache_payload), encoding="utf-8")
    earlier_cache = {
        "generated_at_utc": "2026-07-19T10:10:00Z",
        "records": [{**row, "running_max_c": 32.0}],
    }
    _write_observation_history(
        history,
        cache_payload,
        prefix_rows=_history_rows(earlier_cache, "earlier-batch"),
    )

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=now,
        max_cache_age_min=3.0,
        max_observation_age_min=120.0,
    )

    assert report["status"] == "fail"
    assert report["recent_running_max_regression_count"] == 1
    assert report["history_tail_rows"] == 1000


def test_prod_health_check_warns_on_fresh_reused_observation(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    cache_payload = {
                "generated_at_utc": "2026-07-19T10:19:00Z",
                "records": [
                    {
                        "city": "Singapore",
                        "target_date": "2026-07-19",
                        "station": "WSSS",
                        "status": "reused_after_fetch_error",
                        "current_temp_c": 31.0,
                        "running_max_c": 32.0,
                        "age_min": 15.0,
                    }
                ],
            }
    cache.write_text(json.dumps(cache_payload), encoding="utf-8")
    _write_observation_history(history, cache_payload)

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=datetime(2026, 7, 19, 10, 20, tzinfo=timezone.utc),
        max_cache_age_min=3.0,
        max_observation_age_min=120.0,
    )

    assert report["status"] == "warn"
    assert report["reused_record_count"] == 1


def test_prod_health_check_only_warns_for_stale_inactive_city(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    cache_payload = {
                "generated_at_utc": "2026-08-07T17:12:00Z",
                "records": [
                    {
                        "city": "Denver",
                        "target_date": "2026-08-07",
                        "station": "KBKF",
                        "status": "ok",
                        "current_temp_c": 24.9,
                        "running_max_c": 24.9,
                        "age_min": 134.0,
                    }
                ],
            }
    cache.write_text(json.dumps(cache_payload), encoding="utf-8")
    _write_observation_history(history, cache_payload)

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=datetime(2026, 8, 7, 17, 13, tzinfo=timezone.utc),
        max_cache_age_min=3.0,
        max_observation_age_min=120.0,
        required_cities={"Shanghai"},
    )

    assert report["status"] == "warn"
    assert report["invalid_record_count"] == 1
    assert report["blocking_invalid_record_count"] == 0
    assert report["inactive_invalid_record_count"] == 1


def test_prod_health_check_warns_during_expected_first_observation_gap(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    cache_payload = {
                "generated_at_utc": "2026-07-29T05:17:00Z",
                "records": [
                    {
                        "city": "Chicago",
                        "target_date": "2026-07-29",
                        "station": "KORD",
                        "status": "awaiting_first_observation",
                        "local_day_elapsed_min": 17,
                        "first_observation_grace_min": 90,
                    }
                ],
            }
    cache.write_text(json.dumps(cache_payload), encoding="utf-8")
    _write_observation_history(history, cache_payload)

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=datetime(2026, 7, 29, 5, 18, tzinfo=timezone.utc),
        max_cache_age_min=3.0,
        max_observation_age_min=120.0,
    )

    assert report["status"] == "warn"
    assert report["invalid_record_count"] == 0
    assert report["awaiting_first_observation_cities"] == ["Chicago"]


def test_prod_health_check_fails_when_fresh_cache_has_no_append_only_history(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    cache.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-08-09T14:19:00Z",
                "records": [
                    {
                        "city": "Seoul",
                        "target_date": "2026-08-09",
                        "station": "RKSI",
                        "status": "ok",
                        "current_temp_c": 31.0,
                        "running_max_c": 33.0,
                        "last_obs_utc": "2026-08-09T14:00:00Z",
                        "age_min": 19.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=datetime(2026, 8, 9, 14, 20, tzinfo=timezone.utc),
        max_cache_age_min=3.0,
        max_history_age_min=3.0,
        max_observation_age_min=120.0,
    )

    assert report["status"] == "fail"
    assert report["history_exists"] is False
    assert report["latest_history_batch_matches_cache"] is False


def test_prod_health_check_fails_history_contract_or_daily_parity_drift(tmp_path):
    cache = tmp_path / "latest.json"
    history = tmp_path / "observations.jsonl"
    cache_payload = {
        "generated_at_utc": "2026-08-09T14:19:00Z",
        "records": [
            {
                "city": "Seoul",
                "target_date": "2026-08-09",
                "station": "RKSI",
                "status": "ok",
                "current_temp_c": 31.0,
                "running_max_c": 33.0,
                "last_obs_utc": "2026-08-09T14:00:00Z",
                "age_min": 19.0,
            }
        ],
    }
    cache.write_text(json.dumps(cache_payload), encoding="utf-8")
    _write_observation_history(history, cache_payload)
    history_rows = [json.loads(line) for line in history.read_text().splitlines()]
    history_rows[0].pop("producer_build_id")
    history.write_text(json.dumps(history_rows[0]) + "\n", encoding="utf-8")
    daily = history.parent / "2026-08-09" / history.name
    daily.write_text("", encoding="utf-8")

    report = check_observation_cache(
        cache,
        history_path=history,
        now_utc=datetime(2026, 8, 9, 14, 20, tzinfo=timezone.utc),
        max_cache_age_min=3.0,
        max_history_age_min=3.0,
        max_observation_age_min=120.0,
    )

    assert report["status"] == "fail"
    assert report["history_missing_required_fields"] == {"producer_build_id": 1}
    assert report["daily_history_parity"] is False


def test_prod_health_check_flags_missing_same_day_weather_state(tmp_path):
    snapshot = tmp_path / "snapshot_20260707_1200.json"
    rows = [
        {
            "city": "Chengdu",
            "target_date": "2026-07-07",
            "city_local_date_at_snapshot": "2026-07-07",
            "metar_current_max_f": 98.6,
            "metar_latest_temp_f": 98.6,
            "forecast_peak_delta_hours_local": 0.75,
            "forecast_max_native": 37.2,
        },
        {
            "city": "Manila",
            "target_date": "2026-07-07",
            "city_local_date_at_snapshot": "2026-07-07",
            "live_observation_source": "aviationweather_metar",
            "forecast_peak_delta_hours_local": 0.75,
            "forecast_max_native": 34.0,
        },
    ]
    snapshot.write_text(
        json.dumps(
            {
                "city_pools": {"Chengdu": "t1_trading", "Manila": "t1_trading"},
                "records": rows,
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_city_state_coverage(snapshot)

    assert report["status"] == "missing_same_day_weather_state"
    assert report["same_local_day_city_count"] == 2
    assert report["missing_required_cities"] == ["Manila"]
    assert report["missing_required_by_field"]["metar_current_max_f"] == ["Manila"]
    assert report["missing_required_trading_cities"] == ["Manila"]


def test_snapshot_source_model_health_validates_lineage_without_rejecting_fallback(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "records": [],
                "source_model_summary": {
                    "schema_version": "forecast_source_model_summary_v1",
                    "grain": "city_target_forecast",
                    "expected_city_target_count": 2,
                    "captured_city_target_count": 2,
                    "assigned_model_counts": {"ecmwf": 2},
                    "actual_model_counts": {"ecmwf": 1, "gfs": 1},
                    "fallback_count": 1,
                    "fallback_reason_counts": {"forecast_fetch_unavailable": 1},
                    "missing_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_source_model(snapshot)

    assert report["status"] == "ok"
    assert report["fallback_detected"] is True
    assert report["lineage_errors"] == []


def test_snapshot_source_model_counts_explicit_cached_curve_as_effective_coverage(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "records": [],
                "source_model_summary": {
                    "schema_version": "forecast_source_model_summary_v1",
                    "grain": "city_target_forecast",
                    "expected_city_target_count": 3,
                    "captured_city_target_count": 2,
                    "effective_city_target_count": 3,
                    "cached_curve_fallback_count": 1,
                    "assigned_model_counts": {"ecmwf": 2},
                    "actual_model_counts": {"ecmwf": 2},
                    "fallback_count": 0,
                    "fallback_reason_counts": {},
                    "missing_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_source_model(snapshot)

    assert report["status"] == "ok"
    assert report["lineage_errors"] == []


def test_prod_health_check_warns_for_non_trading_weather_state_gap(tmp_path):
    snapshot = tmp_path / "snapshot_20260707_1200.json"
    rows = [
        {
            "city": "Denver",
            "target_date": "2026-07-07",
            "city_local_date_at_snapshot": "2026-07-07",
            "forecast_peak_delta_hours_local": 0.75,
            "forecast_max_native": 94.0,
        },
    ]
    snapshot.write_text(
        json.dumps(
            {
                "city_pools": {"Denver": "t2_research"},
                "records": rows,
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_city_state_coverage(snapshot)

    assert report["status"] == "missing_non_trading_weather_state"
    assert report["missing_required_trading_cities"] == []
    assert report["missing_required_non_trading_cities"] == ["Denver"]


def test_prod_health_check_does_not_treat_supported_registry_as_active_universe(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "city_pools": {"Tokyo": "t1_trading", "Boston": "t1_trading"},
                "city_models": {"Tokyo": "gfs", "Boston": "gfs"},
                "records": [{"city": "Tokyo", "target_date": "2026-08-04"}],
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_city_state_coverage(snapshot)

    assert report["expectation_basis"] == "snapshot_records"
    assert report["registered_city_count"] == 2
    assert report["missing_record_cities"] == []


def test_prod_health_check_honors_explicit_active_city_contract(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "active_cities": ["Tokyo", "Boston"],
                "city_pools": {"Tokyo": "t1_trading", "Boston": "t1_trading"},
                "records": [{"city": "Tokyo", "target_date": "2026-08-04"}],
            }
        ),
        encoding="utf-8",
    )

    report = check_snapshot_city_state_coverage(snapshot)

    assert report["expectation_basis"] == "active_cities"
    assert report["missing_record_cities"] == ["Boston"]
    assert report["status"] == "missing_record_cities"


def test_prod_health_check_requires_current_complete_curve_capture(tmp_path):
    snapshot_ts = "2026-07-11T03:00:00Z"
    snapshot = tmp_path / "snapshot_20260711_1100.json"
    snapshot.write_text(
        json.dumps(
            {
                "ts_utc": snapshot_ts,
                "records": [{"city": "Shanghai", "target_date": "2026-07-11"}],
            }
        ),
        encoding="utf-8",
    )
    row = build_curve_row(
        snapshot_ts_utc=snapshot_ts,
        city="Shanghai",
        target_date="2026-07-11",
        forecast_source="open_meteo_live_gfs",
        forecast_model="gfs",
        forecast_assigned_model="gfs",
        forecast_values_hash="hash-a",
        hourly_curve=[{"time_local": "2026-07-11T12:00", "temperature_f": 88.0}],
        forecast_max_f=88.0,
        forecast_peak_hour_local=12,
        forecast_peak_time_local="2026-07-11T12:00",
        forecast_peak_hour_utc=4,
        forecast_peak_time_utc="2026-07-11T04:00:00Z",
        forecast_timezone="Asia/Shanghai",
        forecast_timezone_abbreviation="CST",
        forecast_utc_offset_seconds=28800,
        forecast_generationtime_ms=1.0,
        forecast_model_fallback_reason=None,
        forecast_detected_at_utc="2026-07-11T03:00:02Z",
    )
    write_forecast_hourly_curve_capture(
        tmp_path,
        [row],
        available_at_utc=datetime(2026, 7, 11, 3, 0, 4, tzinfo=timezone.utc),
    )

    report = check_forecast_hourly_curves(
        tmp_path / "forecast_hourly_curves",
        snapshot,
        now_utc=datetime(2026, 7, 11, 3, 5, tzinfo=timezone.utc),
        max_age_min=45,
    )

    assert report["status"] == "ok"
    assert report["capture_city_target_count"] == 1
    assert report["early_first_seen_count"] == 0
    assert report["future_first_seen_count"] == 0

    curve_path = next((tmp_path / "forecast_hourly_curves").glob("*/forecast_hourly_curves_*.jsonl"))
    curve_row = json.loads(curve_path.read_text(encoding="utf-8"))
    curve_row["forecast_first_seen_utc"] = "2026-07-11T02:59:59Z"
    curve_path.write_text(json.dumps(curve_row) + "\n", encoding="utf-8")
    early = check_forecast_hourly_curves(
        tmp_path / "forecast_hourly_curves",
        snapshot,
        now_utc=datetime(2026, 7, 11, 3, 5, tzinfo=timezone.utc),
        max_age_min=45,
    )
    assert early["status"] == "invalid_lineage"
    assert early["early_first_seen_count"] == 1

    curve_row["forecast_first_seen_utc"] = "2026-07-11T03:00:05Z"
    curve_path.write_text(json.dumps(curve_row) + "\n", encoding="utf-8")
    future = check_forecast_hourly_curves(
        tmp_path / "forecast_hourly_curves",
        snapshot,
        now_utc=datetime(2026, 7, 11, 3, 5, tzinfo=timezone.utc),
        max_age_min=45,
    )
    assert future["status"] == "invalid_lineage"
    assert future["future_first_seen_count"] == 1


def test_prod_health_check_accepts_verified_cached_curve_reuse(tmp_path):
    capture_ts = "2026-07-11T03:00:00Z"
    row = build_curve_row(
        snapshot_ts_utc=capture_ts,
        city="Shanghai",
        target_date="2026-07-11",
        forecast_source="open_meteo_live_gfs",
        forecast_model="gfs",
        forecast_assigned_model="gfs",
        forecast_values_hash="hash-a",
        hourly_curve=[{"time_local": "2026-07-11T12:00", "temperature_f": 88.0}],
        forecast_max_f=88.0,
        forecast_peak_hour_local=12,
        forecast_peak_time_local="2026-07-11T12:00",
        forecast_peak_hour_utc=4,
        forecast_peak_time_utc="2026-07-11T04:00:00Z",
        forecast_timezone="Asia/Shanghai",
        forecast_timezone_abbreviation="CST",
        forecast_utc_offset_seconds=28800,
        forecast_generationtime_ms=1.0,
        forecast_model_fallback_reason=None,
        forecast_detected_at_utc="2026-07-11T03:00:02Z",
    )
    archive = write_forecast_hourly_curve_capture(
        tmp_path,
        [row],
        available_at_utc=datetime(2026, 7, 11, 3, 0, 4, tzinfo=timezone.utc),
    )
    snapshot = tmp_path / "snapshot_20260711_1110.json"
    snapshot.write_text(
        json.dumps(
            {
                "ts_utc": "2026-07-11T03:10:00Z",
                "records": [
                    {
                        "city": "Shanghai",
                        "target_date": "2026-07-11",
                        "forecast_values_hash": "hash-a",
                        "forecast_curve_evidence": "cached_durable_curve",
                        "forecast_curve_archive_path": str(archive),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    newer_row = build_curve_row(
        snapshot_ts_utc="2026-07-11T03:12:00Z",
        city="Beijing",
        target_date="2026-07-11",
        forecast_source="open_meteo_live_ecmwf",
        forecast_model="ecmwf",
        forecast_assigned_model="ecmwf",
        forecast_values_hash="hash-newer",
        hourly_curve=[{"time_local": "2026-07-11T12:00", "temperature_f": 90.0}],
        forecast_max_f=90.0,
        forecast_peak_hour_local=12,
        forecast_peak_time_local="2026-07-11T12:00",
        forecast_peak_hour_utc=4,
        forecast_peak_time_utc="2026-07-11T04:00:00Z",
        forecast_timezone="Asia/Shanghai",
        forecast_timezone_abbreviation="CST",
        forecast_utc_offset_seconds=28800,
        forecast_generationtime_ms=1.0,
        forecast_model_fallback_reason=None,
        forecast_detected_at_utc="2026-07-11T03:12:02Z",
    )
    write_forecast_hourly_curve_capture(
        tmp_path,
        [newer_row],
        available_at_utc=datetime(2026, 7, 11, 3, 12, 4, tzinfo=timezone.utc),
    )

    report = check_forecast_hourly_curves(
        tmp_path / "forecast_hourly_curves",
        snapshot,
        now_utc=datetime(2026, 7, 11, 3, 15, tzinfo=timezone.utc),
        max_age_min=45,
    )
    assert report["status"] == "ok"
    assert report["cached_reuse_complete"] is True
    assert report["cached_reuse_city_target_count"] == 1

    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["records"][0]["forecast_values_hash"] = "wrong-hash"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    broken = check_forecast_hourly_curves(
        tmp_path / "forecast_hourly_curves",
        snapshot,
        now_utc=datetime(2026, 7, 11, 3, 15, tzinfo=timezone.utc),
        max_age_min=45,
    )
    assert broken["status"] == "snapshot_mismatch"
    assert broken["invalid_cached_reuse_count"] == 1


def test_prod_health_check_allows_reused_run_id_but_flags_duplicate_decisions(tmp_path):
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

    assert report["telemetry_run_id_count"] == 1
    assert report["max_rows_per_telemetry_run_id"] == 2
    assert report["duplicate_decision_count"] == 1
    assert report["missing_required_fields"] == {}


def test_prod_health_overall_status_warns_on_stale_but_fails_on_structural_errors():
    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": True},
        "telemetry": [{"parse_error_count": 0, "duplicate_decision_count": 0}],
        "live_orders": {"parse_error_count": 0, "duplicate_order_id_count": 0, "duplicate_strategy_city_token_count": 0},
        "summaries": [],
    }
    assert overall_status(sections) == "warn"

    sections["snapshot_duplicates"]["duplicate_record_count"] = 1
    assert overall_status(sections) == "fail"


def test_live_order_check_uses_only_production_declared_extra_files(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    legacy = live_dir / "theta_current_yes_tiny_live_v1_orders.jsonl"
    current = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    active_runtime = tmp_path / "late_window_live_orders.jsonl"
    legacy.write_text("{}\n", encoding="utf-8")
    current.write_text("{}\n", encoding="utf-8")
    active_runtime.write_text("{}\n", encoding="utf-8")

    report = check_live_orders(live_dir, tail_rows=10, extra_files=[active_runtime])

    assert report["scope"] == "active_live_order_files"
    assert report["files"] == [str(active_runtime)]

    all_report = check_live_orders(live_dir, tail_rows=10, all_files=True)
    assert str(legacy) in all_report["files"]
    assert str(current) in all_report["files"]


def test_live_order_check_treats_lifecycle_replacements_as_single_active_tip(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    base = {
        "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
        "city": "Shanghai",
        "target_date": "2026-07-08",
        "token_id": "yes-token",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "status": "submitted",
        "exchange_response": {"place": {"success": True, "status": "live"}},
    }
    original = {**base, "exchange_response": {"place": {"orderID": "old-order", "success": True, "status": "live"}}}
    replacement = {
        **base,
        "execution_action": "maker_lifecycle_reprice_maker",
        "source_order_id": "old-order",
        "exchange_response": {
            "place": {"orderID": "new-order", "success": True, "status": "live"},
            "pre_place_cancel_response": {"cancel": {"canceled": ["old-order"], "not_canceled": {}}},
        },
    }
    path.write_text(json.dumps(original) + "\n" + json.dumps(replacement) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        extra_files=[path],
        now_utc=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 1
    assert report["replaced_order_id_count"] == 1
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_live_order_check_excludes_confirmed_zero_fill_retry_parent(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "orders.jsonl"
    base = {
        "schema_version": "fast_source_prev_no_trial_v2",
        "strategy_instance": "fast_source_prev_no_trial_v1",
        "city": "Singapore",
        "target_date": "2026-08-04",
        "token_id": "no-token",
        "signal_side": "BUY_NO",
        "order_side": "BUY",
        "child_order_role": "taker",
        "execution_policy": "depth_retry",
        "status": "cross_candidate",
    }
    canceled_parent = {
        **base,
        "order_id": "parent",
        "exchange_order_status": "canceled",
        "immediate_cancel_confirmed": True,
        "actual_fill_shares": 0.0,
        "exchange_response": {"place": {"success": True, "status": "live"}},
    }
    filled_retry = {
        **base,
        "order_id": "retry",
        "retry_parent_order_id": "parent",
        "exchange_order_status": "matched",
        "actual_fill_shares": 13.5,
        "exchange_response": {"place": {"success": True, "status": "matched"}},
    }
    path.write_text(
        json.dumps(canceled_parent) + "\n" + json.dumps(filled_retry) + "\n",
        encoding="utf-8",
    )

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        extra_files=[path],
        now_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 1
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_live_order_check_separates_historical_duplicates_from_current_risk(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    old_row = {
        "strategy_instance": "low_price_yes_lottery_tiny_live_v1",
        "city": "Shanghai",
        "target_date": "2026-07-05",
        "token_id": "old-token",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "status": "submitted",
    }
    path.write_text(json.dumps(old_row) + "\n" + json.dumps(old_row) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        all_files=True,
        now_utc=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert report["duplicate_strategy_city_token_count"] == 1
    assert report["duplicate_current_strategy_city_token_count"] == 0
    assert report["current_yes_no_conflict_count"] == 0


def test_overall_status_does_not_warn_on_historical_only_order_duplicates():
    sections = {
        "snapshot_parity": {"status": "ok"},
        "snapshot_duplicates": {"duplicate_record_count": 0, "snapshot_stale": False},
        "telemetry": [],
        "live_orders": {
            "parse_error_count": 0,
            "duplicate_order_id_count": 0,
            "duplicate_strategy_city_token_count": 50,
            "duplicate_current_strategy_city_token_count": 0,
            "current_yes_no_conflict_count": 0,
        },
        "summaries": [],
    }

    assert overall_status(sections) == "ok"


def test_live_order_check_excludes_blocked_attempts_from_current_risk(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    blocked = {
        "strategy_instance": "d1_yes_high_mid_live_v1",
        "city": "Madrid",
        "target_date": "2026-07-18",
        "token_id": "yes-token",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "status": "blocked",
        "execution_policy": "d1_yes_high_mid_taker_v1",
        "child_order_role": "d1_maker_next_observation_taker_fallback",
    }
    path.write_text(json.dumps(blocked) + "\n" + json.dumps(blocked) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        all_files=True,
        now_utc=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 0
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_live_order_check_allows_authorized_taker_maker_split(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    base = {
        "strategy_instance": "d1_yes_high_mid_live_v1",
        "city": "Madrid",
        "target_date": "2026-07-18",
        "token_id": "yes-token",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "status": "submitted",
    }
    taker = {
        **base,
        "execution_policy": "d1_yes_high_mid_taker_v1",
        "child_order_role": "taker",
        "exchange_response": {"place": {"success": True, "status": "matched", "orderID": "taker-order"}},
    }
    maker = {
        **base,
        "execution_policy": "d1_yes_high_mid_maker_v1",
        "child_order_role": "maker",
        "exchange_response": {"place": {"success": True, "status": "matched", "orderID": "maker-order"}},
    }
    path.write_text(json.dumps(taker) + "\n" + json.dumps(maker) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        all_files=True,
        now_utc=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 2
    assert report["duplicate_current_execution_identity_count"] == 0
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_live_order_check_allows_distinct_source_events_to_share_market_cap(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "orders.jsonl"
    base = {
        "strategy_instance": "fast_source_prev_no_trial_v1",
        "city": "Seoul",
        "target_date": "2026-08-09",
        "token_id": "no-token",
        "signal_side": "BUY_NO",
        "order_side": "BUY",
        "execution_policy": "fast_source_visible_depth_hot_retry_v1",
        "child_order_role": "taker",
        "status": "cross_candidate",
        "actual_fill_shares": 5.0,
        "max_shares_per_market": 10.0,
        "exchange_order_status": "matched",
    }
    first = {
        **base,
        "event_key": "Seoul|2026-08-09|06:19",
        "execution_key": "Seoul|2026-08-09|06:19|taker|0",
        "order_id": "order-1",
    }
    second = {
        **base,
        "event_key": "Seoul|2026-08-09|06:20",
        "execution_key": "Seoul|2026-08-09|06:20|taker|0",
        "order_id": "order-2",
    }
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        all_files=True,
        now_utc=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 2
    assert report["duplicate_current_strategy_city_token_count"] == 0


def test_live_order_check_flags_current_duplicate_and_yes_no_conflict(tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_dir / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
    yes_row = {
        "strategy_instance": "probe",
        "city": "Chengdu",
        "target_date": "2026-07-07",
        "market_id": "market-1",
        "bracket": "37",
        "token_id": "yes-token",
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "status": "submitted",
        "exchange_response": {"place": {"success": True, "status": "matched", "orderID": "yes-order"}},
    }
    no_row = {
        **yes_row,
        "token_id": "no-token",
        "signal_side": "BUY_NO",
        "exchange_response": {"place": {"success": True, "status": "matched", "orderID": "no-order"}},
    }
    path.write_text(json.dumps(yes_row) + "\n" + json.dumps(yes_row) + "\n" + json.dumps(no_row) + "\n", encoding="utf-8")

    report = check_live_orders(
        live_dir,
        tail_rows=10,
        all_files=True,
        now_utc=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert report["duplicate_current_execution_identity_count"] == 1
    assert report["duplicate_current_strategy_city_token_count"] == 1
    assert report["current_yes_no_conflict_count"] == 1
