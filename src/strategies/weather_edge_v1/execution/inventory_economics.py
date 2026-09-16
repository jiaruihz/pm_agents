"""Pure Decimal accounting contracts for Weather-first inventory episodes.

This module deliberately models economics, not exchange execution.  It makes
the W1 comparison auditable: every inventory arm is evaluated against the
same scenario denominator and realised wealth ledger.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ExecutionContractError, JsonContract, canonical_json


ARM_NAMES = (
    "immediate_taker_exit",
    "passive_then_fallback",
    "complement_then_merge",
    "hold_to_settlement",
    "no_action",
)
INCENTIVE_NAMESPACES = frozenset(
    {"maker_rebate", "taker_rebate", "lp_reward", "holding_reward"}
)


def _text(value: Any, name: str) -> str:
    result = "" if value is None else str(value).strip()
    if not result:
        raise ExecutionContractError(f"missing required field: {name}")
    return result


def _decimal(value: Decimal | str | int, name: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ExecutionContractError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionContractError(f"invalid decimal for {name}: {value!r}") from exc
    if not result.is_finite():
        raise ExecutionContractError(f"{name} must be finite")
    if nonnegative and result < 0:
        raise ExecutionContractError(f"{name} must be non-negative")
    return result


def _decimal_mapping(value: Mapping[str, Decimal | str | int], name: str, *, nonnegative: bool = False) -> Mapping[str, Decimal]:
    if not isinstance(value, Mapping):
        raise ExecutionContractError(f"{name} must be a mapping")
    parsed: dict[str, Decimal] = {}
    for raw_key, raw_amount in value.items():
        key = _text(raw_key, f"{name} key")
        if key in parsed:
            raise ExecutionContractError(f"duplicate key in {name}: {key}")
        parsed[key] = _decimal(raw_amount, f"{name}.{key}", nonnegative=nonnegative)
    return MappingProxyType(parsed)


def canonical_identity(kind: str, value: JsonContract | Mapping[str, Any]) -> str:
    """Stable content address for a contract, suitable for replay joins."""
    prefix = _text(kind, "identity kind")
    payload = value.to_json() if isinstance(value, JsonContract) else dict(value)
    digest = hashlib.sha256(f"{prefix}|{canonical_json(payload)}".encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class PositionLeg(JsonContract):
    """One position quantity in a binary-market condition topology."""

    condition_id: str
    token_id: str
    shares: Decimal | str | int

    def __post_init__(self) -> None:
        object.__setattr__(self, "condition_id", _text(self.condition_id, "condition_id"))
        object.__setattr__(self, "token_id", _text(self.token_id, "token_id"))
        object.__setattr__(self, "shares", _decimal(self.shares, "shares", nonnegative=True))

    @property
    def topology_key(self) -> tuple[str, str]:
        return (self.condition_id, self.token_id)


@dataclass(frozen=True)
class PendingFillCorner(JsonContract):
    """A possible outstanding-order fill corner; buys and sells are independent."""

    corner_id: str
    buy_fills: Mapping[str, Decimal | str | int] = field(default_factory=dict)
    sell_fills: Mapping[str, Decimal | str | int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "corner_id", _text(self.corner_id, "corner_id"))
        object.__setattr__(self, "buy_fills", _decimal_mapping(self.buy_fills, "buy_fills", nonnegative=True))
        object.__setattr__(self, "sell_fills", _decimal_mapping(self.sell_fills, "sell_fills", nonnegative=True))


@dataclass(frozen=True)
class TargetInventoryVector(JsonContract):
    """K-scenario target inventory with one fixed condition/token topology."""

    condition_id: str
    scenario_legs: Mapping[str, tuple[PositionLeg, ...]]
    pending_fill_corners: tuple[PendingFillCorner, ...] = ()

    def __post_init__(self) -> None:
        condition_id = _text(self.condition_id, "condition_id")
        if not isinstance(self.scenario_legs, Mapping) or not self.scenario_legs:
            raise ExecutionContractError("scenario_legs must contain one or more scenarios")
        scenarios: dict[str, tuple[PositionLeg, ...]] = {}
        expected_topology: frozenset[tuple[str, str]] | None = None
        for raw_name, raw_legs in self.scenario_legs.items():
            name = _text(raw_name, "scenario key")
            legs = tuple(raw_legs)
            if not legs or any(not isinstance(leg, PositionLeg) for leg in legs):
                raise ExecutionContractError(f"scenario {name} must contain PositionLeg values")
            topology = frozenset(leg.topology_key for leg in legs)
            if len(topology) != len(legs):
                raise ExecutionContractError(f"scenario {name} has duplicate position topology")
            if any(leg.condition_id != condition_id for leg in legs):
                raise ExecutionContractError("all position legs must belong to condition_id")
            if expected_topology is None:
                expected_topology = topology
            elif topology != expected_topology:
                raise ExecutionContractError("all scenarios must have identical condition/token topology")
            scenarios[name] = legs
        corners = tuple(self.pending_fill_corners)
        if len({corner.corner_id for corner in corners}) != len(corners) or any(not isinstance(corner, PendingFillCorner) for corner in corners):
            raise ExecutionContractError("pending_fill_corners must have unique PendingFillCorner ids")
        token_ids = {token_id for _, token_id in expected_topology or ()}
        for corner in corners:
            if not set(corner.buy_fills).issubset(token_ids) or not set(corner.sell_fills).issubset(token_ids):
                raise ExecutionContractError("pending fill corner references token outside condition topology")
        object.__setattr__(self, "condition_id", condition_id)
        object.__setattr__(self, "scenario_legs", MappingProxyType(scenarios))
        object.__setattr__(self, "pending_fill_corners", corners)

    @property
    def identity(self) -> str:
        return canonical_identity("target_inventory_vector", self)


@dataclass(frozen=True)
class IncentiveCredit(JsonContract):
    namespace: str
    amount: Decimal | str | int
    realization: str = "realized"
    reference: str = ""

    def __post_init__(self) -> None:
        namespace = _text(self.namespace, "incentive namespace")
        if namespace not in INCENTIVE_NAMESPACES:
            raise ExecutionContractError(f"unsupported incentive namespace: {namespace}")
        if self.realization != "realized":
            raise ExecutionContractError("estimated incentive cannot be recorded in EpisodeLedger")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "amount", _decimal(self.amount, "incentive amount", nonnegative=True))
        object.__setattr__(self, "reference", str(self.reference).strip())


@dataclass(frozen=True)
class EpisodeLedger(JsonContract):
    """Realised wealth-conservation ledger.

    Accounting PnL removes external cash flows from wealth movement.  Its
    attribution must reconcile to trading, position operations and realized
    rewards.  Economic profit is a separate management view after financing
    and incremental operating cost; those costs do not alter wallet wealth.
    """

    opening_wealth: Decimal | str | int
    closing_wealth: Decimal | str | int
    external_cash_flow: Decimal | str | int = Decimal("0")
    core_trading_pnl: Decimal | str | int = Decimal("0")
    position_operation_pnl: Decimal | str | int = Decimal("0")
    incentives: tuple[IncentiveCredit, ...] = ()
    rounding_residual: Decimal | str | int = Decimal("0")
    incremental_financing_cost: Decimal | str | int = Decimal("0")
    incremental_operating_cost: Decimal | str | int = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("opening_wealth", "closing_wealth", "external_cash_flow", "core_trading_pnl", "position_operation_pnl", "rounding_residual"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        for name in ("incremental_financing_cost", "incremental_operating_cost"):
            object.__setattr__(
                self,
                name,
                _decimal(getattr(self, name), name, nonnegative=True),
            )
        credits = tuple(self.incentives)
        if any(not isinstance(credit, IncentiveCredit) for credit in credits):
            raise ExecutionContractError("incentives must contain IncentiveCredit values")
        object.__setattr__(self, "incentives", credits)
        if self.accounting_net_pnl != self.accounting_component_total + self.rounding_residual:
            raise ExecutionContractError(
                "wealth conservation failed: rounding_residual does not reconcile accounting components"
            )

    @property
    def accounting_net_pnl(self) -> Decimal:
        return self.closing_wealth - self.opening_wealth - self.external_cash_flow

    @property
    def realized_incentives(self) -> Mapping[str, Decimal]:
        totals = {name: Decimal("0") for name in sorted(INCENTIVE_NAMESPACES)}
        for credit in self.incentives:
            totals[credit.namespace] += credit.amount
        return MappingProxyType(totals)

    @property
    def accounting_component_total(self) -> Decimal:
        return self.core_trading_pnl + self.position_operation_pnl + sum(self.realized_incentives.values(), Decimal("0"))

    @property
    def economic_profit(self) -> Decimal:
        return (
            self.accounting_net_pnl
            - self.incremental_financing_cost
            - self.incremental_operating_cost
        )

    @property
    def identity(self) -> str:
        return canonical_identity("episode_ledger", self)


def expected_shortfall_pnl(scenario_pnl: Mapping[str, Decimal | str | int], tail_count: int) -> Decimal:
    values = _decimal_mapping(scenario_pnl, "scenario_pnl")
    if not values:
        raise ExecutionContractError("scenario_pnl must not be empty")
    if isinstance(tail_count, bool) or not isinstance(tail_count, int) or not 1 <= tail_count <= len(values):
        raise ExecutionContractError("tail_count must be an integer within scenario denominator")
    worst = sorted(values.values())[:tail_count]
    return sum(worst, Decimal("0")) / Decimal(tail_count)


@dataclass(frozen=True)
class InventoryArmOutcome(JsonContract):
    arm: str
    scenario_pnl: Mapping[str, Decimal | str | int]
    capital_time: Decimal | str | int
    expected_shortfall_tail_count: int = 1

    def __post_init__(self) -> None:
        arm = _text(self.arm, "arm")
        if arm not in ARM_NAMES:
            raise ExecutionContractError(f"unsupported inventory arm: {arm}")
        pnl = _decimal_mapping(self.scenario_pnl, "scenario_pnl")
        if not pnl:
            raise ExecutionContractError("scenario_pnl must not be empty")
        capital_time = _decimal(self.capital_time, "capital_time", nonnegative=True)
        if isinstance(self.expected_shortfall_tail_count, bool) or not isinstance(self.expected_shortfall_tail_count, int):
            raise ExecutionContractError("expected_shortfall_tail_count must be an integer")
        expected_shortfall_pnl(pnl, self.expected_shortfall_tail_count)
        object.__setattr__(self, "arm", arm)
        object.__setattr__(self, "scenario_pnl", pnl)
        object.__setattr__(self, "capital_time", capital_time)

    @property
    def worst_case_pnl(self) -> Decimal:
        return min(self.scenario_pnl.values())

    @property
    def expected_shortfall(self) -> Decimal:
        return expected_shortfall_pnl(self.scenario_pnl, self.expected_shortfall_tail_count)

    @property
    def identity(self) -> str:
        return canonical_identity("inventory_arm_outcome", self)


@dataclass(frozen=True)
class PairedInventorySummary(JsonContract):
    """Fixed-denominator comparison of all five inventory lifecycle arms."""

    outcomes: Mapping[str, InventoryArmOutcome]

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, Mapping) or set(self.outcomes) != set(ARM_NAMES):
            raise ExecutionContractError("paired summary requires exactly the five fixed inventory arms")
        outcomes = {name: self.outcomes[name] for name in ARM_NAMES}
        if any(not isinstance(value, InventoryArmOutcome) or value.arm != name for name, value in outcomes.items()):
            raise ExecutionContractError("outcome keys must match InventoryArmOutcome.arm")
        denominator = None
        for outcome in outcomes.values():
            keys = frozenset(outcome.scenario_pnl)
            if denominator is None:
                denominator = keys
            elif keys != denominator:
                raise ExecutionContractError("all arm outcomes must use the same scenario denominator")
        object.__setattr__(self, "outcomes", MappingProxyType(outcomes))

    @property
    def paired_delta_to_no_action(self) -> Mapping[str, Mapping[str, Decimal]]:
        baseline = self.outcomes["no_action"].scenario_pnl
        return MappingProxyType({
            arm: MappingProxyType({key: value - baseline[key] for key, value in outcome.scenario_pnl.items()})
            for arm, outcome in self.outcomes.items()
        })

    @property
    def identity(self) -> str:
        return canonical_identity("paired_inventory_summary", self)
