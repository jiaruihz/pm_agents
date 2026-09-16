from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.alpha_capital_agent.allocator import (
    CapitalActionType,
    CapitalPlanStatus,
    allocate_capital,
)
from src.alpha_capital_agent.capital import (
    AccountCompleteness,
    AccountSnapshotSeal,
    ExecutableLevel,
    OpportunityRecord,
    PositionExposure,
    ReplacementHistory,
    default_capital_policy,
    record_external_replacement_execution,
)
from src.alpha_capital_agent.storage import CapitalAgentRepository
from src.polymarket_alpha.contracts import canonical_json
from src.polymarket_alpha.security import audit_source_tree


D = Decimal
NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)
FRESH = NOW + timedelta(days=1)


def snap(
    cash: str = "100",
    *,
    at: datetime = NOW,
    completeness: AccountCompleteness = AccountCompleteness.AUTHENTICATED_COMPLETE,
) -> AccountSnapshotSeal:
    return AccountSnapshotSeal(
        account_id="acct",
        decision_as_of=at,
        free_cash=D(cash),
        completeness=completeness,
        input_refs=("authenticated-account-receipt",),
    )


def opp(
    name: str = "new",
    *,
    q: str = "0.80",
    price: str = "0.40",
    size: str = "100",
    market: str = "m2",
    event: str = "e2",
    cluster: str = "c2",
    fresh_until: datetime = FRESH,
) -> OpportunityRecord:
    return OpportunityRecord(
        opportunity_id=name,
        market_id=market,
        event_id=event,
        cluster_id=cluster,
        maturity="near",
        direction="YES",
        q_cons=D(q),
        release_days=D("1"),
        research_fresh_until=fresh_until,
        buy_levels=(ExecutableLevel(price=D(price), size=D(size)),),
        input_refs=(f"research:{name}",),
    )


def pos(
    name: str = "old",
    *,
    q: str = "0.50",
    bid: str | None = "0.50",
    shares: str = "100",
    market: str = "m1",
    event: str = "e1",
    cluster: str = "c1",
    fresh_until: datetime = FRESH,
    locked: bool = False,
) -> PositionExposure:
    return PositionExposure(
        position_id=name,
        market_id=market,
        event_id=event,
        cluster_id=cluster,
        maturity="near",
        direction="YES",
        shares=D(shares),
        mark_price=D("0.50"),
        q_cons=D(q),
        release_days=D("1"),
        research_fresh_until=fresh_until,
        sell_levels=(
            ()
            if bid is None
            else (ExecutableLevel(price=D(bid), size=D("1000")),)
        ),
        locked=locked,
        input_refs=(f"position:{name}",),
    )


def test_free_cash_entry_uses_nav_buffer_caps_and_no_order() -> None:
    plan = allocate_capital(snap(), (), (opp(),), default_capital_policy())
    entry = next(
        item for item in plan.actions if item.action_type is CapitalActionType.ENTER_SHADOW
    )

    assert plan.mark_nav == D("100")
    assert plan.cash_buffer_required == D("25")
    assert entry.buy_cost == D("3")  # 3% market cap, not all available cash.
    assert plan.cash_after == D("97")
    assert plan.execution_capability == "NO_ORDER"
    assert plan.status is CapitalPlanStatus.PROPOSED


def test_low_edge_entry_is_sized_by_fractional_kelly_before_hard_cap() -> None:
    plan = allocate_capital(
        snap(),
        (),
        (opp(q="0.45", price="0.40"),),
        default_capital_policy(),
    )
    entry = next(
        item for item in plan.actions if item.action_type is CapitalActionType.ENTER_SHADOW
    )
    expected = D("100") * D("0.10") * ((D("0.45") - D("0.40")) / D("0.60"))

    assert abs(entry.buy_cost - expected) < D("1e-26")
    assert entry.buy_cost < D("3")


