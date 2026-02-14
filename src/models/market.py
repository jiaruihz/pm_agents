"""Canonical market/event models shared across domains."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Market(BaseModel):
    market_id: Optional[str] = None
    slug: Optional[str] = None
    question: Optional[str] = None
    description: Optional[str] = None
    rules: Optional[str] = None
    category: Optional[str] = None
    active: bool = True
    resolved: bool = False
    end_at_utc: Optional[str] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    outcomes: List[Any] = Field(default_factory=list)
    outcome_prices: List[Any] = Field(default_factory=list)
    clob_token_ids: List[str] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)
    event_slugs: List[str] = Field(default_factory=list)
    event_titles: List[str] = Field(default_factory=list)
    event_tickers: List[str] = Field(default_factory=list)
    updated_at_utc: Optional[str] = None
    last_synced_at_utc: str

    def to_storage_row(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "slug": self.slug,
            "question": self.question,
            "description": self.description,
            "rules": self.rules,
            "category": self.category,
            "active": int(self.active),
            "resolved": int(self.resolved),
            "end_at_utc": self.end_at_utc,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "outcomes_json": json.dumps(self.outcomes, ensure_ascii=False),
            "outcome_prices_json": json.dumps(self.outcome_prices, ensure_ascii=False),
            "clob_token_ids_json": json.dumps(self.clob_token_ids, ensure_ascii=False),
            "event_ids_json": json.dumps(self.event_ids, ensure_ascii=False),
            "event_slugs_json": json.dumps(self.event_slugs, ensure_ascii=False),
            "event_titles_json": json.dumps(self.event_titles, ensure_ascii=False),
            "event_tickers_json": json.dumps(self.event_tickers, ensure_ascii=False),
            "updated_at_utc": self.updated_at_utc,
            "last_synced_at_utc": self.last_synced_at_utc,
        }


class Event(BaseModel):
    event_id: Optional[str] = None
    slug: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    ticker: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    closed: bool = False
    start_at_utc: Optional[str] = None
    end_at_utc: Optional[str] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    updated_at_utc: Optional[str] = None
    last_synced_at_utc: str

    def to_storage_row(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "ticker": self.ticker,
            "tags_json": json.dumps(self.tags, ensure_ascii=False),
            "active": int(self.active),
            "closed": int(self.closed),
            "start_at_utc": self.start_at_utc,
            "end_at_utc": self.end_at_utc,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "updated_at_utc": self.updated_at_utc,
            "last_synced_at_utc": self.last_synced_at_utc,
        }
