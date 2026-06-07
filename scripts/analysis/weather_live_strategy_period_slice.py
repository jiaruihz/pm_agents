#!/usr/bin/env python3
"""Slice live weather strategy performance by period, date, and instance.

This report keeps wallet cashflow and strategy realized PnL separate:

- fill_date_bj: when wallet cash was spent on actual fills.
- target_date: weather contract date for strategy attribution.
- realized_pnl_usd: settled-only PnL from fact_trades.
- open_cost_usd: unsettled cost still at risk; not realized loss.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
REPORT_PATH = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-07-live-strategy-period-slice-after-fill-fix.md"
JSON_PATH = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-07-live-strategy-period-slice-after-fill-fix.json"

INSTANCE_EXPR = """
CASE
  WHEN run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65') THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='maker_queue_v1' AND entry_price_window='0.25-0.75' THEN 'legacy_maker_queue_v1_25_75'
  WHEN execution_policy='maker_queue_v2' AND entry_price_window='0.25-0.75' THEN 'legacy_maker_queue_v2_25_75'
  ELSE COALESCE(strategy_id, 'unknown')
END
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar_row(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    return dict(conn.execute(sql, params).fetchone())


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def money(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):+.2f}" if float(value) < 0 else f"{float(value):.2f}"


def roi(pnl: Any, cost: Any) -> float | None:
    pnl_f = float(pnl or 0)
    cost_f = float(cost or 0)
    if cost_f == 0:
        return None
    return pnl_f / cost_f


