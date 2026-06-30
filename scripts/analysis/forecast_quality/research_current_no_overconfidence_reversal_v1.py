#!/usr/bin/env python3
"""Train-only search for fading overconfident current-bracket NO with current YES."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
EVENT_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1/peak_yes_timing_v1_event_rows.csv"
HIST_SUMMARY = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/city_model_error_summary.csv"
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_no_overconfidence_reversal_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-current-no-overconfidence-reversal-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-30-current-no-overconfidence-reversal-v1.json"

STAKE_USD = 5.0
TRAIN_END_EXCLUSIVE = "2026-06-13"
RECENT_START = "2026-06-21"
RNG_SEED = 20260630


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100 * v:+.1f}%"


def money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"${v:+.2f}"


def fmt(x: Any, digits: int = 2) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{v:.{digits}f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    pct_cols = {"win_rate", "roi", "roi_ci_low", "roi_ci_high", "baseline_roi", "excess_roi", "excess_ci_low", "excess_ci_high"}
    money_cols = {"cost", "pnl", "max_daily_loss", "daily_p10_pnl", "daily_p90_pnl", "baseline_pnl", "excess_pnl"}
    for _, r in df.head(max_rows).iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif (
                key in pct_cols
                or key.endswith("_roi")
                or "roi_ci" in key
                or "excess_ci" in key
                or "win_rate" in key
            ):
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl"):
                vals.append(money(val))
            elif key in {"avg_current_yes_ask", "avg_current_no_ask", "avg_forecast_error", "avg_forecast_gap"}:
                vals.append(fmt(val))
            elif isinstance(val, float):
                vals.append(fmt(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def classify_bias(row: pd.Series) -> str:
    bias = float(row["bias"])
    p90 = float(row["p90"])
    p10 = float(row["p10"])
    hot = float(row["pct_actual_ge_forecast_plus_1"])
    cold = float(row["pct_forecast_ge_actual_plus_1"])
    mae = float(row["mae"])
    if bias >= 0.7 and hot >= 0.40 and cold <= 0.15:
        return "hot_underforecast_clean"
    if bias >= 0.5 and p90 >= 2.0 and hot >= 0.35:
        return "hot_underforecast_noisy"
    if bias <= -0.5 and cold >= 0.35 and hot <= 0.20:
        return "cold_overforecast_clean"
    if cold >= 0.25 and p10 <= -1.5:
        return "cold_overforecast_noisy"
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return "balanced_tight"
    if hot >= 0.25 and cold >= 0.20:
        return "two_sided_noisy"
    return "mild_or_mixed"


def forecast_model_from_row(row: pd.Series) -> str:
    source = str(row.get("forecast_source") or "").lower()
    clock = str(row.get("forecast_clock_source") or "").lower()
    if "ecmwf" in source or "ecmwf" in clock:
        return "ecmwf"
    if "gfs" in source or "gfs" in clock:
        return "gfs"
    return "unknown"


def load_db_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "db_path": str(DB_PATH.relative_to(ROOT)),
        "db_mtime": pd.Timestamp(DB_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat() if DB_PATH.exists() else None,
        "gate_pass": None,
    }
    if GATE_JSON.exists():
        gate = json.loads(GATE_JSON.read_text())
        out["gate_pass"] = bool(gate.get("gate_pass"))
        out["live_real_fill_ids"] = gate.get("fact_trades_live_real", {}).get("fill_ids")
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        out["fact_trades_rows"] = conn.execute("SELECT COUNT(*) FROM fact_trades").fetchone()[0]
        out["fact_signal_candidates_rows"] = conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0]
        out["fact_built_at_utc"] = conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
        settlement = conn.execute("SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status").fetchall()
        out["settlement_status_counts"] = {str(k or "NULL"): v for k, v in settlement}
        conn.close()
    return out


def load_rows() -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_error_native",
        "current_bracket",
        "final_winning_bracket",
        "current_bracket_held",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "composite_regime",
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "period",
        "rule",
        "event_key",
    ]
    df = pd.read_csv(EVENT_ROWS, usecols=cols, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["row_forecast_model"] = df.apply(forecast_model_from_row, axis=1)

    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_source_bias_regime"] = hist.apply(classify_bias, axis=1)
    hist["tail_skew"] = hist["pct_actual_ge_forecast_plus_1"] - hist["pct_forecast_ge_actual_plus_1"]
    lookup = hist[["city", "model", "bias", "mae", "p10", "p50", "p90", "tail_skew", "row_source_bias_regime"]]
    df = df.merge(lookup, left_on=["city", "row_forecast_model"], right_on=["city", "model"], how="left")
    df["row_source_bias_regime"] = df["row_source_bias_regime"].fillna("unclassified")

    for col in ["current_yes_ask", "current_yes_payoff", "current_bracket_no_ask", "current_bracket_no_payoff"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    valid = (
        df["current_yes_ask"].gt(0)
        & df["current_yes_ask"].lt(1.0)
        & df["current_bracket_no_ask"].gt(0)
        & df["current_bracket_no_ask"].lt(1.0)
        & df["current_yes_payoff"].isin([0.0, 1.0])
        & df["current_bracket_no_payoff"].isin([0.0, 1.0])
    )
    df = df[valid].copy()
    df["cost"] = STAKE_USD
    df["pnl"] = df["current_yes_payoff"] * (STAKE_USD / df["current_yes_ask"]) - STAKE_USD
    df["roi_row"] = df["pnl"] / df["cost"]
    df["baseline_cost"] = STAKE_USD
    df["baseline_pnl"] = df["current_bracket_no_payoff"] * (STAKE_USD / df["current_bracket_no_ask"]) - STAKE_USD
    df["winner_current"] = df["current_yes_payoff"].eq(1.0)
    return df


def bootstrap_roi_ci(df: pd.DataFrame, pnl_col: str = "pnl", cost_col: str = "cost", n_boot: int = 2000) -> tuple[float, float]:
    daily = df.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = costs[idx].sum()
        if cost:
            vals.append(pnls[idx].sum() / cost)
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def bootstrap_excess_ci(df: pd.DataFrame, n_boot: int = 2000) -> tuple[float, float]:
    daily = df.groupby("target_date", as_index=False).agg(
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        baseline_cost=("baseline_cost", "sum"),
        baseline_pnl=("baseline_pnl", "sum"),
    )
    if len(daily) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RNG_SEED)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        d = daily.iloc[idx]
        roi = d["pnl"].sum() / d["cost"].sum()
        base = d["baseline_pnl"].sum() / d["baseline_cost"].sum()
        vals.append(roi - base)
    return tuple(np.quantile(vals, [0.025, 0.975]).tolist())


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": float("nan"),
            "avg_current_yes_ask": float("nan"),
            "avg_current_no_ask": float("nan"),
            "cost": 0.0,
            "pnl": 0.0,
            "roi": float("nan"),
            "baseline_pnl": 0.0,
            "baseline_roi": float("nan"),
            "excess_roi": float("nan"),
        }
    cost = df["cost"].sum()
    pnl = df["pnl"].sum()
    baseline_cost = df["baseline_cost"].sum()
    baseline_pnl = df["baseline_pnl"].sum()
    ci = bootstrap_roi_ci(df)
    ex_ci = bootstrap_excess_ci(df)
    daily = df.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    return {
        "rows": int(len(df)),
        "dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "win_rate": float(df["current_yes_payoff"].mean()),
        "avg_current_yes_ask": float(df["current_yes_ask"].mean()),
        "avg_current_no_ask": float(df["current_bracket_no_ask"].mean()),
        "avg_forecast_error": float(pd.to_numeric(df["forecast_error_native"], errors="coerce").mean()),
        "avg_forecast_gap": float(pd.to_numeric(df["forecast_gap_to_running_native"], errors="coerce").mean()),
        "cost": float(cost),
        "pnl": float(pnl),
        "roi": float(pnl / cost) if cost else float("nan"),
        "roi_ci_low": float(ci[0]),
        "roi_ci_high": float(ci[1]),
        "baseline_pnl": float(baseline_pnl),
        "baseline_roi": float(baseline_pnl / baseline_cost) if baseline_cost else float("nan"),
        "excess_pnl": float(pnl - baseline_pnl),
        "excess_roi": float((pnl / cost) - (baseline_pnl / baseline_cost)) if cost and baseline_cost else float("nan"),
        "excess_ci_low": float(ex_ci[0]),
        "excess_ci_high": float(ex_ci[1]),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.5).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
        "daily_p10_pnl": float(daily["pnl"].quantile(0.10)),
        "daily_p90_pnl": float(daily["pnl"].quantile(0.90)),
    }


def candidate_masks(df: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    candidates: list[tuple[str, pd.Series]] = []
    no_thresholds = [0.70, 0.75, 0.80, 0.85, 0.90]
    yes_maxes = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.98]
    for no_min in no_thresholds:
        no_mask = df["current_bracket_no_ask"].ge(no_min)
        for yes_max in yes_maxes:
            base = no_mask & df["current_yes_ask"].le(yes_max)
            candidates.append((f"no_ask>={no_min:.2f} & yes_ask<={yes_max:.2f}", base))

            for col in [
                "day_regime",
                "intraday_state",
                "moisture_cloud_regime",
                "wind_regime",
                "row_source_bias_regime",
            ]:
                counts = df.loc[base, col].value_counts(dropna=False)
                for val, n in counts.items():
                    if n >= 10:
                        label = f"no_ask>={no_min:.2f} & yes_ask<={yes_max:.2f} & {col}={val}"
                        candidates.append((label, base & df[col].eq(val)))

            numeric_specs = [
                ("forecast_error_native", "<=", [-3, -2, -1, 0, 1, 2]),
                ("forecast_error_native", ">=", [-2, -1, 0, 1, 2, 3]),
                ("forecast_gap_to_running_native", "<=", [-2, -1, 0, 1, 2]),
                ("forecast_gap_to_running_native", ">=", [-1, 0, 1, 2, 3]),
                ("tail_skew", ">=", [0.2, 0.4, 0.6]),
                ("tail_skew", "<=", [-0.2, -0.4]),
            ]
            for col, op, vals in numeric_specs:
                s = pd.to_numeric(df[col], errors="coerce")
                for val in vals:
                    mask = base & (s <= val if op == "<=" else s >= val)
                    candidates.append((f"no_ask>={no_min:.2f} & yes_ask<={yes_max:.2f} & {col}{op}{val}", mask))
    return candidates


def evaluate_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = df[df["target_date"] < TRAIN_END_EXCLUSIVE].copy()
    holdout = df[df["target_date"] >= TRAIN_END_EXCLUSIVE].copy()
    recent = df[df["target_date"] >= RECENT_START].copy()
    records = []
    for rule, mask_all in candidate_masks(df):
        mask_train = mask_all.loc[train.index]
        g_train = train[mask_train].copy()
        if len(g_train) < 20 or g_train["target_date"].nunique() < 6:
            continue
        train_s = summarize(g_train)
        if train_s["roi"] <= 0:
            continue
        mask_hold = mask_all.loc[holdout.index]
        mask_recent = mask_all.loc[recent.index]
        hold_s = summarize(holdout[mask_hold])
        recent_s = summarize(recent[mask_recent])
        rec: dict[str, Any] = {"rule": rule}
        for prefix, s in [("train", train_s), ("holdout", hold_s), ("recent", recent_s)]:
            for k, v in s.items():
                rec[f"{prefix}_{k}"] = v
        records.append(rec)
    out = pd.DataFrame(records)
    if out.empty:
        return out, pd.DataFrame()
    out["passes_train_ci"] = out["train_roi_ci_low"] > 0
    out["passes_holdout_positive"] = (out["holdout_rows"] >= 10) & (out["holdout_dates"] >= 3) & (out["holdout_roi"] > 0)
    out["passes_recent_positive"] = (out["recent_rows"] == 0) | (out["recent_roi"] > 0)
    out["score"] = (
        out["passes_train_ci"].astype(int) * 3
        + out["passes_holdout_positive"].astype(int) * 3
        + out["passes_recent_positive"].astype(int)
        + out["holdout_roi"].fillna(-9)
        + 0.1 * np.log1p(out["train_rows"])
    )
    out = out.sort_values(["score", "holdout_roi", "train_rows"], ascending=False)
    dedup = out.drop_duplicates(
        ["train_rows", "holdout_rows", "train_roi", "holdout_roi", "recent_roi"], keep="first"
    )
    return out, dedup


def rule_mask(df: pd.DataFrame, rule: str) -> pd.Series:
    masks = dict(candidate_masks(df))
    return masks[rule]


def apply_top_rules(df: pd.DataFrame, rules: pd.DataFrame, top_n: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    daily_frames = []
    for _, r in rules.head(top_n).iterrows():
        rule = str(r["rule"])
        g = df[rule_mask(df, rule)].copy()
        s = summarize(g)
        rec = {"rule": rule, **s}
        rows.append(rec)
        daily = g.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
        daily["roi"] = daily["pnl"] / daily["cost"]
        daily["rule"] = rule
        daily_frames.append(daily)
    return pd.DataFrame(rows), pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()


def slice_summary(df: pd.DataFrame, rule: str) -> dict[str, pd.DataFrame]:
    g = df[rule_mask(df, rule)].copy()
    out = {}
    for col in ["city", "day_regime", "intraday_state", "moisture_cloud_regime", "wind_regime", "row_source_bias_regime"]:
        rows = []
        for val, gg in g.groupby(col, dropna=False):
            rows.append({col: val, **summarize(gg)})
        out[col] = pd.DataFrame(rows).sort_values(["pnl", "rows"], ascending=False) if rows else pd.DataFrame()
    return out


def render_report(
    db: dict[str, Any],
    all_rows: pd.DataFrame,
    candidates: pd.DataFrame,
    top_full: pd.DataFrame,
    top_daily: pd.DataFrame,
    slices: dict[str, pd.DataFrame],
) -> str:
    best = candidates.iloc[0] if not candidates.empty else None
    if best is not None:
        significance_pass = bool(best["passes_train_ci"])
        baseline_pass = bool(best["train_excess_ci_low"] > 0)
        forward_pass = bool(best["holdout_roi_ci_low"] > 0 and best["holdout_excess_ci_low"] > 0)
        shadow_candidate = bool(significance_pass and baseline_pass and best["holdout_roi"] > 0)
        verdict_sentence = (
            f"当前最好 train-only 规则 `{best['rule']}`：train ROI {pct(best['train_roi'])} "
            f"CI {pct(best['train_roi_ci_low'])}..{pct(best['train_roi_ci_high'])}，"
            f"train excess vs same-row current-NO {pct(best['train_excess_roi'])} "
            f"CI {pct(best['train_excess_ci_low'])}..{pct(best['train_excess_ci_high'])}；"
            f"holdout ROI {pct(best['holdout_roi'])} 但 CI {pct(best['holdout_roi_ci_low'])}..{pct(best['holdout_roi_ci_high'])}，"
            f"recent ROI {pct(best['recent_roi'])}。"
        )
        conclusion = (
            "结论暂定 `shadow_candidate_keep_collecting`：不是 live-ready，但这是目前比 naive selector 更像样的方向。"
            "规则只在 train 选择，train 显著且相对同点 current-NO baseline 显著，holdout/recent 点估同号；"
            "但 holdout CI 跨 0，必须先做 zero-notional forward telemetry 和 fresh-book/depth 验证。"
            if shadow_candidate
            else "结论 `inconclusive`：train 显著性、baseline 或 holdout 同号没同时满足，继续找下一方向。"
        )
    else:
        significance_pass = False
        baseline_pass = False
        forward_pass = False
        shadow_candidate = False
        verdict_sentence = "没有找到满足最小 train support 且 train ROI 为正的规则。"
        conclusion = "结论 `inconclusive`。"

    return "\n".join(
        [
            "# Current-NO Overconfidence Reversal v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            verdict_sentence,
            "",
            conclusion,
            "",
            "significance="
            + ("PASS" if significance_pass else "FAIL")
            + " baseline="
            + ("PASS" if baseline_pass else "FAIL")
            + " forward="
            + ("PASS" if forward_pass else "FAIL")
            + " conclusion="
            + ("shadow_candidate" if shadow_candidate else "inconclusive"),
            "",
            "## 数据快照",
            "",
            f"- 数据源：`{db.get('db_path')}` 自检 + generated current YES/current NO expression matrix。",
            f"- 数据快照时间：DB mtime `{db.get('db_mtime')}`, fact_built_at_utc `{db.get('fact_built_at_utc')}`。",
            f"- 记录行数：fact_trades={db.get('fact_trades_rows')}, fact_signal_candidates={db.get('fact_signal_candidates_rows')}, current-pair rows={len(all_rows)}。",
            f"- unsettled 占比：fact_trades NULL/unsettled-like={db.get('settlement_status_counts', {}).get('NULL', 0)} / {db.get('fact_trades_rows')}。",
            f"- missing_bracket 数：settlement_status_counts={db.get('settlement_status_counts')}; CLOB gate_pass={db.get('gate_pass')}。",
            "",
            "## Setup",
            "",
            "- Thesis: when `current_bracket_no_ask` is high, market is confident final max will not remain in current bracket. Fade that overconfidence by buying `current_high_yes` only when train-selected conflict features say current can still hold.",
            f"- Train: target_date < `{TRAIN_END_EXCLUSIVE}`. Holdout: target_date >= `{TRAIN_END_EXCLUSIVE}`. Recent stress: target_date >= `{RECENT_START}`.",
            "- Baseline column in this report is the opposite high current-bracket NO leg on the same rows. The primary question is whether buying YES is positive and holds out.",
            "",
            "## Candidate Rules",
            "",
            md_table(
                candidates,
                [
                    ("rule", "rule"),
                    ("train_rows", "train rows"),
                    ("train_dates", "train dates"),
                    ("train_cities", "cities"),
                    ("train_avg_current_yes_ask", "train YES ask"),
                    ("train_avg_current_no_ask", "train NO ask"),
                    ("train_win_rate", "train win"),
                    ("train_roi", "train ROI"),
                    ("train_roi_ci_low", "CI low"),
                    ("train_roi_ci_high", "CI high"),
                    ("holdout_rows", "holdout rows"),
                    ("holdout_dates", "holdout dates"),
                    ("holdout_roi", "holdout ROI"),
                    ("recent_rows", "recent rows"),
                    ("recent_roi", "recent ROI"),
                ],
                max_rows=30,
            ),
            "",
            "## Top Rules Full-Window Shape",
            "",
            md_table(
                top_full,
                [
                    ("rule", "rule"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("avg_current_yes_ask", "YES ask"),
                    ("avg_current_no_ask", "NO ask"),
                    ("win_rate", "win"),
                    ("roi", "ROI"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                    ("losing_days", "losing days"),
                    ("max_daily_loss", "max loss"),
                ],
                max_rows=10,
            ),
            "",
            "## Best Rule Daily",
            "",
            md_table(
                top_daily[top_daily["rule"].eq(str(best["rule"]))] if best is not None and not top_daily.empty else pd.DataFrame(),
                [
                    ("target_date", "date"),
                    ("rows", "rows"),
                    ("cost", "cost"),
                    ("pnl", "pnl"),
                    ("roi", "ROI"),
                ],
                max_rows=80,
            ),
            "",
            "## Best Rule Contribution",
            "",
            "By city:",
            "",
            md_table(
                slices.get("city", pd.DataFrame()),
                [
                    ("city", "city"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("avg_current_yes_ask", "YES ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                    ("roi_ci_low", "CI low"),
                    ("roi_ci_high", "CI high"),
                ],
                max_rows=30,
            ),
            "",
            "By regime:",
            "",
            md_table(
                slices.get("day_regime", pd.DataFrame()),
                [
                    ("day_regime", "day"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("win_rate", "win"),
                    ("avg_current_yes_ask", "YES ask"),
                    ("roi", "ROI"),
                    ("pnl", "pnl"),
                ],
                max_rows=20,
            ),
            "",
            "## Interpretation",
            "",
            "This is a plausible direction only if the top train-selected rule survives holdout. It is still not a live rule because it uses replay asks, no fresh-book depth, and repeated city-hour states. The next implementation step would be zero-notional forward telemetry for the exact rule fields, not attaching it to the current runner.",
            "",
            "## Artifacts",
            "",
            f"- Script: `{Path('scripts/analysis/forecast_quality/research_current_no_overconfidence_reversal_v1.py')}`",
            f"- JSON summary: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Candidate rules: `{(OUT_DIR / 'candidate_rules.csv').relative_to(ROOT)}`",
            f"- Top rule daily: `{(OUT_DIR / 'top_rule_daily.csv').relative_to(ROOT)}`",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db_snapshot()
    rows = load_rows()
    candidates, dedup = evaluate_rules(rows)
    top_full, top_daily = apply_top_rules(rows, dedup, top_n=5) if not dedup.empty else (pd.DataFrame(), pd.DataFrame())
    best_rule = str(dedup.iloc[0]["rule"]) if not dedup.empty else ""
    slices = slice_summary(rows, best_rule) if best_rule else {}

    rows.to_csv(OUT_DIR / "current_pair_rows.csv", index=False)
    candidates.to_csv(OUT_DIR / "candidate_rules_all.csv", index=False)
    dedup.to_csv(OUT_DIR / "candidate_rules.csv", index=False)
    top_full.to_csv(OUT_DIR / "top_rules_full_window.csv", index=False)
    top_daily.to_csv(OUT_DIR / "top_rule_daily.csv", index=False)
    for name, df in slices.items():
        df.to_csv(OUT_DIR / f"best_rule_by_{name}.csv", index=False)

    best = dedup.iloc[0].to_dict() if not dedup.empty else {}
    summary = {
        "generated_at": "2026-06-30",
        "head": "current_no_overconfidence_reversal_v1",
        "db_snapshot": db,
        "train_end_exclusive": TRAIN_END_EXCLUSIVE,
        "recent_start": RECENT_START,
        "rows": int(len(rows)),
        "candidate_rules": int(len(dedup)),
        "best_rule": best,
        "conclusion": (
            "shadow_candidate"
            if best and best.get("passes_train_ci") and best.get("passes_holdout_positive")
            else "inconclusive"
        ),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_report(db, rows, dedup, top_full, top_daily, slices), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
