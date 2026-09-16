#!/usr/bin/env python3
"""Tokyo current-bracket stay/break model and zero-notional replay.

At every JMA checkpoint the model estimates P(final > current).  The market
expression is restricted to the current exact-bracket contract: YES means the
current bracket survives; NO means the final maximum leaves it upward.  Each
target-date/bracket can open once and cannot add.  No live behavior changes.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import joblib
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
    research_tokyo_continuous_ladder_probability_v2 as v2,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    date_weights,
    write_rows,
)


PRETRAIN_END = "2025-06-30"
CALIBRATION_START = "2025-07-01"
CALIBRATION_END = "2025-12-31"
TRAIN_CUTOFF = "2026-07-15"
DIAGNOSTIC_START = "2026-07-16"
DIAGNOSTIC_END = "2026-07-30"
EDGE_THRESHOLD = 0.02
MODEL_NAMES = (
    "binary_checkpoint_hgb_v5",
    "binary_multigrain_hgb_v5",
)
CHAMPION = "binary_multigrain_hgb_v5"
PARAMS = dict(v3.FROZEN_PARAMS)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_current_break_binary_v5"
)
EPS = 1e-8


def date_equal_binary_loss(
    rows: list[dict[str, Any]],
    p_break: np.ndarray,
    *,
    metric: str,
) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, probability in zip(rows, p_break):
        label = int(row["binary_leave_current"])
        probability = min(max(float(probability), EPS), 1.0 - EPS)
        if metric == "brier":
            value = (probability - label) ** 2
        elif metric == "logloss":
            value = -(
                label * math.log(probability)
                + (1 - label) * math.log(1.0 - probability)
            )
        else:
            raise ValueError(metric)
        daily[str(row["target_date"])].append(value)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def apply_binary_temperature(
    p_break: np.ndarray, temperature: float
) -> np.ndarray:
    probabilities = np.column_stack([1.0 - p_break, p_break])
    return v1.apply_temperature(probabilities, temperature)[:, 1]


def choose_binary_temperature(
    rows: list[dict[str, Any]], p_break: np.ndarray
) -> float:
    grid = np.linspace(0.7, 2.0, 27)
    return float(
        min(
            grid,
            key=lambda value: date_equal_binary_loss(
                rows,
                apply_binary_temperature(p_break, float(value)),
                metric="logloss",
            ),
        )
    )


def objective_weights(
    rows: list[dict[str, Any]], model_name: str
) -> np.ndarray:
    if model_name == "binary_checkpoint_hgb_v5":
        return date_weights(rows)
    if model_name == "binary_multigrain_hgb_v5":
        return v2.multigrain_weights(rows)
    raise ValueError(model_name)


def raw_break_probability(
    model: Any, rows: list[dict[str, Any]]
) -> np.ndarray:
    return v2.probability_for_class(model, rows, 1)


def fit_artifacts(
    all_train: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    pretrain = [
        row for row in all_train if str(row["target_date"]) <= PRETRAIN_END
    ]
    calibration = [
        row
        for row in all_train
        if CALIBRATION_START
        <= str(row["target_date"])
        <= CALIBRATION_END
    ]
    if not pretrain or not calibration:
        raise RuntimeError("binary pretrain/calibration split is empty")

    artifacts: dict[str, dict[str, Any]] = {}
    validation_scores: list[dict[str, Any]] = []
    for name in MODEL_NAMES:
        selection_model = v2.fit_weighted_hgb(
            pretrain,
            "binary_leave_current",
            objective_weights(pretrain, name),
            PARAMS,
        )
        raw_calibration = raw_break_probability(
            selection_model, calibration
        )
        temperature = choose_binary_temperature(
            calibration, raw_calibration
        )
        calibrated = apply_binary_temperature(
            raw_calibration, temperature
        )
        for grain in v2.GRAINS:
            selected = [
                row
                for row in calibration
                if int(row[f"is_{grain}"]) == 1
            ]
            indexes = np.asarray(
                [
                    index
                    for index, row in enumerate(calibration)
                    if int(row[f"is_{grain}"]) == 1
                ],
                dtype=int,
            )
            values = calibrated[indexes]
            validation_scores.append(
                binary_score_record(
                    selected,
                    values,
                    split="2025H2_calibration",
                    model=name,
                    grain=grain,
                )
            )
        final_model = v2.fit_weighted_hgb(
            all_train,
            "binary_leave_current",
            objective_weights(all_train, name),
            PARAMS,
        )
        artifacts[name] = {
            "model": final_model,
            "temperature": temperature,
            "objective": (
                "date_equal_checkpoint"
                if name == "binary_checkpoint_hgb_v5"
                else "date_equal_one_third_each_checkpoint_transition_state_entry"
            ),
        }
    return artifacts, validation_scores


def predict_break(
    artifacts: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, np.ndarray]:
    output = {}
    for name, artifact in artifacts.items():
        raw = raw_break_probability(artifact["model"], rows)
        output[name] = apply_binary_temperature(
            raw, float(artifact["temperature"])
        )
    return output


def binary_score_record(
    rows: list[dict[str, Any]],
    p_break: np.ndarray,
    *,
    split: str,
    model: str,
    grain: str,
) -> dict[str, Any]:
    p_current = 1.0 - p_break
    record = v1.binary_metrics(
        rows, p_current, split=split, model=model
    )
    labels = np.asarray(
        [int(row["binary_leave_current"]) for row in rows], dtype=int
    )
    predictions = (p_break >= 0.5).astype(int)
    daily_accuracy: dict[str, list[int]] = defaultdict(list)
    for row, expected, actual in zip(rows, predictions, labels):
        daily_accuracy[str(row["target_date"])].append(
            int(expected == actual)
        )
    record.update(
        {
            "grain": grain,
            "break_rate": float(
                np.mean(
                    [
                        np.mean(
                            [
                                int(row["binary_leave_current"])
                                for row in rows
                                if str(row["target_date"])
                                == target_date
                            ]
                        )
                        for target_date in daily_accuracy
                    ]
                )
            ),
            "mean_p_break": float(
                np.mean(
                    [
                        np.mean(
                            [
                                float(value)
                                for row, value in zip(rows, p_break)
                                if str(row["target_date"])
                                == target_date
                            ]
                        )
                        for target_date in daily_accuracy
                    ]
                )
            ),
            "accuracy": float(
                np.mean(
                    [np.mean(values) for values in daily_accuracy.values()]
                )
            ),
        }
    )
    return record


def score_by_grain(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    *,
    split: str,
) -> list[dict[str, Any]]:
    output = []
    for model, values in predictions.items():
        for grain in v2.GRAINS:
            indexes = np.asarray(
                [
                    index
                    for index, row in enumerate(rows)
                    if int(row[f"is_{grain}"]) == 1
                ],
                dtype=int,
            )
            selected = [rows[index] for index in indexes]
            output.append(
                binary_score_record(
                    selected,
                    values[indexes],
                    split=split,
                    model=model,
                    grain=grain,
                )
            )
    return output


def calibration_rows(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    boundaries = (0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 1.01)
    output = []
    for model, values in predictions.items():
        for low, high in zip(boundaries[:-1], boundaries[1:]):
            indexes = np.asarray(
                [
                    index
                    for index, value in enumerate(values)
                    if low <= float(value) < high
                ],
                dtype=int,
            )
            if not len(indexes):
                continue
            output.append(
                {
                    "model": model,
                    "p_break_bin": f"[{low:.2f},{min(high, 1.0):.2f})",
                    "states": len(indexes),
                    "target_dates": len(
                        {str(rows[index]["target_date"]) for index in indexes}
                    ),
                    "mean_p_break": float(np.mean(values[indexes])),
                    "actual_break_rate": float(
                        np.mean(
                            [
                                int(rows[index]["binary_leave_current"])
                                for index in indexes
                            ]
                        )
                    ),
                }
            )
    return output


def persist_artifacts(
    out: Path,
    artifacts: dict[str, dict[str, Any]],
    feature_hash: str,
) -> dict[str, dict[str, str]]:
    model_dir = out / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    output = {}
    for name, artifact in artifacts.items():
        spec = {
            "schema_version": "tokyo_current_break_binary_v5",
            "city": "Tokyo",
            "target_id": "eod_leave_current_exact_bracket",
            "model": name,
            "features": list(v1.MODEL_FEATURES),
            "feature_semantic_sha256": feature_hash,
            "pretrain_end": PRETRAIN_END,
            "temperature_selection_window": [
                CALIBRATION_START,
                CALIBRATION_END,
            ],
            "training_cutoff": TRAIN_CUTOFF,
            "diagnostic_window": [DIAGNOSTIC_START, DIAGNOSTIC_END],
            "diagnostic_labels_used_in_fit": False,
            "parameters": PARAMS,
            "temperature": float(artifact["temperature"]),
            "objective": artifact["objective"],
            "strict_pit_forecast": False,
        }
        spec_bytes = (v2.canonical_json(spec) + "\n").encode("utf-8")
        spec_path = model_dir / f"{name}.spec.json"
        spec_path.write_bytes(spec_bytes)
        model_path = model_dir / f"{name}.joblib"
        joblib.dump(artifact, model_path, compress=3)
        output[name] = {
            "model_spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
            "model_artifact_sha256": hashlib.sha256(
                model_path.read_bytes()
            ).hexdigest(),
        }
    return output


def long_predictions(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    exact: dict[str, datetime],
    hashes: dict[str, dict[str, str]],
    feature_hash: str,
) -> Iterable[dict[str, Any]]:
    for index, row in enumerate(rows):
        observed = v1.parse_ts(str(row["decision_ts_utc"]))
        exact_available = exact.get(observed.isoformat())
        for model, values in predictions.items():
            p_break = float(values[index])
            distribution_id = hashlib.sha256(
                (
                    f"{row['state_id']}|{model}|"
                    f"{hashes[model]['model_artifact_sha256']}"
                ).encode()
            ).hexdigest()
            for outcome, probability, label in (
                (
                    "stay_current_exact",
                    1.0 - p_break,
                    1 - int(row["binary_leave_current"]),
                ),
                (
                    "break_current_exact",
                    p_break,
                    int(row["binary_leave_current"]),
                ),
            ):
                yield {
                    "schema_version": "weather_city_probability_prediction_v3_forward",
                    "city": "Tokyo",
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "prediction_ts_utc": (
                        exact_available or observed
                    ).isoformat(),
                    "availability_clock_class": (
                        "collector_exact_hash_verified"
                        if exact_available is not None
                        else "archive_observation_timestamp_not_first_seen"
                    ),
                    "state_id": row["state_id"],
                    "path_phase": row["path_phase"],
                    "target_id": "eod_leave_current_exact_bracket",
                    "distribution_id": distribution_id,
                    "outcome_id": outcome,
                    "p_model": probability,
                    "label": label,
                    "current_bracket": row["current_bracket"],
                    "model_id": model,
                    "training_cutoff": TRAIN_CUTOFF,
                    "diagnostic_labels_used_in_fit": 0,
                    "feature_semantic_sha256": feature_hash,
                    **hashes[model],
                    "pit_provenance": row["pit_provenance"],
                }


def current_contract_candidates(
    joined: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for row in joined:
        if int(row["settlement_lower_bound_violation"]):
            continue
        current = int(row["current_bracket"])
        quote = json.loads(str(row["quotes_json"])).get(str(current))
        if not quote:
            continue
        yes_ask = v1.finite(quote.get("ask"))
        yes_bid = v1.finite(quote.get("bid"))
        for model in MODEL_NAMES:
            p_stay, p_break = json.loads(
                str(row[f"{model}_distribution_json"])
            )
            for side, p_win, ask in (
                ("YES", float(p_stay), yes_ask),
                (
                    "NO",
                    float(p_break),
                    None if yes_bid is None else 1.0 - yes_bid,
                ),
            ):
                if ask is None or not 0 < float(ask) < 1:
                    continue
                fee = v3.official_fee_per_share(float(ask))
                candidate = dict(row)
                candidate.update(
                    {
                        "model": model,
                        "expression_delta": 0,
                        "expression_bracket": str(current),
                        "side": side,
                        "p_win": p_win,
                        "selected_side_ask": float(ask),
                        "fee_per_share": fee,
                        "fee_adjusted_edge": p_win - float(ask) - fee,
                        "strategy_policy": (
                            "current_exact_stay_break_first_edge_ge_2pct"
                        ),
                        "trade_class": "research_counterfactual",
                    }
                )
                output.append(candidate)
    return output


def selected_probability_audit(
    trades: list[dict[str, Any]], *, split: str
) -> list[dict[str, Any]]:
    output = []
    for model in MODEL_NAMES:
        for side in ("ALL", "YES", "NO"):
            selected = [
                row
                for row in trades
                if row["model"] == model
                and int(row.get("five_share_executable", 0)) == 1
                and (side == "ALL" or row["side"] == side)
            ]
            if not selected:
                continue
            market_probabilities = []
            for row in selected:
                quote = json.loads(str(row["quotes_json"]))[
                    str(row["expression_bracket"])
                ]
                yes_mid = float(quote["mid"])
                market_probabilities.append(
                    yes_mid if row["side"] == "YES" else 1.0 - yes_mid
                )
            labels = np.asarray(
                [int(row["settled_win"]) for row in selected], dtype=float
            )
            model_probabilities = np.asarray(
                [float(row["p_win"]) for row in selected], dtype=float
            )
            market_values = np.asarray(market_probabilities, dtype=float)
            output.append(
                {
                    "split": split,
                    "model": model,
                    "side": side,
                    "trades": len(selected),
                    "target_dates": len(
                        {str(row["target_date"]) for row in selected}
                    ),
                    "actual_win_rate": float(np.mean(labels)),
                    "mean_model_p_win": float(
                        np.mean(model_probabilities)
                    ),
                    "mean_market_probability": float(
                        np.mean(market_values)
                    ),
                    "model_brier": float(
                        np.mean((model_probabilities - labels) ** 2)
                    ),
                    "market_brier": float(
                        np.mean((market_values - labels) ** 2)
                    ),
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=v1.FEATURE_ROWS)
    parser.add_argument(
        "--exact-first-seen", type=Path, default=v1.EXACT_FIRST_SEEN
    )
    parser.add_argument("--market-states", type=Path, default=v1.MARKET_STATES)
    parser.add_argument("--pm-history", type=Path, default=v1.PM_HISTORY)
    parser.add_argument("--raw-books", type=Path, default=v1.RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    raw = v1.read_rows(args.features)
    continuous = v2.annotate_grains(
        [
            row
            for row in v1.build_continuous_rows(raw)
            if row["remaining_rise_class"] is not None
        ]
    )
    train, diagnostic = v3.split_train_forward(
        continuous,
        train_cutoff=TRAIN_CUTOFF,
        forward_start=DIAGNOSTIC_START,
        forward_end=DIAGNOSTIC_END,
    )
    diagnostic_dates = sorted(
        {str(row["target_date"]) for row in diagnostic}
    )
    expected_dates = [
        date(2026, 7, day).isoformat() for day in range(16, 31)
    ]
    if diagnostic_dates != expected_dates:
        raise RuntimeError(
            f"diagnostic window is incomplete: {diagnostic_dates}"
        )

    artifacts, validation_scores = fit_artifacts(train)
    predictions_break = predict_break(artifacts, diagnostic)
    predictions_distribution = {
        model: np.column_stack([1.0 - values, values])
        for model, values in predictions_break.items()
    }
    diagnostic_scores = score_by_grain(
        diagnostic,
        predictions_break,
        split="2026-07-16_30_weather_model_diagnostic",
    )
    calibration = calibration_rows(diagnostic, predictions_break)

    exact = v1.exact_first_seen(args.exact_first_seen)
    feature_hash = v2.semantic_file_hash(args.features)
    hashes = persist_artifacts(args.out, artifacts, feature_hash)
    prediction_rows = list(
        long_predictions(
            diagnostic,
            predictions_break,
            exact,
            hashes,
            feature_hash,
        )
    )

    markets = v1.load_market_states(args.market_states)
    winners = v1.load_winners(
        args.pm_history, date(2026, 7, 16), date(2026, 7, 30)
    )
    joined = v3.join_market_asof_books(
        diagnostic,
        predictions_distribution,
        exact,
        markets,
        winners,
    )
    market_dates = sorted({str(row["target_date"]) for row in joined})
    exact_joined = [
        row
        for row in joined
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    exact_dates = sorted(
        {str(row["target_date"]) for row in exact_joined}
    )
    market_scores = v1.market_binary_scores(
        joined,
        MODEL_NAMES,
        "post_forward_binary_selector_diagnostic_market_available",
    )
    market_scores.extend(
        v1.market_binary_scores(
            exact_joined,
            MODEL_NAMES,
            "post_forward_binary_selector_diagnostic_collector_exact",
        )
    )

    candidates = current_contract_candidates(joined)
    trades = v3.select_first_signal(
        candidates,
        args.raw_books,
        selection_policy=(
            "first_signal_per_model_target_date_bracket"
        ),
    )
    exact_candidates = [
        row
        for row in candidates
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    exact_trades = [
        row
        for row in trades
        if row["availability_clock_class"]
        == "collector_exact_hash_verified"
    ]
    strategy_split = "post_forward_binary_selector_diagnostic"
    strategy_scores = v3.strategy_summary(
        candidates,
        trades,
        split=strategy_split,
        denominator_dates=diagnostic_dates,
        model_names=MODEL_NAMES,
    )
    strategy_scores.extend(
        v3.strategy_summary(
            exact_candidates,
            exact_trades,
            split=f"{strategy_split}_collector_exact",
            denominator_dates=diagnostic_dates,
            model_names=MODEL_NAMES,
        )
    )
    side_scores = v3.strategy_side_summary(
        trades, split=strategy_split, model_names=MODEL_NAMES
    )
    selected_audit = selected_probability_audit(
        trades, split=strategy_split
    )
    order_distribution = v3.order_distribution(
        trades, split=strategy_split
    )
    edge_distribution = v3.edge_distribution(
        trades, split=strategy_split
    )

    signal_funnel = [
        {
            "funnel": "signal",
            "stage": "diagnostic_weather_checkpoints",
            "unit": "state",
            "count": len(diagnostic),
            "target_dates": len(diagnostic_dates),
        },
        {
            "funnel": "signal",
            "stage": "market_joined_current_contract_expressions",
            "unit": "expression",
            "count": len(candidates),
            "target_dates": len(market_dates),
        },
        {
            "funnel": "signal",
            "stage": "first_date_bracket_signal_champion",
            "unit": "position",
            "count": sum(row["model"] == CHAMPION for row in trades),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in trades
                    if row["model"] == CHAMPION
                }
            ),
        },
        {
            "funnel": "evidence",
            "stage": "settled_pit_book_join",
            "unit": "book_time_state",
            "count": len(joined),
            "target_dates": len(market_dates),
        },
        {
            "funnel": "evidence",
            "stage": "collector_exact_book_join",
            "unit": "book_time_state",
            "count": len(exact_joined),
            "target_dates": len(exact_dates),
        },
        {
            "funnel": "evidence",
            "stage": "actual_fill",
            "unit": "fill",
            "count": 0,
            "target_dates": 0,
        },
    ]

    write_rows(args.out / "validation_model_scores.csv", validation_scores)
    write_rows(args.out / "diagnostic_model_scores.csv", diagnostic_scores)
    write_rows(args.out / "break_calibration.csv", calibration)
    write_rows(args.out / "prediction_long.csv.gz", prediction_rows)
    write_rows(args.out / "market_join_rows.csv.gz", joined)
    write_rows(args.out / "market_binary_scores.csv", market_scores)
    write_rows(args.out / "current_contract_candidates.csv.gz", candidates)
    write_rows(args.out / "selected_trades.csv", trades)
    write_rows(args.out / "selected_trades_exact.csv", exact_trades)
    write_rows(args.out / "strategy_summary.csv", strategy_scores)
    write_rows(args.out / "strategy_side_summary.csv", side_scores)
    write_rows(args.out / "selected_probability_audit.csv", selected_audit)
    write_rows(args.out / "order_probability_distribution.csv", order_distribution)
    write_rows(args.out / "order_edge_distribution.csv", edge_distribution)
    write_rows(args.out / "funnel.csv", signal_funnel)

    summary = {
        "schema_version": "tokyo_current_break_binary_v5",
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
        "target_id": "eod_leave_current_exact_bracket",
        "model_champion_fixed_by_design": CHAMPION,
        "candidate_models": list(MODEL_NAMES),
        "pretrain_end": PRETRAIN_END,
        "temperature_selection_window": [
            CALIBRATION_START,
            CALIBRATION_END,
        ],
        "training_cutoff": TRAIN_CUTOFF,
        "training_rows": len(train),
        "training_dates": len(
            {str(row["target_date"]) for row in train}
        ),
        "diagnostic_window": [DIAGNOSTIC_START, DIAGNOSTIC_END],
        "diagnostic_rows": len(diagnostic),
        "diagnostic_dates": len(diagnostic_dates),
        "diagnostic_labels_used_in_fit": False,
        "strategy_evaluation_status": (
            "post_forward_user_corrected_expression_diagnostic"
        ),
        "market_join_rows": len(joined),
        "market_join_dates": market_dates,
        "collector_exact_rows": len(exact_joined),
        "collector_exact_dates": exact_dates,
        "market_coverage_gap_dates": sorted(
            set(diagnostic_dates) - set(market_dates)
        ),
        "market_join_policy": (
            "book_snapshot_latest_available_weather_state"
        ),
        "max_weather_state_age_at_book_min": 30,
        "strategy_policy": {
            "expressions": ["current_exact_yes", "current_exact_no"],
            "edge_threshold": EDGE_THRESHOLD,
            "shares": v3.SHARES,
            "fee_rate": v3.FEE_RATE,
            "selection": (
                "first_signal_per_model_target_date_bracket"
            ),
            "add_on_allowed": False,
            "next_exact_enabled": False,
        },
        "strict_pit_forecast_available": False,
        "forecast_route": (
            "observation_only_binary_baseline_pending_pit_peak_ceiling"
        ),
        "feature_semantic_sha256": feature_hash,
        "model_hashes": hashes,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
