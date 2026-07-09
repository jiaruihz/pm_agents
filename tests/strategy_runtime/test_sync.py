import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from src.strategies.runtime.specs import load_instance_specs
from src.strategies.runtime.sync import sync_instance_specs


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    return conn


def test_sync_populates_instances(tmp_path):
    conn = _db(tmp_path)
    result = sync_instance_specs(conn)
    assert result["instance_rows"] == len(load_instance_specs())
    row = conn.execute(
        "SELECT * FROM strategy_instance WHERE instance_id=?",
        ("tmax_distribution_edge_first_lock_no_current_yes_shadow_v1",),
    ).fetchone()
    assert row["execution_mode"] == "zero_notional_shadow"
    assert row["desired_status"] == "enabled"  # table default on first insert
    assert row["params_hash"]
    conn.close()


def test_resync_preserves_operator_desired_status(tmp_path):
    conn = _db(tmp_path)
    sync_instance_specs(conn)
    conn.execute(
        "UPDATE strategy_instance SET desired_status='paused' WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    )
    conn.commit()
    sync_instance_specs(conn)  # re-sync must not clobber operator state
    row = conn.execute(
        "SELECT desired_status FROM strategy_instance WHERE instance_id=?",
        ("low_price_yes_lottery_tiny_live_v1",),
    ).fetchone()
    assert row["desired_status"] == "paused"
    conn.close()
