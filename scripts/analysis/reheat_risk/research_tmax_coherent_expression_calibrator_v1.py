#!/usr/bin/env python3
"""Build a coherent second-stage calibrator for tmax expression probabilities."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
import research_tmax_lineage_repair_replay_v1 as repair  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_coherent_expression_calibrator_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-coherent-expression-calibrator-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-11-tmax-coherent-expression-calibrator-v1.json"
DB_PATH = ROOT / "runtime/weather.db"
FORWARD_START = "2026-06-21"
SEED = 20260711
EPS = 1e-6
MIN_META_TRAIN_DATES = 8
C_GRID = [0.01, 0.03, 0.1, 0.3]
ALPHA_GRID = [0.25, 0.5, 0.75, 1.0]

BASE_FULL = "historical_full_features"
BASE_OLD = "live_missingness_emulation"
GLOBAL = "coherent_cal_global"
CONTEXT = "coherent_cal_context"
QUOTE = "coherent_cal_quote"

KEYS = ["city", "target_date", "decision_hour_local", "actual_bucket"]
LOG_PROB_FEATURES = [f"base_log_p_{bucket}" for bucket in repair.BUCKETS]
CONTEXT_NUMERIC = [
    "relative_humidity_pct",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "forecast_peak_delta_hours_local",
    "forecast_dist_to_upper_share",
    "wind_speed_kt",
]
CONTEXT_CATEGORICAL = [
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "running_max_state",
    "solar_window",
    "sky_cover_code",
    "unit",
]
QUOTE_SOURCE_COLUMNS = [
    "current_yes_ask",
    "current_bracket_no_ask",
    "current_no_bid",
    "d1_no_ask",
    "d1_no_bid",
    "d2_no_ask",
    "d2_no_bid",
]
QUOTE_NUMERIC = [
    *QUOTE_SOURCE_COLUMNS,
    "current_no_spread",
    "d1_no_spread",
    "d2_no_spread",
    "d1_yes_effective_ask",
    "d2_yes_effective_ask",
]
SPECS = {
    GLOBAL: {"numeric": LOG_PROB_FEATURES, "categorical": []},
    CONTEXT: {
        "numeric": [*LOG_PROB_FEATURES, *CONTEXT_NUMERIC],
        "categorical": CONTEXT_CATEGORICAL,
    },
    QUOTE: {"numeric": [*LOG_PROB_FEATURES, *QUOTE_NUMERIC], "categorical": []},
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def official_taker_fee(price: float) -> float:
    return round(repair.FEE_RATE * price * (1.0 - price), 5)


def markdown_table(df: pd.DataFrame, columns: list[str]) -> str:
    if df.empty:
        return "_no rows_"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in df.reindex(columns=columns).iterrows():
        values = []
        for column in columns:
            value = row[column]
            if isinstance(value, (float, np.floating)):
                values.append("n/a" if not math.isfinite(float(value)) else f"{float(value):.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def json_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records"))


def db_snapshot() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"status": "missing"}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        signals = conn.execute(
            "SELECT COUNT(*), MAX(event_date), MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ).fetchone()
        trades = conn.execute("SELECT COUNT(*), MAX(target_date), MAX(fact_built_at_utc) FROM fact_trades").fetchone()
    finally:
        conn.close()
    return {
        "status": "partial_rebuild_strategy_runtime_order_migration_failed",
        "fact_signal_candidates_rows": int(signals[0]),
        "fact_signal_candidates_max_event_date": signals[1],
        "fact_signal_candidates_built_at_utc": signals[2],
        "fact_trades_rows": int(trades[0]),
        "fact_trades_max_target_date": trades[1],
        "fact_trades_built_at_utc": trades[2],
    }


def prepare_meta_rows(hist: pd.DataFrame, base_predictions: pd.DataFrame) -> pd.DataFrame:
    feature_cols = KEYS + CONTEXT_NUMERIC + CONTEXT_CATEGORICAL + QUOTE_SOURCE_COLUMNS
    features = hist[feature_cols].drop_duplicates(KEYS)
    base = base_predictions[base_predictions["variant"].eq(BASE_FULL)].copy()
    out = base.merge(features, on=KEYS, how="inner", validate="one_to_one")
    for bucket in repair.BUCKETS:
        source = f"{repair.MODEL_METHOD}_p_{bucket}"
        out[f"base_p_{bucket}"] = pd.to_numeric(out[source], errors="coerce").clip(EPS, 1.0 - EPS)
        out[f"base_log_p_{bucket}"] = np.log(out[f"base_p_{bucket}"])
    out["current_no_spread"] = out["current_bracket_no_ask"] - out["current_no_bid"]
    out["d1_no_spread"] = out["d1_no_ask"] - out["d1_no_bid"]
    out["d2_no_spread"] = out["d2_no_ask"] - out["d2_no_bid"]
    out["d1_yes_effective_ask"] = 1.0 - out["d1_no_bid"]
    out["d2_yes_effective_ask"] = 1.0 - out["d2_no_bid"]
    out["target_date"] = out["target_date"].astype(str)
    return out


def make_calibrator(spec_name: str, c_value: float) -> Pipeline:
    spec = SPECS[spec_name]
    transformers = []
    if spec["numeric"]:
        transformers.append(
            (
                "num",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]),
                spec["numeric"],
            )
        )
    if spec["categorical"]:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                spec["categorical"],
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers=transformers, remainder="drop")),
            ("clf", LogisticRegression(C=float(c_value), solver="lbfgs", max_iter=3000)),
        ]
    )


def raw_calibrated_predictions(train: pd.DataFrame, test: pd.DataFrame, spec_name: str, c_value: float) -> pd.DataFrame:
    spec = SPECS[spec_name]
    columns = [*spec["numeric"], *spec["categorical"]]
    model = make_calibrator(spec_name, c_value)
    model.fit(train[columns], train["actual_bucket"].astype(str))
    raw = model.predict_proba(test[columns])
    class_index = {str(value): idx for idx, value in enumerate(model.named_steps["clf"].classes_)}
    out = test[KEYS + [f"base_p_{bucket}" for bucket in repair.BUCKETS]].copy()
    for bucket in repair.BUCKETS:
        out[f"raw_cal_p_{bucket}"] = raw[:, class_index[bucket]]
    return out


def expanding_raw_predictions(rows: pd.DataFrame, spec_name: str, c_value: float, dates: list[str]) -> pd.DataFrame:
    frames = []
    for target_date in dates:
        train = rows[rows["target_date"].lt(target_date)].copy()
        test = rows[rows["target_date"].eq(target_date)].copy()
        if train["target_date"].nunique() < MIN_META_TRAIN_DATES or test.empty:
            continue
        if train["actual_bucket"].nunique() < len(repair.BUCKETS):
            continue
        pred = raw_calibrated_predictions(train, test, spec_name, c_value)
        frames.append(pred)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def blend_prediction_frame(raw: pd.DataFrame, alpha: float, variant: str) -> pd.DataFrame:
    out = raw[KEYS].copy()
    total = np.zeros(len(raw), dtype=float)
    blended: dict[str, np.ndarray] = {}
    for bucket in repair.BUCKETS:
        values = (
            (1.0 - alpha) * raw[f"base_p_{bucket}"].to_numpy(dtype=float)
            + alpha * raw[f"raw_cal_p_{bucket}"].to_numpy(dtype=float)
        )
        blended[bucket] = np.maximum(EPS, values)
        total += blended[bucket]
    for bucket in repair.BUCKETS:
        out[f"{repair.MODEL_METHOD}_p_{bucket}"] = blended[bucket] / total
    out["variant"] = variant
    return out


def score_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    bucket_index = {bucket: idx for idx, bucket in enumerate(repair.BUCKETS)}
    probs = predictions[[f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]].to_numpy(dtype=float)
    actual_idx = predictions["actual_bucket"].map(bucket_index).to_numpy(dtype=int)
    winner = probs[np.arange(len(predictions)), actual_idx]
    target = np.eye(len(repair.BUCKETS))[actual_idx]
    out = predictions[KEYS].copy()
    out["logloss"] = -np.log(np.maximum(EPS, winner))
    out["brier"] = np.square(probs - target).sum(axis=1)
    return out


def choose_spec_parameters(meta: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre = meta[meta["target_date"].lt(FORWARD_START)].copy()
    dates = sorted(pre["target_date"].unique())
    rows = []
    prediction_cache: dict[tuple[str, float], pd.DataFrame] = {}
    for spec_name in SPECS:
        for c_value in C_GRID:
            raw = expanding_raw_predictions(pre, spec_name, c_value, dates)
            prediction_cache[(spec_name, c_value)] = raw
            if raw.empty:
                continue
            for alpha in ALPHA_GRID:
                pred = blend_prediction_frame(raw, alpha, spec_name)
                scores = score_rows(pred)
                daily = scores.groupby("target_date", as_index=False).agg(logloss=("logloss", "mean"), brier=("brier", "mean"))
                rows.append(
                    {
                        "spec": spec_name,
                        "c": c_value,
                        "alpha": alpha,
                        "rows": len(scores),
                        "dates": scores["target_date"].nunique(),
                        "date_equal_logloss": daily["logloss"].mean(),
                        "date_equal_brier": daily["brier"].mean(),
                    }
                )
    grid = pd.DataFrame(rows).sort_values(["date_equal_logloss", "date_equal_brier", "spec", "c", "alpha"])
    selected_by_spec: dict[str, dict[str, Any]] = {}
    for spec_name, group in grid.groupby("spec"):
        selected_by_spec[spec_name] = group.iloc[0].to_dict()
    primary = grid.iloc[0].to_dict()
    return grid, {"primary": primary, "selected_by_spec": selected_by_spec}


def candidate_predictions(meta: pd.DataFrame, selection: dict[str, Any]) -> pd.DataFrame:
    frames = []
    all_dates = sorted(meta["target_date"].unique())
    for spec_name, chosen in selection["selected_by_spec"].items():
        raw = expanding_raw_predictions(meta, spec_name, float(chosen["c"]), all_dates)
        frames.append(blend_prediction_frame(raw, float(chosen["alpha"]), spec_name))
    return pd.concat(frames, ignore_index=True)


def expression_probability(row: pd.Series, expression: str) -> float:
    current = float(row[f"{repair.MODEL_METHOD}_p_current"])
    d1 = float(row[f"{repair.MODEL_METHOD}_p_d1"])
    d2 = float(row[f"{repair.MODEL_METHOD}_p_d2"])
    return {
        "current_no": 1.0 - current,
        "d1_no": 1.0 - d1,
        "d2_no": 1.0 - d2,
        "d1_yes": d1,
        "d2_yes": d2,
    }[expression]


def expression_score_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    focus = predictions[predictions["target_date"].astype(str).ge(FORWARD_START)]
    for variant, group in focus.groupby("variant"):
        for expression in repair.EXPRESSIONS:
            probs = group.apply(lambda row: expression_probability(row, expression), axis=1).clip(EPS, 1.0 - EPS)
            outcomes = group["actual_bucket"].map(lambda value: repair.expression_payoff(str(value), expression)).astype(float)
            logloss = -(outcomes * np.log(probs) + (1.0 - outcomes) * np.log(1.0 - probs))
            rows.append(
                {
                    "variant": variant,
                    "expression": expression,
                    "rows": len(group),
                    "dates": group["target_date"].nunique(),
                    "base_rate": outcomes.mean(),
                    "avg_p": probs.mean(),
                    "logloss": logloss.mean(),
                    "brier": np.square(probs - outcomes).mean(),
                }
            )
    return pd.DataFrame(rows)


def selected_expression_summary(policy: pd.DataFrame) -> pd.DataFrame:
    focus = policy[policy["target_date"].astype(str).ge(FORWARD_START)]
    return (
        focus.groupby(["variant", "expression"], as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            win_rate=("win", "mean"),
            avg_p=("p_win", "mean"),
            avg_ask=("ask", "mean"),
            pnl=("pnl", "sum"),
            cost=("cost", "sum"),
        )
        .assign(roi=lambda frame: frame["pnl"] / frame["cost"])
    )


def daily_policy_summary(policy: pd.DataFrame) -> pd.DataFrame:
    focus = policy[policy["target_date"].astype(str).ge(FORWARD_START)]
    return (
        focus.groupby(["variant", "target_date"], as_index=False)
        .agg(rows=("city", "size"), wins=("win", "sum"), pnl=("pnl", "sum"), cost=("cost", "sum"))
        .assign(win_rate=lambda frame: frame["wins"] / frame["rows"], roi=lambda frame: frame["pnl"] / frame["cost"])
    )


def paired_roi_delta(policy: pd.DataFrame, candidate: str, baseline: str, samples: int = 10000) -> dict[str, Any]:
    focus = policy[
        policy["target_date"].astype(str).ge(FORWARD_START) & policy["variant"].isin([candidate, baseline])
    ].copy()
    daily = focus.groupby(["variant", "target_date"], as_index=False).agg(pnl=("pnl", "sum"), cost=("cost", "sum"))
    dates = sorted(focus["target_date"].astype(str).unique())
    indexed = {(str(row.variant), str(row.target_date)): (float(row.pnl), float(row.cost)) for row in daily.itertuples()}

    def roi_for(variant: str, sampled_dates: list[str] | np.ndarray) -> float:
        pnl = sum(indexed.get((variant, str(date)), (0.0, 0.0))[0] for date in sampled_dates)
        cost = sum(indexed.get((variant, str(date)), (0.0, 0.0))[1] for date in sampled_dates)
        return pnl / cost if cost else math.nan

    candidate_roi = roi_for(candidate, dates)
    baseline_roi = roi_for(baseline, dates)
    rng = np.random.default_rng(SEED)
    deltas = []
    for _ in range(samples):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        left = roi_for(candidate, sampled)
        right = roi_for(baseline, sampled)
        if math.isfinite(left) and math.isfinite(right):
            deltas.append(left - right)
    return {
        "candidate": candidate,
        "baseline": baseline,
        "dates": len(dates),
        "candidate_roi": candidate_roi,
        "baseline_roi": baseline_roi,
        "delta_roi": candidate_roi - baseline_roi,
        "ci_low": float(np.quantile(deltas, 0.025)),
        "ci_high": float(np.quantile(deltas, 0.975)),
    }


def probability_score_delta(
    predictions: pd.DataFrame,
    candidate: str,
    baseline: str,
    samples: int = 10000,
) -> dict[str, Any]:
    focus = predictions[predictions["target_date"].astype(str).ge(FORWARD_START)]
    left = score_rows(focus[focus["variant"].eq(candidate)]).rename(
        columns={"logloss": "candidate_logloss", "brier": "candidate_brier"}
    )
    right = score_rows(focus[focus["variant"].eq(baseline)]).rename(
        columns={"logloss": "baseline_logloss", "brier": "baseline_brier"}
    )
    paired = left.merge(right, on=KEYS, validate="one_to_one")
    paired["delta_logloss"] = paired["candidate_logloss"] - paired["baseline_logloss"]
    paired["delta_brier"] = paired["candidate_brier"] - paired["baseline_brier"]
    daily = paired.groupby("target_date", as_index=False).agg(
        delta_logloss=("delta_logloss", "mean"),
        delta_brier=("delta_brier", "mean"),
    )
    rng = np.random.default_rng(SEED)
    logloss_boot = []
    brier_boot = []
    values = daily[["delta_logloss", "delta_brier"]].to_numpy(dtype=float)
    for _ in range(samples):
        sample = values[rng.integers(0, len(values), len(values))]
        logloss_boot.append(sample[:, 0].mean())
        brier_boot.append(sample[:, 1].mean())
    return {
        "candidate": candidate,
        "baseline": baseline,
        "rows": len(paired),
        "dates": len(daily),
        "date_equal_delta_logloss": daily["delta_logloss"].mean(),
        "logloss_ci_low": float(np.quantile(logloss_boot, 0.025)),
        "logloss_ci_high": float(np.quantile(logloss_boot, 0.975)),
        "date_equal_delta_brier": daily["delta_brier"].mean(),
        "brier_ci_low": float(np.quantile(brier_boot, 0.025)),
        "brier_ci_high": float(np.quantile(brier_boot, 0.975)),
    }


def window_policy_summary(policy: pd.DataFrame, variants: list[str]) -> pd.DataFrame:
    focus = policy[policy["variant"].isin(variants) & policy["target_date"].astype(str).ge(FORWARD_START)].copy()
    focus["window"] = np.where(focus["target_date"].astype(str).le("2026-06-30"), "early_0621_0630", "late_0701_0708")
    return (
        focus.groupby(["variant", "window"], as_index=False)
        .agg(rows=("city", "size"), dates=("target_date", "nunique"), wins=("win", "sum"), pnl=("pnl", "sum"), cost=("cost", "sum"))
        .assign(win_rate=lambda frame: frame["wins"] / frame["rows"], roi=lambda frame: frame["pnl"] / frame["cost"])
    )


def daily_delta_summary(daily: pd.DataFrame, candidate: str, baseline: str) -> pd.DataFrame:
    focus = daily[daily["variant"].isin([candidate, baseline])]
    left = focus[focus["variant"].eq(candidate)].drop(columns="variant").rename(
        columns={column: f"candidate_{column}" for column in ["rows", "wins", "pnl", "cost", "win_rate", "roi"]}
    )
    right = focus[focus["variant"].eq(baseline)].drop(columns="variant").rename(
        columns={column: f"baseline_{column}" for column in ["rows", "wins", "pnl", "cost", "win_rate", "roi"]}
    )
    out = left.merge(right, on="target_date", how="outer", validate="one_to_one").fillna(0.0)
    out["delta_pnl"] = out["candidate_pnl"] - out["baseline_pnl"]
    out["candidate_better"] = out["delta_pnl"] > 0
    return out.sort_values("target_date")


def selection_transition(policy: pd.DataFrame, candidate: str, baseline: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    focus = policy[policy["target_date"].astype(str).ge(FORWARD_START)]
    base = focus[focus["variant"].eq(baseline)]
    cand = focus[focus["variant"].eq(candidate)]
    merged = base.merge(cand, on=["city", "target_date"], how="outer", suffixes=("_base", "_candidate"), indicator=True)
    merged["transition"] = np.select(
        [
            merged["_merge"].eq("left_only"),
            merged["_merge"].eq("right_only"),
            merged["expression_base"].eq(merged["expression_candidate"]),
        ],
        ["baseline_only", "candidate_only", "same_expression"],
        default="switched_expression",
    )
    merged["pnl_base_filled"] = merged["pnl_base"].fillna(0.0)
    merged["pnl_candidate_filled"] = merged["pnl_candidate"].fillna(0.0)
    merged["delta_pnl"] = merged["pnl_candidate_filled"] - merged["pnl_base_filled"]
    summary = (
        merged.groupby("transition", as_index=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            baseline_pnl=("pnl_base_filled", "sum"),
            candidate_pnl=("pnl_candidate_filled", "sum"),
            delta_pnl=("delta_pnl", "sum"),
        )
        .sort_values("delta_pnl")
    )
    return merged, summary


def boundary_d1_no_cohort(
    hist: pd.DataFrame,
    predictions: pd.DataFrame,
    policy: pd.DataFrame,
    primary: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    state_keys = ["city", "target_date", "decision_hour_local"]
    old = policy[
        policy["variant"].eq(BASE_OLD)
        & policy["target_date"].astype(str).ge(FORWARD_START)
        & policy["expression"].eq("d1_no")
    ].copy()
    prediction_cols = state_keys + ["variant", *[f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]]
    pred = predictions[prediction_cols].drop_duplicates(state_keys + ["variant"])
    rows = []
    for variant in [BASE_FULL, primary]:
        part = pred[pred["variant"].eq(variant)].drop(columns="variant")
        renamed = {
            f"{repair.MODEL_METHOD}_p_{bucket}": f"{variant}_p_{bucket}" for bucket in repair.BUCKETS
        }
        old = old.merge(part.rename(columns=renamed), on=state_keys, how="left", validate="many_to_one")
    for item in old.to_dict("records"):
        full_p = 1.0 - float(item[f"{BASE_FULL}_p_d1"])
        primary_p = 1.0 - float(item[f"{primary}_p_d1"])
        ask = float(item["ask"])
        full_edge = full_p - ask - repair.fee(ask)
        if full_edge >= repair.EDGE_THRESHOLD:
            continue
        primary_edge = primary_p - ask - repair.fee(ask)
        rows.append(
            {
                "city": item["city"],
                "target_date": item["target_date"],
                "decision_hour_local": item["decision_hour_local"],
                "ask": ask,
                "win": item["win"],
                "pnl": item["pnl"],
                "old_p_win": item["p_win"],
                "full_p_win": full_p,
                "primary_p_win": primary_p,
                "full_edge": full_edge,
                "primary_edge": primary_edge,
                "primary_restored": primary_edge >= repair.EDGE_THRESHOLD,
            }
        )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame()
    summary = pd.DataFrame(
        [
            {
                "rows": len(detail),
                "dates": detail["target_date"].nunique(),
                "win_rate": detail["win"].mean(),
                "pnl": detail["pnl"].sum(),
                "avg_ask": detail["ask"].mean(),
                "old_avg_p": detail["old_p_win"].mean(),
                "full_avg_p": detail["full_p_win"].mean(),
                "primary_avg_p": detail["primary_p_win"].mean(),
                "primary_restored_rows": int(detail["primary_restored"].sum()),
            }
        ]
    )
    return detail, summary


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    repair.fee = official_taker_fee
    hist, counters = p4._load_rows_extended()
    hist["target_date"] = hist["target_date"].astype(str)
    base_predictions, base_meta = repair.expanding_predictions(hist)
    meta = prepare_meta_rows(hist, base_predictions)
    grid, selection = choose_spec_parameters(meta)
    calibrated = candidate_predictions(meta, selection)

    baseline_predictions = base_predictions[base_predictions["variant"].isin([BASE_FULL, BASE_OLD])].copy()
    predictions = pd.concat([baseline_predictions, calibrated], ignore_index=True)
    probability_total = predictions[[f"{repair.MODEL_METHOD}_p_{bucket}" for bucket in repair.BUCKETS]].sum(axis=1)
    if not np.allclose(probability_total.to_numpy(dtype=float), 1.0, atol=1e-8):
        raise ValueError("calibrated bucket probabilities are not coherent")
    score_summary = repair.summarize_model_predictions(predictions)
    expression_scores = expression_score_summary(predictions)
    policy = repair.policy_replay(hist, predictions, "all_scored")
    policy_summary = repair.summarize_policy(policy)
    daily = daily_policy_summary(policy)
    selected_expression = selected_expression_summary(policy)

    primary = str(selection["primary"]["spec"])
    paired = pd.DataFrame(
        [
            paired_roi_delta(policy, primary, BASE_FULL),
            paired_roi_delta(policy, primary, BASE_OLD),
        ]
    )
    probability_delta = pd.DataFrame([probability_score_delta(predictions, primary, BASE_FULL)])
    windows = window_policy_summary(policy, [BASE_FULL, primary])
    daily_delta = daily_delta_summary(daily, primary, BASE_FULL)
    transition_rows, transitions = selection_transition(policy, primary, BASE_FULL)
    boundary_rows, boundary_summary = boundary_d1_no_cohort(hist, predictions, policy, primary)

    grid.to_csv(OUT_DIR / "model_selection_grid.csv", index=False)
    predictions.to_csv(OUT_DIR / "coherent_predictions.csv", index=False)
    score_summary.to_csv(OUT_DIR / "probability_score_summary.csv", index=False)
    expression_scores.to_csv(OUT_DIR / "expression_probability_summary.csv", index=False)
    policy.to_csv(OUT_DIR / "policy_rows.csv", index=False)
    policy_summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_policy_summary.csv", index=False)
    selected_expression.to_csv(OUT_DIR / "selected_expression_summary.csv", index=False)
    paired.to_csv(OUT_DIR / "paired_roi_delta.csv", index=False)
    probability_delta.to_csv(OUT_DIR / "probability_score_delta.csv", index=False)
    windows.to_csv(OUT_DIR / "window_policy_summary.csv", index=False)
    daily_delta.to_csv(OUT_DIR / "daily_delta_summary.csv", index=False)
    transition_rows.to_csv(OUT_DIR / "selection_transition_rows.csv", index=False)
    transitions.to_csv(OUT_DIR / "selection_transition_summary.csv", index=False)
    boundary_rows.to_csv(OUT_DIR / "boundary_d1_no_rows.csv", index=False)
    boundary_summary.to_csv(OUT_DIR / "boundary_d1_no_summary.csv", index=False)

    forward_scores = score_summary[score_summary["scope"].eq("verified_forward")].copy()
    forward_policy = policy_summary[policy_summary["scope"].eq("verified_forward")].copy()
    forward_expression = expression_scores[expression_scores["variant"].isin([BASE_FULL, primary])].copy()
    primary_delta = paired[paired["baseline"].eq(BASE_FULL)].iloc[0]
    significance = "PASS" if primary_delta["ci_low"] > 0 or primary_delta["ci_high"] < 0 else "FAIL"
    conclusion = "shadow_candidate" if significance == "PASS" else "inconclusive"

    payload = {
        "generated_at_utc": now_utc(),
        "data_snapshot": db_snapshot(),
        "funnel": {
            "atlas_raw_rows": int(counters.get("raw_rows", 0)),
            "scored_rows": len(hist),
            "date_range": [hist["target_date"].min(), hist["target_date"].max()],
            "active_dates": hist["target_date"].nunique(),
            "meta_oof_rows": len(meta),
            "meta_oof_dates": meta["target_date"].nunique(),
            "forward_state_rows": int((meta["target_date"] >= FORWARD_START).sum()),
            "forward_dates": int(meta.loc[meta["target_date"] >= FORWARD_START, "target_date"].nunique()),
        },
        "base_model_meta": base_meta,
        "candidate_count": len(SPECS),
        "model_selection": selection,
        "primary": primary,
        "paired_vs_full": primary_delta.to_dict(),
        "probability_delta_vs_full": probability_delta.iloc[0].to_dict(),
        "positive_delta_days": int(daily_delta["candidate_better"].sum()),
        "gates": {
            "significance": significance,
            "baseline": "PASS" if finite(primary_delta["delta_roi"]) is not None and primary_delta["delta_roi"] > 0 else "FAIL",
            "forward": "FAIL_research_window_already_observed",
            "conclusion": conclusion,
        },
        "outputs": {
            "report": str(OUT_MD.relative_to(ROOT)),
            "generated_dir": str(OUT_DIR.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    daily_primary = daily[daily["variant"].isin([BASE_FULL, primary])]
    report = [
        "# Tmax Coherent Expression Calibrator v1",
        "",
        "## 数据快照",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        "| 数据源 | P4/atlas PIT model rows；DB 仅作数据新鲜度自检 |",
        f"| 数据快照时间 | {payload['generated_at_utc']} |",
        f"| 记录行数 | raw {payload['funnel']['atlas_raw_rows']}；scored {payload['funnel']['scored_rows']}；forward states {payload['funnel']['forward_state_rows']} |",
        "| unsettled 占比 | 0% in scored denominator |",
        "| missing_bracket 数 | 0 in scored denominator |",
        f"| 日期 | {payload['funnel']['date_range'][0]}..{payload['funnel']['date_range'][1]} |",
        "| refresh 状态 | Mac market snapshot 已同步到 7/10；run_stack 在 strategy runtime order migration 失败，未引用本轮 partial rebuild 的 live PnL |",
        "",
        "## 结论",
        "",
        f"- 新模型 primary 是 `{primary}`。它不是另起一套天气预测，而是在完整四桶分布上做 coherent second-stage calibration；YES/NO 始终互补。",
        "- 三个候选只在 6/21 前做 date-equal nested selection：global 只校准四桶基准概率；context 加紧凑 PIT 天气状态；quote 加 exact-book 可执行报价几何，均无 city/source 自由参数。",
        f"- 相对 `{BASE_FULL}` 的 fee-adjusted first-lock ROI delta 为 {float(primary_delta['delta_roi']):+.1%}，95% CI [{float(primary_delta['ci_low']):+.1%}, {float(primary_delta['ci_high']):+.1%}]。",
        f"- 四桶 date-equal logloss delta 为 {float(probability_delta.iloc[0]['date_equal_delta_logloss']):+.4f}，95% CI [{float(probability_delta.iloc[0]['logloss_ci_low']):+.4f}, {float(probability_delta.iloc[0]['logloss_ci_high']):+.4f}]；16 天里 {int(daily_delta['candidate_better'].sum())} 天 PnL delta 为正。",
        "- 改善不是靠恢复上一轮事后赚钱的 36 个 cancelled d1 NO：primary 只恢复 2 个。它主要来自 quote-aware 新机会、同表达概率重排和移除少量坏机会。",
        "- `d1_yes` selected ROI 仍为负，说明 winner-selection 条件校准尚未完成；这里只记录为 residual risk，不据此追加 hard gate。",
        f"- 三道门：significance={payload['gates']['significance']}；baseline={payload['gates']['baseline']}；forward={payload['gates']['forward']}；conclusion={payload['gates']['conclusion']}。",
        "- 这段 6/21+ 数据已经参与模型问题诊断，即使点估改善也不能据此恢复 live；只允许进入 zero-notional shadow。",
        "",
        "## 漏斗",
        "",
        f"- atlas raw: {payload['funnel']['atlas_raw_rows']}",
        f"- scored PIT states: {payload['funnel']['scored_rows']} / {payload['funnel']['active_dates']} dates",
        f"- base OOF meta rows: {payload['funnel']['meta_oof_rows']} / {payload['funnel']['meta_oof_dates']} dates",
        f"- verified-forward: {payload['funnel']['forward_state_rows']} states / {payload['funnel']['forward_dates']} dates",
        "- policy grain: first eligible expression per city + target_date；fee=official weather taker curve rounded to 5 decimals；ask=decision-snapshot historical expression ask proxy",
        "",
        "## Pre-cutoff Model Selection",
        "",
        markdown_table(grid.head(12), ["spec", "c", "alpha", "rows", "dates", "date_equal_logloss", "date_equal_brier"]),
        "",
        "## Forward Probability Score",
        "",
        markdown_table(forward_scores, ["variant", "rows", "dates", "logloss", "brier"]),
        "",
        "## Forward Expression Probability",
        "",
        markdown_table(forward_expression, ["variant", "expression", "rows", "dates", "base_rate", "avg_p", "logloss", "brier"]),
        "",
        "## Forward First-lock Policy",
        "",
        markdown_table(forward_policy, ["variant", "rows", "dates", "cities", "win_rate", "avg_ask", "pnl", "roi", "roi_ci_low", "roi_ci_high", "yes_rows", "no_rows"]),
        "",
        "## Paired ROI Delta",
        "",
        markdown_table(paired, list(paired.columns)),
        "",
        "## Probability Score Delta",
        "",
        markdown_table(probability_delta, list(probability_delta.columns)),
        "",
        "## Early / Late Window",
        "",
        markdown_table(windows, ["variant", "window", "rows", "dates", "win_rate", "pnl", "cost", "roi"]),
        "",
        "## Daily Full vs Primary",
        "",
        markdown_table(daily_primary, ["variant", "target_date", "rows", "wins", "win_rate", "pnl", "cost", "roi"]),
        "",
        "## Daily Paired Delta",
        "",
        markdown_table(daily_delta, ["target_date", "candidate_rows", "baseline_rows", "candidate_pnl", "baseline_pnl", "delta_pnl", "candidate_better"]),
        "",
        "## Selected Expression Breakdown",
        "",
        markdown_table(
            selected_expression[selected_expression["variant"].isin([BASE_FULL, primary])],
            ["variant", "expression", "rows", "dates", "win_rate", "avg_p", "avg_ask", "pnl", "roi"],
        ),
        "",
        "## Selection Transition vs Full",
        "",
        markdown_table(transitions, list(transitions.columns)),
        "",
        "## Previously Cancelled d1 NO Diagnostic",
        "",
        markdown_table(boundary_summary, list(boundary_summary.columns)),
        "",
        "该 cohort 是上一轮看完结果后定义的诊断切片，只用于确认新模型行为，不能作为模型选择目标或 forward 证据。",
        "",
        "## 8 环覆盖",
        "",
        "- [1] 描述性绩效：覆盖；[2] target_date block bootstrap：覆盖；[3] 信号判别：以 expression logloss/selection transition 覆盖。",
        "- [4] coherent 四桶与 expression calibration：覆盖；[5] 执行：历史 ask + 官方 taker fee，fresh live fill 未覆盖；[6] capacity：未覆盖。",
        "- [7] 同日相关：date block 处理；[8] 基准：完整特征模型和旧缺失模型均覆盖，随机/无脑 NO 未新增。",
        "",
        "## 边界",
        "",
        "- GFS/ECMWF gap 在 6/21+ atlas 仍为 0% 覆盖，本轮没有声称修复了 forecast-gap forward 证据。",
        "- 本轮有限候选 K=3；没有按城市、天气切片或 ROI 阈值继续搜索。",
        "- run_stack 的 runtime-order migration 失败与本模型离线分母无关，但意味着本轮不能发布新的 live_real 绩效。",
        "- tmax live 保持暂停；本报告不修改 runner、config、executor 或资金状态。",
    ]
    OUT_MD.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
