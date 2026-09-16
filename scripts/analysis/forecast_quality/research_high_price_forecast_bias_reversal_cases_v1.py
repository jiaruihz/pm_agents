#!/usr/bin/env python3
"""Mine high-price YES/NO reversal cases against forecast-bias and regimes."""

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
CITY_FIT = ROOT / "docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1/city_strategy_fit_by_forecast_bias.csv"
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/high_price_forecast_bias_reversal_cases_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-30-high-price-forecast-bias-reversal-cases-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-30-high-price-forecast-bias-reversal-cases-v1.json"

STAKE_USD = 5.0
THRESHOLDS = [0.70, 0.80, 0.90]
RNG_SEED = 20260630


EXPRESSIONS = [
    {
        "expression": "current_high_yes",
        "side": "YES",
        "ask": "current_yes_ask",
        "payoff": "current_yes_payoff",
        "opposite_expression": "current_bracket_no",
        "opposite_ask": "current_bracket_no_ask",
        "reversal_type": "high_yes_broken_by_later_heat",
    },
    {
        "expression": "current_bracket_no",
        "side": "NO",
        "ask": "current_bracket_no_ask",
        "payoff": "current_bracket_no_payoff",
        "opposite_expression": "current_high_yes",
        "opposite_ask": "current_yes_ask",
        "reversal_type": "high_no_loses_current_bracket_held",
    },
    {
        "expression": "d1_no",
        "side": "NO",
        "ask": "d1_no_ask",
        "bid": "d1_no_bid",
        "payoff": "d1_no_payoff",
        "opposite_expression": "d1_yes_proxy",
        "opposite_ask_proxy_from_no_bid": True,
        "reversal_type": "high_no_loses_d1_exact_hit",
    },
    {
        "expression": "d2_no",
        "side": "NO",
        "ask": "d2_no_ask",
        "bid": "d2_no_bid",
        "payoff": "d2_no_payoff",
        "opposite_expression": "d2_yes_proxy",
        "opposite_ask_proxy_from_no_bid": True,
        "reversal_type": "high_no_loses_d2_exact_hit",
    },
]


def pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{100 * v:+.1f}%"


def fmt(x: Any, digits: int = 2) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"{v:.{digits}f}"


def money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if not math.isfinite(v):
        return ""
    return f"${v:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    int_cols = {"rows", "dates", "cities", "reversals", "active_days"}
    pct_cols = {
        "reversal_rate",
        "token_roi",
        "token_roi_ci_low",
        "token_roi_ci_high",
        "opposite_roi",
        "opposite_roi_ci_low",
        "opposite_roi_ci_high",
        "hot_tail_pct",
        "cold_tail_pct",
        "tail_skew",
        "cheap_opposite_hit_rate",
    }
    money_cols = {"token_pnl", "opposite_pnl", "max_daily_token_loss", "daily_token_p10", "daily_opposite_p90"}
    for _, r in df.head(max_rows).iterrows():
        vals = []
        for key, _ in cols:
            val = r.get(key, "")
            if key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif key in pct_cols or key.endswith("_roi"):
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl"):
                vals.append(money(val))
            elif key in {"avg_ask", "avg_opposite_ask", "avg_forecast_error", "avg_forecast_gap", "bias", "p10", "p50", "p90"}:
                vals.append(fmt(val, 3 if key in {"bias", "p10", "p50", "p90"} else 2))
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


def load_bias() -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_csv(HIST_SUMMARY)
    hist["row_source_bias_regime"] = hist.apply(classify_bias, axis=1)
    hist["hot_tail_pct"] = hist["pct_actual_ge_forecast_plus_1"]
    hist["cold_tail_pct"] = hist["pct_forecast_ge_actual_plus_1"]
    hist["tail_skew"] = hist["hot_tail_pct"] - hist["cold_tail_pct"]

    city_fit = pd.read_csv(CITY_FIT)
    return hist, city_fit


