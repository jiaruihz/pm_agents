from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import forecast_repricing_quote_ev as subject


def _event() -> pd.DataFrame:
    rows = []
    for index, bracket in enumerate(("19 or below", "20", "21+")):
        rows.append(
            {
                "forecast_event_id": "event-1",
                "city": "London",
                "target_date": "2026-08-08",
                "condition_id": f"c{index}",
                "bracket": bracket,
                "lead_days": 1,
                "snapshot_epoch": 1.0,
                "model_probability_before": (0.2, 0.5, 0.3)[index],
                "model_probability_after": (0.1, 0.4, 0.5)[index],
                "market_probability_before": (0.25, 0.5, 0.25)[index],
                "market_probability_after": (0.20, 0.45, 0.35)[index],
                "entry_bid": (0.01, 0.44, 0.34)[index],
                "entry_ask": (0.04, 0.46, 0.36)[index],
                "entry_bid_size": 10.0,
                "entry_ask_size": 11.0,
                "h30_bid": (0.01, 0.43, 0.39)[index],
                "h60_bid": (0.03, 0.41, 0.44)[index],
                "h30_window_min_ask": (0.011, 0.45, 0.35)[index],
            }
        )
    return pd.DataFrame(rows)


def test_quote_actions_are_unique_tick_legal_and_strictly_post_only() -> None:
    quotes = subject.materialize_quote_actions(_event())
    assert not quotes.duplicated([*subject.IDENTITY_COLUMNS, "quote_price"]).any()
    assert quotes["quote_price"].lt(quotes["entry_ask"]).all()
    tick_units = quotes["quote_price"] / quotes["native_entry_tick"]
    assert np.allclose(tick_units, tick_units.round())
    low = quotes.loc[quotes["condition_id"].eq("c0")]
    assert low["native_entry_tick"].eq(0.001).all()
    for expected in (0.01, 0.011, 0.013, 0.015):
        assert np.isclose(low["quote_price"], expected).any()


def test_proxy_label_uses_quote_price_not_best_bid_and_keeps_no_touch_zero() -> None:
    quotes = subject.materialize_quote_actions(_event())
    low = quotes.loc[
        quotes["condition_id"].eq("c0") & quotes["quote_price"].eq(0.011)
    ].iloc[0]
    assert bool(low["ask_touch_30_proxy"])
    expected = 0.03 - subject.weather_fee(0.03) - 0.011
    assert low["touch_conditional_net_pnl_60"] == pytest.approx(expected)
    untouched = quotes.loc[
        quotes["condition_id"].eq("c0") & quotes["quote_price"].eq(0.01)
    ].iloc[0]
    assert not bool(untouched["ask_touch_30_proxy"])
    assert untouched["proxy_expected_pnl_label"] == pytest.approx(0.0)


def test_no_quote_is_a_first_class_runtime_decision() -> None:
    features = pd.DataFrame([[0.0] * len(subject.QUOTE_FEATURES), [1.0] * len(subject.QUOTE_FEATURES)])
    direct = DummyRegressor(strategy="constant", constant=-0.01).fit(features, [-0.01, -0.01])
    touch = DummyClassifier(strategy="constant", constant=1).fit(features, [0, 1])
    value = DummyRegressor(strategy="constant", constant=-0.02).fit(features, [-0.02, -0.02])
    bundle = {
        "schema_version": subject.SCHEMA_VERSION,
        "model_id": subject.MODEL_ID,
        "direct_model": direct,
        "market_direct_model": direct,
        "market_static_direct_model": direct,
        "touch_model": touch,
        "value_model": value,
        "signal_ttl_min": 30,
    }
    rungs = []
    for row in _event().to_dict(orient="records"):
        rungs.append(
            {
                **row,
                "yes_bid": row["entry_bid"],
                "yes_ask": row["entry_ask"],
                "yes_bid_size": row["entry_bid_size"],
                "yes_ask_size": row["entry_ask_size"],
                "tick_size": 0.001 if row["entry_bid"] < 0.04 else 0.01,
            }
        )
    scored, selected = subject.score_runtime_quote_ev(
        rungs,
        bundle,
        event_identity={
            "forecast_event_id": "runtime-event",
            "city": "London",
            "target_date": "2026-08-08",
            "lead_days": 1,
            "snapshot_epoch": 1.0,
        },
    )
    assert scored
    assert selected is None
    assert max(row["predicted_agreement_proxy_ev"] for row in scored) < 0.0


@pytest.mark.parametrize("bad_tick", [None, float("nan"), 0.0, -0.01])
def test_runtime_requires_exchange_tick_metadata(bad_tick: float | None) -> None:
    features = pd.DataFrame([[0.0] * len(subject.QUOTE_FEATURES), [1.0] * len(subject.QUOTE_FEATURES)])
    direct = DummyRegressor(strategy="constant", constant=-0.01).fit(features, [-0.01, -0.01])
    touch = DummyClassifier(strategy="constant", constant=1).fit(features, [0, 1])
    bundle = {
        "schema_version": subject.SCHEMA_VERSION,
        "model_id": subject.MODEL_ID,
        "direct_model": direct,
        "market_direct_model": direct,
        "market_static_direct_model": direct,
        "touch_model": touch,
        "value_model": direct,
        "signal_ttl_min": 30,
    }
    row = _event().iloc[0].to_dict()
    if bad_tick is not None:
        row["tick_size"] = bad_tick
    with pytest.raises(ValueError, match="tick_size"):
        subject.score_runtime_quote_ev(
            [{**row, "yes_bid": row["entry_bid"], "yes_ask": row["entry_ask"]}],
            bundle,
            event_identity={
                "forecast_event_id": "runtime-event",
                "city": "London",
                "target_date": "2026-08-08",
                "lead_days": 1,
                "snapshot_epoch": 1.0,
            },
        )


def test_position_clock_requires_fill_and_uses_fill_relative_timeout() -> None:
    with pytest.raises(ValueError, match="actual fill"):
        subject.score_runtime_quote_position({}, elapsed_fill_minutes=1.0)
    position = {"actual_fill_id": "fill-1", "filled_at_utc": "2026-08-08T00:00:00Z"}
    assert subject.score_runtime_quote_position(position, elapsed_fill_minutes=59.9)["action"] == "HOLD"
    assert subject.score_runtime_quote_position(position, elapsed_fill_minutes=60.0)["action"] == "EXIT"
