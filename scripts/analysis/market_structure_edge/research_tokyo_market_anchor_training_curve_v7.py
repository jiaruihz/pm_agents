#!/usr/bin/env python3
"""Tokyo market-anchor PIT coverage and training-size sensitivity audit.

This is a development-only learning-curve experiment.  It keeps a common
three-date test denominator and varies only the number of earlier market dates
used to fit the already-selected v6 physical-offset specification.  It never
creates plans, orders, fills, or live behavior.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np


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
    research_tokyo_market_anchor_binary_v6 as v6,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    write_rows,
)


DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_market_anchor_training_curve_v7"
)
V1_MARKET_ROWS = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_continuous_ladder_probability_v1"
    / "market_join_rows.csv.gz"
)
TRAIN_SIZES = (3, 5, 7, 9)
TEST_DATES = ("2026-07-27", "2026-07-28", "2026-07-29")
RIDGE = 1.0


def model_id(mode: str, train_dates: int) -> str:
    return f"physical_ridge1_{mode}_n{train_dates:02d}_v7"


def score_rows(
    rows: list[dict[str, Any]],
    models: Iterable[str],
    *,
    clock_slice: str,
) -> list[dict[str, Any]]:
    selected_rows = (
        rows
        if clock_slice == "all"
        else [
            row
            for row in rows
            if row["availability_clock_class"]
            == "collector_exact_hash_verified"
        ]
    )
    output: list[dict[str, Any]] = []
    for model in models:
        key = f"p_{model}"
        for grain in v6.v2.GRAINS:
            selected_grain = [
                row
                for row in selected_rows
                if int(row[f"is_{grain}"]) == 1
            ]
            if not selected_grain:
                continue
            record = {
                "clock_slice": clock_slice,
                "model": model,
                "grain": grain,
                **v6.score(selected_rows, key, grain=grain),
            }
            if model != "market_prior_v7":
                for metric in ("brier", "logloss"):
                    delta, low, high = v6.bootstrap_delta(
                        selected_rows,
                        key,
                        metric=metric,
                        grain=grain,
                    )
                    record[f"{metric}_delta_vs_market"] = delta
                    record[f"{metric}_delta_ci_low"] = low
                    record[f"{metric}_delta_ci_high"] = high
            output.append(record)
    return output


def raw_book_dates(path: Path) -> list[str]:
    return sorted(
        item.name
        for item in path.glob("20??-??-??")
        if item.is_dir()
    )


def coverage_audit(
    rows: list[dict[str, Any]],
    v1_rows: list[dict[str, Any]],
    raw_books: Path,
) -> list[dict[str, Any]]:
    prepared_dates = sorted({str(row["target_date"]) for row in rows})
    raw_dates = raw_book_dates(raw_books)
    pre_holdout = [
        row for row in v1_rows if str(row["target_date"]) < "2026-07-16"
    ]
    pre_dates = sorted({str(row["target_date"]) for row in pre_holdout})
    pre_exact = [
        row
        for row in pre_holdout
        if row.get("availability_clock_class")
        == "collector_exact_hash_verified"
    ]
    exact_dates = sorted(
        {
            str(row["target_date"])
            for row in rows
            if row["availability_clock_class"]
            == "collector_exact_hash_verified"
        }
    )
    return [
        {
            "layer": "raw_full_ladder_orderbook",
            "rows": None,
            "target_dates": len(raw_dates),
            "start": raw_dates[0] if raw_dates else None,
            "end": raw_dates[-1] if raw_dates else None,
            "pre_2026_07_16_dates": sum(d < "2026-07-16" for d in raw_dates),
            "clock_class": "raw_snapshot",
        },
        {
            "layer": "v1_join_pre_holdout",
            "rows": len(pre_holdout),
            "target_dates": len(pre_dates),
            "start": pre_dates[0] if pre_dates else None,
            "end": pre_dates[-1] if pre_dates else None,
            "pre_2026_07_16_dates": len(pre_dates),
            "clock_class": json.dumps(
                Counter(
                    str(row.get("availability_clock_class"))
                    for row in pre_holdout
                ),
                sort_keys=True,
            ),
        },
        {
            "layer": "v1_join_pre_holdout_collector_exact",
            "rows": len(pre_exact),
            "target_dates": len(
                {str(row["target_date"]) for row in pre_exact}
            ),
            "start": None,
            "end": None,
            "pre_2026_07_16_dates": 0,
            "clock_class": "collector_exact_hash_verified",
        },
        {
            "layer": "v6_prepared_settled_market",
            "rows": len(rows),
            "target_dates": len(prepared_dates),
            "start": prepared_dates[0],
            "end": prepared_dates[-1],
            "pre_2026_07_16_dates": sum(
                d < "2026-07-16" for d in prepared_dates
            ),
            "clock_class": "mixed",
        },
        {
            "layer": "v6_prepared_collector_exact",
            "rows": sum(
                row["availability_clock_class"]
                == "collector_exact_hash_verified"
                for row in rows
            ),
            "target_dates": len(exact_dates),
            "start": exact_dates[0] if exact_dates else None,
            "end": exact_dates[-1] if exact_dates else None,
            "pre_2026_07_16_dates": 0,
            "clock_class": "collector_exact_hash_verified",
        },
    ]


def prediction_stability(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mode in ("prefix_accumulated", "trailing_fixed_cutoff"):
        for previous_size, current_size in zip(TRAIN_SIZES, TRAIN_SIZES[1:]):
            previous_key = f"p_{model_id(mode, previous_size)}"
            current_key = f"p_{model_id(mode, current_size)}"
            shifts = np.asarray(
                [
                    abs(float(row[current_key]) - float(row[previous_key]))
                    for row in rows
                ],
                dtype=float,
            )
            output.append(
                {
                    "mode": mode,
                    "previous_train_dates": previous_size,
                    "current_train_dates": current_size,
                    "test_rows": len(rows),
                    "mean_abs_probability_shift": float(np.mean(shifts)),
                    "p95_abs_probability_shift": float(
                        np.quantile(shifts, 0.95)
                    ),
                    "max_abs_probability_shift": float(np.max(shifts)),
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared-rows",
        type=Path,
        default=v6.DEFAULT_OUT / "prepared_market_rows.csv.gz",
    )
    parser.add_argument(
        "--v1-market-rows", type=Path, default=V1_MARKET_ROWS
    )
    parser.add_argument("--raw-books", type=Path, default=v1.RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = v6.read_csv(args.prepared_rows)
    for row in rows:
        # v6's paired bootstrap helper reads this canonical baseline key.
        row["p_market_prior_v6"] = float(row["market_p_stay"])
        row["p_market_prior_v7"] = float(row["market_p_stay"])
        row["p_weather_standalone_v5"] = float(
            row["weather_p_stay_v5"]
        )
    dates = sorted({str(row["target_date"]) for row in rows})
    if not set(TEST_DATES).issubset(dates):
        raise RuntimeError(f"common test dates missing from input: {dates}")
    test_start = min(TEST_DATES)
    prior_dates = [date for date in dates if date < test_start]
    if len(prior_dates) != max(TRAIN_SIZES):
        raise RuntimeError(
            f"expected {max(TRAIN_SIZES)} prior dates, got {prior_dates}"
        )
    test = [
        dict(row) for row in rows if str(row["target_date"]) in TEST_DATES
    ]

    fit_audit: list[dict[str, Any]] = []
    candidate_models: list[str] = []
    for mode in ("prefix_accumulated", "trailing_fixed_cutoff"):
        for size in TRAIN_SIZES:
            train_dates = (
                prior_dates[:size]
                if mode == "prefix_accumulated"
                else prior_dates[-size:]
            )
            if max(train_dates) >= test_start:
                raise RuntimeError("training date entered common test window")
            train = [
                row
                for row in rows
                if str(row["target_date"]) in set(train_dates)
            ]
            name = model_id(mode, size)
            artifact = v6.fit_offset(
                train, v6.PHYSICAL_FEATURES, RIDGE
            )
            predictions = v6.predict_offset(artifact, test)
            for row, probability in zip(test, predictions):
                row[f"p_{name}"] = float(probability)
            beta = np.asarray(artifact["beta"], dtype=float)
            fit_audit.append(
                {
                    "model": name,
                    "mode": mode,
                    "train_dates": size,
                    "train_rows": len(train),
                    "train_start": min(train_dates),
                    "train_end": max(train_dates),
                    "test_start": test_start,
                    "test_end": max(TEST_DATES),
                    "test_rows": len(test),
                    "coefficient_l2": float(np.linalg.norm(beta)),
                    "intercept": float(beta[0]),
                    "beta_json": json.dumps(
                        artifact["beta"], separators=(",", ":")
                    ),
                }
            )
            candidate_models.append(name)

    models = [
        "market_prior_v7",
        "weather_standalone_v5",
        *candidate_models,
    ]
    scores = score_rows(test, models, clock_slice="all")
    scores.extend(score_rows(test, models, clock_slice="collector_exact"))

    expressions = v6.expression_rows(test, candidate_models)
    trades = v3.select_first_signal(
        expressions,
        args.raw_books,
        selection_policy="first_signal_per_model_target_date_bracket",
    )
    strategy = v3.strategy_summary(
        expressions,
        trades,
        split="common_test_2026_07_27_29",
        denominator_dates=list(TEST_DATES),
        model_names=candidate_models,
    )
    low_price = v6.low_price_scores(
        expressions, candidate_models, split="common_test"
    )
    stability = prediction_stability(test)
    coverage = coverage_audit(
        rows, v6.read_csv(args.v1_market_rows), args.raw_books
    )

    write_rows(args.out / "coverage_audit.csv", coverage)
    write_rows(args.out / "fit_audit.csv", fit_audit)
    write_rows(args.out / "prediction_stability.csv", stability)
    write_rows(args.out / "common_test_predictions.csv.gz", test)
    write_rows(args.out / "probability_learning_curve.csv", scores)
    write_rows(args.out / "expression_candidates.csv.gz", expressions)
    write_rows(args.out / "selected_trades.csv", trades)
    write_rows(args.out / "strategy_learning_curve.csv", strategy)
    write_rows(args.out / "low_price_learning_curve.csv", low_price)

    checkpoint = {
        str(row["model"]): row
        for row in scores
        if row["clock_slice"] == "all"
        and row["grain"] == "checkpoint"
    }
    exact_checkpoint = {
        str(row["model"]): row
        for row in scores
        if row["clock_slice"] == "collector_exact"
        and row["grain"] == "checkpoint"
    }
    summary = {
        "schema_version": "tokyo_market_anchor_training_curve_v7",
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
        "target": (
            "effect of prior PIT market target-date count on the fixed v6 "
            "physical-offset model"
        ),
        "holdout_2026_07_16_30_recoverable": False,
        "reason": (
            "only one pre-holdout raw-book date (2026-07-15), with four "
            "archive-reconstructed joins and zero collector-exact rows"
        ),
        "strict_pre_holdout_market_dates": 0,
        "common_test_dates": list(TEST_DATES),
        "common_test_rows": len(test),
        "common_test_collector_exact_rows": sum(
            row["availability_clock_class"]
            == "collector_exact_hash_verified"
            for row in test
        ),
        "training_sizes": list(TRAIN_SIZES),
        "candidate_search_k": 1,
        "specification_fixed_after_v6": True,
        "development_only": True,
        "clean_forward_start": "2026-08-01",
        "checkpoint_brier": {
            model: float(row["brier"])
            for model, row in checkpoint.items()
        },
        "collector_exact_checkpoint_brier": {
            model: float(row["brier"])
            for model, row in exact_checkpoint.items()
        },
        "input_hashes": {
            "prepared_rows_semantic_sha256": v6.semantic_hash(
                args.prepared_rows
            ),
            "v1_market_rows_semantic_sha256": v6.semantic_hash(
                args.v1_market_rows
            ),
        },
        "gate_status": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "NA",
            "conclusion": "inconclusive_continue_collection",
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
