from typing import Any, Dict, List

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


def orderbook_to_df(orderbook: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    bids = pd.DataFrame(_normalize_levels(orderbook.get("bids")))
    asks = pd.DataFrame(_normalize_levels(orderbook.get("asks")))
    if not bids.empty:
        bids = bids.sort_values("price", ascending=False).reset_index(drop=True)
    if not asks.empty:
        asks = asks.sort_values("price", ascending=True).reset_index(drop=True)
    return {"bids": bids, "asks": asks}


def best_bid_ask(orderbook: Dict[str, Any]) -> Dict[str, float]:
    dfs = orderbook_to_df(orderbook)
    bids, asks = dfs["bids"], dfs["asks"]
    if bids.empty or asks.empty:
        return {"best_bid": 0.0, "best_ask": 0.0}
    return {"best_bid": float(bids.iloc[0]["price"]), "best_ask": float(asks.iloc[0]["price"])}


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
    dfs = orderbook_to_df(orderbook)
    bids, asks = dfs["bids"], dfs["asks"]
    best = best_bid_ask(orderbook)
    if best["best_bid"] <= 0 or best["best_ask"] <= 0:
        return 0.0
    mid = (best["best_bid"] + best["best_ask"]) / 2
    bid_depth = bids[bids["price"] >= (mid - delta)]["size"].sum() if not bids.empty else 0.0
    ask_depth = asks[asks["price"] <= (mid + delta)]["size"].sum() if not asks.empty else 0.0
    return float(bid_depth + ask_depth)
