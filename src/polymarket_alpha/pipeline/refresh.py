"""Pure, deterministic refresh planning for repeated offline Alpha scans.

The planner turns new scan facts into append-only, non-state Candidate
transitions.  It deliberately has no scheduler, repository, transport, or
packet-writing capability.  A pre-Blind late hit is returned as an explicit
refresh-input action instead: changing the Candidate revision is owned by the
recall aggregator, not by the lifecycle log.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import field_validator, model_validator

from ..contracts import (
    AlphaContract,
    CandidateCard,
    CandidateEventType,
    CandidateTransition,
    MarketSnapshot,
    MarketStatus,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from ..protocol import CandidateLifecycleError, reduce_candidate_lifecycle
from ..recall.aggregate import LateHitImpact, LateHitImpactType


class ScanRefreshPlanningError(ValueError):
    """Raised when a scan fact cannot safely become an append-only event."""


class RefreshSignalKind(StrEnum):
    RECALL_EXPIRED = "RECALL_EXPIRED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    BOOK_REFRESH_REQUIRED = "BOOK_REFRESH_REQUIRED"


_SIGNAL_EVENT_TYPES = {
    RefreshSignalKind.RECALL_EXPIRED: CandidateEventType.RECALL_EXPIRED,
    RefreshSignalKind.EVIDENCE_STALE: CandidateEventType.EVIDENCE_STALE,
    RefreshSignalKind.BOOK_REFRESH_REQUIRED: CandidateEventType.BOOK_REFRESH_REQUIRED,
}
_EVENT_ORDER = {
    CandidateEventType.RECALL_EXPIRED: 10,
    CandidateEventType.EVIDENCE_STALE: 20,
    CandidateEventType.RULE_REVISION_INVALIDATED: 30,
    CandidateEventType.BOOK_REFRESH_REQUIRED: 40,
    CandidateEventType.RESEARCH_REFRESH_REQUIRED: 50,
    CandidateEventType.MARKET_CLOSED: 60,
    CandidateEventType.RESOLVED: 70,
}


class RefreshSignal(AlphaContract):
    """One frozen source fact that requires a non-state lifecycle event."""

    kind: RefreshSignalKind
    artifact_id: str
    artifact_sha256: str
    effective_at: datetime
    reason: str

    @field_validator("artifact_id", "reason")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("refresh signal text must not be blank")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def artifact_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("effective_at")
    @classmethod
    def effective_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PreBlindRefreshInput(AlphaContract):
    """An explicit instruction to rebuild mutable pre-Blind Candidate input."""

    candidate_id: str
    recall_hit_ids: tuple[str, ...]
    input_sha256: str

    @field_validator("recall_hit_ids")
    @classmethod
    def recall_hit_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)) or value != tuple(sorted(value)):
            raise ValueError("recall_hit_ids must be non-empty, unique and sorted")
        return value

    @field_validator("input_sha256")
    @classmethod
    def input_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class ScanRefreshPlan(AlphaContract):
    """Deterministic append-only plan; callers persist it, this module does not."""

    request_sha256: str
    transitions: tuple[CandidateTransition, ...]
    pre_blind_refresh_inputs: tuple[PreBlindRefreshInput, ...]
    projection_state: str

    @field_validator("request_sha256")
    @classmethod
    def request_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class ScanRefreshRequest(AlphaContract):
    """All frozen inputs needed to plan the next scan's lifecycle additions."""

    candidate: CandidateCard
    prior_transitions: tuple[CandidateTransition, ...] = ()
    as_of: datetime
    signals: tuple[RefreshSignal, ...] = ()
    late_hit_impacts: tuple[LateHitImpact, ...] = ()
    prior_snapshot: MarketSnapshot | None = None
    current_snapshot: MarketSnapshot | None = None

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def scope_is_consistent(self) -> "ScanRefreshRequest":
        if self.as_of < self.candidate.selected_at:
            raise ValueError("scan as_of cannot precede candidate selection")
        for impact in self.late_hit_impacts:
            if impact.candidate_id != self.candidate.candidate_id:
                raise ValueError("late hit impact candidate_id does not match request candidate")
        for snapshot in (self.prior_snapshot, self.current_snapshot):
            if snapshot is not None and snapshot.identity.market_id != self.candidate.market_id:
                raise ValueError("snapshot market_id does not match request candidate")
        if self.current_snapshot is not None and self.current_snapshot.status == MarketStatus.SUPERSEDED:
            raise ValueError("SUPERSEDED is unreachable until a source-of-truth is released")
        if self.prior_snapshot is not None and self.current_snapshot is not None:
            if self.prior_snapshot.source_observed_at > self.current_snapshot.source_observed_at:
                raise ValueError("current snapshot cannot precede prior snapshot")
        return self


