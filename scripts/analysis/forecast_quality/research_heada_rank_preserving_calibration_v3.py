#!/usr/bin/env python3
"""HeadA rank-preserving probability calibration and near-tie study v3.

The old model-market disagreement remains the selection rank. This study asks
two narrower questions on the neutral candidate universe:

1. Can that score be mapped monotonically to an honest hit probability?
2. Do PIT source/overshoot features add winner ordering inside near-tied scores?
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_heada_neutral_universe_residual_v2 as v2  # noqa: E402

OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-13-heada-rank-preserving-calibration-v3.md"
OUT_JSON = OUT_MD.with_suffix(".json")
GEN_DIR = ROOT / "docs/analysis/2026-07/generated/heada_rank_preserving_calibration_v3"
PRED_CSV = GEN_DIR / "walk_forward_predictions.csv"
SELECTION_CSV = GEN_DIR / "selection_summary.csv"
PAIR_CSV = GEN_DIR / "near_tie_pair_summary.csv"

SEED = 20260713
N_BOOT = 5000
MIN_TRAIN_ROWS = 700
CAPACITIES = (1, 3, 5)
TIE_BANDS = (0.01, 0.02, 0.05)
PRIMARY_TIE_BAND = 0.02

SOURCE_INCREMENT_FEATURES = [
    "raw_dist_br",
    "bias_mean_asof",
    "bias_p90_asof",
    "hot_tail_pct_asof",
    "forecast_peak_delta_hours_local",
    "source_mae_asof",
    "source_underforecast_pct_asof",
    "source_gap_to_best_asof",
    "is_ecmwf",
    "is_open_upper",
    "is_open_lower",
]
OVERSHOOT_INCREMENT_FEATURES = SOURCE_INCREMENT_FEATURES + [
    "kernel_p_below",
    "kernel_p_overshoot",
]


def safe_logit(values: pd.Series | np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 0.001, 0.999)
    return np.log(p / (1.0 - p))


def monotone_platt(train_score: pd.Series, train_y: pd.Series, test_score: pd.Series) -> tuple[np.ndarray, float]:
    """Fit one-dimensional Platt scaling; reject an order-reversing slope."""
    model = LogisticRegression(C=1_000_000.0, max_iter=3000, random_state=SEED)
    model.fit(train_score.to_numpy(float).reshape(-1, 1), train_y.to_numpy(int))
    slope = float(model.coef_[0, 0])
    if slope <= 0:
        return np.full(len(test_score), float(train_y.mean())), slope
    return model.predict_proba(test_score.to_numpy(float).reshape(-1, 1))[:, 1], slope


def walk_forward(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["old_score"] = out["model_p"] - out["ask"] - out["fee"]
    for column in ["calibrated_p", "source_aug_p", "overshoot_aug_p", "source_increment", "overshoot_increment"]:
        out[column] = np.nan
    out["calibration_slope"] = np.nan
    out["train_rows_v3"] = 0

    for date in sorted(out["target_date"].unique()):
        train = out[out["target_date"] < date]
        test_idx = out.index[out["target_date"] == date]
        if len(train) < MIN_TRAIN_ROWS or train["win"].nunique() < 2:
            continue

        calibrated, slope = monotone_platt(train["old_score"], train["win"], out.loc[test_idx, "old_score"])
        out.loc[test_idx, "calibrated_p"] = calibrated
        out.loc[test_idx, "calibration_slope"] = slope

        for features, p_column, increment_column in [
            (SOURCE_INCREMENT_FEATURES, "source_aug_p", "source_increment"),
            (OVERSHOOT_INCREMENT_FEATURES, "overshoot_aug_p", "overshoot_increment"),
        ]:
            usable = [feature for feature in features if train[feature].notna().any()]
            model = v2.make_model()
            model.fit(train[["old_score", *usable]], train["win"])
            predicted = model.predict_proba(out.loc[test_idx, ["old_score", *usable]])[:, 1]
            out.loc[test_idx, p_column] = predicted
            out.loc[test_idx, increment_column] = safe_logit(predicted) - safe_logit(calibrated)
        out.loc[test_idx, "train_rows_v3"] = len(train)

    return out.dropna(subset=["calibrated_p", "source_increment", "overshoot_increment"]).copy()


def probability_metrics(frame: pd.DataFrame, column: str) -> dict[str, float | int | None]:
    y = frame["win"].to_numpy(int)
    p = np.clip(frame[column].to_numpy(float), 0.001, 0.999)
    return {
        "rows": len(frame),
        "dates": frame["target_date"].nunique(),
        "wins": int(y.sum()),
        "realized": float(y.mean()),
        "mean_pred": float(p.mean()),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }


def metric_delta_ci(frame: pd.DataFrame, column: str, baseline: str, metric: str) -> tuple[float, float, float]:
    work = frame[["target_date", "win", column, baseline]].copy()
    if metric == "brier":
        work["delta"] = (work[column] - work["win"]) ** 2 - (work[baseline] - work["win"]) ** 2
    elif metric == "logloss":
        y = work["win"].to_numpy(float)
        p1 = np.clip(work[column].to_numpy(float), 0.001, 0.999)
        p0 = np.clip(work[baseline].to_numpy(float), 0.001, 0.999)
        work["delta"] = -(y * np.log(p1) + (1 - y) * np.log(1 - p1)) + (y * np.log(p0) + (1 - y) * np.log(1 - p0))
    else:
        raise ValueError(metric)
    daily = work.groupby("target_date").agg(delta=("delta", "sum"), rows=("delta", "size"))
    rng = np.random.default_rng(SEED)
    samples = []
    for _ in range(N_BOOT):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        samples.append(take["delta"].sum() / take["rows"].sum())
    lo, hi = np.quantile(samples, [0.025, 0.975])
    return float(work["delta"].mean()), float(lo), float(hi)


def select_old(frame: pd.DataFrame, capacity: int) -> pd.DataFrame:
    work = frame.sort_values(["target_date", "old_score"], ascending=[True, False])
    work = work.drop_duplicates(["target_date", "city"], keep="first")
    return work.groupby("target_date", group_keys=False).head(capacity).copy()


def select_tiebreak(frame: pd.DataFrame, capacity: int, increment: str, band: float) -> pd.DataFrame:
    work = frame.copy()
    work["score_band"] = np.floor(work["old_score"] / band).astype(int)
    work = work.sort_values(
        ["target_date", "score_band", increment, "old_score"],
        ascending=[True, False, False, False],
    )
    work = work.drop_duplicates(["target_date", "city"], keep="first")
    return work.groupby("target_date", group_keys=False).head(capacity).copy()


def summarize_selection(selected: pd.DataFrame) -> dict[str, float | int | None]:
    if selected.empty:
        return {"rows": 0, "dates": 0}
    selected = selected.copy()
    selected["pnl_share"] = selected["win"] - selected["ask"] - selected["fee"]
    selected["cost_5"] = 5.0 * selected["ask"]
    selected["pnl_5"] = 5.0 * selected["pnl_share"]
    daily = selected.groupby("target_date").agg(pnl=("pnl_5", "sum"), cost=("cost_5", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    rng = np.random.default_rng(SEED)
    roi_samples = []
    for _ in range(N_BOOT):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        if take["cost"].sum() > 0:
            roi_samples.append(take["pnl"].sum() / take["cost"].sum())
    roi_ci_low, roi_ci_high = np.quantile(roi_samples, [0.025, 0.975])
    return {
        "rows": len(selected),
        "dates": selected["target_date"].nunique(),
        "cities": selected["city"].nunique(),
        "wins": int(selected["win"].sum()),
        "win_rate": float(selected["win"].mean()),
        "avg_ask": float(selected["ask"].mean()),
        "avg_calibrated_p": float(selected["calibrated_p"].mean()),
        "cost_5": float(selected["cost_5"].sum()),
        "pnl_5": float(selected["pnl_5"].sum()),
        "roi": float(selected["pnl_5"].sum() / selected["cost_5"].sum()),
        "roi_ci_low": float(roi_ci_low),
        "roi_ci_high": float(roi_ci_high),
        "losing_days": int((daily["pnl"] < 0).sum()),
        "le_minus_50pct_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss_5": float(daily["pnl"].min()),
        "daily_pnl_p10": float(daily["pnl"].quantile(0.10)),
        "daily_pnl_median": float(daily["pnl"].median()),
        "daily_pnl_p90": float(daily["pnl"].quantile(0.90)),
    }


def paired_roi_delta_ci(new: pd.DataFrame, old: pd.DataFrame) -> tuple[float, float, float]:
    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        pnl = 5.0 * (frame["win"] - frame["ask"] - frame["fee"])
        cost = 5.0 * frame["ask"]
        return frame.assign(pnl=pnl, cost=cost).groupby("target_date").agg(pnl=("pnl", "sum"), cost=("cost", "sum"))

    dn, do = daily(new), daily(old)
    dates = sorted(set(dn.index) | set(do.index))
    dn, do = dn.reindex(dates).fillna(0.0), do.reindex(dates).fillna(0.0)
    point = dn["pnl"].sum() / dn["cost"].sum() - do["pnl"].sum() / do["cost"].sum()
    rng = np.random.default_rng(SEED)
    samples = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        nc, oc = dn["cost"].to_numpy()[idx].sum(), do["cost"].to_numpy()[idx].sum()
        if nc > 0 and oc > 0:
            samples.append(dn["pnl"].to_numpy()[idx].sum() / nc - do["pnl"].to_numpy()[idx].sum() / oc)
    lo, hi = np.quantile(samples, [0.025, 0.975])
    return float(point), float(lo), float(hi)


def near_tie_pairs(frame: pd.DataFrame, increment: str, band: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    for date, group in frame.groupby("target_date"):
        winners = group[group["win"] == 1]
        losers = group[group["win"] == 0]
        correct = 0.0
        pairs = 0
        for winner in winners.itertuples(index=False):
            for loser in losers.itertuples(index=False):
                if abs(float(winner.old_score) - float(loser.old_score)) > band:
                    continue
                delta = float(getattr(winner, increment)) - float(getattr(loser, increment))
                correct += 1.0 if delta > 0 else 0.5 if delta == 0 else 0.0
                pairs += 1
        if pairs:
            rows.append({"target_date": date, "correct": correct, "pairs": pairs})
    daily = pd.DataFrame(rows)
    if daily.empty:
        return daily, {"pairs": 0, "dates": 0, "concordance": None, "ci_low": None, "ci_high": None}
    point = daily["correct"].sum() / daily["pairs"].sum()
    rng = np.random.default_rng(SEED)
    samples = []
    for _ in range(N_BOOT):
        take = daily.iloc[rng.integers(0, len(daily), len(daily))]
        samples.append(take["correct"].sum() / take["pairs"].sum())
    lo, hi = np.quantile(samples, [0.025, 0.975])
    return daily, {
        "pairs": int(daily["pairs"].sum()),
        "dates": len(daily),
        "concordance": float(point),
        "ci_low": float(lo),
        "ci_high": float(hi),
    }


def fmt(value: Any, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    return f"{float(value):+.1%}" if pct else f"{float(value):.4f}"


def main() -> None:
    base = v2.add_geometry(v2.source_quality_features(v2.station_bias_features(v2.load_universe())))
    oos = walk_forward(base)
    windows = {
        "oos_all": oos,
        "recent_ge_2026_06_21": oos[oos["target_date"] >= "2026-06-21"],
        "fresh_ge_2026_07_08": oos[oos["target_date"] >= "2026-07-08"],
    }

    probability_columns = {
        "market": "market_p",
        "old_raw_model": "model_p",
        "old_score_platt": "calibrated_p",
        "source_augmented": "source_aug_p",
        "overshoot_augmented": "overshoot_aug_p",
    }
    probability = {
        window: {arm: probability_metrics(data, column) for arm, column in probability_columns.items()}
        for window, data in windows.items() if not data.empty
    }
    ranking_auc = {
        window: {
            "market_ask": float(roc_auc_score(data["win"], data["market_p"])),
            "old_model_p": float(roc_auc_score(data["win"], data["model_p"])),
            "old_disagreement_score": float(roc_auc_score(data["win"], data["old_score"])),
            "source_augmented": float(roc_auc_score(data["win"], data["source_aug_p"])),
            "overshoot_augmented": float(roc_auc_score(data["win"], data["overshoot_aug_p"])),
        }
        for window, data in windows.items() if data["win"].nunique() == 2
    }
    probability_deltas = {
        window: {
            "platt_vs_old_brier": metric_delta_ci(data, "calibrated_p", "model_p", "brier"),
            "platt_vs_market_brier": metric_delta_ci(data, "calibrated_p", "market_p", "brier"),
            "source_aug_vs_platt_brier": metric_delta_ci(data, "source_aug_p", "calibrated_p", "brier"),
            "overshoot_aug_vs_platt_brier": metric_delta_ci(data, "overshoot_aug_p", "calibrated_p", "brier"),
        }
        for window, data in windows.items() if data["target_date"].nunique() >= 3
    }

    selection_frames: dict[tuple[str, str, int, float], pd.DataFrame] = {}
    selection_rows = []
    paired_rows = []
    for window, data in windows.items():
        if data.empty:
            continue
        for capacity in CAPACITIES:
            old = select_old(data, capacity)
            selection_frames[(window, "old", capacity, 0.0)] = old
            selection_rows.append({"window": window, "arm": "old", "tie_band": 0.0, "capacity": capacity, **summarize_selection(old)})
            for increment in ["source_increment", "overshoot_increment"]:
                arm = increment.removesuffix("_increment") + "_tiebreak"
                for band in TIE_BANDS:
                    selected = select_tiebreak(data, capacity, increment, band)
                    selection_frames[(window, arm, capacity, band)] = selected
                    selection_rows.append({"window": window, "arm": arm, "tie_band": band, "capacity": capacity, **summarize_selection(selected)})
                    point, lo, hi = paired_roi_delta_ci(selected, old)
                    old_keys = set(zip(old["target_date"], old["city"], old["bracket"]))
                    new_keys = set(zip(selected["target_date"], selected["city"], selected["bracket"]))
                    paired_rows.append({
                        "window": window,
                        "arm": arm,
                        "tie_band": band,
                        "capacity": capacity,
                        "roi_delta": point,
                        "ci_low": lo,
                        "ci_high": hi,
                        "overlap": len(old_keys & new_keys),
                        "old_rows": len(old_keys),
                        "new_rows": len(new_keys),
                    })
    selection_summary = pd.DataFrame(selection_rows)
    paired_summary = pd.DataFrame(paired_rows)

    pair_rows = []
    for window, data in windows.items():
        if data.empty:
            continue
        for increment in ["source_increment", "overshoot_increment"]:
            for band in TIE_BANDS:
                _, result = near_tie_pairs(data, increment, band)
                pair_rows.append({"window": window, "increment": increment, "tie_band": band, **result})
    pair_summary = pd.DataFrame(pair_rows)

    deciles = oos.copy()
    deciles["old_score_decile"] = pd.qcut(deciles["old_score"], 10, labels=False, duplicates="drop") + 1
    decile_summary = (
        deciles.groupby("old_score_decile")
        .agg(rows=("win", "size"), dates=("target_date", "nunique"), wins=("win", "sum"), realized=("win", "mean"), avg_ask=("ask", "mean"), avg_old_score=("old_score", "mean"), avg_calibrated_p=("calibrated_p", "mean"))
        .reset_index()
    )

    primary = paired_summary[
        (paired_summary["window"] == "oos_all")
        & (paired_summary["arm"] == "source_tiebreak")
        & (paired_summary["tie_band"] == PRIMARY_TIE_BAND)
        & (paired_summary["capacity"] == 5)
    ].iloc[0]
    primary_pairs = pair_summary[
        (pair_summary["window"] == "oos_all")
        & (pair_summary["increment"] == "source_increment")
        & (pair_summary["tie_band"] == PRIMARY_TIE_BAND)
    ].iloc[0]
    source_increment_pass = primary.roi_delta > 0 and primary.ci_low > 0 and primary_pairs.ci_low > 0.5
    verdict = "shadow_candidate" if source_increment_pass else "inconclusive"

    funnel = {
        "neutral_rows": len(base),
        "neutral_dates": base["target_date"].nunique(),
        "neutral_cities": base["city"].nunique(),
        "neutral_first_date": base["target_date"].min(),
        "neutral_last_date": base["target_date"].max(),
        "oos_rows": len(oos),
        "oos_dates": oos["target_date"].nunique(),
        "oos_first_date": oos["target_date"].min(),
        "oos_last_date": oos["target_date"].max(),
        "fact_built_at_utc": str(base["fact_built_at_utc"].max()),
        "min_calibration_slope": float(oos["calibration_slope"].min()),
        "max_calibration_slope": float(oos["calibration_slope"].max()),
    }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": {
            "strategy_family": "forecast_tail_low_price_yes",
            "denominator": "canonical settled BUY_YES ask 0.05..0.20 before old edge/dist filters",
            "walk_forward": "strict expanding target_date; test date excluded from train",
            "selection_rank": "old_score=model_p_yes-ask-official_weather_taker_fee",
            "calibration": "one-dimensional monotone Platt scaling; calibrated p never selects or sizes",
            "tie_break": "source/overshoot increment may reorder only inside fixed 1pp/2pp/5pp old-score bands",
            "primary_tie_band": PRIMARY_TIE_BAND,
            "fixed_shares": 5,
            "city_identity_used": False,
            "threshold_optimization": False,
            "candidate_count_k": 6,
        },
        "funnel": funnel,
        "probability": probability,
        "ranking_auc": ranking_auc,
        "probability_deltas": {w: {k: list(v) for k, v in rows.items()} for w, rows in probability_deltas.items()},
        "score_deciles": decile_summary.to_dict("records"),
        "selection_summary": selection_summary.to_dict("records"),
        "paired_roi_delta": paired_summary.to_dict("records"),
        "near_tie_pairs": pair_summary.to_dict("records"),
        "verdict": verdict,
    }

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    keep = [
        "candidate_id", "city", "target_date", "bracket", "ask", "fee", "win", "model_p",
        "old_score", "calibrated_p", "source_aug_p", "overshoot_aug_p", "source_increment",
        "overshoot_increment", "raw_dist_br", "train_rows_v3", "calibration_slope",
    ]
    oos[keep].to_csv(PRED_CSV, index=False)
    selection_summary.to_csv(SELECTION_CSV, index=False)
    pair_summary.to_csv(PAIR_CSV, index=False)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=lambda x: x.item() if hasattr(x, "item") else x) + "\n")

    probability_lines = []
    for window, arms in probability.items():
        for arm, row in arms.items():
            probability_lines.append(
                f"| {window} | {arm} | {row['rows']} | {row['dates']} | {fmt(row['realized'], True)} | {fmt(row['mean_pred'], True)} | {fmt(row['brier'])} | {fmt(row['logloss'])} | {fmt(row['auc'])} |"
            )
    delta_lines = []
    for window, rows in probability_deltas.items():
        for name, (point, lo, hi) in rows.items():
            delta_lines.append(f"| {window} | {name} | {point:+.6f} | [{lo:+.6f}, {hi:+.6f}] |")
    ranking_lines = [
        f"| {window} | {arm} | {fmt(value)} |"
        for window, rows in ranking_auc.items()
        for arm, value in rows.items()
    ]
    decile_lines = [
        f"| {int(row.old_score_decile)} | {int(row.rows)} | {int(row.dates)} | {int(row.wins)} | {fmt(row.realized, True)} | {fmt(row.avg_ask, True)} | {fmt(row.avg_old_score, True)} | {fmt(row.avg_calibrated_p, True)} |"
        for _, row in decile_summary.iterrows()
    ]
    selection_lines = []
    for _, row in selection_summary.iterrows():
        if row.arm != "old" and row.tie_band != PRIMARY_TIE_BAND:
            continue
        selection_lines.append(
            f"| {row.window} | {row.arm} | {int(row.capacity)} | {int(row.rows)} | {int(row.dates)} | {int(row.wins)} | {fmt(row.win_rate, True)} | {fmt(row.avg_ask, True)} | {fmt(row.avg_calibrated_p, True)} | {fmt(row.roi, True)} [{fmt(row.roi_ci_low, True)}, {fmt(row.roi_ci_high, True)}] | {int(row.losing_days)} | {int(row.le_minus_50pct_days)} | {row.max_daily_loss_5:+.2f} | {row.daily_pnl_p10:+.2f}/{row.daily_pnl_median:+.2f}/{row.daily_pnl_p90:+.2f} |"
        )
    paired_lines = [
        f"| {row.window} | {row.arm} | {fmt(row.tie_band, True)} | {int(row.capacity)} | {fmt(row.roi_delta, True)} | [{fmt(row.ci_low, True)}, {fmt(row.ci_high, True)}] | {int(row.overlap)}/{int(row.old_rows)}/{int(row.new_rows)} |"
        for _, row in paired_summary.iterrows()
    ]
    pair_lines = [
        f"| {row.window} | {row.increment} | {fmt(row.tie_band, True)} | {int(row.pairs)} | {int(row.dates)} | {fmt(row.concordance, True)} | [{fmt(row.ci_low, True)}, {fmt(row.ci_high, True)}] |"
        for _, row in pair_summary.iterrows()
    ]

    OUT_MD.write_text(
        f"""# HeadA Rank-Preserving Calibration v3

