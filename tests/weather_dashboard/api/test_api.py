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


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


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


def test_get_run_metrics_slice_uses_canonical_fields(client, api_db):
    config_id, run_id, signal, plan, order, fill, settlement = _canonical_bundle()
    _insert_metadata(api_db, config_id, run_id)
    ingest_canonical_signals(api_db, [signal], "signals.jsonl")
    ingest_canonical_plans(api_db, [plan], "plans.jsonl")
    ingest_canonical_orders(api_db, [order], "orders.jsonl")
    ingest_canonical_fills(api_db, [fill], "fills.jsonl")
    ingest_canonical_settlements(api_db, [settlement], "settlements.jsonl")

    r = client.get(f"/api/runs/{run_id}/metrics/slice?group_by=city_pool")
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "city_pool"
    assert data["slices"][0]["slice_value"] == "t1_trading"
    assert data["slices"][0]["num_trades"] == 1
    assert data["slices"][0]["settled_trades"] == 1


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
