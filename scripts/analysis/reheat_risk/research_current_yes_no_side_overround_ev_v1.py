#!/usr/bin/env python3
"""Evaluate buying current-bracket NO on the current-YES replay universe.

This is an opportunity-grain counterfactual study.  It uses the v8
current-YES replay rows as the denominator and reads the real current-bracket
NO ask from each row's historical orderbook snapshot.  It deliberately does
not infer NO as ``1 - yes_ask``.
"""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
V8_FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
REHEAT_FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_side_overround_ev_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_ROWS = OUT_DIR / "enriched_rows.csv"

SEED = 20260622
BOOTSTRAP_REPS = 5000


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> float | None:
    if x is None or not math.isfinite(float(x)):
        return None
    return float(x)


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "db_mtime_local": datetime.fromtimestamp(DB.stat().st_mtime).isoformat(),
            "db_size_bytes": DB.stat().st_size,
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
            "fact_signal_candidate_columns": [
                row["name"] for row in query_rows(conn, "PRAGMA table_info(fact_signal_candidates)")
            ],
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
        "db_fills": data.get("db_fills", {}),
        "db_vs_primary_cache": data.get("db_vs_primary_cache", {}),
        "fact_trades_live_real": data.get("fact_trades_live_real", {}),
    }


def as_float(s: Any) -> float:
    if s is None or s == "":
        return math.nan
    return float(s)


def normalize_ts(s: str) -> str:
    return str(s).replace("Z", "+00:00")


def load_v8_rows() -> pd.DataFrame:
    df = pd.read_csv(V8_FEATURE_ROWS)
    df["contains_running_bool"] = df["contains_running"].astype(str).str.lower().isin(["1", "true", "yes"])
    numeric = [
        "yes_current_ask",
        "yes_current_size",
        "label_yes_wins",
        "decline_c",
        "decision_hour_local",
        "d_tmpf_3h",
        "d_relh_3h",
        "tmpf_now",
        "relh_now",
        "running_max_f",
        "final_max_f",
    ]
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["snapshot_ts_norm"] = df["snapshot_ts_utc"].map(normalize_ts)
    return df


def depth_cost(asks: list[dict[str, Any]], max_price: float) -> tuple[float, float]:
    shares = 0.0
    cost = 0.0
    for level in asks:
        price = as_float(level.get("price"))
        size = as_float(level.get("size"))
        if not math.isfinite(price) or not math.isfinite(size) or price > max_price + 1e-9:
            continue
        shares += size
        cost += price * size
    return shares, cost


def fill_cap(asks: list[dict[str, Any]], cap_usd: float, max_price: float | None = None) -> tuple[float, float]:
    shares = 0.0
    cost = 0.0
    remaining = cap_usd
    for level in asks:
        price = as_float(level.get("price"))
        size = as_float(level.get("size"))
        if not math.isfinite(price) or not math.isfinite(size) or price <= 0:
            continue
        if max_price is not None and price > max_price + 1e-9:
            continue
        buy_shares = min(size, remaining / price)
        if buy_shares <= 0:
            continue
        shares += buy_shares
        spent = buy_shares * price
        cost += spent
        remaining -= spent
        if remaining <= 1e-9:
            break
    return shares, cost


