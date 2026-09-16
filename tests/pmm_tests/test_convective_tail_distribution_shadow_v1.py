from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analysis.forecast_quality.research_convective_tail_distribution_v1 import (
    common_entry_expression_tickets,
)
from scripts.ops.convective_tail_distribution_shadow_v1 import build_outputs, candidate_rows
from src.strategies.weather_edge_v1.tools.convective_tail_distribution import (
    C4_CENTER_DIMENSIONS,
    C4_TAIL_DIMENSIONS,
    apply_challenger,
    fit_feature_transform,
    score_from_artifact,
    transform_features,
)


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = ROOT / "src/strategies/weather_edge_v1/config/convective_tail_distribution_v1.json"


def test_missing_feature_transform_is_finite() -> None:
    transform = fit_feature_transform([{"peak_pop_pct": 20.0}, {"peak_pop_pct": 80.0}])
    matrix = transform_features([{"peak_pop_pct": None}], transform)
    assert np.isfinite(matrix).all()
    assert matrix.shape == (1, 24)


def test_frozen_candidate_returns_complete_distribution() -> None:
    artifact = json.loads(ARTIFACT_PATH.read_text())
    feature = {name: 0.0 for name in artifact["feature_transform"]["feature_names"]}
    probability = score_from_artifact(artifact, feature, [0.1, 0.3, 0.4, 0.2], [-1, 0, 1, 2])
    assert len(probability) == 4
    assert probability.sum() == 1.0
    assert (probability > 0).all()


def test_directional_neighbor_transport_moves_mass_with_warming_signal() -> None:
    transform = fit_feature_transform([
        {"warming_innovation_f": -2.0},
        {"warming_innovation_f": 2.0},
    ])
    cold_feature = transform_features([{"warming_innovation_f": -2.0}], transform)[0]
    hot_feature = transform_features([{"warming_innovation_f": 2.0}], transform)[0]
    parameters = np.zeros(C4_CENTER_DIMENSIONS + C4_TAIL_DIMENSIONS)
    parameters[3] = 3.0
    parameters[C4_CENTER_DIMENSIONS] = 4.0
    market = np.array([0.1, 0.4, 0.4, 0.1])
    distances = np.array([-1.5, -0.5, 0.5, 1.5])
    cold = apply_challenger(
        "c5_directional_neighbor_transport", market, cold_feature, parameters, distances
    )
    hot = apply_challenger(
        "c5_directional_neighbor_transport", market, hot_feature, parameters, distances
    )
    assert float(hot @ distances) > float(cold @ distances)
    assert np.isclose(cold.sum(), 1.0)
    assert np.isclose(hot.sum(), 1.0)


def test_common_entry_scores_all_expressions_on_the_same_first_state() -> None:
    rows = []
    for state, decision, edges in (
        ("state-1", "2026-08-09T10:00:00Z", (0.01, -0.02, -0.03)),
        ("state-2", "2026-08-09T12:00:00Z", (0.02, 0.01, 0.01)),
    ):
        for policy, edge in zip(
            ("single_yes", "adjacent_hot_strip", "bounded_hot_tail_basket"),
            edges,
            strict=True,
        ):
            rows.append({
                "tmax_state_id": state,
                "city": "London",
                "target_date": "2026-08-09",
                "decision_ts_utc": decision,
                "policy": policy,
                "predicted_edge": edge,
            })
    tickets = common_entry_expression_tickets(pd.DataFrame(rows))
    assert set(tickets["tmax_state_id"]) == {"state-1"}
    assert set(tickets["policy"]) == {
        "single_yes",
        "adjacent_hot_strip",
        "bounded_hot_tail_basket",
    }
    assert tickets["common_entry_trigger"].all()
    assert tickets["expression_positive_edge"].sum() == 1


