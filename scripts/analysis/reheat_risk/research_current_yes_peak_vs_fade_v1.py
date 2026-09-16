#!/usr/bin/env python3
"""Compare current-YES peak-forming and fade-confirmed timing heads.

Evidence layer: opportunity/orderbook replay, not live_real fills.  The input
is the shared reheat-risk feature factory output; this script keeps one
current-YES state row per city/date/hour/current-bracket and derives the
legacy v9 model feature columns from that shared fact layer.
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
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_vs_fade_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-current-yes-peak-vs-fade-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-current-yes-peak-vs-fade-v1.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260616


@dataclass(frozen=True)
class TimingRule:
    head: str
    hour_start: int
    hour_end: int
    decline_min: float | None
    decline_max: float | None
    ask_min: float
    p_min: float
    edge_min: float

    @property
    def name(self) -> str:
        if self.decline_max is not None:
            state = f"decline<={self.decline_max:g}"
        else:
            state = f"decline>={self.decline_min:g}"
        return (
            f"{self.head}|h{self.hour_start}-{self.hour_end}|{state}|"
            f"ask>={self.ask_min:g}|p>={self.p_min:g}|edge>={self.edge_min:g}"
        )


FIXED_PEAK = TimingRule("current_yes_peak_forming", 13, 13, None, 0.1, 0.55, 0.50, 0.05)
FIXED_FADE = TimingRule("current_yes_fade_confirmed", 13, 15, 0.5, None, 0.55, 0.50, 0.05)
SLIPPAGE_LEVELS = (0.00, 0.01, 0.02, 0.05)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, signed: bool = True) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.1f}%"


def fnum(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


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


def bracket_low_value(value: Any) -> float:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return float("nan")
    text = str(value).strip()
    if not text:
        return float("nan")
    try:
        return float(text.split("-", 1)[0])
    except ValueError:
        return float("nan")


def prepare_factory_state_rows(raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse quote-grain factory rows into current-YES state rows."""
    required = {
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "city",
        "target_date",
        "current_bracket",
        "current_yes_ask",
        "current_yes_ask_size",
        "current_bracket_held",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"feature factory is missing required columns: {missing}")

    state_keys = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
    ]
    df = raw.sort_values(["decision_snapshot_ts_utc", "city", "target_date"]).drop_duplicates(state_keys).copy()

    df["snapshot_ts_utc"] = df["decision_snapshot_ts_utc"]
    df["yes_current_ask"] = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    df["yes_current_size"] = pd.to_numeric(df["current_yes_ask_size"], errors="coerce")
    df["label_yes_wins"] = pd.to_numeric(df["current_bracket_held"], errors="coerce")
    df["decline_c"] = pd.to_numeric(df["decline_from_max_c"], errors="coerce")
    df["month"] = pd.to_datetime(df["target_date"], errors="coerce").dt.month

    df["relh_now"] = pd.to_numeric(df.get("relative_humidity_pct"), errors="coerce")
    df["sknt_now"] = pd.to_numeric(df.get("wind_speed_kt"), errors="coerce")
    df["sky_now"] = pd.to_numeric(df.get("sky_cover_code"), errors="coerce")
    df["d_tmpf_1h"] = pd.to_numeric(df.get("temp_trend_1h_f"), errors="coerce")
    df["d_tmpf_3h"] = pd.to_numeric(df.get("temp_trend_3h_f"), errors="coerce")
    if "d_dwpf_3h" not in df.columns:
        df["d_dwpf_3h"] = np.nan
    if "d_relh_3h" not in df.columns:
        df["d_relh_3h"] = np.nan

    d1_low = df.get("d1_no_bracket", pd.Series(np.nan, index=df.index)).map(bracket_low_value)
    df["gap_running_to_d1_low_native"] = d1_low - pd.to_numeric(df["running_native"], errors="coerce")
    df["gap_current_to_d1_low_native"] = d1_low - pd.to_numeric(df["current_native"], errors="coerce")
    df["decline_band"] = pd.to_numeric(df["decline_native"], errors="coerce")
    df.loc[df["unit"].astype(str).eq("F"), "decline_band"] = df.loc[
        df["unit"].astype(str).eq("F"), "decline_band"
    ] / 2.0
    df["ask_gap_d1_no_minus_yes"] = pd.to_numeric(df.get("d1_no_ask"), errors="coerce") - df["yes_current_ask"]
    df["log_yes_size"] = np.log1p(pd.to_numeric(df["yes_current_size"], errors="coerce").clip(lower=0.0))

    for col in ("is_f",):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin({"true", "1"})

    before = len(df)
    raw_factory_rows = int(len(raw))
    df = df[df["label_yes_wins"].isin([0.0, 1.0])].copy()
    df.attrs["dropped_unlabeled_rows"] = int(before - len(df))
    df.attrs["raw_factory_rows"] = raw_factory_rows
    return df


