from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts/analysis/market_structure_edge/"
    "research_d1_global_region_source_causality_v5.py"
)
SPEC = importlib.util.spec_from_file_location("d1_global_v5", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_bh_qvalues_preserve_monotonic_rank() -> None:
    pvalues = pd.Series([0.001, 0.02, 0.5, 0.04])
    qvalues = MODULE.bh_qvalues(pvalues)
    order = np.argsort(pvalues.to_numpy())
    assert np.all(np.diff(qvalues.iloc[order].to_numpy()) >= 0)
    assert qvalues.iloc[0] == 0.004


def test_all_source_scores_use_fixed_equal_weights() -> None:
    row = {"no_win": 1.0, "market_p_no": 0.8}
    for index, model in enumerate(MODULE.MODEL_KEYS):
        row[f"p_no_{model}"] = 0.7 + 0.02 * index
    scored = MODULE.attach_all_source_scores(pd.DataFrame([row]))
    expected = np.mean(
        [row[f"p_no_{model}"] for model in MODULE.MODEL_KEYS]
    )
    assert scored.iloc[0]["p_no_equal_all5"] == expected
    assert scored.iloc[0]["brier_equal_all5"] == (1.0 - expected) ** 2


def test_fixed_effect_design_uses_asia_reference() -> None:
    rows = pd.DataFrame(
        {
            "region": ["AS", "EU", "ME"],
            "safety_quintile": [0, 1, 2],
            "cost_quintile": [0, 1, 2],
        }
    )
    design, names = MODULE.fixed_effect_design(
        rows, region_reference="AS"
    )
    assert design.shape == (3, len(names))
    assert "region_AS" not in names
    assert "region_EU" in names
    assert "region_ME" in names


def test_training_summary_stays_on_fixed_city_universe() -> None:
    model = MODULE.MODEL_KEYS[0]
    errors = pd.DataFrame(
        {
            "target_date": ["2026-06-01", "2026-06-01"],
            "model_key": [model, model],
            "city": ["Amsterdam", "Tokyo"],
            "error_f": [1.0, 9.0],
            "abs_error_f": [1.0, 9.0],
        }
    )
    summary = MODULE.source_training_summary(
        errors, eligible_cities={"Amsterdam"}
    )
    assert len(summary) == 1
    assert summary.iloc[0]["region"] == "EU"
    assert summary.iloc[0]["cities"] == 1
    assert summary.iloc[0]["mae_f"] == 1.0
