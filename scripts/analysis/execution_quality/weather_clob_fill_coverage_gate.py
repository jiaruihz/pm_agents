#!/usr/bin/env python3
"""Fail-closed consistency gate for weather live CLOB fills.

This script is intentionally read-only. It checks whether recovered CLOB fills
are internally safe enough to use as the basis for live_real strategy PnL.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.ids import make_fill_id  # noqa: E402


def _round(value: float) -> float:
    return round(float(value or 0.0), 6)


def load_order_caps(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          o.execution_id,
          o.order_id,
          o.shares,
          o.limit_price,
          o.posted_price,
          o.cost_usd,
          o.notional,
          json_extract(o.exchange_response, '$.place.makingAmount') AS exchange_cost,
          json_extract(o.exchange_response, '$.place.takingAmount') AS exchange_shares,
          json_extract(o.exchange_response, '$.maker_only') AS maker_only,
          lower(COALESCE(json_extract(o.exchange_response, '$.place.status'), '')) AS place_status,
          NULL AS city,
          NULL AS target_date,
          NULL AS bracket
        FROM orders o
        LEFT JOIN order_execution_aliases alias
          ON alias.alias_execution_id = o.execution_id
        WHERE o.venue='polymarket_clob'
          AND o.status='submitted'
          AND alias.alias_execution_id IS NULL
        """
    ).fetchall()
    out = {}
    for row in rows:
        exchange_cost = 0.0
        exchange_shares = 0.0
        try:
            exchange_cost = float(row["exchange_cost"] or 0.0)
            exchange_shares = float(row["exchange_shares"] or 0.0)
        except (TypeError, ValueError):
            exchange_cost = 0.0
            exchange_shares = 0.0
        shares = float(row["shares"] or 0.0)
        limit_price = float(row["posted_price"] or row["limit_price"] or 0.0)
        out[(str(row["execution_id"]), str(row["order_id"]))] = {
            "shares": float(row["shares"] or 0.0),
            "limit_price": float(row["limit_price"] or 0.0),
            "max_shares": max(shares, exchange_shares),
            "max_cost": max(
                shares * limit_price,
                float(row["cost_usd"] or 0.0),
                float(row["notional"] or 0.0),
                exchange_cost,
            ),
            "notional": float(row["notional"] or 0.0),
            "city": row["city"],
            "target_date": row["target_date"],
            "bracket": row["bracket"],
            "maker_only": bool(row["maker_only"]),
            "place_status": str(row["place_status"] or ""),
        }
    return out


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    order_caps: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    by_execution: dict[str, dict[str, float]] = defaultdict(
        lambda: {"fills": 0.0, "shares": 0.0, "cost": 0.0}
    )
    by_order: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"fills": 0.0, "shares": 0.0, "cost": 0.0}
    )
    missing_order_rows = 0
    missing_order_cost = 0.0
    physical_keys: dict[tuple[str, str, float, float], list[str]] = defaultdict(list)
    synthetic_fill_ids: list[str] = []
    for row in rows:
        execution_id = str(row.get("execution_id") or "")
        order_id = str(row.get("order_id") or "")
        shares = float(row.get("filled_shares") or 0.0)
        price = float(row.get("filled_price") or 0.0)
        cost = shares * price
        filled_at = str(row.get("filled_at_utc") or "")
        physical_keys[(order_id, filled_at, _round(shares), _round(price))].append(
            str(row.get("fill_id") or "")
        )
        if execution_id and str(row.get("fill_id") or "") == make_fill_id(execution_id=execution_id):
            synthetic_fill_ids.append(str(row.get("fill_id") or ""))
        by_execution[execution_id]["fills"] += 1.0
        by_execution[execution_id]["shares"] += shares
        by_execution[execution_id]["cost"] += cost
        key = (execution_id, order_id)
        if key not in order_caps:
            missing_order_rows += 1
            missing_order_cost += cost
            continue
        by_order[key]["fills"] += 1.0
        by_order[key]["shares"] += shares
        by_order[key]["cost"] += cost

    over_order = []
    for key, agg in by_order.items():
        cap = order_caps[key]
        max_shares = cap["max_shares"]
        max_cost = cap["max_cost"]
        shares_over = agg["shares"] - max_shares
        cost_over = agg["cost"] - max_cost
        if shares_over > 1e-6 or cost_over > 0.05:
            over_order.append(
                {
                    "execution_id": key[0],
                    "order_id": key[1],
                    "city": cap["city"],
                    "target_date": cap["target_date"],
                    "bracket": cap["bracket"],
                    "fills": int(agg["fills"]),
                    "fill_shares": _round(agg["shares"]),
                    "order_shares": _round(max_shares),
                    "fill_cost": _round(agg["cost"]),
                    "order_limit_cost": _round(max_cost),
                    "shares_over": _round(max(shares_over, 0.0)),
                    "cost_over": _round(max(cost_over, 0.0)),
                }
            )
    over_order.sort(key=lambda row: (row["cost_over"], row["shares_over"]), reverse=True)
    total_cost = sum(float(row.get("filled_shares") or 0.0) * float(row.get("filled_price") or 0.0) for row in rows)
    duplicate_physical = [
        {
            "order_id": key[0],
            "filled_at_utc": key[1],
            "filled_shares": key[2],
            "filled_price": key[3],
            "fill_ids": fill_ids,
        }
        for key, fill_ids in physical_keys.items()
        if len(fill_ids) > 1
    ]
    return {
        "rows": len(rows),
        "distinct_fill_ids": len({str(row.get("fill_id") or "") for row in rows if row.get("fill_id")}),
        "distinct_executions": len(by_execution),
        "cost_usd": _round(total_cost),
        "multi_fill_executions": sum(1 for agg in by_execution.values() if agg["fills"] > 1),
        "missing_order_rows": missing_order_rows,
        "missing_order_cost_usd": _round(missing_order_cost),
        "over_order_keys": len(over_order),
        "over_order_cost_usd": _round(sum(float(row["fill_cost"]) for row in over_order)),
        "over_order_excess_cost_usd": _round(sum(float(row["cost_over"]) for row in over_order)),
        "top_over_order": over_order[:25],
        "synthetic_fill_rows": len(synthetic_fill_ids),
        "sample_synthetic_fill_ids": synthetic_fill_ids[:25],
        "duplicate_physical_keys": len(duplicate_physical),
        "sample_duplicate_physical_keys": duplicate_physical[:25],
    }


