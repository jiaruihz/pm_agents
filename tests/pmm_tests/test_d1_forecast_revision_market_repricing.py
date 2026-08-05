from __future__ import annotations

from weather_model_evaluation import d1_revision_repricing as subject


def test_markout_horizons_include_thin_book_short_windows() -> None:
    assert subject.MARKOUT_MINUTES == (5, 10, 30, 60, 90)


def test_lineage_impact_separates_backward_and_repeated_forward_rows() -> None:
    rows = [
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-05T00:00:00Z",
            "forecast_run_at_utc": "2026-08-04T18:00:00Z",
            "available_at_utc": "2026-08-05T01:00:00Z",
        },
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-04T18:00:00Z",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T01:01:00Z",
        },
        {
            "model_key": "gfs_global",
            "city": "Tokyo",
            "target_date": "2026-08-06",
            "previous_run_ts": "2026-08-04T18:00:00Z",
            "forecast_run_at_utc": "2026-08-05T00:00:00Z",
            "available_at_utc": "2026-08-05T01:31:00Z",
        },
    ]
    result = subject.lineage_impact(rows)
    assert result["backward_previous_run_rows"] == 1
    assert result["forward_previous_run_rows"] == 2
    assert result["unique_forward_transition_keys"] == 1
    assert result["repeated_forward_transition_rows"] == 1


def test_probability_markout_requires_identical_complete_ladders() -> None:
    before = {
        "market_distribution_complete": True,
        "probabilities": {"30 or below": 0.2, "31+": 0.8},
    }
    after = {
        "market_distribution_complete": True,
        "probabilities": {"30 or below": 0.4, "31+": 0.6},
    }
    result = subject._probability_markout(before, after)
    assert result["status"] == "scoreable"
    assert abs(result["total_variation"] - 0.2) < 1e-12
    assert abs(result["mean_rung_shift"] + 0.2) < 1e-12


def test_d1_checkpoint_policy_uses_city_local_target_midnight() -> None:
    policy, hours = subject.d1_checkpoint_policy(
        "2026-08-06", "2026-08-05T12:00:00Z", "Asia/Tokyo"
    )
    assert policy == "D-1_18_24"
    assert hours == -3.0
