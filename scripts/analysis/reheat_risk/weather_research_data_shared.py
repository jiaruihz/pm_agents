"""Shared read-only data checks used by weather research runners."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def _query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [description[0] for description in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def data_self_check(db: Path) -> dict[str, Any]:
    conn = _connect_ro(db)
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute(
                "SELECT MAX(fact_built_at_utc) FROM fact_trades"
            ).fetchone()[0],
            "fact_trades_by_class": _query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades "
                "GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": _query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, "
                "COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status "
                "ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": _query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, "
                "SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled "
                "FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": _query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def load_fill_coverage_gate(gate: Path) -> dict[str, Any]:
    if not gate.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(gate.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def load_buy_yes_forecast_prior(db: Path) -> pd.DataFrame:
    conn = _connect_ro(db)
    try:
        rows = _query_rows(
            conn,
            """
            SELECT city, event_date AS target_date, bracket,
                   AVG(model_p_yes) AS raw_model_p_yes,
                   COUNT(*) AS prior_rows
            FROM fact_signal_candidates
            WHERE side='BUY_YES' AND model_p_yes IS NOT NULL
            GROUP BY city, event_date, bracket
            """,
        )
    finally:
        conn.close()
    return pd.DataFrame(rows)
