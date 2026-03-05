from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class StrategyManifest(BaseModel):
    """Manifest schema loaded from src/strategies/<strategy_key>/manifest.yaml."""

    strategy_key: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    strategy_group: str = Field(default="")
    strategy_family: str = Field(default="")
    domain: str = Field(default="")
    is_active: bool = Field(default=True)
    runner_module: str = Field(default="")
    strategy_module: str = Field(default="")
    description: str = Field(default="")
    meta: Dict[str, Any] = Field(default_factory=dict)

    def as_runtime_row(self) -> Dict[str, Any]:
        return {
            "strategy_key": self.strategy_key,
            "strategy_name": self.strategy_name,
            "strategy_group": self.strategy_group,
            "strategy_family": self.strategy_family,
            "domain": self.domain,
            "is_active": self.is_active,
            "runner_module": self.runner_module,
            "description": self.description,
            "meta": self.meta,
        }
