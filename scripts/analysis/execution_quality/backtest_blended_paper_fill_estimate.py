"""
backtest_blended_paper_fill_estimate.py

Backtest the initial blended paper profiles and estimate fills/PnL using
historical live fill rates.

This is intentionally an analysis script, not a trading entrypoint.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.blender_shadow.backtest_weather_edge_engine_blended_entry_bands import (
    SideBand,
    _data_self_check,
    _side_edge,
)
from scripts.analysis.city_selection.eval_city_day_basket import DB_DEFAULT, OUT_DEFAULT, _leg_pnl
from weather_dashboard.blend import blend_probability, load_default_config


@dataclass(frozen=True)
class Profile:
    profile_id: str
    label: str
    buy_yes: SideBand
    buy_no: SideBand
    probability_source: str
    strategy_instance: str
    notional: float = 1.0


PROFILES = [
    Profile(
        profile_id="raw_current_25_75",
        label="current raw 25_75 live gate",
        buy_yes=SideBand(0.25, 0.75, 0.10),
        buy_no=SideBand(0.25, 0.75, 0.10),
        probability_source="raw",
        strategy_instance="mid_price_core_v1_25_75",
    ),
    Profile(
        profile_id="blended_25_75_e05",
        label="selected blended paper 25_75",
        buy_yes=SideBand(0.25, 0.75, 0.05),
        buy_no=SideBand(0.25, 0.75, 0.05),
        probability_source="blended",
        strategy_instance="weather_edge_engine_blended_25_75_e05_paper",
    ),
    Profile(
        profile_id="raw_current_side_band",
        label="current raw side_band live gate",
        buy_yes=SideBand(0.20, 0.45, 0.20),
        buy_no=SideBand(0.35, 0.65, 0.10),
        probability_source="raw",
        strategy_instance="mid_price_core_v1_side_band",
    ),
    Profile(
        profile_id="blended_side_band_e05",
        label="selected blended paper side_band",
        buy_yes=SideBand(0.20, 0.45, 0.05),
        buy_no=SideBand(0.35, 0.65, 0.05),
        probability_source="blended",
        strategy_instance="weather_edge_engine_blended_side_band_e05_paper",
    ),
]


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes,
               live_filled, paper_ordered, eligible
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
        """,
        conn,
    )


def _coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    queries = {
        "candidate_all_range": """
            SELECT MIN(event_date) AS min_date,
                   MAX(event_date) AS max_date,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT event_date) AS days
            FROM fact_signal_candidates
        """,
        "candidate_settled_complete_range": """
            SELECT MIN(event_date) AS min_date,
                   MAX(event_date) AS max_date,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT event_date) AS days
            FROM fact_signal_candidates
            WHERE settlement_status = 'settled'
              AND decision_window_missing = 0
              AND model_p_yes IS NOT NULL
              AND market_yes_price IS NOT NULL
              AND decision_entry_price IS NOT NULL
              AND final_yes IS NOT NULL
        """,
        "live_real_settled_range": """
            SELECT MIN(target_date) AS min_date,
                   MAX(target_date) AS max_date,
                   COUNT(*) AS fills,
                   COUNT(DISTINCT target_date) AS days,
                   SUM(cost_usd) AS cost_usd,
                   SUM(pnl_usd_at_fill) AS pnl_usd
            FROM fact_trades
            WHERE trade_class='live_real'
              AND settlement_status='settled'
        """,
    }
    return {name: _fetchall(conn, sql) for name, sql in queries.items()}