def load_db_fill_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              f.fill_id,
              f.execution_id,
              f.order_id,
              f.filled_shares,
              f.filled_price,
              f.filled_at_utc
            FROM fills f
            JOIN orders o ON o.execution_id = f.execution_id
            LEFT JOIN order_execution_aliases alias
              ON alias.alias_execution_id = o.execution_id
            LEFT JOIN fill_validity_adjustments validity
              ON validity.fill_id = f.fill_id
            WHERE o.venue='polymarket_clob'
              AND o.status='submitted'
              AND alias.alias_execution_id IS NULL
              AND COALESCE(validity.effective_status, 'valid') <> 'excluded'
            """
        )
    ]


def load_effective_db_fill_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              f.fill_id,
              f.execution_id,
              f.order_id,
              f.filled_shares,
              COALESCE(adj.corrected_filled_price, f.filled_price) AS filled_price,
              f.filled_at_utc
            FROM fills f
            JOIN orders o ON o.execution_id = f.execution_id
            LEFT JOIN fill_price_adjustments adj ON adj.fill_id = f.fill_id
            LEFT JOIN order_execution_aliases alias
              ON alias.alias_execution_id = o.execution_id
            LEFT JOIN fill_validity_adjustments validity
              ON validity.fill_id = f.fill_id
            WHERE o.venue='polymarket_clob'
              AND o.status='submitted'
              AND alias.alias_execution_id IS NULL
              AND COALESCE(validity.effective_status, 'valid') <> 'excluded'
            """
        )
    ]


