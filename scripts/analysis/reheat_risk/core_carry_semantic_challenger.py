#!/usr/bin/env python3
"""Compact semantic challenger implementation for frozen current-YES Core Carry.

The archived opportunity ledger is the fixed PIT denominator. Candidate choice
uses only expanding development OOF; the final eight target dates are scored by
one model fit before the forward window. This script never changes production.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import SplineTransformer, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_core_carry_semantic_challenger_v3"
OUT_DIR = ROOT / "docs/analysis/2026-08/generated" / RESEARCH_ID
PREREG = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-06-current-yes-core-carry-semantic-challenger-v3-preregistration.json"
)
INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-06-current-yes-core-carry-semantic-challenger-v3.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260806
WARMUP_DATES = 8
FORWARD_DATES = 8
BOOTSTRAP_REPS = 5000
L2 = 0.2
EPS = 1e-6

RAW_SEMANTIC = [
    "strict_high_age_log",
    "same_running_max_obs_count_log",
    "forecast_remaining_gap_to_running_native",
    "forecast_reheat_after_now_f",
    "forecast_peak_delta_hours_local",
    "solar_elevation_delta_2h_deg",
    "daylight_remaining_minutes",
    "temp_curve_acceleration_f",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "reheating_transition_num",
]
SPLINE_CONTINUOUS = [
    "strict_high_age_log",
    "forecast_reheat_after_now_f",
    "forecast_peak_delta_hours_local",
    "daylight_remaining_minutes",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
]
CANDIDATES = [
    "core_recalibration",
    "semantic_linear",
    "semantic_interactions",
    "semantic_spline",
]


def challenger_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def load_ledger() -> pd.DataFrame:
    frame = pd.read_csv(INPUT, low_memory=False)
    frame["target_date"] = frame["target_date"].astype(str)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    frame["label"] = frame["label"].astype(int)
    frame["p_core_hold"] = frame["p_core_no_obs_age"].clip(EPS, 1 - EPS)
    frame["p_market_hold"] = frame["market_mid"].clip(EPS, 1 - EPS)
    frame["base_logit"] = np.log(
        frame["p_core_hold"] / (1.0 - frame["p_core_hold"])
    )
    # Physical interactions are continuous risk summaries, not entry gates.
    daylight_h = frame["daylight_remaining_minutes"].clip(lower=0) / 60.0
    frame["forecast_runway"] = (
        frame["forecast_reheat_after_now_f"].clip(lower=0) * np.sqrt(daylight_h)
    )
    frame["warming_runway"] = (
        frame["temp_trend_1h_f"].clip(lower=0)
        * np.sqrt(daylight_h)
    )
    frame["persistent_warming"] = (
        frame["temp_trend_1h_f"].clip(lower=0)
        + frame["temp_trend_3h_f"].clip(lower=0) / 3.0
        + frame["reheating_transition_num"].fillna(0)
    )
    frame["plateau_evidence"] = (
        frame["strict_high_age_log"]
        + frame["same_running_max_obs_count_log"]
        + (-frame["temp_trend_1h_f"]).clip(lower=0)
    )
    frame["peak_clock_pressure"] = (
        frame["forecast_peak_delta_hours_local"].clip(lower=0)
        * frame["temp_trend_1h_f"].clip(lower=0)
    )
    return frame.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)


def city_day_weights(frame: pd.DataFrame) -> np.ndarray:
    count = frame.groupby(["city", "target_date"])["label"].transform("size")
    return 1.0 / count.clip(lower=1).to_numpy(float)


def numeric_transform(
    train: pd.DataFrame, test: pd.DataFrame, columns: list[str]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    train_arrays: list[np.ndarray] = []
    test_arrays: list[np.ndarray] = []
    names: list[str] = []
    for column in columns:
        train_raw = pd.to_numeric(train[column], errors="coerce")
        test_raw = pd.to_numeric(test[column], errors="coerce")
        median = float(train_raw.median()) if train_raw.notna().any() else 0.0
        a = train_raw.fillna(median).to_numpy(float)
        b = test_raw.fillna(median).to_numpy(float)
        mean, scale = float(a.mean()), float(a.std())
        if scale < 1e-9 or not math.isfinite(scale):
            continue
        train_arrays.append((a - mean) / scale)
        test_arrays.append((b - mean) / scale)
        names.append(column)
        missing = train_raw.isna().to_numpy(float)
        if missing.std() >= 1e-9:
            train_arrays.append((missing - missing.mean()) / missing.std())
            test_arrays.append(
                (test_raw.isna().to_numpy(float) - missing.mean()) / missing.std()
            )
            names.append(f"{column}__missing")
    if not train_arrays:
        return np.empty((len(train), 0)), np.empty((len(test), 0)), []
    return np.column_stack(train_arrays), np.column_stack(test_arrays), names


def design_matrix(
    train: pd.DataFrame, test: pd.DataFrame, candidate: str
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if candidate == "core_recalibration":
        return np.empty((len(train), 0)), np.empty((len(test), 0)), []
    if candidate == "semantic_linear":
        return numeric_transform(train, test, RAW_SEMANTIC)
    if candidate == "semantic_interactions":
        return numeric_transform(
            train,
            test,
            [
                "forecast_runway",
                "warming_runway",
                "persistent_warming",
                "plateau_evidence",
                "peak_clock_pressure",
            ],
        )
    if candidate == "semantic_spline":
        pipe = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                (
                    "spline",
                    SplineTransformer(
                        n_knots=3, degree=2, include_bias=False, extrapolation="linear"
                    ),
                ),
                ("scale", StandardScaler()),
            ]
        )
        x_train = pipe.fit_transform(train[SPLINE_CONTINUOUS])
        x_test = pipe.transform(test[SPLINE_CONTINUOUS])
        binary_train, binary_test, binary_names = numeric_transform(
            train, test, ["reheating_transition_num"]
        )
        if binary_train.shape[1]:
            x_train = np.column_stack([x_train, binary_train])
            x_test = np.column_stack([x_test, binary_test])
        names = [f"spline_{i}" for i in range(x_train.shape[1] - len(binary_names))]
        return x_train, x_test, names + binary_names
    raise ValueError(candidate)


def fit_predict(
    train: pd.DataFrame, test: pd.DataFrame, candidate: str
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train, x_test, names = design_matrix(train, test, candidate)
    # Every challenger gets one calibration intercept; semantic coefficients are
    # regularized while the intercept is weakly regularized.
    x_train = np.column_stack([np.ones(len(train)), x_train])
    x_test = np.column_stack([np.ones(len(test)), x_test])
    y = train["label"].to_numpy(float)
    weights = city_day_weights(train)
    weights /= weights.sum()
    offset = train["base_logit"].to_numpy(float)
    penalty = np.full(x_train.shape[1], L2)
    penalty[0] = 0.01

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + x_train @ beta
        probability = expit(eta)
        loss = float(
            np.sum(weights * (np.logaddexp(0, eta) - y * eta))
            + 0.5 * np.sum(penalty * beta * beta)
        )
        gradient = x_train.T @ (weights * (probability - y)) + penalty * beta
        return loss, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(x_train.shape[1]),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not result.success:
        raise RuntimeError(f"{candidate} fit failed: {result.message}")
    prediction = expit(test["base_logit"].to_numpy(float) + x_test @ result.x)
    return np.clip(prediction, EPS, 1 - EPS), {
        "columns": ["calibration_intercept"] + names,
        "coefficients": result.x.tolist(),
        "coefficient_norm": float(np.linalg.norm(result.x[1:])),
    }


def collapse_city_day(frame: pd.DataFrame, probability: str) -> pd.DataFrame:
    return frame.groupby(["city", "target_date"], as_index=False).agg(
        label=("label", "first"), probability=(probability, "mean")
    )


def metrics(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    day = collapse_city_day(frame, probability)
    y = day["label"].to_numpy(int)
    p = day["probability"].clip(EPS, 1 - EPS).to_numpy(float)
    return {
        "rows": len(frame),
        "city_days": len(day),
        "dates": day["target_date"].nunique(),
        "holds": int(y.sum()),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "mean_probability": float(p.mean()),
        "actual_rate": float(y.mean()),
    }


def daily_delta(frame: pd.DataFrame, candidate: str, baseline: str) -> pd.DataFrame:
    rows = []
    for target_date, group in frame.groupby("target_date", sort=True):
        candidate_day = collapse_city_day(group, candidate)
        baseline_day = collapse_city_day(group, baseline)
        merged = candidate_day.merge(
            baseline_day[["city", "target_date", "probability"]],
            on=["city", "target_date"], suffixes=("_candidate", "_baseline")
        )
        y = merged["label"].to_numpy(float)
        pc = merged["probability_candidate"].clip(EPS, 1 - EPS).to_numpy(float)
        pb = merged["probability_baseline"].clip(EPS, 1 - EPS).to_numpy(float)
        rows.append({
            "target_date": target_date,
            "brier_delta": float(np.mean((y - pc) ** 2 - (y - pb) ** 2)),
            "logloss_delta": float(np.mean(
                -(y * np.log(pc) + (1-y) * np.log(1-pc))
                +(y * np.log(pb) + (1-y) * np.log(1-pb))
            )),
        })
    return pd.DataFrame(rows)


def bootstrap_delta(daily: pd.DataFrame) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_REPS, len(daily)))
    result: dict[str, Any] = {"dates": len(daily)}
    for metric in ["brier_delta", "logloss_delta"]:
        values = daily[metric].to_numpy(float)
        samples = values[indices].mean(axis=1)
        result[metric] = {
            "mean": float(values.mean()),
            "ci95": [float(np.quantile(samples, .025)), float(np.quantile(samples, .975))],
            "win_date_fraction": float((values < 0).mean()),
        }
    return result


def expanding_development(frame: pd.DataFrame, forward_start: str) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    output = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES or target_date >= forward_start:
            continue
        train = frame[frame["target_date"] < target_date]
        test = frame[frame["target_date"] == target_date].copy()
        for candidate in CANDIDATES:
            test[f"p_{candidate}"], _ = fit_predict(train, test, candidate)
        output.append(test)
    return pd.concat(output, ignore_index=True)


def select_candidate(development: pd.DataFrame) -> tuple[str, list[dict[str, Any]]]:
    rows = []
    for candidate in CANDIDATES:
        scored = metrics(development, f"p_{candidate}")
        rows.append({"candidate": candidate, **scored})
    for metric in ["brier", "logloss"]:
        ordered = sorted(rows, key=lambda row: row[metric])
        for rank, row in enumerate(ordered, start=1):
            row[f"{metric}_rank"] = rank
    for row in rows:
        row["mean_rank"] = (row["brier_rank"] + row["logloss_rank"]) / 2
    selected = min(rows, key=lambda row: (row["mean_rank"], row["brier"]))["candidate"]
    return selected, rows


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    frame = load_ledger()
    dates = sorted(frame["target_date"].unique())
    if len(dates) <= WARMUP_DATES + FORWARD_DATES:
        raise RuntimeError("insufficient independent target dates")
    forward_dates = dates[-FORWARD_DATES:]
    forward_start = forward_dates[0]
    development = expanding_development(frame, forward_start)
    selected, selection = select_candidate(development)

    train = frame[frame["target_date"] < forward_start].copy()
    forward = frame[frame["target_date"].isin(forward_dates)].copy()
    forward["p_challenger"], frozen_fit = fit_predict(train, forward, selected)
    comparisons = {}
    for baseline in ["p_core_hold", "p_market_hold"]:
        daily = daily_delta(forward, "p_challenger", baseline)
        comparisons[baseline] = bootstrap_delta(daily)
        daily.assign(baseline=baseline).to_csv(
            OUT_DIR / f"forward_daily_delta_vs_{baseline}.csv", index=False
        )

    vs_core = comparisons["p_core_hold"]
    positive = all(
        vs_core[name]["mean"] < 0 and vs_core[name]["ci95"][1] < 0
        for name in ["brier_delta", "logloss_delta"]
    )
    same_sign = all(
        vs_core[name]["mean"] < 0
        for name in ["brier_delta", "logloss_delta"]
    )
    verdict = "research_positive" if positive else ("inconclusive" if same_sign else "rejected")

    coverage = []
    for column in RAW_SEMANTIC:
        coverage.append({
            "feature": column,
            "coverage": float(pd.to_numeric(frame[column], errors="coerce").notna().mean()),
            "unique_non_null": int(pd.to_numeric(frame[column], errors="coerce").nunique()),
        })
    output_columns = [
        "opportunity_id", "city", "target_date", "decision_snapshot_ts_utc",
        "label", "p_core_hold", "p_market_hold", "p_challenger"
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    development.to_csv(OUT_DIR / "development_oof_predictions.csv", index=False)
    forward[output_columns].to_csv(OUT_DIR / "frozen_forward_predictions.csv", index=False)
    pd.DataFrame(selection).to_csv(OUT_DIR / "candidate_selection.csv", index=False)
    pd.DataFrame(coverage).to_csv(OUT_DIR / "feature_coverage.csv", index=False)

    payload = {
        "schema_version": "current_yes_core_carry_semantic_challenger_v3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": RESEARCH_ID,
        "denominator": {
            "rows": len(frame), "city_days": frame.groupby(["city", "target_date"]).ngroups,
            "dates": len(dates), "date_min": dates[0], "date_max": dates[-1],
            "input": str(INPUT.relative_to(ROOT)), "input_sha256": challenger_file_sha256(INPUT),
        },
        "funnel": {
            "signal": [
                {"stage": "frozen PIT checkpoint parent", "grain": "state", "rows": len(frame)},
                {"stage": "development expanding OOF", "grain": "state", "rows": len(development)},
                {"stage": "frozen forward", "grain": "state", "rows": len(forward)},
            ],
            "evidence": "All parent rows already have PIT model, same-state market, and settlement label; no execution/fill claim is made.",
        },
        "candidate_k": len(CANDIDATES),
        "development_dates": sorted(development["target_date"].unique()),
        "forward_dates": forward_dates,
        "selection": selection,
        "selected_candidate": selected,
        "frozen_fit": frozen_fit,
        "forward_metrics": {
            "challenger": metrics(forward, "p_challenger"),
            "core": metrics(forward, "p_core_hold"),
            "market": metrics(forward, "p_market_hold"),
        },
        "forward_comparisons": comparisons,
        "verdict": verdict,
        "gates": {
            "significance": "PASS" if positive else "FAIL",
            "baseline": "PASS" if comparisons["p_market_hold"]["brier_delta"]["mean"] < 0 and comparisons["p_market_hold"]["logloss_delta"]["mean"] < 0 else "FAIL",
            "forward": "PASS" if same_sign else "FAIL",
            "conclusion": "shadow_candidate" if positive else "inconclusive",
        },
        "limitations": [
            "The fixed ledger ends at 2026-07-08; newer live cases are not appended because the canonical refresh controller currently reports a critical non-zero last exit and they do not yet share this frozen PIT denominator.",
            "Archived source observations are report-time PIT proxies, not first-seen timing; source fields are excluded from challenger candidates.",
            "No fee-adjusted trade replay, sizing, maker-fill, or live action is inferred from probability score alone."
        ],
        "artifacts": {
            "preregistration": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": challenger_file_sha256(PREREG),
            "development_oof_predictions": str((OUT_DIR / "development_oof_predictions.csv").relative_to(ROOT)),
            "frozen_forward_predictions": str((OUT_DIR / "frozen_forward_predictions.csv").relative_to(ROOT)),
        },
    }
    RESULT.write_text(json.dumps(json_ready(payload), indent=2) + "\n", encoding="utf-8")

    def fmt_delta(item: dict[str, Any]) -> str:
        return f"{item['mean']:+.6f}（95% CI [{item['ci95'][0]:+.6f}, {item['ci95'][1]:+.6f}]）"

    lines = [
        "# Core Carry semantic challenger v3",
        "",
        f"**结论：`{verdict}`；未修改 live。** 开发窗选中 `{selected}`。",
        "",
        "## 固定口径",
        "",
        f"- 分母：{len(frame):,} 个 PIT checkpoint、{frame.groupby(['city','target_date']).ngroups} 个 city-day、{len(dates)} 个 target dates（{dates[0]}–{dates[-1]}）。",
        f"- 开发 expanding OOF：{development['target_date'].nunique()} dates；frozen forward：{len(forward_dates)} dates（{forward_dates[0]}–{forward_dates[-1]}）。",
        f"- 候选 K={len(CANDIDATES)}；forward 没参与选型；同 rows 比 frozen core 与 market。",
        "",
        "## 结果",
        "",
        "| 模型 | Forward Brier | Forward logloss |",
        "|---|---:|---:|",
        f"| challenger `{selected}` | {payload['forward_metrics']['challenger']['brier']:.6f} | {payload['forward_metrics']['challenger']['logloss']:.6f} |",
        f"| frozen core | {payload['forward_metrics']['core']['brier']:.6f} | {payload['forward_metrics']['core']['logloss']:.6f} |",
        f"| same-row market | {payload['forward_metrics']['market']['brier']:.6f} | {payload['forward_metrics']['market']['logloss']:.6f} |",
        "",
        f"- Challenger − core Brier：{fmt_delta(vs_core['brier_delta'])}",
        f"- Challenger − core logloss：{fmt_delta(vs_core['logloss_delta'])}",
        f"- Challenger − market Brier：{fmt_delta(comparisons['p_market_hold']['brier_delta'])}",
        f"- Challenger − market logloss：{fmt_delta(comparisons['p_market_hold']['logloss_delta'])}",
        "",
        "## 三道门与动作",
        "",
        f"- significance={payload['gates']['significance']}；baseline={payload['gates']['baseline']}；forward={payload['gates']['forward']}；conclusion={payload['gates']['conclusion']}。",
        "- 本研究只回答概率 challenger；不改 eligibility、fixed 10 sizing、maker policy 或 live 配置。",
        "",
        "## 数据完整性与 8 环",
        "",
        "- 输入是已归档冻结 PIT ledger，未因 challenger 丢行；source first-seen 不可用，因此没有把 source proxy 纳入模型。",
        "- 覆盖：统计推断、概率评估、同分母 market baseline、frozen forward。未覆盖：真实 fill 微结构、容量、组合资金曲线与新 live 日期 canonical 合并。",
        "- 当前 production manifest 的 canonical refresh LaunchAgent 有 critical non-zero last exit，所以没有把 7/08 后 live case 拼入旧分母，也没有做任何生产动作。",
        "",
        "## 复现",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --semantic-challenger",
        "```",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_ready({
        "selected": selected, "verdict": verdict,
        "forward_metrics": payload["forward_metrics"],
        "vs_core": vs_core, "vs_market": comparisons["p_market_hold"],
        "report": str(REPORT.relative_to(ROOT)),
    }), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
