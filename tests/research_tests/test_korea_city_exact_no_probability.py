from __future__ import annotations

import numpy as np
import pandas as pd

from weather_model_evaluation.korea_city_exact_no import (
    add_preferred_path_features,
    bounded_market_posterior,
    effective_exact_quote,
)
from weather_model_evaluation.korea_city_distribution import (
    _snapshot_distribution,
    aggregate_market_distribution,
    geometric_market_posterior,
    remaining_heat_class,
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


def test_remaining_heat_class_preserves_lower_and_upper_tails() -> None:
    assert remaining_heat_class("29", 30) == "negative"
    assert remaining_heat_class("30", 30) == "zero"
    assert remaining_heat_class("31", 30) == "plus1"
    assert remaining_heat_class("32", 30) == "plus2"
    assert remaining_heat_class("33+", 30) == "plus3"


def test_market_distribution_aggregates_complete_ladder() -> None:
    outcomes = [
        {
            "condition_id": f"c{value}",
            "bracket": f"{value}{'+' if value == 34 else ''}",
            "bracket_value": value,
            "yes_mid": probability,
        }
        for value, probability in zip(
            range(27, 35), [0.02, 0.03, 0.05, 0.35, 0.30, 0.15, 0.07, 0.03]
        )
    ]
    probability, metadata = aggregate_market_distribution(
        outcomes, routine_rung=30
    )
    assert metadata["status"] == "scorable"
    assert np.allclose(probability, [0.10, 0.35, 0.30, 0.15, 0.10])
    assert np.isclose(probability.sum(), 1.0)


def test_snapshot_distribution_retains_upper_tail_outcome() -> None:
    rows = []
    for value in range(30, 35):
        bracket = f"{value}{'+' if value == 34 else ''}"
        yes_mid = [0.05, 0.15, 0.45, 0.25, 0.10][value - 30]
        rows.extend(
            [
                {
                    "event_date": "2026-08-01",
                    "event_slug": "seoul",
                    "condition_id": f"c{value}",
                    "bracket": bracket,
                    "outcome": "yes",
                    "status": "ok",
                    "fetched_at_utc": "2026-08-01T01:00:00Z",
                    "summary": {
                        "best_bid": yes_mid - 0.01,
                        "best_ask": yes_mid + 0.01,
                    },
                },
                {
                    "event_date": "2026-08-01",
                    "event_slug": "seoul",
                    "condition_id": f"c{value}",
                    "bracket": bracket,
                    "outcome": "no",
                    "status": "ok",
                    "fetched_at_utc": "2026-08-01T01:00:00Z",
                    "summary": {
                        "best_bid": 1.0 - (yes_mid + 0.01),
                        "best_ask": 1.0 - (yes_mid - 0.01),
                    },
                },
            ]
        )
    snapshot = _snapshot_distribution(rows)
    assert snapshot is not None
    assert [row["bracket"] for row in snapshot["outcomes"]][-1] == "34+"
    assert np.isclose(
        sum(row["normalized_yes_p"] for row in snapshot["outcomes"]), 1.0
    )


def test_geometric_market_posterior_has_market_endpoint() -> None:
    market = np.asarray([[0.1, 0.2, 0.3, 0.25, 0.15]])
    weather = np.asarray([[0.2, 0.1, 0.2, 0.2, 0.3]])
    climatology = np.asarray([0.2, 0.2, 0.2, 0.2, 0.2])
    assert np.allclose(
        geometric_market_posterior(market, weather, climatology, 0.0), market
    )
    posterior = geometric_market_posterior(
        market, weather, climatology, 0.5
    )
    assert np.all(posterior > 0)
    assert np.allclose(posterior.sum(axis=1), 1.0)