def render_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def with_roi(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        r["roi_settled"] = pct(roi(r.get("realized_pnl_usd"), r.get("settled_cost_usd")))
        out.append(r)
    return out


PERIODS = (
    ("recent_7d_2026-05-31_to_2026-06-06", "2026-05-31", "2026-06-06"),
    ("recent_14d_2026-05-24_to_2026-06-06", "2026-05-24", "2026-06-06"),
    ("before_2026-05-24", "0001-01-01", "2026-05-23"),
)


def _aggregate_range(
    conn: sqlite3.Connection,
    date_expr: str,
    group_cols: list[str],
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    if group_cols:
        group_sql = ", ".join(group_cols)
        select_sql = ", ".join(group_cols)
    else:
        group_sql = "_all"
        select_sql = "1 AS _all"
    where_extra = ""
    params: tuple[Any, ...] = ()
    if start is not None and end is not None:
        where_extra = "AND selected_date BETWEEN ? AND ?"
        params = (start, end)
    rows = fetch(
        conn,
        f"""
        WITH live AS (
          SELECT
            *,
            {INSTANCE_EXPR} AS strategy_instance,
            {date_expr} AS selected_date
          FROM fact_trades
          WHERE trade_class='live_real'
        )
        SELECT
          {select_sql},
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          ROUND(SUM(cost_usd), 2) AS cash_cost_usd,
          ROUND(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 2) AS settled_cost_usd,
          ROUND(SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END), 2) AS realized_pnl_usd,
          ROUND(SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END), 2) AS open_cost_usd,
          SUM(CASE
            WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN
              CASE WHEN val_mid IS NULL THEN 1 ELSE 0 END
            ELSE 0
          END) AS open_missing_mid_rows,
          ROUND(SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN unrealized_pnl_mid ELSE 0 END), 2) AS unrealized_pnl_mid_usd,
          ROUND(SUM(CASE
            WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN
              CASE
                WHEN val_bid IS NULL THEN NULL
                WHEN side='BUY_YES' THEN (val_bid-fill_price)*fill_qty
                WHEN side='BUY_NO' THEN ((1.0-val_bid)-fill_price)*fill_qty
              END
          END), 2) AS unrealized_pnl_bid_usd,
          MAX(val_snapshot_ts_utc) AS val_snapshot_ts_utc
        FROM live
        WHERE 1=1
          {where_extra}
        GROUP BY {group_sql}
        ORDER BY {group_sql}
        """,
        params,
    )
    if period is not None:
        for row in rows:
            row["period"] = period
    for row in rows:
        row.pop("_all", None)
    return rows


def aggregate(conn: sqlite3.Connection, date_expr: str, group_cols: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period, start, end in PERIODS:
        rows.extend(
            _aggregate_range(
                conn,
                date_expr,
                group_cols,
                period=period,
                start=start,
                end=end,
            )
        )
    period_first = ["period", *group_cols]
    return [
        {k: row.get(k) for k in period_first + [c for c in row if c not in period_first]}
        for row in rows
    ]


def aggregate_daily(conn: sqlite3.Connection, date_expr: str) -> list[dict[str, Any]]:
    return _aggregate_range(conn, date_expr, ["selected_date", "strategy_instance"])


def possible_300_rows(period_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in period_rows:
        if row.get("period") != "recent_7d_2026-05-31_to_2026-06-06":
            continue
        realized = float(row.get("realized_pnl_usd") or 0)
        open_cost = float(row.get("open_cost_usd") or 0)
        mtm_mid = float(row.get("unrealized_pnl_mid_usd") or 0)
        out.append(
            {
                "date_lens": row.get("date_lens"),
                "cash_cost_usd": row.get("cash_cost_usd"),
                "realized_pnl_usd": row.get("realized_pnl_usd"),
                "open_cost_usd": row.get("open_cost_usd"),
                "open_missing_mid_rows": row.get("open_missing_mid_rows"),
                "available_mid_mtm_open": row.get("unrealized_pnl_mid_usd"),
                "realized_minus_open_cost_diagnostic": round(realized - open_cost, 2),
                "realized_plus_available_mid_mtm": round(realized + mtm_mid, 2),
            }
        )
    return out


def daily_pivot(rows: list[dict[str, Any]], *, metric: str) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    instances = [
        "mid_price_core_v1_25_75",
        "mid_price_core_v2_25_75",
        "mid_price_core_v1_side_band",
    ]
    for row in rows:
        d = row["selected_date"]
        item = by_date.setdefault(d, {"selected_date": d, "total": 0.0})
        inst = row["strategy_instance"]
        if inst in instances:
            item.setdefault(inst, 0.0)
            item[inst] += float(row.get(metric) or 0)
        item["total"] += float(row.get(metric) or 0)
    out = []
    for d in sorted(by_date):
        item = by_date[d]
        out.append({
            "date": d,
            "total": round(item.get("total", 0.0), 2),
            "v1_25_75": round(item.get("mid_price_core_v1_25_75", 0.0), 2),
            "v2_25_75": round(item.get("mid_price_core_v2_25_75", 0.0), 2),
            "side_band": round(item.get("mid_price_core_v1_side_band", 0.0), 2),
        })
    return out


def pick_period(rows: list[dict[str, Any]], period: str, date_lens: str) -> dict[str, Any]:
    for row in rows:
        if row.get("period") == period and row.get("date_lens") == date_lens:
            return row
    raise KeyError(f"missing period={period} date_lens={date_lens}")


def sum_instances(rows: list[dict[str, Any]], period: str, instances: set[str]) -> dict[str, float]:
    out = {"realized_pnl_usd": 0.0, "settled_cost_usd": 0.0, "open_cost_usd": 0.0}
    for row in rows:
        if row.get("period") != period or row.get("strategy_instance") not in instances:
            continue
        out["realized_pnl_usd"] += float(row.get("realized_pnl_usd") or 0.0)
        out["settled_cost_usd"] += float(row.get("settled_cost_usd") or 0.0)
        out["open_cost_usd"] += float(row.get("open_cost_usd") or 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def format_signed(value: Any) -> str:
    return f"{float(value):+.2f}"


def render_conclusion(output: dict[str, Any]) -> list[str]:
    recent7 = "recent_7d_2026-05-31_to_2026-06-06"
    recent14 = "recent_14d_2026-05-24_to_2026-06-06"
    target7 = pick_period(output["period_total"], recent7, "target_date")
    fill7 = pick_period(output["period_total"], recent7, "fill_date_bj")
    target14 = pick_period(output["period_total"], recent14, "target_date")
    active = {
        "mid_price_core_v1_25_75",
        "mid_price_core_v1_side_band",
        "mid_price_core_v2_25_75",
    }
    active7 = sum_instances(output["target_period_by_instance"], recent7, active)
    active14 = sum_instances(output["target_period_by_instance"], recent14, active)
    return [
        (
            f"- 修复 partial-fill 之后，`fact_trades live_real` 是 "
            f"`{output['snapshot']['live_real_rows']}` 行 / `{output['snapshot']['live_real_distinct_fills']}` 个 fill_id；"
            "raw CLOB fill 与 DB fill_id 在账户对账脚本里已经可做到 1:1。"
        ),
        (
            f"- 最近 7 天按 `target_date` 做策略归因，当前活跃三实例已结算 PnL 合计 "
            f"`{format_signed(active7['realized_pnl_usd'])}`，settled cost "
            f"`{active7['settled_cost_usd']:.2f}`，open cost `{active7['open_cost_usd']:.2f}`；"
            f"把 legacy live rows 也算进 live 总体后是 `{format_signed(target7['realized_pnl_usd'])}`。"
        ),
        (
            f"- 最近 7 天按 `fill_date_bj` 看钱包现金流，真实 fill cash cost 是 "
            f"`{fill7['cash_cost_usd']}`，settled realized PnL 是 "
            f"`{format_signed(fill7['realized_pnl_usd'])}`，open cost `{fill7['open_cost_usd']}`。"
        ),
        (
            f"- 最近 14 天按 `target_date` 看，活跃三实例已结算 PnL 合计 "
            f"`{format_signed(active14['realized_pnl_usd'])}`；live 总体为 "
            f"`{format_signed(target14['realized_pnl_usd'])}`。"
        ),
        (
            f"- 这仍不是 Polymarket UI 的账户权益口径：最近 7 天 target_date 还有 "
            f"`open_cost={target7['open_cost_usd']}`，其中 `{target7['open_missing_mid_rows']}` 行 open "
            "缺 mid 估值；UI 需要按 BUY/SELL/REDEEM/REBATE 和当前持仓价值重放。"
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--json-out", type=Path, default=JSON_PATH)
    args = parser.parse_args()

    conn = connect(args.db)
    snapshot = scalar_row(
        conn,
        """
        SELECT
          MAX(fact_built_at_utc) AS fact_built_at_utc,
          MIN(target_date) AS min_target_date,
          MAX(target_date) AS max_target_date,
          MIN(date(fill_ts_utc, '+8 hours')) AS min_fill_date_bj,
          MAX(date(fill_ts_utc, '+8 hours')) AS max_fill_date_bj,
          MAX(order_ts_utc) AS max_order_ts_utc,
          MAX(fill_ts_utc) AS max_fill_ts_utc,
          MAX(val_snapshot_ts_utc) AS max_val_snapshot_ts_utc,
          COUNT(*) AS live_real_rows,
          COUNT(DISTINCT fill_id) AS live_real_distinct_fills
        FROM fact_trades
        WHERE trade_class='live_real'
        """,
    )
    trade_class = fetch(
        conn,
        "SELECT trade_class, COUNT(1) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
    )
    settlement = fetch(
        conn,
        "SELECT settlement_status, COUNT(1) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
    )
    fill_period_by_instance = aggregate(conn, "date(fill_ts_utc, '+8 hours')", ["strategy_instance"])
    target_period_by_instance = aggregate(conn, "target_date", ["strategy_instance"])
    fill_period_total = aggregate(conn, "date(fill_ts_utc, '+8 hours')", [])
    target_period_total = aggregate(conn, "target_date", [])
    for row in fill_period_total:
        row["date_lens"] = "fill_date_bj"
    for row in target_period_total:
        row["date_lens"] = "target_date"
    target_daily_by_instance = aggregate_daily(conn, "target_date")
    fill_daily_by_instance = aggregate_daily(conn, "date(fill_ts_utc, '+8 hours')")

    output = {
        "generated_at_bj": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "snapshot": snapshot,
        "trade_class": trade_class,
        "settlement": settlement,
        "fill_period_by_instance": with_roi(fill_period_by_instance),
        "target_period_by_instance": with_roi(target_period_by_instance),
        "period_total": with_roi(fill_period_total + target_period_total),
        "possible_300": possible_300_rows(fill_period_total + target_period_total),
        "target_daily_realized_pnl": daily_pivot(target_daily_by_instance, metric="realized_pnl_usd"),
        "target_daily_open_cost": daily_pivot(target_daily_by_instance, metric="open_cost_usd"),
        "fill_daily_cash_cost": daily_pivot(fill_daily_by_instance, metric="cash_cost_usd"),
    }

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# 2026-06-07 live strategy period slice after fill fix",
        "",
        "## 结论先行",
        "",
        *render_conclusion(output),
        "",
        "## 数据快照",
        "",
        render_table([snapshot]),
        "",
        "## Trade / Settlement 自检",
        "",
        render_table(trade_class),
        "",
        render_table(settlement),
        "",
        "## 为什么不能直接对上 UI 的 1 周亏损",
        "",
        render_table(possible_300_rows(fill_period_total + target_period_total)),
        "",
        "## Period 总览",
        "",
        render_table(with_roi(fill_period_total + target_period_total)),
        "",
        "## 最近两周 + 更早：fill_date_bj 钱包现金流",
        "",
        render_table(with_roi(fill_period_by_instance)),
        "",
        "## 最近两周 + 更早：target_date 策略归因",
        "",
        render_table(with_roi(target_period_by_instance)),
        "",
        "## Target Date 每日 realized PnL",
        "",
        render_table(output["target_daily_realized_pnl"]),
        "",
        "## Target Date 每日 open cost",
        "",
        render_table(output["target_daily_open_cost"]),
        "",
        "## Fill Date BJ 每日 cash cost",
        "",
        render_table(output["fill_daily_cash_cost"]),
        "",
        "## 口径说明",
        "",
        "- `cash_cost_usd` 是已成交买入花掉的现金，不是亏损。",
        "- `realized_pnl_usd` 只包含 `settlement_status='settled'`。",
        "- `open_cost_usd` 是未结算成本，仍在风险中，但不能直接当作已亏。",
        "- `target_date` 用于策略归因；`fill_date_bj` 用于钱包现金流。",
    ]
    args.report.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(args.report)
    print(args.json_out)


if __name__ == "__main__":
    main()
