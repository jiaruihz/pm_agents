"""One deterministic, offline-only P0 integration fixture.

This is intentionally not a scheduler, collector, provider, or runtime entry
point.  It receives an already-created Alpha repository and joins the released
pure APIs using fixed in-memory fixture bytes.  The only outcome is a
``NO_ORDER`` simulated ledger record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple

from src.platform.market_data.capture_contract import materialize_orderbook_capture

from ..books import FrozenOwnerBookArtifact, normalize_paired_owner_books
from ..change import detect_market_change
from ..contracts import (
    CandidateCard, CandidateEventType, CandidateState, CandidateTransition,
    CaptureScope, ClaimEvidence, EstimateStage, EvidenceOrigin, EvidenceSupport,
    HashScope, MarketIdentity, MarketSnapshot, MarketStatus, PacketStage,
    ProbabilityEstimate, Replayability, ResearchPriority, ResearchResultEnvelope,
    SourceArtifact, SourceTier, bytes_sha256, canonical_json, rule_sha256,
    stable_record_id,
)
from ..decision import RankConfig, RankOutcome, build_ranked_ledger, persist_ranked_ledger
from ..protocol import reduce_candidate_lifecycle
from ..recall.aggregate import ProviderBatch, RecallAggregationRequest, RecallAggregator
from ..recall.new_changed import NewChangedRecaller, PrebookRecallRequest
from ..recall.registry import ProviderRegistry
from ..research import build_blind_research_packet, import_research_result
from ..research.market import build_market_formal_review_demand, freeze_market_research_packet
from ..rules import (
    ParseStatus, RuleCompilationRequest, RuleContractCompiler, RuleSourceEvidence,
    StructuredRuleParse, evaluate_gate_a, evaluate_gate_b,
)
from ..storage import AlphaRepository


UTC = timezone.utc
PILOT_NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
RAW_RULE = (
    "This market resolves Yes if Example Agency publishes a final bulletin "
    "confirming at least ten qualifying events by August 31, 2026."
)


class OfflinePilotResult(NamedTuple):
    """Frozen outputs retained for P0-12 acceptance and replay checks."""

    snapshot: MarketSnapshot
    change_event_id: str
    candidate: CandidateCard
    blind_packet_id: str
    market_packet_id: str
    lifecycle_state: CandidateState
    ranked: RankOutcome
    persisted_record_ids: tuple[str, ...]


def _identity() -> MarketIdentity:
    return MarketIdentity(
        event_id="pilot-event", market_id="pilot-market", condition_id="pilot-condition",
        yes_token_id="pilot-yes", no_token_id="pilot-no",
    )


def _envelope(record_id: str, run_id: str, at: datetime, source: str) -> dict[str, object]:
    return {
        "record_id": record_id, "run_id": run_id, "created_at": at,
        "source": source, "source_version": "p0_12_fixture_v1",
        "provenance": (), "extensions": {},
    }


def _research_result(packet, *, stage: PacketStage, at: datetime, run_id: str, blind_candidate_id: str | None = None):
    """Create replayable manual fixture bytes then use the real importer."""

    content = b"Example Agency issued a final official bulletin confirming ten events."
    artifact_id = stable_record_id("source_artifact", run_id, "official")
    artifact = SourceArtifact(
        **_envelope(artifact_id, run_id, at - timedelta(seconds=2), "fixture_source"),
        artifact_id=artifact_id, source_name="Example Agency",
        source_url_or_source_id="https://agency.example/final", media_type="text/plain",
        captured_at=at - timedelta(seconds=2), effective_as_of=at - timedelta(minutes=1),
        capture_scope=CaptureScope.EXCERPT_ONLY, hash_scope=HashScope.CLAIM_EXCERPT,
        content_sha256=bytes_sha256(content), content_length_bytes=len(content),
        artifact_locator=f"artifact://p0-12/{run_id}", replayability=Replayability.EXCERPT,
    )
    evidence_id = stable_record_id("evidence", run_id, "official")
    evidence = ClaimEvidence(
        **_envelope(evidence_id, run_id, at - timedelta(seconds=1), "fixture_claim"),
        evidence_id=evidence_id, claim="A final official bulletin confirms ten events.",
        supports_yes_or_no=EvidenceSupport.YES, source_tier=SourceTier.T0,
        source_name=artifact.source_name, source_url_or_source_id=artifact.source_url_or_source_id,
        accessed_at=at - timedelta(seconds=1), effective_as_of=artifact.effective_as_of,
        primary_or_secondary="PRIMARY", quotation_or_paraphrase_location="fixture paragraph 1",
        confidence=Decimal("0.95"), origin=EvidenceOrigin.PRIMARY_SOURCE,
        source_artifact_id=artifact_id, capture_scope=artifact.capture_scope,
        hash_scope=artifact.hash_scope, content_sha256=artifact.content_sha256,
        excerpt_context=content.decode(), replayability=artifact.replayability,
    )
    blind_id = packet.projection.blind_candidate_id if stage == PacketStage.BLIND else blind_candidate_id
    if blind_id is None:
        raise ValueError("market-aware fixture result requires the frozen blind candidate id")
    estimate_id = stable_record_id("probability_estimate", run_id, packet.record_id)
    estimate_kwargs = dict(
        blind_candidate_id=blind_id, estimate_stage=(EstimateStage.BLIND if stage == PacketStage.BLIND else EstimateStage.FINAL),
        model_type="offline fixture research", p_event_yes_low=Decimal("0.74"),
        p_event_yes_mid=Decimal("0.80"), p_event_yes_high=Decimal("0.86"),
        uncertainty_drivers=("fixture only",), assumptions=("official source is authoritative",),
        model_version="p0_12_fixture_v1",
    )
    if stage == PacketStage.MARKET_AWARE:
        estimate_kwargs.update(market_id=packet.market_id, p_market_yes_low=Decimal("0.45"),
                               p_market_yes_mid=Decimal("0.50"), p_market_yes_high=Decimal("0.55"))
    estimate = ProbabilityEstimate(**_envelope(estimate_id, run_id, at, "fixture_research"), **estimate_kwargs)
    result_id = stable_record_id("research_result", packet.record_id, run_id)
    result = ResearchResultEnvelope(
        **_envelope(result_id, run_id, at, "fixture_research"), result_id=result_id,
        packet_stage=stage, packet_id=packet.record_id, packet_sha256=packet.canonical_sha256,
        probability_estimate=estimate, evidence=(evidence,), source_artifacts=(artifact,),
        completed_at=at, producer="offline fixture", producer_version="p0_12_fixture_v1",
    )
    return import_research_result(
        packet=packet, submitted_bytes=canonical_json(result).encode(),
        source_contents={artifact_id: content}, imported_at=at + timedelta(seconds=10),
        run_id=f"import-{run_id}", submitted_artifact_locator=f"artifact://p0-12/submitted/{run_id}",
    )


def _transition(candidate: CandidateCard, before: CandidateState, after: CandidateState, at: datetime, artifact_id: str) -> CandidateTransition:
    record_id = stable_record_id("candidate_transition", candidate.candidate_id, before, after, at, artifact_id)
    return CandidateTransition(
        **_envelope(record_id, "p0-12-lifecycle", at, "p0_12_fixture"),
        candidate_id=candidate.candidate_id, event_type=CandidateEventType.STATE_TRANSITION,
        from_state=before, to_state=after, reason="offline fixture protocol advance",
        actor="p0_12_fixture", at=at, related_artifact_ids=(artifact_id,),
        input_hash=bytes_sha256(artifact_id.encode()),
    )


def run_offline_fixture_pilot(repository: AlphaRepository) -> OfflinePilotResult:
    """Execute one closed-world happy path and atomically persist no-order facts."""

    identity, rule_hash = _identity(), rule_sha256(RAW_RULE)
    snapshot = MarketSnapshot(
        **_envelope(stable_record_id("gamma_market_snapshot", identity.market_id, PILOT_NOW), "p0-12-catalog", PILOT_NOW, "fixture_catalog"),
        identity=identity, title="Example Agency final bulletin", question="Will the agency publish?",
        status=MarketStatus.ACTIVE, end_at=PILOT_NOW + timedelta(days=4), rules_raw=RAW_RULE,
        rule_hash=rule_hash, source_observed_at=PILOT_NOW, ingested_at=PILOT_NOW,
    )
    event = detect_market_change(None, snapshot, run_id="p0-12-change", detected_at=PILOT_NOW + timedelta(minutes=1))
    if event is None:
        raise RuntimeError("new fixture market did not produce a change event")
    prebook = NewChangedRecaller().recall(PrebookRecallRequest(
        run_id="p0-12-recall", as_of=PILOT_NOW + timedelta(minutes=2), events=(event,), snapshots=(snapshot,),
    ))
    aggregation = RecallAggregator(ProviderRegistry((NewChangedRecaller().descriptor(),))).aggregate(
        RecallAggregationRequest(run_id="p0-12-aggregate", created_at=PILOT_NOW + timedelta(minutes=2),
            as_of=PILOT_NOW + timedelta(minutes=2), batches=(ProviderBatch(provider_id="new_changed", hits=prebook.hits),))
    )
    candidate = aggregation.results[0].candidate
    compiled_at = PILOT_NOW + timedelta(minutes=3)
    source_hash = bytes_sha256(RAW_RULE.encode())
    evidence = RuleSourceEvidence(field_names=("subject_entity", "entity_match_rule", "yes_trigger", "deadline", "resolution_sources", "source_precedence"),
        source_artifact_id=stable_record_id("source_artifact", "p0-12-rule"), source_content_sha256=source_hash,
        artifact_text=RAW_RULE, quote_start=0, quote_end=len(RAW_RULE), legal_role="binding_resolution_rule",
        adjudication_use="binding", observed_at=compiled_at)
    parsed = StructuredRuleParse(subject_entity="Example Agency", entity_match_rule="named agency",
        yes_trigger="A final bulletin confirms at least ten qualifying events", deadline=PILOT_NOW + timedelta(days=4),
        timezone="UTC", resolution_sources=("Example Agency final bulletin",), source_precedence=("final bulletin",),
        initial_or_final="FINAL", clarity_score=Decimal("0.95"))
    compiled = RuleContractCompiler().compile(RuleCompilationRequest(
        run_id="p0-12-rule", market_id=identity.market_id, market_snapshot_id=snapshot.record_id,
        raw_rule_text=RAW_RULE, expected_rule_hash=rule_hash, parse_status=ParseStatus.PARSED, parsed=parsed,
        parser_version="p0_12_fixture", compiled_at=compiled_at, source_evidence=(evidence,),
    ))
    if compiled.contract is None:
        raise RuntimeError("fixture RuleContract compilation was unexpectedly blocked")
    contract = compiled.contract
    gate_a = evaluate_gate_a(contract, compiled.receipt, run_id="p0-12-gate-a", evaluated_at=PILOT_NOW + timedelta(minutes=4))
    blind = build_blind_research_packet(candidate=candidate, recall_hits=prebook.hits, contract=contract, gate_a=gate_a,
        build_run_identity="p0-12-blind", created_at=PILOT_NOW + timedelta(minutes=5))
    blind_import = _research_result(blind.packet, stage=PacketStage.BLIND, at=PILOT_NOW + timedelta(minutes=6), run_id="p0-12-blind-result")
    if blind_import.result is None:
        raise RuntimeError("fixture Blind research result was not accepted")
    demand = build_market_formal_review_demand(candidate=candidate, contract=contract, gate_a=gate_a, blind_packet=blind.packet,
        blind_result=blind_import.result, blind_import_receipt=blind_import.receipt, identity=identity,
        requested_at=PILOT_NOW + timedelta(minutes=7), valid_until=PILOT_NOW + timedelta(minutes=12),
        max_staleness_seconds=120, target_sizes=(Decimal("10"),), run_id="p0-12-formal-demand")
    def owner_artifact(token_id: str) -> FrozenOwnerBookArtifact:
        raw = {"timestamp": "1787825240000", "bids": [{"price": "0.40", "size": "30"}], "asks": [{"price": "0.50", "size": "30"}]}
        capture = materialize_orderbook_capture(token_id=token_id, raw_book=raw,
            request_started_at_utc="2026-08-27T10:07:00Z", response_received_at_utc="2026-08-27T10:07:20Z",
            parsed_at_utc="2026-08-27T10:07:21Z", request_batch_capture_id="p0-12-paired")
        return FrozenOwnerBookArtifact(token_id=token_id, raw_book=raw, capture=capture,
            raw_artifact_id=f"owner_book:{capture['book_capture_id']}")
    normalized = normalize_paired_owner_books(demand=demand, yes_artifact=owner_artifact(identity.yes_token_id),
        no_artifact=owner_artifact(identity.no_token_id), received_at=PILOT_NOW + timedelta(minutes=8))
    if normalized.snapshot is None:
        raise RuntimeError("fixture paired book was not accepted")
    market_packet = freeze_market_research_packet(candidate=candidate, contract=contract, gate_a=gate_a,
        blind_packet=blind.packet, blind_result=blind_import.result, blind_import_receipt=blind_import.receipt,
        demand=demand, book_receipt=normalized.receipt, snapshot=normalized.snapshot,
        build_run_identity="p0-12-market", created_at=PILOT_NOW + timedelta(minutes=9))
    market_import = _research_result(market_packet, stage=PacketStage.MARKET_AWARE, at=PILOT_NOW + timedelta(minutes=10), run_id="p0-12-market-result", blind_candidate_id=blind.packet.projection.blind_candidate_id)
    if market_import.result is None:
        raise RuntimeError("fixture Market research result was not accepted")
    gate_b = evaluate_gate_b(contract, gate_a, market_packet_id=market_packet.record_id,
        market_packet_rule_hash=contract.rule_hash, market_packet_contract_revision_id=contract.contract_revision_id,
        run_id="p0-12-gate-b", evaluated_at=PILOT_NOW + timedelta(minutes=11))
    transitions = (
        _transition(candidate, CandidateState.CANDIDATE_MERGED, CandidateState.RULE_A_PASSED, PILOT_NOW + timedelta(minutes=4), gate_a.record_id),
        _transition(candidate, CandidateState.RULE_A_PASSED, CandidateState.BLIND_PROJECTION_FROZEN, PILOT_NOW + timedelta(minutes=5), blind.projection.record_id),
        _transition(candidate, CandidateState.BLIND_PROJECTION_FROZEN, CandidateState.BLIND_PACKET_FROZEN, PILOT_NOW + timedelta(minutes=5, seconds=1), blind.packet.record_id),
        _transition(candidate, CandidateState.BLIND_PACKET_FROZEN, CandidateState.BLIND_RESULT_ACCEPTED, PILOT_NOW + timedelta(minutes=6, seconds=10), blind_import.receipt.record_id),
        _transition(candidate, CandidateState.BLIND_RESULT_ACCEPTED, CandidateState.BOOK_SNAPSHOT_ACCEPTED, PILOT_NOW + timedelta(minutes=8), normalized.receipt.record_id),
        _transition(candidate, CandidateState.BOOK_SNAPSHOT_ACCEPTED, CandidateState.MARKET_PACKET_FROZEN, PILOT_NOW + timedelta(minutes=9), market_packet.record_id),
        _transition(candidate, CandidateState.MARKET_PACKET_FROZEN, CandidateState.MARKET_RESULT_ACCEPTED, PILOT_NOW + timedelta(minutes=10, seconds=10), market_import.receipt.record_id),
        _transition(candidate, CandidateState.MARKET_RESULT_ACCEPTED, CandidateState.RULE_B_PASSED, PILOT_NOW + timedelta(minutes=11), gate_b.record_id),
    )
    rule_b_lifecycle = reduce_candidate_lifecycle(candidate, transitions)
    rankable_candidate = candidate.model_copy(update={"state": rule_b_lifecycle.current_state})
    ranked = build_ranked_ledger(candidate=rankable_candidate, contract=contract, gate_a=gate_a, gate_b=gate_b,
        blind_result=blind_import.result, blind_receipt=blind_import.receipt, market_packet=market_packet,
        market_result=market_import.result, market_receipt=market_import.receipt, book=normalized.snapshot,
        config=RankConfig(
            version="p0-12-fixture",
            watch_threshold=Decimal("0.05"),
            simulate_threshold=Decimal("0.10"),
            fee_slippage_cost_policy_id="p0_12_fixture_fee_free",
            fee_slippage_cost_policy_version="v1",
            fee_rate=Decimal("0"),
            slippage_buffer=Decimal("0"),
        ),
        as_of=PILOT_NOW + timedelta(minutes=12), run_id="p0-12-decision")
    if ranked.prediction.position_state.value != "SIMULATED":
        raise RuntimeError("offline fixture must end in a simulated prediction")

    final_transitions = (
        _transition(
            candidate, CandidateState.RULE_B_PASSED, CandidateState.RANKED,
            PILOT_NOW + timedelta(minutes=12, seconds=1), ranked.decision.record_id,
        ),
        _transition(
            candidate, CandidateState.RANKED, CandidateState.SIMULATION_RECORDED,
            PILOT_NOW + timedelta(minutes=12, seconds=2), ranked.prediction.record_id,
        ),
    )
    lifecycle = reduce_candidate_lifecycle(candidate, (*transitions, *final_transitions))

    pipeline_contracts = (
        snapshot,
        event,
        *prebook.hits,
        candidate,
        contract,
        compiled.receipt,
        gate_a,
        blind.projection,
        blind.packet,
        blind_import.submitted_artifact,
        blind_import.result,
        blind_import.receipt,
        demand,
        normalized.snapshot,
        normalized.receipt,
        market_packet,
        market_import.submitted_artifact,
        market_import.result,
        market_import.receipt,
        gate_b,
        *transitions,
    )
    repository.save_contracts_atomic(pipeline_contracts)
    persist_ranked_ledger(repository, ranked)
    repository.save_contracts_atomic(final_transitions)
    persisted_record_ids = tuple(
        item.record_id
        for item in (*pipeline_contracts, ranked.decision, ranked.prediction, *final_transitions)
    )
    return OfflinePilotResult(snapshot, event.record_id, rankable_candidate, blind.packet.record_id,
        market_packet.record_id, lifecycle.current_state, ranked, persisted_record_ids)
