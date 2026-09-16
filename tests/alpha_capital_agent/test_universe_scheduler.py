from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.alpha_capital_agent.policy import market20_v1_policy
from src.alpha_capital_agent.scheduler import (
    KeysetPage,
    ScanLane,
    ScanTermination,
    build_scan_policy,
    run_keyset_scan,
    select_due_evaluations,
)
from src.alpha_capital_agent.universe import (
    AdmissionTrigger,
    EligibilityHistory,
    EligibilityReasonCode,
    EligibilityTrigger,
    MarketabilityFacts,
    UniverseState,
    create_admission_episode,
    evaluate_eligibility,
)
from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    stable_record_id,
)
from src.polymarket_alpha.security import audit_source_tree


UTC = timezone.utc
T0 = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
RULES = (
    "This market resolves YES if the official published final result confirms "
    "the stated event before the deadline, and resolves NO in every other case."
)


def _snapshot(
    *, market_id: str = "m1", at: datetime = T0, status: MarketStatus = MarketStatus.ACTIVE
) -> MarketSnapshot:
    identity = MarketIdentity(
        event_id="e1",
        market_id=market_id,
        condition_id=f"condition-{market_id}",
        yes_token_id=f"yes-{market_id}",
        no_token_id=f"no-{market_id}",
    )
    fields = {
        "run_id": "fixture-snapshot",
        "created_at": at,
        "source": "fixture",
        "source_version": "v1",
        "identity": identity,
        "title": "Fixture market",
        "question": "Will the fixture event happen?",
        "slug": "fixture-market",
        "status": status,
        "end_at": at + timedelta(days=30),
        "tags": (),
        "rules_raw": RULES,
        "volume": Decimal("5000"),
        "liquidity": Decimal("1000"),
        "source_observed_at": at,
        "ingested_at": at,
    }
    record_id = stable_record_id("fixture_market_snapshot", fields)
    return MarketSnapshot(record_id=record_id, **fields)


def _facts(
    snapshot: MarketSnapshot,
    *,
    at: datetime,
    liquidity: str = "1000",
    volume: str = "5000",
    bid: str = "0.45",
    ask: str = "0.50",
    executable_depth: str | None = "100",
    upstream_updated_at: datetime | None = None,
) -> MarketabilityFacts:
    return MarketabilityFacts(
        market_id=snapshot.identity.market_id,
        snapshot_id=snapshot.record_id,
        snapshot_sha256=snapshot.canonical_sha256,
        observed_at=at,
        upstream_updated_at=upstream_updated_at or at,
        accepting_orders=True,
        enable_order_book=True,
        binary_paired=True,
        public_link_available=True,
        restricted=False,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        spread=Decimal(ask) - Decimal(bid),
        liquidity=Decimal(liquidity),
        volume=Decimal(volume),
        executable_depth=(
            None if executable_depth is None else Decimal(executable_depth)
        ),
        stale=False,
        source_artifact_ids=(f"artifact-{at.isoformat()}",),
    )


def test_market20_thresholds_are_released_from_the_controlled_selector() -> None:
    policy = market20_v1_policy()
    assert policy.min_liquidity_enter == Decimal("750")
    assert policy.min_volume_enter == Decimal("2500")
    assert policy.max_spread_enter == Decimal("0.12")
    assert policy.min_hours_to_deadline == 24
    assert policy.entry_confirmations == 2
    assert policy.exit_confirmations == 2
    assert policy.execution == "NO_ORDER"


def test_formerly_ineligible_market_crosses_over_after_two_confirmations() -> None:
    policy = market20_v1_policy()
    snapshot = _snapshot()
    first = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0, liquidity="500"),
        policy=policy,
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="eligibility-run",
    )
    assert first.history.state == UniverseState.INELIGIBLE_DORMANT
    assert first.observation.reason_codes == (EligibilityReasonCode.LOW_LIQUIDITY,)
    near = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=5), liquidity="650"),
        policy=policy,
        history=first.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="eligibility-run",
    )
    assert near.history.state == UniverseState.NEAR_ELIGIBLE_WATCH
    pending = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=10), liquidity="800"),
        policy=policy,
        history=near.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="eligibility-run",
    )
    assert pending.observation.qualifies is True
    assert pending.history.state == UniverseState.NEAR_ELIGIBLE_WATCH
    promoted = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=15), liquidity="800"),
        policy=policy,
        history=pending.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="eligibility-run",
    )
    assert promoted.history.state == UniverseState.ELIGIBLE_UNRESEARCHED
    assert promoted.transition is not None
    assert promoted.transition.new_state == UniverseState.ELIGIBLE_UNRESEARCHED
    episode = create_admission_episode(
        observation=promoted.observation,
        policy=policy,
        trigger=AdmissionTrigger.MARKETABILITY_CROSSOVER,
        admitted_at=promoted.observation.observed_at,
        run_id=promoted.observation.run_id,
    )
    assert episode.execution == "NO_ORDER"
    assert episode.market_id == snapshot.identity.market_id
    replay = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=15), liquidity="800"),
        policy=policy,
        history=pending.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="eligibility-run",
    )
    assert replay == promoted


