from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from scripts.analysis.account_reconcile import weather_live_account_reconcile as reconcile


def test_current_instance_id_precedes_legacy_fallback() -> None:
    conn = sqlite3.connect(":memory:")
    row = conn.execute(
        f"""
        SELECT {reconcile.INSTANCE_CASE} AS strategy_instance
        FROM (
          SELECT 'current_live_v1' AS instance_id,
                 'run_mid_price_core_v1_25_75' AS run_id,
                 'mid_price_core_v1' AS execution_policy,
                 '0.25-0.75' AS entry_price_window,
                 'legacy_strategy' AS strategy_id
        )
        """
    ).fetchone()
    assert row == ("current_live_v1",)


def test_resolve_raw_order_sources_uses_production_desired_state(
    monkeypatch, tmp_path: Path
) -> None:
    journal = tmp_path / "current" / "orders.jsonl"
    runtime = SimpleNamespace(
        expected_live=True,
        live_order_path=journal,
        checkout_root=None,
        instance_id="current_live_v1",
    )
    ignored = SimpleNamespace(
        expected_live=False,
        live_order_path=tmp_path / "shadow.jsonl",
        checkout_root=None,
        instance_id="shadow_v1",
    )
    spec = SimpleNamespace(
        managed_runtimes=[runtime, ignored],
        operational_repo_root=tmp_path,
    )
    monkeypatch.setattr(reconcile, "load_production_spec", lambda: spec)

    assert reconcile.resolve_raw_order_sources() == (
        reconcile.RawOrderSource("current_live_v1", journal),
    )


def test_raw_order_summary_reads_exact_journals_across_roots(tmp_path: Path) -> None:
    first = tmp_path / "one" / "live_orders.jsonl"
    second = tmp_path / "two" / "orders.jsonl"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(
        json.dumps(
            {
                "created_at_utc": "2026-08-08T16:30:00Z",
                "status": "submitted",
                "notional": 5.0,
                "posted_notional": 4.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "ts_utc": "2026-08-08T17:00:00Z",
                "live_attempt_ts_utc": "2026-08-08T17:00:01Z",
                "live_attempted": True,
                "live_submit_status": "submitted",
                "submitted_notional_usd": 3.0,
                "live_order_posted": True,
            }
        )
        + "\n"
        + json.dumps(
            {
                "created_at_utc": "2026-08-08T17:10:00Z",
                "status": "filled",
                "child_order_role": "core_carry_maker_terminal",
                "posted_notional": 3.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = reconcile.raw_order_summary(
        (
            reconcile.RawOrderSource("live_one", first),
            reconcile.RawOrderSource("live_two", second),
        ),
        "2026-08-09",
        "2026-08-09",
    )

    assert summary["existing_files"] == 2
    assert summary["rows"] == 3
    assert summary["range_rows"] == 2
    assert summary["range_submitted_notional_usd"] == 8.0
    assert summary["range_posted_notional_usd"] == 7.0
    assert {row["strategy_instance"] for row in summary["by_created_date_bj"]} == {
        "live_one",
        "live_two",
    }


def test_connect_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "weather.db"
    writer = sqlite3.connect(db)
    writer.execute("CREATE TABLE sample(value INTEGER)")
    writer.commit()
    writer.close()

    conn = reconcile.connect(db)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        try:
            conn.execute("INSERT INTO sample VALUES (1)")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc).lower()
        else:
            raise AssertionError("read-only reconcile connection accepted a write")
    finally:
        conn.close()


def test_order_reconcile_does_not_duplicate_partial_fill_orders() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE orders (
          execution_id TEXT,
          instance_id TEXT,
          run_id TEXT,
          placed_at_utc TEXT,
          venue TEXT,
          cost_usd REAL,
          status TEXT
        );
        CREATE TABLE fills (
          execution_id TEXT,
          filled_at_utc TEXT,
          filled_price REAL,
          filled_shares REAL,
          status TEXT
        );
        INSERT INTO orders VALUES (
          'exec-1', 'current_live_v1', 'run-1', '2026-08-09T01:00:00Z',
          'polymarket_clob', 5.0, 'submitted'
        );
        INSERT INTO fills VALUES
          ('exec-1', '2026-08-09T01:01:00Z', 0.50, 4.0, 'filled'),
          ('exec-1', '2026-08-09T01:02:00Z', 0.50, 6.0, 'filled');
        """
    )
    args = reconcile.Args(
        db=Path("unused"),
        clob_fills=Path("unused"),
        raw_order_sources=(),
        start="2026-08-09",
        end="2026-08-09",
        date_field="fill_date_utc",
        instances=("all",),
        group_by=("instance",),
        format="json",
    )

    rows = reconcile.aggregate_orders(conn, args)

    assert rows == [
        {
            "selected_date": "2026-08-09",
            "strategy_instance": "current_live_v1",
            "status": "submitted",
            "orders": 1,
            "submitted_or_error_cost_usd": 5.0,
            "filled_orders": 1,
            "actual_fill_cost_usd": 5.0,
        }
    ]