def load_cache_rows(
    path: Path,
    *,
    aliased_execution_ids: set[str] | None = None,
    excluded_fill_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Load effective cache fills.

    Keyword filters are optional for backwards compatibility with downstream
    reports. Canonical callers should obtain them from ``load_cache_filters``.
    """
    aliased_execution_ids = aliased_execution_ids or set()
    excluded_fill_ids = excluded_fill_ids or set()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if str(row.get("execution_id") or "") not in aliased_execution_ids:
                if str(row.get("fill_id") or "") not in excluded_fill_ids:
                    rows.append(row)
    return rows


def load_cache_filters(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Return the canonical alias/validity filters used by every cache consumer."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    aliased_execution_ids = (
        {
            str(row[0])
            for row in conn.execute(
                "SELECT alias_execution_id FROM order_execution_aliases"
            ).fetchall()
        }
        if "order_execution_aliases" in tables
        else set()
    )
    excluded_fill_ids = (
        {
            str(row[0])
            for row in conn.execute(
                """
                SELECT fill_id
                FROM fill_validity_adjustments
                WHERE effective_status='excluded'
                """
            ).fetchall()
        }
        if "fill_validity_adjustments" in tables
        else set()
    )
    return {
        "aliased_execution_ids": aliased_execution_ids,
        "excluded_fill_ids": excluded_fill_ids,
    }


def _fill_id_set(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("fill_id") or "") for row in rows if row.get("fill_id")}


def _cost(rows: list[dict[str, Any]]) -> float:
    return sum(
        float(row.get("filled_shares") or 0.0) * float(row.get("filled_price") or 0.0)
        for row in rows
    )


def fact_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS rows,
          COUNT(DISTINCT fill_id) AS fill_ids,
          COUNT(DISTINCT execution_id) AS executions,
          COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM fact_trades
        WHERE trade_class='live_real'
        """
    ).fetchone()
    return {
        "rows": int(row["rows"] or 0),
        "fill_ids": int(row["fill_ids"] or 0),
        "executions": int(row["executions"] or 0),
        "cost_usd": _round(row["cost_usd"] or 0.0),
    }


