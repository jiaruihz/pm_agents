"""Repeat-weekend box-office research and zero-notional shadow runtime."""

from .industry import BoxOfficeProClient, IndustryForecast, parse_forecast_post
from .market import (
    basket_opportunities,
    depth_weighted_buy,
    minimum_binary_cover,
    parse_repeat_event,
    PolymarketBoxOfficeClient,
    taker_fee_usdc,
)

__all__ = [
    "BoxOfficeProClient",
    "IndustryForecast",
    "basket_opportunities",
    "depth_weighted_buy",
    "minimum_binary_cover",
    "parse_forecast_post",
    "parse_repeat_event",
    "PolymarketBoxOfficeClient",
    "taker_fee_usdc",
]