Generated: 2026-07-13  
Scope: `forecast_tail_low_price_yes` only. Research layer; no live selector, sizing, or runner change.

## Verdict

`{verdict}`. The next clean architecture is valid, but only its calibration half earns a role today:

- Keep the old model-market disagreement as the selection rank.
- Replace the literal old `model_p_yes` interpretation with expanding-date monotone calibration for expected hit rate and bankroll diagnostics.
- Source/overshoot do not enter the selector unless their near-tie paired lift is positive and significant. The primary test is source tie-break inside a fixed 2pp old-score band at N=5/day.

Primary source tie-break delta: {fmt(primary.roi_delta, True)} with target-date 95% CI [{fmt(primary.ci_low, True)}, {fmt(primary.ci_high, True)}]; conditional pair concordance {fmt(primary_pairs.concordance, True)} [{fmt(primary_pairs.ci_low, True)}, {fmt(primary_pairs.ci_high, True)}].

## Data And Funnel

- Neutral denominator: {funnel['neutral_rows']} settled 5-20c BUY_YES tickets / {funnel['neutral_dates']} target dates / {funnel['neutral_cities']} cities, {funnel['neutral_first_date']}..{funnel['neutral_last_date']}.
- Strict expanding OOS: {funnel['oos_rows']} tickets / {funnel['oos_dates']} dates, {funnel['oos_first_date']}..{funnel['oos_last_date']}.
- Canonical fact build: `{funnel['fact_built_at_utc']}`. The 2026-07-13 source sync succeeded; rebuild then stopped at strategy-order migration on `database is locked`, so this study intentionally uses the last successful canonical fact snapshot and does not claim later settlement coverage.
- Grain: one city-date-bracket ticket; equal-capacity selection dedupes to one bracket per city-date before N=1/3/5 per target date.
- PIT: station/source quality uses target dates strictly earlier than each test date. No city identity and no future observation.
- Entry cost: canonical decision ask plus official Weather taker fee `0.05*p*(1-p)`. Risk table uses fixed 5 shares.

