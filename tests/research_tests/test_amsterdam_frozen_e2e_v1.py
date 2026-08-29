from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality.research_amsterdam_frozen_e2e_v1 import (
    SUPPORT,
    _exact_market_join,
    _fit_layer_b,
    _weather_metrics,
)


def _base() -> pd.DataFrame:
    return pd.DataFrame({"decision_vintage_id": ["a", "b"], "market_id": ["m", "m"], "condition_id": ["c", "c"], "token_id": ["x", "y"]})


def test_exact_identity_join_preserves_missing_rows() -> None:
    joined = _exact_market_join(_base(), pd.DataFrame({"decision_vintage_id": ["a"], "market_id": ["m"], "condition_id": ["c"], "token_id": ["x"], "quote": [.5]}))
    assert len(joined) == 2
    assert joined.market_join_reason.tolist() == ["EXACT_IDENTITY_MATCHED", "market_row_missing"]
    assert np.isnan(joined.loc[1, "quote"])


def test_exact_join_refuses_fuzzy_token_match() -> None:
    joined = _exact_market_join(_base(), pd.DataFrame({"decision_vintage_id": ["a"], "market_id": ["m"], "condition_id": ["c"], "token_id": ["WRONG"], "quote": [.5]}))
    assert joined.market_join_reason.eq("market_row_missing").all()


def test_weather_metrics_uses_same_scored_denominator() -> None:
    probs = {f"b2_p_delta_{tick:+d}": [0.0, 0.0] for tick in SUPPORT}
    probs["b2_p_delta_+0"] = [1.0, 0.0]
    probs["b2_p_delta_+1"] = [0.0, 1.0]
    frame = pd.DataFrame({"next_official_delta_native_tick": [0, 1], "target_date": ["2026-01-01", "2026-01-02"], "model_score_weight": [.5, .5], **probs})
    result = _weather_metrics(frame, "B2")
    assert result["rows"] == 2 and result["exact_accuracy"] == 1.0 and result["rps"] == 0.0


def test_weather_metrics_defaults_to_equal_weight() -> None:
    probs = {f"b2_p_delta_{tick:+d}": [0.0] for tick in SUPPORT}
    probs["b2_p_delta_+0"] = [1.0]
    frame = pd.DataFrame({"next_official_delta_native_tick": [0], "target_date": ["2026-01-01"], **probs})
    result = _weather_metrics(frame, "B2")
    assert result["status"] == "OK" and result["exact_accuracy"] == 1.0


def test_layer_b_has_nonoverlapping_target_dates_and_no_future_fit() -> None:
    frame = pd.DataFrame({"target_date": ["2026-01-01"] * 3 + ["2026-01-02"] * 3, "settlement_label": [0, 1, 0, 1, 0, 1], "market_probability": [.1, .9, .2, .8, .2, .8], "b2_p_up": [.1, .8, .3, .7, .2, .9]})
    scored, status = _fit_layer_b(frame)
    assert status["status"] == "OK"
    assert status["design_rank"] == status["required_design_rank"] == 3
    assert status["mr0_market_only_eval"]["status"] == "OK"
    assert status["mr1_market_plus_b2_eval"]["status"] == "OK"
    assert set(status["train_target_dates"]).isdisjoint(status["eval_target_dates"])
    assert scored.loc[scored.target_date.eq("2026-01-01"), "mr1_prediction"].isna().all()


def test_layer_b_estimability_gate_fails_closed() -> None:
    frame = pd.DataFrame({"target_date": ["2026-01-01"] * 2, "settlement_label": [0, 1], "market_probability": [.1, .9], "b2_p_up": [.2, .8]})
    _, status = _fit_layer_b(frame)
    assert status["status"] == "NOT_ESTIMABLE"


def test_layer_b_rank_deficiency_fails_closed() -> None:
    frame = pd.DataFrame({
        "target_date": ["2026-01-01"] * 4 + ["2026-01-02"] * 2,
        "settlement_label": [0, 1, 0, 1, 0, 1],
        "market_probability": [.1, .9, .2, .8, .2, .8],
        "b2_p_up": [.1, .9, .2, .8, .2, .8],
    })
    _, status = _fit_layer_b(frame)
    assert status["status"] == "NOT_ESTIMABLE"
    assert status["design_rank"] < status["required_design_rank"]


def test_layer_b_is_deterministic() -> None:
    frame = pd.DataFrame({"target_date": ["2026-01-01"] * 3 + ["2026-01-02"] * 3, "settlement_label": [0, 1, 0, 1, 0, 1], "market_probability": [.1, .9, .2, .8, .2, .8], "b2_p_up": [.1, .8, .3, .7, .2, .9]})
    first, _ = _fit_layer_b(frame)
    second, _ = _fit_layer_b(frame)
    assert first.mr1_prediction.equals(second.mr1_prediction)