def load_current_quotes(rows: pd.DataFrame) -> pd.DataFrame:
    needed = set(rows["condition_id"].astype(str))
    files = sorted(rows["orderbook_file"].dropna().astype(str).unique())
    quotes: dict[tuple[str, str, str], dict[str, Any]] = {}
    raw_asks: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for rel in files:
        path = Path(rel)
        if not path.is_absolute():
            path = ROOT / path
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                cid = str(obj.get("condition_id"))
                if cid not in needed or obj.get("status") != "ok":
                    continue
                outcome = str(obj.get("outcome")).lower()
                if outcome not in {"yes", "no"}:
                    continue
                summary = obj.get("summary") or {}
                asks = sorted((obj.get("raw") or {}).get("asks") or [], key=lambda x: as_float(x.get("price")))
                best_ask = as_float(summary.get("best_ask"))
                best_bid = as_float(summary.get("best_bid"))
                ask_size = as_float(summary.get("ask_size"))
                bid_size = as_float(summary.get("bid_size"))
                spread = as_float(summary.get("spread"))
                depth_5_shares, depth_5_cost = depth_cost(asks, best_ask + 0.05 if math.isfinite(best_ask) else math.nan)
                depth_10_shares, depth_10_cost = depth_cost(asks, best_ask + 0.10 if math.isfinite(best_ask) else math.nan)
                quotes[(rel, cid, outcome)] = {
                    "orderbook_file": rel,
                    "condition_id": cid,
                    f"{outcome}_ask": best_ask,
                    f"{outcome}_bid": best_bid,
                    f"{outcome}_ask_size": ask_size,
                    f"{outcome}_bid_size": bid_size,
                    f"{outcome}_spread": spread,
                    f"{outcome}_depth_ask_5c": as_float(summary.get("depth_ask_5c")),
                    f"{outcome}_depth_ask_10c": as_float(summary.get("depth_ask_10c")),
                    f"{outcome}_depth_ask_5c_cost": depth_5_cost,
                    f"{outcome}_depth_ask_10c_cost": depth_10_cost,
                    f"{outcome}_depth_ask_5c_shares_raw": depth_5_shares,
                    f"{outcome}_depth_ask_10c_shares_raw": depth_10_shares,
                }
                raw_asks[(rel, cid, outcome)] = asks

    enriched = rows.copy()
    for outcome in ("yes", "no"):
        qdf = pd.DataFrame([data for (_rel, _cid, out), data in quotes.items() if out == outcome])
        enriched = enriched.merge(qdf, on=["orderbook_file", "condition_id"], how="left")

    for cap in (1.5, 5.0, 10.0):
        for band, suffix in ((0.0, "top"), (0.05, "within_5c")):
            costs: list[float] = []
            shares: list[float] = []
            for row in enriched.itertuples(index=False):
                asks = raw_asks.get((str(row.orderbook_file), str(row.condition_id), "no"), [])
                best = getattr(row, "no_ask")
                max_price = best + band if math.isfinite(best) else None
                sh, co = fill_cap(asks, cap, max_price=max_price)
                shares.append(sh)
                costs.append(co)
            cap_label = str(cap).replace(".", "p")
            enriched[f"no_fill_{suffix}_{cap_label}_shares"] = shares
            enriched[f"no_fill_{suffix}_{cap_label}_cost"] = costs
    return enriched


