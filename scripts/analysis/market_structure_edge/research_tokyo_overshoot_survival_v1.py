#!/usr/bin/env python3
"""Train and frozen-forward test a Tokyo current-NO overshoot model."""

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
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v1 as v1,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_probability_v2 as v2,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_continuous_ladder_forward_v3 as v3,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_tokyo_current_break_binary_v5 as v5,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    write_rows,
)


FEATURE_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_continuous_ladder_probability_v1"
    / "continuous_feature_rows.csv.gz"
)
MARKET_ROWS = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_current_break_binary_v5"
    / "market_join_rows.csv.gz"
)
V5_ARTIFACT = (
    ROOT / "docs/analysis/2026-07/generated/tokyo_current_break_binary_v5/models"
    / "binary_multigrain_hgb_v5.joblib"
)
DEFAULT_OUT = (
    ROOT / "docs/analysis/2026-08/generated/tokyo_overshoot_survival_v1"
)
TRAIN_END = "2025-06-30"
VALIDATION_START = "2025-07-01"
VALIDATION_END = "2025-12-31"
REFIT_END = "2026-07-15"
FORWARD_START = "2026-07-16"
FORWARD_END = "2026-07-30"
EDGE_THRESHOLD = 0.02
SHARES = 5.0


FEATURES = (
    "jma_temp_slope_30m_cph",
    "jma_temp_slope_60m_cph",
    "distance_to_next_jma_lattice_c",
    "minutes_since_jma_strict_high",
    "jma_warming_run_count",
    "jma_pullback_from_running_max_c",
    "remaining_to_18h",
    "solar_elevation_deg",
    "jma_minus_prior_metar_c",
    "prior_metar_age_min",
    "metar_dewpoint_depression_c",
    "metar_wind_speed_kt",
    "metar_cloud_cover_fraction",
    "metar_precipitating",
    "current_bracket",
)
MONOTONIC = (1, 1, -1, -1, 1, 1, 1, 0, 1, 0, 0, 0, -1, -1, 0)
PARAM_GRID = (
    {"learning_rate": 0.025, "max_iter": 220, "max_leaf_nodes": 7,
     "min_samples_leaf": 240, "l2_regularization": 10.0},
    {"learning_rate": 0.035, "max_iter": 180, "max_leaf_nodes": 7,
     "min_samples_leaf": 160, "l2_regularization": 12.0},
    {"learning_rate": 0.025, "max_iter": 260, "max_leaf_nodes": 15,
     "min_samples_leaf": 240, "l2_regularization": 15.0},
)


def read_csv(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([
        [np.nan if finite(row.get(name)) is None else float(row[name]) for name in FEATURES]
        for row in rows
    ])


def pipeline(params: dict[str, Any]) -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("model", HistGradientBoostingClassifier(
            **params,
            monotonic_cst=list(MONOTONIC),
            random_state=20260802,
        )),
    ])


def fit(rows: list[dict[str, Any]], params: dict[str, Any]) -> Pipeline:
    model = pipeline(params)
    model.fit(
        matrix(rows),
        np.asarray([int(row["binary_leave_current"]) for row in rows]),
        model__sample_weight=v2.multigrain_weights(rows),
    )
    return model


def raw_probability(model: Pipeline, rows: list[dict[str, Any]]) -> np.ndarray:
    values = model.predict_proba(matrix(rows))
    classes = [int(value) for value in model.named_steps["model"].classes_]
    return values[:, classes.index(1)]


def apply_temperature(probability: np.ndarray, temperature: float) -> np.ndarray:
    clipped = np.clip(probability, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1.0 - clipped)) / temperature
    return 1.0 / (1.0 + np.exp(-logits))


def date_equal_brier(rows: list[dict[str, Any]], probability: np.ndarray) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, probability):
        label = int(row["binary_leave_current"])
        daily[str(row["target_date"])].append((float(value) - label) ** 2)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def date_equal_logloss(rows: list[dict[str, Any]], probability: np.ndarray) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, np.clip(probability, 1e-6, 1 - 1e-6)):
        label = int(row["binary_leave_current"])
        daily[str(row["target_date"])].append(
            -label * math.log(float(value)) - (1 - label) * math.log(1 - float(value))
        )
    return float(np.mean([np.mean(values) for values in daily.values()]))


def choose_temperature(rows: list[dict[str, Any]], raw: np.ndarray) -> float:
    candidates = np.arange(0.6, 1.61, 0.05)
    return float(min(candidates, key=lambda t: date_equal_logloss(rows, apply_temperature(raw, float(t)))))


