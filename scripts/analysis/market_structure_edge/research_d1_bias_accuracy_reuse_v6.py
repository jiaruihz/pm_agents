#!/usr/bin/env python3
"""Reuse frozen forecast-bias history in the D1 distance-2 NO denominator."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
for import_path in (ROOT, SCRIPT_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import research_d1_full_ladder_no_source_confidence_city_v2 as v2
import research_d1_global_region_source_causality_v5 as v5
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import (
    CITY_MODEL,
)


DEFAULT_INPUT = v2.DEFAULT_OUTPUT
DEFAULT_ERRORS = v2.DEFAULT_ERRORS
DEFAULT_LONG_ERRORS = (
    ROOT
    / "docs/analysis/2026-06/generated/"
    "historical_forecast_station_bias_v1/daily_error_rows.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_bias_accuracy_reuse_v6"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-bias-accuracy-reuse-v6.md"
)
MODELS = list(v2.MODELS)
REGION_ORDER = v5.REGION_ORDER
SEED = 2026072867
EPS = 1e-6
OOF_MIN_TRAIN_DATES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument(
        "--long-errors", type=Path, default=DEFAULT_LONG_ERRORS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=30_000)
    return parser.parse_args()


def logit(values: pd.Series) -> np.ndarray:
    clipped = values.astype(float).clip(EPS, 1.0 - EPS)
    return np.log(clipped / (1.0 - clipped)).to_numpy()


def score_columns(
    frame: pd.DataFrame, probability_column: str, prefix: str
) -> pd.DataFrame:
    probability = frame[probability_column].astype(float).clip(
        EPS, 1.0 - EPS
    )
    frame[f"{prefix}_brier"] = np.square(
        probability - frame["no_win"]
    )
    frame[f"{prefix}_logloss"] = -(
        frame["no_win"] * np.log(probability)
        + (1.0 - frame["no_win"]) * np.log(1.0 - probability)
    )
    return frame


def load_short_residuals(
    errors: pd.DataFrame, *, eligible_cities: set[str]
) -> tuple[
    dict[tuple[str, str], dict[str, np.ndarray]],
    pd.DataFrame,
]:
    train = errors[
        (errors["target_date"] <= v2.TRAIN_END)
        & errors["city"].astype(str).isin(eligible_cities)
        & errors["model_key"].isin(MODELS)
        & errors["error_f"].notna()
    ].copy()
    global_bias = train.groupby("model_key")["error_f"].mean().to_dict()
    residuals: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    stats: list[dict[str, Any]] = []
    for (city, model), group in train.groupby(["city", "model_key"]):
        values = group["error_f"].astype(float).to_numpy()
        if len(values) < 20:
            continue
        city_bias = float(values.mean())
        model_bias = float(global_bias[str(model)])
        centered = values - city_bias
        residuals[(str(city), str(model))] = {
            "short_full": values,
            "short_debiased": centered,
            "short_global_bias": centered + model_bias,
            "short_shrink50": centered
            + 0.5 * city_bias
            + 0.5 * model_bias,
        }
        stats.append(
            {
                "city": str(city),
                "model_key": str(model),
                "n": len(values),
                "city_bias_f": city_bias,
                "global_model_bias_f": model_bias,
                "mae_f": float(np.mean(np.abs(values))),
            }
        )
    return residuals, pd.DataFrame(stats)


def load_long_residuals(
    rows: pd.DataFrame, *, eligible_cities: set[str]
) -> tuple[
    dict[tuple[str, str], dict[str, np.ndarray]],
    pd.DataFrame,
]:
    history = rows[
        rows["city"].astype(str).isin(eligible_cities)
        & rows["model"].isin(["ecmwf", "gfs"])
        & rows["error_f_actual_minus_forecast"].notna()
        & (rows["date"] < "2026-06-17")
    ].copy()
    residuals: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    stats: list[dict[str, Any]] = []
    for (city, model), group in history.groupby(["city", "model"]):
        values = group[
            "error_f_actual_minus_forecast"
        ].astype(float).to_numpy()
        if len(values) < 20:
            continue
        bias = float(values.mean())
        residuals[(str(city), str(model))] = {
            "long_full": values,
            "long_debiased": values - bias,
        }
        stats.append(
            {
                "city": str(city),
                "model": str(model),
                "n": len(values),
                "first_date": str(group["date"].min()),
                "last_date": str(group["date"].max()),
                "bias_f": bias,
                "mae_f": float(np.mean(np.abs(values))),
            }
        )
    return residuals, pd.DataFrame(stats)


def probability(
    row: Any,
    *,
    forecast_column: str,
    residuals: np.ndarray,
) -> float:
    return v2.source_probability(
        forecast_max_f=float(getattr(row, forecast_column)),
        residuals_f=residuals,
        unit=str(row.market_unit),
        bracket_label=str(row.bracket),
        question=str(row.question),
    )


def attach_bias_probabilities(
    candidates: pd.DataFrame,
    short_residuals: dict[
        tuple[str, str], dict[str, np.ndarray]
    ],
    short_stats: pd.DataFrame,
    long_residuals: dict[
        tuple[str, str], dict[str, np.ndarray]
    ],
    long_stats: pd.DataFrame,
) -> pd.DataFrame:
    short_index = short_stats.set_index(
        ["city", "model_key"]
    ).to_dict("index")
    long_index = long_stats.set_index(["city", "model"]).to_dict("index")
    records: list[dict[str, Any]] = []
    for row in candidates.itertuples(index=False):
        record = row._asdict()
        city = str(row.city)
        short_policy_values: dict[str, list[float]] = {
            name: []
            for name in (
                "short_full",
                "short_debiased",
                "short_global_bias",
                "short_shrink50",
            )
        }
        city_biases: list[float] = []
        city_maes: list[float] = []
        for model in MODELS:
            arrays = short_residuals[(city, model)]
            stats = short_index[(city, model)]
            city_biases.append(float(stats["city_bias_f"]))
            city_maes.append(float(stats["mae_f"]))
            for policy, values in short_policy_values.items():
                values.append(
                    probability(
                        row,
                        forecast_column=f"forecast_{model}",
                        residuals=arrays[policy],
                    )
                )
        for policy, values in short_policy_values.items():
            record[f"p_no_{policy}"] = float(np.mean(values))
        record["short_mean_bias_f"] = float(np.mean(city_biases))
        record["short_mean_abs_bias_f"] = float(
            np.mean(np.abs(city_biases))
        )
        record["short_mean_mae_f"] = float(np.mean(city_maes))
        record["forecast_spread_f"] = float(
            np.std(
                [
                    float(getattr(row, f"forecast_{model}"))
                    for model in MODELS
                ],
                ddof=0,
            )
        )

        assigned_family = CITY_MODEL.get(city, "gfs")
        assigned_key = (
            "ecmwf_ifs025"
            if assigned_family == "ecmwf"
            else "gfs_global"
        )
        record["assigned_family"] = assigned_family
        for suffix in ("full", "debiased"):
            record[f"p_no_long_assigned_{suffix}"] = probability(
                row,
                forecast_column=f"forecast_{assigned_key}",
                residuals=long_residuals[(city, assigned_family)][
                    f"long_{suffix}"
                ],
            )
            source_values = []
            for family, key in (
                ("ecmwf", "ecmwf_ifs025"),
                ("gfs", "gfs_global"),
            ):
                source_values.append(
                    probability(
                        row,
                        forecast_column=f"forecast_{key}",
                        residuals=long_residuals[(city, family)][
                            f"long_{suffix}"
                        ],
                    )
                )
            record[f"p_no_long_equal2_{suffix}"] = float(
                np.mean(source_values)
            )
        long_city_stats = [
            long_index[(city, family)] for family in ("ecmwf", "gfs")
        ]
        record["long_mean_bias_f"] = float(
            np.mean([item["bias_f"] for item in long_city_stats])
        )
        record["long_mean_abs_bias_f"] = float(
            np.mean(
                [abs(float(item["bias_f"])) for item in long_city_stats]
            )
        )
        record["long_mean_mae_f"] = float(
            np.mean([item["mae_f"] for item in long_city_stats])
        )
        records.append(record)
    output = pd.DataFrame(records)
    output["region"] = output["city"].map(
        lambda city: FULL_CITY_CONFIGS[str(city)]["region"]
    )
    recompute_error = float(
        np.max(
            np.abs(
                output["p_no_short_full"]
                - output[
                    [f"p_no_{model}" for model in MODELS]
                ].mean(axis=1)
            )
        )
    )
    if recompute_error > 1e-12:
        raise RuntimeError(
            f"short full-bias probability drift: {recompute_error}"
        )
    for policy in probability_policies():
        output = score_columns(
            output, f"p_no_{policy}", prefix=policy
        )
    output = score_columns(
        output, "market_p_no", prefix="market"
    )
    return output


def probability_policies() -> list[str]:
    return [
        "short_full",
        "short_debiased",
        "short_global_bias",
        "short_shrink50",
        "long_assigned_full",
        "long_assigned_debiased",
        "long_equal2_full",
        "long_equal2_debiased",
    ]


def block_mean_delta(
    rows: pd.DataFrame,
    left: str,
    right: str,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    return v5.block_score_delta_samples(
        rows, left, right, draws=draws, seed=seed
    )


def probability_summary(
    rows: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, policy in enumerate(probability_policies()):
        brier = f"{policy}_brier"
        logloss = f"{policy}_logloss"
        brier_samples = block_mean_delta(
            rows,
            brier,
            "market_brier",
            draws=draws,
            seed=SEED + index,
        )
        logloss_samples = block_mean_delta(
            rows,
            logloss,
            "market_logloss",
            draws=draws,
            seed=SEED + 100 + index,
        )
        records.append(
            {
                "policy": policy,
                "candidate_rows": len(rows),
                "target_dates": int(rows["target_date"].nunique()),
                "brier": float(rows[brier].mean()),
                "market_brier": float(rows["market_brier"].mean()),
                "brier_delta_vs_market": float(brier_samples.mean()),
                "brier_ci_low": float(
                    np.quantile(brier_samples, 0.025)
                ),
                "brier_ci_high": float(
                    np.quantile(brier_samples, 0.975)
                ),
                "brier_p": v5.two_sided_p(brier_samples),
                "logloss": float(rows[logloss].mean()),
                "market_logloss": float(
                    rows["market_logloss"].mean()
                ),
                "logloss_delta_vs_market": float(
                    logloss_samples.mean()
                ),
                "logloss_ci_low": float(
                    np.quantile(logloss_samples, 0.025)
                ),
                "logloss_ci_high": float(
                    np.quantile(logloss_samples, 0.975)
                ),
                "logloss_p": v5.two_sided_p(logloss_samples),
            }
        )
    summary = pd.DataFrame(records)
    summary["brier_q_bh"] = v5.bh_qvalues(summary["brier_p"])
    summary["logloss_q_bh"] = v5.bh_qvalues(summary["logloss_p"])
    return summary


def bias_increment_summary(
    rows: pd.DataFrame, *, draws: int
) -> pd.DataFrame:
    pairs = [
        ("short_full", "short_debiased"),
        ("short_global_bias", "short_debiased"),
        ("short_shrink50", "short_debiased"),
        ("long_assigned_full", "long_assigned_debiased"),
        ("long_equal2_full", "long_equal2_debiased"),
    ]
    records: list[dict[str, Any]] = []
    for index, (corrected, debiased) in enumerate(pairs):
        brier_samples = block_mean_delta(
            rows,
            f"{corrected}_brier",
            f"{debiased}_brier",
            draws=draws,
            seed=SEED + 200 + index,
        )
        logloss_samples = block_mean_delta(
            rows,
            f"{corrected}_logloss",
            f"{debiased}_logloss",
            draws=draws,
            seed=SEED + 300 + index,
        )
        records.append(
            {
                "corrected": corrected,
                "debiased": debiased,
                "brier_delta_corrected_minus_debiased": float(
                    brier_samples.mean()
                ),
                "brier_ci_low": float(
                    np.quantile(brier_samples, 0.025)
                ),
                "brier_ci_high": float(
                    np.quantile(brier_samples, 0.975)
                ),
                "brier_p": v5.two_sided_p(brier_samples),
                "logloss_delta_corrected_minus_debiased": float(
                    logloss_samples.mean()
                ),
                "logloss_ci_low": float(
                    np.quantile(logloss_samples, 0.025)
                ),
                "logloss_ci_high": float(
                    np.quantile(logloss_samples, 0.975)
                ),
                "logloss_p": v5.two_sided_p(logloss_samples),
            }
        )
    output = pd.DataFrame(records)
    output["brier_q_bh"] = v5.bh_qvalues(output["brier_p"])
    output["logloss_q_bh"] = v5.bh_qvalues(output["logloss_p"])
    return output


def select_policy(
    candidates: pd.DataFrame, probability_column: str, policy: str
) -> pd.DataFrame:
    rows = candidates.copy()
    rows["selection_edge"] = rows[probability_column] - rows["cost"]
    rows["selection_policy"] = policy
    return (
        rows.sort_values(
            ["snapshot_key", "selection_edge", "cost"],
            ascending=[True, False, True],
        )
        .groupby("snapshot_key", as_index=False)
        .first()
    )


def trade_summary(
    candidates: pd.DataFrame, *, draws: int
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    selected = {
        policy: select_policy(
            candidates, f"p_no_{policy}", policy
        )
        for policy in probability_policies()
    }
    selected["market"] = select_policy(
        candidates, "market_p_no", "market"
    )
    market = selected["market"]
    debiased = selected["short_debiased"]
    records: list[dict[str, Any]] = []
    for index, (policy, rows) in enumerate(selected.items()):
        roi_samples = v5.block_roi_samples(
            rows, draws=draws, seed=SEED + 400 + index
        )
        market_delta = v5.paired_roi_delta_samples(
            rows,
            market,
            draws=draws,
            seed=SEED + 500 + index,
        )
        debiased_delta = v5.paired_roi_delta_samples(
            rows,
            debiased,
            draws=draws,
            seed=SEED + 550 + index,
        )
        records.append(
            {
                "policy": policy,
                "baskets": len(rows),
                "target_dates": int(rows["target_date"].nunique()),
                "cities": int(rows["city"].nunique()),
                "mean_cost": float(rows["cost"].mean()),
                "win_rate": float(rows["no_win"].mean()),
                "fee_adjusted_roi": v5.roi(rows),
                "roi_ci_low": float(
                    np.quantile(roi_samples, 0.025)
                ),
                "roi_ci_high": float(
                    np.quantile(roi_samples, 0.975)
                ),
                "roi_delta_vs_market": float(market_delta.mean()),
                "market_delta_ci_low": float(
                    np.quantile(market_delta, 0.025)
                ),
                "market_delta_ci_high": float(
                    np.quantile(market_delta, 0.975)
                ),
                "roi_delta_vs_short_debiased": float(
                    debiased_delta.mean()
                ),
                "debiased_delta_ci_low": float(
                    np.quantile(debiased_delta, 0.025)
                ),
                "debiased_delta_ci_high": float(
                    np.quantile(debiased_delta, 0.975)
                ),
            }
        )
    return pd.DataFrame(records), selected


def region_bias_trade_summary(
    selected: dict[str, pd.DataFrame],
    *,
    draws: int,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for region_index, region in enumerate(REGION_ORDER):
        full = selected["short_full"]
        debiased = selected["short_debiased"]
        full_region = full[full["region"].eq(region)]
        debiased_region = debiased[debiased["region"].eq(region)]
        delta_samples = v5.paired_roi_delta_samples(
            full_region,
            debiased_region,
            draws=draws,
            seed=SEED + 1000 + region_index,
        )
        for name, rows in (("short_full", full), ("short_debiased", debiased)):
            region_rows = rows[rows["region"].eq(region)]
            roi_samples = v5.block_roi_samples(
                region_rows,
                draws=draws,
                seed=SEED
                + 1100
                + region_index
                + int(name == "short_debiased") * 20,
            )
            records.append(
                {
                    "region": region,
                    "policy": name,
                    "baskets": len(region_rows),
                    "target_dates": int(
                        region_rows["target_date"].nunique()
                    ),
                    "fee_adjusted_roi": v5.roi(region_rows),
                    "roi_ci_low": float(
                        np.quantile(roi_samples, 0.025)
                    ),
                    "roi_ci_high": float(
                        np.quantile(roi_samples, 0.975)
                    ),
                    "full_minus_debiased_roi_delta": float(
                        delta_samples.mean()
                    ),
                    "delta_ci_low": float(
                        np.quantile(delta_samples, 0.025)
                    ),
                    "delta_ci_high": float(
                        np.quantile(delta_samples, 0.975)
                    ),
                }
            )
        full_index = full[
            full["region"].eq(region)
        ].set_index("snapshot_key")["bracket"]
        debiased_index = debiased[
            debiased["region"].eq(region)
        ].set_index("snapshot_key")["bracket"]
        agreement = float(full_index.eq(debiased_index).mean())
        records[-2]["selection_agreement"] = agreement
        records[-1]["selection_agreement"] = agreement
    return pd.DataFrame(records)


def accuracy_summary(
    errors: pd.DataFrame,
    short_stats: pd.DataFrame,
    long_stats: pd.DataFrame,
    *,
    eligible_cities: set[str],
    draws: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    holdout = errors[
        (errors["target_date"] > v2.TRAIN_END)
        & errors["city"].astype(str).isin(eligible_cities)
        & errors["model_key"].isin(MODELS)
        & errors["error_f"].notna()
    ].copy()
    short_bias = short_stats[
        ["city", "model_key", "city_bias_f"]
    ]
    holdout = holdout.merge(
        short_bias,
        on=["city", "model_key"],
        validate="many_to_one",
    )
    holdout["region"] = holdout["city"].map(
        lambda city: FULL_CITY_CONFIGS[str(city)]["region"]
    )
    holdout["raw_abs_error_f"] = holdout["error_f"].abs()
    holdout["corrected_abs_error_f"] = (
        holdout["error_f"] - holdout["city_bias_f"]
    ).abs()
    overall_samples = block_mean_delta(
        holdout,
        "corrected_abs_error_f",
        "raw_abs_error_f",
        draws=draws,
        seed=SEED + 600,
    )
    records = [
        {
            "scope": "all_short5",
            "rows": len(holdout),
            "target_dates": int(holdout["target_date"].nunique()),
            "cities": int(holdout["city"].nunique()),
            "raw_mae_f": float(holdout["raw_abs_error_f"].mean()),
            "corrected_mae_f": float(
                holdout["corrected_abs_error_f"].mean()
            ),
            "mae_delta": float(overall_samples.mean()),
            "mae_delta_ci_low": float(
                np.quantile(overall_samples, 0.025)
            ),
            "mae_delta_ci_high": float(
                np.quantile(overall_samples, 0.975)
            ),
        }
    ]
    for index, region in enumerate(REGION_ORDER):
        rows = holdout[holdout["region"].eq(region)]
        samples = block_mean_delta(
            rows,
            "corrected_abs_error_f",
            "raw_abs_error_f",
            draws=draws,
            seed=SEED + 610 + index,
        )
        records.append(
            {
                "scope": region,
                "rows": len(rows),
                "target_dates": int(rows["target_date"].nunique()),
                "cities": int(rows["city"].nunique()),
                "raw_mae_f": float(rows["raw_abs_error_f"].mean()),
                "corrected_mae_f": float(
                    rows["corrected_abs_error_f"].mean()
                ),
                "mae_delta": float(samples.mean()),
                "mae_delta_ci_low": float(
                    np.quantile(samples, 0.025)
                ),
                "mae_delta_ci_high": float(
                    np.quantile(samples, 0.975)
                ),
            }
        )

    family_map = {
        "ecmwf_ifs025": "ecmwf",
        "gfs_global": "gfs",
    }
    long_holdout = holdout[
        holdout["model_key"].isin(family_map)
    ].copy()
    long_holdout["model"] = long_holdout["model_key"].map(family_map)
    long_holdout = long_holdout.merge(
        long_stats[["city", "model", "bias_f"]],
        on=["city", "model"],
        validate="many_to_one",
        suffixes=("", "_long"),
    )
    long_holdout["long_corrected_abs_error_f"] = (
        long_holdout["error_f"] - long_holdout["bias_f"]
    ).abs()
    long_samples = block_mean_delta(
        long_holdout,
        "long_corrected_abs_error_f",
        "raw_abs_error_f",
        draws=draws,
        seed=SEED + 700,
    )
    long_summary = pd.DataFrame(
        [
            {
                "scope": "long_history_ecmwf_gfs",
                "rows": len(long_holdout),
                "target_dates": int(
                    long_holdout["target_date"].nunique()
                ),
                "cities": int(long_holdout["city"].nunique()),
                "raw_mae_f": float(
                    long_holdout["raw_abs_error_f"].mean()
                ),
                "corrected_mae_f": float(
                    long_holdout[
                        "long_corrected_abs_error_f"
                    ].mean()
                ),
                "mae_delta": float(long_samples.mean()),
                "mae_delta_ci_low": float(
                    np.quantile(long_samples, 0.025)
                ),
                "mae_delta_ci_high": float(
                    np.quantile(long_samples, 0.975)
                ),
            }
        ]
    )
    return pd.DataFrame(records), long_summary


def expanding_oof(
    candidates: pd.DataFrame,
) -> pd.DataFrame:
    rows = candidates.copy()
    rows["market_logit"] = logit(rows["market_p_no"])
    rows["short_full_logit"] = logit(rows["p_no_short_full"])
    rows["short_debiased_logit"] = logit(
        rows["p_no_short_debiased"]
    )
    rows["long_equal2_full_logit"] = logit(
        rows["p_no_long_equal2_full"]
    )
    specs = {
        "market_platt": ["market_logit"],
        "market_plus_short_full": [
            "market_logit",
            "short_full_logit",
        ],
        "market_plus_short_debiased": [
            "market_logit",
            "short_debiased_logit",
        ],
        "market_plus_bias_accuracy": [
            "market_logit",
            "short_debiased_logit",
            "short_mean_bias_f",
            "short_mean_mae_f",
            "forecast_spread_f",
        ],
        "market_plus_long_history": [
            "market_logit",
            "long_equal2_full_logit",
            "long_mean_bias_f",
            "long_mean_mae_f",
            "forecast_spread_f",
        ],
    }
    dates = sorted(rows["target_date"].astype(str).unique())
    records: list[pd.DataFrame] = []
    for date_index, target_date in enumerate(dates):
        if date_index < OOF_MIN_TRAIN_DATES:
            continue
        train = rows[rows["target_date"].astype(str) < target_date]
        test = rows[rows["target_date"].astype(str).eq(target_date)]
        scored = test[
            [
                "snapshot_key",
                "target_date",
                "city",
                "region",
                "bracket",
                "cost",
                "pnl",
                "no_win",
                "market_p_no",
            ]
        ].copy()
        for name, features in specs.items():
            model = make_pipeline(
                StandardScaler(),
                LogisticRegression(
                    C=0.1,
                    max_iter=2000,
                    random_state=SEED,
                ),
            )
            model.fit(
                train[features].astype(float),
                train["no_win"].astype(int),
            )
            scored[f"p_no_{name}"] = model.predict_proba(
                test[features].astype(float)
            )[:, 1]
        records.append(scored)
    return pd.concat(records, ignore_index=True)


def oof_summary(
    rows: pd.DataFrame, *, draws: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    model_names = [
        column.removeprefix("p_no_")
        for column in rows.columns
        if column.startswith("p_no_")
    ]
    scored = score_columns(rows.copy(), "market_p_no", "market")
    probability_records: list[dict[str, Any]] = []
    selected: dict[str, pd.DataFrame] = {
        "market": select_policy(scored, "market_p_no", "market")
    }
    for index, name in enumerate(model_names):
        scored = score_columns(
            scored, f"p_no_{name}", name
        )
        brier_samples = block_mean_delta(
            scored,
            f"{name}_brier",
            "market_brier",
            draws=draws,
            seed=SEED + 800 + index,
        )
        logloss_samples = block_mean_delta(
            scored,
            f"{name}_logloss",
            "market_logloss",
            draws=draws,
            seed=SEED + 850 + index,
        )
        probability_records.append(
            {
                "model": name,
                "candidate_rows": len(scored),
                "target_dates": int(
                    scored["target_date"].nunique()
                ),
                "brier": float(scored[f"{name}_brier"].mean()),
                "market_brier": float(
                    scored["market_brier"].mean()
                ),
                "brier_delta_vs_market": float(
                    brier_samples.mean()
                ),
                "brier_ci_low": float(
                    np.quantile(brier_samples, 0.025)
                ),
                "brier_ci_high": float(
                    np.quantile(brier_samples, 0.975)
                ),
                "brier_p": v5.two_sided_p(brier_samples),
                "logloss": float(
                    scored[f"{name}_logloss"].mean()
                ),
                "market_logloss": float(
                    scored["market_logloss"].mean()
                ),
                "logloss_delta_vs_market": float(
                    logloss_samples.mean()
                ),
                "logloss_ci_low": float(
                    np.quantile(logloss_samples, 0.025)
                ),
                "logloss_ci_high": float(
                    np.quantile(logloss_samples, 0.975)
                ),
                "logloss_p": v5.two_sided_p(logloss_samples),
            }
        )
        selected[name] = select_policy(
            scored, f"p_no_{name}", name
        )
    probability = pd.DataFrame(probability_records)
    probability["brier_q_bh"] = v5.bh_qvalues(
        probability["brier_p"]
    )
    probability["logloss_q_bh"] = v5.bh_qvalues(
        probability["logloss_p"]
    )

    market = selected["market"]
    trade_records: list[dict[str, Any]] = []
    for index, (name, frame) in enumerate(selected.items()):
        roi_samples = v5.block_roi_samples(
            frame, draws=draws, seed=SEED + 900 + index
        )
        delta_samples = v5.paired_roi_delta_samples(
            frame,
            market,
            draws=draws,
            seed=SEED + 950 + index,
        )
        trade_records.append(
            {
                "model": name,
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "fee_adjusted_roi": v5.roi(frame),
                "roi_ci_low": float(
                    np.quantile(roi_samples, 0.025)
                ),
                "roi_ci_high": float(
                    np.quantile(roi_samples, 0.975)
                ),
                "roi_delta_vs_market": float(
                    delta_samples.mean()
                ),
                "delta_ci_low": float(
                    np.quantile(delta_samples, 0.025)
                ),
                "delta_ci_high": float(
                    np.quantile(delta_samples, 0.975)
                ),
            }
        )
    return probability, pd.DataFrame(trade_records)


def pct(value: float) -> str:
    return f"{value:+.2%}"


def write_report(
    path: Path,
    *,
    accuracy: pd.DataFrame,
    long_accuracy: pd.DataFrame,
    probability: pd.DataFrame,
    increments: pd.DataFrame,
    trades: pd.DataFrame,
    regions: pd.DataFrame,
    oof_probability: pd.DataFrame,
    oof_trades: pd.DataFrame,
    oof_rows: int,
    oof_baskets: int,
    short_stats: pd.DataFrame,
    long_stats: pd.DataFrame,
) -> None:
    acc = accuracy.set_index("scope")
    long_acc = long_accuracy.iloc[0]
    prob = probability.set_index("policy")
    inc = increments.set_index("corrected")
    trade = trades.set_index("policy")
    oof_prob = oof_probability.set_index("model")
    oof_trade = oof_trades.set_index("model")
    region_index = regions.set_index(["region", "policy"])
    eu_full_roi = float(
        region_index.loc[("EU", "short_full"), "fee_adjusted_roi"]
    )
    eu_debiased_roi = float(
        region_index.loc[
            ("EU", "short_debiased"), "fee_adjusted_roi"
        ]
    )
    lines = [
        "# D1 distance-2 NO：历史 forecast bias / accuracy 复用实验 v6",
        "",
        "## 数据快照",
        "",
        "- 当前误差层：`historical_forecast_enrichment_bias_v1/daily_error_rows.csv`，"
        "2026-05-04..2026-07-07；bias calibration 严格冻结到 "
        f"{v2.TRAIN_END}。",
        "- 早期长历史层：`historical_forecast_station_bias_v1/daily_error_rows.csv`，"
        f"{int(long_stats['n'].sum()):,} city-source-days；GFS/ECMWF 截止日期均早于"
        "当前交易窗口。",
        "- 交易分母：immutable D1 distance=2 paired opportunity，1,190 "
        "baskets / 20 target dates / 43 cities / 2,380 candidate rows；"
        "settled=100%，unsettled=0，missing bracket=0，actual fill=0。",
        "- exact bracket：NO 只在最终 Tmax 不落入该 exact bracket 时赢；"
        "entry 使用 archived executable NO ask + Weather taker fee。",
        "",
        "## 结论与动作",
        "",
        "**早期 bias 研究值得保留，但它的作用边界很清楚：显著改善天气预报"
        "准确率，也显著改善纯 forecast probability；仍未打赢同 rows market。"
        "因此 bias/accuracy 应作为共享概率特征，不是 region/source hard gate。**",
        "",
        "- 动作：把 frozen city×source bias、MAE、spread 保留在 feature/shadow "
        "payload；保持 research / frozen shadow，不改 live。",
        "- Europe 残差不能归因于 bias：移除 city-source bias 后 Europe ROI "
        f"反而从 {pct(eu_full_roi)} 变为 {pct(eu_debiased_roi)}。",
        "",
        "## Target 与预注册 A/B",
        "",
        "```text",
        "在固定 D1 distance=2 candidate rows 上，只改变历史 forecast error 的",
        "bias shift；先检验 holdout weather MAE，再检验 P(NO) proper score 与",
        "同 rows market，最后检验相同 executable quotes 的选边 ROI。",
        "```",
        "",
        "- `short_full`：当前五模型 forecast + 冻结 city×model 完整经验残差。",
        "- `short_debiased`：从同一残差逐城逐模型减去均值，保留 dispersion，"
        "只移除 directional bias。",
        "- `short_global_bias` / `short_shrink50`：用 global model bias，或城市/"
        "global 各 50%，属于预定义 shrinkage sensitivity。",
        "- `long_*`：复用早期 356–737 日 ECMWF/GFS city-source 误差；"
        "assigned source 与 equal-two-source 都在全分母测试。",
        "- K=8 frozen probability policies + 5 bias increment comparisons + "
        "5 expanding-OOF market-anchor challengers；分别 BH 校正。",
        "",
        "## 实验 1：bias 是否真的提高天气预报准确率",
        "",
        "| calibration | rows/dates/cities | raw MAE | corrected MAE | "
        "ΔMAE [95% CI] |",
        "|---|---:|---:|---:|---:|",
        f"| 当前五模型短窗 | {int(acc.loc['all_short5', 'rows'])}/"
        f"{int(acc.loc['all_short5', 'target_dates'])}/"
        f"{int(acc.loc['all_short5', 'cities'])} | "
        f"{acc.loc['all_short5', 'raw_mae_f']:.3f}F | "
        f"{acc.loc['all_short5', 'corrected_mae_f']:.3f}F | "
        f"{acc.loc['all_short5', 'mae_delta']:+.3f}F "
        f"[{acc.loc['all_short5', 'mae_delta_ci_low']:+.3f}, "
        f"{acc.loc['all_short5', 'mae_delta_ci_high']:+.3f}] |",
        f"| 早期长历史 ECMWF/GFS | {int(long_acc['rows'])}/"
        f"{int(long_acc['target_dates'])}/"
        f"{int(long_acc['cities'])} | {long_acc['raw_mae_f']:.3f}F | "
        f"{long_acc['corrected_mae_f']:.3f}F | "
        f"{long_acc['mae_delta']:+.3f}F "
        f"[{long_acc['mae_delta_ci_low']:+.3f}, "
        f"{long_acc['mae_delta_ci_high']:+.3f}] |",
        "",
        "按区域，当前短窗 bias correction 的 MAE 改善：",
        "",
        "| region | rows | raw → corrected MAE | ΔMAE [95% CI] |",
        "|---|---:|---:|---:|",
    ]
    for region in REGION_ORDER:
        row = acc.loc[region]
        lines.append(
            f"| {region} | {int(row['rows'])} | "
            f"{row['raw_mae_f']:.2f} → {row['corrected_mae_f']:.2f}F | "
            f"{row['mae_delta']:+.2f} "
            f"[{row['mae_delta_ci_low']:+.2f}, "
            f"{row['mae_delta_ci_high']:+.2f}] |"
        )
    lines.extend(
        [
            "",
            "这证明旧 bias 层不是废资产：方向性误差在新日期具有 persistence。"
            "但这是 weather accuracy gate，不等于 market alpha gate。",
            "",
            "## 实验 2：bias 对 P(NO) 的增量",
            "",
            "| policy | Brier | Δmarket [95% CI] | logloss | "
            "Δmarket [95% CI] |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for policy in probability_policies():
        row = prob.loc[policy]
        lines.append(
            f"| {policy} | {row['brier']:.5f} | "
            f"{row['brier_delta_vs_market']:+.5f} "
            f"[{row['brier_ci_low']:+.5f}, "
            f"{row['brier_ci_high']:+.5f}] | "
            f"{row['logloss']:.5f} | "
            f"{row['logloss_delta_vs_market']:+.5f} "
            f"[{row['logloss_ci_low']:+.5f}, "
            f"{row['logloss_ci_high']:+.5f}] |"
        )
    short_increment = inc.loc["short_full"]
    lines.extend(
        [
            "",
            "- `short_full` 相对 `short_debiased` 的 Brier 增量为 "
            f"{short_increment['brier_delta_corrected_minus_debiased']:+.5f}，"
            f"CI [{short_increment['brier_ci_low']:+.5f}, "
            f"{short_increment['brier_ci_high']:+.5f}]，说明 city-source bias "
            "本身具有概率信息。",
            "- 但 `short_full` 仍比 market Brier 差 "
            f"{prob.loc['short_full', 'brier_delta_vs_market']:+.5f}；"
            "历史 ECMWF/GFS assigned/average 也没有打赢 market。",
            "- 所以旧方法恢复的是 weather probability quality，不是已经确认的"
            "`P(NO)-market` residual。",
            "",
            "## 实验 3：相同盘口下的选边交易",
            "",
            "| policy | baskets/dates | ROI [95% CI] | "
            "ROI Δmarket-selection [95% CI] |",
            "|---|---:|---:|---:|",
        ]
    )
    for policy in probability_policies() + ["market"]:
        row = trade.loc[policy]
        lines.append(
            f"| {policy} | {int(row['baskets'])}/"
            f"{int(row['target_dates'])} | "
            f"{pct(row['fee_adjusted_roi'])} "
            f"[{pct(row['roi_ci_low'])}, "
            f"{pct(row['roi_ci_high'])}] | "
            f"{pct(row['roi_delta_vs_market'])} "
            f"[{pct(row['market_delta_ci_low'])}, "
            f"{pct(row['market_delta_ci_high'])}] |"
        )
    lines.extend(
        [
            "",
            f"- bias-aware all5 ROI {pct(trade.loc['short_full', 'fee_adjusted_roi'])}，"
            f"debiased 为 {pct(trade.loc['short_debiased', 'fee_adjusted_roi'])}；"
            f"paired Δ {pct(trade.loc['short_full', 'roi_delta_vs_short_debiased'])}，"
            f"CI [{pct(trade.loc['short_full', 'debiased_delta_ci_low'])}, "
            f"{pct(trade.loc['short_full', 'debiased_delta_ci_high'])}]；"
            "bias 改善点估但 CI 跨 0，也不是预先独立 frozen forward。",
            "- 早期长历史单一 assigned source 不如五模型经验分布；旧长历史适合"
            "做 prior/稳定器，不应退回单源策略。",
            "",
            "### Region A/B：Europe 是否由 bias 驱动",
            "",
            "| region | bias-aware ROI [CI] | debiased ROI [CI] | "
            "full−debiased Δ [CI] | agreement |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for region in REGION_ORDER:
        full = region_index.loc[(region, "short_full")]
        debiased = region_index.loc[(region, "short_debiased")]
        lines.append(
            f"| {region} | {pct(full['fee_adjusted_roi'])} "
            f"[{pct(full['roi_ci_low'])}, {pct(full['roi_ci_high'])}] | "
            f"{pct(debiased['fee_adjusted_roi'])} "
            f"[{pct(debiased['roi_ci_low'])}, "
            f"{pct(debiased['roi_ci_high'])}] | "
            f"{pct(full['full_minus_debiased_roi_delta'])} "
            f"[{pct(full['delta_ci_low'])}, "
            f"{pct(full['delta_ci_high'])}] | "
            f"{full['selection_agreement']:.1%} |"
        )
    lines.extend(
        [
            "",
            "Europe 在移除 bias 后没有消失，故其剩余 market residual 不是 city-source "
            "mean bias 造成。Asia/US 反而从 bias correction 得到更明显选边改善。",
            "",
            "## 实验 4：market-anchor expanding OOF",
            "",
            "- 每个 target date 只用更早日期拟合；前 5 日 warm-up，后 15 日"
            f"共 {oof_rows:,} candidate rows / {oof_baskets:,} baskets。"
            "固定 L2 logistic `C=0.1`，无阈值调参。",
            "",
            "| model | Brier Δmarket [95% CI] | logloss Δmarket [95% CI] | "
            "BH beats market? |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, row in oof_prob.iterrows():
        beats = (
            row["brier_delta_vs_market"] < 0
            and row["brier_q_bh"] < 0.05
        )
        lines.append(
            f"| {name} | {row['brier_delta_vs_market']:+.5f} "
            f"[{row['brier_ci_low']:+.5f}, "
            f"{row['brier_ci_high']:+.5f}] | "
            f"{row['logloss_delta_vs_market']:+.5f} "
            f"[{row['logloss_ci_low']:+.5f}, "
            f"{row['logloss_ci_high']:+.5f}] | "
            f"{'YES' if beats else 'NO'} |"
        )
    lines.extend(
        [
            "",
            "| OOF selector | baskets/dates | ROI [95% CI] | "
            "Δmarket selector [95% CI] |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, row in oof_trade.iterrows():
        lines.append(
            f"| {name} | {int(row['baskets'])}/"
            f"{int(row['target_dates'])} | "
            f"{pct(row['fee_adjusted_roi'])} "
            f"[{pct(row['roi_ci_low'])}, "
            f"{pct(row['roi_ci_high'])}] | "
            f"{pct(row['roi_delta_vs_market'])} "
            f"[{pct(row['delta_ci_low'])}, "
            f"{pct(row['delta_ci_high'])}] |"
        )
    lines.extend(
        [
            "",
            "market-anchor 的作用是检验 bias 是否在市场价格之外还有增量；"
            "若 proper score 不过，任何 selected ROI 都只能作探索性诊断。",
            "",
            "## Signal / evidence funnel、完整性与三门",
            "",
            "- signal：2,380 fixed candidates → 1,190 paired baskets → "
            f"每 policy 1,190 selections；OOF {oof_rows:,} candidates → "
            f"{oof_baskets:,} baskets。",
            "- evidence：frozen forecast residual + PIT forecast + PIT quote + "
            "settlement 全覆盖；actual fill=0。",
            "- 8 环已覆盖：描述、统计、判别、概率、executable fee、"
            "target-date correlation、同分母 market baseline；缺真实 fill/queue、"
            "capacity、外部 frozen forward。",
            "- significance=bias weather/probability increment PASS；"
            "baseline=FAIL；forward=NA；conclusion=`inconclusive_reusable_feature`；"
            "动作=复用 bias/accuracy 特征，保持 research/frozen shadow，不改 live。",
            "",
            "## Bloodline placement",
            "",
            "- 共享实现继续使用 `weather_feature_layer.bias` 的 as-of city×source "
            "bias/MAE/tails；不新增平行事实表。",
            "- 新 forecast snapshot 应保存 issue/run/first-seen；本报告的历史误差层"
            "只负责 prior calibration，不冒充 decision-time forecast version。",
            "- 后续 `fact_signal_candidates` opportunity payload 记录 frozen bias、"
            "MAE、spread、bias-corrected probability 与 market residual；"
            "region 只作审计切片，不作 eligibility。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_csv(
        args.input_dir / "candidate_source_probs.csv"
    )
    if (
        len(candidates) != 2380
        or candidates["snapshot_key"].nunique() != 1190
        or candidates["target_date"].nunique() != 20
    ):
        raise RuntimeError("fixed denominator drift")
    eligible_cities = set(candidates["city"].astype(str))
    errors = pd.read_csv(args.errors)
    long_errors = pd.read_csv(args.long_errors)
    short_residuals, short_stats = load_short_residuals(
        errors, eligible_cities=eligible_cities
    )
    long_residuals, long_stats = load_long_residuals(
        long_errors, eligible_cities=eligible_cities
    )
    expected_short = len(eligible_cities) * len(MODELS)
    expected_long = len(eligible_cities) * 2
    if len(short_residuals) != expected_short:
        raise RuntimeError(
            f"short residual coverage {len(short_residuals)}/"
            f"{expected_short}"
        )
    if len(long_residuals) != expected_long:
        raise RuntimeError(
            f"long residual coverage {len(long_residuals)}/"
            f"{expected_long}"
        )
    scored = attach_bias_probabilities(
        candidates,
        short_residuals,
        short_stats,
        long_residuals,
        long_stats,
    )
    probability = probability_summary(scored, draws=args.draws)
    increments = bias_increment_summary(scored, draws=args.draws)
    trades, selected = trade_summary(scored, draws=args.draws)
    regions = region_bias_trade_summary(selected, draws=args.draws)
    accuracy, long_accuracy = accuracy_summary(
        errors,
        short_stats,
        long_stats,
        eligible_cities=eligible_cities,
        draws=args.draws,
    )
    oof_rows = expanding_oof(scored)
    oof_probability, oof_trades = oof_summary(
        oof_rows, draws=args.draws
    )

    outputs = {
        "candidate_bias_probabilities.csv": scored,
        "short_bias_stats.csv": short_stats,
        "long_bias_stats.csv": long_stats,
        "weather_accuracy_summary.csv": accuracy,
        "long_weather_accuracy_summary.csv": long_accuracy,
        "probability_summary.csv": probability,
        "bias_increment_summary.csv": increments,
        "trade_summary.csv": trades,
        "region_bias_trade_summary.csv": regions,
        "oof_candidate_probabilities.csv": oof_rows,
        "oof_probability_summary.csv": oof_probability,
        "oof_trade_summary.csv": oof_trades,
    }
    for filename, frame in outputs.items():
        frame.to_csv(args.output_dir / filename, index=False)
    payload = {
        "fixed_denominator": {
            "candidate_rows": len(scored),
            "baskets": int(scored["snapshot_key"].nunique()),
            "target_dates": int(scored["target_date"].nunique()),
            "cities": int(scored["city"].nunique()),
            "actual_fills": 0,
        },
        "accuracy": accuracy.to_dict("records"),
        "long_accuracy": long_accuracy.to_dict("records"),
        "probability": probability.to_dict("records"),
        "bias_increment": increments.to_dict("records"),
        "trades": trades.to_dict("records"),
        "regions": regions.to_dict("records"),
        "oof_probability": oof_probability.to_dict("records"),
        "oof_trades": oof_trades.to_dict("records"),
        "verdict": {
            "bias_improves_weather_accuracy": True,
            "bias_improves_forecast_probability": True,
            "forecast_probability_beats_market": False,
            "bias_explains_europe_residual": False,
            "live_ready": False,
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        accuracy=accuracy,
        long_accuracy=long_accuracy,
        probability=probability,
        increments=increments,
        trades=trades,
        regions=regions,
        oof_probability=oof_probability,
        oof_trades=oof_trades,
        oof_rows=len(oof_rows),
        oof_baskets=int(oof_rows["snapshot_key"].nunique()),
        short_stats=short_stats,
        long_stats=long_stats,
    )


if __name__ == "__main__":
    main()
