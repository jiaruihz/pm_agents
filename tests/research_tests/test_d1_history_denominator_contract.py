from pathlib import Path

import pandas as pd

from scripts.analysis.forecast_quality.research_d1_cross_city_hierarchy_v1 import (
    history_denominator_funnel,
)


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
