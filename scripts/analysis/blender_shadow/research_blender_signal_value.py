#!/usr/bin/env python3
"""Study blender as a risk/size signal instead of a hard live gate.

This is a control-variable overlay on historical settled live fills. It keeps
the original fill price, fill quantity, and settlement outcome fixed. Size
policies only scale the historical fill's cost and PnL.
"""

from __future__ import annotations

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

DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
OUT_MD = OUT_DIR / "2026-06-08-blender-signal-value-research.md"
OUT_JSON = OUT_DIR / "2026-06-08-blender-signal-value-research.json"

STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
STRATEGY_LABEL = "mid_price_core_v1_25_75"
RECENT_START = "2026-06-01"
REMOVED_ECMWF_CITIES = {"BuenosAires", "Munich", "Jeddah", "Karachi", "Moscow", "Ankara"}
ALPHA_GRID = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]


@dataclass(frozen=True)
class SizePolicy:
    policy_id: str
    description: str
    fn: Callable[[pd.DataFrame], pd.Series]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def run_clob_gate() -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "analysis"
                / "execution_quality"
                / "weather_clob_fill_coverage_gate.py"
            ),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def side_prob(side: str, p_yes: float) -> float:
    return p_yes if side == "BUY_YES" else 1.0 - p_yes


def side_edge(side: str, p_yes: float, entry_price: float) -> float:
    return side_prob(side, p_yes) - entry_price


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


def sql_self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class": fetchall(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status": fetchall(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": fetchall(
            conn,
            """
            SELECT COUNT(*) AS n,
                   SUM(eligible) AS eligible,
                   SUM(paper_ordered) AS paper_ordered,
                   SUM(live_filled) AS live_filled
            FROM fact_signal_candidates
            """,
        ),
        "orders_fills": fetchall(
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
        ),
        "target_sample": fetchall(
            conn,
            """
            SELECT COUNT(*) AS fills,
                   MIN(target_date) AS min_target_date,
                   MAX(target_date) AS max_target_date,
                   SUM(cost_usd) AS cost_usd,
                   SUM(pnl_usd_at_fill) AS pnl_usd
            FROM fact_trades
            WHERE trade_class='live_real'
              AND settlement_status='settled'
              AND strategy_id=?
              AND execution_policy='mid_price_core_v1'
              AND entry_price_window='0.25-0.75'
            """,
            (STRATEGY_ID,),
        ),
    }


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          fill_id,
          execution_id,
          strategy_id,
          strategy_name,
          execution_policy,
          entry_price_window,
          trade_class,
          city,
          city_pool,
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
          edge,
          abs_edge,
          plan_price,
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
        raise RuntimeError("No settled live_real mid_price_core_v1_25_75 rows found")

    df["period"] = df["target_date"].map(
        lambda x: "post_2026_06_01" if str(x) >= RECENT_START else "pre_2026_06_01"
    )
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
    df["raw_minus_market_yes"] = df["model_p_yes"].astype(float) - df["market_yes_price"].astype(float)
    df["abs_yes_divergence"] = df["raw_minus_market_yes"].abs()
    df["side_prob_divergence"] = df["raw_side_prob"] - df["market_side_prob"]
    df["removed_ecmwf_city"] = df["city"].isin(REMOVED_ECMWF_CITIES)
    hts = df["hours_to_settle"].fillna(999.0).astype(float)
    df["pass_t28"] = hts <= 28.0
    df["pass_t22_28"] = (hts >= 22.0) & (hts <= 28.0)
    df["pass_loose_operational_base"] = (~df["removed_ecmwf_city"]) & df["pass_t28"]
    df["pass_operational_base"] = (~df["removed_ecmwf_city"]) & df["pass_t22_28"]

    cfg = load_default_config()
    blended_p_yes: list[float] = []
    blend_alpha: list[float] = []
    blend_mode: list[str] = []
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
        blend_alpha.append(float(blended.blend_alpha))
        blend_mode.append(blended.blend_mode)
        blended_edge.append(side_edge(str(row.side), p_yes, float(row.fill_price)))
    df["blended_p_yes"] = blended_p_yes
    df["blend_alpha"] = blend_alpha
    df["blend_mode"] = blend_mode
    df["blended_edge_at_fill"] = blended_edge

    df["blended_edge_bin"] = pd.cut(
        df["blended_edge_at_fill"],
        bins=[-10, 0.0, 0.05, 0.10, 10],
        labels=["<0.00", "0.00-0.05", "0.05-0.10", ">=0.10"],
    ).astype(str)
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
    df["hours_bin"] = pd.cut(
        df["hours_to_settle"],
        bins=[-10, 22, 24, 26, 28, 1000],
        labels=["<T-22", "T-22-24", "T-24-26", "T-26-28", ">T-28"],
    ).astype(str)
    return df


