from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.polymarket_alpha.contracts import (
    ALPHA_CONTRACT_VERSION,
    BookCaptureDemand,
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    CaptureScope,
    ClaimEvidence,
    EstimateStage,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    MarketChangeEvent,
    MarketChangeType,
    MarketIdentity,
    MarketStatus,
    PacketStage,
    ProbabilityEstimate,
    Replayability,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    SourceArtifact,
    SourceTier,
    canonical_json,
    content_sha256,
    contract_schema_bundle,
    contract_schema_fingerprint,
    stable_record_id,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 4, 5, 6, tzinfo=UTC)
FIXTURES = Path(__file__).with_name("fixtures")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _env(record_id: str, *, run_id: str = f"alpha_run:{SHA_A}", source: str = "fixture") -> dict[str, object]:
    return {
        "schema_version": ALPHA_CONTRACT_VERSION,
        "record_id": record_id,
        "run_id": run_id,
        "created_at": NOW,
        "source": source,
        "source_version": "fixture-v1",
        "provenance": (),
        "extensions": {},
    }


def _identity() -> MarketIdentity:
    return MarketIdentity(
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        yes_token_id="token-yes",
        no_token_id="token-no",
    )


def _change_id(*, run_id: str = "ignored") -> str:
    del run_id
    return stable_record_id(
        "market_change",
        "market-1",
        f"market_snapshot:{SHA_B}",
        SHA_B,
        (MarketChangeType.RULE_CHANGED, MarketChangeType.METADATA_CHANGED),
        NOW,
    )


def _change_event(*, run_id: str = f"alpha_run:{SHA_A}") -> MarketChangeEvent:
    record_id = _change_id(run_id=run_id)
    return MarketChangeEvent(
        **_env(record_id, run_id=run_id, source="market_change_detector"),
        change_event_id=record_id,
        market_id="market-1",
        previous_snapshot_id=f"market_snapshot:{SHA_A}",
        current_snapshot_id=f"market_snapshot:{SHA_B}",
        previous_snapshot_sha256=SHA_A,
        current_snapshot_sha256=SHA_B,
        previous_status=MarketStatus.ACTIVE,
        current_status=MarketStatus.ACTIVE,
        previous_rule_hash=SHA_A,
        current_rule_hash=SHA_B,
        change_types=(MarketChangeType.METADATA_CHANGED, MarketChangeType.RULE_CHANGED),
        changed_fields=("question", "rule_hash"),
        effective_at=NOW,
        detected_at=NOW + timedelta(seconds=1),
    )


def _source_artifact(*, reference_only: bool = False) -> SourceArtifact:
    values: dict[str, object] = {
        **_env(f"source_artifact:{SHA_D}", run_id=f"research_run:{SHA_C}", source="source_capture"),
        "artifact_id": f"source_artifact:{SHA_D}",
        "source_name": "Official Agency",
        "source_url_or_source_id": "https://example.org/bulletin/1",
        "media_type": "text/plain",
        "captured_at": NOW,
        "effective_as_of": NOW - timedelta(hours=1),
        "capture_scope": CaptureScope.EXCERPT_ONLY,
        "hash_scope": HashScope.CLAIM_EXCERPT,
        "content_sha256": SHA_D,
        "content_length_bytes": 123,
        "artifact_locator": "artifact-store://sha256/dddd",
        "replayability": Replayability.EXCERPT,
    }
    if reference_only:
        values.update(
            capture_scope=CaptureScope.REFERENCE_ONLY,
            hash_scope=None,
            content_sha256=None,
            content_length_bytes=None,
            artifact_locator=None,
            replayability=Replayability.REFERENCE_ONLY,
        )
    return SourceArtifact.model_validate(values)


