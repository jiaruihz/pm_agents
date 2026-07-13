from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (
    BUCKETS,
    TmaxHazardChainV3,
    ConstrainedMarketRecalibratorV3,
    RECALIBRATION_C_CANDIDATES,
    apply_temporal_coherence,
    condition_posterior_on_anchor_shift,
    fuse_log_linear,
    probability_artifact_row,
    route_feature_columns,
)
from scripts.analysis.reheat_risk.tmax_distribution_v3 import enrich, execution, report, walkforward


def _training_frame() -> pd.DataFrame:
    rows = []
    steps = [0, 1, 2, 3, 4, 0, 1, 2] * 10
    for i, step in enumerate(steps):
        rows.append({"target_date": f"2026-06-{19 + i // 20:02d}", "winner_step": step, "x": float(i % 7)})
    return pd.DataFrame(rows)


def test_hazard_chain_outputs_sequential_hazards_and_sum_one() -> None:
    frame = _training_frame()
    model = TmaxHazardChainV3(["x"], min_head_rows=10).fit(frame)
    pred = model.predict_five_bucket(frame.iloc[:8])
    assert np.allclose(pred[[f"p_{b}" for b in BUCKETS]].sum(axis=1), 1.0)
    assert pred[["h0", "h1", "h2", "h_cont"]].apply(lambda c: c.between(0, 1).all()).all()
    tail = model.tail_rung_split(.3, 5)
    assert sum(tail) == pytest.approx(.3)


def test_missing_market_ladder_falls_back_to_model_not_zero() -> None:
    model = np.array([[.05, .3, .3, .2, .15], [.1, .2, .3, .2, .2]])
    market = np.array([[np.nan] * 5, [.2] * 5])
    fused = fuse_log_linear(model, market, .5)
    assert np.allclose(fused[0], model[0])
    assert np.allclose(fused.sum(axis=1), 1.0)


def test_constrained_market_recalibrators_have_no_city_terms_and_sum_one() -> None:
    rows = []
    for day in range(1, 9):
        for i in range(12):
            actual = BUCKETS[1 + (day + i) % 4]
            row = {
                "city": f"CITY_{i}", "target_date": f"2026-07-{day:02d}",
                "actual_bucket": actual, "winner_step": {"current": 0, "d1": 1, "d2": 2, "tail": 3}[actual],
                **{f"market_p_{bucket}": value for bucket, value in zip(BUCKETS, [.01, .29, .30, .20, .20])},
                "forecast_gap_to_running_f": i % 3, "temp_trend_1h_f": i % 2,
                "temp_trend_3h_f": i % 4, "forecast_peak_delta_hours_local": day % 3,
                "max_age_min": i, "remaining_heat_integral_f": day, "forecast_curve_available": 1,
            }
            rows.append(row)
    frame = pd.DataFrame(rows)
    for include_path in (False, True):
        model = ConstrainedMarketRecalibratorV3(include_path=include_path, c_value=RECALIBRATION_C_CANDIDATES[0]).fit(frame)
        assert all("city" not in column for column in model.feature_columns)
        pred = model.predict_five_bucket(frame.head(10))
        assert np.allclose(pred[[f"p_{bucket}" for bucket in BUCKETS]].sum(axis=1), 1.0)
        assert pred[["h0", "h1", "h2"]].apply(lambda c: c.between(0, 1).all()).all()


def test_temporal_conditioning_uses_anchor_break_evidence() -> None:
    previous = np.array([.01, .39, .30, .20, .10])
    conditioned = condition_posterior_on_anchor_shift(previous, 1, .4)
    assert conditioned[0] == 0
    assert conditioned.sum() == pytest.approx(1.0)
    current = np.array([.01, .10, .20, .30, .39])
    coherent = apply_temporal_coherence(current, previous, 1, .4, .5)
    assert coherent.sum() == pytest.approx(1.0)
    assert not np.allclose(coherent, current)


