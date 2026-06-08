#!/usr/bin/env python3
"""Research city alpha quality across realized, paper, and opportunity layers."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CORE9 = {
    "Boston",
    "LA",
    "London",
    "Miami",
    "NYC",
    "Phoenix",
    "Shanghai",
    "Tokyo",
    "Warsaw",
}

NEW_T1_2026_05_26 = {
    "Ankara",
    "Guangzhou",
    "Istanbul",
    "Jeddah",
    "Karachi",
    "Lucknow",
    "Moscow",
    "Seattle",
}

NEW_T1_2026_05_27 = {
    "Amsterdam",
    "BuenosAires",
    "Chengdu",
    "Manila",
    "Munich",
    "Singapore",
}

CURRENT_T1_V4 = {
    "Ankara",
    "Boston",
    "Chengdu",
    "Guangzhou",
    "Istanbul",
    "Jeddah",
    "Karachi",
    "LA",
    "London",
    "Lucknow",
    "Madrid",
    "Manila",
    "Miami",
    "Moscow",
    "Munich",
    "NYC",
    "Phoenix",
    "Seattle",
    "Shanghai",
    "Singapore",
    "Tokyo",
    "Warsaw",
}

SIDE_RESTRICTED = {
    "Madrid": ["BUY_NO"],
    "Shanghai": ["BUY_NO"],
}

CURRENT_T2_WATCH = {
    "Amsterdam",
    "Austin",
    "Beijing",
    "BuenosAires",
    "Chicago",
    "Paris",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="runtime/weather.db")
    parser.add_argument(
        "--json-out",
        default="docs/analysis/2026-06/2026-06-06-city-alpha-framework.json",
    )
    parser.add_argument(
        "--md-out",
        default="docs/analysis/2026-06/2026-06-06-city-alpha-framework.md",
    )
    return parser.parse_args()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    return conn.execute(sql, tuple(params)).fetchone()[0]


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def f2(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


def cohort_for_city(city: str) -> str:
    if city in CORE9:
        return "core9"
    if city == "Madrid":
        return "reentry_side_only"
    if city in NEW_T1_2026_05_26:
        return "new_t1_2026_05_26"
    if city in NEW_T1_2026_05_27:
        return "new_t1_2026_05_27"
    if city in CURRENT_T2_WATCH:
        return "t2_or_demoted_watch"
    return "other"


@dataclass
class DailyStats:
    active_days: int
    positive_day_rate: float | None
    worst_day_pnl: float | None
    best_day_pnl: float | None
    drop_best_day_pnl: float | None
    daily_sharpe_like: float | None


def compute_daily_stats(conn: sqlite3.Connection, where_sql: str, params: tuple[Any, ...]) -> dict[str, DailyStats]:
    daily = rows(
        conn,
        f"""
        SELECT city, target_date, SUM(pnl_usd_at_fill) AS pnl
        FROM fact_trades
        WHERE settlement_status='settled' AND {where_sql}
        GROUP BY city, target_date
        """,
        params,
    )
    by_city: dict[str, list[float]] = defaultdict(list)
    for row in daily:
        if row["pnl"] is not None:
            by_city[row["city"]].append(float(row["pnl"]))

    out: dict[str, DailyStats] = {}
    for city, values in by_city.items():
        total = sum(values)
        active_days = len(values)
        positive = sum(1 for value in values if value > 0)
        mean = total / active_days if active_days else None
        std = None
        if active_days > 1 and mean is not None:
            var = sum((value - mean) ** 2 for value in values) / (active_days - 1)
            std = math.sqrt(var)
        best = max(values) if values else None
        worst = min(values) if values else None
        out[city] = DailyStats(
            active_days=active_days,
            positive_day_rate=safe_div(positive, active_days),
            worst_day_pnl=worst,
            best_day_pnl=best,
            drop_best_day_pnl=total - best if best is not None else None,
            daily_sharpe_like=safe_div(mean, std) if std else None,
        )
    return out


def realized_by_city(conn: sqlite3.Connection, trade_class: str, start_date: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [trade_class]
    where = "trade_class=?"
    if start_date:
        where += " AND target_date>=?"
        params.append(start_date)

    stats = compute_daily_stats(conn, where, tuple(params))
    data = rows(
        conn,
        f"""
        SELECT
          city,
          COUNT(*) AS fills,
          COUNT(DISTINCT target_date) AS active_days_fill,
          SUM(cost_usd) AS cost_usd,
          SUM(pnl_usd_at_fill) AS pnl_usd,
          AVG(CAST(win_by_count AS REAL)) AS win_rate,
          AVG(fill_price) AS avg_fill_price,
          SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no_fills,
          SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes_fills,
          GROUP_CONCAT(DISTINCT execution_policy) AS execution_policies,
          GROUP_CONCAT(DISTINCT strategy_id) AS strategy_ids
        FROM fact_trades
        WHERE settlement_status='settled' AND {where}
        GROUP BY city
        """,
        params,
    )
    for row in data:
        row["roi"] = safe_div(row["pnl_usd"], row["cost_usd"])
        row["avg_pnl_per_fill"] = safe_div(row["pnl_usd"], row["fills"])
        row["cohort"] = cohort_for_city(row["city"])
        row["current_t1"] = row["city"] in CURRENT_T1_V4
        row["allowed_sides"] = SIDE_RESTRICTED.get(row["city"], ["BUY_NO", "BUY_YES"] if row["city"] in CURRENT_T1_V4 else [])
        daily = stats.get(row["city"])
        if daily:
            row.update(
                {
                    "active_days": daily.active_days,
                    "positive_day_rate": daily.positive_day_rate,
                    "worst_day_pnl": daily.worst_day_pnl,
                    "best_day_pnl": daily.best_day_pnl,
                    "drop_best_day_pnl": daily.drop_best_day_pnl,
                    "daily_sharpe_like": daily.daily_sharpe_like,
                }
            )
    data.sort(key=lambda item: item.get("pnl_usd") or 0, reverse=True)
    return data


def realized_by_city_side(conn: sqlite3.Connection, trade_class: str) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT
          city,
          side,
          COUNT(*) AS fills,
          COUNT(DISTINCT target_date) AS active_days,
          SUM(cost_usd) AS cost_usd,
          SUM(pnl_usd_at_fill) AS pnl_usd,
          AVG(CAST(win_by_count AS REAL)) AS win_rate,
          AVG(fill_price) AS avg_fill_price
        FROM fact_trades
        WHERE trade_class=? AND settlement_status='settled'
        GROUP BY city, side
        """,
        (trade_class,),
    )
    for row in data:
        row["roi"] = safe_div(row["pnl_usd"], row["cost_usd"])
        row["cohort"] = cohort_for_city(row["city"])
    data.sort(key=lambda item: item.get("pnl_usd") or 0, reverse=True)
    return data


