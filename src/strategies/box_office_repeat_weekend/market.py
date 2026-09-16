from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import requests
from scipy.optimize import Bounds, LinearConstraint, milp


GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_BOOKS_URL = "https://clob.polymarket.com/books"

REPEAT_EVENT_RE = re.compile(
    r"^[\"'“](?P<movie>.+?)[\"'”]\s+"
    r"(?P<week>2nd|second|3rd|third|4th|fourth|5th|fifth)"
    r"(?:\s+3-Day)?\s+Weekend Box Office",
    re.I,
)
WEEK_NUMBER = {
    "2nd": 2,
    "second": 2,
    "3rd": 3,
    "third": 3,
    "4th": 4,
    "fourth": 4,
    "5th": 5,
    "fifth": 5,
}


def json_array(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        parsed = json.loads(value)
        return [str(item) for item in parsed]
    return []


def parse_bracket(value: str) -> tuple[float, float]:
    text = value.lower().replace("$", "").replace(" ", "").replace("m", "")
    if text.startswith("<"):
        return -math.inf, float(text[1:])
    if text.startswith(">"):
        return float(text[1:]), math.inf
    if text.endswith("+"):
        return float(text[:-1]), math.inf
    match = re.fullmatch(r"([0-9.]+)-([0-9.]+)", text)
    if not match:
        raise ValueError(f"unrecognized bracket: {value}")
    return float(match.group(1)), float(match.group(2))


def exhaustive(labels: Iterable[str]) -> bool:
    intervals = sorted(parse_bracket(label) for label in labels)
    return bool(
        intervals
        and math.isinf(intervals[0][0])
        and intervals[0][0] < 0
        and math.isinf(intervals[-1][1])
        and intervals[-1][1] > 0
        and all(abs(left[1] - right[0]) < 1e-9 for left, right in zip(intervals, intervals[1:]))
    )


def parse_repeat_event(event: dict[str, Any]) -> dict[str, Any] | None:
    match = REPEAT_EVENT_RE.search(str(event.get("title") or ""))
    if not match:
        return None
    markets = []
    for market in event.get("markets") or []:
        tokens = json_array(market.get("clobTokenIds"))
        outcome_prices = json_array(market.get("outcomePrices"))
        if len(tokens) != 2:
            continue
        label = str(market.get("groupItemTitle") or market.get("question") or "")
        parse_bracket(label)
        markets.append(
            {
                "market_id": str(market.get("id") or ""),
                "condition_id": str(market.get("conditionId") or ""),
                "label": label,
                "yes_token_id": tokens[0],
                "no_token_id": tokens[1],
                "fee_rate": float(
                    (market.get("feeSchedule") or {}).get("rate") or 0.0
                ),
                "min_order_size": float(market.get("orderMinSize") or 5.0),
                "tick_size": float(market.get("orderPriceMinTickSize") or 0.01),
                "accepting_orders": bool(market.get("acceptingOrders", True)),
                "mark_price": (
                    float(outcome_prices[0]) if outcome_prices else None
                ),
            }
        )
    return {
        "event_id": str(event["id"]),
        "event_slug": str(event.get("slug") or ""),
        "event_title": str(event.get("title") or ""),
        "movie": match.group("movie"),
        "week": WEEK_NUMBER[match.group("week").lower()],
        "end_date": str(event.get("endDate") or ""),
        "neg_risk": bool(event.get("negRisk"))
        or all(bool(market.get("negRisk")) for market in event.get("markets") or []),
        "markets": markets,
        "is_exhaustive": exhaustive([market["label"] for market in markets]),
    }


def normalized_levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    levels = []
    for level in book.get(side) or []:
        price = float(level["price"])
        size = float(level["size"])
        if 0 < price < 1 and size > 0:
            levels.append((price, size))
    return sorted(levels, reverse=side == "bids")


@dataclass(frozen=True)
class BuyCost:
    shares: float
    filled_shares: float
    notional: float | None
    vwap: float | None
    fee_usdc: float | None
    all_in_cost: float | None

    @property
    def complete(self) -> bool:
        return self.filled_shares + 1e-9 >= self.shares


def taker_fee_usdc(shares: float, price: float, fee_rate: float) -> float:
    return round(shares * fee_rate * price * (1.0 - price), 5)


def depth_weighted_buy(
    book: dict[str, Any], shares: float, *, fee_rate: float
) -> BuyCost:
    if shares <= 0:
        raise ValueError("shares must be positive")
    remaining = shares
    notional = 0.0
    fee = 0.0
    for price, available in normalized_levels(book, "asks"):
        take = min(remaining, available)
        notional += take * price
        fee += taker_fee_usdc(take, price, fee_rate)
        remaining -= take
        if remaining <= 1e-9:
            break
    filled = shares - remaining
    if remaining > 1e-9:
        return BuyCost(shares, filled, None, None, None, None)
    return BuyCost(
        shares,
        filled,
        notional,
        notional / shares,
        fee,
        notional + fee,
    )


def basket_opportunities(
    event: dict[str, Any],
    books_by_token: dict[str, dict[str, Any]],
    *,
    shares: float,
    fee_rates_by_token: dict[str, float],
) -> list[dict[str, Any]]:
    """Return model-free complete-set taker economics for YES and NO baskets."""

    if not event.get("is_exhaustive") or not event.get("neg_risk"):
        return []
    outputs = []
    n = len(event["markets"])
    for side, payout_per_set in (("YES", 1.0), ("NO", float(n - 1))):
        legs = []
        for market in event["markets"]:
            token = market[f"{side.lower()}_token_id"]
            book = books_by_token.get(token)
            cost = (
                depth_weighted_buy(
                    book,
                    shares,
                    fee_rate=fee_rates_by_token.get(token, 0.0),
                )
                if book is not None
                else BuyCost(shares, 0.0, None, None, None, None)
            )
            legs.append(
                {
                    "market_id": market["market_id"],
                    "condition_id": market["condition_id"],
                    "bracket": market["label"],
                    "token_id": token,
                    "cost": cost,
                }
            )
        complete = all(leg["cost"].complete for leg in legs)
        total_cost = (
            sum(float(leg["cost"].all_in_cost) for leg in legs) if complete else None
        )
        payout = shares * payout_per_set
        outputs.append(
            {
                "event_id": event["event_id"],
                "movie": event["movie"],
                "week": event["week"],
                "side": side,
                "shares_per_leg": shares,
                "leg_count": n,
                "complete_depth": complete,
                "all_in_cost": total_cost,
                "settlement_payout": payout,
                "locked_profit": payout - total_cost if total_cost is not None else None,
                "roi": (payout - total_cost) / total_cost if total_cost else None,
                "legs": legs,
            }
        )
    return outputs


def _atomic_test_points(events: list[dict[str, Any]]) -> list[float]:
    boundaries = sorted(
        {
            endpoint
            for event in events
            for market in event["markets"]
            for endpoint in parse_bracket(market["label"])
            if math.isfinite(endpoint)
        }
    )
    if not boundaries:
        return [0.0]
    points = [boundaries[0] - 1.0]
    points.extend((left + right) / 2.0 for left, right in zip(boundaries, boundaries[1:]))
    points.append(boundaries[-1] + 1.0)
    return points


def _contains(label: str, value: float) -> bool:
    lo, hi = parse_bracket(label)
    return lo <= value < hi


def minimum_binary_cover(
    events: list[dict[str, Any]],
    books_by_token: dict[str, dict[str, Any]],
    *,
    shares: float,
) -> dict[str, Any] | None:
    """Find the cheapest executable fixed-size payoff cover across partitions.

    Each decision is binary: buy exactly ``shares`` of a token or do not buy it.
    Requiring at least one winning token in every atomic gross state locks a
    settlement payout of at least ``shares``. This captures same-market YES+NO,
    full-partition baskets, and cross-partition strike inconsistencies without
    assuming a box-office probability model.
    """

    if not events:
        return None
    points = _atomic_test_points(events)
    legs = []
    for event in events:
        for market in event["markets"]:
            for side in ("YES", "NO"):
                token = market[f"{side.lower()}_token_id"]
                book = books_by_token.get(token)
                if book is None:
                    continue
                cost = depth_weighted_buy(
                    book, shares, fee_rate=float(market["fee_rate"])
                )
                if not cost.complete:
                    continue
                yes_payoff = np.asarray(
                    [float(_contains(market["label"], point)) for point in points]
                )
                payoff = yes_payoff if side == "YES" else 1.0 - yes_payoff
                legs.append(
                    {
                        "event_id": event["event_id"],
                        "market_id": market["market_id"],
                        "condition_id": market["condition_id"],
                        "bracket": market["label"],
                        "side": side,
                        "token_id": token,
                        "payoff": payoff,
                        "cost": cost,
                    }
                )
    if not legs:
        return None
    payoff_matrix = np.column_stack([leg["payoff"] for leg in legs])
    costs = np.asarray([float(leg["cost"].all_in_cost) for leg in legs])
    result = milp(
        c=costs,
        integrality=np.ones(len(legs)),
        bounds=Bounds(np.zeros(len(legs)), np.ones(len(legs))),
        constraints=LinearConstraint(
            payoff_matrix, np.ones(len(points)), np.full(len(points), np.inf)
        ),
        options={"time_limit": 10.0},
    )
    if not result.success or result.x is None:
        return None
    selected_indexes = [index for index, value in enumerate(result.x) if value > 0.5]
    selected = [legs[index] for index in selected_indexes]
    terminal_counts = payoff_matrix[:, selected_indexes].sum(axis=1)
    all_in_cost = float(costs[selected_indexes].sum())
    payout_floor = float(terminal_counts.min() * shares)
    return {
        "event_ids": [event["event_id"] for event in events],
        "movie": events[0]["movie"],
        "week": events[0]["week"],
        "shares_per_leg": shares,
        "atomic_state_count": len(points),
        "selected_leg_count": len(selected),
        "all_in_cost": all_in_cost,
        "settlement_payout_floor": payout_floor,
        "locked_profit": payout_floor - all_in_cost,
        "roi": (payout_floor - all_in_cost) / all_in_cost if all_in_cost else None,
        "legs": [
            {
                key: value
                for key, value in leg.items()
                if key not in {"payoff", "cost"}
            }
            | {
                "vwap": leg["cost"].vwap,
                "fee_usdc": leg["cost"].fee_usdc,
                "all_in_cost": leg["cost"].all_in_cost,
            }
            for leg in selected
        ],
    }


class PolymarketBoxOfficeClient:
    def __init__(
        self, session: requests.Session | None = None, *, timeout: float = 30.0
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "pm-agents-box-office-shadow/1.0"
        )
        self.timeout = timeout

    def discover_open_repeat_events(self) -> list[dict[str, Any]]:
        response = self.session.get(
            GAMMA_EVENTS_URL,
            params={"closed": "false", "tag_id": 51, "limit": 100, "offset": 0},
            timeout=self.timeout,
        )
        response.raise_for_status()
        parsed = [parse_repeat_event(event) for event in response.json()]
        return [
            event
            for event in parsed
            if event is not None
            and event["markets"]
            and any(market["accepting_orders"] for market in event["markets"])
        ]

    def fetch_books(self, token_ids: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if not token_ids:
            return observed_at, {}
        response = self.session.post(
            CLOB_BOOKS_URL,
            json=[{"token_id": token_id} for token_id in token_ids],
            timeout=self.timeout,
        )
        response.raise_for_status()
        books = {str(book["asset_id"]): book for book in response.json()}
        return observed_at, books
