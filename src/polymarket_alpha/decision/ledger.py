"""Fail-closed, deterministic P0 decision ranking and ledger construction.

This is deliberately an offline domain service: all inputs are already frozen
contracts, it opens no transport, and it can only yield ``NO_ORDER`` records.
Repository writes use its public idempotent atomic-group boundary, so a
ReviewDecision and its PredictionRecord either project together or not at all.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import (
    CandidateCard,
    CandidateState,
    DecisionDirection,
    EstimateStage,
    MarketComparisonV2,
    MarketComparisonStatus,
    MarketResearchPacket,
    OrderbookSnapshot,
    PacketStage,
    PositionState,
    PredictionRecord,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    ReviewAction,
    ReviewDecision,
    RuleContract,
    RuleGate,
    RuleGateB,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc
from ..books.executable_cost import EXECUTABLE_COST_VERSION, paired_buy_cost
from ..rules.models import RuleGateDecision, RuleGateStage
from ..storage import AlphaRepository


RANKER_VERSION = "p0_09b_v2"
_ONE = Decimal("1")
_ZERO = Decimal("0")


class DecisionLedgerError(ValueError):
    """Raised before any write when a frozen input cannot safely bind."""


class RankConfig(BaseModel):
    """Pinned Decimal-only score policy; changing it requires a new version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    recall_weight: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    evidence_weight: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    rule_weight: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    edge_weight: Decimal = Field(default=Decimal("0.35"), ge=0, le=1)
    recall_scale: Decimal = Field(default=Decimal("100"), gt=0)
    watch_threshold: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    simulate_threshold: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)
    max_book_age_seconds: int = Field(default=300, gt=0)
    max_result_age_seconds: int = Field(default=86_400, gt=0)
    simulation_target_size: Decimal = Field(default=Decimal("10"), gt=0)
    fee_slippage_cost_policy_id: str
    fee_slippage_cost_policy_version: str
    fee_rate: Decimal = Field(ge=0, le=1)
    slippage_buffer: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_and_thresholds_are_complete(self) -> "RankConfig":
        if not self.version.strip():
            raise ValueError("rank config version must not be blank")
        if self.recall_weight + self.evidence_weight + self.rule_weight + self.edge_weight != _ONE:
            raise ValueError("rank weights must sum exactly to 1")
        if self.watch_threshold > self.simulate_threshold:
            raise ValueError("watch threshold cannot exceed simulate threshold")
        if not self.fee_slippage_cost_policy_id.strip() or not self.fee_slippage_cost_policy_version.strip():
            raise ValueError("cost policy id/version must not be blank")
        return self

    @property
    def config_sha256(self) -> str:
        return content_sha256(self.model_dump(mode="python"))


class RankOutcome(NamedTuple):
    score: Decimal
    score_breakdown: dict[str, Decimal]
    decision: ReviewDecision
    prediction: PredictionRecord


class LedgerWriteResult(NamedTuple):
    decision_sha256: str
    prediction_sha256: str
    cross_record_atomic: bool = True


def _accepted(receipt: ResearchImportReceipt, result: ResearchResultEnvelope) -> None:
    if receipt.status != ResearchImportStatus.ACCEPTED or receipt.reasons != (ResearchImportReason.ACCEPTED,):
        raise DecisionLedgerError("research import receipt is not accepted")
    if receipt.packet_stage != result.packet_stage or receipt.packet_id != result.packet_id:
        raise DecisionLedgerError("research receipt packet binding mismatch")
    if receipt.packet_sha256 != result.packet_sha256:
        raise DecisionLedgerError("research receipt packet hash mismatch")
    if receipt.accepted_result_id != result.result_id or receipt.accepted_result_sha256 != result.canonical_sha256:
        raise DecisionLedgerError("research receipt result binding mismatch")


def _fresh(at: datetime, as_of: datetime, seconds: int, label: str) -> None:
    if at > as_of or (as_of - at).total_seconds() >= seconds:
        raise DecisionLedgerError(f"{label} is stale or lies after as_of")


