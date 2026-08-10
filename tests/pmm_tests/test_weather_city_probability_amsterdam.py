from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np

from src.strategies.weather_city_probability_shadow.amsterdam import (
    AmsterdamKnmiRemainingHeatV7Adapter,
)
from src.strategies.weather_city_probability_shadow.core import (
    AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
)
from weather_city_runtime.legacy_adapters import (
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)


class _V4Model:
    def predict(self, frame, *, features):
        assert list(frame.columns) == list(features)
        return np.asarray([0.30])


class _BinaryModel:
    def __init__(self, probability: float):
        self.probability = probability

    def predict_proba(self, frame):
        return np.asarray([[1.0 - self.probability, self.probability]])


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_amsterdam_v7_maps_knmi_probability_to_wcir_expression(
    tmp_path, monkeypatch
):
    source = tmp_path / "knmi.jsonl"
    observations = tmp_path / "observations"
    ladders = tmp_path / "ladders"
    event_id = "knmi-event-1"
    _write_jsonl(
        source.parent / "2026-08-03" / source.name,
        [
            {
                "city": "Amsterdam",
                "source": "knmi",
                "target_date": "2026-08-03",
                "information_event_status": "material",
                "information_event_id": event_id,
                "observation_time_utc": "2026-08-03T10:00:00+00:00",
                "source_first_seen_at_utc": "2026-08-03T10:05:00+00:00",
                "knmi_station_fields": {
                    "ta": 20.2,
                    "tx": 20.3,
                    "qg": 500.0,
                    "n": 3.0,
                    "rg": 0.0,
                    "rh": 55.0,
                    "td": 11.0,
                    "ff": 4.0,
                    "gff": 6.0,
                    "dd": 230.0,
                    "pp": 1015.0,
                },
            }
        ],
    )
    _write_jsonl(
        observations / "2026-08-03" / "observations.jsonl",
        [
            {
                "city": "Amsterdam",
                "target_date": "2026-08-03",
                "fetched_at_utc": "2026-08-03T10:01:00+00:00",
                "last_obs_utc": "2026-08-03T09:40:00+00:00",
                "running_max_c": 20.4,
                "current_temp_c": 20.0,
                "minutes_since_running_max": 20,
            }
        ],
    )
    snapshot = ladders / "2026-08-03" / "t0.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {
                "source_event_id": event_id,
                "snapshot_id": "book-1",
                "capture_status": "complete",
                "capture_started_at_utc": "2026-08-03T10:05:30+00:00",
                "scheduled_offset_seconds": 0,
                "records": [
                    {
                        "bracket": "20",
                        "condition_id": "condition-20",
                        "market_id": "market-20",
                        "no_token_id": "no-token-20",
                        "no_best_bid": 0.40,
                        "no_best_ask": 0.42,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "model_id": "amsterdam_knmi_remaining_heat_v7",
        "models": {
            "base_features": ["ta_c", "tx_c"],
            "v4_binary_model": _V4Model(),
            "v5_structured_models": {
                "hazards": [_BinaryModel(0.10) for _ in range(4)],
                "next_routine": _BinaryModel(0.20),
            },
            "binary_blend": {"v5_weight": 0.5},
        },
    }
    monkeypatch.setattr(
        "src.strategies.weather_city_probability_shadow.amsterdam.joblib.load",
        lambda path: artifact,
    )
    profile = {
        "profile_id": "amsterdam_knmi_remaining_heat_frozen_v7_clean_forward",
        "forward_start_utc": "2026-08-01T00:00:00+00:00",
        "source_journal": str(source),
        "observation_journal_dir": str(observations),
        "ladder_snapshot_dir": str(ladders),
        "max_source_age_seconds": 900,
        "artifacts": {
            "weather": {"path": str(tmp_path / "model.pkl"), "sha256": "sha"}
        },
    }

    score = AmsterdamKnmiRemainingHeatV7Adapter().score(
        profile, datetime(2026, 8, 3, 10, 6, tzinfo=timezone.utc)
    )[0]

    assert score.market_side == "NO"
    assert score.market["condition_id"] == "condition-20"
    assert score.market["token_id"] == "no-token-20"
    assert score.market["outcome"] == "NO"
    assert score.market["book_snapshot_id"] == "book-1"
    assert 0.0 < score.model_probability < 1.0

    evaluation = {
        **asdict(score),
        "schema_version": AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
        "record_kind": "paper_intent",
        "evaluation_id": "evaluation-1",
        "would_enter": True,
        "effective_cost_per_share": 0.43,
        "edge_after_fee": 0.05,
        "edge_threshold": 0.02,
        "position_key": "Amsterdam|2026-08-03|20|NO|v7",
    }
    bundle = legacy_bundle_from_evaluation(evaluation)
    assert bundle.signal_candidate.candidate_status == "scored"
    assert bundle.signal_candidate.token_id == "no-token-20"
    assert bundle.signal_candidate.selected is True
    intent = legacy_trade_intent_from_paper_intent(evaluation)
    assert intent.mode == "zero_notional"
    assert intent.requested_size == 0.0
