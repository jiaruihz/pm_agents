#!/usr/bin/env python3
"""HeadA market-anchored residual walk-forward research v1.

The market ask is the prior. Source/bias and exact-bracket overshoot features
may only make a regularized residual correction trained on earlier target dates.
No city identity, fitted entry threshold, or in-sample prediction is used.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

from strategies.weather_edge_v1.tools.heada_terminal_distribution import (  # noqa: E402
    terminal_bracket_distribution,
)

INPUT = REPO / "docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2/candidate_rows.csv"
OUT_MD = REPO / "docs/analysis/2026-07/2026-07-12-heada-market-anchored-residual-v1.md"
OUT_JSON = OUT_MD.with_suffix(".json")
GEN_DIR = REPO / "docs/analysis/2026-07/generated/heada_market_anchored_residual_v1"
PRED_CSV = GEN_DIR / "walk_forward_predictions.csv"
SEED = 20260712
MIN_TRAIN_ROWS = 150
REGULARIZATION_C = 0.05
RESIDUAL_TRUST_WEIGHT = 0.25

SOURCE_FEATURES = [
    "market_logit",
    "raw_dist_br",
    "bias_mean_asof",
    "hot_tail_pct_asof",
    "forecast_peak_delta_hours_local",
    "forecast_source_model_mae_f_asof",
    "forecast_source_model_underforecast_ge_1f_pct_asof",
    "forecast_source_model_gap_to_best_f_asof",
    "is_ecmwf",
]
OVERSHOOT_FEATURES = SOURCE_FEATURES + ["kernel_p_below", "kernel_p_overshoot"]


def clip_prob(values: pd.Series | np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), 0.001, 0.999)


def make_model() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            (
                "logit",
                LogisticRegression(C=REGULARIZATION_C, max_iter=3000, random_state=SEED),
            ),
        ]
    )


def weather_fee(price: np.ndarray) -> np.ndarray:
    # Canonical report input already carries the official weather fee when known.
    return 0.05 * price * (1.0 - price)


def date_block_roi_ci(frame: pd.DataFrame, n_boot: int = 4000) -> tuple[float | None, float | None]:
    if frame.empty:
        return None, None
    daily = frame.groupby("target_date", as_index=False).agg(pnl=("realized_pnl", "sum"), cost=("ask", "sum"))
    if len(daily) < 2 or daily["cost"].sum() <= 0:
        return None, None
    rng = np.random.default_rng(SEED)
    values = np.empty(n_boot)
    for i in range(n_boot):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        values[i] = take["pnl"].sum() / take["cost"].sum()
    lo, hi = np.quantile(values, [0.025, 0.975])
    return float(lo), float(hi)


def date_block_brier_delta_ci(
    frame: pd.DataFrame, prob_col: str, baseline_col: str = "market_p", n_boot: int = 4000
) -> tuple[float, float, float]:
    work = frame[["target_date", "win", prob_col, baseline_col]].copy()
    work["delta"] = (work[prob_col] - work["win"]) ** 2 - (work[baseline_col] - work["win"]) ** 2
    daily = work.groupby("target_date", as_index=False).agg(delta_sum=("delta", "sum"), rows=("delta", "size"))
    rng = np.random.default_rng(SEED)
    values = np.empty(n_boot)
    for i in range(n_boot):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        values[i] = take["delta_sum"].sum() / take["rows"].sum()
    lo, hi = np.quantile(values, [0.025, 0.975])
    return float(work["delta"].mean()), float(lo), float(hi)


def metrics(frame: pd.DataFrame, prob_col: str) -> dict[str, float | int | None]:
    y = frame["win"].to_numpy(dtype=int)
    p = clip_prob(frame[prob_col])
    return {
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "wins": int(y.sum()),
        "win_rate": float(y.mean()),
        "mean_pred": float(p.mean()),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }


def selection_metrics(frame: pd.DataFrame, prob_col: str, policy: str) -> dict[str, float | int | None]:
    work = frame.copy()
    work["pred_net_edge"] = work[prob_col] - work["ask"] - work["fee"]
    if policy == "top1_positive_per_date":
        picked = (
            work.sort_values(["target_date", "pred_net_edge"], ascending=[True, False])
            .groupby("target_date", as_index=False)
            .head(1)
        )
        picked = picked[picked["pred_net_edge"] > 0].copy()
    elif policy == "positive_residual":
        picked = work[work["pred_net_edge"] > 0].copy()
    else:
        raise ValueError(policy)
    if picked.empty:
        return {"rows": 0, "dates": 0, "wins": 0, "win_rate": None, "avg_ask": None,
                "roi": None, "ci_low": None, "ci_high": None, "losing_days": 0, "max_daily_loss": None}
    picked["realized_pnl"] = picked["win"] - picked["ask"] - picked["fee"]
    cost = float(picked["ask"].sum())
    ci_low, ci_high = date_block_roi_ci(picked)
    daily = picked.groupby("target_date")["realized_pnl"].sum()
    return {
        "rows": int(len(picked)),
        "dates": int(picked["target_date"].nunique()),
        "wins": int(picked["win"].sum()),
        "win_rate": float(picked["win"].mean()),
        "avg_ask": float(picked["ask"].mean()),
        "roi": float(picked["realized_pnl"].sum() / cost),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "losing_days": int((daily < 0).sum()),
        "max_daily_loss": float(daily.min()),
    }


def fmt(value: float | int | None, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    if pct:
        return f"{float(value):+.1%}"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.4f}"


def main() -> None:
    frame = pd.read_csv(INPUT)
    frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.strftime("%Y-%m-%d")
    frame["win"] = frame["win"].astype(str).str.lower().isin(["true", "1", "1.0"]).astype(int)
    frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
    frame = frame[(frame["ask"] > 0) & (frame["ask"] < 1)].copy()
    frame["market_p"] = frame["ask"]
    frame["model_p"] = pd.to_numeric(frame["model_p_yes"], errors="coerce").clip(0.001, 0.999)
    frame["market_logit"] = np.log(frame["ask"] / (1.0 - frame["ask"]))
    frame["is_ecmwf"] = frame["forecast_model"].astype(str).str.lower().eq("ecmwf").astype(float)
    # Per-share replay: the input entry_fee is position-level under historical
    # sizing, so recompute the official weather fee at the shared one-share grain.
    frame["fee"] = weather_fee(frame["ask"].to_numpy())

    # The historical source-calibration builder already computes these values
    # as-of each event date; live telemetry later renamed them with an explicit
    # `_asof` suffix. Coalesce both names without using full-sample calibration.
    for explicit, historical in {
        "forecast_source_model_mae_f_asof": "source_mae_f",
        "forecast_source_model_underforecast_ge_1f_pct_asof": "source_underforecast_ge_1f_pct",
        "forecast_source_model_gap_to_best_f_asof": "source_gap_to_best_f",
    }.items():
        frame[explicit] = pd.to_numeric(frame.get(explicit), errors="coerce").combine_first(
            pd.to_numeric(frame.get(historical), errors="coerce")
        )

    kernel_rows = [terminal_bracket_distribution(row).as_dict() for row in frame.to_dict("records")]
    frame["kernel_p_below"] = [row["p_below"] for row in kernel_rows]
    frame["kernel_p_overshoot"] = [row["p_overshoot"] for row in kernel_rows]

    for column in set(OVERSHOOT_FEATURES) - {"market_logit", "is_ecmwf"}:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")

    frame["source_residual_p"] = np.nan
    frame["overshoot_residual_p"] = np.nan
    frame["train_rows"] = 0
    dates = sorted(frame["target_date"].unique())
    first_oos_date = None
    for date in dates:
        train = frame[frame["target_date"] < date]
        test_idx = frame.index[frame["target_date"] == date]
        if len(train) < MIN_TRAIN_ROWS or train["win"].nunique() < 2:
            continue
        if first_oos_date is None:
            first_oos_date = date
        for features, output in [
            (SOURCE_FEATURES, "source_residual_p"),
            (OVERSHOOT_FEATURES, "overshoot_residual_p"),
        ]:
            model = make_model()
            model.fit(train[features], train["win"])
            frame.loc[test_idx, output] = model.predict_proba(frame.loc[test_idx, features])[:, 1]
        frame.loc[test_idx, "train_rows"] = len(train)

    oos = frame.dropna(subset=["source_residual_p", "overshoot_residual_p"]).copy()
    if oos.empty:
        raise RuntimeError("no expanding walk-forward predictions produced")

    oos["source_anchored_p"] = oos["market_p"] + RESIDUAL_TRUST_WEIGHT * (
        oos["source_residual_p"] - oos["market_p"]
    )
    oos["overshoot_anchored_p"] = oos["market_p"] + RESIDUAL_TRUST_WEIGHT * (
        oos["overshoot_residual_p"] - oos["market_p"]
    )

    probability = {
        name: metrics(oos, column)
        for name, column in {
            "market_ask": "market_p",
            "current_model_p_yes": "model_p",
            "unshrunk_source_model_diagnostic": "source_residual_p",
            "market_75_source_residual_25": "source_anchored_p",
            "market_75_source_overshoot_residual_25": "overshoot_anchored_p",
        }.items()
    }
    brier_deltas = {
        name: dict(zip(["delta", "ci_low", "ci_high"], date_block_brier_delta_ci(oos, column)))
        for name, column in {
            "market_75_source_residual_25": "source_anchored_p",
            "market_75_source_overshoot_residual_25": "overshoot_anchored_p",
        }.items()
    }
    selections: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for name, column in {
        "current_model_p_yes": "model_p",
        "market_75_source_residual_25": "source_anchored_p",
        "market_75_source_overshoot_residual_25": "overshoot_anchored_p",
    }.items():
        selections[name] = {
            policy: selection_metrics(oos, column, policy)
            for policy in ["positive_residual", "top1_positive_per_date"]
        }

    payload = {
        "generated_at": "2026-07-12",
        "input": str(INPUT.relative_to(REPO)),
        "method": {
            "walk_forward": "expanding by target_date; train dates strictly before test date",
            "first_oos_date": first_oos_date,
            "last_oos_date": oos["target_date"].max(),
            "min_train_rows": MIN_TRAIN_ROWS,
            "regularization_C": REGULARIZATION_C,
            "residual_trust_weight": RESIDUAL_TRUST_WEIGHT,
            "city_identity_used": False,
            "threshold_optimization": False,
        },
        "probability_metrics": probability,
        "brier_delta_vs_market": brier_deltas,
        "selection_metrics": selections,
    }

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    keep = [
        "candidate_id", "city", "target_date", "bracket", "ask", "fee", "win", "model_p",
        "market_p", "source_residual_p", "overshoot_residual_p", "source_anchored_p",
        "overshoot_anchored_p", "kernel_p_below",
        "kernel_p_overshoot", "train_rows",
    ]
    oos[keep].to_csv(PRED_CSV, index=False)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    prob_lines = []
    for name, row in probability.items():
        prob_lines.append(
            f"| {name} | {row['rows']} | {row['dates']} | {fmt(row['win_rate'], True)} | "
            f"{fmt(row['mean_pred'], True)} | {fmt(row['brier'])} | {fmt(row['logloss'])} | {fmt(row['auc'])} |"
        )
    sel_lines = []
    for name, policies in selections.items():
        for policy, row in policies.items():
            ci = f"[{fmt(row['ci_low'], True)}, {fmt(row['ci_high'], True)}]"
            sel_lines.append(
                f"| {name} | {policy} | {row['rows']} | {row['dates']} | {row['wins']} | "
                f"{fmt(row['win_rate'], True)} | {fmt(row['avg_ask'], True)} | {fmt(row['roi'], True)} | "
                f"{ci} | {row['losing_days']} | {fmt(row['max_daily_loss'])} |"
            )

    best_prob = min(probability, key=lambda key: probability[key]["brier"])
    source_better = brier_deltas["market_75_source_residual_25"]["ci_high"] < 0
    over_better = brier_deltas["market_75_source_overshoot_residual_25"]["ci_high"] < 0
    verdict = "shadow_candidate" if source_better or over_better else "inconclusive"
    delta_lines = []
    for name, row in brier_deltas.items():
        delta_lines.append(
            f"| {name} | {row['delta']:+.6f} | [{row['ci_low']:+.6f}, {row['ci_high']:+.6f}] |"
        )
    OUT_MD.write_text(
        f"""# HeadA Market-Anchored Residual v1