class _PendingEvent(AlphaContract):
    event_type: CandidateEventType
    artifact_ids: tuple[str, ...]
    trigger_sha256: str
    effective_at: datetime
    reason: str
    input_sha256: str

    @field_validator("artifact_ids")
    @classmethod
    def artifact_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)) or value != tuple(sorted(value)):
            raise ValueError("artifact_ids must be non-empty, unique and sorted")
        return value


def _event_identity(candidate_id: str, event: _PendingEvent) -> str:
    return stable_record_id(
        "candidate_transition",
        candidate_id,
        event.event_type.value,
        event.artifact_ids,
        event.trigger_sha256,
        event.effective_at,
        event.reason,
        event.input_sha256,
    )


def _pending_from_signal(signal: RefreshSignal) -> _PendingEvent:
    event_type = _SIGNAL_EVENT_TYPES[signal.kind]
    payload = {
        "kind": signal.kind,
        "artifact_id": signal.artifact_id,
        "artifact_sha256": signal.artifact_sha256,
        "effective_at": signal.effective_at,
        "reason": signal.reason,
    }
    return _PendingEvent(
        event_type=event_type,
        artifact_ids=(signal.artifact_id,),
        trigger_sha256=signal.artifact_sha256,
        effective_at=signal.effective_at,
        reason=signal.reason,
        input_sha256=content_sha256(payload),
    )


def _pending_from_snapshots(request: ScanRefreshRequest) -> tuple[_PendingEvent, ...]:
    current = request.current_snapshot
    if current is None:
        return ()
    events: list[_PendingEvent] = []
    previous = request.prior_snapshot
    if previous is not None and previous.rule_hash != current.rule_hash:
        payload = {
            "prior_snapshot_id": previous.record_id,
            "prior_rule_hash": previous.rule_hash,
            "current_snapshot_id": current.record_id,
            "current_rule_hash": current.rule_hash,
            "effective_at": current.source_observed_at,
        }
        events.append(
            _PendingEvent(
                event_type=CandidateEventType.RULE_REVISION_INVALIDATED,
                artifact_ids=(current.record_id,),
                trigger_sha256=current.canonical_sha256,
                effective_at=current.source_observed_at,
                reason="market rule revision changed",
                input_sha256=content_sha256(payload),
            )
        )
    if current.status == MarketStatus.CLOSED:
        events.append(
            _PendingEvent(
                event_type=CandidateEventType.MARKET_CLOSED,
                artifact_ids=(current.record_id,),
                trigger_sha256=current.canonical_sha256,
                effective_at=current.source_observed_at,
                reason="market status is CLOSED",
                input_sha256=content_sha256(
                    {"snapshot_id": current.record_id, "status": current.status, "effective_at": current.source_observed_at}
                ),
            )
        )
    elif current.status == MarketStatus.RESOLVED:
        events.append(
            _PendingEvent(
                event_type=CandidateEventType.RESOLVED,
                artifact_ids=(current.record_id,),
                trigger_sha256=current.canonical_sha256,
                effective_at=current.source_observed_at,
                reason="market status is RESOLVED",
                input_sha256=content_sha256(
                    {"snapshot_id": current.record_id, "status": current.status, "effective_at": current.source_observed_at}
                ),
            )
        )
    return tuple(events)


