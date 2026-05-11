from __future__ import annotations

import importlib
import inspect
from typing import Dict, Optional

from src.strategies.pmm.core.strategy_base import MarketMakingStrategy
from src.strategies.registry import load_strategy_catalog


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: Dict[str, MarketMakingStrategy] = {}

    def register(self, strategy: MarketMakingStrategy) -> None:
        self._strategies[strategy.key] = strategy

    def get(self, key: str) -> Optional[MarketMakingStrategy]:
        return self._strategies.get(key)

    def available_keys(self) -> list[str]:
        return sorted(self._strategies.keys())


def build_pmm_strategy_registry(
    *,
    anchor_quotes_fn,
    quantize_pair_fn,
    target_sizes_fn,
) -> StrategyRegistry:
    """Build a PMM registry from active strategy manifests.

    PMM owns the engine and shared quote primitives. Strategy implementations live
    in their own strategy packages and expose an adapter through `strategy_module`.
    """
    registry = StrategyRegistry()
    deps = {
        "anchor_quotes_fn": anchor_quotes_fn,
        "quantize_pair_fn": quantize_pair_fn,
        "target_sizes_fn": target_sizes_fn,
    }
    for manifest in load_strategy_catalog():
        if not manifest.is_active or manifest.domain != "pmm" or not manifest.strategy_module:
            continue
        module_name, _, attr_name = manifest.strategy_module.partition(":")
        if not module_name or not attr_name:
            raise ValueError(f"Invalid strategy_module for {manifest.strategy_key}: {manifest.strategy_module}")
        module = importlib.import_module(module_name)
        cls = getattr(module, attr_name)
        signature = inspect.signature(cls.__init__)
        kwargs = {}
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            if name in deps:
                kwargs[name] = deps[name]
            elif param.default is inspect.Parameter.empty:
                raise ValueError(f"Cannot instantiate {manifest.strategy_key}: unsupported constructor arg {name}")
        registry.register(cls(**kwargs))
    return registry
