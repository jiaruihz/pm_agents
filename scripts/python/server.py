from typing import Optional
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from agents.polymarket.polymarket import Polymarket

app = FastAPI(title="Polymarket Tool Service", version="0.1.0")


@lru_cache(maxsize=1)
def get_polymarket_client() -> Polymarket:
    return Polymarket()


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("TOOL_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


class LimitOrderRequest(BaseModel):
    token_id: str = Field(..., description="CLOB token id")
    price: float = Field(..., gt=0, lt=1)
    size: float = Field(..., gt=0)
    side: str = Field(..., description="BUY or SELL")


class MarketOrderRequest(BaseModel):
    token_id: str = Field(..., description="CLOB token id")
    amount: float = Field(..., gt=0, description="USDC amount")


class SplitRequest(BaseModel):
    condition_id: str = Field(..., description="CTF condition id (bytes32 hex)")
    partition: list[int] = Field(..., description="Outcome index sets (e.g. [1,2])")
    amount: int = Field(..., gt=0, description="Collateral amount in smallest units")
    collateral_token: Optional[str] = Field(
        default=None, description="Collateral token address (default USDC)"
    )
    parent_collection_id: Optional[str] = Field(
        default=None, description="Parent collection id (bytes32 hex, default 0x0)"
    )


class MergeRequest(BaseModel):
    condition_id: str = Field(..., description="CTF condition id (bytes32 hex)")
    partition: list[int] = Field(..., description="Outcome index sets (e.g. [1,2])")
    amount: int = Field(..., gt=0, description="Collateral amount in smallest units")
    collateral_token: Optional[str] = Field(
        default=None, description="Collateral token address (default USDC)"
    )
    parent_collection_id: Optional[str] = Field(
        default=None, description="Parent collection id (bytes32 hex, default 0x0)"
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/events")
def get_events(
    tradeable: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> list:
    events = (
        polymarket.get_all_tradeable_events()
        if tradeable
        else polymarket.get_all_events()
    )
    if offset:
        events = events[offset:]
    if limit is not None:
        events = events[:limit]
    return jsonable_encoder(events)


@app.get("/markets")
def get_markets(
    tradeable: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> list:
    markets = polymarket.get_all_markets()
    if tradeable:
        markets = polymarket.filter_markets_for_trading(markets)
    if offset:
        markets = markets[offset:]
    if limit is not None:
        markets = markets[:limit]
    return jsonable_encoder(markets)


@app.get("/market/{token_id}")
def get_market(
    token_id: str,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    market = polymarket.get_market(token_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    return jsonable_encoder(market)


@app.get("/orderbook/{token_id}")
def get_orderbook(
    token_id: str,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    try:
        orderbook = polymarket.get_orderbook(token_id)
        return jsonable_encoder(orderbook)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/balance")
def get_balance(
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    return {
        "address": polymarket.get_address_for_private_key(),
        "usdc_balance": polymarket.get_usdc_balance(),
    }


@app.post("/order")
def place_limit_order(
    payload: LimitOrderRequest,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    side = payload.side.upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    try:
        result = polymarket.execute_order(
            price=payload.price,
            size=payload.size,
            side=side,
            token_id=payload.token_id,
        )
        return jsonable_encoder(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/market-order")
def place_market_order(
    payload: MarketOrderRequest,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    try:
        result = polymarket.execute_market_order_by_token(
            token_id=payload.token_id, amount=payload.amount
        )
        return jsonable_encoder(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/ctf/split")
def split_positions(
    payload: SplitRequest,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    try:
        result = polymarket.split_position(
            condition_id=payload.condition_id,
            partition=payload.partition,
            amount=payload.amount,
            collateral_token=payload.collateral_token,
            parent_collection_id=payload.parent_collection_id,
        )
        return jsonable_encoder(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/ctf/merge")
def merge_positions(
    payload: MergeRequest,
    _: None = Depends(require_api_key),
    polymarket: Polymarket = Depends(get_polymarket_client),
) -> dict:
    try:
        result = polymarket.merge_positions(
            condition_id=payload.condition_id,
            partition=payload.partition,
            amount=payload.amount,
            collateral_token=payload.collateral_token,
            parent_collection_id=payload.parent_collection_id,
        )
        return jsonable_encoder(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