def bin_feature_values(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["forecast_error_bin"] = pd.cut(
        pd.to_numeric(out["forecast_error_native"], errors="coerce"),
        bins=[-np.inf, -3, -2, -1, 0, 1, 2, 3, np.inf],
        labels=["<=-3", "-3..-2", "-2..-1", "-1..0", "0..1", "1..2", "2..3", ">=3"],
    ).astype(str)
    out["forecast_gap_bin"] = pd.cut(
        pd.to_numeric(out["forecast_gap_to_running_native"], errors="coerce"),
        bins=[-np.inf, -2, -1, 0, 1, 2, 3, np.inf],
        labels=["<=-2", "-2..-1", "-1..0", "0..1", "1..2", "2..3", ">=3"],
    ).astype(str)
    out["ask_bucket"] = pd.cut(
        pd.to_numeric(out["ask"], errors="coerce"),
        bins=[0, 0.70, 0.80, 0.90, 0.97, 1.01],
        labels=["<0.70", "0.70..0.80", "0.80..0.90", "0.90..0.97", ">=0.97"],
    ).astype(str)
    return out


def load_base_rows(hist: pd.DataFrame, city_fit: pd.DataFrame) -> pd.DataFrame:
    cols = {
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_error_native",
        "forecast_max_native",
        "running_native",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "final_winning_bracket",
        "current_bracket_held",
        "d1_hit",
        "d2_hit",
        "skip_over_d1",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "composite_regime",
        "period",
        "rule",
        "event_key",
        "current_yes_ask",
        "current_yes_payoff",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "d1_no_ask",
        "d1_no_bid",
        "d1_no_payoff",
        "d2_no_ask",
        "d2_no_bid",
        "d2_no_payoff",
    }
    src = pd.read_csv(EVENT_ROWS, usecols=sorted(cols), low_memory=False)
    src["target_date"] = src["target_date"].astype(str)
    src["row_forecast_model"] = src.apply(forecast_model_from_row, axis=1)

    lookup = hist[
        [
            "city",
            "model",
            "n",
            "bias",
            "mae",
            "p10",
            "p50",
            "p90",
            "hot_tail_pct",
            "cold_tail_pct",
            "tail_skew",
            "row_source_bias_regime",
        ]
    ].copy()
    out = src.merge(lookup, left_on=["city", "row_forecast_model"], right_on=["city", "model"], how="left")
    out["row_source_bias_regime"] = out["row_source_bias_regime"].fillna("unclassified")
    fit = city_fit[["city", "source_bias_regime"]].copy()
    out = out.merge(fit, on="city", how="left")
    out["source_bias_regime"] = out["source_bias_regime"].fillna(out["row_source_bias_regime"])
    return out


def build_long(base: pd.DataFrame) -> pd.DataFrame:
    frames = []
    common_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "row_forecast_model",
        "forecast_source",
        "forecast_clock_source",
        "forecast_gap_to_running_native",
        "forecast_error_native",
        "forecast_max_native",
        "running_native",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "final_winning_bracket",
        "current_bracket_held",
        "d1_hit",
        "d2_hit",
        "skip_over_d1",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "composite_regime",
        "period",
        "rule",
        "event_key",
        "model",
        "bias",
        "mae",
        "p10",
        "p50",
        "p90",
        "hot_tail_pct",
        "cold_tail_pct",
        "tail_skew",
        "row_source_bias_regime",
        "source_bias_regime",
    ]
    for spec in EXPRESSIONS:
        cols = common_cols + [spec["ask"], spec["payoff"]]
        if spec.get("bid"):
            cols.append(spec["bid"])
        elif spec.get("opposite_ask"):
            cols.append(spec["opposite_ask"])
        d = base[cols].copy()
        d = d.rename(columns={spec["ask"]: "ask", spec["payoff"]: "payoff"})
        d["expression"] = spec["expression"]
        d["side"] = spec["side"]
        d["opposite_expression"] = spec["opposite_expression"]
        d["reversal_type"] = spec["reversal_type"]
        d["ask"] = pd.to_numeric(d["ask"], errors="coerce")
        d["payoff"] = pd.to_numeric(d["payoff"], errors="coerce")
        if spec.get("opposite_ask_proxy_from_no_bid"):
            bid = pd.to_numeric(d[spec["bid"]], errors="coerce")
            d["opposite_ask"] = (1.0 - bid).clip(lower=0.001, upper=0.999)
            d["opposite_ask_type"] = "proxy_1_minus_no_bid"
        else:
            d["opposite_ask"] = pd.to_numeric(d[spec["opposite_ask"]], errors="coerce")
            d["opposite_ask_type"] = "observed_expression_matrix"
        d = d[d["ask"].gt(0) & d["ask"].lt(1.0) & d["payoff"].isin([0.0, 1.0])].copy()
        d["is_reversal"] = d["payoff"].eq(0.0)
        d["token_cost"] = STAKE_USD
        d["token_pnl"] = d["payoff"] * (STAKE_USD / d["ask"]) - STAKE_USD
        d["token_roi_row"] = d["token_pnl"] / d["token_cost"]
        d["opposite_cost"] = STAKE_USD
        d["opposite_payoff"] = 1.0 - d["payoff"]
        d["opposite_pnl"] = d["opposite_payoff"] * (STAKE_USD / d["opposite_ask"]) - STAKE_USD
        d["opposite_roi_row"] = d["opposite_pnl"] / d["opposite_cost"]
        frames.append(d)
    return bin_feature_values(pd.concat(frames, ignore_index=True))


