#!/usr/bin/env python3
"""Build a daily live PnL curve from rebuilt fact_trades."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def date_range(start: dt.date, end: dt.date) -> list[str]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return days


def fetch_daily(conn: sqlite3.Connection, *, date_field: str, start: str, end: str) -> list[dict[str, Any]]:
    if date_field not in {"target_date", "fill_date_bj"}:
        raise ValueError(f"unsupported date_field={date_field}")
    if date_field == "fill_date_bj":
        day_expr = "date(datetime(fill_ts_utc, '+8 hours'))"
    else:
        day_expr = "target_date"
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT
          {day_expr} AS day,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          SUM(cost_usd) AS cash_cost_usd,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost_usd,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS realized_pnl_usd,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost_usd,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN COALESCE(unrealized_pnl_mid, 0) ELSE 0 END) AS available_mid_mtm_open,
          SUM(CASE WHEN (settlement_status IS NULL OR settlement_status<>'settled') AND unrealized_pnl_mid IS NULL THEN 1 ELSE 0 END) AS open_missing_mid_rows
        FROM fact_trades
        WHERE trade_class='live_real'
          AND {day_expr} BETWEEN ? AND ?
        GROUP BY {day_expr}
        ORDER BY {day_expr}
        """,
        (start, end),
    ).fetchall()
    by_day = {row["day"]: dict(row) for row in rows}
    out = []
    cumulative = 0.0
    for day in date_range(parse_date(start), parse_date(end)):
        row = by_day.get(day) or {"day": day}
        item = {
            "date": day,
            "fills": int(row.get("fills") or 0),
            "settled_fills": int(row.get("settled_fills") or 0),
            "open_fills": int(row.get("open_fills") or 0),
            "cash_cost_usd": round(float(row.get("cash_cost_usd") or 0.0), 2),
            "settled_cost_usd": round(float(row.get("settled_cost_usd") or 0.0), 2),
            "realized_pnl_usd": round(float(row.get("realized_pnl_usd") or 0.0), 2),
            "open_cost_usd": round(float(row.get("open_cost_usd") or 0.0), 2),
            "available_mid_mtm_open": round(float(row.get("available_mid_mtm_open") or 0.0), 2),
            "open_missing_mid_rows": int(row.get("open_missing_mid_rows") or 0),
        }
        cumulative += item["realized_pnl_usd"]
        item["cumulative_realized_pnl_usd"] = round(cumulative, 2)
        item["realized_plus_available_mid_mtm_usd"] = round(
            item["cumulative_realized_pnl_usd"] + item["available_mid_mtm_open"],
            2,
        )
        out.append(item)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "fills": sum(int(row["fills"]) for row in rows),
        "cash_cost_usd": round(sum(float(row["cash_cost_usd"]) for row in rows), 2),
        "settled_cost_usd": round(sum(float(row["settled_cost_usd"]) for row in rows), 2),
        "realized_pnl_usd": round(sum(float(row["realized_pnl_usd"]) for row in rows), 2),
        "open_cost_usd": round(sum(float(row["open_cost_usd"]) for row in rows), 2),
        "available_mid_mtm_open": round(sum(float(row["available_mid_mtm_open"]) for row in rows), 2),
        "open_missing_mid_rows": sum(int(row["open_missing_mid_rows"]) for row in rows),
    }


def render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| date | realized | cumulative realized | open cost | mid MTM open | fills | note |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        note = ""
        if row["open_fills"]:
            note = f"open {row['open_fills']}, missing mid {row['open_missing_mid_rows']}"
        lines.append(
            "| {date} | {realized:+.2f} | {cum:+.2f} | {open_cost:.2f} | {mid:+.2f} | {fills} | {note} |".format(
                date=row["date"],
                realized=float(row["realized_pnl_usd"]),
                cum=float(row["cumulative_realized_pnl_usd"]),
                open_cost=float(row["open_cost_usd"]),
                mid=float(row["available_mid_mtm_open"]),
                fills=int(row["fills"]),
                note=note,
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    snapshot = conn.execute(
        """
        SELECT
          MAX(fact_built_at_utc) AS fact_built_at_utc,
          COUNT(*) AS live_real_rows,
          COUNT(DISTINCT fill_id) AS live_real_distinct_fills
        FROM fact_trades
        WHERE trade_class='live_real'
        """
    ).fetchone()
    target_rows = fetch_daily(conn, date_field="target_date", start=args.start, end=args.end)
    fill_rows = fetch_daily(conn, date_field="fill_date_bj", start=args.start, end=args.end)
    payload = {
        "window": {"start": args.start, "end": args.end},
        "snapshot": {
            "fact_built_at_utc": snapshot[0],
            "live_real_rows": snapshot[1],
            "live_real_distinct_fills": snapshot[2],
        },
        "target_date": {
            "summary": summarize(target_rows),
            "daily": target_rows,
        },
        "fill_date_bj": {
            "summary": summarize(fill_rows),
            "daily": fill_rows,
        },
        "notes": [
            "realized_pnl_usd is settled-only from fact_trades.",
            "open_cost_usd is cost still at risk, not realized loss.",
            "available_mid_mtm_open only covers rows with valuation; missing mid rows are listed separately.",
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = [
            "# 2026-06-07 live PnL 15d curve after history rebuild",
            "",
            "## Scope",
            "",
            f"- Window: `{args.start}`..`{args.end}` inclusive.",
            "- Data source: rebuilt `runtime/weather.db.fact_trades`, `trade_class=live_real`.",
            "- Realized PnL is settled-only. Open cost is not realized loss.",
            "",
            "## Target Date Curve",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| fills | `{payload['target_date']['summary']['fills']}` |",
            f"| cash cost | `${payload['target_date']['summary']['cash_cost_usd']:.2f}` |",
            f"| settled cost | `${payload['target_date']['summary']['settled_cost_usd']:.2f}` |",
            f"| realized PnL | `{payload['target_date']['summary']['realized_pnl_usd']:+.2f}` |",
            f"| open cost | `${payload['target_date']['summary']['open_cost_usd']:.2f}` |",
            f"| available mid MTM open | `{payload['target_date']['summary']['available_mid_mtm_open']:+.2f}` |",
            f"| open missing mid rows | `{payload['target_date']['summary']['open_missing_mid_rows']}` |",
            "",
            render_table(target_rows),
            "",
            "## Fill Date BJ Curve",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| fills | `{payload['fill_date_bj']['summary']['fills']}` |",
            f"| cash cost | `${payload['fill_date_bj']['summary']['cash_cost_usd']:.2f}` |",
            f"| settled cost | `${payload['fill_date_bj']['summary']['settled_cost_usd']:.2f}` |",
            f"| realized PnL | `{payload['fill_date_bj']['summary']['realized_pnl_usd']:+.2f}` |",
            f"| open cost | `${payload['fill_date_bj']['summary']['open_cost_usd']:.2f}` |",
            f"| available mid MTM open | `{payload['fill_date_bj']['summary']['available_mid_mtm_open']:+.2f}` |",
            f"| open missing mid rows | `{payload['fill_date_bj']['summary']['open_missing_mid_rows']}` |",
            "",
            render_table(fill_rows),
            "",
        ]
        args.report.write_text("\n".join(report), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
