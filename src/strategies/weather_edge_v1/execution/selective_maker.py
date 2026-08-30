"""Weather-first Selective Maker V2.1 pure decision and accounting contracts.

This is deliberately a *decision* layer: it accepts point-in-time book and
feature snapshots, produces comparable maker/taker/skip arms, and never
creates an intent or calls a venue.  A blocked arm remains in the denominator.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from src.platform.quote_runtime.target_order_set import TargetOrder, TargetOrderSet

from .contracts import ExecutionContractError, FeeSchedule, JsonContract, MarketBook, VenueCapabilities
from .inventory_economics import IncentiveCredit, canonical_identity
from .quote_engine import QuoteDecision, quote_maker, quote_marketable_limit_exact_shares


CandidateState = Literal["healthy_passive_candidate", "transient_dislocation_candidate", "unknown"]
Route = Literal["maker", "taker", "skip"]
Stage = Literal["zero_notional_readiness", "micro_live_measurement", "frozen_forward_profitability"]
INCENTIVE_KINDS = frozenset(
    {"maker_rebate", "taker_rebate", "liquidity_reward", "holding_reward"}
)


def _text(value: Any, name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ExecutionContractError(f"missing required field: {name}")
    return text


def _decimal(value: Decimal | str | int, name: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ExecutionContractError(f"{name} must be a Decimal, integer, or decimal string")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionContractError(f"invalid decimal for {name}: {value!r}") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise ExecutionContractError(f"{name} must be finite" + (" and non-negative" if nonnegative else ""))
    return result


@dataclass(frozen=True)
class PITMarketState(JsonContract):
    """Only data known at decision time; terminal prices/fills are absent by design."""

    candidate_id: str
    feature_epoch_ref: str
    market_book: MarketBook
    book_age_sec: Decimal | str | int | None
    max_book_age_sec: Decimal | str | int
    coverage_complete: bool
    dislocation_observed: bool = False
    adverse_flow_observed: bool = False
    support_withdrawal_observed: bool = False
    min_passive_spread_ticks: Decimal | str | int = "2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "feature_epoch_ref", _text(self.feature_epoch_ref, "feature_epoch_ref"))
        if not isinstance(self.market_book, MarketBook):
            raise ExecutionContractError("market_book must be MarketBook")
        age = None if self.book_age_sec is None else _decimal(self.book_age_sec, "book_age_sec", nonnegative=True)
        max_age = _decimal(self.max_book_age_sec, "max_book_age_sec", nonnegative=True)
        spread = _decimal(self.min_passive_spread_ticks, "min_passive_spread_ticks", nonnegative=True)
        if spread <= 0:
            raise ExecutionContractError("min_passive_spread_ticks must be positive")
        object.__setattr__(self, "book_age_sec", age)
        object.__setattr__(self, "max_book_age_sec", max_age)
        object.__setattr__(self, "min_passive_spread_ticks", spread)

    @property
    def identity(self) -> str:
        return canonical_identity("selective_maker_pit_state", self)


@dataclass(frozen=True)
class StateClassification(JsonContract):
    candidate_id: str
    state: CandidateState
    blockers: tuple[str, ...] = ()
    identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        if self.state not in {"healthy_passive_candidate", "transient_dislocation_candidate", "unknown"}:
            raise ExecutionContractError("unsupported candidate state")
        object.__setattr__(self, "blockers", tuple(sorted({_text(x, "blocker") for x in self.blockers})))
        object.__setattr__(self, "identity", canonical_identity("selective_maker_state", {
            "candidate_id": self.candidate_id, "state": self.state, "blockers": self.blockers,
        }))


def classify_pit_state(snapshot: PITMarketState) -> StateClassification:
    """Fail closed on book/coverage failures without consulting future outcomes."""
    book = snapshot.market_book
    blockers: list[str] = []
    if not snapshot.coverage_complete:
        blockers.append("coverage_gap")
    if book.status.lower() != "ok":
        blockers.append("book_not_ok")
    if snapshot.book_age_sec is None:
        blockers.append("book_age_unknown")
    elif snapshot.book_age_sec > snapshot.max_book_age_sec:
        blockers.append("book_stale")
    if not book.bids or not book.asks:
        blockers.append("one_sided_book")
    elif book.bids[0].price >= book.asks[0].price:
        blockers.append("book_locked_or_crossed")
    if blockers:
        return StateClassification(snapshot.candidate_id, "unknown", tuple(blockers))
    if snapshot.adverse_flow_observed or snapshot.support_withdrawal_observed:
        reasons = []
        if snapshot.adverse_flow_observed:
            reasons.append("adverse_flow_observed")
        if snapshot.support_withdrawal_observed:
            reasons.append("support_withdrawal_observed")
        return StateClassification(snapshot.candidate_id, "unknown", tuple(reasons))
    spread_ticks = (book.asks[0].price - book.bids[0].price) / book.tick_size
    if snapshot.dislocation_observed:
        return StateClassification(snapshot.candidate_id, "transient_dislocation_candidate")
    if spread_ticks >= snapshot.min_passive_spread_ticks:
        return StateClassification(snapshot.candidate_id, "healthy_passive_candidate")
    return StateClassification(snapshot.candidate_id, "unknown", ("spread_too_narrow",))


@dataclass(frozen=True)
class PassiveFillEstimate(JsonContract):
    """PIT probability that the full passive order fills within its horizon.

    Routing uses ``conservative_probability`` rather than the point estimate.
    A calibrated estimate is intentionally optional: before authoritative
    own-order labels exist, the maker arm is measurable but cannot be treated
    as the profit-maximizing arm.
    """

    model_id: str
    feature_epoch_ref: str
    horizon_seconds: int
    probability: Decimal | str | int
    conservative_probability: Decimal | str | int
    evidence_kind: Literal["actual_own_order_calibrated", "measurement_prior"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id"))
        object.__setattr__(
            self,
            "feature_epoch_ref",
            _text(self.feature_epoch_ref, "feature_epoch_ref"),
        )
        if isinstance(self.horizon_seconds, bool) or self.horizon_seconds <= 0:
            raise ExecutionContractError("horizon_seconds must be a positive integer")
        probability = _decimal(self.probability, "probability", nonnegative=True)
        conservative = _decimal(
            self.conservative_probability,
            "conservative_probability",
            nonnegative=True,
        )
        if probability > 1 or conservative > probability:
            raise ExecutionContractError(
                "fill probabilities require 0 <= conservative_probability <= probability <= 1"
            )
        if self.evidence_kind not in {
            "actual_own_order_calibrated",
            "measurement_prior",
        }:
            raise ExecutionContractError("unsupported fill probability evidence_kind")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "conservative_probability", conservative)

    @property
    def profit_routing_eligible(self) -> bool:
        return self.evidence_kind == "actual_own_order_calibrated"

    @property
    def identity(self) -> str:
        return canonical_identity("selective_maker_fill_estimate", self)


@dataclass(frozen=True)
class TransitionHazardEstimate(JsonContract):
    """PIT probability of adverse value transition during passive waiting.

    Maker routing uses the conservative upper probability.  Public-flow or
    measurement priors may be recorded, but only a frozen model calibrated on
    actual markout labels is eligible for a profit route.
    """

    model_id: str
    feature_epoch_ref: str
    horizon_seconds: int
    probability: Decimal | str | int
    conservative_upper_probability: Decimal | str | int
    evidence_kind: Literal["actual_markout_calibrated", "measurement_prior"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id"))
        object.__setattr__(
            self,
            "feature_epoch_ref",
            _text(self.feature_epoch_ref, "feature_epoch_ref"),
        )
        if isinstance(self.horizon_seconds, bool) or self.horizon_seconds <= 0:
            raise ExecutionContractError("horizon_seconds must be a positive integer")
        probability = _decimal(self.probability, "probability", nonnegative=True)
        upper = _decimal(
            self.conservative_upper_probability,
            "conservative_upper_probability",
            nonnegative=True,
        )
        if probability > upper or upper > 1:
            raise ExecutionContractError(
                "transition hazard requires 0 <= probability <= conservative upper <= 1"
            )
        if self.evidence_kind not in {
            "actual_markout_calibrated",
            "measurement_prior",
        }:
            raise ExecutionContractError("unsupported transition hazard evidence_kind")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "conservative_upper_probability", upper)

    @property
    def profit_routing_eligible(self) -> bool:
        return self.evidence_kind == "actual_markout_calibrated"

    @property
    def identity(self) -> str:
        return canonical_identity("selective_maker_transition_hazard", self)


@dataclass(frozen=True)
class RouteInput(JsonContract):
    pit: PITMarketState
    venue_side: str
    shares: Decimal | str | int
    fee_schedule: FeeSchedule | None
    venue_capabilities: VenueCapabilities | None
    fair_value: Decimal | str | int
    maker_price_rule: str = "improve_bid"
    price_floor: Decimal | str | int | None = None
    price_cap: Decimal | str | int | None = None
    taker_fee_estimate: Decimal | str | int = "0"
    minimum_retained_edge: Decimal | str | int = "0.01"
    uncertainty_buffer: Decimal | str | int = "0.005"
    transition_hazard_estimate: TransitionHazardEstimate | None = None
    transition_hazard_penalty: Decimal | str | int = "0.05"
    inventory_risk_per_share: Decimal | str | int = "0"
    passive_fill_estimate: PassiveFillEstimate | None = None
    nonfill_fallback_edge_per_share: Decimal | str | int = "0"
    maker_order_cost_per_share: Decimal | str | int = "0"
    maker_selection_margin: Decimal | str | int = "0.005"
    taker_urgency: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.pit, PITMarketState):
            raise ExecutionContractError("pit must be PITMarketState")
        side = _text(self.venue_side, "venue_side").upper()
        if side != "BUY":
            raise ExecutionContractError(
                "Selective Maker V2.1 supports BUY acquisition only; W1 owns exits"
            )
        shares = _decimal(self.shares, "shares", nonnegative=True)
        if shares <= 0:
            raise ExecutionContractError("shares must be positive")
        fair = _decimal(self.fair_value, "fair_value")
        if not Decimal("0") <= fair <= Decimal("1"):
            raise ExecutionContractError("fair_value must be in [0, 1]")
        parsed: dict[str, Decimal] = {}
        for name in (
            "taker_fee_estimate",
            "minimum_retained_edge",
            "uncertainty_buffer",
            "transition_hazard_penalty",
            "inventory_risk_per_share",
            "maker_order_cost_per_share",
            "maker_selection_margin",
        ):
            parsed[name] = _decimal(getattr(self, name), name, nonnegative=True)
        parsed["nonfill_fallback_edge_per_share"] = _decimal(
            self.nonfill_fallback_edge_per_share,
            "nonfill_fallback_edge_per_share",
        )
        hazard = self.transition_hazard_estimate
        if hazard is not None:
            if not isinstance(hazard, TransitionHazardEstimate):
                raise ExecutionContractError(
                    "transition_hazard_estimate must be TransitionHazardEstimate"
                )
            if hazard.feature_epoch_ref != self.pit.feature_epoch_ref:
                raise ExecutionContractError(
                    "transition hazard must use the same feature_epoch_ref"
                )
        if self.passive_fill_estimate is not None:
            if not isinstance(self.passive_fill_estimate, PassiveFillEstimate):
                raise ExecutionContractError(
                    "passive_fill_estimate must be PassiveFillEstimate"
                )
            if (
                self.passive_fill_estimate.feature_epoch_ref
                != self.pit.feature_epoch_ref
            ):
                raise ExecutionContractError(
                    "passive fill estimate must use the same feature_epoch_ref"
                )
        object.__setattr__(self, "venue_side", side)
        object.__setattr__(self, "shares", shares)
        object.__setattr__(self, "fair_value", fair)
        for name, value in parsed.items():
            object.__setattr__(self, name, value)

    @property
    def taker_risk_adjusted_fair_value(self) -> Decimal:
        penalty = self.uncertainty_buffer + self.inventory_risk_per_share
        if self.venue_side == "BUY":
            return max(Decimal("0"), self.fair_value - penalty)
        return min(Decimal("1"), self.fair_value + penalty)

    @property
    def maker_risk_adjusted_fair_value(self) -> Decimal | None:
        # Transition hazard is the cost of waiting passively.  Applying it to
        # an immediate taker would erase the very fallback that avoids toxic
        # maker exposure.
        if self.transition_hazard_estimate is None:
            return None
        hazard_penalty = (
            self.transition_hazard_estimate.conservative_upper_probability
            * self.transition_hazard_penalty
        )
        if self.venue_side == "BUY":
            return max(
                Decimal("0"), self.taker_risk_adjusted_fair_value - hazard_penalty
            )
        return min(
            Decimal("1"), self.taker_risk_adjusted_fair_value + hazard_penalty
        )


@dataclass(frozen=True)
class RouteDecision(JsonContract):
    candidate_id: str
    token_id: str
    state: StateClassification
    selected_route: Route
    maker: QuoteDecision | None
    taker: QuoteDecision | None
    skip_incremental_cost: Decimal
    taker_all_in_cost: Decimal | None
    maker_risk_adjusted_fair_value: Decimal | None
    taker_risk_adjusted_fair_value: Decimal
    maker_edge_per_share: Decimal | None
    maker_expected_edge_per_share: Decimal | None
    taker_edge_per_share: Decimal | None
    conservative_fill_probability: Decimal | None
    conservative_transition_hazard: Decimal | None
    blockers: tuple[str, ...]
    identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "token_id", _text(self.token_id, "token_id"))
        if not isinstance(self.state, StateClassification) or self.state.candidate_id != self.candidate_id:
            raise ExecutionContractError("state must belong to candidate_id")
        if self.selected_route not in {"maker", "taker", "skip"}:
            raise ExecutionContractError("unsupported route")
        object.__setattr__(self, "skip_incremental_cost", _decimal(self.skip_incremental_cost, "skip_incremental_cost", nonnegative=True))
        if self.taker_all_in_cost is not None:
            object.__setattr__(self, "taker_all_in_cost", _decimal(self.taker_all_in_cost, "taker_all_in_cost", nonnegative=True))
        if self.maker_risk_adjusted_fair_value is not None:
            object.__setattr__(
                self,
                "maker_risk_adjusted_fair_value",
                _decimal(
                    self.maker_risk_adjusted_fair_value,
                    "maker_risk_adjusted_fair_value",
                ),
            )
        object.__setattr__(
            self,
            "taker_risk_adjusted_fair_value",
            _decimal(
                self.taker_risk_adjusted_fair_value,
                "taker_risk_adjusted_fair_value",
            ),
        )
        for name in (
            "maker_edge_per_share",
            "maker_expected_edge_per_share",
            "taker_edge_per_share",
            "conservative_fill_probability",
            "conservative_transition_hazard",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else _decimal(value, name))
        object.__setattr__(self, "blockers", tuple(sorted({_text(x, "blocker") for x in self.blockers})))
        object.__setattr__(self, "identity", canonical_identity("selective_maker_route", {
            "candidate_id": self.candidate_id, "state_identity": self.state.identity,
            "token_id": self.token_id,
            "selected_route": self.selected_route, "maker": self.maker.to_json() if self.maker else None,
            "taker": self.taker.to_json() if self.taker else None,
            "taker_all_in_cost": self.taker_all_in_cost,
            "maker_risk_adjusted_fair_value": self.maker_risk_adjusted_fair_value,
            "taker_risk_adjusted_fair_value": self.taker_risk_adjusted_fair_value,
            "maker_edge_per_share": self.maker_edge_per_share,
            "maker_expected_edge_per_share": self.maker_expected_edge_per_share,
            "taker_edge_per_share": self.taker_edge_per_share,
            "conservative_fill_probability": self.conservative_fill_probability,
            "conservative_transition_hazard": self.conservative_transition_hazard,
            "blockers": self.blockers,
        }))


def route_candidate(input: RouteInput) -> RouteDecision:
    """Quote all three arms and route on retained weather edge.

    ``unknown`` is a maker-state judgement, not a judgement that the frozen
    weather signal disappeared.  A fresh same-row taker can therefore remain
    the fallback when maker evidence is unavailable.  Only missing/negative
    execution edge selects skip.
    """
    state = classify_pit_state(input.pit)
    taker_fair = input.taker_risk_adjusted_fair_value
    maker_fair = input.maker_risk_adjusted_fair_value
    maker_floor = input.price_floor
    maker_cap = input.price_cap
    taker_floor = input.price_floor
    taker_cap = input.price_cap
    if input.venue_side == "BUY":
        taker_fair_cap = taker_fair - input.minimum_retained_edge
        taker_cap = (
            taker_fair_cap
            if taker_cap is None
            else min(_decimal(taker_cap, "price_cap"), taker_fair_cap)
        )
        if maker_fair is not None:
            maker_fair_cap = maker_fair - input.minimum_retained_edge
            maker_cap = (
                maker_fair_cap
                if maker_cap is None
                else min(_decimal(maker_cap, "price_cap"), maker_fair_cap)
            )
    else:
        taker_fair_floor = taker_fair + input.minimum_retained_edge
        taker_floor = (
            taker_fair_floor
            if taker_floor is None
            else max(_decimal(taker_floor, "price_floor"), taker_fair_floor)
        )
        if maker_fair is not None:
            maker_fair_floor = maker_fair + input.minimum_retained_edge
            maker_floor = (
                maker_fair_floor
                if maker_floor is None
                else max(_decimal(maker_floor, "price_floor"), maker_fair_floor)
            )
    common = dict(
        market_book=input.pit.market_book,
        venue_side=input.venue_side,
        requested_shares=input.shares,
        book_age_sec=input.pit.book_age_sec,
        max_book_age_sec=input.pit.max_book_age_sec,
        fee_schedule=input.fee_schedule,
        venue_capabilities=input.venue_capabilities,
    )
    maker = quote_maker(
        **common,
        price_rule=input.maker_price_rule,  # type: ignore[arg-type]
        price_floor=maker_floor,
        price_cap=maker_cap,
    )
    taker = quote_marketable_limit_exact_shares(
        **common,
        price_floor=taker_floor,
        price_cap=taker_cap,
    )
    taker_cost = None
    maker_edge = None
    maker_expected_edge = None
    taker_edge = None
    maker_joinable = False
    if (
        maker_fair is not None
        and maker.status == "quoted"
        and maker.normalized_price_exact is not None
    ):
        maker_edge = (
            maker_fair - maker.normalized_price_exact
            if input.venue_side == "BUY"
            else maker.normalized_price_exact - maker_fair
        )
        top_same_side = (
            input.pit.market_book.bids[0].price
            if input.venue_side == "BUY" and input.pit.market_book.bids
            else input.pit.market_book.asks[0].price
            if input.venue_side == "SELL" and input.pit.market_book.asks
            else None
        )
        maker_joinable = top_same_side is not None and (
            maker.normalized_price_exact >= top_same_side
            if input.venue_side == "BUY"
            else maker.normalized_price_exact <= top_same_side
        )
    if taker.status == "quoted" and taker.expected_vwap is not None and taker.fillable_shares == input.shares:
        if input.venue_side == "BUY":
            taker_cost = taker.expected_vwap * input.shares + input.taker_fee_estimate
            taker_edge = taker_fair - taker_cost / input.shares
        else:
            # Keep this field non-negative and explicit: for a SELL it is the
            # gross executable value before the upper ledger applies cash-flow
            # signs.  Net proceeds drive the retained-edge check below.
            taker_cost = taker.expected_vwap * input.shares
            taker_edge = (
                taker_cost - input.taker_fee_estimate
            ) / input.shares - taker_fair
    blockers = list(state.blockers)
    fill_estimate = input.passive_fill_estimate
    conservative_fill_probability = (
        None if fill_estimate is None else fill_estimate.conservative_probability
    )
    hazard_estimate = input.transition_hazard_estimate
    conservative_transition_hazard = (
        None
        if hazard_estimate is None
        else hazard_estimate.conservative_upper_probability
    )
    if hazard_estimate is None:
        blockers.append("maker:transition_hazard_missing")
    elif not hazard_estimate.profit_routing_eligible:
        blockers.append("maker:transition_hazard_not_actual_calibrated")
    if fill_estimate is None:
        blockers.append("maker:passive_fill_probability_missing")
    elif not fill_estimate.profit_routing_eligible:
        blockers.append("maker:passive_fill_probability_not_actual_calibrated")
    if maker.status != "quoted":
        blockers.append(f"maker:{maker.reason or maker.status}")
    elif not maker_joinable:
        blockers.append("maker:risk_adjusted_value_outside_joinable_queue")
    if taker.status != "quoted" or taker_cost is None:
        blockers.append(f"taker:{taker.reason or taker.status}")
    if (
        maker_edge is not None
        and fill_estimate is not None
        and fill_estimate.profit_routing_eligible
        and hazard_estimate is not None
        and hazard_estimate.profit_routing_eligible
    ):
        probability = fill_estimate.conservative_probability
        maker_expected_edge = (
            probability * maker_edge
            + (Decimal("1") - probability)
            * input.nonfill_fallback_edge_per_share
            - input.maker_order_cost_per_share
        )
    maker_retains_edge = (
        state.state != "unknown"
        and maker_joinable
        and maker_expected_edge is not None
        and maker_expected_edge >= input.minimum_retained_edge
    )
    taker_retains_edge = taker_edge is not None and taker_edge >= input.minimum_retained_edge
    selected: Route = "skip"
    if input.taker_urgency and taker_retains_edge:
        selected = "taker"
    elif (
        maker_retains_edge
        and (
            not taker_retains_edge
            or maker_expected_edge
            >= taker_edge + input.maker_selection_margin  # type: ignore[operator]
        )
    ):
        selected = "maker"
    elif taker_retains_edge:
        selected = "taker"
    if not maker_retains_edge:
        blockers.append("maker:minimum_retained_edge_not_met")
    if not taker_retains_edge:
        blockers.append("taker:minimum_retained_edge_not_met")
    return RouteDecision(
        input.pit.candidate_id,
        input.pit.market_book.token_id,
        state,
        selected,
        maker,
        taker,
        Decimal("0"),
        taker_cost,
        maker_fair,
        taker_fair,
        maker_edge,
        maker_expected_edge,
        taker_edge,
        conservative_fill_probability,
        conservative_transition_hazard,
        tuple(blockers),
    )


@dataclass(frozen=True)
class StageEvidence(JsonContract):
    stage: Stage
    zero_notional_ready: bool
    actual_maker_fills: int = 0
    forward_lower_bound_per_share: Decimal | str | int | None = None
    minimum_economic_return_per_share: Decimal | str | int | None = None
    minimum_actual_maker_fills: int = 20

    def __post_init__(self) -> None:
        if self.stage not in {"zero_notional_readiness", "micro_live_measurement", "frozen_forward_profitability"}:
            raise ExecutionContractError("unsupported stage")
        if isinstance(self.actual_maker_fills, bool) or self.actual_maker_fills < 0:
            raise ExecutionContractError("actual_maker_fills must be non-negative")
        if (
            isinstance(self.minimum_actual_maker_fills, bool)
            or self.minimum_actual_maker_fills <= 0
        ):
            raise ExecutionContractError(
                "minimum_actual_maker_fills must be a positive integer"
            )
        for name in ("forward_lower_bound_per_share", "minimum_economic_return_per_share"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else _decimal(value, name))

    @property
    def blockers(self) -> tuple[str, ...]:
        result: list[str] = []
        if not self.zero_notional_ready:
            result.append("zero_notional_readiness_incomplete")
        if self.stage == "zero_notional_readiness":
            return tuple(result)
        if self.stage == "micro_live_measurement":
            # No fill floor here: otherwise measurement can never collect fills.
            return tuple(result)
        if self.forward_lower_bound_per_share is None or self.minimum_economic_return_per_share is None:
            result.append("frozen_forward_evidence_missing")
        elif self.actual_maker_fills < self.minimum_actual_maker_fills:
            result.append("first_look_measurement_floor_not_met")
        elif self.forward_lower_bound_per_share <= self.minimum_economic_return_per_share:
            result.append("forward_profitability_not_proven")
        return tuple(result)


@dataclass(frozen=True)
class TinyLiveAuthorization(JsonContract):
    """Pure authorization check.  All controls must be true for permission."""

    authorization_ref: str
    confirm_live: bool
    manifest_healthy: bool
    collateral_asset: str
    expected_collateral_asset: str
    collateral_verified: bool
    requested_notional: Decimal | str | int
    notional_cap: Decimal | str | int
    active_notional: Decimal | str | int
    current_market_notional: Decimal | str | int
    market_notional_cap: Decimal | str | int
    current_city_date_notional: Decimal | str | int
    city_date_notional_cap: Decimal | str | int
    inventory_snapshot_ref: str
    inventory_snapshot_fresh: bool
    daily_submitted_notional: Decimal | str | int
    daily_notional_cap: Decimal | str | int
    current_daily_loss: Decimal | str | int
    daily_loss_cap: Decimal | str | int
    frozen_policy_id: str
    frozen_config_id: str
    sole_reconciler_ready: bool
    cancel_all_ready: bool
    private_user_ws_ready: bool
    rest_reconciliation_ready: bool
    stage0_fee_truth_ready: bool
    stage0_own_order_truth_ready: bool
    release_identity_verified: bool
    stage: StageEvidence

    def __post_init__(self) -> None:
        for name in (
            "authorization_ref",
            "collateral_asset",
            "expected_collateral_asset",
            "frozen_policy_id",
            "frozen_config_id",
            "inventory_snapshot_ref",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in (
            "requested_notional",
            "notional_cap",
            "active_notional",
            "current_market_notional",
            "market_notional_cap",
            "current_city_date_notional",
            "city_date_notional_cap",
            "daily_submitted_notional",
            "daily_notional_cap",
            "current_daily_loss",
            "daily_loss_cap",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, nonnegative=True))
        if not isinstance(self.stage, StageEvidence):
            raise ExecutionContractError("stage must be StageEvidence")

    @property
    def blockers(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.stage.stage != "micro_live_measurement": result.append("not_micro_live_measurement_stage")
        if not self.confirm_live: result.append("live_confirmation_missing")
        if not self.manifest_healthy: result.append("manifest_not_healthy")
        if not self.collateral_verified or self.collateral_asset != self.expected_collateral_asset: result.append("collateral_asset_unverified")
        if self.requested_notional <= 0 or self.active_notional + self.requested_notional > self.notional_cap: result.append("notional_cap_violation")
        if not self.inventory_snapshot_fresh: result.append("inventory_snapshot_stale")
        if self.market_notional_cap <= 0 or self.current_market_notional + self.requested_notional > self.market_notional_cap: result.append("market_notional_cap_violation")
        if self.city_date_notional_cap <= 0 or self.current_city_date_notional + self.requested_notional > self.city_date_notional_cap: result.append("city_date_notional_cap_violation")
        if self.daily_notional_cap <= 0 or self.daily_submitted_notional + self.requested_notional > self.daily_notional_cap: result.append("daily_notional_cap_violation")
        if self.daily_loss_cap <= 0 or self.current_daily_loss >= self.daily_loss_cap: result.append("daily_loss_cap_exhausted")
        if not self.sole_reconciler_ready: result.append("sole_reconciler_not_ready")
        if not self.cancel_all_ready: result.append("cancel_all_not_ready")
        if not self.private_user_ws_ready: result.append("private_user_ws_not_ready")
        if not self.rest_reconciliation_ready: result.append("rest_reconciliation_not_ready")
        if not self.stage0_fee_truth_ready: result.append("stage0_fee_truth_not_ready")
        if not self.stage0_own_order_truth_ready: result.append("stage0_own_order_truth_not_ready")
        if not self.release_identity_verified: result.append("release_identity_unverified")
        result.extend(self.stage.blockers)
        return tuple(result)

    @property
    def allowed(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class AuthorizedRoutePlan(JsonContract):
    """Pure handoff from the router to the sole target-order reconciler.

    A taker route first emits an empty ``CANCEL_ONLY`` target set.  The venue
    adapter may submit the taker only in a later generation after authoritative
    order truth proves that no maker order remains live.  This closes the
    maker-cancel/taker-submit exposure race without making a venue call here.
    """

    route_decision_identity: str
    selected_route: Route
    authorization_ref: str
    frozen_policy_id: str
    frozen_config_id: str
    target_order_set: TargetOrderSet
    taker_quote: QuoteDecision | None
    taker_requires_clean_empty_maker_set: bool
    planned_notional: Decimal | str | int
    identity: str = ""

    def __post_init__(self) -> None:
        for name in (
            "route_decision_identity",
            "authorization_ref",
            "frozen_policy_id",
            "frozen_config_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.selected_route not in {"maker", "taker", "skip"}:
            raise ExecutionContractError("unsupported authorized route")
        if not isinstance(self.target_order_set, TargetOrderSet):
            raise ExecutionContractError("target_order_set must be TargetOrderSet")
        if self.taker_quote is not None and not isinstance(
            self.taker_quote, QuoteDecision
        ):
            raise ExecutionContractError("taker_quote must be QuoteDecision")
        planned = _decimal(
            self.planned_notional, "planned_notional", nonnegative=True
        )
        if self.selected_route == "maker":
            target = (
                self.target_order_set.targets[0]
                if len(self.target_order_set.targets) == 1
                else None
            )
            if (
                self.target_order_set.mode != "NORMAL"
                or target is None
                or target.side != "BUY"
                or not target.post_only
                or target.decision_id != self.route_decision_identity
                or target.continuation_policy_id != self.frozen_policy_id
                or self.taker_quote is not None
                or self.taker_requires_clean_empty_maker_set
                or planned <= 0
            ):
                raise ExecutionContractError("incoherent maker route plan")
        elif self.selected_route == "taker":
            if (
                self.target_order_set.mode != "CANCEL_ONLY"
                or self.target_order_set.targets
                or self.taker_quote is None
                or self.taker_quote.maker_only
                or self.taker_quote.venue_side != "BUY"
                or not self.taker_requires_clean_empty_maker_set
                or planned <= 0
            ):
                raise ExecutionContractError("incoherent taker route plan")
        elif (
            self.target_order_set.mode != "CANCEL_ONLY"
            or self.target_order_set.targets
            or self.taker_quote is not None
            or self.taker_requires_clean_empty_maker_set
            or planned != 0
        ):
            raise ExecutionContractError("incoherent skip route plan")
        object.__setattr__(self, "planned_notional", planned)
        object.__setattr__(
            self,
            "identity",
            canonical_identity(
                "selective_maker_authorized_route_plan",
                {
                    "route_decision_identity": self.route_decision_identity,
                    "selected_route": self.selected_route,
                    "authorization_ref": self.authorization_ref,
                    "frozen_policy_id": self.frozen_policy_id,
                    "frozen_config_id": self.frozen_config_id,
                    "target_order_set_identity": self.target_order_set.identity,
                    "taker_quote": (
                        None if self.taker_quote is None else self.taker_quote.to_json()
                    ),
                    "taker_requires_clean_empty_maker_set": self.taker_requires_clean_empty_maker_set,
                    "planned_notional": planned,
                },
            ),
        )


def build_authorized_route_plan(
    decision: RouteDecision,
    authorization: TinyLiveAuthorization,
    *,
    owner_id: str,
    strategy_head: str,
    capital_sleeve: str,
    generation: int,
    generated_at_utc: str,
    continuation_policy_id: str,
) -> AuthorizedRoutePlan:
    """Create a deterministic target set; never create an order or venue client."""

    if not isinstance(decision, RouteDecision):
        raise ExecutionContractError("decision must be RouteDecision")
    if not isinstance(authorization, TinyLiveAuthorization):
        raise ExecutionContractError("authorization must be TinyLiveAuthorization")
    if not authorization.allowed:
        raise ExecutionContractError(
            "tiny-live authorization blocked: " + ",".join(authorization.blockers)
        )
    policy_id = _text(continuation_policy_id, "continuation_policy_id")
    if policy_id != authorization.frozen_policy_id:
        raise ExecutionContractError(
            "continuation policy does not match authorized frozen_policy_id"
        )
    targets: tuple[TargetOrder, ...] = ()
    mode: Literal["NORMAL", "CANCEL_ONLY"] = "CANCEL_ONLY"
    taker_quote: QuoteDecision | None = None
    taker_handoff = False
    planned_notional = Decimal("0")
    if decision.selected_route == "maker":
        quote = decision.maker
        if (
            quote is None
            or quote.status != "quoted"
            or not quote.maker_only
            or quote.venue_side != "BUY"
            or quote.normalized_price_exact is None
        ):
            raise ExecutionContractError("selected maker route has no executable BUY quote")
        mode = "NORMAL"
        planned_notional = quote.normalized_price_exact * quote.requested_shares
        targets = (
            TargetOrder(
                token_id=decision.token_id,
                side="BUY",
                level=0,
                price=quote.normalized_price_exact,
                size=quote.requested_shares,
                post_only=True,
                expiry_utc=None,
                continuation_policy_id=policy_id,
                decision_id=decision.identity,
            ),
        )
    elif decision.selected_route == "taker":
        quote = decision.taker
        if (
            quote is None
            or quote.status != "quoted"
            or quote.maker_only
            or quote.venue_side != "BUY"
            or decision.taker_all_in_cost is None
        ):
            raise ExecutionContractError("selected taker route has no executable BUY quote")
        planned_notional = decision.taker_all_in_cost
        taker_quote = quote
        taker_handoff = True
    if planned_notional > authorization.requested_notional:
        raise ExecutionContractError(
            "planned route notional exceeds the explicitly authorized request"
        )
    target_set = TargetOrderSet(
        owner_id=_text(owner_id, "owner_id"),
        strategy_head=_text(strategy_head, "strategy_head"),
        capital_sleeve=_text(capital_sleeve, "capital_sleeve"),
        generation=generation,
        generated_at_utc=_text(generated_at_utc, "generated_at_utc"),
        mode=mode,
        targets=targets,
    )
    return AuthorizedRoutePlan(
        route_decision_identity=decision.identity,
        selected_route=decision.selected_route,
        authorization_ref=authorization.authorization_ref,
        frozen_policy_id=authorization.frozen_policy_id,
        frozen_config_id=authorization.frozen_config_id,
        target_order_set=target_set,
        taker_quote=taker_quote,
        taker_requires_clean_empty_maker_set=taker_handoff,
        planned_notional=planned_notional,
    )


@dataclass(frozen=True)
class RealizedPayout(JsonContract):
    payout_identity: str
    asset: str
    total_amount: Decimal | str | int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payout_identity",
            _text(self.payout_identity, "payout_identity"),
        )
        object.__setattr__(self, "asset", _text(self.asset, "asset"))
        object.__setattr__(
            self,
            "total_amount",
            _decimal(self.total_amount, "total_amount", nonnegative=True),
        )

    @property
    def identity(self) -> str:
        return canonical_identity("realized_selective_maker_payout", self)


@dataclass(frozen=True)
class RealizedIncentive(JsonContract):
    kind: str
    payout_identity: str
    asset: str
    amount: Decimal | str | int
    eligibility_identity: str
    realization: str = "realized"

    def __post_init__(self) -> None:
        if self.kind not in INCENTIVE_KINDS:
            raise ExecutionContractError("unsupported incentive kind")
        if self.realization != "realized":
            raise ExecutionContractError("estimated incentive cannot be recorded as realized")
        for name in ("payout_identity", "asset", "eligibility_identity"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "amount", _decimal(self.amount, "amount", nonnegative=True))

    @property
    def identity(self) -> str:
        return canonical_identity("realized_selective_maker_incentive", self)


@dataclass(frozen=True)
class RealizedIncentiveLedger(JsonContract):
    incentives: tuple[RealizedIncentive, ...] = ()
    payouts: tuple[RealizedPayout, ...] = ()

    def __post_init__(self) -> None:
        values = tuple(self.incentives)
        payouts = tuple(self.payouts)
        if any(not isinstance(item, RealizedIncentive) for item in values):
            raise ExecutionContractError("incentives must contain RealizedIncentive")
        if any(not isinstance(item, RealizedPayout) for item in payouts):
            raise ExecutionContractError("payouts must contain RealizedPayout")
        payout_by_id = {item.payout_identity: item for item in payouts}
        if len(payout_by_id) != len(payouts):
            raise ExecutionContractError("duplicate realized payout evidence")
        allocation_keys = {
            (item.payout_identity, item.kind, item.eligibility_identity)
            for item in values
        }
        if len(allocation_keys) != len(values):
            raise ExecutionContractError("duplicate realized payout allocation")
        allocated_by_payout: dict[str, Decimal] = {}
        for item in values:
            payout = payout_by_id.get(item.payout_identity)
            if payout is None:
                raise ExecutionContractError(
                    "realized allocation requires matching payout evidence"
                )
            if payout.asset != item.asset:
                raise ExecutionContractError(
                    "one payout_identity cannot use multiple assets"
                )
            allocated_by_payout[item.payout_identity] = (
                allocated_by_payout.get(item.payout_identity, Decimal("0"))
                + item.amount
            )
        for payout_identity, allocated in allocated_by_payout.items():
            if allocated > payout_by_id[payout_identity].total_amount:
                raise ExecutionContractError(
                    "realized allocations exceed immutable payout total"
                )
        object.__setattr__(
            self,
            "incentives",
            tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item.payout_identity,
                        item.kind,
                        item.eligibility_identity,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "payouts",
            tuple(sorted(payouts, key=lambda item: item.payout_identity)),
        )

    @property
    def total(self) -> Decimal:
        return sum((item.amount for item in self.incentives), Decimal("0"))

    def net_economic(self, *, realized_trading_pnl: Decimal | str | int, realized_costs: Decimal | str | int = "0") -> Decimal:
        return _decimal(realized_trading_pnl, "realized_trading_pnl") + self.total - _decimal(realized_costs, "realized_costs", nonnegative=True)

    def as_episode_credits(self) -> tuple[IncentiveCredit, ...]:
        namespace = {
            "maker_rebate": "maker_rebate",
            "taker_rebate": "taker_rebate",
            "liquidity_reward": "lp_reward",
            "holding_reward": "holding_reward",
        }
        return tuple(
            IncentiveCredit(
                namespace=namespace[item.kind],
                amount=item.amount,
                reference=item.identity,
            )
            for item in self.incentives
        )

    @property
    def identity(self) -> str:
        return canonical_identity("realized_selective_maker_incentive_ledger", self)
