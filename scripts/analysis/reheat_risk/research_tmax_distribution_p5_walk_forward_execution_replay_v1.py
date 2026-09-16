#!/usr/bin/env python3
"""P5 walk-forward execution replay for the Tmax distribution model.

This script keeps the P3/P4 model family fixed and asks whether the probability
advantage can be translated into executable asks without adding new hard gates.

It is research-only:
  - no canonical settlement writes
  - no live runner changes
  - 2026-06-27+ labels remain observed-max-derived pressure-test labels
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
from research_tmax_distribution_p0_anchor_scorecard_v1 import ATLAS_PATH, BUCKETS  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p5-walk-forward-execution-replay-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p5-walk-forward-execution-replay-v1.json"
DB_PATH = ROOT / "runtime/weather.db"

METHODS = [
    "market_local_norm",
    "market_recal_blend",
    "mkt_path_core_blend",
    "mkt_boundary_blend",
    "mkt_meteo_blend",
    "mkt_regime_blend",
    "mkt_city_source_blend",
    "loo_no_regime_blend",
    "loo_no_city_source_blend",
]
EDGE_THRESHOLDS = [0.0, 0.02, 0.05, 0.10]
FOCUS_METHODS = [
    "mkt_regime_blend",
    "loo_no_city_source_blend",
    "mkt_city_source_blend",
    "loo_no_regime_blend",
    "market_recal_blend",
    "market_local_norm",
]
PRIMARY_THRESHOLD = 0.02
EPS = 1e-9


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _fmt_float(v: object, *, pct: bool = False, signed: bool = False, digits: int = 1) -> str:
    if isinstance(v, (float, np.floating)):
        if math.isnan(float(v)):
            return "n/a"
        if pct:
            sign = "+" if signed else ""
            return f"{float(v):{sign}.{digits}%}"
        sign = "+" if signed else ""
        return f"{float(v):{sign}.{digits}f}"
    return str(v)


def _date_block_roi_ci(rows: pd.DataFrame, n_boot: int = 1000, seed: int = 20260703) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan, "n_dates": 0}
    by_date = rows.groupby("target_date", as_index=False).agg(cost=("ask", "sum"), pnl=("unit_pnl", "sum"))
    roi = float(rows["unit_pnl"].sum() / rows["ask"].sum()) if rows["ask"].sum() else math.nan
    if len(by_date) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan, "n_dates": int(len(by_date))}
    rng = np.random.default_rng(seed)
    arr = by_date[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        cost = sample[:, 0].sum()
        pnl = sample[:, 1].sum()
        boot.append(float(pnl / cost) if cost else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
        "n_dates": int(len(by_date)),
    }


def _data_inventory() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        try:
            for table, date_col in [
                ("fact_signal_candidates", "event_date"),
                ("fact_trades", "target_date"),
                ("settlement_outcomes", "target_date"),
            ]:
                out[table] = dict(
                    conn.execute(
                        f"SELECT COUNT(*) AS rows, MIN({date_col}) AS min_date, MAX({date_col}) AS max_date "
                        f"FROM {table}"
                    ).fetchone()
                )
        finally:
            conn.close()
    return out


def _load_size_columns() -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_no_ask_size",
        "d1_no_ask_size",
        "d2_no_ask_size",
    ]
    return pd.read_csv(ATLAS_PATH, usecols=lambda c: c in set(cols)).drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )


def _prepare_df() -> tuple[pd.DataFrame, dict[str, Any]]:
    df, counters = p4._load_rows_extended()
    if df.empty:
        raise RuntimeError("No P4 rows available")
    size_df = _load_size_columns()
    df = df.merge(size_df, on=["city", "target_date", "decision_hour_local"], how="left", validate="many_to_one")
    return df, counters


def _selected_specs_and_predictions(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    specs = p3._feature_specs(df)
    p1.MODEL_SPECS = specs
    train = df[df["target_date"] < p1.TRAIN_CUTOFF].copy()
    selections = {name: p1._select_model(train, name) for name in specs}
    selection_df = p1._selection_table(selections)

    train_pred_frames = []
    train_score_frames = []
    train_market_keys = ["city", "target_date", "decision_hour_local", "actual_bucket"] + [f"market_p_{b}" for b in BUCKETS]
    train_market = p1._add_market_method(train[train_market_keys].copy())
    train_score_frames.append(p1._score_probs(train_market, "market_local_norm"))
    for spec_name, payload in selections.items():
        c_value = payload["selected"]["c"]
        alpha = payload["selected"]["cv_blend_alpha"]
        pred = p1._expanding_cv_predictions(train, spec_name, c_value)
        if pred.empty:
            continue
        pred = p1._blend_predictions(pred, 1.0, f"{spec_name}_model")
        pred = p1._blend_predictions(pred, alpha, f"{spec_name}_blend")
        train_pred_frames.append(pred)
        train_score_frames.append(p1._score_probs(pred, f"{spec_name}_blend"))
    dev_cv_preds = p1._merge_prediction_frames(train_pred_frames)
    dev_cv_scores = pd.concat(train_score_frames, ignore_index=True)

    expanding_scores, expanding_preds = p1._expanding_forward_predictions(df, selections)
    return selection_df, dev_cv_preds, expanding_preds, {
        "model_specs": list(specs),
        "dev_cv_score_rows": int(len(dev_cv_scores)),
        "forward_score_rows": int(len(expanding_scores)),
    }


def _make_base(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "label_source",
        "eval_slice",
        "current_yes_ask",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
        "current_no_ask_size",
        "d1_no_ask_size",
        "d2_no_ask_size",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "forecast_source",
        "city_family",
        "forecast_to_current_upper_native",
        "forecast_to_d1_upper_native",
        "forecast_to_d2_upper_native",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    ]
    available = [c for c in cols if c in df.columns]
    out = df[available].drop_duplicates(["city", "target_date", "decision_hour_local", "actual_bucket"]).copy()
    for col in ["market_raw_local_mass", "market_tail_residual_raw", "market_local_overround_raw", "market_local_undermass_raw"]:
        out[col] = np.nan
    return out


def _build_opportunities(scope: str, base: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    old_methods = list(p2.METHODS)
    try:
        p2.METHODS = METHODS
        opps = p2._build_opportunities(scope, base, preds)
    finally:
        p2.METHODS = old_methods
    if opps.empty:
        return opps
    meta_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "label_source",
        "eval_slice",
        "moisture_cloud_regime",
        "wind_regime",
        "running_max_state",
        "solar_window",
        "forecast_to_current_upper_native",
        "forecast_to_d1_upper_native",
        "forecast_to_d2_upper_native",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    ]
    meta = base[[c for c in meta_cols if c in base.columns]].drop_duplicates(["city", "target_date", "decision_hour_local"])
    return opps.merge(meta, on=["city", "target_date", "decision_hour_local"], how="left", validate="many_to_one")


def _best_expression(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    selected = rows[rows["model_edge"] >= threshold].copy()
    if selected.empty:
        return selected
    keys = ["scope", "method", "city", "target_date", "decision_hour_local"]
    return selected.sort_values(["model_edge", "model_roi"], ascending=False).groupby(keys, as_index=False).head(1)


def _summarize(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    selected = _best_expression(rows, threshold)
    summaries = []
    for (scope, method), grp in selected.groupby(["scope", "method"], dropna=False):
        ci = _date_block_roi_ci(grp)
        by_day = grp.groupby("target_date", as_index=False).agg(cost=("ask", "sum"), pnl=("unit_pnl", "sum"))
        by_day["roi"] = by_day["pnl"] / by_day["cost"]
        expr = grp["expression"].value_counts().to_dict()
        summaries.append(
            {
                "scope": scope,
                "method": method,
                "edge_threshold": threshold,
                "selected_rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "avg_rows_per_date": float(len(grp) / max(1, grp["target_date"].nunique())),
                "avg_ask": float(grp["ask"].mean()),
                "avg_edge": float(grp["model_edge"].mean()),
                "win_rate": float(grp["win"].mean()),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": ci["roi"],
                "roi_ci_low": ci["ci_low"],
                "roi_ci_high": ci["ci_high"],
                "daily_win_rate": float((by_day["pnl"] > 0).mean()) if len(by_day) else math.nan,
                "losing_days": int((by_day["pnl"] < 0).sum()) if len(by_day) else 0,
                "worst_day_pnl": float(by_day["pnl"].min()) if len(by_day) else math.nan,
                "worst_day_roi": float(by_day.loc[by_day["pnl"].idxmin(), "roi"]) if len(by_day) else math.nan,
                "current_yes_rows": int(expr.get("current_yes", 0)),
                "current_no_rows": int(expr.get("current_no", 0)),
                "d1_no_rows": int(expr.get("d1_no", 0)),
                "d2_no_rows": int(expr.get("d2_no", 0)),
            }
        )
    return pd.DataFrame(summaries)


def _all_summaries(opps: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([_summarize(opps, t) for t in EDGE_THRESHOLDS], ignore_index=True).sort_values(
        ["scope", "edge_threshold", "method"]
    )


def _daily(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    selected = _best_expression(rows, threshold)
    if selected.empty:
        return pd.DataFrame()
    out = []
    for (scope, method, target_date), grp in selected.groupby(["scope", "method", "target_date"], dropna=False):
        out.append(
            {
                "scope": scope,
                "method": method,
                "target_date": target_date,
                "rows": int(len(grp)),
                "cities": int(grp["city"].nunique()),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": float(grp["unit_pnl"].sum() / grp["ask"].sum()) if grp["ask"].sum() else math.nan,
                "current_yes_rows": int((grp["expression"] == "current_yes").sum()),
                "current_no_rows": int((grp["expression"] == "current_no").sum()),
                "d1_no_rows": int((grp["expression"] == "d1_no").sum()),
                "d2_no_rows": int((grp["expression"] == "d2_no").sum()),
            }
        )
    return pd.DataFrame(out).sort_values(["scope", "method", "target_date"])


def _slice_summary(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    selected = _best_expression(rows, threshold)
    if selected.empty:
        return pd.DataFrame()
    slice_defs = {
        "expression": "expression",
        "actual_bucket": "actual_bucket",
        "day_regime": "day_regime",
        "intraday_state": "intraday_state",
        "running_max_state": "running_max_state",
        "wind_regime": "wind_regime",
        "moisture_cloud_regime": "moisture_cloud_regime",
        "ask_bucket": None,
        "edge_bucket": None,
    }
    selected["ask_bucket"] = pd.cut(
        selected["ask"], bins=[0.0, 0.25, 0.5, 0.75, 1.0], labels=["0-0.25", "0.25-0.50", "0.50-0.75", "0.75-1.00"], include_lowest=True
    ).astype(str)
    selected["edge_bucket"] = pd.cut(
        selected["model_edge"],
        bins=[-1.0, 0.0, 0.02, 0.05, 0.10, 1.0],
        labels=["<0", "0-0.02", "0.02-0.05", "0.05-0.10", "0.10+"],
        include_lowest=True,
    ).astype(str)
    rows_out = []
    for slice_name, col in slice_defs.items():
        group_col = col or slice_name
        if group_col not in selected.columns:
            continue
        for (scope, method, value), grp in selected.groupby(["scope", "method", group_col], dropna=False):
            ci = _date_block_roi_ci(grp)
            rows_out.append(
                {
                    "scope": scope,
                    "method": method,
                    "slice": slice_name,
                    "value": str(value),
                    "rows": int(len(grp)),
                    "dates": int(grp["target_date"].nunique()),
                    "cost": float(grp["ask"].sum()),
                    "pnl": float(grp["unit_pnl"].sum()),
                    "roi": ci["roi"],
                    "roi_ci_low": ci["ci_low"],
                    "roi_ci_high": ci["ci_high"],
                }
            )
    return pd.DataFrame(rows_out).sort_values(["scope", "method", "slice", "value"])


def _dev_selected_policy(summary: pd.DataFrame) -> pd.DataFrame:
    dev = summary[
        summary["scope"].eq("dev_cv")
        & summary["method"].isin(FOCUS_METHODS)
        & summary["selected_rows"].ge(50)
        & summary["dates"].ge(10)
    ].copy()
    if dev.empty:
        return pd.DataFrame()
    dev["ci_score"] = dev["roi_ci_low"].fillna(-999)
    return dev.sort_values(["ci_score", "roi", "selected_rows"], ascending=[False, False, False]).head(8)


def _table(df: pd.DataFrame, columns: list[str], *, max_rows: int | None = None) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    if max_rows is not None:
        df = df.head(max_rows)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in df.to_dict("records"):
        vals = []
        for col in columns:
            val = row.get(col)
            if col in {"roi", "roi_ci_low", "roi_ci_high", "win_rate", "daily_win_rate", "worst_day_roi"}:
                vals.append(_fmt_float(val, pct=True, signed=col not in {"win_rate", "daily_win_rate"}))
            elif col in {"avg_ask", "avg_edge", "cost", "pnl", "worst_day_pnl", "avg_rows_per_date"}:
                vals.append(_fmt_float(val, signed=col in {"avg_edge", "pnl", "worst_day_pnl"}, digits=3 if col in {"avg_ask", "avg_edge"} else 2))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _write_report(
    *,
    df: pd.DataFrame,
    counters: dict[str, Any],
    inventory: dict[str, Any],
    selection_df: pd.DataFrame,
    summary: pd.DataFrame,
    daily: pd.DataFrame,
    slices: pd.DataFrame,
    dev_policy: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    focus_summary = summary[
        summary["method"].isin(FOCUS_METHODS) & summary["edge_threshold"].eq(PRIMARY_THRESHOLD)
    ].sort_values(["scope", "roi"], ascending=[True, False])
    forward_focus = focus_summary[focus_summary["scope"].eq("verified_forward")]
    extension_focus = focus_summary[focus_summary["scope"].eq("extension_forward")]
    dev_focus = focus_summary[focus_summary["scope"].eq("dev_cv")]

    best_forward = forward_focus.iloc[0].to_dict() if not forward_focus.empty else {}
    best_extension = extension_focus.iloc[0].to_dict() if not extension_focus.empty else {}

    daily_focus = daily[
        daily["method"].isin(["mkt_regime_blend", "loo_no_city_source_blend", "mkt_city_source_blend"])
    ].copy()
    daily_focus["sort_key"] = daily_focus["scope"].map({"verified_forward": 1, "extension_forward": 2, "dev_cv": 0}).fillna(9)
    daily_focus = daily_focus.sort_values(["sort_key", "method", "target_date"]).drop(columns=["sort_key"])

    bad_days = daily_focus[daily_focus["scope"].isin(["verified_forward", "extension_forward"])].sort_values("pnl").head(12)

    slice_focus = slices[
        slices["method"].isin(["mkt_regime_blend", "loo_no_city_source_blend", "mkt_city_source_blend"])
        & slices["scope"].isin(["verified_forward", "extension_forward"])
        & slices["slice"].isin(["expression", "ask_bucket", "edge_bucket", "intraday_state", "running_max_state"])
    ].copy()
    slice_focus = slice_focus.sort_values(["scope", "method", "slice", "roi"], ascending=[True, True, True, False])
    threshold_forward = summary[
        summary["method"].isin(["mkt_regime_blend", "loo_no_city_source_blend", "mkt_city_source_blend", "loo_no_regime_blend"])
        & summary["scope"].isin(["verified_forward", "extension_forward"])
        & summary["edge_threshold"].isin([0.05, 0.10])
    ].sort_values(["edge_threshold", "scope", "roi"], ascending=[True, True, False])

    lines = [
        "# Tmax Distribution P5 Walk-Forward Execution Replay v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: research-only walk-forward execution replay; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        "- P5 固定 P3/P4 的分布模型，不新增天气 gate；交易表达由 `P(win) - ask` 决定，每个 city-date-hour 只保留最高 edge 的一个表达。",
        "- 这一步把问题从“模型 logloss 是否赢盘口”推进到“真实 ask 下，概率优势能不能稳定落地”。",
        (
            f"- verified 6/21-6/26 最好的一组是 `{best_forward.get('method', 'n/a')}`："
            f"{best_forward.get('selected_rows', 0)} rows，ROI {_fmt_float(best_forward.get('roi', math.nan), pct=True, signed=True)}，"
            f"CI [{_fmt_float(best_forward.get('roi_ci_low', math.nan), pct=True, signed=True)}, "
            f"{_fmt_float(best_forward.get('roi_ci_high', math.nan), pct=True, signed=True)}]。"
        ),
        (
            f"- extension 6/27-6/29 最好的一组是 `{best_extension.get('method', 'n/a')}`："
            f"{best_extension.get('selected_rows', 0)} rows，ROI {_fmt_float(best_extension.get('roi', math.nan), pct=True, signed=True)}，"
            f"CI [{_fmt_float(best_extension.get('roi_ci_low', math.nan), pct=True, signed=True)}, "
            f"{_fmt_float(best_extension.get('roi_ci_high', math.nan), pct=True, signed=True)}]。"
        ),
        "- 结论：`inconclusive_positive_signal`。概率模型方向仍有 edge 痕迹，但 extension 只有 3 天，EV CI 宽，不能 live。",
        "- 旧样本 dev-CV 最偏好的 `edge>=0.10` 在 verified forward 仍很强，但在 6/27-6/29 extension 明显变薄；这说明高 edge 排序有信号，但 recent 压力测试还没过。",
        "",
        "## Funnel / Evidence",
        "",
        f"- P4 scored rows: `{len(df)}`; date range `{df['target_date'].min()}`..`{df['target_date'].max()}`; cities `{df['city'].nunique()}`。",
        f"- raw label sources: `{counters.get('label_sources_raw', {})}`。",
        f"- skipped observed-derived before 6/27: `{counters.get('observed_derived_before_extension_window')}`。",
        "- scopes: `dev_cv` = 6/21 前训练窗内 expanding-CV；`verified_forward` = forward rows backed by `settlement_outcomes`；`extension_forward` = forward rows still using observed-max-derived labels。",
        f"- DB inventory: `{inventory}`。",
        "",
        "## Primary Replay, Edge >= 0.02",
        "",
        *_table(
            focus_summary,
            [
                "scope",
                "method",
                "selected_rows",
                "dates",
                "cities",
                "avg_rows_per_date",
                "avg_ask",
                "avg_edge",
                "win_rate",
                "cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "daily_win_rate",
                "losing_days",
                "worst_day_pnl",
                "worst_day_roi",
                "current_yes_rows",
                "current_no_rows",
                "d1_no_rows",
                "d2_no_rows",
            ],
        ),
        "",
        "## Dev-CV Policy Ranking",
        "",
        "这个表只用 6/21 前 expanding-CV 排序，目的是看如果先在旧样本里选方法/threshold，forward 是否同号。不是 live 选择器。",
        "",
        *_table(
            dev_policy,
            [
                "method",
                "edge_threshold",
                "selected_rows",
                "dates",
                "cities",
                "avg_ask",
                "avg_edge",
                "win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "daily_win_rate",
                "losing_days",
            ],
            max_rows=8,
        ),
        "",
        "## Dev-Selected Threshold Forward Check",
        "",
        "dev-CV 排名前几名偏向 `edge>=0.10`。如果把这个阈值冻结到未来，verified 仍强，但 extension 变薄，所以不能把旧样本最优阈值直接当 live 参数。",
        "",
        *_table(
            threshold_forward,
            [
                "scope",
                "method",
                "edge_threshold",
                "selected_rows",
                "dates",
                "cities",
                "avg_ask",
                "avg_edge",
                "win_rate",
                "cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "daily_win_rate",
                "losing_days",
                "worst_day_pnl",
                "worst_day_roi",
            ],
        ),
        "",
        "## Worst Forward Days",
        "",
        *_table(
            bad_days,
            [
                "scope",
                "method",
                "target_date",
                "rows",
                "cities",
                "cost",
                "pnl",
                "roi",
                "current_yes_rows",
                "current_no_rows",
                "d1_no_rows",
                "d2_no_rows",
            ],
            max_rows=12,
        ),
        "",
        "## Diagnostic Slices",
        "",
        "以下是诊断切片，不是 hard gate。用于看 EV 来自哪里、坏在哪里。",
        "",
        *_table(
            slice_focus,
            ["scope", "method", "slice", "value", "rows", "dates", "cost", "pnl", "roi", "roi_ci_low", "roi_ci_high"],
            max_rows=80,
        ),
        "",
        "## Model Selection",
        "",
        *_table(selection_df, ["spec", "c", "cv_rows", "cv_dates", "cv_market_logloss", "cv_blend_alpha", "cv_blend_logloss"]),
        "",
        "## Verdict",
        "",
        "significance=FAIL/THIN; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`.",
        "",
        "下一步需要两件事：第一，补完整 6/27+ official settlement/orderbook depth 后重跑；第二，把 P5 输出接成 zero-notional shadow telemetry，而不是 live 下单。",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'policy_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'daily.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'diagnostic_slices.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'opportunities.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, counters = _prepare_df()
    selection_df, dev_cv_preds, forward_preds, pred_meta = _selected_specs_and_predictions(df)
    base = _make_base(df)

    dev_opps = _build_opportunities("dev_cv", base, dev_cv_preds)
    forward_opps = _build_opportunities("forward_all", base, forward_preds)
    if not forward_opps.empty:
        forward_opps["scope"] = np.where(
            forward_opps["eval_slice"].eq(p4.VERIFIED_SLICE),
            "verified_forward",
            "extension_forward",
        )
    opps = pd.concat([dev_opps, forward_opps], ignore_index=True)
    summary = _all_summaries(opps)
    daily = _daily(opps, PRIMARY_THRESHOLD)
    slices = _slice_summary(opps, PRIMARY_THRESHOLD)
    dev_policy = _dev_selected_policy(summary)

    opps.to_csv(OUT_DIR / "opportunities.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    slices.to_csv(OUT_DIR / "diagnostic_slices.csv", index=False)
    dev_policy.to_csv(OUT_DIR / "dev_cv_policy_ranking.csv", index=False)
    selection_df.to_csv(OUT_DIR / "model_selection.csv", index=False)
    dev_cv_preds.to_csv(OUT_DIR / "dev_cv_predictions.csv", index=False)
    forward_preds.to_csv(OUT_DIR / "forward_predictions.csv", index=False)

    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "date_range": [str(df["target_date"].min()), str(df["target_date"].max())],
        "rows": int(len(df)),
        "cities": int(df["city"].nunique()),
        "counters": counters,
        "prediction_meta": pred_meta,
        "inventory": _data_inventory(),
        "methods": METHODS,
        "edge_thresholds": EDGE_THRESHOLDS,
        "primary_threshold": PRIMARY_THRESHOLD,
        "verdict": "inconclusive_positive_signal",
        "summary_focus": summary[
            summary["method"].isin(FOCUS_METHODS) & summary["edge_threshold"].eq(PRIMARY_THRESHOLD)
        ].to_dict("records"),
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _write_report(
        df=df,
        counters=counters,
        inventory=report["inventory"],
        selection_df=selection_df,
        summary=summary,
        daily=daily,
        slices=slices,
        dev_policy=dev_policy,
        report=report,
    )
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "date_range": report["date_range"],
                "rows": report["rows"],
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