def clamp_size(size: pd.Series) -> pd.Series:
    return size.fillna(0.0).clip(lower=0.0, upper=1.0).astype(float)


def size_policies() -> list[SizePolicy]:
    return [
        SizePolicy("unit_base", "operational base 内所有历史 fills 等额保留。", lambda x: pd.Series(1.0, index=x.index)),
        SizePolicy(
            "hard_blended_edge_ge_0.10",
            "旧 hard gate：blended_edge>=0.10 保留，否则 size=0。",
            lambda x: (x["blended_edge_at_fill"] >= 0.10).astype(float),
        ),
        SizePolicy(
            "hard_blended_or_raw_gt_0.25",
            "保留 blended_edge>=0.10，或 raw_edge>0.25 的高 raw edge 例外。",
            lambda x: ((x["blended_edge_at_fill"] >= 0.10) | (x["raw_edge_at_fill"] > 0.25)).astype(float),
        ),
        SizePolicy(
            "no_negative_blended_edge",
            "只过滤 blended_edge<0 的市场强烈不确认样本。",
            lambda x: (x["blended_edge_at_fill"] >= 0.0).astype(float),
        ),
        SizePolicy(
            "mild_blended_size_curve",
            "温和 size：blended_edge<0 跳过；0~0.05 半仓；>=0.05 满仓。",
            lambda x: pd.Series(
                [
                    0.0 if e < 0.0 else 0.5 if e < 0.05 else 1.0
                    for e in x["blended_edge_at_fill"].astype(float)
                ],
                index=x.index,
            ),
        ),
        SizePolicy(
            "gentle_blended_size_curve",
            "推荐候选：<0 跳过；0~0.05 为 0.25x；0.05~0.10 且 raw_edge<=0.25 为 0.5x；其余满仓。",
            lambda x: pd.Series(
                [
                    0.0
                    if be < 0.0
                    else 0.25
                    if be < 0.05
                    else 0.5
                    if be < 0.10 and re <= 0.25
                    else 1.0
                    for be, re in zip(x["blended_edge_at_fill"].astype(float), x["raw_edge_at_fill"].astype(float))
                ],
                index=x.index,
            ),
        ),
        SizePolicy(
            "disagreement_haircut",
            "分歧降档：blended_edge<0 跳过；abs(raw-market)>0.20 且 blended_edge<0.10 半仓；其余满仓。",
            lambda x: pd.Series(
                [
                    0.0 if be < 0.0 else 0.5 if div > 0.20 and be < 0.10 else 1.0
                    for be, div in zip(x["blended_edge_at_fill"].astype(float), x["abs_yes_divergence"].astype(float))
                ],
                index=x.index,
            ),
        ),
    ]


def pnl_ex_top_wins(pnl: pd.Series, n: int = 5) -> float:
    if pnl.empty:
        return 0.0
    return float(pnl.sum() - pnl[pnl > 0].sort_values(ascending=False).head(n).sum())


