"""Pure resolution binding for the append-only Alpha P1 learning ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..contracts import (
    CaptureScope,
    DecisionDirection,
    EstimateStage,
    MarketResolution,
    OrderbookSnapshot,
    PositionState,
    PredictionRecord,
    PredictionResolutionLink,
    ProbabilityEstimate,
    ResolutionAdjudicationStatus,
    ResolutionOutcome,
    ReviewAction,
    ReviewDecision,
    RuleContract,
    ScoringEligibility,
    SimulationEntryBasis,
    SourceArtifact,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc


LEARNING_RESOLUTION_VERSION = "p1_learning_resolution_v1"


class LearningResolutionError(ValueError):
    """Raised when immutable P1 learning inputs cannot be safely joined."""


def _frozen(value, cls):
    """Re-validate every object: Pydantic ``model_copy`` skips validators."""

    if not isinstance(value, cls):
        raise LearningResolutionError(f"expected {cls.__name__}")
    try:
        rebuilt = cls.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise LearningResolutionError(f"invalid frozen {cls.__name__}: {exc}") from exc
    if rebuilt.canonical_sha256 != value.canonical_sha256:
        raise LearningResolutionError(f"{cls.__name__} canonical replay mismatch")
    return rebuilt


def build_market_resolution(
    *,
    source_artifact: SourceArtifact,
    rule_contract: RuleContract,
    market_id: str,
    outcome: ResolutionOutcome,
    adjudication_status: ResolutionAdjudicationStatus,
    resolved_at: datetime,
    source_observed_at: datetime,
    created_at: datetime,
    run_id: str,
    parser_version: str,
    condition_id: str | None = None,
    supersedes: MarketResolution | None = None,
) -> MarketResolution:
    """Create a replayable source-backed settlement assertion without fetching anything."""

    artifact = _frozen(source_artifact, SourceArtifact)
    contract = _frozen(rule_contract, RuleContract)
    market_id = market_id.strip()
    parser_version = parser_version.strip()
    if not market_id or market_id != contract.market_id or not parser_version:
        raise LearningResolutionError("market and parser must bind to the RuleContract")
    if artifact.capture_scope == CaptureScope.REFERENCE_ONLY or artifact.content_sha256 is None:
        raise LearningResolutionError("resolution requires captured, content-hashed source evidence")
    resolved_at, source_observed_at, created_at = map(ensure_utc, (resolved_at, source_observed_at, created_at))
    if not (contract.created_at <= resolved_at <= source_observed_at <= created_at):
        raise LearningResolutionError("RuleContract/resolution/source/creation clocks are not ordered")
    if artifact.effective_as_of > resolved_at or artifact.captured_at > source_observed_at:
        raise LearningResolutionError("source artifact lies after the resolution observation")
    supersedes_id = supersedes_hash = None
    if supersedes is not None:
        prior = _frozen(supersedes, MarketResolution)
        if prior.market_id != market_id or prior.rule_hash != contract.rule_hash:
            raise LearningResolutionError("superseded resolution market/rule mismatch")
        supersedes_id, supersedes_hash = prior.record_id, prior.canonical_sha256
    resolution_id = stable_record_id(
        "market_resolution", market_id, contract.record_id, contract.canonical_sha256,
        artifact.record_id, artifact.canonical_sha256, artifact.content_sha256, outcome.value,
        adjudication_status.value, resolved_at, source_observed_at, parser_version, condition_id,
        supersedes_id, supersedes_hash,
    )
    return MarketResolution(
        record_id=resolution_id, resolution_id=resolution_id, run_id=run_id, created_at=created_at,
        source="alpha_p1_resolution", source_version=LEARNING_RESOLUTION_VERSION,
        provenance=(), extensions={}, market_id=market_id, condition_id=condition_id,
        outcome=outcome, adjudication_status=adjudication_status, resolved_at=resolved_at,
        source_observed_at=source_observed_at, source_artifact_id=artifact.record_id,
        source_artifact_sha256=artifact.canonical_sha256, rule_contract_id=contract.record_id,
        rule_contract_sha256=contract.canonical_sha256, rule_hash=contract.rule_hash,
        contract_revision_id=contract.contract_revision_id, parser_version=parser_version,
        supersedes_resolution_id=supersedes_id, supersedes_resolution_sha256=supersedes_hash,
    )


def _entry_basis(
    *, prediction: PredictionRecord, decision: ReviewDecision, book: OrderbookSnapshot,
    fee_amount: Decimal, fee_model_version: str,
) -> SimulationEntryBasis | None:
    if prediction.position_state == PositionState.NO_POSITION:
        return None
    if (
        decision.action != ReviewAction.SIMULATE
        or decision.direction not in {DecisionDirection.YES, DecisionDirection.NO}
        or decision.target_size is None
    ):
        raise LearningResolutionError("SIMULATED prediction requires a directional SIMULATE decision")
    if book.stale or book.quality_flags:
        raise LearningResolutionError("simulation cannot use stale or flagged orderbook")
    target = decision.target_size
    depth = book.yes_depth if decision.direction == DecisionDirection.YES else book.no_depth
    matches = tuple(item for item in depth if item.target_size == target)
    if len(matches) != 1 or matches[0].buy_insufficient_depth or matches[0].buy_vwap is None:
        raise LearningResolutionError("orderbook lacks sufficient directional buy depth at target size")
    if fee_amount < 0 or not fee_model_version.strip():
        raise LearningResolutionError("simulation fee must be non-negative and versioned")
    direction = decision.direction.value
    token_id = book.yes_leg.token_id if direction == "YES" else book.no_leg.token_id
    return SimulationEntryBasis(
        direction=direction, token_id=token_id, quantity=target, entry_vwap=matches[0].buy_vwap,
        gross_cost=target * matches[0].buy_vwap, fee_amount=fee_amount,
        fee_model_version=fee_model_version, orderbook_snapshot_id=book.record_id,
        orderbook_snapshot_sha256=book.canonical_sha256,
    )


def build_prediction_resolution_link(
    *,
    prediction: PredictionRecord,
    decision: ReviewDecision,
    probability_estimate: ProbabilityEstimate,
    rule_contract: RuleContract,
    resolution: MarketResolution,
    orderbook: OrderbookSnapshot,
    market_type: str,
    linked_at: datetime,
    run_id: str,
    fee_amount: Decimal = Decimal("0"),
    fee_model_version: str = "no_fee_v1",
) -> PredictionResolutionLink:
    """Bind exactly one frozen P0 prediction to a P1 settlement assertion."""

    prediction, decision, estimate, contract, resolved, book = (
        _frozen(prediction, PredictionRecord), _frozen(decision, ReviewDecision),
        _frozen(probability_estimate, ProbabilityEstimate), _frozen(rule_contract, RuleContract),
        _frozen(resolution, MarketResolution), _frozen(orderbook, OrderbookSnapshot),
    )
    linked_at = ensure_utc(linked_at)
    market_type = market_type.strip()
    if not market_type:
        raise LearningResolutionError("market_type must not be blank")
    if prediction.final_resolution is not None or prediction.resolved_at is not None or prediction.simulated_pnl is not None:
        raise LearningResolutionError("P1 must not link a legacy-mutated PredictionRecord")
    if (
        prediction.market_id != contract.market_id
        or decision.market_id != prediction.market_id
        or estimate.market_id != prediction.market_id
        or resolved.market_id != prediction.market_id
        or book.identity.market_id != prediction.market_id
    ):
        raise LearningResolutionError("prediction lineage market mismatch")
    if resolved.condition_id != book.identity.condition_id:
        raise LearningResolutionError("resolution canonical condition identity mismatch")
    if prediction.rule_hash != contract.rule_hash or decision.rule_hash != contract.rule_hash or resolved.rule_hash != contract.rule_hash:
        raise LearningResolutionError("prediction lineage rule hash mismatch")
    if (
        resolved.rule_contract_id != contract.record_id
        or resolved.rule_contract_sha256 != contract.canonical_sha256
        or resolved.contract_revision_id != contract.contract_revision_id
    ):
        raise LearningResolutionError("resolution RuleContract id/hash/revision mismatch")
    if prediction.decision_id != decision.record_id or prediction.probability_estimate_id != estimate.record_id:
        raise LearningResolutionError("prediction decision or estimate id mismatch")
    if prediction.orderbook_snapshot_id != book.record_id:
        raise LearningResolutionError("prediction frozen orderbook id mismatch")
    if estimate.estimate_stage != EstimateStage.FINAL:
        raise LearningResolutionError("only FINAL probability estimates can enter P1 learning")
    if decision.execution != "NO_ORDER":
        raise LearningResolutionError("P1 accepts only explicit NO_ORDER decisions")
    expected_position = (
        PositionState.SIMULATED
        if decision.action == ReviewAction.SIMULATE
        else PositionState.NO_POSITION
    )
    if prediction.position_state != expected_position:
        raise LearningResolutionError("prediction position state contradicts frozen decision")
    if not (prediction.created_at <= linked_at and resolved.resolved_at <= linked_at):
        raise LearningResolutionError("prediction/resolution must precede link")
    if resolved.adjudication_status == ResolutionAdjudicationStatus.PENDING_DISPUTE:
        eligibility, reason = ScoringEligibility.EXCLUDED_PENDING_DISPUTE, "PENDING_DISPUTE"
    elif resolved.outcome == ResolutionOutcome.INVALID:
        eligibility, reason = ScoringEligibility.EXCLUDED_INVALID, "INVALID"
    elif resolved.adjudication_status == ResolutionAdjudicationStatus.FINAL:
        eligibility, reason = ScoringEligibility.ELIGIBLE, None
    else:
        raise LearningResolutionError("unknown resolution eligibility")
    basis = _entry_basis(prediction=prediction, decision=decision, book=book,
                         fee_amount=fee_amount, fee_model_version=fee_model_version)
    link_id = stable_record_id(
        "prediction_resolution_link", prediction.record_id, prediction.canonical_sha256,
        decision.record_id, decision.canonical_sha256, estimate.record_id, estimate.canonical_sha256,
        contract.record_id, contract.canonical_sha256, resolved.record_id, resolved.canonical_sha256,
        book.record_id, book.canonical_sha256, market_type, linked_at,
        basis.model_dump(mode="python") if basis else None, eligibility.value,
    )
    return PredictionResolutionLink(
        record_id=link_id, link_id=link_id, run_id=run_id, created_at=linked_at,
        source="alpha_p1_learning_linker", source_version=LEARNING_RESOLUTION_VERSION,
        provenance=(), extensions={}, market_id=prediction.market_id, prediction_id=prediction.record_id,
        prediction_sha256=prediction.canonical_sha256, decision_id=decision.record_id,
        decision_sha256=decision.canonical_sha256, probability_estimate_id=estimate.record_id,
        probability_estimate_sha256=estimate.canonical_sha256, rule_contract_id=contract.record_id,
        rule_contract_sha256=contract.canonical_sha256, resolution_id=resolved.record_id,
        resolution_sha256=resolved.canonical_sha256, linked_at=linked_at,
        scoring_eligibility=eligibility, exclusion_reason=reason,
        predicted_probability=estimate.p_event_yes_mid,
        market_baseline_probability=estimate.p_market_yes_mid, market_type=market_type,
        rule_clarity=contract.clarity_score, entry_basis=basis,
    )
