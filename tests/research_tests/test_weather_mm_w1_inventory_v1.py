import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

from scripts.analysis.market_making.research_weather_mm_w1_inventory_v1 import (
    _bootstrap_paired_per_share,
    _replay_episode,
    _walk,
    run,
)


def book(at, *, bids, asks):
    return {
        "available_at_utc": at,
        "snapshot_ts_utc": at,
        "bids": [{"price": price, "size": size} for price, size in bids],
        "asks": [{"price": price, "size": size} for price, size in asks],
        "source_path": "fixture",
    }


def config():
    return {
        "execution": {
            "initial_book_max_lag_minutes": 30,
            "passive_ttl_minutes": 60,
            "fallback_book_max_lag_minutes": 30,
            "merge_operation_cost_usd": "0",
            "incremental_operating_cost_usd": "0",
        },
        "economics": {"annual_financing_rate": "0.10"},
    }


def position():
    return {
        "condition_id": "condition",
        "token_id": "yes",
        "city": "City",
        "target_date": "2026-08-01",
        "bracket": "30",
        "unit": "C",
        "shares": 5,
        "entry_cash_cost_usd": 3,
        "entry_principal_usd": 3,
        "entry_fees_usd": 0,
        "first_fill_at_utc": "2026-08-01T00:00:00Z",
        "last_fill_at_utc": "2026-08-01T00:00:00Z",
        "fill_rows": 1,
        "final_yes": 1,
        "settled": 1,
        "settlement_available_at_utc": "2026-08-01T04:00:00Z",
        "settlement_outcome_id": "settlement",
    }


def test_walk_uses_full_depth_and_v2_fee() -> None:
    result = _walk(
        [{"price": "0.60", "size": "2"}, {"price": "0.59", "size": "3"}],
        Decimal("5"),
        sell=True,
    )
    assert result is not None
    assert result["principal"] == Decimal("2.97")
    assert result["fee"] == Decimal("0.06029")
    assert _walk([{"price": "0.60", "size": "4"}], Decimal("5"), sell=True) is None


def test_five_arm_replay_uses_same_position_and_no_action_equals_hold() -> None:
    books = {
        "yes": [
            book("2026-08-01T00:01:00Z", bids=[("0.59", "5")], asks=[("0.62", "5")]),
            book("2026-08-01T00:30:00Z", bids=[("0.61", "5")], asks=[("0.62", "5")]),
        ],
        "no": [book("2026-08-01T00:02:00Z", bids=[("0.36", "5")], asks=[("0.38", "5")])],
    }
    episode, rows = _replay_episode(
        position(),
        {
            "condition_id": "condition",
            "yes_token_id": "yes",
            "no_token_id": "no",
            "tick_size": "0.01",
            "topology": "same_condition_binary_yes_no",
        },
        books,
        config(),
    )
    assert [row["arm"] for row in rows] == [
        "immediate_taker_exit",
        "passive_then_fallback",
        "complement_then_merge",
        "hold_to_settlement",
        "no_action",
    ]
    assert episode["passive_proxy_filled"] is True
    hold = next(row for row in rows if row["arm"] == "hold_to_settlement")
    no_action = next(row for row in rows if row["arm"] == "no_action")
    assert hold["accounting_pnl_usd"] == no_action["accounting_pnl_usd"]
    assert all(row["episode_id"] == episode["episode_id"] for row in rows)


def test_target_date_bootstrap_keeps_nonfills_in_fixed_rows() -> None:
    rows = []
    for index, date in enumerate(("2026-08-01", "2026-08-02"), 1):
        for arm, pnl in (("passive_then_fallback", index), ("no_action", 0)):
            rows.append(
                {
                    "episode_id": f"e{index}",
                    "arm": arm,
                    "target_date": date,
                    "shares": "5",
                    "economic_profit_usd": str(pnl),
                }
            )
    point, lower, upper, episodes, dates = _bootstrap_paired_per_share(
        rows, "passive_then_fallback", repetitions=100, seed=7
    )
    assert point == Decimal("0.3")
    assert lower is not None and upper is not None
    assert (episodes, dates) == (2, 2)


def test_known_payoff_without_terminal_time_is_not_economic_evidence() -> None:
    stale = position()
    stale["settlement_available_at_utc"] = None
    episode, rows = _replay_episode(stale, None, {}, config())
    hold = next(row for row in rows if row["arm"] == "hold_to_settlement")
    assert hold["accounting_evidence_complete"] is True
    assert hold["evidence_complete"] is False
    assert hold["economic_profit_usd"] is None
    assert episode["paired_complete"] is False


def test_empty_actual_inventory_denominator_writes_fail_closed_report(
    tmp_path,
) -> None:
    db = tmp_path / "weather.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE fact_trades (
          condition_id TEXT, token_id TEXT, city TEXT, target_date TEXT,
          bracket TEXT, unit TEXT, fill_qty REAL, cost_usd REAL,
          fees_usd REAL, fill_ts_utc TEXT, settlement_status TEXT,
          final_yes REAL, trade_class TEXT, venue TEXT, instance_id TEXT,
          side TEXT, fact_built_at_utc TEXT
        );
        CREATE TABLE settlement_outcomes (
          condition_id TEXT, token_id TEXT, settlement_status TEXT,
          final_price REAL, available_at_utc TEXT, first_seen_at_utc TEXT,
          created_at_utc TEXT, settlement_outcome_id TEXT
        );
        """
    )
    connection.commit()
    connection.close()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "pre_live_scores.jsonl").write_text("")
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    book_root = tmp_path / "books"
    book_root.mkdir()
    output = tmp_path / "output"
    args = SimpleNamespace(
        config="src/strategies/weather_edge_v1/config/weather_mm_w1_inventory_prereg_v1.json",
        runtime=str(runtime),
        db=str(db),
        snapshot_root=str(snapshot_root),
        book_root=[str(book_root)],
        run_id=None,
        output_dir=str(output),
    )
    report = run(args)
    assert report["denominator"]["actual_inventory_episodes"] == 0
    assert report["denominator"]["date_min"] is None
    assert report["gates"]["correctness"]["status"] == "FAIL"
    assert report["gates"]["measurement"]["status"] == "FAIL"
    assert report["gates"]["promotion"] == "FAIL_CLOSED"
    assert report["decision"]["candidate_for_zero_notional_shadow"] == "none"
    assert json.loads((output / "report.json").read_text())["book_coverage"][
        "reason"
    ] == "empty_actual_inventory_denominator"
