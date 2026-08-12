from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from src.strategies.weather_city_probability_shadow.busan import (
    BusanOnlineMarketPriorAdapter,
)
from weather_model_evaluation.busan_market_prior import (
    CONFIRMATION_FEATURES,
    RUNTIME_ARTIFACT_SCHEMA_VERSION,
    logit_shrunk_probability,
)
from weather_city_runtime.legacy_adapters import (
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


class _FixedProbability:
    def __init__(self, probability: float):
        self.probability = probability

    def predict_proba(self, frame):
        return np.tile(
            np.asarray([[1.0 - self.probability, self.probability]]),
            (len(frame), 1),
        )


def _artifact() -> dict:
    return {
        "schema_version": RUNTIME_ARTIFACT_SCHEMA_VERSION,
        "city": "Busan",
        "model_id": "busan_test",
        "physical_model": _FixedProbability(0.40),
        "physical_features": [
            "local_hour",
            "running_max_market_value",
            "day_of_year_sin",
            "day_of_year_cos",
        ],
        "confirmation_model": _FixedProbability(0.50),
        "confirmation_features": list(CONFIRMATION_FEATURES),
        "confirmation_train_end": "2026-08-03",
        "physical_train_end": "2026-07-21",
        "expression_weight": 0.25,
        "expression_weight_trained_through": "2026-08-11",
        "candidate_grain_version": "busan_pending_confirmation_state_v1",
        "parity": {"pass": True},
    }


def _checkpoint(*, book_ts: str = "2026-08-11T02:16:15+00:00") -> dict:
    return {
        "schema_version": "korea_amos_first_seen_state_v1",
        "city": "Busan",
        "target_date": "2026-08-11",
        "source_event_key": "busan-event-1",
        "source_available_at_utc": "2026-08-11T02:16:12+00:00",
        "source_first_seen_ts_utc": "2026-08-11T02:16:10+00:00",
        "source_observation_ts_utc": "2026-08-11T02:15:00+00:00",
        "collector_emitted_at_utc": "2026-08-11T02:16:18+00:00",
        "source_temp_c": 30.1,
        "source_running_max_c": 30.4,
        "routine_running_max_market_value": 29,
        "minutes_since_source_running_max": 2.0,
        "distance_below_source_running_max_c": 0.3,
        "raw_metar": "METAR RKPK 110200Z 07012KT 9999 SCT040 29/17 Q1010=",
        "observation_history_count": 207,
        "relative_humidity_pct": 45.7,
        "dewpoint_depression_c": 13.0,
        "path_windows": {
            "15m": {"temp_slope_c_per_hour": 3.6},
            "60m": {"temp_slope_c_per_hour": 2.3},
        },
        "forecast_context": {
            "forecast_max_f": 84.7,
            "forecast_peak_hour_local": 14,
            "forecast_cloud_cover_remaining_3h_mean_pct": 89.0,
            "forecast_precip_probability_remaining_3h_max_pct": 0.0,
            "forecast_wind_speed_remaining_3h_max_kt": 10.5,
            "forecast_first_seen_utc": "2026-08-10T20:38:14Z",
            "forecast_available_at_utc": "2026-08-10T20:40:24Z",
        },
        "market_capture": {
            "books": [
                {
                    "bracket": "29",
                    "outcome": "no",
                    "token_id": "no-token-29",
                    "condition_id": "condition-29",
                    "question": "Will the highest temperature be 29 C?",
                    "fetched_at_utc": book_ts,
                    "summary": {"best_bid": 0.55, "best_ask": 0.57},
                    "raw": {
                        "bids": [{"price": 0.55, "size": 10.0}],
                        "asks": [{"price": 0.57, "size": 10.0}],
                    },
                }
            ]
        },
    }


def _score(tmp_path, monkeypatch, row):
    source = tmp_path / "checkpoint.jsonl"
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.busan.joblib.load",
        lambda path: _artifact(),
    )
    return BusanOnlineMarketPriorAdapter().score(
        {
            "source_journal": str(source),
            "artifacts": {"runtime": {"path": str(tmp_path / "model.joblib")}},
            "max_source_age_seconds": 180,
            "max_feature_book_response_seconds": 10,
            "max_execution_book_age_seconds": 90,
        },
        datetime(2026, 8, 11, 2, 16, 16, tzinfo=UTC),
    )[0]


def test_busan_adapter_scores_pending_state_without_settlement_label(
    tmp_path, monkeypatch
):
    score = _score(tmp_path, monkeypatch, _checkpoint())

    expected_weather = 0.50 + (1.0 - 0.50) * 0.40
    expected_posterior = float(
        logit_shrunk_probability(0.56, expected_weather, weather_weight=0.25)
    )
    assert score.evaluation_status == "scored"
    assert score.model_probability == pytest.approx(expected_posterior)
    assert score.market_entry_price == 0.57
    assert score.market["outcome"] == "NO"
    assert score.market["book_snapshot_id"]
    assert score.feature_coverage == 1.0
    assert score.lineage["label_fields_consumed"] == []
    assert score.market["feature_book_snapshot_id"]
    assert score.market["execution_book_snapshot_id"]
    evaluation = {
        **asdict(score),
        "schema_version": "weather_city_probability_runtime_v3",
        "record_kind": "paper_intent",
        "evaluation_id": "busan-evaluation-1",
        "would_enter": True,
        "effective_cost_per_share": 0.58,
        "edge_after_fee": 0.01,
        "edge_threshold": 0.0,
        "position_key": "Busan|2026-08-11|29|NO|busan_test",
    }
    bundle = legacy_bundle_from_evaluation(evaluation)
    assert bundle.signal_candidate.candidate_status == "scored"
    assert bundle.signal_candidate.token_id == "no-token-29"
    intent = legacy_trade_intent_from_paper_intent(evaluation)
    assert intent.mode == "zero_notional"
    assert intent.requested_size == 0.0


def test_busan_adapter_is_label_independent_and_blocks_stale_feature_book(
    tmp_path, monkeypatch
):
    baseline = _checkpoint()
    baseline_score = _score(tmp_path, monkeypatch, baseline)
    baseline["label_no"] = 1
    labeled_score = _score(tmp_path, monkeypatch, baseline)
    assert labeled_score.model_probability == baseline_score.model_probability

    stale = _checkpoint(book_ts="2026-08-11T02:17:00+00:00")
    stale_score = _score(tmp_path, monkeypatch, stale)
    assert stale_score.evaluation_status == "not_scorable"
    assert stale_score.not_scorable_reason == "feature_book_response_stale"
    assert stale_score.model_probability is None


def test_busan_production_profile_is_zero_notional_clean_forward() -> None:
    config = json.loads(
        (ROOT / "configs/weather/city_probability_runtime_v3.json").read_text()
    )
    profile = next(row for row in config["profiles"] if row["city"] == "Busan")

    assert config["execution_mode"] == "zero_notional_shadow"
    assert config["orders_submitted"] == 0
    assert profile["adapter"] == "busan_online_market_prior_v1"
    assert profile["forward_start_utc"] == "2026-08-12T02:00:00Z"
    assert profile["max_feature_book_response_seconds"] == 10
    assert profile["max_execution_book_age_seconds"] == 90
    assert profile["edge_threshold"] == 0.0
    assert profile["selection_shares"] == 5.0
    assert profile["emit_paper_intents"] is True
    contract = next(
        row
        for row in config["producer_contracts"]
        if row["journal_path"].endswith("korea_first_seen_state_v1/checkpoints")
    )
    assert contract["schema_version"] == "korea_first_seen_collector_latest_v2"
