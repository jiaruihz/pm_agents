#!/usr/bin/env python3
"""Control-variable study: removed ECMWF cities + T28 ban + blender overlay.

This keeps historical live fill price, size, and settlement fixed. Filters only
decide whether a historical v1_25_75 fill would have been kept.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
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
OUT_MD = OUT_DIR / "2026-06-08-v1-removed-ecmwf-t28-blender-overlay.md"
OUT_JSON = OUT_DIR / "2026-06-08-v1-removed-ecmwf-t28-blender-overlay.json"

RECENT_START = "2026-06-01"
STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
REMOVED_CITIES = {"BuenosAires", "Munich", "Jeddah", "Karachi", "Moscow", "Ankara"}


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def side_prob(side: str, p_yes: float) -> float:
    return p_yes if side == "BUY_YES" else 1.0 - p_yes


def side_edge(side: str, p_yes: float, entry_price: float) -> float:
    return side_prob(side, p_yes) - entry_price


def run_clob_gate() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "analysis" / "weather_clob_fill_coverage_gate.py")],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          fill_id,
          strategy_id,
          strategy_name,
          execution_policy,
          entry_price_window,
          trade_class,
          city,
          target_date,
          bracket,
          side,
          forecast_source,
          model_version,
          order_ts_utc,
          fill_ts_utc,
          snapshot_ts_utc,
          hours_to_settle,
          model_p_yes,
          market_price,
          fill_price,
          fill_qty,
          cost_usd,
          settlement_status,
          final_yes,
          pnl_usd_at_fill,
          fact_built_at_utc
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
          AND model_p_yes IS NOT NULL
          AND market_price IS NOT NULL
          AND fill_price IS NOT NULL
          AND cost_usd IS NOT NULL
          AND pnl_usd_at_fill IS NOT NULL
        """,
        conn,
        params=(STRATEGY_ID,),
    )
    if df.empty:
        raise RuntimeError("No settled live_real v1_25_75 trades found")
    df["period"] = df["target_date"].map(lambda x: "post_2026_06_01" if str(x) >= RECENT_START else "pre_2026_06_01")
    df["market_yes_price"] = df.apply(
        lambda r: float(r["market_price"]) if r["side"] == "BUY_YES" else 1.0 - float(r["market_price"]),
        axis=1,
    )
    df["raw_edge_at_fill"] = df.apply(
        lambda r: side_edge(str(r["side"]), float(r["model_p_yes"]), float(r["fill_price"])),
        axis=1,
    )
    cfg = load_default_config()
    blended_p_yes: list[float] = []
    blended_edge: list[float] = []
    for row in df.itertuples():
        blended = blend_probability(
            city=str(row.city),
            model_p_yes_raw=float(row.model_p_yes),
            market_implied_p_yes=float(row.market_yes_price),
            config=cfg,
        )
        p_yes = float(blended.p_yes_used)
        blended_p_yes.append(p_yes)
        blended_edge.append(side_edge(str(row.side), p_yes, float(row.fill_price)))
    df["blended_p_yes"] = blended_p_yes
    df["blended_edge_at_fill"] = blended_edge
    df["remove_city"] = df["city"].isin(REMOVED_CITIES)
    df["pass_t28"] = df["hours_to_settle"].fillna(999.0).astype(float) <= 28.0
    df["pass_operational_base"] = (~df["remove_city"]) & df["pass_t28"]
    df["pass_blended_010"] = df["blended_edge_at_fill"] >= 0.10
    df["pass_blended_or_highraw"] = df["pass_blended_010"] | (df["raw_edge_at_fill"] > 0.25)
    return df


def pnl_ex_top_wins(df: pd.DataFrame, n: int = 5) -> float:
    if df.empty:
        return 0.0
    wins = df[df["pnl_usd_at_fill"] > 0]["pnl_usd_at_fill"].sort_values(ascending=False)
    return float(df["pnl_usd_at_fill"].sum()) - float(wins.head(n).sum())