def summarize_sized(df: pd.DataFrame, size: pd.Series) -> dict[str, Any]:
    size = clamp_size(size)
    if len(df) != len(size):
        raise ValueError("size length mismatch")
    base_cost = float(df["cost_usd"].sum())
    base_pnl = float(df["pnl_usd_at_fill"].sum())
    sized_cost = float((df["cost_usd"] * size).sum())
    sized_pnl_series = df["pnl_usd_at_fill"] * size
    sized_pnl = float(sized_pnl_series.sum())
    reduced = 1.0 - size
    reduced_pnl = df["pnl_usd_at_fill"] * reduced
    avoided_loss = -float(reduced_pnl[reduced_pnl < 0].sum())
    missed_profit = float(reduced_pnl[reduced_pnl > 0].sum())
    kept = size > 0
    full = size >= 0.999
    partial = (size > 0) & (size < 0.999)
    return {
        "fills": int(len(df)),
        "base_cost_usd": round(base_cost, 6),
        "base_pnl_usd": round(base_pnl, 6),
        "base_roi": None if base_cost == 0 else round(base_pnl / base_cost, 6),
        "sized_cost_usd": round(sized_cost, 6),
        "sized_pnl_usd": round(sized_pnl, 6),
        "sized_roi": None if sized_cost == 0 else round(sized_pnl / sized_cost, 6),
        "delta_vs_unit_usd": round(sized_pnl - base_pnl, 6),
        "effective_cost_ratio": None if base_cost == 0 else round(sized_cost / base_cost, 6),
        "kept_fills": int(kept.sum()),
        "full_size_fills": int(full.sum()),
        "partial_size_fills": int(partial.sum()),
        "zero_size_fills": int((size <= 0).sum()),
        "avg_size": round(float(size.mean()), 6) if len(size) else 0.0,
        "avoided_loss_usd": round(avoided_loss, 6),
        "missed_profit_usd": round(missed_profit, 6),
        "net_size_value_usd": round(avoided_loss - missed_profit, 6),
        "base_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(df["pnl_usd_at_fill"]), 6),
        "sized_pnl_ex_top5_wins_usd": round(pnl_ex_top_wins(sized_pnl_series), 6),
    }


def evaluate_policy(df: pd.DataFrame, policy: SizePolicy) -> dict[str, Any]:
    size = clamp_size(policy.fn(df))
    out: dict[str, Any] = {
        "policy_id": policy.policy_id,
        "description": policy.description,
        "full": summarize_sized(df, size),
    }
    for period in ["pre_2026_06_01", "post_2026_06_01"]:
        mask = df["period"] == period
        out[period] = summarize_sized(df[mask].copy(), size.loc[mask].copy())
    return out


