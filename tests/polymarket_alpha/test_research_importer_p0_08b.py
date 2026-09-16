from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from src.polymarket_alpha.contracts import (
    BlindCandidateProjection,
    BlindResearchPacket,
    BlindResearchQuestion,
    BlindRuleView,
    CaptureScope,
    ClaimEvidence,
    EstimateStage,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    PacketStage,
    ProbabilityEstimate,
    Replayability,
    ResearchImportReason,
    ResearchImportStatus,
    ResearchResultEnvelope,
    SourceArtifact,
    SourceTier,
    canonical_json,
    stable_record_id,
)
from src.polymarket_alpha.research import import_research_result
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 13, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _env(record_id: str, *, run_id: str, source: str) -> dict[str, object]:
    return {
        "record_id": record_id,
        "run_id": run_id,
        "created_at": NOW,
        "source": source,
        "source_version": "fixture-v1",
        "provenance": (),
        "extensions": {},
    }


def _packet() -> BlindResearchPacket:
    run_id = f"blind_run:{SHA_A}"
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
        **_env(f"blind_packet:{SHA_D}", run_id=run_id, source="blind_packet_builder"),
        packet_stage=PacketStage.BLIND,
        projection=projection,
        blind_rule=rule,
    )


def _result(
    packet: BlindResearchPacket,
    *,
    source_bytes: bytes | None = b"Final official bulletin excerpt.",
    declared_bytes: bytes | None = None,
    capture_scope: CaptureScope = CaptureScope.EXCERPT_ONLY,
    hash_scope: HashScope | None = HashScope.CLAIM_EXCERPT,
) -> tuple[ResearchResultEnvelope, dict[str, bytes]]:
    run_id = f"research_run:{SHA_E}"
    reference_only = capture_scope == CaptureScope.REFERENCE_ONLY
    actual_declared = source_bytes if declared_bytes is None else declared_bytes
    if hash_scope == HashScope.NORMALIZED_TEXT and actual_declared is not None:
        normalized = actual_declared.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    else:
        content_hash = None if actual_declared is None else hashlib.sha256(actual_declared).hexdigest()
    artifact = SourceArtifact(
        **_env(f"source_artifact:{SHA_A}", run_id=run_id, source="source_capture"),
        artifact_id=f"source_artifact:{SHA_A}",
        source_name="Official Agency",
        source_url_or_source_id="https://agency.example/final",
        media_type="text/plain",
        captured_at=NOW,
        effective_as_of=NOW - timedelta(minutes=1),
        capture_scope=capture_scope,
        hash_scope=None if reference_only else hash_scope,
        content_sha256=None if reference_only else content_hash,
        content_length_bytes=None if reference_only else len(actual_declared or b""),
        artifact_locator=None if reference_only else "artifact://official-final",
        replayability=(
            Replayability.REFERENCE_ONLY
            if reference_only
            else Replayability.EXCERPT
        ),
    )
    claim = ClaimEvidence(
        **_env(f"evidence:{SHA_B}", run_id=run_id, source="claim_importer"),
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
        excerpt_context=(
            None if reference_only else (actual_declared or b"").decode("utf-8")
        ),
        replayability=artifact.replayability,
    )
    estimate = ProbabilityEstimate(
        **_env(f"probability_estimate:{SHA_C}", run_id=run_id, source="manual_research"),
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
    result_id = stable_record_id("research_result", packet.record_id, artifact.artifact_id)
    result = ResearchResultEnvelope(
        **_env(result_id, run_id=run_id, source="research_result_importer"),
        result_id=result_id,
        packet_stage=PacketStage.BLIND,
        packet_id=packet.record_id,
        packet_sha256=packet.canonical_sha256,
        probability_estimate=estimate,
        evidence=(claim,),
        source_artifacts=(artifact,),
        completed_at=NOW,
        producer="manual research provider",
        producer_version="fixture-v1",
    )
    contents = {} if source_bytes is None else {artifact.artifact_id: source_bytes}
    return result, contents


def _submitted(result: ResearchResultEnvelope) -> bytes:
    return canonical_json(result).encode("utf-8")


def test_accepts_only_after_recomputing_source_bytes_and_persists_atomically(tmp_path) -> None:
    packet = _packet()
    result, contents = _result(packet)
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://submitted-result",
    )
    assert outcome.result == result
    assert outcome.receipt.status == ResearchImportStatus.ACCEPTED
    assert outcome.receipt.reasons == (ResearchImportReason.ACCEPTED,)
    assert outcome.receipt.accepted_result_sha256 == result.canonical_sha256

    repo = AlphaRepository(tmp_path / "alpha.db")
    repo.save_contract(packet)
    repo.save_contract(outcome.submitted_artifact)
    repo.save_research_result(outcome.result)
    repo.save_contract(outcome.receipt)
    assert repo.get_contract(outcome.receipt.record_id) is not None


def test_wrong_declared_source_hash_is_quarantined_not_trusted() -> None:
    packet = _packet()
    result, contents = _result(packet, declared_bytes=b"different declared bytes")
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://submitted-result",
    )
    assert outcome.result is None
    assert outcome.receipt.status == ResearchImportStatus.QUARANTINED
    assert outcome.receipt.reasons == (
        ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,
    )


