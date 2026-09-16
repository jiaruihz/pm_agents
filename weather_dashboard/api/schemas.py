"""Pydantic response schemas for the weather dashboard API."""

from typing import Any, Optional
from pydantic import BaseModel


class RunSummary(BaseModel):
    run_id: str
    producer_system: Optional[str] = None
    producer_run_id: Optional[str] = None
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
    # Cached metrics snapshot (precomputed by metrics-refresh / get_run)
    metrics: Optional[dict[str, Any]] = None


class RunDetail(RunSummary):
    pass  # metrics inherited from RunSummary


class SignalRow(BaseModel):
    signal_id: str
    snapshot_ts_utc: Optional[str]
    snapshot_file: Optional[str]
    target_date: Optional[str]
    city: Optional[str]
    bracket: Optional[str]
    signal_side: Optional[str]
    model_version: Optional[str]
    model_p_yes: Optional[float]
    market_price: Optional[float]
    edge: Optional[float]
    abs_edge: Optional[str]
    created_at_utc: str


class OrderRow(BaseModel):
    execution_id: str
    order_id: Optional[str]
    run_id: str
    plan_id: Optional[str]
    venue: Optional[str]
    order_side: Optional[str]
    entry_price: Optional[float]
    shares: Optional[float]
    cost_usd: Optional[float]
    placed_at_utc: Optional[str]
    created_at_utc: str


class FillRow(BaseModel):
    fill_id: str
    execution_id: str
    order_id: Optional[str]
    filled_shares: Optional[float]
    filled_price: Optional[float]
    fees_usd: Optional[float]
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
    model_p_yes: Optional[float]
    market_price: Optional[float]
    edge: Optional[float]
    order_id: Optional[str]
    execution_id: Optional[str] = None
    order_side: Optional[str]
    entry_price: Optional[float]
    shares: Optional[float]
    cost_usd: Optional[float]
    fill_status: Optional[str]
    filled_at_utc: Optional[str]
    final_price: Optional[float]
    settlement_status: Optional[str]
    pnl_usd: Optional[float]
    # P0 enrichment
    city_pool: Optional[str]
    forecast_source: Optional[str]
    icao: Optional[str]
    hours_to_settle: Optional[float]


class TradeDrilldown(BaseModel):
    run_id: str
    signal_id: str
    signal: Optional[dict[str, Any]]
    plans: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    settlement: Optional[dict[str, Any]]
    artifacts: list[dict[str, Any]]


class MetricsResponse(BaseModel):
    run_id: str
    metrics: dict[str, Any]


class CompareResponse(BaseModel):
    runs: list[dict[str, Any]]  # list of {run_id, run_summary, metrics}


class ConfigRow(BaseModel):
    config_id: str
    strategy_key: Optional[str] = None
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
    condition_id: Optional[str] = None
    market_id: Optional[str] = None
    bracket: str
    token_id: Optional[str] = None
    final_price: Optional[float]
    settlement_status: Optional[str]
    created_at_utc: str
