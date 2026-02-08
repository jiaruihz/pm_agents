import asyncio
from typing import Any, Dict, Optional

import aiohttp


class ToolServiceClient:
    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "ToolServiceClient":
        timeout = aiohttp.ClientTimeout(total=10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        return {"X-API-Key": self.api_key}

    async def _get(self, path: str) -> Any:
        assert self._session is not None
        url = f"{self.base_url}{path}"
        async with self._session.get(url, headers=self._headers()) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        assert self._session is not None
        url = f"{self.base_url}{path}"
        async with self._session.post(url, json=payload, headers=self._headers()) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        return await self._get(f"/orderbook/{token_id}")

    async def get_balance(self) -> Dict[str, Any]:
        return await self._get("/balance")

    async def place_limit_order(self, token_id: str, price: float, size: float, side: str) -> Any:
        payload = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": side,
        }
        return await self._post("/order", payload)

    async def place_market_order(self, token_id: str, amount: float) -> Any:
        payload = {"token_id": token_id, "amount": amount}
        return await self._post("/market-order", payload)

    async def retry(self, func, *args, retries: int = 3, backoff: float = 0.5, **kwargs):
        for i in range(retries):
            try:
                return await func(*args, **kwargs)
            except Exception:
                if i == retries - 1:
                    raise
                await asyncio.sleep(backoff * (2**i))
