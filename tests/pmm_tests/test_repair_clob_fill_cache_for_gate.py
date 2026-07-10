import sqlite3

from scripts.ops.repair_clob_fill_cache_for_gate import load_order_caps, repair_rows


def _setup_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE signals (
          signal_id TEXT PRIMARY KEY,
          city TEXT,
          target_date TEXT,
          bracket TEXT
        );
        CREATE TABLE plans (
          plan_id TEXT PRIMARY KEY,
          signal_id TEXT
        );
        CREATE TABLE orders (
          execution_id TEXT,
          order_id TEXT,
          plan_id TEXT,
          venue TEXT,
          status TEXT,
          shares REAL,
          limit_price REAL,
          cost_usd REAL,
          notional REAL,
          exchange_response TEXT
        );
        """
    )
    conn.execute("INSERT INTO signals VALUES ('s1', 'Miami', '2026-06-05', '82-83')")
    conn.execute("INSERT INTO plans VALUES ('p1', 's1')")
    conn.execute(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "exec-1",
            "order-1",
            "p1",
            "polymarket_clob",
            "submitted",
            5.0,
            0.40,
            2.0,
            2.0,
            "{}",
        ),
    )
    conn.commit()
    conn.close()


def _fill(fill_id, shares, *, source="public_activity_fallback", execution_id="exec-1", order_id="order-1"):
    return {
        "fill_id": fill_id,
        "execution_id": execution_id,
        "order_id": order_id,
        "filled_shares": shares,
        "filled_price": 0.40,
        "filled_at_utc": f"2026-06-05T00:00:0{fill_id[-1]}Z",
        "source": source,
        "public_trade_key": fill_id if source == "public_activity_fallback" else None,
    }


def test_repair_rows_drops_stale_over_cap_and_duplicate_fills(tmp_path):
    db_path = tmp_path / "weather.db"
    _setup_db(db_path)
    caps = load_order_caps(db_path)

    rows = [
        _fill("fill-1", 3.0, source="order_exchange_response_matched"),
        _fill("fill-1", 3.0, source="public_activity_fallback"),
        _fill("fill-2", 3.0, source="public_activity_fallback"),
        _fill("fill-3", 1.0, execution_id="stale", order_id="stale-order"),
    ]

    repaired, summary = repair_rows(rows, caps)

    assert [row["fill_id"] for row in repaired] == ["fill-1"]
    assert summary["input_rows"] == 4
    assert summary["output_rows"] == 1
    assert summary["dropped_stale_order_rows"] == 1
    assert summary["dropped_duplicate_fill_id_rows"] == 1
    assert summary["dropped_over_cap_rows"] == 1
    assert summary["sample_dropped_over_cap"][0]["fill_id"] == "fill-2"


def test_repair_rows_dedupes_same_physical_fill_with_different_ids(tmp_path):
    db_path = tmp_path / "weather.db"
    _setup_db(db_path)
    caps = load_order_caps(db_path)
    first = _fill("fill-1", 3.0, source="order_exchange_response_matched")
    second = {**first, "fill_id": "fill-2", "source": "public_activity_fallback"}

    repaired, summary = repair_rows([first, second], caps)

    assert [row["fill_id"] for row in repaired] == ["fill-1"]
    assert summary["dropped_duplicate_physical_key_rows"] == 1
