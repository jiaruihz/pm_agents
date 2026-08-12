import numpy as np
import pandas as pd

from weather_model_evaluation.market_offset_probability import (
    date_block_score_delta,
    predict_fixed_market_offset,
    select_market_offset_model,
)


def _frame() -> pd.DataFrame:
    rows = []
    for day in range(1, 13):
        market = 0.25 if day % 2 else 0.75
        label = int(day % 2 == 0)
        for checkpoint in range(3):
            rows.append(
                {
                    "target_date": f"2026-01-{day:02d}",
                    "p_market": market,
                    "label": label,
                    "weather_disagreement": (1 if label else -1)
                    * (0.8 + checkpoint * 0.1),
                }
            )
    return pd.DataFrame(rows)


def test_fixed_market_offset_selection_and_prediction_are_causal_by_window():
    frame = _frame()
    artifact, selection = select_market_offset_model(
        frame,
        feature_sets={"simple": ["weather_disagreement"]},
        l2_grid=[0.01, 0.1],
        fit_window=("2026-01-01", "2026-01-06"),
        validation_window=("2026-01-07", "2026-01-09"),
        refit_window=("2026-01-01", "2026-01-09"),
        market_probability_column="p_market",
        label_column="label",
    )
    test = frame[frame["target_date"].between("2026-01-10", "2026-01-12")]
    probability = predict_fixed_market_offset(artifact, test)

    assert selection["fit_dates"] == 6
    assert selection["validation_dates"] == 3
    assert artifact["training_dates"] == 9
    assert np.isfinite(probability).all()
    assert ((probability >= 0.5) == test["label"].astype(bool)).all()


def test_date_block_delta_uses_target_dates_not_row_bootstrap():
    frame = _frame().iloc[:12].copy()
    candidate = np.where(frame["label"].eq(1), 0.9, 0.1)
    result = date_block_score_delta(
        frame,
        candidate,
        frame["p_market"],
        label_column="label",
        draws=200,
        seed=7,
    )

    assert result["target_dates"] == 4
    assert result["brier"]["delta"] < 0
    assert result["logloss"]["delta"] < 0
