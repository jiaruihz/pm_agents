#!/usr/bin/env python3
"""Current-YES regime hazard v1.

This model branch is deliberately model-first:

1. Use the intraday weather regime atlas as point-in-time structure features.
2. Estimate P(current YES survives) / P(future break) with and without market
   price as a feature.
3. Turn the calibrated probability into EV only after a fresh/current YES ask.

Regime labels are features and diagnostics here, not hard trade gates.
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
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_regime_hazard_v1"
OUT_SCORED = OUT_DIR / "current_yes_regime_hazard_v1_scored_rows.csv"
OUT_METRICS = OUT_DIR / "current_yes_regime_hazard_v1_model_metrics.csv"
OUT_RULES = OUT_DIR / "current_yes_regime_hazard_v1_rule_comparison.csv"
OUT_REGIME_SLICES = OUT_DIR / "current_yes_regime_hazard_v1_regime_slices.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-regime-hazard-v1.md"

TRAIN_END = "2026-05-31"
HOLDOUT_START = "2026-06-01"
FORWARD_START = "2026-06-21"
SEED = 20260625
BOOT_REPS = 500

PIT_NUMERIC_FEATURES = [
    "decision_hour_local",
    "current_native",
    "running_native",
    "decline_native",
    "running_value",
    "current_temp_c",
    "running_max_c",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_max_native",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_max_native",
    "ecmwf_forecast_max_native",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_peak_models_agree_le_1h",
    "feature_quote_rows",
    "bracket_count",
    "outcome_count",
    "current_no_ask",
    "current_no_spread",
    "d1_no_ask",
    "d2_no_ask",
]

MARKET_FEATURES = [
    "current_yes_ask",
    "market_survive_logit",
]

CAT_FEATURES = [
    "city",
    "unit",
    "forecast_source",
    "forecast_clock_source",
    "city_family",
    "solar_window",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "composite_regime",
]

LEAKAGE_COL_HINTS = {
    "final_max_c",
    "final_max_f",
    "final_max_native",
    "final_winning_bracket",
    "remaining_heat_native",
    "forecast_error_native",
    "future_break_any",
    "future_break_step",
    "capped_day",
    "current_yes_payoff",
    "current_yes_roi",
    "current_bracket_held",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def money(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"${f:+,.2f}"


def safe_auc(y: pd.Series, p: pd.Series) -> float | None:
    mask = y.notna() & p.notna()
    if mask.sum() < 2 or y[mask].nunique() < 2:
        return None
    return float(roc_auc_score(y[mask].astype(int), p[mask].astype(float)))


def safe_brier(y: pd.Series, p: pd.Series) -> float | None:
    mask = y.notna() & p.notna()
    if mask.sum() < 2:
        return None
    return float(brier_score_loss(y[mask].astype(int), p[mask].clip(1e-6, 1 - 1e-6)))


def safe_logloss(y: pd.Series, p: pd.Series) -> float | None:
    mask = y.notna() & p.notna()
    if mask.sum() < 2 or y[mask].nunique() < 2:
        return None
    return float(log_loss(y[mask].astype(int), p[mask].clip(1e-6, 1 - 1e-6)))


def date_block_bootstrap_roi(frame: pd.DataFrame, *, reps: int = BOOT_REPS) -> dict[str, Any]:
    if frame.empty:
        return {"reps": 0, "ci95": None}
    dates = np.array(sorted(frame["target_date"].astype(str).unique()))
    if len(dates) < 2:
        return {"reps": 0, "ci95": None}
    by_date = frame.groupby("target_date")[["trade_cost", "trade_pnl"]].sum()
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(reps):
        sample = rng.choice(dates, size=len(dates), replace=True)
        sub = by_date.loc[sample]
        cost = float(sub["trade_cost"].sum())
        if cost > 0:
            vals.append(float(sub["trade_pnl"].sum() / cost))
    if not vals:
        return {"reps": 0, "ci95": None}
    return {
        "reps": len(vals),
        "ci95": [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))],
    }


def load_rows(path: Path = ATLAS_ROWS) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["current_yes_ask"] = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    df = df[df["current_yes_ask"].between(0.01, 0.999, inclusive="both")].copy()

    label = pd.to_numeric(df["current_bracket_held"], errors="coerce")
    if label.isna().all() and "current_yes_payoff" in df.columns:
        label = pd.to_numeric(df["current_yes_payoff"], errors="coerce")
    df["label_survive"] = label
    df = df[df["label_survive"].isin([0, 1])].copy()
    df["label_future_break"] = 1 - df["label_survive"].astype(int)

    for col in PIT_NUMERIC_FEATURES + MARKET_FEATURES:
        if col not in df.columns:
            df[col] = np.nan
    for col in CAT_FEATURES:
        if col not in df.columns:
            df[col] = "missing"

    ask = df["current_yes_ask"].clip(1e-6, 1 - 1e-6)
    df["market_survive_logit"] = np.log(ask / (1 - ask))
    df["period"] = np.where(
        df["target_date"].le(TRAIN_END),
        "train",
        np.where(df["target_date"].ge(FORWARD_START), "forward", "holdout"),
    )
    return df.reset_index(drop=True)


def model_pipeline(numeric_features: list[str], categorical_features: list[str]) -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=10)),
        ]
    )
    pre = ColumnTransformer(
        transformers=[
            ("num", numeric, numeric_features),
            ("cat", categorical, categorical_features),
        ],
        remainder="drop",
    )
    return Pipeline(
        steps=[
            ("pre", pre),
            ("clf", LogisticRegression(max_iter=3000, C=0.5, random_state=SEED)),
        ]
    )


def fit_models(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Pipeline]]:
    train = rows[rows["period"].eq("train")].copy()
    if train.empty:
        raise RuntimeError("empty train period")
    y = train["label_survive"].astype(int)

    model_specs = {
        "weather_regime_only": (PIT_NUMERIC_FEATURES, CAT_FEATURES),
        "market_plus_regime": (PIT_NUMERIC_FEATURES + MARKET_FEATURES, CAT_FEATURES),
    }
    out = rows.copy()
    out["p_survive_market"] = out["current_yes_ask"].clip(1e-6, 1 - 1e-6)
    models: dict[str, Pipeline] = {}
    for name, (num_features, cat_features) in model_specs.items():
        pipe = model_pipeline(num_features, cat_features)
        pipe.fit(train[num_features + cat_features], y)
        out[f"p_survive_{name}"] = pipe.predict_proba(out[num_features + cat_features])[:, 1]
        out[f"p_break_{name}"] = 1.0 - out[f"p_survive_{name}"]
        out[f"edge_{name}"] = out[f"p_survive_{name}"] - out["current_yes_ask"]
        models[name] = pipe
    out["p_break_market"] = 1.0 - out["p_survive_market"]
    out["edge_market"] = 0.0
    return out, models


def metric_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    models = {
        "market_price": "p_survive_market",
        "weather_regime_only": "p_survive_weather_regime_only",
        "market_plus_regime": "p_survive_market_plus_regime",
    }
    for period in ["train", "holdout", "forward"]:
        sub = scored[scored["period"].eq(period)].copy()
        if sub.empty:
            continue
        y = sub["label_survive"].astype(int)
        for model_name, p_col in models.items():
            p = pd.to_numeric(sub[p_col], errors="coerce")
            rows.append(
                {
                    "period": period,
                    "model": model_name,
                    "rows": int(len(sub)),
                    "dates": int(sub["target_date"].nunique()),
                    "cities": int(sub["city"].nunique()),
                    "actual_survive_rate": float(y.mean()),
                    "mean_p_survive": float(p.mean()),
                    "auc": safe_auc(y, p),
                    "brier": safe_brier(y, p),
                    "logloss": safe_logloss(y, p),
                    "avg_edge_vs_ask": float((p - sub["current_yes_ask"]).mean()),
                }
            )
    return rows


def summarize_trades(frame: pd.DataFrame, label: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": label,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": None,
            "avg_ask": None,
            "roi": None,
            "trade_cost": 0.0,
            "trade_pnl": 0.0,
            "ci95": None,
        }
    cost = float(frame["trade_cost"].sum())
    pnl = float(frame["trade_pnl"].sum())
    boot = date_block_bootstrap_roi(frame)
    return {
        "rule": label,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "win_rate": float(frame["label_survive"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_p_survive": float(frame["trade_p_survive"].mean()),
        "avg_edge": float(frame["trade_edge"].mean()),
        "trade_cost": cost,
        "trade_pnl": pnl,
        "roi": pnl / cost if cost > 0 else None,
        "ci95": boot["ci95"],
        "bootstrap_reps": boot["reps"],
    }


def rule_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rules = []
    for model in ["weather_regime_only", "market_plus_regime"]:
        for min_edge in [0.00, 0.02, 0.05]:
            p_col = f"p_survive_{model}"
            edge_col = f"edge_{model}"
            rules.append((f"{model}_edge_ge_{min_edge:.2f}", p_col, edge_col, min_edge))
    for period in ["train", "holdout", "forward"]:
        base = scored[scored["period"].eq(period)].copy()
        base = base[base["current_yes_ask"].between(0.35, 0.97, inclusive="both")].copy()
        for name, p_col, edge_col, min_edge in rules:
            sub = base[pd.to_numeric(base[edge_col], errors="coerce").ge(min_edge)].copy()
            sub["trade_p_survive"] = pd.to_numeric(sub[p_col], errors="coerce")
            sub["trade_edge"] = pd.to_numeric(sub[edge_col], errors="coerce")
            sub["trade_cost"] = sub["current_yes_ask"]
            sub["trade_pnl"] = sub["label_survive"].astype(float) - sub["current_yes_ask"]
            row = summarize_trades(sub, f"{period}::{name}")
            row["period"] = period
            row["model"] = name.split("_edge_ge_")[0]
            row["min_edge"] = min_edge
            out.append(row)
    return out


def regime_slice_rows(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in ["holdout", "forward"]:
        base = scored[scored["period"].eq(period)].copy()
        for dim in ["day_regime", "intraday_state", "composite_regime"]:
            for key, sub in base.groupby(dim, dropna=False):
                if len(sub) < 20:
                    continue
                p = pd.to_numeric(sub["p_survive_market_plus_regime"], errors="coerce")
                ask = pd.to_numeric(sub["current_yes_ask"], errors="coerce")
                pnl = sub["label_survive"].astype(float) - ask
                cost = float(ask.sum())
                rows.append(
                    {
                        "period": period,
                        "dimension": dim,
                        "bucket": str(key),
                        "rows": int(len(sub)),
                        "dates": int(sub["target_date"].nunique()),
                        "cities": int(sub["city"].nunique()),
                        "actual_survive_rate": float(sub["label_survive"].mean()),
                        "avg_ask": float(ask.mean()),
                        "avg_p_survive_market_plus_regime": float(p.mean()),
                        "avg_edge": float((p - ask).mean()),
                        "roi_buy_current_yes_all": float(pnl.sum() / cost) if cost > 0 else None,
                    }
                )
    return rows


def write_report(payload: dict[str, Any], metrics: pd.DataFrame, rules: pd.DataFrame, slices: pd.DataFrame) -> None:
    holdout_metrics = metrics[metrics["period"].eq("holdout")].copy()
    forward_metrics = metrics[metrics["period"].eq("forward")].copy()
    holdout_rules = rules[rules["period"].eq("holdout")].copy().sort_values("roi", ascending=False, na_position="last")
    forward_rules = rules[rules["period"].eq("forward")].copy().sort_values("roi", ascending=False, na_position="last")

    def metric_line(frame: pd.DataFrame, model: str) -> str:
        row = frame[frame["model"].eq(model)]
        if row.empty:
            return "`NA`"
        r = row.iloc[0]
        return f"AUC {num(r['auc'])}, Brier {num(r['brier'])}, mean edge {pct(r['avg_edge_vs_ask'])}"

    lines = [
        "# Current-YES Regime Hazard V1",
        "",
        "Status: `model_branch_shadow_only`",
        "",
        "## Human Summary",
        "",
        "这版是 current-YES 的重构原型：不再把 `day_regime` / `intraday_state` 当 hard gate，",
        "而是把它们作为 PIT 特征进入 `P(current bracket survives)` 模型。交易判断只在最后用 `p_survive - current_yes_ask` 做 EV。",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`.",
        f"- Input rows: `{payload['data']['rows']}` atlas state rows / `{payload['data']['dates']}` dates / `{payload['data']['cities']}` cities.",
        f"- Train cutoff: `<= {TRAIN_END}`; holdout: `{HOLDOUT_START}`..`2026-06-20`; forward-like: `>= {FORWARD_START}` with available labels.",
        "",
        "## Model Metrics",
        "",
        "| period | market | weather+regime only | market+weather+regime |",
        "|---|---|---|---|",
    ]
    for period, frame in [("holdout", holdout_metrics), ("forward", forward_metrics)]:
        lines.append(
            f"| {period} | {metric_line(frame, 'market_price')} | "
            f"{metric_line(frame, 'weather_regime_only')} | {metric_line(frame, 'market_plus_regime')} |"
        )

    lines += [
        "",
        "## Best Rule Snapshots",
        "",
        "| slice | rule | rows | dates | avg ask | win | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for label, frame in [("holdout", holdout_rules.head(5)), ("forward", forward_rules.head(5))]:
        for _, row in frame.iterrows():
            ci = row.get("ci95")
            ci_txt = "NA" if not isinstance(ci, list) else f"[{pct(ci[0])}, {pct(ci[1])}]"
            lines.append(
                f"| {label} | `{row['rule'].split('::', 1)[-1]}` | {int(row['rows'])} | {int(row['dates'])} | "
                f"{num(row['avg_ask'])} | {pct(row['win_rate'])} | {pct(row['roi'])} | {ci_txt} |"
            )

    top_slices = slices[
        slices["period"].eq("holdout") & slices["dimension"].isin(["day_regime", "intraday_state"])
    ].copy()
    top_slices = top_slices.sort_values("avg_edge", ascending=False).head(12)
    lines += [
        "",
        "## Regime Diagnostics",
        "",
        "| dimension | bucket | rows | survive | avg ask | model p | edge | buy-all ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in top_slices.iterrows():
        lines.append(
            f"| `{row['dimension']}` | `{row['bucket']}` | {int(row['rows'])} | "
            f"{pct(row['actual_survive_rate'])} | {num(row['avg_ask'])} | "
            f"{num(row['avg_p_survive_market_plus_regime'])} | {pct(row['avg_edge'])} | "
            f"{pct(row['roi_buy_current_yes_all'])} |"
        )

    lines += [
        "",
        "## Verdict",
        "",
        "- 这是模型路线分支原型，不接 live，不恢复 current-YES 实盘。",
        "- 这版的结构是对的：regime/state 进入模型层，hard gate 只留给数据/执行安全。",
        "- 是否值得继续，要看 holdout/forward 的 `market+weather+regime` 是否稳定超过 market baseline，且 EV 规则的日期 bootstrap CI 是否不跨 0。",
        "",
        "## Files",
        "",
        f"- Summary JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Scored rows: `{OUT_SCORED.relative_to(ROOT)}`",
        f"- Metrics: `{OUT_METRICS.relative_to(ROOT)}`",
        f"- Rule comparison: `{OUT_RULES.relative_to(ROOT)}`",
        f"- Regime slices: `{OUT_REGIME_SLICES.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = load_rows()
    scored, _models = fit_models(rows)

    metrics = pd.DataFrame(metric_rows(scored))
    rules = pd.DataFrame(rule_rows(scored))
    slices = pd.DataFrame(regime_slice_rows(scored))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    keep_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_bracket",
        "current_yes_ask",
        "label_survive",
        "period",
        "p_survive_market",
        "p_survive_weather_regime_only",
        "p_survive_market_plus_regime",
        "p_break_weather_regime_only",
        "p_break_market_plus_regime",
        "edge_weather_regime_only",
        "edge_market_plus_regime",
        *CAT_FEATURES,
    ]
    existing_keep = [c for c in keep_cols if c in scored.columns]
    scored[existing_keep].to_csv(OUT_SCORED, index=False)
    metrics.to_csv(OUT_METRICS, index=False)
    rules.to_csv(OUT_RULES, index=False)
    slices.to_csv(OUT_REGIME_SLICES, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "model_branch": "current_yes_regime_hazard_v1",
        "status": "model_branch_shadow_only",
        "input": str(ATLAS_ROWS.relative_to(ROOT)),
        "outputs": {
            "scored": str(OUT_SCORED.relative_to(ROOT)),
            "metrics": str(OUT_METRICS.relative_to(ROOT)),
            "rules": str(OUT_RULES.relative_to(ROOT)),
            "regime_slices": str(OUT_REGIME_SLICES.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
        },
        "data": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "cities": int(rows["city"].nunique()),
            "min_target_date": str(rows["target_date"].min()),
            "max_target_date": str(rows["target_date"].max()),
            "period_rows": {str(k): int(v) for k, v in rows["period"].value_counts().sort_index().items()},
        },
        "feature_policy": {
            "regime_labels_are_features_not_gates": True,
            "excluded_leakage_columns": sorted(LEAKAGE_COL_HINTS),
            "numeric_features": PIT_NUMERIC_FEATURES,
            "market_features": MARKET_FEATURES,
            "categorical_features": CAT_FEATURES,
        },
        "headline": {
            "holdout_metrics": metrics[metrics["period"].eq("holdout")].to_dict(orient="records"),
            "forward_metrics": metrics[metrics["period"].eq("forward")].to_dict(orient="records"),
            "best_holdout_rules": rules[rules["period"].eq("holdout")].sort_values(
                "roi", ascending=False, na_position="last"
            ).head(5).to_dict(orient="records"),
            "best_forward_rules": rules[rules["period"].eq("forward")].sort_values(
                "roi", ascending=False, na_position="last"
            ).head(5).to_dict(orient="records"),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload, metrics, rules, slices)
    print(json.dumps(json_ready({"summary": payload["outputs"], "data": payload["data"]}), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