def _book_prices(
    book: OrderbookSnapshot, *, config: RankConfig
) -> tuple[Decimal, Decimal]:
    if book.stale or book.quality_flags:
        raise DecisionLedgerError("orderbook is stale or has quality flags")
    target_size = config.simulation_target_size
    yes = tuple(item for item in book.yes_depth if item.target_size == target_size)
    no = tuple(item for item in book.no_depth if item.target_size == target_size)
    if len(yes) != 1 or len(no) != 1:
        raise DecisionLedgerError("orderbook lacks the configured paired target depth")
    if (
        yes[0].buy_insufficient_depth
        or no[0].buy_insufficient_depth
        or yes[0].buy_vwap is None
        or no[0].buy_vwap is None
    ):
        raise DecisionLedgerError("orderbook has insufficient target depth")
    costs = paired_buy_cost(
        book,
        target_size=target_size,
        fee_rate=config.fee_rate,
        slippage_buffer=config.slippage_buffer,
    )
    if not costs.fully_executable:
        raise DecisionLedgerError("orderbook has insufficient sealed fill-level depth")
    if costs.yes.vwap != yes[0].buy_vwap or costs.no.vwap != no[0].buy_vwap:
        raise DecisionLedgerError(
            "orderbook target-depth VWAP disagrees with sealed levels"
        )
    assert costs.yes.effective_price is not None
    assert costs.no.effective_price is not None
    return costs.yes.effective_price, costs.no.effective_price


def _bind_market_comparison(
    *,
    comparison: MarketComparisonV2 | None,
    blind_result: ResearchResultEnvelope,
    market_result: ResearchResultEnvelope,
    contract: RuleContract,
    book: OrderbookSnapshot,
    config: RankConfig,
) -> None:
    comparison_id = market_result.extensions.get("market_comparison_id")
    comparison_sha256 = market_result.extensions.get("market_comparison_sha256")
    deterministic = market_result.extensions.get("probability_update") == "NONE"
    if deterministic and comparison is None:
        raise DecisionLedgerError(
            "deterministic Market result requires its sealed MarketComparison"
        )
    if comparison is None:
        if comparison_id is not None or comparison_sha256 is not None:
            raise DecisionLedgerError("market comparison binding is incomplete")
        return
    if not isinstance(comparison, MarketComparisonV2):
        raise DecisionLedgerError("fee-aware MarketComparisonV2 is required")
    if comparison.status is not MarketComparisonStatus.READY:
        raise DecisionLedgerError("market comparison is not executable")
    if (comparison_id, comparison_sha256) != (
        comparison.record_id,
        comparison.comparison_sha256,
    ):
        raise DecisionLedgerError("market result comparison id/hash mismatch")
    if (
        comparison.accepted_blind_result_id != blind_result.result_id
        or comparison.accepted_blind_result_sha256 != blind_result.canonical_sha256
        or comparison.rule_contract_id != contract.record_id
        or comparison.rule_contract_sha256 != contract.canonical_sha256
        or comparison.rule_hash != contract.rule_hash
        or comparison.orderbook_snapshot_id != book.record_id
        or comparison.orderbook_snapshot_sha256 != book.canonical_sha256
    ):
        raise DecisionLedgerError("market comparison input lineage mismatch")
    if (
        comparison.policy_size != config.simulation_target_size
        or comparison.fee_slippage_cost_policy_id
        != config.fee_slippage_cost_policy_id
        or comparison.fee_slippage_cost_policy_version
        != config.fee_slippage_cost_policy_version
        or comparison.fee_model_version != EXECUTABLE_COST_VERSION
        or comparison.fee_rate != config.fee_rate
        or comparison.slippage_buffer != config.slippage_buffer
    ):
        raise DecisionLedgerError("ranker and market comparison cost policies differ")
    sealed_cost = paired_buy_cost(
        book,
        target_size=config.simulation_target_size,
        fee_rate=config.fee_rate,
        slippage_buffer=config.slippage_buffer,
    )
    if not sealed_cost.fully_executable:
        raise DecisionLedgerError("sealed orderbook cannot execute comparison target")
    if (
        sealed_cost.yes.fills != comparison.yes_buy_fills
        or sealed_cost.no.fills != comparison.no_buy_fills
        or sealed_cost.yes.vwap != comparison.yes_buy_vwap
        or sealed_cost.no.vwap != comparison.no_buy_vwap
        or sealed_cost.yes.taker_fee != comparison.yes_taker_fee
        or sealed_cost.no.taker_fee != comparison.no_taker_fee
        or sealed_cost.yes.effective_price != comparison.yes_all_in_buy_price
        or sealed_cost.no.effective_price != comparison.no_all_in_buy_price
    ):
        raise DecisionLedgerError(
            "market comparison fills/costs do not match sealed orderbook"
        )


