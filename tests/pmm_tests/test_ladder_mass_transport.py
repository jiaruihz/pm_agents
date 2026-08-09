from __future__ import annotations

import numpy as np
import pandas as pd

from weather_model_evaluation import ladder_mass_transport as subject


def test_feature_blocks_are_nested_and_static_kink_is_control_only() -> None:
    assert set(subject.M0) < set(subject.M1) < set(subject.M2) < set(subject.M3)
    assert "kink_score" not in subject.M3
    assert set(subject.STATIC_KINK) == set(subject.M0) | {"kink_score"}


def test_relative_target_removes_common_ladder_move() -> None:
    frame = pd.DataFrame(
        {
            "ladder_snapshot_id": ["a", "a", "a"],
            "h60_mid_move": [0.03, 0.04, 0.05],
        }
    )
    frame["common"] = frame.groupby("ladder_snapshot_id")["h60_mid_move"].transform("median")
    relative = frame["h60_mid_move"] - frame["common"]
    assert np.allclose(relative, [-0.01, 0.0, 0.01])


def test_date_ci_resamples_target_dates() -> None:
    rows = pd.DataFrame({"target_date": ["d1", "d1", "d2", "d2", "d3", "d3"], "delta": [-1, -1, -2, -2, -3, -3]})
    point, low, high = subject._date_ci(rows, "delta", 500, 7)
    assert point == -2.0
    assert high < 0

