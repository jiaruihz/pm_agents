"""Pure sole-owner target-order reconciliation.

This module computes an idempotent diff only.  It never talks to a venue and
never assumes that a cancel request is a cancel confirmation.  A side-effect
adapter may execute the returned actions serially, then feed authoritative
order truth into the next generation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal, Mapping


Side = Literal["BUY", "SELL"]
ActionKind = Literal["KEEP", "CANCEL", "CREATE", "BLOCK"]


class TargetReconciliationError(ValueError):
    """The target set or authoritative observation is unsafe."""


def _text(value: Any, name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise TargetReconciliationError(f"{name} is required")
    return text


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TargetReconciliationError(f"{name} must be an exact decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TargetReconciliationError(f"invalid {name}") from exc
    if not parsed.is_finite():
        raise TargetReconciliationError(f"{name} must be finite")
    return parsed


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            field: _canonical(getattr(value, field))
            for field in value.__dataclass_fields__
            if field != "identity"
        }
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    return value


def _identity(kind: str, value: Any) -> str:
    import json

    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return f"{kind}:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class TargetOrder:
    token_id: str
    side: Side
    level: int
    price: Decimal | str | int
    size: Decimal | str | int
    post_only: bool
    expiry_utc: str | None
    continuation_policy_id: str
    decision_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_id", _text(self.token_id, "token_id"))
        side = str(self.side).upper()
        if side not in {"BUY", "SELL"}:
            raise TargetReconciliationError("side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        if self.level < 0:
            raise TargetReconciliationError("level cannot be negative")
        object.__setattr__(self, "price", _decimal(self.price, "price"))
        object.__setattr__(self, "size", _decimal(self.size, "size"))
        if not Decimal("0") < self.price < Decimal("1"):
            raise TargetReconciliationError("price must be between zero and one")
        if self.size <= 0:
            raise TargetReconciliationError("size must be positive")
        object.__setattr__(
            self,
            "continuation_policy_id",
            _text(self.continuation_policy_id, "continuation_policy_id"),
        )
        object.__setattr__(self, "decision_id", _text(self.decision_id, "decision_id"))

    @property
    def key(self) -> tuple[str, Side, int]:
        return self.token_id, self.side, self.level


@dataclass(frozen=True)
class TargetOrderSet:
    owner_id: str
    strategy_head: str
    capital_sleeve: str
    generation: int
    generated_at_utc: str
    mode: Literal["NORMAL", "CANCEL_ONLY", "CLOSED_ONLY"]
    targets: tuple[TargetOrder, ...]
    identity: str = ""

    def __post_init__(self) -> None:
        for name in ("owner_id", "strategy_head", "capital_sleeve", "generated_at_utc"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.generation < 0:
            raise TargetReconciliationError("generation cannot be negative")
        mode = str(self.mode).upper()
        if mode not in {"NORMAL", "CANCEL_ONLY", "CLOSED_ONLY"}:
            raise TargetReconciliationError("unsupported target-set mode")
        object.__setattr__(self, "mode", mode)
        targets = tuple(self.targets)
        if any(not isinstance(target, TargetOrder) for target in targets):
            raise TargetReconciliationError("targets must contain TargetOrder values")
        if mode != "NORMAL" and targets:
            raise TargetReconciliationError("cancel/closed-only sets cannot create targets")
        keys = [target.key for target in targets]
        if len(keys) != len(set(keys)):
            raise TargetReconciliationError("target keys must be unique")
        _validate_no_self_cross(targets)
        object.__setattr__(self, "targets", tuple(sorted(targets, key=lambda item: item.key)))
        object.__setattr__(self, "identity", _identity("target-set", self))


def _validate_no_self_cross(targets: Iterable[TargetOrder]) -> None:
    by_token: dict[str, list[TargetOrder]] = {}
    for target in targets:
        by_token.setdefault(target.token_id, []).append(target)
    for token_id, rows in by_token.items():
        buys = [row.price for row in rows if row.side == "BUY"]
        sells = [row.price for row in rows if row.side == "SELL"]
        if buys and sells and max(buys) >= min(sells):
            raise TargetReconciliationError(f"self-cross for token {token_id}")


@dataclass(frozen=True)
class AuthoritativeOwnOrder:
    order_id: str
    owner_id: str
    token_id: str
    side: Side
    level: int
    price: Decimal | str | int
    remaining_size: Decimal | str | int
    status: str
    clean: bool
    reconciliation_required: bool
    cancel_confirmed: bool
    target_set_identity: str | None = None

    def __post_init__(self) -> None:
        for name in ("order_id", "owner_id", "token_id", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        side = str(self.side).upper()
        if side not in {"BUY", "SELL"}:
            raise TargetReconciliationError("side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        if self.level < 0:
            raise TargetReconciliationError("level cannot be negative")
        object.__setattr__(self, "price", _decimal(self.price, "price"))
        object.__setattr__(self, "remaining_size", _decimal(self.remaining_size, "remaining_size"))
        if self.remaining_size < 0:
            raise TargetReconciliationError("remaining_size cannot be negative")

    @property
    def key(self) -> tuple[str, Side, int]:
        return self.token_id, self.side, self.level

    @property
    def live(self) -> bool:
        return self.status.upper() in {"LIVE", "OPEN", "PARTIAL"} and self.remaining_size > 0


@dataclass(frozen=True)
class ReconcileAction:
    kind: ActionKind
    reason: str
    action_id: str
    order_id: str | None = None
    target: TargetOrder | None = None


@dataclass(frozen=True)
class TargetReconciliation:
    target_set_identity: str
    status: Literal["READY", "BLOCKED"]
    actions: tuple[ReconcileAction, ...]
    blockers: tuple[str, ...]


def _action(
    target_set: TargetOrderSet,
    kind: ActionKind,
    reason: str,
    *,
    order_id: str | None = None,
    target: TargetOrder | None = None,
) -> ReconcileAction:
    seed = {
        "kind": kind,
        "order_id": order_id,
        "reason": reason,
        "target": target,
        "target_set_identity": target_set.identity,
    }
    return ReconcileAction(kind, reason, _identity("target-action", seed), order_id, target)


def reconcile_target_order_set(
    target_set: TargetOrderSet,
    observed_orders: Iterable[AuthoritativeOwnOrder],
    *,
    previous_generation: int | None = None,
) -> TargetReconciliation:
    """Return a deterministic diff; ambiguous truth blocks every side effect."""
    if previous_generation is not None and target_set.generation <= previous_generation:
        raise TargetReconciliationError("generation must increase monotonically")
    orders = tuple(observed_orders)
    foreign = sorted(order.order_id for order in orders if order.owner_id != target_set.owner_id)
    ambiguous = sorted(
        order.order_id
        for order in orders
        if order.owner_id == target_set.owner_id
        and (
            not order.clean
            or order.reconciliation_required
            or (
                order.status.upper() in {"CANCELLED", "CANCELED"}
                and not order.cancel_confirmed
            )
            or (
                order.status.upper() not in {
                    "LIVE",
                    "OPEN",
                    "PARTIAL",
                    "FILLED",
                    "EXPIRED",
                    "REJECTED",
                    "CANCELLED",
                    "CANCELED",
                }
                and order.remaining_size > 0
            )
        )
    )
    blockers = tuple(
        [f"foreign_owner:{order_id}" for order_id in foreign]
        + [f"ambiguous_order:{order_id}" for order_id in ambiguous]
    )
    if blockers:
        return TargetReconciliation(
            target_set.identity,
            "BLOCKED",
            tuple(_action(target_set, "BLOCK", blocker) for blocker in blockers),
            blockers,
        )

    live_by_key: dict[tuple[str, Side, int], list[AuthoritativeOwnOrder]] = {}
    for order in orders:
        if order.owner_id == target_set.owner_id and order.live:
            live_by_key.setdefault(order.key, []).append(order)
    targets = {target.key: target for target in target_set.targets}
    actions: list[ReconcileAction] = []

    for key in sorted(set(live_by_key) | set(targets)):
        existing = sorted(live_by_key.get(key, []), key=lambda item: item.order_id)
        target = targets.get(key)
        if target is None:
            actions.extend(
                _action(target_set, "CANCEL", "target_removed", order_id=order.order_id)
                for order in existing
            )
            continue
        exact = [
            order
            for order in existing
            if order.price == target.price and order.remaining_size == target.size
        ]
        if exact:
            keeper = exact[0]
            actions.append(
                _action(target_set, "KEEP", "authoritative_order_matches", order_id=keeper.order_id, target=target)
            )
            actions.extend(
                _action(target_set, "CANCEL", "duplicate_live_order", order_id=order.order_id)
                for order in existing
                if order.order_id != keeper.order_id
            )
            continue
        if existing:
            actions.extend(
                _action(target_set, "CANCEL", "replace_requires_confirmed_cancel", order_id=order.order_id)
                for order in existing
            )
            # No CREATE until the next generation observes every cancel as
            # confirmed.  This is the central cancel-race boundary.
            continue
        if target_set.mode == "NORMAL":
            actions.append(_action(target_set, "CREATE", "target_missing", target=target))

    return TargetReconciliation(target_set.identity, "READY", tuple(actions), ())