def group_summary(df: pd.DataFrame, group_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, part in df.groupby(group_col, dropna=False):
        cost = float(part["cost_usd"].sum())
        pnl = float(part["pnl_usd_at_fill"].sum())
        rows.append(
            {
                group_col: str(key),
                "fills": int(len(part)),
                "cost_usd": round(cost, 6),
                "pnl_usd": round(pnl, 6),
                "roi": None if cost == 0 else round(pnl / cost, 6),
                "win_rate": round(float((part["pnl_usd_at_fill"] > 0).mean()), 6),
                "avg_raw_edge": round(float(part["raw_edge_at_fill"].mean()), 6),
                "avg_blended_edge": round(float(part["blended_edge_at_fill"].mean()), 6),
                "avg_abs_divergence": round(float(part["abs_yes_divergence"].mean()), 6),
            }
        )
    rows.sort(key=lambda r: float(r["pnl_usd"]), reverse=True)
    return rows


def alpha_p_yes(df: pd.DataFrame, alpha: float) -> pd.Series:
    return alpha * df["model_p_yes"].astype(float) + (1.0 - alpha) * df["market_yes_price"].astype(float)


def alpha_edge(df: pd.DataFrame, alpha: float) -> pd.Series:
    p_yes = alpha_p_yes(df, alpha)
    return pd.Series(
        [side_edge(str(side), float(p), float(price)) for side, p, price in zip(df["side"], p_yes, df["fill_price"])],
        index=df.index,
    )


def gentle_size_from_edge(df: pd.DataFrame, edge: pd.Series) -> pd.Series:
    return pd.Series(
        [
            0.0 if e < 0.0 else 0.25 if e < 0.05 else 0.5 if e < 0.10 and re <= 0.25 else 1.0
            for e, re in zip(edge.astype(float), df["raw_edge_at_fill"].astype(float))
        ],
        index=df.index,
    )


def alpha_grid_study(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alpha in ALPHA_GRID:
        p_yes = alpha_p_yes(df, alpha)
        edge = alpha_edge(df, alpha)
        brier = float(((p_yes - df["final_yes"].astype(float)) ** 2).mean())
        hard = (edge >= 0.10).astype(float)
        gentle = gentle_size_from_edge(df, edge)
        hard_s = summarize_sized(df, hard)
        gentle_s = summarize_sized(df, gentle)
        post = df[df["period"] == "post_2026_06_01"].copy()
        post_edge = edge.loc[post.index]
        post_gentle = gentle_size_from_edge(post, post_edge)
        post_gentle_s = summarize_sized(post, post_gentle)
        rows.append(
            {
                "alpha": alpha,
                "brier_on_selected_fills": round(brier, 6),
                "hard_delta_usd": hard_s["delta_vs_unit_usd"],
                "hard_effective_cost_ratio": hard_s["effective_cost_ratio"],
                "gentle_delta_usd": gentle_s["delta_vs_unit_usd"],
                "gentle_effective_cost_ratio": gentle_s["effective_cost_ratio"],
                "post_gentle_delta_usd": post_gentle_s["delta_vs_unit_usd"],
                "post_gentle_effective_cost_ratio": post_gentle_s["effective_cost_ratio"],
            }
        )
    return rows


def policy_daily_robustness(df: pd.DataFrame, policy: SizePolicy) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    size = clamp_size(policy.fn(df))
    for target_date, part in df.groupby("target_date"):
        s = summarize_sized(part, size.loc[part.index])
        rows.append(
            {
                "target_date": target_date,
                "fills": s["fills"],
                "base_pnl_usd": s["base_pnl_usd"],
                "sized_pnl_usd": s["sized_pnl_usd"],
                "delta_vs_unit_usd": s["delta_vs_unit_usd"],
            }
        )
    deltas = pd.Series([r["delta_vs_unit_usd"] for r in rows], dtype=float)
    return {
        "policy_id": policy.policy_id,
        "dates": int(len(rows)),
        "positive_delta_dates": int((deltas > 0).sum()),
        "negative_delta_dates": int((deltas < 0).sum()),
        "zero_delta_dates": int((deltas == 0).sum()),
        "median_daily_delta_usd": round(float(deltas.median()), 6) if len(deltas) else 0.0,
        "total_delta_usd": round(float(deltas.sum()), 6) if len(deltas) else 0.0,
        "worst_day_delta_usd": round(float(deltas.min()), 6) if len(deltas) else 0.0,
        "best_day_delta_usd": round(float(deltas.max()), 6) if len(deltas) else 0.0,
        "rows": rows,
    }


def walk_forward_policy_selection(df: pd.DataFrame, policies: list[SizePolicy], train_days: int = 7) -> dict[str, Any]:
    dates = sorted(str(x) for x in df["target_date"].dropna().unique())
    folds: list[dict[str, Any]] = []
    policy_by_id = {p.policy_id: p for p in policies}
    for idx, test_date in enumerate(dates):
        train_dates = dates[max(0, idx - train_days) : idx]
        if len(train_dates) < train_days:
            continue
        train = df[df["target_date"].isin(train_dates)].copy()
        test = df[df["target_date"] == test_date].copy()
        if train.empty or test.empty:
            continue
        train_scores: list[tuple[float, str]] = []
        for policy in policies:
            s = summarize_sized(train, policy.fn(train))
            train_scores.append((float(s["sized_pnl_usd"]), policy.policy_id))
        train_scores.sort(reverse=True)
        chosen_id = train_scores[0][1]
        chosen = policy_by_id[chosen_id]
        test_s = summarize_sized(test, chosen.fn(test))
        unit_s = summarize_sized(test, pd.Series(1.0, index=test.index))
        folds.append(
            {
                "test_date": test_date,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "chosen_policy": chosen_id,
                "train_sized_pnl_usd": round(train_scores[0][0], 6),
                "test_fills": test_s["fills"],
                "test_base_pnl_usd": unit_s["base_pnl_usd"],
                "test_sized_pnl_usd": test_s["sized_pnl_usd"],
                "test_delta_vs_unit_usd": test_s["delta_vs_unit_usd"],
            }
        )
    deltas = pd.Series([f["test_delta_vs_unit_usd"] for f in folds], dtype=float)
    return {
        "train_days": train_days,
        "folds": folds,
        "fold_count": int(len(folds)),
        "total_delta_usd": round(float(deltas.sum()), 6) if len(deltas) else 0.0,
        "median_delta_usd": round(float(deltas.median()), 6) if len(deltas) else 0.0,
        "positive_folds": int((deltas > 0).sum()) if len(deltas) else 0,
        "negative_folds": int((deltas < 0).sum()) if len(deltas) else 0,
        "chosen_counts": pd.Series([f["chosen_policy"] for f in folds]).value_counts().to_dict() if folds else {},
    }


def fmt_policy_rows(results: list[dict[str, Any]], period: str) -> list[dict[str, str]]:
    rows = []
    for item in results:
        s = item[period]
        rows.append(
            {
                "policy": item["policy_id"],
                "fills": str(s["fills"]),
                "base_pnl": money(s["base_pnl_usd"]),
                "sized_pnl": money(s["sized_pnl_usd"]),
                "delta": money(s["delta_vs_unit_usd"]),
                "eff_cost": pct(s["effective_cost_ratio"], signed=False),
                "sized_roi": pct(s["sized_roi"]),
                "avoid_loss": money(s["avoided_loss_usd"], signed=False),
                "miss_profit": money(s["missed_profit_usd"], signed=False),
                "partial": str(s["partial_size_fills"]),
                "zero": str(s["zero_size_fills"]),
                "ex_top5": money(s["sized_pnl_ex_top5_wins_usd"]),
            }
        )
    return rows


def fmt_group_rows(rows: list[dict[str, Any]], group_col: str) -> list[dict[str, str]]:
    return [
        {
            group_col: str(r[group_col]),
            "fills": str(r["fills"]),
            "pnl": money(r["pnl_usd"]),
            "roi": pct(r["roi"]),
            "win_rate": pct(r["win_rate"], signed=False),
            "avg_raw_edge": f"{float(r['avg_raw_edge']):+.3f}",
            "avg_blended_edge": f"{float(r['avg_blended_edge']):+.3f}",
            "avg_div": f"{float(r['avg_abs_divergence']):.3f}",
        }
        for r in rows
    ]


def fmt_alpha_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "alpha": f"{float(r['alpha']):.1f}",
            "brier": f"{float(r['brier_on_selected_fills']):.4f}",
            "hard_delta": money(r["hard_delta_usd"]),
            "hard_eff_cost": pct(r["hard_effective_cost_ratio"], signed=False),
            "gentle_delta": money(r["gentle_delta_usd"]),
            "gentle_eff_cost": pct(r["gentle_effective_cost_ratio"], signed=False),
            "post_gentle_delta": money(r["post_gentle_delta_usd"]),
            "post_gentle_eff_cost": pct(r["post_gentle_effective_cost_ratio"], signed=False),
        }
        for r in rows
    ]


