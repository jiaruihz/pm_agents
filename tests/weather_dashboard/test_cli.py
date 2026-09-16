"""Tests for CLI modules: config_register, universe_register, run_create."""

import json
import pytest
import yaml

from weather_dashboard.cli.config_register import register_config, _config_id
from weather_dashboard.cli.universe_register import register_universe
from weather_dashboard.cli.run_create import create_run


# ── config_register ──────────────────────────────────────────────────────────

def test_config_id_deterministic():
    params = {"min_edge": 0.08, "max_cost": 100}
    assert _config_id(params) == _config_id(params)


def test_config_id_differs_on_different_params():
    assert _config_id({"a": 1}) != _config_id({"a": 2})


def test_register_config_inserts(tmp_db_with_schema):
    cid = register_config(tmp_db_with_schema, "test_config", {"min_edge": 0.1})
    row = tmp_db_with_schema.execute(
        "SELECT * FROM strategy_config WHERE config_id=?", (cid,)
    ).fetchone()
    assert row is not None
    assert row["name"] == "test_config"


def test_register_config_idempotent(tmp_db_with_schema):
    params = {"min_edge": 0.1}
    cid1 = register_config(tmp_db_with_schema, "cfg", params)
    cid2 = register_config(tmp_db_with_schema, "cfg", params)
    assert cid1 == cid2
    count = tmp_db_with_schema.execute("SELECT COUNT(*) FROM strategy_config").fetchone()[0]
    assert count == 1


# ── universe_register ─────────────────────────────────────────────────────────

def test_register_universe_inserts(tmp_db_with_schema):
    uid = register_universe(
        tmp_db_with_schema,
        universe_id="u1",
        name="Test Universe",
        description="desc",
        cities=["Tokyo", "Warsaw"],
        models=["ecmwf"],
    )
    row = tmp_db_with_schema.execute(
        "SELECT * FROM universes WHERE universe_id=?", (uid,)
    ).fetchone()
    assert row is not None
    assert json.loads(row["cities"]) == ["Tokyo", "Warsaw"]


def test_register_universe_idempotent(tmp_db_with_schema):
    for _ in range(2):
        register_universe(tmp_db_with_schema, "u1", "U", "", ["NYC"], ["gfs"])
    count = tmp_db_with_schema.execute("SELECT COUNT(*) FROM universes").fetchone()[0]
    assert count == 1


# ── run_create ────────────────────────────────────────────────────────────────

def _setup_config_and_universe(conn):
    """Insert prerequisite rows for run tests."""
    register_config(conn, "cfg", {"min_edge": 0.08})
    cid = _config_id({"min_edge": 0.08})
    register_universe(conn, "u1", "Universe 1", "", ["Tokyo"], ["ecmwf"])
    return cid, "u1"


def test_create_run_returns_run_id(tmp_db_with_schema):
    cid, uid = _setup_config_and_universe(tmp_db_with_schema)
    run_id = create_run(
        tmp_db_with_schema, cid, uid, "abc123",
        execution_mode="snapshot_replay",
        date_range_start="2026-01-01",
        date_range_end="2026-05-01",
    )
    assert run_id is not None
    row = tmp_db_with_schema.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["execution_mode"] == "snapshot_replay"
    assert row["repro_key"] is not None


def test_create_run_repro_key_deterministic(tmp_db_with_schema):
    """Two runs with same params get same repro_key."""
    from weather_dashboard.cli.run_create import _repro_key
    k1 = _repro_key("cfg1", "sha1", "u1", "2026-01-01", "2026-05-01")
    k2 = _repro_key("cfg1", "sha1", "u1", "2026-01-01", "2026-05-01")
    assert k1 == k2


def test_create_run_missing_config_raises(tmp_db_with_schema):
    register_universe(tmp_db_with_schema, "u1", "U", "", ["Tokyo"], ["ecmwf"])
    with pytest.raises(ValueError, match="config_id not found"):
        create_run(tmp_db_with_schema, "nonexistent_config", "u1", "sha1",
                   execution_mode="paper")


def test_create_run_missing_universe_raises(tmp_db_with_schema):
    cid = register_config(tmp_db_with_schema, "cfg", {"x": 1})
    with pytest.raises(ValueError, match="universe_id not found"):
        create_run(tmp_db_with_schema, cid, "nonexistent_universe", "sha1",
                   execution_mode="paper")


# ── YAML round-trip ───────────────────────────────────────────────────────────

def test_register_config_from_yaml(tmp_path, tmp_db_with_schema):
    from weather_dashboard.cli.config_register import register_config_from_yaml
    from weather_dashboard.db.apply_schema import init_db

    yaml_file = tmp_path / "cfg.yaml"
    yaml_file.write_text(yaml.dump({"name": "my_cfg", "params": {"min_edge": 0.05}}))

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    cid = register_config_from_yaml(str(yaml_file), db_path)
    assert cid == _config_id({"min_edge": 0.05})


def test_register_universe_from_yaml(tmp_path):
    from weather_dashboard.cli.universe_register import register_universe_from_yaml
    from weather_dashboard.db.apply_schema import init_db
    from weather_dashboard.db.connection import get_conn

    yaml_file = tmp_path / "u.yaml"
    yaml_file.write_text(yaml.dump({
        "universe_id": "utest",
        "name": "Test",
        "cities": ["Tokyo"],
        "models": ["ecmwf"],
    }))

    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    uid = register_universe_from_yaml(str(yaml_file), db_path)
    assert uid == "utest"

    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM universes WHERE universe_id='utest'").fetchone()
    conn.close()
    assert row is not None
