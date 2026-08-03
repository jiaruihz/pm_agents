import sqlite3
import json

from scripts.analysis.execution_quality.weather_clob_fill_coverage_gate import (
    fee_lineage_summary,
    load_cache_filters,
    load_cache_rows,
    order_identity_summary,
    summarize_rows,
)
from src.strategies.weather_edge_v1.ids import make_fill_id


def test_fill_gate_detects_synthetic_and_physical_duplicates() -> None:
    execution_id = "a" * 64
    base = {
        "execution_id": execution_id,
        "order_id": "0xorder",
        "filled_shares": 5.0,
        "filled_price": 0.5,
        "filled_at_utc": "2026-07-10T01:00:00Z",
    }
    rows = [
        {**base, "fill_id": make_fill_id(execution_id=execution_id)},
        {**base, "fill_id": "real-fill"},
    ]
    caps = {
        (execution_id, "0xorder"): {
            "max_shares": 10.0,
            "max_cost": 5.0,
            "city": "Paris",
            "target_date": "2026-07-10",
            "bracket": "30",
        }
    }

    summary = summarize_rows(rows, order_caps=caps)

    assert summary["synthetic_fill_rows"] == 1
    assert summary["duplicate_physical_keys"] == 1


def test_fill_gate_requires_fee_or_adjustment_for_known_matched_taker() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE orders (
          execution_id TEXT, plan_id TEXT, venue TEXT, status TEXT,
          order_side TEXT, exchange_response TEXT
        );
        CREATE TABLE plans (plan_id TEXT, signal_id TEXT);
        CREATE TABLE signals (signal_id TEXT, city TEXT, target_date TEXT);
        CREATE TABLE fills (
          fill_id TEXT, execution_id TEXT, order_id TEXT, filled_shares REAL,
          filled_price REAL, fees_usd REAL, status TEXT, filled_at_utc TEXT
        );
        CREATE TABLE fill_fee_adjustments (
          adjustment_id TEXT, fill_id TEXT, fee_delta_usd REAL,
          fee_source TEXT, fee_evidence_class TEXT
        );
        INSERT INTO signals VALUES ('signal', 'Busan', '2026-07-09');
        INSERT INTO plans VALUES ('plan', 'signal');
        INSERT INTO orders VALUES (
          'exec', 'plan', 'polymarket_clob', 'submitted', 'BUY_NO',
          '{"maker_only":false,"place":{"status":"matched"}}'
        );
        INSERT INTO fills VALUES ('fill', 'exec', 'order', 5, 0.67, 0, 'filled', '2026-07-09T03:51:47Z');
        """
    )

    assert fee_lineage_summary(conn)["known_matched_taker_zero_fee_without_adjustment"] == 1
    conn.execute(
        "INSERT INTO fill_fee_adjustments VALUES "
        "('adj', 'fill', 0.05527, 'weather_fee_curve_estimate', 'estimate')"
    )
    assert fee_lineage_summary(conn)["known_matched_taker_zero_fee_without_adjustment"] == 0
    assert fee_lineage_summary(conn)["lineage_counts"]["estimate"] == 1


def test_cache_loader_keeps_backwards_compatibility_and_uses_canonical_filters(
    tmp_path,
) -> None:
    cache = tmp_path / "fills.jsonl"
    cache.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"fill_id": "keep", "execution_id": "canonical"},
                {"fill_id": "alias-fill", "execution_id": "alias"},
                {"fill_id": "excluded", "execution_id": "canonical"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert len(load_cache_rows(cache)) == 3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE order_execution_aliases (alias_execution_id TEXT);
        CREATE TABLE fill_validity_adjustments (
          fill_id TEXT, effective_status TEXT
        );
        INSERT INTO order_execution_aliases VALUES ('alias');
        INSERT INTO fill_validity_adjustments VALUES ('excluded', 'excluded');
        """
    )
    assert load_cache_rows(cache, **load_cache_filters(conn)) == [
        {"fill_id": "keep", "execution_id": "canonical"}
    ]


def test_order_identity_summary_resolves_duplicate_without_correlated_order_scan() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE orders (
          execution_id TEXT PRIMARY KEY, order_id TEXT, venue TEXT,
          created_at_utc TEXT
        );
        CREATE TABLE order_execution_aliases (
          alias_execution_id TEXT PRIMARY KEY, physical_order_id TEXT
        );
        CREATE TABLE fills (
          execution_id TEXT, filled_shares REAL, filled_price REAL
        );
        INSERT INTO orders VALUES
          ('canonical', 'physical', 'polymarket_clob', '2026-01-01T00:00:00Z'),
          ('duplicate', 'physical', 'polymarket_clob', '2026-01-02T00:00:00Z'),
          ('other', 'other-order', 'polymarket_clob', '2026-01-01T00:00:00Z');
        """
    )

    unresolved = order_identity_summary(conn)
    assert unresolved["duplicate_physical_order_ids"] == 1
    assert unresolved["unresolved_alias_executions"] == 1

    conn.execute(
        "INSERT INTO order_execution_aliases VALUES ('duplicate', 'physical')"
    )
    resolved = order_identity_summary(conn)
    assert resolved["unresolved_alias_executions"] == 0
