"""Shared market-data namespace with lazy imports.

Keep this module dependency-light so submodules can be imported independently
in minimal environments (e.g. backtest-only runners without aiohttp/websockets/pandas).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_TO_MODULE = {
    "ToolServiceClient": "src.platform.market_data.http_client",
    "LocalOrderBookStore": "src.platform.market_data.market_ws",
    "MarketWsFeed": "src.platform.market_data.market_ws",
    "orderbook_to_df": "src.platform.market_data.orderbook",
    "best_bid_ask": "src.platform.market_data.orderbook",
    "mid_price": "src.platform.market_data.orderbook",
    "spread": "src.platform.market_data.orderbook",
    "depth_within_delta": "src.platform.market_data.orderbook",
    "mid_from_market": "src.platform.market_data.parsers",
    "parse_partition": "src.platform.market_data.parsers",
    "parse_account_state": "src.platform.market_data.parsers",
    "pending_credit_total": "src.platform.market_data.parsers",
    "CaptureDemand": "src.platform.market_data.capture_demand",
    "CaptureAssignment": "src.platform.market_data.capture_demand",
    "coalesce_capture_demands": "src.platform.market_data.capture_demand",
    "CaptureDemandInbox": "src.platform.market_data.capture_inbox",
    "InboxDemandLine": "src.platform.market_data.capture_inbox",
    "CaptureReceipt": "src.platform.market_data.capture_receipt",
    "MarketExpression": "src.platform.market_data.market_group",
    "MarketGroupSnapshot": "src.platform.market_data.market_group",
    "binary_market_group_snapshot": "src.platform.market_data.market_group",
    "condition_market_group_snapshot": "src.platform.market_data.market_group",
}

__all__ = list(_EXPORT_TO_MODULE.keys())


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
