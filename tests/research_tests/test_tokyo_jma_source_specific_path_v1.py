from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = load(
    "tokyo_jma_feature_timestamp_audit_v1",
    "scripts/analysis/market_structure_edge/"
    "audit_tokyo_jma_feature_timestamps_v1.py",
)
MODEL = load(
    "tokyo_jma_source_specific_path_v1",
    "scripts/analysis/market_structure_edge/"
    "research_tokyo_jma_source_specific_path_v1.py",
)


def test_timestamp_contract_rejects_future_information() -> None:
    observation = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
    decision = observation + timedelta(minutes=7)
    available = decision + timedelta(milliseconds=1)

    assert (
        AUDIT.timestamp_contract_violation(observation, decision, available)
        == ""
    )
    assert (
        AUDIT.timestamp_contract_violation(
            observation, observation - timedelta(seconds=1), available
        )
        == "decision_before_observation"
    )
    assert (
        AUDIT.timestamp_contract_violation(
            observation, decision, decision - timedelta(seconds=1)
        )
        == "available_before_decision"
    )


def test_exact_enrichment_requires_hash_verification_and_same_decision_clock() -> None:
    decision = "2026-07-30T01:07:00+00:00"
    states = [
        {
            "source_observation_ts_utc": "2026-07-30T01:00:00+00:00",
            "decision_clock_ts_utc": decision,
        }
    ]
    enriched = [
        {
            "observation_time_utc": "2026-07-30T01:00:00+00:00",
            "decision_ts_utc": decision,
            "hash_verified_against_jma_point_archive": "1",
            "wind_speed_kt": "7.8",
            "wind_dir_deg": "270",
            "wind_gust_kt": "12.6",
            "precipitation_10m_mm": "0",
            "first_seen_age_min": "7",
        }
    ]

    merged, audit = MODEL.merge_exact_enrichment(states, enriched)

    assert audit == {"hash_verified_joined": 1}
    assert merged[0]["source_specific_exact_available"] == 1
    assert abs(merged[0]["source_wind_dir_cos"]) < 1e-12
    assert merged[0]["source_wind_dir_sin"] == -1.0

    enriched[0]["decision_ts_utc"] = "2026-07-30T01:08:00+00:00"
    rejected, audit = MODEL.merge_exact_enrichment(states, enriched)
    assert audit == {"missing_or_ambiguous": 1}
    assert rejected[0]["source_specific_exact_available"] == 0


def test_wind_change_uses_only_prior_rows() -> None:
    base = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
    rows = [
        {
            "target_date": "2026-07-30",
            "decision_clock_ts_utc": (base + timedelta(minutes=offset)).isoformat(),
            "source_wind_speed_kt": speed,
            "source_wind_gust_kt": speed + 3,
            "source_wind_dir_deg": direction,
            "source_precipitation_10m_mm": 0,
        }
        for offset, speed, direction in (
            (0, 4.0, 180.0),
            (60, 7.0, 225.0),
            (120, 20.0, 10.0),
        )
    ]

    MODEL.add_past_only_dynamics(rows[:2])

    assert rows[0]["source_wind_speed_change_60m_kt"] is None
    assert rows[1]["source_wind_speed_change_60m_kt"] == 3.0
    assert rows[1]["source_wind_dir_change_60m_deg"] == 45.0
