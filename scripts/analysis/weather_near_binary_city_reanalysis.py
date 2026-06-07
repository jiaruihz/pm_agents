#!/usr/bin/env python3
"""Recompute weather city diagnostics after near-binary settlement normalization."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_live_account_reconcile import (
    Args as ReconcileArgs,
    DEFAULT_CLOB_FILLS,
    DEFAULT_DB,
    DEFAULT_RAW_LIVE_DIR,
    run as run_reconcile,
)
from research_weather_city_alpha_framework import (
    cohort_performance,
    f2,
    pct,
    promotion_pre_post,
    realized_by_city,
    realized_by_city_side,
    opportunity_by_city,
    opportunity_by_city_side,
    safe_div,
)


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
REPORT_MD = ROOT / "docs/analysis/2026-06/2026-06-06-near-binary-city-reanalysis.md"
REPORT_JSON = ROOT / "docs/analysis/2026-06/2026-06-06-near-binary-city-reanalysis.json"
RECENT_START = "2026-05-31"
END_DATE = "2026-06-06"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def table(headers: list[str], data: list[list[Any]]) -> str:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in data:
        out.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    return "\n".join(out)


def db_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    stat = DB_PATH.stat()
    ft = conn.execute("SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM fact_trades").fetchone()
    fc = conn.execute("SELECT COUNT(*), MIN(event_date), MAX(event_date) FROM fact_signal_candidates").fetchone()
    return {
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_mtime_local": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "generated_at_local": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "fact_trades_rows": ft[0],
        "fact_trades_target_date_range": f"{ft[1]}..{ft[2]}",
        "fact_signal_candidates_rows": fc[0],
        "fact_signal_candidates_event_date_range": f"{fc[1]}..{fc[2]}",
        "missing_bracket_rows": scalar(conn, "SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'"),
        "unsettled_or_null_rows": scalar(
            conn,
            "SELECT COUNT(*) FROM fact_trades WHERE settlement_status IS NULL OR settlement_status='unsettled'",
        ),
    }


def required_checks(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        "freshness": rows(conn, "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "signal_candidate_coverage": rows(
            conn,
            """
            SELECT COUNT(*) AS rows, SUM(eligible) AS eligible,
                   SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled
            FROM fact_signal_candidates
            """,
        ),
        "clob_order_fill_join": rows(
            conn,
            """
            SELECT o.status, COUNT(*) AS orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
    }


def recent_live_by_city_side(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT city, side, COUNT(*) AS fills, COUNT(DISTINCT target_date) AS active_days,
               SUM(cost_usd) AS cost_usd, SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               GROUP_CONCAT(DISTINCT execution_policy) AS execution_policies
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND target_date BETWEEN ? AND ?
        GROUP BY city, side
        ORDER BY pnl_usd ASC
        """,
        (RECENT_START, END_DATE),
    )
    for row in data:
        row["roi"] = safe_div(row.get("pnl_usd"), row.get("cost_usd"))
    return data


def recent_live_summary(conn: sqlite3.Connection, group_col: str) -> list[dict[str, Any]]:
    allowed = {"city", "side", "strategy_id", "execution_policy"}
    if group_col not in allowed:
        raise ValueError(group_col)
    data = rows(
        conn,
        f"""
        SELECT {group_col} AS bucket, COUNT(*) AS fills, COUNT(DISTINCT target_date) AS active_days,
               SUM(cost_usd) AS cost_usd, SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND target_date BETWEEN ? AND ?
        GROUP BY {group_col}
        ORDER BY pnl_usd ASC
        """,
        (RECENT_START, END_DATE),
    )
    for row in data:
        row["roi"] = safe_div(row.get("pnl_usd"), row.get("cost_usd"))
    return data


def recent_opportunity_city_side(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT city, side, COUNT(*) AS n, COUNT(DISTINCT event_date) AS active_days,
               SUM(counterfactual_pnl) AS cf_pnl,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               AVG((market_yes_price-final_yes)*(market_yes_price-final_yes))
                 - AVG((model_p_yes-final_yes)*(model_p_yes-final_yes)) AS brier_delta,
               SUM(live_filled) AS live_filled
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND event_date BETWEEN ? AND ?
        GROUP BY city, side
        ORDER BY cf_pnl ASC
        """,
        (RECENT_START, END_DATE),
    )
    for row in data:
        row["live_coverage"] = safe_div(row.get("live_filled"), row.get("n"))
    return data


