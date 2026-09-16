from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import shutil
import sqlite3

import pytest

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION, BlindCandidateProjection, BlindResearchPacket,
    BlindResearchQuestion, BlindRuleView, BookCaptureDemand,
    BookCapturePurpose, BookCaptureReceipt, BookCaptureStatus, CaptureScope,
    ClaimEvidence, EstimateStage, EvidenceOrigin, EvidenceSupport, HashScope,
    MarketChangeEvent, MarketChangeType, MarketIdentity, MarketSnapshot,
    MarketStatus, PacketStage, ProbabilityEstimate, Replayability,
    ResearchImportReason, ResearchImportReceipt, ResearchImportStatus,
    ResearchResultEnvelope, SourceArtifact, SourceTier, canonical_json,
    stable_record_id,
)
from src.polymarket_alpha.storage import AlphaRepository, ContractConflictError, migrate


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 4, 5, 6, tzinfo=UTC)
SHA_A, SHA_B, SHA_C, SHA_D, SHA_E, SHA_F = (
    "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64,
)


def _env(record_id: str, *, run_id: str = "run-1", source: str = "fixture") -> dict[str, object]:
    return dict(schema_version=ALPHA_CONTRACT_VERSION, record_id=record_id, run_id=run_id,
                created_at=NOW, source=source, source_version="v1", provenance=(), extensions={})


def _identity() -> MarketIdentity:
    return MarketIdentity(event_id="event-1", market_id="market-1", condition_id="condition-1",
                          yes_token_id="yes-token", no_token_id="no-token")


def _snapshot(record_id: str, *, rule: str = "The official source decides.") -> MarketSnapshot:
    return MarketSnapshot(**_env(record_id, source="catalog"), identity=_identity(), title="Fixture",
                          question="Does it happen?", status=MarketStatus.ACTIVE, end_at=NOW + timedelta(days=1),
                          rules_raw=rule, volume=Decimal("0"), liquidity=Decimal("0"),
                          source_observed_at=NOW, ingested_at=NOW)


def _artifact(record_id: str = f"source_artifact:{SHA_A}", *, reference_only: bool = False) -> SourceArtifact:
    values: dict[str, object] = dict(**_env(record_id, run_id="research-run", source="capture"), artifact_id=record_id,
        source_name="Official", source_url_or_source_id="https://example.org/source", media_type="text/plain",
        captured_at=NOW, effective_as_of=NOW - timedelta(hours=1), capture_scope=CaptureScope.EXCERPT_ONLY,
        hash_scope=HashScope.CLAIM_EXCERPT, content_sha256=SHA_A, content_length_bytes=4,
        artifact_locator="artifact://a", replayability=Replayability.EXCERPT)
    if reference_only:
        values.update(capture_scope=CaptureScope.REFERENCE_ONLY, hash_scope=None, content_sha256=None,
                      content_length_bytes=None, artifact_locator=None, replayability=Replayability.REFERENCE_ONLY)
    return SourceArtifact(**values)


def _packet() -> BlindResearchPacket:
    run_id = f"blind_run:{SHA_D}"
    question = BlindResearchQuestion(
        question_id=f"blind_question:{SHA_B}",
        template_id="RULE_TRIGGER_EVIDENCE",
        generated_from_rule_contract_hash=SHA_A,
        generated_from_evidence_ids=(),
        text="What primary evidence confirms the defined rule trigger?",
    )
    projection = BlindCandidateProjection(
        **_env(
            f"blind_candidate:{SHA_C}",
            run_id=run_id,
            source="blind_projection_builder",
        ),
        blind_candidate_id=f"blind_candidate:{SHA_C}",
        rule_contract_hash=SHA_A,
        neutral_proposition="The official agency satisfies the defined rule condition.",
        subject_entity="Official Agency",
        deadline=NOW + timedelta(days=1),
        research_questions=(question,),
        evidence=(),
    )
    rule = BlindRuleView(
        rule_hash=SHA_A,
        subject_entity="Official Agency",
        entity_match_rule="The agency explicitly named in the official bulletin",
        yes_trigger="A final official bulletin confirms the defined event",
        deadline=NOW + timedelta(days=1),
        timezone="UTC",
        resolution_sources=("Official Agency bulletin",),
        source_precedence=("final bulletin",),
        initial_or_final="FINAL",
        clarity_score=Decimal("0.9"),
        parser_version="fixture-v1",
    )
    return BlindResearchPacket(
        **_env(f"blind_packet:{SHA_A}", run_id=run_id, source="blind_packet_builder"),
        packet_stage=PacketStage.BLIND,
        projection=projection,
        blind_rule=rule,
    )