def test_replacement_requires_two_confirmations_and_exact_partial_legs() -> None:
    policy = default_capital_policy()
    old = pos()
    new = opp()
    first = allocate_capital(snap("0"), (old,), (new,), policy, run_id="first")
    watch = next(
        item for item in first.actions if item.action_type is CapitalActionType.REPLACE_WATCH
    )

    assert D("0") < watch.sell_shares < old.shares
    assert watch.sell_proceeds == watch.buy_cost
    assert first.cash_after == D("0")
    assert first.status is CapitalPlanStatus.WATCHING
    assert first.replacement_histories[0].consecutive_confirmations == 1

    at = NOW + timedelta(minutes=15)
    second = allocate_capital(
        snap("0", at=at),
        (old,),
        (new,),
        policy,
        histories=first.replacement_histories,
        run_id="second",
    )
    replacement = next(
        item
        for item in second.actions
        if item.action_type is CapitalActionType.REPLACE_REVIEW
    )

    assert replacement.sell_proceeds == replacement.buy_cost
    assert second.cash_after == D("0")  # no proceeds from unsold depth appear.
    assert second.cash_floor_effective == D("0")
    assert second.after_conservative_value > second.do_nothing_conservative_value
    assert second.replacement_histories[0].consecutive_confirmations == 2


def test_confirmation_inside_fifteen_minutes_stays_watch() -> None:
    first = allocate_capital(
        snap("0"), (pos(),), (opp(),), default_capital_policy()
    )
    second = allocate_capital(
        snap("0", at=NOW + timedelta(minutes=14)),
        (pos(),),
        (opp(),),
        default_capital_policy(),
        histories=first.replacement_histories,
    )

    assert any(
        item.action_type is CapitalActionType.REPLACE_WATCH
        for item in second.actions
    )
    assert second.replacement_histories[0].consecutive_confirmations == 1


def test_failed_intermediate_tick_resets_replacement_confirmation_streak() -> None:
    policy = default_capital_policy()
    first = allocate_capital(snap("0"), (pos(),), (opp(),), policy)
    interrupted = allocate_capital(
        snap("0", at=NOW + timedelta(minutes=5)),
        (pos(),),
        (opp(q="0.42"),),
        policy,
        histories=first.replacement_histories,
    )
    resumed = allocate_capital(
        snap("0", at=NOW + timedelta(minutes=15)),
        (pos(),),
        (opp(),),
        policy,
        histories=interrupted.replacement_histories,
    )

    assert interrupted.replacement_histories[0].consecutive_confirmations == 0
    assert any(
        item.action_type is CapitalActionType.REPLACE_WATCH
        for item in resumed.actions
    )
    assert resumed.replacement_histories[0].consecutive_confirmations == 1


def test_locked_old_position_never_becomes_replacement_proceeds() -> None:
    plan = allocate_capital(
        snap("0"),
        (pos(bid=None, locked=True),),
        (opp(),),
        default_capital_policy(),
    )

    locked = next(
        item for item in plan.actions if item.action_type is CapitalActionType.CAPITAL_LOCKED
    )
    assert locked.reason_codes == ("NO_EXECUTABLE_BID_DEPTH",)
    assert plan.cash_after == D("0")


def test_stale_held_research_blocks_replacement() -> None:
    plan = allocate_capital(
        snap("0"),
        (pos(fresh_until=NOW - timedelta(seconds=1)),),
        (opp(),),
        default_capital_policy(),
    )

    locked = next(
        item for item in plan.actions if item.action_type is CapitalActionType.CAPITAL_LOCKED
    )
    assert "STALE_HELD_RESEARCH" in locked.reason_codes
    assert not any(
        item.action_type in {
            CapitalActionType.REPLACE_WATCH,
            CapitalActionType.REPLACE_REVIEW,
        }
        for item in plan.actions
    )