def summarize(df: pd.DataFrame, keep: pd.Series) -> dict[str, Any]:
    keep = keep.fillna(False).astype(bool)
    kept = df[keep].copy()
    filtered = df[~keep].copy()
    filtered_winners = filtered[filtered["pnl_usd_at_fill"] > 0]
    filtered_losers = filtered[filtered["pnl_usd_at_fill"] < 0]

    def block(x: pd.DataFrame) -> tuple[int, float, float, float | None, float]:
        cost = float(x["cost_usd"].sum()) if not x.empty else 0.0
        pnl = float(x["pnl_usd_at_fill"].sum()) if not x.empty else 0.0
        roi = pnl / cost if cost else None
        win_rate = float((x["pnl_usd_at_fill"] > 0).mean()) if not x.empty else 0.0
        return int(len(x)), cost, pnl, roi, win_rate

    fills, cost, pnl, roi, win_rate = block(df)
    kept_fills, kept_cost, kept_pnl, kept_roi, kept_win_rate = block(kept)
    filtered_fills, filtered_cost, filtered_pnl, filtered_roi, filtered_win_rate = block(filtered)
    avoided_loss = -float(filtered_losers["pnl_usd_at_fill"].sum()) if not filtered_losers.empty else 0.0
    missed_profit = float(filtered_winners["pnl_usd_at_fill"].sum()) if not filtered_winners.empty else 0.0
    return {
        "fills": fills,
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": None if roi is None else round(roi, 6),
        "win_rate": round(win_rate, 6),
        "kept_fills": kept_fills,
        "kept_cost_usd": round(kept_cost, 6),
        "kept_pnl_usd": round(kept_pnl, 6),
        "kept_roi": None if kept_roi is None else round(kept_roi, 6),
        "kept_win_rate": round(kept_win_rate, 6),
        "filtered_fills": filtered_fills,
        "filtered_cost_usd": round(filtered_cost, 6),
        "filtered_pnl_usd": round(filtered_pnl, 6),
        "filtered_roi": None if filtered_roi is None else round(filtered_roi, 6),
        "filtered_win_rate": round(filtered_win_rate, 6),
        "delta_pnl_if_filter": round(-filtered_pnl, 6),
        "avoided_loss_usd": round(avoided_loss, 6),
        "missed_profit_usd": round(missed_profit, 6),
        "net_filter_value_usd": round(avoided_loss - missed_profit, 6),
        "filtered_winner_fills": int(len(filtered_winners)),
        "filtered_loser_fills": int(len(filtered_losers)),
        "pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(df), 6),
        "kept_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(kept), 6),
        "filtered_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(filtered), 6),
    }


def evaluate(df: pd.DataFrame, name: str, keep: pd.Series) -> dict[str, Any]:
    out = {"gate": name, "full": summarize(df, keep)}
    for period in ["pre_2026_06_01", "post_2026_06_01"]:
        mask = df["period"] == period
        out[period] = summarize(df[mask].copy(), keep[mask].copy())
    return out


def incremental_summary(base_df: pd.DataFrame, gate_name: str, keep: pd.Series) -> dict[str, Any]:
    return evaluate(base_df, gate_name, keep.loc[base_df.index])