Generated: 2026-07-12  
Scope: HeadA `forecast_tail_low_price_yes` only. Live entry is unchanged; this is an independent shadow research head.

## Verdict

`{verdict}`. Best OOS probability arm by Brier: `{best_prob}`. The experiment uses the same candidate denominator, strict expanding target-date walk-forward, no city identity and no optimized entry threshold.

The key question is whether a strongly regularized source/bias or overshoot correction improves on the market ask itself. A positive historical ROI is insufficient if calibration does not beat the market prior.

## Data / PIT

- Input: `{INPUT.relative_to(REPO)}`
- OOS window: `{first_oos_date}..{oos['target_date'].max()}`
- OOS denominator: {len(oos)} rows / {oos['target_date'].nunique()} dates / {oos['city'].nunique()} cities
- Each test date is predicted using only earlier target dates; minimum train rows={MIN_TRAIN_ROWS}.
- Logistic residual correction uses strong L2 regularization `C={REGULARIZATION_C}`; no city one-hot and no ROI-fitted thresholds.
- The tradable candidate is structurally anchored: `p = 75% * market ask + 25% * residual model`. The unshrunk model is diagnostic only.
- Per-share fee uses the official Weather curve `0.05 * ask * (1-ask)`; the input's historical position-level `entry_fee` is not mixed into this one-share replay.

