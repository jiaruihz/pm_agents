#!/usr/bin/env python3
"""Reconcile weather live cash usage, settled PnL, and open exposure.

This script intentionally separates:
- cash cost / submitted order notional
- settled realized PnL
- unsettled exposure valuation

It is the right entrypoint when a wallet balance changed and the question is
not just settled strategy performance.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime" / "weather.db"
DEFAULT_CLOB_FILLS = ROOT / "runtime" / "weather_edge_v1" / "clob_fills.jsonl"
DEFAULT_RAW_LIVE_DIR = ROOT / "runtime" / "weather_edge_v1" / "remote_pm_agent" / "live"
DEFAULT_COVERAGE_GATE = ROOT / "scripts" / "analysis" / "execution_quality" / "weather_clob_fill_coverage_gate.py"


INSTANCE_CASE = """
CASE
  WHEN run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65') THEN 'mid_price_core_v1_side_band'
  ELSE COALESCE(strategy_id, 'unknown')
END
"""

ORDER_INSTANCE_CASE = """
CASE
  WHEN run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  ELSE 'unknown'
END
"""


DATE_EXPR = {
    "target_date": "target_date",
    "order_date_bj": "order_date_bj",
    "fill_date_utc": "substr(fill_ts_utc, 1, 10)",
    "fill_date_bj": "date(fill_ts_utc, '+8 hours')",
}


GROUP_EXPR = {
    "selected_date": "selected_date",
    "instance": "strategy_instance",
    "target_date": "target_date",
    "order_date_bj": "order_date_bj",
    "fill_date_utc": "fill_date_utc",
    "city": "city",
    "side": "side",
    "bracket": "bracket",
    "execution_policy": "execution_policy",
    "entry_price_window": "entry_price_window",
}


@dataclass(frozen=True)
class Args:
    db: Path
    clob_fills: Path
    raw_live_dir: Path
    start: str
    end: str
    date_field: str
    instances: tuple[str, ...]
    group_by: tuple[str, ...]
    format: str


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def instance_filter(instances: tuple[str, ...]) -> tuple[str, list[str]]:
    if not instances or instances == ("all",):
        return "", []
    placeholders = ",".join("?" for _ in instances)
    return f" AND strategy_instance IN ({placeholders})", list(instances)


def base_cte(date_expr: str) -> str:
    return f"""
    WITH live AS (
      SELECT
        *,
        {INSTANCE_CASE} AS strategy_instance,
        {date_expr} AS selected_date,
        substr(fill_ts_utc, 1, 10) AS fill_date_utc
      FROM fact_trades
      WHERE trade_class='live_real'
    )
    """


def db_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    out = fetch_all(
        conn,
        """
        SELECT
          MAX(fact_built_at_utc) AS fact_built_at_utc,
          MAX(order_ts_utc) AS max_order_ts_utc,
          MAX(fill_ts_utc) AS max_fill_ts_utc,
          MAX(val_snapshot_ts_utc) AS max_val_snapshot_ts_utc,
          COUNT(*) AS live_real_rows,
          COUNT(DISTINCT fill_id) AS live_real_distinct_fills
        FROM fact_trades
        WHERE trade_class='live_real'
        """,
    )[0]
    out["trade_class_distribution"] = fetch_all(
        conn,
        """
        SELECT trade_class, COUNT(*) AS rows
        FROM fact_trades
        GROUP BY trade_class
        ORDER BY trade_class
        """,
    )
    out["settlement_distribution"] = fetch_all(
        conn,
        """
        SELECT trade_class, settlement_status, COUNT(*) AS rows
        FROM fact_trades
        GROUP BY trade_class, settlement_status
        ORDER BY trade_class, settlement_status
        """,
    )
    return out


def aggregate_live(conn: sqlite3.Connection, args: Args) -> list[dict[str, Any]]:
    date_expr = DATE_EXPR[args.date_field]
    filter_sql, params = instance_filter(args.instances)
    group_cols = [GROUP_EXPR[item] for item in args.group_by]
    select_cols = ", ".join(group_cols)
    group_sql = ", ".join(group_cols)
    params = [args.start, args.end, *params]
    return fetch_all(
        conn,
        f"""
        {base_cte(date_expr)}
        SELECT
          {select_cols},
          COUNT(*) AS fills,
          COUNT(DISTINCT fill_id) AS distinct_fills,
          ROUND(SUM(cost_usd), 4) AS cash_cost_usd,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          ROUND(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 4) AS settled_cost_usd,
          ROUND(SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END), 4) AS realized_pnl_usd,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          ROUND(SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END), 4) AS open_cost_usd,
          SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_fills,
          SUM(CASE WHEN settlement_status IS NULL THEN 1 ELSE 0 END) AS null_status_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN CASE WHEN val_mid IS NULL THEN 1 ELSE 0 END ELSE 0 END) AS open_missing_mid,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN CASE WHEN val_bid IS NULL THEN 1 ELSE 0 END ELSE 0 END) AS open_missing_bid,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN CASE WHEN val_last_fill IS NULL THEN 1 ELSE 0 END ELSE 0 END) AS open_missing_last_fill,
          ROUND(SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN unrealized_pnl_mid ELSE 0 END), 4) AS unrealized_pnl_mid_usd,
          ROUND(SUM(CASE
            WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN
              CASE
                WHEN val_bid IS NULL THEN NULL
                WHEN side='BUY_YES' THEN (val_bid-fill_price)*fill_qty
                WHEN side='BUY_NO' THEN ((1.0-val_bid)-fill_price)*fill_qty
              END
          END), 4) AS unrealized_pnl_bid_usd,
          ROUND(SUM(CASE
            WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN
              CASE
                WHEN val_last_fill IS NULL THEN NULL
                WHEN side='BUY_YES' THEN (val_last_fill-fill_price)*fill_qty
                WHEN side='BUY_NO' THEN ((1.0-val_last_fill)-fill_price)*fill_qty
              END
          END), 4) AS unrealized_pnl_last_fill_usd,
          MAX(val_snapshot_ts_utc) AS max_val_snapshot_ts_utc
        FROM live
        WHERE selected_date BETWEEN ? AND ?
          {filter_sql}
        GROUP BY {group_sql}
        ORDER BY cash_cost_usd DESC
        """,
        params,
    )


def aggregate_orders(conn: sqlite3.Connection, args: Args) -> list[dict[str, Any]]:
    # Orders do not carry strategy metadata directly beyond run_id. Use the same run_id suffix mapping.
    date_col = "substr(placed_at_utc, 1, 10)"
    filter_sql = ""
    params: list[Any] = [args.start, args.end]
    if args.date_field in ("order_date_bj", "placed_date_bj"):
        # Approximate BJ date from UTC string inside SQLite.
        date_col = "date(placed_at_utc, '+8 hours')"
    elif args.date_field == "fill_date_bj":
        date_col = "date(filled_at_utc, '+8 hours')"
    elif args.date_field == "target_date":
        # orders table has no target_date; return no rows rather than pretending.
        return []
    inst_filter, inst_params = instance_filter(args.instances)
    filter_sql += inst_filter
    params.extend(inst_params)
    return fetch_all(
        conn,
        f"""
        WITH o AS (
          SELECT
            orders.*,
            fills.filled_at_utc,
            fills.filled_price,
            fills.filled_shares,
            {ORDER_INSTANCE_CASE} AS strategy_instance,
            {date_col} AS selected_date
          FROM orders
          LEFT JOIN fills USING(execution_id)
          WHERE venue='polymarket_clob'
        )
        SELECT
          selected_date,
          strategy_instance,
          status,
          COUNT(*) AS orders,
          ROUND(SUM(cost_usd), 4) AS submitted_or_error_cost_usd,
          SUM(CASE WHEN filled_at_utc IS NOT NULL THEN 1 ELSE 0 END) AS filled_orders,
          ROUND(SUM(CASE WHEN filled_at_utc IS NOT NULL THEN filled_price*filled_shares ELSE 0 END), 4) AS actual_fill_cost_usd
        FROM o
        WHERE selected_date BETWEEN ? AND ?
          {filter_sql}
        GROUP BY selected_date, strategy_instance, status
        ORDER BY selected_date, strategy_instance, status
        """,
        params,
    )


def bj_date(iso_ts: str) -> str:
    if not iso_ts:
        return ""
    normalized = iso_ts.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def raw_order_summary(path: Path, start: str, end: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "exists": path.exists(),
        "files": 0,
        "rows": 0,
        "range_rows": 0,
        "range_submitted_notional_usd": 0.0,
        "range_posted_notional_usd": 0.0,
        "by_created_date_bj": [],
    }
    if not path.exists():
        return out
    by_date: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "created_date_bj": "",
            "orders": 0,
            "submitted_notional_usd": 0.0,
            "posted_notional_usd": 0.0,
        }
    )
    for file_path in sorted(path.glob("*orders.jsonl")):
        out["files"] += 1
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                out["rows"] += 1
                created_date = bj_date(str(row.get("created_at_utc") or ""))
                if start <= created_date <= end:
                    submitted = float(row.get("notional") or 0.0)
                    posted = float(row.get("posted_notional") or submitted)
                    out["range_rows"] += 1
                    out["range_submitted_notional_usd"] += submitted
                    out["range_posted_notional_usd"] += posted
                    item = by_date[created_date]
                    item["created_date_bj"] = created_date
                    item["orders"] += 1
                    item["submitted_notional_usd"] += submitted
                    item["posted_notional_usd"] += posted
    out["range_submitted_notional_usd"] = round(float(out["range_submitted_notional_usd"]), 4)
    out["range_posted_notional_usd"] = round(float(out["range_posted_notional_usd"]), 4)
    out["by_created_date_bj"] = [
        {
            "created_date_bj": k,
            "orders": v["orders"],
            "submitted_notional_usd": round(v["submitted_notional_usd"], 4),
            "posted_notional_usd": round(v["posted_notional_usd"], 4),
        }
        for k, v in sorted(by_date.items())
    ]
    return out


def raw_clob_summary(path: Path, start: str, end: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "exists": path.exists(),
        "rows": 0,
        "distinct_fill_ids": 0,
        "range_rows": 0,
        "range_cost_usd": 0.0,
        "by_fill_date_utc": [],
    }
    if not path.exists():
        return out
    fill_ids: set[str] = set()
    by_date: dict[str, dict[str, Any]] = defaultdict(lambda: {"fill_date_utc": "", "fills": 0, "cost_usd": 0.0})
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            out["rows"] += 1
            fill_id = str(row.get("fill_id") or "")
            if fill_id:
                fill_ids.add(fill_id)
            filled_at = str(row.get("filled_at_utc") or "")
            fill_date = filled_at[:10]
            price = float(row.get("filled_price") or 0.0)
            shares = float(row.get("filled_shares") or 0.0)
            cost = price * shares
            if start <= fill_date <= end:
                out["range_rows"] += 1
                out["range_cost_usd"] += cost
                item = by_date[fill_date]
                item["fill_date_utc"] = fill_date
                item["fills"] += 1
                item["cost_usd"] += cost
    out["distinct_fill_ids"] = len(fill_ids)
    out["range_cost_usd"] = round(float(out["range_cost_usd"]), 4)
    out["by_fill_date_utc"] = [
        {"fill_date_utc": k, "fills": v["fills"], "cost_usd": round(v["cost_usd"], 4)}
        for k, v in sorted(by_date.items())
    ]
    return out


def unmatched_summary(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    db_ids = {
        str(row["fill_id"])
        for row in conn.execute("SELECT fill_id FROM fact_trades WHERE trade_class='live_real'").fetchall()
    }
    raw_ids: set[str] = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    raw_ids.add(str(json.loads(line).get("fill_id") or ""))
    raw_ids.discard("")
    return {
        "db_live_real_distinct_fills": len(db_ids),
        "raw_clob_distinct_fills": len(raw_ids),
        "db_not_in_raw": len(db_ids - raw_ids),
        "raw_not_in_db": len(raw_ids - db_ids),
        "sample_db_not_in_raw": sorted(db_ids - raw_ids)[:10],
        "sample_raw_not_in_db": sorted(raw_ids - db_ids)[:10],
    }


def clob_coverage_gate(db: Path, cache: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "weather_clob_fill_coverage_gate",
        DEFAULT_COVERAGE_GATE,
    )
    if spec is None or spec.loader is None:
        return {"gate_pass": False, "fail_reasons": ["coverage_gate_import_failed"]}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    conn = sqlite3.connect(str(db))
    try:
        order_caps = module.load_order_caps(conn)
        # Keep the account report on the same effective-price basis as the
        # standalone coverage gate and fact_trades.  The raw fills table can
        # have an append-only price correction; using it here produced a
        # false coverage failure even though the canonical gate passed.
        raw_db_rows = module.load_db_fill_rows(conn)
        db_rows = module.load_effective_db_fill_rows(conn)
        db_fills = module.summarize_rows(db_rows, order_caps=order_caps)
        facts = module.fact_summary(conn)
        cache_filters = module.load_cache_filters(conn)
        cache_rows = module.load_cache_rows(cache, **cache_filters)
        cache_fills = module.summarize_rows(cache_rows, order_caps=order_caps)
        fee_lineage = module.fee_lineage_summary(conn)
        db_fill_ids = module._fill_id_set(raw_db_rows)
        cache_fill_ids = module._fill_id_set(cache_rows)
        db_not_in_cache = sorted(db_fill_ids - cache_fill_ids)
        cache_not_in_db = sorted(cache_fill_ids - db_fill_ids)
        # Cache is raw exchange evidence, so compare it to raw DB fills.  The
        # effective-price series above is instead compared to fact_trades.
        db_cost_minus_cache_cost = module._round(module._cost(raw_db_rows) - module._cost(cache_rows))
        db_fill_cost_minus_fact_cost = module._round(db_fills["cost_usd"] - facts["cost_usd"])

        fail_reasons: list[str] = []
        if db_fills["missing_order_rows"]:
            fail_reasons.append("db_fills_missing_or_mismatched_order_id")
        if db_fills["over_order_keys"]:
            fail_reasons.append("db_fills_exceed_order_cap")
        if cache_fills["missing_order_rows"]:
            fail_reasons.append("cache_fills_missing_or_mismatched_order_id")
        if cache_fills["over_order_keys"]:
            fail_reasons.append("cache_fills_exceed_order_cap")
        if db_not_in_cache or cache_not_in_db:
            fail_reasons.append("db_cache_fill_id_mismatch")
        if abs(db_cost_minus_cache_cost) > 0.01:
            fail_reasons.append("db_cache_cost_mismatch")
        if abs(db_fill_cost_minus_fact_cost) > 0.01:
            fail_reasons.append("fact_trades_cost_not_equal_fills_cost")
        if fee_lineage["known_matched_taker_zero_fee_without_adjustment"]:
            fail_reasons.append(
                "known_matched_taker_fills_have_zero_fee_without_adjustment"
            )
        if fee_lineage["unknown_fee_lineage_rows"]:
            fail_reasons.append("clob_fills_have_unknown_fee_lineage")
        if fee_lineage["invalid_maker_taker_lineage_rows"]:
            fail_reasons.append("clob_fills_have_invalid_maker_taker_fee_lineage")

        return {
            "gate_pass": not fail_reasons,
            "fail_reasons": fail_reasons,
            "order_caps": len(order_caps),
            "db_fills": {
                "rows": db_fills["rows"],
                "distinct_fill_ids": db_fills["distinct_fill_ids"],
                "cost_usd": db_fills["cost_usd"],
                "missing_order_rows": db_fills["missing_order_rows"],
                "over_order_keys": db_fills["over_order_keys"],
            },
            "cache_fills": {
                "rows": cache_fills["rows"],
                "distinct_fill_ids": cache_fills["distinct_fill_ids"],
                "cost_usd": cache_fills["cost_usd"],
                "missing_order_rows": cache_fills["missing_order_rows"],
                "over_order_keys": cache_fills["over_order_keys"],
            },
            "fact_trades_live_real": facts,
            "fee_lineage": fee_lineage,
            "db_vs_primary_cache": {
                "db_not_in_cache": len(db_not_in_cache),
                "cache_not_in_db": len(cache_not_in_db),
                "db_cost_minus_cache_cost": db_cost_minus_cache_cost,
                "sample_db_not_in_cache": db_not_in_cache[:10],
                "sample_cache_not_in_db": cache_not_in_db[:10],
            },
            "db_fill_cost_minus_fact_cost": db_fill_cost_minus_fact_cost,
        }
    finally:
        conn.close()


def render_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(lines)


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Weather Live Account Reconcile",
        "",
        "## Scope",
        "",
        render_table([result["scope"]]),
        "",
        "## DB Snapshot",
        "",
        render_table([{k: v for k, v in result["db_snapshot"].items() if not isinstance(v, list)}]),
        "",
        "## Live Real Reconcile",
        "",
        render_table(result["live_reconcile"]),
        "",
        "## CLOB Raw Cross Check",
        "",
        render_table([result["raw_clob_summary"] | {"by_fill_date_utc": "see below"}]),
        "",
        render_table(result["raw_clob_summary"].get("by_fill_date_utc", [])),
        "",
        "## Raw Live Order Files",
        "",
        render_table([result["raw_order_summary"] | {"by_created_date_bj": "see below"}]),
        "",
        render_table(result["raw_order_summary"].get("by_created_date_bj", [])),
        "",
        "## Fill ID Reconciliation",
        "",
        render_table([result["fill_id_reconciliation"]]),
        "",
        "## CLOB Fill Coverage Gate",
        "",
        render_table([result["clob_fill_coverage_gate"]]),
        "",
    ]
    if result["order_reconcile"]:
        lines += ["## Order Submitted Cost", "", render_table(result["order_reconcile"]), ""]
    lines += [
        "## Notes",
        "",
        "- `cash_cost_usd` is wallet cash spent on fills, not PnL.",
        "- `realized_pnl_usd` only counts settled fills.",
        "- `open_cost_usd` is still at risk until settlement or exit.",
        "- `fill_date_bj` is the default wallet cashflow lens; `target_date` is the weather contract date.",
        "- Do not use `fact_trades.order_date_bj` as wallet cashflow evidence; use raw live order files for submitted/posted notional.",
        "- `unrealized_pnl_*` depends on fact-table valuation freshness; check `max_val_snapshot_ts_utc` before using it as current wallet equity.",
        "- Raw CLOB fill mismatch means the DB and local raw mirror are not identical sources; inspect unmatched samples before treating either as authoritative.",
        "- `clob_fill_coverage_gate.gate_pass=false` means live_real PnL is not publishable until fill recovery is fixed and facts are rebuilt.",
    ]
    return "\n".join(lines)


def run(args: Args) -> dict[str, Any]:
    conn = connect(args.db)
    coverage = clob_coverage_gate(args.db, args.clob_fills)
    db_vs_cache = coverage.get("db_vs_primary_cache") or {}
    fill_id_reconciliation = {
        "db_live_real_distinct_fills": coverage.get("db_fills", {}).get(
            "distinct_fill_ids", 0
        ),
        "raw_clob_distinct_fills": coverage.get("cache_fills", {}).get(
            "distinct_fill_ids", 0
        ),
        "db_not_in_raw": db_vs_cache.get("db_not_in_cache", 0),
        "raw_not_in_db": db_vs_cache.get("cache_not_in_db", 0),
        "sample_db_not_in_raw": db_vs_cache.get("sample_db_not_in_cache", []),
        "sample_raw_not_in_db": db_vs_cache.get("sample_cache_not_in_db", []),
    }
    return {
        "scope": {
            "db": str(args.db.relative_to(ROOT) if args.db.is_relative_to(ROOT) else args.db),
            "date_field": args.date_field,
            "start": args.start,
            "end": args.end,
            "instances": ",".join(args.instances),
            "group_by": ",".join(args.group_by),
        },
        "db_snapshot": db_snapshot(conn),
        "live_reconcile": aggregate_live(conn, args),
        "order_reconcile": aggregate_orders(conn, args),
        "raw_clob_summary": raw_clob_summary(args.clob_fills, args.start, args.end),
        "raw_order_summary": raw_order_summary(args.raw_live_dir, args.start, args.end),
        "fill_id_reconciliation": fill_id_reconciliation,
        "clob_fill_coverage_gate": coverage,
    }


def parse_args() -> Args:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--clob-fills", type=Path, default=DEFAULT_CLOB_FILLS)
    parser.add_argument("--raw-live-dir", type=Path, default=DEFAULT_RAW_LIVE_DIR)
    parser.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument(
        "--date-field",
        choices=sorted(DATE_EXPR),
        default="fill_date_bj",
        help="Which date lens to use for fact_trades.",
    )
    parser.add_argument("--instances", default="all", help="Comma-separated strategy instances or all.")
    parser.add_argument(
        "--group-by",
        default="instance,selected_date",
        help=f"Comma-separated grouping columns. Allowed: {','.join(sorted(GROUP_EXPR))}",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ns = parser.parse_args()
    group_by = parse_csv(ns.group_by)
    bad = sorted(set(group_by) - set(GROUP_EXPR))
    if bad:
        parser.error(f"unsupported --group-by values: {','.join(bad)}")
    return Args(
        db=ns.db,
        clob_fills=ns.clob_fills,
        raw_live_dir=ns.raw_live_dir,
        start=ns.start,
        end=ns.end,
        date_field=ns.date_field,
        instances=parse_csv(ns.instances),
        group_by=group_by,
        format=ns.format,
    )


def main() -> None:
    args = parse_args()
    result = run(args)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))
    if not result["clob_fill_coverage_gate"].get("gate_pass"):
        sys.exit(1)


if __name__ == "__main__":
    main()
