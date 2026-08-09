#!/usr/bin/env python3
"""Price-conditioned residual challenger for frozen current-YES Core Carry.

The model keeps the deployed Core probability as an offset and tests whether
its remaining error varies continuously with market odds and PIT path state.
The last seven target dates are excluded from fitting and candidate selection.
This module is research-only and never changes production configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import SplineTransformer, StandardScaler

from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)
from weather_model_evaluation import (
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
)


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_core_carry_price_conditioned_challenger_v1"
RUN_ID = "historical_temporal_holdout_7d_20260809"
INPUT = ROOT / (
    "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)
PREREG = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-09-current-yes-core-carry-price-conditioned-"
    "challenger-preregistration.json"
)
REPORT = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-09-current-yes-core-carry-price-conditioned-challenger-v1.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260809
WARMUP_DATES = 8
HOLDOUT_DATES = 7
BOOTSTRAP_DRAWS = 5000
L2 = 0.5
EPS = 1e-6

CANDIDATES = (
    "core_recalibration",
    "price_spline_residual",
    "price_path_interactions",
)
PATH_FEATURES = (
    "remaining_heat_load",
    "forecast_cross_pressure",
    "warming_persistence",
    "plateau_evidence",
    "fresh_high_runway",
)
INTERACTION_FEATURES = tuple(f"market_x_{name}" for name in PATH_FEATURES)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def safe_logit(probability: pd.Series | np.ndarray) -> np.ndarray:
    value = np.clip(np.asarray(probability, dtype=float), EPS, 1 - EPS)
    return np.log(value / (1.0 - value))


def load_ledger(path: Path = INPUT) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = {
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "label",
        "market_mid",
        "p_core_no_obs_age",
        "current_bracket",
        "ten_share_executable",
        "ten_share_cost_per_share",
        "forecast_reheat_after_now_f",
        "forecast_remaining_gap_to_running_native",
        "daylight_remaining_minutes",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "reheating_transition_num",
        "strict_high_age_log",
        "same_running_max_obs_count_log",
        "minutes_since_last_strict_new_high",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"input ledger missing columns: {missing}")
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    frame["label"] = frame["label"].astype(int)
    if not frame["label"].isin([0, 1]).all():
        raise ValueError("label must be binary")
    frame["p_core_hold"] = pd.to_numeric(
        frame["p_core_no_obs_age"], errors="raise"
    ).clip(EPS, 1 - EPS)
    frame["p_market_hold"] = pd.to_numeric(
        frame["market_mid"], errors="raise"
    ).clip(EPS, 1 - EPS)
    frame["core_logit"] = safe_logit(frame["p_core_hold"])
    frame["market_logit"] = safe_logit(frame["p_market_hold"])
    return add_price_path_features(
        frame.sort_values(
            ["target_date", "city", "decision_snapshot_dt"], kind="stable"
        ).reset_index(drop=True)
    )


def add_price_path_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    daylight_h = pd.to_numeric(
        result["daylight_remaining_minutes"], errors="coerce"
    ).clip(lower=0) / 60.0
    daylight_scale = np.sqrt(daylight_h.clip(upper=15.0))
    forecast_reheat = pd.to_numeric(
        result["forecast_reheat_after_now_f"], errors="coerce"
    )
    forecast_gap = pd.to_numeric(
        result["forecast_remaining_gap_to_running_native"], errors="coerce"
    )
    trend_1h = pd.to_numeric(result["temp_trend_1h_f"], errors="coerce")
    trend_3h = pd.to_numeric(result["temp_trend_3h_f"], errors="coerce")
    transition = pd.to_numeric(
        result["reheating_transition_num"], errors="coerce"
    ).fillna(0.0)
    strict_age_log = pd.to_numeric(
        result["strict_high_age_log"], errors="coerce"
    )
    same_max_log = pd.to_numeric(
        result["same_running_max_obs_count_log"], errors="coerce"
    )
    strict_age_minutes = pd.to_numeric(
        result["minutes_since_last_strict_new_high"], errors="coerce"
    ).clip(lower=0)

    result["remaining_heat_load"] = forecast_reheat.clip(lower=0) * daylight_scale
    result["forecast_cross_pressure"] = forecast_gap.clip(lower=0) * daylight_scale
    result["warming_persistence"] = (
        trend_1h.clip(lower=0)
        + (trend_3h / 3.0).clip(lower=0)
        + transition.clip(lower=0)
    )
    result["plateau_evidence"] = (
        strict_age_log + same_max_log + (-trend_1h).clip(lower=0)
    )
    result["fresh_high_runway"] = (
        np.exp(-strict_age_minutes / 90.0)
        * daylight_scale
        * (trend_1h + 0.25 * trend_3h).clip(lower=0)
    )
    for source, target in zip(PATH_FEATURES, INTERACTION_FEATURES, strict=True):
        result[target] = result[source] * result["market_logit"]
    return result


def training_weights(frame: pd.DataFrame) -> np.ndarray:
    """Equalize dates, then city-days, then checkpoints within city-day."""

    dates = frame["target_date"].astype(str)
    city_day = dates + "|" + frame["city"].astype(str)
    date_city_days = (
        pd.DataFrame({"date": dates, "city_day": city_day})
        .drop_duplicates()
        .groupby("date")["city_day"]
        .count()
    )
    rows_per_city_day = city_day.map(city_day.value_counts()).to_numpy(float)
    cities_per_date = dates.map(date_city_days).to_numpy(float)
    weights = 1.0 / (len(date_city_days) * cities_per_date * rows_per_city_day)
    if not np.isfinite(weights).all() or weights.sum() <= 0:
        raise ValueError("invalid training weights")
    return weights / weights.sum()


def make_transformer(candidate: str) -> ColumnTransformer | None:
    if candidate == "core_recalibration":
        return None
    market_spline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "spline",
                SplineTransformer(
                    n_knots=4,
                    degree=2,
                    include_bias=False,
                    extrapolation="linear",
                ),
            ),
            ("scale", StandardScaler()),
        ]
    )
    transformers: list[tuple[str, Any, list[str]]] = [
        ("market_spline", market_spline, ["market_logit"])
    ]
    if candidate == "price_path_interactions":
        path = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
            ]
        )
        transformers.append(
            ("path_and_interactions", path, [*PATH_FEATURES, *INTERACTION_FEATURES])
        )
    elif candidate != "price_spline_residual":
        raise ValueError(f"unknown candidate: {candidate}")
    return ColumnTransformer(transformers, remainder="drop")


def fit_model(train: pd.DataFrame, candidate: str) -> dict[str, Any]:
    transformer = make_transformer(candidate)
    if transformer is None:
        transformed = np.empty((len(train), 0), dtype=float)
        names: list[str] = []
    else:
        transformed = np.asarray(transformer.fit_transform(train), dtype=float)
        names = transformer.get_feature_names_out().tolist()
    design = np.column_stack([np.ones(len(train)), transformed])
    y = train["label"].to_numpy(float)
    offset = train["core_logit"].to_numpy(float)
    weights = training_weights(train)
    penalty = np.full(design.shape[1], L2, dtype=float)
    penalty[0] = 0.01

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        linear = offset + design @ beta
        loss = float(
            np.sum(weights * (np.logaddexp(0.0, linear) - y * linear))
            + 0.5 * np.sum(penalty * beta**2)
        )
        gradient = design.T @ (weights * (expit(linear) - y)) + penalty * beta
        return loss, gradient

    fitted = minimize(
        objective,
        np.zeros(design.shape[1], dtype=float),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 2000, "ftol": 1e-12},
    )
    if not fitted.success:
        raise RuntimeError(f"model fit failed for {candidate}: {fitted.message}")
    return {
        "candidate": candidate,
        "transformer": transformer,
        "coef": np.asarray(fitted.x, dtype=float),
        "feature_names": ["intercept", *names],
        "training_rows": int(len(train)),
        "training_dates": sorted(train["target_date"].unique().tolist()),
        "optimizer_iterations": int(fitted.nit),
        "objective": float(fitted.fun),
    }


def predict_model(bundle: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    transformer = bundle["transformer"]
    if transformer is None:
        transformed = np.empty((len(frame), 0), dtype=float)
    else:
        transformed = np.asarray(transformer.transform(frame), dtype=float)
    design = np.column_stack([np.ones(len(frame)), transformed])
    probability = expit(
        frame["core_logit"].to_numpy(float) + design @ bundle["coef"]
    )
    return np.clip(probability, EPS, 1 - EPS)


def expanding_development(
    frame: pd.DataFrame, holdout_start: str
) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique().tolist())
    output: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES or target_date >= holdout_start:
            continue
        train = frame[frame["target_date"].lt(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        for candidate in CANDIDATES:
            bundle = fit_model(train, candidate)
            test[f"p_{candidate}"] = predict_model(bundle, test)
        output.append(test)
    if not output:
        raise RuntimeError("no expanding development rows")
    return pd.concat(output, ignore_index=True)


def score(frame: pd.DataFrame, probability_column: str) -> dict[str, Any]:
    return binary_score(
        frame,
        frame[probability_column].to_numpy(float),
        label_column="label",
        date_column="target_date",
    )


def compare(
    frame: pd.DataFrame, candidate_column: str, baseline_column: str
) -> dict[str, Any]:
    y = frame["label"].to_numpy(float)
    candidate = frame[candidate_column].to_numpy(float)
    baseline = frame[baseline_column].to_numpy(float)
    return {
        metric: date_block_bootstrap_delta(
            frame,
            binary_loss_values(y, candidate, metric=metric),
            binary_loss_values(y, baseline, metric=metric),
            date_column="target_date",
            draws=BOOTSTRAP_DRAWS,
            seed=SEED + (0 if metric == "brier" else 1),
        )
        for metric in ("brier", "logloss")
    }


def select_candidate(development: pd.DataFrame) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        metrics = score(development, f"p_{candidate}")
        rows.append({"candidate": candidate, **metrics})
    for metric in ("brier", "logloss"):
        for rank, row in enumerate(sorted(rows, key=lambda item: item[metric]), start=1):
            row[f"{metric}_rank"] = rank
    for row in rows:
        row["mean_rank"] = (row["brier_rank"] + row["logloss_rank"]) / 2.0
    selected = min(rows, key=lambda row: (row["mean_rank"], row["brier"]))[
        "candidate"
    ]
    return str(selected), rows


def probability_by_mid_band(frame: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [-np.inf, 0.50, 0.80, 0.90, 0.9895, np.inf]
    labels = ["below_0.50", "0.50_to_0.80", "0.80_to_0.90", "0.90_to_0.9895", "above_0.9895"]
    work = frame.copy()
    work["mid_band"] = pd.cut(
        work["p_market_hold"], bins=bins, labels=labels, right=False
    )
    rows: list[dict[str, Any]] = []
    for band, group in work.groupby("mid_band", observed=True):
        rows.append(
            {
                "mid_band": str(band),
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "challenger": score(group, "p_challenger"),
                "core": score(group, "p_core_hold"),
                "market": score(group, "p_market_hold"),
            }
        )
    return rows


def exact_bounded(frame: pd.DataFrame) -> pd.Series:
    bracket = frame["current_bracket"].astype(str).str.lower()
    return ~bracket.str.contains(r"\+|below|under|higher|above", regex=True)


def select_first_positive(
    frame: pd.DataFrame, probability_column: str, *, floor: float | None
) -> pd.DataFrame:
    cost = pd.to_numeric(frame["ten_share_cost_per_share"], errors="coerce")
    executable = frame["ten_share_executable"].fillna(False).astype(bool)
    eligible = frame[
        executable
        & exact_bounded(frame)
        & cost.notna()
        & frame[probability_column].gt(cost)
        & (True if floor is None else frame["p_market_hold"].ge(floor))
    ].copy()
    eligible["effective_cost"] = cost.loc[eligible.index]
    eligible["model_probability"] = eligible[probability_column]
    return (
        eligible.sort_values(
            ["target_date", "city", "decision_snapshot_dt"], kind="stable"
        )
        .drop_duplicates(["city", "target_date"], keep="first")
        .reset_index(drop=True)
    )


def trade_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "entries": 0,
            "dates": 0,
            "wins": 0,
            "losses": 0,
            "pnl_usd_10_shares": 0.0,
            "roi": None,
        }
    cost = frame["effective_cost"].to_numpy(float)
    pnl_per_share = frame["label"].to_numpy(float) - cost
    return {
        "entries": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "wins": int(frame["label"].sum()),
        "losses": int(frame["label"].eq(0).sum()),
        "avg_mid": float(frame["p_market_hold"].mean()),
        "avg_cost": float(cost.mean()),
        "pnl_usd_10_shares": float(10.0 * pnl_per_share.sum()),
        "roi": float(pnl_per_share.sum() / cost.sum()),
    }


def compact_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": bundle["candidate"],
        "training_rows": bundle["training_rows"],
        "training_dates": bundle["training_dates"],
        "feature_names": bundle["feature_names"],
        "coef": bundle["coef"].tolist(),
        "optimizer_iterations": bundle["optimizer_iterations"],
        "objective": bundle["objective"],
        "l2": L2,
        "offset": "frozen p_core_no_obs_age logit",
    }


def render_report(payload: dict[str, Any]) -> str:
    holdout = payload["holdout_metrics"]
    vs_core = payload["holdout_comparisons"]["core"]
    vs_market = payload["holdout_comparisons"]["market"]

    def delta_line(label: str, values: dict[str, Any]) -> str:
        brier = values["brier"]
        logloss = values["logloss"]
        return (
            f"- {label} Brier Δ `{brier['delta']:+.6f}` "
            f"CI `[{brier['ci_low']:+.6f},{brier['ci_high']:+.6f}]`；"
            f"logloss Δ `{logloss['delta']:+.6f}` "
            f"CI `[{logloss['ci_low']:+.6f},{logloss['ci_high']:+.6f}]`。"
        )

    lines = [
        "# Current-YES Core Carry：连续赔率条件 challenger v1",
        "",
        f"Status: `{payload['status']}`",
        "",
        "## 结论与动作",
        "",
        payload["headline"],
        "",
        "- 当前 live Core Carry 不变；本研究没有修改 market-mid floor、sizing 或 execution。",
        "- 最后 7 个历史 target dates 未参与拟合或候选选择，但此前研究已经看过这些结果，因此这里只称 temporal holdout，不称 clean frozen forward。",
        "- 真正 clean forward 只能从本 artifact 冻结后的新 target dates 开始。",
        "",
        "## 固定分母与选型",
        "",
        f"- 输入：{payload['denominator']['rows']:,} PIT checkpoints / {payload['denominator']['city_days']} city-days / {payload['denominator']['dates']} target dates（{payload['denominator']['date_min']}–{payload['denominator']['date_max']}）。",
        f"- expanding development OOF：{payload['development']['dates']} dates / {payload['development']['rows']} rows；temporal holdout：{payload['holdout']['dates']} dates / {payload['holdout']['rows']} rows（{payload['holdout']['date_min']}–{payload['holdout']['date_max']}）。",
        f"- 候选 K={len(CANDIDATES)}；开发窗选中 `{payload['selected_candidate']}`。",
        "- 所有 challenger 都以 frozen Core logit 为 offset；不引入赔率 hard bucket，price 只以连续 spline/interaction 进入。",
        "",
        "## Temporal holdout 概率结果",
        "",
        "| 模型 | Brier | Logloss |",
        "|---|---:|---:|",
        f"| challenger | {holdout['challenger']['brier']:.6f} | {holdout['challenger']['logloss']:.6f} |",
        f"| frozen Core | {holdout['core']['brier']:.6f} | {holdout['core']['logloss']:.6f} |",
        f"| same-row market | {holdout['market']['brier']:.6f} | {holdout['market']['logloss']:.6f} |",
        "",
        delta_line("challenger − Core", vs_core),
        delta_line("challenger − market", vs_market),
        "",
        "## 交易层次要诊断",
        "",
        "下表都是同一 temporal holdout 的 10-share full-ladder 反事实，不是 actual fills；概率 proper score 仍是主门。",
        "",
        "| selector | entries | W-L | PnL | ROI |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in payload["holdout_trade_replay"].items():
        roi = "NA" if metrics["roi"] is None else f"{metrics['roi']:+.2%}"
        lines.append(
            f"| {name} | {metrics['entries']} | {metrics['wins']}-{metrics['losses']} | "
            f"${metrics['pnl_usd_10_shares']:+.2f} | {roi} |"
        )
    lines.extend(
        [
            "",
            "## Readiness、双漏斗与证据边界",
            "",
            "- PIT state + clocks：READY；输入为原 Core frozen PIT opportunity ledger。",
            "- canonical/build identity：READY for frozen input；本轮未读取当前 critical canonical build 形成新同分母结论。",
            "- market quote + depth：READY for archived checkpoint；10-share replay只用冻结 ladder cost。",
            "- settlement/label：READY；输入行均有 binary exact-bracket label。",
            "- independent target dates：低；holdout 只有 7 dates。",
            "- clean frozen-forward：BLOCKED；temporal holdout 已被既往研究间接查看。",
            "- signal funnel：frozen PIT checkpoints → expanding development OOF → one selected candidate → 7-date holdout。",
            "- evidence funnel：same-row market + label 全覆盖；执行仅 archived 10-share counterfactual；无新 actual fill claim。",
            "- 8环覆盖：概率评估、统计推断、同分母 market baseline、历史 ladder execution；缺 clean forward、真实 fill、容量与组合相关性。",
            "",
            "## 三道门",
            "",
            f"- significance={payload['gates']['significance']}；baseline={payload['gates']['baseline']}；forward={payload['gates']['forward']}；conclusion={payload['gates']['conclusion']}。",
            "",
            "## 复现",
            "",
            "```bash",
            ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --price-conditioned-challenger",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    frame = load_ledger()
    dates = sorted(frame["target_date"].unique().tolist())
    if len(dates) <= WARMUP_DATES + HOLDOUT_DATES:
        raise RuntimeError("insufficient target dates")
    holdout_dates = dates[-HOLDOUT_DATES:]
    holdout_start = holdout_dates[0]

    development = expanding_development(frame, holdout_start)
    selected, selection = select_candidate(development)
    train = frame[frame["target_date"].lt(holdout_start)].copy()
    holdout = frame[frame["target_date"].isin(holdout_dates)].copy()
    frozen_bundle = fit_model(train, selected)
    holdout["p_challenger"] = predict_model(frozen_bundle, holdout)

    comparisons = {
        "core": compare(holdout, "p_challenger", "p_core_hold"),
        "market": compare(holdout, "p_challenger", "p_market_hold"),
    }
    holdout_metrics = {
        "challenger": score(holdout, "p_challenger"),
        "core": score(holdout, "p_core_hold"),
        "market": score(holdout, "p_market_hold"),
    }
    vs_core = comparisons["core"]
    vs_market = comparisons["market"]
    same_sign_core = all(vs_core[name]["delta"] < 0 for name in ("brier", "logloss"))
    significance = all(
        vs_core[name]["ci_high"] < 0 for name in ("brier", "logloss")
    )
    baseline = all(
        vs_market[name]["delta"] < 0 for name in ("brier", "logloss")
    )

    trade_replay: dict[str, Any] = {}
    for probability, label in (
        ("p_challenger", "challenger"),
        ("p_core_hold", "core"),
    ):
        for floor, floor_label in ((None, "no_floor"), (0.80, "floor_0.80")):
            entries = select_first_positive(
                holdout, probability, floor=floor
            )
            trade_replay[f"{label}_{floor_label}"] = trade_metrics(entries)

    if significance and baseline and same_sign_core:
        status = "historical_holdout_pass_clean_forward_required"
        conclusion = "shadow_candidate"
        headline = (
            "选中的连续赔率条件 challenger 在 7-date temporal holdout 上同时显著优于 Core，"
            "但该窗口不是 clean forward；冻结为 zero-notional forward candidate，不改 live。"
        )
    elif same_sign_core:
        status = "point_improvement_inconclusive"
        conclusion = "inconclusive"
        headline = (
            "选中的 challenger 对 Core 为同向点估改善，但 7-date temporal holdout 不足以通过显著性门；"
            "保留研究 artifact，不改 live。"
        )
    else:
        status = "historical_holdout_failed"
        conclusion = "inconclusive"
        headline = (
            "选中的 challenger 没有在 7-date temporal holdout 上同时改善 Brier 与 logloss；"
            "本版不进入 shadow，现有 Core 继续运行。"
        )

    output_dir = prepare_new_run_output(
        resolve_run_output(RESEARCH_ID, run_id=RUN_ID, explicit_output=None)
    )
    development_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "label",
        "p_core_hold",
        "p_market_hold",
        *[f"p_{candidate}" for candidate in CANDIDATES],
    ]
    holdout_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "label",
        "p_core_hold",
        "p_market_hold",
        "p_challenger",
        "ten_share_executable",
        "ten_share_cost_per_share",
    ]
    development[development_columns].to_csv(
        output_dir / "development_oof_predictions.csv", index=False
    )
    holdout[holdout_columns].to_csv(
        output_dir / "temporal_holdout_predictions.csv", index=False
    )
    pd.DataFrame(selection).to_csv(
        output_dir / "candidate_selection.csv", index=False
    )
    joblib.dump(frozen_bundle, output_dir / "frozen_challenger.joblib")

    payload = {
        "schema_version": RESEARCH_ID,
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "headline": headline,
        "denominator": {
            "rows": int(len(frame)),
            "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
            "cities": int(frame["city"].nunique()),
            "dates": int(len(dates)),
            "date_min": dates[0],
            "date_max": dates[-1],
            "input": str(INPUT.relative_to(ROOT)),
            "input_sha256": file_sha256(INPUT),
            "grain": "frozen PIT current-exact checkpoint",
        },
        "development": {
            "rows": int(len(development)),
            "dates": int(development["target_date"].nunique()),
            "date_min": str(development["target_date"].min()),
            "date_max": str(development["target_date"].max()),
        },
        "holdout": {
            "rows": int(len(holdout)),
            "dates": int(len(holdout_dates)),
            "date_min": holdout_dates[0],
            "date_max": holdout_dates[-1],
            "evidence_class": "retrospective_temporal_holdout_not_clean_forward",
        },
        "candidate_selection": selection,
        "selected_candidate": selected,
        "frozen_model": compact_bundle(frozen_bundle),
        "holdout_metrics": holdout_metrics,
        "holdout_comparisons": comparisons,
        "holdout_probability_by_mid_band": probability_by_mid_band(holdout),
        "holdout_trade_replay": trade_replay,
        "gates": {
            "significance": "PASS" if significance else "FAIL",
            "baseline": "PASS" if baseline else "FAIL",
            "forward": "NA_clean_forward_not_started",
            "conclusion": conclusion,
        },
        "readiness": {
            "pit_state_and_clocks": "READY",
            "canonical_build": "READY_frozen_input_only",
            "market_quote_and_depth": "READY_archived_checkpoint",
            "settlement_label": "READY",
            "independent_target_dates": "LOW_7_HOLDOUT_DATES",
            "clean_frozen_forward": "BLOCKED_prior_outcome_inspection",
        },
        "production_action": "none",
        "artifacts": {
            "output_dir": str(output_dir),
            "preregistration": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": file_sha256(PREREG),
            "frozen_model": str(output_dir / "frozen_challenger.joblib"),
            "frozen_model_sha256": file_sha256(output_dir / "frozen_challenger.joblib"),
        },
    }
    payload = json_ready(payload)
    RESULT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(render_report(payload), encoding="utf-8")
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "selected_candidate": selected,
                "status": status,
                "holdout_metrics": holdout_metrics,
                "holdout_comparisons": comparisons,
                "trade_replay": trade_replay,
                "report": str(REPORT.relative_to(ROOT)),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
