#!/usr/bin/env python3
"""Score current-YES rules using the shared forecast peak clock dataset.

This is the first scorecard that treats forecast peak clock as a reusable data
layer instead of a one-off API backfill inside a strategy script.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
FORECAST_PEAK = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_forecast_peak_scorecard_v14"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-forecast-peak-scorecard-v14.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260618
NOTIONAL = 5.0
TAKER_CUSHION = 0.02


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    ask_min: float = 0.55
    p_min: float = 0.50
    edge_min: float = 0.05
    liquidity_min: float = 2.0
    decline_min: float | None = None
    decline_max: float | None = None
    hour_min: int | None = None
    hour_max: int | None = None
    both_after_peak: bool = False
    both_peak_delta_min: float | None = None
    both_peak_delta_max: float | None = None
    models_agree_le_1h: bool = False
    both_gap_to_running_max: float | None = None
    gfs_after_peak: bool = False
    ecmwf_after_peak: bool = False


RULES = [
    Rule(
        name="v9_fixed_fade_confirmed",
        description="original fixed local h13-15 fade-confirmed v9 gate",
        decline_min=0.5,
        hour_min=13,
        hour_max=15,
    ),
    Rule(
        name="v9_fixed_fade_models_agree",
        description="v9 plus GFS/ECMWF forecast peak hours agree within 1h",
        decline_min=0.5,
        hour_min=13,
        hour_max=15,
        models_agree_le_1h=True,
    ),
    Rule(
        name="v9_fixed_fade_both_peaks_passed",
        description="v9 plus both GFS and ECMWF say forecast peak hour has passed",
        decline_min=0.5,
        hour_min=13,
        hour_max=15,
        both_after_peak=True,
    ),
    Rule(
        name="v9_fixed_fade_after_peak_agree",
        description="v9 plus both peaks passed and models agree within 1h",
        decline_min=0.5,
        hour_min=13,
        hour_max=15,
        both_after_peak=True,
        models_agree_le_1h=True,
    ),
    Rule(
        name="forecast_fade_no_fixed_hour",
        description="fade-confirmed, no fixed clock, both peaks passed within +4h",
        decline_min=0.5,
        both_peak_delta_min=0.0,
        both_peak_delta_max=4.0,
        both_gap_to_running_max=0.5,
    ),
    Rule(
        name="forecast_peak_forming_agree",
        description="early current-YES sleeve: no visible fade, near agreed forecast peak",
        decline_max=0.1,
        both_peak_delta_min=-1.0,
        both_peak_delta_max=1.0,
        both_gap_to_running_max=0.5,
        models_agree_le_1h=True,
    ),
    Rule(
        name="gfs_peak_forming_shadow",
        description="looser early sleeve: no visible fade, GFS peak nearby",
        decline_max=0.1,
        gfs_after_peak=False,
        both_peak_delta_min=None,
        both_peak_delta_max=None,
    ),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any, signed: bool = True) -> str:
    if value is None:
        return "NA"
    try:
        value_f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(value_f):
        return "NA"
    sign = "+" if signed else ""
    return f"{100 * value_f:{sign}.1f}%"


def fnum(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        value_f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(value_f):
        return "NA"
    return f"{value_f:.{digits}f}"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
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
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def score_rows(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = artifact["numeric_features"]
    categorical_features = artifact["categorical_features"]
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        lookup = {cat: col for col, cat in enumerate(cats)}
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        for row_idx, value in enumerate(values):
            col = lookup.get(str(value))
            if col is not None:
                mat[row_idx, col] = 1.0
        cat_parts.append(mat)
    transformed = np.concatenate([numeric, *cat_parts], axis=1)
    logits = transformed @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def load_rows() -> pd.DataFrame:
    features = pd.read_csv(FEATURE_ROWS)
    peak = pd.read_csv(FORECAST_PEAK)
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    features["p_yes_win"] = score_rows(features, artifact)
    out = features.merge(peak, on=["city", "target_date"], how="left", suffixes=("", "_peak"))

    numeric_cols = [
        "yes_current_ask",
        "yes_current_size",
        "d1_no_ask",
        "d1_no_size",
        "decline_c",
        "decision_hour_local",
        "running_native",
        "bracket_low",
        "bracket_high",
        "gfs_forecast_peak_hour_local",
        "ecmwf_forecast_peak_hour_local",
        "gfs_forecast_max_native",
        "ecmwf_forecast_max_native",
    ]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out["available_notional_at_ask"] = out["yes_current_ask"] * out["yes_current_size"]
    out["edge_snapshot"] = out["p_yes_win"] - out["yes_current_ask"]
    out["snapshot_dt"] = pd.to_datetime(out["snapshot_ts_utc"], utc=True, errors="coerce")
    out["gfs_forecast_peak_delta_hours_local"] = out["decision_hour_local"] - out["gfs_forecast_peak_hour_local"]
    out["ecmwf_forecast_peak_delta_hours_local"] = out["decision_hour_local"] - out["ecmwf_forecast_peak_hour_local"]
    out["gfs_forecast_gap_to_running_native"] = out["gfs_forecast_max_native"] - out["running_native"]
    out["ecmwf_forecast_gap_to_running_native"] = out["ecmwf_forecast_max_native"] - out["running_native"]
    out["forecast_peak_hour_spread"] = (
        out["gfs_forecast_peak_hour_local"] - out["ecmwf_forecast_peak_hour_local"]
    ).abs()
    out["both_models_peak_present"] = out[["gfs_forecast_peak_hour_local", "ecmwf_forecast_peak_hour_local"]].notna().all(axis=1)
    out["forecast_peak_models_agree_le_1h"] = out["forecast_peak_hour_spread"].le(1.0)
    out["period"] = np.where(out["target_date"].astype(str) < SPLIT_DATE, "train", "holdout")
    return out


def rule_mask(df: pd.DataFrame, rule: Rule) -> pd.Series:
    mask = (
        df["has_d1_no"].astype(bool)
        & df["both_models_peak_present"].astype(bool)
        & df["yes_current_ask"].ge(rule.ask_min)
        & df["available_notional_at_ask"].ge(rule.liquidity_min)
        & df["p_yes_win"].ge(rule.p_min)
        & df["edge_snapshot"].ge(rule.edge_min)
    )
    if rule.decline_min is not None:
        mask &= df["decline_c"].ge(rule.decline_min)
    if rule.decline_max is not None:
        mask &= df["decline_c"].le(rule.decline_max)
    if rule.hour_min is not None:
        mask &= df["decision_hour_local"].ge(rule.hour_min)
    if rule.hour_max is not None:
        mask &= df["decision_hour_local"].le(rule.hour_max)
    if rule.both_after_peak:
        mask &= df["gfs_forecast_peak_delta_hours_local"].ge(0) & df["ecmwf_forecast_peak_delta_hours_local"].ge(0)
    if rule.both_peak_delta_min is not None:
        mask &= df["gfs_forecast_peak_delta_hours_local"].ge(rule.both_peak_delta_min)
        mask &= df["ecmwf_forecast_peak_delta_hours_local"].ge(rule.both_peak_delta_min)
    if rule.both_peak_delta_max is not None:
        mask &= df["gfs_forecast_peak_delta_hours_local"].le(rule.both_peak_delta_max)
        mask &= df["ecmwf_forecast_peak_delta_hours_local"].le(rule.both_peak_delta_max)
    if rule.models_agree_le_1h:
        mask &= df["forecast_peak_models_agree_le_1h"].astype(bool)
    if rule.both_gap_to_running_max is not None:
        mask &= df["gfs_forecast_gap_to_running_native"].le(rule.both_gap_to_running_max)
        mask &= df["ecmwf_forecast_gap_to_running_native"].le(rule.both_gap_to_running_max)
    if rule.gfs_after_peak:
        mask &= df["gfs_forecast_peak_delta_hours_local"].ge(0)
    if rule.ecmwf_after_peak:
        mask &= df["ecmwf_forecast_peak_delta_hours_local"].ge(0)

    if rule.name == "gfs_peak_forming_shadow":
        mask &= df["gfs_forecast_peak_delta_hours_local"].between(-1.0, 1.0)
        mask &= df["gfs_forecast_gap_to_running_native"].le(0.5)
    return mask


def dedupe_live(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values("snapshot_dt")
        .drop_duplicates(["target_date", "city", "current_bracket"], keep="first")
        .sort_values(["target_date", "city", "snapshot_dt"])
        .groupby(["target_date", "city"])
        .head(2)
        .copy()
    )


def pnl_for_binary(price: np.ndarray, wins: np.ndarray) -> np.ndarray:
    return np.where(wins.astype(bool), NOTIONAL / price - NOTIONAL, -NOTIONAL)


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    d = dedupe_live(frame)
    if d.empty:
        return {
            "orders": 0,
            "active_dates": 0,
            "cities": 0,
            "notional_usd": 0.0,
            "yes_wins": 0,
            "yes_win_rate": None,
            "yes_roi": None,
            "d1_no_roi": None,
            "yes_minus_d1_no_roi": None,
            "pnl_usd": 0.0,
        }

    yes_price = np.minimum(d["yes_current_ask"].astype(float).to_numpy() + TAKER_CUSHION, 0.999)
    yes_wins = d["label_yes_wins"].astype(int).to_numpy() == 1
    yes_pnl = pnl_for_binary(yes_price, yes_wins)

    no_price = np.minimum(d["d1_no_ask"].astype(float).to_numpy() + TAKER_CUSHION, 0.999)
    no_wins = ~d["d1_no_loses"].astype(bool).to_numpy()
    no_pnl = pnl_for_binary(no_price, no_wins)

    cost = NOTIONAL * len(d)
    return {
        "orders": int(len(d)),
        "active_dates": int(d["target_date"].nunique()),
        "cities": int(d["city"].nunique()),
        "notional_usd": float(cost),
        "yes_wins": int(yes_wins.sum()),
        "yes_win_rate": float(yes_wins.mean()),
        "yes_roi": float(yes_pnl.sum() / cost),
        "d1_no_win_rate": float(no_wins.mean()),
        "d1_no_roi": float(no_pnl.sum() / cost),
        "yes_minus_d1_no_roi": float((yes_pnl.sum() - no_pnl.sum()) / cost),
        "pnl_usd": float(yes_pnl.sum()),
        "avg_yes_ask": float(d["yes_current_ask"].mean()),
        "avg_d1_no_ask": float(d["d1_no_ask"].mean()),
        "avg_p_yes_win": float(d["p_yes_win"].mean()),
        "avg_edge_snapshot": float(d["edge_snapshot"].mean()),
        "avg_gfs_delta": float(d["gfs_forecast_peak_delta_hours_local"].mean()),
        "avg_ecmwf_delta": float(d["ecmwf_forecast_peak_delta_hours_local"].mean()),
        "models_agree_rate": float(d["forecast_peak_models_agree_le_1h"].mean()),
        "orders_per_active_day": float(len(d) / d["target_date"].nunique()),
        "date_min": str(d["target_date"].min()),
        "date_max": str(d["target_date"].max()),
    }


def bootstrap_ci(frame: pd.DataFrame, iterations: int = 3000) -> dict[str, list[float | None]]:
    d = dedupe_live(frame)
    if d.empty or d["target_date"].nunique() < 3:
        return {"yes_roi_ci95": [None, None], "yes_minus_d1_no_roi_ci95": [None, None]}
    rng = np.random.default_rng(SEED)
    day_rows = []
    for _, g in d.groupby("target_date"):
        yes_price = np.minimum(g["yes_current_ask"].astype(float).to_numpy() + TAKER_CUSHION, 0.999)
        yes_wins = g["label_yes_wins"].astype(int).to_numpy() == 1
        yes_pnl = pnl_for_binary(yes_price, yes_wins).sum()

        no_price = np.minimum(g["d1_no_ask"].astype(float).to_numpy() + TAKER_CUSHION, 0.999)
        no_wins = ~g["d1_no_loses"].astype(bool).to_numpy()
        no_pnl = pnl_for_binary(no_price, no_wins).sum()
        day_rows.append((float(yes_pnl), float(no_pnl), float(NOTIONAL * len(g))))
    arr = np.asarray(day_rows, dtype=float)
    yes_draws = []
    delta_draws = []
    for _ in range(iterations):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        cost = sample[:, 2].sum()
        yes_draws.append(sample[:, 0].sum() / cost)
        delta_draws.append((sample[:, 0].sum() - sample[:, 1].sum()) / cost)
    yes_lo, yes_hi = np.nanpercentile(yes_draws, [2.5, 97.5])
    delta_lo, delta_hi = np.nanpercentile(delta_draws, [2.5, 97.5])
    return {
        "yes_roi_ci95": [float(yes_lo), float(yes_hi)],
        "yes_minus_d1_no_roi_ci95": [float(delta_lo), float(delta_hi)],
    }


def evaluate_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    selected_frames = []
    for rule in RULES:
        selected = df[rule_mask(df, rule)].copy()
        selected["rule"] = rule.name
        selected["rule_description"] = rule.description
        selected_frames.append(dedupe_live(selected))
        for period in ["train", "holdout", "all"]:
            scope = selected if period == "all" else selected[selected["period"].eq(period)]
            row = summarize(scope)
            row.update(bootstrap_ci(scope))
            row.update(
                {
                    "rule": rule.name,
                    "description": rule.description,
                    "period": period,
                    "taker_cushion": TAKER_CUSHION,
                }
            )
            rows.append(row)
    selected_all = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    return pd.DataFrame(rows), selected_all


def forecast_bins(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base = df[
        df["has_d1_no"].astype(bool)
        & df["both_models_peak_present"].astype(bool)
        & df["yes_current_ask"].ge(0.55)
        & df["available_notional_at_ask"].ge(2.0)
        & df["p_yes_win"].ge(0.50)
        & df["edge_snapshot"].ge(0.05)
    ].copy()
    base["gfs_delta_bucket"] = pd.cut(
        base["gfs_forecast_peak_delta_hours_local"],
        bins=[-99, -2, -1, 0, 1, 2, 4, 99],
        labels=["<=-2h", "-2..-1h", "-1..0h", "0..1h", "1..2h", "2..4h", ">4h"],
        right=False,
    )
    for period in ["train", "holdout", "all"]:
        scope = base if period == "all" else base[base["period"].eq(period)]
        for bucket, group in scope.groupby("gfs_delta_bucket", observed=False):
            row = summarize(group)
            row.update({"period": period, "gfs_delta_bucket": str(bucket)})
            rows.append(row)
    return pd.DataFrame(rows)


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame, bins: pd.DataFrame) -> None:
    holdout = summary[summary["period"].eq("holdout")].copy()
    lines = [
        "# Theta Current YES Forecast Peak Scorecard v14",
        "",
        "Status: research_scorecard / not_live_ready_upgrade",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `forecast_peak_clock_current_yes_scorecard` = 在同一 current-YES replay 上，用共享 forecast peak clock 表评估固定 v9、after-peak 过滤、peak-forming 早入场的成功率和收益率。",
        "",
        "## 数据完整性自检",
        "",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- fact_trades trade_class: `{payload['data_self_check']['fact_trades_by_class']}`",
        f"- settlement_status: `{payload['data_self_check']['fact_trades_by_settlement_status']}`",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['fact_signal_candidate_coverage']}`",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_order_fill_join']}`",
        f"- CLOB gate: `{payload['clob_gate']}`",
        "",
        "## 人话结论",
        "",
        "forecast peak clock 有价值，但它目前更像“解释和分层风险”的特征，不是一个能直接替换 v9 的上线规则。",
        "",
        "最稳的仍然是原 v9：看到温度已经从 running max 回落，再买当前最高温 YES。给 v9 额外加 forecast peak 过滤，样本会变少，收益没有变得更可靠；而更早的 peak-forming 规则虽然试图在价格还没完全收敛前入场，但 holdout 仍然不够稳。",
        "",
        "所以当前推进路线不是扩大 live，而是：继续保留 v9 tiny-live/telemetry，生产原生落盘 forecast peak fields，用 forward would-order 样本验证 forecast-clock 是否能提前入场或过滤坏单。",
        "",
        "## Holdout 核心结果（$5/order, taker +2c）",
        "",
        "| rule | orders | days | win | YES ROI | CI95 | d1 NO ROI | YES-NO | YES-NO CI95 | avg ask | avg GFS delta | avg ECMWF delta |",
        "|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---:|",
    ]
    for _, row in holdout.iterrows():
        yes_ci = row.get("yes_roi_ci95") or [None, None]
        delta_ci = row.get("yes_minus_d1_no_roi_ci95") or [None, None]
        lines.append(
            f"| {row['rule']} | {int(row['orders'])} | {int(row['active_dates'])} | "
            f"{pct(row['yes_win_rate'])} | {pct(row['yes_roi'])} | "
            f"[{pct(yes_ci[0])}, {pct(yes_ci[1])}] | {pct(row.get('d1_no_roi'))} | "
            f"{pct(row.get('yes_minus_d1_no_roi'))} | "
            f"[{pct(delta_ci[0])}, {pct(delta_ci[1])}] | "
            f"{fnum(row.get('avg_yes_ask'))} | {fnum(row.get('avg_gfs_delta'), 1)} | "
            f"{fnum(row.get('avg_ecmwf_delta'), 1)} |"
        )

    lines.extend(
        [
            "",
            "## Forecast Delta Diagnostic",
            "",
            "下面这张表不是参数选择器，只看满足基础 price/model gate 后，GFS 预报峰值相对决策时间的粗分桶。它回答的是：是不是越接近/越过预报峰值，current YES 越安全。",
            "",
            "| period | GFS delta bucket | orders | days | win | YES ROI | avg ask |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in bins[bins["period"].eq("holdout")].iterrows():
        if int(row["orders"]) == 0:
            continue
        lines.append(
            f"| {row['period']} | {row['gfs_delta_bucket']} | {int(row['orders'])} | "
            f"{int(row['active_dates'])} | {pct(row['yes_win_rate'])} | "
            f"{pct(row['yes_roi'])} | {fnum(row.get('avg_yes_ask'))} |"
        )

    lines.extend(
        [
            "",
            "## Funnel",
            "",
            f"- raw current-YES replay rows: `{payload['coverage']['raw_rows']}`",
            f"- joined forecast peak rows: `{payload['coverage']['joined_rows']}`",
            f"- rows with both forecast models: `{payload['coverage']['rows_with_both_peak_models']}`",
            f"- live-style dedupe grain: first `target_date + city + current_bracket`, max 2 current brackets per city-day",
            "",
            "## Verdict",
            "",
            "- significance=FAIL for forecast-clock upgrade: after-peak/agreement variants do not beat v9 with stronger support.",
            "- baseline=FAIL: v9 remains the stronger simple baseline; forecast-clock filters mostly shrink samples.",
            "- forward=FAIL/NA: these are historical backfilled forecast fields, not production native forward fields.",
            "- conclusion=`inconclusive` for live upgrade; `telemetry_required` for forward sample collection.",
            "",
            "## Outputs",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- rule summary CSV: `{(OUT_DIR / 'rule_summary.csv').relative_to(ROOT)}`",
            f"- forecast bin CSV: `{(OUT_DIR / 'forecast_delta_bins.csv').relative_to(ROOT)}`",
            f"- selected rows CSV: `{(OUT_DIR / 'selected_rule_rows.csv').relative_to(ROOT)}`",
            f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    summary, selected = evaluate_rules(rows)
    bins = forecast_bins(rows)

    summary.to_csv(OUT_DIR / "rule_summary.csv", index=False)
    selected.to_csv(OUT_DIR / "selected_rule_rows.csv", index=False)
    bins.to_csv(OUT_DIR / "forecast_delta_bins.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "historical current-YES orderbook replay joined to shared forecast peak clock backfill",
        "split_date": SPLIT_DATE,
        "taker_cushion": TAKER_CUSHION,
        "notional_usd_per_order": NOTIONAL,
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": {
            "raw_rows": int(pd.read_csv(FEATURE_ROWS, usecols=["city"]).shape[0]),
            "joined_rows": int(len(rows)),
            "rows_with_both_peak_models": int(rows["both_models_peak_present"].sum()),
            "cities": int(rows["city"].nunique()),
            "date_min": str(rows["target_date"].min()),
            "date_max": str(rows["target_date"].max()),
            "active_dates": int(rows["target_date"].nunique()),
        },
        "holdout_rule_summary": json_ready(summary[summary["period"].eq("holdout")].to_dict("records")),
        "all_rule_summary": json_ready(summary[summary["period"].eq("all")].to_dict("records")),
        "forecast_delta_bins_holdout": json_ready(bins[bins["period"].eq("holdout")].to_dict("records")),
        "outputs": {
            "md": str(OUT_MD.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "rule_summary_csv": str((OUT_DIR / "rule_summary.csv").relative_to(ROOT)),
            "forecast_delta_bins_csv": str((OUT_DIR / "forecast_delta_bins.csv").relative_to(ROOT)),
            "selected_rows_csv": str((OUT_DIR / "selected_rule_rows.csv").relative_to(ROOT)),
        },
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, summary, bins)
    print(json.dumps(payload["outputs"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
