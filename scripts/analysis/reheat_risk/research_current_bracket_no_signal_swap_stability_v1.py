#!/usr/bin/env python3
"""Train/validation swap stability for current-bracket NO remaining-heat signals.

This is not a city blacklist test.  It asks whether the model and signal
indicators themselves survive when early and late windows are swapped:

- train on early dates, evaluate late and forward;
- train on late dates, evaluate early and forward;
- compare base vs enhanced feature sets;
- inspect fixed threshold variants and p_cross calibration buckets.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_remaining_heat_mechanism_features_v3 as v3  # noqa: E402
import research_current_bracket_no_remaining_heat_model_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_signal_swap_stability_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MODEL = OUT_DIR / "model_swap_metrics.csv"
OUT_VARIANT = OUT_DIR / "variant_swap_performance.csv"
OUT_BUCKET = OUT_DIR / "score_bucket_calibration.csv"
OUT_DAILY = OUT_DIR / "selected_daily_context.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-signal-swap-stability-v1.md"

EARLY_END = "2026-06-10"
LATE_START = "2026-06-11"
LATE_END = "2026-06-20"
FORWARD_START = "2026-06-21"
FOCUS_VARIANTS = ["baseline_trade_base", "remaining_heat_p35_ev05", "remaining_heat_p40_ev10", "remaining_heat_p45_ev10"]
SEED = 20260624


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+,.2f}"


def add_error_cols(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["actual_margin_f"] = pd.to_numeric(out["future_delta_to_daymax_f"], errors="coerce") - pd.to_numeric(
        out["required_gap_f"], errors="coerce"
    )
    out["pred_error_f"] = pd.to_numeric(out["pred_remaining_heat_f"], errors="coerce") - pd.to_numeric(
        out["future_delta_to_daymax_f"], errors="coerce"
    )
    out["loss_reason"] = np.select(
        [
            out["label_no_wins"].eq(1),
            out["actual_margin_f"].lt(0) & out["pred_error_f"].gt(0.35),
            out["actual_margin_f"].lt(0),
            out["pred_error_f"].gt(0.35),
        ],
        ["win", "capped_day_model_overestimate", "capped_day_shortfall", "model_overestimate"],
        default="other_loss",
    )
    return out


def prepare_history() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, stats = v1.load_historical_forced_gfs()
    frame["target_date"] = frame["target_date"].astype(str)
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)
    frame = v3.add_enhanced_mechanism_features(frame)
    return frame.reset_index(drop=True), stats


def prepare_forward() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, stats = v1.load_forward_forced_gfs()
    if frame.empty:
        return frame, stats
    frame["target_date"] = frame["target_date"].astype(str)
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)
    frame = v3.add_enhanced_mechanism_features(frame)
    return frame.reset_index(drop=True), stats


def window_mask(frame: pd.DataFrame, window: str) -> pd.Series:
    dates = frame["target_date"].astype(str)
    if window == "early":
        return dates.le(EARLY_END)
    if window == "late":
        return dates.between(LATE_START, LATE_END)
    if window == "historical":
        return dates.le(LATE_END)
    if window == "forward":
        return dates.ge(FORWARD_START)
    raise ValueError(window)


def score_frame(frame: pd.DataFrame, model: Any, sigma: float, num_features: list[str], cat_features: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["pred_remaining_heat_f"] = model.predict(out[num_features + cat_features])
    out["remaining_heat_sigma_f"] = sigma
    out["p_cross_upper"] = v1.norm_sf((out["required_gap_f"] - out["pred_remaining_heat_f"]) / sigma)
    out["mechanism_edge"] = out["p_cross_upper"] - pd.to_numeric(out["no_ask"], errors="coerce")
    out["p_no_win"] = out["p_cross_upper"]
    out["p_up_margin"] = out["p_cross_upper"]
    out["edge_no_win"] = out["mechanism_edge"]
    out["edge_up_margin"] = out["mechanism_edge"]
    return add_error_cols(out)


def fit_spec(hist: pd.DataFrame, spec: dict[str, Any]) -> tuple[pd.DataFrame, Any, float]:
    train_mask = window_mask(hist, spec["train_window"])
    if spec["train_scope"] == "trade_base":
        train_mask = train_mask & hist["trade_base_mechanism"].fillna(False)
    scored, model, sigma = v3.score_model(hist, train_mask, spec["num_features"], spec["cat_features"])
    return add_error_cols(scored), model, sigma


def selected_perf(selected: pd.DataFrame, train_spec: str, eval_window: str, variant: str) -> dict[str, Any]:
    settled = selected[selected["label_no_wins"].notna()].copy()
    cost = float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0
    profit = float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0
    return {
        "train_spec": train_spec,
        "eval_window": eval_window,
        "variant": variant,
        "selected_trades": int(len(selected)),
        "settled_trades": int(len(settled)),
        "active_dates": int(selected["target_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "wins": float(settled["label_no_wins"].sum()) if len(settled) else 0.0,
        "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else None,
        "profit_usd": profit,
        "cost_usd": cost,
        "roi": profit / cost if cost else None,
        "avg_p_cross": float(selected["p_cross_upper"].mean()) if len(selected) else None,
        "avg_no_ask": float(selected["no_ask"].mean()) if len(selected) else None,
        "avg_edge": float(selected["mechanism_edge"].mean()) if len(selected) else None,
        "avg_required_gap_f": float(selected["required_gap_f"].mean()) if len(selected) else None,
        "avg_pred_remaining_heat_f": float(selected["pred_remaining_heat_f"].mean()) if len(selected) else None,
        "avg_actual_remaining_heat_f": float(settled["future_delta_to_daymax_f"].mean()) if len(settled) else None,
        "avg_actual_margin_f": float(settled["actual_margin_f"].mean()) if len(settled) else None,
        "avg_pred_error_f": float(settled["pred_error_f"].mean()) if len(settled) else None,
        "loss_reason_counts": ",".join(
            f"{k}:{v}" for k, v in settled["loss_reason"].value_counts().sort_index().to_dict().items()
        )
        if len(settled)
        else "",
        "dates": ",".join(sorted(selected["target_date"].astype(str).unique())) if len(selected) else "",
    }


def evaluate_variants(scored: pd.DataFrame, train_spec: str, eval_window: str) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    if scored.empty:
        return [], pd.DataFrame()
    rows = []
    selected_parts = []
    for variant, raw in v1.variant_raws(scored).items():
        if variant not in FOCUS_VARIANTS:
            continue
        selected = v1.select_first(raw)
        rows.append(selected_perf(selected, train_spec, eval_window, variant))
        if not selected.empty:
            out = selected.copy()
            out["train_spec"] = train_spec
            out["eval_window"] = eval_window
            out["variant"] = variant
            selected_parts.append(out)
    selected_all = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    return rows, selected_all


def eval_model_metrics(scored: pd.DataFrame, mask: pd.Series, train_spec: str, eval_window: str) -> dict[str, Any]:
    metric = v3.model_metrics(scored, mask)
    metric["train_spec"] = train_spec
    metric["eval_window"] = eval_window
    metric["trade_base_rows"] = int((mask & scored["trade_base_mechanism"].fillna(False)).sum())
    return metric


def bucket_calibration(scored: pd.DataFrame, train_spec: str, eval_window: str) -> list[dict[str, Any]]:
    tb = scored[scored["trade_base_mechanism"].fillna(False) & scored["label_no_wins"].notna()].copy()
    if tb.empty:
        return []
    try:
        tb["score_bucket"] = pd.qcut(tb["p_cross_upper"], q=5, labels=False, duplicates="drop") + 1
    except ValueError:
        tb["score_bucket"] = 1
    rows = []
    for bucket, group in tb.groupby("score_bucket", dropna=False):
        rows.append(
            {
                "train_spec": train_spec,
                "eval_window": eval_window,
                "bucket": int(bucket) if pd.notna(bucket) else None,
                "rows": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "avg_p_cross": float(group["p_cross_upper"].mean()),
                "cross_rate": float(group["cross_upper_margin_label"].mean()),
                "avg_no_ask": float(group["no_ask"].mean()),
                "avg_edge": float(group["mechanism_edge"].mean()),
                "avg_actual_margin_f": float(group["actual_margin_f"].mean()),
                "avg_pred_error_f": float(group["pred_error_f"].mean()),
            }
        )
    return rows


def daily_context(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    settled = selected[selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return pd.DataFrame()
    out = (
        settled.groupby(["train_spec", "eval_window", "variant", "target_date"])
        .agg(
            trades=("city", "size"),
            wins=("label_no_wins", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_p_cross=("p_cross_upper", "mean"),
            avg_pred_error_f=("pred_error_f", "mean"),
            avg_actual_margin_f=("actual_margin_f", "mean"),
            loss_reasons=("loss_reason", lambda x: ",".join(sorted(set(x.astype(str))))),
            cities=("city", lambda x: ",".join(sorted(x.astype(str)))),
        )
        .reset_index()
    )
    out["roi"] = out["profit_usd"] / out["cost_usd"]
    out["win_rate"] = out["wins"] / out["trades"]
    return out


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("rate") or col in {"cross_auc", "cross_brier"}:
                vals.append(pct(val) if col.endswith("roi") or col.endswith("rate") else (f"{float(val):.3f}" if pd.notna(val) else "NA"))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], model_df: pd.DataFrame, variant_df: pd.DataFrame, bucket_df: pd.DataFrame, daily_df: pd.DataFrame) -> str:
    focus_models = model_df[
        model_df["train_spec"].isin(["enhanced_early_all", "enhanced_late_all", "base_early_all", "base_late_all"])
        & model_df["eval_window"].isin(["early", "late", "forward_settled"])
    ].copy()
    focus_variants = variant_df[
        variant_df["train_spec"].isin(["enhanced_early_all", "enhanced_late_all"])
        & variant_df["eval_window"].isin(["early", "late", "forward_settled"])
        & variant_df["variant"].eq("remaining_heat_p40_ev10")
    ].copy()
    bucket_focus = bucket_df[
        bucket_df["train_spec"].isin(["enhanced_early_all", "enhanced_late_all"])
        & bucket_df["eval_window"].isin(["late", "early", "forward_settled"])
    ].copy()
    daily_focus = daily_df[
        daily_df["variant"].eq("remaining_heat_p40_ev10")
        & daily_df["eval_window"].isin(["early", "late", "forward_settled"])
        & (daily_df["roi"].le(-0.8) | daily_df["target_date"].ge("2026-06-21"))
    ].copy()

    return "\n".join(
        [
            "# Current-Bracket NO Signal Swap Stability V1",
            "",
            "## 结论",
            "",
            "这次换训练/验证窗口后，结论不是“某些城市该删”，而是当前 remaining-heat 信号本身跨窗口不稳定。early 训练出来的 enhanced 模型在 late 能改善部分指标，但 late 训练反看 early 和 forward 都不能给出稳定交易收益；p_cross 分桶也不是稳定单调。问题集中在 capped-day/model-overestimate：模型能识别一部分午后升温机会，但没有稳定识别“会不会真的突破 current upper + margin”。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Data",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Historical dates: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
            f"- Historical mechanism rows: `{payload['dataset']['mechanism_rows']}`",
            f"- Historical trade-base rows: `{payload['dataset']['trade_base_rows']}`",
            f"- Forward rows: `{payload['forward_dataset']['mechanism_rows']}`",
            "",
            "## Model Swap Metrics",
            "",
            table(focus_models, ["train_spec", "eval_window", "rows", "active_dates", "trade_base_rows", "mae_f", "rmse_f", "r2", "cross_rate", "cross_auc", "cross_brier"]),
            "",
            "## Fixed Signal Variant: p40_ev10",
            "",
            table(
                focus_variants,
                [
                    "train_spec",
                    "eval_window",
                    "selected_trades",
                    "settled_trades",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "profit_usd",
                    "avg_p_cross",
                    "avg_pred_error_f",
                    "avg_actual_margin_f",
                    "loss_reason_counts",
                ],
            ),
            "",
            "## p_cross Bucket Calibration",
            "",
            table(bucket_focus, ["train_spec", "eval_window", "bucket", "rows", "active_dates", "avg_p_cross", "cross_rate", "avg_edge", "avg_actual_margin_f", "avg_pred_error_f"], limit=80),
            "",
            "## Bad Daily Context",
            "",
            table(daily_focus, ["train_spec", "eval_window", "target_date", "trades", "win_rate", "roi", "profit_usd", "avg_p_cross", "avg_pred_error_f", "avg_actual_margin_f", "loss_reasons", "cities"], limit=80),
            "",
            "## Interpretation",
            "",
            "1. 如果问题主要是样本波动，互换训练后至少应看到同一类高分信号在反向窗口保持同号收益；实际没有稳定保持。",
            "2. 如果问题主要是城市机制，模型指标和 score buckets 应仍然稳，只是某些城市 drag；实际 score bucket 的跨窗口校准也漂。",
            "3. 因此当前证据指向机制缺口：day-level capped heat / forecast overestimate regime 没建模。城市和气候族群应作为 interaction feature，而不是主过滤器。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Model metrics: `{OUT_MODEL.relative_to(ROOT)}`",
            f"- Variant performance: `{OUT_VARIANT.relative_to(ROOT)}`",
            f"- Score bucket calibration: `{OUT_BUCKET.relative_to(ROOT)}`",
            f"- Daily context: `{OUT_DAILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist, hist_stats = prepare_history()
    forward, forward_stats = prepare_forward()

    specs = [
        {
            "name": "base_early_all",
            "train_window": "early",
            "train_scope": "all",
            "num_features": v1.MECH_NUM_FEATURES,
            "cat_features": v1.MECH_CAT_FEATURES,
        },
        {
            "name": "base_late_all",
            "train_window": "late",
            "train_scope": "all",
            "num_features": v1.MECH_NUM_FEATURES,
            "cat_features": v1.MECH_CAT_FEATURES,
        },
        {
            "name": "enhanced_early_all",
            "train_window": "early",
            "train_scope": "all",
            "num_features": v3.ENHANCED_NUM_FEATURES,
            "cat_features": v3.ENHANCED_CAT_FEATURES,
        },
        {
            "name": "enhanced_late_all",
            "train_window": "late",
            "train_scope": "all",
            "num_features": v3.ENHANCED_NUM_FEATURES,
            "cat_features": v3.ENHANCED_CAT_FEATURES,
        },
        {
            "name": "enhanced_early_trade_base",
            "train_window": "early",
            "train_scope": "trade_base",
            "num_features": v3.ENHANCED_NUM_FEATURES,
            "cat_features": v3.ENHANCED_CAT_FEATURES,
        },
        {
            "name": "enhanced_late_trade_base",
            "train_window": "late",
            "train_scope": "trade_base",
            "num_features": v3.ENHANCED_NUM_FEATURES,
            "cat_features": v3.ENHANCED_CAT_FEATURES,
        },
    ]

    model_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []

    for spec in specs:
        hist_scored, model, sigma = fit_spec(hist, spec)
        forward_scored = score_frame(forward, model, sigma, spec["num_features"], spec["cat_features"]) if not forward.empty else forward.copy()

        for eval_window in ["early", "late", "historical"]:
            mask = window_mask(hist_scored, eval_window)
            scored_slice = hist_scored[mask].copy()
            model_rows.append(eval_model_metrics(hist_scored, mask, spec["name"], eval_window))
            rows, selected = evaluate_variants(scored_slice, spec["name"], eval_window)
            variant_rows.extend(rows)
            bucket_rows.extend(bucket_calibration(scored_slice, spec["name"], eval_window))
            if not selected.empty:
                selected_parts.append(selected)

        if not forward_scored.empty:
            settled_mask = forward_scored["target_date"].astype(str).ge(FORWARD_START) & forward_scored["label_no_wins"].notna()
            all_mask = forward_scored["target_date"].astype(str).ge(FORWARD_START)
            model_rows.append(eval_model_metrics(forward_scored, settled_mask, spec["name"], "forward_settled"))
            rows, selected = evaluate_variants(forward_scored[all_mask].copy(), spec["name"], "forward_all")
            for row in rows:
                row["eval_window"] = "forward_settled"
            variant_rows.extend(rows)
            bucket_rows.extend(bucket_calibration(forward_scored[settled_mask].copy(), spec["name"], "forward_settled"))
            if not selected.empty:
                selected_parts.append(selected)

    model_df = pd.DataFrame(model_rows)
    variant_df = pd.DataFrame(variant_rows)
    bucket_df = pd.DataFrame(bucket_rows)
    selected_df = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    daily_df = daily_context(selected_df)

    model_df.to_csv(OUT_MODEL, index=False)
    variant_df.to_csv(OUT_VARIANT, index=False)
    bucket_df.to_csv(OUT_BUCKET, index=False)
    daily_df.to_csv(OUT_DAILY, index=False)

    focus = variant_df[
        variant_df["train_spec"].isin(["enhanced_early_all", "enhanced_late_all"])
        & variant_df["variant"].eq("remaining_heat_p40_ev10")
        & variant_df["eval_window"].isin(["early", "late", "forward_settled"])
    ].copy()
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_signal_swap_stability_v1",
        "dataset": hist_stats,
        "forward_dataset": {"mechanism_rows": int(len(forward)), "source_stats": forward_stats},
        "windows": {
            "early": f"<= {EARLY_END}",
            "late": f"{LATE_START}..{LATE_END}",
            "forward": f">= {FORWARD_START}",
        },
        "focus_p40_rows": finite_or_none(focus.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "model_swap_metrics_csv": str(OUT_MODEL.relative_to(ROOT)),
            "variant_swap_performance_csv": str(OUT_VARIANT.relative_to(ROOT)),
            "score_bucket_calibration_csv": str(OUT_BUCKET.relative_to(ROOT)),
            "selected_daily_context_csv": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "signal_mechanism_not_stable_enough_for_live",
            "live_ready": False,
            "reason": "Early/late swap shows model score and p40 signal do not generalize cleanly; capped-day/model-overestimate is a mechanism gap, not just city sample noise.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, model_df, variant_df, bucket_df, daily_df), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
