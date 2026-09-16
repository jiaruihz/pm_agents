from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.strategies.weather_city_probability_shadow.core import OUTPUT_SCHEMA_VERSION
from weather_city_runtime import TemporaryCanonicalBridge, legacy_bundle_from_evaluation
from weather_dashboard.ingest.settlement_labels import labels_as_of


def _evaluation() -> dict:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "record_kind": "evaluation",
        "evaluation_id": "settlement-label-eval",
        "city": "Helsinki",
        "target_date": "2026-08-01",
        "decision_ts_utc": "2026-08-01T05:42:00Z",
        "source_obs_ts_utc": "2026-08-01T05:40:00Z",
        "current_bracket": 18,
        "market_side": "NO",
        "market_probability": 0.4,
        "market_entry_price": 0.42,
        "model_probability": 0.7,
        "model_id": "helsinki-v1",
        "feature_coverage": 1.0,
        "missing_features": [],
        "features": {"remaining_heat": 0.25},
        "market": {
            "condition_id": "condition-18",
            "market_id": "market-18",
            "token_id": "token-no-18",
            "outcome": "no",
            "book_snapshot_id": "book-18",
        },
        "lineage": {
            "source": "fmi",
            "source_first_seen_at_utc": "2026-08-01T05:41:00Z",
            "source_payload_hash": "source-payload",
            "book_snapshot_id": "book-18",
            "model_artifact_sha256": "artifact-18",
            "profile_id": "helsinki_remaining_heat_v1",
            "source_input_ref": {"physical_path": "source.jsonl", "physical_line": 1},
        },
        "evaluation_status": "scored",
        "not_scorable_reason": None,
        "effective_cost_per_share": 0.43,
        "edge_after_fee": 0.27,
        "edge_threshold": 0.05,
        "would_enter": True,
    }


def _insert_outcome(
    conn: sqlite3.Connection,
    *,
    outcome_id: str,
    final_price: float,
    available: str,
    source_system: str = "pm_history",
) -> None:
    conn.execute(
        """
        INSERT INTO settlement_outcomes (
          settlement_outcome_id, source_system, city, target_date, bracket,
          condition_id, raw_final_price, final_price, settlement_status,
          question, source_payload_hash, first_seen_at_utc, available_at_utc,
          pit_lineage_class, producer_build_id, created_at_utc
        ) VALUES (?, ?, 'Helsinki', '2026-08-01', '18',
                  'condition-18', ?, ?, 'settled', ?, ?, ?, ?,
                  'collector_exact', 'test-build', ?)
        """,
        (
            outcome_id,
            source_system,
            final_price,
            final_price,
            "Will the highest temperature in Helsinki be 18°C?",
            f"hash-{outcome_id}",
            available,
            available,
            available,
        ),
    )


def test_settlement_labels_are_append_only_namespace_aware_and_cutoff_safe(
    tmp_path: Path,
) -> None:
    db = tmp_path / "labels.db"
    bridge = TemporaryCanonicalBridge(db)
    bundle = legacy_bundle_from_evaluation(_evaluation())
    bridge.append([bundle])

    conn = sqlite3.connect(db)
    _insert_outcome(
        conn,
        outcome_id="outcome-first",
        final_price=1.0,
        available="2026-08-02T12:00:00Z",
    )
    conn.commit()
    conn.close()

    first = bridge.append(
        [],
        attach_candidate_settlements=True,
        max_candidate_settlement_updates=10,
    )
    assert first["settlement_label_facts_inserted"] == 1
    conn = sqlite3.connect(db)
    try:
        assert labels_as_of(conn, cutoff_utc="2026-08-02T11:59:59Z") == []
        labels = labels_as_of(conn, cutoff_utc="2026-08-02T12:00:00Z")
        assert len(labels) == 1
        assert labels[0]["measurement_namespace"] == "tmax"
        assert labels[0]["namespace_status"] == "explicit_question"
        assert labels[0]["settlement_yes"] == 1.0
        _insert_outcome(
            conn,
            outcome_id="outcome-correction",
            final_price=0.0,
            available="2026-08-03T12:00:00Z",
            source_system="polymarket_api",
        )
        conn.commit()
    finally:
        conn.close()

    correction = bridge.append(
        [],
        attach_candidate_settlements=True,
        max_candidate_settlement_updates=10,
    )
    assert correction["settlement_label_facts_inserted"] == 1
    conn = sqlite3.connect(db)
    try:
        before = labels_as_of(conn, cutoff_utc="2026-08-03T11:59:59Z")
        after = labels_as_of(conn, cutoff_utc="2026-08-03T12:00:00Z")
        assert before[0]["settlement_source_id"] == "outcome-first"
        assert before[0]["settlement_yes"] == 1.0
        assert after[0]["settlement_source_id"] == "outcome-correction"
        assert after[0]["settlement_yes"] == 0.0
        assert conn.execute(
            "SELECT count(*) FROM weather_candidate_settlement_labels"
        ).fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE weather_candidate_settlement_labels "
                "SET settlement_yes = 1.0"
            )
    finally:
        conn.close()