def _band_for_side(profile: Profile, side: str) -> SideBand:
    return profile.buy_yes if side == "BUY_YES" else profile.buy_no


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _decide(df: pd.DataFrame, profile: Profile) -> pd.DataFrame:
    blend_cfg = load_default_config()
    rows: list[dict[str, Any]] = []
    for r in df.itertuples():
        side = str(r.side)
        entry = float(r.decision_entry_price)
        band = _band_for_side(profile, side)
        if entry < band.min_entry or entry >= band.max_entry:
            continue
        raw_p = float(r.model_p_yes)
        if profile.probability_source == "raw":
            p_used = raw_p
            alpha = 1.0
            beta = 0.0
            mode = "raw"
        else:
            blended = blend_probability(
                city=str(r.city),
                model_p_yes_raw=raw_p,
                market_implied_p_yes=float(r.market_yes_price),
                config=blend_cfg,
            )
            p_used = float(blended.p_yes_used)
            alpha = float(blended.blend_alpha)
            beta = float(blended.blend_beta)
            mode = str(blended.blend_mode)
        edge = _side_edge(side, p_used, entry)
        if edge < band.min_edge:
            continue
        pnl = _leg_pnl(side, entry, profile.notional, float(r.final_yes))
        rows.append(
            {
                "city": str(r.city),
                "event_date": str(r.event_date),
                "bracket": str(r.bracket),
                "side": side,
                "entry_price": entry,
                "notional": profile.notional,
                "pnl": pnl,
                "is_win": pnl > 0,
                "live_filled": _to_int(r.live_filled),
                "paper_ordered": _to_int(r.paper_ordered),
                "eligible": _to_int(r.eligible),
                "model_p_yes_raw": raw_p,
                "market_implied_p_yes": float(r.market_yes_price),
                "model_p_yes_used": p_used,
                "blend_alpha": alpha,
                "blend_beta": beta,
                "blend_mode": mode,
                "edge": edge,
            }
        )
    return pd.DataFrame(rows)


def _summarize_selected(selected: pd.DataFrame) -> dict[str, Any]:
    if selected.empty:
        return {
            "legs": 0,
            "days": 0,
            "min_date": "",
            "max_date": "",
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": 0.0,
            "win_rate": 0.0,
            "positive_day_rate": 0.0,
            "max_win_pnl_usd": 0.0,
            "top5_win_count": 0,
            "top5_win_cost_usd": 0.0,
            "top5_win_pnl_usd": 0.0,
            "top5_win_share_of_total_pnl": 0.0,
            "top5_removed_cost_usd": 0.0,
            "top5_removed_pnl_usd": 0.0,
            "top5_removed_roi": 0.0,
            "legs_per_day": 0.0,
            "live_filled_overlap": 0,
            "live_filled_overlap_rate": 0.0,
            "live_filled_overlap_pnl_usd": 0.0,
            "live_filled_overlap_roi": 0.0,
        }
    cost = float(selected["notional"].sum())
    pnl = float(selected["pnl"].sum())
    days = int(selected["event_date"].nunique())
    daily = selected.groupby("event_date", as_index=False).agg(notional=("notional", "sum"), pnl=("pnl", "sum"))
    sorted_pnl = selected.sort_values("pnl", ascending=False)
    top5_wins = sorted_pnl.head(5)
    top5_removed = sorted_pnl.iloc[5:] if len(sorted_pnl) > 5 else sorted_pnl.iloc[0:0]
    top5_win_cost = float(top5_wins["notional"].sum()) if not top5_wins.empty else 0.0
    top5_win_pnl = float(top5_wins["pnl"].sum()) if not top5_wins.empty else 0.0
    top5_cost = float(top5_removed["notional"].sum()) if not top5_removed.empty else 0.0
    top5_pnl = float(top5_removed["pnl"].sum()) if not top5_removed.empty else 0.0
    live_overlap = selected[selected["live_filled"] == 1]
    live_cost = float(live_overlap["notional"].sum())
    live_pnl = float(live_overlap["pnl"].sum())
    return {
        "legs": int(len(selected)),
        "days": days,
        "min_date": str(selected["event_date"].min()),
        "max_date": str(selected["event_date"].max()),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": pnl / cost if cost else 0.0,
        "win_rate": float(selected["is_win"].mean()),
        "positive_day_rate": float((daily["pnl"] > 0).mean()) if len(daily) else 0.0,
        "max_win_pnl_usd": round(float(sorted_pnl.iloc[0]["pnl"]), 6) if len(sorted_pnl) else 0.0,
        "top5_win_count": int(len(top5_wins)),
        "top5_win_cost_usd": round(top5_win_cost, 6),
        "top5_win_pnl_usd": round(top5_win_pnl, 6),
        "top5_win_share_of_total_pnl": top5_win_pnl / pnl if abs(pnl) > 1e-12 else 0.0,
        "top5_removed_cost_usd": round(top5_cost, 6),
        "top5_removed_pnl_usd": round(top5_pnl, 6),
        "top5_removed_roi": top5_pnl / top5_cost if top5_cost else 0.0,
        "legs_per_day": len(selected) / days if days else 0.0,
        "live_filled_overlap": int(len(live_overlap)),
        "live_filled_overlap_rate": len(live_overlap) / len(selected) if len(selected) else 0.0,
        "live_filled_overlap_pnl_usd": round(live_pnl, 6),
        "live_filled_overlap_roi": live_pnl / live_cost if live_cost else 0.0,
    }


