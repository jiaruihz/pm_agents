from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/analysis/forecast_quality/build_d1_d2_run_aware_dataset_v1.py"
SPEC = importlib.util.spec_from_file_location("d1_d2_dataset", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_output_requires_stable_run_id() -> None:
    with pytest.raises(SystemExit, match="stable --run-id is required"):
        MODULE.main([])


def test_explicit_output_refuses_to_overwrite_prior_run(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "summary.json").write_text("{}", encoding="utf-8")
    forecast = tmp_path / "forecast.json"
    forecast.write_text('{"forecast_rows": []}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        MODULE.main(["--forecast-rows", str(forecast), "--output-dir", str(output)])


def test_signal_and_evidence_funnels_are_separate() -> None:
    forecast_rows = [
        {
            "batch_capture_id": "batch-d1",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "horizon_days_local": 1,
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-04T12:00:00Z",
            "first_seen_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T00:00:01Z",
        },
        {
            "batch_capture_id": "batch-d2",
            "city": "Tokyo",
            "target_date": "2026-08-07",
            "horizon_days_local": 2,
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-04T12:00:00Z",
            "first_seen_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T00:00:01Z",
        },
    ]
    batches = [
        {"batch_capture_id": "batch-d1", "city": "Tokyo", "target_date": "2026-08-06", "missing_model_keys": [], "model_count": 1},
        {"batch_capture_id": "batch-d2", "city": "Tokyo", "target_date": "2026-08-07", "missing_model_keys": [], "model_count": 1},
    ]
    settlements = [
        {"city": "Tokyo", "target_date": "2026-08-06", "settlement_complete": True, "settlement_native_tmax": 34, "settlement_unit": "C"},
        {"city": "Tokyo", "target_date": "2026-08-07", "settlement_complete": True, "settlement_native_tmax": 35, "settlement_unit": "C"},
    ]
    ladders = [
        {"city": "Tokyo", "target_date": "2026-08-06", "horizon_days": 1, "rung_completeness": True, "market_distribution_complete": True, "feature_book_snapshot_id": "book-1"}
    ]
    dataset, summary = MODULE.build_dataset(forecast_rows, batches, settlements, ladders)
    assert summary["signal_funnel"]["oof_scoreable"] == 2
    assert summary["evidence_funnel"]["market_complete"] == 1
    d2 = next(row for row in dataset if row["horizon_days_local"] == 2)
    assert d2["weather_only_status"] == "scoreable"
    assert d2["market_residual_status"] == "blocked"
    assert "d2_market_ladder_unavailable" in d2["market_residual_blockers"]


def test_unverified_run_is_not_oof_scoreable() -> None:
    rows = [{"batch_capture_id": "b", "city": "Tokyo", "target_date": "2026-08-06", "horizon_days_local": 1, "forecast_run_lineage_status": "blocked", "first_seen_at_utc": "2026-08-05T00:00:00Z", "available_at_utc": "2026-08-05T00:00:01Z"}]
    batches = [{"batch_capture_id": "b", "city": "Tokyo", "target_date": "2026-08-06", "missing_model_keys": []}]
    settlements = [{"city": "Tokyo", "target_date": "2026-08-06", "settlement_complete": True, "settlement_native_tmax": 34}]
    dataset, summary = MODULE.build_dataset(rows, batches, settlements, [])
    assert summary["signal_funnel"]["real_run_identified"] == 0
    assert dataset[0]["weather_only_blockers"] == ["real_run_unidentified"]


def test_repeated_poll_batches_collapse_and_use_full_batch_availability() -> None:
    rows = [
        {
            "batch_capture_id": "first",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "horizon_days_local": 1,
            "model_key": "gfs_global",
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "first_seen_at_utc": "2026-08-05T07:00:00Z",
            "available_at_utc": "2026-08-05T07:00:01Z",
        },
        {
            "batch_capture_id": "first",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "horizon_days_local": 1,
            "model_key": "ecmwf_ifs025",
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "first_seen_at_utc": "2026-08-05T07:00:02Z",
            "available_at_utc": "2026-08-05T07:00:03Z",
        },
        {
            "batch_capture_id": "repeat",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "horizon_days_local": 1,
            "model_key": "gfs_global",
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "first_seen_at_utc": "2026-08-05T07:00:00Z",
            "available_at_utc": "2026-08-05T07:30:01Z",
        },
        {
            "batch_capture_id": "repeat",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "horizon_days_local": 1,
            "model_key": "ecmwf_ifs025",
            "forecast_run_lineage_status": "identified",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "first_seen_at_utc": "2026-08-05T07:00:02Z",
            "available_at_utc": "2026-08-05T07:30:03Z",
        },
    ]
    batches = [
        {
            "batch_capture_id": batch_id,
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "batch_content_hash": "same-content",
            "missing_model_keys": [],
            "model_count": 2,
        }
        for batch_id in ("first", "repeat")
    ]
    dataset, summary = MODULE.build_dataset(rows, batches, [], [])
    assert len(dataset) == 1
    assert dataset[0]["delivery_count"] == 2
    assert dataset[0]["available_at_utc"] == "2026-08-05T07:00:03Z"
    assert summary["signal_funnel"]["raw_forecast_batches"] == 2
    assert summary["signal_funnel"]["duplicate_poll_batches_collapsed"] == 1
