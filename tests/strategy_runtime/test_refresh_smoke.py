import sqlite3

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from scripts.ops import refresh_weather_strategy_runtime_registry as registry
from src.strategies.runtime.specs import load_instance_specs


def test_strategy_specs_delegates_to_yaml():
    assert len(registry.strategy_specs()) == len(load_instance_specs())


def test_refresh_populates_registry_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    conn.row_factory = sqlite3.Row
    apply_schema_canonical(conn)
    # fact_trades is built by scripts/etl/build_weather_fact_trades.py in prod, not
    # by the canonical schema; refresh() reads it. An empty table is enough here.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fact_trades (
            strategy_name TEXT, strategy_id TEXT, execution_mode TEXT,
            trade_class TEXT, cost_usd REAL, target_date TEXT, fill_ts_utc TEXT
        )
        """
    )
    registry.refresh(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM weather_strategy_runtime_registry"
    ).fetchone()[0]
    assert count == len(load_instance_specs())
    conn.close()
