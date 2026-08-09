from __future__ import annotations

from datetime import date, timedelta

import joblib
import pandas as pd

from weather_model_evaluation import lmvm_repricing_challenger as subject


def _fixture_rows(date_count: int = 36) -> pd.DataFrame:
    rows = []
    start = date(2026, 5, 1)
    for day_index in range(date_count):
        target = (start + timedelta(days=day_index)).isoformat()
        for city_index, city in enumerate(("Alpha", "Beta", "Gamma", "Delta")):
            for update in range(3):
                innovation = 0.10 if update == 1 else -0.02
                entry_bid = 0.20 + 0.01 * city_index
                entry_ask = entry_bid + 0.01
                entry_fee = float(subject.weather_fee(entry_ask))
                row = {
                    "policy": "forecast_innovation_argmax",
                    "lead_days": 1,
                    "target_date": target,
                    "snapshot_epoch": day_index * 10_000 + city_index * 100 + update,
                    "city": city,
                    "forecast_source": "fixture",
                    "forecast_model": "fixture_model",
                    "entry_bid": entry_bid,
                    "entry_ask": entry_ask,
                    "entry_bid_size": 100,
                    "entry_ask_size": 100,
                    "entry_fee_per_share": entry_fee,
                    "forecast_innovation_score": innovation,
                    "model_probability_before": 0.20,
                    "model_probability_after": 0.20 + innovation,
                    "model_probability_delta": innovation,
                    "market_probability_before": 0.20,
                    "market_probability_after": 0.20,
                    "market_probability_delta": 0.0,
                    "model_prob": 0.20 + innovation,
                    "market_prob": 0.20,
                    "forecast_max_f": 75.0,
                    "decision_hour_local": 9.0 + update,
                    "rung_count": 7,
                    "model_edge_after_entry_fee": innovation - entry_fee,
                }
                for horizon in subject.HORIZONS:
                    future_bid = entry_bid + (0.06 if update == 1 else -0.01)
                    exit_fee = float(subject.weather_fee(future_bid))
                    net = future_bid - exit_fee - entry_ask - entry_fee
                    row[f"h{horizon}_bid"] = future_bid
                    row[f"h{horizon}_exit_fee_per_share"] = exit_fee
                    row[f"h{horizon}_net_markout_per_share"] = net
                    row[f"h{horizon}_executable_shares"] = 5.0
                    row[f"h{horizon}_net_pnl_usd"] = net * 5.0
                rows.append(row)
    return pd.DataFrame(rows)


def _full_ladder_fixture(date_count: int = 32) -> pd.DataFrame:
    rows = []
    start = date(2026, 5, 1)
    for day_index in range(date_count):
        target = (start + timedelta(days=day_index)).isoformat()
        for city_index, city in enumerate(("Alpha", "Beta")):
            for event_index in range(2):
                innovation = 0.08 if (day_index + city_index + event_index) % 2 == 0 else -0.08
                event_id = f"{target}-{city}-{event_index}"
                for rung_index in range(3):
                    entry_bid = 0.16 + 0.04 * rung_index
                    entry_ask = entry_bid + 0.01
                    gross_move = innovation * (1.0 if rung_index == 1 else -0.35)
                    row = {
                        "forecast_event_id": event_id,
                        "target_date": target,
                        "city": city,
                        "lead_days": 1 + event_index,
                        "condition_id": f"{event_id}-r{rung_index}",
                        "snapshot_epoch": day_index * 10_000 + city_index * 100 + event_index * 10,
                        "entry_bid": entry_bid,
                        "entry_ask": entry_ask,
                        "entry_bid_size": 50.0,
                        "entry_ask_size": 60.0,
                        "market_probability_after": entry_bid,
                        "market_probability_rank": rung_index + 1,
                        "market_mid_sum_raw": 0.99,
                        "model_probability_before": 0.20,
                        "model_probability_after": 0.20 + innovation,
                        "model_probability_delta": innovation,
                        "forecast_innovation_score": abs(innovation),
                        "forecast_source": "fixture",
                        "forecast_model": "fixture_model",
                        "forecast_max_f": 70.0 + innovation * 10,
                        "model_probability_rank": rung_index + 1,
                        "decision_hour_local": 8.0 + event_index,
                        "rung_count": 3,
                        "selected_by_innovation": rung_index == 1,
                    }
                    for horizon in subject.FULL_LADDER_HORIZONS:
                        future_bid = max(0.01, min(0.99, entry_bid + gross_move))
                        row[f"h{horizon}_bid"] = future_bid
                        row[f"h{horizon}_bid_size"] = 40.0
                        row[f"h{horizon}_exit_fee_per_share"] = float(
                            subject.weather_fee(future_bid)
                        )
                        row[f"h{horizon}_executable_shares"] = 40.0
                    rows.append(row)
    return pd.DataFrame(rows)


