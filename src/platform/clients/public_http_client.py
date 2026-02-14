"""Public-data HTTP client with rate limiting, retries, caching and concurrency control."""

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


class RateLimiter:
    def __init__(self, rate_per_sec: float) -> None:
        self.rate = max(rate_per_sec, 0.1)
        self._tokens = self.rate
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
            if self._tokens < 1:
                sleep_for = (1 - self._tokens) / self.rate
                await asyncio.sleep(sleep_for)
                self._tokens = 0
            else:
                self._tokens -= 1


class CachedResponse:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = max(0, ttl_seconds)
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._store:
            expires, value = self._store[key]
            if expires >= time.time():
                return value
            self._store.pop(key, None)
        return None

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        self._store[key] = (time.time() + self.ttl, value)


class PublicHttpClient:
    """Reusable async client for public market-data APIs (Gamma/CLOB)."""

    def __init__(
        self,
        rate_limit_per_sec: float = 5.0,
        max_concurrency: int = 5,
        cache_ttl_seconds: int = 300,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.rate_limiter = RateLimiter(rate_limit_per_sec)
        self.cache = CachedResponse(cache_ttl_seconds)
        self.semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    @staticmethod
    def _retryable_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code if exc.response is not None else None
            return status in {429, 500, 502, 503, 504}
        return isinstance(exc, httpx.RequestError)

    @retry(
        retry=retry_if_exception(_retryable_error),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        await self.rate_limiter.acquire()
        async with self.semaphore:
            resp = await self.client.get(url, params=params)
            if resp.status_code == 429:
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()

    async def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        key = f"{url}::{params or {}}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        data = await self._get(url, params=params)
        self.cache.set(key, data)
        return data

    async def aclose(self) -> None:
        await self.client.aclose()