def _claim(*, reference_only: bool = False, origin: EvidenceOrigin = EvidenceOrigin.PRIMARY_SOURCE) -> ClaimEvidence:
    values: dict[str, object] = {
        **_env(f"evidence:{SHA_E}", run_id=f"research_run:{SHA_C}", source="claim_importer"),
        "evidence_id": f"evidence:{SHA_E}",
        "entity_id": "entity:official-agency",
        "claim": "The official agency published a final bulletin.",
        "supports_yes_or_no": EvidenceSupport.YES,
        "source_tier": SourceTier.T0,
        "source_name": "Official Agency",
        "source_url_or_source_id": "https://example.org/bulletin/1",
        "published_at": NOW - timedelta(hours=2),
        "accessed_at": NOW,
        "effective_as_of": NOW - timedelta(hours=1),
        "primary_or_secondary": "PRIMARY",
        "quotation_or_paraphrase_location": "paragraph 2",
        "confidence": Decimal("0.9"),
        "origin": origin,
        "source_artifact_id": f"source_artifact:{SHA_D}",
        "capture_scope": CaptureScope.EXCERPT_ONLY,
        "hash_scope": HashScope.CLAIM_EXCERPT,
        "content_sha256": SHA_D,
        "excerpt_context": "The final bulletin states that the event occurred.",
        "replayability": Replayability.EXCERPT,
    }
    if reference_only:
        values.update(
            capture_scope=CaptureScope.REFERENCE_ONLY,
            hash_scope=None,
            content_sha256=None,
            excerpt_context=None,
            replayability=Replayability.REFERENCE_ONLY,
        )
    return ClaimEvidence.model_validate(values)


def _estimate(*, stage: EstimateStage = EstimateStage.BLIND) -> ProbabilityEstimate:
    values: dict[str, object] = {
        **_env(
            f"probability_estimate:{SHA_F}",
            run_id=f"research_run:{SHA_C}",
            source="manual_research_import",
        ),
        "blind_candidate_id": f"blind_candidate:{SHA_A}",
        "estimate_stage": stage,
        "model_type": "manual structured research",
        "p_event_yes_low": Decimal("0.2"),
        "p_event_yes_mid": Decimal("0.3"),
        "p_event_yes_high": Decimal("0.4"),
        "uncertainty_drivers": ("source timing",),
        "assumptions": ("the official source is authoritative",),
        "model_version": "fixture-v1",
    }
    if stage != EstimateStage.BLIND:
        values.update(
            market_id="market-1",
            p_market_yes_low=Decimal("0.2"),
            p_market_yes_mid=Decimal("0.3"),
            p_market_yes_high=Decimal("0.4"),
        )
    return ProbabilityEstimate.model_validate(values)


def _blind_result(*, claim: ClaimEvidence | None = None, artifact: SourceArtifact | None = None) -> ResearchResultEnvelope:
    record_id = stable_record_id("research_result", f"blind_packet:{SHA_A}", SHA_B, SHA_F, SHA_E)
    return ResearchResultEnvelope(
        **_env(record_id, run_id=f"research_run:{SHA_C}", source="research_result_importer"),
        result_id=record_id,
        packet_stage=PacketStage.BLIND,
        packet_id=f"blind_packet:{SHA_A}",
        packet_sha256=SHA_B,
        probability_estimate=_estimate(),
        evidence=(claim or _claim(),),
        source_artifacts=(artifact or _source_artifact(),),
        completed_at=NOW,
        producer="manual research provider",
        producer_version="fixture-v1",
    )


def test_change_event_has_deterministic_cross_run_identity_and_typed_lineage() -> None:
    first = _change_event(run_id=f"alpha_run:{SHA_A}")
    retry = _change_event(run_id=f"alpha_run:{SHA_C}")
    assert first.change_event_id == retry.change_event_id == _change_id()
    assert first.run_id != retry.run_id
    assert first.change_types == (
        MarketChangeType.METADATA_CHANGED,
        MarketChangeType.RULE_CHANGED,
    )
    with pytest.raises(ValidationError, match="RULE_CHANGED"):
        MarketChangeEvent.model_validate(
            {**_change_event().model_dump(), "current_rule_hash": SHA_A}
        )


def test_new_change_has_no_previous_lineage_and_superseded_is_unreachable() -> None:
    record_id = stable_record_id("market_change", "market-1", None, SHA_A, (MarketChangeType.NEW,), NOW)
    new_event = MarketChangeEvent(
        **_env(record_id, source="market_change_detector"),
        change_event_id=record_id,
        market_id="market-1",
        current_snapshot_id=f"market_snapshot:{SHA_A}",
        current_snapshot_sha256=SHA_A,
        current_status=MarketStatus.ACTIVE,
        current_rule_hash=SHA_A,
        change_types=(MarketChangeType.NEW,),
        changed_fields=("__new__",),
        effective_at=NOW,
        detected_at=NOW,
    )
    assert new_event.previous_snapshot_id is None
    with pytest.raises(ValidationError, match="SUPERSEDED"):
        MarketChangeEvent.model_validate({**new_event.model_dump(), "current_status": "SUPERSEDED"})


