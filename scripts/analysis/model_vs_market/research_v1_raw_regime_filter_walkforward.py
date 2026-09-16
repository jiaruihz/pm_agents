#!/usr/bin/env python3
"""Walk-forward gate study for mid_price_core_v1 0.25-0.75 live fills.

The analysis keeps the original live fill price, size, and settlement fixed.
Each gate only asks whether a historical v1_25_75 fill would have been kept or
filtered. This is a control-variable overlay, not a fill-rate simulation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.blend import blend_probability, load_default_config
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)

DB_PATH = ROOT / "runtime" / "weather.db"

RECENT_START = "2026-06-01"
STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
STRATEGY_LABEL = "mid_price_core_v1_25_75"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


@dataclass(frozen=True)
class Gate:
    gate_id: str
    description: str
    family: str
    is_hindsight: bool
    predicate: Callable[[pd.DataFrame], pd.Series]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def side_prob(side: str, p_yes: float) -> float:
    return p_yes if side == "BUY_YES" else 1.0 - p_yes


def side_edge(side: str, p_yes: float, entry_price: float) -> float:
    return side_prob(side, p_yes) - entry_price


def period_of(target_date: str) -> str:
    return "post_2026_06_01" if target_date >= RECENT_START else "pre_2026_06_01"


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


def num(x: Any, digits: int = 3, signed: bool = False) -> str:
    if x is None:
        return ""
    prefix = "+" if signed else ""
    return f"{float(x):{prefix}.{digits}f}"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def pnl_ex_top_wins(df: pd.DataFrame, n: int = 5) -> float:
    if df.empty:
        return 0.0
    wins = df[df["pnl_usd_at_fill"] > 0]["pnl_usd_at_fill"].sort_values(ascending=False)
    return float(df["pnl_usd_at_fill"].sum()) - float(wins.head(n).sum())


def summarize_overlay(df: pd.DataFrame, keep: pd.Series) -> dict[str, Any]:
    if len(df) != len(keep):
        raise ValueError("keep mask length mismatch")
    keep = keep.fillna(False).astype(bool)
    kept = df[keep].copy()
    filtered = df[~keep].copy()
    filtered_winners = filtered[filtered["pnl_usd_at_fill"] > 0]
    filtered_losers = filtered[filtered["pnl_usd_at_fill"] < 0]
    kept_winners = kept[kept["pnl_usd_at_fill"] > 0]
    kept_losers = kept[kept["pnl_usd_at_fill"] < 0]

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
        "kept_winner_fills": int(len(kept_winners)),
        "kept_loser_fills": int(len(kept_losers)),
        "pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(df), 6),
        "kept_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(kept), 6),
        "filtered_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(filtered), 6),
        "avg_raw_edge": None if df.empty else round(float(df["raw_edge_at_fill"].mean()), 6),
        "avg_blended_edge": None if df.empty else round(float(df["blended_edge_at_fill"].mean()), 6),
        "kept_avg_raw_edge": None if kept.empty else round(float(kept["raw_edge_at_fill"].mean()), 6),
        "kept_avg_blended_edge": None if kept.empty else round(float(kept["blended_edge_at_fill"].mean()), 6),
        "filtered_avg_raw_edge": None if filtered.empty else round(float(filtered["raw_edge_at_fill"].mean()), 6),
        "filtered_avg_blended_edge": None if filtered.empty else round(float(filtered["blended_edge_at_fill"].mean()), 6),
    }


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
          execution_id,
          order_id,
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
          settlement_status,
          final_yes,
          pnl_usd_at_fill,
          win_by_count,
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
    df["abs_yes_divergence"] = (df["model_p_yes"] - df["market_yes_price"]).abs()
    df["side_prob_divergence"] = df["raw_side_prob"] - df["market_side_prob"]
    df["hours_bin"] = pd.cut(
        df["hours_to_settle"],
        bins=[-10, 22, 24, 26, 28, 1000],
        labels=["<T-22", "T-22-24", "T-24-26", "T-26-28", ">T-28"],
    ).astype(str)
    df["raw_edge_bin"] = pd.cut(
        df["raw_edge_at_fill"],
        bins=[-10, 0.10, 0.15, 0.25, 10],
        labels=["<=0.10", "0.10-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    df["divergence_bin"] = pd.cut(
        df["abs_yes_divergence"],
        bins=[-0.001, 0.05, 0.10, 0.20, 1.0],
        labels=["<=0.05", "0.05-0.10", "0.10-0.20", ">0.20"],
    ).astype(str)

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
    return df


def pre_profitable_cities(df: pd.DataFrame) -> set[str]:
    pre = df[df["target_date"] < RECENT_START]
    rows = []
    for city, gdf in pre.groupby("city"):
        days = int(gdf["target_date"].nunique())
        pnl = float(gdf["pnl_usd_at_fill"].sum())
        if days >= 3 and pnl > 0:
            rows.append(city)
    return set(rows)


def pre_weak_cities(df: pd.DataFrame) -> set[str]:
    pre = df[df["target_date"] < RECENT_START]
    rows = []
    for city, gdf in pre.groupby("city"):
        days = int(gdf["target_date"].nunique())
        pnl = float(gdf["pnl_usd_at_fill"].sum())
        if days >= 3 and pnl < 0:
            rows.append(city)
    return set(rows)


def gates_for(df: pd.DataFrame) -> list[Gate]:
    profitable_cities = pre_profitable_cities(df)
    weak_cities = pre_weak_cities(df)
    return [
        Gate(
            "no_gate",
            "原始 v1_25_75，不过滤；作为对照基线。",
            "baseline",
            False,
            lambda x: pd.Series(True, index=x.index),
        ),
        Gate(
            "blended_edge_ge_0.10",
            "当前 blender 配置下，按成交价重算 blended side edge >= 0.10。",
            "market_confirmation",
            False,
            lambda x: x["blended_edge_at_fill"] >= 0.10,
        ),
        Gate(
            "market_confirm_or_raw_edge_gt_0.25",
            "保留 blended_edge>=0.10，或 raw_edge_at_fill>0.25 的高 raw edge 例外。",
            "market_confirmation",
            False,
            lambda x: (x["blended_edge_at_fill"] >= 0.10) | (x["raw_edge_at_fill"] > 0.25),
        ),
        Gate(
            "raw_edge_gt_0.25",
            "只保留 raw_edge_at_fill > 0.25 的高 raw edge 样本。",
            "raw_edge",
            False,
            lambda x: x["raw_edge_at_fill"] > 0.25,
        ),
        Gate(
            "raw_edge_ge_0.15",
            "过滤 raw_edge_at_fill < 0.15 的最边际样本。",
            "raw_edge",
            False,
            lambda x: x["raw_edge_at_fill"] >= 0.15,
        ),
        Gate(
            "timing_T22_26",
            "只保留成交时距结算 T-22 到 T-26 小时。",
            "timing",
            False,
            lambda x: (x["hours_to_settle"] >= 22) & (x["hours_to_settle"] < 26),
        ),
        Gate(
            "timing_T22_28",
            "只保留成交时距结算 T-22 到 T-28 小时。",
            "timing",
            False,
            lambda x: (x["hours_to_settle"] >= 22) & (x["hours_to_settle"] < 28),
        ),
        Gate(
            "exclude_gt_T28",
            "过滤 >T-28 的过早信号。",
            "timing",
            False,
            lambda x: x["hours_to_settle"] <= 28,
        ),
        Gate(
            "model_gfs_only",
            "只保留 model_version=gfs。",
            "model_version",
            False,
            lambda x: x["model_version"].fillna("").str.lower() == "gfs",
        ),
        Gate(
            "model_ecmwf_only",
            "只保留 model_version=ecmwf，诊断用。",
            "model_version",
            False,
            lambda x: x["model_version"].fillna("").str.lower() == "ecmwf",
        ),
        Gate(
            "pre_profitable_cities_only",
            "只保留 6 月前 active_days>=3 且 PnL>0 的城市；仅用训练期城市表现生成。",
            "city",
            False,
            lambda x: x["city"].isin(profitable_cities),
        ),
        Gate(
            "exclude_pre_weak_cities",
            "剔除 6 月前 active_days>=3 且 PnL<0 的城市；仅用训练期城市表现生成。",
            "city",
            False,
            lambda x: ~x["city"].isin(weak_cities),
        ),
        Gate(
            "buy_no_only",
            "只保留 BUY_NO，诊断 side risk。",
            "side",
            False,
            lambda x: x["side"] == "BUY_NO",
        ),
        Gate(
            "buy_yes_only",
            "只保留 BUY_YES，诊断 side risk。",
            "side",
            False,
            lambda x: x["side"] == "BUY_YES",
        ),
        Gate(
            "combo_blended_or_highraw_T22_28",
            "保留 T-22 到 T-28，且 blended_edge>=0.10 或 raw_edge>0.25。",
            "combo",
            False,
            lambda x: ((x["hours_to_settle"] >= 22) & (x["hours_to_settle"] < 28))
            & ((x["blended_edge_at_fill"] >= 0.10) | (x["raw_edge_at_fill"] > 0.25)),
        ),
        Gate(
            "post_weak_city_blacklist_DIAGNOSTIC",
            "剔除 6 月后已知弱城市，后验诊断用，不能作为上线依据。",
            "hindsight",
            True,
            lambda x: ~x["city"].isin({"BuenosAires", "Munich", "Jeddah", "Karachi", "NYC", "Moscow", "Ankara"}),
        ),
    ]


def evaluate_gate(df: pd.DataFrame, gate: Gate) -> dict[str, Any]:
    keep = gate.predicate(df)
    out = {
        "gate_id": gate.gate_id,
        "description": gate.description,
        "family": gate.family,
        "is_hindsight": gate.is_hindsight,
        "full": summarize_overlay(df, keep),
    }
    for period in ["pre_2026_06_01", "post_2026_06_01"]:
        mask = df["period"] == period
        out[period] = summarize_overlay(df[mask].copy(), keep[mask].copy())
    return out


def group_rows(df: pd.DataFrame, gate: Gate, group_col: str, period: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    gdf = df if period is None else df[df["period"] == period].copy()
    keep = gate.predicate(gdf)
    rows: list[dict[str, Any]] = []
    for key, part in gdf.groupby(group_col, dropna=False):
        part_keep = keep.loc[part.index]
        summary = summarize_overlay(part, part_keep)
        rows.append(
            {
                group_col: str(key),
                "fills": summary["fills"],
                "pnl_usd": summary["pnl_usd"],
                "kept_pnl_usd": summary["kept_pnl_usd"],
                "filtered_pnl_usd": summary["filtered_pnl_usd"],
                "delta_pnl_if_filter": summary["delta_pnl_if_filter"],
                "avoided_loss_usd": summary["avoided_loss_usd"],
                "missed_profit_usd": summary["missed_profit_usd"],
                "kept_fills": summary["kept_fills"],
                "filtered_fills": summary["filtered_fills"],
            }
        )
    rows.sort(key=lambda r: abs(float(r["delta_pnl_if_filter"])), reverse=True)
    return rows[:limit]


def target_date_robustness(df: pd.DataFrame, gate: Gate) -> dict[str, Any]:
    keep = gate.predicate(df)
    rows: list[dict[str, Any]] = []
    for date, part in df.groupby("target_date"):
        summary = summarize_overlay(part, keep.loc[part.index])
        rows.append(
            {
                "target_date": date,
                "fills": summary["fills"],
                "pnl_usd": summary["pnl_usd"],
                "delta_pnl_if_filter": summary["delta_pnl_if_filter"],
            }
        )
    deltas = [float(r["delta_pnl_if_filter"]) for r in rows]
    return {
        "dates": len(rows),
        "positive_delta_dates": sum(1 for x in deltas if x > 0),
        "negative_delta_dates": sum(1 for x in deltas if x < 0),
        "median_delta_usd": round(float(pd.Series(deltas).median()), 6) if deltas else 0.0,
        "rows": rows,
    }


def gate_summary_rows(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in results:
        pre = item["pre_2026_06_01"]
        post = item["post_2026_06_01"]
        full = item["full"]
        rows.append(
            {
                "gate": item["gate_id"],
                "family": item["family"],
                "pre_delta": money(pre["delta_pnl_if_filter"]),
                "post_delta": money(post["delta_pnl_if_filter"]),
                "pre_kept_pnl": money(pre["kept_pnl_usd"]),
                "post_kept_pnl": money(post["kept_pnl_usd"]),
                "post_kept_fills": str(post["kept_fills"]),
                "post_filtered_fills": str(post["filtered_fills"]),
                "post_avoided_loss": money(post["avoided_loss_usd"], signed=False),
                "post_missed_profit": money(post["missed_profit_usd"], signed=False),
                "full_delta": money(full["delta_pnl_if_filter"]),
                "hindsight": "yes" if item["is_hindsight"] else "",
            }
        )
    return rows


def format_metric_rows(results: list[dict[str, Any]], period_key: str) -> list[dict[str, str]]:
    rows = []
    for item in results:
        s = item[period_key]
        rows.append(
            {
                "gate": item["gate_id"],
                "fills": str(s["fills"]),
                "pnl": money(s["pnl_usd"]),
                "kept_fills": str(s["kept_fills"]),
                "kept_pnl": money(s["kept_pnl_usd"]),
                "kept_roi": pct(s["kept_roi"]),
                "filtered_pnl": money(s["filtered_pnl_usd"]),
                "delta": money(s["delta_pnl_if_filter"]),
                "filtered_winners": str(s["filtered_winner_fills"]),
                "filtered_losers": str(s["filtered_loser_fills"]),
                "ex_top5_kept": money(s["kept_pnl_ex_top5_wins_usd"]),
            }
        )
    return rows


def formatted_group_rows(rows: list[dict[str, Any]], group_col: str) -> list[dict[str, str]]:
    out = []
    for row in rows:
        out.append(
            {
                group_col: str(row[group_col]),
                "fills": str(row["fills"]),
                "pnl": money(row["pnl_usd"]),
                "kept_pnl": money(row["kept_pnl_usd"]),
                "filtered_pnl": money(row["filtered_pnl_usd"]),
                "delta": money(row["delta_pnl_if_filter"]),
                "avoided_loss": money(row["avoided_loss_usd"], signed=False),
                "missed_profit": money(row["missed_profit_usd"], signed=False),
                "kept_fills": str(row["kept_fills"]),
                "filtered_fills": str(row["filtered_fills"]),
            }
        )
    return out


def build_markdown(
    df: pd.DataFrame,
    self_checks: dict[str, Any],
    clob_gate: dict[str, Any],
    results: list[dict[str, Any]],
    robustness: dict[str, Any],
    group_details: dict[str, Any],
) -> str:
    baseline = next(r for r in results if r["gate_id"] == "no_gate")
    blended = next(r for r in results if r["gate_id"] == "blended_edge_ge_0.10")
    blended_or_highraw = next(r for r in results if r["gate_id"] == "market_confirm_or_raw_edge_gt_0.25")
    raw_high = next(r for r in results if r["gate_id"] == "raw_edge_gt_0.25")
    combo = next(r for r in results if r["gate_id"] == "combo_blended_or_highraw_T22_28")
    pre_city = next(r for r in results if r["gate_id"] == "pre_profitable_cities_only")
    exclude_weak_city = next(r for r in results if r["gate_id"] == "exclude_pre_weak_cities")

    gate_rows = gate_summary_rows(results)
    pre_rows = format_metric_rows(results, "pre_2026_06_01")
    post_rows = format_metric_rows(results, "post_2026_06_01")

    lines = [
        "# v1 raw regime filter walk-forward 控制变量研究",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{DB_PATH}`，只读 `fact_trades` / `fact_signal_candidates`；本报告不使用 legacy DB。",
        f"- 生成时间 UTC：`{dt.datetime.now(dt.timezone.utc).isoformat()}`。",
        f"- DB mtime UTC：`{dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, dt.timezone.utc).isoformat()}`。",
        f"- `MAX(fact_built_at_utc)`：`{self_checks['max_fact_built_at_utc'][0]['MAX(fact_built_at_utc)']}`。",
        f"- CLOB coverage gate：`gate_pass={clob_gate.get('gate_pass')}`；`missing_order_rows={clob_gate['db_fills']['missing_order_rows']}`；`over_order_keys={clob_gate['db_fills']['over_order_keys']}`；`db_fill_cost_minus_fact_cost={clob_gate.get('db_fill_cost_minus_fact_cost')}`。",
        f"- 目标样本：`{STRATEGY_LABEL}` / `strategy_id={STRATEGY_ID}` / `trade_class=live_real` / `settlement_status=settled`，共 `{len(df)}` fills，`{df['target_date'].min()} -> {df['target_date'].max()}`。",
        "",
        "### 5 行 SQL 自检",
        "",
        "trade_class 分布：",
        table(self_checks["trade_class"]),
        "",
        "settlement_status 分布：",
        table(self_checks["settlement_status"]),
        "",
        "fact_signal_candidates 覆盖：",
        table(self_checks["candidate_coverage"]),
        "",
        "orders/fills by venue/status：",
        table(self_checks["orders_fills"]),
        "",
        "目标策略样本：",
        table(self_checks["target_sample"]),
        "",
        "## 目标指标与方法",
        "",
        "`regime_filter_walkforward` = 对原始 `mid_price_core_v1_25_75` 真实已成交已结算 fill 做控制变量 overlay：成交价、size、结算结果固定，只改变 gate 是否会保留这笔 fill。",
        "",
        "- `pre` = `target_date < 2026-06-01`，视作训练/历史稳定期。",
        "- `post` = `target_date >= 2026-06-01`，视作退化观察期。",
        "- `delta_pnl_if_filter = - filtered_pnl`；为正表示 gate 过滤掉的是净亏损。",
        "- `avoided_loss` 是被过滤 loser 的亏损绝对值；`missed_profit` 是被过滤 winner 的盈利。",
        "- 后验 gate 标为 `hindsight=yes`，只能做诊断，不能作为上线依据。",
        "",
        "## 结论",
        "",
        f"- **原始 v1_25_75 明确出现 regime shift**：pre PnL `{money(baseline['pre_2026_06_01']['pnl_usd'])}`，post PnL `{money(baseline['post_2026_06_01']['pnl_usd'])}`。",
        f"- **最强的非后验 walk-forward 线索是城市层风控**：`pre_profitable_cities_only` 的 pre delta `{money(pre_city['pre_2026_06_01']['delta_pnl_if_filter'])}`、post delta `{money(pre_city['post_2026_06_01']['delta_pnl_if_filter'])}`；`exclude_pre_weak_cities` 的 pre delta `{money(exclude_weak_city['pre_2026_06_01']['delta_pnl_if_filter'])}`、post delta `{money(exclude_weak_city['post_2026_06_01']['delta_pnl_if_filter'])}`。这说明 v1 raw 的失效更像城市/数据源 regime 分化，而不是全局 raw edge 失效。",
        f"- **单纯 blended gate 是有效止血，但不是稳定增强**：pre delta `{money(blended['pre_2026_06_01']['delta_pnl_if_filter'])}`，post delta `{money(blended['post_2026_06_01']['delta_pnl_if_filter'])}`。它 6 月后避开亏损，但 6 月前会过滤掉净盈利。",
        f"- **如果要用 blender，更合理的候选不是纯 blended gate，而是 `market_confirm_or_raw_edge_gt_0.25`**：允许高 raw edge 例外后，pre delta `{money(blended_or_highraw['pre_2026_06_01']['delta_pnl_if_filter'])}`，post delta `{money(blended_or_highraw['post_2026_06_01']['delta_pnl_if_filter'])}`；它比纯 blended 更少伤害训练期，但日期级 median delta 仍接近 0，不足以单独上线。",
        f"- **raw_edge>0.25 本身仍有信号，但样本少**：post kept PnL `{money(raw_high['post_2026_06_01']['kept_pnl_usd'])}`，post kept fills `{raw_high['post_2026_06_01']['kept_fills']}`；这支持“不要简单黑掉高分歧/高 raw edge”。",
        f"- **timing 过滤有交易含义，但不能单独上线**：组合 gate `combo_blended_or_highraw_T22_28` 的 post delta `{money(combo['post_2026_06_01']['delta_pnl_if_filter'])}`，但 pre delta `{money(combo['pre_2026_06_01']['delta_pnl_if_filter'])}`，说明 timing 可作为 size/risk 条件，不足以单独证明 alpha。",
        "- **下一步不是直接 live 替换**：先 paper/shadow `blended_filter_25_75_v0`，同时物化 `forecast_jump` / `side_flip` / snapshot transition 字段，才能解释 raw 为什么在某些窗口失效。",
        "",
        "## Gate 总表",
        "",
        table(gate_rows),
        "",
        "## Pre 训练期明细",
        "",
        table(pre_rows),
        "",
        "## Post 退化期明细",
        "",
        table(post_rows),
        "",
        "## 日期鲁棒性",
        "",
        "重点看非后验 gate。`positive_delta_dates` 表示某天过滤有帮助，`negative_delta_dates` 表示某天过滤伤害收益。",
        "",
        table(
            [
                {
                    "gate": gate_id,
                    "dates": str(v["dates"]),
                    "positive_delta_dates": str(v["positive_delta_dates"]),
                    "negative_delta_dates": str(v["negative_delta_dates"]),
                    "median_delta": money(v["median_delta_usd"]),
                }
                for gate_id, v in robustness.items()
                if not gate_id.endswith("DIAGNOSTIC")
            ]
        ),
        "",
        "## 推荐候选 gate 的 Post 下钻",
        "",
        "候选口径：`market_confirm_or_raw_edge_gt_0.25`。它保留 market confirmation，也给高 raw edge 一个例外，避免把 raw 还有效的尾部信号一起砍掉。注意：从 walk-forward 强度看，城市层 gate 比这个 blended 变体更强；本节下钻 blended 变体，是为了给 `blended_filter_25_75_v0` 的 paper/shadow 参数提供依据。",
        "",
        "按 side：",
        table(formatted_group_rows(group_details["candidate_by_side"], "side")),
        "",
        "按 model_version：",
        table(formatted_group_rows(group_details["candidate_by_model_version"], "model_version")),
        "",
        "按 timing：",
        table(formatted_group_rows(group_details["candidate_by_hours_bin"], "hours_bin")),
        "",
        "按城市（按过滤影响绝对值排序）：",
        table(formatted_group_rows(group_details["candidate_by_city"], "city")),
        "",
        "## 交易建议",
        "",
        "1. **原始 `mid_price_core_v1_25_75` 不建议继续原 size live**。post 已从正收益转为明显负收益，且 BUY_YES/BUY_NO 都退化；继续裸跑没有量化依据。",
        "2. **下一步主研究方向应转向城市层 regime / 数据源质量**。非后验 city gate 同时改善 pre 和 post，优先级高于继续调 blended 阈值。",
        "3. **`blended_filter_25_75_v0` 可以 paper/shadow，暂不建议直接 live**。理由是 pure blended gate post 有效，但 pre 明显伤害收益；它更像 recent drift filter，不是稳定 alpha。",
        "4. **paper/shadow 候选参数**：主线用城市风险 gate 控 size/城市池；blender 侧优先测试 `blended_edge>=0.10 OR raw_edge>0.25`，再叠加 `T-22 到 T-28` 加权更高、`>T-28` 降 size。",
        "5. **上线前 gate**：至少要等 1 周新 shadow 样本，并要求 `missed_profit <= avoided_loss`、日期级 median delta 不为负、city/side/model_version 不集中靠单一尾部事件。",
        "6. **研究工程下一步**：把 `forecast_jump`、`side_flip`、同 market opposite-side transition 物化进 `fact_signal_candidates`，再做逐 snapshot 血缘复盘；现在的 fact 表只能做成交后 overlay，无法完整解释 raw 失效机制。",
        "",
        "## 口径限制",
        "",
        "- 本报告没有模拟真实成交率变化；如果 gate 影响订单排队、盘口滑点或资金占用，真实 paper/live 会偏离 overlay。",
        "- gate 阈值不是最终参数；本报告只用于收敛候选研究方向。",
        "- `post_weak_city_blacklist_DIAGNOSTIC` 是后验黑名单，只用于估计亏损城市贡献，不能作为上线依据。",
        "- 当前 fact 表没有显式 `forecast_jump` / `side_flip` 字段，因此机制解释仍需下一轮血缘研究。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    output_dir = prepare_new_run_output(
        resolve_run_output(
            "v1_raw_regime_filter_walkforward",
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    clob_gate = run_clob_gate()
    if not clob_gate.get("gate_pass"):
        raise RuntimeError("CLOB coverage gate failed; refusing to publish live_real PnL analysis")

    conn = connect()
    self_checks = {
        "max_fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class"),
        "settlement_status": fetchall(
            conn,
            "SELECT COALESCE(settlement_status, '[NULL]') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY settlement_status",
        ),
        "candidate_coverage": fetchall(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        ),
        "orders_fills": fetchall(
            conn,
            "SELECT o.venue, o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.venue, o.status",
        ),
        "target_sample": fetchall(
            conn,
            "SELECT strategy_id, strategy_name, trade_class, COUNT(*) AS fills, "
            "MIN(target_date) AS min_target_date, MAX(target_date) AS max_target_date "
            "FROM fact_trades WHERE strategy_id=? AND execution_policy='mid_price_core_v1' "
            "AND entry_price_window='0.25-0.75' GROUP BY strategy_id, strategy_name, trade_class",
            (STRATEGY_ID,),
        ),
    }
    df = load_trades(conn)
    gates = gates_for(df)
    results = [evaluate_gate(df, gate) for gate in gates]
    robustness = {gate.gate_id: target_date_robustness(df, gate) for gate in gates}
    candidate_gate = next(g for g in gates if g.gate_id == "market_confirm_or_raw_edge_gt_0.25")
    group_details = {
        "candidate_by_side": group_rows(df, candidate_gate, "side", period="post_2026_06_01"),
        "candidate_by_model_version": group_rows(df, candidate_gate, "model_version", period="post_2026_06_01"),
        "candidate_by_hours_bin": group_rows(df, candidate_gate, "hours_bin", period="post_2026_06_01"),
        "candidate_by_city": group_rows(df, candidate_gate, "city", period="post_2026_06_01", limit=16),
    }

    output = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, dt.timezone.utc).isoformat(),
        "strategy_id": STRATEGY_ID,
        "strategy_label": STRATEGY_LABEL,
        "recent_start": RECENT_START,
        "clob_gate": clob_gate,
        "self_checks": self_checks,
        "rows": int(len(df)),
        "min_target_date": str(df["target_date"].min()),
        "max_target_date": str(df["target_date"].max()),
        "results": results,
        "robustness": robustness,
        "group_details": group_details,
        "living_doc": "docs/analysis/model_vs_market.md",
    }
    result_json = output_dir / "result.json"
    result_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_json": str(result_json), "rows": len(df)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