def load_scored() -> pd.DataFrame:
    if not FEATURE_ROWS.exists():
        raise FileNotFoundError(f"missing feature rows: {FEATURE_ROWS}")
    if not MODEL_ARTIFACT.exists():
        raise FileNotFoundError(f"missing model artifact: {MODEL_ARTIFACT}")
    raw = pd.read_csv(FEATURE_ROWS)
    df = prepare_factory_state_rows(raw)
    for col in ("current_yes_wins", "has_d1_no", "d1_no_loses", "is_f"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin({"true", "1"})
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    df["label_yes_wins"] = df["label_yes_wins"].astype(int)
    df["p_yes_win"] = score_rows(df, artifact)
    df["edge_snapshot"] = df["p_yes_win"] - df["yes_current_ask"]
    df["available_notional_at_ask"] = df["yes_current_ask"] * df["yes_current_size"]
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    df["period"] = np.where(df["target_date"].astype(str) < SPLIT_DATE, "train", "holdout")
    return df


def rule_grid() -> list[TimingRule]:
    peak = [
        TimingRule("current_yes_peak_forming", h0, h1, None, decline_max, ask_min, 0.50, edge_min)
        for h0, h1 in ((12, 14), (13, 13), (13, 14), (14, 15))
        for decline_max in (0.01, 0.1, 0.25)
        for ask_min in (0.55, 0.65)
        for edge_min in (0.03, 0.05)
    ]
    fade = [
        TimingRule("current_yes_fade_confirmed", h0, h1, decline_min, None, ask_min, 0.50, edge_min)
        for h0, h1 in ((13, 15), (13, 16), (14, 16), (15, 17))
        for decline_min in (0.5, 1.0)
        for ask_min in (0.55, 0.65)
        for edge_min in (0.03, 0.05)
    ]
    return peak + fade


def apply_rule(df: pd.DataFrame, rule: TimingRule) -> pd.DataFrame:
    mask = (
        df["decision_hour_local"].between(rule.hour_start, rule.hour_end)
        & df["yes_current_ask"].ge(rule.ask_min)
        & df["available_notional_at_ask"].ge(5.0)
        & df["p_yes_win"].ge(rule.p_min)
        & df["edge_snapshot"].ge(rule.edge_min)
    )
    if rule.decline_max is not None:
        mask &= df["decline_c"].le(rule.decline_max)
    if rule.decline_min is not None:
        mask &= df["decline_c"].ge(rule.decline_min)
    work = df[mask].copy()
    if work.empty:
        return work
    work["timing_head"] = rule.head
    work["rule"] = rule.name
    return (
        work.sort_values(["snapshot_dt", "city", "target_date", "current_bracket"])
        .drop_duplicates(["city", "target_date", "current_bracket"], keep="first")
        .sort_values(["target_date", "city", "snapshot_dt"])
        .groupby(["target_date", "city"])
        .head(2)
        .copy()
    )


def summarize(frame: pd.DataFrame, slippage: float = 0.0) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "active_dates": 0,
            "cities": 0,
            "win_rate": None,
            "roi": None,
            "model_ev_roi": None,
        }
    px = np.minimum(frame["yes_current_ask"].astype(float).to_numpy() + slippage, 0.999)
    y = frame["label_yes_wins"].astype(int).to_numpy()
    p = frame["p_yes_win"].astype(float).to_numpy()
    pnl = y - px
    ev = p - px
    cost = float(px.sum())
    daily = (
        pd.DataFrame({"target_date": frame["target_date"].astype(str).to_numpy(), "cost": px, "pnl": pnl})
        .groupby("target_date")
        .agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    )
    return {
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "avg_hour": float(frame["decision_hour_local"].mean()),
        "avg_decline_c": float(frame["decline_c"].mean()),
        "avg_ask": float(frame["yes_current_ask"].mean()),
        "median_ask": float(frame["yes_current_ask"].median()),
        "avg_p_yes_win": float(frame["p_yes_win"].mean()),
        "avg_edge": float(frame["edge_snapshot"].mean()),
        "win_rate": float(y.mean()),
        "reheat_loss_rate": float(1.0 - y.mean()),
        "cost": cost,
        "pnl": float(pnl.sum()),
        "roi": float(pnl.sum() / cost) if cost else None,
        "model_ev": float(ev.sum()),
        "model_ev_roi": float(ev.sum() / cost) if cost else None,
        "positive_date_rate": float((daily["pnl"] > 0).mean()) if not daily.empty else None,
        "date_min": str(frame["target_date"].min()),
        "date_max": str(frame["target_date"].max()),
    }


