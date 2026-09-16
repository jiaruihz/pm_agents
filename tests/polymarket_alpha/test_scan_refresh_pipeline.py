"""Offline scan-refresh planner coverage; no scheduler, storage, or transport."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    CandidateCard,
    CandidateEventType,
    CandidateState,
    CandidateTransition,
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    ResearchPriority,
    content_sha256,
    stable_record_id,
)
from src.polymarket_alpha.pipeline.refresh import (
    RefreshSignal,
    RefreshSignalKind,
    ScanRefreshPlanningError,
    ScanRefreshRequest,
    plan_scan_refresh,
)
from src.polymarket_alpha.protocol import reduce_candidate_lifecycle
from src.polymarket_alpha.recall import LateHitImpact, LateHitImpactType


T0 = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
H1 = "a" * 64
H2 = "b" * 64


def _candidate(state: CandidateState = CandidateState.CANDIDATE_MERGED) -> CandidateCard:
    candidate_id = stable_record_id("candidate", "market-refresh")
    return CandidateCard(
        record_id=stable_record_id("candidate_card", candidate_id, state.value),
        run_id="fixture-run",
        created_at=T0,
        source="fixture",
        source_version="v1",
        provenance=(),
        extensions={},
        candidate_id=candidate_id,
        market_id="market-refresh",
        recall_hit_ids=("recall_hit:seed",),
        recall_score=Decimal("1"),
        dedup_group="market-refresh",
        selected_at=T0,
        state=state,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("fixture",),
    )


def _snapshot(version: str, *, status: MarketStatus = MarketStatus.ACTIVE, rules: str = "Rule A") -> MarketSnapshot:
    observed = T0 + timedelta(minutes=int(version))
    return MarketSnapshot(
        record_id=stable_record_id("market_snapshot", "market-refresh", version),
        run_id="catalog-run",
        created_at=observed,
        source="fixture_catalog",
        source_version="v1",
        provenance=(),
        extensions={},
        identity=MarketIdentity(event_id="event", market_id="market-refresh", yes_token_id="yes", no_token_id="no"),
        title="Fixture", question="Does it happen?", status=status, rules_raw=rules,
        source_observed_at=observed, ingested_at=observed,
    )


def _signal(kind: RefreshSignalKind, marker: str, at: datetime) -> RefreshSignal:
    return RefreshSignal(kind=kind, artifact_id=f"artifact:{marker}", artifact_sha256=content_sha256(marker), effective_at=at, reason=f"{kind.value}:{marker}")


def _transition(candidate: CandidateCard, before: CandidateState, after: CandidateState, at: datetime) -> CandidateTransition:
    return CandidateTransition(
        record_id=stable_record_id("candidate_transition", candidate.candidate_id, before.value, after.value, at),
        run_id="fixture-run", created_at=at, source="fixture", source_version="v1", provenance=(), extensions={},
        candidate_id=candidate.candidate_id, event_type=CandidateEventType.STATE_TRANSITION,
        from_state=before, to_state=after, reason="fixture state", actor="fixture", at=at,
        related_artifact_ids=("artifact:state",), input_hash=content_sha256({"at": at}),
    )


def _request(candidate: CandidateCard, **updates: object) -> ScanRefreshRequest:
    values: dict[str, object] = {"candidate": candidate, "as_of": T0 + timedelta(hours=2)}
    values.update(updates)
    return ScanRefreshRequest(**values)


def _late(candidate: CandidateCard, impact: LateHitImpactType, *, before: str | None = None, after: str | None = None) -> LateHitImpact:
    required = CandidateEventType.RESEARCH_REFRESH_REQUIRED if impact == LateHitImpactType.RESEARCH_REFRESH_REQUIRED else None
    return LateHitImpact(impact=impact, candidate_id=candidate.candidate_id, new_recall_hit_ids=("recall_hit:late",), prior_projection_input_sha256=before, merged_projection_input_sha256=after, required_event=required, safe_to_advance=impact not in {LateHitImpactType.RESEARCH_REFRESH_REQUIRED, LateHitImpactType.IMPACT_UNDETERMINED})


def test_second_scan_plans_all_invalidation_routes_and_reduces_projection() -> None:
    candidate = _candidate()
    prior = _snapshot("1", rules="Rule A")
    current = _snapshot("2", rules="Rule B")
    plan = plan_scan_refresh(_request(candidate, prior_snapshot=prior, current_snapshot=current, signals=(
        _signal(RefreshSignalKind.RECALL_EXPIRED, "expired", T0 + timedelta(minutes=3)),
        _signal(RefreshSignalKind.EVIDENCE_STALE, "evidence", T0 + timedelta(minutes=3)),
        _signal(RefreshSignalKind.BOOK_REFRESH_REQUIRED, "book", T0 + timedelta(minutes=3)),
    )))
    assert [item.event_type for item in plan.transitions] == [CandidateEventType.RULE_REVISION_INVALIDATED, CandidateEventType.RECALL_EXPIRED, CandidateEventType.EVIDENCE_STALE, CandidateEventType.BOOK_REFRESH_REQUIRED]
    assert len({item.at for item in plan.transitions}) == 4
    projection = reduce_candidate_lifecycle(candidate, plan.transitions)
    assert projection.invalidated and projection.refresh_required
    assert all(item.related_artifact_ids and item.input_hash for item in plan.transitions)


def test_late_hit_pre_blind_is_explicit_input_refresh_but_frozen_blind_requires_event() -> None:
    candidate = _candidate()
    pre = _late(candidate, LateHitImpactType.PRE_BLIND_REFRESH)
    pre_plan = plan_scan_refresh(_request(candidate, late_hit_impacts=(pre,)))
    assert pre_plan.transitions == ()
    assert pre_plan.pre_blind_refresh_inputs[0].recall_hit_ids == ("recall_hit:late",)

    frozen = _candidate(CandidateState.BLIND_PACKET_FROZEN)
    impact = _late(frozen, LateHitImpactType.RESEARCH_REFRESH_REQUIRED, before=H1, after=H2)
    plan = plan_scan_refresh(_request(frozen, late_hit_impacts=(impact,)))
    assert [item.event_type for item in plan.transitions] == [CandidateEventType.RESEARCH_REFRESH_REQUIRED]
    assert plan.transitions[0].from_state == CandidateState.BLIND_PACKET_FROZEN


def test_exact_retry_and_input_reordering_are_byte_identical_and_dedupe() -> None:
    candidate = _candidate()
    a = _signal(RefreshSignalKind.EVIDENCE_STALE, "a", T0 + timedelta(minutes=2))
    b = _signal(RefreshSignalKind.BOOK_REFRESH_REQUIRED, "b", T0 + timedelta(minutes=2))
    one = plan_scan_refresh(_request(candidate, signals=(a, b)))
    two = plan_scan_refresh(_request(candidate, signals=(b, a, a)))
    assert one.model_dump_json() == two.model_dump_json()
    retry = plan_scan_refresh(_request(candidate, signals=(a, b), prior_transitions=one.transitions))
    assert retry.transitions == ()


def test_same_record_id_conflict_and_undetermined_late_hit_fail_closed() -> None:
    candidate = _candidate()
    signal = _signal(RefreshSignalKind.EVIDENCE_STALE, "x", T0 + timedelta(minutes=2))
    plan = plan_scan_refresh(_request(candidate, signals=(signal,)))
    tampered = plan.transitions[0].model_copy(update={"reason": "tampered"})
    with pytest.raises(ScanRefreshPlanningError, match="conflicting append-only"):
        plan_scan_refresh(_request(candidate, signals=(signal,), prior_transitions=(tampered,)))
    undetermined = _late(candidate, LateHitImpactType.IMPACT_UNDETERMINED)
    with pytest.raises(ScanRefreshPlanningError, match="undetermined"):
        plan_scan_refresh(_request(candidate, late_hit_impacts=(undetermined,)))


def test_closed_then_resolved_and_terminal_refresh_rejection() -> None:
    candidate = _candidate(CandidateState.WATCHLISTED)
    closed = plan_scan_refresh(_request(candidate, current_snapshot=_snapshot("3", status=MarketStatus.CLOSED)))
    assert [item.event_type for item in closed.transitions] == [CandidateEventType.MARKET_CLOSED]
    resolved = plan_scan_refresh(_request(candidate, prior_transitions=closed.transitions, current_snapshot=_snapshot("4", status=MarketStatus.RESOLVED)))
    assert [item.event_type for item in resolved.transitions] == [CandidateEventType.RESOLVED]
    full = (*closed.transitions, *resolved.transitions)
    assert reduce_candidate_lifecycle(candidate, full).terminal_event == CandidateEventType.RESOLVED
    with pytest.raises(ScanRefreshPlanningError, match="terminal candidate"):
        plan_scan_refresh(_request(candidate, prior_transitions=full, signals=(_signal(RefreshSignalKind.EVIDENCE_STALE, "late", T0 + timedelta(minutes=5)),)))


def test_old_fact_after_existing_append_only_history_and_superseded_fail_closed() -> None:
    candidate = _candidate()
    first = plan_scan_refresh(_request(candidate, signals=(_signal(RefreshSignalKind.EVIDENCE_STALE, "first", T0 + timedelta(minutes=10)),)))
    with pytest.raises(ScanRefreshPlanningError, match="precedes"):
        plan_scan_refresh(_request(candidate, prior_transitions=first.transitions, signals=(_signal(RefreshSignalKind.BOOK_REFRESH_REQUIRED, "old", T0 + timedelta(minutes=5)),)))
    with pytest.raises(ValueError, match="SUPERSEDED"):
        _request(candidate, current_snapshot=_snapshot("4", status=MarketStatus.SUPERSEDED))