def _result(
    packet: BlindResearchPacket,
    artifact: SourceArtifact,
    *,
    result_id: str = f"research_result:{SHA_D}",
) -> ResearchResultEnvelope:
    evidence = ClaimEvidence(
        **_env(f"evidence:{SHA_B}", run_id="research-run", source="claim_importer"),
        evidence_id=f"evidence:{SHA_B}",
        claim="The official agency issued a final bulletin.",
        supports_yes_or_no=EvidenceSupport.YES,
        source_tier=SourceTier.T0,
        source_name=artifact.source_name,
        source_url_or_source_id=artifact.source_url_or_source_id,
        accessed_at=NOW,
        effective_as_of=artifact.effective_as_of,
        primary_or_secondary="PRIMARY",
        quotation_or_paraphrase_location="paragraph 1",
        confidence=Decimal("0.9"),
        origin=EvidenceOrigin.PRIMARY_SOURCE,
        source_artifact_id=artifact.artifact_id,
        capture_scope=artifact.capture_scope,
        hash_scope=artifact.hash_scope,
        content_sha256=artifact.content_sha256,
        excerpt_context=(None if artifact.capture_scope == CaptureScope.REFERENCE_ONLY else "Final bulletin excerpt."),
        replayability=artifact.replayability,
    )
    estimate = ProbabilityEstimate(
        **_env(f"probability_estimate:{SHA_C}", run_id="research-run", source="manual_research"),
        blind_candidate_id=packet.projection.blind_candidate_id,
        estimate_stage=EstimateStage.BLIND,
        model_type="manual structured research",
        p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"),
        p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("source timing",),
        assumptions=("official source is authoritative",),
        model_version="fixture-v1",
    )
    return ResearchResultEnvelope(
        **_env(result_id, run_id="research-run", source="research_result_importer"),
        result_id=result_id,
        packet_stage=PacketStage.BLIND,
        packet_id=packet.record_id,
        packet_sha256=packet.canonical_sha256,
        probability_estimate=estimate,
        evidence=(evidence,),
        source_artifacts=(artifact,),
        completed_at=NOW,
        producer="fixture",
        producer_version="v1",
    )


def test_p0_01r2_change_demand_and_receipt_are_append_only(tmp_path):
    repo = AlphaRepository(tmp_path / "alpha.db")
    previous, current = _snapshot(f"market_snapshot:{SHA_A}"), _snapshot(f"market_snapshot:{SHA_B}", rule="Changed official source decides.")
    repo.save_contract(previous); repo.save_contract(current)
    change_id = stable_record_id("market_change", "market-1", previous.record_id, current.record_id)
    change = MarketChangeEvent(**_env(change_id), change_event_id=change_id, market_id="market-1",
        previous_snapshot_id=previous.record_id, current_snapshot_id=current.record_id,
        previous_snapshot_sha256=previous.canonical_sha256, current_snapshot_sha256=current.canonical_sha256,
        previous_status=MarketStatus.ACTIVE, current_status=MarketStatus.ACTIVE,
        previous_rule_hash=previous.rule_hash, current_rule_hash=current.rule_hash,
        change_types=(MarketChangeType.RULE_CHANGED,), changed_fields=("rule_hash",), effective_at=NOW, detected_at=NOW)
    repo.save_contract(change)
    demand_id = stable_record_id("book_demand", "market-1", change.record_id)
    demand = BookCaptureDemand(**_env(demand_id), demand_id=demand_id, identity=_identity(),
        purpose=BookCapturePurpose.SENSING, trigger_artifact_id=change.record_id,
        trigger_artifact_sha256=change.canonical_sha256, requested_at=NOW,
        valid_until=NOW + timedelta(minutes=1), max_staleness_seconds=30, target_sizes=(Decimal("10"),))
    repo.save_contract(demand)
    receipt_id = stable_record_id("book_receipt", demand_id, "skipped")
    receipt = BookCaptureReceipt(**_env(receipt_id), receipt_id=receipt_id, demand_id=demand_id,
        demand_sha256=demand.canonical_sha256, market_id="market-1", purpose=BookCapturePurpose.SENSING,
        status=BookCaptureStatus.SKIPPED, capture_owner="fixture-owner", received_at=NOW, error_code="BUDGET")
    assert repo.save_contract(receipt) == receipt.canonical_sha256
    assert repo.save_contract(receipt) == receipt.canonical_sha256
    conn = sqlite3.connect(tmp_path / "alpha.db")
    assert conn.execute("SELECT count(*) FROM alpha_market_change_event_v2").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM alpha_book_capture_receipt_v2").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_result_children_are_atomic_and_import_status_is_not_forged(tmp_path):
    db = tmp_path / "alpha.db"; repo = AlphaRepository(db)
    packet, artifact = _packet(), _artifact(); repo.save_contract(packet)
    result = _result(packet, artifact)
    assert repo.save_research_result(result) == result.canonical_sha256
    accepted_id = stable_record_id("research_import_receipt", "accepted")
    accepted = ResearchImportReceipt(**_env(accepted_id, run_id="research-run"), import_receipt_id=accepted_id,
        packet_stage=PacketStage.BLIND, packet_id=packet.record_id, packet_sha256=packet.canonical_sha256,
        submitted_artifact_id=artifact.artifact_id, submitted_result_sha256=result.canonical_sha256,
        status=ResearchImportStatus.ACCEPTED, reasons=(ResearchImportReason.ACCEPTED,), imported_at=NOW,
        importer_version="v1", accepted_result_id=result.result_id, accepted_result_sha256=result.canonical_sha256)
    repo.save_contract(accepted)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_research_result_envelope_v2").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM alpha_research_result_artifact_v2").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM alpha_research_result_evidence_v2").fetchone()[0] == 1
    assert conn.execute("SELECT accepted_result_id FROM alpha_research_import_receipt_v2").fetchone()[0] == result.result_id
    conn.close()
    bad = result.model_copy(update={"packet_id": "blind_packet:" + "e" * 64})
    with pytest.raises(ContractConflictError):
        repo.save_research_result(bad)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_contract_record WHERE record_id=?", (bad.record_id,)).fetchone()[0] == 1


