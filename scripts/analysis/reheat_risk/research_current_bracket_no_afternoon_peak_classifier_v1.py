#!/usr/bin/env python3
"""Train an ex-ante afternoon-peak classifier for current-bracket NO.

This is the missing piece behind current-bracket NO pass-through: predict,
before trading, whether the city-day high is still likely to occur in the
afternoon.  The label is actual afternoon peak, but the model features are
limited to forecast-clock/backfill context and intraday state available at the
decision hour.

Important limitation: the forecast-clock fields used here are the current
research backfill layer, not verified point-in-time previous-day issue-time
forecasts.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import research_current_bracket_no_pass_through_v1 as pass_through


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_afternoon_peak_classifier_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_SCORED = OUT_DIR / "scored_current_no_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-23-current-bracket-no-afternoon-peak-classifier-v1.md"

SEED = 20260623
STAKE_USD = pass_through.STAKE_USD

NUM_FEATURES = [
    "decision_hour_local",
    "forecast_peak_hour_local",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
    "forecast_gap_to_bracket_upper_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "decline_native",
    "distance_into_bracket_native",
    "current_native",
    "running_native",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
]
CAT_FEATURES = ["city", "unit", "forecast_clock_source"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{100.0 * float(value):+.1f}%"


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


def load_dataset() -> pd.DataFrame:
    raw = pass_through.load_feature_rows()
    df = pass_through.enrich_current_no(raw)
    df["depth5_notional"] = df["no_ask"] * df["quote_depth_ask_5c"]
    df = df[df["midday_h10_14"] & df["actual_peak_afternoon"].notna()].copy()
    df["label_afternoon_peak"] = df["actual_peak_afternoon"].astype(int)
    df["trade_base"] = df["no_ask"].between(0.10, 0.35) & df["depth5_notional"].ge(STAKE_USD)
    for col in NUM_FEATURES:
        if col not in df.columns:
            df[col] = np.nan
    for col in CAT_FEATURES:
        if col not in df.columns:
            df[col] = ""
    return df


def split_dates(df: pd.DataFrame) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    split_date = dates[split_idx - 1]
    train = df[df["target_date"].astype(str) <= split_date].copy()
    holdout = df[df["target_date"].astype(str) > split_date].copy()
    return split_date, train, holdout


def build_model(c: float) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                NUM_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                CAT_FEATURES,
            ),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(C=c, max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )


def model_metrics(pipe: Pipeline, frame: pd.DataFrame) -> dict[str, Any]:
    prob = pipe.predict_proba(frame[NUM_FEATURES + CAT_FEATURES])[:, 1]
    label = frame["label_afternoon_peak"].astype(int)
    return {
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "label_rate": float(label.mean()),
        "auc": float(roc_auc_score(label, prob)),
        "brier": float(brier_score_loss(label, prob)),
        "prob_mean": float(prob.mean()),
    }


def summarize_trade(name: str, raw: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    selected = pass_through.select_first_per_city_day(raw.copy())
    return pass_through.summarize(name, raw, selected, baseline)


def run_thresholds(df: pd.DataFrame, prob_col: str, baseline_selected: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in np.arange(0.45, 0.86, 0.05):
        mask = df["trade_base"] & df[prob_col].ge(float(threshold))
        raw = df[mask].copy()
        selected = pass_through.select_first_per_city_day(raw)
        row = pass_through.summarize(f"{prob_col}>={threshold:.2f}", raw, selected, baseline_selected)
        row["threshold"] = float(threshold)
        row["prob_col"] = prob_col
        rows.append(row)
    return pd.DataFrame(rows)


def table_lines(rows: pd.DataFrame, cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows.to_dict("records"):
        vals = []
        for col in cols:
            val = row.get(col)
            if col in {
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "train_roi",
                "baseline_roi",
                "excess_roi_vs_baseline",
                "excess_roi_ci_low",
                "excess_roi_ci_high",
            }:
                vals.append(pct(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append(str(val))
        out.append("| " + " | ".join(vals) + " |")
    return out


def write_report(payload: dict[str, Any], variants: pd.DataFrame) -> None:
    main = next(r for r in payload["variants"] if r["variant"] == "logit_c0p2_p_ge_0p50")
    base = next(r for r in payload["variants"] if r["variant"] == "baseline_ask10_35_depth5_ge5")
    lines = [
        "# Current-Bracket NO Afternoon-Peak Classifier v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{pass_through.FEATURE_ROWS.relative_to(ROOT)}` + `actual_peak_afternoon` label；输出 `{OUT_JSON.relative_to(ROOT)}`。",
        f"- 生成时间：`{payload['generated_at_utc']}`。",
        f"- 样本：h10-14 current-bracket NO states `{payload['coverage']['model_rows']}` rows / `{payload['coverage']['active_dates']}` dates / `{payload['coverage']['cities']}` cities。",
        f"- Train：`{payload['split']['train_start']}`..`{payload['split']['train_end']}`；Holdout：`{payload['split']['holdout_start']}`..`{payload['split']['holdout_end']}`。",
        "- 预测特征：forecast peak hour / GFS+ECMWF peak delta / forecast max gap / 1h-3h temp trend / running max state / humidity-wind-sky proxy / city。",
        "- 重要限制：forecast peak 来自 research backfill，不是已经核验的 point-in-time 前一日 issue forecast；因此不能直接 live。",
        "",
        "## 结论",
        "",
        (
            f"`logit_c0p2_p_ge_0p50` 选出 {main['selected_trades']} 笔 / {main['active_dates']} 天，"
            f"current-bracket NO ROI {pct(main['roi'])}，日期 bootstrap CI "
            f"[{pct(main['roi_ci_low'])}, {pct(main['roi_ci_high'])}]；"
            f"holdout ROI {pct(main['holdout_roi'])}，train ROI {pct(main['train_roi'])}。"
        ),
        "",
        (
            f"同一 ask/cap baseline `{base['variant']}` 是 {base['selected_trades']} 笔，ROI {pct(base['roi'])}，"
            f"CI [{pct(base['roi_ci_low'])}, {pct(base['roi_ci_high'])}]。classifier 相对 baseline 的 excess ROI "
            f"{pct(main['excess_roi_vs_baseline'])}，CI [{pct(main['excess_roi_ci_low'])}, {pct(main['excess_roi_ci_high'])}]。"
        ),
        "",
        (
            f"Peak classifier holdout AUC `{payload['model_metrics']['logit_c0p2']['holdout']['auc']:.3f}`，"
            f"Brier `{payload['model_metrics']['logit_c0p2']['holdout']['brier']:.3f}`。"
        ),
        "",
        (
            f"三门：significance={payload['three_gate']['significance']} / "
            f"baseline={payload['three_gate']['baseline']} / forward={payload['three_gate']['forward']}；"
            f"conclusion={payload['three_gate']['level']}。"
        ),
        "",
        "交易动作：这是一个明显更合理的版本，值得进入 zero-notional shadow / point-in-time forecast 修复；但由于前一日 forecast 口径还不是生产级 point-in-time，暂不 live。",
        "",
        "## Variant Table",
        "",
        *table_lines(
            variants[
                [
                    "variant",
                    "selected_trades",
                    "active_dates",
                    "avg_no_ask",
                    "no_win_rate",
                    "actual_peak_afternoon_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "train_roi",
                    "holdout_roi",
                    "baseline_roi",
                    "excess_roi_vs_baseline",
                ]
            ],
            [
                "variant",
                "selected_trades",
                "active_dates",
                "avg_no_ask",
                "no_win_rate",
                "actual_peak_afternoon_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "train_roi",
                "holdout_roi",
                "baseline_roi",
                "excess_roi_vs_baseline",
            ],
        ),
        "",
        "## 为什么这版不是 oracle",
        "",
        "`actual_peak_afternoon` 只作为训练/验证 label；交易筛选时使用的是 classifier score。模型特征不包含 final max、actual peak hour、settlement winner。上一版 oracle 行只是事后机制上界，这版才是在做事前识别。",
        "",
        "## 后续硬要求",
        "",
        "1. 补真正 point-in-time 的前一日 forecast issue snapshot，替换 research backfill。",
        "2. 用 live/shadow forward rows 验证同样 score 分布和真实可成交 capacity。",
        "3. 再评估 maker-first 执行；现在只是 replay best ask / depth proxy。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_dataset()
    split_date, train, holdout = split_dates(df)

    models = {
        "logit_c0p2": build_model(0.2),
        "logit_c1": build_model(1.0),
    }
    metrics: dict[str, Any] = {}
    for name, pipe in models.items():
        pipe.fit(train[NUM_FEATURES + CAT_FEATURES], train["label_afternoon_peak"].astype(int))
        df[f"{name}_p"] = pipe.predict_proba(df[NUM_FEATURES + CAT_FEATURES])[:, 1]
        metrics[name] = {
            "train": model_metrics(pipe, train),
            "holdout": model_metrics(pipe, holdout),
        }

    baseline_raw = df[df["trade_base"]].copy()
    baseline_selected = pass_through.select_first_per_city_day(baseline_raw)
    variant_rows = [summarize_trade("baseline_ask10_35_depth5_ge5", baseline_raw, None)]

    threshold_tables = []
    for model_name in models:
        threshold_tables.append(run_thresholds(df, f"{model_name}_p", baseline_selected))
    thresholds = pd.concat(threshold_tables, ignore_index=True)

    chosen = thresholds[
        (thresholds["prob_col"].eq("logit_c0p2_p"))
        & (thresholds["threshold"].sub(0.50).abs().lt(1e-9))
    ].copy()
    if chosen.empty:
        raise SystemExit("chosen threshold row missing")
    chosen_row = chosen.iloc[0].to_dict()
    chosen_row["variant"] = "logit_c0p2_p_ge_0p50"
    variant_rows.append(chosen_row)

    c1_chosen = thresholds[
        (thresholds["prob_col"].eq("logit_c1_p"))
        & (thresholds["threshold"].sub(0.50).abs().lt(1e-9))
    ].copy()
    if not c1_chosen.empty:
        c1_row = c1_chosen.iloc[0].to_dict()
        c1_row["variant"] = "logit_c1_p_ge_0p50"
        variant_rows.append(c1_row)

    variants = pd.DataFrame(variant_rows)
    variants.to_csv(OUT_VARIANTS, index=False)
    scored_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "depth5_notional",
        "label_afternoon_peak",
        "label_no_wins",
        "actual_peak_first_hour_local",
        "forecast_peak_hour_local",
        "forecast_gap_to_bracket_upper_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "logit_c0p2_p",
        "logit_c1_p",
        "trade_base",
    ]
    df[scored_cols].to_csv(OUT_SCORED, index=False)

    main_row = variants[variants["variant"].eq("logit_c0p2_p_ge_0p50")].iloc[0].to_dict()
    sig_pass = main_row.get("roi_ci_low") is not None and float(main_row.get("roi_ci_low")) > 0
    baseline_pass = main_row.get("excess_roi_ci_low") is not None and float(main_row.get("excess_roi_ci_low")) > 0
    forward_pass = main_row.get("holdout_roi") is not None and float(main_row.get("holdout_roi")) > 0
    three_gate = {
        "significance": "PASS" if sig_pass else "FAIL",
        "baseline": "PASS" if baseline_pass else "FAIL",
        "forward": "PASS" if forward_pass else "FAIL",
        "level": "shadow_candidate" if sig_pass and baseline_pass and forward_pass else "inconclusive",
        "live_blocker": "forecast features are research backfill, not verified point-in-time previous-day forecasts",
    }

    payload = finite_or_none(
        {
            "generated_at_utc": now_utc(),
            "strategy": "current_bracket_no_afternoon_peak_classifier_v1",
            "target": "predict actual afternoon peak ex-ante, then buy current-bracket NO",
            "coverage": {
                "model_rows": int(len(df)),
                "active_dates": int(df["target_date"].nunique()),
                "cities": int(df["city"].nunique()),
                "trade_base_rows": int(df["trade_base"].sum()),
                "trade_base_selected": int(len(baseline_selected)),
            },
            "split": {
                "split_date": split_date,
                "train_start": str(train["target_date"].min()),
                "train_end": str(train["target_date"].max()),
                "holdout_start": str(holdout["target_date"].min()),
                "holdout_end": str(holdout["target_date"].max()),
            },
            "features": {"numeric": NUM_FEATURES, "categorical": CAT_FEATURES},
            "model_metrics": metrics,
            "variants": variants.to_dict("records"),
            "threshold_grid": thresholds.to_dict("records"),
            "three_gate": three_gate,
            "outputs": {
                "json": str(OUT_JSON.relative_to(ROOT)),
                "markdown": str(OUT_MD.relative_to(ROOT)),
                "variant_summary": str(OUT_VARIANTS.relative_to(ROOT)),
                "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
            },
        }
    )
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, variants)
    print(
        json.dumps(
            {
                "out_json": str(OUT_JSON.relative_to(ROOT)),
                "out_md": str(OUT_MD.relative_to(ROOT)),
                "main": main_row,
                "three_gate": three_gate,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