def _research_temporally_closed(
    result: ResearchResultEnvelope,
    receipt: ResearchImportReceipt,
    as_of: datetime,
    label: str,
) -> None:
    if not (
        result.completed_at <= receipt.imported_at <= as_of
        and all(item.captured_at <= result.completed_at for item in result.source_artifacts)
        and all(item.accessed_at <= result.completed_at for item in result.evidence)
    ):
        raise DecisionLedgerError(f"{label} result/import/source chronology is invalid")


def _bind(
    *, candidate: CandidateCard, contract: RuleContract, gate_a: RuleGateDecision,
    gate_b: RuleGateDecision, blind_result: ResearchResultEnvelope,
    blind_receipt: ResearchImportReceipt, market_packet: MarketResearchPacket,
    market_result: ResearchResultEnvelope, market_receipt: ResearchImportReceipt,
    book: OrderbookSnapshot, market_comparison: MarketComparisonV2 | None,
    config: RankConfig, as_of: datetime,
) -> None:
    if candidate.state not in {CandidateState.RULE_B_PASSED, CandidateState.RULE_B_RISK}:
        raise DecisionLedgerError("candidate is not at a rankable Rule B state")
    if candidate.market_id != contract.market_id or candidate.market_id != market_packet.market_id:
        raise DecisionLedgerError("candidate, RuleContract, and market packet market mismatch")
    if market_packet.candidate_id != candidate.candidate_id or market_packet.orderbook.record_id != book.record_id:
        raise DecisionLedgerError("market packet candidate or book binding mismatch")
    if (
        market_packet.orderbook.canonical_sha256 != book.canonical_sha256
        or book.identity.market_id != candidate.market_id
    ):
        raise DecisionLedgerError("market packet orderbook content/market binding mismatch")
    if market_packet.rule_contract.record_id != contract.record_id or market_packet.rule_contract.canonical_sha256 != contract.canonical_sha256:
        raise DecisionLedgerError("market packet RuleContract binding mismatch")
    if market_packet.blind_result_id != blind_result.result_id:
        raise DecisionLedgerError("market packet Blind result binding mismatch")
    if tuple(item.canonical_sha256 for item in market_packet.blind_evidence) != tuple(
        item.canonical_sha256 for item in blind_result.evidence
    ):
        raise DecisionLedgerError("market packet Blind evidence binding mismatch")
    _bind_market_comparison(
        comparison=market_comparison,
        blind_result=blind_result,
        market_result=market_result,
        contract=contract,
        book=book,
        config=config,
    )
    if gate_a.stage != RuleGateStage.A or gate_a.decision != "PASS":
        raise DecisionLedgerError("Gate A must be PASS")
    if gate_b.stage != RuleGateStage.B or gate_b.decision not in {"PASS", "PASS_WITH_RULE_RISK"}:
        raise DecisionLedgerError("Gate B must be rankable")
    for gate in (gate_a, gate_b):
        if (gate.market_id, gate.rule_contract_id, gate.rule_hash, gate.contract_revision_id) != (
            contract.market_id, contract.record_id, contract.rule_hash, contract.contract_revision_id
        ):
            raise DecisionLedgerError("rule gate binding/hash mismatch")
    if gate_b.gate_a_decision_id != gate_a.record_id:
        raise DecisionLedgerError("Gate B is not bound to Gate A")
    if contract.rule_gate != RuleGate.PASS:
        raise DecisionLedgerError("current RuleContract is not rankable")
    _accepted(blind_receipt, blind_result)
    _accepted(market_receipt, market_result)
    if blind_result.packet_stage != PacketStage.BLIND or blind_result.packet_id != market_packet.blind_packet_id:
        raise DecisionLedgerError("Blind result packet binding mismatch")
    if market_result.packet_stage != PacketStage.MARKET_AWARE or market_result.packet_id != market_packet.record_id:
        raise DecisionLedgerError("Market result packet binding mismatch")
    if market_result.packet_sha256 != market_packet.canonical_sha256:
        raise DecisionLedgerError("Market result packet hash mismatch")
    if market_result.probability_estimate.estimate_stage == EstimateStage.BLIND:
        raise DecisionLedgerError("market result contains a blind probability")
    if market_result.probability_estimate.market_id != candidate.market_id:
        raise DecisionLedgerError("market probability market binding mismatch")
    if (
        market_result.probability_estimate.blind_candidate_id
        != blind_result.probability_estimate.blind_candidate_id
    ):
        raise DecisionLedgerError("market probability Blind candidate binding mismatch")
    if (
        market_result.probability_estimate.p_event_yes_low,
        market_result.probability_estimate.p_event_yes_mid,
        market_result.probability_estimate.p_event_yes_high,
    ) != (
        blind_result.probability_estimate.p_event_yes_low,
        blind_result.probability_estimate.p_event_yes_mid,
        blind_result.probability_estimate.p_event_yes_high,
    ):
        raise DecisionLedgerError("market result cannot overwrite the accepted Blind probability")
    _fresh(book.source_observed_at, as_of, config.max_book_age_seconds, "orderbook")
    _fresh(blind_result.completed_at, as_of, config.max_result_age_seconds, "Blind result")
    _fresh(market_result.completed_at, as_of, config.max_result_age_seconds, "Market result")
    _research_temporally_closed(blind_result, blind_receipt, as_of, "Blind")
    _research_temporally_closed(market_result, market_receipt, as_of, "Market")
    if not (
        candidate.selected_at
        <= contract.created_at
        <= gate_a.evaluated_at
        <= blind_result.completed_at
        <= blind_receipt.imported_at
        <= book.source_observed_at
        <= book.captured_at
        <= market_packet.created_at
        <= market_result.completed_at
        <= market_receipt.imported_at
        <= gate_b.evaluated_at
        <= as_of
    ):
        raise DecisionLedgerError("research/book protocol chronology is not ordered")