def test_blocks_when_independent_dates_are_too_few() -> None:
    result = subject.run_tournament(_fixture_rows(6), min_train_dates=3, draws=20)
    assert result["status"] == "blocked_insufficient_target_dates"
    assert result["available_target_dates"] == 6


def test_legacy_after_probability_aliases_are_materialized() -> None:
    rows = _fixture_rows(6).drop(
        columns=["model_probability_after", "market_probability_after"]
    )
    prepared = subject.prepare_candidates(rows)
    assert prepared["model_probability_after"].equals(prepared["model_prob"])
    assert prepared["market_probability_after"].equals(prepared["market_prob"])


def test_expanding_oof_never_trains_on_test_or_future_date(tmp_path) -> None:
    source = tmp_path / "candidates.csv"
    _fixture_rows().to_csv(source, index=False)
    result = subject.run_tournament(
        pd.read_csv(source), min_train_dates=8, holdout_fraction=0.20, draws=50
    )
    assert result["status"] in {"freeze_candidate", "maker_probe_candidate"}
    oof = result["oof_predictions"]
    assert (oof["max_train_date"] < oof["target_date"]).all()
    assert max(result["development_dates"]) < min(result["holdout_dates"])
    assert result["holdout"]["roi"] > 0
    selected = result["selected_holdout"]
    assert selected.groupby(["target_date", "city"]).size().max() == 1
    output = tmp_path / "output"
    subject.write_outputs(result, source, output)
    stem = "frozen_model" if result["status"] == "freeze_candidate" else "maker_probe_model"
    assert (output / f"{stem}.json").exists()
    assert (output / f"{stem}.joblib").exists()
    spec = subject.ModelSpec(**result["model_spec"])
    scored, candidates = subject.score_frozen_rows(
        _fixture_rows(4),
        joblib.load(output / f"{stem}.joblib"),
        spec,
        float(result["selected"]["threshold"]),
    )
    assert "predicted_net_markout" in scored
    assert candidates.groupby(["target_date", "city"]).size().max() == 1


def test_full_ladder_weights_keep_dates_events_and_rungs_equal() -> None:
    frame = subject.prepare_full_ladder_rows(_full_ladder_fixture(3))
    frame["weight"] = subject.date_event_rung_equal_weights(frame)
    assert frame.groupby("target_date")["weight"].sum().round(10).eq(1.0).all()
    event_weight = frame.groupby(["target_date", "forecast_event_id"])["weight"].sum()
    assert event_weight.groupby("target_date").nunique().eq(1).all()


def test_full_ladder_tournament_is_chronological_and_keeps_all_rungs(tmp_path) -> None:
    source = tmp_path / "forecast_event_rungs.csv"
    rows = _full_ladder_fixture()
    rows.to_csv(source, index=False)
    result = subject.run_full_ladder_tournament(
        rows, min_train_dates=8, holdout_fraction=0.20, draws=30
    )
    assert result["status"] in {
        "historical_reconstructed_point_only",
        "historical_reconstructed_pass_forward_still_required",
    }
    assert len(result["frame"]) == len(rows)
    assert result["frame"]["forecast_event_id"].nunique() == 32 * 2 * 2
    assert max(result["development_dates"]) < min(result["holdout_dates"])
    assert (result["oof_predictions"]["max_train_date"] < result["oof_predictions"]["target_date"]).all()
    assert set(result["holdout_metrics"]["model_id"]) == {
        spec.model_id for spec in subject.FULL_LADDER_SPECS
    }
    selected = result["selected_signals"]
    if not selected.empty:
        assert selected.groupby(["target_date", "forecast_event_id"]).size().max() == 1
        assert selected.groupby(["target_date", "city"]).size().max() == 1
    output = tmp_path / "full_ladder_output"
    subject.write_full_ladder_outputs(result, source, output)
    assert (output / "summary.json").exists()
    assert (output / "report.md").exists()
