"""Pydantic response schemas for the weather dashboard API."""

from typing import Any, Optional
from pydantic import BaseModel


class RunSummary(BaseModel):
    run_id: str
    config_id: str
    universe_id: Optional[str]
    code_version: Optional[str]
    execution_mode: str
    date_range_start: Optional[str]
    date_range_end: Optional[str]
    state: str
    repro_key: Optional[str]
    tags: Optional[list[str]]
    notes: Optional[str]
    created_at_utc: str
    started_at_utc: Optional[str]


class RunDetail(RunSummary):
    metrics: Optional[dict[str, Any]]


class SignalRow(BaseModel):
    signal_id: str
    snapshot_ts_utc: Optional[str]
    snapshot_file: Optional[str]
    target_date: Optional[str]
    city: Optional[str]
    bracket: Optional[str]
    side: Optional[str]
    model_version: Optional[str]
    model_p_yes: Optional[str]
    market_price: Optional[str]
    edge: Optional[str]
    abs_edge: Optional[str]
    created_at_utc: str


class OrderRow(BaseModel):
    order_id: str
    run_id: str
    plan_id: Optional[str]
    execution_mode: Optional[str]
    side: Optional[str]
    entry_price: Optional[str]
    shares: Optional[str]
    cost_usd: Optional[str]
    placed_at_utc: Optional[str]
    created_at_utc: str


class FillRow(BaseModel):
    fill_id: str
    order_id: str
    filled_shares: Optional[str]
    filled_price: Optional[str]
    fees_usd: Optional[str]
    status: Optional[str]
    filled_at_utc: Optional[str]
    created_at_utc: str


class TradeRow(BaseModel):
    """Joined signal + order + fill + settlement for the history view."""
    signal_id: str
    target_date: Optional[str]
    city: Optional[str]
    bracket: Optional[str]
    signal_side: Optional[str]
    model_version: Optional[str]
    model_p_yes: Optional[str]
    market_price: Optional[str]
    edge: Optional[str]
    order_id: Optional[str]
    order_side: Optional[str]
    entry_price: Optional[str]
    shares: Optional[str]
    cost_usd: Optional[str]
    fill_status: Optional[str]
    filled_at_utc: Optional[str]
    final_yes: Optional[int]
    settlement_status: Optional[str]
    pnl_usd: Optional[str]


class MetricsResponse(BaseModel):
    run_id: str
    metrics: dict[str, Any]


class CompareResponse(BaseModel):
    runs: list[dict[str, Any]]  # list of {run_id, run_summary, metrics}


class ConfigRow(BaseModel):
    config_id: str
    name: str
    params: Any
    created_at_utc: str


class UniverseRow(BaseModel):
    universe_id: str
    name: str
    description: Optional[str]
    cities: list[str]
    models: list[str]
    created_at_utc: str
    frozen_at_utc: Optional[str]
    deprecated_at_utc: Optional[str]


class SettlementRow(BaseModel):
    settlement_id: str
    target_date: str
    bracket: str
    final_yes: Optional[int]
    status: Optional[str]
    created_at_utc: str
