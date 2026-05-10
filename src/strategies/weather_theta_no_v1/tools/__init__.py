"""Support modules for the weather_theta_no_v1 strategy."""

from typing import Any

__all__ = ["build_market_snapshot"]


def build_market_snapshot(*args: Any, **kwargs: Any) -> Any:
    from src.strategies.weather_theta_no_v1.tools.market_query_tool import build_market_snapshot as _impl

    return _impl(*args, **kwargs)