def test_missing_extra_and_reference_only_source_content_are_explicit() -> None:
    packet = _packet()
    result, _ = _result(packet)
    missing = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents={},
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://submitted-result",
    )
    assert missing.receipt.reasons == (ResearchImportReason.SOURCE_ARTIFACT_MISSING,)

    _, contents = _result(packet)
    extra = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents={**contents, f"source_artifact:{SHA_D}": b"rogue"},
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://submitted-result",
    )
    assert extra.receipt.reasons == (
        ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,
    )

    reference, no_content = _result(
        packet,
        source_bytes=None,
        declared_bytes=None,
        capture_scope=CaptureScope.REFERENCE_ONLY,
        hash_scope=None,
    )
    accepted_reference = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(reference),
        source_contents=no_content,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://submitted-reference",
    )
    assert accepted_reference.receipt.status == ResearchImportStatus.ACCEPTED
    assert accepted_reference.result is not None
    assert accepted_reference.result.source_artifacts[0].replayability == Replayability.REFERENCE_ONLY


def test_packet_schema_stage_id_and_hash_mismatch_are_quarantined() -> None:
    packet = _packet()
    result, contents = _result(packet)
    base = json.loads(_submitted(result))
    cases = (
        ("schema_version", "alpha_p0_v9.0", ResearchImportReason.SCHEMA_VERSION_MISMATCH),
        ("packet_stage", "MARKET_AWARE", ResearchImportReason.PACKET_STAGE_MISMATCH),
        ("packet_id", f"blind_packet:{SHA_A}", ResearchImportReason.PACKET_ID_MISMATCH),
        ("packet_sha256", SHA_A, ResearchImportReason.PACKET_HASH_MISMATCH),
    )
    for field, value, reason in cases:
        payload = {**base, field: value}
        outcome = import_research_result(
            packet=packet,
            submitted_bytes=json.dumps(payload, sort_keys=True).encode("utf-8"),
            source_contents=contents,
            imported_at=NOW,
            run_id="import-run-1",
            submitted_artifact_locator=f"artifact://mismatch-{field}",
        )
        assert outcome.receipt.status == ResearchImportStatus.QUARANTINED
        assert reason in outcome.receipt.reasons


def test_expected_submission_hash_malformed_json_and_blind_leak_quarantine() -> None:
    packet = _packet()
    result, contents = _result(packet)
    wrong_hash = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://wrong-hash",
        expected_submitted_sha256=SHA_A,
    )
    assert wrong_hash.receipt.reasons == (ResearchImportReason.RESULT_HASH_MISMATCH,)

    malformed = import_research_result(
        packet=packet,
        submitted_bytes=b"{not-json",
        source_contents={},
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://malformed",
    )
    assert malformed.receipt.reasons == (ResearchImportReason.VALIDATION_FAILED,)

    leaked = json.loads(_submitted(result))
    leaked["probability_estimate"]["assumptions"] = ["A wallet bought YES"]
    leak_outcome = import_research_result(
        packet=packet,
        submitted_bytes=json.dumps(leaked, sort_keys=True).encode("utf-8"),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://leaked",
    )
    assert leak_outcome.receipt.reasons == (ResearchImportReason.BLIND_SEMANTIC_LEAK,)


def test_normalized_text_hash_uses_frozen_normalization_semantics() -> None:
    packet = _packet()
    raw = b"Line one\r\nLine two"
    result, contents = _result(
        packet,
        source_bytes=raw,
        declared_bytes=raw,
        hash_scope=HashScope.NORMALIZED_TEXT,
    )
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://normalized",
    )
    assert outcome.receipt.status == ResearchImportStatus.ACCEPTED


def test_future_evidence_clock_is_quarantined_at_import_boundary() -> None:
    packet = _packet()
    result, contents = _result(packet)
    payload = json.loads(_submitted(result))
    payload["completed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=json.dumps(payload, sort_keys=True).encode("utf-8"),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://future-result",
    )
    assert outcome.receipt.reasons == (ResearchImportReason.VALIDATION_FAILED,)


def test_claim_excerpt_must_be_present_in_frozen_source_artifact() -> None:
    packet = _packet()
    result, contents = _result(packet)
    payload = json.loads(_submitted(result))
    payload["evidence"][0]["excerpt_context"] = "Text absent from the frozen excerpt."
    outcome = import_research_result(
        packet=packet,
        submitted_bytes=json.dumps(payload, sort_keys=True).encode("utf-8"),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://excerpt-mismatch",
    )
    assert outcome.receipt.reasons == (
        ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,
    )


def test_reimport_uses_distinct_attempt_lineage_without_id_collision() -> None:
    packet = _packet()
    result, contents = _result(packet)
    first = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW,
        run_id="import-run-1",
        submitted_artifact_locator="artifact://attempt-1",
    )
    second = import_research_result(
        packet=packet,
        submitted_bytes=_submitted(result),
        source_contents=contents,
        imported_at=NOW + timedelta(seconds=1),
        run_id="import-run-2",
        submitted_artifact_locator="artifact://attempt-2",
    )
    assert first.submitted_artifact.artifact_id != second.submitted_artifact.artifact_id
    assert first.receipt.record_id != second.receipt.record_id


def test_importer_passes_offline_capability_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_source_tree(root / "src/polymarket_alpha/research")
    assert result.passed, result.violations
