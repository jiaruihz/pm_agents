from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    CalibrationDimension,
    CaptureScope,
    HashScope,
    MarketResolution,
    OrderbookSnapshot,
    PredictionRecord,
    ProbabilityEstimate,
    Replayability,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    ReviewDecision,
    RuleContract,
    SourceArtifact,
    bytes_sha256,
    p1_contract_schema_fingerprint,
    stable_record_id,
)
from src.polymarket_alpha.learning import (
    CalibrationPolicy,
    ScoringPolicy,
    build_calibration_report,
    build_market_resolution,
    build_prediction_resolution_link,
    score_prediction,
)
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.pilot.offline import PILOT_NOW
from src.polymarket_alpha.storage import (
    P1_RESOLUTION_LEARNING_MIGRATION_ID,
    AlphaRepository,
    ContractConflictError,
    migrate,
    p1_resolution_learning_manifest,
)


def _load_one(db, contract_type: str, model, *, predicate=lambda _: True):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT canonical_json FROM alpha_contract_record WHERE contract_type=? ORDER BY record_id",
        (contract_type,),
    ).fetchall()
    conn.close()
    values = tuple(model.model_validate(json.loads(row[0])) for row in rows)
    matches = tuple(item for item in values if predicate(item))
    assert len(matches) == 1
    return matches[0]


def _learning_facts(tmp_path):
    db = tmp_path / "alpha-learning.db"
    repo = AlphaRepository(db)
    pilot = run_offline_fixture_pilot(repo)
    prediction = PredictionRecord.model_validate(repo.get_contract(pilot.ranked.prediction.record_id))
    decision = ReviewDecision.model_validate(repo.get_contract(pilot.ranked.decision.record_id))
    estimate = _load_one(
        db,
        "ProbabilityEstimate",
        ProbabilityEstimate,
        predicate=lambda item: item.estimate_stage.value == "FINAL",
    )
    contract = _load_one(db, "RuleContract", RuleContract)
    book = _load_one(db, "OrderbookSnapshot", OrderbookSnapshot)
    resolved_at = PILOT_NOW + timedelta(days=5)
    observed_at = resolved_at + timedelta(minutes=2)
    source_bytes = b"Example Agency final bulletin: condition satisfied."
    artifact_id = stable_record_id("source_artifact", "p1-resolution", bytes_sha256(source_bytes))
    artifact = SourceArtifact(
        record_id=artifact_id,
        artifact_id=artifact_id,
        run_id="p1-resolution",
        created_at=observed_at,
        source="p1-resolution-fixture",
        source_version="v1",
        provenance=(),
        extensions={},
        source_name="Example Agency",
        source_url_or_source_id="https://agency.example/resolution",
        media_type="text/plain",
        captured_at=observed_at,
        effective_as_of=resolved_at,
        capture_scope=CaptureScope.FULL_DOCUMENT,
        hash_scope=HashScope.RAW_BYTES,
        content_sha256=bytes_sha256(source_bytes),
        content_length_bytes=len(source_bytes),
        artifact_locator="artifact://p1-resolution/final",
        replayability=Replayability.FULL,
    )
    resolution = build_market_resolution(
        source_artifact=artifact,
        rule_contract=contract,
        market_id=prediction.market_id,
        condition_id=pilot.snapshot.identity.condition_id,
        outcome=ResolutionOutcome.YES,
        adjudication_status=ResolutionAdjudicationStatus.FINAL,
        resolved_at=resolved_at,
        source_observed_at=observed_at,
        created_at=observed_at + timedelta(seconds=1),
        run_id="p1-resolution",
        parser_version="fixture-v1",
    )
    link = build_prediction_resolution_link(
        prediction=prediction,
        decision=decision,
        probability_estimate=estimate,
        rule_contract=contract,
        resolution=resolution,
        orderbook=book,
        market_type="binary-event",
        linked_at=observed_at + timedelta(seconds=2),
        run_id="p1-link",
        fee_amount=Decimal("0.02"),
        fee_model_version="explicit-fixture-fee-v1",
    )
    score = score_prediction(
        link=link,
        resolution=resolution,
        policy=ScoringPolicy(version="score-v1"),
        scored_at=observed_at + timedelta(seconds=3),
        run_id="p1-score",
    )
    report = build_calibration_report(
        scores=(score,),
        dimension=CalibrationDimension.MARKET_TYPE,
        policy=CalibrationPolicy(version="calibration-v1"),
        reported_at=observed_at + timedelta(seconds=4),
        run_id="p1-calibration",
    )
    return db, repo, artifact, resolution, link, score, report