def test_hysteresis_keeps_existing_eligible_market_above_exit_threshold() -> None:
    policy = market20_v1_policy()
    snapshot = _snapshot()
    history = EligibilityHistory(state=UniverseState.ELIGIBLE_UNRESEARCHED)
    kept = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0, liquidity="650"),
        policy=policy,
        history=history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="hysteresis",
    )
    assert kept.observation.threshold_mode == "STAY"
    assert kept.observation.thresholds["liquidity"] == Decimal("600")
    assert kept.history.state == UniverseState.ELIGIBLE_UNRESEARCHED
    pending_exit = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=5), liquidity="590"),
        policy=policy,
        history=kept.history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="hysteresis",
    )
    assert pending_exit.history.state == UniverseState.ELIGIBLE_UNRESEARCHED
    assert pending_exit.history.consecutive_disqualifying == 1
    demoted = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=10), liquidity="590"),
        policy=policy,
        history=pending_exit.history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="hysteresis",
    )
    assert demoted.history.state == UniverseState.NEAR_ELIGIBLE_WATCH


def test_candidate_stales_and_recovers_without_research_state_rewrite() -> None:
    policy = market20_v1_policy()
    snapshot = _snapshot()
    active = EligibilityHistory(state=UniverseState.CANDIDATE_ACTIVE)
    pending_stale = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0, ask="0.70", bid="0.45"),
        policy=policy,
        history=active,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="candidate-watch",
    )
    assert pending_stale.history.state == UniverseState.CANDIDATE_ACTIVE
    stale = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(
            snapshot,
            at=T0 + timedelta(minutes=5),
            ask="0.70",
            bid="0.45",
        ),
        policy=policy,
        history=pending_stale.history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="candidate-watch",
    )
    assert stale.history.state == UniverseState.CANDIDATE_STALE
    pending_recovery = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=10)),
        policy=policy,
        history=stale.history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="candidate-watch",
    )
    assert pending_recovery.history.state == UniverseState.CANDIDATE_STALE
    recovered = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=15)),
        policy=policy,
        history=pending_recovery.history,
        trigger=EligibilityTrigger.BOOK_EVENT,
        run_id="candidate-watch",
    )
    assert recovered.history.state == UniverseState.CANDIDATE_ACTIVE


def test_missing_executable_depth_fails_closed_even_when_quantity_floor_is_zero() -> None:
    policy = market20_v1_policy()
    snapshot = _snapshot()
    result = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0, executable_depth=None),
        policy=policy,
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="missing-depth",
    )

    assert result.observation.qualifies is False
    assert (
        EligibilityReasonCode.INSUFFICIENT_EXECUTABLE_DEPTH
        in result.observation.reason_codes
    )


def test_closed_is_terminal_and_held_position_can_create_override_episode() -> None:
    policy = market20_v1_policy()
    closed = _snapshot(status=MarketStatus.CLOSED)
    terminal = evaluate_eligibility(
        snapshot=closed,
        facts=_facts(closed, at=T0),
        policy=policy,
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.LIFECYCLE_EVENT,
        run_id="closed",
        held_position=True,
    )
    assert terminal.history.state == UniverseState.TERMINAL
    episode = create_admission_episode(
        observation=terminal.observation,
        policy=policy,
        trigger=AdmissionTrigger.HELD_POSITION_BOOTSTRAP,
        admitted_at=terminal.observation.observed_at,
        run_id=terminal.observation.run_id,
    )
    assert episode.held_position_override is True


def test_stale_upstream_facts_fail_closed() -> None:
    policy = market20_v1_policy()
    snapshot = _snapshot()
    result = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(
            snapshot,
            at=T0 + timedelta(minutes=10),
            upstream_updated_at=T0,
        ),
        policy=policy,
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="stale",
    )
    assert EligibilityReasonCode.DATA_STALE in result.observation.reason_codes
    assert result.observation.qualifies is False


def _row(market_id: str, updated_at: datetime) -> dict[str, str]:
    return {"id": market_id, "updatedAt": updated_at.isoformat()}


