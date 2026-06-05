"""
city_day_basket.py

Pure function that takes a set of bracket candidates for one
(city, target_date, snapshot_ts) and produces one coherent basket plan:
which legs to take, what the payoff vector looks like across every
possible final temperature, and the trade/shadow/skip decision.

Reference design: docs/WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md §2.3-2.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

Side = Literal["BUY_YES", "BUY_NO"]
Decision = Literal["TRADE", "REDUCE", "SHADOW", "SKIP"]


@dataclass(frozen=True)
class BasketConfig:
    single_leg_notional_small: float = 3.0
    single_leg_notional_normal: float = 5.0
    city_day_notional_cap: float = 15.0
    daily_notional_cap: float = 50.0
    max_no_legs_per_city_day: int = 3
    forecast_jump_reduce_threshold_f: float = 1.0
    edge_small_threshold: float = 0.03
    edge_normal_threshold: float = 0.06
    narrow_bracket_edge_multiplier: float = 1.5
    prefer_no_over_yes: bool = True


@dataclass(frozen=True)
class BracketCandidate:
    bracket_id: str             # canonical bracket key, e.g. "Tokyo_2026-05-09_23"
    bracket_label: str          # human label, e.g. "23"
    final_temp_key: str         # canonical key used in payoff_by_final_temp dict
    p_yes_used: float           # post-blend probability
    yes_best_ask: Optional[float]
    no_best_ask: Optional[float]
    forecast_jump_f: float = 0.0
    side_flip_count_today: int = 0
    narrow_bracket: bool = False


@dataclass(frozen=True)
class BasketInput:
    city: str
    target_date: str            # YYYY-MM-DD
    snapshot_ts: str            # ISO-8601 UTC
    candidates: list[BracketCandidate]
    config: BasketConfig
    existing_city_day_exposure: float = 0.0  # already-deployed USD on this city-day
    possible_final_temps: Optional[list[str]] = None  # default = candidate keys


@dataclass(frozen=True)
class SelectedLeg:
    bracket_id: str
    bracket_label: str
    final_temp_key: str
    side: Side
    entry_price: float
    notional_usd: float
    executable_edge: float


@dataclass(frozen=True)
class BasketPlan:
    city: str
    target_date: str
    snapshot_ts: str
    basket_id: str
    selected_legs: list[SelectedLeg]
    shadow_legs: list[SelectedLeg]
    payoff_by_final_temp: dict[str, float]
    worst_case_loss: float       # negative number; 0 if no legs
    expected_value: float
    max_notional: float          # sum of |notional| across selected_legs
    decision: Decision
    reason_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _basket_id(city: str, target_date: str, snapshot_ts: str) -> str:
    return f"{city}_{target_date}_{snapshot_ts}"


def _leg_payoff(side: Side, entry_price: float, notional: float, hit: bool) -> float:
    """
    Payoff of a single $notional leg at entry_price given whether the bracket
    we bet on is the final winning bracket.

    BUY_YES wins when its bracket is the final winner.
    BUY_NO  wins when its bracket is NOT the final winner.
    """
    if entry_price <= 0.0 or entry_price >= 1.0:
        raise ValueError(f"entry_price must be in (0,1), got {entry_price}")
    if side == "BUY_YES":
        return notional * (1.0 - entry_price) / entry_price if hit else -notional
    # BUY_NO
    return -notional if hit else notional * (1.0 - entry_price) / (1.0 - (1.0 - entry_price))


def _no_leg_profit_if_other(entry_no_price: float, notional: float) -> float:
    """When a BUY_NO leg's bracket is NOT the final winner, profit = notional * (1-q)/q
    where q is the NO ask price. Cleaner than _leg_payoff's algebra for NO."""
    return notional * (1.0 - entry_no_price) / entry_no_price


def _yes_leg_profit_if_hit(entry_yes_price: float, notional: float) -> float:
    return notional * (1.0 - entry_yes_price) / entry_yes_price


