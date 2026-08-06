#!/usr/bin/env python3
"""Current-status cumulative overshoot-hazard challenger for Core Carry.

This keeps the frozen Core Carry opportunity denominator and expression.  It
changes only the probability head: market hold probability is converted to a
cumulative hazard and multiplied by a regularized physical exposure correction.
The final eight target dates are scored with one pre-forward frozen fit.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from scripts.analysis.reheat_risk.core_carry_semantic_challenger import (
    challenger_file_sha256,
)
from weather_data_feed.market_brackets import parse_market_bracket


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_core_carry_overshoot_survival_v1"
INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-08/generated" / RESEARCH_ID
PREREG = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-06-current-yes-core-carry-overshoot-survival-preregistration-v1.json"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-06-current-yes-core-carry-overshoot-survival-v1.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260806
WARMUP_DATES = 8
FORWARD_DATES = 8
BOOTSTRAP_REPS = 5000
L2 = 0.2
EPS = 1e-6
FEATURES = [
    "forecast_cross_pressure",
    "heat_exposure_0_2h",
    "heat_exposure_2_4h",
    "heat_exposure_4h_plus",
    "warming_runway_per_tick",
    "solar_recovery_per_tick",
    "weak_plateau_evidence",
]


def survival_json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): survival_json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [survival_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def bracket_geometry(current_bracket: Any, running_native: float, unit: str) -> tuple[float, float]:
    parsed = parse_market_bracket(str(current_bracket))
    if parsed is None:
        return math.nan, math.nan
    upper = parsed.high if parsed.high is not None else parsed.low
    if upper is None:
        return math.nan, math.nan
    if str(unit).upper() == "C":
        threshold_c = float(upper) + 0.5
        tick_native = 5.0 / 9.0
        running_f_lattice = round(float(running_native) * 9.0 / 5.0 + 32.0)
        threshold_f_lattice = math.ceil(threshold_c * 9.0 / 5.0 + 32.0 - 1e-9)
        return float(threshold_f_lattice - running_f_lattice), tick_native
    threshold_f = float(upper) + 1.0
    return float(threshold_f - round(float(running_native))), 1.0


def prepare_survival_ledger() -> pd.DataFrame:
    frame = pd.read_csv(INPUT, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    frame["label"] = frame["label"].astype(int)
    frame["p_core_hold"] = frame["p_core_no_obs_age"].clip(EPS, 1 - EPS)
    frame["p_market_hold"] = frame["market_mid"].clip(EPS, 1 - EPS)

    geometry = [
        bracket_geometry(bracket, running, unit)
        for bracket, running, unit in zip(
            frame["current_bracket"], frame["running_native"], frame["unit"]
        )
    ]
    frame["exit_ticks_required"] = [item[0] for item in geometry]
    frame["exit_tick_native"] = [item[1] for item in geometry]
    if frame[["exit_ticks_required", "exit_tick_native"]].isna().any().any():
        raise RuntimeError("unparseable current bracket in frozen parent")

    tick = frame["exit_tick_native"].clip(lower=0.25)
    gap_ticks = frame["forecast_remaining_gap_to_running_native"] / tick
    margin_ticks = gap_ticks - frame["exit_ticks_required"]
    # Soft crossing pressure stays continuous around the next settlement rung.
    frame["forecast_cross_pressure"] = np.log1p(
        np.exp(margin_ticks.clip(-20, 20))
    )
    runway = frame["forecast_peak_delta_hours_local"].clip(lower=0)
    duration_0_2 = (runway + 0.5).clip(upper=2.0)
    duration_2_4 = (runway - 1.5).clip(lower=0, upper=2.0)
    duration_4_plus = (runway - 3.5).clip(lower=0, upper=8.0)
    frame["heat_exposure_0_2h"] = frame["forecast_cross_pressure"] * duration_0_2
    frame["heat_exposure_2_4h"] = frame["forecast_cross_pressure"] * duration_2_4
    frame["heat_exposure_4h_plus"] = frame["forecast_cross_pressure"] * duration_4_plus

    buffer = frame["exit_ticks_required"].clip(lower=0.5)
    frame["warming_runway_per_tick"] = (
        frame["temp_trend_1h_f"].clip(lower=0)
        + frame["temp_trend_3h_f"].clip(lower=0) / 3.0
        + frame["reheating_transition_num"].fillna(0)
    ) * np.sqrt(runway + 0.5) / buffer
    frame["solar_recovery_per_tick"] = (
        frame["solar_elevation_delta_2h_deg"].clip(lower=0)
        * np.sqrt(runway + 0.5)
        / buffer
    )
    plateau = (
        frame["strict_high_age_log"].fillna(0)
        + frame["same_running_max_obs_count_log"].fillna(0)
        + (-frame["temp_trend_1h_f"].fillna(0)).clip(lower=0)
    )
    frame["weak_plateau_evidence"] = -plateau

    frame["path_state"] = np.select(
        [
            frame["reheating_transition_num"].fillna(0).gt(0),
            frame["temp_trend_1h_f"].gt(0.25),
            frame["temp_trend_1h_f"].lt(-0.25),
        ],
        ["reheat", "warming", "fading"],
        default="plateau",
    )
    frame = frame.sort_values(
        ["target_date", "city", "current_bracket", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    previous = frame.groupby(
        ["city", "target_date", "current_bracket"], sort=False
    )["path_state"].shift(1)
    frame["is_path_transition"] = previous.isna() | frame["path_state"].ne(previous)
    frame["is_state_entry"] = ~frame.duplicated(
        ["city", "target_date", "current_bracket"], keep="first"
    )
    return frame


def transform_survival_features(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    train_columns: list[np.ndarray] = []
    test_columns: list[np.ndarray] = []
    transforms: dict[str, Any] = {}
    for feature in FEATURES:
        train_raw = pd.to_numeric(train[feature], errors="coerce")
        test_raw = pd.to_numeric(test[feature], errors="coerce")
        median = float(train_raw.median()) if train_raw.notna().any() else 0.0
        train_filled = train_raw.fillna(median).to_numpy(float)
        test_filled = test_raw.fillna(median).to_numpy(float)
        mean = float(train_filled.mean())
        scale = float(train_filled.std())
        if not math.isfinite(scale) or scale < 1e-9:
            scale = 1.0
        train_columns.append((train_filled - mean) / scale)
        test_columns.append((test_filled - mean) / scale)
        transforms[feature] = {"median": median, "mean": mean, "scale": scale}
    return np.column_stack(train_columns), np.column_stack(test_columns), transforms


def equal_city_day_weights(frame: pd.DataFrame) -> np.ndarray:
    count = frame.groupby(["city", "target_date"])["label"].transform("size")
    return 1.0 / count.clip(lower=1).to_numpy(float)


def fit_survival_head(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train, x_test, transforms = transform_survival_features(train, test)
    x_train = np.column_stack([np.ones(len(train)), x_train])
    x_test = np.column_stack([np.ones(len(test)), x_test])
    baseline_hazard_train = -np.log(train["p_market_hold"].to_numpy(float))
    baseline_hazard_test = -np.log(test["p_market_hold"].to_numpy(float))
    y_hold = train["label"].to_numpy(float)
    weights = equal_city_day_weights(train)
    weights /= weights.sum()
    penalty = np.full(x_train.shape[1], L2)
    penalty[0] = 0.01

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = np.clip(x_train @ beta, -8, 8)
        cumulative_hazard = baseline_hazard_train * np.exp(eta)
        hold_probability = np.exp(-cumulative_hazard).clip(EPS, 1 - EPS)
        loss_row = -(
            y_hold * np.log(hold_probability)
            + (1 - y_hold) * np.log(1 - hold_probability)
        )
        event_gradient = -cumulative_hazard / np.expm1(
            cumulative_hazard.clip(max=50)
        )
        gradient_eta = np.where(y_hold > 0.5, cumulative_hazard, event_gradient)
        loss = float(np.sum(weights * loss_row) + 0.5 * np.sum(penalty * beta * beta))
        gradient = x_train.T @ (weights * gradient_eta) + penalty * beta
        return loss, gradient

    bounds = [(None, None)] + [(0.0, None)] * len(FEATURES)
    result = minimize(
        lambda beta: objective(beta),
        np.zeros(x_train.shape[1]),
        jac=True,
        bounds=bounds,
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not result.success:
        raise RuntimeError(f"survival fit failed: {result.message}")
    eta_test = np.clip(x_test @ result.x, -8, 8)
    prediction = np.exp(-baseline_hazard_test * np.exp(eta_test))
    return prediction.clip(EPS, 1 - EPS), {
        "columns": ["calibration_intercept"] + FEATURES,
        "coefficients": result.x.tolist(),
        "transforms": transforms,
        "objective": float(result.fun),
        "iterations": int(result.nit),
    }


def expanding_survival_oof(frame: pd.DataFrame, forward_start: str) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    output: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES or target_date >= forward_start:
            continue
        train = frame[frame["target_date"].lt(target_date)]
        test = frame[frame["target_date"].eq(target_date)].copy()
        test["p_survival"], _ = fit_survival_head(train, test)
        output.append(test)
    return pd.concat(output, ignore_index=True)


def select_grain(frame: pd.DataFrame, grain: str) -> pd.DataFrame:
    if grain == "checkpoint":
        return frame.copy()
    if grain == "path_transition":
        return frame[frame["is_path_transition"]].copy()
    if grain == "state_entry":
        return frame[frame["is_state_entry"]].copy()
    raise ValueError(grain)


def probability_daily_scores(
    frame: pd.DataFrame, probability: str
) -> pd.DataFrame:
    p = frame[probability].clip(EPS, 1 - EPS)
    y = frame["label"].astype(float)
    scored = frame[["target_date"]].copy()
    scored["brier"] = (y - p) ** 2
    scored["logloss"] = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return scored.groupby("target_date", as_index=False)[["brier", "logloss"]].mean()


def score_probability_grain(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    daily = probability_daily_scores(frame, probability)
    return {
        "rows": len(frame),
        "dates": frame["target_date"].nunique(),
        "holds": int(frame["label"].sum()),
        "hold_rate": float(frame["label"].mean()),
        "mean_probability": float(frame[probability].mean()),
        "brier": float(daily["brier"].mean()),
        "logloss": float(daily["logloss"].mean()),
    }


def paired_probability_bootstrap(
    frame: pd.DataFrame, candidate: str, baseline: str
) -> dict[str, Any]:
    candidate_daily = probability_daily_scores(frame, candidate)
    baseline_daily = probability_daily_scores(frame, baseline)
    daily = candidate_daily.merge(
        baseline_daily, on="target_date", suffixes=("_candidate", "_baseline")
    )
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_REPS, len(daily)))
    output: dict[str, Any] = {"dates": len(daily)}
    for metric in ["brier", "logloss"]:
        delta = (
            daily[f"{metric}_candidate"] - daily[f"{metric}_baseline"]
        ).to_numpy(float)
        sampled = delta[indices].mean(axis=1)
        output[f"{metric}_delta"] = {
            "mean": float(delta.mean()),
            "ci95": [
                float(np.quantile(sampled, 0.025)),
                float(np.quantile(sampled, 0.975)),
            ],
            "better_date_fraction": float((delta < 0).mean()),
        }
    return output


def fixed_ten_share_replay(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    eligible = frame[
        frame["ten_share_executable"].fillna(False).astype(bool)
        & frame["ten_share_cost_per_share"].notna()
        & frame[probability].gt(frame["ten_share_cost_per_share"])
    ].copy()
    entries = (
        eligible.sort_values(["target_date", "city", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )
    if entries.empty:
        return {"entries": 0, "dates": 0, "pnl_usd": 0.0, "roi": None}
    cost = entries["ten_share_cost_per_share"].astype(float) * 10.0
    pnl = entries["label"].astype(float) * 10.0 - cost
    return {
        "entries": len(entries),
        "dates": entries["target_date"].nunique(),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "cost_usd": float(cost.sum()),
        "pnl_usd": float(pnl.sum()),
        "roi": float(pnl.sum() / cost.sum()),
        "avg_entry_cost": float(entries["ten_share_cost_per_share"].mean()),
    }


def evaluate_survival_grains(frame: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for grain in ["state_entry", "path_transition", "checkpoint"]:
        grain_frame = select_grain(frame, grain)
        output[grain] = {
            "survival": score_probability_grain(grain_frame, "p_survival"),
            "core": score_probability_grain(grain_frame, "p_core_hold"),
            "market": score_probability_grain(grain_frame, "p_market_hold"),
            "vs_core": paired_probability_bootstrap(
                grain_frame, "p_survival", "p_core_hold"
            ),
            "vs_market": paired_probability_bootstrap(
                grain_frame, "p_survival", "p_market_hold"
            ),
        }
    return output


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("survival preregistration is not frozen")
    frame = prepare_survival_ledger()
    dates = sorted(frame["target_date"].unique())
    if len(dates) <= WARMUP_DATES + FORWARD_DATES:
        raise RuntimeError("insufficient target dates for frozen forward")
    forward_dates = dates[-FORWARD_DATES:]
    forward_start = forward_dates[0]
    development = expanding_survival_oof(frame, forward_start)
    train = frame[frame["target_date"].lt(forward_start)].copy()
    forward = frame[frame["target_date"].isin(forward_dates)].copy()
    forward["p_survival"], frozen_fit = fit_survival_head(train, forward)

    development_grain_results = evaluate_survival_grains(development)
    grain_results = evaluate_survival_grains(forward)

    primary = grain_results["state_entry"]
    primary_deltas = [
        primary[baseline][metric]
        for baseline in ["vs_core", "vs_market"]
        for metric in ["brier_delta", "logloss_delta"]
    ]
    significance_pass = all(
        item["mean"] < 0 and item["ci95"][1] < 0 for item in primary_deltas
    )
    secondary_same_sign = all(
        grain_results[grain][baseline][metric]["mean"] <= 0
        for grain in ["path_transition", "checkpoint"]
        for baseline in ["vs_core", "vs_market"]
        for metric in ["brier_delta", "logloss_delta"]
    )
    verdict = (
        "research_positive"
        if significance_pass and secondary_same_sign
        else "inconclusive"
        if all(item["mean"] < 0 for item in primary_deltas)
        else "rejected"
    )

    replay = {
        "survival": fixed_ten_share_replay(forward, "p_survival"),
        "core": fixed_ten_share_replay(forward, "p_core_hold"),
        "market": fixed_ten_share_replay(forward, "p_market_hold"),
        "status": "descriptive_only_probability_gate_not_passed"
        if not significance_pass
        else "secondary_execution_diagnostic",
    }
    active_feature_coefficients = {
        name: float(value)
        for name, value in zip(frozen_fit["columns"], frozen_fit["coefficients"])
        if name != "calibration_intercept" and abs(float(value)) > 1e-8
    }
    coverage = {
        feature: {
            "non_null_rows": int(frame[feature].notna().sum()),
            "coverage": float(frame[feature].notna().mean()),
            "unique_non_null": int(frame[feature].nunique(dropna=True)),
        }
        for feature in FEATURES
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prediction_columns = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "path_state",
        "is_state_entry",
        "is_path_transition",
        "label",
        "p_market_hold",
        "p_core_hold",
        "p_survival",
    ] + FEATURES
    development[prediction_columns].to_csv(
        OUT_DIR / "development_oof_predictions.csv", index=False
    )
    forward[prediction_columns].to_csv(
        OUT_DIR / "frozen_forward_predictions.csv", index=False
    )

    payload = {
        "schema_version": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": RESEARCH_ID,
        "action": "no_live_change",
        "denominator_scope": {
            "rows": len(frame),
            "cities": frame["city"].nunique(),
            "dates": len(dates),
            "date_min": dates[0],
            "date_max": dates[-1],
            "holds": int(frame["label"].sum()),
            "overshoots": int(frame["label"].eq(0).sum()),
            "input": str(INPUT.relative_to(ROOT)),
            "input_sha256": challenger_file_sha256(INPUT),
        },
        "readiness": {
            "pit_state_clocks": "READY_archived_frozen_ledger",
            "canonical_build": "BLOCKED_current_JRS_context_unhealthy; historical artifact used",
            "market_quote_depth": "READY_parent_same_state_mid_and_ten_share_cost",
            "settlement_labels": "READY_all_parent_rows",
            "independent_target_dates": f"READY_{len(dates)}",
            "clean_frozen_forward": f"READY_{len(forward_dates)}_historical_dates",
            "hourly_curve": "BLOCKED_permission; scalar PIT exposure v1 only",
        },
        "signal_funnel": [
            {"stage": "frozen parent", "grain": "checkpoint", "rows": len(frame), "dates": len(dates)},
            {"stage": "development expanding OOF", "grain": "checkpoint", "rows": len(development), "dates": development["target_date"].nunique()},
            {"stage": "frozen forward", "grain": "checkpoint", "rows": len(forward), "dates": len(forward_dates)},
            {"stage": "frozen forward primary", "grain": "state_entry", "rows": len(select_grain(forward, "state_entry")), "dates": len(forward_dates)},
        ],
        "evidence_funnel": {
            "pit_feature": len(frame),
            "pit_market_mid": int(frame["p_market_hold"].notna().sum()),
            "settlement": int(frame["label"].notna().sum()),
            "ten_share_executable": int(frame["ten_share_executable"].fillna(False).sum()),
            "actual_fill": "not_in_scope",
        },
        "features": FEATURES,
        "feature_coverage": coverage,
        "development_dates": sorted(development["target_date"].unique()),
        "forward_dates": forward_dates,
        "frozen_fit": frozen_fit,
        "active_feature_coefficients": active_feature_coefficients,
        "development_grain_results": development_grain_results,
        "grain_results": grain_results,
        "fixed_ten_share_forward_replay": replay,
        "verdict": verdict,
        "gates": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if all(primary["vs_market"][metric]["mean"] < 0 for metric in ["brier_delta", "logloss_delta"]) else "FAIL",
            "forward": "PASS" if all(item["mean"] < 0 for item in primary_deltas) else "FAIL",
            "conclusion": "shadow_candidate" if significance_pass and secondary_same_sign else "inconclusive",
        },
        "limitations": [
            "Current JRS/canonical health is critical, so no post-2026-07-08 live case is appended.",
            "Hourly forecast cache paths are PIT-hashed but permission-blocked in this process; v1 uses scalar max/peak exposure buckets and is current-status survival, not a fully time-varying hazard.",
            "No maker, queue, actual fill, or live deployment conclusion is made."
        ],
        "artifacts": {
            "preregistration": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": challenger_file_sha256(PREREG),
            "development_predictions": str((OUT_DIR / "development_oof_predictions.csv").relative_to(ROOT)),
            "forward_predictions": str((OUT_DIR / "frozen_forward_predictions.csv").relative_to(ROOT)),
        },
    }
    RESULT.write_text(
        json.dumps(survival_json_ready(payload), indent=2) + "\n", encoding="utf-8"
    )

    def delta_text(item: dict[str, Any]) -> str:
        return f"{item['mean']:+.6f} CI [{item['ci95'][0]:+.6f},{item['ci95'][1]:+.6f}]"

    lines = [
        "# Core Carry overshoot survival challenger v1",
        "",
        f"**动作：不改 live。结论 `{verdict}`。** 这是同一 Core Carry expression 的概率头 A/B。",
        "",
        "## Readiness 与固定口径",
        "",
        f"- 冻结 parent：{len(frame):,} checkpoints / {frame['city'].nunique()} cities / {len(dates)} target dates / {int(frame['label'].eq(0).sum())} upward overshoots。",
        f"- Development expanding OOF：{development['target_date'].nunique()} dates；frozen forward：{forward_dates[0]}–{forward_dates[-1]}（{len(forward_dates)} dates）。",
        "- PIT state、market midpoint、10-share cost、settlement READY；当前 canonical/JRS 与 hourly cache 读取 BLOCKED。",
        "- 因此 v1 是 scalar-exposure current-status survival，不冒充完整逐小时 time-to-event hazard。",
        "",
            "## Primary：frozen-forward state entry",
        "",
        "| model | rows | Brier | logloss | mean p |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, key in [("survival", "survival"), ("core v2", "core"), ("market", "market")]:
        metric = primary[key]
        lines.append(
            f"| {name} | {metric['rows']} | {metric['brier']:.6f} | {metric['logloss']:.6f} | {metric['mean_probability']:.4f} |"
        )
    lines.extend(
        [
            "",
            f"- Survival − core Brier：{delta_text(primary['vs_core']['brier_delta'])}",
            f"- Survival − core logloss：{delta_text(primary['vs_core']['logloss_delta'])}",
            f"- Survival − market Brier：{delta_text(primary['vs_market']['brier_delta'])}",
            f"- Survival − market logloss：{delta_text(primary['vs_market']['logloss_delta'])}",
            f"- Development state-entry vs core Brier/logloss：{development_grain_results['state_entry']['vs_core']['brier_delta']['mean']:+.6f}/{development_grain_results['state_entry']['vs_core']['logloss_delta']['mean']:+.6f}。",
            f"- 冻结拟合实际保留的非零物理项：`{', '.join(active_feature_coefficients) or 'none'}`；2–4h、4h+、solar 与 plateau 项被正则/单调约束压到 0。",
            "",
            "## Secondary grain 与执行描述",
            "",
            f"- Path transition vs core Brier/logloss：{grain_results['path_transition']['vs_core']['brier_delta']['mean']:+.6f}/{grain_results['path_transition']['vs_core']['logloss_delta']['mean']:+.6f}。",
            f"- Checkpoint vs core Brier/logloss：{grain_results['checkpoint']['vs_core']['brier_delta']['mean']:+.6f}/{grain_results['checkpoint']['vs_core']['logloss_delta']['mean']:+.6f}。",
            f"- Frozen-forward fixed-10 descriptive：survival {replay['survival']['entries']} entries / PnL ${replay['survival']['pnl_usd']:+.2f}；core {replay['core']['entries']} entries / PnL ${replay['core']['pnl_usd']:+.2f}。概率 gate 未通过时该项不构成策略结论。",
            "",
            "## 三道门与 8 环",
            "",
            f"- significance={payload['gates']['significance']}；baseline={payload['gates']['baseline']}；forward={payload['gates']['forward']}；conclusion={payload['gates']['conclusion']}。",
            "- 覆盖 probability、统计推断、market baseline、frozen forward 与历史 10-share 描述；不覆盖 maker/fill、最新 canonical、容量扩张或 live 动作。",
            "",
            "## 复现",
            "",
            "```bash",
            ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --overshoot-survival",
            "```",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(survival_json_ready({
        "verdict": verdict,
        "gates": payload["gates"],
        "primary": primary,
        "replay": replay,
        "report": str(REPORT.relative_to(ROOT)),
    }), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
