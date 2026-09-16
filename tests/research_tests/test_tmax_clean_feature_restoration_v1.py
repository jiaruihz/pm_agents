from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/analysis/reheat_risk/research_tmax_clean_feature_restoration_v1.py"
SPEC = importlib.util.spec_from_file_location("restoration", PATH)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_same_snapshot_quote_geometry_rejects_cross_snapshot():
    state = {"decision_snapshot_ts_utc": "2026-07-01T12:00:00Z", "current_bracket": "30", "d1_bracket": "31", "d2_bracket": "32"}
    records = [{"bracket": "30", "yes_best_bid": .4, "yes_best_ask": .5, "yes_bid_size": 8, "yes_ask_size": 9}]
    out = mod.quote_geometry_for_state(state, records, "2026-07-01T12:00:00Z")
    assert out["geom_current_yes_spread"] == pytest.approx(.1)
    with pytest.raises(AssertionError):
        mod.quote_geometry_for_state(state, records, "2026-07-01T12:01:00Z")


def test_strict_asof_never_uses_future_and_keeps_denominator():
    states = pd.DataFrame([
        {"state_row_id": 0, "city": "X", "target_date": "2026-07-01", "decision_hour_local": 12, "decision_snapshot_ts_utc": pd.Timestamp("2026-07-01T12:10:00Z"), "unit": "F"},
        {"state_row_id": 1, "city": "X", "target_date": "2026-07-01", "decision_hour_local": 12, "decision_snapshot_ts_utc": pd.Timestamp("2026-07-01T12:20:00Z"), "unit": "F"},
    ])
    features = pd.DataFrame([
        {"city": "X", "target_date": "2026-07-01", "decision_hour_local": 12, "feature_snapshot_ts_utc": pd.Timestamp("2026-07-01T12:15:00Z"), "relative_humidity_pct": 50, "feature_factory_path": "a"},
    ])
    out, meta = mod.strict_asof_enrich(states, features)
    assert len(out) == 2
    assert out.loc[0, "factory_match_type"] == "missing"
    assert out.loc[1, "factory_match_type"] == "asof_prior"
    assert out.loc[1, "factory_feature_snapshot_ts_utc"] <= out.loc[1, "decision_snapshot_ts_utc"]
    assert meta["missing"] == 1
    assert out.loc[1, "atlas_asof_within_60m"] == 1


def test_research_tags_and_fixed_training_contract():
    assert mod.VARIANTS["atlas_meteo_regime_upper_bound"]["provenance"] == "report_time_reconstruction_only"
    assert mod.VARIANTS["gfs_ecmwf_backfill_upper_bound"]["provenance"] == "research_backfill"
    assert mod.MIN_TRAIN_DATES == 5
    assert mod.MODEL_C == .1


def test_direct_ask_capacity_and_same_arm_first_lock():
    frame = pd.DataFrame([
        {"state_row_id": 0, "city": "X", "target_date": "2026-07-01", "unit": "F", "decision_snapshot_ts_utc": pd.Timestamp("2026-07-01T12:00:00Z"), "actual_bucket": "d1", "current_no_ask": .5, "current_no_ask_size": 1, "d1_no_ask": .5, "d1_no_ask_size": 1, "d2_no_ask": .5, "d2_no_ask_size": 1, "d1_yes_direct_ask": .5, "d1_yes_direct_ask_size": np.nan, "d2_yes_direct_ask": .5, "d2_yes_direct_ask_size": 1},
        {"state_row_id": 1, "city": "X", "target_date": "2026-07-01", "unit": "F", "decision_snapshot_ts_utc": pd.Timestamp("2026-07-01T12:01:00Z"), "actual_bucket": "d1", "current_no_ask": .5, "current_no_ask_size": 1, "d1_no_ask": .5, "d1_no_ask_size": 1, "d2_no_ask": .5, "d2_no_ask_size": 1, "d1_yes_direct_ask": .5, "d1_yes_direct_ask_size": 5, "d2_yes_direct_ask": .5, "d2_yes_direct_ask_size": 1},
    ])
    pred = pd.DataFrame([{"state_row_id": i, "p_below": .01, "p_current": .01, "p_d1": .9, "p_d2": .04, "p_tail": .04} for i in range(2)])
    out = mod.execution_policy(frame, pred, "v", "raw")
    assert len(out) == 1
    assert out.iloc[0].state_row_id == 1
    assert out.iloc[0].expression == "d1_yes"
    assert out.iloc[0].ask_size >= 5
