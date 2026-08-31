from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.alpha_capital_agent.coordinator import (
    evaluate_and_persist_claimed_market,
    evaluate_and_persist_market,
    run_and_persist_scan,
)
from src.alpha_capital_agent.scheduler import (
    KeysetPage,
    ScanLane,
    build_cadence_work_order,
    build_scan_policy,
)
from src.alpha_capital_agent.storage import (
    AcaContractConflictError,
    AcaStoredContractCorruptionError,
    CapitalAgentRepository,
)
from src.alpha_capital_agent.universe import (
    EligibilityHistory,
    EligibilityHistoryCheckpoint,
    EligibilityTrigger,
    UniverseState,
)

from src.alpha_capital_agent.policy import market20_v1_policy
from src.alpha_capital_agent.universe import MarketabilityFacts
from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    canonical_json,
    content_sha256,
    stable_record_id,
)


T0 = datetime(2026, 8, 29, tzinfo=timezone.utc)


def _snapshot() -> MarketSnapshot:
    identity = MarketIdentity(event_id="e1", market_id="m1", condition_id="condition-m1", yes_token_id="yes-m1", no_token_id="no-m1")
    fields = {"run_id": "snapshot-run", "created_at": T0, "source": "fixture", "source_version": "v1", "identity": identity, "title": "Fixture market", "question": "Will the fixture event happen?", "slug": "fixture-market", "status": MarketStatus.ACTIVE, "end_at": T0 + timedelta(days=30), "tags": (), "rules_raw": "This market resolves YES if the official final result confirms the stated event before its deadline and otherwise resolves NO under the public rules.", "volume": Decimal("5000"), "liquidity": Decimal("1000"), "source_observed_at": T0, "ingested_at": T0}
    return MarketSnapshot(record_id=stable_record_id("fixture_market_snapshot", fields), **fields)


def _facts(snapshot: MarketSnapshot, at: datetime) -> MarketabilityFacts:
    return MarketabilityFacts(market_id="m1", snapshot_id=snapshot.record_id, snapshot_sha256=snapshot.canonical_sha256, observed_at=at, upstream_updated_at=at, accepting_orders=True, enable_order_book=True, binary_paired=True, public_link_available=True, restricted=False, best_bid=Decimal("0.45"), best_ask=Decimal("0.50"), spread=Decimal("0.05"), liquidity=Decimal("1000"), volume=Decimal("5000"), executable_depth=Decimal("100"), source_artifact_ids=(f"gamma:{at.isoformat()}",))


def _scan(repository: CapitalAgentRepository, *, items=({"id": "m1", "updatedAt": T0.isoformat()},), run_id="scan"):
    return run_and_persist_scan(
        repository=repository,
        fetch_page=lambda cursor, limit: KeysetPage.of(items, None),
        lane=ScanLane.GAMMA_DELTA,
        policy=build_scan_policy(policy_name="durable", run_id="policy", created_at=T0),
        scheduled_for=T0, started_at=T0, completed_at=T0 + timedelta(seconds=1), run_id=run_id,
    )


