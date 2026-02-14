"""HTTP client used by research pipelines (Gamma/CLOB public endpoints)."""

from src.platform.clients.public_http_client import (
    CachedResponse,
    PublicHttpClient,
    RateLimiter,
)

from ..config import get_settings


class HttpClient(PublicHttpClient):
    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            rate_limit_per_sec=settings.rate_limit_per_sec,
            max_concurrency=settings.max_concurrency,
            cache_ttl_seconds=settings.cache_ttl_seconds,
            timeout_seconds=60.0,
        )


__all__ = ["CachedResponse", "PublicHttpClient", "RateLimiter", "HttpClient"]
