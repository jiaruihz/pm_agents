"""Compatibility import for the platform Polymarket WS reconstruction contract.

The implementation moved to ``src.platform.market_data``.  Existing weather
producers and replay code keep this import path so the extraction does not
change runtime behavior, serialized schema versions, or snapshot identities.
"""

from src.platform.market_data.ws_incremental_book import *  # noqa: F401,F403
