import json
import sqlite3

import pytest

from src.strategies.weather_edge_v1.ids import (
    make_execution_id,
    make_paper_order_id,
    make_plan_id,
    make_signal_id,
)
from weather_dashboard.contract import CanonicalValidationError
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import apply_pragmas
from weather_dashboard.ingest.canonical import (
    ingest_canonical_fills,
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_settlements,
    ingest_canonical_signals,
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)


@pytest.fixture
def canonical_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    apply_schema_canonical(conn)
    yield conn
    conn.close()


def _canonical_bundle():
    run_id = "run-canonical"
    config_id = "cfg-canonical"
    signal_id = make_signal_id(
        target_date="2026-05-17",
        city="LA",
        bracket="68-69",
        signal_side="NO",
        model_version="gfs",
        forecast_source="open_meteo_live_gfs",
        snapshot_ts_utc="2026-05-17T04:00:53Z",
        condition_id="0x9abc",
    )
    plan_id = make_plan_id(
        run_id=run_id,
        signal_id=signal_id,
        order_side="BUY_NO",
        execution_policy="mid_price_core_v1",
    )
    execution_id = make_execution_id(
        run_id=run_id,
        plan_id=plan_id,
        venue="paper",
        attempt_index=0,
    )
    order_id = make_paper_order_id(execution_id=execution_id)

    signal = {
        "signal_id": signal_id,
        "producer_system": "pm_agent_local",
        "producer_run_id": "snapshot_20260517_1200",
        "snapshot_ts_utc": "2026-05-17T04:00:53Z",
        "snapshot_file": "snapshot_20260517_1200.json",
        "target_date": "2026-05-17",
        "city": "LA",
        "city_pool": "t1_trading",
        "icao": "KLAX",
        "bracket": "68-69",
        "unit": "F",
        "signal_side": "NO",
        "model_version": "gfs",
        "model_p_yes": 0.3633,
        "forecast_source": "open_meteo_live_gfs",
        "market_price": 0.485,
        "edge": 0.1517,
        "abs_edge": 0.1517,
        "condition_id": "0x9abc",
        "market_id": "2266022",
        "token_id": "token-no",
        "hours_to_settle": 12.5,
    }
    plan = {
        "plan_id": plan_id,
        "run_id": run_id,
        "signal_id": signal_id,
        "config_id": config_id,
        "order_side": "BUY_NO",
        "notional": 5.0,
        "desired_shares": 10.309278,
        "sizing_mode": "notional",
        "entry_price_window": "0.25-0.75",
        "execution_policy": "mid_price_core_v1",
        "limit_price": 0.485,
        "skip_reason": None,
        "status": "accepted",
    }
    order = {
        "execution_id": execution_id,
        "order_id": order_id,
        "run_id": run_id,
        "plan_id": plan_id,
        "venue": "paper",
        "order_side": "BUY_NO",
        "limit_price": 0.485,
        "entry_price": 0.45,
        "shares": 10.309278,
        "cost_usd": 4.639175,
        "notional": 5.0,
        "status": "filled",
        "exchange_response": None,
        "placed_at_utc": "2026-05-17T04:06:33Z",
    }
    fill = {
        "fill_id": "fill-canonical",
        "execution_id": execution_id,
        "order_id": order_id,
        "filled_shares": 10.309278,
        "filled_price": 0.45,
        "fees_usd": 0.0,
        "status": "filled",
        "filled_at_utc": "2026-05-17T04:06:33Z",
    }
    settlement = {
        "settlement_id": "d" * 64,
        "target_date": "2026-05-17",
        "condition_id": "0x9abc",
        "market_id": "2266022",
        "bracket": "68-69",
        "token_id": "token-no",
        "final_price": 0.0,
        "settlement_status": "settled",
    }
    return config_id, run_id, signal, plan, order, fill, settlement


def _insert_metadata(conn, config_id: str, run_id: str) -> None:
    insert_strategy_config(conn, config_id, "weather_edge_canonical", {"min_edge": 0.1})
    insert_universe(conn, "u-canonical", "T24", cities=["LA"], models=["gfs"])
    insert_code_version(conn, "test-sha", branch="test")
    insert_run(
        conn,
        {
            "run_id": run_id,
            "producer_system": "pm_agent_local",
            "producer_run_id": "snapshot_20260517_1200",
            "config_id": config_id,
            "universe_id": "u-canonical",
            "code_version": "test-sha",
            "execution_mode": "paper",
            "state": "paper",
            "tags": ["canonical"],
            "metrics": {"placeholder": True},
        },
    )


def test_ingest_canonical_bundle_is_queryable(canonical_conn):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(canonical_conn, config_id, run_id)

    assert ingest_canonical_signals(canonical_conn, [signal], "signals.jsonl") == 1
    assert ingest_canonical_plans(canonical_conn, [plan], "plans.jsonl") == 1
    assert ingest_canonical_orders(canonical_conn, [order], "orders.jsonl") == 1
    assert ingest_canonical_fills(canonical_conn, [fill], "fills.jsonl") == 1
    assert ingest_canonical_settlements(canonical_conn, [settlement], "settlements.jsonl") == 1

    row = canonical_conn.execute(
        """
        SELECT sig.city, p.order_side, o.execution_id, f.fill_id, s.final_price
        FROM signals sig
        JOIN plans p ON p.signal_id = sig.signal_id
        JOIN orders o ON o.plan_id = p.plan_id
        JOIN fills f ON f.execution_id = o.execution_id
        JOIN settlements s
          ON s.target_date = sig.target_date
         AND s.condition_id = sig.condition_id
         AND s.bracket = sig.bracket
        WHERE sig.signal_id = ?
        """,
        (signal["signal_id"],),
    ).fetchone()

    assert dict(row) == {
        "city": "LA",
        "order_side": "BUY_NO",
        "execution_id": order["execution_id"],
        "fill_id": "fill-canonical",
        "final_price": 0.0,
    }

    run = canonical_conn.execute("SELECT tags, metrics FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert json.loads(run["tags"]) == ["canonical"]
    assert json.loads(run["metrics"]) == {"placeholder": True}


def test_ingest_canonical_signals_is_idempotent(canonical_conn):
    config_id, run_id, signal, *_ = _canonical_bundle()
    _insert_metadata(canonical_conn, config_id, run_id)

    assert ingest_canonical_signals(canonical_conn, [signal], "signals.jsonl") == 1
    assert ingest_canonical_signals(canonical_conn, [signal], "signals.jsonl") == 0
    assert canonical_conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1


def test_ingest_canonical_signal_rejects_legacy_alias(canonical_conn):
    config_id, run_id, signal, *_ = _canonical_bundle()
    _insert_metadata(canonical_conn, config_id, run_id)
    signal = dict(signal)
    signal.pop("target_date")
    signal["event_date"] = "2026-05-17"

    with pytest.raises(CanonicalValidationError, match="target_date"):
        ingest_canonical_signals(canonical_conn, [signal], "legacy.csv")
