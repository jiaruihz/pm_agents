#!/usr/bin/env python3
"""Analyze whether side-band daily PnL is associated with entry timing."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path("runtime/weather.db")
REPORT_PATH = Path("docs/analysis/2026-06/2026-06-08-side-band-entry-timing-impact.md")
SIDE_INSTANCE = "mid_price_core_v1_side_band"

INSTANCE_EXPR = """
CASE
  WHEN producer_run_id LIKE '%mid_price_core_v1_side_band%'
    THEN 'mid_price_core_v1_side_band'
  ELSE strategy_id
END
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def one(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else {}


def money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}"


def usd(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{100.0 * float(value):+.1f}%"


def num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def md_table(headers: list[str], data: list[list[Any]]) -> str:
    if not data:
        return "_No rows._\n"
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in data:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out) + "\n"


def build_report() -> str:
    conn = connect()
    now_bj = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    snapshot = one(
        conn,
        """
        SELECT
          COUNT(*) AS total_rows,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
          SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows,
          MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_trades
        """,
    )
    total = one(
        conn,
        f"""
        SELECT
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT target_date) AS days,
          COUNT(DISTINCT city) AS cities,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          AVG(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS avg_hts,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        """,
        (SIDE_INSTANCE,),
    )
    by_bin = rows(
        conn,
        f"""
        WITH scoped AS (
          SELECT *,
            CASE
              WHEN hours_to_settle < 22 THEN '<T-22'
              WHEN hours_to_settle <= 24 THEN 'T-22-24'
              WHEN hours_to_settle <= 26 THEN 'T-24-26'
              WHEN hours_to_settle <= 28 THEN 'T-26-28'
              WHEN hours_to_settle > 28 THEN '>T-28'
              ELSE '[missing]'
            END AS hts_bin,
            CASE
              WHEN hours_to_settle < 22 THEN 0
              WHEN hours_to_settle <= 24 THEN 1
              WHEN hours_to_settle <= 26 THEN 2
              WHEN hours_to_settle <= 28 THEN 3
              WHEN hours_to_settle > 28 THEN 4
              ELSE 9
            END AS hts_sort
          FROM fact_trades
          WHERE trade_class='live_real'
            AND ({INSTANCE_EXPR})=?
        )
        SELECT
          hts_bin,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT target_date) AS days,
          COUNT(DISTINCT city) AS cities,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill_price,
          AVG(CASE WHEN settlement_status='settled' THEN abs_edge END) AS avg_abs_edge,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost,
          MIN(hts_sort) AS hts_sort
        FROM scoped
        GROUP BY hts_bin
        ORDER BY hts_sort
        """,
        (SIDE_INSTANCE,),
    )
    by_day_bin = rows(
        conn,
        f"""
        WITH scoped AS (
          SELECT *,
            CASE
              WHEN hours_to_settle < 22 THEN '<T-22'
              WHEN hours_to_settle <= 24 THEN 'T-22-24'
              WHEN hours_to_settle <= 26 THEN 'T-24-26'
              WHEN hours_to_settle <= 28 THEN 'T-26-28'
              WHEN hours_to_settle > 28 THEN '>T-28'
              ELSE '[missing]'
            END AS hts_bin,
            CASE
              WHEN hours_to_settle < 22 THEN 0
              WHEN hours_to_settle <= 24 THEN 1
              WHEN hours_to_settle <= 26 THEN 2
              WHEN hours_to_settle <= 28 THEN 3
              WHEN hours_to_settle > 28 THEN 4
              ELSE 9
            END AS hts_sort
          FROM fact_trades
          WHERE trade_class='live_real'
            AND ({INSTANCE_EXPR})=?
        )
        SELECT
          target_date,
          hts_bin,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT city) AS cities,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS avg_hts,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost,
          MIN(hts_sort) AS hts_sort
        FROM scoped
        GROUP BY target_date, hts_bin
        ORDER BY target_date, hts_sort
        """,
        (SIDE_INSTANCE,),
    )
    by_day = rows(
        conn,
        f"""
        SELECT
          target_date,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          AVG(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS avg_hts,
          MIN(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS min_hts,
          MAX(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS max_hts,
          SUM(CASE WHEN hours_to_settle < 22 THEN 1 ELSE 0 END) AS early_lt22,
          SUM(CASE WHEN hours_to_settle BETWEEN 22 AND 24 THEN 1 ELSE 0 END) AS t22_24,
          SUM(CASE WHEN hours_to_settle > 28 THEN 1 ELSE 0 END) AS late_gt28,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        GROUP BY target_date
        ORDER BY target_date
        """,
        (SIDE_INSTANCE,),
    )
    city_day_bin = rows(
        conn,
        f"""
        WITH scoped AS (
          SELECT *,
            CASE
              WHEN hours_to_settle < 22 THEN '<T-22'
              WHEN hours_to_settle <= 24 THEN 'T-22-24'
              WHEN hours_to_settle <= 26 THEN 'T-24-26'
              WHEN hours_to_settle <= 28 THEN 'T-26-28'
              WHEN hours_to_settle > 28 THEN '>T-28'
              ELSE '[missing]'
            END AS hts_bin
          FROM fact_trades
          WHERE trade_class='live_real'
            AND settlement_status='settled'
            AND ({INSTANCE_EXPR})=?
        )
        SELECT
          target_date,
          city,
          hts_bin,
          COUNT(*) AS fills,
          SUM(cost_usd) AS cost,
          SUM(pnl_usd_at_fill) AS pnl,
          SUM(pnl_usd_at_fill) / NULLIF(SUM(cost_usd), 0) AS roi
        FROM scoped
        GROUP BY target_date, city, hts_bin
        HAVING ABS(pnl) >= 5 OR fills >= 3
        ORDER BY target_date, ABS(pnl) DESC
        """,
        (SIDE_INSTANCE,),
    )

    lines = [
        "# Side-band 入场 timing 对每日 PnL 的影响",
        "",
        "## 数据快照",
        "",
        f"- 生成时间：{now_bj}。",
        f"- 数据源：`{DB_PATH}` 的 `fact_trades`；只取 `trade_class='live_real'` 且严格归因 `{SIDE_INSTANCE}`。",
        f"- fact built：{snapshot.get('fact_built_at_utc') or '-'}。",
        f"- fact_trades：{snapshot.get('total_rows')} rows；settled {snapshot.get('settled_rows')}；missing_bracket {snapshot.get('missing_bracket_rows') or 0}。",
        "- timing 字段：`hours_to_settle`，按 `<T-22`、`T-22-24`、`T-24-26`、`T-26-28`、`>T-28` 分桶。",
        "",
        "## 结论",
        "",
        f"- side-band 总体：{total.get('fills')} fills，{total.get('settled')} settled，"
        f"已结算 PnL `{money(total.get('pnl'))}`，ROI `{pct(total.get('roi'))}`，平均 hts `{num(total.get('avg_hts'), 1)}` 小时。",
        "- **确实受到入场 timing 影响，但不是唯一解释。** `T-22-24` 是主要正收益桶；`T-24-26` 和 `<T-22` 是主要亏损桶。",
        "- 坏日 `2026-06-04` 不是单纯“太早/太晚”导致：它在 `<T-22`、`T-22-24`、`T-24-26`、`T-26-28` 都亏，说明当天更多是城市/方向/天气结果共同作用。",
        "- `2026-06-05` 的亏损更像 timing+城市叠加：亏损集中在 `T-22-24` 的 LA/NYC/Miami/London，而 Tokyo 同日 `>T-28` 是正的。",
        "- `>T-28` 整体反而是正收益，主要来自 6/1 和 Tokyo，不支持简单地砍掉更早入场。",
        "",
        "## 总体 timing 分桶",
        "",
        md_table(
            ["hts_bin", "fills", "settled", "open", "days", "cities", "NO/YES", "cost", "pnl", "ROI", "win_rate", "avg_fill", "avg_abs_edge", "open_cost"],
            [
                [
                    r["hts_bin"],
                    r["fills"],
                    r["settled"],
                    r["open_fills"],
                    r["days"],
                    r["cities"],
                    f"{r['buy_no']}/{r['buy_yes']}",
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    num(r["avg_fill_price"], 3),
                    num(r["avg_abs_edge"], 3),
                    usd(r["open_cost"]),
                ]
                for r in by_bin
            ],
        ),
        "## 每日 timing 摘要",
        "",
        md_table(
            ["date", "fills", "settled", "open", "avg_hts", "min-max hts", "<22", "22-24", ">28", "cost", "pnl", "ROI", "open_cost"],
            [
                [
                    r["target_date"],
                    r["fills"],
                    r["settled"],
                    r["open_fills"],
                    num(r["avg_hts"], 1),
                    f"{num(r['min_hts'], 1)}-{num(r['max_hts'], 1)}",
                    r["early_lt22"],
                    r["t22_24"],
                    r["late_gt28"],
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    usd(r["open_cost"]),
                ]
                for r in by_day
            ],
        ),
        "## 每日 x timing 分桶",
        "",
        md_table(
            ["date", "hts_bin", "fills", "settled", "open", "cities", "NO/YES", "cost", "pnl", "ROI", "avg_hts", "open_cost"],
            [
                [
                    r["target_date"],
                    r["hts_bin"],
                    r["fills"],
                    r["settled"],
                    r["open_fills"],
                    r["cities"],
                    f"{r['buy_no']}/{r['buy_yes']}",
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    num(r["avg_hts"], 1),
                    usd(r["open_cost"]),
                ]
                for r in by_day_bin
            ],
        ),
        "## 主要 city-day timing 贡献",
        "",
        md_table(
            ["date", "city", "hts_bin", "fills", "cost", "pnl", "ROI"],
            [
                [
                    r["target_date"],
                    r["city"],
                    r["hts_bin"],
                    r["fills"],
                    usd(r["cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                ]
                for r in city_day_bin
            ],
        ),
        "## 口径说明",
        "",
        "- 这里是相关性分析，不是因果证明；同一个 timing 桶里城市、方向、模型和天气结果仍会混在一起。",
        "- `pnl` 只读 `fact_trades.pnl_usd_at_fill`；未结算只报 `open_cost`。",
        "- 日度按 `target_date`，不是钱包现金流日期。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(), encoding="utf-8")
    print(REPORT_PATH)


if __name__ == "__main__":
    main()
