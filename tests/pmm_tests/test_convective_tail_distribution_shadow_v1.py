from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from scripts.ops.convective_tail_distribution_shadow_v1 import build_outputs
from src.strategies.weather_edge_v1.tools.convective_tail_distribution import (
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
