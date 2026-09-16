#!/usr/bin/env python3
"""Test forecast innovation as an incremental current-YES carry risk feature.

The frozen core hold probability remains the offset.  The candidate adds only
PIT observation-minus-assigned-model temperature innovation to the existing
native-lattice baseline.  Expanding OOF and execution replay use prior target
dates only and preserve the original five-share direct-ask denominator.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_overshoot_missing_mechanisms_v2 as v2,
)


RESEARCH_ID = "current_yes_carry_forecast_innovation_v1"
INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_missing_mechanisms_v2/feature_ledger.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-current-yes-carry-forecast-innovation-v1.md"
)
MIN_TRAIN_DATES = 8
BOOTSTRAP_REPS = 5_000
SEED = 20260728
EPS = 1e-6

BASELINE_FEATURES = list(v2.NATIVE_LATTICE)
INNOVATION_FEATURES = [
    "forecast_innovation_basis_f",
    "forecast_innovation_abs_f",
    "innovation_x_remaining_peak",
    "innovation_per_exit_tick",
]
MODEL_SPECS = {
    "core": [],
    "lattice": BASELINE_FEATURES,
    "lattice_plus_innovation": BASELINE_FEATURES + INNOVATION_FEATURES,
}
PRIMARY = "lattice_plus_innovation"
BASELINE = "lattice"


def interpolate_model_temperature_f(
    row: pd.Series,
    cache: dict[str, Any],
) -> float:
    model = str(row.get("forecast_assigned_model") or "").lower()
    if model not in {"gfs", "ecmwf"}:
        return math.nan
    points = v2.curve_points(
        row.get(f"{model}_forecast_cache_path"),
        str(row["target_date"]),
        cache,
    )
    converted: list[tuple[float, float]] = []
    for hour, value, unit in points:
        value_f = value if "f" in str(unit).lower() else value * 9.0 / 5.0 + 32.0
        converted.append((hour, value_f))
    converted.sort()
    target = float(row["decision_hour_local"])
    for hour, value in converted:
        if abs(hour - target) < 1e-9:
            return value
    for (left_hour, left), (right_hour, right) in zip(
        converted, converted[1:]
    ):
        if left_hour < target < right_hour:
            weight = (target - left_hour) / (right_hour - left_hour)
            return left + weight * (right - left)
    return math.nan


def prepare() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_csv(INPUT)
    frame["decision_snapshot_dt"] = pd.to_datetime(
        frame["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    cache: dict[str, Any] = {}
    frame["assigned_model_temp_at_decision_f"] = [
        interpolate_model_temperature_f(row, cache)
        for _, row in frame.iterrows()
    ]
    frame["forecast_innovation_raw_f"] = (
        frame["source_latest_temp_f"]
        - frame["assigned_model_temp_at_decision_f"]
    )
    frame["forecast_innovation_basis_f"] = (
        frame["source_latest_temp_f"]
        - frame["source_to_settlement_basis_mean_pit_f"].fillna(0.0)
        - frame["assigned_model_temp_at_decision_f"]
    )
    frame["forecast_innovation_abs_f"] = frame[
        "forecast_innovation_basis_f"
    ].abs()
    frame["innovation_x_remaining_peak"] = (
        frame["forecast_innovation_basis_f"]
        * frame["forecast_peak_delta_hours_local"].clip(lower=0)
    )
    frame["innovation_per_exit_tick"] = (
        frame["forecast_innovation_basis_f"]
        / frame["exit_ticks_required"].clip(lower=0.25)
    )
    frame["p_over_core"] = frame["p_over_core"].clip(EPS, 1 - EPS)
    frame["base_offset_logit"] = np.log(
        frame["p_over_core"] / (1.0 - frame["p_over_core"])
    )
    frame = frame.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)
    lineage = {
        "input_rows": len(frame),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cache_files": len(cache),
        "model_temp_covered": int(
            frame["assigned_model_temp_at_decision_f"].notna().sum()
        ),
        "raw_innovation_covered": int(
            frame["forecast_innovation_raw_f"].notna().sum()
        ),
        "basis_innovation_covered": int(
            frame["forecast_innovation_basis_f"].notna().sum()
        ),
        "basis_prior_covered": int(
            frame["source_to_settlement_basis_mean_pit_f"].notna().sum()
        ),
        "source_first_seen_limitation": (
            "historical source report is latest report_ts<=decision archive PIT proxy; "
            "first-seen unavailable"
        ),
    }
    return frame, lineage


def expanding_oof(frame: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    rows: list[pd.DataFrame] = []
    keep = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_snapshot_dt",
        "current_bracket",
        "overshoot",
        "label",
        "p_over_core",
        "five_share_executable",
        "five_share_cost_per_share",
        "forecast_innovation_basis_f",
        "assigned_model_temp_at_decision_f",
        "source_latest_temp_f",
    ]
    for index, target_date in enumerate(dates):
        train_dates = dates[:index]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = frame[frame["target_date"].isin(train_dates)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        if train["overshoot"].nunique() < 2 or test.empty:
            continue
        result = test[keep].copy()
        result["train_dates"] = len(train_dates)
        result["train_through_date"] = max(train_dates)
        for name, features in MODEL_SPECS.items():
            if not features:
                result[f"p_over_{name}"] = test["p_over_core"].to_numpy(float)
            else:
                probability, _fit = v2.fit_offset_residual(
                    train, test, features
                )
                result[f"p_over_{name}"] = probability
        rows.append(result)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def city_day_scores(frame: pd.DataFrame, probability_column: str) -> pd.DataFrame:
    grouped = (
        frame.groupby(["city", "target_date"], as_index=False)
        .agg(
            overshoot=("overshoot", "first"),
            probability=(probability_column, "mean"),
        )
    )
    probability = grouped["probability"].clip(EPS, 1 - EPS)
    grouped["brier"] = (probability - grouped["overshoot"]) ** 2
    grouped["logloss"] = -(
        grouped["overshoot"] * np.log(probability)
        + (1 - grouped["overshoot"]) * np.log(1 - probability)
    )
    return grouped


def date_mean_ci(frame: pd.DataFrame, column: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[column].mean().to_numpy(float)
    if not len(daily):
        return math.nan, math.nan
    rng = np.random.default_rng(SEED)
    draws = rng.choice(
        daily, size=(BOOTSTRAP_REPS, len(daily)), replace=True
    ).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def scorecard(oof: pd.DataFrame) -> pd.DataFrame:
    baseline = city_day_scores(oof, f"p_over_{BASELINE}").rename(
        columns={"brier": "base_brier", "logloss": "base_logloss"}
    )
    records: list[dict[str, Any]] = []
    for name in MODEL_SPECS:
        scores = city_day_scores(oof, f"p_over_{name}").merge(
            baseline[
                ["city", "target_date", "base_brier", "base_logloss"]
            ],
            on=["city", "target_date"],
            validate="one_to_one",
        )
        scores["brier_delta"] = scores["brier"] - scores["base_brier"]
        scores["logloss_delta"] = (
            scores["logloss"] - scores["base_logloss"]
        )
        brier_low, brier_high = date_mean_ci(scores, "brier_delta")
        log_low, log_high = date_mean_ci(scores, "logloss_delta")
        records.append(
            {
                "model": name,
                "rows": len(oof),
                "city_days": len(scores),
                "dates": int(scores["target_date"].nunique()),
                "brier": float(scores["brier"].mean()),
                "logloss": float(scores["logloss"].mean()),
                "brier_delta_vs_lattice": float(scores["brier_delta"].mean()),
                "brier_delta_ci_low": brier_low,
                "brier_delta_ci_high": brier_high,
                "logloss_delta_vs_lattice": float(
                    scores["logloss_delta"].mean()
                ),
                "logloss_delta_ci_low": log_low,
                "logloss_delta_ci_high": log_high,
            }
        )
    return pd.DataFrame(records)


def select_first_positive(
    oof: pd.DataFrame, model: str
) -> pd.DataFrame:
    rows = oof[oof["five_share_executable"].fillna(False).astype(bool)].copy()
    rows["p_hold"] = 1.0 - rows[f"p_over_{model}"]
    rows["edge"] = rows["p_hold"] - rows["five_share_cost_per_share"]
    rows = rows[rows["edge"].gt(0)].copy()
    rows = (
        rows.sort_values(["city", "target_date", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )
    rows["model"] = model
    rows["shares"] = 5.0
    rows["capital"] = 5.0 * rows["five_share_cost_per_share"]
    rows["payout"] = 5.0 * (1.0 - rows["overshoot"])
    rows["pnl"] = rows["payout"] - rows["capital"]
    return rows


def roi_ci(trades: pd.DataFrame) -> tuple[float, float]:
    if trades.empty:
        return math.nan, math.nan
    daily = trades.groupby("target_date", as_index=False).agg(
        pnl=("pnl", "sum"), capital=("capital", "sum")
    )
    rng = np.random.default_rng(SEED)
    index = rng.integers(
        0, len(daily), size=(BOOTSTRAP_REPS, len(daily))
    )
    pnl = daily["pnl"].to_numpy()[index].sum(axis=1)
    capital = daily["capital"].to_numpy()[index].sum(axis=1)
    roi = np.divide(
        pnl,
        capital,
        out=np.full_like(pnl, np.nan, dtype=float),
        where=capital > 0,
    )
    return float(np.quantile(roi, 0.025)), float(np.quantile(roi, 0.975))


def trade_scorecard(oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades: list[pd.DataFrame] = []
    records: list[dict[str, Any]] = []
    for model in MODEL_SPECS:
        selected = select_first_positive(oof, model)
        trades.append(selected)
        low, high = roi_ci(selected)
        capital = float(selected["capital"].sum()) if len(selected) else 0.0
        records.append(
            {
                "model": model,
                "opportunity_city_days": oof.groupby(
                    ["city", "target_date"]
                ).ngroups,
                "trades": len(selected),
                "dates": int(selected["target_date"].nunique())
                if len(selected)
                else 0,
                "wins": int((1 - selected["overshoot"]).sum())
                if len(selected)
                else 0,
                "capital": capital,
                "pnl": float(selected["pnl"].sum()) if len(selected) else 0.0,
                "roi": float(selected["pnl"].sum() / capital)
                if capital
                else math.nan,
                "roi_ci_low": low,
                "roi_ci_high": high,
            }
        )
    return pd.concat(trades, ignore_index=True), pd.DataFrame(records)


def paired_trade_delta(trades: pd.DataFrame) -> dict[str, Any]:
    daily = (
        trades[trades["model"].isin([PRIMARY, BASELINE])]
        .groupby(["target_date", "model"], as_index=False)
        .agg(pnl=("pnl", "sum"))
        .pivot(index="target_date", columns="model", values="pnl")
        .fillna(0.0)
    )
    daily["delta"] = daily[PRIMARY] - daily[BASELINE]
    low, high = date_mean_ci(daily.reset_index(), "delta")
    return {
        "dates": len(daily),
        "candidate_pnl": float(daily[PRIMARY].sum()),
        "baseline_pnl": float(daily[BASELINE].sum()),
        "pnl_delta": float(daily["delta"].sum()),
        "date_mean_pnl_delta": float(daily["delta"].mean()),
        "date_mean_delta_ci_low": low,
        "date_mean_delta_ci_high": high,
    }


def markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.to_dict("records"):
        cells = []
        for column in columns:
            value = row[column]
            if isinstance(value, (float, np.floating)):
                cells.append("NA" if not math.isfinite(value) else f"{value:.5f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(
    lineage: dict[str, Any],
    scores: pd.DataFrame,
    trades: pd.DataFrame,
    paired: dict[str, Any],
) -> None:
    primary = scores[scores["model"].eq(PRIMARY)].iloc[0]
    primary_trade = trades[trades["model"].eq(PRIMARY)].iloc[0]
    probability_pass = (
        primary["brier_delta_ci_high"] < 0
        and primary["logloss_delta_ci_high"] < 0
    )
    absolute_execution_pass = primary_trade["roi_ci_low"] > 0
    paired_baseline_pass = paired["date_mean_delta_ci_low"] > 0
    lines = [
        "# Current-YES carry forecast innovation v1",
        "",
        "## 结论与动作",
        "",
        "把 innovation 放进当前最接近可交易的 `current_yes_core_carry` 概率头，"
        "检验它相对同分母 native-lattice baseline 的 overshoot risk 增量。"
        "本轮是 research replay，不改 live。",
        "",
        "## Feature contract",
        "",
        "`innovation = latest source temp - expanding source/settlement basis - "
        "assigned-model temperature at the true decision local minute`。",
        "模型当前温度由当时缓存的 GFS/ECMWF hourly curve 线性插值；assigned model "
        "继续使用固定 CITY_MODEL。β 由 expanding OOF residual 学习。",
        "",
        "## Coverage",
        "",
        "```json",
        json.dumps(lineage, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Probability",
        "",
        markdown_table(scores),
        "",
        "## Five-share first-positive-EV replay",
        "",
        markdown_table(trades),
        "",
        "## Candidate vs lattice paired daily PnL",
        "",
        "```json",
        json.dumps(paired, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Three gates",
        "",
        f"- absolute fee-adjusted execution："
        f"{'PASS' if absolute_execution_pass else 'FAIL'}",
        f"- innovation probability/significance："
        f"{'PASS' if probability_pass else 'FAIL'}",
        f"- paired baseline delta："
        f"{'PASS' if paired_baseline_pass else 'FAIL'}",
        "- fresh frozen forward：NA",
        "",
        "```text",
        f"status={'shadow_candidate' if probability_pass and execution_pass else 'inconclusive'}",
        "live_action=none",
        "```",
        "",
        "历史 source event 只有 `report_ts<=decision` 的 archive PIT proxy，缺 first-seen；"
        "即使两项历史门通过，也只能先进入 zero-notional forward。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    frame, lineage = prepare()
    oof = expanding_oof(frame)
    if oof.empty:
        raise RuntimeError("no expanding OOF rows")
    scores = scorecard(oof)
    trade_rows, trade_summary = trade_scorecard(oof)
    paired = paired_trade_delta(trade_rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "feature_ledger.csv", index=False)
    oof.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    scores.to_csv(OUT_DIR / "probability_scorecard.csv", index=False)
    trade_rows.to_csv(OUT_DIR / "selected_trades.csv", index=False)
    trade_summary.to_csv(OUT_DIR / "trade_scorecard.csv", index=False)
    payload = {
        "lineage": lineage,
        "scores": scores.to_dict("records"),
        "trades": trade_summary.to_dict("records"),
        "paired_trade_delta": paired,
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(lineage, scores, trade_summary, paired)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
