"""P0-08C offline market-aware packet construction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.platform.market_data.capture_contract import materialize_orderbook_capture
from src.polymarket_alpha.books import FrozenOwnerBookArtifact, normalize_paired_owner_books
from src.polymarket_alpha.contracts import (
    BookCapturePurpose,
    CaptureScope,
    ClaimEvidence,
    EstimateStage,
    EvidenceOrigin,
    EvidenceSupport,
    HashScope,
    MarketIdentity,
    ProbabilityEstimate,
    RecallHit,
    RecallerType,
    Replayability,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    RuleContract,
    RuleGate,
    SourceArtifact,
    SourceTier,
    bytes_sha256,
    stable_record_id,
)
from src.polymarket_alpha.research.blind import build_blind_research_packet
from src.polymarket_alpha.research.market import (
    build_market_formal_review_demand,
    freeze_market_research_packet,
)
from src.polymarket_alpha.rules.models import RuleGateDecision, RuleGateStage
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
NOW = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)
RULE_HASH = "a" * 64


def _env(record_id: str, *, run_id: str, source: str = "fixture") -> dict[str, object]:
    return {
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
        event_id="event-market-stage",
        market_id="market-market-stage",
        condition_id="condition-market-stage",
        yes_token_id="yes-market-stage",
        no_token_id="no-market-stage",
    )


def _contract() -> RuleContract:
    record_id = "rule_contract:" + "b" * 64
    return RuleContract(
        **_env(record_id, run_id="rule-run", source="rule_compiler"),
        market_id=_identity().market_id,
        rule_hash=RULE_HASH,
        contract_revision_id="rule_revision:" + "c" * 64,
        subject_entity="Example Agency",
        entity_match_rule="The named agency in the rule",
        yes_trigger="A final official bulletin confirms the defined event",
        deadline=NOW + timedelta(days=1),
        timezone="UTC",
        resolution_sources=("Example Agency public bulletin",),
        source_precedence=("final bulletin",),
        initial_or_final="FINAL",
        clarity_score=Decimal("0.95"),
        rule_gate=RuleGate.PASS,
        parser_version="fixture-v1",
    )


def _gate(contract: RuleContract) -> RuleGateDecision:
    record_id = stable_record_id("rule_gate_decision", contract.record_id, "A")
    return RuleGateDecision(
        **_env(record_id, run_id="gate-run", source="rule_gate"),
        stage=RuleGateStage.A,
        market_id=contract.market_id,
        rule_contract_id=contract.record_id,
        rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id,
        compiler_version=contract.source_version,
        decision="PASS",
        reasons=("fixture",),
        input_artifact_ids=(contract.record_id,),
        evaluated_at=NOW,
    )


def _blind_inputs():
    contract = _contract()
    recall = RecallHit(
        **_env("recall:market-stage", run_id="recall-run", source="recaller"),
        market_id=contract.market_id,
        recaller=RecallerType.NEW_CHANGED,
        recaller_version="fixture-v1",
        reason_codes=("NEW",),
        features={"private": "not copied to blind"},
        raw_score=Decimal("1"),
        observed_at=NOW,
    )
    from src.polymarket_alpha.contracts import CandidateCard, ResearchPriority

    candidate = CandidateCard(
        **_env("candidate_card:" + "d" * 64, run_id="candidate-run", source="aggregator"),
        candidate_id="candidate:" + "e" * 64,
        market_id=contract.market_id,
        recall_hit_ids=(recall.record_id,),
        recall_score=Decimal("1"),
        dedup_group="market-stage",
        selected_at=NOW,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("private",),
    )
    gate = _gate(contract)
    blind = build_blind_research_packet(
        candidate=candidate,
        recall_hits=(recall,),
        contract=contract,
        gate_a=gate,
        build_run_identity="blind-stage-run",
        created_at=NOW,
    )
    return candidate, contract, gate, blind.packet


def _accepted_blind_result():
    candidate, contract, gate, blind_packet = _blind_inputs()
    run_id = "research-run"
    raw = b"Example Agency issued a final factual bulletin."
    artifact_id = "source_artifact:" + "f" * 64
    artifact = SourceArtifact(
        **_env(artifact_id, run_id=run_id, source="source_capture"),
        artifact_id=artifact_id,
        source_name="Example Agency",
        source_url_or_source_id="https://agency.example/final",
        media_type="text/plain",
        captured_at=NOW,
        effective_as_of=NOW - timedelta(minutes=1),
        capture_scope=CaptureScope.EXCERPT_ONLY,
        hash_scope=HashScope.CLAIM_EXCERPT,
        content_sha256=bytes_sha256(raw),
        content_length_bytes=len(raw),
        artifact_locator="artifact://agency-final",
        replayability=Replayability.EXCERPT,
    )
    evidence_id = "evidence:" + "1" * 64
    evidence = ClaimEvidence(
        **_env(evidence_id, run_id=run_id, source="claim_importer"),
        evidence_id=evidence_id,
        claim="Example Agency issued a final factual bulletin.",
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
        excerpt_context=raw.decode(),
        replayability=artifact.replayability,
    )
    estimate = ProbabilityEstimate(
        **_env("probability_estimate:" + "2" * 64, run_id=run_id, source="manual_research"),
        blind_candidate_id=blind_packet.projection.blind_candidate_id,
        estimate_stage=EstimateStage.BLIND,
        model_type="manual structured research",
        p_event_yes_low=Decimal("0.2"),
        p_event_yes_mid=Decimal("0.3"),
        p_event_yes_high=Decimal("0.4"),
        uncertainty_drivers=("source timing",),
        assumptions=("official source is authoritative",),
        model_version="fixture-v1",
    )
    result_id = stable_record_id("research_result", blind_packet.record_id, "fixture")
    result = ResearchResultEnvelope(
        **_env(result_id, run_id=run_id, source="research_result_importer"),
        result_id=result_id,
        packet_stage="BLIND",
        packet_id=blind_packet.record_id,
        packet_sha256=blind_packet.canonical_sha256,
        probability_estimate=estimate,
        evidence=(evidence,),
        source_artifacts=(artifact,),
        completed_at=NOW,
        producer="manual provider",
        producer_version="fixture-v1",
    )
    receipt_id = stable_record_id("research_import_receipt", result.result_id, result.canonical_sha256)
    receipt = ResearchImportReceipt(
        **_env(receipt_id, run_id=run_id, source="research_result_importer"),
        import_receipt_id=receipt_id,
        packet_stage="BLIND",
        packet_id=blind_packet.record_id,
        packet_sha256=blind_packet.canonical_sha256,
        submitted_artifact_id="source_artifact:" + "3" * 64,
        submitted_result_sha256="4" * 64,
        status=ResearchImportStatus.ACCEPTED,
        reasons=(ResearchImportReason.ACCEPTED,),
        imported_at=NOW,
        importer_version="fixture-v1",
        accepted_result_id=result.result_id,
        accepted_result_sha256=result.canonical_sha256,
    )
    return candidate, contract, gate, blind_packet, result, receipt


def _demand_and_book(*, max_staleness_seconds: int = 30):
    candidate, contract, gate, blind_packet, result, blind_receipt = _accepted_blind_result()
    demand = build_market_formal_review_demand(
        candidate=candidate,
        contract=contract,
        gate_a=gate,
        blind_packet=blind_packet,
        blind_result=result,
        blind_import_receipt=blind_receipt,
        identity=_identity(),
        requested_at=NOW,
        valid_until=NOW + timedelta(minutes=2),
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=(Decimal("10"),),
        run_id="formal-demand-run",
    )
    def artifact(token_id: str) -> FrozenOwnerBookArtifact:
        raw = {
            "bids": [{"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.50", "size": "20"}],
        }
        captured = materialize_orderbook_capture(
            token_id=token_id,
            raw_book=raw,
            request_started_at_utc="2026-08-27T16:00:00Z",
            response_received_at_utc="2026-08-27T16:00:01Z",
            parsed_at_utc="2026-08-27T16:00:01.001Z",
            request_batch_capture_id="market-stage-paired-capture",
        )
        return FrozenOwnerBookArtifact(
            token_id=token_id,
            raw_book=raw,
            capture=captured,
            raw_artifact_id=f"owner_book_artifact:{captured['book_capture_id']}",
        )
    normalized = normalize_paired_owner_books(
        demand=demand,
        yes_artifact=artifact(_identity().yes_token_id),
        no_artifact=artifact(_identity().no_token_id),
        received_at=NOW + timedelta(seconds=2),
    )
    assert normalized.snapshot is not None
    return candidate, contract, gate, blind_packet, result, blind_receipt, demand, normalized.receipt, normalized.snapshot


def _freeze(
    *,
    build_run_identity: str = "market-packet-run",
    created_at: datetime = NOW + timedelta(seconds=3),
):
    return freeze_market_research_packet(
        **dict(zip(
            ("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"),
            _demand_and_book(),
            strict=True,
        )),
        build_run_identity=build_run_identity,
        created_at=created_at,
    )


def test_accepted_blind_path_declares_exact_formal_review_demand_and_freezes_packet() -> None:
    values = _demand_and_book()
    packet = freeze_market_research_packet(
        **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
        build_run_identity="market-packet-run",
        created_at=NOW + timedelta(seconds=3),
    )
    demand = values[6]
    assert demand.purpose == BookCapturePurpose.FORMAL_REVIEW
    assert demand.trigger_artifact_id == values[4].result_id
    assert packet.blind_packet_id == values[3].record_id
    assert packet.blind_result_id == values[4].result_id
    assert packet.rule_contract == values[1]
    assert packet.orderbook == values[8]
    assert {item.relation for item in packet.provenance} == {
        "gate_a", "blind_packet", "accepted_blind_result", "blind_import_receipt",
        "formal_review_demand", "formal_review_receipt", "fresh_paired_orderbook",
    }


def test_unaccepted_blind_result_cannot_create_formal_demand() -> None:
    values = list(_demand_and_book())
    receipt = values[5].model_copy(update={"status": ResearchImportStatus.QUARANTINED, "reasons": (ResearchImportReason.VALIDATION_FAILED,), "accepted_result_id": None, "accepted_result_sha256": None, "quarantine_artifact_id": "source_artifact:" + "3" * 64})
    with pytest.raises(ValueError, match="accepted Blind import receipt"):
        build_market_formal_review_demand(
            candidate=values[0], contract=values[1], gate_a=values[2], blind_packet=values[3],
            blind_result=values[4], blind_import_receipt=receipt, identity=_identity(),
            requested_at=NOW, valid_until=NOW + timedelta(minutes=2), max_staleness_seconds=30,
            target_sizes=(Decimal("10"),), run_id="formal-demand-run",
        )


@pytest.mark.parametrize("mutation, message", [
    ("sensing", "SENSING"),
    ("stale", "successful fresh"),
    ("one_sided", "one-sided"),
    ("insufficient", "insufficient target depth"),
    ("missing_depth", "exact YES target-depth coverage"),
])
def test_market_packet_rejects_unusable_or_wrong_purpose_book(mutation: str, message: str) -> None:
    values = list(_demand_and_book(max_staleness_seconds=1 if mutation == "stale" else 30))
    if mutation == "sensing":
        values[6] = values[6].model_copy(update={"purpose": BookCapturePurpose.SENSING, "blind_result_id": None})
    elif mutation == "stale":
        values[7] = values[7].model_copy(update={"status": "STALE", "error_code": "BOOK_STALE"})
        values[8] = values[8].model_copy(update={"stale": True})
    elif mutation == "one_sided":
        values[8] = values[8].model_copy(update={"yes_leg": values[8].yes_leg.model_copy(update={"bids": ()})})
    else:
        if mutation == "insufficient":
            values[8] = values[8].model_copy(update={"yes_depth": tuple(row.model_copy(update={"buy_insufficient_depth": True}) for row in values[8].yes_depth)})
        else:
            values[8] = values[8].model_copy(update={"yes_depth": ()})
    if mutation in {"one_sided", "insufficient", "missing_depth"}:
        # Keep the receipt cryptographically bound to this adversarial frozen
        # snapshot so the test reaches the independent quality gate.
        values[7] = values[7].model_copy(
            update={"orderbook_snapshot_sha256": values[8].canonical_sha256}
        )
    with pytest.raises(ValueError, match=message):
        freeze_market_research_packet(
            **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
            build_run_identity="market-packet-run", created_at=NOW + timedelta(seconds=3),
        )


def test_mismatched_book_receipt_or_blind_packet_fails_closed() -> None:
    values = list(_demand_and_book())
    values[7] = values[7].model_copy(update={"demand_sha256": "9" * 64})
    with pytest.raises(ValueError, match="exact formal-review demand"):
        freeze_market_research_packet(
            **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
            build_run_identity="market-packet-run", created_at=NOW + timedelta(seconds=3),
        )
    values = list(_demand_and_book())
    values[4] = values[4].model_copy(update={"packet_sha256": "8" * 64})
    with pytest.raises(ValueError, match="Blind packet"):
        freeze_market_research_packet(
            **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
            build_run_identity="market-packet-run", created_at=NOW + timedelta(seconds=3),
        )
    values = list(_demand_and_book())
    values[6] = values[6].model_copy(update={"trigger_artifact_sha256": "7" * 64})
    with pytest.raises(ValueError, match="trigger hash"):
        freeze_market_research_packet(
            **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
            build_run_identity="market-packet-run", created_at=NOW + timedelta(seconds=3),
        )


def test_market_stage_rejects_blind_candidate_mismatch_and_compiler_mismatch() -> None:
    values = list(_demand_and_book())
    values[0] = values[0].model_copy(update={"candidate_id": "candidate:" + "9" * 64})
    with pytest.raises(ValueError, match="does not bind this Candidate"):
        build_market_formal_review_demand(
            candidate=values[0], contract=values[1], gate_a=values[2],
            blind_packet=values[3], blind_result=values[4],
            blind_import_receipt=values[5], identity=_identity(), requested_at=NOW,
            valid_until=NOW + timedelta(minutes=2), max_staleness_seconds=30,
            target_sizes=(Decimal("10"),), run_id="formal-demand-run",
        )

    values = list(_demand_and_book())
    values[2] = values[2].model_copy(update={"compiler_version": "wrong-compiler"})
    with pytest.raises(ValueError, match="compiler"):
        build_market_formal_review_demand(
            candidate=values[0], contract=values[1], gate_a=values[2],
            blind_packet=values[3], blind_result=values[4],
            blind_import_receipt=values[5], identity=_identity(), requested_at=NOW,
            valid_until=NOW + timedelta(minutes=2), max_staleness_seconds=30,
            target_sizes=(Decimal("10"),), run_id="formal-demand-run",
        )


def test_market_stage_rejects_future_acceptance_and_expired_packet_creation() -> None:
    values = list(_demand_and_book())
    future_receipt = values[5].model_copy(
        update={"imported_at": NOW + timedelta(seconds=1)}
    )
    with pytest.raises(ValueError, match="cannot precede Blind acceptance"):
        build_market_formal_review_demand(
            candidate=values[0], contract=values[1], gate_a=values[2],
            blind_packet=values[3], blind_result=values[4],
            blind_import_receipt=future_receipt, identity=_identity(), requested_at=NOW,
            valid_until=NOW + timedelta(minutes=2), max_staleness_seconds=30,
            target_sizes=(Decimal("10"),), run_id="formal-demand-run",
        )

    with pytest.raises(ValueError, match="after demand expiry"):
        freeze_market_research_packet(
            **dict(zip(("candidate", "contract", "gate_a", "blind_packet", "blind_result", "blind_import_receipt", "demand", "book_receipt", "snapshot"), values, strict=True)),
            build_run_identity="market-packet-run",
            created_at=NOW + timedelta(minutes=3),
        )


def test_exact_retry_is_stable_and_cross_run_attempt_has_new_id() -> None:
    first = _freeze()
    retry = _freeze()
    other = _freeze(build_run_identity="market-packet-run-2", created_at=NOW + timedelta(minutes=1))
    assert retry.record_id == first.record_id
    assert retry.canonical_sha256 == first.canonical_sha256
    assert other.record_id != first.record_id
    assert other.canonical_sha256 != first.canonical_sha256


def test_market_stage_source_tree_passes_static_security_audit() -> None:
    findings = audit_source_tree("src/polymarket_alpha/research/market.py")
    assert findings.violations == ()
