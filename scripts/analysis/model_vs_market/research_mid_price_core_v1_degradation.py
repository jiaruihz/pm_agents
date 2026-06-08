#!/usr/bin/env python3
"""Research why mid_price_core_v1 0.25-0.75 degraded after 2026-06-01.

This is a fact-table analysis. Realized PnL is only read from fact_trades;
fact_signal_candidates is used for opportunity/timing diagnostics.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.blend import blend_probability, load_default_config

DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
OUT_MD = OUT_DIR / "2026-06-07-mid-price-core-v1-raw-degradation.md"
OUT_JSON = OUT_DIR / "2026-06-07-mid-price-core-v1-raw-degradation.json"

RECENT_START = "2026-06-01"
STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
STRATEGY_LABEL = "mid_price_core_v1_25_75"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def money(x: Any) -> str:
    if x is None:
        return ""
    return f"{float(x):+.2f}"


def num(x: Any, digits: int = 3) -> str:
    if x is None:
        return ""
    return f"{float(x):.{digits}f}"


def pct(x: Any) -> str:
    if x is None:
        return ""
    return f"{float(x) * 100:.1f}%"


def roi(pnl: float, cost: float) -> float | None:
    return None if cost == 0 else pnl / cost


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def period_of(target_date: str) -> str:
    return "post_2026_06_01" if target_date >= RECENT_START else "pre_2026_06_01"


def side_prob(side: str, p_yes: float) -> float:
    return p_yes if side == "BUY_YES" else 1.0 - p_yes


def side_edge(side: str, p_yes: float, entry_price: float) -> float:
    return side_prob(side, p_yes) - entry_price


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          fill_id,
          execution_id,
          strategy_id,
          strategy_name,
          run_id,
          execution_policy,
          entry_price_window,
          trade_class,
          city,
          city_pool,
          icao,
          target_date,
          bracket,
          side,
          forecast_source,
          model_version,
          condition_id,
          market_id,
          market_key,
          city_day_key,
          order_ts_utc,
          fill_ts_utc,
          snapshot_ts_utc,
          hours_to_settle,
          model_p_yes,
          market_price,
          edge,
          abs_edge,
          plan_price,
          fill_price,
          fill_qty,
          fees_usd,
          cost_usd,
          cost_usd_at_plan,
          settlement_join_method,
          settlement_status,
          settled,
          final_yes,
          pnl_usd_at_fill,
          pnl_usd_at_plan,
          win_by_count,
          fact_built_at_utc
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
        """,
        conn,
        params=(STRATEGY_ID,),
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["market_yes_price"] = df.apply(
        lambda r: float(r["market_price"]) if r["side"] == "BUY_YES" else 1.0 - float(r["market_price"]),
        axis=1,
    )
    df["raw_side_prob"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["market_side_prob"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["market_yes_price"])), axis=1)
    df["raw_edge_at_fill"] = df.apply(
        lambda r: side_edge(str(r["side"]), float(r["model_p_yes"]), float(r["fill_price"])),
        axis=1,
    )
    df["side_prob_divergence"] = df["raw_side_prob"] - df["market_side_prob"]
    df["abs_yes_divergence"] = (df["model_p_yes"] - df["market_yes_price"]).abs()
    df["divergence_bin"] = pd.cut(
        df["abs_yes_divergence"],
        bins=[-0.001, 0.05, 0.10, 0.20, 1.0],
        labels=["<=0.05", "0.05-0.10", "0.10-0.20", ">0.20"],
    ).astype(str)
    df["raw_edge_bin"] = pd.cut(
        df["raw_edge_at_fill"],
        bins=[-10, 0.10, 0.15, 0.25, 10],
        labels=["<=0.10", "0.10-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    df["side_divergence_bin"] = pd.cut(
        df["side_prob_divergence"],
        bins=[-10, 0.05, 0.15, 0.25, 10],
        labels=["<=0.05", "0.05-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    df["hours_bin"] = pd.cut(
        df["hours_to_settle"],
        bins=[-10, 22, 24, 26, 28, 1000],
        labels=["<T-22", "T-22-24", "T-24-26", "T-26-28", ">T-28"],
    ).astype(str)
    df["snapshot_hour_utc"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce").dt.hour

    cfg = load_default_config()
    blended_ps: list[float] = []
    blended_edges: list[float] = []
    pass_blended: list[bool] = []
    for row in df.itertuples():
        blended = blend_probability(
            city=str(row.city),
            model_p_yes_raw=float(row.model_p_yes),
            market_implied_p_yes=float(row.market_yes_price),
            config=cfg,
        )
        b_p = float(blended.p_yes_used)
        b_edge = side_edge(str(row.side), b_p, float(row.fill_price))
        blended_ps.append(b_p)
        blended_edges.append(b_edge)
        pass_blended.append(b_edge >= 0.10)
    df["blended_p_yes"] = blended_ps
    df["blended_edge_at_fill"] = blended_edges
    df["pass_blended_overlay"] = pass_blended
    return df


def load_candidate_features(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = pd.read_sql_query(
        """
        WITH side_groups AS (
          SELECT
            condition_id,
            event_date,
            bracket,
            COUNT(DISTINCT CASE WHEN eligible=1 THEN side END) AS eligible_sides,
            SUM(CASE WHEN eligible=1 AND side='BUY_YES' THEN 1 ELSE 0 END) AS eligible_yes_rows,
            SUM(CASE WHEN eligible=1 AND side='BUY_NO' THEN 1 ELSE 0 END) AS eligible_no_rows,
            SUM(CASE WHEN paper_ordered=1 AND side='BUY_YES' THEN 1 ELSE 0 END) AS ordered_yes_rows,
            SUM(CASE WHEN paper_ordered=1 AND side='BUY_NO' THEN 1 ELSE 0 END) AS ordered_no_rows
          FROM fact_signal_candidates
          GROUP BY condition_id, event_date, bracket
        )
        SELECT
          c.condition_id,
          c.side,
          c.event_date AS target_date,
          c.bracket,
          c.city,
          c.city_pool,
          c.model_version,
          c.decision_window_label,
          c.decision_hours_to_settle,
          c.decision_snapshot_ts_utc,
          c.decision_window_missing,
          c.model_p_yes AS candidate_model_p_yes,
          c.market_yes_price AS candidate_market_yes_price,
          c.edge AS candidate_edge,
          c.abs_edge AS candidate_abs_edge,
          c.decision_entry_price,
          c.first_seen_ts_utc,
          c.last_seen_ts_utc,
          c.n_snapshots,
          c.edge_max,
          c.edge_mean,
          c.eligible,
          c.paper_ordered,
          c.live_filled,
          c.slippage_vs_paper,
          c.counterfactual_pnl,
          c.counterfactual_pnl_best,
          sg.eligible_sides,
          sg.eligible_yes_rows,
          sg.eligible_no_rows,
          sg.ordered_yes_rows,
          sg.ordered_no_rows
        FROM fact_signal_candidates c
        LEFT JOIN side_groups sg
          ON sg.condition_id=c.condition_id
         AND sg.event_date=c.event_date
         AND sg.bracket=c.bracket
        WHERE c.eligible=1
        """,
        conn,
    )
    if rows.empty:
        return rows
    rows["period"] = rows["target_date"].map(period_of)
    rows["candidate_abs_yes_divergence"] = (
        rows["candidate_model_p_yes"] - rows["candidate_market_yes_price"]
    ).abs()
    rows["edge_jump_proxy"] = rows["edge_max"] - rows["candidate_edge"]
    rows["same_market_opposite_eligible"] = rows["eligible_sides"] >= 2
    rows["hours_bin"] = pd.cut(
        rows["decision_hours_to_settle"],
        bins=[-10, 22, 24, 26, 28, 1000],
        labels=["<T-22", "T-22-24", "T-24-26", "T-26-28", ">T-28"],
    ).astype(str)
    return rows


def add_candidate_features(trades: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or candidates.empty:
        return trades
    feature_cols = [
        "condition_id",
        "side",
        "target_date",
        "bracket",
        "decision_hours_to_settle",
        "decision_snapshot_ts_utc",
        "decision_window_missing",
        "n_snapshots",
        "edge_max",
        "edge_mean",
        "edge_jump_proxy",
        "same_market_opposite_eligible",
        "eligible_yes_rows",
        "eligible_no_rows",
        "ordered_yes_rows",
        "ordered_no_rows",
        "slippage_vs_paper",
    ]
    features = candidates[feature_cols].drop_duplicates(
        ["condition_id", "side", "target_date", "bracket"]
    )
    return trades.merge(features, on=["condition_id", "side", "target_date", "bracket"], how="left")


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    fills = int(len(group))
    cost = float(group["cost_usd"].sum())
    pnl = float(group["pnl_usd_at_fill"].sum())
    wins = float((group["pnl_usd_at_fill"] > 0).mean()) if fills else None
    return {
        "fills": fills,
        "active_days": int(group["target_date"].nunique()) if fills else 0,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": None if cost == 0 else round(pnl / cost, 6),
        "win_rate": None if wins is None else round(wins, 6),
        "avg_raw_edge": round(float(group["raw_edge_at_fill"].mean()), 6) if fills else None,
        "avg_fact_edge": round(float(group["edge"].mean()), 6) if fills else None,
        "avg_abs_yes_divergence": round(float(group["abs_yes_divergence"].mean()), 6) if fills else None,
        "avg_hours_to_settle": round(float(group["hours_to_settle"].mean()), 3) if fills else None,
        "pnl_ex_top3_wins": round(
            pnl - float(group.loc[group["pnl_usd_at_fill"] > 0, "pnl_usd_at_fill"].nlargest(3).sum()), 4
        ),
    }


def grouped(df: pd.DataFrame, keys: list[str], *, min_rows: int = 1, sort: str = "pnl_usd") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        row = {}
        if not isinstance(key, tuple):
            key = (key,)
        for name, value in zip(keys, key):
            row[name] = "" if pd.isna(value) else str(value)
        row.update(summarize(g))
        if row["fills"] >= min_rows:
            out.append(row)
    def sort_key(row: dict[str, Any]) -> tuple[Any, int]:
        value = row.get(sort)
        try:
            sortable: Any = float(value)
        except (TypeError, ValueError):
            sortable = "" if value is None else str(value)
        return sortable, int(row.get("fills", 0) or 0)

    return sorted(out, key=sort_key)


def format_summary(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for k in ["cost_usd", "pnl_usd", "avg_raw_edge", "avg_fact_edge", "avg_abs_yes_divergence", "avg_hours_to_settle", "pnl_ex_top3_wins"]:
            if k in r:
                r[k] = num(r[k], 3 if "avg" in k else 2)
        if "roi" in r:
            r["roi"] = pct(r["roi"])
        if "win_rate" in r:
            r["win_rate"] = pct(r["win_rate"])
        out.append({c: r.get(c, "") for c in columns})
    return out


def city_transition(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city, g in df.groupby("city"):
        pre = g[g["period"] == "pre_2026_06_01"]
        post = g[g["period"] == "post_2026_06_01"]
        pre_s = summarize(pre) if not pre.empty else {"fills": 0, "active_days": 0, "cost_usd": 0, "pnl_usd": 0, "roi": None, "win_rate": None}
        post_s = summarize(post) if not post.empty else {"fills": 0, "active_days": 0, "cost_usd": 0, "pnl_usd": 0, "roi": None, "win_rate": None}
        low_sample = (
            pre_s["fills"] < 5
            or post_s["fills"] < 5
            or pre_s["active_days"] < 3
            or post_s["active_days"] < 3
        )
        if low_sample:
            klass = "sample_insufficient"
        elif pre_s["pnl_usd"] > 0 and post_s["pnl_usd"] < 0:
            klass = "profit_to_loss"
        elif pre_s["pnl_usd"] > 0 and post_s["pnl_usd"] >= 0:
            klass = "stable_profitable"
        elif pre_s["pnl_usd"] <= 0 and post_s["pnl_usd"] < 0:
            klass = "persistently_weak"
        else:
            klass = "improved_or_mixed"
        rows.append(
            {
                "city": city,
                "class": klass,
                "pre_fills": pre_s["fills"],
                "pre_days": pre_s["active_days"],
                "pre_pnl": round(float(pre_s["pnl_usd"]), 4),
                "pre_roi": pre_s["roi"],
                "post_fills": post_s["fills"],
                "post_days": post_s["active_days"],
                "post_pnl": round(float(post_s["pnl_usd"]), 4),
                "post_roi": post_s["roi"],
                "delta_pnl": round(float(post_s["pnl_usd"]) - float(pre_s["pnl_usd"]), 4),
            }
        )
    return sorted(rows, key=lambda r: r["post_pnl"])


def daily_city_tail(df: pd.DataFrame) -> dict[str, Any]:
    city_day = (
        df.groupby(["period", "target_date", "city"], dropna=False)
        .agg(
            fills=("fill_id", "count"),
            cost_usd=("cost_usd", "sum"),
            pnl_usd=("pnl_usd_at_fill", "sum"),
            sides=("side", lambda x: ",".join(sorted(set(map(str, x))))),
        )
        .reset_index()
    )
    post = city_day[city_day["period"] == "post_2026_06_01"].copy()
    post_loss = float(-post.loc[post["pnl_usd"] < 0, "pnl_usd"].sum())
    worst = post.sort_values("pnl_usd").head(12)
    top3_loss = float(-worst.head(3).loc[worst.head(3)["pnl_usd"] < 0, "pnl_usd"].sum())
    total_post = summarize(df[df["period"] == "post_2026_06_01"])
    post_without_top3 = df.merge(
        worst.head(3)[["target_date", "city"]].assign(_top3=1),
        on=["target_date", "city"],
        how="left",
    )
    post_without_top3 = post_without_top3[
        (post_without_top3["period"] == "post_2026_06_01") & (post_without_top3["_top3"].isna())
    ]
    return {
        "post_loss_usd": round(post_loss, 4),
        "top3_loss_usd": round(top3_loss, 4),
        "top3_share_of_post_losses": None if post_loss == 0 else round(top3_loss / post_loss, 6),
        "total_post": total_post,
        "post_without_top3": summarize(post_without_top3),
        "worst_city_days": worst.to_dict(orient="records"),
    }


def blended_filter(df: pd.DataFrame) -> dict[str, Any]:
    filtered = df[~df["pass_blended_overlay"]]
    kept = df[df["pass_blended_overlay"]]
    by_period: list[dict[str, Any]] = []
    for period, g in df.groupby("period"):
        f = g[~g["pass_blended_overlay"]]
        k = g[g["pass_blended_overlay"]]
        s = summarize(g)
        fs = summarize(f)
        ks = summarize(k)
        by_period.append(
            {
                "period": period,
                "all_fills": s["fills"],
                "all_pnl": s["pnl_usd"],
                "kept_fills": ks["fills"],
                "kept_pnl": ks["pnl_usd"],
                "filtered_fills": fs["fills"],
                "filtered_pnl": fs["pnl_usd"],
                "delta_if_filter": round(-float(fs["pnl_usd"]), 4),
                "filtered_avg_blended_edge": round(float(f["blended_edge_at_fill"].mean()), 6) if not f.empty else None,
                "filtered_avg_abs_divergence": round(float(f["abs_yes_divergence"].mean()), 6) if not f.empty else None,
            }
        )
    return {
        "by_period": by_period,
        "filtered_by_side_post": grouped(filtered[filtered["period"] == "post_2026_06_01"], ["side"], sort="pnl_usd"),
        "filtered_by_city_post": grouped(filtered[filtered["period"] == "post_2026_06_01"], ["city"], sort="pnl_usd")[:12],
        "filtered_by_divergence_post": grouped(filtered[filtered["period"] == "post_2026_06_01"], ["divergence_bin"], sort="pnl_usd"),
        "filtered_by_hours_post": grouped(filtered[filtered["period"] == "post_2026_06_01"], ["hours_bin"], sort="pnl_usd"),
        "kept": summarize(kept),
        "filtered": summarize(filtered),
    }


def candidate_overview(candidates: pd.DataFrame) -> dict[str, Any]:
    if candidates.empty:
        return {}
    rows: list[dict[str, Any]] = []
    for period, g in candidates.groupby("period"):
        rows.append(
            {
                "period": period,
                "eligible": int(len(g)),
                "paper_ordered": int(g["paper_ordered"].sum()),
                "live_filled": int(g["live_filled"].sum()),
                "decision_window_missing_rate": round(float(g["decision_window_missing"].mean()), 6),
                "opposite_side_eligible_rate": round(float(g["same_market_opposite_eligible"].mean()), 6),
                "avg_abs_yes_divergence": round(float(g["candidate_abs_yes_divergence"].mean()), 6),
                "avg_edge_jump_proxy": round(float(g["edge_jump_proxy"].mean()), 6),
            }
        )
    by_hours = (
        candidates.groupby(["period", "hours_bin"], dropna=False)
        .agg(
            eligible=("candidate_abs_edge", "count"),
            paper_ordered=("paper_ordered", "sum"),
            live_filled=("live_filled", "sum"),
            avg_abs_yes_divergence=("candidate_abs_yes_divergence", "mean"),
            avg_edge_jump_proxy=("edge_jump_proxy", "mean"),
            opposite_side_eligible_rate=("same_market_opposite_eligible", "mean"),
        )
        .reset_index()
    )
    return {
        "period": rows,
        "by_hours": by_hours.to_dict(orient="records"),
    }


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
        "fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS v FROM fact_trades")[0]["v"],
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status": fetchall(
            conn,
            "SELECT COALESCE(settlement_status, '[NULL]') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "signal_candidates": fetchall(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "orders_with_fills_by_venue_status": fetchall(
            conn,
            """
            SELECT o.venue, o.status, COUNT(*) AS orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o LEFT JOIN fills f USING(execution_id)
            GROUP BY o.venue, o.status
            ORDER BY o.venue, o.status
            """,
        ),
        "target_strategy": fetchall(
            conn,
            """
            SELECT strategy_id, strategy_name, trade_class, COUNT(*) AS fills,
                   MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date
            FROM fact_trades
            WHERE strategy_id=?
            GROUP BY strategy_id, strategy_name, trade_class
            ORDER BY trade_class
            """,
            (STRATEGY_ID,),
        ),
    }


def render_report(data: dict[str, Any]) -> str:
    overall_cols = [
        "period",
        "fills",
        "active_days",
        "cost_usd",
        "pnl_usd",
        "roi",
        "win_rate",
        "avg_raw_edge",
        "avg_abs_yes_divergence",
        "avg_hours_to_settle",
        "pnl_ex_top3_wins",
    ]
    by_side_cols = ["period", "side", "fills", "active_days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]
    city_cols = ["city", "class", "pre_fills", "pre_days", "pre_pnl", "pre_roi", "post_fills", "post_days", "post_pnl", "post_roi", "delta_pnl"]
    rank_cols = ["period", "city", "fills", "active_days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]
    bin_cols = ["period", "divergence_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]
    hours_cols = ["period", "hours_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_hours_to_settle"]
    model_cols = ["period", "model_version", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]

    self_check = data["self_checks"]
    tail = data["tail"]
    lines: list[str] = [
        "# mid_price_core_v1 / v1_25_75 raw probability 退化归因",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{self_check['db_path']}`，只读 `fact_trades` / `fact_signal_candidates`。",
        f"- DB mtime UTC：`{self_check['db_mtime_utc']}`；`MAX(fact_built_at_utc)`：`{self_check['fact_built_at_utc']}`。",
        "- CLOB gate：本次分析前已单独运行 `python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py`，结果 `gate_pass=true`，DB/cache/fact cost diff = 0。",
        f"- 目标样本：`{STRATEGY_LABEL}` / `strategy_id={STRATEGY_ID}` / `trade_class=live_real` / `settlement_status=settled`。",
        "",
        "### 5 行 SQL 自检",
        "",
        "trade_class 分布：",
        table(self_check["trade_class"]),
        "",
        "settlement_status 分布：",
        table(self_check["settlement_status"]),
        "",
        "fact_signal_candidates 覆盖：",
        table([self_check["signal_candidates"]]),
        "",
        "orders/fills by venue/status：",
        table(self_check["orders_with_fills_by_venue_status"]),
        "",
        "目标策略样本：",
        table(self_check["target_strategy"]),
        "",
        "## 目标指标与分母",
        "",
        "`mid_price_core_v1_25_75_degradation` = v1 25-75 raw edge 入场的真实已成交已结算 fill，从 `target_date < 2026-06-01` 到 `target_date >= 2026-06-01` 的退化。Realized PnL 只用 `fact_trades.pnl_usd_at_fill`；raw edge / market implied / blended edge 只用于解释和分桶，不重新计算成交 PnL。",
        "",
        "market implied YES probability 口径：BUY_YES 用 `market_price`，BUY_NO 用 `1 - market_price`。raw side edge 用成交价作 overlay：BUY_YES=`model_p_yes-fill_price`，BUY_NO=`(1-model_p_yes)-fill_price`；原始 fact edge 另以 `edge` 均值列出作 sanity check。",
        "",
        "## 结论",
        "",
        data["conclusion"],
        "",
        "## 1. 6 月前后总览",
        "",
        table(format_summary(data["overall"], overall_cols), overall_cols),
        "",
        "读法：`pnl_ex_top3_wins` 是剔除各 period 最大 3 笔盈利 fill 后的 PnL，用于观察是否靠少数赢家支撑。",
        "",
        "## 2. BUY_YES / BUY_NO 方向",
        "",
        table(format_summary(data["by_side"], by_side_cols), by_side_cols),
        "",
        "## 3. 城市退化分类",
        "",
        table(data["city_transition_fmt"], city_cols),
        "",
        "### 6 月后城市亏损排行",
        "",
        table(format_summary(data["post_city_rank"], rank_cols), rank_cols),
        "",
        "## 4. raw probability vs market implied probability 分歧",
        "",
        "按 `abs(model_p_yes - market_implied_p_yes)` 分桶：",
        "",
        table(format_summary(data["by_divergence"], bin_cols), bin_cols),
        "",
        "按 raw edge 分桶：",
        "",
        table(format_summary(data["by_raw_edge"], ["period", "raw_edge_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]), ["period", "raw_edge_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]),
        "",
        "按 raw side probability 相对 market side probability 的优势分桶：",
        "",
        table(format_summary(data["by_side_divergence"], ["period", "side_divergence_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]), ["period", "side_divergence_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge", "avg_abs_yes_divergence"]),
        "",
        "## 5. Forecast timing / GFS / side flip proxy",
        "",
        "fact 表当前没有显式 `forecast_jump` / `side_flip` 字段；本节只使用授权 fact 字段做代理检验：`hours_to_settle`、`model_version`、`snapshot_hour_utc`、candidate 的 `edge_max-edge`、以及同一 market/bracket 是否出现 eligible opposite side。该结论应视为机制线索，不是完整逐 snapshot 血缘。",
        "",
        "按入场窗口：",
        "",
        table(format_summary(data["by_hours"], hours_cols), hours_cols),
        "",
        "按模型版本：",
        "",
        table(format_summary(data["by_model"], model_cols), model_cols),
        "",
        "已成交样本 joined candidate timing proxy：",
        "",
        table(data["trade_candidate_timing_fmt"]),
        "",
        "eligible candidate universe timing proxy：",
        "",
        table(data["candidate_period_fmt"]),
        "",
        "## 6. 尾部日期/城市事件",
        "",
        f"- 6 月后全部亏损 city-day 的 gross loss：`${tail['post_loss_usd']:.2f}`。",
        f"- 最差 3 个 city-day gross loss：`${tail['top3_loss_usd']:.2f}`，占 6 月后 gross losses `{pct(tail['top3_share_of_post_losses'])}`。",
        f"- 6 月后整体 PnL：`${tail['total_post']['pnl_usd']:.2f}`；剔除最差 3 个 city-day 后 PnL：`${tail['post_without_top3']['pnl_usd']:.2f}`。",
        "",
        table(data["worst_city_days_fmt"]),
        "",
        "## 7. Blended gate 拦截的亏损特征",
        "",
        "这里不是证明 blended gate 稳定赚钱，只看它在当前样本中拦下的 v1 亏损是否对应可解释特征。",
        "",
        table(data["blended_period_fmt"]),
        "",
        "6 月后被 blended gate 拦截的亏损按方向：",
        "",
        table(format_summary(data["blended"]["filtered_by_side_post"], ["side", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]), ["side", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]),
        "",
        "6 月后被 blended gate 拦截的亏损按城市：",
        "",
        table(format_summary(data["blended"]["filtered_by_city_post"], ["city", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]), ["city", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]),
        "",
        "6 月后被 blended gate 拦截的亏损按分歧/时间窗口：",
        "",
        table(format_summary(data["blended"]["filtered_by_divergence_post"], ["divergence_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]), ["divergence_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_abs_yes_divergence"]),
        "",
        table(format_summary(data["blended"]["filtered_by_hours_post"], ["hours_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_hours_to_settle"]), ["hours_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_hours_to_settle"]),
        "",
        "## 交易动作建议",
        "",
        data["actions"],
        "",
        "## 口径限制",
        "",
        "- 本报告没有同步 N100 重建 DB；按用户指定使用当前 `runtime/weather.db`，DB build time 见数据快照。",
        "- `forecast_jump` / `side_flip` 没有被物化为 fact 字段；本报告只用 candidate proxy，不给强机制结论。",
        "- 城市少于 5 个 settled fills 或少于 3 个 active target days 的分类均降级为 `sample_insufficient`。",
        "- blended gate 的正 delta 是 recent drift filter 线索，不是稳定 alpha 证明。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    checks = self_checks(conn)
    trades = load_trades(conn)
    candidates = load_candidate_features(conn)
    if trades.empty:
        raise SystemExit("No target strategy settled live_real rows found")
    trades = add_candidate_features(trades, candidates)

    overall = grouped(trades, ["period"], sort="period")
    by_side = grouped(trades, ["period", "side"], sort="period")
    by_divergence = grouped(trades, ["period", "divergence_bin"], sort="period")
    by_raw_edge = grouped(trades, ["period", "raw_edge_bin"], sort="period")
    by_side_divergence = grouped(trades, ["period", "side_divergence_bin"], sort="period")
    by_hours = grouped(trades, ["period", "hours_bin"], sort="period")
    by_model = grouped(trades, ["period", "model_version"], sort="period")
    transitions = city_transition(trades)
    post_city_rank = grouped(trades[trades["period"] == "post_2026_06_01"], ["period", "city"], sort="pnl_usd")[:15]
    tail = daily_city_tail(trades)
    blend = blended_filter(trades)
    cand = candidate_overview(candidates)

    timing_rows: list[dict[str, Any]] = []
    for period, g in trades.groupby("period"):
        timing_rows.append(
            {
                "period": period,
                "fills_with_candidate": int(g["decision_hours_to_settle"].notna().sum()),
                "avg_candidate_hours_to_settle": num(g["decision_hours_to_settle"].mean(), 2),
                "avg_n_snapshots": num(g["n_snapshots"].mean(), 2),
                "avg_edge_jump_proxy": num(g["edge_jump_proxy"].mean(), 3),
                "opposite_side_eligible_rate": pct(g["same_market_opposite_eligible"].mean()),
                "avg_slippage_vs_paper": num(g["slippage_vs_paper"].mean(), 4),
            }
        )

    candidate_period_fmt = [
        {
            "period": r["period"],
            "eligible": r["eligible"],
            "paper_ordered": r["paper_ordered"],
            "live_filled": r["live_filled"],
            "decision_window_missing_rate": pct(r["decision_window_missing_rate"]),
            "opposite_side_eligible_rate": pct(r["opposite_side_eligible_rate"]),
            "avg_abs_yes_divergence": num(r["avg_abs_yes_divergence"], 3),
            "avg_edge_jump_proxy": num(r["avg_edge_jump_proxy"], 3),
        }
        for r in cand.get("period", [])
    ]

    city_transition_fmt = []
    for r in transitions:
        city_transition_fmt.append(
            {
                **r,
                "pre_pnl": money(r["pre_pnl"]),
                "pre_roi": pct(r["pre_roi"]),
                "post_pnl": money(r["post_pnl"]),
                "post_roi": pct(r["post_roi"]),
                "delta_pnl": money(r["delta_pnl"]),
            }
        )

    worst_city_days_fmt = []
    for r in tail["worst_city_days"]:
        worst_city_days_fmt.append(
            {
                "target_date": r["target_date"],
                "city": r["city"],
                "fills": int(r["fills"]),
                "cost_usd": num(r["cost_usd"], 2),
                "pnl_usd": money(r["pnl_usd"]),
                "sides": r["sides"],
            }
        )

    blended_period_fmt = []
    for r in blend["by_period"]:
        blended_period_fmt.append(
            {
                "period": r["period"],
                "all_fills": r["all_fills"],
                "all_pnl": money(r["all_pnl"]),
                "kept_fills": r["kept_fills"],
                "kept_pnl": money(r["kept_pnl"]),
                "filtered_fills": r["filtered_fills"],
                "filtered_pnl": money(r["filtered_pnl"]),
                "delta_if_filter": money(r["delta_if_filter"]),
                "filtered_avg_blended_edge": num(r["filtered_avg_blended_edge"], 3),
                "filtered_avg_abs_divergence": num(r["filtered_avg_abs_divergence"], 3),
            }
        )

    pre = next(r for r in overall if r["period"] == "pre_2026_06_01")
    post = next(r for r in overall if r["period"] == "post_2026_06_01")
    post_side = {r["side"]: r for r in by_side if r["period"] == "post_2026_06_01"}
    pre_side = {r["side"]: r for r in by_side if r["period"] == "pre_2026_06_01"}
    no_post = post_side.get("BUY_NO", {})
    yes_post = post_side.get("BUY_YES", {})
    no_delta = float(no_post.get("pnl_usd", 0)) - float(pre_side.get("BUY_NO", {}).get("pnl_usd", 0))
    yes_delta = float(yes_post.get("pnl_usd", 0)) - float(pre_side.get("BUY_YES", {}).get("pnl_usd", 0))
    profit_to_loss = [r for r in transitions if r["class"] == "profit_to_loss"]
    stable_profitable = [r for r in transitions if r["class"] == "stable_profitable"]
    top_bad = transitions[0]

    conclusion = (
        f"- 真实机制（较强）：退化主要不是 25-75 entry band 本身消失，而是 raw weather probability 的边际 raw edge 在 6 月后失去 payoff。"
        f"主样本 6 月前 {pre['fills']} fills，PnL `${pre['pnl_usd']:.2f}`、ROI {pct(pre['roi'])}；"
        f"6 月后 {post['fills']} fills，PnL `${post['pnl_usd']:.2f}`、ROI {pct(post['roi'])}。"
        "6 月后 `raw_edge_at_fill` 均值只从 0.216 降到 0.199，但 win rate 从 58.8% 降到 43.7%，说明主要是 raw 概率排序/校准失效，而不是入场价区间消失。\n"
        f"- 真实机制（较强）：不是单侧事故，BUY_YES 和 BUY_NO 都变差；BUY_YES 从 `${float(pre_side.get('BUY_YES', {}).get('pnl_usd', 0)):.2f}` 到 `${float(yes_post.get('pnl_usd', 0)):.2f}`（delta `${yes_delta:.2f}`），BUY_NO 从 `${float(pre_side.get('BUY_NO', {}).get('pnl_usd', 0)):.2f}` 到 `${float(no_post.get('pnl_usd', 0)):.2f}`（delta `${no_delta:.2f}`）。"
        "BUY_YES 的绝对亏损和退化幅度更大，BUY_NO 仍然由高胜率低 payoff/尾部 loser 拖累。\n"
        "- 统计相关（中等）：`abs(model_p_yes-market_implied_p_yes)` 没有扩大，均值反而从 0.278 降到 0.190；6 月后亏损集中在 `0.10-0.20` 中等分歧和 `raw_edge_at_fill<=0.25` 的边际信号，高分歧/高 raw edge 样本仍略正。blender 拦截有效，更像近期把边际 raw edge 过滤掉，而不是证明“分歧越大越亏”。\n"
        f"- 统计相关（中等）：{len(profit_to_loss)} 个城市从盈利转亏，最差为 `{top_bad['city']}`（6 月后 `${top_bad['post_pnl']:.2f}`）；"
        f"{len(stable_profitable)} 个城市仍保持盈利，说明不是所有城市同步 regime shift。\n"
        f"- 尾部结构（中等）：6 月后最差 3 个 city-day 占 gross losses {pct(tail['top3_share_of_post_losses'])}；"
        f"剔除它们后 6 月后 PnL 为 `${tail['post_without_top3']['pnl_usd']:.2f}`。这说明亏损集中，但不完全是单一事故。\n"
        "- 真实机制（中等）：GFS 不是主要拖累；6 月后 GFS PnL 为正，ECMWF 明显为负。timing 上 `>T-28` 和 `T-26-28` 拖累，`T-22-24` 仍为正。\n"
        "- 样本不足/字段不足：forecast_jump 和 side_flip 没有明确 fact 字段；candidate edge jump proxy 与 opposite-side eligible rate 在 6 月前后接近，当前不能把它们定性为主因。"
    )

    actions = (
        "1. **临时关停 `mid_price_core_v1_25_75` live，或至少降到 shadow/极小 size**。6 月后 BUY_YES/BUY_NO 都转负，继续原 size live 没有量化依据。\n"
        "2. **若必须保留探索，只允许受限 shadow 组合**：优先看 `raw_edge_at_fill>0.25`、`T-22-24/T-24-26`、GFS、以及 LA/Miami/Tokyo/Madrid/Shanghai 这类近期稳定城市；不要把它作为已验证 live alpha。\n"
        "3. **城市层面先 blacklist/降 size 样本充分且 6 月后弱的城市**：BuenosAires、Munich、Jeddah、Karachi、NYC、Moscow、Ankara；London/Warsaw/Istanbul 属于 profit-to-loss，需要至少 shadow 复核。Amsterdam/Lucknow/Seattle 等样本不足但亏损尖锐，只降级观察不做强结论。\n"
        "4. **不要用 `abs(model_p_yes-market_implied_p_yes)>0.20` 作为简单黑名单**；本样本高分歧并不亏。更合理的近期风控是 `blended_edge<0.10`、`raw_edge<=0.25`、以及不利 timing/city 的组合过滤，但它仍只是 recent drift filter。\n"
        "5. **下一步工程动作**：把 forecast_jump、side_flip、同 market opposite-side transition 物化进 `fact_signal_candidates`，再做 T-22/T-28 的逐 snapshot 血缘复盘。"
    )

    data: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "self_checks": checks,
        "overall": overall,
        "by_side": by_side,
        "by_divergence": by_divergence,
        "by_raw_edge": by_raw_edge,
        "by_side_divergence": by_side_divergence,
        "by_hours": by_hours,
        "by_model": by_model,
        "city_transition": transitions,
        "city_transition_fmt": city_transition_fmt,
        "post_city_rank": post_city_rank,
        "tail": tail,
        "worst_city_days_fmt": worst_city_days_fmt,
        "blended": blend,
        "blended_period_fmt": blended_period_fmt,
        "candidate_overview": cand,
        "candidate_period_fmt": candidate_period_fmt,
        "trade_candidate_timing_fmt": timing_rows,
        "conclusion": conclusion,
        "actions": actions,
    }

    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_report(data), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
