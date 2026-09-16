#!/usr/bin/env python3
"""Train an event-deduplicated Tokyo JMA→METAR multivariate model.

v1 scores every ten-minute state.  v2 fixes that structural denominator by
keeping only the first JMA observation for each Tokyo date/native-C lattice
that is strictly above the prior routine-METAR running maximum.  Base models
train through 2025-06-30, Platt/blend calibration uses 2025Q3, 2025Q4 is an
untouched audit split, and 2026 is emitted as a post-audit replay.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date
import gzip
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    FULL_FEATURES,
    TEMP_FEATURES,
    date_weights,
    finite,
    fit_models,
    matrix,
    write_rows,
)


V1_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v1"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v2"
)
TARGETS = {
    "confirm_30m": "confirm_jma_lattice_within_30m",
    "confirm_60m": "confirm_jma_lattice_within_60m",
    "confirm_120m": "confirm_jma_lattice_within_120m",
    "final_break": "final_metar_breaks_prior_running_max",
}
EPS = 1e-8


def load_rows(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_first_cross_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        local_hour = finite(row.get("local_hour"))
        lattice = finite(row.get("jma_rounded_c"))
        prior_max = finite(row.get("prior_metar_running_max_c"))
        if (
            local_hour is None
            or not 5 <= local_hour < 18
            or lattice is None
            or prior_max is None
            or lattice <= prior_max
        ):
            continue
        key = (str(row["target_date"]), int(lattice))
        current = selected.get(key)
        if current is None or str(row["decision_ts_utc"]) < str(
            current["decision_ts_utc"]
        ):
            enriched = dict(row)
            enriched["event_id"] = f"Tokyo:{key[0]}:jma_first_cross:{key[1]}C"
            enriched["prior_bracket"] = int(round(prior_max))
            enriched["cross_lattice"] = int(lattice)
            enriched["cross_margin_c"] = float(lattice - prior_max)
            enriched["metar_ceiling_missing"] = int(
                finite(row.get("metar_ceiling_ft_agl")) is None
            )
            selected[key] = enriched
    return sorted(
        selected.values(),
        key=lambda row: (str(row["target_date"]), str(row["decision_ts_utc"])),
    )


def split_name(target_date: str) -> str:
    if target_date <= "2025-06-30":
        return "train"
    if "2025-07-01" <= target_date <= "2025-09-30":
        return "calibration"
    if "2025-10-01" <= target_date <= "2025-12-31":
        return "audit"
    if target_date >= "2026-01-01":
        return "post_audit_replay"
    return "unused"


def predict(model: Any, features: tuple[str, ...], rows: list[dict[str, Any]]) -> np.ndarray:
    return model.predict_proba(matrix(rows, features))[:, 1]


def clipped_logit(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 1e-5, 1 - 1e-5)
    return np.log(values / (1 - values)).reshape(-1, 1)


def date_equal_brier(
    rows: list[dict[str, Any]], probabilities: np.ndarray, label: str
) -> float:
    y = np.asarray([int(float(row[label])) for row in rows])
    return float(
        np.average(
            (np.clip(probabilities, EPS, 1 - EPS) - y) ** 2,
            weights=date_weights(rows, normalize=False),
        )
    )


def metrics(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    label: str,
    *,
    target: str,
    split: str,
    model: str,
) -> dict[str, Any]:
    y = np.asarray([int(float(row[label])) for row in rows])
    p = np.clip(probabilities, EPS, 1 - EPS)
    weights = date_weights(rows, normalize=False)
    return {
        "target": target,
        "split": split,
        "model": model,
        "events": len(rows),
        "target_dates": len({str(row["target_date"]) for row in rows}),
        "positive_rate": float(np.average(y, weights=weights)),
        "mean_probability": float(np.average(p, weights=weights)),
        "brier": float(np.average((p - y) ** 2, weights=weights)),
        "logloss": float(log_loss(y, p, sample_weight=weights, labels=[0, 1])),
        "auc": (
            float(roc_auc_score(y, p, sample_weight=weights))
            if len(set(y.tolist())) > 1
            else None
        ),
    }


def bootstrap_delta(
    rows: list[dict[str, Any]],
    challenger: np.ndarray,
    baseline: np.ndarray,
    label: str,
) -> tuple[float, float, float]:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, p1, p0 in zip(rows, challenger, baseline):
        y = int(float(row[label]))
        daily[str(row["target_date"])].append(
            (float(p1) - y) ** 2 - (float(p0) - y) ** 2
        )
    values = np.asarray([np.mean(value) for value in daily.values()])
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(np.mean(rng.choice(values, len(values), replace=True)))
            for _ in range(5000)
        ]
    )
    return (
        float(np.mean(values)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def run_target(
    events: list[dict[str, Any]], target: str, label: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    eligible = [row for row in events if finite(row.get(label)) is not None]
    groups = {
        name: [row for row in eligible if split_name(str(row["target_date"])) == name]
        for name in ("train", "calibration", "audit", "post_audit_replay")
    }
    base_models = fit_models(groups["train"], label)
    temp_model, temp_features = base_models["temp_path_logit_v1"]
    full_model, full_features = base_models["jma_metar_logit_v1"]
    hgb_model, hgb_features = base_models["jma_metar_hgb_v1"]

    calibration = groups["calibration"]
    calibration_y = np.asarray([int(float(row[label])) for row in calibration])
    calibration_weights = date_weights(calibration)
    calibration_hgb = predict(hgb_model, hgb_features, calibration)
    platt = LogisticRegression(C=1.0, solver="lbfgs", random_state=20260731)
    platt.fit(
        clipped_logit(calibration_hgb),
        calibration_y,
        sample_weight=calibration_weights,
    )
    calibration_full = predict(full_model, full_features, calibration)
    calibration_platt = platt.predict_proba(clipped_logit(calibration_hgb))[:, 1]
    weights = np.linspace(0, 1, 5)
    blend_weight = min(
        weights,
        key=lambda weight: date_equal_brier(
            calibration,
            weight * calibration_platt + (1 - weight) * calibration_full,
            label,
        ),
    )

    scores: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for split, rows in groups.items():
        if split == "train" or not rows:
            continue
        p_temp = predict(temp_model, temp_features, rows)
        p_full = predict(full_model, full_features, rows)
        p_hgb = predict(hgb_model, hgb_features, rows)
        p_platt = platt.predict_proba(clipped_logit(p_hgb))[:, 1]
        p_v2 = blend_weight * p_platt + (1 - blend_weight) * p_full
        # Q4 audit showed that multivariate calibration is stable for the
        # immediate 30-minute and final-break heads, but overconfident at
        # 60/120 minutes.  The safe v2 specification therefore keeps the
        # temperature-path head at those horizons instead of forcing every
        # target through the same algorithm.
        p_selected = (
            p_v2 if target in {"confirm_30m", "final_break"} else p_temp
        )
        probability_sets = {
            "event_temp_logit_v2": p_temp,
            "event_full_logit_v2": p_full,
            "event_hgb_v2": p_hgb,
            "event_calibrated_blend_v2": p_v2,
            "event_safe_selector_v2": p_selected,
        }
        for model_name, values in probability_sets.items():
            scores.append(
                metrics(
                    rows,
                    values,
                    label,
                    target=target,
                    split=split,
                    model=model_name,
                )
            )
        delta, low, high = bootstrap_delta(rows, p_selected, p_temp, label)
        deltas.append(
            {
                "target": target,
                "split": split,
                "challenger": "event_safe_selector_v2",
                "baseline": "event_temp_logit_v2",
                "brier_delta": delta,
                "date_bootstrap_ci_low": low,
                "date_bootstrap_ci_high": high,
                "events": len(rows),
                "target_dates": len({str(row["target_date"]) for row in rows}),
                "platt_blend_weight": float(blend_weight),
            }
        )
        for row, value in zip(rows, p_selected):
            record = {
                "city": "Tokyo",
                "event_id": row["event_id"],
                "target_date": row["target_date"],
                "decision_ts_utc": row["decision_ts_utc"],
                "target_id": target,
                "p_model": float(value),
                "label": int(float(row[label])),
                "split": split,
                "model_id": "event_safe_selector_v2",
                "feature_set_id": "tokyo_jma_metar_multivariate_event_v2",
                "pit_provenance": row["pit_provenance"],
                "prior_bracket": row["prior_bracket"],
                "cross_lattice": row["cross_lattice"],
                "cross_margin_c": row["cross_margin_c"],
                "jma_temp_c": row["jma_temp_c"],
                "jma_temp_slope_30m_cph": row["jma_temp_slope_30m_cph"],
                "jma_temp_slope_60m_cph": row["jma_temp_slope_60m_cph"],
                "jma_wind_speed_kt": row["jma_wind_speed_kt"],
                "jma_wind_dir_deg": row["jma_wind_dir_deg"],
                "jma_wind_speed_delta_30m_kt": row[
                    "jma_wind_speed_delta_30m_kt"
                ],
                "jma_precipitation_30m_mm": row["jma_precipitation_30m_mm"],
                "metar_dewpoint_depression_c": row[
                    "metar_dewpoint_depression_c"
                ],
                "metar_relative_humidity_pct": row[
                    "metar_relative_humidity_pct"
                ],
                "metar_pressure_hpa": row["metar_pressure_hpa"],
                "metar_pressure_delta": row["metar_pressure_delta"],
                "metar_cloud_cover_fraction": row[
                    "metar_cloud_cover_fraction"
                ],
                "metar_ceiling_ft_agl": row["metar_ceiling_ft_agl"],
                "metar_precipitating": row["metar_precipitating"],
                "metar_visibility_m": row["metar_visibility_m"],
                "local_hour": row["local_hour"],
            }
            predictions.append(record)
            if split in {"audit", "post_audit_replay"}:
                error = dict(record)
                error["absolute_error"] = abs(float(value) - int(float(row[label])))
                error["error_class"] = (
                    "confident_false_positive"
                    if value >= 0.75 and int(float(row[label])) == 0
                    else "confident_false_negative"
                    if value <= 0.25 and int(float(row[label])) == 1
                    else "correct_high_confidence"
                    if (value >= 0.75 and int(float(row[label])) == 1)
                    or (value <= 0.25 and int(float(row[label])) == 0)
                    else "ambiguous"
                )
                errors.append(error)

    coefficients: list[dict[str, Any]] = []
    for feature, coefficient in zip(
        FULL_FEATURES, full_model.named_steps["model"].coef_[0]
    ):
        coefficients.append(
            {
                "target": target,
                "model": "event_full_logit_v2",
                "feature": feature,
                "standardized_coefficient": float(coefficient),
                "platt_blend_weight": float(blend_weight),
            }
        )
    return scores, deltas, predictions, coefficients, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features", type=Path, default=V1_DIR / "feature_rows.csv.gz"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.features)
    events = build_first_cross_events(rows)
    all_scores: list[dict[str, Any]] = []
    all_deltas: list[dict[str, Any]] = []
    all_predictions: list[dict[str, Any]] = []
    all_coefficients: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    for target, label in TARGETS.items():
        scores, deltas, predictions, coefficients, errors = run_target(
            events, target, label
        )
        all_scores.extend(scores)
        all_deltas.extend(deltas)
        all_predictions.extend(predictions)
        all_coefficients.extend(coefficients)
        all_errors.extend(errors)

    write_rows(args.out / "first_cross_events.csv.gz", events)
    write_rows(args.out / "scores.csv", all_scores)
    write_rows(args.out / "brier_deltas.csv", all_deltas)
    write_rows(args.out / "predictions.csv.gz", all_predictions)
    write_rows(args.out / "logit_coefficients.csv", all_coefficients)
    ranked_errors = sorted(
        all_errors,
        key=lambda row: (
            str(row["target_id"]),
            -float(row["absolute_error"]),
        ),
    )
    write_rows(args.out / "error_cases.csv", ranked_errors)
    summary = {
        "schema_version": "tokyo_jma_multivariate_path_v2",
        "clock_class": "historical_observation_clock_not_first_seen",
        "denominator": (
            "first daytime JMA row per target_date×native-C lattice strictly "
            "above prior routine-METAR running maximum"
        ),
        "splits": {
            "train": "<=2025-06-30",
            "calibration": "2025Q3",
            "audit": "2025Q4",
            "post_audit_replay": ">=2026-01-01",
        },
        "raw_feature_rows": len(rows),
        "first_cross_events": len(events),
        "event_dates": len({str(row["target_date"]) for row in events}),
        "targets": TARGETS,
        "live_behavior_changed": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