def load_reheat_join(v8: pd.DataFrame) -> pd.DataFrame:
    if not REHEAT_FEATURE_ROWS.exists():
        return v8
    keys = set(zip(v8["condition_id"], v8["snapshot_ts_norm"], v8["city"], v8["target_date"]))
    keep_cols = [
        "condition_id",
        "decision_snapshot_ts_utc",
        "city",
        "target_date",
        "outcome",
        "minutes_since_running_max",
        "forecast_max_f",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "running_max_f",
        "current_yes_bid",
        "current_yes_spread",
    ]
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(REHEAT_FEATURE_ROWS, usecols=lambda c: c in keep_cols, chunksize=50000):
        chunk = chunk[chunk["outcome"].astype(str).str.lower().eq("yes")].copy()
        chunk["snapshot_ts_norm"] = chunk["decision_snapshot_ts_utc"].map(normalize_ts)
        mask = [
            (cid, ts, city, date) in keys
            for cid, ts, city, date in zip(
                chunk["condition_id"], chunk["snapshot_ts_norm"], chunk["city"], chunk["target_date"]
            )
        ]
        if any(mask):
            chunks.append(chunk.loc[mask].copy())
    if not chunks:
        return v8
    r = pd.concat(chunks, ignore_index=True)
    r = r.drop_duplicates(["condition_id", "snapshot_ts_norm", "city", "target_date"], keep="first")
    r = r.rename(
        columns={
            "minutes_since_running_max": "factory_minutes_since_running_max",
            "forecast_max_f": "factory_forecast_max_f",
            "forecast_max_native": "factory_forecast_max_native",
            "forecast_peak_hour_local": "factory_forecast_peak_hour_local",
            "forecast_peak_delta_hours_local": "factory_forecast_peak_delta_hours_local",
            "current_yes_bid": "factory_current_yes_bid",
            "current_yes_spread": "factory_current_yes_spread",
            "running_max_f": "factory_running_max_f",
        }
    )
    merge_cols = [
        "condition_id",
        "snapshot_ts_norm",
        "city",
        "target_date",
        "factory_minutes_since_running_max",
        "factory_forecast_max_f",
        "factory_forecast_max_native",
        "factory_forecast_peak_hour_local",
        "factory_forecast_peak_delta_hours_local",
        "factory_current_yes_bid",
        "factory_current_yes_spread",
        "factory_running_max_f",
    ]
    return v8.merge(r[merge_cols], on=["condition_id", "snapshot_ts_norm", "city", "target_date"], how="left")


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d = d[d["contains_running_bool"] & d["label_yes_wins"].notna()].copy()
    d = d[d["yes_current_ask"].notna() & d["no_ask"].notna()].copy()
    d["label_yes_wins"] = d["label_yes_wins"].astype(int)
    d["label_no_wins"] = 1 - d["label_yes_wins"]
    d["yes_profit_per_share"] = d["label_yes_wins"] - d["yes_current_ask"]
    d["no_profit_per_share"] = d["label_no_wins"] - d["no_ask"]
    d["yes_roi_row"] = d["yes_profit_per_share"] / d["yes_current_ask"]
    d["no_roi_row"] = d["no_profit_per_share"] / d["no_ask"]
    d["no_edge_pp"] = d["label_no_wins"] - d["no_ask"]
    d["yes_edge_pp"] = d["label_yes_wins"] - d["yes_current_ask"]
    d["overround_ask"] = d["yes_current_ask"] + d["no_ask"] - 1.0
    d["yes_quote_mismatch"] = d["yes_current_ask"] - d["yes_ask"]
    d["factory_no_ask_from_yes_bid"] = 1.0 - d["factory_current_yes_bid"]
    d["factory_no_ask_diff"] = d["no_ask"] - d["factory_no_ask_from_yes_bid"]
    d["forecast_remaining_f"] = d["factory_forecast_max_f"] - d["factory_running_max_f"]
    dates = sorted(d["target_date"].unique())
    split_idx = max(1, int(len(dates) * 0.7))
    train_dates = set(dates[:split_idx])
    d["period_split"] = np.where(d["target_date"].isin(train_dates), "train", "holdout")
    return d


def bucket_yes_ask(x: float) -> str:
    if x < 0.5:
        return "<0.50"
    if x < 0.6:
        return "[0.50,0.60)"
    if x < 0.7:
        return "[0.60,0.70)"
    if x < 0.8:
        return "[0.70,0.80)"
    if x < 0.9:
        return "[0.80,0.90)"
    return "[0.90,1.00]"


def bucket_no_ask(x: float) -> str:
    if x < 0.05:
        return "<0.05"
    if x < 0.1:
        return "[0.05,0.10)"
    if x < 0.2:
        return "[0.10,0.20)"
    if x < 0.3:
        return "[0.20,0.30)"
    if x < 0.5:
        return "[0.30,0.50)"
    return ">=0.50"


