#!/usr/bin/env python3
"""Freeze the minimal Amsterdam previous-running-max NO reaction model.

The design is fixed: target-date-equal weighted ridge (alpha=1), market-only
MR0 and one additional frozen next-break probability for B2/M1/M2.  The last
available target date is a development audit only; the final coefficients are
refit on all pre-freeze dates for a new prospective zero-notional epoch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_city_runtime.amsterdam_action_shadow_v1 import (  # noqa: E402
    POLICY_ARMS,
    canonical_hash,
    file_sha256,
    training_observation,
)


DEFAULT_LEDGER = Path(
    "/Volumes/jrs/pm_agents/runtime/research/wcir_amsterdam_frozen_v2/"
    "epoch=wcir_amsterdam_score_only_v2_bb04a8acbeafd66b/common_ledger_snapshot.json"
)
ALPHA = 1.0
BASE_FEATURES = (
    "entry_effective_cost_per_share",
    "entry_spread",
    "minutes_to_expected_routine",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _features(arm: str) -> tuple[str, ...]:
    return BASE_FEATURES if arm == "MR0" else (*BASE_FEATURES, f"{arm.lower()}_p_next_break")


def _weights(rows: list[dict[str, Any]]) -> np.ndarray:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["target_date"]] = counts.get(row["target_date"], 0) + 1
    raw = np.array([1.0 / counts[row["target_date"]] for row in rows], dtype=float)
    return raw * (len(raw) / raw.sum())


def _fit(rows: list[dict[str, Any]], arm: str) -> tuple[dict[str, Any], Ridge, np.ndarray, np.ndarray]:
    names = _features(arm)
    matrix = np.array([[float(row[name]) for name in names] for row in rows], dtype=float)
    target = np.array([float(row["realized_markout_per_share"]) for row in rows], dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales < 1e-9] = 1.0
    model = Ridge(alpha=ALPHA).fit((matrix - means) / scales, target, sample_weight=_weights(rows))
    payload = {
        "feature_names": list(names),
        "feature_means": means.tolist(),
        "feature_scales": scales.tolist(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "alpha": ALPHA,
    }
    return payload, model, means, scales


def _predict(rows: list[dict[str, Any]], arm: str, model: Ridge, means: np.ndarray, scales: np.ndarray) -> np.ndarray:
    names = _features(arm)
    matrix = np.array([[float(row[name]) for name in names] for row in rows], dtype=float)
    return model.predict((matrix - means) / scales)


def _date_equal_metric(rows: list[dict[str, Any]], predictions: np.ndarray) -> dict[str, Any]:
    by_date: dict[str, list[tuple[float, float]]] = {}
    for row, prediction in zip(rows, predictions):
        by_date.setdefault(row["target_date"], []).append(
            (float(prediction), float(row["realized_markout_per_share"]))
        )
    mae = []
    rmse = []
    for values in by_date.values():
        errors = np.array([prediction - actual for prediction, actual in values])
        mae.append(float(np.mean(np.abs(errors))))
        rmse.append(float(np.sqrt(np.mean(errors ** 2))))
    return {
        "rows": len(rows),
        "target_dates": len(by_date),
        "date_equal_mae_per_share": float(np.mean(mae)) if mae else None,
        "date_equal_rmse_per_share": float(np.mean(rmse)) if rmse else None,
    }


def _policy_eval(rows: list[dict[str, Any]], predictions: np.ndarray, *, shares: int = 5) -> dict[str, Any]:
    candidates = [
        (row, float(expected))
        for row, expected in zip(rows, predictions)
        if row["opportunity_matched"] and float(expected) > 0.0
    ]
    candidates.sort(key=lambda item: (item[0]["entry_checkpoint_at_utc"], item[0]["prediction_row_id"]))
    selected: list[tuple[dict[str, Any], float]] = []
    seen: set[tuple[str, str]] = set()
    for row, expected in candidates:
        key = (row["target_date"], row["native_bracket"])
        if key in seen:
            continue
        seen.add(key)
        selected.append((row, expected))
    cost = sum(float(row["entry_effective_cost_per_share"]) * shares for row, _ in selected)
    pnl = sum(float(row["realized_markout_per_share"]) * shares for row, _ in selected)
    return {
        "raw_positive_candidates": len(candidates),
        "selected_signals": len(selected),
        "selected_target_dates": len({row["target_date"] for row, _ in selected}),
        "wins": sum(float(row["realized_markout_per_share"]) > 0 for row, _ in selected),
        "counterfactual_cost_usd": cost,
        "fee_adjusted_markout_pnl_usd": pnl,
        "fee_adjusted_markout_roi": pnl / cost if cost else None,
        "rows": [
            {
                "prediction_row_id": row["prediction_row_id"],
                "target_date": row["target_date"],
                "native_bracket": row["native_bracket"],
                "expected_markout_per_share": expected,
                "realized_markout_per_share": row["realized_markout_per_share"],
            }
            for row, expected in selected
        ],
    }


def build_artifact(ledger_path: Path, *, generated_at_utc: str) -> dict[str, Any]:
    joined = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    for item in joined:
        row, reason = training_observation(item)
        if row is None:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        else:
            rows.append(row)
    rows.sort(key=lambda row: (row["entry_checkpoint_at_utc"], row["prediction_row_id"]))
    dates = sorted({row["target_date"] for row in rows})
    if len(dates) < 2:
        raise RuntimeError("reaction model requires non-overlapping target dates")
    train_dates, eval_dates = set(dates[:-1]), {dates[-1]}
    train = [row for row in rows if row["target_date"] in train_dates]
    evaluation = [row for row in rows if row["target_date"] in eval_dates]
    models: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    for arm in POLICY_ARMS:
        _, development, means, scales = _fit(train, arm)
        predictions = _predict(evaluation, arm, development, means, scales)
        final_payload, _, _, _ = _fit(rows, arm)
        models[arm] = final_payload
        audit[arm] = {
            **_date_equal_metric(evaluation, predictions),
            "policy": _policy_eval(evaluation, predictions),
        }
    model_core = {
        "schema_version": "wcir_amsterdam_prevmax_no_reaction_ridge_v1",
        "fit_protocol": "target_date_equal_weighted_ridge_alpha_1_last_date_development_audit_final_refit_all_pre_freeze_dates",
        "target": "official_plus_30s_direct_no_sell_5_effective_per_share_minus_entry_direct_no_buy_5_effective_per_share",
        "input_snapshot_sha256": file_sha256(ledger_path),
        "source_model_epoch_id": "wcir_amsterdam_score_only_v2_bb04a8acbeafd66b",
        "generated_at_utc": generated_at_utc,
        "training_rows": len(rows),
        "training_target_dates": dates,
        "training_first_entry_at_utc": rows[0]["entry_checkpoint_at_utc"],
        "training_last_entry_at_utc": rows[-1]["entry_checkpoint_at_utc"],
        "development_train_target_dates": sorted(train_dates),
        "development_eval_target_dates": sorted(eval_dates),
        "opportunity_training_rows": sum(bool(row["opportunity_matched"]) for row in rows),
        "extraction_failure_reasons": reasons,
        "models": models,
        "development_audit": audit,
        "selection_rule": "opportunity_matched AND expected_after_fee_markout_per_share > 0; first per target_date/native_bracket/arm",
        "orders": 0,
        "fills": 0,
        "notional": 0,
        "live_eligible": False,
    }
    return {**model_core, "artifact_id": canonical_hash(model_core)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--generated-at-utc", default=None)
    args = parser.parse_args()
    artifact = build_artifact(args.ledger, generated_at_utc=args.generated_at_utc or _now())
    encoded = json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