def build_ranked_ledger(
    *, candidate: CandidateCard, contract: RuleContract, gate_a: RuleGateDecision,
    gate_b: RuleGateDecision, blind_result: ResearchResultEnvelope,
    blind_receipt: ResearchImportReceipt, market_packet: MarketResearchPacket,
    market_result: ResearchResultEnvelope, market_receipt: ResearchImportReceipt,
    book: OrderbookSnapshot, config: RankConfig, as_of: datetime, run_id: str,
    market_comparison: MarketComparisonV2 | None = None,
) -> RankOutcome:
    """Build deterministic, no-order decision facts from fully bound frozen inputs."""
    as_of = ensure_utc(as_of)
    if not run_id.strip():
        raise ValueError("run_id must not be blank")
    _bind(candidate=candidate, contract=contract, gate_a=gate_a, gate_b=gate_b,
          blind_result=blind_result, blind_receipt=blind_receipt, market_packet=market_packet,
          market_result=market_result, market_receipt=market_receipt, book=book,
          market_comparison=market_comparison, config=config, as_of=as_of)
    if market_comparison is None:
        yes_cost, no_cost = _book_prices(book, config=config)
    else:
        assert market_comparison.yes_all_in_buy_price is not None
        assert market_comparison.no_all_in_buy_price is not None
        yes_cost = market_comparison.yes_all_in_buy_price
        no_cost = market_comparison.no_all_in_buy_price
    estimate = market_result.probability_estimate
    yes_edge = estimate.p_event_yes_low - yes_cost
    no_edge = (_ONE - estimate.p_event_yes_high) - no_cost
    if yes_edge > no_edge:
        direction, net_edge, conservative_probability = DecisionDirection.YES, yes_edge, estimate.p_event_yes_low
    elif no_edge > yes_edge:
        direction, net_edge, conservative_probability = DecisionDirection.NO, no_edge, _ONE - estimate.p_event_yes_high
    else:
        direction, net_edge, conservative_probability = DecisionDirection.NONE, yes_edge, None
    recall_component = min(candidate.recall_score / config.recall_scale, _ONE)
    # A deterministic market comparison contributes execution state, not new
    # research evidence. Preserve the accepted Blind evidence quality instead
    # of awarding full credit to its synthetic neutral comparison receipt.
    scored_evidence = (
        blind_result.evidence
        if market_result.extensions.get("probability_update") == "NONE"
        else market_result.evidence
    )
    evidence_component = sum((item.confidence for item in scored_evidence), _ZERO) / Decimal(len(scored_evidence))
    edge_component = max(net_edge, _ZERO)
    breakdown = {
        "recall": recall_component * config.recall_weight,
        "evidence": evidence_component * config.evidence_weight,
        "rule": contract.clarity_score * config.rule_weight,
        "edge": edge_component * config.edge_weight,
    }
    score = sum(breakdown.values(), _ZERO)
    if direction == DecisionDirection.NONE or net_edge <= _ZERO:
        action = ReviewAction.REJECT_EDGE
    elif gate_b.decision == "PASS_WITH_RULE_RISK" or score < config.watch_threshold:
        action = ReviewAction.WATCH
    elif score >= config.simulate_threshold:
        action = ReviewAction.SIMULATE
    else:
        action = ReviewAction.WATCH
    input_ids = (candidate.record_id, contract.record_id, gate_a.record_id, gate_b.record_id,
                 blind_result.result_id, blind_receipt.record_id, book.record_id,
                 market_packet.record_id, market_result.result_id, market_receipt.record_id) + (
                     () if market_comparison is None else (market_comparison.record_id,)
                 )
    decision_id = stable_record_id("review_decision", candidate.candidate_id, contract.rule_hash,
                                   gate_b.record_id, market_result.result_id, book.record_id,
                                   config.config_sha256, as_of, run_id, action.value, direction.value)
    decision = ReviewDecision(
        record_id=decision_id, run_id=run_id, created_at=as_of, source="alpha_deterministic_ranker",
        source_version=f"{RANKER_VERSION}:{config.version}", provenance=(),
        extensions={
            "rank_config_sha256": config.config_sha256,
            "score": score,
            "score_breakdown": breakdown,
            "executable_cost": {
                "fee_model_version": EXECUTABLE_COST_VERSION,
                "policy_id": config.fee_slippage_cost_policy_id,
                "policy_version": config.fee_slippage_cost_policy_version,
                "fee_rate": config.fee_rate,
                "slippage_buffer": config.slippage_buffer,
                "yes_all_in_buy_price": yes_cost,
                "no_all_in_buy_price": no_cost,
                "market_comparison_id": None
                if market_comparison is None
                else market_comparison.record_id,
            },
        },
        market_id=candidate.market_id, rule_hash=contract.rule_hash,
        rule_gate_b=RuleGateB(gate_b.decision), action=action, direction=direction,
        conservative_probability=conservative_probability, net_edge=net_edge,
        target_size=config.simulation_target_size if action == ReviewAction.SIMULATE else None,
        execution="NO_ORDER",
        blocking_reasons=gate_b.reasons if gate_b.decision == "PASS_WITH_RULE_RISK" else (),
        input_artifact_ids=input_ids,
    )
    position = PositionState.SIMULATED if action == ReviewAction.SIMULATE else PositionState.NO_POSITION
    prediction_id = stable_record_id("prediction_record", candidate.market_id, decision_id,
                                     estimate.record_id, market_packet.record_id, book.record_id,
                                     config.config_sha256, as_of)
    prediction = PredictionRecord(
        record_id=prediction_id, run_id=run_id, created_at=as_of, source="alpha_decision_ledger",
        source_version=f"{RANKER_VERSION}:{config.version}", provenance=(),
        extensions={"rank_config_sha256": config.config_sha256, "rank_score": score},
        prediction_id=prediction_id, market_id=candidate.market_id, rule_hash=contract.rule_hash,
        packet_id=market_packet.record_id, probability_estimate_id=estimate.record_id,
        orderbook_snapshot_id=book.record_id, decision_id=decision.record_id, position_state=position,
        review_notes=(f"rank_config={config.version}",),
    )
    return RankOutcome(score, breakdown, decision, prediction)


def persist_ranked_ledger(repository: AlphaRepository, outcome: RankOutcome) -> LedgerWriteResult:
    """Atomically and idempotently persist a bound decision/prediction pair."""
    decision_sha256, prediction_sha256 = repository.save_contracts_atomic(
        (outcome.decision, outcome.prediction)
    )
    return LedgerWriteResult(decision_sha256, prediction_sha256, True)


def watchlist(decisions: Iterable[ReviewDecision]) -> tuple[ReviewDecision, ...]:
    """Pure, stable watchlist projection; repository query ownership remains storage."""
    return tuple(sorted((item for item in decisions if item.action == ReviewAction.WATCH), key=lambda item: item.record_id))
