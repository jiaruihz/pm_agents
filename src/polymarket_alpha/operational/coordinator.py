"""Resumable, filesystem-handoff operational coordinator (GLM-OP-04).

Assembles the released services end to end and owns no business logic:

.. code-block:: text

    captured Gamma bytes -> GLM-OP-01 catalog ingest -> change/recall/candidate
    -> Rule A + Blind packet handoff -> caller supplies Blind result
    -> FORMAL_REVIEW paired demand -> GLM-OP-02 outbox append
    -> caller supplies paired owner book artifacts -> GLM-OP-03 bridge
    -> Market packet handoff -> caller supplies Market result
    -> Rule B + NO_POSITION/SIMULATED ledger.

Every stage stops at its filesystem handoff boundary and resumes only from
the immutable, hash-bound state manifest sealed under ``state/`` inside the
caller-supplied artifact root.  There is no model call, no polling, no
scheduler, no waiting for production artifacts, and no order or signing
capability anywhere in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..books.adapter import (
    BookCaptureReceipt,
    OwnerDemandBundle,
    PairedBookNormalization,
    build_owner_capture_demands,
)
from ..contracts import (
    BlindResearchPacket,
    BookCaptureDemand,
    CandidateCard,
    CandidateTransition,
    MarketResearchPacket,
    MarketSnapshot,
    OrderbookSnapshot,
    RecallHit,
    ResearchImportReceipt,
    ResearchResultEnvelope,
    RuleContract,
    canonical_json,
)
from ..change import detect_market_change
from ..decision import RankConfig
from ..pipeline import (
    BlindAcceptedStage,
    BlindResumeOutcome,
    BlindReviewStage,
    MarketBuildOutcome,
    MarketResumeOutcome,
    MarketReviewStage,
    ReviewPipelineBlocked,
    accept_formal_book,
    persist_scan_candidates,
    resume_blind_result,
    resume_market_result,
    start_blind_review,
)
from ..pipeline.recall import (
    MultiRecallScanRequest,
    MultiRecallScanner,
    _descriptor as _provider_descriptor,
)
from ..recall.book_anomaly import BookAnomalyRecallProvider
from ..recall.controversy import ControversyRecaller
from ..recall.new_changed import NewChangedRecaller, PrebookRecallRequest
from ..recall.registry import ProviderRegistry
from ..recall.structural_metadata import StructuralMetadataRecaller
from ..recall.wallet import WalletRecallProvider
from ..research.handoff import (
    HandoffConflictError,
    PacketHandoffManifest,
    ResultHandoffReceipt,
    _read_allowed,
    _write_immutable,
)
from ..rules.models import RuleCompilationReceipt, RuleCompilationRequest, RuleGateDecision
from ..storage import AlphaRepository
from .book_bridge import (
    BookBridgeResult,
    OwnerBookLegSubmission,
    bridge_owner_books,
)
from .demand_outbox import (
    DEMAND_OUTBOX_LOCATOR,
    DemandOutboxAppendReceipt,
    append_owner_demand_bundle,
)
from .gamma_ingest import (
    GammaOperationalIngestResult,
    GammaResponseReceipt,
    ingest_captured_events_response,
)


COORDINATOR_VERSION = "op_coordinator_v1"
STATE_LOCATOR_PREFIX = "state"
BLIND_PACKET_LOCATOR = "outbox/blind.packet.json"
BLIND_MANIFEST_LOCATOR = "outbox/blind.manifest.json"
MARKET_PACKET_LOCATOR = "outbox/market.packet.json"
MARKET_MANIFEST_LOCATOR = "outbox/market.manifest.json"


class OperationalStage(StrEnum):
    GAMMA_INGESTED = "GAMMA_INGESTED"
    CANDIDATE_SCANNED = "CANDIDATE_SCANNED"
    BLIND_PACKET_FROZEN = "BLIND_PACKET_FROZEN"
    BLIND_RESULT_ACCEPTED = "BLIND_RESULT_ACCEPTED"
    MARKET_PACKET_FROZEN = "MARKET_PACKET_FROZEN"
    FINALIZED = "FINALIZED"


class CoordinatorBlocked(ValueError):
    """A stage cannot run or resume from the supplied immutable inputs."""


@dataclass(frozen=True)
class CoordinatorState:
    """Hash-bound resume point for exactly one candidate's stage machine."""

    stage: OperationalStage
    run_id: str
    market_id: str
    created_at: datetime
    hashes: dict[str, str] = field(default_factory=dict)
    snapshot_ids: tuple[str, ...] = ()
    change_event_ids: tuple[str, ...] = ()
    hit_ids: tuple[str, ...] = ()
    candidate_id: str | None = None
    rule_contract_id: str | None = None
    compilation_receipt_id: str | None = None
    gate_a_id: str | None = None
    blind_projection_id: str | None = None
    blind_packet_id: str | None = None
    blind_packet_manifest: dict[str, Any] | None = None
    blind_handoff_receipt: dict[str, Any] | None = None
    blind_result_id: str | None = None
    blind_import_receipt_id: str | None = None
    demand_id: str | None = None
    outbox_bundle_id: str | None = None
    book_receipt_id: str | None = None
    book_snapshot_id: str | None = None
    market_packet_id: str | None = None
    market_packet_manifest: dict[str, Any] | None = None
    market_handoff_receipt: dict[str, Any] | None = None
    market_result_id: str | None = None
    market_import_receipt_id: str | None = None
    gate_b_id: str | None = None
    decision_id: str | None = None
    prediction_id: str | None = None
    transition_ids: tuple[str, ...] = ()
    blind_transition_count: int = 0
    accepted_transition_count: int = 0
    market_transition_count: int = 0

    def locator(self) -> str:
        return f"{STATE_LOCATOR_PREFIX}/{self.stage.value}.json"

    def to_payload(self) -> dict[str, Any]:
        return {
            "coordinator_version": COORDINATOR_VERSION,
            "stage": self.stage.value,
            "run_id": self.run_id,
            "market_id": self.market_id,
            "created_at": self.created_at.isoformat(),
            "hashes": dict(self.hashes),
            "snapshot_ids": list(self.snapshot_ids),
            "change_event_ids": list(self.change_event_ids),
            "hit_ids": list(self.hit_ids),
            "candidate_id": self.candidate_id,
            "rule_contract_id": self.rule_contract_id,
            "compilation_receipt_id": self.compilation_receipt_id,
            "gate_a_id": self.gate_a_id,
            "blind_projection_id": self.blind_projection_id,
            "blind_packet_id": self.blind_packet_id,
            "blind_packet_manifest": self.blind_packet_manifest,
            "blind_handoff_receipt": self.blind_handoff_receipt,
            "blind_result_id": self.blind_result_id,
            "blind_import_receipt_id": self.blind_import_receipt_id,
            "demand_id": self.demand_id,
            "outbox_bundle_id": self.outbox_bundle_id,
            "book_receipt_id": self.book_receipt_id,
            "book_snapshot_id": self.book_snapshot_id,
            "market_packet_id": self.market_packet_id,
            "market_packet_manifest": self.market_packet_manifest,
            "market_handoff_receipt": self.market_handoff_receipt,
            "market_result_id": self.market_result_id,
            "market_import_receipt_id": self.market_import_receipt_id,
            "gate_b_id": self.gate_b_id,
            "decision_id": self.decision_id,
            "prediction_id": self.prediction_id,
            "transition_ids": list(self.transition_ids),
            "blind_transition_count": self.blind_transition_count,
            "accepted_transition_count": self.accepted_transition_count,
            "market_transition_count": self.market_transition_count,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "CoordinatorState":
        if payload.get("coordinator_version") != COORDINATOR_VERSION:
            raise CoordinatorBlocked("state manifest was written by a different coordinator version")
        return CoordinatorState(
            stage=OperationalStage(str(payload["stage"])),
            run_id=str(payload["run_id"]),
            market_id=str(payload["market_id"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            hashes=dict(payload.get("hashes") or {}),
            snapshot_ids=tuple(payload.get("snapshot_ids") or ()),
            change_event_ids=tuple(payload.get("change_event_ids") or ()),
            hit_ids=tuple(payload.get("hit_ids") or ()),
            candidate_id=payload.get("candidate_id"),
            rule_contract_id=payload.get("rule_contract_id"),
            compilation_receipt_id=payload.get("compilation_receipt_id"),
            gate_a_id=payload.get("gate_a_id"),
            blind_projection_id=payload.get("blind_projection_id"),
            blind_packet_id=payload.get("blind_packet_id"),
            blind_packet_manifest=dict(payload["blind_packet_manifest"]) if payload.get("blind_packet_manifest") else None,
            blind_handoff_receipt=dict(payload["blind_handoff_receipt"]) if payload.get("blind_handoff_receipt") else None,
            blind_result_id=payload.get("blind_result_id"),
            blind_import_receipt_id=payload.get("blind_import_receipt_id"),
            demand_id=payload.get("demand_id"),
            outbox_bundle_id=payload.get("outbox_bundle_id"),
            book_receipt_id=payload.get("book_receipt_id"),
            book_snapshot_id=payload.get("book_snapshot_id"),
            market_packet_id=payload.get("market_packet_id"),
            market_packet_manifest=dict(payload["market_packet_manifest"]) if payload.get("market_packet_manifest") else None,
            market_handoff_receipt=dict(payload["market_handoff_receipt"]) if payload.get("market_handoff_receipt") else None,
            market_result_id=payload.get("market_result_id"),
            market_import_receipt_id=payload.get("market_import_receipt_id"),
            gate_b_id=payload.get("gate_b_id"),
            decision_id=payload.get("decision_id"),
            prediction_id=payload.get("prediction_id"),
            transition_ids=tuple(payload.get("transition_ids") or ()),
            blind_transition_count=int(payload.get("blind_transition_count") or 0),
            accepted_transition_count=int(payload.get("accepted_transition_count") or 0),
            market_transition_count=int(payload.get("market_transition_count") or 0),
        )


def seal_state(artifact_root: Path, state: CoordinatorState) -> None:
    """Seal one stage state immutably; a different-bytes rewrite is a block.

    Replaying a stage with identical inputs produces identical canonical
    bytes and stays idempotent; any other content at the same locator is a
    coordinator-level block, not an untyped filesystem error.
    """

    try:
        _write_immutable(
            Path(artifact_root),
            state.locator(),
            canonical_json(state.to_payload()).encode("utf-8"),
        )
    except ValueError as error:
        raise CoordinatorBlocked(
            f"{state.stage.value} state manifest cannot be sealed: {error}"
        ) from error


def load_state(artifact_root: Path, stage: OperationalStage) -> CoordinatorState:
    raw = _read_allowed(Path(artifact_root), f"{STATE_LOCATOR_PREFIX}/{stage.value}.json")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoordinatorBlocked(f"{stage.value} state manifest is not valid JSON") from error
    if not isinstance(payload, Mapping):
        raise CoordinatorBlocked(f"{stage.value} state manifest must be a JSON object")
    try:
        return CoordinatorState.from_payload(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise CoordinatorBlocked(f"{stage.value} state manifest is malformed: {error}") from error


def _require_stage(artifact_root: Path, stage: OperationalStage) -> CoordinatorState:
    try:
        return load_state(artifact_root, stage)
    except (OSError, ValueError) as error:
        raise CoordinatorBlocked(
            f"stage requires a sealed {stage.value} state manifest: {error}"
        ) from error


def _contract(repository: AlphaRepository, state: CoordinatorState, record_id: str, model: type):
    expected = state.hashes.get(record_id)
    if expected is None:
        raise CoordinatorBlocked(f"state manifest does not hash-bind {record_id}")
    payload = repository.get_contract(record_id)
    if payload is None:
        raise CoordinatorBlocked(f"{record_id} is missing from the Alpha repository")
    try:
        rebuilt = model.model_validate(payload)
    except ValueError as error:
        raise CoordinatorBlocked(f"{record_id} no longer validates as {model.__name__}") from error
    if rebuilt.canonical_sha256 != expected:
        raise CoordinatorBlocked(f"{record_id} hash does not match the sealed state manifest")
    return rebuilt


def _bind_hashes(repository: AlphaRepository, contracts: Sequence[Any], hashes: dict[str, str]) -> None:
    for contract in contracts:
        if contract is None:
            continue
        record_id = getattr(contract, "record_id", None)
        digest = getattr(contract, "canonical_sha256", None)
        if not isinstance(record_id, str) or not isinstance(digest, str):
            continue
        existing = hashes.get(record_id)
        if existing is not None and existing != digest:
            raise CoordinatorBlocked(f"conflicting hash binding for {record_id}")
        hashes[record_id] = digest


def _manifest_payload(manifest: PacketHandoffManifest) -> dict[str, Any]:
    return {
        "handoff_version": manifest.handoff_version,
        "state": manifest.state.value if hasattr(manifest.state, "value") else str(manifest.state),
        "packet_stage": manifest.packet_stage.value if hasattr(manifest.packet_stage, "value") else str(manifest.packet_stage),
        "packet_id": manifest.packet_id,
        "packet_sha256": manifest.packet_sha256,
        "packet_bytes_sha256": manifest.packet_bytes_sha256,
        "packet_byte_length": manifest.packet_byte_length,
        "packet_locator": manifest.packet_locator,
        "manifest_locator": manifest.manifest_locator,
        "created_at": manifest.created_at.isoformat(),
    }


def _manifest_from_payload(payload: Mapping[str, Any]) -> PacketHandoffManifest:
    try:
        return PacketHandoffManifest(
            handoff_version=str(payload["handoff_version"]),
            state=str(payload["state"]),
            packet_stage=str(payload["packet_stage"]),
            packet_id=str(payload["packet_id"]),
            packet_sha256=str(payload["packet_sha256"]),
            packet_bytes_sha256=str(payload["packet_bytes_sha256"]),
            packet_byte_length=int(payload["packet_byte_length"]),
            packet_locator=str(payload["packet_locator"]),
            manifest_locator=str(payload["manifest_locator"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CoordinatorBlocked(f"state manifest has a malformed packet manifest: {error}") from error


def _handoff_payload(receipt: ResultHandoffReceipt) -> dict[str, Any]:
    return {
        "state": receipt.state.value if hasattr(receipt.state, "value") else str(receipt.state),
        "packet_manifest": _manifest_payload(receipt.packet_manifest),
        "result_locator": receipt.result_locator,
        "sealed_submission_locator": receipt.sealed_submission_locator,
        "submitted_bytes_sha256": receipt.submitted_bytes_sha256,
        "submitted_byte_length": receipt.submitted_byte_length,
        "import_receipt_id": receipt.import_receipt.record_id,
    }


def _load_blind_stage(repository: AlphaRepository, state: CoordinatorState) -> BlindReviewStage:
    if not state.candidate_id or not state.blind_packet_id:
        raise CoordinatorBlocked("state does not carry a frozen Blind stage")
    candidate = _contract(repository, state, state.candidate_id, CandidateCard)
    hits = tuple(
        _contract(repository, state, hit_id, RecallHit) for hit_id in state.hit_ids
    )
    contract = _contract(repository, state, state.rule_contract_id or "", RuleContract)
    compilation = _contract(
        repository, state, state.compilation_receipt_id or "", RuleCompilationReceipt
    )
    gate_a = _contract(repository, state, state.gate_a_id or "", RuleGateDecision)
    packet = _contract(repository, state, state.blind_packet_id, BlindResearchPacket)
    assert state.blind_packet_manifest is not None
    manifest = _manifest_from_payload(state.blind_packet_manifest)
    transitions = tuple(
        _contract(repository, state, record_id, CandidateTransition)
        for record_id in state.transition_ids[: state.blind_transition_count]
    )
    return BlindReviewStage(
        candidate=candidate,
        recall_hits=hits,
        contract=contract,
        compilation_receipt=compilation,
        gate_a=gate_a,
        blind_packet=packet,
        packet_manifest=manifest,
        transitions=transitions,
    )


def _load_accepted_stage(repository: AlphaRepository, state: CoordinatorState) -> BlindAcceptedStage:
    blind = _load_blind_stage(repository, state)
    result = _contract(repository, state, state.blind_result_id or "", ResearchResultEnvelope)
    import_receipt = _contract(
        repository, state, state.blind_import_receipt_id or "", ResearchImportReceipt
    )
    demand = _contract(repository, state, state.demand_id or "", BookCaptureDemand)
    if state.blind_handoff_receipt is None:
        raise CoordinatorBlocked("state does not carry the Blind handoff receipt")
    handoff_payload = state.blind_handoff_receipt
    try:
        handoff = ResultHandoffReceipt(
            state=str(handoff_payload["state"]),
            packet_manifest=_manifest_from_payload(handoff_payload["packet_manifest"]),
            result_locator=str(handoff_payload["result_locator"]),
            sealed_submission_locator=str(handoff_payload["sealed_submission_locator"]),
            submitted_bytes_sha256=str(handoff_payload["submitted_bytes_sha256"]),
            submitted_byte_length=int(handoff_payload["submitted_byte_length"]),
            import_receipt=import_receipt,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CoordinatorBlocked(
            f"{state.stage.value} state manifest has a malformed handoff receipt: {error}"
        ) from error
    transitions = tuple(
        _contract(repository, state, record_id, CandidateTransition)
        for record_id in state.transition_ids[: state.accepted_transition_count]
    )
    return BlindAcceptedStage(blind, result, import_receipt, handoff, demand, transitions)


# --- Stage 1: captured Gamma response ingest ---------------------------------


@dataclass(frozen=True)
class GammaIngestStageInputs:
    raw_response: bytes
    response_receipt: GammaResponseReceipt
    run_id: str
    observed_at: datetime
    ingested_at: datetime
    page_budget: int
    requested_offset: int = 0
    requested_limit: int | None = None


@dataclass(frozen=True)
class GammaIngestStageResult:
    state: CoordinatorState | None
    ingest: GammaOperationalIngestResult


def run_gamma_ingest_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: GammaIngestStageInputs,
) -> GammaIngestStageResult:
    result = ingest_captured_events_response(
        repository,
        raw_response=inputs.raw_response,
        response_receipt=inputs.response_receipt,
        run_id=inputs.run_id,
        observed_at=inputs.observed_at,
        ingested_at=inputs.ingested_at,
        page_budget=inputs.page_budget,
        requested_offset=inputs.requested_offset,
        requested_limit=inputs.requested_limit,
        max_flattened_markets=1,
    )
    if not result.accepted or result.catalog is None:
        failure = result.failure
        detail = f"{failure.code.value}: {failure.detail}" if failure else "unknown"
        raise CoordinatorBlocked(f"captured Gamma response was rejected: {detail}")
    market_ids = sorted({snapshot.identity.market_id for snapshot in result.catalog.snapshots})
    if not market_ids:
        raise CoordinatorBlocked("captured Gamma response produced no market snapshots")
    if len(market_ids) > 1:
        raise CoordinatorBlocked(
            "operational coordinator drives exactly one market per stage machine; "
            f"response carried {len(market_ids)}"
        )
    hashes: dict[str, str] = {}
    _bind_hashes(repository, result.catalog.snapshots, hashes)
    state = CoordinatorState(
        stage=OperationalStage.GAMMA_INGESTED,
        run_id=inputs.run_id,
        market_id=market_ids[0],
        created_at=inputs.ingested_at,
        hashes=hashes,
        snapshot_ids=tuple(snapshot.record_id for snapshot in result.catalog.snapshots),
    )
    seal_state(artifact_root, state)
    return GammaIngestStageResult(state, result)


# --- Stage 2: change events + recall + candidate ------------------------------


@dataclass(frozen=True)
class ScanStageInputs:
    run_id: str
    as_of: datetime


@dataclass(frozen=True)
class ScanStageResult:
    state: CoordinatorState
    candidate: CandidateCard


def run_scan_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: ScanStageInputs,
) -> ScanStageResult:
    prior = _require_stage(artifact_root, OperationalStage.GAMMA_INGESTED)
    snapshots = [
        _contract(repository, prior, record_id, MarketSnapshot)
        for record_id in prior.snapshot_ids
    ]
    if not snapshots:
        raise CoordinatorBlocked("no catalog snapshots are available for the scan")
    events = []
    for snapshot in snapshots:
        event = detect_market_change(
            None,
            snapshot,
            run_id=inputs.run_id,
            detected_at=inputs.as_of,
        )
        if event is not None:
            repository.save_contract(event)
            events.append(event)
    if not events:
        raise CoordinatorBlocked("no change event was produced from the captured snapshots")
    registry = ProviderRegistry(
        tuple(
            _provider_descriptor(provider)
            for provider in (
                NewChangedRecaller(),
                StructuralMetadataRecaller(),
                ControversyRecaller(),
                WalletRecallProvider(),
                BookAnomalyRecallProvider(),
            )
        )
    )
    scanner = MultiRecallScanner(
        registry,
        new_changed=NewChangedRecaller(),
        structural_metadata=StructuralMetadataRecaller(),
    )
    scan = scanner.scan(
        MultiRecallScanRequest(
            run_id=inputs.run_id,
            created_at=inputs.as_of,
            as_of=inputs.as_of,
            prebook_request=PrebookRecallRequest(
                run_id=inputs.run_id,
                as_of=inputs.as_of,
                events=tuple(events),
                snapshots=tuple(snapshots),
            ),
        )
    )
    if not scan.aggregation.results:
        raise CoordinatorBlocked("recall aggregation produced no candidate")
    persist_scan_candidates(repository, outcome=scan)
    candidate = scan.aggregation.results[0].candidate
    hashes = dict(prior.hashes)
    _bind_hashes(repository, (*events, candidate, *scan.accepted_hits), hashes)
    state = CoordinatorState(
        stage=OperationalStage.CANDIDATE_SCANNED,
        run_id=prior.run_id,
        market_id=candidate.market_id,
        created_at=inputs.as_of,
        hashes=hashes,
        snapshot_ids=prior.snapshot_ids,
        change_event_ids=tuple(event.record_id for event in events),
        hit_ids=tuple(hit.record_id for hit in scan.accepted_hits),
        candidate_id=candidate.record_id,
    )
    seal_state(artifact_root, state)
    return ScanStageResult(state, candidate)


# --- Stage 3: Rule A + Blind packet export ------------------------------------


@dataclass(frozen=True)
class BlindStageInputs:
    rule_request: RuleCompilationRequest
    gate_evaluated_at: datetime
    packet_created_at: datetime
    handoff_created_at: datetime


@dataclass(frozen=True)
class BlindStageResult:
    state: CoordinatorState
    blind: BlindReviewStage


def run_blind_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: BlindStageInputs,
) -> BlindStageResult:
    prior = _require_stage(artifact_root, OperationalStage.CANDIDATE_SCANNED)
    candidate = _contract(repository, prior, prior.candidate_id or "", CandidateCard)
    snapshots = [
        _contract(repository, prior, record_id, MarketSnapshot)
        for record_id in prior.snapshot_ids
    ]
    current = [item for item in snapshots if item.identity.market_id == candidate.market_id]
    if not current:
        raise CoordinatorBlocked("no snapshot matches the scanned candidate market")
    snapshot = current[-1]
    request = inputs.rule_request
    if request.market_id != candidate.market_id:
        raise CoordinatorBlocked("rule compilation market does not match the candidate")
    if request.expected_rule_hash != snapshot.rule_hash:
        raise CoordinatorBlocked("rule compilation hash does not match the snapshot rule hash")
    request = request.model_copy(update={"market_snapshot_id": snapshot.record_id})
    blind = start_blind_review(
        repository=repository,
        candidate=candidate,
        recall_hits=tuple(
            _contract(repository, prior, hit_id, RecallHit) for hit_id in prior.hit_ids
        ),
        compilation_request=request,
        artifact_root=Path(artifact_root),
        packet_locator=BLIND_PACKET_LOCATOR,
        manifest_locator=BLIND_MANIFEST_LOCATOR,
        gate_evaluated_at=inputs.gate_evaluated_at,
        packet_created_at=inputs.packet_created_at,
        handoff_created_at=inputs.handoff_created_at,
    )
    hashes = dict(prior.hashes)
    _bind_hashes(
        repository,
        (blind.contract, blind.compilation_receipt, blind.gate_a, blind.blind_packet, *blind.transitions),
        hashes,
    )
    state = CoordinatorState(
        stage=OperationalStage.BLIND_PACKET_FROZEN,
        run_id=prior.run_id,
        market_id=candidate.market_id,
        created_at=inputs.handoff_created_at,
        hashes=hashes,
        snapshot_ids=prior.snapshot_ids,
        change_event_ids=prior.change_event_ids,
        hit_ids=prior.hit_ids,
        candidate_id=candidate.record_id,
        rule_contract_id=blind.contract.record_id,
        compilation_receipt_id=blind.compilation_receipt.record_id,
        gate_a_id=blind.gate_a.record_id,
        blind_packet_id=blind.blind_packet.record_id,
        blind_packet_manifest=_manifest_payload(blind.packet_manifest),
        transition_ids=tuple(item.record_id for item in blind.transitions),
        blind_transition_count=len(blind.transitions),
    )
    seal_state(artifact_root, state)
    return BlindStageResult(state, blind)


# --- Stage 4: Blind result import + FORMAL_REVIEW outbox append ---------------


@dataclass(frozen=True)
class BlindResumeStageInputs:
    result_locator: str
    receipt_locator: str
    source_contents: Mapping[str, bytes]
    imported_at: datetime
    demand_requested_at: datetime
    demand_valid_until: datetime
    max_staleness_seconds: int
    target_sizes: tuple[Decimal, ...]


@dataclass(frozen=True)
class BlindResumeStageResult:
    state: CoordinatorState | None
    outcome: BlindResumeOutcome
    outbox_receipt: DemandOutboxAppendReceipt | None


def run_blind_resume_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: BlindResumeStageInputs,
) -> BlindResumeStageResult:
    prior = _require_stage(artifact_root, OperationalStage.BLIND_PACKET_FROZEN)
    stage = _load_blind_stage(repository, prior)
    snapshots = [
        _contract(repository, prior, record_id, MarketSnapshot)
        for record_id in prior.snapshot_ids
        if record_id in prior.hashes
    ]
    market_snapshots = [item for item in snapshots if item.identity.market_id == prior.market_id]
    if not market_snapshots:
        raise CoordinatorBlocked("state does not bind a snapshot for the candidate market")
    identity = market_snapshots[-1].identity
    try:
        outcome = resume_blind_result(
            repository=repository,
            stage=stage,
            artifact_root=Path(artifact_root),
            result_locator=inputs.result_locator,
            receipt_locator=inputs.receipt_locator,
            source_contents=inputs.source_contents,
            imported_at=inputs.imported_at,
            identity=identity,
            demand_requested_at=inputs.demand_requested_at,
            demand_valid_until=inputs.demand_valid_until,
            max_staleness_seconds=inputs.max_staleness_seconds,
            target_sizes=inputs.target_sizes,
        )
    except (ReviewPipelineBlocked, HandoffConflictError) as error:
        raise CoordinatorBlocked(f"Blind result import was blocked: {error}") from error
    if outcome.accepted is None:
        # A quarantined/rejected Blind result must never create a
        # FORMAL_REVIEW demand or touch the owner outbox.
        return BlindResumeStageResult(None, outcome, None)
    accepted = outcome.accepted
    bundle = build_owner_capture_demands(accepted.demand)
    outbox_receipt = append_owner_demand_bundle(
        Path(artifact_root),
        bundle=bundle,
        now=accepted.demand.requested_at,
    )
    hashes = dict(prior.hashes)
    _bind_hashes(
        repository,
        (
            outcome.import_outcome.submitted_artifact,
            outcome.import_outcome.receipt,
            accepted.result,
            accepted.demand,
            accepted.transitions[-1],
        ),
        hashes,
    )
    state = CoordinatorState(
        stage=OperationalStage.BLIND_RESULT_ACCEPTED,
        run_id=prior.run_id,
        market_id=prior.market_id,
        created_at=inputs.demand_requested_at,
        hashes=hashes,
        snapshot_ids=prior.snapshot_ids,
        change_event_ids=prior.change_event_ids,
        hit_ids=prior.hit_ids,
        candidate_id=prior.candidate_id,
        rule_contract_id=prior.rule_contract_id,
        compilation_receipt_id=prior.compilation_receipt_id,
        gate_a_id=prior.gate_a_id,
        blind_packet_id=prior.blind_packet_id,
        blind_packet_manifest=prior.blind_packet_manifest,
        blind_handoff_receipt=_handoff_payload(accepted.handoff_receipt),
        blind_result_id=accepted.result.record_id,
        blind_import_receipt_id=accepted.import_receipt.record_id,
        demand_id=accepted.demand.record_id,
        outbox_bundle_id=outbox_receipt.bundle_id,
        transition_ids=tuple(item.record_id for item in accepted.transitions),
        blind_transition_count=prior.blind_transition_count,
        accepted_transition_count=len(accepted.transitions),
    )
    seal_state(artifact_root, state)
    return BlindResumeStageResult(state, outcome, outbox_receipt)


# --- Stage 5: owner book bridge + Market packet export -------------------------


@dataclass(frozen=True)
class BookStageInputs:
    owner_receipt: Mapping[str, Any]
    yes_submission: OwnerBookLegSubmission
    no_submission: OwnerBookLegSubmission
    received_at: datetime
    packet_created_at: datetime
    handoff_created_at: datetime


@dataclass(frozen=True)
class BookStageResult:
    state: CoordinatorState | None
    bridge: BookBridgeResult
    build: MarketBuildOutcome | None


def _require_outbox_line(artifact_root: Path, bundle_id: str | None) -> None:
    """Fail closed unless the sealed demand bundle has an outbox line.

    The demand row is persisted before the outbox append; if the append
    never happened (budget/corruption), the book stage must not advance on
    an unannounced demand.
    """

    if not bundle_id:
        raise CoordinatorBlocked("state does not carry the demand outbox bundle id")
    try:
        raw = _read_allowed(Path(artifact_root), DEMAND_OUTBOX_LOCATOR)
    except (OSError, ValueError) as error:
        raise CoordinatorBlocked(f"owner demand outbox is unreadable: {error}") from error
    for line in raw.decode("utf-8", errors="strict").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise CoordinatorBlocked(f"owner demand outbox has a corrupt line: {error}") from error
        if isinstance(payload, Mapping) and payload.get("bundle_id") == bundle_id:
            return
    raise CoordinatorBlocked(
        f"demand bundle {bundle_id} was never announced in the owner outbox"
    )


def run_book_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: BookStageInputs,
) -> BookStageResult:
    prior = _require_stage(artifact_root, OperationalStage.BLIND_RESULT_ACCEPTED)
    _require_outbox_line(Path(artifact_root), prior.outbox_bundle_id)
    accepted_stage = _load_accepted_stage(repository, prior)
    demand = accepted_stage.demand
    bundle: OwnerDemandBundle = build_owner_capture_demands(demand)
    bridge = bridge_owner_books(
        demand=demand,
        owner_demands=bundle,
        owner_receipt=inputs.owner_receipt,
        yes_submission=inputs.yes_submission,
        no_submission=inputs.no_submission,
        received_at=inputs.received_at,
    )
    if not bridge.accepted or bridge.yes_artifact is None or bridge.no_artifact is None:
        return BookStageResult(None, bridge, None)
    try:
        build = accept_formal_book(
            repository=repository,
            stage=accepted_stage,
            yes_artifact=bridge.yes_artifact,
            no_artifact=bridge.no_artifact,
            received_at=inputs.received_at,
            artifact_root=Path(artifact_root),
            packet_locator=MARKET_PACKET_LOCATOR,
            manifest_locator=MARKET_MANIFEST_LOCATOR,
            packet_created_at=inputs.packet_created_at,
            handoff_created_at=inputs.handoff_created_at,
        )
    except (ReviewPipelineBlocked, HandoffConflictError) as error:
        raise CoordinatorBlocked(f"Market packet export was blocked: {error}") from error
    if build.accepted is None:
        return BookStageResult(None, bridge, build)
    market_stage = build.accepted
    hashes = dict(prior.hashes)
    _bind_hashes(
        repository,
        (
            market_stage.normalization.snapshot,
            market_stage.normalization.receipt,
            market_stage.market_packet,
            *market_stage.transitions,
        ),
        hashes,
    )
    state = CoordinatorState(
        stage=OperationalStage.MARKET_PACKET_FROZEN,
        run_id=prior.run_id,
        market_id=prior.market_id,
        created_at=inputs.handoff_created_at,
        hashes=hashes,
        snapshot_ids=prior.snapshot_ids,
        change_event_ids=prior.change_event_ids,
        hit_ids=prior.hit_ids,
        candidate_id=prior.candidate_id,
        rule_contract_id=prior.rule_contract_id,
        compilation_receipt_id=prior.compilation_receipt_id,
        gate_a_id=prior.gate_a_id,
        blind_packet_id=prior.blind_packet_id,
        blind_packet_manifest=prior.blind_packet_manifest,
        blind_handoff_receipt=prior.blind_handoff_receipt,
        blind_result_id=prior.blind_result_id,
        blind_import_receipt_id=prior.blind_import_receipt_id,
        demand_id=prior.demand_id,
        outbox_bundle_id=prior.outbox_bundle_id,
        book_receipt_id=market_stage.normalization.receipt.record_id,
        book_snapshot_id=market_stage.normalization.snapshot.record_id if market_stage.normalization.snapshot else None,
        market_packet_id=market_stage.market_packet.record_id,
        market_packet_manifest=_manifest_payload(market_stage.packet_manifest),
        market_handoff_receipt=None,
        transition_ids=tuple(item.record_id for item in market_stage.transitions),
        blind_transition_count=prior.blind_transition_count,
        accepted_transition_count=prior.accepted_transition_count,
        market_transition_count=len(market_stage.transitions),
    )
    seal_state(artifact_root, state)
    return BookStageResult(state, bridge, build)


# --- Stage 6: Market result import + Rule B + no-order ledger ------------------


@dataclass(frozen=True)
class MarketResumeStageInputs:
    result_locator: str
    receipt_locator: str
    source_contents: Mapping[str, bytes]
    imported_at: datetime
    gate_b_evaluated_at: datetime
    decision_as_of: datetime
    rank_config: RankConfig
    rule_risk_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketResumeStageResult:
    state: CoordinatorState | None
    outcome: MarketResumeOutcome


def run_market_resume_stage(
    repository: AlphaRepository,
    artifact_root: Path,
    inputs: MarketResumeStageInputs,
) -> MarketResumeStageResult:
    prior = _require_stage(artifact_root, OperationalStage.MARKET_PACKET_FROZEN)
    accepted_stage = _load_accepted_stage(repository, prior)
    snapshot = _contract(repository, prior, prior.book_snapshot_id or "", OrderbookSnapshot)
    book_receipt = _contract(repository, prior, prior.book_receipt_id or "", BookCaptureReceipt)
    market_packet = _contract(repository, prior, prior.market_packet_id or "", MarketResearchPacket)
    assert prior.market_packet_manifest is not None
    transitions = tuple(
        _contract(repository, prior, record_id, CandidateTransition)
        for record_id in prior.transition_ids[: prior.market_transition_count]
    )
    market_stage = MarketReviewStage(
        blind=accepted_stage,
        normalization=PairedBookNormalization(snapshot, book_receipt),
        market_packet=market_packet,
        packet_manifest=_manifest_from_payload(prior.market_packet_manifest),
        transitions=transitions,
    )
    try:
        outcome = resume_market_result(
            repository=repository,
            stage=market_stage,
            artifact_root=Path(artifact_root),
            result_locator=inputs.result_locator,
            receipt_locator=inputs.receipt_locator,
            source_contents=inputs.source_contents,
            imported_at=inputs.imported_at,
            gate_b_evaluated_at=inputs.gate_b_evaluated_at,
            decision_as_of=inputs.decision_as_of,
            rank_config=inputs.rank_config,
            rule_risk_reasons=inputs.rule_risk_reasons,
        )
    except (ReviewPipelineBlocked, HandoffConflictError) as error:
        raise CoordinatorBlocked(f"Market result import was blocked: {error}") from error
    final = outcome.final
    if final is None:
        return MarketResumeStageResult(None, outcome)
    if final.ranked is None:
        # Rule B BLOCK: the gate receipts are persisted but no decision or
        # prediction ledger row may exist.
        return MarketResumeStageResult(None, outcome)
    if final.ranked.decision.execution != "NO_ORDER":
        raise CoordinatorBlocked("coordinator refuses any execution capability")
    hashes = dict(prior.hashes)
    _bind_hashes(
        repository,
        (
            outcome.import_outcome.submitted_artifact,
            outcome.import_outcome.receipt,
            final.result,
            final.gate_b,
            final.ranked.decision,
            final.ranked.prediction,
            *final.transitions,
        ),
        hashes,
    )
    state = CoordinatorState(
        stage=OperationalStage.FINALIZED,
        run_id=prior.run_id,
        market_id=prior.market_id,
        created_at=inputs.decision_as_of,
        hashes=hashes,
        snapshot_ids=prior.snapshot_ids,
        change_event_ids=prior.change_event_ids,
        hit_ids=prior.hit_ids,
        candidate_id=prior.candidate_id,
        rule_contract_id=prior.rule_contract_id,
        compilation_receipt_id=prior.compilation_receipt_id,
        gate_a_id=prior.gate_a_id,
        blind_packet_id=prior.blind_packet_id,
        blind_packet_manifest=prior.blind_packet_manifest,
        blind_handoff_receipt=prior.blind_handoff_receipt,
        blind_result_id=prior.blind_result_id,
        blind_import_receipt_id=prior.blind_import_receipt_id,
        demand_id=prior.demand_id,
        outbox_bundle_id=prior.outbox_bundle_id,
        book_receipt_id=prior.book_receipt_id,
        book_snapshot_id=prior.book_snapshot_id,
        market_packet_id=prior.market_packet_id,
        market_packet_manifest=prior.market_packet_manifest,
        market_result_id=final.result.record_id,
        market_import_receipt_id=final.import_receipt.record_id,
        gate_b_id=final.gate_b.record_id,
        decision_id=final.ranked.decision.record_id,
        prediction_id=final.ranked.prediction.record_id,
        transition_ids=tuple(item.record_id for item in final.transitions),
        blind_transition_count=prior.blind_transition_count,
        accepted_transition_count=prior.accepted_transition_count,
        market_transition_count=prior.market_transition_count,
    )
    seal_state(artifact_root, state)
    return MarketResumeStageResult(state, outcome)
