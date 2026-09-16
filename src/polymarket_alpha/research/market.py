"""Offline-only construction of the market-aware research packet.

This stage is intentionally a pure join over frozen Alpha contracts.  It
declares a fresh formal-review capture through the existing book-owner facade,
then accepts only the exact successful paired receipt and snapshot that answer
that demand.  It does not open transport, invoke a research provider, or write
storage.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence

from src.polymarket_alpha.books import build_formal_review_demand
from src.polymarket_alpha.contracts import (
    BlindResearchPacket,
    BookCaptureDemand,
    BookCapturePurpose,
    BookCaptureReceipt,
    BookCaptureStatus,
    CandidateCard,
    MarketIdentity,
    MarketResearchPacket,
    OrderbookSnapshot,
    PacketStage,
    ProvenanceRef,
    ResearchImportReason,
    ResearchImportReceipt,
    ResearchImportStatus,
    ResearchResultEnvelope,
    RuleContract,
    RuleGate,
    stable_record_id,
)
from src.polymarket_alpha.contracts.base import ensure_utc
from src.polymarket_alpha.rules.models import RuleGateDecision, RuleGateStage


MARKET_PACKET_BUILDER_VERSION = "p0_08c_v1"


def _require_gate_a_pass(contract: RuleContract, gate_a: RuleGateDecision) -> None:
    if contract.rule_gate != RuleGate.PASS:
        raise ValueError("Market stage requires RuleContract Gate A PASS")
    if gate_a.stage != RuleGateStage.A or gate_a.decision != RuleGate.PASS.value:
        raise ValueError("Market stage requires an explicit Gate A PASS decision")
    if gate_a.market_id != contract.market_id:
        raise ValueError("Gate A market does not match RuleContract")
    if gate_a.rule_contract_id != contract.record_id:
        raise ValueError("Gate A decision does not reference this RuleContract")
    if gate_a.rule_hash != contract.rule_hash:
        raise ValueError("Gate A decision rule hash does not match RuleContract")
    if gate_a.contract_revision_id != contract.contract_revision_id:
        raise ValueError("Gate A decision revision does not match RuleContract")
    if gate_a.compiler_version != contract.source_version:
        raise ValueError("Gate A compiler does not match RuleContract")


def _require_blind_acceptance(
    *,
    candidate: CandidateCard,
    contract: RuleContract,
    gate_a: RuleGateDecision,
    blind_packet: BlindResearchPacket,
    blind_result: ResearchResultEnvelope,
    blind_import_receipt: ResearchImportReceipt,
) -> None:
    if candidate.market_id != contract.market_id:
        raise ValueError("Candidate market does not match RuleContract")
    _require_gate_a_pass(contract, gate_a)
    if blind_packet.projection.rule_contract_hash != contract.rule_hash:
        raise ValueError("Blind packet rule hash does not match RuleContract")
    if blind_packet.blind_rule.rule_hash != contract.rule_hash:
        raise ValueError("Blind rule view does not match RuleContract")
    expected_blind_candidate_id = stable_record_id(
        "blind_candidate",
        {
            "candidate_id": candidate.candidate_id,
            "rule_hash": contract.rule_hash,
            "contract_revision_id": contract.contract_revision_id,
            "questions": tuple(
                question.question_id
                for question in blind_packet.projection.research_questions
            ),
            "evidence": tuple(
                item.evidence_id for item in blind_packet.projection.evidence
            ),
        },
    )
    if blind_packet.projection.blind_candidate_id != expected_blind_candidate_id:
        raise ValueError("Blind projection does not bind this Candidate")
    if blind_result.packet_stage != PacketStage.BLIND:
        raise ValueError("Market stage requires a BLIND research result")
    if blind_result.packet_id != blind_packet.record_id:
        raise ValueError("Blind result packet id does not match Blind packet")
    if blind_result.packet_sha256 != blind_packet.canonical_sha256:
        raise ValueError("Blind result packet hash does not match Blind packet")
    if blind_result.probability_estimate.blind_candidate_id != blind_packet.projection.blind_candidate_id:
        raise ValueError("Blind result does not bind the Blind projection")
    if blind_import_receipt.status != ResearchImportStatus.ACCEPTED:
        raise ValueError("Market stage requires an accepted Blind import receipt")
    if blind_import_receipt.packet_stage != PacketStage.BLIND:
        raise ValueError("Market stage requires a BLIND import receipt")
    if blind_import_receipt.reasons != (ResearchImportReason.ACCEPTED,):
        raise ValueError("accepted Blind import receipt has inconsistent reasons")
    if blind_import_receipt.packet_id != blind_packet.record_id:
        raise ValueError("Blind import receipt packet id does not match Blind packet")
    if blind_import_receipt.packet_sha256 != blind_packet.canonical_sha256:
        raise ValueError("Blind import receipt packet hash does not match Blind packet")
    if blind_import_receipt.accepted_result_id != blind_result.result_id:
        raise ValueError("Blind import receipt does not accept this Blind result")
    if blind_import_receipt.accepted_result_sha256 != blind_result.canonical_sha256:
        raise ValueError("Blind import receipt does not accept these Blind result bytes")
    if blind_result.completed_at < blind_packet.created_at:
        raise ValueError("Blind result cannot precede Blind packet creation")
    if blind_import_receipt.imported_at < blind_result.completed_at:
        raise ValueError("Blind import receipt cannot precede result completion")


def build_market_formal_review_demand(
    *,
    candidate: CandidateCard,
    contract: RuleContract,
    gate_a: RuleGateDecision,
    blind_packet: BlindResearchPacket,
    blind_result: ResearchResultEnvelope,
    blind_import_receipt: ResearchImportReceipt,
    identity: MarketIdentity,
    requested_at: datetime,
    valid_until: datetime,
    max_staleness_seconds: int,
    target_sizes: Sequence[Decimal],
    run_id: str,
) -> BookCaptureDemand:
    """Declare a FORMAL_REVIEW book request only after accepted Blind research."""

    _require_blind_acceptance(
        candidate=candidate,
        contract=contract,
        gate_a=gate_a,
        blind_packet=blind_packet,
        blind_result=blind_result,
        blind_import_receipt=blind_import_receipt,
    )
    if identity.market_id != candidate.market_id:
        raise ValueError("formal-review identity does not match Candidate")
    requested_at = ensure_utc(requested_at)
    if requested_at < blind_import_receipt.imported_at:
        raise ValueError("formal-review demand cannot precede Blind acceptance")
    if requested_at < gate_a.evaluated_at:
        raise ValueError("formal-review demand cannot precede Gate A")
    return build_formal_review_demand(
        blind_result=blind_result,
        import_receipt=blind_import_receipt,
        identity=identity,
        requested_at=requested_at,
        valid_until=valid_until,
        max_staleness_seconds=max_staleness_seconds,
        target_sizes=target_sizes,
        run_id=run_id,
    )


def _require_usable_formal_book(
    *,
    demand: BookCaptureDemand,
    receipt: BookCaptureReceipt,
    snapshot: OrderbookSnapshot,
) -> None:
    if demand.purpose != BookCapturePurpose.FORMAL_REVIEW:
        raise ValueError("Market packet rejects SENSING book demand")
    if receipt.purpose != BookCapturePurpose.FORMAL_REVIEW:
        raise ValueError("Market packet rejects non-FORMAL_REVIEW book receipt")
    if receipt.status != BookCaptureStatus.ACCEPTED:
        raise ValueError("Market packet requires successful fresh book receipt")
    if receipt.demand_id != demand.demand_id or receipt.demand_sha256 != demand.canonical_sha256:
        raise ValueError("Book receipt does not bind the exact formal-review demand")
    if receipt.market_id != demand.identity.market_id:
        raise ValueError("Book receipt market does not match formal-review demand")
    if receipt.orderbook_snapshot_id != snapshot.record_id:
        raise ValueError("Book receipt snapshot id does not match supplied snapshot")
    if receipt.orderbook_snapshot_sha256 != snapshot.canonical_sha256:
        raise ValueError("Book receipt snapshot hash does not match supplied snapshot")
    if receipt.capture_group_id != snapshot.capture_group_id:
        raise ValueError("Book receipt capture group does not match supplied snapshot")
    if receipt.source_observed_at != snapshot.source_observed_at:
        raise ValueError("Book receipt source clock does not match supplied snapshot")
    if snapshot.identity != demand.identity:
        raise ValueError("Book snapshot identity does not match formal-review demand")
    if snapshot.stale:
        raise ValueError("Market packet rejects stale book snapshot")
    if receipt.received_at < snapshot.captured_at:
        raise ValueError("Book receipt cannot precede snapshot capture")
    if snapshot.source_observed_at > snapshot.captured_at:
        raise ValueError("Book source clock cannot follow snapshot capture")
    if snapshot.captured_at < demand.requested_at:
        raise ValueError("Book snapshot cannot precede formal-review demand")
    if receipt.received_at >= demand.valid_until:
        raise ValueError("Book receipt exceeds formal-review demand validity")
    if (receipt.received_at - snapshot.source_observed_at).total_seconds() > demand.max_staleness_seconds:
        raise ValueError("Book snapshot exceeds formal-review staleness budget")
    if not snapshot.yes_leg.bids or not snapshot.yes_leg.asks or not snapshot.no_leg.bids or not snapshot.no_leg.asks:
        raise ValueError("Market packet rejects one-sided paired book")
    if snapshot.yes_leg.bids[0].price >= snapshot.yes_leg.asks[0].price or snapshot.no_leg.bids[0].price >= snapshot.no_leg.asks[0].price:
        raise ValueError("Market packet rejects crossed paired book")
    if any(
        row.buy_insufficient_depth or row.sell_insufficient_depth
        for row in (*snapshot.yes_depth, *snapshot.no_depth)
    ):
        raise ValueError("Market packet rejects insufficient target depth")
    for label, rows in (("YES", snapshot.yes_depth), ("NO", snapshot.no_depth)):
        targets = tuple(row.target_size for row in rows)
        if targets != demand.target_sizes or len(targets) != len(set(targets)):
            raise ValueError(f"Market packet requires exact {label} target-depth coverage")
        if any(row.buy_vwap is None or row.sell_vwap is None for row in rows):
            raise ValueError(f"Market packet requires complete {label} target-depth VWAP")
    if any(
        flag.endswith("_BIDS_EMPTY")
        or flag.endswith("_ASKS_EMPTY")
        or flag.endswith("_BOOK_CROSSED")
        or flag == "TARGET_DEPTH_INSUFFICIENT"
        for flag in snapshot.quality_flags
    ):
        raise ValueError("Market packet rejects unusable book quality flags")


def freeze_market_research_packet(
    *,
    candidate: CandidateCard,
    contract: RuleContract,
    gate_a: RuleGateDecision,
    blind_packet: BlindResearchPacket,
    blind_result: ResearchResultEnvelope,
    blind_import_receipt: ResearchImportReceipt,
    demand: BookCaptureDemand,
    book_receipt: BookCaptureReceipt,
    snapshot: OrderbookSnapshot,
    build_run_identity: str,
    created_at: datetime,
) -> MarketResearchPacket:
    """Freeze the market-aware packet from the exact accepted book lineage."""

    if not build_run_identity.strip():
        raise ValueError("build_run_identity must not be blank")
    created_at = ensure_utc(created_at)
    _require_blind_acceptance(
        candidate=candidate,
        contract=contract,
        gate_a=gate_a,
        blind_packet=blind_packet,
        blind_result=blind_result,
        blind_import_receipt=blind_import_receipt,
    )
    if demand.purpose != BookCapturePurpose.FORMAL_REVIEW:
        raise ValueError("Market packet rejects SENSING book demand")
    if demand.blind_result_id != blind_result.result_id:
        raise ValueError("formal-review demand does not bind the accepted Blind result")
    if demand.trigger_artifact_id != blind_result.result_id:
        raise ValueError("formal-review demand trigger is not the accepted Blind result")
    if demand.trigger_artifact_sha256 != blind_result.canonical_sha256:
        raise ValueError("formal-review demand trigger hash does not match accepted Blind result")
    _require_usable_formal_book(demand=demand, receipt=book_receipt, snapshot=snapshot)
    if demand.identity.market_id != candidate.market_id:
        raise ValueError("formal-review demand market does not match Candidate")
    if created_at < book_receipt.received_at:
        raise ValueError("Market packet cannot precede accepted book receipt")
    if created_at < blind_import_receipt.imported_at:
        raise ValueError("Market packet cannot precede Blind acceptance")
    if created_at >= demand.valid_until:
        raise ValueError("Market packet cannot be created after demand expiry")

    market_run_id = stable_record_id("market_research_run", build_run_identity)
    packet_id = stable_record_id(
        "market_packet",
        candidate.candidate_id,
        contract.record_id,
        contract.canonical_sha256,
        gate_a.record_id,
        gate_a.canonical_sha256,
        blind_packet.record_id,
        blind_packet.canonical_sha256,
        blind_result.result_id,
        blind_result.canonical_sha256,
        blind_import_receipt.record_id,
        blind_import_receipt.canonical_sha256,
        demand.demand_id,
        demand.canonical_sha256,
        book_receipt.receipt_id,
        book_receipt.canonical_sha256,
        snapshot.record_id,
        snapshot.canonical_sha256,
        market_run_id,
        created_at,
    )
    provenance = (
        ProvenanceRef(source_artifact_id=gate_a.record_id, relation="gate_a", content_sha256=gate_a.canonical_sha256),
        ProvenanceRef(source_artifact_id=blind_packet.record_id, relation="blind_packet", content_sha256=blind_packet.canonical_sha256),
        ProvenanceRef(source_artifact_id=blind_result.result_id, relation="accepted_blind_result", content_sha256=blind_result.canonical_sha256),
        ProvenanceRef(source_artifact_id=blind_import_receipt.record_id, relation="blind_import_receipt", content_sha256=blind_import_receipt.canonical_sha256),
        ProvenanceRef(source_artifact_id=demand.demand_id, relation="formal_review_demand", content_sha256=demand.canonical_sha256),
        ProvenanceRef(source_artifact_id=book_receipt.receipt_id, relation="formal_review_receipt", content_sha256=book_receipt.canonical_sha256),
        ProvenanceRef(source_artifact_id=snapshot.record_id, relation="fresh_paired_orderbook", content_sha256=snapshot.canonical_sha256, source_observed_at=snapshot.source_observed_at),
    )
    return MarketResearchPacket(
        record_id=packet_id,
        run_id=market_run_id,
        created_at=created_at,
        source="market_packet_builder",
        source_version=MARKET_PACKET_BUILDER_VERSION,
        provenance=provenance,
        extensions={},
        candidate_id=candidate.candidate_id,
        market_id=candidate.market_id,
        blind_packet_id=blind_packet.record_id,
        blind_result_id=blind_result.result_id,
        rule_contract=contract,
        blind_evidence=blind_result.evidence,
        orderbook=snapshot,
    )
