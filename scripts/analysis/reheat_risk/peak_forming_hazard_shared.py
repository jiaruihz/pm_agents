"""Shared, version-neutral helpers for peak-forming hazard experiments."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def _query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [description[0] for description in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def data_self_check(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute(
                "SELECT MAX(fact_built_at_utc) FROM fact_trades"
            ).fetchone()[0],
            "fact_trades_by_class": _query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades "
                "GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": _query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, "
                "COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status "
                "ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": _query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, "
                "SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled "
                "FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": _query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def metric_row(name: str, frame: pd.DataFrame, p_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"model": name, "rows": 0, "active_dates": 0}
    y = frame["label_current_yes_survives"].to_numpy(dtype=int)
    probability = np.clip(frame[p_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    return {
        "model": name,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "actual_survive_rate": float(y.mean()),
        "mean_pred_survive": float(probability.mean()),
        "auc": (
            float(roc_auc_score(y, probability))
            if len(np.unique(y)) > 1
            else None
        ),
        "brier": float(brier_score_loss(y, probability)),
        "logloss": (
            float(log_loss(y, probability)) if len(np.unique(y)) > 1 else None
        ),
        "mean_edge_vs_ask": float(
            (frame[p_col] - frame["current_yes_ask"]).mean()
        ),
    }


def date_cluster_bootstrap_roi(
    frame: pd.DataFrame,
    cost_col: str,
    pnl_col: str,
    *,
    seed: int,
    reps: int = 3000,
) -> list[float | None]:
    by_date = frame.groupby("target_date")[[cost_col, pnl_col]].sum()
    if by_date.shape[0] < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    values = by_date.to_numpy(dtype=float)
    results = []
    for _ in range(reps):
        indexes = rng.integers(0, len(values), size=len(values))
        sample = values[indexes]
        cost = float(sample[:, 0].sum())
        results.append(float(sample[:, 1].sum() / cost) if cost else np.nan)
    finite = np.asarray(results)
    finite = finite[np.isfinite(finite)]
    if len(finite) == 0:
        return [None, None]
    return [float(np.quantile(finite, 0.025)), float(np.quantile(finite, 0.975))]


def summarize_trade(
    frame: pd.DataFrame, p_col: str, name: str, *, seed: int
) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
            "orders": 0,
            "active_dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "win_rate": None,
            "avg_ask": None,
            "avg_p": None,
            "avg_edge": None,
            "bootstrap_roi_ci95": [None, None],
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl_series = frame["label_current_yes_survives"] - frame["current_yes_ask"]
    pnl = float(pnl_series.sum())
    confidence_interval = date_cluster_bootstrap_roi(
        frame.assign(_pnl=pnl_series),
        cost_col="current_yes_ask",
        pnl_col="_pnl",
        seed=seed,
    )
    return {
        "rule": name,
        "orders": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float(frame["label_current_yes_survives"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_p": float(frame[p_col].mean()),
        "avg_edge": float((frame[p_col] - frame["current_yes_ask"]).mean()),
        "bootstrap_roi_ci95": confidence_interval,
    }


def approx_metar_veto(frame: pd.DataFrame) -> pd.Series:
    minutes = pd.to_numeric(frame["minutes_since_running_max"], errors="coerce")
    warming = pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce")
    min_gap = pd.to_numeric(
        frame["min_forecast_gap_to_running_native"], errors="coerce"
    )
    return (
        (minutes.notna() & minutes.lt(10))
        | (warming.notna() & warming.ge(1.5))
        | (min_gap.notna() & min_gap.lt(-0.1))
    )
