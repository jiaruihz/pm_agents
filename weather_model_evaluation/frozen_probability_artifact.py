"""Load and score frozen single-model or probability-ensemble artifacts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_artifact(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"frozen artifact SHA-256 mismatch: expected={expected_sha256} actual={actual}")
    artifact = joblib.load(path)
    if not isinstance(artifact, dict):
        raise TypeError("frozen probability artifact must be a dictionary")
    return artifact


def _normalize(probability: np.ndarray) -> np.ndarray:
    value = np.clip(np.asarray(probability, dtype=float), 1e-8, None)
    return value / value.sum(axis=1, keepdims=True)


def _full_class_probability(
    model: Any,
    frame: pd.DataFrame,
    features: list[str],
    *,
    n_classes: int,
) -> np.ndarray:
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise ValueError(f"scoring frame misses frozen features: {missing}")
    raw = model.predict_proba(frame[features])
    output = np.full((len(frame), n_classes), 1e-8, dtype=float)
    classes = np.asarray(model.classes_, dtype=int)
    output[:, classes] = raw
    return _normalize(output)


def score_frozen_artifact(artifact: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """Return a coherent PMF without changing the frozen model or weights."""
    n_classes = len(artifact["classes"])
    members = list(artifact.get("ensemble_members") or [])
    if not members:
        return _full_class_probability(
            artifact["model"],
            frame,
            list(artifact["features"]),
            n_classes=n_classes,
        )
    weights = {
        str(name): float(value)
        for name, value in dict(artifact.get("ensemble_weights") or {}).items()
    }
    if set(members) != set(weights) or not math.isclose(sum(weights.values()), 1.0):
        raise ValueError("frozen ensemble members and weights are inconsistent")
    probability = sum(
        weights[member]
        * _full_class_probability(
            artifact["ensemble_models"][member],
            frame,
            list(artifact["ensemble_features"][member]),
            n_classes=n_classes,
        )
        for member in members
    )
    return _normalize(probability)
