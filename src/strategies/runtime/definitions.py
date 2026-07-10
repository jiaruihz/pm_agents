"""Git-authored catalog for weather strategy definitions.

A definition describes the trading idea. Deployment details belong to an
instance and immutable execution parameters belong to a config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFINITIONS_PATH = ROOT / "src/strategies/runtime/definitions.yaml"


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_key: str
    family: str
    strategy_name: str
    description: str
    strategy_group: str = "weather"
    domain: str = "weather"
    is_active: bool = True
    meta: dict[str, object] = field(default_factory=dict)


def load_strategy_definitions(path: Path | None = None) -> list[StrategyDefinition]:
    raw = yaml.safe_load((path or DEFINITIONS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"strategy definitions must be a list, got {type(raw)}")
    return [StrategyDefinition(**dict(item)) for item in raw]
