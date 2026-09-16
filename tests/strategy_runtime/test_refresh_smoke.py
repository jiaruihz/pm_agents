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


def test_schema_accepts_manifest_runtime_taxonomy(tmp_path):
    conn = sqlite3.connect(tmp_path / "weather.db")
    apply_schema_canonical(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='weather_strategy_runtime_registry'"
    ).fetchone()[0]
    for value in (
        "pre_live",
        "superseded-for-now",
        "tiny_live_probe",
        "deprecated",
        "zero_notional_pre_live",
        "tiny_live_taker_probe",
        "legacy_read_only",
    ):
        assert value in sql
    conn.close()


def test_registry_journal_helpers_resolve_dated_partitions(tmp_path):
    semantic_path = tmp_path / "opportunities.jsonl"
    oldest = tmp_path / "2026-08-09" / "opportunities.jsonl"
    newest = tmp_path / "2026-08-10" / "opportunities.jsonl"
    oldest.parent.mkdir()
    newest.parent.mkdir()
    oldest.write_text('{"ts_utc":"2026-08-09T00:00:00Z"}\n', encoding="utf-8")
    newest.write_text(
        '{"ts_utc":"2026-08-10T00:00:00Z"}\n'
        '{"ts_utc":"2026-08-10T00:05:00Z"}\n',
        encoding="utf-8",
    )

    assert registry.count_lines(semantic_path) == 3
    assert registry.latest_record_ts(semantic_path).isoformat() == "2026-08-10T00:05:00+00:00"
    assert "2026-08-10T00:05:00Z" in registry.sample_last_json(semantic_path)
    assert registry.file_mtime(semantic_path) is not None
