import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from src.strategies.runtime.sync import sync_instance_specs
from scripts.ops import weather_strategy_launcher as launcher


def test_instance_rows_reads_from_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    sync_instance_specs(conn)
    rows = launcher.instance_rows(conn)
    ids = {r["instance_id"] for r in rows}
    assert "low_price_yes_lottery_tiny_live_v1" in ids
    sample = next(r for r in rows if r["instance_id"] == "low_price_yes_lottery_tiny_live_v1")
    assert sample["desired_status"] == "enabled"
    assert "execution_mode" in sample
    conn.close()