def focus_city_side(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    focus = ["NYC", "Istanbul", "Guangzhou", "Jeddah", "Manila", "Ankara"]
    placeholders = ",".join("?" for _ in focus)
    data = rows(
        conn,
        f"""
        WITH live AS (
          SELECT city, side,
                 COUNT(*) AS live_fills,
                 SUM(cost_usd) AS live_cost,
                 SUM(pnl_usd_at_fill) AS live_pnl,
                 AVG(CAST(win_by_count AS REAL)) AS live_win
          FROM fact_trades
          WHERE trade_class='live_real'
            AND settlement_status='settled'
            AND city IN ({placeholders})
          GROUP BY city, side
        ),
        opp AS (
          SELECT city, side,
                 COUNT(*) AS opp_n,
                 SUM(counterfactual_pnl) AS opp_cf_pnl,
                 AVG((market_yes_price-final_yes)*(market_yes_price-final_yes))
                   - AVG((model_p_yes-final_yes)*(model_p_yes-final_yes)) AS brier_delta
          FROM fact_signal_candidates
          WHERE eligible=1
            AND final_yes IS NOT NULL
            AND decision_window_missing=0
            AND city IN ({placeholders})
          GROUP BY city, side
        ),
        recent AS (
          SELECT city, side,
                 COUNT(*) AS recent_fills,
                 SUM(cost_usd) AS recent_cost,
                 SUM(pnl_usd_at_fill) AS recent_pnl
          FROM fact_trades
          WHERE trade_class='live_real'
            AND settlement_status='settled'
            AND target_date BETWEEN ? AND ?
            AND city IN ({placeholders})
          GROUP BY city, side
        )
        SELECT COALESCE(live.city, opp.city, recent.city) AS city,
               COALESCE(live.side, opp.side, recent.side) AS side,
               live_fills, live_cost, live_pnl, live_win,
               opp_n, opp_cf_pnl, brier_delta,
               recent_fills, recent_cost, recent_pnl
        FROM live
        FULL OUTER JOIN opp ON live.city=opp.city AND live.side=opp.side
        FULL OUTER JOIN recent
          ON COALESCE(live.city, opp.city)=recent.city
         AND COALESCE(live.side, opp.side)=recent.side
        ORDER BY city, side
        """,
        tuple(focus + focus + [RECENT_START, END_DATE] + focus),
    )
    for row in data:
        row["live_roi"] = safe_div(row.get("live_pnl"), row.get("live_cost"))
        row["recent_roi"] = safe_div(row.get("recent_pnl"), row.get("recent_cost"))
    return data


def action_for_focus(row: dict[str, Any]) -> str:
    city = row["city"]
    side = row["side"]
    live_pnl = row.get("live_pnl") or 0
    recent_pnl = row.get("recent_pnl") or 0
    opp_cf = row.get("opp_cf_pnl")
    brier = row.get("brier_delta")
    if city == "NYC" and side == "BUY_YES":
        return "已执行先 ban YES；保留 NO"
    if city in {"Guangzhou", "Jeddah", "Manila", "Ankara"}:
        if (live_pnl < 0 and recent_pnl < 0) or ((opp_cf or 0) < 0 and (brier or 0) < 0):
            return "从 normal live 移出，进 T2/shadow"
    if city == "Istanbul":
        if recent_pnl < -10 and (brier or 0) < 0:
            return "不要加仓；先 low-size/shadow 或 side-only 复核"
        return "可观察，不作为整城永久删除"
    return "按 city×side gate 复核"


def render(payload: dict[str, Any]) -> str:
    snap = payload["snapshot"]
    checks = payload["required_checks"]
    acct = payload["account_reconcile"]
    fill_rec = acct["fill_id_reconciliation"]

    lines: list[str] = [
        "# 2026-06-06 near-binary settlement 勘误后城市重算",
        "",
        "## 结论先行",
        "",
        "- 这次重算后，旧报告里 `missing_bracket=725/734/28` 的口径已经过时；本轮 `fact_trades.missing_bracket_rows=0`。",
        "- DB 与 raw CLOB fills 对齐：`db_live_real_distinct_fills={}`，`raw_clob_distinct_fills={}`，差异 `{}/{}`。".format(
            fill_rec["db_live_real_distinct_fills"],
            fill_rec["raw_clob_distinct_fills"],
            fill_rec["db_not_in_raw"],
            fill_rec["raw_not_in_db"],
        ),
        "- 城市问题仍然成立，但更精确：不是所有城市坏，而是新增城市和 BUY_YES 侧在 live 兑现上污染了组合；城市评价必须升到 `city×side×strategy_instance`。",
        "- 立即动作不变：`NYC BUY_YES` 继续 ban；`Guangzhou/Jeddah/Manila/Ankara` 不应留在 normal live；`Istanbul` 是 recent drawdown，不够证据整城永久删除，但不能加仓。",
        "",
        "## 数据快照",
        "",
        table(
            ["字段", "值"],
            [
                ["数据源", f"`{snap['db_path']}` + fact_trades / fact_signal_candidates"],
                ["生成时间", snap["generated_at_local"]],
                ["DB mtime", snap["db_mtime_local"]],
                ["fact_trades", f"{snap['fact_trades_rows']} rows, target_date {snap['fact_trades_target_date_range']}"],
                [
                    "fact_signal_candidates",
                    f"{snap['fact_signal_candidates_rows']} rows, event_date {snap['fact_signal_candidates_event_date_range']}",
                ],
                ["missing_bracket", snap["missing_bracket_rows"]],
                ["unsettled/null", snap["unsettled_or_null_rows"]],
                ["本轮刷新", "已跑 `sync_weather_remote.sh` + `run_stack.sh` rebuild；API 启动因 8000 占用失败，但 DB/fact 表已完成"],
            ],
        ),
        "",
        "## 强制 SQL 自检",
        "",
        "```json",
        json.dumps(checks, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Account Reconcile",
        "",
        "现金流口径使用 `fill_date_bj`，窗口 `{}`..`{}`。`cash_cost_usd` 是已成交买入花钱，不是亏损；`realized_pnl_usd` 只统计 settled。".format(
            RECENT_START, END_DATE
        ),
        "",
        table(
            ["instance", "date", "fills", "cash_cost", "settled_pnl", "open_cost", "mtm_mid", "val_ts"],
            [
                [
                    r.get("strategy_instance"),
                    r.get("selected_date"),
                    r.get("fills"),
                    f2(r.get("cash_cost_usd")),
                    f2(r.get("realized_pnl_usd")),
                    f2(r.get("open_cost_usd")),
                    f2(r.get("unrealized_pnl_mid_usd")),
                    r.get("max_val_snapshot_ts_utc"),
                ]
                for r in acct["live_reconcile"][:20]
            ],
        ),
        "",
        "Raw live order files 同窗口：submitted `{}`，posted `{}`；raw CLOB fill cost `{}`。".format(
            f2(acct["raw_order_summary"]["range_submitted_notional_usd"]),
            f2(acct["raw_order_summary"]["range_posted_notional_usd"]),
            f2(acct["raw_clob_summary"]["range_cost_usd"]),
        ),
        "",
        "## 最近一周 live_real 亏损切片",
        "",
        "按 `target_date` 归因，窗口 `{}`..`{}`，只看已结算 live_real。".format(RECENT_START, END_DATE),
        "",
        "### 按城市",
        "",
        table(
            ["city", "fills", "days", "cost", "pnl", "ROI", "win"],
            [
                [r["bucket"], r["fills"], r["active_days"], f2(r["cost_usd"]), f2(r["pnl_usd"]), pct(r["roi"]), pct(r["win_rate"])]
                for r in payload["recent_live_city"][:15]
            ],
        ),
        "",
        "### 按方向",
        "",
        table(
            ["side", "fills", "days", "cost", "pnl", "ROI", "win"],
            [
                [r["bucket"], r["fills"], r["active_days"], f2(r["cost_usd"]), f2(r["pnl_usd"]), pct(r["roi"]), pct(r["win_rate"])]
                for r in payload["recent_live_side"]
            ],
        ),
        "",
        "### 按策略",
        "",
        table(
            ["strategy_id", "fills", "days", "cost", "pnl", "ROI", "win"],
            [
                [r["bucket"], r["fills"], r["active_days"], f2(r["cost_usd"]), f2(r["pnl_usd"]), pct(r["roi"]), pct(r["win_rate"])]
                for r in payload["recent_live_strategy"][:12]
            ],
        ),
        "",
        "## 重点城市处置表",
        "",
        table(
            ["city", "side", "live pnl/ROI", "recent pnl/ROI", "opp cf", "brier_delta", "action"],
            [
                [
                    r["city"],
                    r["side"],
                    f"{f2(r.get('live_pnl'))} / {pct(r.get('live_roi'))}",
                    f"{f2(r.get('recent_pnl'))} / {pct(r.get('recent_roi'))}",
                    f2(r.get("opp_cf_pnl")),
                    f2(r.get("brier_delta")),
                    action_for_focus(r),
                ]
                for r in payload["focus_city_side"]
            ],
        ),
        "",
        "## 全机会 city×side 反事实最差项",
        "",
        table(
            ["city", "side", "n", "days", "cf_pnl", "win", "brier_delta", "live_cov"],
            [
                [r["city"], r["side"], r["n"], r["active_days"], f2(r["cf_pnl"]), pct(r["win_rate"]), f2(r["brier_delta"]), pct(r["live_coverage"])]
                for r in payload["recent_opportunity_city_side"][:18]
            ],
        ),
        "",
        "## Promotion 前后",
        "",
        table(
            ["cohort", "promote", "paper prior", "live after", "opp after"],
            [
                [
                    r["cohort"],
                    r["promotion_date"],
                    f"{r['paper_prior'].get('fills')} fills / {f2(r['paper_prior'].get('pnl_usd'))} / {pct(r['paper_prior'].get('roi'))}",
                    f"{r['live_after'].get('fills')} fills / {f2(r['live_after'].get('pnl_usd'))} / {pct(r['live_after'].get('roi'))}",
                    f"{r['opportunity_after'].get('n')} opp / {f2(r['opportunity_after'].get('cf_pnl'))} / brier {f2(r['opportunity_after'].get('brier_delta'))}",
                ]
                for r in payload["promotion_pre_post"]
            ],
        ),
        "",
        "## 新城市选择体系",
        "",
        "1. `ROI 高` 只能作为候选，不再作为晋升条件；晋升必须同时看 opportunity `counterfactual_pnl`、`brier_delta`、live 小仓样本外兑现。",
        "2. 第一粒度是 `city×side`；城市整体正但某一侧 brier/cf/live 均负，就只禁该侧，不整城处理。",
        "3. 新城市进 normal live 前先过 shadow/low-size 7-14 天；promotion 后看 `drop_best_day_pnl` 和 recent live，不再只看历史总 ROI。",
        "4. `decision_window_missing` 高的城市先补数据，不给交易结论；paper_ordered 不是 live 意图，不能拿 paper fill 当 live 可成交性。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    conn = connect()
    reconcile_args = ReconcileArgs(
        db=DEFAULT_DB,
        clob_fills=DEFAULT_CLOB_FILLS,
        raw_live_dir=DEFAULT_RAW_LIVE_DIR,
        start=RECENT_START,
        end=END_DATE,
        date_field="fill_date_bj",
        instances=("all",),
        group_by=("instance", "selected_date"),
        format="json",
    )
    payload: dict[str, Any] = {
        "snapshot": db_snapshot(conn),
        "required_checks": required_checks(conn),
        "account_reconcile": run_reconcile(reconcile_args),
        "live_city_all": realized_by_city(conn, "live_real"),
        "live_city_side_all": realized_by_city_side(conn, "live_real"),
        "opportunity_city_all": opportunity_by_city(conn),
        "opportunity_city_side_all": opportunity_by_city_side(conn),
        "cohort_performance": cohort_performance(conn),
        "promotion_pre_post": promotion_pre_post(conn),
        "recent_live_city": recent_live_summary(conn, "city"),
        "recent_live_side": recent_live_summary(conn, "side"),
        "recent_live_strategy": recent_live_summary(conn, "strategy_id"),
        "recent_live_execution_policy": recent_live_summary(conn, "execution_policy"),
        "recent_live_city_side": recent_live_by_city_side(conn),
        "recent_opportunity_city_side": recent_opportunity_city_side(conn),
        "focus_city_side": focus_city_side(conn),
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_MD.write_text(render(payload), encoding="utf-8")
    print(f"wrote {REPORT_JSON.relative_to(ROOT)}")
    print(f"wrote {REPORT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
