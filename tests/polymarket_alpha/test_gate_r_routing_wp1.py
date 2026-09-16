"""Gate R WP1 deterministic lifecycle, identity, routing, and replay evidence."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.polymarket_alpha.contracts import (
    CandidateCard,
    CandidateEligibility,
    CandidateEventType,
    CandidateState,
    ResearchPriority,
    RiskTier,
    TriageRoutingAction,
)
from src.polymarket_alpha.protocol import CandidateLifecycleProjection
from src.polymarket_alpha.storage import AlphaRepository, ContractConflictError
from src.polymarket_alpha.triage import (
    ProviderReturnMetadata,
    Researchability,
    ResolutionSourceType,
    SemanticTriageDecision,
    SemanticTriageDisposition,
    SemanticTriageEligibility,
    SemanticTriageItemResult,
    SemanticTriageReceipt,
    build_candidate_snapshot_seal,
    build_rule_dry_run_receipt,
    compare_typed_reviews,
    route_triage,
)


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
ATTEMPT_ID = "attempt:glm47"


def _candidate() -> CandidateCard:
    return CandidateCard(
        record_id="candidate_card:" + "a" * 64,
        candidate_id="candidate:" + "b" * 64,
        run_id="wp1",
        created_at=NOW,
        source="test",
        source_version="v1",
        market_id="market-1",
        recall_hit_ids=("recall:one",),
        recall_score=Decimal("1"),
        dedup_group="d",
        selected_at=NOW,
        research_priority=ResearchPriority.CORE,
        selection_rationale=("R",),
    )


def _lifecycle(
    *,
    terminal_event: CandidateEventType | None = None,
    invalidated: bool = False,
    refresh_required: bool = False,
    archived: bool = False,
    active: bool | None = None,
) -> CandidateLifecycleProjection:
    if active is None:
        active = terminal_event is None and not invalidated and not archived
    return CandidateLifecycleProjection(
        candidate_id=_candidate().candidate_id,
        initial_state=CandidateState.RECALLED,
        current_state=CandidateState.RECALLED,
        applied_transition_ids=(),
        event_count=0,
        active=active,
        invalidated=invalidated,
        refresh_required=refresh_required,
        terminal=terminal_event is not None or archived,
        archived=archived,
        terminal_event=terminal_event,
        last_at=NOW,
    )


def _snapshot(
    *,
    lifecycle: CandidateLifecycleProjection | None = None,
    deadline: datetime | None = None,
    duplicate: bool = False,
    market_revision_suffix: str = "c",
):
    candidate = _candidate()
    return build_candidate_snapshot_seal(
        candidate=candidate,
        candidate_revision_id=candidate.record_id,
        market_revision_id="market_snapshot:" + market_revision_suffix * 64,
        rule_source_artifact_id="source:rules",
        rule_source_bytes=b"official rules",
        lifecycle=lifecycle or _lifecycle(),
        deadline_utc=deadline,
        as_of_utc=NOW,
        allowed_projection_input_ids=("rule",),
        run_id="wp1",
        duplicate=duplicate,
    )


def _dry(
    snapshot,
    *,
    complete: bool = True,
    direct: bool = True,
    source_count: int = 1,
    complexity: int = 0,
    ambiguities: tuple[str, ...] = (),
    reasons: tuple[str, ...] = ("OK",),
):
    return build_rule_dry_run_receipt(
        snapshot=snapshot,
        complete=complete,
        direct_compile_possible=direct,
        authoritative_source_count=source_count,
        complexity_score=complexity,
        ambiguity_codes=ambiguities,
        reason_codes=reasons,
        compiler_version="v1",
        run_id="wp1",
        created_at=NOW,
    )


def _triage(
    snapshot,
    *,
    disposition: SemanticTriageDisposition = SemanticTriageDisposition.ADVANCE,
    confidence_milli: int = 900,
    ambiguity_codes: tuple[str, ...] = (),
    topic_family: str = "FAMILY_A",
):
    item = SemanticTriageItemResult(
        item_id="blind:item",
        topic_family=topic_family,
        subject_entities=("Entity",),
        deadline_interpretation="Inclusive deadline",
        resolution_source_type=ResolutionSourceType.OFFICIAL,
        researchability=Researchability.HIGH,
        ambiguity_codes=ambiguity_codes,
        disposition=disposition,
        confidence_milli=confidence_milli,
        reason_codes=("TYPED",),
    )
    decision_id = "semantic_decision:" + "d" * 64
    projection_id = "semantic_projection:" + "e" * 64
    decision = SemanticTriageDecision(
        record_id=decision_id,
        decision_id=decision_id,
        run_id="wp1",
        created_at=NOW,
        source="test",
        source_version="v1",
        projection_id=projection_id,
        item_id=item.item_id,
        market_id="market-1",
        result=item,
        eligibility=SemanticTriageEligibility.ELIGIBLE,
        effective_disposition=disposition,
        candidate_snapshot_id=snapshot.record_id,
        candidate_snapshot_sha256=snapshot.canonical_sha256,
        attempt_id=ATTEMPT_ID,
    )
    provider_counts = {
        value.value: int(value == disposition)
        for value in SemanticTriageDisposition
    }
    receipt_id = "semantic_receipt:" + "f" * 64
    receipt = SemanticTriageReceipt(
        record_id=receipt_id,
        receipt_id=receipt_id,
        run_id="wp1",
        created_at=NOW,
        source="test",
        source_version="v1",
        projection_id=projection_id,
        projection_sha256="1" * 64,
        prompt_sha256="2" * 64,
        provider_wrapper_sha256="3" * 64,
        provider_result_sha256="4" * 64,
        imported_at=NOW,
        provider=ProviderReturnMetadata(
            provider="test",
            requested_model="glm-4.7",
            reported_model="glm-4.7",
        ),
        item_count=1,
        provider_dispositions=provider_counts,
        dispositions=provider_counts,
        candidate_snapshot_id=snapshot.record_id,
        candidate_snapshot_sha256=snapshot.canonical_sha256,
        attempt_id=ATTEMPT_ID,
    )
    return decision, receipt


def _route(
    snapshot,
    dry,
    *,
    triage=None,
    seed: str = "frozen",
    pilot_mode: bool = True,
    family_cohort_index: int | None = 1,
):
    decision, receipt = triage if triage is not None else (None, None)
    return route_triage(
        snapshot=snapshot,
        dry_run=dry,
        triage_decision=decision,
        triage_receipt=receipt,
        sample_seed=seed,
        run_id="wp1",
        created_at=NOW,
        pilot_mode=pilot_mode,
        family_cohort_index=family_cohort_index,
    )


@pytest.mark.parametrize(
    "snapshot,expected",
    [
        (_snapshot(deadline=NOW), CandidateEligibility.DEADLINE_ELAPSED),
        (_snapshot(duplicate=True), CandidateEligibility.DUPLICATE),
        (
            _snapshot(lifecycle=_lifecycle(terminal_event=CandidateEventType.MARKET_CLOSED)),
            CandidateEligibility.MARKET_CLOSED,
        ),
        (
            _snapshot(lifecycle=_lifecycle(terminal_event=CandidateEventType.RESOLVED)),
            CandidateEligibility.RESOLVED,
        ),
        (
            _snapshot(lifecycle=_lifecycle(terminal_event=CandidateEventType.SUPERSEDED)),
            CandidateEligibility.SUPERSEDED,
        ),
        (
            _snapshot(lifecycle=_lifecycle(invalidated=True, refresh_required=True)),
            CandidateEligibility.REFRESH_REQUIRED,
        ),
        (
            _snapshot(lifecycle=_lifecycle(invalidated=True)),
            CandidateEligibility.INVALIDATED,
        ),
        (
            _snapshot(lifecycle=_lifecycle(archived=True)),
            CandidateEligibility.ARCHIVED,
        ),
    ],
)
def test_hard_facts_are_sealed_before_models(snapshot, expected):
    assert snapshot.eligibility == expected
    route = _route(
        snapshot,
        _dry(snapshot),
        triage=None,
        family_cohort_index=None,
    )
    assert route.risk_tier == RiskTier.D_DETERMINISTIC
    assert route.action == TriageRoutingAction.DEFER_NONTERMINAL
    assert route.triage_receipt_id is None
    assert route == _route(
        snapshot,
        _dry(snapshot),
        triage=None,
        family_cohort_index=None,
    )


def test_snapshot_identity_and_repository_replay_are_fail_closed(tmp_path):
    snapshot = _snapshot()
    assert snapshot.seal_sha256
    assert snapshot == _snapshot()
    with pytest.raises(ValueError):
        build_candidate_snapshot_seal(
            candidate=_candidate(),
            candidate_revision_id=_candidate().record_id,
            market_revision_id="market_snapshot:" + "c" * 64,
            rule_source_artifact_id="source:rules",
            rule_source_bytes=b"",
            lifecycle=_lifecycle(),
            deadline_utc=None,
            as_of_utc=NOW,
            allowed_projection_input_ids=("rule",),
            run_id="wp1",
        )

    repository = AlphaRepository(tmp_path / "alpha.db")
    assert repository.save_contract(snapshot) == snapshot.canonical_sha256
    assert repository.save_contract(snapshot) == snapshot.canonical_sha256
    conflicting = type(snapshot).model_validate(
        {**snapshot.model_dump(mode="python"), "source_version": "conflict"}
    )
    with pytest.raises(ContractConflictError):
        repository.save_contract(conflicting)


def test_rule_dry_run_rejects_inconsistent_direct_compile():
    with pytest.raises(ValueError, match="incomplete"):
        _dry(_snapshot(), complete=False, direct=True)


def test_eligible_routing_rejects_cross_candidate_or_attempt_binding():
    snapshot = _snapshot()
    other = _snapshot(market_revision_suffix="9")
    triage = _triage(snapshot)
    with pytest.raises(ValueError, match="current snapshot"):
        _route(other, _dry(other), triage=triage)

    decision, receipt = triage
    wrong_attempt = type(receipt).model_validate(
        {**receipt.model_dump(mode="python"), "attempt_id": "attempt:other"}
    )
    with pytest.raises(ValueError, match="one attempt"):
        _route(snapshot, _dry(snapshot), triage=(decision, wrong_attempt))


def test_r1_pilot_route_records_actual_and_future_sampling():
    snapshot = _snapshot()
    triage = _triage(snapshot)
    future_direct = None
    for index in range(1, 50):
        candidate = _route(
            snapshot,
            _dry(snapshot),
            triage=triage,
            seed=f"seed-{index}",
            pilot_mode=True,
            family_cohort_index=index,
        )
        if candidate.future_policy_action == TriageRoutingAction.TRY_DIRECT_COMPILE:
            future_direct = candidate
            break
    assert future_direct is not None
    assert future_direct.risk_tier == RiskTier.R1_SIMPLE
    assert future_direct.action == TriageRoutingAction.REQUEST_GLM53
    assert future_direct.sampled is True
    assert future_direct.future_policy_sampled is False

    cohort_minimum = _route(
        snapshot,
        _dry(snapshot),
        triage=triage,
        seed="any",
        pilot_mode=False,
        family_cohort_index=50,
    )
    assert cohort_minimum.future_policy_sampled is True
    assert cohort_minimum.action == TriageRoutingAction.REQUEST_GLM53


@pytest.mark.parametrize(
    "dry_kwargs,triage_kwargs,expected",
    [
        ({"ambiguities": ("AMBIGUOUS",)}, {}, RiskTier.R2_REVIEW),
        ({}, {"confidence_milli": 799}, RiskTier.R2_REVIEW),
        ({"source_count": 0}, {}, RiskTier.R3_CRITICAL),
        (
            {"reasons": ("SOURCE_PRECEDENCE_INCOMPLETE",)},
            {},
            RiskTier.R3_CRITICAL,
        ),
    ],
)
def test_r2_and_r3_policy(dry_kwargs, triage_kwargs, expected):
    snapshot = _snapshot()
    route = _route(
        snapshot,
        _dry(snapshot, **dry_kwargs),
        triage=_triage(snapshot, **triage_kwargs),
        pilot_mode=False,
    )
    assert route.risk_tier == expected
    assert route.action == TriageRoutingAction.REQUEST_GLM53


def test_model_only_defer_has_deterministic_twenty_percent_counterfactual():
    snapshot = _snapshot()
    triage = _triage(snapshot, disposition=SemanticTriageDisposition.DEFER)
    seen = set()
    for index in range(200):
        route = _route(
            snapshot,
            _dry(snapshot),
            triage=triage,
            seed=f"seed-{index}",
            pilot_mode=False,
            family_cohort_index=1,
        )
        assert route.risk_tier == RiskTier.D_MODEL_ONLY
        seen.add(route.action)
    assert seen == {
        TriageRoutingAction.REQUEST_GLM53,
        TriageRoutingAction.DEFER_NONTERMINAL,
    }

    pilot = _route(
        snapshot,
        _dry(snapshot),
        triage=triage,
        seed="seed-with-future-defer",
        pilot_mode=True,
        family_cohort_index=1,
    )
    assert pilot.action == TriageRoutingAction.REQUEST_GLM53
    assert pilot.sampled is True


def _review_mapping(topic_family: str = "FAMILY_A"):
    return {
        "topic_family": topic_family,
        "subject_entities": ["Entity"],
        "deadline_interpretation": "Inclusive deadline",
        "resolution_source_type": "OFFICIAL",
        "researchability": "HIGH",
        "ambiguity_codes": [],
        "disposition": "ADVANCE",
    }


def test_agreement_cannot_clear_missing_source_and_comparator_binds_route():
    snapshot = _snapshot()
    route = _route(
        snapshot,
        _dry(snapshot, source_count=0),
        triage=_triage(snapshot),
        pilot_mode=True,
    )
    assert route.risk_tier == RiskTier.R3_CRITICAL
    receipt = compare_typed_reviews(
        snapshot=snapshot,
        first_attempt_id=ATTEMPT_ID,
        first_attempt_sha256="a" * 64,
        first=_review_mapping(),
        second_attempt_id="attempt:glm53",
        second_attempt_sha256="b" * 64,
        second=_review_mapping(),
        route=route,
        run_id="wp1",
        created_at=NOW,
    )
    assert receipt.difference_codes == ()
    assert route.action == TriageRoutingAction.REQUEST_GLM53

    with pytest.raises(ValueError, match="comparison snapshot"):
        compare_typed_reviews(
            snapshot=_snapshot(market_revision_suffix="9"),
            first_attempt_id=ATTEMPT_ID,
            first_attempt_sha256="a" * 64,
            first=_review_mapping(),
            second_attempt_id="attempt:glm53",
            second_attempt_sha256="b" * 64,
            second=_review_mapping(),
            route=route,
            run_id="wp1",
            created_at=NOW,
        )


def test_comparator_rejects_unknown_fields_and_wrong_first_attempt():
    snapshot = _snapshot()
    route = _route(
        snapshot,
        _dry(snapshot),
        triage=_triage(snapshot),
        pilot_mode=True,
    )
    with pytest.raises(ValueError, match="frozen typed"):
        compare_typed_reviews(
            snapshot=snapshot,
            first_attempt_id=ATTEMPT_ID,
            first_attempt_sha256="a" * 64,
            first={**_review_mapping(), "price": "1"},
            second_attempt_id="attempt:glm53",
            second_attempt_sha256="b" * 64,
            second=_review_mapping(),
            route=route,
            run_id="wp1",
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="GLM-4.7 route"):
        compare_typed_reviews(
            snapshot=snapshot,
            first_attempt_id="attempt:wrong",
            first_attempt_sha256="a" * 64,
            first=_review_mapping(),
            second_attempt_id="attempt:glm53",
            second_attempt_sha256="b" * 64,
            second={**_review_mapping(), "topic_family": "FAMILY_B"},
            route=route,
            run_id="wp1",
            created_at=NOW,
        )
