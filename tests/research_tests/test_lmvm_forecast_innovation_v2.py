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
        "snapshot_epoch": epoch,
        "snapshot_id": str(epoch),
        "forecast_state_key": key,
        "city": "Shanghai",
        "target_date": "2026-08-06",
        "event_slug": "event",
        "forecast_source": "source",
        "forecast_model": "model",
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
