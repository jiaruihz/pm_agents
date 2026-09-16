"""Thin dashboard import wrapper for the shared weather_edge_v1 ID contract."""

from src.strategies.weather_edge_v1.ids import (
    STRATEGY_ID_NAMESPACE,
    WeatherIdError,
    make_execution_id,
    make_execution_id_from_row,
    make_fill_id,
    make_paper_order_id,
    make_plan_id,
    make_plan_id_from_row,
    make_signal_id,
    make_signal_id_from_row,
    make_settlement_id,
)

__all__ = [
    "STRATEGY_ID_NAMESPACE",
    "WeatherIdError",
    "make_execution_id",
    "make_execution_id_from_row",
    "make_fill_id",
    "make_paper_order_id",
    "make_plan_id",
    "make_plan_id_from_row",
    "make_signal_id",
    "make_signal_id_from_row",
    "make_settlement_id",
]