## Probability Quality

| window | probability head | rows | dates | realized | mean p | Brier | logloss | AUC |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(probability_lines)}

Negative paired delta is better.

| window | comparison | metric delta | target-date 95% CI |
|---|---|---:|---:|
{chr(10).join(delta_lines)}

The Platt slope stayed positive in every OOS fit ({funnel['min_calibration_slope']:.3f}..{funnel['max_calibration_slope']:.3f}), so calibration never reverses the old rank. Its AUC should therefore match `old_score`; its job is honest probability, not a new alpha claim.

### Ranking AUC

| window | score | AUC |
|---|---|---:|
{chr(10).join(ranking_lines)}

Important nuance: the old disagreement score is not a strong broad-universe ranker; its full OOS AUC is {ranking_auc['oos_all']['old_disagreement_score']:.4f}. Its positive trading result is concentrated in the extreme daily top-N selection. Therefore v3 preserves that observed top-tail ordering, not a claim that every score increment is monotonically informative across all 5-20c tickets.

## Old Score Dose Response

| decile | rows | dates | wins | realized | avg ask | avg old score | avg calibrated p |
|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(decile_lines)}

## Fixed 5-Share Selection

Only the predeclared 2pp tie-break is shown here; 1pp/5pp sensitivity and all paired deltas are retained below and in CSV/JSON.

