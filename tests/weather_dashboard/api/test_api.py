"""Integration tests for the FastAPI endpoints."""

import json
import pytest

from tests.weather_dashboard.test_canonical_ingest import _canonical_bundle, _insert_metadata
from weather_dashboard.ingest.canonical import (
    ingest_canonical_fills,
    ingest_canonical_orders,
    ingest_canonical_plans,
    ingest_canonical_settlements,
    ingest_canonical_signals,
)
from scripts.etl.build_weather_fact_trades import build as _build_fact, write_db as _write_fact
from src.strategies.runtime.specs import load_instance_specs
from src.strategies.runtime.runtime_state import push_runtime_state
from src.strategies.runtime.ownership import backfill_order_instance_links
from src.strategies.runtime.sync import sync_instance_specs


def _rebuild_fact(conn):
    """Populate fact_trades from raw tables in the test DB."""
    rows, _ = _build_fact(conn)
    _write_fact(conn, rows)


def _seed_runtime_config_refs(conn):
    """Seed config refs required by the git-authored runtime instance spec."""
    for config_id in sorted({s.config_id for s in load_instance_specs() if s.config_id}):
        conn.execute(
            """
            INSERT OR IGNORE INTO strategy_config (config_id, name, params)
            VALUES (?, ?, '{}')
            """,
            (config_id, f"test_ref_{config_id}"),
        )
    conn.commit()


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_live_book_exposes_strategy_instance(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")
    _rebuild_fact(api_db)
    api_db.execute(
        "UPDATE fact_trades SET trade_class='live_real', instance_id='fast_source_prev_no_trial_v1' WHERE fill_id=?",
        (fill["fill_id"],),
    )
    api_db.commit()

    response = client.get("/api/live/book")

    assert response.status_code == 200
    assert response.json()["rows"][0]["instance_id"] == "fast_source_prev_no_trial_v1"


# ── /api/runs ─────────────────────────────────────────────────────────────────

def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_returns_seeded(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["run_id"] == run_id


def test_list_runs_filter_state(client, seeded_run):
    r = client.get("/api/runs?state=explore")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r2 = client.get("/api/runs?state=live")
    assert r2.status_code == 200
    assert len(r2.json()) == 0


def test_get_run(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == run_id
    assert "metrics" in data
    assert data["metrics"]["num_trades"] == 0


def test_get_run_not_found(client):
    r = client.get("/api/runs/nonexistent-run-id")
    assert r.status_code == 404


def test_get_run_trades_empty(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/runs/{run_id}/trades")
    assert r.status_code == 200
    assert r.json() == []


def test_get_run_trades_returns_canonical_lineage(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")

    r = client.get(f"/api/runs/{run_id}/trades?city_pool=t1_trading&forecast_source=open_meteo_live_gfs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["signal_id"] == signal["signal_id"]
    assert data[0]["execution_id"] == order["execution_id"]
    assert data[0]["signal_side"] == "NO"
    assert data[0]["order_side"] == "BUY_NO"
    assert data[0]["final_price"] == 0.0
    assert float(data[0]["pnl_usd"]) > 0


def test_get_trade_drilldown_returns_vertical_lineage(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    order = {
        **order,
        "venue": "polymarket_clob",
        "status": "submitted",
        "exchange_response": json.dumps({"place": {"orderID": "0xabc", "success": True}}),
    }
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")

    r = client.get(f"/api/runs/{run_id}/trades/{signal['signal_id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == run_id
    assert data["signal"]["signal_id"] == signal["signal_id"]
    assert data["plans"][0]["plan_id"] == plan["plan_id"]
    assert data["orders"][0]["execution_id"] == order["execution_id"]
    assert data["orders"][0]["exchange_response"]["place"]["success"] is True
    assert data["fills"][0]["fill_id"] == fill["fill_id"]
    assert data["settlement"]["final_price"] == 0.0


def test_get_run_trades_includes_live_orders_without_fills(client, api_db):
    config_id, run_id, signal, plan, order, _, _ = _canonical_bundle()
    order = {
        **order,
        "venue": "polymarket_clob",
        "status": "submitted",
        "exchange_response": json.dumps({"place": {"orderID": "0xabc", "success": True}}),
    }
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")

    r = client.get(f"/api/runs/{run_id}/trades")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["execution_id"] == order["execution_id"]
    assert data[0]["fill_status"] == "submitted"
    assert data[0]["pnl_usd"] is None


def test_order_blotter_returns_filled_fact_rows(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")
    _rebuild_fact(api_db)

    r = client.get("/api/order-blotter?trade_class=paper&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["row_kind"] == "fill"
    assert data["rows"][0]["fill_id"] == fill["fill_id"]
    assert data["rows"][0]["config_id"] == config_id
    assert data["rows"][0]["pnl_usd_at_fill"] is not None
    assert data["rows"][0]["settlement_status"] == "settled"
    assert data["rows"][0]["final_yes"] in {0.0, 1.0}


def test_order_blotter_daily_summary_groups_complete_fill_grain(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")
    _rebuild_fact(api_db)

    r = client.get("/api/order-blotter/daily-summary?trade_class=paper")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["target_date"] == signal["target_date"]
    assert rows[0]["fill_count"] == 1
    assert rows[0]["settled_count"] == 1
    assert rows[0]["open_count"] == 0


def test_order_blotter_includes_unfilled_orders(client, api_db):
    config_id, run_id, signal, plan, order, _, _ = _canonical_bundle()
    order = {**order, "status": "submitted"}
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")

    r = client.get("/api/order-blotter?trade_class=paper&status=unfilled&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["row_kind"] == "order"
    assert data["rows"][0]["execution_id"] == order["execution_id"]
    assert data["rows"][0]["fill_id"] is None


def test_order_blotter_filters_by_strategy_instance(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")
    _seed_runtime_config_refs(api_db)
    sync_instance_specs(api_db)
    api_db.execute(
        "UPDATE strategy_instance SET config_id=? WHERE instance_id=?",
        (config_id, "low_price_yes_lottery_tiny_live_v1"),
    )
    backfill_order_instance_links(api_db)
    api_db.commit()
    _rebuild_fact(api_db)

    r = client.get("/api/order-blotter?trade_class=paper&instance_id=low_price_yes_lottery_tiny_live_v1")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["filters"]["config_id"] is None
    assert data["rows"][0]["strategy_instance"] == "low_price_yes_lottery_tiny_live_v1"

    instance = client.get("/api/strategy-instances/low_price_yes_lottery_tiny_live_v1")
    assert instance.status_code == 200
    summary = instance.json()["execution_summary"]
    assert summary["order_count"] == 1
    assert summary["fill_count"] == 1
    assert summary["fact_trade_count"] == 1


def test_strategy_management_exposes_definition_config_and_instance(client, api_db):
    _seed_runtime_config_refs(api_db)
    api_db.execute(
        "UPDATE strategy_config SET params=? WHERE config_id=?",
        (json.dumps({"execution_policy": "low_price_yes_lottery_guarded_taker_v1"}), "live_weather_edge_v1_dfdc707d8ac7"),
    )
    sync_instance_specs(api_db)

    definitions = client.get("/api/strategy-definitions")
    assert definitions.status_code == 200
    low_price = next(
        row for row in definitions.json()
        if row["strategy_key"] == "forecast_quality.low_price_yes_lottery"
    )
    assert low_price["config_count"] >= 1
    assert low_price["instance_count"] >= 1
    assert low_price["portfolio_status"] == "forward_live"

    detail = client.get("/api/strategy-definitions/forecast_quality.low_price_yes_lottery")
    assert detail.status_code == 200
    assert detail.json()["strategy"]["strategy_name"] == "低价 YES 彩票型策略"
    assert "tiny-live" in detail.json()["strategy"]["portfolio_note"]

    instances = client.get("/api/strategy-instances?strategy_key=forecast_quality.low_price_yes_lottery")
    assert instances.status_code == 200
    assert any(row["instance_id"] == "low_price_yes_lottery_tiny_live_v1" for row in instances.json())


def test_get_run_metrics_slice_uses_canonical_fields(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")
    _rebuild_fact(api_db)

    r = client.get(f"/api/runs/{run_id}/metrics/slice?group_by=city_pool")
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "city_pool"
    assert data["slices"][0]["slice_value"] == "t1_trading"
    assert data["slices"][0]["num_trades"] == 1
    assert data["slices"][0]["settled_trades"] == 1


def test_strategy_runtime_overview_reads_instance_runtime(client, api_db):
    _seed_runtime_config_refs(api_db)
    sync_instance_specs(api_db)
    push_runtime_state(
        api_db,
        instance_id="low_price_yes_lottery_tiny_live_v1",
        process_status="running",
        health_status="healthy",
        candidate_rows=3,
        live_order_rows=2,
    )
    api_db.commit()

    r = client.get("/api/strategy-runtime/overview")
    assert r.status_code == 200
    rows = r.json()["strategies"]
    row = next(x for x in rows if x["strategy_instance"] == "low_price_yes_lottery_tiny_live_v1")
    assert row["config_id"] == "live_weather_edge_v1_dfdc707d8ac7"
    assert row["process_status"] == "running"
    assert row["health_status"] == "healthy"
    assert row["candidate_rows"] == 3
    assert row["live_order_rows"] == 2


# ── /api/compare ──────────────────────────────────────────────────────────────

def test_compare_single_run(client, seeded_run):
    _, run_id, _ = seeded_run
    r = client.get(f"/api/compare?run_ids={run_id}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == run_id
    assert "metrics" in data["runs"][0]


def test_compare_missing_run(client):
    r = client.get("/api/compare?run_ids=bad-id-1,bad-id-2")
    assert r.status_code == 200
    data = r.json()
    assert len(data["runs"]) == 2
    assert data["runs"][0]["error"] == "not found"


# ── /api/configs ──────────────────────────────────────────────────────────────

def test_list_configs(client, seeded_run):
    r = client.get("/api/configs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "test_cfg"
    assert isinstance(data[0]["params"], dict)


def test_get_config(client, seeded_run):
    _, _, cid = seeded_run
    r = client.get(f"/api/configs/{cid}")
    assert r.status_code == 200
    assert r.json()["config_id"] == cid


def test_get_config_not_found(client):
    r = client.get("/api/configs/nonexistent")
    assert r.status_code == 404


# ── /api/universes ────────────────────────────────────────────────────────────

def test_list_universes(client, seeded_run):
    r = client.get("/api/universes")
    assert r.status_code == 200
    data = r.json()
    # bootstrap universe + our test universe
    names = {d["universe_id"] for d in data}
    assert "u1" in names


def test_get_universe(client, seeded_run):
    r = client.get("/api/universes/u1")
    assert r.status_code == 200
    data = r.json()
    assert data["cities"] == ["Tokyo"]
    assert data["models"] == ["ecmwf"]


# ── /api/settlements ──────────────────────────────────────────────────────────

def test_list_settlements_empty(client):
    r = client.get("/api/settlements")
    assert r.status_code == 200
    assert r.json() == []


def test_list_settlements_with_filter(client, api_db):
    api_db.execute(
        """
        INSERT INTO settlements (
            settlement_id, target_date, condition_id, market_id, bracket,
            token_id, final_price, settlement_status
        )
        VALUES (?,?,?,?,?,?,?,?)
        """,
        ("d" * 64, "2026-05-09", "0xabc", "m1", "23", "token", 1.0, "settled"),
    )
    api_db.commit()

    r = client.get("/api/settlements?target_date=2026-05-09")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["bracket"] == "23"
    assert data[0]["final_price"] == 1.0
    assert data[0]["settlement_status"] == "settled"