def _pending_from_late_impact(impact: LateHitImpact, as_of: datetime) -> _PendingEvent | None:
    if impact.impact in {LateHitImpactType.NOT_APPLICABLE, LateHitImpactType.NO_BLIND_INPUT_CHANGE, LateHitImpactType.PRE_BLIND_REFRESH}:
        return None
    if impact.impact == LateHitImpactType.IMPACT_UNDETERMINED:
        raise ScanRefreshPlanningError("late hit impact is undetermined; cannot advance safely")
    # The aggregator intentionally marks this unsafe to *advance* because a
    # frozen packet must not be silently changed.  Emitting the explicit
    # invalidation is nevertheless safe and is precisely the required action.
    if impact.impact != LateHitImpactType.RESEARCH_REFRESH_REQUIRED:
        raise ScanRefreshPlanningError("late hit impact cannot safely become a refresh event")
    if impact.required_event != CandidateEventType.RESEARCH_REFRESH_REQUIRED:
        raise ScanRefreshPlanningError("late hit impact has an invalid required lifecycle event")
    if not impact.new_recall_hit_ids or impact.prior_projection_input_sha256 is None or impact.merged_projection_input_sha256 is None:
        raise ScanRefreshPlanningError("research refresh requires complete late-hit projection lineage")
    payload = {
        "candidate_id": impact.candidate_id,
        "new_recall_hit_ids": tuple(sorted(impact.new_recall_hit_ids)),
        "prior_projection_input_sha256": impact.prior_projection_input_sha256,
        "merged_projection_input_sha256": impact.merged_projection_input_sha256,
    }
    return _PendingEvent(
        event_type=CandidateEventType.RESEARCH_REFRESH_REQUIRED,
        artifact_ids=tuple(sorted(impact.new_recall_hit_ids)),
        trigger_sha256=impact.merged_projection_input_sha256,
        effective_at=as_of,
        reason="late recall changed frozen Blind input",
        input_sha256=content_sha256(payload),
    )


def _same_logical_event(transition: CandidateTransition, event: _PendingEvent) -> bool:
    return (
        transition.event_type == event.event_type
        and transition.reason == event.reason
        and transition.related_artifact_ids == event.artifact_ids
        and transition.input_hash == event.input_sha256
    )


def _preblind_actions(impacts: tuple[LateHitImpact, ...]) -> tuple[PreBlindRefreshInput, ...]:
    actions: dict[str, PreBlindRefreshInput] = {}
    for impact in impacts:
        if impact.impact != LateHitImpactType.PRE_BLIND_REFRESH:
            continue
        if not impact.safe_to_advance or not impact.new_recall_hit_ids:
            raise ScanRefreshPlanningError("pre-Blind refresh requires non-empty safe recall lineage")
        payload = {"candidate_id": impact.candidate_id, "recall_hit_ids": tuple(sorted(impact.new_recall_hit_ids))}
        action = PreBlindRefreshInput(
            candidate_id=impact.candidate_id,
            recall_hit_ids=payload["recall_hit_ids"],
            input_sha256=content_sha256(payload),
        )
        prior = actions.get(action.input_sha256)
        if prior is not None and prior != action:
            raise ScanRefreshPlanningError("same pre-Blind refresh identity has conflicting content")
        actions[action.input_sha256] = action
    return tuple(sorted(actions.values(), key=lambda item: item.input_sha256))


