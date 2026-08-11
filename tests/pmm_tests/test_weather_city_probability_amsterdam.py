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


def test_amsterdam_v9_maps_fixed_lead_forecast_to_wcir_expression(
    tmp_path, monkeypatch
):
    source = tmp_path / "knmi.jsonl"
    observations = tmp_path / "observations"
    ladders = tmp_path / "ladders"
    forecasts = tmp_path / "forecasts"
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
                        "yes_token_id": "yes-token-20",
                        "yes_best_bid": 0.58,
                        "yes_best_ask": 0.60,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        forecasts / "2026-08-03" / "forecast_hourly_curves_test.jsonl",
        [
            {
                "city": "Amsterdam",
                "target_date": "2026-08-03",
                "forecast_source": "open_meteo_previous_runs_ecmwf_day1",
                "forecast_values_hash": "forecast-hash",
                "available_at_utc": "2026-08-03T09:00:00+00:00",
                "hourly_curve": [
                    {
                        "time_local": f"2026-08-03T{hour:02d}:00",
                        "temperature_f": 60.0 + hour,
                    }
                    for hour in range(24)
                ],
            }
        ],
    )
    artifact = {
        "schema_version": "amsterdam_knmi_remaining_heat_model_v9",
        "model_id": "amsterdam_knmi_remaining_heat_v9_ecmwf_day1",
        "features": ["ta_c", "tx_c", "forecast_now_c"],
        "estimator": _BinaryModel(0.70),
        "calibrator": None,
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
        "forecast_previous_day1_dir": str(forecasts),
        "max_source_age_seconds": 900,
        "expression_sides": ["YES", "NO"],
        "artifacts": {
            "weather": {"path": str(tmp_path / "model.pkl"), "sha256": "sha"}
        },
    }

    scores = AmsterdamKnmiRemainingHeatV7Adapter().score(
        profile, datetime(2026, 8, 3, 10, 6, tzinfo=timezone.utc)
    )
    by_side = {score.market_side: score for score in scores}
    score = by_side["NO"]

    assert set(by_side) == {"YES", "NO"}
    assert by_side["YES"].market["token_id"] == "yes-token-20"
    assert by_side["YES"].model_probability == 1.0 - score.model_probability
    assert score.market_side == "NO"
    assert score.market["condition_id"] == "condition-20"
    assert score.market["token_id"] == "no-token-20"
    assert score.market["outcome"] == "NO"
    assert score.market["book_snapshot_id"] == "book-1"
    assert score.model_probability == 0.70
    assert score.feature_coverage == 1.0
    assert score.missing_features == []
    assert score.lineage["forecast_input_ref"]["forecast_values_hash"] == "forecast-hash"

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
