"""Pure, offline Gate R WP5 Blind-versus-book comparison.

This module owns no capture, import repository, network, or order capability.
It only compiles immutable inputs into a comparison and (when executable) a
market-stage result for the already-existing importer.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import (
    BookCaptureReceipt, BookCaptureStatus, CaptureScope, ClaimEvidence,
    EstimateStage, EvidenceOrigin, EvidenceSupport, HashScope, MarketComparison,
    MarketComparisonStatus, MarketResearchPacket, ProbabilityEstimate,
    Replayability, ResearchResultEnvelope, SourceArtifact, SourceTier,
    content_sha256, stable_record_id,
)
from ..contracts.base import ensure_utc


MARKET_COMPARISON_COMPILER_VERSION = "gate_r_wp5_v1"
_ONE = Decimal("1")


class MarketComparisonError(ValueError):
    """Raised for identity/tamper failures before any comparison is emitted."""


class MarketComparisonPolicy(BaseModel):
    """Pinned Decimal-only cost and freshness policy for one comparison revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    version: str
    policy_size: Decimal = Field(gt=0)
    max_book_age_seconds: int = Field(gt=0)
    fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    slippage_buffer: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    optional_critique_enabled: bool = False

    def model_post_init(self, __context: object) -> None:
        if not self.policy_id.strip() or not self.version.strip():
            raise ValueError("policy id/version must not be blank")
        if self.optional_critique_enabled:
            raise ValueError("optional MarketCritique is disabled for Gate R")


class MarketAssessment(NamedTuple):
    comparison: MarketComparison
    result: ResearchResultEnvelope | None


def _require_bound_inputs(
    *, packet: MarketResearchPacket, blind_result: ResearchResultEnvelope,
    book_receipt: BookCaptureReceipt,
) -> None:
    packet = MarketResearchPacket.model_validate(packet.model_dump(mode="python"))
    blind = ResearchResultEnvelope.model_validate(blind_result.model_dump(mode="python"))
    receipt = BookCaptureReceipt.model_validate(book_receipt.model_dump(mode="python"))
    if blind.packet_stage.value != "BLIND" or blind.result_id != packet.blind_result_id:
        raise MarketComparisonError("Market packet does not bind the supplied accepted Blind result")
    refs = tuple(ref for ref in packet.provenance if ref.relation == "accepted_blind_result")
    if len(refs) != 1 or refs[0].source_artifact_id != blind.result_id or refs[0].content_sha256 != blind.canonical_sha256:
        raise MarketComparisonError("accepted Blind result hash does not bind Market packet")
    receipt_refs = tuple(ref for ref in packet.provenance if ref.relation == "formal_review_receipt")
    if len(receipt_refs) != 1 or receipt_refs[0].source_artifact_id != receipt.receipt_id or receipt_refs[0].content_sha256 != receipt.canonical_sha256:
        raise MarketComparisonError("book receipt hash does not bind Market packet provenance")
    if receipt.orderbook_snapshot_id != packet.orderbook.record_id or receipt.orderbook_snapshot_sha256 != packet.orderbook.canonical_sha256:
        raise MarketComparisonError("book receipt snapshot/hash does not bind Market packet")
    book_refs = tuple(ref for ref in packet.provenance if ref.relation == "fresh_paired_orderbook")
    if len(book_refs) != 1 or book_refs[0].source_artifact_id != packet.orderbook.record_id or book_refs[0].content_sha256 != packet.orderbook.canonical_sha256:
        raise MarketComparisonError("orderbook id/hash does not bind Market packet provenance")


def _depth(snapshot, size: Decimal, attr: str):
    rows = tuple(row for row in getattr(snapshot, attr) if row.target_size == size)
    return rows[0] if len(rows) == 1 else None