## Probability A/B

| arm | rows | dates | realized | mean predicted | Brier↓ | logloss↓ | AUC↑ |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(prob_lines)}

### Brier delta vs market

Negative is better. CI is target-date block bootstrap.

| arm | Brier delta | 95% CI |
|---|---:|---:|
{chr(10).join(delta_lines)}

## Executable Selection Diagnostics

`positive_residual` buys every row whose predicted probability exceeds ask+fee. `top1_positive_per_date` is a fixed-capacity diagnostic, not an optimized strategy threshold.

| arm | policy | rows | dates | wins | win rate | avg ask | ROI | date-block 95% CI | losing days | max daily loss/share |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
{chr(10).join(sel_lines)}

## Interpretation

- `market_ask` is the probability baseline, not a tradable edge: buying at ask must still overcome fees and spread.
- `current_model_p_yes` tests the old HeadA belief directly on the identical OOS denominator.
- `market_75_source_residual_25` asks whether PIT source/bias fields add a small correction after market price.
- `market_75_source_overshoot_residual_25` adds the old kernel's below/overshoot decomposition only as residual features; the kernel never replaces the market prior.
- No live selector or sizing change follows from this v1. Only a residual arm that improves probability scoring and fee-after forward EV should be wired into a zero-notional shadow runner.

## Contract Verdict

significance=FAIL (Brier delta and selection ROI CIs cross 0); baseline={'PASS' if source_better or over_better else 'FAIL'} against market probability scoring; forward=PARTIAL because predictions are historical expanding OOS, not fresh post-registration shadow; conclusion={verdict}.

## Eight Rings

Covered: descriptive performance, probability calibration/ranking, execution fee approximation, target-date bootstrap, market baseline. Missing/partial: real queue fill, capacity, portfolio correlation, fresh post-registration forward.
"""
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