def test_lifecycle_change_status_and_terminal_type_are_emitted_together() -> None:
    event = _change_event()
    closed = MarketChangeEvent.model_validate(
        {
            **event.model_dump(),
            "current_status": MarketStatus.CLOSED,
            "change_types": (MarketChangeType.CLOSED, MarketChangeType.LIFECYCLE_CHANGED),
            "changed_fields": ("status",),
            "previous_rule_hash": SHA_B,
        }
    )
    assert closed.current_status == MarketStatus.CLOSED
    with pytest.raises(ValidationError, match="LIFECYCLE_CHANGED"):
        MarketChangeEvent.model_validate(
            {**closed.model_dump(), "change_types": (MarketChangeType.CLOSED,)}
        )


def test_book_capture_purpose_ttl_and_receipt_states_fail_closed() -> None:
    sensing_id = stable_record_id("book_demand", "market-1", "SENSING", SHA_A, NOW)
    sensing = BookCaptureDemand(
        **_env(sensing_id, source="book_demand_builder"),
        demand_id=sensing_id,
        identity=_identity(),
        purpose=BookCapturePurpose.SENSING,
        trigger_artifact_id=f"market_change:{SHA_A}",
        trigger_artifact_sha256=SHA_A,
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
        max_staleness_seconds=30,
        target_sizes=(Decimal("10"), Decimal("100")),
    )
    assert sensing.blind_result_id is None
    with pytest.raises(ValidationError, match="blind_result_id"):
        BookCaptureDemand.model_validate(
            {
                **sensing.model_dump(),
                "purpose": "FORMAL_REVIEW",
            }
        )
    formal_id = stable_record_id("book_demand", "market-1", "FORMAL_REVIEW", SHA_C, NOW)
    formal = BookCaptureDemand.model_validate(
        {
            **sensing.model_dump(),
            "record_id": formal_id,
            "demand_id": formal_id,
            "purpose": BookCapturePurpose.FORMAL_REVIEW,
            "trigger_artifact_id": f"research_result:{SHA_C}",
            "trigger_artifact_sha256": SHA_C,
            "blind_result_id": f"research_result:{SHA_C}",
        }
    )
    assert formal.trigger_artifact_id == formal.blind_result_id

    receipt_id = stable_record_id("book_receipt", sensing.demand_id, "ACCEPTED", SHA_B)
    accepted = BookCaptureReceipt(
        **_env(receipt_id, source="existing_book_owner_adapter"),
        receipt_id=receipt_id,
        demand_id=sensing.demand_id,
        demand_sha256=sensing.canonical_sha256,
        market_id="market-1",
        purpose=BookCapturePurpose.SENSING,
        status=BookCaptureStatus.ACCEPTED,
        capture_owner="weather_market_books",
        received_at=NOW + timedelta(seconds=2),
        orderbook_snapshot_id=f"orderbook_snapshot:{SHA_B}",
        orderbook_snapshot_sha256=SHA_B,
        capture_group_id=f"capture_group:{SHA_C}",
        source_observed_at=NOW + timedelta(seconds=1),
    )
    assert accepted.error_code is None
    with pytest.raises(ValidationError, match="complete paired-book lineage"):
        BookCaptureReceipt.model_validate(
            {**accepted.model_dump(), "orderbook_snapshot_sha256": None}
        )


def test_source_artifact_freezes_hash_scope_and_reference_only_semantics() -> None:
    artifact = _source_artifact()
    assert artifact.content_sha256 == SHA_D
    reference = _source_artifact(reference_only=True)
    assert reference.replayability == Replayability.REFERENCE_ONLY
    with pytest.raises(ValidationError, match="REFERENCE_ONLY"):
        SourceArtifact.model_validate({**reference.model_dump(), "content_sha256": SHA_A})


def test_blind_result_binds_packet_estimate_claims_and_actual_artifacts() -> None:
    result = _blind_result()
    assert result.packet_stage == PacketStage.BLIND
    assert result.evidence[0].source_artifact_id == result.source_artifacts[0].artifact_id
    assert content_sha256(result) == result.canonical_sha256

    missing_id = f"source_artifact:{SHA_F}"
    with pytest.raises(ValidationError, match="exactly cover referenced claim artifacts"):
        _blind_result(
            claim=ClaimEvidence.model_validate(
                {**_claim().model_dump(), "source_artifact_id": missing_id}
            )
        )
    with pytest.raises(ValidationError, match="capture semantics"):
        _blind_result(
            artifact=SourceArtifact.model_validate(
                {**_source_artifact().model_dump(), "content_sha256": SHA_A}
            )
        )


