import numpy as np
import pandas as pd

from weather_model_evaluation.market_offset_probability import (
    date_block_score_delta,
    logit,
    multi_grain_binary_score,
    predict_fixed_market_offset,
    select_market_offset_model,
)
from weather_modeling.amsterdam_market_offset import (
    add_amsterdam_evaluation_grains,
    add_amsterdam_market_offset_features,
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


def test_balanced_bounded_selection_records_grain_contract_and_caps_correction():
    frame = _frame()
    frame["is_transition"] = frame.groupby("target_date").cumcount().eq(0)
    frame["is_state_entry"] = frame.groupby("target_date").cumcount().le(1)
    artifact, selection = select_market_offset_model(
        frame,
        feature_sets={"simple": ["weather_disagreement"]},
        l2_grid=[0.01],
        fit_window=("2026-01-01", "2026-01-06"),
        validation_window=("2026-01-07", "2026-01-09"),
        refit_window=("2026-01-01", "2026-01-09"),
        market_probability_column="p_market",
        label_column="label",
        membership_columns=("is_transition", "is_state_entry"),
        correction_cap_grid=(0.1,),
    )
    test = frame[frame["target_date"].between("2026-01-10", "2026-01-12")]
    probability = predict_fixed_market_offset(artifact, test)
    correction = logit(probability) - logit(test["p_market"])

    assert artifact["schema_version"] == "fixed_market_logit_offset_v2"
    assert artifact["training_membership_columns"] == [
        "is_transition",
        "is_state_entry",
    ]
    assert artifact["correction_cap_logit"] == 0.1
    assert np.abs(correction).max() <= 0.1 + 1e-12
    assert "objective_logloss" in selection["selected"]["validation"]


def test_zero_cap_is_exact_market_baseline_and_multigrain_score_is_equal_weighted():
    frame = _frame()
    frame["is_transition"] = frame.groupby("target_date").cumcount().eq(0)
    frame["is_state_entry"] = frame.groupby("target_date").cumcount().le(1)
    artifact, _selection = select_market_offset_model(
        frame,
        feature_sets={"simple": ["weather_disagreement"]},
        l2_grid=[0.01],
        fit_window=("2026-01-01", "2026-01-06"),
        validation_window=("2026-01-07", "2026-01-09"),
        refit_window=("2026-01-01", "2026-01-09"),
        market_probability_column="p_market",
        label_column="label",
        membership_columns=("is_transition", "is_state_entry"),
        correction_cap_grid=(0.0,),
    )
    probability = predict_fixed_market_offset(artifact, frame)
    score = multi_grain_binary_score(
        frame,
        probability,
        label_column="label",
        membership_columns=("is_transition", "is_state_entry"),
    )

    assert np.allclose(probability, frame["p_market"])
    assert set(score["by_grain"]) == {
        "checkpoint",
        "is_transition",
        "is_state_entry",
    }
    assert all(np.isclose(weight, 1 / 3) for weight in score["grain_weights"].values())


def test_amsterdam_grains_include_path_regime_transitions_and_keep_input_order():
    frame = pd.DataFrame(
        {
            "row_id": [2, 1, 3],
            "target_date": ["2026-01-01"] * 3,
            "observed_at_utc": [
                "2026-01-01T10:20:00Z",
                "2026-01-01T10:10:00Z",
                "2026-01-01T10:30:00Z",
            ],
            "current_bracket_c": [10, 10, 10],
            "decline_from_running_max_c": [0.0, 0.0, 0.3],
            "ta_delta_30m_c": [0.3, 0.3, -0.1],
            "is_rebounding_after_pullback": [0, 0, 0],
        }
    )

    scored = add_amsterdam_evaluation_grains(frame)

    assert scored["row_id"].tolist() == [2, 1, 3]
    by_row = scored.set_index("row_id")
    assert bool(by_row.loc[1, "is_state_entry"])
    assert not bool(by_row.loc[2, "is_state_entry"])
    assert bool(by_row.loc[1, "is_transition"])
    assert not bool(by_row.loc[2, "is_transition"])
    assert bool(by_row.loc[3, "is_transition"])


def test_zero_correction_scale_is_market_and_market_logit_feature_is_available():
    frame = _frame()
    frame["p_model"] = np.where(frame["label"].eq(1), 0.8, 0.2)
    featured = add_amsterdam_market_offset_features(
        frame.rename(columns={"p_market": "market_p"}),
        required_features=["market_logit_level"],
    ).rename(columns={"market_p": "p_market"})
    artifact, selection = select_market_offset_model(
        featured,
        feature_sets={"calibrated_market": ["market_logit_level"]},
        l2_grid=[0.1],
        fit_window=("2026-01-01", "2026-01-06"),
        validation_window=("2026-01-07", "2026-01-09"),
        refit_window=("2026-01-01", "2026-01-09"),
        market_probability_column="p_market",
        label_column="label",
        correction_scale_grid=(0.0,),
    )

    probability = predict_fixed_market_offset(artifact, featured)

    assert np.allclose(probability, featured["p_market"])
    assert artifact["correction_scale"] == 0.0
    assert selection["correction_scale_grid"] == [0.0]
