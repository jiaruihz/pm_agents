"""P0-09A Candidate lifecycle reducer evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import (
    CandidateCard,
    CandidateEventType,
    CandidateState,
    CandidateTransition,
    ResearchPriority,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.protocol import (
    CandidateLifecycleError,
    NORMAL_TRANSITION_MATRIX,
    reduce_candidate_lifecycle,
)
from src.polymarket_alpha.security import audit_source_tree


T0 = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)


def _candidate(state: CandidateState = CandidateState.RECALLED) -> CandidateCard:
    candidate_id = stable_record_id("candidate", "market-1", "dedup-1")
    return CandidateCard(
        record_id=stable_record_id("candidate_card", candidate_id, state.value),
        run_id="test-run",
        created_at=T0,
        source="test",
        source_version="v1",
        candidate_id=candidate_id,
        market_id="market-1",
        recall_hit_ids=("recall_hit:one",),
        recall_score=Decimal("1"),
        dedup_group="dedup-1",
        selected_at=T0,
        state=state,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("test",),
    )


def _transition(
    candidate: CandidateCard,
    index: int,
    *,
    event_type: CandidateEventType = CandidateEventType.STATE_TRANSITION,
    from_state: CandidateState | None = None,
    to_state: CandidateState | None = None,
    at: datetime | None = None,
    reason: str = "test transition",
) -> CandidateTransition:
    source_state = from_state or candidate.state
    target_state = to_state or candidate.state
    moment = at or T0 + timedelta(minutes=index)
    return CandidateTransition(
        record_id=stable_record_id(
            "candidate_transition", candidate.candidate_id, index, event_type.value, reason
        ),
        run_id="test-run",
        created_at=moment,
        source="test",
        source_version="v1",
        candidate_id=candidate.candidate_id,
        event_type=event_type,
        from_state=source_state,
        to_state=target_state,
        reason=reason,
        actor="test",
        at=moment,
        related_artifact_ids=(f"artifact:{index}",),
        input_hash=content_sha256({"index": index, "reason": reason}),
    )


def test_every_allowed_normal_edge_is_reduced() -> None:
    for origin, destinations in NORMAL_TRANSITION_MATRIX.items():
        candidate = _candidate(origin)
        for destination in destinations:
            transition = _transition(
                candidate,
                1,
                from_state=origin,
                to_state=destination,
                reason=f"{origin.value}->{destination.value}",
            )
            projection = reduce_candidate_lifecycle(candidate, (transition,))
            assert projection.current_state == destination
            assert projection.active is bool(NORMAL_TRANSITION_MATRIX[destination])


def test_complete_normal_path_reaches_simulation() -> None:
    states = (
        CandidateState.RECALLED,
        CandidateState.CANDIDATE_MERGED,
        CandidateState.RULE_A_PASSED,
        CandidateState.BLIND_PROJECTION_FROZEN,
        CandidateState.BLIND_PACKET_FROZEN,
        CandidateState.BLIND_RESULT_ACCEPTED,
        CandidateState.BOOK_SNAPSHOT_ACCEPTED,
        CandidateState.MARKET_PACKET_FROZEN,
        CandidateState.MARKET_RESULT_ACCEPTED,
        CandidateState.RULE_B_PASSED,
        CandidateState.RANKED,
        CandidateState.SIMULATION_RECORDED,
    )
    candidate = _candidate(states[0])
    events = tuple(
        _transition(candidate, index, from_state=before, to_state=after)
        for index, (before, after) in enumerate(zip(states, states[1:]), start=1)
    )
    projection = reduce_candidate_lifecycle(candidate, events)
    assert projection.current_state == CandidateState.SIMULATION_RECORDED
    assert projection.active is False
    assert projection.event_count == len(events)


@pytest.mark.parametrize(
    "event_type",
    (
        CandidateEventType.RECALL_EXPIRED,
        CandidateEventType.EVIDENCE_STALE,
        CandidateEventType.RESEARCH_REFRESH_REQUIRED,
        CandidateEventType.RULE_REVISION_INVALIDATED,
        CandidateEventType.BOOK_REFRESH_REQUIRED,
    ),
)
def test_invalidation_events_are_append_only_and_stop_advancement(
    event_type: CandidateEventType,
) -> None:
    candidate = _candidate(CandidateState.CANDIDATE_MERGED)
    invalidation = _transition(candidate, 1, event_type=event_type)
    projection = reduce_candidate_lifecycle(candidate, (invalidation,))
    assert projection.current_state == CandidateState.CANDIDATE_MERGED
    assert projection.invalidated is True
    assert projection.active is False
    assert projection.refresh_required is (event_type != CandidateEventType.RECALL_EXPIRED)
    assert projection.invalidation_events == (event_type,)

    later_advance = _transition(
        candidate,
        2,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.RULE_A_PASSED,
    )
    with pytest.raises(CandidateLifecycleError, match="invalidated"):
        reduce_candidate_lifecycle(candidate, (invalidation, later_advance))


def test_late_recall_expiry_invalidates_a_previously_advanced_candidate() -> None:
    candidate = _candidate()
    merge = _transition(
        candidate,
        1,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    expired = _transition(
        candidate,
        2,
        event_type=CandidateEventType.RECALL_EXPIRED,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    projection = reduce_candidate_lifecycle(candidate, (merge, expired))
    assert projection.current_state == CandidateState.CANDIDATE_MERGED
    assert projection.invalidated is True


@pytest.mark.parametrize(
    "event_type",
    (
        CandidateEventType.MARKET_CLOSED,
        CandidateEventType.RESOLVED,
        CandidateEventType.SUPERSEDED,
        CandidateEventType.ARCHIVED,
    ),
)
def test_terminal_events_are_explicit_and_stop_normal_advancement(
    event_type: CandidateEventType,
) -> None:
    candidate = _candidate(CandidateState.WATCHLISTED)
    terminal = _transition(candidate, 1, event_type=event_type)
    projection = reduce_candidate_lifecycle(candidate, (terminal,))
    assert projection.terminal is True
    assert projection.active is False
    assert projection.archived is (event_type == CandidateEventType.ARCHIVED)
    assert projection.terminal_event == event_type

    advance = _transition(
        candidate,
        2,
        from_state=CandidateState.WATCHLISTED,
        to_state=CandidateState.SIMULATION_RECORDED,
    )
    with pytest.raises(CandidateLifecycleError, match="terminal"):
        reduce_candidate_lifecycle(candidate, (terminal, advance))


def test_closed_resolved_archived_terminal_sequence_is_replayable() -> None:
    candidate = _candidate(CandidateState.WATCHLISTED)
    closed = _transition(candidate, 1, event_type=CandidateEventType.MARKET_CLOSED)
    resolved = _transition(candidate, 2, event_type=CandidateEventType.RESOLVED)
    archived = _transition(candidate, 3, event_type=CandidateEventType.ARCHIVED)
    projection = reduce_candidate_lifecycle(candidate, (closed, resolved, archived))
    assert projection.terminal is True
    assert projection.archived is True
    assert projection.terminal_event == CandidateEventType.ARCHIVED
    assert projection.event_count == 3


def test_terminal_event_sequence_fails_closed_on_regression_or_duplicate_fact() -> None:
    candidate = _candidate(CandidateState.WATCHLISTED)
    resolved = _transition(candidate, 1, event_type=CandidateEventType.RESOLVED)
    closed_late = _transition(candidate, 2, event_type=CandidateEventType.MARKET_CLOSED)
    with pytest.raises(CandidateLifecycleError, match="illegal terminal event sequence"):
        reduce_candidate_lifecycle(candidate, (resolved, closed_late))

    resolved_again = _transition(
        candidate,
        2,
        event_type=CandidateEventType.RESOLVED,
        reason="distinct duplicate resolution fact",
    )
    with pytest.raises(CandidateLifecycleError, match="illegal terminal event sequence"):
        reduce_candidate_lifecycle(candidate, (resolved, resolved_again))


@pytest.mark.parametrize(
    "state", (CandidateState.REJECTED, CandidateState.SIMULATION_RECORDED)
)
def test_completed_workflow_seed_is_not_active(state: CandidateState) -> None:
    projection = reduce_candidate_lifecycle(_candidate(state), ())
    assert projection.active is False
    assert projection.terminal is False


def test_illegal_edge_wrong_from_state_and_non_state_change_fail_closed() -> None:
    candidate = _candidate()
    illegal = _transition(
        candidate,
        1,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.BLIND_PACKET_FROZEN,
    )
    with pytest.raises(CandidateLifecycleError, match="illegal normal"):
        reduce_candidate_lifecycle(candidate, (illegal,))

    wrong_from = _transition(
        candidate,
        1,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.RULE_A_PASSED,
    )
    with pytest.raises(CandidateLifecycleError, match="does not match"):
        reduce_candidate_lifecycle(candidate, (wrong_from,))

    invalidation_changes_state = _transition(
        candidate,
        1,
        event_type=CandidateEventType.EVIDENCE_STALE,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    with pytest.raises(CandidateLifecycleError, match="must retain"):
        reduce_candidate_lifecycle(candidate, (invalidation_changes_state,))


def test_duplicate_replay_is_idempotent_but_conflicting_record_id_fails() -> None:
    candidate = _candidate()
    event = _transition(
        candidate,
        1,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    once = reduce_candidate_lifecycle(candidate, (event,))
    replay = reduce_candidate_lifecycle(candidate, (event, event))
    assert replay == once
    assert replay.event_count == 1

    conflict = event.model_copy(update={"reason": "tampered append-only content"})
    with pytest.raises(CandidateLifecycleError, match="conflicting"):
        reduce_candidate_lifecycle(candidate, (event, conflict))


def test_out_of_order_and_concurrent_events_fail_closed() -> None:
    candidate = _candidate()
    merge = _transition(
        candidate,
        2,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    early = _transition(
        candidate,
        1,
        event_type=CandidateEventType.EVIDENCE_STALE,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    with pytest.raises(CandidateLifecycleError, match="increasing"):
        reduce_candidate_lifecycle(candidate, (merge, early))

    same_time = _transition(
        candidate,
        3,
        event_type=CandidateEventType.EVIDENCE_STALE,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.CANDIDATE_MERGED,
        at=merge.at,
    )
    with pytest.raises(CandidateLifecycleError, match="increasing"):
        reduce_candidate_lifecycle(candidate, (merge, same_time))


def test_projection_rebuild_is_deterministic() -> None:
    candidate = _candidate()
    merge = _transition(
        candidate,
        1,
        from_state=CandidateState.RECALLED,
        to_state=CandidateState.CANDIDATE_MERGED,
    )
    rule_a = _transition(
        candidate,
        2,
        from_state=CandidateState.CANDIDATE_MERGED,
        to_state=CandidateState.RULE_A_PASSED,
    )
    events = (merge, rule_a)
    assert reduce_candidate_lifecycle(candidate, events) == reduce_candidate_lifecycle(candidate, events)


def test_protocol_source_tree_passes_offline_capability_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_source_tree(root / "src/polymarket_alpha/protocol")
    assert result.passed, result.violations