def test_probability_artifact_has_full_ladder_five_buckets_and_hazards() -> None:
    row = probability_artifact_row(
        city="X", target_date="2026-07-01", decision_ts_utc="2026-07-01T12:00:00Z",
        route="strict_pit_full", lineage="strict_pit", anchor_bracket="30",
        ladder_rungs=["29", "30", "31", "32"], full_ladder_probs=[.1, .4, .3, .2],
        five_bucket={"below": .1, "current": .4, "d1": .3, "d2": .1, "tail": .1},
        fusion_alpha=.5, coherence_weight=.25, anchor_shift_from_prev=0,
        train_through_date="2026-06-30", market_available=True,
        sequential_hazards={"h0": .5, "h1": .4, "h2": .3, "h_cont": .2},
    )
    assert sum(json.loads(row["p_full_ladder_json"])) == pytest.approx(1.0)
    assert sum(row[f"p_{b}"] for b in BUCKETS) == pytest.approx(1.0)
    assert row["hazard_reach_d1"] == .5


def test_full_ladder_preserves_below_mass_at_bottom_anchor() -> None:
    record = _execution_row(0, "2026-07-05T06:00:00Z", 5.0)
    labels, probs = report.full_ladder_probs(record)
    assert labels[0] == "__below_anchor__"
    assert probs[0] == pytest.approx(record["route_p_below"])
    assert sum(probs) == pytest.approx(1.0)


def test_expanding_fit_never_uses_test_or_future_date() -> None:
    columns = route_feature_columns("weather_only")
    rows = []
    for day in range(1, 9):
        for city_i in range(8):
            bucket = BUCKETS[(day + city_i) % 4 + 1]
            row = {column: float((day + city_i) % 5) for column in columns}
            row.update({
                "city": f"C{city_i}", "target_date": f"2026-07-{day:02d}",
                "decision_hour_local": 12, "decision_snapshot_ts_utc": f"2026-07-{day:02d}T12:00:00Z",
                "actual_bucket": bucket, "winner_step": {"current": 0, "d1": 1, "d2": 2, "tail": 3}[bucket],
                "labeled": True, "market_score_ready": True, "unit": "F",
                **{f"market_p_{b}": .2 for b in BUCKETS},
            })
            rows.append(row)
    frame = pd.DataFrame(rows)
    result = walkforward.fit_predict_by_date(frame, frame, "weather_only")
    assert result
    for date, entry in result.items():
        assert entry["chain"].train_through < date