def _payoff_vector(
    legs: list[SelectedLeg],
    possible_final_temps: list[str],
) -> dict[str, float]:
    """Compute basket payoff for each possible final temp."""
    payoff: dict[str, float] = {t: 0.0 for t in possible_final_temps}
    for leg in legs:
        for t in possible_final_temps:
            hit = leg.final_temp_key == t
            if leg.side == "BUY_YES":
                payoff[t] += (
                    _yes_leg_profit_if_hit(leg.entry_price, leg.notional_usd)
                    if hit
                    else -leg.notional_usd
                )
            else:  # BUY_NO
                payoff[t] += (
                    -leg.notional_usd
                    if hit
                    else _no_leg_profit_if_other(leg.entry_price, leg.notional_usd)
                )
    return payoff


def _expected_value(
    legs: list[SelectedLeg],
    p_by_bracket: dict[str, float],
    payoff_by_final_temp: dict[str, float],
) -> float:
    """
    EV computed with the basket's own probability vector. We use the YES leg
    p_yes_used per bracket as p(final_temp == bracket); buckets that have no
    candidate get residual mass split uniformly. This is good enough for v1
    ranking, not a calibrated forecast.
    """
    explicit = sum(p_by_bracket.values())
    residual = max(0.0, 1.0 - explicit)
    buckets_no_p = [t for t in payoff_by_final_temp if t not in p_by_bracket]
    residual_each = residual / len(buckets_no_p) if buckets_no_p else 0.0

    ev = 0.0
    for t, payoff in payoff_by_final_temp.items():
        p = p_by_bracket.get(t, residual_each)
        ev += p * payoff
    return ev


def _stability_ok(c: BracketCandidate, cfg: BasketConfig) -> tuple[bool, list[str]]:
    """Return (ok, reasons). When not ok the leg must be shadow-only."""
    reasons: list[str] = []
    if c.side_flip_count_today > 0:
        reasons.append(f"side_flip:{c.bracket_id}")
    if c.forecast_jump_f >= cfg.forecast_jump_reduce_threshold_f:
        reasons.append(f"forecast_jump:{c.bracket_id}:{c.forecast_jump_f}")
    return (len(reasons) == 0, reasons)


def _edge_threshold(c: BracketCandidate, cfg: BasketConfig) -> float:
    """Narrow brackets need a higher edge to enter live."""
    base = cfg.edge_small_threshold
    return base * cfg.narrow_bracket_edge_multiplier if c.narrow_bracket else base