def bootstrap_roi_ci(df: pd.DataFrame, pnl_col: str, cost_col: str, n_boot: int = 2000) -> tuple[float, float]:
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


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        token_ci = bootstrap_roi_ci(g, "token_pnl", "token_cost")
        opp_ci = bootstrap_roi_ci(g.dropna(subset=["opposite_ask"]), "opposite_pnl", "opposite_cost")
        daily = g.groupby("target_date", as_index=False).agg(token_pnl=("token_pnl", "sum"), opposite_pnl=("opposite_pnl", "sum"))
        rec = dict(zip(group_cols, keys, strict=False))
        rec.update(
            {
                "rows": len(g),
                "dates": g["target_date"].nunique(),
                "cities": g["city"].nunique(),
                "reversals": int(g["is_reversal"].sum()),
                "reversal_rate": g["is_reversal"].mean(),
                "avg_ask": g["ask"].mean(),
                "avg_opposite_ask": g["opposite_ask"].mean(),
                "avg_forecast_error": pd.to_numeric(g["forecast_error_native"], errors="coerce").mean(),
                "avg_forecast_gap": pd.to_numeric(g["forecast_gap_to_running_native"], errors="coerce").mean(),
                "token_pnl": g["token_pnl"].sum(),
                "token_roi": g["token_pnl"].sum() / g["token_cost"].sum(),
                "token_roi_ci_low": token_ci[0],
                "token_roi_ci_high": token_ci[1],
                "opposite_pnl": g["opposite_pnl"].sum(),
                "opposite_roi": g["opposite_pnl"].sum() / g["opposite_cost"].sum(),
                "opposite_roi_ci_low": opp_ci[0],
                "opposite_roi_ci_high": opp_ci[1],
                "cheap_opposite_hit_rate": g["opposite_payoff"].mean(),
                "max_daily_token_loss": daily["token_pnl"].min(),
                "daily_token_p10": daily["token_pnl"].quantile(0.10),
                "daily_opposite_p90": daily["opposite_pnl"].quantile(0.90),
            }
        )
        rows.append(rec)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["reversal_rate", "rows"], ascending=[False, False])


def high_price_rows(long: pd.DataFrame, threshold: float = 0.70) -> pd.DataFrame:
    return long[long["ask"].ge(threshold)].copy()