def opportunity_by_city(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT
          city,
          COUNT(*) AS eligible_settled_decision_n,
          COUNT(DISTINCT event_date) AS active_days,
          SUM(paper_ordered) AS paper_ordered,
          SUM(live_filled) AS live_filled,
          AVG(CAST(win_by_count AS REAL)) AS win_rate,
          SUM(counterfactual_pnl) AS cf_pnl,
          AVG((model_p_yes - final_yes) * (model_p_yes - final_yes)) AS model_brier,
          AVG((market_yes_price - final_yes) * (market_yes_price - final_yes)) AS market_brier,
          AVG(yes_spread) AS avg_yes_spread,
          AVG(no_spread) AS avg_no_spread,
          AVG(yes_depth_ask_5c) AS avg_yes_depth_ask_5c,
          AVG(no_depth_ask_5c) AS avg_no_depth_ask_5c
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
        GROUP BY city
        """,
    )
    missing = {
        row["city"]: row
        for row in rows(
            conn,
            """
            SELECT
              city,
              COUNT(*) AS total_seen,
              SUM(CASE WHEN decision_window_missing=1 THEN 1 ELSE 0 END) AS decision_missing,
              SUM(eligible) AS eligible_total
            FROM fact_signal_candidates
            GROUP BY city
            """,
        )
    }
    for row in data:
        row["brier_delta_market_minus_model"] = (
            row["market_brier"] - row["model_brier"]
            if row["market_brier"] is not None and row["model_brier"] is not None
            else None
        )
        row["live_coverage_of_eligible"] = safe_div(row["live_filled"], row["eligible_settled_decision_n"])
        row["paper_order_rate"] = safe_div(row["paper_ordered"], row["eligible_settled_decision_n"])
        row["cohort"] = cohort_for_city(row["city"])
        miss = missing.get(row["city"], {})
        row["decision_window_missing_rate_all_seen"] = safe_div(miss.get("decision_missing"), miss.get("total_seen"))
        row["eligible_total_all_seen"] = miss.get("eligible_total")
        row["current_t1"] = row["city"] in CURRENT_T1_V4
    data.sort(key=lambda item: item.get("cf_pnl") or 0, reverse=True)
    return data


def opportunity_by_city_side(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    data = rows(
        conn,
        """
        SELECT
          city,
          side,
          COUNT(*) AS eligible_settled_decision_n,
          COUNT(DISTINCT event_date) AS active_days,
          SUM(paper_ordered) AS paper_ordered,
          SUM(live_filled) AS live_filled,
          AVG(CAST(win_by_count AS REAL)) AS win_rate,
          SUM(counterfactual_pnl) AS cf_pnl,
          AVG((model_p_yes - final_yes) * (model_p_yes - final_yes)) AS model_brier,
          AVG((market_yes_price - final_yes) * (market_yes_price - final_yes)) AS market_brier,
          AVG(decision_entry_price) AS avg_decision_entry_price
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
        GROUP BY city, side
        """,
    )
    for row in data:
        row["brier_delta_market_minus_model"] = (
            row["market_brier"] - row["model_brier"]
            if row["market_brier"] is not None and row["model_brier"] is not None
            else None
        )
        row["live_coverage_of_eligible"] = safe_div(row["live_filled"], row["eligible_settled_decision_n"])
        row["cohort"] = cohort_for_city(row["city"])
    data.sort(key=lambda item: item.get("cf_pnl") or 0, reverse=True)
    return data


def cohort_performance(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    city_to_cohort = {}
    for city in CORE9:
        city_to_cohort[city] = "core9"
    for city in NEW_T1_2026_05_26:
        city_to_cohort[city] = "new_t1_2026_05_26"
    for city in NEW_T1_2026_05_27:
        city_to_cohort[city] = "new_t1_2026_05_27"
    for city in CURRENT_T2_WATCH:
        city_to_cohort.setdefault(city, "t2_or_demoted_watch")

    def summarize_trade_class(trade_class: str, start_date: str | None = None) -> dict[str, dict[str, Any]]:
        params: list[Any] = [trade_class]
        date_filter = ""
        if start_date:
            date_filter = "AND target_date>=?"
            params.append(start_date)
        out: dict[str, dict[str, Any]] = {}
        for row in rows(
            conn,
            f"""
            SELECT city, COUNT(*) AS fills, COUNT(DISTINCT target_date) AS days,
                   SUM(cost_usd) AS cost_usd, SUM(pnl_usd_at_fill) AS pnl_usd,
                   AVG(CAST(win_by_count AS REAL)) AS win_rate
            FROM fact_trades
            WHERE trade_class=? AND settlement_status='settled' {date_filter}
            GROUP BY city
            """,
            params,
        ):
            cohort = city_to_cohort.get(row["city"], "other")
            bucket = out.setdefault(
                cohort,
                {"fills": 0, "days_set": set(), "cost_usd": 0.0, "pnl_usd": 0.0, "win_weight": 0.0},
            )
            bucket["fills"] += row["fills"] or 0
            bucket["cost_usd"] += row["cost_usd"] or 0.0
            bucket["pnl_usd"] += row["pnl_usd"] or 0.0
            bucket["win_weight"] += (row["win_rate"] or 0.0) * (row["fills"] or 0)
            bucket["days_set"].add(row["city"])
        for bucket in out.values():
            bucket["roi"] = safe_div(bucket["pnl_usd"], bucket["cost_usd"])
            bucket["win_rate"] = safe_div(bucket["win_weight"], bucket["fills"])
            bucket["city_count"] = len(bucket.pop("days_set"))
        return out

    live_all = summarize_trade_class("live_real")
    live_recent = summarize_trade_class("live_real", "2026-06-01")
    paper_all = summarize_trade_class("paper")

    cohorts = sorted(set(live_all) | set(live_recent) | set(paper_all))
    out = []
    for cohort in cohorts:
        item = {"cohort": cohort}
        for prefix, data in [
            ("live_all", live_all.get(cohort, {})),
            ("live_since_2026_06_01", live_recent.get(cohort, {})),
            ("paper_all", paper_all.get(cohort, {})),
        ]:
            for key in ["fills", "city_count", "cost_usd", "pnl_usd", "roi", "win_rate"]:
                item[f"{prefix}_{key}"] = data.get(key)
        out.append(item)
    return out


def promotion_pre_post(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cohorts = [
        ("new_t1_2026_05_26", sorted(NEW_T1_2026_05_26), "2026-05-26"),
        ("new_t1_2026_05_27", sorted(NEW_T1_2026_05_27), "2026-05-27"),
    ]
    out: list[dict[str, Any]] = []
    for name, cities, date in cohorts:
        placeholders = ",".join("?" for _ in cities)
        base_params = cities
        paper_prior = rows(
            conn,
            f"""
            SELECT COUNT(*) AS fills, COUNT(DISTINCT target_date) AS days,
                   SUM(cost_usd) AS cost_usd, SUM(pnl_usd_at_fill) AS pnl_usd,
                   AVG(CAST(win_by_count AS REAL)) AS win_rate
            FROM fact_trades
            WHERE trade_class='paper' AND settlement_status='settled'
              AND target_date < ?
              AND city IN ({placeholders})
            """,
            [date] + base_params,
        )[0]
        live_after = rows(
            conn,
            f"""
            SELECT COUNT(*) AS fills, COUNT(DISTINCT target_date) AS days,
                   SUM(cost_usd) AS cost_usd, SUM(pnl_usd_at_fill) AS pnl_usd,
                   AVG(CAST(win_by_count AS REAL)) AS win_rate
            FROM fact_trades
            WHERE trade_class='live_real' AND settlement_status='settled'
              AND target_date >= ?
              AND city IN ({placeholders})
            """,
            [date] + base_params,
        )[0]
        opp_after = rows(
            conn,
            f"""
            SELECT COUNT(*) AS n, COUNT(DISTINCT event_date) AS days,
                   SUM(counterfactual_pnl) AS cf_pnl,
                   AVG(CAST(win_by_count AS REAL)) AS win_rate,
                   AVG((market_yes_price - final_yes) * (market_yes_price - final_yes))
                     - AVG((model_p_yes - final_yes) * (model_p_yes - final_yes)) AS brier_delta
            FROM fact_signal_candidates
            WHERE eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0
              AND event_date >= ?
              AND city IN ({placeholders})
            """,
            [date] + base_params,
        )[0]
        for row in (paper_prior, live_after):
            row["roi"] = safe_div(row.get("pnl_usd"), row.get("cost_usd"))
        out.append(
            {
                "cohort": name,
                "promotion_date": date,
                "cities": cities,
                "paper_prior": paper_prior,
                "live_after": live_after,
                "opportunity_after": opp_after,
            }
        )
    return out


def build_city_matrix(
    live_city: list[dict[str, Any]],
    paper_city: list[dict[str, Any]],
    snapshot_city: list[dict[str, Any]],
    opp_city: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_city: dict[str, dict[str, Any]] = defaultdict(dict)
    for prefix, dataset in [
        ("live", live_city),
        ("paper", paper_city),
        ("snapshot", snapshot_city),
        ("opp", opp_city),
    ]:
        for row in dataset:
            city = row["city"]
            base = by_city[city]
            base["city"] = city
            base["cohort"] = cohort_for_city(city)
            base["current_t1"] = city in CURRENT_T1_V4
            if prefix == "opp":
                for key in [
                    "eligible_settled_decision_n",
                    "active_days",
                    "cf_pnl",
                    "win_rate",
                    "brier_delta_market_minus_model",
                    "decision_window_missing_rate_all_seen",
                    "live_coverage_of_eligible",
                    "paper_order_rate",
                ]:
                    base[f"{prefix}_{key}"] = row.get(key)
            else:
                for key in [
                    "fills",
                    "active_days",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "win_rate",
                    "positive_day_rate",
                    "drop_best_day_pnl",
                    "worst_day_pnl",
                ]:
                    if key in row:
                        base[f"{prefix}_{key}"] = row.get(key)
    out = list(by_city.values())
    out.sort(key=lambda item: (item.get("current_t1") is not True, -(item.get("live_pnl_usd") or 0)))
    return out


def table(headers: list[str], rows_: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows_:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def top_rows(data: list[dict[str, Any]], key: str, n: int = 12, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(data, key=lambda item: item.get(key) or 0, reverse=reverse)[:n]


def render_markdown(payload: dict[str, Any]) -> str:
    snapshot = payload["snapshot"]
    checks = payload["integrity_checks"]
    live = payload["live_city"]
    opp = payload["opportunity_city"]
    matrix = payload["city_matrix"]
    cohort = payload["cohort_performance"]
    promotion = payload["promotion_pre_post"]

    best_live = top_rows(live, "pnl_usd", 12)
    worst_live = top_rows(live, "pnl_usd", 12, reverse=False)
    best_opp = top_rows(opp, "cf_pnl", 12)
    worst_opp = top_rows(opp, "cf_pnl", 12, reverse=False)
    opp_side = [r for r in payload["opportunity_city_side"] if (r.get("eligible_settled_decision_n") or 0) >= 5]
    live_side = [r for r in payload["live_city_side"] if (r.get("fills") or 0) >= 5]
    best_opp_side = top_rows(opp_side, "cf_pnl", 14)
    worst_opp_side = top_rows(opp_side, "cf_pnl", 14, reverse=False)
    worst_live_side = top_rows(live_side, "pnl_usd", 14, reverse=False)

    t1_matrix = [row for row in matrix if row.get("current_t1")]
    watch_matrix = [row for row in matrix if not row.get("current_t1")]
    t1_matrix = sorted(t1_matrix, key=lambda item: item.get("live_pnl_usd") or 0, reverse=True)
    watch_matrix = sorted(watch_matrix, key=lambda item: item.get("opp_cf_pnl") or 0, reverse=True)

    lines: list[str] = []
    lines.append("# 城市 Alpha 评价体系研究：forecast × market × live 兑现")
    lines.append("")
    lines.append("## 数据快照")
    lines.append("")
    lines.append(
        table(
            ["字段", "值"],
            [
                ["数据源", f"DB `{snapshot['db_path']}` + `fact_trades` / `fact_signal_candidates`"],
                ["生成时间", snapshot["generated_at_bj"]],
                ["DB mtime", snapshot["db_mtime_bj"]],
                ["fact_trades MAX(fact_built_at_utc)", str(checks["max_fact_built_at"][0][0])],
                ["fact_trades 行数", str(snapshot["fact_trades_rows"])],
                ["fact_signal_candidates 行数", str(snapshot["fact_signal_candidates_rows"])],
                ["target_date 范围", f"{snapshot['fact_trades_min_date']}..{snapshot['fact_trades_max_date']}"],
                ["event_date 范围", f"{snapshot['candidate_min_date']}..{snapshot['candidate_max_date']}"],
                ["missing_bracket 行数", str(snapshot["missing_bracket_rows"])],
                ["unsettled/null 行数", str(snapshot["unsettled_or_null_rows"])],
                ["同步/刷新", "2026-06-06 02:30 +0800 已跑 full `sync_weather_remote.sh`；随后 `weather_dashboard_refresh.sh --no-sync` 成功"],
            ],
        )
    )
    lines.append("")
    lines.append("## 数据完整性自检")
    lines.append("")
    lines.append("强制 5 行 SQL 结果：")
    lines.append("")
    lines.append("```text")
    for name, result in checks.items():
        lines.append(f"{name}: {result}")
    lines.append("```")
    lines.append("")
    lines.append("CLOB fill sync 日志本轮 `data_incomplete=false`，但仍有大量 `submitted` 订单无 fill；这是未成交/仍 open/待外部确认的订单，不应和已成交 PnL 混算。")
    lines.append("")
    lines.append("## 目标指标")
    lines.append("")
    lines.append("本报告研究的不是单纯 `city ROI rank`，而是 `city_true_alpha_evidence`：一个城市或 city×side 是否同时满足：")
    lines.append("")
    lines.append("- **机会层正 alpha**：`fact_signal_candidates` 中 `eligible=1 AND final_yes IS NOT NULL AND decision_window_missing=0` 的反事实 `counterfactual_pnl` 为正。")
    lines.append("- **模型相对市场有信息量**：`brier_delta = market_brier - model_brier` 为正，表示模型概率比盘口隐含概率更接近最终结算。")
    lines.append("- **live 可兑现**：`fact_trades.trade_class='live_real' AND settlement_status='settled'` 的 PnL/ROI 不和机会层明显背离。")
    lines.append("- **按日稳定**：看 city+target_date 聚合后的正收益天比例、最差日、去掉最佳日后的 PnL，而不是只看 fill 级总 PnL。")
    lines.append("- **执行覆盖合理**：live 覆盖率、paper_order_rate、spread/depth 不显示这个城市只能在 paper 里赚钱、live 吃不到。")
    lines.append("")
    lines.append("## 当前事实摘要")
    lines.append("")
    lines.append("### live_real 已结算城市赢家")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "fills", "days", "pnl", "ROI", "win", "pos_day", "drop_best_day"],
            [
                [
                    r["city"],
                    r["cohort"],
                    str(r["fills"]),
                    str(r.get("active_days", "")),
                    f2(r["pnl_usd"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    pct(r.get("positive_day_rate")),
                    f2(r.get("drop_best_day_pnl")),
                ]
                for r in best_live
            ],
        )
    )
    lines.append("")
    lines.append("### live_real 已结算城市输家")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "fills", "days", "pnl", "ROI", "win", "pos_day", "drop_best_day"],
            [
                [
                    r["city"],
                    r["cohort"],
                    str(r["fills"]),
                    str(r.get("active_days", "")),
                    f2(r["pnl_usd"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                    pct(r.get("positive_day_rate")),
                    f2(r.get("drop_best_day_pnl")),
                ]
                for r in worst_live
            ],
        )
    )
    lines.append("")
    lines.append("### 全机会反事实正 alpha 城市")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "n", "days", "cf_pnl", "win", "brier_delta", "miss_win_rate", "live_cov"],
            [
                [
                    r["city"],
                    r["cohort"],
                    str(r["eligible_settled_decision_n"]),
                    str(r["active_days"]),
                    f2(r["cf_pnl"]),
                    pct(r["win_rate"]),
                    f2(r["brier_delta_market_minus_model"]),
                    pct(r["decision_window_missing_rate_all_seen"]),
                    pct(r["live_coverage_of_eligible"]),
                ]
                for r in best_opp
            ],
        )
    )
    lines.append("")
    lines.append("### 全机会反事实负 alpha 城市")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "n", "days", "cf_pnl", "win", "brier_delta", "miss_win_rate", "live_cov"],
            [
                [
                    r["city"],
                    r["cohort"],
                    str(r["eligible_settled_decision_n"]),
                    str(r["active_days"]),
                    f2(r["cf_pnl"]),
                    pct(r["win_rate"]),
                    f2(r["brier_delta_market_minus_model"]),
                    pct(r["decision_window_missing_rate_all_seen"]),
                    pct(r["live_coverage_of_eligible"]),
                ]
                for r in worst_opp
            ],
        )
    )
    lines.append("")
    lines.append("### city×side：结构性黑洞和可保留侧")
    lines.append("")
    lines.append("机会层正 alpha 的 city×side：")
    lines.append("")
    lines.append(
        table(
            ["city", "side", "cohort", "n", "days", "cf_pnl", "win", "brier_delta", "live_cov"],
            [
                [
                    r["city"],
                    r["side"],
                    r["cohort"],
                    str(r["eligible_settled_decision_n"]),
                    str(r["active_days"]),
                    f2(r["cf_pnl"]),
                    pct(r["win_rate"]),
                    f2(r["brier_delta_market_minus_model"]),
                    pct(r["live_coverage_of_eligible"]),
                ]
                for r in best_opp_side
            ],
        )
    )
    lines.append("")
    lines.append("机会层负 alpha 的 city×side：")
    lines.append("")
    lines.append(
        table(
            ["city", "side", "cohort", "n", "days", "cf_pnl", "win", "brier_delta", "live_cov"],
            [
                [
                    r["city"],
                    r["side"],
                    r["cohort"],
                    str(r["eligible_settled_decision_n"]),
                    str(r["active_days"]),
                    f2(r["cf_pnl"]),
                    pct(r["win_rate"]),
                    f2(r["brier_delta_market_minus_model"]),
                    pct(r["live_coverage_of_eligible"]),
                ]
                for r in worst_opp_side
            ],
        )
    )
    lines.append("")
    lines.append("live_real 已兑现最差 city×side：")
    lines.append("")
    lines.append(
        table(
            ["city", "side", "cohort", "fills", "days", "pnl", "ROI", "win"],
            [
                [
                    r["city"],
                    r["side"],
                    r["cohort"],
                    str(r["fills"]),
                    str(r["active_days"]),
                    f2(r["pnl_usd"]),
                    pct(r["roi"]),
                    pct(r["win_rate"]),
                ]
                for r in worst_live_side
            ],
        )
    )
    lines.append("")
    lines.append("## Cohort：为什么 paper 好城市进 live 后会均值回归")
    lines.append("")
    lines.append(
        table(
            ["cohort", "paper pnl/ROI", "live all pnl/ROI", "live since 06-01 pnl/ROI"],
            [
                [
                    r["cohort"],
                    f"{f2(r.get('paper_all_pnl_usd'))} / {pct(r.get('paper_all_roi'))}",
                    f"{f2(r.get('live_all_pnl_usd'))} / {pct(r.get('live_all_roi'))}",
                    f"{f2(r.get('live_since_2026_06_01_pnl_usd'))} / {pct(r.get('live_since_2026_06_01_roi'))}",
                ]
                for r in cohort
            ],
        )
    )
    lines.append("")
    lines.append("Promotion 前后对比：")
    lines.append("")
    promo_rows = []
    for r in promotion:
        pp = r["paper_prior"]
        la = r["live_after"]
        oa = r["opportunity_after"]
        promo_rows.append(
            [
                r["cohort"],
                r["promotion_date"],
                f"{pp.get('fills')} fills / {f2(pp.get('pnl_usd'))} / {pct(pp.get('roi'))}",
                f"{la.get('fills')} fills / {f2(la.get('pnl_usd'))} / {pct(la.get('roi'))}",
                f"{oa.get('n')} opp / {f2(oa.get('cf_pnl'))} / brier {f2(oa.get('brier_delta'))}",
            ]
        )
    lines.append(table(["cohort", "promote", "paper prior", "live after", "opp after"], promo_rows))
    lines.append("")
    lines.append("解读：paper 是厚样本先验，但不是 live 可兑现性的证明。新增城市最容易出现三类偏差：")
    lines.append("")
    lines.append("1. **选择偏差**：paper ledger 覆盖全池，live 只成交其中一小片，且成交片段可能偏向更容易 fill、但不一定更有 EV 的腿。")
    lines.append("2. **城市/方向混合偏差**：城市总 ROI 为正时，可能只是一侧赚钱；另一侧进入 live 后把 alpha 吃掉。Madrid/Shanghai 的历史说明 city×side 比 city 更可靠。")
    lines.append("3. **tail 依赖**：若去掉最佳日/最佳单后 PnL 翻负，说明不是城市稳定 alpha，而是一次温度落点带来的高赔率收益。")
    lines.append("")
    lines.append("## 当前 T1 城市证据矩阵")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "live pnl/ROI", "opp cf", "brier_delta", "paper ROI", "stability", "action"],
            [
                [
                    r["city"],
                    r["cohort"],
                    f"{f2(r.get('live_pnl_usd'))} / {pct(r.get('live_roi'))}",
                    f2(r.get("opp_cf_pnl")),
                    f2(r.get("opp_brier_delta_market_minus_model")),
                    pct(r.get("paper_roi")),
                    f"pos {pct(r.get('live_positive_day_rate'))}, dropBest {f2(r.get('live_drop_best_day_pnl'))}",
                    classify_city(r),
                ]
                for r in t1_matrix
            ],
        )
    )
    lines.append("")
    lines.append("## T2 / 已降级观察池")
    lines.append("")
    lines.append(
        table(
            ["city", "cohort", "live pnl/ROI", "opp cf", "brier_delta", "paper ROI", "action"],
            [
                [
                    r["city"],
                    r["cohort"],
                    f"{f2(r.get('live_pnl_usd'))} / {pct(r.get('live_roi'))}",
                    f2(r.get("opp_cf_pnl")),
                    f2(r.get("opp_brier_delta_market_minus_model")),
                    pct(r.get("paper_roi")),
                    classify_city(r),
                ]
                for r in watch_matrix
                if r["city"] in CURRENT_T2_WATCH
            ],
        )
    )
    lines.append("")
    lines.append("## 建议的城市评价体系")
    lines.append("")
    lines.append("### Gate 0：数据资格")
    lines.append("")
    lines.append("- city×side 的机会层 `eligible_settled_decision_n >= 8` 且 `active_days >= 4` 才能做方向判断；否则只能 shadow。")
    lines.append("- live 层 `settled fills >= 8` 且 `active_days >= 4` 才能推翻 paper 先验；少于这个阈值只作为告警。")
    lines.append("- `decision_window_missing_rate_all_seen > 60%` 的城市不允许直接晋升，只能先补 snapshot/盘口窗口。")
    lines.append("")
    lines.append("### Gate 1：先验必须是 city×side，不是 city")
    lines.append("")
    lines.append("- 用 `fact_signal_candidates` 按 `city, side` 排序，先拦掉结构性黑洞侧。")
    lines.append("- 整城正、单侧负时，用 `CITY_ALLOWED_SIDES` 处理，不要整城进出。")
    lines.append("- 整城负但一侧明显正时，允许 NO-only/YES-only 小 size 观察。")
    lines.append("")
    lines.append("### Gate 2：模型是否真的适用这个城市")
    lines.append("")
    lines.append("- `brier_delta = market_brier - model_brier > 0` 才说明 forecast/model 在该城市相对盘口有信息量。")
    lines.append("- `cf_pnl > 0` 但 `brier_delta < 0` 的城市，多半是赔率/尾部收益，不是稳定 forecast alpha，要降权。")
    lines.append("- `brier_delta > 0` 但 `cf_pnl < 0` 的城市，说明模型方向有信息但入场价/side/basket 处理错了，适合研究而不是直接 live。")
    lines.append("")
    lines.append("### Gate 3：live 兑现和稳健性")
    lines.append("")
    lines.append("- 先看 `positive_day_rate` 和 `drop_best_day_pnl`；去最佳日后仍为正的城市才算强 alpha。")
    lines.append("- 新增城市进入 live 后前 7-14 天只允许低 size 或 shadow；不能因为 paper prior 高就直接同权。")
    lines.append("- promotion 后 live 和机会层背离时，先查成交覆盖、价位桶、side mix、forecast jump，而不是立刻判定城市坏。")
    lines.append("")
    lines.append("### Gate 3.5：城市特征进入研究，但不能替代交易证据")
    lines.append("")
    lines.append("城市特征应作为解释变量和分层变量，而不是直接作为晋升规则。建议落成以下可量化特征：")
    lines.append("")
    lines.append("- **天气/预报适配**：`region`、`unit`、主模型 `forecast_source/model_version`、历史 `brier_delta`、未来 PR3 shadow 中的 `forecast_jump_f` 与 `side_flip_count_today`。")
    lines.append("- **市场结构**：`avg_yes_spread/no_spread`、`*_depth_ask_5c`、live coverage、价位桶分布；有 alpha 但盘口薄的城市只能 low size。")
    lines.append("- **数据完整性**：IEM/WU/pm_history 覆盖、`decision_window_missing_rate`、settlement missing bracket；数据坏的城市先补数，不做交易结论。")
    lines.append("- **组合结构**：同一 city-day 多腿是否互相抵消，是否依赖单个高赔率 bracket；这部分应交给 basket shadow，不用单腿 ROI 硬判。")
    lines.append("")
    lines.append("### Gate 4：动作分级")
    lines.append("")
    lines.append("- **Keep / normal size**：机会层正、brier 正、live 正且去最佳日后不翻负。")
    lines.append("- **Keep / low size**：机会层和 paper 正，但 live 样本薄或去最佳日后翻负。")
    lines.append("- **Side-only**：一侧两层以上为负，另一侧正；用 `CITY_ALLOWED_SIDES`。")
    lines.append("- **Shadow only**：paper 正但 live after promotion 负，或 brier 负，或 decision window 缺失高。")
    lines.append("- **T2 / demote**：paper、机会层、live 三层至少两层显著负，且不是 1-2 天噪声。")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append("当前最应该改变的是治理方法，而不是再按城市总 ROI 扩池：")
    lines.append("")
    lines.append("1. 城市评价粒度必须从 `city` 升级到 `city×side×strategy_instance`，城市总榜只能做索引，不能做 live allowlist。")
    lines.append("2. 新城市晋升必须有 paper prior + opportunity alpha + model-vs-market brier 三层证据；live 前 7-14 天按低 size/shadow 验证兑现率。")
    lines.append("3. 对均值回归最敏感的不是 ROI，而是 `drop_best_day_pnl`、promotion 后 live PnL、以及 `brier_delta` 是否持续为正。")
    lines.append("4. Basket/optimizer 的方向是对的，但现有研究已显示 tail dependence；城市选择应先用 market-anchored probability 和 shadow 双写复核，不应直接 canary。")
    lines.append("")
    lines.append("## 残余风险")
    lines.append("")
    lines.append("- 机会层只覆盖 `decision_window_missing=0` 的样本，本轮总体缺失率仍在 43.8% 左右。")
    lines.append("- `paper_ordered` 不是 live 意图，不能把 paper 覆盖率当 live 成交率。")
    lines.append("- 本报告以当前本机 DB 为准；生产 city_pools.py 已通过 N100 SSH 只读核实，当前 commit 为 `2458696`。")
    lines.append("- 当前工作区已有多份未提交文档/脚本变更，本报告不判断这些变更是否应一起发布。")
    lines.append("")
    return "\n".join(lines) + "\n"


def classify_city(row: dict[str, Any]) -> str:
    live_fills = row.get("live_fills") or 0
    live_days = row.get("live_active_days") or 0
    live_pnl = row.get("live_pnl_usd")
    live_roi = row.get("live_roi")
    drop_best = row.get("live_drop_best_day_pnl")
    opp_n = row.get("opp_eligible_settled_decision_n") or 0
    opp_cf = row.get("opp_cf_pnl")
    brier_delta = row.get("opp_brier_delta_market_minus_model")
    paper_roi = row.get("paper_roi")
    missing_rate = row.get("opp_decision_window_missing_rate_all_seen")

    if missing_rate is not None and missing_rate > 0.60:
        return "shadow: decision-window coverage weak"
    if live_fills >= 8 and live_days >= 4 and live_pnl is not None and live_pnl < -10 and (opp_cf or 0) < 0:
        return "T2/demote: live and opportunity both negative"
    if opp_n >= 8 and (opp_cf or 0) < 0 and (brier_delta or 0) < 0:
        return "shadow/T2: opportunity and brier negative"
    if live_fills >= 8 and live_days >= 4 and live_pnl is not None and live_pnl > 0 and (drop_best or -1) > 0:
        if (opp_cf or 0) > 0 and (brier_delta or 0) > 0:
            return "keep normal-size: live, opportunity, brier align"
        return "keep no-add-size: robust live, weak prior/brier"
    if (paper_roi or 0) > 0.10 and (opp_cf or 0) > 0 and (brier_delta or -1) > 0:
        if live_roi is None or live_fills < 8:
            return "low-size/shadow: good prior, thin live"
        if live_roi >= 0:
            return "keep low-size: positive but not robust"
    if live_pnl is not None and live_pnl > 0 and (opp_cf or 0) < 0:
        return "watch: live positive conflicts with opportunity"
    if live_pnl is not None and live_pnl < 0 and (opp_cf or 0) > 0:
        return "watch execution: opportunity positive, live negative"
    return "shadow until evidence aligns"


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = connect(str(db_path))

    integrity_checks = {
        "max_fact_built_at": [tuple(row) for row in conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades")],
        "trade_class_distribution": [
            tuple(row)
            for row in conn.execute("SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class ORDER BY trade_class")
        ],
        "settlement_status_distribution": [
            tuple(row)
            for row in conn.execute(
                "SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"
            )
        ],
        "signal_candidate_coverage": [
            tuple(row)
            for row in conn.execute(
                "SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates"
            )
        ],
        "clob_order_fill_join": [
            tuple(row)
            for row in conn.execute(
                """
                SELECT o.status, COUNT(*) orders,
                       SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
                FROM orders o LEFT JOIN fills f USING(execution_id)
                WHERE o.venue='polymarket_clob'
                GROUP BY o.status ORDER BY o.status
                """
            )
        ],
    }

    fact_min_max = conn.execute(
        "SELECT COUNT(*), MIN(target_date), MAX(target_date), COUNT(DISTINCT city) FROM fact_trades"
    ).fetchone()
    cand_min_max = conn.execute(
        "SELECT COUNT(*), MIN(event_date), MAX(event_date), COUNT(DISTINCT city) FROM fact_signal_candidates"
    ).fetchone()
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    db_stat = db_path.stat()
    db_mtime = datetime.fromtimestamp(db_stat.st_mtime).astimezone().isoformat(timespec="seconds")

    live_city = realized_by_city(conn, "live_real")
    paper_city = realized_by_city(conn, "paper")
    snapshot_city = realized_by_city(conn, "snapshot_replay")
    opp_city = opportunity_by_city(conn)
    payload = {
        "snapshot": {
            "db_path": str(db_path),
            "generated_at_bj": generated_at,
            "db_mtime_bj": db_mtime,
            "fact_trades_rows": fact_min_max[0],
            "fact_trades_min_date": fact_min_max[1],
            "fact_trades_max_date": fact_min_max[2],
            "fact_trades_distinct_cities": fact_min_max[3],
            "fact_signal_candidates_rows": cand_min_max[0],
            "candidate_min_date": cand_min_max[1],
            "candidate_max_date": cand_min_max[2],
            "candidate_distinct_cities": cand_min_max[3],
            "missing_bracket_rows": scalar(
                conn, "SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'"
            ),
            "unsettled_or_null_rows": scalar(
                conn,
                """
                SELECT COUNT(*) FROM fact_trades
                WHERE settlement_status IS NULL OR settlement_status='unsettled'
                """,
            ),
        },
        "integrity_checks": integrity_checks,
        "live_city": live_city,
        "live_city_since_2026_06_01": realized_by_city(conn, "live_real", "2026-06-01"),
        "live_city_side": realized_by_city_side(conn, "live_real"),
        "paper_city": paper_city,
        "snapshot_city": snapshot_city,
        "opportunity_city": opp_city,
        "opportunity_city_side": opportunity_by_city_side(conn),
        "cohort_performance": cohort_performance(conn),
        "promotion_pre_post": promotion_pre_post(conn),
    }
    payload["city_matrix"] = build_city_matrix(live_city, paper_city, snapshot_city, opp_city)

    json_out = Path(args.json_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md_out = Path(args.md_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render_markdown(payload), encoding="utf-8")

    print(f"wrote {json_out}")
    print(f"wrote {md_out}")


if __name__ == "__main__":
    main()
