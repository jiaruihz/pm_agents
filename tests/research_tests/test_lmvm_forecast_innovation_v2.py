from __future__ import annotations

import gzip
import json
from pathlib import Path

from scripts.analysis.market_structure_edge import research_lmvm_forecast_innovation_v2 as module


def test_cli_defaults_to_tminus1_cutoff(monkeypatch) -> None:
    monkeypatch.setattr(module, "DEFAULT_END_TARGET_DATE", "2026-08-09")
    monkeypatch.setattr(module.sys, "argv", ["research_lmvm_forecast_innovation_v2.py"])

    args = module.parse_args()

    assert args.end_target_date == "2026-08-09"


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


def test_migrated_snapshot_uses_same_capture_orderbook_to_restore_ladder(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot_20260708_1200.json"
    book = tmp_path / "orderbook_snapshot_20260708_1200.jsonl.gz"
    snapshot.write_text(
        json.dumps(
            {
                "ts_utc": "2026-07-08T04:00:00Z",
                "records": [
                    {
                        "city": "London",
                        "target_date": "2026-07-09",
                        "event_slug": "london-july-9",
                        "timezone_name": "Europe/London",
                        "bracket": str(20 + index),
                        "condition_id": f"condition-{index}",
                        "model_prob": probability,
                        "forecast_values_hash": "forecast-a",
                        "forecast_source": "source",
                        "forecast_model": "model",
                    }
                    for index, probability in enumerate((0.2, 0.3, 0.5))
                ],
            }
        ),
        encoding="utf-8",
    )
    with gzip.open(book, "wt", encoding="utf-8") as handle:
        for index, midpoint in enumerate((0.2, 0.3, 0.5)):
            handle.write(
                json.dumps(
                    {
                        "status": "ok",
                        "outcome": "yes",
                        "condition_id": f"condition-{index}",
                        "fetched_at_utc": "2026-07-08T04:00:03Z",
                        "summary": {
                            "best_bid": midpoint - 0.01,
                            "best_ask": midpoint + 0.01,
                            "bid_size": 10.0,
                            "ask_size": 11.0,
                        },
                    }
                )
                + "\n"
            )

    rows, counts = module.base.parse_snapshot_with_orderbook((str(snapshot), str(book)))

    assert counts["historical_companion_orderbook_files"] == 1
    assert counts["complete_d2_d1_ladders"] == 1
    assert rows[0]["target_date"] == "2026-07-09"
    assert rows[0]["clock_lineage_status"] == "historical_companion_orderbook_same_capture_v1"
    assert rows[0]["source_snapshot_ts_utc"] == "2026-07-08T04:00:00Z"
    assert rows[0]["snapshot_ts_utc"] == "2026-07-08T04:00:03Z"
    assert rows[0]["rungs"][2]["yes_ask"] == 0.51
