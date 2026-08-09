import sqlite3

from scripts.ops.reconcile_weather_order_execution_aliases import reconcile


def test_duplicate_physical_order_is_resolved_to_earliest_execution() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE signals (
          signal_id TEXT PRIMARY KEY, condition_id TEXT, token_id TEXT
        );
        CREATE TABLE plans (
          plan_id TEXT PRIMARY KEY, signal_id TEXT
        );
        CREATE TABLE orders (
          execution_id TEXT PRIMARY KEY, order_id TEXT, plan_id TEXT,
          venue TEXT, order_side TEXT, created_at_utc TEXT
        );
        CREATE TABLE order_execution_aliases (
          alias_execution_id TEXT PRIMARY KEY,
          canonical_execution_id TEXT NOT NULL,
          physical_order_id TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          source_path TEXT,
          created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO signals VALUES ('s1', 'condition', 'token');
        INSERT INTO signals VALUES ('s2', 'condition', 'token');
        INSERT INTO plans VALUES ('p1', 's1');
        INSERT INTO plans VALUES ('p2', 's2');
        INSERT INTO orders VALUES (
          'earliest', 'physical-order', 'p1', 'polymarket_clob',
          'BUY_NO', '2026-07-20T00:00:00Z'
        );
        INSERT INTO orders VALUES (
          'later', 'physical-order', 'p2', 'polymarket_clob',
          'BUY_NO', '2026-07-25T00:00:00Z'
        );
        """
    )

    payload = reconcile(conn, apply=True, source_path="test")

    assert payload["conflicts"] == []
    assert payload["inserted_aliases"] == 1
    assert tuple(
        conn.execute(
            """
            SELECT alias_execution_id, canonical_execution_id, physical_order_id
            FROM order_execution_aliases
            """
        ).fetchone()
    ) == ("later", "earliest", "physical-order")