def _comparison(
    *, packet: MarketResearchPacket, blind: ResearchResultEnvelope,
    receipt: BookCaptureReceipt, policy: MarketComparisonPolicy, as_of: datetime,
    run_id: str, created_at: datetime,
) -> MarketComparison:
    snapshot = packet.orderbook
    yes = _depth(snapshot, policy.policy_size, "yes_depth")
    no = _depth(snapshot, policy.policy_size, "no_depth")
    yes_bid = snapshot.yes_leg.bids[0].price if snapshot.yes_leg.bids else None
    yes_ask = snapshot.yes_leg.asks[0].price if snapshot.yes_leg.asks else None
    no_bid = snapshot.no_leg.bids[0].price if snapshot.no_leg.bids else None
    no_ask = snapshot.no_leg.asks[0].price if snapshot.no_leg.asks else None
    one_sided = any(item is None for item in (yes_bid, yes_ask, no_bid, no_ask))
    crossed_outcome = (
        (not one_sided and (yes_bid >= yes_ask or no_bid >= no_ask))
        or (not one_sided and (yes_bid + no_bid > _ONE or yes_ask + no_ask < _ONE))
    )
    insufficient = (
        yes is None or no is None or yes.buy_insufficient_depth or no.buy_insufficient_depth
        or yes.buy_vwap is None or no.buy_vwap is None
    )
    stale = (
        receipt.status != BookCaptureStatus.ACCEPTED or snapshot.stale
        or (as_of - snapshot.source_observed_at).total_seconds() >= policy.max_book_age_seconds
    )
    reasons: list[str] = []
    if stale:
        reasons.append("BOOK_REFRESH_REQUIRED")
    if one_sided:
        reasons.append("ONE_SIDED_BOOK")
    if insufficient:
        reasons.append("INSUFFICIENT_POLICY_DEPTH")
    if crossed_outcome:
        reasons.append("CROSS_OUTCOME_INCONSISTENT")
    ready = not reasons
    status = MarketComparisonStatus.READY if ready else (
        MarketComparisonStatus.BOOK_REFRESH_REQUIRED if stale or one_sided or insufficient else MarketComparisonStatus.NON_ADVANCING
    )
    estimate = blind.probability_estimate
    cost_yes = cost_no = None
    if ready:
        assert yes is not None and no is not None and yes.buy_vwap is not None and no.buy_vwap is not None
        cost_yes = yes.buy_vwap * (_ONE + policy.fee_rate) + policy.slippage_buffer
        cost_no = no.buy_vwap * (_ONE + policy.fee_rate) + policy.slippage_buffer
        edges = (
            estimate.p_event_yes_low - cost_yes, estimate.p_event_yes_mid - cost_yes, estimate.p_event_yes_high - cost_yes,
            (_ONE - estimate.p_event_yes_high) - cost_no, (_ONE - estimate.p_event_yes_mid) - cost_no, (_ONE - estimate.p_event_yes_low) - cost_no,
        )
        reasons = ["EXECUTABLE_PAIRED_BOOK"]
    else:
        edges = (None,) * 6
    payload = dict(
        schema_version="alpha_p0_v1.0", run_id=run_id, created_at=created_at,
        source="deterministic_market_comparison", source_version=MARKET_COMPARISON_COMPILER_VERSION,
        provenance=(), extensions={}, accepted_blind_result_id=blind.result_id,
        accepted_blind_result_sha256=blind.canonical_sha256, blind_p_yes_low=estimate.p_event_yes_low,
        blind_p_yes_mid=estimate.p_event_yes_mid, blind_p_yes_high=estimate.p_event_yes_high,
        blind_as_of_utc=blind.completed_at, rule_contract_id=packet.rule_contract.record_id,
        rule_contract_sha256=packet.rule_contract.canonical_sha256, rule_hash=packet.rule_contract.rule_hash,
        book_receipt_id=receipt.receipt_id, book_receipt_sha256=receipt.canonical_sha256,
        orderbook_snapshot_id=snapshot.record_id, orderbook_snapshot_sha256=snapshot.canonical_sha256,
        book_capture_at_utc=snapshot.captured_at, comparison_as_of_utc=as_of,
        policy_size=policy.policy_size, yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask,
        yes_buy_vwap=None if yes is None else yes.buy_vwap, no_buy_vwap=None if no is None else no.buy_vwap,
        fee_slippage_cost_policy_id=policy.policy_id, fee_slippage_cost_policy_version=policy.version,
        fee_rate=policy.fee_rate, slippage_buffer=policy.slippage_buffer,
        yes_edge_low=edges[0], yes_edge_mid=edges[1], yes_edge_high=edges[2],
        no_edge_low=edges[3], no_edge_mid=edges[4], no_edge_high=edges[5], stale=stale,
        insufficient_depth=insufficient, one_sided=one_sided, crossed_outcome=crossed_outcome,
        status=status, reason_codes=tuple(sorted(reasons)),
    )
    digest = content_sha256(payload)
    record_id = stable_record_id("market_comparison", payload)
    return MarketComparison(record_id=record_id, comparison_id=record_id, comparison_sha256=digest, **payload)


