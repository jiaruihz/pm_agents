import sqlite3

from weather_dashboard.ingest.clob_fill_fee_adjustments import (
    append_fee_adjustment,
    import_fee_adjustments,
)
from scripts.ops.reconcile_clob_fill_fees import _load_rows


def test_fee_adjustment_journal_and_import_are_idempotent(tmp_path):
    journal = tmp_path / "fee_adjustments.jsonl"
    row = {
        "adjustment_id": "adjustment-1",
        "fill_id": "fill-1",
        "fee_delta_usd": 0.05527,
        "fee_source": "public_activity_tx_exact",
        "fee_evidence_class": "exact",
        "transaction_hash": "0xc055",
        "fee_rate": 0.05,
        "market_fee_metadata": {"usdc_size": 3.40527},
        "evidence": {"size": 5, "price": 0.67},
        "created_at_utc": "2026-07-11T00:00:00+00:00",
    }

    assert append_fee_adjustment(row, journal)
    assert not append_fee_adjustment(row, journal)
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE fills (fill_id TEXT PRIMARY KEY)")
    assert import_fee_adjustments(conn, journal) == 1
    assert import_fee_adjustments(conn, journal) == 0
    assert conn.execute("SELECT COUNT(*) FROM fill_fee_adjustments").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='trigger' AND name LIKE 'trg_fill_fee_adjustments_no_%'"
    ).fetchone()[0] == 2


def test_fee_reconcile_ignores_alias_and_excluded_fills() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE signals (
          signal_id TEXT, condition_id TEXT, token_id TEXT, city TEXT,
          target_date TEXT, bracket TEXT
        );
        CREATE TABLE plans (plan_id TEXT, signal_id TEXT);
        CREATE TABLE orders (
          execution_id TEXT, plan_id TEXT, venue TEXT, status TEXT,
          order_side TEXT, exchange_response TEXT
        );
        CREATE TABLE fills (
          fill_id TEXT, execution_id TEXT, order_id TEXT, filled_shares REAL,
          filled_price REAL, fees_usd REAL, fee_source TEXT,
          status TEXT, filled_at_utc TEXT
        );
        CREATE TABLE order_execution_aliases (alias_execution_id TEXT);
        CREATE TABLE fill_validity_adjustments (
          fill_id TEXT, effective_status TEXT
        );
        INSERT INTO signals VALUES ('signal', 'condition', 'token', 'Busan', '2026-07-09', '30');
        INSERT INTO plans VALUES ('plan', 'signal');
        INSERT INTO orders VALUES
          ('canonical', 'plan', 'polymarket_clob', 'submitted', 'BUY_NO', '{}'),
          ('alias', 'plan', 'polymarket_clob', 'submitted', 'BUY_NO', '{}');
        INSERT INTO fills VALUES
          ('keep', 'canonical', 'order-1', 1, 0.5, 0, 'legacy_unknown', 'filled', '2026-07-09T00:00:00Z'),
          ('excluded', 'canonical', 'order-2', 1, 0.5, 0, 'legacy_unknown', 'filled', '2026-07-09T00:01:00Z'),
          ('alias-fill', 'alias', 'order-3', 1, 0.5, 0, 'legacy_unknown', 'filled', '2026-07-09T00:02:00Z');
        INSERT INTO order_execution_aliases VALUES ('alias');
        INSERT INTO fill_validity_adjustments VALUES ('excluded', 'excluded');
        """
    )
    assert [row["fill_id"] for row in _load_rows(conn)] == ["keep"]