def add_slice_labels(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["yes_ask_bucket"] = d["yes_current_ask"].map(bucket_yes_ask)
    d["no_ask_bucket"] = d["no_ask"].map(bucket_no_ask)
    d["temp_trend_3h_bucket"] = pd.cut(
        d["d_tmpf_3h"],
        bins=[-math.inf, -0.5, 0.5, math.inf],
        labels=["cooling", "flat", "warming"],
    ).astype(str)
    d.loc[d["d_tmpf_3h"].isna(), "temp_trend_3h_bucket"] = "missing"
    d["humidity_trend_3h_bucket"] = pd.cut(
        d["d_relh_3h"],
        bins=[-math.inf, -5, 5, math.inf],
        labels=["drying", "flat", "humidifying"],
    ).astype(str)
    d.loc[d["d_relh_3h"].isna(), "humidity_trend_3h_bucket"] = "missing"
    d["decline_bucket"] = pd.cut(
        d["decline_c"],
        bins=[-math.inf, 0.25, 0.5, math.inf],
        labels=["peak_forming_like", "shallow_decline", "fade_like"],
    ).astype(str)
    d["decision_hour_bucket"] = d["decision_hour_local"].map(lambda x: f"h{int(x)}" if math.isfinite(x) else "missing")
    d["no_reheat_proxy"] = "mixed"
    d.loc[(d["d_tmpf_3h"] <= 0) & (d["decline_c"] >= 0.5), "no_reheat_proxy"] = "cooling_fade_proxy"
    d.loc[(d["d_tmpf_3h"] > 0.5) | (d["d_relh_3h"] < -5), "no_reheat_proxy"] = "warming_or_drying_proxy"
    d["minutes_since_max_bucket"] = pd.cut(
        d["factory_minutes_since_running_max"],
        bins=[-math.inf, 30, 90, math.inf],
        labels=["<30m", "30-90m", ">=90m"],
    ).astype(str)
    d.loc[d["factory_minutes_since_running_max"].isna(), "minutes_since_max_bucket"] = "missing"
    d["forecast_remaining_bucket"] = pd.cut(
        d["forecast_remaining_f"],
        bins=[-math.inf, 0.5, 2.0, math.inf],
        labels=["<=0.5F", "0.5-2F", ">2F"],
    ).astype(str)
    d.loc[d["forecast_remaining_f"].isna(), "forecast_remaining_bucket"] = "missing"
    return d


def block_bootstrap_roi(df: pd.DataFrame, profit_col: str, cost_col: str, reps: int = BOOTSTRAP_REPS) -> dict[str, Any]:
    clean = df[df[cost_col].fillna(0) > 0].copy()
    dates = sorted(clean["target_date"].dropna().unique())
    if len(dates) < 3 or clean.empty:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    grouped = {
        date: (
            float(g[profit_col].sum()),
            float(g[cost_col].sum()),
        )
        for date, g in clean.groupby("target_date")
    }
    rng = np.random.default_rng(SEED)
    vals: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = sum(grouped[d][0] for d in draw)
        cost = sum(grouped[d][1] for d in draw)
        if cost > 0:
            vals.append(profit / cost)
    if not vals:
        return {"ci_low": None, "ci_high": None, "reps": 0, "active_dates": len(dates)}
    return {
        "ci_low": float(np.quantile(vals, 0.025)),
        "ci_high": float(np.quantile(vals, 0.975)),
        "reps": len(vals),
        "active_dates": len(dates),
    }


def summarize(df: pd.DataFrame, name: str) -> dict[str, Any]:
    if df.empty:
        return {"slice": name, "n": 0}
    yes_cost = float(df["yes_current_ask"].sum())
    no_cost = float(df["no_ask"].sum())
    yes_profit = float(df["yes_profit_per_share"].sum())
    no_profit = float(df["no_profit_per_share"].sum())
    no_ci = block_bootstrap_roi(df, "no_profit_per_share", "no_ask")
    yes_ci = block_bootstrap_roi(df, "yes_profit_per_share", "yes_current_ask")
    return {
        "slice": name,
        "n": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "sample_start": str(df["target_date"].min()),
        "sample_end": str(df["target_date"].max()),
        "avg_yes_ask": float(df["yes_current_ask"].mean()),
        "avg_no_ask": float(df["no_ask"].mean()),
        "avg_yes_bid": float(df["yes_bid"].mean()),
        "avg_no_bid": float(df["no_bid"].mean()),
        "avg_no_spread": float(df["no_spread"].mean()),
        "avg_overround_ask": float(df["overround_ask"].mean()),
        "yes_win_rate": float(df["label_yes_wins"].mean()),
        "no_win_rate": float(df["label_no_wins"].mean()),
        "yes_edge_pp": float(df["yes_edge_pp"].mean()),
        "no_edge_pp": float(df["no_edge_pp"].mean()),
        "yes_cost": yes_cost,
        "no_cost": no_cost,
        "yes_profit": yes_profit,
        "no_profit": no_profit,
        "yes_roi": yes_profit / yes_cost if yes_cost else None,
        "no_roi": no_profit / no_cost if no_cost else None,
        "yes_roi_ci_low": yes_ci["ci_low"],
        "yes_roi_ci_high": yes_ci["ci_high"],
        "no_roi_ci_low": no_ci["ci_low"],
        "no_roi_ci_high": no_ci["ci_high"],
        "bootstrap_active_dates": no_ci["active_dates"],
    }


def table_by(df: pd.DataFrame, col: str, order: list[str] | None = None, min_n: int = 1) -> list[dict[str, Any]]:
    rows = [summarize(g, str(k)) for k, g in df.groupby(col, dropna=False) if len(g) >= min_n]
    if order is not None:
        rank = {v: i for i, v in enumerate(order)}
        rows.sort(key=lambda r: rank.get(r["slice"], 999))
    else:
        rows.sort(key=lambda r: str(r["slice"]))
    return rows


def capacity_summary(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {
        "top_ask_shares_p10": float(df["no_ask_size"].quantile(0.10)),
        "top_ask_shares_median": float(df["no_ask_size"].median()),
        "top_ask_shares_p90": float(df["no_ask_size"].quantile(0.90)),
        "depth_5c_shares_p10": float(df["no_depth_ask_5c_shares_raw"].quantile(0.10)),
        "depth_5c_shares_median": float(df["no_depth_ask_5c_shares_raw"].median()),
        "depth_5c_shares_p90": float(df["no_depth_ask_5c_shares_raw"].quantile(0.90)),
        "top_ask_cost_median": float((df["no_ask"] * df["no_ask_size"]).median()),
        "depth_5c_cost_median": float(df["no_depth_ask_5c_cost"].median()),
        "rows_with_top_ask_size_ge_1_share": int((df["no_ask_size"] >= 1).sum()),
        "rows_with_top_ask_size_ge_5_shares": int((df["no_ask_size"] >= 5).sum()),
        "rows_with_depth_5c_cost_ge_1p5": int((df["no_depth_ask_5c_cost"] >= 1.5).sum()),
        "rows_with_depth_5c_cost_ge_5": int((df["no_depth_ask_5c_cost"] >= 5).sum()),
    }
    for cap in (1.5, 5.0, 10.0):
        cap_label = str(cap).replace(".", "p")
        for suffix in ("top", "within_5c"):
            cost_col = f"no_fill_{suffix}_{cap_label}_cost"
            shares_col = f"no_fill_{suffix}_{cap_label}_shares"
            tmp = df[df[cost_col] > 0].copy()
            tmp[f"pnl_{suffix}_{cap_label}"] = tmp["label_no_wins"] * tmp[shares_col] - tmp[cost_col]
            ci = block_bootstrap_roi(tmp, f"pnl_{suffix}_{cap_label}", cost_col)
            total_cost = float(tmp[cost_col].sum())
            total_pnl = float(tmp[f"pnl_{suffix}_{cap_label}"].sum())
            out[f"roi_cap_{cap_label}_{suffix}"] = total_pnl / total_cost if total_cost else None
            out[f"roi_cap_{cap_label}_{suffix}_ci_low"] = ci["ci_low"]
            out[f"roi_cap_{cap_label}_{suffix}_ci_high"] = ci["ci_high"]
            out[f"cost_cap_{cap_label}_{suffix}"] = total_cost
            out[f"filled_rows_cap_{cap_label}_{suffix}"] = int(len(tmp))
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    self_check = data_self_check()
    gate = load_gate()

    v8 = load_v8_rows()
    v8 = load_reheat_join(v8)
    quoted = load_current_quotes(v8)
    raw_denominator = quoted[quoted["contains_running_bool"] & quoted["label_yes_wins"].notna()].copy()
    current_no_ask_missing_rows = int(raw_denominator["no_ask"].isna().sum())
    enriched = add_derived(quoted)
    enriched = add_slice_labels(enriched)
    enriched.to_csv(OUT_ROWS, index=False)

    yes_order = ["<0.50", "[0.50,0.60)", "[0.60,0.70)", "[0.70,0.80)", "[0.80,0.90)", "[0.90,1.00]"]
    no_order = ["<0.05", "[0.05,0.10)", "[0.10,0.20)", "[0.20,0.30)", "[0.30,0.50)", ">=0.50"]

    high_yes = enriched[enriched["yes_current_ask"] >= 0.70].copy()
    result = {
        "generated_at_utc": now_utc(),
        "sources": {
            "v8_feature_rows": str(V8_FEATURE_ROWS.relative_to(ROOT)),
            "reheat_feature_rows": str(REHEAT_FEATURE_ROWS.relative_to(ROOT)),
            "db": str(DB.relative_to(ROOT)),
            "output_rows": str(OUT_ROWS.relative_to(ROOT)),
            "current_no_price_source": "orderbook_file rows where outcome='no' and condition_id matches current bracket",
        },
        "data_self_check": self_check,
        "clob_gate": gate,
        "schema_notes": {
            "fact_signal_candidates_has_market_yes_price": "market_yes_price" in self_check["fact_signal_candidate_columns"],
            "fact_signal_candidates_has_no_spread": "no_spread" in self_check["fact_signal_candidate_columns"],
            "fact_signal_candidates_has_current_no_ask": "current_no_ask" in self_check["fact_signal_candidate_columns"],
        },
        "input_coverage": {
            "v8_rows": int(len(v8)),
            "analysis_rows": int(len(enriched)),
            "sample_start": str(enriched["target_date"].min()),
            "sample_end": str(enriched["target_date"].max()),
            "unsettled_rows": 0,
            "unsettled_share": 0.0,
            "current_no_ask_missing_rows": current_no_ask_missing_rows,
            "yes_quote_abs_mismatch_gt_1c": int((enriched["yes_quote_mismatch"].abs() > 0.01).sum()),
            "factory_join_rows": int(enriched["factory_minutes_since_running_max"].notna().sum()),
        },
        "overall": summarize(enriched, "all_current_yes_v8"),
        "high_yes_ask_ge_70": summarize(high_yes, "yes_ask_ge_0.70"),
        "period_split": table_by(enriched, "period_split", ["train", "holdout"]),
        "yes_calibration_by_yes_ask": table_by(enriched, "yes_ask_bucket", yes_order),
        "yes_calibration_high_ask_aggregate": [summarize(high_yes, ">=0.70")],
        "no_ev_by_yes_ask": table_by(enriched, "yes_ask_bucket", yes_order),
        "no_ev_by_no_ask": table_by(enriched, "no_ask_bucket", no_order),
        "attribution": {
            "temp_trend_3h": table_by(enriched, "temp_trend_3h_bucket", ["cooling", "flat", "warming", "missing"]),
            "humidity_trend_3h": table_by(enriched, "humidity_trend_3h_bucket", ["drying", "flat", "humidifying", "missing"]),
            "decline": table_by(enriched, "decline_bucket", ["peak_forming_like", "shallow_decline", "fade_like"]),
            "decision_hour": table_by(enriched, "decision_hour_bucket", ["h13", "h14", "h15", "h16", "h17"]),
            "combined_proxy": table_by(enriched, "no_reheat_proxy", ["cooling_fade_proxy", "warming_or_drying_proxy", "mixed"]),
            "minutes_since_max": table_by(enriched, "minutes_since_max_bucket", ["<30m", "30-90m", ">=90m", "missing"]),
            "forecast_remaining": table_by(enriched, "forecast_remaining_bucket", ["<=0.5F", "0.5-2F", ">2F", "missing"]),
        },
        "capacity": capacity_summary(enriched),
    }

    overall = result["overall"]
    holdout = next((r for r in result["period_split"] if r["slice"] == "holdout"), {})
    sig_pass = (
        overall.get("no_roi_ci_low") is not None
        and overall.get("no_roi_ci_low") > 0
        and overall.get("no_roi_ci_high") is not None
        and overall.get("no_roi_ci_high") > 0
    )
    baseline_pass = sig_pass and (overall.get("no_roi") or 0) > 0
    forward_pass = holdout.get("no_roi") is not None and holdout.get("no_roi") > 0
    result["three_gate"] = {
        "significance": "PASS" if sig_pass else "FAIL",
        "baseline": "PASS" if baseline_pass else "FAIL",
        "forward": "PASS" if forward_pass else "FAIL",
        "level": "confirmed" if sig_pass and baseline_pass and forward_pass else "inconclusive",
    }

    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({
        "out_json": str(OUT_JSON.relative_to(ROOT)),
        "out_rows": str(OUT_ROWS.relative_to(ROOT)),
        "overall_no_roi": result["overall"]["no_roi"],
        "overall_no_roi_ci": [result["overall"]["no_roi_ci_low"], result["overall"]["no_roi_ci_high"]],
        "three_gate": result["three_gate"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
