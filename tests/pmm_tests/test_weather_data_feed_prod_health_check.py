from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops.weather_data_feed_prod_health_check import (
    check_forecast_hourly_curves,
    check_fast_observation_state,
    check_live_orders,
    check_snapshot_city_state_coverage,
    check_snapshot_duplicates,
    check_snapshot_source_model,
    check_telemetry,
    overall_status,
)
from weather_data_feed.forecast_hourly_curves import build_curve_row, write_forecast_hourly_curve_capture


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


def test_live_order_check_defaults_to_active_runtime_files_only(tmp_path):
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
    assert report["files"] == [str(current), str(active_runtime)]

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
        now_utc=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert report["effective_current_or_future_rows"] == 1
    assert report["replaced_order_id_count"] == 1
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

    assert report["duplicate_current_strategy_city_token_count"] == 1
    assert report["current_yes_no_conflict_count"] == 1
