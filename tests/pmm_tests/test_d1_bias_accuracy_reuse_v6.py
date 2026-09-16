from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_bias_accuracy_reuse_v6.py"
)
SPEC = importlib.util.spec_from_file_location("d1_bias_v6", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_short_residual_variants_only_change_bias_shift() -> None:
    model = MODULE.MODELS[0]
    errors = pd.DataFrame(
        {
            "target_date": ["2026-06-01"] * 20,
            "city": ["Amsterdam"] * 20,
            "model_key": [model] * 20,
            "error_f": np.arange(20, dtype=float),
        }
    )
    residuals, stats = MODULE.load_short_residuals(
        errors, eligible_cities={"Amsterdam"}
    )
    variants = residuals[("Amsterdam", model)]
    assert np.isclose(variants["short_debiased"].mean(), 0.0)
    assert np.isclose(
        variants["short_full"].std(),
        variants["short_debiased"].std(),
    )
    assert np.isclose(
        stats.iloc[0]["city_bias_f"],
        errors["error_f"].mean(),
    )


def test_select_policy_keeps_one_row_per_basket() -> None:
    rows = pd.DataFrame(
        {
            "snapshot_key": ["a", "a", "b", "b"],
            "p": [0.8, 0.9, 0.7, 0.6],
            "cost": [0.7, 0.85, 0.65, 0.50],
        }
    )
    selected = MODULE.select_policy(rows, "p", "test")
    assert len(selected) == 2
    assert set(selected["snapshot_key"]) == {"a", "b"}
    assert selected.set_index("snapshot_key").loc["a", "p"] == 0.8


def test_logit_clips_binary_probabilities() -> None:
    values = MODULE.logit(pd.Series([0.0, 0.5, 1.0]))
    assert np.isfinite(values).all()
    assert values[1] == 0.0
