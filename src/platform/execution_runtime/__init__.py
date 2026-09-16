"""Shared execution-boundary contracts.

Importing this package has no venue client, credentials, or side effects.
"""

from src.platform.execution_runtime.intents import TradeIntent
from src.platform.execution_runtime.paper import (
    PaperFill,
    PaperOrder,
    PaperPlan,
    execution_bundle,
)

__all__ = ["PaperFill", "PaperOrder", "PaperPlan", "TradeIntent", "execution_bundle"]