def test_shadow_candidates_choose_one_best_row_per_policy_and_share_trigger() -> None:
    rungs = [
        {
            "bracket": str(70 + rank),
            "condition_id": f"condition-{rank}",
            "bracket_center_native": 70.0 + rank,
            "yes_ask": 0.10,
            "yes_ask_size": 20.0,
            "yes_depth_ask_5c": 100.0,
            "model_probability": probability,
        }
        for rank, probability in enumerate((0.30, 0.25, 0.20, 0.10))
    ]
    candidates = candidate_rows(
        "model-output", "London", "2026-08-09", datetime(2026, 8, 9, tzinfo=timezone.utc),
        rungs, 69.0, "test-build",
    )
    assert len(candidates) == 3
    assert {row["expression_policy"] for row in candidates} == {
        "single_yes",
        "adjacent_hot_strip",
        "bounded_hot_tail_basket",
    }
    assert all(row["common_entry_trigger"] for row in candidates)
    assert all(row["selected_for_common_entry_score"] for row in candidates)


def test_shadow_build_emits_model_and_candidates_without_execution_objects() -> None:
    decision = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    batch = "batch-1"
    rungs = []
    books_records = []
    for rank, (bracket, bid, ask) in enumerate(((68, 0.08, 0.10), (69, 0.28, 0.30), (70, 0.38, 0.40), (71, 0.10, 0.12))):
        capture = f"book-{rank}"
        rungs.append({
            "bracket": str(bracket), "condition_id": f"condition-{rank}",
            "market_id": f"market-{rank}", "yes_book_capture_id": capture,
        })
        books_records.append({
            "book_capture_id": capture, "fetched_at_utc": "2026-08-09T11:59:30Z",
            "summary": {"best_bid": bid, "best_ask": ask, "bid_size": 20, "ask_size": 20, "depth_ask_5c": 100},
        })
    books = {"batch_capture_id": batch, "records": books_records}
    ladders = {
        "batch_capture_id": batch, "available_at_utc": "2026-08-09T12:00:00Z",
        "records": [{"city": "London", "target_date": "2026-08-09", "event_id": "event-1", "rungs": rungs}],
    }
    strategy = {"ts_utc": "2026-08-09T11:59:40Z", "records": [{"city": "London", "target_date": "2026-08-09", "unit": "F"}]}
    observations = {"records": [{
        "city": "London", "target_date": "2026-08-09", "status": "ok",
        "tmpf_now": 69.0, "last_obs_utc": "2026-08-09T11:50:00Z",
        "relative_humidity_pct": 70.0, "source": "test_metar",
    }]}
    curve = [{
        "city": "London", "target_date": "2026-08-09", "capture_id": "curve-1",
        "available_at_utc": "2026-08-09T11:30:00Z", "forecast_first_seen_utc": "2026-08-09T11:29:00Z",
        "forecast_values_hash": "hash-1", "forecast_model": "ecmwf", "forecast_source": "test",
        "forecast_timezone": "Europe/London",
        "hourly_curve": [
            {"time_local": f"2026-08-09T{hour:02d}:00", "temperature_f": 58 + min(hour, 12),
             "precipitation_probability_pct": 40, "cloud_cover_pct": 70, "wind_speed_10m_kt": 8}
            for hour in range(24)
        ],
    }]
    first = {"London|2026-08-09": {"obs_ts_utc": "2026-08-09T06:00:00Z", "first_seen_at_utc": "2026-08-09T06:01:00Z", "temp_f": 61.0}}
    artifact = json.loads(ARTIFACT_PATH.read_text())
    outputs, candidates, blockers, counters = build_outputs(
        books, ladders, strategy, observations, {("London", "2026-08-09"): curve},
        first, artifact, set(), decision, "test-build",
    )
    assert counters["events_scored"] == 1
    assert not blockers
    assert outputs and candidates
    assert sum(row["model_probability"] for row in outputs[0]["ladder"]) == 1.0
    assert all(row["notional_usd"] == 0.0 for row in [*outputs, *candidates])
    forbidden = {"trade_intent", "TradeIntent", "plan", "order", "fill"}
    assert not any(forbidden.intersection(row) for row in [*outputs, *candidates])
