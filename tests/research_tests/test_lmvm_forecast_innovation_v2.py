from __future__ import annotations

from scripts.analysis.market_structure_edge import research_lmvm_forecast_innovation_v2 as module


def rung(bracket: str, model: float, market: float) -> dict:
    return {
        "bracket": bracket,
        "question": bracket,
        "condition_id": f"condition-{bracket}",
        "model_prob": model,
        "market_prob": market,
        "yes_bid": market - 0.01,
        "yes_ask": market + 0.01,
        "yes_bid_size": 10.0,
        "yes_ask_size": 10.0,
    }


def state(epoch: float, key: str, rungs: list[dict]) -> dict:
    return {
        "source_path": f"snapshot-{epoch}.json",
        "snapshot_ts_utc": f"2026-08-0{int(epoch)}T00:00:00Z",
        "snapshot_epoch": epoch,
        "snapshot_id": str(epoch),
        "decision_local": f"2026-08-0{int(epoch)}T08:00:00+08:00",
        "decision_hour_local": 8.0,
        "lead_days": 1,
        "forecast_state_key": key,
        "forecast_state_basis": "forecast_values_hash",
        "city": "Shanghai",
        "target_date": "2026-08-06",
        "event_slug": "event",
        "market_timezone": "Asia/Shanghai",
        "forecast_source": "source",
        "forecast_model": "model",
        "model_version": "model-v1",
        "model_init_utc_estimated": "2026-08-01T00:00:00Z",
        "forecast_max_f": 90.0,
        "rung_count": len(rungs),
        "model_probability_sum_raw": 1.0,
        "market_mid_sum_raw": 1.0,
        "rungs": rungs,
    }


def test_innovation_selector_uses_model_change_net_of_market_change() -> None:
    previous = state(1, "old", [rung("30", 0.4, 0.4), rung("31", 0.3, 0.3), rung("32", 0.3, 0.3)])
    current = state(2, "new", [rung("30", 0.5, 0.48), rung("31", 0.3, 0.31), rung("32", 0.2, 0.21)])
    paired = module.paired_rungs(previous, current)
    selected = max(paired, key=lambda row: row["forecast_innovation_score"])

    assert selected["bracket"] == "30"
    assert round(selected["model_probability_delta"], 6) == 0.1
    assert round(selected["market_probability_delta"], 6) == 0.08
    assert round(selected["forecast_innovation_score"], 6) == 0.02


def test_update_pair_uses_immediately_previous_snapshot_market() -> None:
    states = [
        state(1, "old", [rung("30", 0.5, 0.40), rung("31", 0.3, 0.30), rung("32", 0.2, 0.30)]),
        state(2, "old", [rung("30", 0.5, 0.45), rung("31", 0.3, 0.28), rung("32", 0.2, 0.27)]),
        state(3, "new", [rung("30", 0.6, 0.50), rung("31", 0.25, 0.26), rung("32", 0.15, 0.24)]),
    ]
    pairs, counts = module.forecast_update_pairs(states)

    assert counts["forecast_update_events"] == 1
    assert pairs[0][0]["snapshot_epoch"] == 2
    paired = module.paired_rungs(*pairs[0])
    bracket_30 = next(row for row in paired if row["bracket"] == "30")
    assert round(bracket_30["market_probability_delta"], 6) == 0.05


def test_full_ladder_panel_keeps_selected_and_unselected_rows_and_clock_gaps() -> None:
    previous = state(1, "old", [rung("30", 0.4, 0.4), rung("31", 0.3, 0.3), rung("32", 0.3, 0.3)])
    current = state(2, "new", [rung("30", 0.5, 0.48), rung("31", 0.3, 0.31), rung("32", 0.2, 0.21)])

    panel = module.build_full_ladder_panel([(previous, current)])

    assert len(panel) == 3
    assert panel["forecast_event_id"].nunique() == 1
    assert panel["selected_by_innovation"].sum() == 1
    assert panel.loc[panel["selected_by_innovation"], "bracket"].item() == "30"
    assert panel["provider_first_seen_at_utc"].isna().all()
    assert set(panel["provider_first_seen_status"]) == {"unavailable_in_reconstructed_archive"}
    assert set(panel["collector_first_seen_status"]) == {"legacy_earliest_observed_not_collector_exact"}