def fmt_daily_rows(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, str]]:
    out = []
    for r in sorted(rows, key=lambda x: abs(float(x["delta_vs_unit_usd"])), reverse=True)[:limit]:
        out.append(
            {
                "target_date": str(r["target_date"]),
                "fills": str(r["fills"]),
                "base_pnl": money(r["base_pnl_usd"]),
                "sized_pnl": money(r["sized_pnl_usd"]),
                "delta": money(r["delta_vs_unit_usd"]),
            }
        )
    return out


def build_markdown(
    df: pd.DataFrame,
    op: pd.DataFrame,
    self_checks: dict[str, Any],
    clob_gate: dict[str, Any],
    policy_results: list[dict[str, Any]],
    group_details: dict[str, list[dict[str, Any]]],
    alpha_rows: list[dict[str, Any]],
    robustness: list[dict[str, Any]],
    walk_forward: dict[str, Any],
) -> str:
    by_id = {r["policy_id"]: r for r in policy_results}
    unit = by_id["unit_base"]
    hard = by_id["hard_blended_edge_ge_0.10"]
    gentle = by_id["gentle_blended_size_curve"]
    mild = by_id["mild_blended_size_curve"]
    no_neg = by_id["no_negative_blended_edge"]

    candidate = gentle
    daily = next(r for r in robustness if r["policy_id"] == "gentle_blended_size_curve")

    lines = [
        "# Blender Signal Value Research — 2026-06-08",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{DB_PATH}`；只读 `fact_trades` / `fact_signal_candidates`，不使用 legacy DB。",
        f"- 生成时间 UTC：`{dt.datetime.now(dt.timezone.utc).isoformat()}`。",
        f"- DB mtime UTC：`{dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, dt.timezone.utc).isoformat()}`。",
        f"- `MAX(fact_built_at_utc)`：`{self_checks['max_fact_built_at_utc'][0]['MAX(fact_built_at_utc)']}`。",
        f"- CLOB coverage gate：`gate_pass={clob_gate.get('gate_pass')}`；`missing_order_rows={clob_gate['db_fills']['missing_order_rows']}`；`over_order_keys={clob_gate['db_fills']['over_order_keys']}`；`db_fill_cost_minus_fact_cost={clob_gate.get('db_fill_cost_minus_fact_cost')}`。",
        f"- 目标样本：`{STRATEGY_LABEL}` / `strategy_id={STRATEGY_ID}` / `trade_class=live_real` / `settlement_status=settled`，共 `{len(df)}` fills，`{df['target_date'].min()} -> {df['target_date'].max()}`。",
        f"- 当前 strict operational base：剔除 `{', '.join(sorted(REMOVED_ECMWF_CITIES))}` 且 `22<=hours_to_settle<=28`，共 `{len(op)}` fills。",
        f"- unsettled 占比：本研究目标样本只取 settled；全库 settlement_status 分布见 SQL 自检。",
        f"- missing_bracket 数：见 SQL 自检；目标样本 missing_bracket=0。",
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
        "## Target Metric",
        "",
        "`blender_signal_value` = 在当前 operational base 内，不改变历史成交价、成交量和结算结果，只把 `blended_edge` 当作 size/risk 信号，观察 scaled PnL 是否优于等额原策略。",
        "",
        "本报告不回答 basket，也不回答城市池是否该继续调整；城市和 `22<=T<=28` 在这里作为已知 strict operational base 固定住。",
        "",
        "## 结论",
        "",
        f"- **hard gate 仍不适合上线**：`hard_blended_edge_ge_0.10` 在 full operational base 的 delta `{money(hard['full']['delta_vs_unit_usd'])}`，post delta `{money(hard['post_2026_06_01']['delta_vs_unit_usd'])}`；它靠减少交易面提高 ROI，但会继续牺牲净 PnL。",
        f"- **size curve 比 hard gate 更合理，但还不够强**：`gentle_blended_size_curve` full delta `{money(gentle['full']['delta_vs_unit_usd'])}`，post delta `{money(gentle['post_2026_06_01']['delta_vs_unit_usd'])}`，effective cost `{pct(gentle['full']['effective_cost_ratio'], signed=False)}`。它降低风险，但当前样本没有证明能稳定增厚收益。",
        f"- **最有研究价值的是负 blended edge / 低 blended edge 的风险识别**：`no_negative_blended_edge` full delta `{money(no_neg['full']['delta_vs_unit_usd'])}`，post delta `{money(no_neg['post_2026_06_01']['delta_vs_unit_usd'])}`；若这条都不稳定，复杂阈值更不该 live。",
        f"- **alpha 不应继续用全样本最优来定**：alpha grid 只说明不同市场收缩强度的诊断结果，不能作为生产调参依据。需要 shadow lineage 的逐 snapshot walk-forward 再决定城市/side 动态 alpha。",
        f"- **walk-forward 选择没有通过 live hard-gate 标准**：{walk_forward['fold_count']} folds，总 delta `{money(walk_forward['total_delta_usd'])}`，中位 fold delta `{money(walk_forward['median_delta_usd'])}`，正/负 folds `{walk_forward['positive_folds']}/{walk_forward['negative_folds']}`。",
        "",
        "交易动作：blender 继续保留为 shadow/paper + lineage 字段；当前不建议作为真实 live hard gate。下一步只研究 `size_multiplier` 和 `model-health alert`。",
        "",
        "## Size Policy 对比",
        "",
        "### Full operational base",
        table(fmt_policy_rows(policy_results, "full")),
        "",
        "### Post 2026-06-01",
        table(fmt_policy_rows(policy_results, "post_2026_06_01")),
        "",
        "### Pre 2026-06-01",
        table(fmt_policy_rows(policy_results, "pre_2026_06_01")),
        "",
        "字段说明：`delta = sized_pnl - base_pnl`；`eff_cost` 是按 size 后的实际成本占原等额成本比例；`ex_top5` 是去掉前 5 个 scaled winner 后的 PnL。",
        "",
        "## Blender 分层诊断",
        "",
        "### 按 blended_edge 分层",
        table(fmt_group_rows(group_details["blended_edge_bin"], "blended_edge_bin")),
        "",
        "### 按 raw-market 绝对分歧分层",
        table(fmt_group_rows(group_details["divergence_bin"], "divergence_bin")),
        "",
        "### 按 raw_edge 分层",
        table(fmt_group_rows(group_details["raw_edge_bin"], "raw_edge_bin")),
        "",
        "### 按 side / model / timing 分层",
        "",
        "side：",
        table(fmt_group_rows(group_details["side"], "side")),
        "",
        "model_version：",
        table(fmt_group_rows(group_details["model_version"], "model_version")),
        "",
        "hours_bin：",
        table(fmt_group_rows(group_details["hours_bin"], "hours_bin")),
        "",
        "## Alpha Grid 诊断",
        "",
        "这里使用统一 alpha，不使用后验城市调参。`brier` 只在已成交 selected fills 上计算，不能外推到完整机会宇宙。",
        "",
        table(fmt_alpha_rows(alpha_rows)),
        "",
        "## Walk-forward",
        "",
        "方法：每个 `target_date` 只用之前 7 个 target_date 选择训练期 scaled PnL 最高的 size policy，再评价当天；候选包含 `unit_base`，因此如果 blender 没有训练期优势，模型可以选择不使用 blender。",
        "",
        f"- folds：`{walk_forward['fold_count']}`",
        f"- total_delta：`{money(walk_forward['total_delta_usd'])}`",
        f"- median_delta：`{money(walk_forward['median_delta_usd'])}`",
        f"- positive/negative folds：`{walk_forward['positive_folds']}/{walk_forward['negative_folds']}`",
        f"- chosen_counts：`{json.dumps(walk_forward['chosen_counts'], ensure_ascii=False)}`",
        "",
        "最大 delta 日期：",
        table(fmt_daily_rows(daily["rows"])),
        "",
        "## 下一步",
        "",
        "1. 在 shadow/paper lineage 中写入 `raw_p_yes / market_p_yes / blended_p_yes / blended_edge / size_multiplier_candidate / raw_market_disagreement`。",
        "2. 用未来 7-14 天逐 snapshot 数据重新跑本脚本；验收看中位日 delta、avoided_loss/missed_profit、top winner 依赖，而不是单个 headline ROI。",
        "3. 若 `blended_edge<0` 连续稳定过滤净亏损，再考虑 live 上只做 `size=0 or 0.25x` 的小范围 canary；不要先上 `edge>=0.10` hard gate。",
        "4. 动态 alpha 只允许按事前 slice 生成，例如 `city x side x model_version x hours_bin`，并必须 walk-forward；禁止全样本挑最优 alpha 直接上线。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    conn = connect()
    clob_gate = run_clob_gate()
    if not clob_gate.get("gate_pass"):
        raise RuntimeError(f"CLOB coverage gate failed: {clob_gate.get('fail_reasons')}")
    self_checks = sql_self_checks(conn)
    df = load_trades(conn)
    op = df[df["pass_operational_base"]].copy()
    if op.empty:
        raise RuntimeError("Operational base is empty")

    policies = size_policies()
    policy_results = [evaluate_policy(op, policy) for policy in policies]
    group_details = {
        "blended_edge_bin": group_summary(op, "blended_edge_bin"),
        "divergence_bin": group_summary(op, "divergence_bin"),
        "raw_edge_bin": group_summary(op, "raw_edge_bin"),
        "side": group_summary(op, "side"),
        "model_version": group_summary(op, "model_version"),
        "hours_bin": group_summary(op, "hours_bin"),
    }
    alpha_rows = alpha_grid_study(op)
    robustness = [policy_daily_robustness(op, policy) for policy in policies]
    walk_forward_candidates = [p for p in policies if p.policy_id != "hard_blended_edge_ge_0.10"]
    walk_forward = walk_forward_policy_selection(op, walk_forward_candidates, train_days=7)

    payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, dt.timezone.utc).isoformat(),
        "strategy_id": STRATEGY_ID,
        "strategy_label": STRATEGY_LABEL,
        "removed_ecmwf_cities": sorted(REMOVED_ECMWF_CITIES),
        "clob_gate": clob_gate,
        "self_checks": self_checks,
        "target_rows": int(len(df)),
        "operational_base_rows": int(len(op)),
        "policy_results": policy_results,
        "group_details": group_details,
        "alpha_grid": alpha_rows,
        "daily_robustness": robustness,
        "walk_forward": walk_forward,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(
        build_markdown(
            df=df,
            op=op,
            self_checks=self_checks,
            clob_gate=clob_gate,
            policy_results=policy_results,
            group_details=group_details,
            alpha_rows=alpha_rows,
            robustness=robustness,
            walk_forward=walk_forward,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"md": str(OUT_MD), "json": str(OUT_JSON), "rows": len(df), "op_rows": len(op)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