def test_p1_learning_projection_is_append_only_atomic_and_repairable(tmp_path) -> None:
    db, repo, artifact, resolution, link, score, report = _learning_facts(tmp_path)
    repo.save_contract(artifact)
    expected = repo.save_contracts_atomic((resolution, link, score, report))
    assert expected == tuple(item.canonical_sha256 for item in (resolution, link, score, report))
    assert repo.save_contracts_atomic((resolution, link, score, report)) == expected

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_market_resolution_v1").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM alpha_prediction_resolution_link_v1").fetchone()[0] == 1
    assert conn.execute("SELECT simulated_pnl FROM alpha_prediction_score_v1").fetchone()[0] == "4.98"
    assert conn.execute("SELECT count(*) FROM alpha_calibration_slice_v1").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    conn.execute("DELETE FROM alpha_calibration_slice_v1")
    conn.execute("DELETE FROM alpha_calibration_report_score_v1")
    conn.execute("DELETE FROM alpha_calibration_report_v1")
    conn.execute("DELETE FROM alpha_prediction_score_v1")
    conn.execute("DELETE FROM alpha_prediction_resolution_link_v1")
    conn.execute("DELETE FROM alpha_market_resolution_v1")
    conn.commit()
    conn.close()
    assert repo.save_contracts_atomic((resolution, link, score, report)) == expected
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_calibration_slice_v1").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_p1_bad_link_rolls_back_new_resolution_projection_and_contract(tmp_path) -> None:
    db, repo, artifact, resolution, link, _score, _report = _learning_facts(tmp_path)
    repo.save_contract(artifact)
    bad_id = stable_record_id("prediction_resolution_link", "bad-parent-hash")
    bad_link = link.model_copy(
        update={"record_id": bad_id, "link_id": bad_id, "prediction_sha256": "f" * 64}
    )
    with pytest.raises(ContractConflictError, match="hash-mismatched"):
        repo.save_contracts_atomic((resolution, bad_link))
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT count(*) FROM alpha_contract_record WHERE record_id=?", (resolution.record_id,)
    ).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM alpha_market_resolution_v1").fetchone()[0] == 0
    conn.close()


def test_p1_migration_manifest_is_repeatable_and_tamper_fails_closed(tmp_path) -> None:
    db = tmp_path / "migration.db"
    assert migrate(db) == migrate(db)
    manifest = p1_resolution_learning_manifest()
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT sql_sha256 FROM alpha_schema_migrations WHERE migration_id=?",
        (P1_RESOLUTION_LEARNING_MIGRATION_ID,),
    ).fetchone()[0] == manifest["sql_sha256"]
    conn.execute(
        "UPDATE alpha_schema_migrations SET sql_sha256=? WHERE migration_id=?",
        ("f" * 64, P1_RESOLUTION_LEARNING_MIGRATION_ID),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="P1 resolution/learning"):
        migrate(db)


def test_p1_contract_and_migration_goldens_are_sealed() -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "p1_learning_contract_golden.json"
        ).read_text(encoding="utf-8")
    )
    migration = p1_resolution_learning_manifest()
    assert fixture == {
        "contract_version": ALPHA_CONTRACT_VERSION,
        "p1_contract_schema_sha256": p1_contract_schema_fingerprint(),
        "migration_id": migration["migration_id"],
        "migration_sql_sha256": migration["sql_sha256"],
    }


def test_prediction_record_remains_unmodified_after_p1_closure(tmp_path) -> None:
    db, repo, artifact, resolution, link, score, report = _learning_facts(tmp_path)
    before = repo.get_contract(link.prediction_id)
    repo.save_contract(artifact)
    repo.save_contracts_atomic((resolution, link, score, report))
    assert repo.get_contract(link.prediction_id) == before
    assert before["final_resolution"] is None
    assert before["resolved_at"] is None
    assert before["simulated_pnl"] is None


def test_calibration_projection_recomputes_policy_slices_and_exclusions(tmp_path) -> None:
    _db, repo, artifact, resolution, link, score, report = _learning_facts(tmp_path)
    repo.save_contract(artifact)
    repo.save_contracts_atomic((resolution, link, score))
    changed_slice = report.slices[0].model_copy(
        update={"mean_brier_score": Decimal("0.999")}
    )
    cases = (
        {"slices": (changed_slice,)},
        {"slices": (report.slices[0].model_copy(update={"count": 2}),)},
        {"calibration_policy_sha256": "f" * 64},
        {"excluded_score_ids": (score.record_id,)},
    )
    for index, update in enumerate(cases):
        bad_id = stable_record_id("calibration_report", "tamper", index)
        bad = report.model_copy(
            update={"record_id": bad_id, "report_id": bad_id, **update}
        )
        with pytest.raises(
            ContractConflictError, match="deterministic score replay"
        ):
            repo.save_contract(bad)
        assert repo.get_contract(bad_id) is None


def test_score_projection_rejects_forged_derived_metric(tmp_path) -> None:
    _db, repo, artifact, resolution, link, score, _report = _learning_facts(tmp_path)
    repo.save_contract(artifact)
    repo.save_contracts_atomic((resolution, link))
    bad_id = stable_record_id("prediction_score", "forged-brier")
    forged = score.model_copy(
        update={"record_id": bad_id, "score_id": bad_id, "brier_score": Decimal("0.999")}
    )
    with pytest.raises(ContractConflictError, match="Brier score"):
        repo.save_contract(forged)
    assert repo.get_contract(bad_id) is None
