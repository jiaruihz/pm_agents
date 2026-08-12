from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.reheat_risk import research_helsinki_market_expression_v2 as v2
from scripts.analysis.reheat_risk.evaluate_helsinki_market_expression_strategy import (
    sweep_asks,
)


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_date": ["a", "a", "b", "b"],
            "decision_ts_utc": pd.to_datetime(
                ["2026-01-01T00:00Z", "2026-01-01T00:10Z", "2026-01-02T00:00Z", "2026-01-02T00:10Z"]
            ),
            "official_running_max_c": [1, 2, 1, 2],
            "path_state": ["fade", "fade", "pullback", "pullback"],
            "market_probability": [0.2, 0.4, 0.6, 0.8],
            "q1_v7": [0.2, 0.3, 0.4, 0.5],
            "q2_v7": [0.3, 0.3, 0.3, 0.3],
            "q3_v7": [0.5, 0.4, 0.3, 0.2],
        }
    )


def test_balanced_objective_gives_each_grain_and_date_equal_mass() -> None:
    stacked = v2.balanced_training_rows(_rows())
    grain_mass = stacked.groupby("training_grain")["objective_weight"].sum()
    assert np.allclose(grain_mass / grain_mass.sum(), 1 / 3)
    for _, grain in stacked.groupby("training_grain"):
        date_mass = grain.groupby("target_date")["objective_weight"].sum()
        assert np.allclose(date_mass, date_mass.iloc[0])


def test_market_weather_joint_base_is_coherent_and_preserves_break_mass() -> None:
    rows = _rows()
    distribution = v2.market_weather_joint_base(rows)
    assert np.allclose(distribution.sum(axis=1), 1)
    assert np.all(distribution > 0)
    assert np.allclose(1 - distribution[:, 0], rows["market_probability"])


def test_date_x_entries_keeps_first_decision_per_date_and_x() -> None:
    rows = _rows()
    duplicate = rows.iloc[[0]].copy()
    duplicate["decision_ts_utc"] += pd.Timedelta(minutes=20)
    combined = pd.concat([rows, duplicate], ignore_index=True)
    selected = v2.date_x_entries(combined)
    assert len(selected) == 4
    first = selected.loc[
        selected["target_date"].eq("a")
        & selected["official_running_max_c"].eq(1),
        "decision_ts_utc",
    ].iloc[0]
    assert first == pd.Timestamp("2026-01-01T00:00Z")


def test_bounded_weather_residual_respects_logit_cap() -> None:
    rows = pd.DataFrame(
        {"market_probability": [0.8], "p_break_v7": [0.01]}
    )
    probability, joint = v2.predict_candidate(
        {"kind": "bounded_weather_market_residual", "logit_cap": 0.15}, rows
    )
    market_logit = np.log(0.8 / 0.2)
    model_logit = np.log(probability[0] / (1 - probability[0]))
    assert joint is None
    assert 0 < market_logit - model_logit <= 0.15


def test_strategy_sweeps_five_share_depth_and_fee_per_level() -> None:
    result = sweep_asks(
        [{"price": 0.40, "size": 2.0}, {"price": 0.50, "size": 5.0}],
        shares=5.0,
    )
    assert result is not None
    assert result["entry_vwap"] == pytest.approx(0.46)
    assert result["cash_cost"] > 5 * result["entry_vwap"]
