#!/usr/bin/env python3
"""Tokyo v6 market-anchored current-bracket probability research.

The direct current-exact market midpoint is a fixed logit offset.  Models can
only learn a prior-date weather/path correction.  Expanding OOF starts after
five market dates; no 2026-08 labels are read.  Outputs are research-only and
never create plans, orders, fills, or live behavior.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

import joblib
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_forward_v3 as v3,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v1 as v1,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v2 as v2,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_current_break_binary_v5 as v5,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    write_rows,
)


V5_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_current_break_binary_v5"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_market_anchor_binary_v6"
)
MIN_TRAIN_DATES = 5
RIDGE_GRID = (1.0, 5.0, 20.0, 100.0)
EPS = 1e-6
LOGIT_FLOOR = 0.001
BOOTSTRAP_DRAWS = 5000
MODEL_BASELINES = (
    "market_prior_v6",
    "weather_standalone_v5",
)
COMPACT_FEATURES = ("weather_market_logit_gap",)
PHYSICAL_FEATURES = (
    "weather_market_logit_gap",
    "weather_logit_innovation",
    "remaining_to_18h",
    "jma_pullback_from_running_max_c",
    "jma_temp_slope_60m_cph",
    "solar_elevation_deg",
)
FAMILIES = {
    "compact": COMPACT_FEATURES,
    "physical": PHYSICAL_FEATURES,
}


def logit(value: float) -> float:
    clipped = min(max(float(value), LOGIT_FLOOR), 1.0 - LOGIT_FLOOR)
    return math.log(clipped / (1.0 - clipped))


def numeric(row: dict[str, Any], key: str) -> float:
    value = v1.finite(row.get(key))
    return math.nan if value is None else float(value)


def model_name(family: str, ridge: float) -> str:
    return f"offset_{family}_ridge{int(ridge)}_v6"


def read_csv(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def prepare_rows(
    market_path: Path,
    predictions_path: Path,
    features_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = json.loads((V5_OUT / "summary.json").read_text(encoding="utf-8"))
    if summary["diagnostic_labels_used_in_fit"]:
        raise RuntimeError("v5 diagnostic labels unexpectedly entered model fit")
    if summary["diagnostic_window"] != ["2026-07-16", "2026-07-30"]:
        raise RuntimeError("unexpected v5 diagnostic window")

    stay_probability = {
        str(row["state_id"]): float(row["p_model"])
        for row in read_csv(predictions_path)
        if row["model_id"] == v5.CHAMPION
        and row["outcome_id"] == "stay_current_exact"
    }
    continuous = v2.annotate_grains(
        [
            row
            for row in v1.build_continuous_rows(v1.read_rows(features_path))
            if row["remaining_rise_class"] is not None
            and v5.DIAGNOSTIC_START
            <= str(row["target_date"])
            <= v5.DIAGNOSTIC_END
        ]
    )
    by_state = {str(row["state_id"]): row for row in continuous}
    previous_same_bracket: dict[str, str | None] = {}
    latest: dict[tuple[str, int], str] = {}
    for row in sorted(
        continuous,
        key=lambda item: (
            str(item["target_date"]),
            str(item["decision_ts_utc"]),
        ),
    ):
        state_id = str(row["state_id"])
        key = (str(row["target_date"]), int(row["current_bracket"]))
        previous_same_bracket[state_id] = latest.get(key)
        latest[key] = state_id

    rows = []
    skipped_missing_current_quote = 0
    for source in read_csv(market_path):
        if int(source["settlement_lower_bound_violation"]):
            continue
        state_id = str(source["state_id"])
        feature = by_state.get(state_id)
        if feature is None:
            raise RuntimeError(f"missing continuous feature row: {state_id}")
        current = str(source["current_bracket"])
        quote = json.loads(str(source["quotes_json"])).get(current)
        if not quote or v1.finite(quote.get("mid")) is None:
            skipped_missing_current_quote += 1
            continue
        market_probability = float(quote["mid"])
        weather_probability = float(
            source[f"{v5.CHAMPION}_p_current"]
        )
        previous_state = previous_same_bracket.get(state_id)
        previous_probability = stay_probability.get(
            str(previous_state), weather_probability
        )
        market_logit = logit(market_probability)
        weather_logit = logit(weather_probability)
        row = dict(source)
        row.update(
            {
                "y_stay": int(
                    str(source["winning_bracket"])
                    == str(source["current_bracket"])
                ),
                "market_p_stay": market_probability,
                "weather_p_stay_v5": weather_probability,
                "market_logit": market_logit,
                "weather_market_logit_gap": float(
                    np.clip(weather_logit - market_logit, -6.0, 6.0)
                ),
                "weather_logit_innovation": float(
                    np.clip(
                        weather_logit - logit(previous_probability),
                        -4.0,
                        4.0,
                    )
                ),
                "is_checkpoint": 1,
            }
        )
        for key in (
            "remaining_to_18h",
            "jma_pullback_from_running_max_c",
            "jma_temp_slope_60m_cph",
            "solar_elevation_deg",
        ):
            row[key] = numeric(feature, key)
        rows.append(row)
    rows.sort(
        key=lambda item: (
            str(item["target_date"]),
            str(item["decision_ts_utc"]),
        )
    )
    return rows, {
        "input_market_rows": len(read_csv(market_path)),
        "usable_current_contract_rows": len(rows),
        "target_dates": sorted({str(row["target_date"]) for row in rows}),
        "collector_exact_rows": sum(
            row["availability_clock_class"]
            == "collector_exact_hash_verified"
            for row in rows
        ),
        "skipped_missing_current_quote": skipped_missing_current_quote,
        "v5_model_hash": summary["model_hashes"][v5.CHAMPION],
    }


def fit_offset(
    train: list[dict[str, Any]],
    features: Sequence[str],
    ridge: float,
) -> dict[str, Any]:
    raw = np.asarray(
        [[float(row[feature]) for feature in features] for row in train],
        dtype=float,
    )
    median = np.nanmedian(raw, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(raw), raw, median)
    mean = np.mean(filled, axis=0)
    scale = np.std(filled, axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    matrix = (filled - mean) / scale
    matrix = np.column_stack([np.ones(len(matrix)), matrix])
    labels = np.asarray([int(row["y_stay"]) for row in train], dtype=float)
    offset = np.asarray([float(row["market_logit"]) for row in train])
    weights = v2.multigrain_weights(train)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        probability = np.clip(expit(offset + matrix @ beta), EPS, 1 - EPS)
        loss = np.average(
            -labels * np.log(probability)
            - (1 - labels) * np.log(1 - probability),
            weights=weights,
        )
        # The intercept is also shrunk: the market remains the actual anchor.
        penalty = float(ridge) * float(beta @ beta) / len(train)
        gradient = (
            matrix.T @ (weights * (probability - labels)) / weights.sum()
        )
        gradient += 2 * float(ridge) * beta / len(train)
        return loss + penalty, gradient

    result = minimize(
        lambda beta: objective(beta),
        np.zeros(matrix.shape[1]),
        jac=True,
        method="L-BFGS-B",
    )
    if not result.success:
        raise RuntimeError(f"offset fit failed: {result.message}")
    return {
        "features": list(features),
        "ridge": float(ridge),
        "median": median.tolist(),
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "beta": result.x.tolist(),
        "market_logit_fixed_offset": True,
        "intercept_regularized": True,
        "train_rows": len(train),
        "train_dates": sorted({str(row["target_date"]) for row in train}),
    }


def predict_offset(
    artifact: dict[str, Any], rows: list[dict[str, Any]]
) -> np.ndarray:
    features = list(artifact["features"])
    raw = np.asarray(
        [[float(row[feature]) for feature in features] for row in rows],
        dtype=float,
    )
    median = np.asarray(artifact["median"], dtype=float)
    matrix = np.where(np.isfinite(raw), raw, median)
    matrix = (
        matrix - np.asarray(artifact["mean"], dtype=float)
    ) / np.asarray(artifact["scale"], dtype=float)
    matrix = np.column_stack([np.ones(len(matrix)), matrix])
    offset = np.asarray([float(row["market_logit"]) for row in rows])
    return expit(offset + matrix @ np.asarray(artifact["beta"], dtype=float))


def expanding_predictions(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dates = sorted({str(row["target_date"]) for row in rows})
    output: list[dict[str, Any]] = []
    fit_audit: list[dict[str, Any]] = []
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train_dates = set(dates[:index])
        train = [
            row for row in rows if str(row["target_date"]) in train_dates
        ]
        test = [
            dict(row)
            for row in rows
            if str(row["target_date"]) == target_date
        ]
        if not test or len({int(row["y_stay"]) for row in train}) < 2:
            continue
        for row in test:
            row["p_market_prior_v6"] = float(row["market_p_stay"])
            row["p_weather_standalone_v5"] = float(
                row["weather_p_stay_v5"]
            )
        for family, features in FAMILIES.items():
            for ridge in RIDGE_GRID:
                name = model_name(family, ridge)
                artifact = fit_offset(train, features, ridge)
                values = predict_offset(artifact, test)
                for row, value in zip(test, values):
                    row[f"p_{name}"] = float(value)
                fit_audit.append(
                    {
                        "test_date": target_date,
                        "model": name,
                        "train_rows": len(train),
                        "train_dates": len(train_dates),
                        "train_end": dates[index - 1],
                        "beta_json": json.dumps(artifact["beta"]),
                    }
                )
        output.extend(test)
    return output, fit_audit


def score(
    rows: list[dict[str, Any]], probability_key: str, *, grain: str
) -> dict[str, Any]:
    selected = [
        row for row in rows if int(row[f"is_{grain}"]) == 1
    ]
    daily: dict[str, list[tuple[float, float]]] = defaultdict(list)
    correct = []
    for row in selected:
        probability = min(
            max(float(row[probability_key]), EPS), 1.0 - EPS
        )
        label = int(row["y_stay"])
        daily[str(row["target_date"])].append(
            (
                (probability - label) ** 2,
                -(
                    label * math.log(probability)
                    + (1 - label) * math.log(1 - probability)
                ),
            )
        )
        correct.append(int((probability >= 0.5) == label))
    return {
        "states": len(selected),
        "target_dates": len(daily),
        "brier": float(
            np.mean([np.mean([value[0] for value in group]) for group in daily.values()])
        ),
        "logloss": float(
            np.mean([np.mean([value[1] for value in group]) for group in daily.values()])
        ),
        "accuracy": float(np.mean(correct)),
        "date_equal_mean_probability": float(
            np.mean(
                [
                    np.mean(
                        [
                            float(row[probability_key])
                            for row in selected
                            if str(row["target_date"]) == target_date
                        ]
                    )
                    for target_date in daily
                ]
            )
        ),
        "date_equal_actual_rate": float(
            np.mean(
                [
                    np.mean(
                        [
                            int(row["y_stay"])
                            for row in selected
                            if str(row["target_date"]) == target_date
                        ]
                    )
                    for target_date in daily
                ]
            )
        ),
    }


def bootstrap_delta(
    rows: list[dict[str, Any]],
    candidate_key: str,
    *,
    metric: str,
    grain: str,
) -> tuple[float, float, float]:
    selected = [row for row in rows if int(row[f"is_{grain}"]) == 1]
    daily: dict[str, list[float]] = defaultdict(list)
    for row in selected:
        label = int(row["y_stay"])
        candidate = min(max(float(row[candidate_key]), EPS), 1 - EPS)
        market = min(max(float(row["p_market_prior_v6"]), EPS), 1 - EPS)
        if metric == "brier":
            delta = (candidate - label) ** 2 - (market - label) ** 2
        elif metric == "logloss":
            candidate_loss = -(
                label * math.log(candidate)
                + (1 - label) * math.log(1 - candidate)
            )
            market_loss = -(
                label * math.log(market)
                + (1 - label) * math.log(1 - market)
            )
            delta = candidate_loss - market_loss
        else:
            raise ValueError(metric)
        daily[str(row["target_date"])].append(delta)
    blocks = np.asarray([np.mean(group) for group in daily.values()])
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            np.mean(rng.choice(blocks, len(blocks), replace=True))
            for _ in range(BOOTSTRAP_DRAWS)
        ]
    )
    return (
        float(np.mean(blocks)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def probability_scores(
    rows: list[dict[str, Any]], models: Sequence[str], *, split: str
) -> list[dict[str, Any]]:
    output = []
    for model in models:
        key = f"p_{model}"
        for grain in v2.GRAINS:
            record = {
                "split": split,
                "model": model,
                "grain": grain,
                **score(rows, key, grain=grain),
            }
            if model != "market_prior_v6":
                for metric in ("brier", "logloss"):
                    delta, low, high = bootstrap_delta(
                        rows, key, metric=metric, grain=grain
                    )
                    record[f"{metric}_delta_vs_market"] = delta
                    record[f"{metric}_delta_ci_low"] = low
                    record[f"{metric}_delta_ci_high"] = high
            output.append(record)
    return output


def choose_champion(scores: list[dict[str, Any]]) -> str:
    checkpoint = {
        str(row["model"]): row
        for row in scores
        if row["grain"] == "checkpoint"
        and row["split"] == "expanding_oof_after_five_market_dates"
    }
    market = checkpoint["market_prior_v6"]
    candidates = []
    for name, row in checkpoint.items():
        if not name.startswith("offset_"):
            continue
        if (
            float(row["brier"]) < float(market["brier"])
            and float(row["logloss"]) < float(market["logloss"])
        ):
            candidates.append(row)
    if not candidates:
        return "market_prior_v6"
    return str(
        min(candidates, key=lambda row: (float(row["brier"]), float(row["logloss"])))[
            "model"
        ]
    )


def expression_rows(
    rows: list[dict[str, Any]], models: Sequence[str]
) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        current = str(row["current_bracket"])
        quote = json.loads(str(row["quotes_json"])).get(current)
        if not quote:
            continue
        yes_ask = v1.finite(quote.get("ask"))
        yes_bid = v1.finite(quote.get("bid"))
        yes_mid = v1.finite(quote.get("mid"))
        if yes_mid is None:
            continue
        for model in models:
            p_stay = float(row[f"p_{model}"])
            for side, probability, ask, market_probability, label in (
                (
                    "YES",
                    p_stay,
                    yes_ask,
                    yes_mid,
                    int(row["y_stay"]),
                ),
                (
                    "NO",
                    1.0 - p_stay,
                    None if yes_bid is None else 1.0 - yes_bid,
                    1.0 - yes_mid,
                    1 - int(row["y_stay"]),
                ),
            ):
                if ask is None or not 0 < float(ask) < 1:
                    continue
                fee = v3.official_fee_per_share(float(ask))
                item = dict(row)
                item.update(
                    {
                        "model": model,
                        "expression_delta": 0,
                        "expression_bracket": current,
                        "side": side,
                        "p_win": probability,
                        "selected_side_ask": float(ask),
                        "market_side_probability": market_probability,
                        "settled_win": label,
                        "fee_per_share": fee,
                        "fee_adjusted_edge": probability - float(ask) - fee,
                        "strategy_policy": (
                            "current_exact_first_edge_ge_2pct_no_add"
                        ),
                        "trade_class": "research_counterfactual",
                    }
                )
                output.append(item)
    return output


def low_price_scores(
    expressions: list[dict[str, Any]], models: Sequence[str], *, split: str
) -> list[dict[str, Any]]:
    output = []
    for model in models:
        for selected_only in (0, 1):
            rows = [
                row
                for row in expressions
                if row["model"] == model
                and float(row["selected_side_ask"]) < 0.05
                and (
                    not selected_only
                    or float(row["fee_adjusted_edge"]) >= v3.EDGE_THRESHOLD
                )
            ]
            if not rows:
                continue
            dates = sorted({str(row["target_date"]) for row in rows})
            output.append(
                {
                    "model": model,
                    "slice": (
                        f"{split}_eligible_edge_ge_2pct_ask_lt_5c"
                        if selected_only
                        else f"{split}_all_expressions_ask_lt_5c"
                    ),
                    "expressions": len(rows),
                    "target_dates": len(dates),
                    "wins": sum(int(row["settled_win"]) for row in rows),
                    "date_equal_actual_rate": float(
                        np.mean(
                            [
                                np.mean(
                                    [
                                        int(row["settled_win"])
                                        for row in rows
                                        if str(row["target_date"]) == target_date
                                    ]
                                )
                                for target_date in dates
                            ]
                        )
                    ),
                    "date_equal_model_probability": float(
                        np.mean(
                            [
                                np.mean(
                                    [
                                        float(row["p_win"])
                                        for row in rows
                                        if str(row["target_date"]) == target_date
                                    ]
                                )
                                for target_date in dates
                            ]
                        )
                    ),
                    "date_equal_market_probability": float(
                        np.mean(
                            [
                                np.mean(
                                    [
                                        float(row["market_side_probability"])
                                        for row in rows
                                        if str(row["target_date"]) == target_date
                                    ]
                                )
                                for target_date in dates
                            ]
                        )
                    ),
                }
            )
    return output


def persist_artifact(
    out: Path,
    all_rows: list[dict[str, Any]],
    champion: str,
    input_hashes: dict[str, str],
) -> dict[str, str] | None:
    if champion == "market_prior_v6":
        return None
    parts = champion.split("_")
    family = parts[1]
    ridge = float(parts[2].replace("ridge", ""))
    artifact = fit_offset(all_rows, FAMILIES[family], ridge)
    artifact.update(
        {
            "schema_version": "tokyo_market_anchor_binary_v6",
            "model_id": champion,
            "target_id": "current_exact_stay_probability",
            "training_end": max(
                str(row["target_date"]) for row in all_rows
            ),
            "clean_forward_start": "2026-08-01",
            "strict_pit_forecast_available": False,
            "diagnostic_window_used_for_model_development": True,
            "input_hashes": input_hashes,
        }
    )
    model_dir = out / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"{champion}.joblib"
    joblib.dump(artifact, path, compress=3)
    spec = {
        key: value
        for key, value in artifact.items()
        if key not in {"median", "mean", "scale", "beta"}
    }
    spec_path = model_dir / f"{champion}.spec.json"
    spec_bytes = (
        json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    spec_path.write_bytes(spec_bytes)
    return {
        "model_artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "model_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
    }


def semantic_hash(path: Path) -> str:
    return v2.semantic_file_hash(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-rows", type=Path, default=V5_OUT / "market_join_rows.csv.gz"
    )
    parser.add_argument(
        "--v5-predictions", type=Path, default=V5_OUT / "prediction_long.csv.gz"
    )
    parser.add_argument("--features", type=Path, default=v1.FEATURE_ROWS)
    parser.add_argument("--raw-books", type=Path, default=v1.RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows, coverage = prepare_rows(
        args.market_rows, args.v5_predictions, args.features
    )
    oof, fit_audit = expanding_predictions(rows)
    candidate_models = list(MODEL_BASELINES) + [
        model_name(family, ridge)
        for family in FAMILIES
        for ridge in RIDGE_GRID
    ]
    scores = probability_scores(
        oof,
        candidate_models,
        split="expanding_oof_after_five_market_dates",
    )
    exact_oof = [
        row
        for row in oof
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    scores.extend(
        probability_scores(
            exact_oof,
            candidate_models,
            split="expanding_oof_collector_exact",
        )
    )
    champion = choose_champion(scores)

    expressions = expression_rows(oof, candidate_models)
    trades = v3.select_first_signal(
        expressions,
        args.raw_books,
        selection_policy="first_signal_per_model_target_date_bracket",
    )
    oof_dates = sorted({str(row["target_date"]) for row in oof})
    strategy = v3.strategy_summary(
        expressions,
        trades,
        split="expanding_oof_after_five_market_dates",
        denominator_dates=oof_dates,
        model_names=candidate_models,
    )
    exact_expressions = [
        row
        for row in expressions
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    exact_trades = [
        row
        for row in trades
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    strategy.extend(
        v3.strategy_summary(
            exact_expressions,
            exact_trades,
            split="expanding_oof_collector_exact",
            denominator_dates=oof_dates,
            model_names=candidate_models,
        )
    )
    side = v3.strategy_side_summary(
        trades,
        split="expanding_oof_after_five_market_dates",
        model_names=candidate_models,
    )
    low_price = low_price_scores(
        expressions, candidate_models, split="all_clock_classes"
    )
    low_price.extend(
        low_price_scores(
            exact_expressions,
            candidate_models,
            split="collector_exact",
        )
    )

    input_hashes = {
        "market_rows_semantic_sha256": semantic_hash(args.market_rows),
        "v5_predictions_semantic_sha256": semantic_hash(args.v5_predictions),
        "features_semantic_sha256": semantic_hash(args.features),
    }
    artifact_hashes = persist_artifact(
        args.out, rows, champion, input_hashes
    )

    write_rows(args.out / "prepared_market_rows.csv.gz", rows)
    write_rows(args.out / "expanding_oof_predictions.csv.gz", oof)
    write_rows(args.out / "fit_audit.csv", fit_audit)
    write_rows(args.out / "probability_scores.csv", scores)
    write_rows(args.out / "expression_candidates.csv.gz", expressions)
    write_rows(args.out / "selected_trades.csv", trades)
    write_rows(args.out / "strategy_summary.csv", strategy)
    write_rows(args.out / "strategy_side_summary.csv", side)
    write_rows(args.out / "low_price_tail_audit.csv", low_price)

    summary = {
        "schema_version": "tokyo_market_anchor_binary_v6",
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
        "market_logit_fixed_offset": True,
        "candidate_search_k": len(FAMILIES) * len(RIDGE_GRID),
        "candidate_models": candidate_models,
        "selection_metric": (
            "checkpoint date-equal Brier, require Brier and logloss below market"
        ),
        "selected_champion": champion,
        "minimum_prior_market_dates": MIN_TRAIN_DATES,
        "oof_dates": oof_dates,
        "oof_rows": len(oof),
        "oof_collector_exact_rows": len(exact_oof),
        "clean_forward_start": "2026-08-01",
        "strict_pit_forecast_available": False,
        "low_price_removed_by_hard_filter": False,
        "development_window_used_for_model_selection": True,
        "gate_status": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "NA",
            "conclusion": "inconclusive_research_challenger",
        },
        "coverage": coverage,
        "input_hashes": input_hashes,
        "artifact_hashes": artifact_hashes,
        "strategy_policy": {
            "expressions": ["current_exact_yes", "current_exact_no"],
            "edge_threshold": v3.EDGE_THRESHOLD,
            "shares": v3.SHARES,
            "fee_rate": v3.FEE_RATE,
            "selection": "first_signal_per_model_target_date_bracket",
            "add_on_allowed": False,
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
