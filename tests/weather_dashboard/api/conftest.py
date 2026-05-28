"""Fixtures for API tests: in-memory DB with schema + TestClient."""

import json
import pytest
from fastapi.testclient import TestClient

from weather_dashboard.api.app import create_app
from weather_dashboard.api.deps import get_db
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.ingest.canonical import (
    insert_code_version,
    insert_run,
    insert_strategy_config,
    insert_universe,
)


@pytest.fixture
def api_db(tmp_path):
    """In-memory SQLite with schema applied, plus seed data."""
    import sqlite3
    from scripts.analysis.build_weather_fact_trades import FACT_DDL
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema_canonical(conn)
    conn.execute(FACT_DDL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client(api_db):
    """TestClient with the DB dependency overridden to use api_db."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: api_db
    return TestClient(app)


@pytest.fixture
def seeded_run(api_db):
    """Seed one config + universe + run, return (conn, run_id, config_id)."""
    cid = "cfg_test"
    run_id = "run_test"
    insert_strategy_config(api_db, cid, "test_cfg", {"min_edge": 0.08})
    insert_universe(api_db, "u1", "Test Universe", description="", cities=["Tokyo"], models=["ecmwf"])
    insert_code_version(api_db, "sha_test")
    insert_run(api_db, {
        "run_id": run_id,
        "producer_system": "pm_agent_local",
        "producer_run_id": "test_producer_run",
        "config_id": cid,
        "universe_id": "u1",
        "code_version": "sha_test",
        "execution_mode": "snapshot_replay",
        "date_range_start": "2026-01-01",
        "state": "explore",
    })
    api_db.commit()
    return api_db, run_id, cid
