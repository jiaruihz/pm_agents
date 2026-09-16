"""Settlement replay for D-1 full-ladder shadow predictions."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from weather_model_evaluation.contracts import stable_sha256
from weather_model_evaluation.probability import ordinal_loss_values

from .runtime import PREDICTION_SCHEMA_VERSION, _safe_research_output, _write_jsonl


EVALUATION_SCHEMA_VERSION = "weather_d1_market_residual_settlement_replay_v1"


def _date_equal(frame: pd.DataFrame, column: str) -> float:
    return float(frame.groupby("target_date", sort=True)[column].mean().mean())


def _bootstrap_daily_delta(values: pd.Series, *, samples: int = 4000, seed: int = 20260806) -> dict[str, float]:
    array = values.to_numpy(dtype=float)
    if not len(array):
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan")}
    rng = np.random.default_rng(seed)
    draws = rng.choice(array, size=(samples, len(array)), replace=True).mean(axis=1)
    return {
        "mean": float(array.mean()),
        "ci_low": float(np.quantile(draws, 0.025)),
        "ci_high": float(np.quantile(draws, 0.975)),
    }


def evaluate_settlements(
    predictions: Sequence[Mapping[str, Any]],
    settlements: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settlement_index: dict[tuple[str, str, str], str] = {}
    for row in settlements:
        key = (str(row["city"]), str(row["target_date"]), str(row["target_id"]))
        winner = str(row["winning_expression_id"])
        if key in settlement_index and settlement_index[key] != winner:
            raise ValueError(f"conflicting settlement labels for {key}")
        settlement_index[key] = winner
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    status = Counter()
    for row in predictions:
        if row.get("schema_version") != PREDICTION_SCHEMA_VERSION or row.get("record_kind") != "prediction":
            continue
        key = (str(row["checkpoint_id"]), str(row["city"]), str(row["target_date"]), str(row["base_target_id"]))
        grouped.setdefault(key, []).append(row)
    labeled_rows: list[dict[str, Any]] = []
    checkpoint_scores: list[dict[str, Any]] = []
    for (checkpoint_id, city, target_date, target_id), rows in sorted(grouped.items()):
        winner = settlement_index.get((city, target_date, target_id))
        if winner is None:
            status["settlement_missing"] += 1
            continue
        ordered = sorted(rows, key=lambda row: int(row["native_order"]))
        expressions = [str(row["expression_id"]) for row in ordered]
        if len(expressions) != len(set(expressions)):
            status["duplicate_expression_rows"] += 1
            continue
        if any(row.get("posterior_probability") is None for row in ordered):
            status["model_not_scorable"] += 1
            continue
        if winner not in expressions:
            status["winner_outside_captured_ladder"] += 1
            continue
        label = expressions.index(winner)
        posterior = np.array([[float(row["posterior_probability"]) for row in ordered]])
        market = np.array([[float(row["market_probability"]) for row in ordered]])
        model_logloss = float(ordinal_loss_values([label], posterior, metric="logloss")[0])
        market_logloss = float(ordinal_loss_values([label], market, metric="logloss")[0])
        model_brier = float(ordinal_loss_values([label], posterior, metric="brier")[0])
        market_brier = float(ordinal_loss_values([label], market, metric="brier")[0])
        model_rps = float(ordinal_loss_values([label], posterior, metric="rps")[0])
        market_rps = float(ordinal_loss_values([label], market, metric="rps")[0])
        checkpoint_scores.append({
            "checkpoint_id": checkpoint_id,
            "city": city,
            "target_date": target_date,
            "target_id": target_id,
            "winner_expression_id": winner,
            "model_logloss": model_logloss,
            "market_logloss": market_logloss,
            "model_brier": model_brier,
            "market_brier": market_brier,
            "model_rps": model_rps,
            "market_rps": market_rps,
            "logloss_delta": model_logloss - market_logloss,
            "brier_delta": model_brier - market_brier,
            "rps_delta": model_rps - market_rps,
        })
        for row in ordered:
            value = dict(row)
            value["label"] = int(row["expression_id"] == winner)
            value["winning_expression_id"] = winner
            labeled_rows.append(value)
        status["scoreable"] += 1
    frame = pd.DataFrame(checkpoint_scores)
    if frame.empty:
        quality: dict[str, Any] = {"status": "not_available", "checkpoint_rows": 0}
        bootstrap: dict[str, Any] = {"status": "not_available"}
    else:
        quality = {
            "status": "available",
            "checkpoint_rows": int(len(frame)),
            "target_dates": int(frame["target_date"].nunique()),
            "model": {
                metric: _date_equal(frame, f"model_{metric}") for metric in ("logloss", "brier", "rps")
            },
            "market": {
                metric: _date_equal(frame, f"market_{metric}") for metric in ("logloss", "brier", "rps")
            },
            "model_minus_market": {
                metric: _date_equal(frame, f"{metric}_delta") for metric in ("logloss", "brier", "rps")
            },
        }
        daily = frame.groupby("target_date", sort=True)[["logloss_delta", "brier_delta", "rps_delta"]].mean()
        bootstrap = {
            metric: _bootstrap_daily_delta(daily[f"{metric}_delta"])
            for metric in ("logloss", "brier", "rps")
        }
    report = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "coverage": {
            "prediction_rows": len(predictions),
            "checkpoint_universe": len(grouped),
            "settlement_rows": len(settlements),
            "status": dict(sorted(status.items())),
        },
        "same_denominator_probability_quality": quality,
        "target_date_block_bootstrap_model_minus_market": bootstrap,
        "execution": {
            "status": "not_available_shadow",
            "orders": 0,
            "fills": 0,
            "no_order_placed": True,
        },
    }
    report["report_hash"] = stable_sha256(report)
    return report, labeled_rows


def write_evaluation(
    predictions: Sequence[Mapping[str, Any]],
    settlements: Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir = _safe_research_output(output_dir)
    report, labeled = evaluate_settlements(predictions, settlements)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "labeled_predictions.jsonl", labeled)
    (output_dir / "settlement_evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