def test_blind_result_rejects_wrong_stage_and_recursive_semantic_leakage() -> None:
    with pytest.raises(ValidationError, match="BLIND probability"):
        ResearchResultEnvelope.model_validate(
            {**_blind_result().model_dump(), "probability_estimate": _estimate(stage=EstimateStage.FINAL)}
        )
    leaked = ProbabilityEstimate.model_validate(
        {**_estimate().model_dump(), "assumptions": ("A wallet bought YES",)}
    )
    with pytest.raises(ValidationError, match="market-derived semantics"):
        ResearchResultEnvelope.model_validate(
            {**_blind_result().model_dump(), "probability_estimate": leaked}
        )
    with pytest.raises(ValidationError, match="market, wallet"):
        _blind_result(claim=_claim(origin=EvidenceOrigin.WALLET))


def test_reference_only_result_is_explicitly_not_content_replayable() -> None:
    result = _blind_result(claim=_claim(reference_only=True), artifact=_source_artifact(reference_only=True))
    assert result.evidence[0].content_sha256 is None
    assert result.source_artifacts[0].artifact_locator is None


def test_import_receipt_accept_reject_and_quarantine_are_unambiguous() -> None:
    base: dict[str, object] = {
        **_env(f"research_import_receipt:{SHA_A}", source="research_result_importer"),
        "import_receipt_id": f"research_import_receipt:{SHA_A}",
        "packet_stage": PacketStage.BLIND,
        "packet_id": f"blind_packet:{SHA_A}",
        "packet_sha256": SHA_A,
        "submitted_artifact_id": f"source_artifact:{SHA_B}",
        "submitted_result_sha256": SHA_B,
        "status": ResearchImportStatus.ACCEPTED,
        "reasons": (ResearchImportReason.ACCEPTED,),
        "imported_at": NOW,
        "importer_version": "importer-v1",
        "accepted_result_id": f"research_result:{SHA_C}",
        "accepted_result_sha256": SHA_C,
    }
    accepted = ResearchImportReceipt.model_validate(base)
    assert accepted.status == ResearchImportStatus.ACCEPTED

    rejected = ResearchImportReceipt.model_validate(
        {
            **base,
            "status": ResearchImportStatus.REJECTED,
            "reasons": (ResearchImportReason.PACKET_HASH_MISMATCH,),
            "accepted_result_id": None,
            "accepted_result_sha256": None,
        }
    )
    assert rejected.accepted_result_id is None
    with pytest.raises(ValidationError, match="only the ACCEPTED reason"):
        ResearchImportReceipt.model_validate(
            {**base, "reasons": (ResearchImportReason.PACKET_HASH_MISMATCH,)}
        )
    with pytest.raises(ValidationError, match="quarantine_artifact_id"):
        ResearchImportReceipt.model_validate(
            {
                **base,
                "status": ResearchImportStatus.QUARANTINED,
                "reasons": (ResearchImportReason.SOURCE_ARTIFACT_HASH_MISMATCH,),
                "accepted_result_id": None,
                "accepted_result_sha256": None,
            }
        )
    with pytest.raises(ValidationError, match="packet_id namespace"):
        ResearchImportReceipt.model_validate(
            {**base, "packet_stage": PacketStage.MARKET_AWARE}
        )


def test_r2_schema_bundle_and_golden_release_are_stable() -> None:
    required = {
        "MarketChangeEvent",
        "BookCaptureDemand",
        "BookCaptureReceipt",
        "SourceArtifact",
        "ResearchResultEnvelope",
        "ResearchImportReceipt",
    }
    assert required <= set(contract_schema_bundle())
    golden = json.loads((FIXTURES / "p0_01r2_golden.json").read_text(encoding="utf-8"))
    assert golden == {
        "schema_version": ALPHA_CONTRACT_VERSION,
        "contract_schema_sha256": contract_schema_fingerprint(),
        "market_change_id": _change_id(),
        "market_change_sha256": _change_event().canonical_sha256,
        "blind_result_id": _blind_result().result_id,
        "blind_result_sha256": _blind_result().canonical_sha256,
    }
    assert canonical_json(_change_event()) == canonical_json(_change_event())