def test_durable_inbox_crash_gap_claim_reclaim_ack_and_publish(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    result = _scan(repository)
    # Simulated crash after persistence: restart observes no published cursor.
    restarted = CapitalAgentRepository(tmp_path / "aca.sqlite")
    assert restarted.latest_cursor(ScanLane.GAMMA_DELTA) is None
    first = restarted.claim_scan_work(owner="one", now=T0 + timedelta(seconds=2), lease_seconds=5)
    assert len(first) == 1 and first[0].attempts == 1
    assert restarted.claim_scan_work(owner="two", now=T0 + timedelta(seconds=3), lease_seconds=5) == ()
    reclaimed = restarted.claim_scan_work(owner="two", now=T0 + timedelta(seconds=8), lease_seconds=5)
    assert reclaimed[0].work_id == first[0].work_id and reclaimed[0].attempts == 2
    restarted.ack_scan_work(work_id=reclaimed[0].work_id, owner="two", now=T0 + timedelta(seconds=9))
    restarted.ack_scan_work(work_id=reclaimed[0].work_id, owner="two", now=T0 + timedelta(seconds=10))
    assert restarted.latest_cursor(ScanLane.GAMMA_DELTA) == result.result.cursor


def test_empty_scan_commits_and_replay_is_idempotent(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    first = _scan(repository, items=(), run_id="empty")
    replay = repository.persist_successful_scan(
        build_scan_policy(policy_name="durable", run_id="policy", created_at=T0),
        first.result.cursor, first.result.receipt, (),
    )
    assert replay == first.stored_sha256
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) == first.result.cursor


def test_retryable_failure_requeues_then_dead_letters_without_cursor_publish(
    tmp_path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    _scan(repository, run_id="dead-letter")
    first = repository.claim_scan_work(
        owner="worker", now=T0 + timedelta(seconds=2), lease_seconds=30
    )
    assert repository.fail_scan_work(
        work_id=first[0].work_id,
        owner="worker",
        now=T0 + timedelta(seconds=3),
        error="TRANSIENT",
        max_attempts=2,
    ) == "PENDING"
    second = repository.claim_scan_work(
        owner="worker", now=T0 + timedelta(seconds=4), lease_seconds=30
    )
    assert second[0].attempts == 2
    assert repository.fail_scan_work(
        work_id=second[0].work_id,
        owner="worker",
        now=T0 + timedelta(seconds=5),
        error="STILL_BROKEN",
        max_attempts=2,
    ) == "DEAD_LETTER"
    assert repository.claim_scan_work(
        owner="worker", now=T0 + timedelta(seconds=6), lease_seconds=30
    ) == ()
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) is None


def test_incomplete_scan_persists_attempt_but_does_not_advance_cursor(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    result = run_and_persist_scan(
        repository=repository,
        fetch_page=lambda cursor, limit: KeysetPage.of(({"id": "m1", "updatedAt": T0.isoformat()},), "loop"),
        lane=ScanLane.GAMMA_DELTA,
        policy=build_scan_policy(policy_name="incomplete", run_id="policy", created_at=T0),
        scheduled_for=T0, started_at=T0, completed_at=T0 + timedelta(seconds=1), run_id="incomplete",
    )
    assert not result.result.receipt.coverage_complete
    assert len(result.stored_sha256) == 3
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) is None
    assert len(repository.list_contracts("UniverseScanRunReceipt")) == 1
    assert repository.claim_scan_work(owner="x", now=T0 + timedelta(seconds=2), lease_seconds=1) == ()


def test_history_projection_recovers_when_history_argument_is_omitted(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    first = evaluate_and_persist_market(repository=repository, snapshot=snapshot, facts=_facts(snapshot, T0), policy=market20_v1_policy(), trigger=EligibilityTrigger.NEW_MARKET, run_id="history")
    assert first.evaluation.history.state is UniverseState.NEAR_ELIGIBLE_WATCH
    restarted = CapitalAgentRepository(tmp_path / "aca.sqlite")
    second = evaluate_and_persist_market(repository=restarted, snapshot=snapshot, facts=_facts(snapshot, T0 + timedelta(minutes=5)), policy=market20_v1_policy(), trigger=EligibilityTrigger.DELTA_SCAN, run_id="history")
    assert second.evaluation.history.state is UniverseState.ELIGIBLE_UNRESEARCHED


def test_history_rebuild_uses_exact_checkpoint_not_naive_observation_count(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    first = evaluate_and_persist_market(
        repository=repository, snapshot=snapshot, facts=_facts(snapshot, T0),
        policy=market20_v1_policy(), trigger=EligibilityTrigger.NEW_MARKET,
        run_id="history-rebuild",
    )
    second = evaluate_and_persist_market(
        repository=repository, snapshot=snapshot,
        facts=_facts(snapshot, T0 + timedelta(minutes=1)),
        policy=market20_v1_policy(), trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="history-rebuild",
    )
    assert first.evaluation.history.consecutive_qualifying == 1
    assert second.evaluation.history.consecutive_qualifying == 1

    import sqlite3

    connection = sqlite3.connect(tmp_path / "aca.sqlite")
    try:
        connection.execute("DELETE FROM aca_eligibility_history_current")
        connection.commit()
    finally:
        connection.close()
    assert repository.current_eligibility_history("m1") == second.evaluation.history


def test_history_checkpoint_hash_binds_observation_on_write_and_rebuild(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    step = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0),
        policy=market20_v1_policy(),
        trigger=EligibilityTrigger.NEW_MARKET,
        run_id="history-binding",
    )
    checkpoint = step.history_checkpoint
    wrong_hash = "0" * 64
    assert wrong_hash != checkpoint.observation_sha256
    fields = {
        "run_id": checkpoint.run_id,
        "created_at": checkpoint.created_at,
        "source": checkpoint.source,
        "source_version": checkpoint.source_version,
        "market_id": checkpoint.market_id,
        "observation_id": checkpoint.observation_id,
        "observation_sha256": wrong_hash,
        "observed_at": checkpoint.observed_at,
        "history": checkpoint.history,
        "execution": "NO_ORDER",
    }
    forged_id = stable_record_id("eligibility_history_checkpoint", fields)
    forged = EligibilityHistoryCheckpoint(
        record_id=forged_id,
        checkpoint_id=forged_id,
        **fields,
    )
    with pytest.raises(AcaContractConflictError, match="does not bind"):
        repository.save_contract(forged)
    assert repository.get_contract(forged_id) is None

    # Corruption introduced below the repository boundary must also be rejected
    # when the mutable projection is rebuilt after a restart.
    raw = checkpoint.model_dump(mode="python")
    raw["observation_sha256"] = wrong_hash
    import sqlite3

    connection = sqlite3.connect(tmp_path / "aca.sqlite")
    try:
        connection.execute("DELETE FROM aca_eligibility_history_current")
        connection.execute(
            "UPDATE aca_contract_record SET canonical_json=?, canonical_sha256=? "
            "WHERE record_id=?",
            (canonical_json(raw), content_sha256(raw), checkpoint.record_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(
        AcaStoredContractCorruptionError,
        match="observation binding mismatch",
    ):
        repository.rebuild_eligibility_history_current("m1")


def test_historical_replay_cannot_rewind_current_history_or_cursor(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    first = evaluate_and_persist_market(
        repository=repository, snapshot=snapshot, facts=_facts(snapshot, T0),
        policy=market20_v1_policy(), history=EligibilityHistory(),
        trigger=EligibilityTrigger.NEW_MARKET, run_id="history-replay",
    )
    second = evaluate_and_persist_market(
        repository=repository, snapshot=snapshot,
        facts=_facts(snapshot, T0 + timedelta(minutes=5)),
        policy=market20_v1_policy(), trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="history-replay",
    )
    evaluate_and_persist_market(
        repository=repository, snapshot=snapshot, facts=_facts(snapshot, T0),
        policy=market20_v1_policy(), history=EligibilityHistory(),
        trigger=EligibilityTrigger.NEW_MARKET, run_id="history-replay",
    )
    assert repository.current_eligibility_history("m1") == second.evaluation.history
    assert first.evaluation.history != second.evaluation.history

    first_scan = _scan(repository, run_id="cursor-first")
    work = repository.claim_scan_work(
        owner="worker", now=T0 + timedelta(seconds=2), lease_seconds=30
    )
    repository.ack_scan_work(
        work_id=work[0].work_id, owner="worker", now=T0 + timedelta(seconds=3)
    )
    second_scan = _scan(repository, run_id="cursor-second")
    work = repository.claim_scan_work(
        owner="worker", now=T0 + timedelta(seconds=4), lease_seconds=30
    )
    repository.ack_scan_work(
        work_id=work[0].work_id, owner="worker", now=T0 + timedelta(seconds=5)
    )
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) == second_scan.result.cursor

    repository.persist_successful_scan(
        build_scan_policy(
            policy_name="durable", run_id="policy", created_at=T0
        ),
        first_scan.result.cursor,
        first_scan.result.receipt,
        first_scan.result.selected_items,
    )
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) == second_scan.result.cursor


def _work_status(repository: CapitalAgentRepository, table: str, work_id: str) -> str:
    import sqlite3

    key = "schedule_id" if table == "aca_evaluation_work_item" else "work_id"
    connection = sqlite3.connect(repository._target)  # noqa: SLF001 - storage fixture
    try:
        row = connection.execute(
            f"SELECT status FROM {table} WHERE {key}=?", (work_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


def test_due_evaluation_is_leased_once_and_atomically_rolls_to_next_schedule(
    tmp_path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    initial = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0),
        policy=market20_v1_policy(),
        trigger=EligibilityTrigger.NEW_MARKET,
        run_id="due-initial",
    )
    due = initial.evaluation.next_evaluation.due_at

    first = repository.claim_due_evaluations(
        owner="worker-a", now=due, lease_seconds=30
    )
    assert len(first) == 1 and first[0].attempts == 1
    assert repository.claim_due_evaluations(
        owner="worker-b", now=due + timedelta(seconds=1), lease_seconds=30
    ) == ()

    completed = evaluate_and_persist_claimed_market(
        repository=repository,
        work=first[0],
        owner="worker-a",
        snapshot=snapshot,
        facts=_facts(snapshot, due + timedelta(seconds=2)),
        policy=market20_v1_policy(),
        run_id="due-complete",
        lease_checked_at=due + timedelta(seconds=3),
    )
    assert _work_status(
        repository, "aca_evaluation_work_item", first[0].work_id
    ) == "ACKED"
    assert repository.latest_next_evaluations() == (
        completed.evaluation.next_evaluation,
    )
    assert repository.claim_due_evaluations(
        owner="worker-b", now=due + timedelta(seconds=3), lease_seconds=30
    ) == ()

    restarted = CapitalAgentRepository(tmp_path / "aca.sqlite")
    next_due = completed.evaluation.next_evaluation.due_at
    next_work = restarted.claim_due_evaluations(
        owner="worker-b", now=next_due, lease_seconds=30
    )
    assert len(next_work) == 1
    assert next_work[0].work_id == completed.evaluation.next_evaluation.schedule_id


def test_due_evaluation_reclaims_expired_lease_and_supersedes_unclaimed_old_work(
    tmp_path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    snapshot = _snapshot()
    initial = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0),
        policy=market20_v1_policy(),
        trigger=EligibilityTrigger.NEW_MARKET,
        run_id="due-reclaim",
    )
    due = initial.evaluation.next_evaluation.due_at
    first = repository.claim_due_evaluations(
        owner="crashed", now=due, lease_seconds=5
    )
    reclaimed = repository.claim_due_evaluations(
        owner="recovery", now=due + timedelta(seconds=5), lease_seconds=30
    )
    assert reclaimed[0].work_id == first[0].work_id
    assert reclaimed[0].attempts == 2
    assert repository.fail_evaluation_work(
        work_id=reclaimed[0].work_id,
        owner="recovery",
        now=due + timedelta(seconds=6),
        error="UPSTREAM_UNAVAILABLE",
    ) == "PENDING"

    newer = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, due + timedelta(seconds=7)),
        policy=market20_v1_policy(),
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="event-supersedes-due",
    )
    assert _work_status(
        repository, "aca_evaluation_work_item", first[0].work_id
    ) == "SUPERSEDED"
    assert repository.latest_next_evaluations() == (
        newer.evaluation.next_evaluation,
    )


def test_periodic_cadence_collapses_ticks_and_serializes_reconciliation_lanes(
    tmp_path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = build_scan_policy(
        policy_name="cadence", run_id="cadence-policy", created_at=T0
    )
    as_of = T0 + timedelta(hours=7)
    created = repository.materialize_due_cadence(policy=policy, as_of=as_of)
    assert {item.lane for item in created} == {
        ScanLane.GAMMA_DELTA,
        ScanLane.DAILY_ANTI_ENTROPY,
    }
    # Seven missed hours produce one latest delta slot, not 84 backlog items.
    delta = next(item for item in created if item.lane is ScanLane.GAMMA_DELTA)
    assert delta.cadence_due_at == as_of
    assert repository.materialize_due_cadence(policy=policy, as_of=as_of) == ()

    leased = repository.claim_cadence_work(
        owner="scheduler", now=as_of, lease_seconds=60, limit=3
    )
    assert {item.order.lane for item in leased} == {
        ScanLane.GAMMA_DELTA,
        ScanLane.DAILY_ANTI_ENTROPY,
    }
    assert repository.claim_cadence_work(
        owner="competitor", now=as_of + timedelta(seconds=1), lease_seconds=60
    ) == ()

    for work in leased:
        completed = as_of + timedelta(seconds=2)
        run_and_persist_scan(
            repository=repository,
            fetch_page=lambda cursor, limit: KeysetPage.of((), None),
            lane=work.order.lane,
            policy=policy,
            scheduled_for=as_of,
            started_at=as_of,
            completed_at=completed,
            run_id=f"cadence-{work.order.lane.value}",
            cadence_work=work,
            cadence_owner="scheduler",
            lease_checked_at=completed,
        )
        assert _work_status(
            repository, "aca_cadence_work_item", work.work_id
        ) == "ACKED"

    # The 6h census becomes materializable only after daily reconciliation
    # releases their shared UNIVERSE_RECONCILIATION mutex.
    follow_up = repository.materialize_due_cadence(policy=policy, as_of=as_of)
    assert len(follow_up) == 1
    assert follow_up[0].lane is ScanLane.FULL_ACTIVE_CENSUS


def test_cadence_lease_recovery_and_nonempty_scan_waits_for_inner_ack(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = build_scan_policy(
        policy_name="cadence-recovery", run_id="cadence-recovery", created_at=T0
    )
    repository.materialize_due_cadence(policy=policy, as_of=T0)
    first = repository.claim_cadence_work(
        owner="crashed", now=T0, lease_seconds=5, limit=1
    )
    reclaimed = repository.claim_cadence_work(
        owner="recovery", now=T0 + timedelta(seconds=5), lease_seconds=30, limit=1
    )
    assert reclaimed[0].work_id == first[0].work_id
    assert reclaimed[0].attempts == 2

    scan = run_and_persist_scan(
        repository=repository,
        fetch_page=lambda cursor, limit: KeysetPage.of(
            ({"id": "m1", "updatedAt": T0.isoformat()},), None
        ),
        lane=reclaimed[0].order.lane,
        policy=policy,
        scheduled_for=T0 + timedelta(seconds=5),
        started_at=T0 + timedelta(seconds=5),
        completed_at=T0 + timedelta(seconds=6),
        run_id="cadence-inner-work",
        cadence_work=reclaimed[0],
        cadence_owner="recovery",
        lease_checked_at=T0 + timedelta(seconds=7),
    )
    assert _work_status(
        repository, "aca_cadence_work_item", reclaimed[0].work_id
    ) == "WAITING"
    inner = repository.claim_scan_work(
        owner="market-worker", now=T0 + timedelta(seconds=7), lease_seconds=30
    )
    repository.ack_scan_work(
        work_id=inner[0].work_id,
        owner="market-worker",
        now=T0 + timedelta(seconds=8),
    )
    assert _work_status(
        repository, "aca_cadence_work_item", reclaimed[0].work_id
    ) == "ACKED"
    assert repository.latest_cursor(reclaimed[0].order.lane) == scan.result.cursor


def test_newer_disabled_cadence_policy_supersedes_pending_old_policy(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    active = build_scan_policy(
        policy_name="active", run_id="active", created_at=T0
    )
    pending = repository.materialize_due_cadence(policy=active, as_of=T0)
    assert pending
    disabled = build_scan_policy(
        policy_name="disabled",
        run_id="disabled",
        created_at=T0 + timedelta(seconds=1),
        enabled=False,
    )
    assert repository.materialize_due_cadence(
        policy=disabled, as_of=T0 + timedelta(seconds=1)
    ) == ()
    assert repository.claim_cadence_work(
        owner="scheduler",
        now=T0 + timedelta(seconds=2),
        lease_seconds=30,
        limit=10,
    ) == ()
    assert all(
        _work_status(repository, "aca_cadence_work_item", item.work_id)
        == "SUPERSEDED"
        for item in pending
    )
    # A historical policy replay cannot roll desired cadence state backwards.
    assert repository.materialize_due_cadence(
        policy=active, as_of=T0 + timedelta(hours=1)
    ) == ()


def test_dead_lettered_cadence_slot_is_not_reported_as_new_work(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = build_scan_policy(
        policy_name="cadence-dead-letter",
        run_id="cadence-dead-letter",
        created_at=T0,
    )
    repository.materialize_due_cadence(policy=policy, as_of=T0)
    work = next(
        item
        for item in repository.claim_cadence_work(
            owner="scheduler", now=T0, lease_seconds=30, limit=3
        )
        if item.order.lane is ScanLane.GAMMA_DELTA
    )
    assert repository.fail_cadence_work(
        work_id=work.work_id,
        owner="scheduler",
        now=T0 + timedelta(seconds=1),
        error="PERMANENT",
        max_attempts=1,
    ) == "DEAD_LETTER"
    assert repository.materialize_due_cadence(policy=policy, as_of=T0) == ()

    next_tick = T0 + timedelta(seconds=policy.delta_interval_seconds)
    later = repository.materialize_due_cadence(policy=policy, as_of=next_tick)
    assert any(
        item.lane is work.order.lane and item.work_id != work.work_id
        for item in later
    )


def test_cadence_batch_claims_at_most_one_work_per_mutex_resource(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = build_scan_policy(
        policy_name="cadence-batch-mutex",
        run_id="cadence-batch-mutex",
        created_at=T0,
    )
    repository.materialize_due_cadence(policy=policy, as_of=T0)
    full = build_cadence_work_order(
        policy=policy,
        lane=ScanLane.FULL_ACTIVE_CENSUS,
        cadence_due_at=T0,
    )
    repository.save_contract(full)
    claimed = repository.claim_cadence_work(
        owner="scheduler", now=T0, lease_seconds=30, limit=10
    )
    assert len(claimed) == 2
    resources = tuple(item.order.resource for item in claimed)
    assert len(resources) == len(set(resources))


def test_expired_operational_lease_cannot_be_acked_with_earlier_business_clock(
    tmp_path,
) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    scan_policy = build_scan_policy(
        policy_name="expired-cadence",
        run_id="expired-cadence",
        created_at=T0,
    )
    repository.materialize_due_cadence(policy=scan_policy, as_of=T0)
    cadence = repository.claim_cadence_work(
        owner="late-worker", now=T0, lease_seconds=5, limit=1
    )[0]
    with pytest.raises(ValueError, match="not leased"):
        run_and_persist_scan(
            repository=repository,
            fetch_page=lambda cursor, limit: KeysetPage.of((), None),
            lane=cadence.order.lane,
            policy=scan_policy,
            scheduled_for=T0,
            started_at=T0,
            completed_at=T0 + timedelta(seconds=1),
            run_id="late-cadence",
            cadence_work=cadence,
            cadence_owner="late-worker",
            lease_checked_at=T0 + timedelta(seconds=6),
        )

    snapshot = _snapshot()
    initial = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0),
        policy=market20_v1_policy(),
        trigger=EligibilityTrigger.NEW_MARKET,
        run_id="expired-evaluation",
    )
    due = initial.evaluation.next_evaluation.due_at
    evaluation = repository.claim_due_evaluations(
        owner="late-evaluator", now=due, lease_seconds=5
    )[0]
    with pytest.raises(ValueError, match="not leased"):
        evaluate_and_persist_claimed_market(
            repository=repository,
            work=evaluation,
            owner="late-evaluator",
            snapshot=snapshot,
            facts=_facts(snapshot, due + timedelta(seconds=1)),
            policy=market20_v1_policy(),
            run_id="late-evaluation",
            lease_checked_at=due + timedelta(seconds=6),
        )
