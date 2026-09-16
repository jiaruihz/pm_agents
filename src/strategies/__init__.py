"""Global strategy catalog package."""

from __future__ import annotations

from typing import Any


def load_strategy_catalog(*args: Any, **kwargs: Any) -> Any:
    from src.strategies.registry import load_strategy_catalog as _load_strategy_catalog

    return _load_strategy_catalog(*args, **kwargs)


def load_strategy_rows(*args: Any, **kwargs: Any) -> Any:
    from src.strategies.registry import load_strategy_rows as _load_strategy_rows

    return _load_strategy_rows(*args, **kwargs)


__all__ = ["load_strategy_catalog", "load_strategy_rows"]
