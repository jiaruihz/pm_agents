from decimal import Decimal

import pytest

from src.platform.quote_runtime.target_order_set import (
    AuthoritativeOwnOrder,
    TargetOrder,
    TargetOrderSet,
    TargetReconciliationError,
    reconcile_target_order_set,
)


def target(side: str = "BUY", price: str = "0.49", level: int = 0) -> TargetOrder:
    return TargetOrder("token", side, level, price, "5", True, None, "continue-v1", "d1")


def target_set(*targets: TargetOrder, generation: int = 1) -> TargetOrderSet:
    return TargetOrderSet("sole-owner", "weather-w2", "weather-mm", generation, "2026-08-30T00:00:00Z", "NORMAL", tuple(targets))


def observed(
    *,
    price: str = "0.49",
    size: str = "5",
    clean: bool = True,
    order_id: str = "o1",
    status: str = "LIVE",
    cancel_confirmed: bool = False,
) -> AuthoritativeOwnOrder:
    return AuthoritativeOwnOrder(
        order_id,
        "sole-owner",
        "token",
        "BUY",
        0,
        price,
        size,
        status,
        clean,
        not clean,
        cancel_confirmed,
    )


def test_empty_observation_creates_target_deterministically() -> None:
    desired = target_set(target())
    first = reconcile_target_order_set(desired, [])
    second = reconcile_target_order_set(desired, [])
    assert [action.kind for action in first.actions] == ["CREATE"]
    assert first == second


def test_exact_order_is_kept_and_duplicate_cancelled() -> None:
    desired = target_set(target())
    result = reconcile_target_order_set(desired, [observed(order_id="o2"), observed(order_id="o1")])
    assert [(action.kind, action.order_id) for action in result.actions] == [
        ("KEEP", "o1"),
        ("CANCEL", "o2"),
    ]


def test_replace_is_two_phase_and_never_creates_before_cancel_confirmation() -> None:
    result = reconcile_target_order_set(target_set(target()), [observed(price="0.48")])
    assert [action.kind for action in result.actions] == ["CANCEL"]
    assert result.actions[0].reason == "replace_requires_confirmed_cancel"


def test_replace_waits_for_authoritative_cancel_confirmation() -> None:
    desired = target_set(target(price="0.55"), generation=2)
    blocked = reconcile_target_order_set(
        desired,
        [observed(order_id="old", price="0.50", status="CANCELLED")],
    )
    assert blocked.status == "BLOCKED"
    assert [action.kind for action in blocked.actions] == ["BLOCK"]

    ready = reconcile_target_order_set(
        desired,
        [
            observed(
                order_id="old",
                price="0.50",
                status="CANCELLED",
                cancel_confirmed=True,
            )
        ],
    )
    assert ready.status == "READY"
    assert [action.kind for action in ready.actions] == ["CREATE"]


def test_ambiguous_truth_blocks_all_actions() -> None:
    result = reconcile_target_order_set(target_set(target()), [observed(clean=False)])
    assert result.status == "BLOCKED"
    assert [action.kind for action in result.actions] == ["BLOCK"]


def test_foreign_owner_blocks_instead_of_cancelling_other_strategy() -> None:
    foreign = AuthoritativeOwnOrder("o2", "other", "token", "BUY", 0, "0.49", "5", "LIVE", True, False, False)
    result = reconcile_target_order_set(target_set(target()), [foreign])
    assert result.status == "BLOCKED"
    assert result.blockers == ("foreign_owner:o2",)


def test_self_cross_and_non_monotone_generation_rejected() -> None:
    with pytest.raises(TargetReconciliationError, match="self-cross"):
        target_set(target("BUY", "0.51"), target("SELL", "0.50", 1))
    with pytest.raises(TargetReconciliationError, match="generation"):
        reconcile_target_order_set(target_set(target(), generation=2), [], previous_generation=2)


def test_decimal_only_contract() -> None:
    with pytest.raises(TargetReconciliationError, match="exact decimal"):
        target(price=0.49)
    assert target().price == Decimal("0.49")