def test_public_only_account_fails_closed() -> None:
    plan = allocate_capital(
        snap(completeness=AccountCompleteness.PUBLIC_ONLY),
        (),
        (opp(),),
        default_capital_policy(),
    )

    assert plan.status is CapitalPlanStatus.DATA_BLOCKED
    assert plan.actions[-1].action_type is CapitalActionType.DATA_BLOCKED
    assert plan.cash_after == plan.cash_before


def test_deterministic_tie_break_and_exact_replay() -> None:
    a = opp("a", market="am", event="ae", cluster="ac")
    b = opp("b", market="bm", event="be", cluster="bc")
    first = allocate_capital(snap(), (), (b, a), default_capital_policy())
    second = allocate_capital(snap(), (), (b, a), default_capital_policy())

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    entries = [
        item
        for item in first.actions
        if item.action_type is CapitalActionType.ENTER_SHADOW
    ]
    assert entries[0].opportunity_id == "a"


def test_one_old_position_cannot_fund_two_replacements_in_one_plan() -> None:
    plan = allocate_capital(
        snap("0"),
        (pos(),),
        (
            opp("a", market="am", event="ae", cluster="ac"),
            opp("b", market="bm", event="be", cluster="bc"),
        ),
        default_capital_policy(),
    )

    replacements = [
        item
        for item in plan.actions
        if item.action_type
        in {CapitalActionType.REPLACE_WATCH, CapitalActionType.REPLACE_REVIEW}
    ]
    assert len(replacements) == 1


def test_recent_execution_enforces_pair_cooldown() -> None:
    history = ReplacementHistory(
        position_id="old",
        opportunity_id="new",
        last_executed_at=NOW - timedelta(hours=1),
        last_execution_ref="fill:old-new",
    )
    plan = allocate_capital(
        snap("0"),
        (pos(),),
        (opp(),),
        default_capital_policy(),
        histories=(history,),
    )

    assert any("REPLACEMENT_COOLDOWN" in item.reason_codes for item in plan.actions)
    assert not any(
        item.action_type in {
            CapitalActionType.REPLACE_WATCH,
            CapitalActionType.REPLACE_REVIEW,
        }
        for item in plan.actions
    )


def test_confirmed_review_only_enters_cooldown_after_external_fill_receipt() -> None:
    policy = default_capital_policy()
    first = allocate_capital(snap("0"), (pos(),), (opp(),), policy)
    confirmed_at = NOW + timedelta(minutes=15)
    second = allocate_capital(
        snap("0", at=confirmed_at),
        (pos(),),
        (opp(),),
        policy,
        histories=first.replacement_histories,
    )
    confirmed = second.replacement_histories[0]
    executed = record_external_replacement_execution(
        confirmed,
        executed_at=confirmed_at,
        execution_receipt_ref="external-fill:old-new:1",
    )
    third = allocate_capital(
        snap("0", at=confirmed_at + timedelta(minutes=1)),
        (pos(),),
        (opp(),),
        policy,
        histories=(executed,),
    )

    assert any("REPLACEMENT_COOLDOWN" in item.reason_codes for item in third.actions)
    assert not any(
        item.action_type
        in {CapitalActionType.REPLACE_WATCH, CapitalActionType.REPLACE_REVIEW}
        for item in third.actions
    )


def test_policy_and_plan_are_append_only_replayable(tmp_path) -> None:
    repository = CapitalAgentRepository(tmp_path / "aca.sqlite")
    policy = default_capital_policy()
    plan = allocate_capital(snap(), (), (opp(),), policy)

    first = repository.save_contracts_atomic((policy, plan))
    second = repository.save_contracts_atomic((policy, plan))

    assert first == second
    assert repository.get_contract_json(policy.record_id) == canonical_json(policy)
    assert repository.get_contract_json(plan.record_id) == canonical_json(plan)


def test_float_rejected_and_source_has_no_external_capabilities() -> None:
    with pytest.raises(ValueError):
        ExecutableLevel(price=0.5, size=D("1"))
    assert audit_source_tree("src/alpha_capital_agent").violations == ()
