from scripts.analysis.forecast_quality import research_d1_d2_weather_only_v2 as subject


def _challenger() -> dict:
    return {
        "model_identity": "d1_weather_only_probability_challenger",
        "spec_sha256": "abc",
    }


def test_d1_readiness_is_not_blocked_by_d2_coverage() -> None:
    rows = [
        {
            "oof_scoreable": True,
            "horizon_days_local": 1,
            "target_date": f"2026-08-{day:02d}",
        }
        for day in range(1, 6)
    ]

    model_rows, summary = subject.build_gate(
        rows,
        minimum_settled_target_dates=5,
        d1_challenger=_challenger(),
    )

    assert summary["weather_only_status"] == "partially_ready_by_horizon"
    assert summary["weather_only_status_by_horizon"] == {
        "1": "ready_for_inner_train",
        "2": "blocked_insufficient_clean_run_aware_history",
    }
    assert summary["d1_blocked_by_d2"] is False
    assert summary["learning_curve_by_horizon"]["1"] == {
        "settled_target_dates": 5,
        "diagnostic_milestones": [7, 14, 21],
        "formal_inner_train_milestone": 5,
        "next_milestone": None,
        "status": "formal_inner_train_ready",
    }
    assert summary["learning_curve_by_horizon"]["2"]["next_milestone"] == 5
    d1_rows = [
        row
        for row in model_rows
        if row["horizon_days_local"] == 1 and row["model_code"] != "L0"
    ]
    d2_rows = [row for row in model_rows if row["horizon_days_local"] == 2]
    assert all(row["status"] == "ready_for_inner_train" for row in d1_rows)
    assert all(row["status"] == "blocked_insufficient_clean_run_aware_history" for row in d2_rows)


def test_frozen_challenger_is_registered_only_for_d1() -> None:
    model_rows, summary = subject.build_gate(
        [],
        minimum_settled_target_dates=3,
        d1_challenger=_challenger(),
    )

    challenger_rows = [row for row in model_rows if row["model_code"] == "L0"]
    assert len(challenger_rows) == 1
    assert challenger_rows[0]["horizon_days_local"] == 1
    assert challenger_rows[0]["status"] == "locked_reference_only"
    assert challenger_rows[0]["forward_status"] == "reference_only_not_forward_candidate"
    assert summary["d1_frozen_challenger"]["spec_sha256"] == "abc"
    assert summary["d1_locked_w0_reference"]["spec_sha256"] == "abc"
    assert summary["w1_freeze_status"] == "not_frozen_pending_clean_development_results"
    assert summary["untouched_forward_status"] == "not_started_until_w1_freeze_timestamp"


def test_learning_curve_milestones_are_reported_before_formal_train() -> None:
    rows = [
        {"oof_scoreable": True, "horizon_days_local": 1, "target_date": f"2026-08-{day:02d}"}
        for day in range(1, 9)
    ]

    _, summary = subject.build_gate(
        rows,
        minimum_settled_target_dates=30,
        d1_challenger=_challenger(),
    )

    d1 = summary["learning_curve_by_horizon"]["1"]
    assert d1["settled_target_dates"] == 8
    assert d1["next_milestone"] == 14
    assert d1["status"] == "diagnostic_accumulating"