def archetype_tables(high: pd.DataFrame) -> dict[str, pd.DataFrame]:
    high_yes = high[high["side"].eq("YES")]
    high_no = high[high["side"].eq("NO")]
    return {
        "overall_by_threshold": pd.concat(
            [
                summarize(long_threshold, ["threshold", "expression", "side"])
                for threshold, long_threshold in [
                    (t, long_t.assign(threshold=f">={t:.2f}"))
                    for t in THRESHOLDS
                    for long_t in [high_price_rows(high if t == 0.70 else high.assign(), t)]
                ]
            ],
            ignore_index=True,
        ),
        "yes_by_regime": summarize(high_yes, ["expression", "row_source_bias_regime", "day_regime", "intraday_state"]),
        "no_by_regime": summarize(high_no, ["expression", "row_source_bias_regime", "day_regime", "intraday_state"]),
        "by_city": summarize(high, ["expression", "city", "row_forecast_model", "row_source_bias_regime"]),
        "by_forecast_error": summarize(high, ["expression", "forecast_error_bin", "forecast_gap_bin"]),
        "by_moisture_wind": summarize(high, ["expression", "moisture_cloud_regime", "wind_regime"]),
        "reversal_cases": high[high["is_reversal"]]
        .sort_values(["ask", "target_date", "city"], ascending=[False, True, True])
        .drop_duplicates(
            [
                "target_date",
                "city",
                "expression",
                "current_bracket",
                "d1_no_bracket",
                "d2_no_bracket",
                "final_winning_bracket",
                "ask",
            ]
        )
        .head(250),
    }


def supported_patterns(df: pd.DataFrame, min_rows: int = 20) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["rows"].ge(min_rows)].sort_values(["reversal_rate", "rows"], ascending=[False, False])