def _top_winners(selected: pd.DataFrame, n: int = 5) -> list[dict[str, Any]]:
    if selected.empty:
        return []
    rows: list[dict[str, Any]] = []
    cols = [
        "city",
        "event_date",
        "bracket",
        "side",
        "entry_price",
        "notional",
        "pnl",
        "edge",
        "model_p_yes_used",
        "market_implied_p_yes",
    ]
    for row in selected.sort_values("pnl", ascending=False).head(n)[cols].to_dict("records"):
        rows.append({
            **row,
            "entry_price": round(float(row["entry_price"]), 6),
            "notional": round(float(row["notional"]), 6),
            "pnl": round(float(row["pnl"]), 6),
            "edge": round(float(row["edge"]), 6),
            "model_p_yes_used": round(float(row["model_p_yes_used"]), 6),
            "market_implied_p_yes": round(float(row["market_implied_p_yes"]), 6),
        })
    return rows


def _group_breakdown(selected: pd.DataFrame, group_col: str) -> list[dict[str, Any]]:
    if selected.empty:
        return []
    grouped = selected.groupby(group_col, as_index=False).agg(
        legs=("pnl", "size"),
        days=("event_date", "nunique"),
        cost_usd=("notional", "sum"),
        pnl_usd=("pnl", "sum"),
        win_rate=("is_win", "mean"),
    )
    grouped["roi"] = grouped.apply(
        lambda r: float(r["pnl_usd"]) / float(r["cost_usd"]) if float(r["cost_usd"]) else 0.0,
        axis=1,
    )
    grouped = grouped.sort_values(["pnl_usd", group_col], ascending=[False, True])
    out: list[dict[str, Any]] = []
    for row in grouped.to_dict("records"):
        out.append({
            str(group_col): row[group_col],
            "legs": int(row["legs"]),
            "days": int(row["days"]),
            "cost_usd": round(float(row["cost_usd"]), 6),
            "pnl_usd": round(float(row["pnl_usd"]), 6),
            "roi": float(row["roi"]),
            "win_rate": float(row["win_rate"]),
        })
    return out


def _bootstrap_daily_roi(selected: pd.DataFrame, *, seed: int = 7, n: int = 5000) -> dict[str, Any]:
    if selected.empty:
        return {"n_days": 0, "roi_p05": 0.0, "roi_p50": 0.0, "roi_p95": 0.0, "prob_roi_gt_0": 0.0}
    daily = selected.groupby("event_date", as_index=False).agg(notional=("notional", "sum"), pnl=("pnl", "sum"))
    if daily.empty:
        return {"n_days": 0, "roi_p05": 0.0, "roi_p50": 0.0, "roi_p95": 0.0, "prob_roi_gt_0": 0.0}
    values = list(zip(daily["notional"].tolist(), daily["pnl"].tolist()))
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(n):
        sample = [values[rng.randrange(len(values))] for _ in values]
        cost = sum(x[0] for x in sample)
        pnl = sum(x[1] for x in sample)
        rois.append(pnl / cost if cost else 0.0)
    rois.sort()

    def q(p: float) -> float:
        idx = min(len(rois) - 1, max(0, int(round((len(rois) - 1) * p))))
        return rois[idx]

    return {
        "n_days": len(values),
        "roi_p05": q(0.05),
        "roi_p50": q(0.50),
        "roi_p95": q(0.95),
        "prob_roi_gt_0": sum(1 for x in rois if x > 0) / len(rois),
    }


