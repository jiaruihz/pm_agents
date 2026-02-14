"""Polymarket automated order helpers (requires external signing/auth)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urlencode

import httpx

from .config import get_settings
from .clients.http import RateLimiter


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_price(entries: Any) -> Optional[float]:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict):
            price = entry.get("price") or entry.get("p")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 1:
            price = entry[0]
        else:
            continue
        try:
            return float(price)
        except Exception:
            continue
    return None


def load_token_ids(path: str) -> list[str]:
    """从文件中读取 token_id 列表，支持逗号分隔与注释行."""
    token_ids: list[str] = []
    seen = set()
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")] if "," in line else [line]
            for part in parts:
                if not part or part in seen:
                    continue
                seen.add(part)
                token_ids.append(part)
    return token_ids


# 订单簿快照
@dataclass(frozen=True)
class PriceSnapshot:
    token_id: str
    fetched_at_utc: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    mid: Optional[float]
    spread: Optional[float]


# 下单请求对象（未签名）
@dataclass(frozen=True)
class OrderRequest:
    token_id: str
    side: str
    price: float
    size: float
    order_type: str = "limit"
    time_in_force: str = "gtc"
    post_only: bool = True
    reduce_only: bool = False
    expires_at: Optional[int] = None
    nonce: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        payload = {
            "token_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "post_only": self.post_only,
            "reduce_only": self.reduce_only,
        }
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        if self.nonce is not None:
            payload["nonce"] = self.nonce
        if self.extra:
            payload.update(self.extra)
        return payload


# 签名后的订单载荷
@dataclass(frozen=True)
class SignedOrder:
    order: Dict[str, Any]
    signature: str
    signer: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload = {"order": self.order, "signature": self.signature}
        if self.signer:
            payload["signer"] = self.signer
        return payload


# 订单签名接口（由外部实现）
class OrderSigner(Protocol):
    def sign(self, order: OrderRequest) -> SignedOrder:
        ...


# 鉴权头构造接口（由外部实现）
class AuthProvider(Protocol):
    def build_headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        ...


# 直接复用静态 headers 的鉴权实现
@dataclass(frozen=True)
class StaticHeadersAuth:
    headers: Dict[str, str]

    def build_headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        return dict(self.headers)


# CLOB 路径配置，便于不同环境复用
@dataclass(frozen=True)
class CLOBEndpoints:
    submit_order: str = "/order"
    cancel_order: str = "/order/cancel"
    open_orders: str = "/orders"
    order_status: str = "/order"


# 订单簿读取客户端（轮询 /book）
class OrderbookClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 20.0) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.clob_base_url).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._rate_limiter = RateLimiter(settings.rate_limit_per_sec)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def get_book(self, token_id: str, depth: Optional[int] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"token_id": token_id}
        if depth is not None:
            params["depth"] = depth
        await self._rate_limiter.acquire()
        async with self._semaphore:
            resp = await self._client.get(f"{self.base_url}/book", params=params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}

    async def get_best_snapshot(self, token_id: str, depth: Optional[int] = None) -> PriceSnapshot:
        book = await self.get_book(token_id, depth=depth)
        bids = book.get("bids") if isinstance(book, dict) else []
        asks = book.get("asks") if isinstance(book, dict) else []
        best_bid = _first_price(bids)
        best_ask = _first_price(asks)
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        return PriceSnapshot(
            token_id=token_id,
            fetched_at_utc=_now_utc_iso(),
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=spread,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


# 下单/撤单/查询客户端（需外部鉴权）
class PolymarketOrderClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        auth: Optional[AuthProvider] = None,
        endpoints: Optional[CLOBEndpoints] = None,
        timeout: float = 20.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.clob_base_url).rstrip("/")
        self.auth = auth
        self.endpoints = endpoints or CLOBEndpoints()
        self._client = httpx.AsyncClient(timeout=timeout)
        self._rate_limiter = RateLimiter(settings.rate_limit_per_sec)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    def _build_path(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        clean_path = "/" + path.lstrip("/")
        if not params:
            return clean_path
        return f"{clean_path}?{urlencode(params, doseq=True)}"

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = ""
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.auth is not None:
            headers.update(self.auth.build_headers(method, self._build_path(path, params), body))
        await self._rate_limiter.acquire()
        async with self._semaphore:
            resp = await self._client.request(method, url, params=params, content=body or None, headers=headers)
            resp.raise_for_status()
            if not resp.text:
                return None
            return resp.json()

    async def submit_order(self, signed: SignedOrder) -> Any:
        return await self._request("POST", self.endpoints.submit_order, payload=signed.to_payload())

    async def cancel_order(self, order_id: str) -> Any:
        payload = {"order_id": order_id}
        return await self._request("POST", self.endpoints.cancel_order, payload=payload)

    async def get_open_orders(self, params: Optional[Dict[str, Any]] = None) -> Any:
        return await self._request("GET", self.endpoints.open_orders, params=params)

    async def get_order_status(self, order_id: str) -> Any:
        path = f"{self.endpoints.order_status.rstrip('/')}/{order_id}"
        return await self._request("GET", path)

    async def aclose(self) -> None:
        await self._client.aclose()


# 自动下单配置（按 token 维度）
@dataclass(frozen=True)
class AutoOrderConfig:
    token_id: str
    side: str
    size: float
    limit_price: Optional[float] = None
    price_offset: float = 0.0
    max_spread: Optional[float] = None
    order_type: str = "limit"
    time_in_force: str = "gtc"
    post_only: bool = True
    reduce_only: bool = False
    expires_in: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# 单 token 自动下单引擎（默认 dry_run）
class AutoOrderEngine:
    def __init__(
        self,
        orderbook: OrderbookClient,
        trader: PolymarketOrderClient,
        signer: Optional[OrderSigner],
        config: AutoOrderConfig,
        dry_run: bool = True,
    ) -> None:
        self.orderbook = orderbook
        self.trader = trader
        self.signer = signer
        self.config = config
        self.dry_run = dry_run

    def _build_order(self, snapshot: PriceSnapshot) -> Optional[OrderRequest]:
        side = self.config.side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"Unsupported side: {self.config.side}")
        if self.config.max_spread is not None and snapshot.spread is not None:
            if snapshot.spread > self.config.max_spread:
                return None
        if side == "buy":
            best = snapshot.best_ask
            if best is None:
                return None
            price = best + self.config.price_offset
            if self.config.limit_price is not None and price > self.config.limit_price:
                return None
        else:
            best = snapshot.best_bid
            if best is None:
                return None
            price = best - self.config.price_offset
            if self.config.limit_price is not None and price < self.config.limit_price:
                return None
        expires_at = None
        if self.config.expires_in is not None:
            expires_at = int(time.time()) + self.config.expires_in
        return OrderRequest(
            token_id=self.config.token_id,
            side=side,
            price=price,
            size=self.config.size,
            order_type=self.config.order_type,
            time_in_force=self.config.time_in_force,
            post_only=self.config.post_only,
            reduce_only=self.config.reduce_only,
            expires_at=expires_at,
            extra=self.config.extra,
        )

    async def run_once(self) -> Optional[Any]:
        snapshot = await self.orderbook.get_best_snapshot(self.config.token_id)
        order = self._build_order(snapshot)
        if order is None:
            return None
        if self.dry_run:
            return {"dry_run": True, "order": order.to_payload(), "snapshot": snapshot}
        if self.signer is None:
            raise RuntimeError("signer is required when dry_run=False")
        signed = self.signer.sign(order)
        return await self.trader.submit_order(signed)

    async def run_loop(self, interval_seconds: float, max_orders: Optional[int] = None) -> Dict[str, Any]:
        results = []
        while True:
            result = await self.run_once()
            if result is not None:
                results.append(result)
                if max_orders is not None and len(results) >= max_orders:
                    break
            await asyncio.sleep(interval_seconds)
        return {"count": len(results), "results": results}


# 多 token 并发轮询引擎（从文件读取 token_id）
class MultiTokenAutoOrderEngine:
    def __init__(
        self,
        orderbook: OrderbookClient,
        trader: PolymarketOrderClient,
        signer: Optional[OrderSigner],
        config_template: AutoOrderConfig,
        token_file: str,
        dry_run: bool = True,
        max_concurrency: Optional[int] = None,
    ) -> None:
        self.orderbook = orderbook
        self.trader = trader
        self.signer = signer
        self.config_template = config_template
        self.token_ids = load_token_ids(token_file)
        settings = get_settings()
        self._semaphore = asyncio.Semaphore(max_concurrency or settings.max_concurrency)
        self.dry_run = dry_run

    def _config_for_token(self, token_id: str) -> AutoOrderConfig:
        # 按模板生成每个 token 的配置
        return replace(self.config_template, token_id=token_id)

    async def _run_once_token(self, token_id: str) -> Optional[Any]:
        config = self._config_for_token(token_id)
        engine = AutoOrderEngine(
            orderbook=self.orderbook,
            trader=self.trader,
            signer=self.signer,
            config=config,
            dry_run=self.dry_run,
        )
        return await engine.run_once()

    async def run_once_all(self) -> Dict[str, Any]:
        async def _guarded(token_id: str) -> Dict[str, Any]:
            async with self._semaphore:
                result = await self._run_once_token(token_id)
                return {"token_id": token_id, "result": result}

        tasks = [_guarded(token_id) for token_id in self.token_ids]
        results = await asyncio.gather(*tasks)
        return {"count": len(results), "results": results}

    async def run_loop(
        self,
        interval_seconds: float,
        max_rounds: Optional[int] = None,
        max_orders: Optional[int] = None,
    ) -> Dict[str, Any]:
        collected = []
        rounds = 0
        while True:
            batch = await self.run_once_all()
            for entry in batch["results"]:
                if entry.get("result") is not None:
                    collected.append(entry)
                    if max_orders is not None and len(collected) >= max_orders:
                        return {"count": len(collected), "results": collected}
            rounds += 1
            if max_rounds is not None and rounds >= max_rounds:
                break
            await asyncio.sleep(interval_seconds)
        return {"count": len(collected), "results": collected}
