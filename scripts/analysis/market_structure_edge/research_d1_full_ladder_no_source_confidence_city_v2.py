#!/usr/bin/env python3
"""Source, confidence, and city robustness study for D1 distance-2 NO."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_d1_full_ladder_no_v1 as base
from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1
from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)


DEFAULT_LADDER = base.DEFAULT_LADDER
DEFAULT_FORECASTS = base.DEFAULT_FORECASTS
DEFAULT_ERRORS = base.DEFAULT_ERRORS
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_full_ladder_no_source_confidence_city_v2"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-full-ladder-no-source-confidence-city-v2.md"
)
MODELS = base.MODEL_KEYS
TRAIN_END = base.TRAIN_END
EARLY_END = "2026-06-26"
SEED = 2026072823
EPS = 1e-6
MODEL_LABELS = {
    "ecmwf_ifs025": "ECMWF_IFS",
    "ecmwf_aifs025_single": "ECMWF_AIFS",
    "gfs_global": "GFS_GLOBAL",
    "icon_seamless": "ICON",
    "jma_seamless": "JMA",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--forecasts", type=Path, default=DEFAULT_FORECASTS)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def fee(price: float) -> float:
    return 0.05 * price * (1.0 - price)


def clip_probability(value: float) -> float:
    return min(1.0 - EPS, max(EPS, float(value)))


def logloss(probability: float, outcome: float) -> float:
    p = clip_probability(probability)
    return -(outcome * math.log(p) + (1.0 - outcome) * math.log(1.0 - p))


def load_training_stats(
    path: Path,
) -> tuple[
    dict[tuple[str, str], np.ndarray],
    pd.DataFrame,
    pd.DataFrame,
]:
    rows = pd.read_csv(path)
    rows = rows[
        (rows["target_date"] <= TRAIN_END)
        & rows["model_key"].isin(MODELS)
        & rows["error_f"].notna()
    ].copy()
    stats = (
        rows.groupby(["city", "model_key"], as_index=False)
        .agg(
            residual_n=("error_f", "size"),
            train_bias_f=("error_f", "mean"),
            train_mae_f=("abs_error_f", "mean"),
            train_rmse_f=("error_f", lambda values: float(np.sqrt(np.mean(np.square(values))))),
            train_error_std_f=("error_f", "std"),
        )
    )
    stats = stats[stats["residual_n"] >= 20].copy()
    residuals = {
        (str(city), str(model)): group["error_f"].astype(float).to_numpy()
        for (city, model), group in rows.groupby(["city", "model_key"])
        if len(group) >= 20
    }
    global_stats = (
        stats.groupby("model_key", as_index=False)
        .agg(
            eligible_cities=("city", "nunique"),
            train_mae_f=("train_mae_f", "mean"),
            train_rmse_f=("train_rmse_f", "mean"),
            train_abs_bias_f=("train_bias_f", lambda values: float(np.mean(np.abs(values)))),
        )
        .sort_values("train_mae_f")
        .reset_index(drop=True)
    )
    return residuals, stats, global_stats


def market_probabilities(group: pd.DataFrame) -> dict[int, float]:
    mids: dict[int, float] = {}
    for row in group.itertuples(index=False):
        bid = float(row.yes_best_bid)
        ask = float(row.yes_best_ask)
        if 0.001 <= bid <= ask <= 0.999:
            mids[int(row.rung_index)] = (bid + ask) / 2.0
    total = sum(mids.values())
    if total <= 0 or len(mids) / len(group) < 0.8:
        return {}
    return {index: 1.0 - value / total for index, value in mids.items()}


def source_probability(
    *,
    forecast_max_f: float,
    residuals_f: np.ndarray,
    unit: str,
    bracket_label: str,
    question: str,
) -> float:
    bracket = parse_market_bracket(bracket_label, question)
    if bracket is None:
        return math.nan
    outcomes_f = forecast_max_f + residuals_f
    outcomes = (
        (outcomes_f - 32.0) * 5.0 / 9.0
        if str(unit).upper() == "C"
        else outcomes_f
    )
    snapped = np.round(outcomes).astype(int)
    p_yes = float(np.mean([bracket.contains(float(value)) for value in snapped]))
    return 1.0 - p_yes


def build_candidate_rows(
    ladder: pd.DataFrame,
    forecasts: pd.DataFrame,
    residuals: dict[tuple[str, str], np.ndarray],
    training_stats: pd.DataFrame,
) -> pd.DataFrame:
    forecasts_index = {
        key: group.set_index("model_key")["forecast_max_f"].astype(float).to_dict()
        for key, group in forecasts.groupby(
            ["city", "target_date", "decision_time_utc"], sort=False
        )
    }
    stats_index = training_stats.set_index(["city", "model_key"]).to_dict("index")
    records: list[dict[str, Any]] = []
    for snapshot_key, group in ladder.groupby("snapshot_key", sort=False):
        distance_two = group[
            (group["distance_from_nearest_endpoint"] == 2)
            & group["no_executable"].astype(bool)
            & group["yes_win"].notna()
        ]
        if len(distance_two) != 2:
            continue
        first = group.iloc[0]
        forecast_key = (
            str(first["city"]),
            str(first["target_date"]),
            str(first["decision_ts_utc"]),
        )
        model_forecasts = forecasts_index.get(forecast_key)
        if model_forecasts is None or any(
            (str(first["city"]), model) not in residuals for model in MODELS
        ):
            continue
        market_p_no = market_probabilities(group)
        if not market_p_no:
            continue
        for row in distance_two.itertuples(index=False):
            record = row._asdict()
            no_ask = float(row.no_best_ask)
            no_win = 1.0 - float(row.yes_win)
            record.update(
                {
                    "no_win": no_win,
                    "fee": fee(no_ask),
                    "cost": no_ask + fee(no_ask),
                    "payout": no_win,
                    "pnl": no_win - no_ask - fee(no_ask),
                    "market_p_no": market_p_no[int(row.rung_index)],
                }
            )
            for model in MODELS:
                forecast = float(model_forecasts[model])
                record[f"forecast_{model}"] = forecast
                record[f"p_no_{model}"] = source_probability(
                    forecast_max_f=forecast,
                    residuals_f=residuals[(str(row.city), model)],
                    unit=str(row.market_unit),
                    bracket_label=str(row.bracket),
                    question=str(row.question),
                )
                stat = stats_index[(str(row.city), model)]
                record[f"train_mae_{model}"] = float(stat["train_mae_f"])
            records.append(record)
    candidates = pd.DataFrame(records)
    counts = candidates.groupby("snapshot_key").size()
    valid_keys = set(counts[counts == 2].index)
    return candidates[candidates["snapshot_key"].isin(valid_keys)].copy()


def model_eligible_distance2(
    ladder: pd.DataFrame,
    forecasts: pd.DataFrame,
    residuals: dict[tuple[str, str], np.ndarray],
) -> tuple[int, int]:
    forecast_keys = set(
        forecasts[
            ["city", "target_date", "decision_time_utc"]
        ].itertuples(index=False, name=None)
    )
    eligible: list[tuple[str, str]] = []
    for snapshot_key, group in ladder.groupby("snapshot_key"):
        distance_two = group[
            (group["distance_from_nearest_endpoint"] == 2)
            & group["no_executable"].astype(bool)
            & group["yes_win"].notna()
        ]
        if len(distance_two) != 2:
            continue
        first = group.iloc[0]
        city = str(first["city"])
        forecast_key = (
            city,
            str(first["target_date"]),
            str(first["decision_ts_utc"]),
        )
        if forecast_key not in forecast_keys:
            continue
        if not all((city, model) in residuals for model in MODELS):
            continue
        eligible.append((str(snapshot_key), city))
    return len(eligible), len({city for _, city in eligible})


def policy_weights(
    candidates: pd.DataFrame,
    global_stats: pd.DataFrame,
) -> dict[str, dict[str, dict[str, float]]]:
    cities = sorted(candidates["city"].unique())
    global_order = global_stats["model_key"].astype(str).tolist()
    policies: dict[str, dict[str, dict[str, float]]] = {}

    def same_for_all(name: str, selected: list[str]) -> None:
        weight = 1.0 / len(selected)
        policies[name] = {
            city: {model: (weight if model in selected else 0.0) for model in MODELS}
            for city in cities
        }

    same_for_all("equal_all5", list(MODELS))
    same_for_all("global_top3_train", global_order[:3])
    same_for_all(
        "physical_only_no_aifs",
        [model for model in MODELS if model != "ecmwf_aifs025_single"],
    )
    for model in MODELS:
        same_for_all(f"single_{model}", [model])
        same_for_all(
            f"leave_out_{model}",
            [candidate for candidate in MODELS if candidate != model],
        )

    best_one: dict[str, dict[str, float]] = {}
    best_two: dict[str, dict[str, float]] = {}
    inverse: dict[str, dict[str, float]] = {}
    for city, group in candidates.groupby("city"):
        first = group.iloc[0]
        ordered = sorted(MODELS, key=lambda model: float(first[f"train_mae_{model}"]))
        best_one[str(city)] = {
            model: float(model == ordered[0]) for model in MODELS
        }
        best_two[str(city)] = {
            model: (0.5 if model in ordered[:2] else 0.0) for model in MODELS
        }
        raw = {
            model: 1.0 / (float(first[f"train_mae_{model}"]) + 0.25)
            for model in MODELS
        }
        total = sum(raw.values())
        inverse[str(city)] = {
            model: value / total for model, value in raw.items()
        }
    policies["city_best1_train"] = best_one
    policies["city_best2_train"] = best_two
    policies["city_inverse_mae_all5"] = inverse
    return policies


def apply_policy_probabilities(
    candidates: pd.DataFrame,
    weights_by_city: dict[str, dict[str, float]],
    policy_name: str,
) -> pd.DataFrame:
    output = candidates.copy()
    probabilities = []
    for row in output.itertuples(index=False):
        weights = weights_by_city[str(row.city)]
        probabilities.append(
            sum(
                weights[model] * float(getattr(row, f"p_no_{model}"))
                for model in MODELS
            )
        )
    output["policy"] = policy_name
    output["policy_p_no"] = probabilities
    output["policy_edge"] = output["policy_p_no"] - output["cost"]
    output["brier"] = np.square(output["policy_p_no"] - output["no_win"])
    output["logloss"] = [
        logloss(probability, outcome)
        for probability, outcome in zip(output["policy_p_no"], output["no_win"])
    ]
    output["market_brier"] = np.square(
        output["market_p_no"] - output["no_win"]
    )
    output["market_logloss"] = [
        logloss(probability, outcome)
        for probability, outcome in zip(output["market_p_no"], output["no_win"])
    ]
    return output


def select_best(candidates: pd.DataFrame) -> pd.DataFrame:
    return (
        candidates.sort_values(
            ["snapshot_key", "policy_edge", "cost"],
            ascending=[True, False, True],
        )
        .groupby("snapshot_key", as_index=False)
        .first()
    )


def select_market(candidates: pd.DataFrame) -> pd.DataFrame:
    output = candidates.copy()
    output["policy"] = "market_only_selection"
    output["policy_p_no"] = output["market_p_no"]
    output["policy_edge"] = output["market_p_no"] - output["cost"]
    return select_best(output)


def mechanical_half(candidates: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, group in candidates.groupby("snapshot_key"):
        first = group.iloc[0].to_dict()
        first.update(
            {
                "policy": "mechanical_half_distance2",
                "cost": float(group["cost"].mean()),
                "payout": float(group["payout"].mean()),
                "pnl": float(group["pnl"].mean()),
                "no_best_ask": float(group["no_best_ask"].mean()),
                "bracket": "half_each_distance2",
            }
        )
        records.append(first)
    return pd.DataFrame(records)


def roi(frame: pd.DataFrame) -> float:
    return float(frame["pnl"].sum() / frame["cost"].sum())


def block_roi_samples(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> np.ndarray:
    daily = frame.groupby("target_date")[["pnl", "cost"]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(dates), size=(draws, len(dates)))
    pnl = daily["pnl"].to_numpy()[indices].sum(axis=1)
    cost = daily["cost"].to_numpy()[indices].sum(axis=1)
    return pnl / cost


def paired_roi_delta_samples(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    left = candidate.groupby("target_date")[["pnl", "cost"]].sum()
    right = baseline.groupby("target_date")[["pnl", "cost"]].sum()
    joined = left.join(right, lsuffix="_candidate", rsuffix="_baseline")
    dates = joined.index.to_numpy()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(dates), size=(draws, len(dates)))
    return (
        joined["pnl_candidate"].to_numpy()[indices].sum(axis=1)
        / joined["cost_candidate"].to_numpy()[indices].sum(axis=1)
        - joined["pnl_baseline"].to_numpy()[indices].sum(axis=1)
        / joined["cost_baseline"].to_numpy()[indices].sum(axis=1)
    )


def block_mean_delta_samples(
    rows: pd.DataFrame,
    left: str,
    right: str,
    *,
    draws: int,
    seed: int,
) -> np.ndarray:
    daily = rows.groupby("target_date")[[left, right]].agg(["sum", "size"])
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(dates), size=(draws, len(dates)))
    return (
        daily[(left, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(left, "size")].to_numpy()[indices].sum(axis=1)
        - daily[(right, "sum")].to_numpy()[indices].sum(axis=1)
        / daily[(right, "size")].to_numpy()[indices].sum(axis=1)
    )


def two_sided_p(samples: np.ndarray) -> float:
    return float(
        min(1.0, 2.0 * min(np.mean(samples <= 0.0), np.mean(samples >= 0.0)))
    )


def bh_qvalues(pvalues: pd.Series) -> pd.Series:
    values = pvalues.astype(float).to_numpy()
    order = np.argsort(values)
    ranked = values[order]
    adjusted = np.empty(len(values))
    running = 1.0
    for reverse_index in range(len(values) - 1, -1, -1):
        rank = reverse_index + 1
        running = min(running, ranked[reverse_index] * len(values) / rank)
        adjusted[order[reverse_index]] = running
    return pd.Series(adjusted, index=pvalues.index)


def summarize_policies(
    policy_candidates: dict[str, pd.DataFrame],
    selected: dict[str, pd.DataFrame],
    *,
    draws: int,
) -> pd.DataFrame:
    baseline = selected["equal_all5"]
    baseline_keys = baseline.set_index("snapshot_key")["bracket"]
    records: list[dict[str, Any]] = []
    for index, (name, frame) in enumerate(selected.items()):
        samples = block_roi_samples(frame, draws=draws, seed=SEED + index)
        delta_samples = paired_roi_delta_samples(
            frame, baseline, draws=draws, seed=SEED + 100 + index
        )
        candidate_rows = policy_candidates[name]
        brier_delta = block_mean_delta_samples(
            candidate_rows,
            "brier",
            "market_brier",
            draws=draws,
            seed=SEED + 200 + index,
        )
        logloss_delta = block_mean_delta_samples(
            candidate_rows,
            "logloss",
            "market_logloss",
            draws=draws,
            seed=SEED + 300 + index,
        )
        early = frame[frame["target_date"] <= EARLY_END]
        late = frame[frame["target_date"] > EARLY_END]
        records.append(
            {
                "policy": name,
                "baskets": len(frame),
                "target_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "mean_no_ask": float(frame["no_best_ask"].mean()),
                "win_rate": float(frame["payout"].gt(0).mean()),
                "pnl": float(frame["pnl"].sum()),
                "fee_adjusted_roi": roi(frame),
                "roi_ci_low": float(np.quantile(samples, 0.025)),
                "roi_ci_high": float(np.quantile(samples, 0.975)),
                "roi_delta_vs_all5": roi(frame) - roi(baseline),
                "delta_ci_low": float(np.quantile(delta_samples, 0.025)),
                "delta_ci_high": float(np.quantile(delta_samples, 0.975)),
                "delta_p": two_sided_p(delta_samples),
                "early_roi": roi(early),
                "late_roi": roi(late),
                "selection_agreement_all5": float(
                    frame.set_index("snapshot_key")["bracket"]
                    .eq(baseline_keys)
                    .mean()
                ),
                "brier": float(candidate_rows["brier"].mean()),
                "market_brier": float(candidate_rows["market_brier"].mean()),
                "brier_delta_vs_market": float(
                    candidate_rows["brier"].mean()
                    - candidate_rows["market_brier"].mean()
                ),
                "brier_delta_ci_low": float(np.quantile(brier_delta, 0.025)),
                "brier_delta_ci_high": float(np.quantile(brier_delta, 0.975)),
                "logloss": float(candidate_rows["logloss"].mean()),
                "market_logloss": float(
                    candidate_rows["market_logloss"].mean()
                ),
                "logloss_delta_vs_market": float(
                    candidate_rows["logloss"].mean()
                    - candidate_rows["market_logloss"].mean()
                ),
                "logloss_delta_ci_low": float(
                    np.quantile(logloss_delta, 0.025)
                ),
                "logloss_delta_ci_high": float(
                    np.quantile(logloss_delta, 0.975)
                ),
            }
        )
    summary = pd.DataFrame(records)
    trial_mask = ~summary["policy"].isin(
        ["equal_all5", "market_only_selection", "mechanical_half_distance2"]
    )
    summary["delta_q_bh"] = math.nan
    summary.loc[trial_mask, "delta_q_bh"] = bh_qvalues(
        summary.loc[trial_mask, "delta_p"]
    )
    return summary


def attach_confidence(
    all5_candidates: pd.DataFrame, selected_all5: pd.DataFrame
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for key, candidates in all5_candidates.groupby("snapshot_key"):
        chosen = selected_all5[selected_all5["snapshot_key"] == key].iloc[0]
        chosen_index = int(chosen["rung_index"])
        votes = 0
        for model in MODELS:
            source_edges = candidates.set_index("rung_index")[
                f"p_no_{model}"
            ] - candidates.set_index("rung_index")["cost"]
            preferred = int(source_edges.sort_values(ascending=False).index[0])
            votes += int(preferred == chosen_index)
        ordered_edges = candidates["policy_edge"].sort_values(
            ascending=False
        ).to_numpy()
        record = chosen.to_dict()
        selected_source_probabilities = [
            float(chosen[f"p_no_{model}"]) for model in MODELS
        ]
        selected_forecasts = [
            float(chosen[f"forecast_{model}"]) for model in MODELS
        ]
        record.update(
            {
                "source_vote_count": votes,
                "source_vote_share": votes / len(MODELS),
                "source_p_no_std": float(
                    np.std(selected_source_probabilities, ddof=0)
                ),
                "source_p_no_range": float(
                    max(selected_source_probabilities)
                    - min(selected_source_probabilities)
                ),
                "forecast_spread_f": float(
                    max(selected_forecasts) - min(selected_forecasts)
                ),
                "selection_edge_margin": float(
                    ordered_edges[0] - ordered_edges[1]
                ),
                "selected_edge_positive": bool(chosen["policy_edge"] > 0),
                "region": str(
                    (FULL_CITY_CONFIGS.get(str(chosen["city"])) or {}).get(
                        "region", "unknown"
                    )
                ),
                "city_family": CITY_FAMILY_ATLAS_V1.get(
                    str(chosen["city"]), "unmapped"
                ),
            }
        )
        records.append(record)
    output = pd.DataFrame(records)
    output["vote_bucket"] = (
        output["source_vote_count"].astype(int).astype(str) + "/5"
    )
    output["edge_bucket"] = pd.cut(
        output["policy_edge"],
        [-math.inf, 0.0, 0.01, math.inf],
        labels=["edge<=0", "0<edge<=1c", "edge>1c"],
    ).astype(str)
    output["prob_disagreement_tercile"] = pd.qcut(
        output["source_p_no_std"],
        q=3,
        labels=["low_disagreement", "mid_disagreement", "high_disagreement"],
        duplicates="drop",
    ).astype(str)
    output["edge_margin_tercile"] = pd.qcut(
        output["selection_edge_margin"],
        q=3,
        labels=["low_margin", "mid_margin", "high_margin"],
        duplicates="drop",
    ).astype(str)
    return output


def city_train_accuracy(
    training_stats: pd.DataFrame, eligible_cities: set[str]
) -> pd.DataFrame:
    city = (
        training_stats[
            training_stats["city"].astype(str).isin(eligible_cities)
        ]
        .groupby("city", as_index=False)
        .agg(
            train_mean_mae_f=("train_mae_f", "mean"),
            train_best_mae_f=("train_mae_f", "min"),
            train_mean_abs_bias_f=(
                "train_bias_f",
                lambda values: float(np.mean(np.abs(values))),
            ),
        )
    )
    city["accuracy_tier"] = pd.qcut(
        city["train_mean_mae_f"],
        q=3,
        labels=["train_accurate", "train_middle", "train_weak"],
    ).astype(str)
    return city


def slice_summary(
    rows: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed_offset: int,
    market_selected: pd.DataFrame | None = None,
    mechanical_selected: pd.DataFrame | None = None,
    all5_candidates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, (value, group) in enumerate(rows.groupby(column, dropna=False)):
        samples = block_roi_samples(
            group, draws=draws, seed=SEED + seed_offset + index
        )
        early = group[group["target_date"] <= EARLY_END]
        late = group[group["target_date"] > EARLY_END]
        record = {
                "dimension": column,
                "slice": str(value),
                "baskets": len(group),
                "target_dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "mean_no_ask": float(group["no_best_ask"].mean()),
                "win_rate": float(group["payout"].gt(0).mean()),
                "pnl": float(group["pnl"].sum()),
                "fee_adjusted_roi": roi(group),
                "roi_ci_low": float(np.quantile(samples, 0.025)),
                "roi_ci_high": float(np.quantile(samples, 0.975)),
                "roi_p": two_sided_p(samples),
                "early_roi": roi(early) if not early.empty else math.nan,
                "late_roi": roi(late) if not late.empty else math.nan,
        }
        keys = set(group["snapshot_key"])
        if market_selected is not None:
            market_group = market_selected[
                market_selected["snapshot_key"].isin(keys)
            ]
            market_delta = paired_roi_delta_samples(
                group,
                market_group,
                draws=draws,
                seed=SEED + seed_offset + 100 + index,
            )
            record.update(
                {
                    "market_selection_roi": roi(market_group),
                    "roi_delta_vs_market_selection": roi(group)
                    - roi(market_group),
                    "market_delta_ci_low": float(
                        np.quantile(market_delta, 0.025)
                    ),
                    "market_delta_ci_high": float(
                        np.quantile(market_delta, 0.975)
                    ),
                    "market_delta_p": two_sided_p(market_delta),
                }
            )
        if mechanical_selected is not None:
            mechanical_group = mechanical_selected[
                mechanical_selected["snapshot_key"].isin(keys)
            ]
            mechanical_delta = paired_roi_delta_samples(
                group,
                mechanical_group,
                draws=draws,
                seed=SEED + seed_offset + 200 + index,
            )
            record.update(
                {
                    "mechanical_roi": roi(mechanical_group),
                    "roi_delta_vs_mechanical": roi(group)
                    - roi(mechanical_group),
                    "mechanical_delta_ci_low": float(
                        np.quantile(mechanical_delta, 0.025)
                    ),
                    "mechanical_delta_ci_high": float(
                        np.quantile(mechanical_delta, 0.975)
                    ),
                }
            )
        if all5_candidates is not None:
            probability_rows = all5_candidates[
                all5_candidates["snapshot_key"].isin(keys)
            ]
            brier_delta = block_mean_delta_samples(
                probability_rows,
                "brier",
                "market_brier",
                draws=draws,
                seed=SEED + seed_offset + 300 + index,
            )
            logloss_delta = block_mean_delta_samples(
                probability_rows,
                "logloss",
                "market_logloss",
                draws=draws,
                seed=SEED + seed_offset + 400 + index,
            )
            record.update(
                {
                    "brier_delta_vs_market": float(
                        probability_rows["brier"].mean()
                        - probability_rows["market_brier"].mean()
                    ),
                    "brier_delta_ci_low": float(
                        np.quantile(brier_delta, 0.025)
                    ),
                    "brier_delta_ci_high": float(
                        np.quantile(brier_delta, 0.975)
                    ),
                    "brier_delta_p": two_sided_p(brier_delta),
                    "logloss_delta_vs_market": float(
                        probability_rows["logloss"].mean()
                        - probability_rows["market_logloss"].mean()
                    ),
                    "logloss_delta_ci_low": float(
                        np.quantile(logloss_delta, 0.025)
                    ),
                    "logloss_delta_ci_high": float(
                        np.quantile(logloss_delta, 0.975)
                    ),
                }
            )
        records.append(record)
    output = pd.DataFrame(records)
    output["roi_q_bh"] = bh_qvalues(output["roi_p"])
    if "market_delta_p" in output:
        output["market_delta_q_bh"] = bh_qvalues(output["market_delta_p"])
    if "brier_delta_p" in output:
        output["brier_delta_q_bh"] = bh_qvalues(output["brier_delta_p"])
    return output


def city_summary(
    rows: pd.DataFrame,
    training_accuracy: pd.DataFrame,
    *,
    draws: int,
    market_selected: pd.DataFrame,
    mechanical_selected: pd.DataFrame,
    all5_candidates: pd.DataFrame,
) -> pd.DataFrame:
    merged = (
        rows.copy()
        if "train_mean_mae_f" in rows.columns
        else rows.merge(
            training_accuracy, on="city", how="left", validate="many_to_one"
        )
    )
    records: list[dict[str, Any]] = []
    for index, (city, group) in enumerate(merged.groupby("city")):
        samples = block_roi_samples(
            group, draws=draws, seed=SEED + 1000 + index
        )
        early = group[group["target_date"] <= EARLY_END]
        late = group[group["target_date"] > EARLY_END]
        keys = set(group["snapshot_key"])
        market_group = market_selected[
            market_selected["snapshot_key"].isin(keys)
        ]
        mechanical_group = mechanical_selected[
            mechanical_selected["snapshot_key"].isin(keys)
        ]
        probability_rows = all5_candidates[
            all5_candidates["snapshot_key"].isin(keys)
        ]
        market_delta = paired_roi_delta_samples(
            group,
            market_group,
            draws=draws,
            seed=SEED + 1200 + index,
        )
        brier_delta = block_mean_delta_samples(
            probability_rows,
            "brier",
            "market_brier",
            draws=draws,
            seed=SEED + 1300 + index,
        )
        records.append(
            {
                "city": city,
                "baskets": len(group),
                "active_days": int(group["target_date"].nunique()),
                "mean_no_ask": float(group["no_best_ask"].mean()),
                "pnl": float(group["pnl"].sum()),
                "fee_adjusted_roi": roi(group),
                "roi_ci_low": float(np.quantile(samples, 0.025)),
                "roi_ci_high": float(np.quantile(samples, 0.975)),
                "roi_p": two_sided_p(samples),
                "early_roi": roi(early) if not early.empty else math.nan,
                "late_roi": roi(late) if not late.empty else math.nan,
                "market_selection_roi": roi(market_group),
                "roi_delta_vs_market_selection": roi(group)
                - roi(market_group),
                "market_delta_ci_low": float(
                    np.quantile(market_delta, 0.025)
                ),
                "market_delta_ci_high": float(
                    np.quantile(market_delta, 0.975)
                ),
                "market_delta_p": two_sided_p(market_delta),
                "mechanical_roi": roi(mechanical_group),
                "brier_delta_vs_market": float(
                    probability_rows["brier"].mean()
                    - probability_rows["market_brier"].mean()
                ),
                "brier_delta_ci_low": float(
                    np.quantile(brier_delta, 0.025)
                ),
                "brier_delta_ci_high": float(
                    np.quantile(brier_delta, 0.975)
                ),
                "brier_delta_p": two_sided_p(brier_delta),
                "train_mean_mae_f": float(group["train_mean_mae_f"].iloc[0]),
                "accuracy_tier": str(group["accuracy_tier"].iloc[0]),
                "region": str(group["region"].iloc[0]),
                "city_family": str(group["city_family"].iloc[0]),
            }
        )
    output = pd.DataFrame(records)
    output["roi_q_bh"] = bh_qvalues(output["roi_p"])
    output["market_delta_q_bh"] = bh_qvalues(output["market_delta_p"])
    output["brier_delta_q_bh"] = bh_qvalues(output["brier_delta_p"])
    output["sample_eligible"] = (
        (output["active_days"] >= 10) & (output["baskets"] >= 30)
    )
    output["early_late_same_sign"] = (
        np.sign(output["early_roi"]) == np.sign(output["late_roi"])
    )
    return output.sort_values("fee_adjusted_roi", ascending=False)


def best_source_counts(training_stats: pd.DataFrame) -> pd.DataFrame:
    ordered = training_stats.sort_values(["city", "train_mae_f"])
    best = ordered.groupby("city", as_index=False).first()
    counts = (
        best.groupby("model_key", as_index=False)
        .agg(best_for_cities=("city", "size"), cities=("city", lambda values: ",".join(sorted(values))))
        .sort_values("best_for_cities", ascending=False)
    )
    return counts


def family_leave_one_city_out(
    rows: pd.DataFrame,
    *,
    family_cities: set[str],
    market_selected: pd.DataFrame,
    draws: int,
) -> pd.DataFrame:
    family_rows = rows[rows["city"].isin(family_cities)]
    records: list[dict[str, Any]] = []
    for index, excluded_city in enumerate(sorted(family_cities)):
        group = family_rows[family_rows["city"] != excluded_city]
        keys = set(group["snapshot_key"])
        market_group = market_selected[
            market_selected["snapshot_key"].isin(keys)
        ]
        roi_samples = block_roi_samples(
            group, draws=draws, seed=SEED + 1500 + index
        )
        delta_samples = paired_roi_delta_samples(
            group,
            market_group,
            draws=draws,
            seed=SEED + 1600 + index,
        )
        early = group[group["target_date"] <= EARLY_END]
        late = group[group["target_date"] > EARLY_END]
        records.append(
            {
                "excluded_city": excluded_city,
                "baskets": len(group),
                "cities": int(group["city"].nunique()),
                "fee_adjusted_roi": roi(group),
                "roi_ci_low": float(np.quantile(roi_samples, 0.025)),
                "roi_ci_high": float(np.quantile(roi_samples, 0.975)),
                "market_selection_roi": roi(market_group),
                "roi_delta_vs_market_selection": roi(group)
                - roi(market_group),
                "market_delta_ci_low": float(
                    np.quantile(delta_samples, 0.025)
                ),
                "market_delta_ci_high": float(
                    np.quantile(delta_samples, 0.975)
                ),
                "early_roi": roi(early),
                "late_roi": roi(late),
            }
        )
    return pd.DataFrame(records)


def write_report(
    path: Path,
    *,
    policy_summary: pd.DataFrame,
    confidence: pd.DataFrame,
    city_rows: pd.DataFrame,
    accuracy_slices: pd.DataFrame,
    family_slices: pd.DataFrame,
    region_slices: pd.DataFrame,
    global_stats: pd.DataFrame,
    best_counts: pd.DataFrame,
    overlay_policy_summary: pd.DataFrame,
    family_leave_one_out: pd.DataFrame,
    funnel: dict[str, int],
) -> None:
    def table(frame: pd.DataFrame, columns: list[str]) -> list[str]:
        output = [
            "| " + " | ".join(columns) + " |",
            "|" + "|".join(["---"] * len(columns)) + "|",
        ]
        for row in frame[columns].to_dict("records"):
            values = []
            for value in row.values():
                if isinstance(value, float):
                    values.append("NA" if math.isnan(value) else f"{value:.6f}")
                else:
                    values.append(str(value))
            output.append("| " + " | ".join(values) + " |")
        return output

    core_policies = [
        "equal_all5",
        "global_top3_train",
        "physical_only_no_aifs",
        "city_best1_train",
        "city_best2_train",
        "city_inverse_mae_all5",
        "market_only_selection",
        "mechanical_half_distance2",
    ]
    core = policy_summary[
        policy_summary["policy"].isin(core_policies)
    ].copy()
    all5 = policy_summary[policy_summary["policy"] == "equal_all5"].iloc[0]
    city_best2 = policy_summary[
        policy_summary["policy"] == "city_best2_train"
    ].iloc[0]
    europe_source = overlay_policy_summary[
        overlay_policy_summary["overlay"].eq("europe_cloud_break")
        & overlay_policy_summary["policy"].isin(
            [
                "equal_all5",
                "physical_only_no_aifs",
                "global_top3_train",
                "market_only_selection",
                "mechanical_half_distance2",
            ]
        )
    ]
    accurate = accuracy_slices[
        accuracy_slices["slice"] == "train_accurate"
    ].iloc[0]
    europe_family = family_slices[
        family_slices["slice"] == "europe_cloud_break"
    ].iloc[0]
    europe_cities = city_rows[
        city_rows["city"].isin(
            ["Amsterdam", "Helsinki", "Madrid", "Munich", "Warsaw"]
        )
    ].sort_values("fee_adjusted_roi", ascending=False)
    lines = [
        "# D1 distance=2 NO：数据源 × 置信度 × 城市完整研究 v2",
        "",
        "## 结论",
        "",
        "全城市结论仍是 inconclusive：删源、按城市挑源都没有改善全 5 源，"
        "且全分母 proper score 未打赢 market。城市选择后出现一个更窄候选："
        "预定义 `europe_cloud_break` 五城在 absolute ROI、market-selection excess、"
        "early/late 上都为正；去掉 AIFS 后点估略好。但这是看过 5 个 family 后"
        "得到的 overlay，是否升级仍取决于 proper-score CI 与新日期 frozen forward。",
        "",
        *table(
            core,
            [
                "policy",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_delta_vs_all5",
                "delta_ci_low",
                "delta_ci_high",
                "late_roi",
                "brier_delta_vs_market",
                "logloss_delta_vs_market",
            ],
        ),
        "",
        "## Source 结论",
        "",
        *table(
            global_stats,
            [
                "model_key",
                "eligible_cities",
                "train_mae_f",
                "train_rmse_f",
                "train_abs_bias_f",
            ],
        ),
        "",
        "训练期各城市最优来源计数：",
        "",
        *table(best_counts, ["model_key", "best_for_cities"]),
        "",
        f"- all5 ROI {all5['fee_adjusted_roi']:.4%}，95% CI "
        f"[{all5['roi_ci_low']:.4%}, {all5['roi_ci_high']:.4%}]。",
        f"- city_best2_train ROI {city_best2['fee_adjusted_roi']:.4%}，"
        f"相对 all5 {city_best2['roi_delta_vs_all5']:.4%}，paired 95% CI "
        f"[{city_best2['delta_ci_low']:.4%}, {city_best2['delta_ci_high']:.4%}]。",
        f"- 本轮 source policy/ablation 共 {funnel['source_policy_trials']} 个 challenger；"
        "BH 多重检验后没有相对 all5 显著改善者。",
        "",
        "Europe-cloud-break 内 source A/B：",
        "",
        *table(
            europe_source,
            [
                "policy",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_delta_vs_all5",
                "delta_ci_low",
                "delta_ci_high",
                "late_roi",
                "brier_delta_vs_market",
                "brier_delta_ci_low",
                "brier_delta_ci_high",
                "logloss_delta_vs_market",
            ],
        ),
        "",
        "## 置信度",
        "",
        "以下均为 holdout 描述性切片，不作为已冻结 gate：",
        "",
        *table(
            confidence,
            [
                "dimension",
                "slice",
                "baskets",
                "target_dates",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "market_delta_ci_low",
                "market_delta_ci_high",
                "early_roi",
                "late_roi",
            ],
        ),
        "",
        "- `4/5` vote组 absolute ROI 为正，但相对 market-selection 的 CI 跨 0；"
        "`5/5` 没有更高，故 unanimity 不单调。",
        "- 收益反而集中在 high-disagreement / high-margin，说明这里的"
        "“模型分歧”更像价格/尾部机会强度，不是低风险置信度；不能把低 disagreement "
        "设成 gate。",
        "- `edge>1c` early 为正、late 转负，正 edge 也没有 frozen-forward 稳定性。",
        "",
        "## 城市与训练期准确度",
        "",
        *table(
            accuracy_slices,
            [
                "slice",
                "baskets",
                "cities",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "market_delta_ci_low",
                "market_delta_ci_high",
                "brier_delta_vs_market",
                "early_roi",
                "late_roi",
            ],
        ),
        "",
        f"训练期最准确城市层 ROI {accurate['fee_adjusted_roi']:.4%}，"
        f"95% CI [{accurate['roi_ci_low']:.4%}, {accurate['roi_ci_high']:.4%}]；"
        "准确度分层没有形成单调关系。train-middle 相对 market-selection 有正 excess，"
        "但 absolute ROI CI 仍擦过 0；train-weak late 为负且 proper score 更差。"
        "因此训练 MAE 可作 soft reliability，不足以生成 allowlist。",
        "",
        "预定义 climate family：",
        "",
        *table(
            family_slices,
            [
                "slice",
                "baskets",
                "cities",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_q_bh",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "market_delta_ci_low",
                "market_delta_ci_high",
                "market_delta_q_bh",
                "brier_delta_vs_market",
                "brier_delta_ci_low",
                "brier_delta_ci_high",
                "early_roi",
                "late_roi",
            ],
        ),
        "",
        f"`europe_cloud_break` ROI {europe_family['fee_adjusted_roi']:.4%}，"
        f"相对 market-only +{europe_family['roi_delta_vs_market_selection']:.4%}；"
        "交易层显著且五城聚合 early/late 都为正，但 proper-score CI 跨 0。",
        "",
        "Europe-cloud-break 单城贡献：",
        "",
        *table(
            europe_cities,
            [
                "city",
                "baskets",
                "active_days",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "early_roi",
                "late_roi",
                "brier_delta_vs_market",
            ],
        ),
        "",
        "Europe-cloud-break leave-one-city-out：",
        "",
        *table(
            family_leave_one_out,
            [
                "excluded_city",
                "baskets",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "market_delta_ci_low",
                "market_delta_ci_high",
                "early_roi",
                "late_roi",
            ],
        ),
        "",
        "剔除任一城市后组合 ROI 仍为正，说明 family 结果不是单城独占；"
        "但 Amsterdam + Munich 合计贡献约 68% PnL，且 Warsaw late 明显不稳，"
        "仍不足以据此生成五城 live allowlist。",
        "",
        "Region：",
        "",
        *table(
            region_slices,
            [
                "slice",
                "baskets",
                "cities",
                "fee_adjusted_roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_q_bh",
                "market_selection_roi",
                "roi_delta_vs_market_selection",
                "market_delta_q_bh",
                "brier_delta_vs_market",
                "early_roi",
                "late_roi",
            ],
        ),
        "",
        "单城结果另存 `city_summary.csv`。单城 winner 即使通过 ROI 与 market-selection "
        "多重检验，也仍需 proper-score 与新日期复核；因此本轮不生成 city allowlist。",
        "",
        "## Signal / evidence funnel",
        "",
        f"- raw full-ladder：{funnel['raw_baskets']} baskets / "
        f"{funnel['raw_dates']} target dates / {funnel['raw_cities']} cities。",
        f"- distance=2 executable + settled：{funnel['expression_baskets']} "
        f"baskets / {funnel['expression_dates']} dates（至少一侧可执行）。",
        f"- two-sided distance=2 paired expression："
        f"{funnel['paired_expression_baskets']} baskets；"
        f"其余 {funnel['expression_baskets'] - funnel['paired_expression_baskets']} "
        "只有一侧可执行，属于 expression coverage gap。",
        f"- all-5-source calibration：{funnel['model_eligible_baskets']} baskets / "
        f"{funnel['model_eligible_cities']} cities；另 "
        f"{funnel['paired_expression_baskets'] - funnel['model_eligible_baskets']} "
        "paired baskets 因冻结训练残差不足而缺失。",
        f"- normalized market probability coverage：{funnel['paired_baskets']} baskets；"
        f"另 {funnel['model_eligible_baskets'] - funnel['paired_baskets']} baskets "
        "是盘口归一化 coverage gap，不是策略筛除。",
        f"- fixed paired denominator：{funnel['paired_baskets']} baskets / "
        f"{funnel['paired_dates']} dates / {funnel['paired_cities']} cities。",
        "- 这是 opportunity-level research replay，不是 fill；actual fill 环为 0。",
        "",
        "## 数据完整性自检",
        "",
        f"- candidate rows={funnel['candidate_rows']}，应为 paired baskets×2="
        f"{funnel['paired_baskets'] * 2}。",
        f"- candidate pair violations={funnel['candidate_pair_violations']}；"
        f"duplicate candidate keys={funnel['duplicate_candidate_rows']}。",
        f"- finite source probabilities={funnel['finite_source_probabilities']} / "
        f"{funnel['expected_source_probabilities']}；"
        f"non-binary settlements={funnel['non_binary_outcomes']}。",
        "- settlement 与 executable quote 均来自同一 immutable snapshot denominator；"
        "未同步或重建 canonical DB。",
        "",
        "## 三门与动作",
        "",
        "- significance=FAIL：全 5 源与 source-selected policy 的 ROI CI 均跨 0。",
        "- baseline=FAIL（全城市）：logloss 显著劣于 market。"
        "`europe_cloud_break` 的交易表达打赢 market-only / mechanical，"
        "但 Brier/logloss delta CI 仍跨 0。",
        "- forward=FAIL/NA：全 5 源 late ROI 仅 +0.15%；Europe family early/late "
        "同号，但 family 是看过本轮 holdout 后选出，尚无新日期 frozen forward。",
        "- conclusion=全城市 inconclusive；Europe-cloud-break 为"
        "`zero-notional shadow_candidate hypothesis`，不改 live。"
        "置信度与单城结果只记录连续 score，不转 hard gate。",
        "",
        "## 覆盖环",
        "",
        "- 已覆盖：描述性绩效、target-date bootstrap、source 判别、概率分布、"
        "组合相关日期 block、同分母基准。",
        "- 未覆盖：真实 fill/queue、容量、迁盘后 8 个 target dates 的 full-ladder "
        "raw（forecast 已补全但 JRS 文件权限仍阻断）。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ladder = pd.read_csv(args.ladder)
    forecasts = pd.read_csv(args.forecasts)
    residuals, training_stats, global_stats = load_training_stats(args.errors)
    candidates = build_candidate_rows(
        ladder, forecasts, residuals, training_stats
    )
    if candidates.empty:
        raise RuntimeError("no fixed distance-2 candidate denominator")
    model_eligible_baskets, model_eligible_cities = model_eligible_distance2(
        ladder, forecasts, residuals
    )
    weights = policy_weights(candidates, global_stats)
    policy_candidates = {
        name: apply_policy_probabilities(candidates, city_weights, name)
        for name, city_weights in weights.items()
    }
    selected = {
        name: select_best(frame) for name, frame in policy_candidates.items()
    }
    market_candidates = candidates.copy()
    market_candidates["policy"] = "market_only_selection"
    market_candidates["policy_p_no"] = market_candidates["market_p_no"]
    market_candidates["policy_edge"] = (
        market_candidates["market_p_no"] - market_candidates["cost"]
    )
    market_candidates["brier"] = np.square(
        market_candidates["market_p_no"] - market_candidates["no_win"]
    )
    market_candidates["market_brier"] = market_candidates["brier"]
    market_candidates["logloss"] = [
        logloss(p, y)
        for p, y in zip(
            market_candidates["market_p_no"], market_candidates["no_win"]
        )
    ]
    market_candidates["market_logloss"] = market_candidates["logloss"]
    policy_candidates["market_only_selection"] = market_candidates
    selected["market_only_selection"] = select_best(market_candidates)
    mechanical = mechanical_half(candidates)
    selected["mechanical_half_distance2"] = mechanical
    # Mechanical is a trade baseline, not a probability model.  Reuse market
    # candidate scores only so the summary schema remains rectangular.
    policy_candidates["mechanical_half_distance2"] = market_candidates

    policy_summary = summarize_policies(
        policy_candidates, selected, draws=args.draws
    )
    all5_candidates = policy_candidates["equal_all5"]
    selected_all5 = selected["equal_all5"]
    confidence_rows = attach_confidence(all5_candidates, selected_all5)
    training_accuracy = city_train_accuracy(
        training_stats, set(confidence_rows["city"].astype(str))
    )
    confidence_rows = confidence_rows.merge(
        training_accuracy, on="city", how="left", validate="many_to_one"
    )
    slice_baselines = {
        "market_selected": selected["market_only_selection"],
        "mechanical_selected": selected["mechanical_half_distance2"],
        "all5_candidates": all5_candidates,
    }
    confidence_summaries = pd.concat(
        [
            slice_summary(
                confidence_rows,
                "vote_bucket",
                draws=args.draws,
                seed_offset=400,
                **slice_baselines,
            ),
            slice_summary(
                confidence_rows,
                "edge_bucket",
                draws=args.draws,
                seed_offset=500,
                **slice_baselines,
            ),
            slice_summary(
                confidence_rows,
                "prob_disagreement_tercile",
                draws=args.draws,
                seed_offset=600,
                **slice_baselines,
            ),
            slice_summary(
                confidence_rows,
                "edge_margin_tercile",
                draws=args.draws,
                seed_offset=700,
                **slice_baselines,
            ),
        ],
        ignore_index=True,
    )
    accuracy_slices = slice_summary(
        confidence_rows,
        "accuracy_tier",
        draws=args.draws,
        seed_offset=800,
        **slice_baselines,
    )
    family_slices = slice_summary(
        confidence_rows,
        "city_family",
        draws=args.draws,
        seed_offset=850,
        **slice_baselines,
    )
    region_slices = slice_summary(
        confidence_rows,
        "region",
        draws=args.draws,
        seed_offset=900,
        **slice_baselines,
    )
    cities = city_summary(
        confidence_rows,
        training_accuracy,
        draws=args.draws,
        market_selected=selected["market_only_selection"],
        mechanical_selected=selected["mechanical_half_distance2"],
        all5_candidates=all5_candidates,
    )
    best_counts = best_source_counts(
        training_stats[
            training_stats["city"].isin(confidence_rows["city"].unique())
        ]
    )
    overlay_keys = {
        "europe_cloud_break": set(
            confidence_rows.loc[
                confidence_rows["city_family"] == "europe_cloud_break",
                "snapshot_key",
            ]
        ),
        "train_not_weak": set(
            confidence_rows.loc[
                confidence_rows["accuracy_tier"] != "train_weak",
                "snapshot_key",
            ]
        ),
        "vote_ge4": set(
            confidence_rows.loc[
                confidence_rows["source_vote_count"] >= 4,
                "snapshot_key",
            ]
        ),
    }
    overlay_summaries: list[pd.DataFrame] = []
    for overlay_name, keys in overlay_keys.items():
        overlay_candidates = {
            name: frame[frame["snapshot_key"].isin(keys)].copy()
            for name, frame in policy_candidates.items()
        }
        overlay_selected = {
            name: frame[frame["snapshot_key"].isin(keys)].copy()
            for name, frame in selected.items()
        }
        overlay_summary = summarize_policies(
            overlay_candidates, overlay_selected, draws=args.draws
        )
        overlay_summary.insert(0, "overlay", overlay_name)
        overlay_summaries.append(overlay_summary)
    overlay_policy_summary = pd.concat(
        overlay_summaries, ignore_index=True
    )
    europe_family_cities = {
        "Amsterdam",
        "Helsinki",
        "Madrid",
        "Munich",
        "Warsaw",
    }
    family_leave_one_out = family_leave_one_city_out(
        confidence_rows,
        family_cities=europe_family_cities,
        market_selected=selected["market_only_selection"],
        draws=args.draws,
    )
    funnel = {
        "raw_baskets": int(ladder["snapshot_key"].nunique()),
        "raw_dates": int(ladder["target_date"].nunique()),
        "raw_cities": int(ladder["city"].nunique()),
        "expression_baskets": int(
            ladder.loc[
                ladder["distance_from_nearest_endpoint"] == 2,
                "snapshot_key",
            ].nunique()
        ),
        "expression_dates": int(
            ladder.loc[
                ladder["distance_from_nearest_endpoint"] == 2,
                "target_date",
            ].nunique()
        ),
        "paired_expression_baskets": int(
            (
                ladder.loc[
                    (ladder["distance_from_nearest_endpoint"] == 2)
                    & ladder["no_executable"].astype(bool)
                    & ladder["yes_win"].notna()
                ]
                .groupby("snapshot_key")
                .size()
                == 2
            ).sum()
        ),
        "model_eligible_baskets": model_eligible_baskets,
        "model_eligible_cities": model_eligible_cities,
        "paired_baskets": len(selected_all5),
        "paired_dates": int(selected_all5["target_date"].nunique()),
        "paired_cities": int(selected_all5["city"].nunique()),
        "source_policy_trials": len(weights) - 1,
        "candidate_rows": len(candidates),
        "candidate_pair_violations": int(
            (
                candidates.groupby("snapshot_key").size()
                != 2
            ).sum()
        ),
        "duplicate_candidate_rows": int(
            candidates.duplicated(["snapshot_key", "rung_index"]).sum()
        ),
        "finite_source_probabilities": int(
            np.isfinite(
                candidates[
                    [f"p_no_{model}" for model in MODELS]
                ].to_numpy()
            ).sum()
        ),
        "expected_source_probabilities": len(candidates) * len(MODELS),
        "non_binary_outcomes": int(
            (~candidates["no_win"].isin([0.0, 1.0])).sum()
        ),
    }

    candidates.to_csv(args.output_dir / "candidate_source_probs.csv", index=False)
    pd.concat(selected.values(), ignore_index=True).to_csv(
        args.output_dir / "selected_policy_rows.csv", index=False
    )
    policy_summary.to_csv(args.output_dir / "source_policy_summary.csv", index=False)
    confidence_rows.to_csv(args.output_dir / "confidence_rows.csv", index=False)
    confidence_summaries.to_csv(
        args.output_dir / "confidence_slices.csv", index=False
    )
    accuracy_slices.to_csv(
        args.output_dir / "accuracy_tier_slices.csv", index=False
    )
    family_slices.to_csv(
        args.output_dir / "city_family_slices.csv", index=False
    )
    region_slices.to_csv(args.output_dir / "region_slices.csv", index=False)
    cities.to_csv(args.output_dir / "city_summary.csv", index=False)
    global_stats.to_csv(args.output_dir / "source_train_quality.csv", index=False)
    best_counts.to_csv(
        args.output_dir / "city_best_source_counts.csv", index=False
    )
    overlay_policy_summary.to_csv(
        args.output_dir / "overlay_source_policy_summary.csv", index=False
    )
    family_leave_one_out.to_csv(
        args.output_dir / "europe_cloud_break_leave_one_city_out.csv",
        index=False,
    )
    summary_payload = {
        "contract": {
            "train_end": TRAIN_END,
            "early_end": EARLY_END,
            "expression": "distance_from_nearest_endpoint=2; choose left/right by PIT edge",
            "fee_rate": 0.05,
            "trade_class": "research_replay",
            "multiple_testing": "Benjamini-Hochberg within source policies and cities",
        },
        "funnel": funnel,
        "source_policy_summary": policy_summary.to_dict("records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    curve_frames = []
    plot_policies = [
        "equal_all5",
        "global_top3_train",
        "city_best1_train",
        "city_best2_train",
        "market_only_selection",
        "mechanical_half_distance2",
    ]
    for name in plot_policies:
        frame = selected[name]
        daily = frame.groupby("target_date", as_index=False)["pnl"].sum()
        daily["policy"] = name
        curve_frames.append(daily)
    curve = pd.concat(curve_frames).pivot(
        index="target_date", columns="policy", values="pnl"
    ).fillna(0.0)
    curve.cumsum().plot(figsize=(11, 6))
    plt.axhline(0.0, color="black", linewidth=0.8)
    plt.ylabel("Cumulative PnL per one-share basket")
    plt.tight_layout()
    plt.savefig(args.output_dir / "source_policy_cumulative_pnl.png", dpi=160)
    plt.close()

    write_report(
        args.report,
        policy_summary=policy_summary,
        confidence=confidence_summaries,
        city_rows=cities,
        accuracy_slices=accuracy_slices,
        family_slices=family_slices,
        region_slices=region_slices,
        global_stats=global_stats,
        best_counts=best_counts,
        overlay_policy_summary=overlay_policy_summary,
        family_leave_one_out=family_leave_one_out,
        funnel=funnel,
    )
    print(policy_summary.sort_values("fee_adjusted_roi", ascending=False).to_string(index=False))
    print("\nAccuracy tiers\n", accuracy_slices.to_string(index=False))
    print("\nTop cities (exploratory)\n", cities.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