def order_identity_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        WITH ranked_orders AS (
          SELECT
            execution_id,
            order_id,
            ROW_NUMBER() OVER (
              PARTITION BY order_id
              ORDER BY created_at_utc, execution_id
            ) AS identity_rank,
            COUNT(*) OVER (PARTITION BY order_id) AS identity_rows
          FROM orders
          WHERE venue='polymarket_clob' AND COALESCE(order_id, '') <> ''
        ),
        duplicate_orders AS (
          SELECT DISTINCT order_id
          FROM ranked_orders
          WHERE identity_rows > 1
        ),
        unresolved AS (
          SELECT ranked.execution_id
          FROM ranked_orders ranked
          WHERE ranked.identity_rows > 1
          AND ranked.identity_rank > 1
          AND NOT EXISTS (
            SELECT 1
            FROM order_execution_aliases a
            WHERE a.alias_execution_id=ranked.execution_id
          )
        )
        SELECT
          (SELECT COUNT(*) FROM duplicate_orders),
          (SELECT COUNT(*) FROM order_execution_aliases),
          (SELECT COUNT(*) FROM unresolved),
          (SELECT COUNT(*) FROM fills f JOIN order_execution_aliases a
             ON a.alias_execution_id=f.execution_id),
          (SELECT COALESCE(SUM(f.filled_shares*f.filled_price), 0.0)
             FROM fills f JOIN order_execution_aliases a
             ON a.alias_execution_id=f.execution_id)
        """
    ).fetchone()
    return {
        "duplicate_physical_order_ids": int(row[0] or 0),
        "resolved_alias_executions": int(row[1] or 0),
        "unresolved_alias_executions": int(row[2] or 0),
        "excluded_alias_fill_rows": int(row[3] or 0),
        "excluded_alias_fill_cost_usd": _round(row[4] or 0.0),
    }


def fee_lineage_summary(
    conn: sqlite3.Connection,
    *,
    order_caps: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    has_adjustments = "fill_fee_adjustments" in tables
    has_validity_adjustments = "fill_validity_adjustments" in tables
    fill_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(fills)").fetchall()
    }
    base_source_expr = "f.fee_source" if "fee_source" in fill_columns else "'legacy_unknown'"
    adjustment_cte = (
        "SELECT fill_id, SUM(fee_delta_usd) AS fee_delta_usd, COUNT(*) AS adjustment_rows, "
        "MAX(fee_source) AS fee_source, MAX(fee_evidence_class) AS fee_evidence_class "
        "FROM fill_fee_adjustments GROUP BY fill_id"
        if has_adjustments
        else "SELECT NULL AS fill_id, 0.0 AS fee_delta_usd, 0 AS adjustment_rows, "
        "NULL AS fee_source, NULL AS fee_evidence_class WHERE 0"
    )
    validity_join = (
        "LEFT JOIN fill_validity_adjustments validity ON validity.fill_id = f.fill_id"
        if has_validity_adjustments
        else ""
    )
    validity_filter = (
        "AND COALESCE(validity.effective_status, 'valid') <> 'excluded'"
        if has_validity_adjustments
        else ""
    )
    order_caps = order_caps if order_caps is not None else load_order_caps(conn)
    rows = conn.execute(
        f"""
        WITH fee_adj AS ({adjustment_cte})
        SELECT
          f.fill_id,
          f.execution_id,
          f.order_id,
          f.filled_shares,
          f.filled_price,
          f.fees_usd AS base_fee_usd,
          COALESCE(fee_adj.fee_delta_usd, 0.0) AS fee_adjustment_usd,
          f.fees_usd + COALESCE(fee_adj.fee_delta_usd, 0.0) AS effective_fee_usd,
          COALESCE(fee_adj.adjustment_rows, 0) AS adjustment_rows,
          COALESCE(fee_adj.fee_source, {base_source_expr}) AS effective_fee_source,
          fee_adj.fee_evidence_class
        FROM fills f
        LEFT JOIN fee_adj ON fee_adj.fill_id = f.fill_id
        {validity_join}
        WHERE f.status IN ('filled', 'partial')
          {validity_filter}
        ORDER BY f.filled_at_utc, f.fill_id
        """
    ).fetchall()
    counts = {"exact": 0, "maker_zero": 0, "estimate": 0, "unknown": 0}
    unknown_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    eligible_rows = 0
    exact_sources = {
        "authenticated_taker_fee",
        "public_activity_tx_exact",
        "public_activity_cash_delta_exact",
    }
    for raw_row in rows:
        row = dict(raw_row)
        cap = order_caps.get((str(row.get("execution_id") or ""), str(row.get("order_id") or "")))
        if cap is None:
            continue
        eligible_rows += 1
        row.update(
            {
                "city": cap.get("city"),
                "target_date": cap.get("target_date"),
                "maker_only": bool(cap.get("maker_only")),
                "place_status": str(cap.get("place_status") or ""),
            }
        )
        source = str(row.get("effective_fee_source") or "legacy_unknown")
        evidence_class = str(row.get("fee_evidence_class") or "")
        maker_only = bool(row.get("maker_only"))
        if source == "maker_zero":
            category = "maker_zero"
        elif source == "weather_fee_curve_estimate" or evidence_class == "estimate":
            category = "estimate"
        elif source in exact_sources or evidence_class == "exact":
            category = "exact"
        else:
            category = "unknown"
        counts[category] += 1
        if category == "unknown":
            unknown_rows.append(row)
        if (category == "maker_zero" and not maker_only) or (
            category == "estimate" and maker_only
        ):
            invalid_rows.append(row)
    return {
        "rows": eligible_rows,
        "lineage_counts": counts,
        "unknown_fee_lineage_rows": len(unknown_rows),
        "sample_unknown_fee_lineage_rows": unknown_rows[:25],
        "invalid_maker_taker_lineage_rows": len(invalid_rows),
        "sample_invalid_maker_taker_lineage_rows": invalid_rows[:25],
        "known_matched_taker_zero_fee_without_adjustment": sum(
            1
            for row in unknown_rows
            if row.get("place_status") == "matched" and not bool(row.get("maker_only"))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "runtime" / "weather_edge_v1" / "clob_fills.jsonl",
    )
    parser.add_argument("--extra-cache", type=Path, action="append", default=[])
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    order_caps = load_order_caps(conn)
    raw_db_rows = load_db_fill_rows(conn)
    db_rows = load_effective_db_fill_rows(conn)
    db_fills = summarize_rows(db_rows, order_caps=order_caps)
    db_fill_ids = _fill_id_set(raw_db_rows)
    facts = fact_summary(conn)
    order_identity = order_identity_summary(conn)
    fee_lineage = fee_lineage_summary(conn, order_caps=order_caps)
    cache_filters = load_cache_filters(conn)
    payload: dict[str, Any] = {
        "db": str(args.db),
        "order_caps": len(order_caps),
        "db_fills": db_fills,
        "fact_trades_live_real": facts,
        "order_identity": order_identity,
        "fee_lineage": fee_lineage,
        "db_fill_cost_minus_fact_cost": _round(db_fills["cost_usd"] - facts["cost_usd"]),
        "db_vs_primary_cache": None,
        "cache_checks": {},
        "gate_pass": False,
        "fail_reasons": [],
    }
    cache_paths = [args.cache, *args.extra_cache]
    for index, cache_path in enumerate(cache_paths):
        cache_rows = load_cache_rows(
            cache_path,
            **cache_filters,
        )
        cache_summary = summarize_rows(cache_rows, order_caps=order_caps)
        payload["cache_checks"][str(cache_path)] = cache_summary

        if cache_summary["missing_order_rows"]:
            payload["fail_reasons"].append(
                f"cache_fills_missing_or_mismatched_order_id:{cache_path}"
            )
        if cache_summary["over_order_keys"]:
            payload["fail_reasons"].append(f"cache_fills_exceed_order_cap:{cache_path}")
        if cache_summary["synthetic_fill_rows"]:
            payload["fail_reasons"].append(f"cache_contains_synthetic_runtime_fills:{cache_path}")
        if cache_summary["duplicate_physical_keys"]:
            payload["fail_reasons"].append(f"cache_contains_duplicate_physical_fills:{cache_path}")

        if index == 0:
            cache_fill_ids = _fill_id_set(cache_rows)
            db_not_in_cache = sorted(db_fill_ids - cache_fill_ids)
            cache_not_in_db = sorted(cache_fill_ids - db_fill_ids)
            cost_delta = _round(_cost(raw_db_rows) - _cost(cache_rows))
            payload["db_vs_primary_cache"] = {
                "db_not_in_cache": len(db_not_in_cache),
                "cache_not_in_db": len(cache_not_in_db),
                "db_cost_minus_cache_cost": cost_delta,
                "sample_db_not_in_cache": db_not_in_cache[:25],
                "sample_cache_not_in_db": cache_not_in_db[:25],
            }
            if db_not_in_cache or cache_not_in_db:
                payload["fail_reasons"].append("db_cache_fill_id_mismatch")
            if abs(cost_delta) > 0.01:
                payload["fail_reasons"].append("db_cache_cost_mismatch")

    if db_fills["missing_order_rows"]:
        payload["fail_reasons"].append("db_fills_missing_or_mismatched_order_id")
    if db_fills["over_order_keys"]:
        payload["fail_reasons"].append("db_fills_exceed_order_cap")
    if db_fills["synthetic_fill_rows"]:
        payload["fail_reasons"].append("db_contains_synthetic_runtime_fills")
    if db_fills["duplicate_physical_keys"]:
        payload["fail_reasons"].append("db_contains_duplicate_physical_fills")
    if abs(float(payload["db_fill_cost_minus_fact_cost"])) > 0.01:
        payload["fail_reasons"].append("fact_trades_cost_not_equal_fills_cost")
    if order_identity["unresolved_alias_executions"]:
        payload["fail_reasons"].append("unresolved_duplicate_physical_order_executions")
    if fee_lineage["known_matched_taker_zero_fee_without_adjustment"]:
        payload["fail_reasons"].append(
            "known_matched_taker_fills_have_zero_fee_without_adjustment"
        )
    if fee_lineage["unknown_fee_lineage_rows"]:
        payload["fail_reasons"].append("clob_fills_have_unknown_fee_lineage")
    if fee_lineage["invalid_maker_taker_lineage_rows"]:
        payload["fail_reasons"].append("clob_fills_have_invalid_maker_taker_fee_lineage")
    payload["gate_pass"] = not payload["fail_reasons"]

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
