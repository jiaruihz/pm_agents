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


def test_directional_repricing_flips_downward_revision_sign() -> None:
    events = [
        {
            "target_date": "2026-08-06",
            "event_class": "forward_new_complete_run",
            "checkpoint_policy": "D-1_18_24",
            "consensus_median_revision_f": 1.0,
            "assigned_model_revision_f": 1.0,
            "immediate_market_status": "scoreable",
            "immediate_mean_rung_shift": 0.20,
            **{
                f"markout_{minutes}m_status": "scoreable"
                for minutes in subject.MARKOUT_MINUTES
            },
            **{
                f"markout_{minutes}m_mean_rung_shift": 0.10
                for minutes in subject.MARKOUT_MINUTES
            },
        },
        {
            "target_date": "2026-08-07",
            "event_class": "forward_new_complete_run",
            "checkpoint_policy": "D-1_18_24",
            "consensus_median_revision_f": -2.0,
            "assigned_model_revision_f": -2.0,
            "immediate_market_status": "scoreable",
            "immediate_mean_rung_shift": -0.30,
            **{
                f"markout_{minutes}m_status": "scoreable"
                for minutes in subject.MARKOUT_MINUTES
            },
            **{
                f"markout_{minutes}m_mean_rung_shift": -0.20
                for minutes in subject.MARKOUT_MINUTES
            },
        },
    ]

    rows = subject.directional_repricing_summary(events)
    immediate = next(
        row
        for row in rows
        if row["scope"] == "forward_new_complete_run"
        and row["revision_field"] == "consensus_median_revision_f"
        and row["horizon"] == "immediate"
    )
    assert immediate["events"] == 2
    assert immediate["direction_agreement_rate"] == 1.0
    assert abs(immediate["mean_directional_rung_shift"] - 0.25) < 1e-12
