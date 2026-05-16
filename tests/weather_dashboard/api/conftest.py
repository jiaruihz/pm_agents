"""Fixtures for API tests: in-memory DB with schema + TestClient."""

import json
import pytest
from fastapi.testclient import TestClient

from weather_dashboard.api.app import create_app
from weather_dashboard.api.deps import get_db
from weather_dashboard.db.apply_schema import apply_schema
from weather_dashboard.db.connection import get_conn
from weather_dashboard.cli.config_register import register_config, _config_id
from weather_dashboard.cli.universe_register import register_universe
from weather_dashboard.cli.run_create import create_run


@pytest.fixture
def api_db(tmp_path):
    """In-memory SQLite with schema applied, plus seed data."""
    import sqlite3
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)
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
    register_config(api_db, "test_cfg", {"min_edge": 0.08})
    cid = _config_id({"min_edge": 0.08})
    register_universe(api_db, "u1", "Test Universe", "", ["Tokyo"], ["ecmwf"])
    run_id = create_run(api_db, cid, "u1", "sha_test", "snapshot_replay",
                        date_range_start="2026-01-01", state="explore")
    api_db.commit()
    return api_db, run_id, cid