def _size_for_edge(edge: float, cfg: BasketConfig) -> float:
    if edge >= cfg.edge_normal_threshold:
        return cfg.single_leg_notional_normal
    return cfg.single_leg_notional_small


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_city_day_basket(req: BasketInput) -> BasketPlan:
    cfg = req.config
    bid = _basket_id(req.city, req.target_date, req.snapshot_ts)

    if not req.candidates:
        return BasketPlan(
            city=req.city,
            target_date=req.target_date,
            snapshot_ts=req.snapshot_ts,
            basket_id=bid,
            selected_legs=[],
            shadow_legs=[],
            payoff_by_final_temp={},
            worst_case_loss=0.0,
            expected_value=0.0,
            max_notional=0.0,
            decision="SKIP",
            reason_codes=["no_candidates"],
        )

    possible_temps = req.possible_final_temps or [
        c.final_temp_key for c in req.candidates
    ]

    # ---- Step 1: split stable vs unstable candidates -----------------------
    stable: list[BracketCandidate] = []
    shadow_only: list[BracketCandidate] = []
    reason_codes: list[str] = []
    for c in req.candidates:
        ok, why = _stability_ok(c, cfg)
        if ok:
            stable.append(c)
        else:
            shadow_only.append(c)
            reason_codes.extend(why)

    # ---- Step 2: paper-only edge → cannot trade live -----------------------
    def _no_book(c: BracketCandidate) -> bool:
        return c.yes_best_ask is None and c.no_best_ask is None

    if all(_no_book(c) for c in req.candidates):
        return BasketPlan(
            city=req.city,
            target_date=req.target_date,
            snapshot_ts=req.snapshot_ts,
            basket_id=bid,
            selected_legs=[],
            shadow_legs=[],
            payoff_by_final_temp={t: 0.0 for t in possible_temps},
            worst_case_loss=0.0,
            expected_value=0.0,
            max_notional=0.0,
            decision="SKIP",
            reason_codes=["paper_only_edge_no_orderbook"],
        )

    # ---- Step 3: build per-candidate yes/no edge ---------------------------
    yes_choices: list[tuple[BracketCandidate, float, float]] = []  # (cand, edge, size)
    no_choices: list[tuple[BracketCandidate, float, float]] = []

    for c in stable:
        thr = _edge_threshold(c, cfg)
        if c.yes_best_ask is not None:
            edge_yes = c.p_yes_used - c.yes_best_ask
            if edge_yes > thr:
                yes_choices.append((c, edge_yes, _size_for_edge(edge_yes, cfg)))
        if c.no_best_ask is not None:
            edge_no = (1.0 - c.p_yes_used) - c.no_best_ask
            if edge_no > thr:
                no_choices.append((c, edge_no, _size_for_edge(edge_no, cfg)))

    # ---- Step 4: pick legs (NO-priority, no YES/NO mixing on same bracket)
    selected: list[SelectedLeg] = []

    # Resolve same-bracket YES vs NO collisions by keeping the higher-edge side.
    yes_by_bracket = {c.bracket_id: (c, e, n) for c, e, n in yes_choices}
    no_by_bracket = {c.bracket_id: (c, e, n) for c, e, n in no_choices}
    collisions = set(yes_by_bracket) & set(no_by_bracket)
    for bracket_id in collisions:
        ye = yes_by_bracket[bracket_id][1]
        ne = no_by_bracket[bracket_id][1]
        if ne >= ye:
            del yes_by_bracket[bracket_id]
        else:
            del no_by_bracket[bracket_id]

    no_sorted = sorted(
        no_by_bracket.values(), key=lambda triple: triple[1], reverse=True
    )
    yes_sorted = sorted(
        yes_by_bracket.values(), key=lambda triple: triple[1], reverse=True
    )

    if cfg.prefer_no_over_yes:
        for c, edge, size in no_sorted[: cfg.max_no_legs_per_city_day]:
            selected.append(
                SelectedLeg(
                    bracket_id=c.bracket_id,
                    bracket_label=c.bracket_label,
                    final_temp_key=c.final_temp_key,
                    side="BUY_NO",
                    entry_price=c.no_best_ask,  # type: ignore[arg-type]
                    notional_usd=size,
                    executable_edge=edge,
                )
            )
        # Only add YES legs when no NO legs were viable — keep v1 simple.
        if not selected and yes_sorted:
            c, edge, size = yes_sorted[0]
            selected.append(
                SelectedLeg(
                    bracket_id=c.bracket_id,
                    bracket_label=c.bracket_label,
                    final_temp_key=c.final_temp_key,
                    side="BUY_YES",
                    entry_price=c.yes_best_ask,  # type: ignore[arg-type]
                    notional_usd=size,
                    executable_edge=edge,
                )
            )
    else:
        combined = sorted(
            list(no_by_bracket.values()) + list(yes_by_bracket.values()),
            key=lambda triple: triple[1],
            reverse=True,
        )
        for c, edge, size in combined[: cfg.max_no_legs_per_city_day]:
            side: Side = "BUY_NO" if c.bracket_id in no_by_bracket else "BUY_YES"
            entry = c.no_best_ask if side == "BUY_NO" else c.yes_best_ask
            assert entry is not None
            selected.append(
                SelectedLeg(
                    bracket_id=c.bracket_id,
                    bracket_label=c.bracket_label,
                    final_temp_key=c.final_temp_key,
                    side=side,
                    entry_price=entry,
                    notional_usd=size,
                    executable_edge=edge,
                )
            )

    shadow_legs = _materialize_shadow_legs(shadow_only, cfg)

    if not selected:
        return BasketPlan(
            city=req.city,
            target_date=req.target_date,
            snapshot_ts=req.snapshot_ts,
            basket_id=bid,
            selected_legs=[],
            shadow_legs=shadow_legs,
            payoff_by_final_temp={t: 0.0 for t in possible_temps},
            worst_case_loss=0.0,
            expected_value=0.0,
            max_notional=0.0,
            decision="SHADOW" if shadow_legs else "SKIP",
            reason_codes=reason_codes
            + (["no_live_edge"] if not shadow_legs else ["all_unstable_shadow"]),
        )

    # ---- Step 5: compute payoff matrix, worst-case, EV ---------------------
    payoff = _payoff_vector(selected, possible_temps)
    worst = min(payoff.values()) if payoff else 0.0
    max_notional = sum(leg.notional_usd for leg in selected)

    p_by_bracket = {
        c.final_temp_key: c.p_yes_used
        for c in req.candidates
        if c.final_temp_key in possible_temps
    }
    ev = _expected_value(selected, p_by_bracket, payoff)

    # ---- Step 6: risk gates ------------------------------------------------
    decision: Decision = "TRADE"
    headroom = cfg.city_day_notional_cap - req.existing_city_day_exposure
    if headroom <= 0:
        decision = "SHADOW"
        reason_codes.append("city_day_cap_already_exhausted")

    # Worst-case loss is a negative number, take abs for comparison.
    if abs(worst) > headroom > 0:
        scale = max(0.0, headroom) / abs(worst)
        if scale <= 0.0:
            decision = "SHADOW"
            reason_codes.append("worst_case_exceeds_headroom")
        else:
            selected = [
                SelectedLeg(
                    bracket_id=l.bracket_id,
                    bracket_label=l.bracket_label,
                    final_temp_key=l.final_temp_key,
                    side=l.side,
                    entry_price=l.entry_price,
                    notional_usd=round(l.notional_usd * scale, 4),
                    executable_edge=l.executable_edge,
                )
                for l in selected
            ]
            payoff = _payoff_vector(selected, possible_temps)
            worst = min(payoff.values()) if payoff else 0.0
            max_notional = sum(leg.notional_usd for leg in selected)
            ev = _expected_value(selected, p_by_bracket, payoff)
            decision = "REDUCE"
            reason_codes.append("scaled_to_city_day_cap")

    if ev <= 0.0 and decision == "TRADE":
        decision = "SHADOW"
        reason_codes.append("non_positive_ev")

    return BasketPlan(
        city=req.city,
        target_date=req.target_date,
        snapshot_ts=req.snapshot_ts,
        basket_id=bid,
        selected_legs=selected,
        shadow_legs=shadow_legs,
        payoff_by_final_temp=payoff,
        worst_case_loss=worst,
        expected_value=ev,
        max_notional=max_notional,
        decision=decision,
        reason_codes=reason_codes,
    )