| window | arm | N/day | rows | dates | wins | win rate | avg ask | avg calibrated p | ROI [95% CI] | losing days | <=-50% days | max daily loss USD | daily PnL p10/med/p90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(selection_lines)}

## Tie-Break Paired Delta

`overlap` is `common/old/new`. Candidate count K=6 (source/overshoot x 1pp/2pp/5pp); no best-band cherry-pick.

| window | arm | band | N/day | ROI delta vs old | target-date 95% CI | overlap |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(paired_lines)}

## Conditional Near-Tie Test

This removes the broad ranking question: only winner-loser pairs from the same target date whose old scores differ by at most the stated band are compared. `50%` means the increment adds no ordering information.

| window | increment | band | pairs | dates | concordance | target-date 95% CI |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(pair_lines)}

## Interpretation

HeadA is a convex tail-lottery, so a small number of winners carrying PnL is expected and is not a standalone rejection test. The relevant question is whether a feature improves ex-ante capture at the same ticket capacity without seeing settlement. Market anchoring improved v2 calibration mostly by copying price and damaged this disagreement rank; v3 therefore does not let market probability overwrite selection.

The monotone calibration head is useful for forecasting hit cadence, losing streaks, and bankroll stress, but it cannot improve trading ROI because it deliberately preserves order. A new selector requires paired lift. Source/overshoot are tested only where they could plausibly help: near-tied old scores. If that lift is absent, they stay telemetry rather than adding complexity.

## Contract Verdict

significance={'PASS' if source_increment_pass else 'FAIL'} for the predeclared source 2pp/N=5 paired delta and pair concordance; baseline={'PASS' if source_increment_pass else 'FAIL'} versus old equal-capacity rank; forward=FAIL/NA because the fresh slice remains thin; conclusion={verdict}.

No live action. Current fixed 5-share HeadA remains unchanged. Promotion would require a positive target-date paired CI and same-sign recent/fresh evidence, followed by executable-book/fill replay.

## Eight Rings

Covered: neutral opportunity denominator, target-date inference, signal ranking, probability calibration, official fee, fixed-share daily risk, same-capacity counterfactual, recent/fresh windows. Missing/partial: fresh executable-book replay, actual maker fill probability, size capacity, live portfolio correlation, pristine post-registration forward.
"""
    )
    print(json.dumps({"funnel": funnel, "primary": primary.to_dict(), "primary_pairs": primary_pairs.to_dict(), "verdict": verdict}, indent=2, default=str))


if __name__ == "__main__":
    main()
