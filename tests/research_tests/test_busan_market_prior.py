from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from weather_model_evaluation.busan_market_prior import (
    evaluate_busan_market_prior,
    evaluate_online_busan_market_prior,
    logit_shrunk_probability,
)


def _row(
    target_date: str,
    checkpoint: str,
    *,
    label: int,
    market: float,
    weather: float,
    ask: float,
    rung: int,
    minute: int = 0,
) -> dict[str, object]:
    decision = f"{target_date}T01:{minute:02d}:00Z"
    book = f"{target_date}T01:{minute:02d}:02Z"
    return {
        "city": "Busan",
        "target_date": target_date,
        "decision_ts_utc": decision,
        "checkpoint_id": checkpoint,
        "target_id": f"final_exact_{rung}_NO",
        "label_no": label,
        "market_p_no": market,
        "no_ask": ask,
        "no_ask_size": 8.0,
        "no_book_ts_utc": book,
        "routine_running_max_market_value": rung,
        "p_factorized_random_forest_full_weather": weather,
    }


def test_logit_shrink_endpoints_and_finite_extremes() -> None:
    market = np.array([0.0, 0.2, 1.0])
    weather = np.array([1.0, 0.8, 0.0])
    assert logit_shrunk_probability(
        market, weather, weather_weight=0.0
    ).tolist() == np.clip(market, 1e-5, 1 - 1e-5).tolist()
    assert logit_shrunk_probability(
        market, weather, weather_weight=1.0
    ).tolist() == np.clip(weather, 1e-5, 1 - 1e-5).tolist()
    posterior = logit_shrunk_probability(market, weather, weather_weight=0.125)
    assert np.isfinite(posterior).all()
    assert ((posterior > 0.0) & (posterior < 1.0)).all()
    with pytest.raises(ValueError, match="weather_weight"):
        logit_shrunk_probability(0.5, 0.5, weather_weight=1.1)


def test_evaluation_locks_same_market_denominator_and_first_rung_entry() -> None:
    frame = pd.DataFrame(
        [
            _row(
                "2026-08-10",
                "a",
                label=1,
                market=0.60,
                weather=0.90,
                ask=0.61,
                rung=34,
            ),
            _row(
                "2026-08-10",
                "b",
                label=1,
                market=0.61,
                weather=0.92,
                ask=0.62,
                rung=34,
                minute=15,
            ),
            _row(
                "2026-08-11",
                "c",
                label=0,
                market=0.40,
                weather=0.10,
                ask=0.90,
                rung=35,
            ),
        ]
    )
    noncausal = _row(
        "2026-08-11",
        "bad-clock",
        label=1,
        market=0.5,
        weather=0.8,
        ask=0.5,
        rung=36,
        minute=20,
    )
    noncausal["no_book_ts_utc"] = "2026-08-11T01:19:59Z"
    frame = pd.concat([frame, pd.DataFrame([noncausal])], ignore_index=True)

    result = evaluate_busan_market_prior(
        frame, weather_weight=0.5, bootstrap_draws=200, seed=10
    )

    assert len(result.predictions) == 3
    assert result.summary["denominator"]["noncausal_book_rows"] == 1
    assert result.summary["scores"]["market"]["rows"] == 3
    assert result.summary["scores"]["market_prior_posterior"]["rows"] == 3
    assert result.summary["fee_adjusted_taker_replay"]["orders"] == 1
    assert result.trades.iloc[0]["checkpoint_id"] == "a"
    assert result.trades.iloc[0]["market_feature_role"] == "prior_offset"


def test_online_weight_for_date_does_not_use_same_date_label() -> None:
    development = pd.DataFrame(
        [
            _row(
                "2026-08-08",
                "dev-1",
                label=1,
                market=0.8,
                weather=0.2,
                ask=0.81,
                rung=33,
            ),
            _row(
                "2026-08-09",
                "dev-2",
                label=0,
                market=0.2,
                weather=0.8,
                ask=0.21,
                rung=34,
            ),
        ]
    )
    evaluation = pd.DataFrame(
        [
            _row(
                "2026-08-10",
                "test",
                label=1,
                market=0.5,
                weather=0.9,
                ask=0.51,
                rung=35,
            )
        ]
    )
    flipped = evaluation.copy()
    flipped["label_no"] = 0

    first = evaluate_online_busan_market_prior(
        development, evaluation, bootstrap_draws=100
    )
    second = evaluate_online_busan_market_prior(
        development, flipped, bootstrap_draws=100
    )

    assert first.weight_history.iloc[0]["train_end"] == "2026-08-09"
    assert first.weight_history.iloc[0]["selected_weather_weight"] == 0.0
    assert (
        first.weight_history.iloc[0]["selected_weather_weight"]
        == second.weight_history.iloc[0]["selected_weather_weight"]
    )
    assert first.summary["paired_candidate_minus_market"]["logloss"][
        "ci_high"
    ] == 0.0
    assert first.summary["paired_candidate_minus_market"]["brier"][
        "ci_high"
    ] == 0.0
