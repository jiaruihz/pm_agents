"""Fail-closed reducer for the append-only Candidate lifecycle.

This module deliberately owns no persistence or scheduling.  Callers supply a
seed ``CandidateCard`` and its ordered transition log; the reducer returns a
fully deterministic immutable projection.
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from pydantic import field_validator

from ..contracts import (
    AlphaContract,
    CandidateCard,
    CandidateEventType,
    CandidateState,
    CandidateTransition,
)
from ..contracts.base import ensure_utc


class CandidateLifecycleError(ValueError):
    """Raised when an event stream cannot be safely replayed."""


# This is the only normal (state-changing) path.  Rejection is intentionally
# available at each review checkpoint, but never permits a later resurrection.
_NORMAL_TRANSITION_MATRIX: dict[CandidateState, frozenset[CandidateState]] = {
    CandidateState.RECALLED: frozenset(
        {CandidateState.CANDIDATE_MERGED, CandidateState.REJECTED}
    ),
    CandidateState.CANDIDATE_MERGED: frozenset(
        {
            CandidateState.RULE_A_PASSED,
            CandidateState.RULE_A_BLOCKED,
            CandidateState.REJECTED,
        }
    ),
    CandidateState.RULE_A_PASSED: frozenset(
        {CandidateState.BLIND_PROJECTION_FROZEN, CandidateState.REJECTED}
    ),
    CandidateState.RULE_A_BLOCKED: frozenset({CandidateState.REJECTED}),
    CandidateState.BLIND_PROJECTION_FROZEN: frozenset(
        {CandidateState.BLIND_PACKET_FROZEN, CandidateState.REJECTED}
    ),
    CandidateState.BLIND_PACKET_FROZEN: frozenset(
        {CandidateState.BLIND_RESULT_ACCEPTED, CandidateState.REJECTED}
    ),
    CandidateState.BLIND_RESULT_ACCEPTED: frozenset(
        {CandidateState.BOOK_SNAPSHOT_ACCEPTED, CandidateState.REJECTED}
    ),
    CandidateState.BOOK_SNAPSHOT_ACCEPTED: frozenset(
        {CandidateState.MARKET_PACKET_FROZEN, CandidateState.REJECTED}
    ),
    CandidateState.MARKET_PACKET_FROZEN: frozenset(
        {CandidateState.MARKET_RESULT_ACCEPTED, CandidateState.REJECTED}
    ),
    CandidateState.MARKET_RESULT_ACCEPTED: frozenset(
        {
            CandidateState.RULE_B_PASSED,
            CandidateState.RULE_B_RISK,
            CandidateState.RULE_B_BLOCKED,
            CandidateState.REJECTED,
        }
    ),
    CandidateState.RULE_B_PASSED: frozenset(
        {CandidateState.RANKED, CandidateState.REJECTED}
    ),
    CandidateState.RULE_B_RISK: frozenset(
        {
            CandidateState.RANKED,
            CandidateState.WATCHLISTED,
            CandidateState.REJECTED,
        }
    ),
    CandidateState.RULE_B_BLOCKED: frozenset({CandidateState.REJECTED}),
    CandidateState.RANKED: frozenset(
        {
            CandidateState.WATCHLISTED,
            CandidateState.SIMULATION_RECORDED,
            CandidateState.REJECTED,
        }
    ),
    CandidateState.WATCHLISTED: frozenset(
        {CandidateState.SIMULATION_RECORDED, CandidateState.REJECTED}
    ),
    CandidateState.REJECTED: frozenset(),
    CandidateState.SIMULATION_RECORDED: frozenset(),
}
NORMAL_TRANSITION_MATRIX: Mapping[CandidateState, frozenset[CandidateState]] = (
    MappingProxyType(_NORMAL_TRANSITION_MATRIX)
)

_INVALIDATION_EVENTS = frozenset(
    {
        CandidateEventType.RECALL_EXPIRED,
        CandidateEventType.EVIDENCE_STALE,
        CandidateEventType.RESEARCH_REFRESH_REQUIRED,
        CandidateEventType.RULE_REVISION_INVALIDATED,
        CandidateEventType.BOOK_REFRESH_REQUIRED,
    }
)
_REFRESH_EVENTS = frozenset(
    {
        CandidateEventType.EVIDENCE_STALE,
        CandidateEventType.RESEARCH_REFRESH_REQUIRED,
        CandidateEventType.RULE_REVISION_INVALIDATED,
        CandidateEventType.BOOK_REFRESH_REQUIRED,
    }
)
_TERMINAL_EVENTS = frozenset(
    {
        CandidateEventType.MARKET_CLOSED,
        CandidateEventType.RESOLVED,
        CandidateEventType.SUPERSEDED,
        CandidateEventType.ARCHIVED,
    }
)
_TERMINAL_EVENT_MATRIX: Mapping[
    CandidateEventType | None, frozenset[CandidateEventType]
] = MappingProxyType(
    {
        None: _TERMINAL_EVENTS,
        CandidateEventType.MARKET_CLOSED: frozenset(
            {
                CandidateEventType.RESOLVED,
                CandidateEventType.SUPERSEDED,
                CandidateEventType.ARCHIVED,
            }
        ),
        CandidateEventType.RESOLVED: frozenset({CandidateEventType.ARCHIVED}),
        CandidateEventType.SUPERSEDED: frozenset({CandidateEventType.ARCHIVED}),
        CandidateEventType.ARCHIVED: frozenset(),
    }
)


class CandidateLifecycleProjection(AlphaContract):
    """Immutable current view reconstructed from a seed and event log."""

    candidate_id: str
    initial_state: CandidateState
    current_state: CandidateState
    applied_transition_ids: tuple[str, ...]
    event_count: int
    active: bool
    invalidated: bool
    refresh_required: bool
    terminal: bool
    archived: bool
    invalidation_events: tuple[CandidateEventType, ...] = ()
    terminal_event: CandidateEventType | None = None
    last_at: datetime | None = None

    @field_validator("last_at")
    @classmethod
    def last_at_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


def _non_state_event_is_well_formed(
    transition: CandidateTransition,
    current_state: CandidateState,
) -> None:
    if transition.from_state != current_state or transition.to_state != current_state:
        raise CandidateLifecycleError(
            f"{transition.event_type.value} must retain current state {current_state.value}"
        )


def _apply_normal_transition(
    transition: CandidateTransition,
    current_state: CandidateState,
    *,
    invalidated: bool,
    terminal: bool,
) -> CandidateState:
    if invalidated or terminal:
        raise CandidateLifecycleError("cannot advance an invalidated or terminal candidate")
    if transition.from_state != current_state:
        raise CandidateLifecycleError(
            f"transition from_state {transition.from_state.value} does not match "
            f"current state {current_state.value}"
        )
    if transition.to_state not in NORMAL_TRANSITION_MATRIX[current_state]:
        raise CandidateLifecycleError(
            f"illegal normal transition {current_state.value} -> {transition.to_state.value}"
        )
    return transition.to_state


def reduce_candidate_lifecycle(
    candidate: CandidateCard,
    transitions: tuple[CandidateTransition, ...] | list[CandidateTransition],
) -> CandidateLifecycleProjection:
    """Replay one append-only Candidate stream without sorting or side effects.

    The supplied order is itself part of the evidence.  Distinct transitions at
    the same timestamp are rejected as unresolved concurrency; an exact replay
    of a previously seen record id is ignored idempotently.
    """

    current_state = candidate.state
    invalidated = False
    refresh_required = False
    terminal = False
    archived = False
    terminal_event: CandidateEventType | None = None
    invalidation_events: list[CandidateEventType] = []
    applied_ids: list[str] = []
    seen_record_hashes: dict[str, str] = {}
    last_at: datetime | None = None

    for transition in transitions:
        if transition.candidate_id != candidate.candidate_id:
            raise CandidateLifecycleError("transition candidate_id does not match seed")

        transition_hash = transition.canonical_sha256
        prior_hash = seen_record_hashes.get(transition.record_id)
        if prior_hash is not None:
            if prior_hash != transition_hash:
                raise CandidateLifecycleError(
                    "same transition record_id has conflicting append-only content"
                )
            continue
        seen_record_hashes[transition.record_id] = transition_hash

        if transition.at < candidate.selected_at:
            raise CandidateLifecycleError("transition precedes candidate selection")
        if last_at is not None and transition.at <= last_at:
            raise CandidateLifecycleError(
                "distinct transitions must have strictly increasing timestamps"
            )

        if transition.event_type == CandidateEventType.STATE_TRANSITION:
            current_state = _apply_normal_transition(
                transition,
                current_state,
                invalidated=invalidated,
                terminal=terminal,
            )
        elif transition.event_type in _INVALIDATION_EVENTS:
            if terminal:
                raise CandidateLifecycleError("cannot invalidate a terminal candidate")
            _non_state_event_is_well_formed(transition, current_state)
            invalidated = True
            refresh_required = refresh_required or transition.event_type in _REFRESH_EVENTS
            if transition.event_type not in invalidation_events:
                invalidation_events.append(transition.event_type)
        elif transition.event_type in _TERMINAL_EVENTS:
            _non_state_event_is_well_formed(transition, current_state)
            if transition.event_type not in _TERMINAL_EVENT_MATRIX[terminal_event]:
                previous = terminal_event.value if terminal_event is not None else "NONE"
                raise CandidateLifecycleError(
                    f"illegal terminal event sequence {previous} -> {transition.event_type.value}"
                )
            terminal = True
            terminal_event = transition.event_type
            archived = transition.event_type == CandidateEventType.ARCHIVED
        else:  # defensive guard for future contract enum extensions
            raise CandidateLifecycleError(f"unsupported event type: {transition.event_type}")

        applied_ids.append(transition.record_id)
        last_at = transition.at

    return CandidateLifecycleProjection(
        candidate_id=candidate.candidate_id,
        initial_state=candidate.state,
        current_state=current_state,
        applied_transition_ids=tuple(applied_ids),
        event_count=len(applied_ids),
        active=(
            not invalidated
            and not terminal
            and bool(NORMAL_TRANSITION_MATRIX[current_state])
        ),
        invalidated=invalidated,
        refresh_required=refresh_required,
        terminal=terminal,
        archived=archived,
        invalidation_events=tuple(invalidation_events),
        terminal_event=terminal_event,
        last_at=last_at,
    )
