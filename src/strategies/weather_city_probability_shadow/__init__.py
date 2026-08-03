"""Weather City Intraday Runtime (WCIR) model plugins and harness."""

from .core import CityScore, ShadowRuntime
from .coverage import ObservationCoverageAdapter
from .amsterdam import AmsterdamKnmiRemainingHeatV7Adapter

__all__ = [
    "AmsterdamKnmiRemainingHeatV7Adapter",
    "CityScore",
    "ObservationCoverageAdapter",
    "ShadowRuntime",
]
