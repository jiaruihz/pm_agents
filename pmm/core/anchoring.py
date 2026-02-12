"""Quote anchoring to the live orderbook.

Extracted from tick_loop.py for independent testing and reuse.
"""
from __future__ import annotations


def anchor_quotes_to_book(
    target_bid: float,
    target_ask: float,
    best_bid: float,
    best_ask: float,
    join_epsilon: float,
    fair_value: float,
    min_edge: float,
) -> tuple[float, float]:
    """Anchor theoretical quotes to the current BBO with edge protection.

    Ensures quotes stay within join_epsilon of the BBO while maintaining
    a minimum edge around fair value to avoid adverse selection.
    """
    if best_bid <= 0 or best_ask <= 0:
        return target_bid, target_ask
    eps = max(0.0, join_epsilon)
    edge = max(0.0001, min_edge)

    desired_bid = max(target_bid, best_bid - eps)
    desired_ask = min(target_ask, best_ask + eps)

    # Keep edge around fair value to avoid infinite chasing and adverse selection.
    max_bid = fair_value - edge
    min_ask = fair_value + edge
    anchored_bid = min(desired_bid, max_bid)
    anchored_ask = max(desired_ask, min_ask)

    anchored_bid = max(0.0001, min(0.9998, anchored_bid))
    anchored_ask = max(0.0002, min(0.9999, anchored_ask))
    if anchored_bid >= anchored_ask:
        fallback_bid = max(0.0001, min(0.9998, target_bid))
        fallback_ask = max(0.0002, min(0.9999, target_ask))
        anchored_bid = min(fallback_bid, fair_value - 0.0001)
        anchored_ask = max(fallback_ask, fair_value + 0.0001)
        if anchored_bid >= anchored_ask:
            anchored_ask = min(0.9999, anchored_bid + 0.0001)
    return anchored_bid, anchored_ask