class DeterministicMarketAssessmentCompiler:
    """Compile one immutable assessment; non-ready comparisons cannot import."""

    def compile(
        self, *, packet: MarketResearchPacket, accepted_blind_result: ResearchResultEnvelope,
        book_receipt: BookCaptureReceipt, policy: MarketComparisonPolicy, as_of: datetime,
        run_id: str, created_at: datetime,
    ) -> MarketAssessment:
        as_of, created_at = ensure_utc(as_of), ensure_utc(created_at)
        if not run_id.strip() or created_at < as_of:
            raise MarketComparisonError("run id is required and creation cannot precede comparison as-of")
        _require_bound_inputs(packet=packet, blind_result=accepted_blind_result, book_receipt=book_receipt)
        if as_of < packet.created_at or as_of < packet.orderbook.captured_at or created_at < packet.created_at:
            raise MarketComparisonError("comparison clocks cannot precede frozen market packet/book")
        comparison = _comparison(packet=packet, blind=accepted_blind_result, receipt=book_receipt, policy=policy, as_of=as_of, run_id=run_id, created_at=created_at)
        if comparison.status != MarketComparisonStatus.READY:
            return MarketAssessment(comparison, None)
        return MarketAssessment(comparison, self._result(packet=packet, blind=accepted_blind_result, comparison=comparison, run_id=run_id, created_at=created_at))

    @staticmethod
    def _result(*, packet: MarketResearchPacket, blind: ResearchResultEnvelope, comparison: MarketComparison, run_id: str, created_at: datetime) -> ResearchResultEnvelope:
        estimate = blind.probability_estimate
        estimate_id = stable_record_id("probability_estimate", "market_comparison", comparison.record_id, comparison.comparison_sha256, run_id)
        market_estimate = ProbabilityEstimate(
            record_id=estimate_id, run_id=run_id, created_at=created_at, source="deterministic_market_assessment",
            source_version=MARKET_COMPARISON_COMPILER_VERSION, provenance=(),
            extensions={"probability_update": "NONE", "accepted_blind_result_id": blind.result_id},
            market_id=packet.market_id, blind_candidate_id=estimate.blind_candidate_id, estimate_stage=EstimateStage.FINAL,
            model_type="blind_probability_preserved", p_event_yes_low=estimate.p_event_yes_low,
            p_event_yes_mid=estimate.p_event_yes_mid, p_event_yes_high=estimate.p_event_yes_high,
            p_market_yes_low=estimate.p_event_yes_low, p_market_yes_mid=estimate.p_event_yes_mid,
            p_market_yes_high=estimate.p_event_yes_high,
            uncertainty_drivers=estimate.uncertainty_drivers,
            assumptions=estimate.assumptions + ("market comparison does not update Blind probability",), model_version=MARKET_COMPARISON_COMPILER_VERSION,
        )
        artifact_id = stable_record_id("source_artifact", "market_comparison", comparison.record_id, comparison.comparison_sha256, run_id)
        artifact = SourceArtifact(record_id=artifact_id, artifact_id=artifact_id, run_id=run_id, created_at=created_at,
            source="deterministic_market_assessment", source_version=MARKET_COMPARISON_COMPILER_VERSION, provenance=(), extensions={},
            source_name="Deterministic MarketComparison", source_url_or_source_id=comparison.record_id,
            media_type="application/vnd.polymarket-alpha.market-comparison+json", captured_at=comparison.book_capture_at_utc,
            effective_as_of=comparison.book_capture_at_utc, capture_scope=CaptureScope.REFERENCE_ONLY,
            replayability=Replayability.REFERENCE_ONLY)
        evidence_id = stable_record_id("evidence", "market_comparison", artifact_id, comparison.comparison_sha256, run_id)
        evidence = ClaimEvidence(record_id=evidence_id, evidence_id=evidence_id, run_id=run_id, created_at=created_at,
            source="deterministic_market_assessment", source_version=MARKET_COMPARISON_COMPILER_VERSION, provenance=(), extensions={},
            claim="Executable paired-book comparison completed without modifying the Blind probability interval.",
            supports_yes_or_no=EvidenceSupport.NEUTRAL, source_tier=SourceTier.T0, source_name=artifact.source_name,
            source_url_or_source_id=artifact.source_url_or_source_id, accessed_at=comparison.comparison_as_of_utc,
            effective_as_of=comparison.book_capture_at_utc, primary_or_secondary="PRIMARY", quotation_or_paraphrase_location="deterministic comparison",
            confidence=Decimal("1"), origin=EvidenceOrigin.MARKET, source_artifact_id=artifact_id,
            capture_scope=CaptureScope.REFERENCE_ONLY, replayability=Replayability.REFERENCE_ONLY)
        result_id = stable_record_id("research_result", packet.record_id, packet.canonical_sha256, market_estimate.canonical_sha256, comparison.record_id, comparison.comparison_sha256, run_id, created_at)
        return ResearchResultEnvelope(record_id=result_id, result_id=result_id, run_id=run_id, created_at=created_at,
            source="deterministic_market_assessment", source_version=MARKET_COMPARISON_COMPILER_VERSION,
            provenance=(), extensions={"market_comparison_id": comparison.record_id,
                "market_comparison_sha256": comparison.comparison_sha256,
                "probability_update": "NONE", "accepted_blind_result_id": blind.result_id,
                "blind_probability_interval": [estimate.p_event_yes_low, estimate.p_event_yes_mid, estimate.p_event_yes_high]},
            packet_stage=packet.packet_stage, packet_id=packet.record_id, packet_sha256=packet.canonical_sha256,
            probability_estimate=market_estimate, evidence=(evidence,), source_artifacts=(artifact,), completed_at=created_at,
            producer="deterministic_market_assessment", producer_version=MARKET_COMPARISON_COMPILER_VERSION)


__all__ = ["MARKET_COMPARISON_COMPILER_VERSION", "DeterministicMarketAssessmentCompiler", "MarketAssessment", "MarketComparisonError", "MarketComparisonPolicy"]