def test_keyset_bootstrap_delta_overlap_and_exact_replay() -> None:
    policy = build_scan_policy(
        policy_name="shadow_v1",
        run_id="scan-policy",
        created_at=T0,
    )
    pages = {
        None: KeysetPage.of((_row("m3", T0), _row("m2", T0 - timedelta(minutes=5))), "c1"),
        "c1": KeysetPage.of((_row("m1", T0 - timedelta(minutes=10)),), None),
    }

    def fetch(cursor: str | None, limit: int) -> KeysetPage:
        assert limit == 500
        return pages[cursor]

    bootstrap = run_keyset_scan(
        fetch_page=fetch,
        lane=ScanLane.GAMMA_DELTA,
        policy=policy,
        prior_cursor=None,
        scheduled_for=T0,
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        run_id="bootstrap",
    )
    assert bootstrap.receipt.termination == ScanTermination.END_OF_STREAM
    assert bootstrap.receipt.coverage_complete is True
    assert bootstrap.cursor.watermark_market_id == "m3"
    assert len(bootstrap.selected_items) == 3
    replay = run_keyset_scan(
        fetch_page=fetch,
        lane=ScanLane.GAMMA_DELTA,
        policy=policy,
        prior_cursor=None,
        scheduled_for=T0,
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        run_id="bootstrap",
    )
    assert replay == bootstrap

    delta_at = T0 + timedelta(minutes=5)
    delta_page = KeysetPage.of(
        (
            _row("m4", delta_at),
            _row("m3", T0),
            _row("old", T0 - timedelta(minutes=20)),
        ),
        "unused-because-watermark-reached",
    )
    delta = run_keyset_scan(
        fetch_page=lambda cursor, limit: delta_page,
        lane=ScanLane.GAMMA_DELTA,
        policy=policy,
        prior_cursor=bootstrap.cursor,
        scheduled_for=delta_at,
        started_at=delta_at,
        completed_at=delta_at + timedelta(seconds=1),
        run_id="delta",
    )
    assert delta.receipt.termination == ScanTermination.WATERMARK_REACHED
    assert [item["id"] for item in delta.selected_items] == ["m4", "m3"]
    assert delta.cursor.watermark_market_id == "m4"


def test_keyset_ordering_failure_does_not_advance_or_emit_partial_items() -> None:
    policy = build_scan_policy(
        policy_name="shadow_v1",
        run_id="scan-policy",
        created_at=T0,
    )
    invalid = KeysetPage.of(
        (_row("older", T0 - timedelta(minutes=1)), _row("newer", T0)), None
    )
    result = run_keyset_scan(
        fetch_page=lambda cursor, limit: invalid,
        lane=ScanLane.GAMMA_DELTA,
        policy=policy,
        prior_cursor=None,
        scheduled_for=T0,
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        run_id="invalid-order",
    )
    assert result.receipt.termination == ScanTermination.ORDERING_INVALID
    assert result.receipt.coverage_complete is False
    assert result.selected_items == ()
    assert result.cursor.watermark_updated_at is None


def test_page_budget_and_due_queue_are_bounded_and_deterministic() -> None:
    policy = build_scan_policy(
        policy_name="one-page",
        run_id="one-page-policy",
        created_at=T0,
        max_pages_per_run=1,
    )
    result = run_keyset_scan(
        fetch_page=lambda cursor, limit: KeysetPage.of((_row("m1", T0),), "next"),
        lane=ScanLane.FULL_ACTIVE_CENSUS,
        policy=policy,
        prior_cursor=None,
        scheduled_for=T0,
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        run_id="bounded",
    )
    assert result.receipt.termination == ScanTermination.MAX_PAGES_BOUND
    assert result.selected_items == ()

    snapshot = _snapshot()
    first = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0),
        policy=market20_v1_policy(),
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="due",
        held_position=True,
    )
    second = evaluate_eligibility(
        snapshot=snapshot,
        facts=_facts(snapshot, at=T0 + timedelta(minutes=5)),
        policy=market20_v1_policy(),
        history=first.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="due",
        held_position=True,
    )
    due = select_due_evaluations(
        (first.next_evaluation, second.next_evaluation),
        as_of=T0 + timedelta(minutes=10),
        max_items=1,
    )
    assert due == (second.next_evaluation,)


def test_universe_scheduler_has_no_network_daemon_or_order_capability() -> None:
    audit = audit_source_tree("src/alpha_capital_agent")
    assert audit.passed, audit.findings


def test_float_inputs_are_rejected() -> None:
    snapshot = _snapshot()
    with pytest.raises(ValueError, match="float is forbidden"):
        MarketabilityFacts(
            market_id=snapshot.identity.market_id,
            snapshot_id=snapshot.record_id,
            snapshot_sha256=snapshot.canonical_sha256,
            observed_at=T0,
            accepting_orders=True,
            enable_order_book=True,
            binary_paired=True,
            public_link_available=True,
            best_bid=0.5,
            best_ask=Decimal("0.6"),
            spread=Decimal("0.1"),
            liquidity=Decimal("1000"),
            volume=Decimal("5000"),
            source_artifact_ids=("artifact",),
        )
