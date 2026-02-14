from __future__ import annotations

from typing import Any, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def _normalize_levels(levels: Any) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    if not levels:
        return normalized
    for level in levels:
        if isinstance(level, dict):
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = float(level[0])
            size = float(level[1])
        else:
            continue
        if price > 0 and size > 0:
            normalized.append({"price": price, "size": size})
    return normalized


def _best_levels(orderbook: Dict[str, Any]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    bids = _normalize_levels(orderbook.get("bids"))
    asks = _normalize_levels(orderbook.get("asks"))
    best_bid = max(((x["price"], x["size"]) for x in bids), default=(0.0, 0.0), key=lambda x: x[0])
    best_ask = min(((x["price"], x["size"]) for x in asks), default=(0.0, 0.0), key=lambda x: x[0])
    return best_bid, best_ask


def orderbook_to_df(orderbook: Dict[str, Any]) -> Dict[str, "pd.DataFrame"]:
    import pandas as pd

    bids = pd.DataFrame(_normalize_levels(orderbook.get("bids")))
    asks = pd.DataFrame(_normalize_levels(orderbook.get("asks")))
    if not bids.empty:
        bids = bids.sort_values("price", ascending=False).reset_index(drop=True)
    if not asks.empty:
        asks = asks.sort_values("price", ascending=True).reset_index(drop=True)
    return {"bids": bids, "asks": asks}


def best_bid_ask(orderbook: Dict[str, Any]) -> Dict[str, float]:
    (best_bid, _), (best_ask, _) = _best_levels(orderbook)
    return {"best_bid": best_bid, "best_ask": best_ask}


def mid_price(orderbook: Dict[str, Any]) -> float:
    best = best_bid_ask(orderbook)
    if best["best_bid"] <= 0 or best["best_ask"] <= 0:
        return 0.0
    return (best["best_bid"] + best["best_ask"]) / 2


def spread(orderbook: Dict[str, Any]) -> float:
    best = best_bid_ask(orderbook)
    if best["best_bid"] <= 0 or best["best_ask"] <= 0:
        return 0.0
    return best["best_ask"] - best["best_bid"]


def depth_within_delta(orderbook: Dict[str, Any], delta: float) -> float:
    bids = _normalize_levels(orderbook.get("bids"))
    asks = _normalize_levels(orderbook.get("asks"))
    best = best_bid_ask(orderbook)
    if best["best_bid"] <= 0 or best["best_ask"] <= 0:
        return 0.0
    mid = (best["best_bid"] + best["best_ask"]) / 2
    bid_depth = sum(x["size"] for x in bids if x["price"] >= (mid - delta))
    ask_depth = sum(x["size"] for x in asks if x["price"] <= (mid + delta))
    return float(bid_depth + ask_depth)