def bootstrap_roi(frame: pd.DataFrame, slippage: float = 0.0, reps: int = 1200) -> dict[str, Any]:
    if frame.empty or frame["target_date"].nunique() < 3:
        return {"roi_ci95": [None, None], "reps": 0}
    px = np.minimum(frame["yes_current_ask"].astype(float).to_numpy() + slippage, 0.999)
    work = pd.DataFrame(
        {
            "target_date": frame["target_date"].astype(str).to_numpy(),
            "cost": px,
            "pnl": frame["label_yes_wins"].astype(int).to_numpy() - px,
        }
    )
    daily = work.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum")).reset_index()
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(reps):
        sample = daily.iloc[rng.integers(0, len(daily), len(daily))]
        cost = float(sample["cost"].sum())
        if cost > 0:
            vals.append(float(sample["pnl"].sum() / cost))
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals else (float("nan"), float("nan"))
    return {"roi_ci95": [float(lo), float(hi)], "reps": len(vals)}


def choose_train_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, TimingRule]]:
    rows = []
    chosen: dict[str, TimingRule] = {}
    train = df[df["period"].eq("train")].copy()
    for rule in rule_grid():
        selected = apply_rule(train, rule)
        sm = summarize(selected, slippage=0.02)
        rows.append({"head": rule.head, "rule": rule.name, **sm})
    table = pd.DataFrame(rows)
    for head, group in table.groupby("head"):
        eligible = group[group["rows"].ge(10) & group["active_dates"].ge(5) & group["model_ev_roi"].gt(0)].copy()
        if eligible.empty:
            eligible = group[group["rows"].ge(5) & group["model_ev_roi"].gt(0)].copy()
        if eligible.empty:
            continue
        best = eligible.sort_values(["roi", "model_ev_roi", "rows"], ascending=[False, False, False]).iloc[0]
        for rule in rule_grid():
            if rule.name == best["rule"]:
                chosen[head] = rule
                break
    return table.sort_values(["head", "roi", "model_ev_roi"], ascending=[True, False, False]), chosen


