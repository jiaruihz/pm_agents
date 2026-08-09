"""Execution-neutral city-day position lifecycle state machine.

This module deliberately does not decide whether a weather signal is good
enough to trade.  The model/expression layer supplies an ``eligible`` intent;
the state machine only makes that intent idempotent, converts target inventory
into an incremental order, accounts for city-day exposure, and maintains the
position lifecycle through lock and settlement.

It is a pure in-memory capability.  Importing it cannot place an order or
mutate a runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal


Side = Literal["BUY_YES", "BUY_NO"]
PositionStatus = Literal[
    "OPEN",
    "LOCKED_WIN",
    "LOCKED_LOSS",
    "SETTLED_WIN",
    "SETTLED_LOSS",
]
ActionKind = Literal["OPEN", "ADD", "HOLD", "NOOP", "RISK_BLOCK", "DUPLICATE"]


@dataclass(frozen=True)
class PositionIntent:
    signal_id: str
    signal_ts_utc: str
    instrument_id: str
    side: Side
    target_shares: float
    unit_cash_cost: float
    p_win: float
    eligible: bool
    eligibility_reason: str


@dataclass
class Position:
    instrument_id: str
    side: Side
    shares: float = 0.0
    cash_cost: float = 0.0
    status: PositionStatus = "OPEN"
    opened_at_utc: str = ""
    updated_at_utc: str = ""
    payout_usd: float = 0.0

    @property
    def average_unit_cost(self) -> float:
        return self.cash_cost / self.shares if self.shares > 0 else 0.0

    @property
    def pnl_usd(self) -> float:
        return self.payout_usd - self.cash_cost


@dataclass(frozen=True)
class PositionAction:
    signal_id: str
    signal_ts_utc: str
    instrument_id: str
    side: Side
    action: ActionKind
    reason: str
    incremental_shares: float
    incremental_cash_cost: float
    target_shares: float
    resulting_shares: float
    marginal_expected_value: float


@dataclass
class CityDayPositionState:
    city: str
    target_date: str
    positions: dict[str, Position] = field(default_factory=dict)
    processed_signal_ids: set[str] = field(default_factory=set)
    actions: list[PositionAction] = field(default_factory=list)

    @staticmethod
    def position_key(instrument_id: str, side: Side) -> str:
        return f"{instrument_id}|{side}"

    @property
    def cash_deployed(self) -> float:
        return sum(position.cash_cost for position in self.positions.values())

    @property
    def cash_at_risk(self) -> float:
        return sum(
            position.cash_cost
            for position in self.positions.values()
            if position.status == "OPEN"
        )

    @property
    def locked_payout(self) -> float:
        return sum(
            position.shares
            for position in self.positions.values()
            if position.status in {"LOCKED_WIN", "SETTLED_WIN"}
        )

    @property
    def total_pnl(self) -> float:
        return sum(position.pnl_usd for position in self.positions.values())


def _record(
    state: CityDayPositionState,
    intent: PositionIntent,
    *,
    action: ActionKind,
    reason: str,
    incremental_shares: float = 0.0,
    incremental_cash_cost: float = 0.0,
    resulting_shares: float = 0.0,
) -> PositionAction:
    result = PositionAction(
        signal_id=intent.signal_id,
        signal_ts_utc=intent.signal_ts_utc,
        instrument_id=intent.instrument_id,
        side=intent.side,
        action=action,
        reason=reason,
        incremental_shares=incremental_shares,
        incremental_cash_cost=incremental_cash_cost,
        target_shares=intent.target_shares,
        resulting_shares=resulting_shares,
        marginal_expected_value=incremental_shares
        * (intent.p_win - intent.unit_cash_cost),
    )
    state.actions.append(result)
    return result


def apply_position_intent(
    state: CityDayPositionState,
    intent: PositionIntent,
    *,
    max_city_day_cash: float | None = None,
) -> PositionAction:
    """Apply an upstream inventory target without introducing a signal gate.

    ``eligible`` is intentionally an input.  This function never creates an
    edge threshold and never changes an ineligible model decision into a trade.
    ``max_city_day_cash`` is an optional risk budget, not a model selector.
    """

    if not intent.signal_id:
        raise ValueError("signal_id is required")
    if not intent.instrument_id:
        raise ValueError("instrument_id is required")
    if intent.side not in {"BUY_YES", "BUY_NO"}:
        raise ValueError(f"unsupported side: {intent.side}")
    if not math.isfinite(intent.target_shares) or intent.target_shares < 0:
        raise ValueError("target_shares must be finite and non-negative")
    if not math.isfinite(intent.unit_cash_cost) or not 0 < intent.unit_cash_cost < 1:
        raise ValueError("unit_cash_cost must be finite and in (0,1)")
    if not math.isfinite(intent.p_win) or not 0 <= intent.p_win <= 1:
        raise ValueError("p_win must be finite and in [0,1]")
    if max_city_day_cash is not None and (
        not math.isfinite(max_city_day_cash) or max_city_day_cash < 0
    ):
        raise ValueError("max_city_day_cash must be finite and non-negative")
    if intent.signal_id in state.processed_signal_ids:
        key = state.position_key(intent.instrument_id, intent.side)
        shares = state.positions.get(key, Position(intent.instrument_id, intent.side)).shares
        return _record(
            state,
            intent,
            action="DUPLICATE",
            reason="signal_id_already_processed",
            resulting_shares=shares,
        )
    state.processed_signal_ids.add(intent.signal_id)

    key = state.position_key(intent.instrument_id, intent.side)
    position = state.positions.get(key)
    existing_shares = position.shares if position is not None else 0.0

    if not intent.eligible:
        return _record(
            state,
            intent,
            action="NOOP",
            reason=intent.eligibility_reason,
            resulting_shares=existing_shares,
        )
    if position is not None and position.status != "OPEN":
        return _record(
            state,
            intent,
            action="HOLD",
            reason=f"position_{position.status.lower()}",
            resulting_shares=existing_shares,
        )
    desired_increment = max(0.0, intent.target_shares - existing_shares)
    if desired_increment <= 1e-12:
        return _record(
            state,
            intent,
            action="HOLD",
            reason="target_inventory_already_reached",
            resulting_shares=existing_shares,
        )

    if max_city_day_cash is not None:
        headroom = max(0.0, max_city_day_cash - state.cash_deployed)
        affordable = headroom / intent.unit_cash_cost
        desired_increment = min(desired_increment, affordable)
        if desired_increment <= 1e-12:
            return _record(
                state,
                intent,
                action="RISK_BLOCK",
                reason="city_day_cash_budget_exhausted",
                resulting_shares=existing_shares,
            )

    incremental_cash = desired_increment * intent.unit_cash_cost
    if position is None:
        position = Position(
            instrument_id=intent.instrument_id,
            side=intent.side,
            opened_at_utc=intent.signal_ts_utc,
        )
        state.positions[key] = position
        action: ActionKind = "OPEN"
    else:
        action = "ADD"
    position.shares += desired_increment
    position.cash_cost += incremental_cash
    position.updated_at_utc = intent.signal_ts_utc
    reason = (
        "opened_to_target_inventory"
        if action == "OPEN"
        else "added_to_target_inventory"
    )
    if position.shares + 1e-12 < intent.target_shares:
        reason += "_scaled_by_cash_budget"
    return _record(
        state,
        intent,
        action=action,
        reason=reason,
        incremental_shares=desired_increment,
        incremental_cash_cost=incremental_cash,
        resulting_shares=position.shares,
    )


def mark_position_status(
    state: CityDayPositionState,
    *,
    instrument_id: str,
    side: Side,
    status: PositionStatus,
    updated_at_utc: str,
) -> None:
    if status not in {
        "OPEN",
        "LOCKED_WIN",
        "LOCKED_LOSS",
        "SETTLED_WIN",
        "SETTLED_LOSS",
    }:
        raise ValueError(f"unsupported position status: {status}")
    key = state.position_key(instrument_id, side)
    position = state.positions.get(key)
    if position is None:
        return
    position.status = status
    position.updated_at_utc = updated_at_utc
    if status in {"LOCKED_WIN", "SETTLED_WIN"}:
        position.payout_usd = position.shares
    elif status in {"LOCKED_LOSS", "SETTLED_LOSS"}:
        position.payout_usd = 0.0


def settle_exact_bracket_positions(
    state: CityDayPositionState,
    *,
    winning_instrument_id: str,
    settled_at_utc: str,
) -> None:
    """Settle BUY_YES/BUY_NO positions against one exact winning instrument."""

    if not winning_instrument_id:
        raise ValueError("winning_instrument_id is required")
    for position in state.positions.values():
        yes_hit = position.instrument_id == winning_instrument_id
        won = yes_hit if position.side == "BUY_YES" else not yes_hit
        position.status = "SETTLED_WIN" if won else "SETTLED_LOSS"
        position.payout_usd = position.shares if won else 0.0
        position.updated_at_utc = settled_at_utc
