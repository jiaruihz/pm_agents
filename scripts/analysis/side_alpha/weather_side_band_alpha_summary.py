#!/usr/bin/env python3
"""Current side-band alpha summary from canonical weather facts."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path("runtime/weather.db")
REMOTE_PM_AGENT_DIR = Path("runtime/weather_edge_v1/remote_pm_agent")
REPORT_PATH = Path("docs/analysis/2026-06/2026-06-08-performance-side-band-alpha-summary.md")

SIDE_INSTANCE = "mid_price_core_v1_side_band"
V1_INSTANCE = "mid_price_core_v1_25_75"
V2_INSTANCE = "mid_price_core_v2_25_75"

INSTANCE_EXPR = """
CASE
  WHEN producer_run_id LIKE '%mid_price_core_v2_25_75%'
    THEN 'mid_price_core_v2_25_75'
  WHEN producer_run_id LIKE '%mid_price_core_v1_25_75%'
    THEN 'mid_price_core_v1_25_75'
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_runtime_orders(instance: str) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for path in sorted((REMOTE_PM_AGENT_DIR / "plans").glob(f"live_{instance}_*_trade_plans.jsonl")):
        for row in read_jsonl(path):
            row["_source_file"] = path.name
            plans.append(row)
    plan_by_id = {row.get("plan_id"): row for row in plans if row.get("plan_id")}

    orders: list[dict[str, Any]] = []
    for path in sorted((REMOTE_PM_AGENT_DIR / "live").glob(f"live_{instance}_*_orders.jsonl")):
        for row in read_jsonl(path):
            plan = plan_by_id.get(row.get("plan_id")) or {}
            merged = dict(plan)
            merged.update(row)
            merged["_source_file"] = path.name
            orders.append(merged)
    return orders


