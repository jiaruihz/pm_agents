from __future__ import annotations

from weather_data_feed_service.forecast_enrichment import (
    enrich_forecast_version_lineage,
    forecast_batch_summaries,
    materialize_run_contract_capture,
)
from weather_data_feed.forecast_run_contract import EXACT_SINGLE_RUN_ENDPOINT


def _version(run: str, value: float, *, model: str = "gfs_global", capture: str = "capture") -> dict:
    return {
        "producer": "forecast-test",
        "capture_id": capture,
        "forecast_version_hash": f"hash-{run}-{value}",
        "city": "Shanghai",
        "forecast_target_date": "2026-08-06",
        "timezone_name": "Asia/Shanghai",
        "model_key": model,
        "model_label": model,
        "assigned_model_family": "gfs",
        "forecast_max_f": value,
        "captured_at_utc": "2026-08-05T13:00:00Z",
        "available_at_utc": "2026-08-05T13:00:03Z",
        "source_fetch_start_utc": "2026-08-05T13:00:01Z",
        "source_fetch_end_utc": "2026-08-05T13:00:03Z",
        "source_raw_payload_hash": "raw",
        "forecast_run_at_utc": run,
        "forecast_run_lineage_status": "explicit_single_run_requested_and_returned",
    }


def test_provider_run_revision_and_same_run_content_revision_are_separate() -> None:
    first, state = enrich_forecast_version_lineage(
        [
            _version("2026-08-05T00:00:00Z", 90.0, capture="run-00"),
            _version("2026-08-05T06:00:00Z", 92.0, capture="run-06"),
        ],
        state={}, batch_id="batch-1", producer_build="sha",
        ingested_at_utc="2026-08-05T13:00:04Z",
    )
    later_run = next(row for row in first if row["forecast_run_at_utc"].endswith("06:00:00Z"))
    assert later_run["previous_run_ts"] == "2026-08-05T00:00:00Z"
    assert later_run["run_to_run_revision_f"] == 2.0
    assert later_run["content_revision_f"] is None

    revised, _ = enrich_forecast_version_lineage(
        [_version("2026-08-05T06:00:00Z", 92.5, capture="run-06-revised")],
        state=state, batch_id="batch-2", producer_build="sha",
        ingested_at_utc="2026-08-05T13:01:04Z",
    )
    assert revised[0]["previous_run_ts"] is None
    assert revised[0]["run_to_run_revision_f"] is None
    assert revised[0]["content_revision_f"] == 0.5


def test_batch_summary_persists_missing_models_and_assigned_delta() -> None:
    rows = [
        _version("2026-08-05T06:00:00Z", 92.0, model="gfs_global", capture="gfs"),
        _version("2026-08-05T06:00:00Z", 90.0, model="ecmwf_ifs025", capture="ecmwf"),
    ]
    summary = forecast_batch_summaries(
        rows, batch_id="batch",
        expected_model_keys={"gfs_global", "ecmwf_ifs025", "icon_seamless"},
    )[0]
    assert summary["mean_f"] == 91.0
    assert summary["spread_f"] == 2.0
    assert summary["assigned_model_key"] == "gfs_global"
    assert summary["assigned_minus_consensus_f"] == 1.0
    assert summary["model_keys_missing"] == ["icon_seamless"]


def test_strict_contract_accepts_exact_request_and_blocks_generic_endpoint() -> None:
    exact = _version("2026-08-05T06:00:00Z", 92.0, capture="exact")
    exact.update(
        {
            "batch_capture_id": "batch",
            "single_run_request_endpoint": EXACT_SINGLE_RUN_ENDPOINT,
            "single_run_request_hash": "request",
            "single_run_request_succeeded": True,
            "single_run_fallback_applied": False,
            "producer_build_id": "sha",
        }
    )
    generic = {
        **_version("", 91.0, model="ecmwf_ifs025", capture="generic"),
        "batch_capture_id": "batch",
        "forecast_run_at_utc": None,
        "forecast_run_lineage_status": "provider_run_unavailable_collector_versioned",
    }
    rows, _, _ = materialize_run_contract_capture([exact, generic])
    by_model = {row["model_key"]: row for row in rows}
    assert by_model["gfs_global"]["forecast_run_lineage_status"] == "identified"
    assert by_model["gfs_global"]["forecast_run_evidence"] == "exact_single_run_request"
    assert by_model["ecmwf_ifs025"]["forecast_run_lineage_status"] == "blocked"
