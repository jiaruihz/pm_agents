"""Staged offline orchestration from Candidate to no-order decision ledger.

This module coordinates released domain services; it does not call a model,
open a network connection, capture a book, or schedule work.  Manual research
crosses the immutable filesystem handoff boundary between stages.  Every stage
can therefore stop safely and resume only from hash-bound caller inputs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

from ..books import FrozenOwnerBookArtifact, PairedBookNormalization, normalize_paired_owner_books
from ..contracts import (
    CandidateCard,
    CandidateEventType,
    CandidateState,
    CandidateTransition,
    ClaimEvidence,
    BlindResearchPacket,
    BookCaptureDemand,
    MarketIdentity,
    MarketSnapshot,
    MarketResearchPacket,
    PositionState,
    RecallHit,
    ResearchImportReceipt,
    ResearchResultEnvelope,
    RuleContract,
    canonical_json,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..decision import RankConfig, RankOutcome, build_ranked_ledger
from ..protocol import CandidateLifecycleProjection, reduce_candidate_lifecycle
from ..research import build_blind_research_packet
from ..research.handoff import (
    PacketHandoffManifest,
    ResultHandoffReceipt,
    export_research_packet,
    ingest_research_result,
    seal_result_handoff,
)
from ..research.importer import ResearchImportOutcome
from ..research.market import build_market_formal_review_demand, freeze_market_research_packet
from ..rules import RuleCompilationRequest, RuleContractCompiler, evaluate_gate_a, evaluate_gate_b
from ..rules.models import RuleCompilationReceipt, RuleGateDecision
from ..storage import AlphaRepository
from .recall import MultiRecallScanOutcome


REVIEW_PIPELINE_VERSION = "p0_unified_review_v1"


class ReviewPipelineBlocked(ValueError):
    """A stage cannot advance from the supplied immutable inputs."""


class BlindReviewStage(NamedTuple):
    candidate: CandidateCard
    recall_hits: tuple[RecallHit, ...]
    contract: RuleContract
    compilation_receipt: RuleCompilationReceipt
    gate_a: RuleGateDecision
    blind_packet: BlindResearchPacket
    packet_manifest: PacketHandoffManifest
    transitions: tuple[CandidateTransition, ...]


class BlindAcceptedStage(NamedTuple):
    blind: BlindReviewStage
    result: ResearchResultEnvelope
    import_receipt: ResearchImportReceipt
    handoff_receipt: ResultHandoffReceipt
    demand: BookCaptureDemand
    transitions: tuple[CandidateTransition, ...]


class BlindResumeOutcome(NamedTuple):
    import_outcome: ResearchImportOutcome
    handoff_receipt: ResultHandoffReceipt
    accepted: BlindAcceptedStage | None


class MarketReviewStage(NamedTuple):
    blind: BlindAcceptedStage
    normalization: PairedBookNormalization
    market_packet: MarketResearchPacket
    packet_manifest: PacketHandoffManifest
    transitions: tuple[CandidateTransition, ...]


class MarketBuildOutcome(NamedTuple):
    normalization: PairedBookNormalization
    accepted: MarketReviewStage | None


class FinalReviewStage(NamedTuple):
    market: MarketReviewStage
    result: ResearchResultEnvelope
    import_receipt: ResearchImportReceipt
    handoff_receipt: ResultHandoffReceipt
    gate_b: RuleGateDecision
    ranked: RankOutcome | None
    lifecycle: CandidateLifecycleProjection
    transitions: tuple[CandidateTransition, ...]


class MarketResumeOutcome(NamedTuple):
    import_outcome: ResearchImportOutcome
    handoff_receipt: ResultHandoffReceipt
    final: FinalReviewStage | None


def _transition(
    candidate: CandidateCard,
    before: CandidateState,
    after: CandidateState,
    *,
    at: datetime,
    artifact_id: str,
    reason: str,
) -> CandidateTransition:
    at = ensure_utc(at)
    record_id = stable_record_id(
        "candidate_transition",
        candidate.candidate_id,
        before,
        after,
    )
    return CandidateTransition(
        record_id=record_id,
        run_id=stable_record_id("review_pipeline_run", candidate.candidate_id),
        created_at=at,
        source="unified_review_pipeline",
        source_version=REVIEW_PIPELINE_VERSION,
        provenance=(),
        extensions={},
        candidate_id=candidate.candidate_id,
        event_type=CandidateEventType.STATE_TRANSITION,
        from_state=before,
        to_state=after,
        reason=reason,
        actor="unified_review_pipeline",
        at=at,
        related_artifact_ids=(artifact_id,),
        input_hash=stable_record_id("transition_input", artifact_id, reason).split(":", 1)[1],
    )


def _next_at(prior: Sequence[CandidateTransition], requested: datetime) -> datetime:
    requested = ensure_utc(requested)
    if not prior:
        return requested
    return max(requested, prior[-1].at + timedelta(microseconds=1))


def _require_sealed(repository: AlphaRepository, contract: object, *, label: str) -> None:
    record_id = getattr(contract, "record_id", None)
    if not isinstance(record_id, str):
        raise ReviewPipelineBlocked(f"{label} is not a sealed Alpha contract")
    stored = repository.get_contract_json(record_id)
    if stored is None or stored != canonical_json(contract):
        raise ReviewPipelineBlocked(f"{label} is absent or hash-mismatched in the Alpha repository")


def persist_scan_candidates(
    repository: AlphaRepository,
    *,
    outcome: MultiRecallScanOutcome,
) -> tuple[str, ...]:
    """Persist one multi-route scan atomically from exact accepted hit bytes."""

    by_id = {item.record_id: item for item in outcome.accepted_hits}
    accepted = tuple(
        sorted(
            {
                hit_id
                for result in outcome.aggregation.results
                for hit_id in result.accepted_recall_hit_ids
            }
        )
    )
    if set(by_id) != set(accepted):
        raise ReviewPipelineBlocked("scan outcome does not carry exactly the aggregated RecallHit payloads")
    candidates = tuple(item.candidate for item in outcome.aggregation.results)
    contracts = (*tuple(by_id[item] for item in accepted), *candidates)
    if not contracts:
        return ()
    repository.save_contracts_atomic(contracts)
    return tuple(item.record_id for item in contracts)


def start_blind_review(
    *,
    repository: AlphaRepository,
    candidate: CandidateCard,
    recall_hits: Sequence[RecallHit],
    compilation_request: RuleCompilationRequest,
    artifact_root: Path,
    packet_locator: str,
    manifest_locator: str,
    gate_evaluated_at: datetime,
    packet_created_at: datetime,
    handoff_created_at: datetime,
    evidence: Sequence[ClaimEvidence] = (),
    compiler: RuleContractCompiler | None = None,
) -> BlindReviewStage:
    """Compile Rule A, freeze Blind packet and stop at the outbox boundary."""

    hits = tuple(sorted(recall_hits, key=lambda item: item.record_id))
    if candidate.state != CandidateState.CANDIDATE_MERGED:
        raise ReviewPipelineBlocked("Blind review must start from CANDIDATE_MERGED")
    if {item.record_id for item in hits} != set(candidate.recall_hit_ids):
        raise ReviewPipelineBlocked("Blind review requires exact Candidate RecallHit lineage")
    _require_sealed(repository, candidate, label="Candidate")
    for hit in hits:
        _require_sealed(repository, hit, label="RecallHit")
    if compilation_request.market_id != candidate.market_id:
        raise ReviewPipelineBlocked("rule compilation market does not match Candidate")
    snapshot_payload = repository.get_contract(compilation_request.market_snapshot_id)
    if snapshot_payload is None:
        raise ReviewPipelineBlocked("rule compilation snapshot is not sealed in the Alpha repository")
    try:
        snapshot = MarketSnapshot.model_validate(snapshot_payload)
    except ValueError as error:
        raise ReviewPipelineBlocked("rule compilation snapshot id is not a MarketSnapshot") from error
    if (
        snapshot.identity.market_id != candidate.market_id
        or snapshot.rule_hash != compilation_request.expected_rule_hash
    ):
        raise ReviewPipelineBlocked("rule compilation snapshot market/rule binding mismatch")
    compilation = (compiler or RuleContractCompiler()).compile(compilation_request)
    if compilation.contract is None:
        repository.save_contract(compilation.receipt)
        raise ReviewPipelineBlocked("RuleContract compilation was blocked")
    contract = compilation.contract
    gate_a = evaluate_gate_a(
        contract,
        compilation.receipt,
        run_id=stable_record_id("gate_a_run", candidate.candidate_id),
        evaluated_at=gate_evaluated_at,
    )
    if gate_a.decision != "PASS":
        repository.save_contracts_atomic((contract, compilation.receipt, gate_a))
        raise ReviewPipelineBlocked("Gate A did not pass")
    blind = build_blind_research_packet(
        candidate=candidate,
        recall_hits=hits,
        contract=contract,
        gate_a=gate_a,
        evidence=evidence,
        build_run_identity=stable_record_id("blind_build", candidate.candidate_id),
        created_at=packet_created_at,
    )
    first = _transition(
        candidate,
        CandidateState.CANDIDATE_MERGED,
        CandidateState.RULE_A_PASSED,
        at=gate_evaluated_at,
        artifact_id=gate_a.record_id,
        reason="Rule A passed",
    )
    second = _transition(
        candidate,
        CandidateState.RULE_A_PASSED,
        CandidateState.BLIND_PROJECTION_FROZEN,
        at=_next_at((first,), packet_created_at),
        artifact_id=blind.projection.record_id,
        reason="Blind projection frozen",
    )
    third = _transition(
        candidate,
        CandidateState.BLIND_PROJECTION_FROZEN,
        CandidateState.BLIND_PACKET_FROZEN,
        at=_next_at((first, second), packet_created_at),
        artifact_id=blind.packet.record_id,
        reason="Blind packet exported",
    )
    transitions = (first, second, third)
    reduce_candidate_lifecycle(candidate, transitions)
    manifest = export_research_packet(
        artifact_root=artifact_root,
        packet=blind.packet,
        packet_locator=packet_locator,
        manifest_locator=manifest_locator,
        created_at=handoff_created_at,
    )
    repository.save_contracts_atomic(
        (contract, compilation.receipt, gate_a, blind.projection, blind.packet, *transitions)
    )
    return BlindReviewStage(
        candidate,
        hits,
        contract,
        compilation.receipt,
        gate_a,
        blind.packet,
        manifest,
        transitions,
    )


def resume_blind_result(
    *,
    repository: AlphaRepository,
    stage: BlindReviewStage,
    artifact_root: Path,
    result_locator: str,
    receipt_locator: str,
    source_contents: Mapping[str, bytes],
    imported_at: datetime,
    identity: MarketIdentity,
    demand_requested_at: datetime,
    demand_valid_until: datetime,
    max_staleness_seconds: int,
    target_sizes: Sequence[Decimal],
) -> BlindResumeOutcome:
    """Import Blind result; accepted results alone may create formal demand."""

    _require_sealed(repository, stage.blind_packet, label="Blind packet")
    _require_sealed(repository, stage.contract, label="RuleContract")
    _require_sealed(repository, stage.gate_a, label="Rule A decision")
    imported, handoff = ingest_research_result(
        artifact_root=artifact_root,
        packet=stage.blind_packet,
        packet_manifest=stage.packet_manifest,
        allowed_result_locator=result_locator,
        source_contents=source_contents,
        imported_at=imported_at,
        run_id=stable_record_id("blind_import_run", stage.candidate.candidate_id),
    )
    seal_result_handoff(
        artifact_root=artifact_root,
        receipt=handoff,
        receipt_locator=receipt_locator,
    )
    persisted = [imported.submitted_artifact]
    if imported.result is not None:
        persisted.append(imported.result)
    persisted.append(imported.receipt)
    repository.save_contracts_atomic(tuple(persisted))
    if imported.result is None:
        return BlindResumeOutcome(imported, handoff, None)
    accepted_transition = _transition(
        stage.candidate,
        CandidateState.BLIND_PACKET_FROZEN,
        CandidateState.BLIND_RESULT_ACCEPTED,
        at=_next_at(stage.transitions, imported.receipt.imported_at),
        artifact_id=imported.receipt.record_id,
        reason="Blind result accepted",
    )
    transitions = (*stage.transitions, accepted_transition)
    reduce_candidate_lifecycle(stage.candidate, transitions)
    demand = build_market_formal_review_demand(
        candidate=stage.candidate,
        contract=stage.contract,
        gate_a=stage.gate_a,
        blind_packet=stage.blind_packet,
        blind_result=imported.result,
        blind_import_receipt=imported.receipt,
        identity=identity,
        requested_at=demand_requested_at,
        valid_until=demand_valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=target_sizes,
        run_id=stable_record_id("formal_demand_run", stage.candidate.candidate_id),
    )
    repository.save_contracts_atomic((accepted_transition, demand))
    accepted = BlindAcceptedStage(
        stage,
        imported.result,
        imported.receipt,
        handoff,
        demand,
        transitions,
    )
    return BlindResumeOutcome(imported, handoff, accepted)


def accept_formal_book(
    *,
    repository: AlphaRepository,
    stage: BlindAcceptedStage,
    yes_artifact: FrozenOwnerBookArtifact | None,
    no_artifact: FrozenOwnerBookArtifact | None,
    received_at: datetime,
    artifact_root: Path,
    packet_locator: str,
    manifest_locator: str,
    packet_created_at: datetime,
    handoff_created_at: datetime,
) -> MarketBuildOutcome:
    """Normalize existing-owner artifacts and export Market packet if usable."""

    _require_sealed(repository, stage.demand, label="formal book demand")
    _require_sealed(repository, stage.result, label="Blind result")
    normalized = normalize_paired_owner_books(
        demand=stage.demand,
        yes_artifact=yes_artifact,
        no_artifact=no_artifact,
        received_at=received_at,
    )
    if normalized.snapshot is None:
        repository.save_contract(normalized.receipt)
        return MarketBuildOutcome(normalized, None)
    accepted_book = _transition(
        stage.blind.candidate,
        CandidateState.BLIND_RESULT_ACCEPTED,
        CandidateState.BOOK_SNAPSHOT_ACCEPTED,
        at=_next_at(stage.transitions, normalized.receipt.received_at),
        artifact_id=normalized.receipt.record_id,
        reason="fresh paired owner book accepted",
    )
    market_packet = freeze_market_research_packet(
        candidate=stage.blind.candidate,
        contract=stage.blind.contract,
        gate_a=stage.blind.gate_a,
        blind_packet=stage.blind.blind_packet,
        blind_result=stage.result,
        blind_import_receipt=stage.import_receipt,
        demand=stage.demand,
        book_receipt=normalized.receipt,
        snapshot=normalized.snapshot,
        build_run_identity=stable_record_id("market_build", stage.blind.candidate.candidate_id),
        created_at=packet_created_at,
    )
    packet_transition = _transition(
        stage.blind.candidate,
        CandidateState.BOOK_SNAPSHOT_ACCEPTED,
        CandidateState.MARKET_PACKET_FROZEN,
        at=_next_at((*stage.transitions, accepted_book), packet_created_at),
        artifact_id=market_packet.record_id,
        reason="Market packet exported",
    )
    transitions = (*stage.transitions, accepted_book, packet_transition)
    reduce_candidate_lifecycle(stage.blind.candidate, transitions)
    manifest = export_research_packet(
        artifact_root=artifact_root,
        packet=market_packet,
        packet_locator=packet_locator,
        manifest_locator=manifest_locator,
        created_at=handoff_created_at,
    )
    repository.save_contracts_atomic(
        (normalized.snapshot, normalized.receipt, accepted_book, market_packet, packet_transition)
    )
    accepted = MarketReviewStage(stage, normalized, market_packet, manifest, transitions)
    return MarketBuildOutcome(normalized, accepted)


def resume_market_result(
    *,
    repository: AlphaRepository,
    stage: MarketReviewStage,
    artifact_root: Path,
    result_locator: str,
    receipt_locator: str,
    source_contents: Mapping[str, bytes],
    imported_at: datetime,
    gate_b_evaluated_at: datetime,
    decision_as_of: datetime,
    rank_config: RankConfig,
    rule_risk_reasons: Sequence[str] = (),
) -> MarketResumeOutcome:
    """Import Market result, enforce Rule B, and persist only no-order ledger."""

    _require_sealed(repository, stage.market_packet, label="Market packet")
    _require_sealed(repository, stage.blind.result, label="Blind result")
    imported, handoff = ingest_research_result(
        artifact_root=artifact_root,
        packet=stage.market_packet,
        packet_manifest=stage.packet_manifest,
        allowed_result_locator=result_locator,
        source_contents=source_contents,
        imported_at=imported_at,
        run_id=stable_record_id("market_import_run", stage.blind.blind.candidate.candidate_id),
    )
    seal_result_handoff(
        artifact_root=artifact_root,
        receipt=handoff,
        receipt_locator=receipt_locator,
    )
    persisted = [imported.submitted_artifact]
    if imported.result is not None:
        persisted.append(imported.result)
    persisted.append(imported.receipt)
    repository.save_contracts_atomic(tuple(persisted))
    if imported.result is None:
        return MarketResumeOutcome(imported, handoff, None)

    candidate = stage.blind.blind.candidate
    market_result_transition = _transition(
        candidate,
        CandidateState.MARKET_PACKET_FROZEN,
        CandidateState.MARKET_RESULT_ACCEPTED,
        at=_next_at(stage.transitions, imported.receipt.imported_at),
        artifact_id=imported.receipt.record_id,
        reason="Market result accepted",
    )
    gate_b = evaluate_gate_b(
        stage.blind.blind.contract,
        stage.blind.blind.gate_a,
        market_packet_id=stage.market_packet.record_id,
        market_packet_rule_hash=stage.market_packet.rule_contract.rule_hash,
        market_packet_contract_revision_id=stage.market_packet.rule_contract.contract_revision_id,
        run_id=stable_record_id("gate_b_run", candidate.candidate_id),
        evaluated_at=gate_b_evaluated_at,
        rule_risk_reasons=rule_risk_reasons,
    )
    target_state = {
        "PASS": CandidateState.RULE_B_PASSED,
        "PASS_WITH_RULE_RISK": CandidateState.RULE_B_RISK,
        "BLOCK": CandidateState.RULE_B_BLOCKED,
    }[gate_b.decision]
    gate_transition = _transition(
        candidate,
        CandidateState.MARKET_RESULT_ACCEPTED,
        target_state,
        at=_next_at((*stage.transitions, market_result_transition), gate_b.evaluated_at),
        artifact_id=gate_b.record_id,
        reason=f"Rule B {gate_b.decision}",
    )
    transitions = (*stage.transitions, market_result_transition, gate_transition)
    lifecycle = reduce_candidate_lifecycle(candidate, transitions)
    if target_state == CandidateState.RULE_B_BLOCKED:
        repository.save_contracts_atomic((market_result_transition, gate_b, gate_transition))
        final = FinalReviewStage(
            stage,
            imported.result,
            imported.receipt,
            handoff,
            gate_b,
            None,
            lifecycle,
            transitions,
        )
        return MarketResumeOutcome(imported, handoff, final)

    rankable = candidate.model_copy(update={"state": target_state})
    ranked = build_ranked_ledger(
        candidate=rankable,
        contract=stage.blind.blind.contract,
        gate_a=stage.blind.blind.gate_a,
        gate_b=gate_b,
        blind_result=stage.blind.result,
        blind_receipt=stage.blind.import_receipt,
        market_packet=stage.market_packet,
        market_result=imported.result,
        market_receipt=imported.receipt,
        book=stage.normalization.snapshot,
        config=rank_config,
        as_of=decision_as_of,
        run_id=stable_record_id("decision_run", candidate.candidate_id),
    )
    if ranked.decision.execution != "NO_ORDER":
        raise ReviewPipelineBlocked("unified offline pipeline emitted execution capability")
    ranked_transition = _transition(
        candidate,
        target_state,
        CandidateState.RANKED,
        at=_next_at(transitions, decision_as_of),
        artifact_id=ranked.decision.record_id,
        reason="candidate ranked without execution",
    )
    final_state = (
        CandidateState.SIMULATION_RECORDED
        if ranked.prediction.position_state == PositionState.SIMULATED
        else CandidateState.WATCHLISTED
    )
    final_transition = _transition(
        candidate,
        CandidateState.RANKED,
        final_state,
        at=_next_at((*transitions, ranked_transition), decision_as_of),
        artifact_id=ranked.prediction.record_id,
        reason="no-order prediction recorded",
    )
    transitions = (*transitions, ranked_transition, final_transition)
    lifecycle = reduce_candidate_lifecycle(candidate, transitions)
    repository.save_contracts_atomic(
        (
            market_result_transition,
            gate_b,
            gate_transition,
            ranked.decision,
            ranked.prediction,
            ranked_transition,
            final_transition,
        )
    )
    final = FinalReviewStage(
        stage,
        imported.result,
        imported.receipt,
        handoff,
        gate_b,
        ranked,
        lifecycle,
        transitions,
    )
    return MarketResumeOutcome(imported, handoff, final)