def test_import_receipt_cannot_accept_a_result_from_another_packet(tmp_path):
    db = tmp_path / "alpha.db"; repo = AlphaRepository(db)
    first_packet, artifact = _packet(), _artifact(); repo.save_contract(first_packet)
    first_result = _result(first_packet, artifact); repo.save_research_result(first_result)
    second_packet = BlindResearchPacket.model_validate(
        {**first_packet.model_dump(), "record_id": f"blind_packet:{SHA_E}"}
    )
    repo.save_contract(second_packet)
    second_result = _result(
        second_packet, artifact, result_id=f"research_result:{SHA_E}"
    )
    repo.save_research_result(second_result)

    receipt_id = stable_record_id("research_import_receipt", "cross-packet")
    cross_packet = ResearchImportReceipt(
        **_env(receipt_id, run_id="research-run"),
        import_receipt_id=receipt_id,
        packet_stage=PacketStage.BLIND,
        packet_id=first_packet.record_id,
        packet_sha256=first_packet.canonical_sha256,
        submitted_artifact_id=artifact.artifact_id,
        submitted_result_sha256=second_result.canonical_sha256,
        status=ResearchImportStatus.ACCEPTED,
        reasons=(ResearchImportReason.ACCEPTED,),
        imported_at=NOW,
        importer_version="v1",
        accepted_result_id=second_result.result_id,
        accepted_result_sha256=second_result.canonical_sha256,
    )
    with pytest.raises(ContractConflictError, match="another packet"):
        repo.save_contract(cross_packet)

    missing_submission = ResearchImportReceipt.model_validate(
        {
            **cross_packet.model_dump(),
            "record_id": f"research_import_receipt:{SHA_F}",
            "import_receipt_id": f"research_import_receipt:{SHA_F}",
            "submitted_artifact_id": f"source_artifact:{SHA_F}",
            "status": ResearchImportStatus.REJECTED,
            "reasons": (ResearchImportReason.VALIDATION_FAILED,),
            "accepted_result_id": None,
            "accepted_result_sha256": None,
        }
    )
    with pytest.raises(ContractConflictError, match="submitted research artifact"):
        repo.save_contract(missing_submission)


def test_result_transaction_rolls_back_children_when_packet_is_missing(tmp_path):
    db = tmp_path / "rollback.db"; repo = AlphaRepository(db)
    packet, artifact = _packet(), _artifact()
    with pytest.raises(ContractConflictError, match="packet"):
        repo.save_research_result(_result(packet, artifact))
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM alpha_contract_record").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM alpha_source_artifact_v2").fetchone()[0] == 0


def test_reference_only_replay_repair_and_copy_preservation(tmp_path):
    source = tmp_path / "source.db"; repo = AlphaRepository(source)
    artifact = _artifact(reference_only=True)
    repo.save_contract(artifact)
    clone = tmp_path / "clone.db"; shutil.copy2(source, clone)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    conn = sqlite3.connect(clone)
    conn.execute("DELETE FROM alpha_source_artifact_v2 WHERE artifact_id=?", (artifact.artifact_id,)); conn.commit(); conn.close()
    AlphaRepository(clone).save_contract(artifact)
    conn = sqlite3.connect(clone)
    assert conn.execute("SELECT capture_scope, content_sha256 FROM alpha_source_artifact_v2").fetchone() == ("REFERENCE_ONLY", None)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_migration_is_repeatable_and_wrong_record_conflicts(tmp_path):
    db = tmp_path / "alpha.db"; first = migrate(db); assert migrate(db) == first
    repo = AlphaRepository(db); artifact = _artifact(); repo.save_contract(artifact)
    with pytest.raises(ContractConflictError):
        repo.save_contract(artifact.model_copy(update={"source_name": "Changed"}))