def _order_fill_rates(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = _fetchall(
        conn,
        """
        SELECT o.status,
               COUNT(*) AS orders,
               SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
        FROM orders o
        LEFT JOIN fills f USING(execution_id)
        WHERE o.venue='polymarket_clob'
        GROUP BY o.status
        """,
    )
    submitted_orders = sum(int(r["orders"] or 0) for r in rows if r["status"] == "submitted")
    submitted_fills = sum(int(r["with_fill"] or 0) for r in rows if r["status"] == "submitted")
    all_orders = sum(int(r["orders"] or 0) for r in rows)
    all_fills = sum(int(r["with_fill"] or 0) for r in rows)
    return {
        "by_status": rows,
        "submitted_fill_rate": submitted_fills / submitted_orders if submitted_orders else 0.0,
        "all_attempt_fill_rate": all_fills / all_orders if all_orders else 0.0,
        "submitted_orders": submitted_orders,
        "submitted_fills": submitted_fills,
        "all_orders": all_orders,
        "all_fills": all_fills,
    }


def _estimate(summary: dict[str, Any], fill_rate: float) -> dict[str, Any]:
    return {
        "fill_rate": fill_rate,
        "estimated_fills": summary["legs"] * fill_rate,
        "estimated_cost_usd": summary["cost_usd"] * fill_rate,
        "estimated_pnl_usd": summary["pnl_usd"] * fill_rate,
        "estimated_daily_fills": summary["legs_per_day"] * fill_rate,
        "estimated_daily_pnl_usd": (summary["pnl_usd"] / summary["days"] * fill_rate) if summary["days"] else 0.0,
        "roi_assumption": "same as opportunity replay; assumes fills are random with respect to edge/PnL",
    }


def _paired_delta(blended: pd.DataFrame, raw: pd.DataFrame) -> dict[str, Any]:
    if blended.empty and raw.empty:
        return {"shared_legs": 0, "blend_only": 0, "raw_only": 0, "net_pnl_delta_usd": 0.0}
    key_cols = ["city", "event_date", "bracket", "side"]
    b = blended.set_index(key_cols) if not blended.empty else pd.DataFrame(columns=["pnl"])
    r = raw.set_index(key_cols) if not raw.empty else pd.DataFrame(columns=["pnl"])
    b_keys = set(b.index.tolist())
    r_keys = set(r.index.tolist())
    shared = b_keys & r_keys
    b_only = b_keys - r_keys
    r_only = r_keys - b_keys
    shared_delta = sum(float(b.loc[k]["pnl"]) - float(r.loc[k]["pnl"]) for k in shared)
    b_only_pnl = sum(float(b.loc[k]["pnl"]) for k in b_only)
    r_only_pnl = sum(float(r.loc[k]["pnl"]) for k in r_only)
    return {
        "shared_legs": len(shared),
        "blend_only": len(b_only),
        "raw_only": len(r_only),
        "shared_pnl_delta_usd": round(shared_delta, 6),
        "blend_only_pnl_usd": round(b_only_pnl, 6),
        "raw_only_pnl_usd": round(r_only_pnl, 6),
        "net_pnl_delta_usd": round(shared_delta + b_only_pnl - r_only_pnl, 6),
    }


def _actual_live_by_instance(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _fetchall(
        conn,
        """
        SELECT COALESCE(NULLIF(strategy_name,''), NULLIF(strategy_id,''), execution_policy) AS strategy_instance,
               COUNT(*) AS fills,
               COUNT(DISTINCT target_date) AS days,
               SUM(cost_usd) AS cost_usd,
               SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               MIN(target_date) AS min_target_date,
               MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
        GROUP BY 1
        ORDER BY 1
        """,
    )
    for row in rows:
        cost = float(row.get("cost_usd") or 0.0)
        row["roi"] = (float(row.get("pnl_usd") or 0.0) / cost) if cost else 0.0
    return rows


def _mid_price_v1_actual_baselines(conn: sqlite3.Connection) -> dict[str, Any]:
    base_sql = """
        SELECT entry_price_window,
               COUNT(*) AS fills,
               COUNT(DISTINCT target_date) AS days,
               SUM(cost_usd) AS cost_usd,
               SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               MIN(target_date) AS min_target_date,
               MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND execution_policy='mid_price_core_v1'
        GROUP BY entry_price_window
        ORDER BY entry_price_window
    """
    by_window = _fetchall(conn, base_sql)
    for row in by_window:
        cost = float(row.get("cost_usd") or 0.0)
        row["roi"] = (float(row.get("pnl_usd") or 0.0) / cost) if cost else 0.0

    all_rows = _fetchall(
        conn,
        """
        SELECT COUNT(*) AS fills,
               COUNT(DISTINCT target_date) AS days,
               SUM(cost_usd) AS cost_usd,
               SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               MIN(target_date) AS min_target_date,
               MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND execution_policy='mid_price_core_v1'
        """,
    )
    all_mid_v1 = all_rows[0] if all_rows else {}
    all_cost = float(all_mid_v1.get("cost_usd") or 0.0)
    all_mid_v1["roi"] = (float(all_mid_v1.get("pnl_usd") or 0.0) / all_cost) if all_cost else 0.0

    side_rows = _fetchall(
        conn,
        """
        SELECT COUNT(*) AS fills,
               COUNT(DISTINCT target_date) AS days,
               SUM(cost_usd) AS cost_usd,
               SUM(pnl_usd_at_fill) AS pnl_usd,
               AVG(CAST(win_by_count AS REAL)) AS win_rate,
               MIN(target_date) AS min_target_date,
               MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window IN ('0.20-0.45', '0.35-0.65')
        """,
    )
    side_combined = side_rows[0] if side_rows else {}
    side_cost = float(side_combined.get("cost_usd") or 0.0)
    side_pnl = float(side_combined.get("pnl_usd") or 0.0)
    return {
        "all_mid_price_core_v1": all_mid_v1,
        "by_entry_window": by_window,
        "combined_side_band_windows": {
            "entry_price_window": "0.20-0.45 + 0.35-0.65",
            "fills": int(side_combined.get("fills") or 0),
            "days": int(side_combined.get("days") or 0),
            "cost_usd": round(side_cost, 6),
            "pnl_usd": round(side_pnl, 6),
            "roi": side_pnl / side_cost if side_cost else 0.0,
            "win_rate": float(side_combined.get("win_rate") or 0.0),
            "min_target_date": str(side_combined.get("min_target_date") or ""),
            "max_target_date": str(side_combined.get("max_target_date") or ""),
        },
    }


def main() -> int:
    bj_today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with sqlite3.connect(str(DB_DEFAULT)) as conn:
        data_self_check = _data_self_check(conn)
        fill_rates = _order_fill_rates(conn)
        df = _load_candidates(conn)
        report: dict[str, Any] = {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "db_path": str(DB_DEFAULT),
            "data_self_check": data_self_check,
            "coverage": _coverage(conn),
            "fill_rates": fill_rates,
            "profiles": {p.profile_id: asdict(p) for p in PROFILES},
            "slices": {},
            "comparisons": {},
            "current_live_actual": _actual_live_by_instance(conn),
            "mid_price_v1_actual": _mid_price_v1_actual_baselines(conn),
        }
        for slice_name in ["full", "holdout_from_2026_05_26", "recent_from_2026_06_01", "live_filled_only"]:
            block: dict[str, Any] = {}
            sliced = _slice(df, slice_name)
            selected_by_profile: dict[str, pd.DataFrame] = {}
            for profile in PROFILES:
                selected = _decide(sliced, profile)
                selected_by_profile[profile.profile_id] = selected
                summary = _summarize_selected(selected)
                block[profile.profile_id] = {
                    "summary": summary,
                    "bootstrap_daily_roi": _bootstrap_daily_roi(selected),
                    "top_winners": _top_winners(selected),
                    "city_breakdown": _group_breakdown(selected, "city"),
                    "date_breakdown": _group_breakdown(selected, "event_date"),
                    "estimate_submitted_fill_rate": _estimate(summary, fill_rates["submitted_fill_rate"]),
                    "estimate_all_attempt_fill_rate": _estimate(summary, fill_rates["all_attempt_fill_rate"]),
                }
            report["slices"][slice_name] = block
            report["comparisons"][slice_name] = {
                "blended_25_75_e05_vs_raw_current_25_75": {
                    "paired_delta": _paired_delta(
                        selected_by_profile["blended_25_75_e05"],
                        selected_by_profile["raw_current_25_75"],
                    ),
                    "blend_bootstrap_daily_roi": block["blended_25_75_e05"]["bootstrap_daily_roi"],
                },
                "blended_side_band_e05_vs_raw_current_side_band": {
                    "paired_delta": _paired_delta(
                        selected_by_profile["blended_side_band_e05"],
                        selected_by_profile["raw_current_side_band"],
                    ),
                    "blend_bootstrap_daily_roi": block["blended_side_band_e05"]["bootstrap_daily_roi"],
                },
            }

    out_dir = OUT_DEFAULT / bj_today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{bj_today.isoformat()}-blended-paper-fill-estimate.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON written to {json_path}")
    for profile_id in ["blended_25_75_e05", "blended_side_band_e05"]:
        recent = report["slices"]["recent_from_2026_06_01"][profile_id]
        s = recent["summary"]
        est = recent["estimate_submitted_fill_rate"]
        print(
            f"recent {profile_id}: legs={s['legs']} roi={s['roi']*100:+.2f}% "
            f"pnl=${s['pnl_usd']:+.2f}; est_submitted_fills={est['estimated_fills']:.1f} "
            f"est_pnl=${est['estimated_pnl_usd']:+.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