def test_restoration_archive_loader_enforces_pit_and_60m(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "restoration.csv"
    base = {
        "city": "X", "target_date": "2026-07-01", "decision_snapshot_ts_utc": "2026-07-01T12:00:00Z",
        "factory_feature_snapshot_ts_utc": "2026-07-01T11:30:00Z", "factory_lag_min": 30,
        "factory_match_type": "asof_prior", "atlas_asof_within_60m": 1,
        "atlas_relative_humidity_pct": 50, "atlas_dewpoint_depression_f": 10,
        "atlas_wind_speed_kt": 5, "atlas_sky_cover_code": "BKN",
        "backfill_gfs_forecast_peak_delta_hours_local": np.nan,
        "backfill_ecmwf_forecast_peak_delta_hours_local": np.nan,
    }
    pd.DataFrame([base]).to_csv(path, index=False)
    monkeypatch.setattr(enrich, "RESTORATION_STATES", path)
    loaded = enrich.load_restoration_mechanism()
    assert loaded.iloc[0].mechanism_feature_ts_utc <= loaded.iloc[0].decision_snapshot_ts_utc
    assert loaded.iloc[0].sky_cover_num == 3
    future = {**base, "factory_feature_snapshot_ts_utc": "2026-07-01T12:01:00Z", "factory_lag_min": -1}
    pd.DataFrame([future]).to_csv(path, index=False)
    with pytest.raises(AssertionError):
        enrich.load_restoration_mechanism()


def _execution_row(state_id: int, ts: str, direct_size: float | None) -> dict[str, object]:
    book = [
        {"bracket": "30", "yes_ask": .5, "no_ask": .5},
        {"bracket": "31", "yes_ask": .5, "no_ask": .2},
        {"bracket": "32", "yes_ask": .5, "no_ask": .5},
        {"bracket": "33", "yes_ask": .5, "no_ask": .5},
    ]
    return {
        "state_id": state_id, "city": "Lucknow", "target_date": "2026-07-05", "unit": "C",
        "decision_hour_local": 12, "decision_snapshot_ts_utc": ts,
        "current_bracket": "30", "d1_bracket": "31", "d2_bracket": "32", "anchor_rung_index": 0,
        "ladder_book_json": json.dumps(book), "settlement_winning_bracket_label": "31",
        "current_no_ask": np.nan, "current_no_ask_size": np.nan,
        "d1_no_ask": np.nan, "d1_no_ask_size": np.nan, "d2_no_ask": np.nan, "d2_no_ask_size": np.nan,
        "d1_yes_direct_ask": .5, "d1_yes_direct_ask_size": direct_size,
        "d2_yes_direct_ask": np.nan, "d2_yes_direct_ask_size": np.nan,
        "route_p_below": .01, "route_p_current": .04,
        "route_p_d1": .85 if state_id == 0 else .10,
        "route_p_d2": .05, "route_p_tail": .05 if state_id == 0 else .80,
        "h_cont": .4, "forecast_source": "gfs",
    }


def test_direct_ask_capacity_first_lock_and_single_position_target_book() -> None:
    blocked = _execution_row(0, "2026-07-05T06:00:00Z", np.nan)
    eligible = _execution_row(0, "2026-07-05T06:05:00Z", 5.0)
    first = execution.first_lock_replay(pd.DataFrame([blocked, eligible]), "strict")
    assert len(first) == 1 and first.iloc[0].decision_snapshot_ts_utc.endswith("06:05:00Z")
    assert first.iloc[0].shares == 5
    assert first.iloc[0].ask_size >= 5
    open_row = _execution_row(0, "2026-07-05T06:00:00Z", 5.0)
    close_row = _execution_row(1, "2026-07-05T06:10:00Z", np.nan)
    ledger, positions = execution.target_book_replay(pd.DataFrame([open_row, close_row]), "strict")
    assert not ledger.empty and positions.position_id.is_unique
    assert not ((ledger.action == "reopen") & ledger.position_id.duplicated()).any()
    assert set(ledger.action) >= {"open", "close_lock", "payout"}


def test_lucknow_fixed_case_writes_lineage_without_self_cross(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = _execution_row(0, "2026-07-05T06:00:00Z", 5.0)
    row.update({
        "fusion_alpha": .5, "coherence_weight": .25, "train_through_date": "2026-07-04",
        "market_score_ready": True, "actual_bucket": "d1",
    })
    ledger = pd.DataFrame([
        {"city": "Lucknow", "target_date": "2026-07-05", "ts": row["decision_snapshot_ts_utc"],
         "action": "open", "position_id": "Lucknow|2026-07-05|1", "expression": "d1_yes", "side": "YES", "bracket": "31"},
    ])
    first = pd.DataFrame([{"route": "market_path", "city": "Lucknow", "target_date": "2026-07-05"}])
    monkeypatch.setattr(report, "OUT_DIR", tmp_path)
    result = report.lucknow_case(pd.DataFrame([row]), ledger, first, "market_path")
    assert result["decisions"] == 1
    assert result["self_cross_possible"] is False
    assert (tmp_path / "lucknow_20260705_decisions.csv").exists()
    decisions = pd.read_csv(tmp_path / "lucknow_20260705_decisions.csv")
    assert {"old_target_position", "new_target_position", "estimated_entry_cost_5sh", "decision_reason"} <= set(decisions)
    assert decisions.hazard_tail_continue.notna().all()