def grain_score(rows: list[dict[str, Any]], probability: np.ndarray, grain: str) -> dict[str, Any]:
    selected = [
        index for index, row in enumerate(rows)
        if grain == "checkpoint" or int(row[f"is_{grain}"]) == 1
    ]
    part = [rows[index] for index in selected]
    values = probability[selected]
    return {
        "grain": grain,
        "rows": len(part),
        "target_dates": len({str(row["target_date"]) for row in part}),
        "brier": date_equal_brier(part, values),
        "logloss": date_equal_logloss(part, values),
        "accuracy": float(np.mean([
            (value >= 0.5) == bool(int(row["binary_leave_current"]))
            for row, value in zip(part, values)
        ])),
    }


def model_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def market_evaluation(
    market_rows: list[dict[str, Any]], probability_by_state: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scores: dict[str, list[tuple[float, float, int, str]]] = defaultdict(list)
    candidates: list[dict[str, Any]] = []
    for row in market_rows:
        state_id = str(row["state_id"])
        if state_id not in probability_by_state or int(row["settlement_lower_bound_violation"]):
            continue
        quote = json.loads(row["quotes_json"]).get(str(row["current_bracket"]))
        if not quote or finite(quote.get("mid")) is None:
            continue
        yes_mid = float(quote["mid"])
        p_no = probability_by_state[state_id]
        label_no = int(str(row["winning_bracket"]) != str(row["current_bracket"]))
        scores["model"].append((p_no, 1 - yes_mid, label_no, str(row["target_date"])))
        scores["market"].append((1 - yes_mid, 1 - yes_mid, label_no, str(row["target_date"])))
        yes_bid = finite(quote.get("bid"))
        if yes_bid is None:
            continue
        no_ask = 1.0 - yes_bid
        fee = v3.official_fee_per_share(no_ask)
        candidates.append({
            **row,
            "side": "NO",
            "p_win": p_no,
            "selected_side_ask": no_ask,
            "fee_per_share": fee,
            "fee_adjusted_edge": p_no - no_ask - fee,
            "settled_win": label_no,
        })

    output = []
    for name, values in scores.items():
        daily_brier: dict[str, list[float]] = defaultdict(list)
        daily_logloss: dict[str, list[float]] = defaultdict(list)
        for probability, _, label, target_date in values:
            p = min(max(probability, 1e-6), 1 - 1e-6)
            daily_brier[target_date].append((p - label) ** 2)
            daily_logloss[target_date].append(-label * math.log(p) - (1 - label) * math.log(1 - p))
        output.append({
            "model": name,
            "rows": len(values),
            "target_dates": len(daily_brier),
            "brier": float(np.mean([np.mean(value) for value in daily_brier.values()])),
            "logloss": float(np.mean([np.mean(value) for value in daily_logloss.values()])),
        })
    return output, candidates


def rolling_no_trades(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first_by_bracket: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(candidates, key=lambda item: (str(item["target_date"]), str(item["availability_ts_utc"]))):
        if float(row["fee_adjusted_edge"]) < EDGE_THRESHOLD:
            continue
        key = (str(row["target_date"]), str(row["current_bracket"]))
        first_by_bracket.setdefault(key, row)
    trades = []
    for row in first_by_bracket.values():
        cost = SHARES * (float(row["selected_side_ask"]) + float(row["fee_per_share"]))
        pnl = SHARES * int(row["settled_win"]) - cost
        trades.append({
            **row,
            "position_key": f"{row['target_date']}|{row['current_bracket']}|NO",
            "shares": SHARES,
            "entry_cost_usd": cost,
            "fee_adjusted_pnl_usd": pnl,
            "position_state_at_entry": "unresolved_current_no",
            "prior_bracket_positions": "locked_after_official_transition",
        })
    return trades


def strategy_summary(trades: list[dict[str, Any]], denominator_dates: list[str]) -> dict[str, Any]:
    cost = sum(float(row["entry_cost_usd"]) for row in trades)
    pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in trades)
    daily = {date: {"cost": 0.0, "pnl": 0.0, "trades": 0} for date in denominator_dates}
    for row in trades:
        item = daily[str(row["target_date"])]
        item["cost"] += float(row["entry_cost_usd"])
        item["pnl"] += float(row["fee_adjusted_pnl_usd"])
        item["trades"] += 1
    losing = [date for date, value in daily.items() if value["pnl"] < 0]
    return {
        "denominator_dates": len(denominator_dates),
        "trades": len(trades),
        "trade_dates": len({str(row["target_date"]) for row in trades}),
        "wins": sum(int(row["settled_win"]) for row in trades),
        "cost_usd": cost,
        "fee_adjusted_pnl_usd": pnl,
        "fee_adjusted_roi": pnl / cost if cost else None,
        "losing_target_dates": losing,
        "max_daily_loss_usd": min((value["pnl"] for value in daily.values()), default=0.0),
        "daily": daily,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=FEATURE_ROWS)
    parser.add_argument("--market-rows", type=Path, default=MARKET_ROWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = v2.annotate_grains([
        row for row in read_csv(args.features)
        if row.get("binary_leave_current") not in (None, "")
    ])
    train = [row for row in rows if str(row["target_date"]) <= TRAIN_END]
    validation = [row for row in rows if VALIDATION_START <= str(row["target_date"]) <= VALIDATION_END]
    refit = [row for row in rows if str(row["target_date"]) <= REFIT_END]
    forward = [row for row in rows if FORWARD_START <= str(row["target_date"]) <= FORWARD_END]
    if {row["state_id"] for row in refit} & {row["state_id"] for row in forward}:
        raise RuntimeError("refit/forward overlap")

    selection = []
    fitted = []
    for index, params in enumerate(PARAM_GRID, start=1):
        model = fit(train, params)
        raw = raw_probability(model, validation)
        temperature = choose_temperature(validation, raw)
        probability = apply_temperature(raw, temperature)
        grain_scores = [grain_score(validation, probability, grain) for grain in v2.GRAINS]
        objective = float(np.mean([score["brier"] for score in grain_scores]))
        selection.append({
            "candidate": index, **params, "temperature": temperature,
            "multigrain_brier": objective,
            **{f"{score['grain']}_brier": score["brier"] for score in grain_scores},
        })
        fitted.append((objective, params, temperature))
    _, best_params, best_temperature = min(fitted, key=lambda item: item[0])
    champion = fit(refit, best_params)
    forward_probability = apply_temperature(raw_probability(champion, forward), best_temperature)

    incumbent = joblib.load(V5_ARTIFACT)
    incumbent_probability = v5.apply_binary_temperature(
        v5.raw_break_probability(incumbent["model"], forward),
        float(incumbent["temperature"]),
    )
    score_rows = []
    for model_id, probability in (
        ("tokyo_overshoot_monotonic_hgb_v1", forward_probability),
        ("binary_multigrain_hgb_v5", incumbent_probability),
    ):
        for grain in v2.GRAINS:
            score_rows.append({"model": model_id, **grain_score(forward, probability, grain)})

    model_path = args.out / "tokyo_overshoot_monotonic_hgb_v1.joblib"
    artifact = {
        "schema_version": "tokyo_overshoot_survival_v1",
        "model_id": "tokyo_overshoot_monotonic_hgb_v1",
        "target_id": "eod_leave_current_exact_bracket",
        "model": champion,
        "features": list(FEATURES),
        "monotonic_cst": list(MONOTONIC),
        "temperature": best_temperature,
        "parameters": best_params,
        "training_cutoff": REFIT_END,
        "forward_window": [FORWARD_START, FORWARD_END],
        "forward_labels_used_in_fit": False,
    }
    joblib.dump(artifact, model_path, compress=3)
    artifact_sha = model_hash(model_path)
    spec = {key: value for key, value in artifact.items() if key != "model"}
    spec["artifact_sha256"] = artifact_sha
    spec_path = args.out / "tokyo_overshoot_monotonic_hgb_v1.spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    probability_by_state = {
        str(row["state_id"]): float(value) for row, value in zip(forward, forward_probability)
    }
    market_rows = [
        row for row in read_csv(args.market_rows)
        if FORWARD_START <= str(row["target_date"]) <= FORWARD_END
    ]
    market_scores, candidates = market_evaluation(market_rows, probability_by_state)
    trades = rolling_no_trades(candidates)
    denominator = sorted({str(row["target_date"]) for row in forward})
    strategy = strategy_summary(trades, denominator)

    write_rows(args.out / "selection_scores.csv", selection)
    write_rows(args.out / "forward_probability_scores.csv", score_rows)
    write_rows(args.out / "market_scores.csv", market_scores)
    write_rows(args.out / "rolling_no_candidates.csv.gz", candidates)
    write_rows(args.out / "rolling_no_trades.csv", trades)
    (args.out / "summary.json").write_text(json.dumps({
        "schema_version": "tokyo_overshoot_survival_v1",
        "target": "P(final official Tmax bracket > current official bracket)",
        "training_rows": len(refit),
        "training_dates": len({str(row["target_date"]) for row in refit}),
        "forward_rows": len(forward),
        "forward_dates": len(denominator),
        "forward_labels_used_in_fit": False,
        "selected_parameters": best_params,
        "temperature": best_temperature,
        "artifact_sha256": artifact_sha,
        "artifact_path": str(model_path.relative_to(ROOT)),
        "market_rows": len(candidates),
        "market_dates": sorted({str(row["target_date"]) for row in candidates}),
        "probability_scores": score_rows,
        "market_scores": market_scores,
        "rolling_no_strategy": strategy,
        "strategy_policy": {
            "side": "NO_only",
            "edge_threshold": EDGE_THRESHOLD,
            "selection": "first_positive_edge_per_target_date_bracket",
            "portfolio": "one unresolved current NO; crossed prior NOs become locked",
            "shares": SHARES,
            "actual_orders": 0,
            "actual_fills": 0,
        },
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print((args.out / "summary.json").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