def plan_scan_refresh(request: ScanRefreshRequest) -> ScanRefreshPlan:
    """Plan deterministic invalidation/terminal facts without changing Candidate state.

    ``prior_transitions`` are replayed first.  Input facts older than an
    already-persisted distinct transition cannot be honestly appended and fail
    closed.  Equal-time new facts are assigned deterministic microsecond slots
    while their immutable source ``effective_at`` remains in ``input_hash``.
    """

    try:
        projection = reduce_candidate_lifecycle(request.candidate, request.prior_transitions)
    except CandidateLifecycleError as error:
        raise ScanRefreshPlanningError(f"invalid prior lifecycle stream: {error}") from error

    pending = [_pending_from_signal(item) for item in request.signals]
    pending.extend(_pending_from_snapshots(request))
    for impact in request.late_hit_impacts:
        item = _pending_from_late_impact(impact, request.as_of)
        if item is not None:
            pending.append(item)

    actions = _preblind_actions(request.late_hit_impacts)
    if projection.terminal and any(item.event_type not in {CandidateEventType.MARKET_CLOSED, CandidateEventType.RESOLVED} for item in pending):
        raise ScanRefreshPlanningError("terminal candidate cannot be refreshed")

    unique: dict[str, _PendingEvent] = {}
    existing_by_id = {item.record_id: item for item in request.prior_transitions}
    for event in pending:
        if event.effective_at > request.as_of:
            raise ScanRefreshPlanningError("refresh fact cannot be effective after scan as_of")
        event_id = _event_identity(request.candidate.candidate_id, event)
        prior = unique.get(event_id)
        if prior is not None and prior != event:
            raise ScanRefreshPlanningError("same refresh event identity has conflicting content")
        existing = existing_by_id.get(event_id)
        if existing is not None:
            if not _same_logical_event(existing, event):
                raise ScanRefreshPlanningError("same transition record_id has conflicting append-only content")
            continue
        unique[event_id] = event

    ordered = sorted(
        unique.items(),
        key=lambda item: (
            item[1].effective_at,
            _EVENT_ORDER[item[1].event_type],
            item[1].artifact_ids,
            item[1].input_sha256,
            item[0],
        ),
    )
    existing_last_at = projection.last_at
    last_at = existing_last_at or request.candidate.selected_at
    transitions: list[CandidateTransition] = []
    state = projection.current_state
    for event_id, event in ordered:
        if existing_last_at is not None and event.effective_at <= existing_last_at:
            raise ScanRefreshPlanningError("new refresh fact precedes an existing append-only transition")
        at = event.effective_at if event.effective_at > last_at else last_at + timedelta(microseconds=1)
        transition = CandidateTransition(
            record_id=event_id,
            run_id=stable_record_id("scan_refresh_run", request.candidate.candidate_id, request.as_of),
            created_at=at,
            source="scan_refresh_planner",
            source_version="p0-refresh-v1",
            provenance=(),
            extensions={},
            candidate_id=request.candidate.candidate_id,
            event_type=event.event_type,
            from_state=state,
            to_state=state,
            reason=event.reason,
            actor="scan_refresh_planner",
            at=at,
            related_artifact_ids=event.artifact_ids,
            input_hash=event.input_sha256,
        )
        transitions.append(transition)
        last_at = at

    # Verify generated slots and terminal progression using the released reducer.
    try:
        final = reduce_candidate_lifecycle(request.candidate, (*request.prior_transitions, *transitions))
    except CandidateLifecycleError as error:
        raise ScanRefreshPlanningError(f"planned lifecycle stream is unsafe: {error}") from error
    return ScanRefreshPlan(
        request_sha256=content_sha256(
            {
                "candidate": request.candidate,
                "prior_transitions": request.prior_transitions,
                "as_of": request.as_of,
                "signals": tuple(
                    item
                    for _, item in sorted(
                        {content_sha256(item): item for item in request.signals}.items()
                    )
                ),
                "late_hit_impacts": tuple(
                    item
                    for _, item in sorted(
                        {content_sha256(item): item for item in request.late_hit_impacts}.items()
                    )
                ),
                "prior_snapshot": request.prior_snapshot,
                "current_snapshot": request.current_snapshot,
            }
        ),
        transitions=tuple(transitions),
        pre_blind_refresh_inputs=actions,
        projection_state=final.current_state.value,
    )
