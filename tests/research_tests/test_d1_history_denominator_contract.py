from pathlib import Path

import pandas as pd
import pytest

from scripts.analysis.forecast_quality.research_d1_cross_city_hierarchy_v1 import (
    history_denominator_funnel,
    main,
    model_assignments,
)


def test_hierarchy_default_output_requires_stable_run_id() -> None:
    with pytest.raises(SystemExit, match="stable --run-id is required"):
        main([])


def test_history_denominator_funnel_does_not_call_training_slice_full_history() -> None:
    history = pd.DataFrame(
        [
            {"date": "2024-01-01", "city": "A", "model": "gfs", "is_best_model": True, "month_num": 1},
            {"date": "2024-05-01", "city": "A", "model": "gfs", "is_best_model": True, "month_num": 5},
            {"date": "2024-05-01", "city": "A", "model": "ecmwf", "is_best_model": False, "month_num": 5},
            {"date": "2026-08-01", "city": "B", "model": "gfs", "is_best_model": True, "month_num": 8},
        ]
    )

    funnel = history_denominator_funnel(
        history,
        "2026-07-01",
        input_artifact=Path("custom-history.csv"),
    )

    assert funnel["artifact_input"]["rows"] == 4
    assert funnel["before_test_start"]["rows"] == 3
    assert funnel["best_model_only"]["rows"] == 2
    assert funnel["best_model_summer_training_slice"]["rows"] == 1
    assert funnel["project_history_complete"] is False
    assert funnel["denominator_scope"].endswith("training_slice")


def test_authoritative_assignment_does_not_depend_on_legacy_best_flag() -> None:
    history = pd.DataFrame(
        [
            {"date": "2024-05-01", "city": "Amsterdam", "model": "ecmwf", "is_best_model": False},
            {"date": "2024-05-01", "city": "Amsterdam", "model": "gfs", "is_best_model": True},
            {"date": "2024-05-01", "city": "Paris", "model": "gfs", "is_best_model": False},
            {"date": "2024-05-01", "city": "Paris", "model": "ecmwf", "is_best_model": True},
        ]
    )

    assigned = model_assignments(history, "authoritative_city_model")

    assert assigned.set_index("city")["model"].to_dict() == {
        "Amsterdam": "ecmwf",
        "Paris": "gfs",
    }