def by_group(df: pd.DataFrame, keep: pd.Series, group_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, part in df.groupby(group_col, dropna=False):
        s = summarize(part, keep.loc[part.index])
        rows.append(
            {
                group_col: str(key),
                "fills": s["fills"],
                "pnl_usd": s["pnl_usd"],
                "kept_pnl_usd": s["kept_pnl_usd"],
                "filtered_pnl_usd": s["filtered_pnl_usd"],
                "delta_pnl_if_filter": s["delta_pnl_if_filter"],
                "avoided_loss_usd": s["avoided_loss_usd"],
                "missed_profit_usd": s["missed_profit_usd"],
                "kept_fills": s["kept_fills"],
                "filtered_fills": s["filtered_fills"],
            }
        )
    rows.sort(key=lambda r: abs(float(r["delta_pnl_if_filter"])), reverse=True)
    return rows


def money(x: Any, signed: bool = True) -> str:
    if x is None:
        return ""
    prefix = "+" if signed else ""
    return f"${float(x):{prefix}.2f}"


def pct(x: Any, signed: bool = True) -> str:
    if x is None:
        return ""
    prefix = "+" if signed else ""
    return f"{float(x) * 100:{prefix}.1f}%"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def result_rows(results: list[dict[str, Any]], period: str) -> list[dict[str, str]]:
    rows = []
    for item in results:
        s = item[period]
        rows.append(
            {
                "gate": item["gate"],
                "fills": str(s["fills"]),
                "pnl": money(s["pnl_usd"]),
                "roi": pct(s["roi"]),
                "kept_fills": str(s["kept_fills"]),
                "kept_pnl": money(s["kept_pnl_usd"]),
                "kept_roi": pct(s["kept_roi"]),
                "filtered_fills": str(s["filtered_fills"]),
                "filtered_pnl": money(s["filtered_pnl_usd"]),
                "avoided_loss": money(s["avoided_loss_usd"], signed=False),
                "missed_profit": money(s["missed_profit_usd"], signed=False),
                "delta": money(s["delta_pnl_if_filter"]),
            }
        )
    return rows


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    clob_gate = run_clob_gate()
    if not clob_gate.get("gate_pass"):
        raise RuntimeError(f"CLOB coverage gate failed: {clob_gate.get('fail_reasons')}")
    df = load_trades(conn)
    base = df[df["pass_operational_base"]].copy()

    checks = {
        "max_fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades"),
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status": fetchall(conn, "SELECT COALESCE(settlement_status, '[NULL]') settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"),
        "candidate_coverage": fetchall(conn, "SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled FROM fact_signal_candidates"),
        "orders_fills": fetchall(
            conn,
            """
            SELECT o.status, COUNT(*) orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill
            FROM orders o LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
        "target_sample": fetchall(
            conn,
            """
            SELECT trade_class, execution_policy, entry_price_window, COUNT(*) rows,
                   MIN(target_date) min_target_date, MAX(target_date) max_target_date
            FROM fact_trades
            WHERE strategy_id=?
            GROUP BY trade_class, execution_policy, entry_price_window
            ORDER BY trade_class, execution_policy, entry_price_window
            """,
            (STRATEGY_ID,),
        ),
    }

    all_true = pd.Series(True, index=df.index)
    pass_no_removed_city = ~df["remove_city"]
    pass_t28 = df["pass_t28"]
    pass_base = df["pass_operational_base"]
    pass_base_blended = pass_base & df["pass_blended_010"]
    pass_base_blended_or_highraw = pass_base & df["pass_blended_or_highraw"]
    results = [
        evaluate(df, "no_gate_original_v1_25_75", all_true),
        evaluate(df, "remove_6_ecmwf_cities_only", pass_no_removed_city),
        evaluate(df, "ban_gt_T28_only", pass_t28),
        evaluate(df, "operational_base_remove_cities_and_T28", pass_base),
        evaluate(df, "operational_base_plus_blended_edge_ge_0.10", pass_base_blended),
        evaluate(df, "operational_base_plus_blended_or_raw_edge_gt_0.25", pass_base_blended_or_highraw),
    ]
    incremental = [
        incremental_summary(base, "within_operational_base_no_extra_gate", pd.Series(True, index=base.index)),
        incremental_summary(base, "within_base_blended_edge_ge_0.10", df["pass_blended_010"]),
        incremental_summary(base, "within_base_blended_or_raw_edge_gt_0.25", df["pass_blended_or_highraw"]),
    ]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, dt.timezone.utc).isoformat(),
        "removed_cities": sorted(REMOVED_CITIES),
        "clob_gate": clob_gate,
        "self_checks": checks,
        "results": results,
        "incremental_within_operational_base": incremental,
        "operational_base_by_city_blended": by_group(base, df["pass_blended_010"], "city"),
        "operational_base_by_target_date_blended": by_group(base, df["pass_blended_010"], "target_date"),
        "removed_city_contribution": by_group(df[df["remove_city"]].copy(), pd.Series(False, index=df[df["remove_city"]].index), "city"),
        "gt_t28_contribution": by_group(df[~df["pass_t28"]].copy(), pd.Series(False, index=df[~df["pass_t28"]].index), "city"),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# v1_25_75 去除 6 个 ECMWF 城市 + T28 ban 后的 blender overlay",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{DB_PATH}`，只读 fact tables，不使用 legacy DB。",
        f"- 生成时间 UTC：`{report['generated_at_utc']}`。",
        f"- DB mtime UTC：`{report['db_mtime_utc']}`。",
        f"- `MAX(fact_built_at_utc)`：`{checks['max_fact_built_at_utc'][0]['max_fact_built_at_utc']}`。",
        f"- CLOB coverage gate：`gate_pass={clob_gate.get('gate_pass')}`；`missing_order_rows={clob_gate['db_fills']['missing_order_rows']}`；`over_order_keys={clob_gate['db_fills']['over_order_keys']}`；`db_fill_cost_minus_fact_cost={clob_gate.get('db_fill_cost_minus_fact_cost')}`。",
        f"- 目标样本：`mid_price_core_v1_25_75` / `strategy_id={STRATEGY_ID}` / settled `live_real`，共 `{len(df)}` fills，`{df['target_date'].min()} -> {df['target_date'].max()}`。",
        f"- 新 operational base：剔除 `{', '.join(sorted(REMOVED_CITIES))}`，且 `hours_to_settle <= 28`。",
        "",
        "### 5 行 SQL 自检",
        "",
        "trade_class 分布：",
        table(checks["trade_class"]),
        "",
        "settlement_status 分布：",
        table(checks["settlement_status"]),
        "",
        "fact_signal_candidates 覆盖：",
        table(checks["candidate_coverage"]),
        "",
        "orders/fills by venue/status：",
        table(checks["orders_fills"]),
        "",
        "目标策略样本：",
        table(checks["target_sample"]),
        "",
        "## 结论",
        "",
    ]
    op = next(x for x in results if x["gate"] == "operational_base_remove_cities_and_T28")
    op_blend = next(x for x in results if x["gate"] == "operational_base_plus_blended_edge_ge_0.10")
    incr_blend = next(x for x in incremental if x["gate"] == "within_base_blended_edge_ge_0.10")
    incr_hi = next(x for x in incremental if x["gate"] == "within_base_blended_or_raw_edge_gt_0.25")
    lines.extend(
        [
            f"- 剔除 6 城 + ban `T>28` 后，post 样本从原始 `{results[0]['post_2026_06_01']['fills']}` fills / `{money(results[0]['post_2026_06_01']['pnl_usd'])}` 变为 `{op['post_2026_06_01']['kept_fills']}` fills / `{money(op['post_2026_06_01']['kept_pnl_usd'])}`。这一步本身已经把 6 月后的主要亏损拦掉。",
            f"- 在这个新 base 内再叠纯 blender `blended_edge>=0.10`，post 保留 `{incr_blend['post_2026_06_01']['kept_fills']}` fills / `{money(incr_blend['post_2026_06_01']['kept_pnl_usd'])}`，相对新 base 的增量 `delta={money(incr_blend['post_2026_06_01']['delta_pnl_if_filter'])}`。",
            f"- 但它 pre 期相对新 base 的增量是 `{money(incr_blend['pre_2026_06_01']['delta_pnl_if_filter'])}`，仍然说明纯 blender 是近期漂移过滤器，不是稳定 alpha。",
            f"- 如果用“blender 或 raw_edge>0.25 例外”，post 相对新 base `delta={money(incr_hi['post_2026_06_01']['delta_pnl_if_filter'])}`，pre 伤害 `{money(incr_hi['pre_2026_06_01']['delta_pnl_if_filter'])}`；这是比纯 blender 更温和的 paper/shadow 候选。",
            "",
            "## 总体控制变量结果",
            "",
            table(result_rows(results, "full")),
            "",
            "## Pre / Post 控制变量结果",
            "",
            "### pre: target_date < 2026-06-01",
            "",
            table(result_rows(results, "pre_2026_06_01")),
            "",
            "### post: target_date >= 2026-06-01",
            "",
            table(result_rows(results, "post_2026_06_01")),
            "",
            "## 在新 operational base 内看 blender 增量",
            "",
            "这里分母已经是“剔除 6 城 + T<=28”的剩余 fill，只看 blender 是否还有额外价值。",
            "",
            "### full",
            "",
            table(result_rows(incremental, "full")),
            "",
            "### pre",
            "",
            table(result_rows(incremental, "pre_2026_06_01")),
            "",
            "### post",
            "",
            table(result_rows(incremental, "post_2026_06_01")),
            "",
            "## 新 base 内 blender 按城市/日期归因",
            "",
            "### by city",
            "",
            table(report["operational_base_by_city_blended"][:20]),
            "",
            "### by target_date",
            "",
            table(report["operational_base_by_target_date_blended"][:20]),
            "",
            "## 被新规则移除的亏损来源",
            "",
            "### removed cities",
            "",
            table(report["removed_city_contribution"][:20]),
            "",
            "### >T28",
            "",
            table(report["gt_t28_contribution"][:20]),
            "",
            "## 交易解释",
            "",
            "这次新规则把 blender 的定位改变了：城市黑名单和 T28 ban 已经承担了大部分风险控制，blender 只剩二级确认作用。相对原始 v1，带 blender 的组合仍改善 6 月后结果；但相对“已剔除 6 城 + T<=28”的新 base，纯 blender 在 post 期也是负增量，所以不应把它解释为独立 alpha。",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD)
    print(OUT_JSON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
