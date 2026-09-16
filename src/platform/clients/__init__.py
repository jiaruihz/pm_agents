"""Shared client implementations."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CachedResponse",
    "PublicHttpClient",
    "RateLimiter",
    "HttpClient",
    "TelegramClient",
    "PolymarketCommentsClient",
    "PolymarketDataClient",
    "PolymarketProfilesClient",
    "PolymarketGammaClient",
]


def __getattr__(name: str) -> Any:
    if name in {"CachedResponse", "PublicHttpClient", "RateLimiter"}:
        from src.platform.clients.public_http_client import CachedResponse, PublicHttpClient, RateLimiter

        return {"CachedResponse": CachedResponse, "PublicHttpClient": PublicHttpClient, "RateLimiter": RateLimiter}[name]
    if name == "HttpClient":
        from src.platform.clients.research_http_client import HttpClient

        return HttpClient
    if name == "TelegramClient":
        from src.platform.clients.telegram_client import TelegramClient

        return TelegramClient
    if name == "PolymarketCommentsClient":
        from src.platform.clients.polymarket_comments import PolymarketCommentsClient

        return PolymarketCommentsClient
    if name == "PolymarketDataClient":
        from src.platform.clients.polymarket_data import PolymarketDataClient

        return PolymarketDataClient
    if name == "PolymarketProfilesClient":
        from src.platform.clients.polymarket_profiles import PolymarketProfilesClient

        return PolymarketProfilesClient
    if name == "PolymarketGammaClient":
        from src.platform.clients.polymarket_gamma import PolymarketGammaClient

        return PolymarketGammaClient
    raise AttributeError(name)
