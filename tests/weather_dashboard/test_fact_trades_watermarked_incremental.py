import sqlite3

from scripts.etl.build_weather_fact_trades import (
    FACT_DDL,
    build,
    collect_incremental_scope,
    write_db_watermarked_incremental,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE fills ("
        "fill_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE order_execution_aliases ("
        "alias_execution_id TEXT PRIMARY KEY, canonical_execution_id TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE fill_validity_adjustments ("
        "adjustment_id TEXT PRIMARY KEY, fill_id TEXT UNIQUE, effective_status TEXT)"
    )
    conn.execute(FACT_DDL)
    return conn


def test_incremental_bootstrap_finds_missing_tail_and_replays_exclusions():
    conn = _conn()
    try:
        conn.executemany(
            "INSERT INTO fills(fill_id, execution_id, status) VALUES (?,?,?)",
            [
                ("known", "exec-known", "filled"),
                ("alias", "exec-alias", "filled"),
                ("invalid", "exec-invalid", "filled"),
                ("new", "exec-new", "filled"),
            ],
        )
        conn.execute("INSERT INTO fact_trades(fill_id) VALUES ('known')")
        conn.execute("INSERT INTO fact_trades(fill_id) VALUES ('alias')")
        conn.execute("INSERT INTO fact_trades(fill_id) VALUES ('invalid')")
        conn.execute(
            "INSERT INTO order_execution_aliases VALUES ('exec-alias','exec-known')"
        )
        conn.execute(
            "INSERT INTO fill_validity_adjustments VALUES ('adj','invalid','excluded')"
        )
        conn.commit()

        scope, watermarks = collect_incremental_scope(conn)

        assert scope == {"alias", "invalid", "new"}
        assert watermarks["fills"] == 4
    finally:
        conn.close()


def test_incremental_publish_only_mutates_scope_and_advances_watermarks():
    conn = _conn()
    try:
        conn.executemany(
            "INSERT INTO fact_trades(fill_id) VALUES (?)",
            [("keep",), ("remove",)],
        )
        conn.commit()
        cols = [
            str(row[1])
            for row in conn.execute("PRAGMA table_info(fact_trades)").fetchall()
        ]
        replacement = {col: None for col in cols}
        replacement["fill_id"] = "insert"

        write_db_watermarked_incremental(
            conn,
            [replacement],
            {"remove", "insert"},
            {"fills": 10, "settlements": 20},
        )

        assert conn.execute(
            "SELECT fill_id FROM fact_trades ORDER BY fill_id"
        ).fetchall() == [("insert",), ("keep",)]
        assert conn.execute(
            "SELECT source_table,last_rowid "
            "FROM fact_materialization_watermarks "
            "WHERE fact_name='fact_trades' ORDER BY source_table"
        ).fetchall() == [("fills", 10), ("settlements", 20)]
    finally:
        conn.close()


def test_new_partial_fill_invalidates_existing_sibling_fill():
    conn = _conn()
    try:
        conn.executemany(
            "INSERT INTO fills(fill_id, execution_id, status) VALUES (?,?,?)",
            [("first", "shared-exec", "filled")],
        )
        conn.execute("INSERT INTO fact_trades(fill_id) VALUES ('first')")
        conn.commit()
        _, watermarks = collect_incremental_scope(conn)
        write_db_watermarked_incremental(conn, [], set(), watermarks)

        conn.execute(
            "INSERT INTO fills(fill_id, execution_id, status) VALUES (?,?,?)",
            ("second", "shared-exec", "filled"),
        )
        conn.commit()

        scope, _ = collect_incremental_scope(conn)
        assert scope == {"first", "second"}
    finally:
        conn.close()


def test_watermarked_incremental_preserves_older_schema_and_extra_columns():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE fact_trades ("
            "fill_id TEXT PRIMARY KEY, settled INTEGER, legacy_note TEXT)"
        )
        conn.execute("INSERT INTO fact_trades VALUES ('fill-1', 0, 'keep-me')")
        conn.commit()
        ddl_conn = sqlite3.connect(":memory:")
        ddl_conn.execute(FACT_DDL)
        cols = [row[1] for row in ddl_conn.execute("PRAGMA table_info(fact_trades)")]
        ddl_conn.close()
        replacement = {col: None for col in cols}
        replacement.update({"fill_id": "fill-1", "settled": 1})

        write_db_watermarked_incremental(
            conn,
            [replacement],
            {"fill-1"},
            {"fills": 1},
        )

        assert conn.execute(
            "SELECT settled, legacy_note FROM fact_trades WHERE fill_id='fill-1'"
        ).fetchone() == (1, "keep-me")
    finally:
        conn.close()


def test_empty_incremental_scope_does_not_fall_back_to_full_build():
    conn = sqlite3.connect(":memory:")
    try:
        rows, alerts = build(conn, fill_ids=[])
        assert rows == []
        assert alerts == []
    finally:
        conn.close()
