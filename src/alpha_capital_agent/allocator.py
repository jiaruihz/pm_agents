"""Deterministic, depth-aware capital planning in permanent shadow mode.

The allocator consumes sealed account, position and opportunity inputs.  Its
outputs are recommendations and replay facts only: this module has no venue,
wallet, credential, network, scheduler, or order-submission capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.polymarket_alpha.contracts.base import (
    AlphaContract,
    CommonEnvelope,
    content_sha256,
    ensure_utc,
    stable_record_id,
    validate_sha256,
)

from .capital import (
    AccountCompleteness,
    AccountSnapshotSeal,
    CapitalPolicy,
    ExecutableLevel,
    OpportunityRecord,
    PositionExposure,
    ReplacementHistory,
    ZERO,
)


ALLOCATOR_SOURCE = "alpha_capital_agent.allocator"
ALLOCATOR_VERSION = "aca_allocator_v1"


class CapitalActionType(StrEnum):
    DATA_BLOCKED = "DATA_BLOCKED"
    REDEEM_REVIEW = "REDEEM_REVIEW"
    CAPITAL_LOCKED = "CAPITAL_LOCKED"
    ENTER_SHADOW = "ENTER_SHADOW"
    KEEP = "KEEP"
    REPLACE_WATCH = "REPLACE_WATCH"
    REPLACE_REVIEW = "REPLACE_REVIEW"
    PASS = "PASS"


class CapitalPlanStatus(StrEnum):
    DATA_BLOCKED = "DATA_BLOCKED"
    NO_CHANGE = "NO_CHANGE"
    WATCHING = "WATCHING"
    PROPOSED = "PROPOSED"


class CapitalAction(AlphaContract):
    action_id: str
    action_type: CapitalActionType
    market_id: str
    opportunity_id: str | None = None
    position_id: str | None = None
    sell_shares: Decimal = Field(default=ZERO, ge=ZERO)
    buy_shares: Decimal = Field(default=ZERO, ge=ZERO)
    sell_proceeds: Decimal = Field(default=ZERO, ge=ZERO)
    buy_cost: Decimal = Field(default=ZERO, ge=ZERO)
    cash_before: Decimal = Field(ge=ZERO)
    cash_after: Decimal = Field(ge=ZERO)
    exposure_before: Decimal = Field(ge=ZERO)
    exposure_after: Decimal = Field(ge=ZERO)
    delta_ev: Decimal = ZERO
    switch_return: Decimal | None = None
    reason_codes: tuple[str, ...] = ()
    input_hashes: tuple[str, ...]
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution_capability: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("action_id")
    @classmethod
    def id_is_namespaced(cls, value: str) -> str:
        if not value.startswith("capital_action:"):
            raise ValueError("action id must use capital_action namespace")
        return value

    @field_validator("market_id")
    @classmethod
    def market_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("market_id must not be blank")
        return value

    @field_validator("input_hashes")
    @classmethod
    def hashes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("action input hashes must be non-empty, unique and sorted")
        return tuple(validate_sha256(item) for item in value)

    @field_validator("reason_codes")
    @classmethod
    def reasons_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("reason codes must not be blank")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("reason codes must be unique and sorted")
        return normalized

    @model_validator(mode="after")
    def action_semantics_hold(self) -> "CapitalAction":
        inert = {
            CapitalActionType.DATA_BLOCKED,
            CapitalActionType.REDEEM_REVIEW,
            CapitalActionType.CAPITAL_LOCKED,
            CapitalActionType.KEEP,
            CapitalActionType.PASS,
        }
        if self.action_type in inert:
            if any(
                value != ZERO
                for value in (
                    self.sell_shares,
                    self.buy_shares,
                    self.sell_proceeds,
                    self.buy_cost,
                    self.delta_ev,
                )
            ):
                raise ValueError("inert action cannot mutate or value a trade")
            if self.cash_after != self.cash_before:
                raise ValueError("inert action cannot change cash")
            if self.exposure_after != self.exposure_before:
                raise ValueError("inert action cannot change exposure")
        elif self.action_type is CapitalActionType.ENTER_SHADOW:
            if self.buy_shares <= ZERO or self.buy_cost <= ZERO:
                raise ValueError("entry requires positive executable shares and cost")
            if self.sell_shares != ZERO or self.sell_proceeds != ZERO:
                raise ValueError("entry cannot sell an existing position")
            if self.cash_after != self.cash_before - self.buy_cost:
                raise ValueError("entry cash ledger is not exact")
            if self.exposure_after != self.exposure_before + self.buy_cost:
                raise ValueError("entry exposure ledger is not exact")
        elif self.action_type is CapitalActionType.REPLACE_WATCH:
            if min(
                self.sell_shares,
                self.buy_shares,
                self.sell_proceeds,
                self.buy_cost,
            ) <= ZERO:
                raise ValueError("replacement watch requires an executable proposal")
            if self.cash_after != self.cash_before:
                raise ValueError("replacement watch cannot change plan cash")
            if self.exposure_after != self.exposure_before:
                raise ValueError("replacement watch cannot change plan exposure")
        elif self.action_type is CapitalActionType.REPLACE_REVIEW:
            if min(
                self.sell_shares,
                self.buy_shares,
                self.sell_proceeds,
                self.buy_cost,
            ) <= ZERO:
                raise ValueError("replacement review requires executable legs")
            if self.cash_after != self.cash_before + self.sell_proceeds - self.buy_cost:
                raise ValueError("replacement cash ledger is not exact")
        if self.action_type in {
            CapitalActionType.REPLACE_WATCH,
            CapitalActionType.REPLACE_REVIEW,
        }:
            if self.position_id is None or self.opportunity_id is None:
                raise ValueError("replacement requires old and new identities")
            if self.switch_return is None:
                raise ValueError("replacement requires switch return")
        return self

    @property
    def content_hash(self) -> str:
        return content_sha256(self)


class CapitalPlan(CommonEnvelope):
    plan_id: str
    account_id: str
    decision_as_of: datetime
    snapshot_hash: str
    policy_hash: str
    input_hashes: tuple[str, ...]
    status: CapitalPlanStatus
    mark_nav: Decimal = Field(ge=ZERO)
    cash_buffer_required: Decimal = Field(ge=ZERO)
    cash_floor_effective: Decimal = Field(ge=ZERO)
    do_nothing_conservative_value: Decimal = Field(ge=ZERO)
    after_conservative_value: Decimal = Field(ge=ZERO)
    cash_before: Decimal = Field(ge=ZERO)
    cash_after: Decimal = Field(ge=ZERO)
    reserved_cash: Decimal = Field(ge=ZERO)
    exposure_before: Decimal = Field(ge=ZERO)
    exposure_after: Decimal = Field(ge=ZERO)
    actions: tuple[CapitalAction, ...]
    replacement_histories: tuple[ReplacementHistory, ...] = ()
    mode: Literal["READ_ONLY_SHADOW"] = "READ_ONLY_SHADOW"
    execution_capability: Literal["NO_ORDER"] = "NO_ORDER"

    @field_validator("decision_as_of")
    @classmethod
    def decision_clock_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("snapshot_hash", "policy_hash")
    @classmethod
    def hashes_are_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("input_hashes")
    @classmethod
    def inputs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("plan input hashes must be non-empty, unique and sorted")
        return tuple(validate_sha256(item) for item in value)

    @model_validator(mode="after")
    def plan_ledger_and_identity_hold(self) -> "CapitalPlan":
        if self.plan_id != self.record_id:
            raise ValueError("plan_id must equal record_id")
        if not self.record_id.startswith("capital_plan:"):
            raise ValueError("capital plan id must use capital_plan namespace")
        if self.created_at != self.decision_as_of:
            raise ValueError("plan clock must equal sealed account decision clock")
        if self.cash_floor_effective != min(
            self.cash_before, self.cash_buffer_required
        ):
            raise ValueError("effective cash floor must preserve an existing shortfall")
        if self.cash_after < self.cash_floor_effective:
            raise ValueError("plan may not worsen the effective cash floor")
        cash_cursor = self.cash_before
        exposure_cursor = self.exposure_before
        applied_delta = ZERO
        for action in self.actions:
            if action.cash_before != cash_cursor:
                raise ValueError("action cash chain is discontinuous")
            if action.exposure_before != exposure_cursor:
                raise ValueError("action exposure chain is discontinuous")
            cash_cursor = action.cash_after
            exposure_cursor = action.exposure_after
            if action.action_type in {
                CapitalActionType.ENTER_SHADOW,
                CapitalActionType.REPLACE_REVIEW,
            }:
                applied_delta += action.delta_ev
        if cash_cursor != self.cash_after or exposure_cursor != self.exposure_after:
            raise ValueError("plan totals do not match the action ledger")
        if self.after_conservative_value != self.do_nothing_conservative_value + applied_delta:
            raise ValueError("conservative value ledger is not exact")
        pairs = tuple(item.pair_key for item in self.replacement_histories)
        if pairs != tuple(sorted(set(pairs))):
            raise ValueError("replacement histories must be unique and sorted")
        return self


@dataclass
class _BookCursor:
    prices: tuple[Decimal, ...]
    remaining: list[Decimal]

    @classmethod
    def from_levels(
        cls,
        levels: tuple[ExecutableLevel, ...],
        participation: Decimal,
        *,
        max_shares: Decimal | None = None,
    ) -> "_BookCursor":
        cap = max_shares
        remaining: list[Decimal] = []
        for level in levels:
            allowed = level.size * participation
            if cap is not None:
                allowed = min(allowed, cap)
                cap -= allowed
            remaining.append(allowed)
        return cls(tuple(level.price for level in levels), remaining)

    def quote_shares(self, shares: Decimal) -> Decimal | None:
        if shares < ZERO:
            raise ValueError("shares cannot be negative")
        needed = shares
        value = ZERO
        for price, available in zip(self.prices, self.remaining):
            take = min(needed, available)
            value += take * price
            needed -= take
            if needed == ZERO:
                return value
        return value if needed == ZERO else None

    def consume_shares(self, shares: Decimal) -> Decimal:
        quoted = self.quote_shares(shares)
        if quoted is None:
            raise ValueError("requested shares exceed remaining executable depth")
        needed = shares
        for index, available in enumerate(self.remaining):
            take = min(needed, available)
            self.remaining[index] -= take
            needed -= take
            if needed == ZERO:
                break
        return quoted

    def quote_buy_budget(
        self, budget: Decimal, *, max_price: Decimal
    ) -> tuple[Decimal, Decimal]:
        if budget <= ZERO or max_price <= ZERO:
            return ZERO, ZERO
        remaining_budget = budget
        shares = ZERO
        cost = ZERO
        for price, available in zip(self.prices, self.remaining):
            if price > max_price:
                break
            take = min(available, remaining_budget / price)
            shares += take
            level_cost = take * price
            cost += level_cost
            remaining_budget -= level_cost
            if remaining_budget == ZERO:
                break
        return shares, cost

    def quote_sell_proceeds(
        self, target_proceeds: Decimal
    ) -> tuple[Decimal, Decimal] | None:
        if target_proceeds <= ZERO:
            return None
        remaining_value = target_proceeds
        shares = ZERO
        proceeds = ZERO
        for price, available in zip(self.prices, self.remaining):
            take = min(available, remaining_value / price)
            shares += take
            level_value = take * price
            proceeds += level_value
            remaining_value -= level_value
            if remaining_value == ZERO:
                return shares, proceeds
        return None

    @property
    def total_value(self) -> Decimal:
        return sum(
            (price * size for price, size in zip(self.prices, self.remaining)),
            ZERO,
        )

    @property
    def total_shares(self) -> Decimal:
        return sum(self.remaining, ZERO)

    @property
    def best_price(self) -> Decimal | None:
        for price, size in zip(self.prices, self.remaining):
            if size > ZERO:
                return price
        return None


@dataclass(frozen=True)
class _ReplacementCandidate:
    position: PositionExposure
    opportunity: OpportunityRecord
    sell_shares: Decimal
    buy_shares: Decimal
    sell_proceeds: Decimal
    buy_cost: Decimal
    delta_ev: Decimal
    switch_return: Decimal
    history: ReplacementHistory


_GROUP_FIELDS = (
    ("market_id", "market_cap"),
    ("event_id", "event_cap"),
    ("cluster_id", "cluster_cap"),
    ("maturity", "maturity_cap"),
)


def _group_key(field: str, value: str) -> tuple[str, str]:
    return field, value


def _initial_group_exposure(
    positions: tuple[PositionExposure, ...],
) -> dict[tuple[str, str], Decimal]:
    result: dict[tuple[str, str], Decimal] = {}
    for position in positions:
        for field, _ in _GROUP_FIELDS:
            key = _group_key(field, str(getattr(position, field)))
            result[key] = result.get(key, ZERO) + position.mark_notional
    return result


def _allowed_group_exposure(
    current: dict[tuple[str, str], Decimal],
    mark_nav: Decimal,
    policy: CapitalPolicy,
) -> dict[tuple[str, str], Decimal]:
    result: dict[tuple[str, str], Decimal] = {}
    for key, baseline in current.items():
        field = key[0]
        policy_field = next(item[1] for item in _GROUP_FIELDS if item[0] == field)
        result[key] = max(baseline, mark_nav * getattr(policy, policy_field))
    return result


def _cap_room(
    opportunity: OpportunityRecord,
    current: dict[tuple[str, str], Decimal],
    allowed: dict[tuple[str, str], Decimal],
    mark_nav: Decimal,
    policy: CapitalPolicy,
) -> Decimal:
    rooms: list[Decimal] = []
    for field, policy_field in _GROUP_FIELDS:
        key = _group_key(field, str(getattr(opportunity, field)))
        limit = allowed.get(key, mark_nav * getattr(policy, policy_field))
        rooms.append(limit - current.get(key, ZERO))
    return max(ZERO, min(rooms))


def _replacement_budget_limit(
    opportunity: OpportunityRecord,
    position: PositionExposure,
    sellable_shares: Decimal,
    current: dict[tuple[str, str], Decimal],
    allowed: dict[tuple[str, str], Decimal],
    mark_nav: Decimal,
    policy: CapitalPolicy,
) -> Decimal:
    """Upper bound using only mark exposure of shares that can really sell."""

    rooms: list[Decimal] = []
    for field, policy_field in _GROUP_FIELDS:
        new_key = _group_key(field, str(getattr(opportunity, field)))
        old_key = _group_key(field, str(getattr(position, field)))
        limit = allowed.get(new_key, mark_nav * getattr(policy, policy_field))
        room = limit - current.get(new_key, ZERO)
        if new_key == old_key:
            room += sellable_shares * position.mark_price
        rooms.append(room)
    return max(ZERO, min(rooms))


def _replacement_capacity_holds(
    opportunity: OpportunityRecord,
    position: PositionExposure,
    *,
    buy_cost: Decimal,
    sell_shares: Decimal,
    current: dict[tuple[str, str], Decimal],
    allowed: dict[tuple[str, str], Decimal],
    mark_nav: Decimal,
    policy: CapitalPolicy,
) -> tuple[bool, Decimal]:
    """Check actual paired legs and return a tighter retry budget if needed."""

    actual_rooms: list[Decimal] = []
    for field, policy_field in _GROUP_FIELDS:
        new_key = _group_key(field, str(getattr(opportunity, field)))
        old_key = _group_key(field, str(getattr(position, field)))
        limit = allowed.get(new_key, mark_nav * getattr(policy, policy_field))
        room = limit - current.get(new_key, ZERO)
        if new_key == old_key:
            room += sell_shares * position.mark_price
        actual_rooms.append(room)
    retry_budget = max(ZERO, min(actual_rooms))
    return buy_cost <= retry_budget, retry_budget


def _apply_group_delta(
    current: dict[tuple[str, str], Decimal],
    record: PositionExposure | OpportunityRecord,
    delta: Decimal,
) -> None:
    for field, _ in _GROUP_FIELDS:
        key = _group_key(field, str(getattr(record, field)))
        updated = current.get(key, ZERO) + delta
        if updated < ZERO:
            raise ValueError("group exposure cannot become negative")
        current[key] = updated


def _fractional_kelly_budget(
    opportunity: OpportunityRecord,
    book: _BookCursor,
    mark_nav: Decimal,
    policy: CapitalPolicy,
) -> Decimal:
    price = book.best_price
    if price is None or opportunity.q_cons <= price:
        return ZERO
    full_kelly = (opportunity.q_cons - price) / (Decimal("1") - price)
    return mark_nav * policy.kelly_fraction * min(Decimal("1"), full_kelly)


def _history_index(
    histories: tuple[ReplacementHistory, ...],
) -> dict[tuple[str, str], ReplacementHistory]:
    result: dict[tuple[str, str], ReplacementHistory] = {}
    for history in histories:
        if history.pair_key in result:
            raise ValueError("duplicate replacement history pair")
        result[history.pair_key] = history
    return result


def _advance_history(
    prior: ReplacementHistory | None,
    position_id: str,
    opportunity_id: str,
    at: datetime,
    policy: CapitalPolicy,
) -> ReplacementHistory:
    if prior is None or prior.consecutive_confirmations == 0:
        return ReplacementHistory(
            position_id=position_id,
            opportunity_id=opportunity_id,
            consecutive_confirmations=1,
            first_confirmation_at=at,
            last_confirmation_at=at,
            last_executed_at=None if prior is None else prior.last_executed_at,
            last_execution_ref=None if prior is None else prior.last_execution_ref,
        )
    assert prior.last_confirmation_at is not None
    if at - prior.last_confirmation_at < timedelta(
        seconds=policy.confirmation_interval_seconds
    ):
        return prior
    return ReplacementHistory(
        position_id=position_id,
        opportunity_id=opportunity_id,
        consecutive_confirmations=prior.consecutive_confirmations + 1,
        first_confirmation_at=prior.first_confirmation_at,
        last_confirmation_at=at,
        last_executed_at=prior.last_executed_at,
        last_execution_ref=prior.last_execution_ref,
    )


def _reset_history(prior: ReplacementHistory) -> ReplacementHistory:
    return ReplacementHistory(
        position_id=prior.position_id,
        opportunity_id=prior.opportunity_id,
        consecutive_confirmations=0,
        last_executed_at=prior.last_executed_at,
        last_execution_ref=prior.last_execution_ref,
    )


def _make_action(
    *,
    run_id: str,
    decision_as_of: datetime,
    input_hashes: tuple[str, ...],
    action_type: CapitalActionType,
    market_id: str,
    cash_before: Decimal,
    cash_after: Decimal,
    exposure_before: Decimal,
    exposure_after: Decimal,
    opportunity_id: str | None = None,
    position_id: str | None = None,
    sell_shares: Decimal = ZERO,
    buy_shares: Decimal = ZERO,
    sell_proceeds: Decimal = ZERO,
    buy_cost: Decimal = ZERO,
    delta_ev: Decimal = ZERO,
    switch_return: Decimal | None = None,
    reason_codes: tuple[str, ...] = (),
) -> CapitalAction:
    payload = {
        "action_type": action_type,
        "market_id": market_id,
        "opportunity_id": opportunity_id,
        "position_id": position_id,
        "sell_shares": sell_shares,
        "buy_shares": buy_shares,
        "sell_proceeds": sell_proceeds,
        "buy_cost": buy_cost,
        "cash_before": cash_before,
        "cash_after": cash_after,
        "exposure_before": exposure_before,
        "exposure_after": exposure_after,
        "delta_ev": delta_ev,
        "switch_return": switch_return,
        "reason_codes": tuple(sorted(set(reason_codes))),
        "input_hashes": input_hashes,
        "mode": "READ_ONLY_SHADOW",
        "execution_capability": "NO_ORDER",
    }
    return CapitalAction(
        action_id=stable_record_id(
            "capital_action", run_id, decision_as_of, payload
        ),
        **payload,
    )


def allocate_capital(
    snapshot: AccountSnapshotSeal,
    positions: tuple[PositionExposure, ...] | list[PositionExposure],
    opportunities: tuple[OpportunityRecord, ...] | list[OpportunityRecord],
    policy: CapitalPolicy,
    *,
    histories: tuple[ReplacementHistory, ...] | list[ReplacementHistory] = (),
    run_id: str = "shadow",
) -> CapitalPlan:
    """Return an exact-replay shadow plan; never submit or encode an order."""

    positions = tuple(positions)
    opportunities = tuple(opportunities)
    histories = tuple(histories)
    decision_as_of = snapshot.decision_as_of

    if len({item.position_id for item in positions}) != len(positions):
        raise ValueError("position ids must be unique")
    if len({item.opportunity_id for item in opportunities}) != len(opportunities):
        raise ValueError("opportunity ids must be unique")

    history_state = _history_index(histories)
    input_hashes = tuple(
        sorted(
            {
                snapshot.content_hash,
                policy.content_hash,
                *(item.content_hash for item in positions),
                *(item.content_hash for item in opportunities),
                *(item.content_hash for item in histories),
            }
        )
    )
    exposure_before = sum((item.mark_notional for item in positions), ZERO)
    mark_nav = snapshot.free_cash + snapshot.reserved_cash + exposure_before
    cash_buffer_required = mark_nav * policy.cash_buffer
    cash_floor_effective = min(snapshot.free_cash, cash_buffer_required)
    conservative_before = (
        snapshot.free_cash
        + snapshot.reserved_cash
        + sum((item.shares * item.q_cons for item in positions), ZERO)
    )
    cash = snapshot.free_cash
    exposure = exposure_before
    conservative_delta = ZERO
    actions: list[CapitalAction] = []

    for position in sorted(positions, key=lambda item: item.position_id):
        if position.redeemable:
            actions.append(
                _make_action(
                    run_id=run_id,
                    decision_as_of=decision_as_of,
                    input_hashes=input_hashes,
                    action_type=CapitalActionType.REDEEM_REVIEW,
                    market_id=position.market_id,
                    position_id=position.position_id,
                    cash_before=cash,
                    cash_after=cash,
                    exposure_before=exposure,
                    exposure_after=exposure,
                    reason_codes=("REDEEMABLE_REQUIRES_SEPARATE_SETTLEMENT_REVIEW",),
                )
            )

    if snapshot.completeness is not AccountCompleteness.AUTHENTICATED_COMPLETE:
        actions.append(
            _make_action(
                run_id=run_id,
                decision_as_of=decision_as_of,
                input_hashes=input_hashes,
                action_type=CapitalActionType.DATA_BLOCKED,
                market_id="ACCOUNT",
                cash_before=cash,
                cash_after=cash,
                exposure_before=exposure,
                exposure_after=exposure,
                reason_codes=("ACCOUNT_OR_OPEN_ORDER_COVERAGE_INCOMPLETE",),
            )
        )
        return _build_plan(
            snapshot=snapshot,
            policy=policy,
            run_id=run_id,
            input_hashes=input_hashes,
            status=CapitalPlanStatus.DATA_BLOCKED,
            mark_nav=mark_nav,
            cash_buffer_required=cash_buffer_required,
            cash_floor_effective=cash_floor_effective,
            conservative_before=conservative_before,
            conservative_delta=ZERO,
            exposure_before=exposure_before,
            cash_after=cash,
            exposure_after=exposure,
            actions=actions,
            histories=tuple(sorted(history_state.values(), key=lambda item: item.pair_key)),
        )

    current_groups = _initial_group_exposure(positions)
    allowed_groups = _allowed_group_exposure(current_groups, mark_nav, policy)
    buy_books = {
        item.opportunity_id: _BookCursor.from_levels(
            item.buy_levels, policy.depth_participation
        )
        for item in opportunities
    }
    sell_books = {
        item.position_id: _BookCursor.from_levels(
            item.sell_levels,
            policy.depth_participation,
            max_shares=item.shares,
        )
        for item in positions
    }
    actioned_opportunities: set[str] = set()
    allocated_cost_by_opportunity: dict[str, Decimal] = {
        item.opportunity_id: ZERO for item in opportunities
    }

    ranked_opportunities = sorted(
        opportunities,
        key=lambda item: (
            -(item.q_cons - item.buy_levels[0].price),
            item.market_id,
            item.opportunity_id,
        ),
    )

    # Stage 1: deploy only free cash above the effective NAV buffer.
    for opportunity in ranked_opportunities:
        if not opportunity.research_is_fresh(decision_as_of):
            continue
        spendable = max(ZERO, cash - cash_floor_effective)
        cap_room = _cap_room(
            opportunity, current_groups, allowed_groups, mark_nav, policy
        )
        kelly_room = max(
            ZERO,
            _fractional_kelly_budget(
                opportunity,
                buy_books[opportunity.opportunity_id],
                mark_nav,
                policy,
            )
            - allocated_cost_by_opportunity[opportunity.opportunity_id],
        )
        budget = min(spendable, cap_room, kelly_room)
        max_price = opportunity.q_cons - policy.entry_edge_floor
        shares, cost = buy_books[opportunity.opportunity_id].quote_buy_budget(
            budget, max_price=max_price
        )
        if shares <= ZERO or cost <= ZERO:
            continue
        consumed_cost = buy_books[opportunity.opportunity_id].consume_shares(shares)
        if consumed_cost != cost:
            raise ValueError("buy book changed during deterministic allocation")
        before_cash = cash
        before_exposure = exposure
        cash -= cost
        exposure += cost
        delta_ev = shares * opportunity.q_cons - cost
        conservative_delta += delta_ev
        _apply_group_delta(current_groups, opportunity, cost)
        allocated_cost_by_opportunity[opportunity.opportunity_id] += cost
        actions.append(
            _make_action(
                run_id=run_id,
                decision_as_of=decision_as_of,
                input_hashes=input_hashes,
                action_type=CapitalActionType.ENTER_SHADOW,
                market_id=opportunity.market_id,
                opportunity_id=opportunity.opportunity_id,
                buy_shares=shares,
                buy_cost=cost,
                cash_before=before_cash,
                cash_after=cash,
                exposure_before=before_exposure,
                exposure_after=exposure,
                delta_ev=delta_ev,
            )
        )
        actioned_opportunities.add(opportunity.opportunity_id)

    # Stage 2: rank one replacement proposal.  No position, depth, or proceeds
    # can be consumed twice because only the selected, confirmed pair mutates.
    candidates: list[_ReplacementCandidate] = []
    blocked_reasons: dict[str, set[str]] = {
        item.opportunity_id: set() for item in opportunities
    }
    required_return = max(
        policy.replacement_return_floor,
        policy.model_buffer,
        policy.unpriced_buffer,
    )
    qualifying_pairs: set[tuple[str, str]] = set()
    for opportunity in ranked_opportunities:
        if not opportunity.research_is_fresh(decision_as_of):
            blocked_reasons[opportunity.opportunity_id].add("STALE_OPPORTUNITY_RESEARCH")
            continue
        buy_book = buy_books[opportunity.opportunity_id]
        if buy_book.total_value <= ZERO:
            blocked_reasons[opportunity.opportunity_id].add(
                "NO_REMAINING_EXECUTABLE_BUY_DEPTH"
            )
            continue
        for position in sorted(positions, key=lambda item: item.position_id):
            pair = (position.position_id, opportunity.opportunity_id)
            prior = history_state.get(pair)
            if position.redeemable:
                continue
            if position.market_id == opportunity.market_id:
                blocked_reasons[opportunity.opportunity_id].add("SAME_MARKET_REPLACEMENT")
                continue
            if not position.research_is_fresh(decision_as_of):
                blocked_reasons[opportunity.opportunity_id].add("STALE_HELD_RESEARCH")
                if prior is not None and prior.consecutive_confirmations:
                    history_state[pair] = _reset_history(prior)
                continue
            if position.locked or sell_books[position.position_id].total_value <= ZERO:
                blocked_reasons[opportunity.opportunity_id].add("NO_EXECUTABLE_BID_DEPTH")
                if prior is not None and prior.consecutive_confirmations:
                    history_state[pair] = _reset_history(prior)
                continue
            if (
                prior is not None
                and prior.last_executed_at is not None
                and decision_as_of - prior.last_executed_at
                < timedelta(seconds=policy.replacement_cooldown_seconds)
            ):
                blocked_reasons[opportunity.opportunity_id].add("REPLACEMENT_COOLDOWN")
                continue
            sell_book = sell_books[position.position_id]
            budget_limit = _replacement_budget_limit(
                opportunity,
                position,
                sell_book.total_shares,
                current_groups,
                allowed_groups,
                mark_nav,
                policy,
            )
            kelly_room = max(
                ZERO,
                _fractional_kelly_budget(
                    opportunity, buy_book, mark_nav, policy
                )
                - allocated_cost_by_opportunity[opportunity.opportunity_id],
            )
            budget = min(
                budget_limit,
                sell_book.total_value,
                buy_book.total_value,
                kelly_room,
            )
            if budget <= ZERO:
                blocked_reasons[opportunity.opportunity_id].add(
                    "NO_REPLACEMENT_CONCENTRATION_CAPACITY"
                )
                continue
            quote: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
            for _ in range(16):
                buy_shares, buy_cost = buy_book.quote_buy_budget(
                    budget,
                    max_price=opportunity.q_cons - policy.entry_edge_floor,
                )
                if buy_shares <= ZERO or buy_cost <= ZERO:
                    break
                sale = sell_book.quote_sell_proceeds(buy_cost)
                if sale is None:
                    break
                sell_shares, sell_proceeds = sale
                capacity_holds, retry_budget = _replacement_capacity_holds(
                    opportunity,
                    position,
                    buy_cost=buy_cost,
                    sell_shares=sell_shares,
                    current=current_groups,
                    allowed=allowed_groups,
                    mark_nav=mark_nav,
                    policy=policy,
                )
                if capacity_holds:
                    quote = sell_shares, sell_proceeds, buy_shares, buy_cost
                    break
                if retry_budget <= ZERO or retry_budget >= budget:
                    break
                budget = retry_budget
            if quote is None:
                blocked_reasons[opportunity.opportunity_id].add(
                    "NO_REPLACEMENT_CONCENTRATION_CAPACITY"
                )
                continue
            sell_shares, sell_proceeds, buy_shares, buy_cost = quote
            delta_ev = (
                buy_shares * opportunity.q_cons
                - buy_cost
                + sell_proceeds
                - sell_shares * position.q_cons
            )
            switch_return = delta_ev / sell_proceeds
            if switch_return < required_return:
                blocked_reasons[opportunity.opportunity_id].add(
                    "SWITCH_RETURN_BELOW_REQUIRED_BUFFER"
                )
                if prior is not None and prior.consecutive_confirmations:
                    history_state[pair] = _reset_history(prior)
                continue
            advanced = _advance_history(
                prior,
                position.position_id,
                opportunity.opportunity_id,
                decision_as_of,
                policy,
            )
            history_state[pair] = advanced
            qualifying_pairs.add(pair)
            candidates.append(
                _ReplacementCandidate(
                    position=position,
                    opportunity=opportunity,
                    sell_shares=sell_shares,
                    buy_shares=buy_shares,
                    sell_proceeds=sell_proceeds,
                    buy_cost=buy_cost,
                    delta_ev=delta_ev,
                    switch_return=switch_return,
                    history=advanced,
                )
            )

    # Confirmation means consecutive eligible plan ticks. Any carried pair
    # that is absent, stale, depth/cap blocked, or below threshold on this tick
    # loses its streak instead of silently resuming later.
    for pair, carried in tuple(history_state.items()):
        if pair not in qualifying_pairs and carried.consecutive_confirmations:
            history_state[pair] = _reset_history(carried)

    selected = min(
        candidates,
        key=lambda item: (
            -item.switch_return,
            item.opportunity.market_id,
            item.opportunity.opportunity_id,
            item.position.position_id,
        ),
        default=None,
    )
    if selected is not None:
        before_cash = cash
        before_exposure = exposure
        if selected.history.consecutive_confirmations < policy.replacement_confirmations:
            actions.append(
                _make_action(
                    run_id=run_id,
                    decision_as_of=decision_as_of,
                    input_hashes=input_hashes,
                    action_type=CapitalActionType.REPLACE_WATCH,
                    market_id=selected.opportunity.market_id,
                    opportunity_id=selected.opportunity.opportunity_id,
                    position_id=selected.position.position_id,
                    sell_shares=selected.sell_shares,
                    buy_shares=selected.buy_shares,
                    sell_proceeds=selected.sell_proceeds,
                    buy_cost=selected.buy_cost,
                    cash_before=cash,
                    cash_after=cash,
                    exposure_before=exposure,
                    exposure_after=exposure,
                    delta_ev=selected.delta_ev,
                    switch_return=selected.switch_return,
                    reason_codes=("AWAITING_SECOND_CONFIRMATION",),
                )
            )
        else:
            consumed_sell = sell_books[
                selected.position.position_id
            ].consume_shares(selected.sell_shares)
            consumed_buy = buy_books[
                selected.opportunity.opportunity_id
            ].consume_shares(selected.buy_shares)
            if consumed_sell != selected.sell_proceeds or consumed_buy != selected.buy_cost:
                raise ValueError("replacement book changed during deterministic allocation")
            cash += consumed_sell - consumed_buy
            sold_mark = selected.sell_shares * selected.position.mark_price
            exposure += consumed_buy - sold_mark
            _apply_group_delta(current_groups, selected.position, -sold_mark)
            _apply_group_delta(current_groups, selected.opportunity, consumed_buy)
            allocated_cost_by_opportunity[
                selected.opportunity.opportunity_id
            ] += consumed_buy
            conservative_delta += selected.delta_ev
            actions.append(
                _make_action(
                    run_id=run_id,
                    decision_as_of=decision_as_of,
                    input_hashes=input_hashes,
                    action_type=CapitalActionType.REPLACE_REVIEW,
                    market_id=selected.opportunity.market_id,
                    opportunity_id=selected.opportunity.opportunity_id,
                    position_id=selected.position.position_id,
                    sell_shares=selected.sell_shares,
                    buy_shares=selected.buy_shares,
                    sell_proceeds=consumed_sell,
                    buy_cost=consumed_buy,
                    cash_before=before_cash,
                    cash_after=cash,
                    exposure_before=before_exposure,
                    exposure_after=exposure,
                    delta_ev=selected.delta_ev,
                    switch_return=selected.switch_return,
                    reason_codes=("SECOND_CONFIRMATION_REACHED",),
                )
            )
        actioned_opportunities.add(selected.opportunity.opportunity_id)

    for opportunity in sorted(
        opportunities, key=lambda item: (item.market_id, item.opportunity_id)
    ):
        if opportunity.opportunity_id in actioned_opportunities:
            continue
        reasons = blocked_reasons[opportunity.opportunity_id]
        action_type = (
            CapitalActionType.CAPITAL_LOCKED
            if reasons.intersection(
                {"NO_EXECUTABLE_BID_DEPTH", "STALE_HELD_RESEARCH"}
            )
            else CapitalActionType.PASS
        )
        actions.append(
            _make_action(
                run_id=run_id,
                decision_as_of=decision_as_of,
                input_hashes=input_hashes,
                action_type=action_type,
                market_id=opportunity.market_id,
                opportunity_id=opportunity.opportunity_id,
                cash_before=cash,
                cash_after=cash,
                exposure_before=exposure,
                exposure_after=exposure,
                reason_codes=tuple(reasons or {"NO_ALLOCATABLE_CAPITAL_OR_EDGE"}),
            )
        )

    status = CapitalPlanStatus.NO_CHANGE
    if any(
        item.action_type
        in {CapitalActionType.ENTER_SHADOW, CapitalActionType.REPLACE_REVIEW}
        for item in actions
    ):
        status = CapitalPlanStatus.PROPOSED
    elif any(item.action_type is CapitalActionType.REPLACE_WATCH for item in actions):
        status = CapitalPlanStatus.WATCHING

    return _build_plan(
        snapshot=snapshot,
        policy=policy,
        run_id=run_id,
        input_hashes=input_hashes,
        status=status,
        mark_nav=mark_nav,
        cash_buffer_required=cash_buffer_required,
        cash_floor_effective=cash_floor_effective,
        conservative_before=conservative_before,
        conservative_delta=conservative_delta,
        exposure_before=exposure_before,
        cash_after=cash,
        exposure_after=exposure,
        actions=actions,
        histories=tuple(sorted(history_state.values(), key=lambda item: item.pair_key)),
    )


def _build_plan(
    *,
    snapshot: AccountSnapshotSeal,
    policy: CapitalPolicy,
    run_id: str,
    input_hashes: tuple[str, ...],
    status: CapitalPlanStatus,
    mark_nav: Decimal,
    cash_buffer_required: Decimal,
    cash_floor_effective: Decimal,
    conservative_before: Decimal,
    conservative_delta: Decimal,
    exposure_before: Decimal,
    cash_after: Decimal,
    exposure_after: Decimal,
    actions: list[CapitalAction],
    histories: tuple[ReplacementHistory, ...],
) -> CapitalPlan:
    identity = {
        "account_id": snapshot.account_id,
        "decision_as_of": snapshot.decision_as_of,
        "snapshot_hash": snapshot.content_hash,
        "policy_hash": policy.content_hash,
        "input_hashes": input_hashes,
        "status": status,
        "actions": tuple(item.content_hash for item in actions),
        "histories": tuple(item.content_hash for item in histories),
        "run_id": run_id,
    }
    record_id = stable_record_id("capital_plan", identity)
    return CapitalPlan(
        record_id=record_id,
        plan_id=record_id,
        run_id=run_id,
        created_at=snapshot.decision_as_of,
        source=ALLOCATOR_SOURCE,
        source_version=ALLOCATOR_VERSION,
        account_id=snapshot.account_id,
        decision_as_of=snapshot.decision_as_of,
        snapshot_hash=snapshot.content_hash,
        policy_hash=policy.content_hash,
        input_hashes=input_hashes,
        status=status,
        mark_nav=mark_nav,
        cash_buffer_required=cash_buffer_required,
        cash_floor_effective=cash_floor_effective,
        do_nothing_conservative_value=conservative_before,
        after_conservative_value=conservative_before + conservative_delta,
        cash_before=snapshot.free_cash,
        cash_after=cash_after,
        reserved_cash=snapshot.reserved_cash,
        exposure_before=exposure_before,
        exposure_after=exposure_after,
        actions=tuple(actions),
        replacement_histories=histories,
    )


build_capital_plan = allocate_capital