def submitted_summary(instance: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    orders = load_runtime_orders(instance)
    day_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    city_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in orders:
        target_date = str(row.get("target_date") or "-")
        city = str(row.get("city") or "-")
        day_groups[target_date].append(row)
        city_groups[city].append(row)

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        prices = [
            float(row.get("posted_price") if row.get("posted_price") is not None else row.get("limit_price"))
            for row in group
            if row.get("posted_price") is not None or row.get("limit_price") is not None
        ]
        return {
            "orders": len(group),
            "cities": len({row.get("city") for row in group if row.get("city")}),
            "buy_no": sum(1 for row in group if (row.get("side") or row.get("signal_side")) in ("BUY_NO", "NO")),
            "buy_yes": sum(1 for row in group if (row.get("side") or row.get("signal_side")) in ("BUY_YES", "YES")),
            "avg_entry": (sum(prices) / len(prices)) if prices else None,
        }

    by_day = [{"target_date": key, **summarize(group)} for key, group in sorted(day_groups.items())]
    by_city = [{"city": key, **summarize(group)} for key, group in sorted(city_groups.items())]
    by_city.sort(key=lambda row: (-row["orders"], row["city"]))
    return by_day, by_city


def build_report() -> str:
    conn = connect()
    now_bj = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    snapshot = one(
        conn,
        """
        SELECT
          COUNT(*) AS total_rows,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS unsettled_rows,
          SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows,
          MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_trades
        """,
    )
    trade_class = rows(
        conn,
        """
        SELECT trade_class, COUNT(*) AS rows,
               SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled
        FROM fact_trades
        GROUP BY trade_class
        ORDER BY trade_class
        """,
    )
    signal_cov = one(
        conn,
        """
        SELECT COUNT(*) AS rows,
               SUM(eligible) AS eligible,
               SUM(paper_ordered) AS paper_ordered,
               SUM(live_filled) AS live_filled,
               SUM(decision_window_missing) AS decision_window_missing
        FROM fact_signal_candidates
        """,
    )
    order_fill_cov = rows(
        conn,
        """
        SELECT o.status,
               COUNT(*) AS orders,
               SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
        FROM orders o
        LEFT JOIN fills f USING(execution_id)
        WHERE o.venue='polymarket_clob'
        GROUP BY o.status
        ORDER BY o.status
        """,
    )

    instance_overview = rows(
        conn,
        f"""
        SELECT
          ({INSTANCE_EXPR}) AS instance,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT city) AS cities,
          COUNT(DISTINCT target_date) AS days,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR}) IN (?, ?, ?)
        GROUP BY instance
        ORDER BY instance
        """,
        (SIDE_INSTANCE, V1_INSTANCE, V2_INSTANCE),
    )

    side_total = one(
        conn,
        f"""
        SELECT
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT city) AS cities,
          COUNT(DISTINCT target_date) AS days,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost,
          MIN(target_date) AS min_target_date,
          MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        """,
        (SIDE_INSTANCE,),
    )

    by_day = rows(
        conn,
        f"""
        SELECT
          target_date,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT city) AS cities,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no_fills,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes_fills,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        GROUP BY target_date
        ORDER BY target_date
        """,
        (SIDE_INSTANCE,),
    )

    by_city = rows(
        conn,
        f"""
        SELECT
          city,
          COALESCE(city_pool, '-') AS city_pool,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN 1 ELSE 0 END) AS open_fills,
          COUNT(DISTINCT target_date) AS days,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no_fills,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes_fills,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        GROUP BY city, city_pool
        ORDER BY fills DESC, pnl DESC
        """,
        (SIDE_INSTANCE,),
    )

    by_city_day = rows(
        conn,
        f"""
        SELECT
          target_date,
          city,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no_fills,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes_fills,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status IS NULL OR settlement_status<>'settled' THEN cost_usd ELSE 0 END) AS open_cost
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        GROUP BY target_date, city
        HAVING fills >= 2 OR ABS(pnl) >= 5 OR open_cost >= 5
        ORDER BY target_date, ABS(pnl) DESC, fills DESC
        """,
        (SIDE_INSTANCE,),
    )

    by_model_side = rows(
        conn,
        f"""
        SELECT
          model_version,
          side,
          COUNT(*) AS fills,
          SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
          SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
          SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
            / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
          AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
          AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill,
          AVG(CASE WHEN settlement_status='settled' THEN abs_edge END) AS avg_abs_edge
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})=?
        GROUP BY model_version, side
        ORDER BY model_version, side
        """,
        (SIDE_INSTANCE,),
    )

    candidate_gate = rows(
        conn,
        """
        WITH scoped AS (
          SELECT
            model_version,
            side,
            decision_entry_price,
            abs_edge,
            counterfactual_pnl,
            CASE
              WHEN decision_entry_price >= 0.25
                   AND decision_entry_price <= 0.75
                   AND abs_edge >= 0.10 THEN 1
              ELSE 0
            END AS pass_v1,
            CASE
              WHEN side='BUY_YES'
                   AND decision_entry_price >= 0.20
                   AND decision_entry_price <= 0.45
                   AND abs_edge >= 0.20 THEN 1
              WHEN side='BUY_NO'
                   AND decision_entry_price >= 0.35
                   AND decision_entry_price <= 0.65
                   AND abs_edge >= 0.10 THEN 1
              ELSE 0
            END AS pass_side_band
          FROM fact_signal_candidates
          WHERE eligible=1
            AND final_yes IS NOT NULL
            AND decision_window_missing=0
            AND model_version IN ('ecmwf', 'gfs')
        )
        SELECT
          model_version,
          side,
          COUNT(*) AS evaluable,
          SUM(pass_v1) AS pass_v1,
          SUM(pass_side_band) AS pass_side_band,
          SUM(CASE WHEN pass_v1=1 THEN counterfactual_pnl ELSE 0 END) AS v1_cf_pnl,
          SUM(CASE WHEN pass_side_band=1 THEN counterfactual_pnl ELSE 0 END) AS side_cf_pnl,
          AVG(CASE WHEN pass_v1=1 THEN decision_entry_price END) AS v1_avg_entry,
          AVG(CASE WHEN pass_side_band=1 THEN decision_entry_price END) AS avg_entry,
          AVG(CASE WHEN pass_v1=1 THEN abs_edge END) AS v1_avg_abs_edge,
          AVG(CASE WHEN pass_side_band=1 THEN abs_edge END) AS avg_abs_edge
        FROM scoped
        GROUP BY model_version, side
        ORDER BY model_version, side
        """,
    )

    submitted_day, submitted_city = submitted_summary(SIDE_INSTANCE)

    lines: list[str] = [
        "# Side-band 当前 alpha / 日度 / 城市集中度复盘",
        "",
        "## 数据快照",
        "",
        f"- 生成时间：{now_bj}。",
        f"- 数据源：`{DB_PATH}` 的 `fact_trades` / `fact_signal_candidates`，以及 synced runtime submitted order JSONL。",
        f"- fact built：{snapshot.get('fact_built_at_utc') or '-'}。",
        f"- fact_trades：{snapshot.get('total_rows')} rows；settled {snapshot.get('settled_rows')}；unsettled/null {snapshot.get('unsettled_rows')}；missing_bracket {snapshot.get('missing_bracket_rows') or 0}。",
        f"- fact_signal_candidates：{signal_cov.get('rows')} rows；eligible {signal_cov.get('eligible')}；paper_ordered {signal_cov.get('paper_ordered')}；live_filled {signal_cov.get('live_filled')}；decision_window_missing {signal_cov.get('decision_window_missing')}。",
        "- CLOB coverage gate：本次手动已跑 `weather_clob_fill_coverage_gate.py`，`gate_pass=true`，DB/cache/fact cost delta = 0。",
        "",
        "强制自检：",
        "",
        md_table(
            ["trade_class", "rows", "settled"],
            [[r["trade_class"], r["rows"], r["settled"]] for r in trade_class],
        ),
        md_table(
            ["clob_order_status", "orders", "with_fill"],
            [[r["status"], r["orders"], r["with_fill"]] for r in order_fill_cov],
        ),
        "## 结论先行",
        "",
    ]

    lines.append(
        f"- `side-band` 当前严格归因 live_real：{side_total.get('fills')} fills，"
        f"{side_total.get('settled_fills')} settled，已结算成本 `${usd(side_total.get('settled_cost'))}`，"
        f"已结算 PnL `{money(side_total.get('pnl'))}`，ROI `{pct(side_total.get('roi'))}`，"
        f"未结算成本 `${usd(side_total.get('open_cost'))}`。"
    )
    lines.append(
        "- 交易动作：**不建议扩大 live size**。它是正 PnL，但 ROI 低于同期开跑的 v1 25-75，"
        f"且只有 {side_total.get('days')} 个 target days、{side_total.get('settled_fills')} 个 settled fills，日度波动明显；适合继续 shadow/小 size 收样本。"
    )
    lines.append(
        "- alpha 判断：已成交样本和机会层 gate 都有正向信号，但 live 样本仍小，且收益主要集中在少数日期/城市，"
        "所以只能算**初步 alpha 迹象**，还不是可放大 size 的强证据。"
    )

    lines.extend([
        "",
        "## 同期 live_real 对照",
        "",
        md_table(
            ["instance", "fills", "settled", "open", "cities", "days", "settled_cost", "pnl", "ROI", "win_rate", "open_cost"],
            [
                [
                    r["instance"],
                    r["fills"],
                    r["settled_fills"],
                    r["open_fills"],
                    r["cities"],
                    r["days"],
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    usd(r["open_cost"]),
                ]
                for r in instance_overview
            ],
        ),
        "## 每天明细（按 target_date）",
        "",
        md_table(
            ["date", "submitted_orders", "fills", "settled", "open", "cities", "NO/YES fills", "settled_cost", "pnl", "ROI", "win_rate", "open_cost"],
            [
                [
                    r["target_date"],
                    next((d["orders"] for d in submitted_day if d["target_date"] == r["target_date"]), 0),
                    r["fills"],
                    r["settled_fills"],
                    r["open_fills"],
                    r["cities"],
                    f"{r['buy_no_fills']}/{r['buy_yes_fills']}",
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    usd(r["open_cost"]),
                ]
                for r in by_day
            ],
        ),
        "## 下单集中城市（submitted orders）",
        "",
        md_table(
            ["city", "submitted_orders", "NO", "YES", "avg_entry"],
            [[r["city"], r["orders"], r["buy_no"], r["buy_yes"], num(r["avg_entry"], 3)] for r in submitted_city[:20]],
        ),
        "## 成交 / 盈利集中城市（live_real fills）",
        "",
        md_table(
            ["city", "pool", "fills", "settled", "open", "days", "NO/YES fills", "settled_cost", "pnl", "ROI", "win_rate", "open_cost"],
            [
                [
                    r["city"],
                    r["city_pool"],
                    r["fills"],
                    r["settled_fills"],
                    r["open_fills"],
                    r["days"],
                    f"{r['buy_no_fills']}/{r['buy_yes_fills']}",
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    usd(r["open_cost"]),
                ]
                for r in by_city
            ],
        ),
        "## 重点 city-day 明细",
        "",
        md_table(
            ["date", "city", "fills", "settled", "NO/YES fills", "settled_cost", "pnl", "open_cost"],
            [
                [
                    r["target_date"],
                    r["city"],
                    r["fills"],
                    r["settled_fills"],
                    f"{r['buy_no_fills']}/{r['buy_yes_fills']}",
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    usd(r["open_cost"]),
                ]
                for r in by_city_day
            ],
        ),
        "## 模型 / 方向",
        "",
        md_table(
            ["model", "side", "fills", "settled", "settled_cost", "pnl", "ROI", "win_rate", "avg_fill", "avg_abs_edge"],
            [
                [
                    r["model_version"],
                    r["side"],
                    r["fills"],
                    r["settled_fills"],
                    usd(r["settled_cost"]),
                    money(r["pnl"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    num(r["avg_fill"], 3),
                    num(r["avg_abs_edge"], 3),
                ]
                for r in by_model_side
            ],
        ),
        "## 机会层 side-band gate（非成交 PnL）",
        "",
        "这段只看 `fact_signal_candidates` 的 eligible + settled + decision window available 机会，"
        "用于判断 gate 本身有没有信号，不等同于 live realized PnL。v1 gate = 0.25-0.75 + abs_edge>=0.10；"
        "side-band gate = YES 0.20-0.45/edge>=0.20，NO 0.35-0.65/edge>=0.10。",
        "",
        md_table(
            ["model", "side", "evaluable", "pass_v1", "pass_side", "v1_cf_pnl", "side_cf_pnl", "v1_entry", "side_entry", "v1_abs_edge", "side_abs_edge"],
            [
                [
                    r["model_version"],
                    r["side"],
                    r["evaluable"],
                    r["pass_v1"],
                    r["pass_side_band"],
                    money(r["v1_cf_pnl"]),
                    money(r["side_cf_pnl"]),
                    num(r["v1_avg_entry"], 3),
                    num(r["avg_entry"], 3),
                    num(r["v1_avg_abs_edge"], 3),
                    num(r["avg_abs_edge"], 3),
                ]
                for r in candidate_gate
            ],
        ),
        "## 口径说明",
        "",
        "- `pnl` 只来自 `fact_trades.pnl_usd_at_fill`，不重写 BUY_YES / BUY_NO 结算公式。",
        "- 日度按 `target_date` 归属，不用 `order_date_bj` 解释钱包现金流。",
        "- `submitted_orders` 是订单提交数量，不等于真实成交数量；真实成交看 `live_real fills`。",
        "- 未结算只报 `open_cost`，不混入 realized PnL。",
    ])

    return "\n".join(lines) + "\n"


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = build_report()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(REPORT_PATH)


if __name__ == "__main__":
    main()