def evaluate_rules(df: pd.DataFrame, rules: dict[str, TimingRule]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows = []
    frames = {}
    for head, rule in rules.items():
        selected = apply_rule(df[df["period"].eq("holdout")].copy(), rule)
        frames[head] = selected
        for slip in SLIPPAGE_LEVELS:
            rows.append(
                {
                    "head": head,
                    "rule": rule.name,
                    "period": "holdout",
                    "slippage": slip,
                    **summarize(selected, slippage=slip),
                    **bootstrap_roi(selected, slippage=slip),
                }
            )
    return pd.DataFrame(rows), frames


def fixed_rule_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rules = {
        FIXED_PEAK.head: FIXED_PEAK,
        FIXED_FADE.head: FIXED_FADE,
    }
    return evaluate_rules(df, rules)


def prefix_walkforward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    decisions = []
    dates = sorted(df["target_date"].astype(str).unique())
    for date in dates:
        hist = df[df["target_date"].astype(str) < date].copy()
        day = df[df["target_date"].astype(str).eq(date)].copy()
        if hist["target_date"].nunique() < 10:
            continue
        _, chosen = choose_train_rules(hist)
        for head in ("current_yes_peak_forming", "current_yes_fade_confirmed"):
            rule = chosen.get(head)
            if rule is None:
                decisions.append({"target_date": date, "head": head, "rule": None, "day_rows": 0})
                continue
            selected = apply_rule(day, rule)
            decisions.append({"target_date": date, "head": head, "rule": rule.name, "day_rows": int(len(selected))})
            if not selected.empty:
                rows.append(selected)
    wf_rows = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if wf_rows.empty:
        summary = pd.DataFrame()
    else:
        out = []
        for head, group in wf_rows.groupby("timing_head"):
            out.append({"head": head, **summarize(group, slippage=0.02), **bootstrap_roi(group, slippage=0.02)})
        summary = pd.DataFrame(out)
    return summary, pd.DataFrame(decisions)


def paired_city_date(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    peak_pool = apply_rule(df, TimingRule("current_yes_peak_forming", 12, 16, None, 0.1, 0.55, 0.50, 0.03))
    fade_pool = apply_rule(df, TimingRule("current_yes_fade_confirmed", 12, 17, 0.5, None, 0.55, 0.50, 0.03))
    if peak_pool.empty or fade_pool.empty:
        return pd.DataFrame(), {"pairs": 0}
    peak_first = peak_pool.sort_values("snapshot_dt").drop_duplicates(["city", "target_date"], keep="first")
    fade_first = fade_pool.sort_values("snapshot_dt").drop_duplicates(["city", "target_date"], keep="first")
    merged = peak_first.merge(
        fade_first,
        on=["city", "target_date"],
        suffixes=("_peak", "_fade"),
        how="inner",
    )
    merged = merged[merged["snapshot_dt_fade"] > merged["snapshot_dt_peak"]].copy()
    if merged.empty:
        return merged, {"pairs": 0}
    peak_px = np.minimum(merged["yes_current_ask_peak"].astype(float).to_numpy() + 0.02, 0.999)
    fade_px = np.minimum(merged["yes_current_ask_fade"].astype(float).to_numpy() + 0.02, 0.999)
    peak_y = merged["label_yes_wins_peak"].astype(int).to_numpy()
    fade_y = merged["label_yes_wins_fade"].astype(int).to_numpy()
    merged["hours_later"] = (
        (merged["snapshot_dt_fade"] - merged["snapshot_dt_peak"]).dt.total_seconds() / 3600.0
    )
    merged["early_price_saving"] = merged["yes_current_ask_fade"] - merged["yes_current_ask_peak"]
    merged["same_bracket"] = merged["current_bracket_peak"].astype(str).eq(merged["current_bracket_fade"].astype(str))
    merged["peak_pnl_plus_2c"] = peak_y - peak_px
    merged["fade_pnl_plus_2c"] = fade_y - fade_px
    merged["peak_minus_fade_pnl_plus_2c"] = merged["peak_pnl_plus_2c"] - merged["fade_pnl_plus_2c"]
    summary = {
        "pairs": int(len(merged)),
        "active_dates": int(merged["target_date"].nunique()),
        "cities": int(merged["city"].nunique()),
        "avg_hours_later": float(merged["hours_later"].mean()),
        "avg_early_price_saving": float(merged["early_price_saving"].mean()),
        "median_early_price_saving": float(merged["early_price_saving"].median()),
        "peak_win_rate": float(peak_y.mean()),
        "fade_win_rate": float(fade_y.mean()),
        "peak_roi_plus_2c": float((peak_y - peak_px).sum() / peak_px.sum()),
        "fade_roi_plus_2c": float((fade_y - fade_px).sum() / fade_px.sum()),
        "avg_peak_minus_fade_pnl_plus_2c": float(merged["peak_minus_fade_pnl_plus_2c"].mean()),
        "same_bracket_rate": float(merged["same_bracket"].mean()),
        "early_better_rate": float((merged["peak_minus_fade_pnl_plus_2c"] > 0).mean()),
    }
    return merged, summary


def slippage_table(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for head, frame in frames.items():
        for slip in SLIPPAGE_LEVELS:
            rows.append({"head": head, "slippage": slip, **summarize(frame, slippage=slip)})
    return pd.DataFrame(rows)


def table_holdout(rows: pd.DataFrame, slip: float = 0.02) -> list[str]:
    view = rows[rows["slippage"].eq(slip)].copy()
    out = [
        "| head | rows | days | win | ROI +2c | CI95 | model EV ROI +2c | avg ask | avg p | avg decline | rule |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    if view.empty:
        out.append("| NA | 0 | 0 | NA | NA | NA | NA | NA | NA | NA | NA |")
        return out
    for _, r in view.sort_values("head").iterrows():
        ci = r.get("roi_ci95") if isinstance(r.get("roi_ci95"), list) else [None, None]
        out.append(
            f"| `{r['head']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['win_rate'], signed=False)} | "
            f"{pct(r['roi'])} | [{pct(ci[0])}, {pct(ci[1])}] | {pct(r['model_ev_roi'])} | "
            f"{fnum(r['avg_ask'])} | {fnum(r['avg_p_yes_win'])} | {fnum(r['avg_decline_c'])} | `{r['rule']}` |"
        )
    return out


def table_slippage(rows: pd.DataFrame) -> list[str]:
    out = [
        "| head | slippage | rows | win | ROI | model EV ROI | avg ask |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in rows.sort_values(["head", "slippage"]).iterrows():
        out.append(
            f"| `{r['head']}` | {int(round(float(r['slippage']) * 100))}c | {int(r['rows'])} | "
            f"{pct(r['win_rate'], signed=False)} | {pct(r['roi'])} | {pct(r['model_ev_roi'])} | {fnum(r['avg_ask'])} |"
        )
    return out


def table_wf(rows: pd.DataFrame) -> list[str]:
    out = [
        "| head | rows | days | win | ROI +2c | CI95 | model EV ROI +2c | avg ask |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if rows.empty:
        out.append("| NA | 0 | 0 | NA | NA | NA | NA | NA |")
        return out
    for _, r in rows.sort_values("head").iterrows():
        ci = r.get("roi_ci95") if isinstance(r.get("roi_ci95"), list) else [None, None]
        out.append(
            f"| `{r['head']}` | {int(r['rows'])} | {int(r['active_dates'])} | {pct(r['win_rate'], signed=False)} | "
            f"{pct(r['roi'])} | [{pct(ci[0])}, {pct(ci[1])}] | {pct(r['model_ev_roi'])} | {fnum(r['avg_ask'])} |"
        )
    return out


def write_markdown(payload: dict[str, Any], tables: dict[str, pd.DataFrame]) -> None:
    sc = payload["data_self_check"]
    gate = payload["clob_gate"]
    paired = payload["paired_city_date_summary"]
    fixed_slip = tables["fixed_holdout"][tables["fixed_holdout"]["slippage"].eq(0.02)].set_index("head")
    train_slip = tables["train_holdout"][tables["train_holdout"]["slippage"].eq(0.02)].set_index("head")

    def metric_line(table: pd.DataFrame, head: str) -> str:
        if head not in table.index:
            return "NA"
        r = table.loc[head]
        return f"ROI {pct(r['roi'])}, win {pct(r['win_rate'], signed=False)}, avg ask {fnum(r['avg_ask'])}, EV ROI {pct(r['model_ev_roi'])}"

    lines = [
        "# Current YES Peak-Forming vs Fade-Confirmed v1",
        "",
        "Status: research_only / no_live_change",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metrics:",
        "",
        "- `current_yes_peak_forming_ev`: buy current running-max bracket YES while `decline_c <= 0.1C`; EV is `sum(p_yes_win - executable_ask) / sum(executable_ask)` on time-aligned current-YES quotes.",
        "- `current_yes_fade_confirmed_ev`: buy current running-max bracket YES after visible fade (`decline_c >= 0.5C`); same EV formula and quote grain.",
        "",
        "Row grain: `city + target_date + decision_snapshot_ts_utc + decision_hour_local + current_bracket` current-YES quote. Same-day paired rows use the first peak-forming quote and the first later fade-confirmed quote for the same `city + target_date`.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db` self-check + `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` shared feature factory, collapsed to one current-YES state row per city/date/hour/current bracket.",
        f"- 数据快照时间: fact_built_at_utc `{sc['fact_trades_max_built_at_utc']}`; feature rows mtime `{payload['feature_rows_mtime_utc']}`.",
        f"- 记录行数: raw factory quote rows={payload['funnel']['raw_factory_quote_rows']}; state feature_rows={payload['funnel']['feature_rows']}; dropped unlabeled state rows={payload['funnel']['dropped_unlabeled_rows']}; usable current-YES rows={payload['funnel']['usable_rows']}; holdout usable rows={payload['funnel']['holdout_usable_rows']}.",
        f"- unsettled 占比: {payload['funnel']['unsettled_rows']} / {payload['funnel']['fact_trade_rows']} fact_trades rows.",
        f"- missing_bracket 数: {payload['funnel']['missing_bracket_rows']}.",
        f"- fact_trades trade_class: `{sc['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{sc['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{sc['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{sc['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}；本报告不发布 live_real PnL。",
        "",
        "## 人话结论",
        "",
        "结论：不要把 peak-forming 直接升级成主规则。迁到 shared feature factory 后，证据比旧 replay 更明确地偏向 fade-confirmed：fade-confirmed 是后续应继续研究的主 timing head；peak-forming 只保留一个很窄的 early shadow sleeve，用来验证 h13 附近是否能稳定拿到便宜价且不被二次升温吃掉。",
        "",
        f"固定规则下，peak-forming: {metric_line(fixed_slip, 'current_yes_peak_forming')}；fade-confirmed: {metric_line(fixed_slip, 'current_yes_fade_confirmed')}。",
        f"train 选规则再投 holdout 后，peak-forming: {metric_line(train_slip, 'current_yes_peak_forming')}；fade-confirmed: {metric_line(train_slip, 'current_yes_fade_confirmed')}。",
        "",
        f"同 city/date 配对里，早买平均省 {fnum(paired.get('avg_early_price_saving'))} 价格点，但只有 {paired.get('pairs')} 对，而且这几对 same-bracket rate 是 {pct(paired.get('same_bracket_rate'), signed=False)}；+2c 后 peak ROI {pct(paired.get('peak_roi_plus_2c'))}，fade ROI {pct(paired.get('fade_roi_plus_2c'))}。也就是说，早买确实能省钱，但当前 paired sample 主要是“没换档”的成功日，不能证明它已经覆盖了二次升温/后来换档风险。",
        "",
        "交易动作：继续 shadow，不改 N100/live。后续研究优先放在 `fade-confirmed` 的 execution freshness / ask slippage gate；`peak-forming` 只在 h13/edge 足够厚/fresh ask 未跳价时零 notional 记录；不要做 h14-h15 的宽 plateau 追单。",
        "",
        "## 数据漏斗",
        "",
        f"- raw factory quote rows: `{payload['funnel']['raw_factory_quote_rows']}`; collapsed current-YES state rows: `{payload['funnel']['feature_rows']}` from `{payload['funnel']['date_min']}` to `{payload['funnel']['date_max']}`。",
        f"- usable rows after ask/model/liquidity basics: `{payload['funnel']['usable_rows']}`。",
        f"- fixed peak-forming holdout rows: `{payload['funnel']['fixed_peak_holdout_rows']}`；fixed fade-confirmed holdout rows: `{payload['funnel']['fixed_fade_holdout_rows']}`。",
        f"- paired same city/date rows: `{paired.get('pairs')}` over `{paired.get('active_dates')}` active dates; average later gap `{fnum(paired.get('avg_hours_later'))}` hours; same-bracket rate `{pct(paired.get('same_bracket_rate'), signed=False)}`。",
        "",
        "## Fixed Interpretable Rules",
        "",
        *table_holdout(tables["fixed_holdout"], slip=0.02),
        "",
        "## Train -> Holdout Rule Selection",
        "",
        "Small grid only: decline state, local hour window, ask floor, and edge floor. Rules were selected on train dates before 2026-06-01, then evaluated on holdout.",
        "",
        *table_holdout(tables["train_holdout"], slip=0.02),
        "",
        "## Slippage Sensitivity",
        "",
        *table_slippage(tables["fixed_slippage"]),
        "",
        "## Prefix Walk-Forward",
        "",
        "Each target date chooses a rule from prior dates only, separately for the two heads.",
        "",
        *table_wf(tables["walkforward"]),
        "",
        "## Failure Reasons / Rules",
        "",
        "- Peak-forming's advantage is price: it can be cheaper before the market reprices after visible fade.",
        "- Peak-forming's failure mode is path risk: a later reheat can make the early current bracket stale, while a later fade entry may buy the updated current bracket.",
        "- Fade-confirmed's failure mode is execution: by the time the fade is visible, the current YES ask is often high and the edge is sensitive to 1-2c fresh-book slippage.",
        "- Forecast peak clock is still a necessary upstream field; fixed local h13 is only a proxy, not a deployable universal clock.",
        "",
        "## 三道门",
        "",
        "- significance=PARTIAL/LOW_SAMPLE：fixed fade-confirmed ROI 为正但 CI 下沿略低于 0；prefix walk-forward fade 的 CI 下沿刚过 0；peak-forming fixed holdout 为负且 CI 跨 0。",
        "- baseline=PARTIAL：comparison is against sibling timing head on the same current-YES expression, not a full zero-model or live fill baseline.",
        "- forward=PARTIAL：prefix walk-forward 支持 fade-confirmed 继续研究，但还不足以做 live timing switch。",
        "- conclusion=shadow_only：后续主线应研究 fade-confirmed 的 execution freshness gate；peak-forming 只保留 early shadow sleeve；禁止 live change。",
        "",
        "## Outputs",
        "",
        f"- Script: `{payload['outputs']['script']}`",
        f"- JSON: `{payload['outputs']['json']}`",
        f"- CSV: `{payload['outputs']['paired_rows']}`",
        f"- CSV: `{payload['outputs']['train_grid']}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_scored()
    usable = df[
        df["yes_current_ask"].ge(0.55)
        & df["available_notional_at_ask"].ge(5.0)
        & df["p_yes_win"].ge(0.50)
        & df["edge_snapshot"].ge(0.03)
        & df["decision_hour_local"].between(12, 17)
    ].copy()

    train_grid_df, chosen = choose_train_rules(df)
    fixed_holdout_df, fixed_frames = fixed_rule_summary(df)
    train_holdout_df, train_frames = evaluate_rules(df, chosen)
    fixed_slippage_df = slippage_table(fixed_frames)
    wf_summary_df, wf_decisions_df = prefix_walkforward(df)
    pairs_df, pair_summary = paired_city_date(df[df["period"].eq("holdout")].copy())

    paired_path = OUT_DIR / "paired_city_date_rows.csv"
    train_grid_path = OUT_DIR / "train_rule_grid.csv"
    fixed_holdout_path = OUT_DIR / "fixed_holdout.csv"
    train_holdout_path = OUT_DIR / "train_selected_holdout.csv"
    wf_path = OUT_DIR / "prefix_walkforward_summary.csv"
    wf_decisions_path = OUT_DIR / "prefix_walkforward_decisions.csv"
    pairs_df.to_csv(paired_path, index=False)
    train_grid_df.to_csv(train_grid_path, index=False)
    fixed_holdout_df.to_csv(fixed_holdout_path, index=False)
    train_holdout_df.to_csv(train_holdout_path, index=False)
    wf_summary_df.to_csv(wf_path, index=False)
    wf_decisions_df.to_csv(wf_decisions_path, index=False)

    self_check = data_self_check()
    settlement_counts = {r["settlement_status"]: r["rows"] for r in self_check["fact_trades_by_settlement_status"]}
    fact_trade_rows = sum(int(r["rows"]) for r in self_check["fact_trades_by_settlement_status"])
    unsettled_rows = int(settlement_counts.get("", 0))
    missing_bracket_rows = int(settlement_counts.get("missing_bracket", 0))
    feature_mtime = datetime.fromtimestamp(FEATURE_ROWS.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated_at_utc": now_utc(),
        "target_metrics": {
            "current_yes_peak_forming_ev": "sum(p_yes_win - executable_ask) / sum(executable_ask) where decline_c <= 0.1C",
            "current_yes_fade_confirmed_ev": "sum(p_yes_win - executable_ask) / sum(executable_ask) where decline_c >= 0.5C",
        },
        "row_grain": "city + target_date + decision_snapshot_ts_utc + decision_hour_local + current_bracket current-YES quote",
        "feature_rows_mtime_utc": feature_mtime,
        "data_self_check": self_check,
        "clob_gate": load_gate(),
        "funnel": {
            "raw_factory_quote_rows": int(df.attrs.get("raw_factory_rows", len(df))),
            "feature_rows": int(len(df)),
            "dropped_unlabeled_rows": int(df.attrs.get("dropped_unlabeled_rows", 0)),
            "usable_rows": int(len(usable)),
            "holdout_usable_rows": int(usable["period"].eq("holdout").sum()),
            "date_min": str(df["target_date"].min()),
            "date_max": str(df["target_date"].max()),
            "fact_trade_rows": fact_trade_rows,
            "unsettled_rows": unsettled_rows,
            "missing_bracket_rows": missing_bracket_rows,
            "fixed_peak_holdout_rows": int(len(fixed_frames.get("current_yes_peak_forming", pd.DataFrame()))),
            "fixed_fade_holdout_rows": int(len(fixed_frames.get("current_yes_fade_confirmed", pd.DataFrame()))),
        },
        "chosen_train_rules": {head: rule.name for head, rule in chosen.items()},
        "fixed_holdout": fixed_holdout_df.to_dict(orient="records"),
        "train_selected_holdout": train_holdout_df.to_dict(orient="records"),
        "prefix_walkforward": wf_summary_df.to_dict(orient="records"),
        "paired_city_date_summary": pair_summary,
        "verdict": {
            "trading_action": "fade_first_shadow_only",
            "peak_forming": "shadow_only_narrow_h13_like_sleeve",
            "fade_confirmed": "preferred_main_timing_head_but_execution_sensitive",
            "live_change": False,
            "reason": "early price saving is real, but forward and paired evidence are not stable enough to prove it covers extra reheat/path risk",
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "paired_rows": str(paired_path.relative_to(ROOT)),
            "train_grid": str(train_grid_path.relative_to(ROOT)),
            "fixed_holdout": str(fixed_holdout_path.relative_to(ROOT)),
            "train_holdout": str(train_holdout_path.relative_to(ROOT)),
            "walkforward_summary": str(wf_path.relative_to(ROOT)),
            "walkforward_decisions": str(wf_decisions_path.relative_to(ROOT)),
        },
    }

    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, {
        "fixed_holdout": fixed_holdout_df,
        "train_holdout": train_holdout_df,
        "fixed_slippage": fixed_slippage_df,
        "walkforward": wf_summary_df,
    })
    print(json.dumps(json_ready({
        "funnel": payload["funnel"],
        "fixed_holdout_plus_2c": fixed_holdout_df[fixed_holdout_df["slippage"].eq(0.02)].to_dict(orient="records"),
        "train_selected_holdout_plus_2c": train_holdout_df[train_holdout_df["slippage"].eq(0.02)].to_dict(orient="records"),
        "prefix_walkforward": payload["prefix_walkforward"],
        "paired_city_date_summary": pair_summary,
        "verdict": payload["verdict"],
    }), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
