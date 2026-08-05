from __future__ import annotations

from datetime import datetime, timezone

from weather_data_feed_service.forecast_run_capture import (
    latest_cycle_candidate,
    materialize_capture,
)


def _response() -> dict[str, object]:
    return {
        "hourly": {
            "time": [
                "2026-08-06T12:00",
                "2026-08-06T13:00",
                "2026-08-07T12:00",
                "2026-08-07T13:00",
            ],
            "temperature_2m": [86.0, 88.0, 87.0, 89.0],
        }
    }


def test_latest_cycle_candidate_is_request_only() -> None:
    assert latest_cycle_candidate(datetime(2026, 8, 5, 5, 59, tzinfo=timezone.utc)) == "2026-08-05T00:00"


def test_capture_materializes_d1_d2_and_missing_models() -> None:
    rows, batches, state = materialize_capture(
        run="2026-08-04T12:00",
        captured_at_utc=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        city_inputs=[{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        responses_by_model={"gfs_global": [_response()]},
        metadata_by_model={"gfs_global": {"raw_hash": "raw", "request_key": "request"}},
        expected_models=["gfs_global", "ecmwf_ifs025"],
    )
    assert [row["horizon_days_local"] for row in rows] == [1, 2]
    assert all(row["forecast_run_lineage_status"] == "identified" for row in rows)
    assert all(batch["missing_model_keys"] == ["ecmwf_ifs025"] for batch in batches)
    assert state["latest_by_model_city_target"]


def test_run_transition_and_same_run_revision_are_separate() -> None:
    base_args = {
        "captured_at_utc": datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
        "city_inputs": [{"city": "Tokyo", "timezone_name": "Asia/Tokyo"}],
        "responses_by_model": {"gfs_global": [_response()]},
        "metadata_by_model": {"gfs_global": {"raw_hash": "raw-1", "request_key": "request-1"}},
        "expected_models": ["gfs_global"],
    }
    first, _, state = materialize_capture(run="2026-08-04T12:00", **base_args)
    changed_response = _response()
    changed_response["hourly"]["temperature_2m"][1] = 88.5
    revised, _, state = materialize_capture(
        run="2026-08-04T12:00",
        **{**base_args, "captured_at_utc": datetime(2026, 8, 5, 3, 10, tzinfo=timezone.utc), "responses_by_model": {"gfs_global": [changed_response]}, "metadata_by_model": {"gfs_global": {"raw_hash": "raw-2", "request_key": "request-1"}}, "previous_state": state},
    )
    d1_revision = next(row for row in revised if row["horizon_days_local"] == 1)
    assert d1_revision["content_revision_delta_f"] == 0.5
    assert d1_revision["run_to_run_delta_f"] is None
    transitioned, _, _ = materialize_capture(
        run="2026-08-04T18:00",
        **{**base_args, "captured_at_utc": datetime(2026, 8, 5, 3, 20, tzinfo=timezone.utc), "metadata_by_model": {"gfs_global": {"raw_hash": "raw-3", "request_key": "request-2"}}, "previous_state": state},
    )
    d1_transition = next(row for row in transitioned if row["horizon_days_local"] == 1)
    assert d1_transition["run_to_run_delta_f"] == -0.5
    assert d1_transition["previous_content_hash"] is None
