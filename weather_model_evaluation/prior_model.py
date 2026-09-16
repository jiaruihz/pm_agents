"""Stable, serializable empirical-prior container for weather models."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class EmpiricalPriorModel:
    """Probability fallback keyed by a model-specific discrete context."""

    global_probability: np.ndarray
    keyed_probability: dict[tuple[int, ...], np.ndarray]
