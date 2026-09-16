import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical


def _cols(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_control_plane_tables_exist(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    apply_schema_canonical(conn)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"strategy_def", "strategy_instance", "strategy_instance_runtime", "strategy_control_log", "order_instance_lineage"} <= tables
    assert {"instance_id", "desired_status", "config_id", "spec_commit", "params_hash"} <= _cols(
        conn, "strategy_instance"
    )
    assert {"log_id", "action", "from_state", "to_state", "reason"} <= _cols(
        conn, "strategy_control_log"
    )
    assert {"instance_id", "process_status", "heartbeat_at_utc", "health_status"} <= _cols(
        conn, "strategy_instance_runtime"
    )
    assert {"strategy_key"} <= _cols(conn, "strategy_config")
    assert {"instance_id"} <= _cols(conn, "orders")
    conn.close()
