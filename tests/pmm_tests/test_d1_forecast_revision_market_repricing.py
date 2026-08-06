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
            "event_class": "forward_provider_run_first_seen",
            "checkpoint_policy": "D-1_18_24",
            "model_revision_f": 1.0,
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
            "event_class": "forward_provider_run_first_seen",
            "checkpoint_policy": "D-1_18_24",
            "model_revision_f": -2.0,
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
        if row["scope"] == "forward_provider_run_first_seen"
        and row["revision_field"] == "consensus_median_revision_f"
        and row["horizon"] == "immediate"
    )
    assert immediate["events"] == 2
    assert immediate["direction_agreement_rate"] == 1.0
    assert abs(immediate["mean_directional_rung_shift"] - 0.25) < 1e-12


def test_provider_run_events_use_asof_model_arrivals_not_complete_batches() -> None:
    models = [
        ("gfs_global", 80.0),
        ("ecmwf_ifs025", 81.0),
        ("icon_seamless", 82.0),
        ("gem_global", 83.0),
        ("jma_gsm", 84.0),
    ]
    rows = []
    for index, (model, value) in enumerate(models):
        rows.append(
            {
                "schema_version": "weather_forecast_run_row_v2",
                "model_key": model,
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T00:00:00Z",
                "forecast_max_f": value,
                "first_seen_at_utc": f"2026-08-06T00:0{index}:00Z",
                "assigned_model": model == "gfs_global",
            }
        )
    rows.extend(
        [
            {
                "schema_version": "weather_forecast_run_row_v2",
                "model_key": "icon_seamless",
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T06:00:00Z",
                "forecast_max_f": 85.0,
                "first_seen_at_utc": "2026-08-06T01:00:00Z",
                "assigned_model": False,
            },
            {
                "schema_version": "weather_forecast_run_row_v3",
                "model_key": "gfs_global",
                "city": "Tokyo",
                "target_date": "2026-08-07",
                "horizon_days_local": 1,
                "forecast_run_at_utc": "2026-08-05T06:00:00Z",
                "forecast_max_f": 82.0,
                "run_first_seen_at_utc": "2026-08-06T01:10:00Z",
                "run_first_seen_status": "collector_exact",
                "assigned_model": True,
            },
        ]
    )
    events, summary = subject.build_provider_run_events(rows)
    assert summary["provider_run_transition_events"] == 2
    assert events[0]["event_class"] == "legacy_provider_run_earliest_observed"
    assert events[0]["model_key"] == "icon_seamless"
    assert events[0]["consensus_median_revision_f"] == 1.0
    assert events[1]["event_class"] == "forward_provider_run_first_seen"
    assert events[1]["assigned_model_revision_f"] == 2.0


def test_markout_does_not_compare_late_post_checkpoint_to_itself() -> None:
    event = {
        "city": "Tokyo",
        "target_date": "2026-08-07",
        "event_available_at_utc": "2026-08-06T00:00:00Z",
    }
    common = {
        "city": "Tokyo",
        "target_date": "2026-08-07",
        "market_distribution_complete": True,
        "probabilities": {"30": 0.4, "31+": 0.6},
    }
    checkpoints = [
        {
            **common,
            "checkpoint_ts_utc": "2026-08-05T23:59:00Z",
            "available_at_utc": "2026-08-05T23:59:30Z",
            "feature_book_snapshot_id": "pre",
        },
        {
            **common,
            "checkpoint_ts_utc": "2026-08-06T00:12:00Z",
            "available_at_utc": "2026-08-06T00:12:30Z",
            "feature_book_snapshot_id": "post",
        },
        {
            **common,
            "probabilities": {"30": 0.3, "31+": 0.7},
            "checkpoint_ts_utc": "2026-08-06T00:31:00Z",
            "available_at_utc": "2026-08-06T00:31:30Z",
            "feature_book_snapshot_id": "later",
        },
    ]
    result = subject.attach_market_evidence([event], checkpoints)[0]
    assert result["markout_5m_status"] == "post_checkpoint_after_markout_horizon"
    assert result["markout_10m_status"] == "post_checkpoint_after_markout_horizon"
    assert result["markout_30m_status"] == "scoreable"
    assert abs(result["markout_30m_mean_rung_shift"] - 0.1) < 1e-12
