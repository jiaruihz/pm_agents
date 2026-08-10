import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from src.strategies.runtime.sync import sync_instance_specs
from src.strategies.runtime import control


def _db(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    return conn


def test_record_action_sets_desired_and_logs(tmp_path):
    conn = _db(tmp_path)
    iid = "low_price_yes_lottery_tiny_live_v1"
    control.record_control_action(
        conn, instance_id=iid, action="stop", to_state="paused",
        reason="ops test", actor="tester",
    )
    assert control.get_desired_status(conn, iid) == "paused"
    log = conn.execute(
        "SELECT action, from_state, to_state, reason, actor FROM strategy_control_log "
        "WHERE instance_id=? ORDER BY ts_utc DESC LIMIT 1",
        (iid,),
    ).fetchone()
    assert log["action"] == "stop"
    assert log["from_state"] == "shelved"
    assert log["to_state"] == "paused"
    assert log["reason"] == "ops test"
    assert log["actor"] == "tester"
    conn.close()


def test_log_id_is_unique(tmp_path):
    conn = _db(tmp_path)
    iid = "low_price_yes_lottery_tiny_live_v1"
    a = control.record_control_action(conn, instance_id=iid, action="start",
                                      to_state="enabled", reason="r", actor="t")
    b = control.record_control_action(conn, instance_id=iid, action="stop",
                                      to_state="paused", reason="r", actor="t")
    assert a != b
    conn.close()
