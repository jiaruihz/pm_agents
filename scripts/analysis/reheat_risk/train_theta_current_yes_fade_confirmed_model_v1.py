#!/usr/bin/env python3
"""Train a fade-confirmed specialist probability artifact for current YES.

This artifact is scored with the same shared helper used by live.
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
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.current_yes_model import (  # noqa: E402
    DEFAULT_FADE_GATE,
    fade_live_like_mask,
    fade_training_population_mask,
    score_artifact,
)

FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
BASE_MODEL = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1"
OUT_ARTIFACT = OUT_DIR / "fade_confirmed_model.json"
OUT_METRICS = OUT_DIR / "model_metrics.csv"
OUT_SELECTED = OUT_DIR / "live_like_selected_holdout.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-current-yes-fade-confirmed-specialist-model-v1.md"
SEED = 20260618

BASE_FEATURES = [
    "decision_hour_local",
    "month",
    "decline_c",
    "decline_native",
    "decline_band",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "running_value",
    "current_native",
    "running_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "d_dwpf_3h",
    "d_relh_3h",
]
PRICE_FEATURES = ["yes_current_ask", "log_yes_size", "d1_no_ask", "ask_gap_d1_no_minus_yes"]
CAT_FEATURES = ["city", "unit"]
NUMERIC_FEATURES = BASE_FEATURES + PRICE_FEATURES


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def normalize_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def load_rows() -> pd.DataFrame:
    df = pd.read_csv(FEATURE_ROWS)
    for col in ("current_yes_wins", "has_d1_no", "d1_no_loses", "is_f"):
        if col in df.columns:
            df[col] = normalize_bool(df[col])
    df["label_yes_wins"] = df["label_yes_wins"].astype(int)
    return df


def pipeline() -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
        ]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(max_iter=3000, C=0.8, random_state=SEED))])


def artifact_from_model(model: Pipeline, train: pd.DataFrame) -> dict[str, Any]:
    pre = model.named_steps["pre"]
    num_pipe = pre.named_transformers_["num"]
    cat = pre.named_transformers_["cat"]
    clf = model.named_steps["model"]
    categories = [[str(v) for v in values] for values in cat.categories_]
    feature_names = list(NUMERIC_FEATURES)
    for feature, cats in zip(CAT_FEATURES, categories, strict=True):
        feature_names.extend([f"cat__{feature}_{cat_value}" for cat_value in cats])
    return {
        "artifact_type": "theta_current_yes_fade_confirmed_logistic_v1",
        "strategy_id": "theta_current_yes_fade_confirmed_specialist_v1",
        "label": "current_yes_wins",
        "source_feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
        "trained_at_utc": now_utc(),
        "training_filter": DEFAULT_FADE_GATE.training_filter_label,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CAT_FEATURES,
        "numeric_medians": num_pipe.named_steps["imputer"].statistics_.tolist(),
        "numeric_means": num_pipe.named_steps["scale"].mean_.tolist(),
        "numeric_scales": num_pipe.named_steps["scale"].scale_.tolist(),
        "categories": categories,
        "feature_names": feature_names,
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "train_rows": int(len(train)),
        "train_dates": int(train["target_date"].nunique()),
        "train_positive_rate": float(train["label_yes_wins"].mean()),
        "sklearn_spec": {
            "model": "LogisticRegression(max_iter=3000, C=0.8)",
            "numeric_preprocess": "median_impute_then_standard_scale",
            "categorical_preprocess": "one_hot_handle_unknown_ignore",
            "random_state": SEED,
        },
    }


def metric_row(name: str, frame: pd.DataFrame, p_col: str) -> dict[str, Any]:
    y = frame["label_yes_wins"].to_numpy(dtype=int)
    p = np.clip(frame[p_col].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    return {
        "model": name,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "actual_rate": float(y.mean()) if len(y) else None,
        "mean_pred": float(p.mean()) if len(y) else None,
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
        "brier": float(brier_score_loss(y, p)) if len(y) else None,
        "logloss": float(log_loss(y, p)) if len(np.unique(y)) > 1 else None,
    }


def summarize_trades(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"orders": 0, "active_dates": 0, "yes_cost": 0.0, "yes_pnl": 0.0, "yes_roi": None, "yes_win_rate": None}
    yes_cost = float(frame["yes_current_ask"].sum())
    yes_pnl = float(frame["yes_current_pnl"].sum())
    return {
        "orders": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "avg_yes_ask": float(frame["yes_current_ask"].mean()),
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "yes_win_rate": float(frame["current_yes_wins"].mean()),
    }


def write_markdown(payload: dict[str, Any]) -> None:
    comp = payload["live_like_comparison"]
    lines = [
        "# Current YES Fade-Confirmed Specialist Model v1",
        "",
        "Status: shadow-artifact",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `current_yes_fade_confirmed_specialist_v1` = 已经从 running max 回落后，当前最高温 bracket 最终是否守住。",
        "",
        "## Human Summary",
        "",
        "这不是新的信号接收层；它是 fade-confirmed 分支的候选概率层。METAR、盘口、fresh-book guard、city-day cap 继续共用现有 current-YES runner。",
        "",
        "线上区别：`fade_confirmed` 会同时记录 base p 和本 artifact 的 shadow p；默认真钱决策仍用通用 v8/v9 artifact。只有显式设置 `FADE_CONFIRMED_MODEL_MODE=specialist` 才会用本 artifact 替换 `p_yes_win`。",
        "",
        "## Training Slice",
        "",
        f"- rows: {payload['coverage']['train_rows']} train / {payload['coverage']['holdout_rows']} holdout",
        f"- active dates: {payload['coverage']['train_dates']} train / {payload['coverage']['holdout_dates']} holdout",
        f"- filter: {DEFAULT_FADE_GATE.markdown_filter_label}",
        "",
        "## Live-Like Holdout Comparison",
        "",
        "| model | orders | dates | YES ROI | win rate | avg ask |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("base_current_yes_model", "fade_confirmed_specialist"):
        row = comp[name]
        roi = "n/a" if row["yes_roi"] is None else f"{row['yes_roi']:.1%}"
        win = "n/a" if row["yes_win_rate"] is None else f"{row['yes_win_rate']:.1%}"
        ask = "n/a" if row.get("avg_yes_ask") is None else f"{row['avg_yes_ask']:.3f}"
        lines.append(f"| {name} | {row['orders']} | {row['active_dates']} | {roi} | {win} | {ask} |")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- artifact: `{payload['outputs']['artifact']}`",
            f"- metrics: `{payload['outputs']['metrics']}`",
            f"- selected holdout rows: `{payload['outputs']['selected_holdout']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_rows()
    fade = df[fade_training_population_mask(df, DEFAULT_FADE_GATE)].copy()
    train = fade[fade["period"].eq("train")].copy()
    holdout = fade[fade["period"].eq("holdout")].copy()
    model = pipeline()
    model.fit(train[NUMERIC_FEATURES + CAT_FEATURES], train["label_yes_wins"])
    artifact = artifact_from_model(model, train)
    OUT_ARTIFACT.write_text(json.dumps(json_ready(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    base_artifact = json.loads(BASE_MODEL.read_text(encoding="utf-8"))
    scored = holdout.copy()
    scored["p_base"] = score_artifact(scored, base_artifact)
    scored["p_fade_specialist"] = score_artifact(scored, artifact)
    metrics = pd.DataFrame(
        [
            metric_row("base_current_yes_model", scored, "p_base"),
            metric_row("fade_confirmed_specialist", scored, "p_fade_specialist"),
        ]
    )
    metrics.to_csv(OUT_METRICS, index=False)

    def live_like(p_col: str) -> pd.DataFrame:
        out = scored[fade_live_like_mask(scored, p_col, DEFAULT_FADE_GATE)].copy()
        return out.sort_values(["target_date", "city", "snapshot_ts_utc"])

    base_live = live_like("p_base")
    fade_live = live_like("p_fade_specialist")
    selected = pd.concat(
        [
            base_live.assign(selected_model="base_current_yes_model", p_model=base_live["p_base"]),
            fade_live.assign(selected_model="fade_confirmed_specialist", p_model=fade_live["p_fade_specialist"]),
        ],
        ignore_index=True,
    )
    selected.to_csv(OUT_SELECTED, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "artifact_type": artifact["artifact_type"],
        "coverage": {
            "source_rows": int(len(df)),
            "fade_rows": int(len(fade)),
            "train_rows": int(len(train)),
            "train_dates": int(train["target_date"].nunique()),
            "holdout_rows": int(len(holdout)),
            "holdout_dates": int(holdout["target_date"].nunique()),
        },
        "holdout_metrics": metrics.to_dict(orient="records"),
        "live_like_comparison": {
            "base_current_yes_model": summarize_trades(base_live),
            "fade_confirmed_specialist": summarize_trades(fade_live),
        },
        "outputs": {
            "artifact": str(OUT_ARTIFACT.relative_to(ROOT)),
            "metrics": str(OUT_METRICS.relative_to(ROOT)),
            "selected_holdout": str(OUT_SELECTED.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(json_ready(payload))
    print(json.dumps(json_ready(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
