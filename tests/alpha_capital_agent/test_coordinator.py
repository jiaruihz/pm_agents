from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.alpha_capital_agent.allocator import CapitalActionType
from src.alpha_capital_agent.capital import (
    AccountCompleteness,
    AccountSnapshotSeal,
    ExecutableLevel,
    OpportunityRecord,
    default_capital_policy,
)
from src.alpha_capital_agent.coordinator import (
    build_and_persist_capital_plan,
    evaluate_and_persist_market,
    run_and_persist_scan,
)
from src.alpha_capital_agent.policy import market20_v1_policy
from src.alpha_capital_agent.scheduler import (
    KeysetPage,
    ScanLane,
    build_scan_policy,
)
from src.alpha_capital_agent.storage import CapitalAgentRepository
from src.alpha_capital_agent.universe import (
    AdmissionTrigger,
    EligibilityHistory,
    EligibilityTrigger,
    MarketabilityFacts,
    UniverseState,
)
from src.polymarket_alpha.contracts import (
    MarketIdentity,
    MarketSnapshot,
    MarketStatus,
    stable_record_id,
)


D = Decimal
T0 = datetime(2026, 8, 29, tzinfo=timezone.utc)
RULES = (
    "This market resolves YES if the official final result confirms the stated "
    "event before its deadline and otherwise resolves NO under the public rules."
)


def _snapshot() -> MarketSnapshot:
    identity = MarketIdentity(
        event_id="e1",
        market_id="m1",
        condition_id="condition-m1",
        yes_token_id="yes-m1",
        no_token_id="no-m1",
    )
    fields = {
        "run_id": "snapshot-run",
        "created_at": T0,
        "source": "fixture",
        "source_version": "v1",
        "identity": identity,
        "title": "Fixture market",
        "question": "Will the fixture event happen?",
        "slug": "fixture-market",
        "status": MarketStatus.ACTIVE,
        "end_at": T0 + timedelta(days=30),
        "tags": (),
        "rules_raw": RULES,
        "volume": D("5000"),
        "liquidity": D("1000"),
        "source_observed_at": T0,
        "ingested_at": T0,
    }
    return MarketSnapshot(
        record_id=stable_record_id("fixture_market_snapshot", fields), **fields
    )


def _facts(snapshot: MarketSnapshot, at: datetime) -> MarketabilityFacts:
    return MarketabilityFacts(
        market_id="m1",
        snapshot_id=snapshot.record_id,
        snapshot_sha256=snapshot.canonical_sha256,
        observed_at=at,
        upstream_updated_at=at,
        accepting_orders=True,
        enable_order_book=True,
        binary_paired=True,
        public_link_available=True,
        restricted=False,
        best_bid=D("0.45"),
        best_ask=D("0.50"),
        spread=D("0.05"),
        liquidity=D("1000"),
        volume=D("5000"),
        executable_depth=D("100"),
        source_artifact_ids=(f"gamma:{at.isoformat()}",),
    )


def test_universe_coordinator_admits_crossover_and_replays_atomically(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = market20_v1_policy()
    snapshot = _snapshot()
    first = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0),
        policy=policy,
        history=EligibilityHistory(),
        trigger=EligibilityTrigger.NEW_MARKET,
        run_id="universe-run",
    )
    assert first.evaluation.history.state is UniverseState.NEAR_ELIGIBLE_WATCH
    assert first.admission_episode is None

    second = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0 + timedelta(minutes=5)),
        policy=policy,
        history=first.evaluation.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="universe-run",
    )
    replay = evaluate_and_persist_market(
        repository=repository,
        snapshot=snapshot,
        facts=_facts(snapshot, T0 + timedelta(minutes=5)),
        policy=policy,
        history=first.evaluation.history,
        trigger=EligibilityTrigger.DELTA_SCAN,
        run_id="universe-run",
    )

    assert second.evaluation.history.state is UniverseState.ELIGIBLE_UNRESEARCHED
    assert second.admission_episode is not None
    assert second.admission_episode.trigger is AdmissionTrigger.MARKETABILITY_CROSSOVER
    assert replay == second
    assert len(repository.list_contracts("MarketAdmissionEpisode")) == 1


def test_scan_coordinator_seals_policy_cursor_and_receipt(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = build_scan_policy(
        policy_name="shadow-v1", run_id="scan-policy", created_at=T0
    )
    page = KeysetPage.of(
        ({"id": "m1", "updatedAt": T0.isoformat()},), None
    )

    result = run_and_persist_scan(
        repository=repository,
        fetch_page=lambda cursor, limit: page,
        lane=ScanLane.GAMMA_DELTA,
        policy=policy,
        prior_cursor=None,
        scheduled_for=T0,
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        run_id="scan-run",
    )

    assert result.result.receipt.coverage_complete is True
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) is None
    claimed = repository.claim_scan_work(
        owner="test-worker", now=T0 + timedelta(seconds=2), lease_seconds=30
    )
    assert claimed[0].market_id == "m1"
    repository.ack_scan_work(
        work_id=claimed[0].work_id, owner="test-worker", now=T0 + timedelta(seconds=3)
    )
    assert repository.latest_cursor(ScanLane.GAMMA_DELTA) == result.result.cursor
    assert len(repository.list_contracts("UniverseScanRunReceipt")) == 1


def test_capital_coordinator_persists_policy_and_shadow_plan(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    account = AccountSnapshotSeal(
        account_id="acct",
        decision_as_of=T0,
        free_cash=D("100"),
        completeness=AccountCompleteness.AUTHENTICATED_COMPLETE,
        input_refs=("authenticated-account-receipt",),
    )
    opportunity = OpportunityRecord(
        opportunity_id="o1",
        market_id="m2",
        event_id="e2",
        cluster_id="c2",
        maturity="near",
        direction="YES",
        q_cons=D("0.80"),
        release_days=D("1"),
        research_fresh_until=T0 + timedelta(days=1),
        buy_levels=(ExecutableLevel(price=D("0.40"), size=D("100")),),
        input_refs=("accepted-blind-result:o1",),
    )

    result = build_and_persist_capital_plan(
        repository=repository,
        snapshot=account,
        positions=(),
        opportunities=(opportunity,),
        policy=default_capital_policy(),
        run_id="capital-run",
    )

    assert any(
        item.action_type is CapitalActionType.ENTER_SHADOW
        for item in result.plan.actions
    )
    assert result.plan.execution_capability == "NO_ORDER"
    assert len(repository.list_contracts("CapitalPlan")) == 1