def _materialize_shadow_legs(
    candidates: Iterable[BracketCandidate], cfg: BasketConfig
) -> list[SelectedLeg]:
    """Emit shadow legs for unstable candidates so the dashboard can show them."""
    out: list[SelectedLeg] = []
    for c in candidates:
        thr = _edge_threshold(c, cfg)
        if c.no_best_ask is not None:
            edge_no = (1.0 - c.p_yes_used) - c.no_best_ask
            if edge_no > thr:
                out.append(
                    SelectedLeg(
                        bracket_id=c.bracket_id,
                        bracket_label=c.bracket_label,
                        final_temp_key=c.final_temp_key,
                        side="BUY_NO",
                        entry_price=c.no_best_ask,
                        notional_usd=cfg.single_leg_notional_small,
                        executable_edge=edge_no,
                    )
                )
                continue
        if c.yes_best_ask is not None:
            edge_yes = c.p_yes_used - c.yes_best_ask
            if edge_yes > thr:
                out.append(
                    SelectedLeg(
                        bracket_id=c.bracket_id,
                        bracket_label=c.bracket_label,
                        final_temp_key=c.final_temp_key,
                        side="BUY_YES",
                        entry_price=c.yes_best_ask,
                        notional_usd=cfg.single_leg_notional_small,
                        executable_edge=edge_yes,
                    )
                )
    return out
