import sqlite3
from pathlib import Path

import pytest

from weather_dashboard.cli import ingest_strategy_runtime_orders as cli
from src.strategies.runtime.production import WeatherManagedRuntimeSpec, WeatherProductionSpec


def test_order_files_supplement_default_runtime_roots(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    discovered = root / "d1" / "live_orders.jsonl"
    discovered.parent.mkdir(parents=True)
    discovered.write_text("")
    explicit = tmp_path / "external" / "orders.jsonl"
    explicit.parent.mkdir()
    explicit.write_text("")
    monkeypatch.setattr(cli, "DEFAULT_ROOTS", (str(root),))

    assert cli.resolve_order_paths(None, [str(explicit)]) == [discovered, explicit]


def test_active_live_order_paths_follow_production_manifest(tmp_path) -> None:
    active = tmp_path / "runtime" / "active"
    active.mkdir(parents=True)
    active_orders = active / "live_orders.jsonl"
    active_orders.write_text("")
    dormant = tmp_path / "runtime" / "dormant"
    dormant.mkdir(parents=True)
    (dormant / "live_orders.jsonl").write_text("")
    external = tmp_path / "external"
    external.mkdir()
    external_orders = external / "orders.jsonl"
    external_orders.write_text("")

    spec = WeatherProductionSpec(
        version="test",
        host_role="test",
        operational_repo_root=tmp_path,
        canonical_db_path=tmp_path / "runtime/weather.db",
        compatibility_db_paths=(Path("runtime/weather.db"),),
        data_feed_runtime_root=tmp_path / "feed",
        pm_runtime_root=tmp_path / "runtime",
        canonical_tmux_socket="test",
        canonical_tmux_binary=Path("/usr/bin/tmux"),
        market_proxy_state_path=tmp_path / "runtime/market_proxy_state.json",
        market_proxy_default_url="http://127.0.0.1:7897",
        managed_runtimes=(
            WeatherManagedRuntimeSpec(
                instance_id="active", tmux_session="active", role="strategy",
                execution_mode="live", expected_live=True,
                live_order_path=active_orders.relative_to(tmp_path),
            ),
            WeatherManagedRuntimeSpec(
                instance_id="paper", tmux_session="paper", role="strategy",
                execution_mode="shadow", live_order_path=dormant / "live_orders.jsonl",
            ),
            WeatherManagedRuntimeSpec(
                instance_id="external", tmux_session="external", role="strategy",
                execution_mode="live", expected_live=True, live_order_path=external_orders,
            ),
        )
    )

    assert cli.resolve_active_live_order_paths(tmp_path, production_spec=spec) == [
        active_orders,
        external_orders,
    ]


def _existing_order_ingest_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        for table in sorted(cli._REQUIRED_EXISTING_TABLES):
            conn.execute(f"CREATE TABLE {table} (id TEXT)")
        conn.commit()
    finally:
        conn.close()


def test_require_existing_order_ingest_schema_is_read_only_and_fail_closed(
    tmp_path,
) -> None:
    db = tmp_path / "weather.db"
    _existing_order_ingest_db(db)
    before = db.read_bytes()

    conn = cli.get_existing_wal_conn(db)
    try:
        cli.require_existing_order_ingest_schema(conn)
    finally:
        conn.close()

    assert db.read_bytes() == before
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE orders")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="orders"):
        conn = cli.get_existing_wal_conn(db)
        try:
            cli.require_existing_order_ingest_schema(conn)
        finally:
            conn.close()


def test_existing_order_ingest_connection_does_not_convert_journal_mode(
    tmp_path,
) -> None:
    db = tmp_path / "weather.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sentinel (id TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="must already use WAL"):
        cli.get_existing_wal_conn(db)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_cli_require_existing_schema_never_calls_schema_initializer(
    tmp_path, monkeypatch
) -> None:
    db = tmp_path / "weather.db"
    _existing_order_ingest_db(db)
    monkeypatch.setattr(
        cli,
        "init_db_canonical",
        lambda _path: pytest.fail("schema initializer must not run"),
    )
    monkeypatch.setattr(cli, "resolve_active_live_order_paths", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "write_report", lambda *_a, **_kw: tmp_path / "report.json")

    cli.main(
        [
            "--db-path",
            str(db),
            "--report-dir",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--active-live-only",
            "--require-existing-schema",
        ]
    )
