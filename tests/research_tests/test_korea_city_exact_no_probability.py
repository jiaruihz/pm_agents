from __future__ import annotations

import numpy as np
import pandas as pd

from weather_model_evaluation.korea_city_exact_no import (
    add_preferred_path_features,
    bounded_market_posterior,
    effective_exact_quote,
)


def test_effective_exact_quote_uses_mirrored_tokens() -> None:
    books = [
        {
            "bracket": "31",
            "outcome": "yes",
            "status": "ok",
            "condition_id": "c",
            "token_id": "yes",
            "fetched_at_utc": "2026-08-12T01:00:02Z",
            "summary": {"best_ask": 0.44, "ask_size": 8.0},
        },
        {
            "bracket": "31",
            "outcome": "no",
            "status": "ok",
            "condition_id": "c",
            "token_id": "no",
            "fetched_at_utc": "2026-08-12T01:00:02Z",
            "summary": {
                "best_ask": 0.58,
                "ask_size": 7.0,
                "best_bid": 0.56,
                "bid_size": 9.0,
            },
        },
        {
            "bracket": "31+",
            "outcome": "no",
            "status": "ok",
            "summary": {"best_ask": 0.10, "ask_size": 1.0},
        },
    ]
    quote = effective_exact_quote(books, 31)
    assert quote["book_status"] == "scorable"
    assert np.isclose(quote["market_yes_bid"], 0.42)
    assert np.isclose(quote["market_yes_ask"], 0.44)
    assert np.isclose(quote["market_no_p"], 0.57)
    assert np.isclose(quote["no_ask"], 0.58)
    assert np.isclose(quote["effective_no_ask"], 0.58)
    assert quote["no_ask_size"] == 7.0
    assert quote["no_token_id"] == "no"


def test_bounded_market_posterior_endpoints() -> None:
    market = np.asarray([0.2, 0.8])
    weather = np.asarray([0.8, 0.2])
    assert np.allclose(bounded_market_posterior(market, weather, 0.0), market)
    assert np.allclose(bounded_market_posterior(market, weather, 1.0), weather)
    halfway = bounded_market_posterior(market, weather, 0.5)
    assert np.allclose(halfway, [0.5, 0.5])


def test_preferred_path_orders_revisions_by_pit_availability() -> None:
    frame = pd.DataFrame(
        [
            {
                "source_event_key": "initial",
                "city": "Seoul",
                "target_date": "2026-08-01",
                "source_observation_ts_utc": "2026-08-01T00:00:00Z",
                "decision_ts_utc": "2026-08-01T00:01:00Z",
                "preferred_runway_temp_c": 20.0,
                "max_runway_temp_c": 20.0,
                "max_runway_running_max_c": 20.0,
                "routine_rung": 20,
            },
            {
                "source_event_key": "late_revision",
                "city": "Seoul",
                "target_date": "2026-08-01",
                "source_observation_ts_utc": "2026-08-01T00:00:00Z",
                "decision_ts_utc": "2026-08-01T00:10:00Z",
                "preferred_runway_temp_c": 30.0,
                "max_runway_temp_c": 30.0,
                "max_runway_running_max_c": 30.0,
                "routine_rung": 30,
            },
            {
                "source_event_key": "next_observation",
                "city": "Seoul",
                "target_date": "2026-08-01",
                "source_observation_ts_utc": "2026-08-01T00:05:00Z",
                "decision_ts_utc": "2026-08-01T00:06:00Z",
                "preferred_runway_temp_c": 21.0,
                "max_runway_temp_c": 21.0,
                "max_runway_running_max_c": 21.0,
                "routine_rung": 21,
            },
        ]
    )
    result = add_preferred_path_features(frame)
    assert result["source_event_key"].tolist() == [
        "initial",
        "next_observation",
        "late_revision",
    ]
    next_row = result[result["source_event_key"].eq("next_observation")].iloc[0]
    assert next_row["preferred_running_max_c"] == 21.0
