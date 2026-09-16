from __future__ import annotations

import json

import numpy as np

from scripts.analysis.market_structure_edge.research_tokyo_continuous_ladder_probability_v1 import (
    absolute_probabilities,
    build_continuous_rows,
    map_absolute_to_market,
    select_trades,
)


class _ProbabilityModel:
    def __init__(self) -> None:
        self.named_steps = {
            "model": type("Model", (), {"classes_": np.asarray([29, 30, 31])})()
        }

    def predict_proba(self, _features: np.ndarray) -> np.ndarray:
        return np.asarray([[0.2, 0.3, 0.5]])


def _base_feature_row() -> dict[str, object]:
    return {
        "target_date": "2026-07-01",
        "decision_ts_utc": "2026-07-01T01:00:00+00:00",
        "local_hour": 10,
        "prior_metar_running_max_c": 30.0,
        "prior_metar_temp_c": 30.0,
        "final_metar_rounded_c": 31.0,
        "jma_temp_c": 30.4,
        "jma_running_max_c": 30.4,
        "pit_provenance": "test",
    }


def test_continuous_label_is_relative_to_strict_metar_lower_bound() -> None:
    rows = build_continuous_rows([_base_feature_row()])

    assert len(rows) == 1
    assert rows[0]["current_bracket"] == 30
    assert rows[0]["remaining_rise_class"] == 1
    assert rows[0]["historical_lower_bound_violation"] == 0


def test_absolute_model_masks_passed_brackets_and_renormalizes() -> None:
    rows = build_continuous_rows([_base_feature_row()])
    classes, probabilities = absolute_probabilities(
        _ProbabilityModel(), rows
    )

    assert classes.tolist() == [29, 30, 31]
    assert probabilities[0, 0] == 0
    assert np.allclose(probabilities[0, 1:], [0.375, 0.625])
    assert np.isclose(probabilities[0].sum(), 1)


def test_market_distribution_uses_same_lower_bound_constraint() -> None:
    quotes = {
        "29": {"mid": 0.4},
        "30": {"mid": 0.3},
        "31": {"mid": 0.2},
        "32": {"mid": 0.1},
    }
    mapped = map_absolute_to_market(
        {"29": 0.2, "30": 0.3, "31": 0.5}, quotes, current=30
    )

    assert mapped is not None
    labels, model, market = mapped
    assert labels == ["29", "30", "31", "32"]
    assert model[0] == 0
    assert market[0] == 0
    assert np.isclose(model.sum(), 1)
    assert np.isclose(market.sum(), 1)


def test_exact_bracket_no_loses_when_winner_is_same_bracket(
    monkeypatch,
) -> None:
    row = {
        "model": "test",
        "state_id": "state",
        "target_date": "2026-07-01",
        "availability_ts_utc": "2026-07-01T01:00:00+00:00",
        "snapshot_ts_utc": "2026-07-01T01:00:00+00:00",
        "expression_bracket": "30",
        "side": "NO",
        "fee_adjusted_edge": 0.1,
        "ask": 0.5,
        "fee_per_share": 0.0125,
        "winning_bracket": "30",
        "quotes_json": json.dumps({}),
    }
    monkeypatch.setattr(
        "scripts.analysis.market_structure_edge."
        "research_tokyo_continuous_ladder_probability_v1.raw_ask_size",
        lambda *_args: 10.0,
    )

    trades = select_trades([row], raw_root=None)  # type: ignore[arg-type]

    assert len(trades) == 1
    assert trades[0]["won"] == 0
    assert trades[0]["fee_adjusted_pnl_usd"] < 0