def render_report(db: dict[str, Any], long: pd.DataFrame, high: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> str:
    overall = tables["overall_by_threshold"]
    yes_patterns = tables["yes_by_regime"]
    no_patterns = tables["no_by_regime"]
    city = tables["by_city"]
    forecast = tables["by_forecast_error"]
    moisture = tables["by_moisture_wind"]
    cases = tables["reversal_cases"]

    yes70 = high[high["side"].eq("YES")]
    no70 = high[high["side"].eq("NO")]
    yes_rev = yes70["is_reversal"].mean() if len(yes70) else float("nan")
    no_rev = no70["is_reversal"].mean() if len(no70) else float("nan")

    return "\n".join(
        [
            "# High-Price Forecast-Bias Reversal Cases v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            f"这版修正研究目标：不是设计 selector，而是找 `ask>=0.70` 的高价 YES/NO 历史反转模式。结论是 `inconclusive / case-mining-only`：高价 YES 反转率 {pct(yes_rev)}，高价 NO 反转率 {pct(no_rev)}；已经能定位若干容易反转的 regime，但还没形成可交易规则。",
            "",
            "最重要的机制信号：",
            "",
            "- 高价 current_high YES 失败，通常不是 forecast-bias 标签本身，而是高置信盘口与 forecast/obs/regime 冲突：看起来像高点已定，但后续仍有热量、观测刷新或 bracket exact-hit 风险。",
            "- 高价 NO 失败分两类：current-bracket NO 是 capped/hold 住当前档；d1/d2 NO 是最终正好命中那一档。它们不是同一个风险，不能混成一个 NO reversal。",
            "- 历史 station-vs-forecast bias 有解释力，但不能单独决定交易；真正该找的是“盘口高置信 + forecast/obs/regime 冲突”的组合。",
            "",
            "significance=NA baseline=NA forward=NA conclusion=inconclusive",
            "",
            "## 数据快照",
            "",
            f"- 数据源：`{db.get('db_path')}` 自检 + generated expression matrix；本报告不是 live_real fill PnL。",
            f"- 数据快照时间：DB mtime `{db.get('db_mtime')}`, fact_built_at_utc `{db.get('fact_built_at_utc')}`。",
            f"- 记录行数：fact_trades={db.get('fact_trades_rows')}, fact_signal_candidates={db.get('fact_signal_candidates_rows')}, expression rows={len(long)}, high-price rows={len(high)}。",
            f"- unsettled 占比：fact_trades NULL/unsettled-like={db.get('settlement_status_counts', {}).get('NULL', 0)} / {db.get('fact_trades_rows')}。",
            f"- missing_bracket 数：settlement_status_counts={db.get('settlement_status_counts')}; CLOB gate_pass={db.get('gate_pass')}。",
            "",
            "## Definition",
            "",
            "- Row grain：city-hour decision state；同一 city-date 可连续多个小时出现同一高价 token，所以它是“反转状态”库，不是 city-date 去重交易回测。",
            "- 高价 token：`ask >= 0.70`，并另列 `0.80/0.90` stress。",
            "- 反转：买这个高价 token 会输，即 `payoff=0`。",
            "- Opposite payoff：同一 snapshot 下买反面 cheap token 的结果。`current_high_yes` / `current_bracket_no` 用 matrix 里的 observed opposite ask；`d1/d2 YES` 暂无真实 ask，用 `1 - NO bid` proxy，只作诊断。",
            "",
            "## High-Price Reversal Summary",
            "",
            md_table(
                overall,
                [
                    ("threshold", "threshold"),
                    ("expression", "expression"),
                    ("side", "side"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("token_roi", "buy high token ROI"),
                    ("token_roi_ci_low", "CI low"),
                    ("token_roi_ci_high", "CI high"),
                    ("opposite_roi", "buy opposite ROI"),
                    ("opposite_roi_ci_low", "opp CI low"),
                    ("opposite_roi_ci_high", "opp CI high"),
                    ("max_daily_token_loss", "max token day loss"),
                ],
                max_rows=40,
            ),
            "",
            "## High-Price YES Reversal Patterns",
            "",
            md_table(
                supported_patterns(yes_patterns, 20),
                [
                    ("expression", "expression"),
                    ("row_source_bias_regime", "bias regime"),
                    ("day_regime", "day regime"),
                    ("intraday_state", "intraday"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("avg_forecast_error", "avg fcst error"),
                    ("avg_forecast_gap", "avg fcst gap"),
                    ("opposite_roi", "opposite ROI"),
                ],
                max_rows=30,
            ),
            "",
            "_Small-sample 100% reversal clusters are kept in CSV but hidden from the main table unless rows >= 20._",
            "",
            "## High-Price NO Reversal Patterns",
            "",
            md_table(
                supported_patterns(no_patterns, 20),
                [
                    ("expression", "expression"),
                    ("row_source_bias_regime", "bias regime"),
                    ("day_regime", "day regime"),
                    ("intraday_state", "intraday"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("avg_forecast_error", "avg fcst error"),
                    ("avg_forecast_gap", "avg fcst gap"),
                    ("opposite_roi", "opposite ROI"),
                ],
                max_rows=35,
            ),
            "",
            "_Small-sample 100% reversal clusters are kept in CSV but hidden from the main table unless rows >= 20._",
            "",
            "## Forecast Conflict Bins",
            "",
            md_table(
                forecast,
                [
                    ("expression", "expression"),
                    ("forecast_error_bin", "actual - forecast"),
                    ("forecast_gap_bin", "forecast - running"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("opposite_roi", "opposite ROI"),
                    ("token_roi", "high token ROI"),
                ],
                max_rows=45,
            ),
            "",
            "## Moisture / Wind Patterns",
            "",
            md_table(
                moisture,
                [
                    ("expression", "expression"),
                    ("moisture_cloud_regime", "moisture/cloud"),
                    ("wind_regime", "wind"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("cities", "cities"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("opposite_roi", "opposite ROI"),
                ],
                max_rows=45,
            ),
            "",
            "## City / Source Concentration",
            "",
            md_table(
                city.sort_values(["reversal_rate", "rows"], ascending=[False, False]),
                [
                    ("expression", "expression"),
                    ("city", "city"),
                    ("row_forecast_model", "model"),
                    ("row_source_bias_regime", "bias regime"),
                    ("rows", "rows"),
                    ("dates", "dates"),
                    ("reversal_rate", "reversal"),
                    ("avg_ask", "avg ask"),
                    ("opposite_roi", "opposite ROI"),
                    ("avg_forecast_error", "avg fcst error"),
                ],
                max_rows=50,
            ),
            "",
            "## Example Reversal Cases",
            "",
            md_table(
                cases,
                [
                    ("target_date", "date"),
                    ("city", "city"),
                    ("expression", "expression"),
                    ("side", "side"),
                    ("ask", "ask"),
                    ("opposite_ask", "opp ask"),
                    ("final_winning_bracket", "winner"),
                    ("current_bracket", "current"),
                    ("d1_no_bracket", "d1"),
                    ("d2_no_bracket", "d2"),
                    ("row_source_bias_regime", "bias regime"),
                    ("day_regime", "day"),
                    ("intraday_state", "intraday"),
                    ("moisture_cloud_regime", "moisture"),
                    ("wind_regime", "wind"),
                    ("forecast_error_native", "fcst err"),
                    ("forecast_gap_to_running_native", "fcst gap"),
                ],
                max_rows=40,
            ),
            "",
            "## Interpretation",
            "",
            "这才是下一步该继续的方向：先把反转 case 当成事故/机会库，而不是立刻做 selector。后续应该训练/验证的是 `P(high token loses | market high ask, forecast/obs/regime conflict)`，并且 YES 反转、current NO 反转、d1/d2 NO exact-hit 反转要分头建模。",
            "",
            "当前还不能交易，因为：",
            "",
            "- d1/d2 opposite YES 只有 proxy ask，没有真实 YES ask/depth。",
            "- 这些表是 case-mining，不是 train/holdout selector。",
            "- 需要把 high-price reversal score 在 forward telemetry 里记录，并等 settled forward 样本验证。",
            "",
            "## Artifacts",
            "",
            f"- Script: `{Path('scripts/analysis/forecast_quality/research_high_price_forecast_bias_reversal_cases_v1.py')}`",
            f"- JSON summary: `{OUT_JSON.relative_to(ROOT)}`",
            f"- High-price rows: `{(OUT_DIR / 'high_price_rows.csv').relative_to(ROOT)}`",
            f"- Overall summary: `{(OUT_DIR / 'high_price_reversal_summary.csv').relative_to(ROOT)}`",
            f"- Reversal cases: `{(OUT_DIR / 'top_reversal_cases.csv').relative_to(ROOT)}`",
            "",
        ]
    )


def build(_: argparse.Namespace) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db_snapshot()
    hist, city_fit = load_bias()
    base = load_base_rows(hist, city_fit)
    long = build_long(base)
    high = high_price_rows(long, 0.70)
    tables = archetype_tables(high)

    long.to_csv(OUT_DIR / "all_expression_rows.csv", index=False)
    high.to_csv(OUT_DIR / "high_price_rows.csv", index=False)
    for name, df in tables.items():
        if name == "reversal_cases":
            filename = "top_reversal_cases.csv"
        elif name == "overall_by_threshold":
            filename = "high_price_reversal_summary.csv"
        else:
            filename = f"high_price_{name}.csv"
        df.to_csv(OUT_DIR / filename, index=False)

    yes70 = high[high["side"].eq("YES")]
    no70 = high[high["side"].eq("NO")]
    summary = {
        "generated_at": "2026-06-30",
        "head": "high_price_forecast_bias_reversal_cases_v1",
        "boundary": "case_mining_not_selector_not_live",
        "db_snapshot": db,
        "funnel": {
            "base_event_rows": int(len(base)),
            "expression_rows": int(len(long)),
            "high_price_rows_ask_ge_070": int(len(high)),
            "high_price_yes_rows": int(len(yes70)),
            "high_price_no_rows": int(len(no70)),
        },
        "high_price_yes_reversal_rate": float(yes70["is_reversal"].mean()) if len(yes70) else None,
        "high_price_no_reversal_rate": float(no70["is_reversal"].mean()) if len(no70) else None,
        "conclusion": "inconclusive_case_mining_only",
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_report(db, long, high, tables), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
