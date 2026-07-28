from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "analysis"
    / "market_structure_edge"
    / "research_full_ladder_first_seen_residual_v1.py"
)
SPEC = importlib.util.spec_from_file_location("full_ladder_first_seen_residual_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _rows() -> pd.DataFrame:
    base = {
        "state_checkpoint_id": "cp1",
        "trigger_event_id": "event1",
        "event_date": "2026-07-28",
        "city": "Helsinki",
        "candidate_status": "scored",
        "candidate_blocker": None,
        "model_probability_before": 0.6,
        "market_probability_before": 0.55,
        "decision_entry_price": 0.58,
        "final_yes": None,
    }
    return pd.DataFrame(
        [
            {
                **base,
                "candidate_id": "a-y",
                "condition_id": "a",
                "bracket": "24",
                "side": "BUY_YES",
                "model_probability_after": 0.7,
                "market_probability": 0.6,
            },
            {
                **base,
                "candidate_id": "a-n",
                "condition_id": "a",
                "bracket": "24",
                "side": "BUY_NO",
                "model_probability_before": 0.4,
                "model_probability_after": 0.3,
                "market_probability_before": 0.45,
                "market_probability": 0.4,
            },
            {
                **base,
                "candidate_id": "b-y",
                "condition_id": "b",
                "bracket": "25",
                "side": "BUY_YES",
                "model_probability_before": 0.4,
                "model_probability_after": 0.3,
                "market_probability_before": 0.45,
                "market_probability": 0.4,
            },
            {
                **base,
                "candidate_id": "b-n",
                "condition_id": "b",
                "bracket": "25",
                "side": "BUY_NO",
                "model_probability_after": 0.7,
                "market_probability": 0.6,
            },
        ]
    )


def test_checkpoint_audit_requires_complete_two_sided_ladder() -> None:
    audit = MODULE.checkpoint_audit(_rows())
    assert len(audit) == 1
    row = audit.iloc[0]
    assert bool(row["all_conditions_have_yes_no"])
    assert bool(row["full_model_distribution"])
    assert bool(row["model_simplex_within_2c"])
    assert bool(row["full_two_sided_executable_ladder"])
    assert bool(row["model_changed_after_event"])


def test_missing_one_ask_blocks_full_ladder_not_checkpoint_visibility() -> None:
    rows = _rows()
    rows.loc[rows["candidate_id"].eq("b-n"), "decision_entry_price"] = None
    audit = MODULE.checkpoint_audit(rows)
    assert bool(audit.iloc[0]["any_scored_expression"])
    assert not bool(audit.iloc[0]["full_two_sided_executable_ladder"])


def test_weather_fee_matches_contract() -> None:
    assert MODULE.weather_fee(0.5) == 0.0125
    assert MODULE.weather_fee(0.99) == 0.0005
