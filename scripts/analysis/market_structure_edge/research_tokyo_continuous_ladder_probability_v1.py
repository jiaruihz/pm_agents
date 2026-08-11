#!/usr/bin/env python3
"""Continuous Tokyo 10-minute exact-ladder probability research.

Compares:
1. binary current-bracket stay vs leave;
2. direct multinomial remaining-rise distribution {0,1,2,3+};
3. ordered hazard cascade with the same four bins.

The hard lower-bound constraint is driven only by strictly earlier RJTT METAR
reports, including special reports.  JMA is a faster probabilistic feature and
never zeros a settlement bracket by itself.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (  # noqa: E402
    FULL_FEATURES,
    date_weights,
    finite,
    matrix,
    round_native_c,
    write_rows,
)
from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_market_v1 import (  # noqa: E402
    exact_first_seen,
    parse_ts,
)
from weather_data_feed.production_paths import historical_full_ladder_root  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
FEATURE_ROWS = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v1"
    / "feature_rows.csv.gz"
)
EXACT_FIRST_SEEN = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_feature_timestamp_audit_v1"
    / "tokyo_jma_exact_enriched.csv"
)
MARKET_STATES = load_production_spec().resolved_historical_data_feed_runtime_root() / (
    "research/five_city_weather_microstructure/v1/"
    "snapshot=20260730T120000Z/state_rows.csv.gz"
)
PM_HISTORY = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
RAW_BOOKS = historical_full_ladder_root() / "orderbook_snapshots"
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_continuous_ladder_probability_v1"
)
FEE_RATE = 0.05
SHARES = 5.0
CLASSES = (0, 1, 2, 3)
EPS = 1e-8
DERIVED_FEATURES = (
    "current_bracket",
    "jma_current_minus_current_bracket",
    "jma_running_max_minus_current_bracket",
    "jma_pullback_from_running_max_c",
    "jma_above_next_boundary_c",
    "remaining_to_18h",
    "metar_pullback_from_current_bracket_c",
)
MODEL_FEATURES = FULL_FEATURES + DERIVED_FEATURES


def read_rows(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_continuous_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in rows:
        local_hour = finite(raw.get("local_hour"))
        prior_running = finite(raw.get("prior_metar_running_max_c"))
        final_rounded = finite(raw.get("final_metar_rounded_c"))
        jma_temp = finite(raw.get("jma_temp_c"))
        jma_running = finite(raw.get("jma_running_max_c"))
        prior_temp = finite(raw.get("prior_metar_temp_c"))
        if (
            local_hour is None
            or not 5 <= local_hour < 18
            or prior_running is None
            or final_rounded is None
            or jma_temp is None
            or jma_running is None
            or prior_temp is None
        ):
            continue
        current = round_native_c(prior_running)
        raw_delta = int(final_rounded - current)
        if raw_delta < 0:
            # This is a source-to-settlement lower-bound violation and must
            # remain visible rather than being silently clipped into class 0.
            label = None
            violation = 1
        else:
            label = min(raw_delta, 3)
            violation = 0
        row = dict(raw)
        row.update(
            {
                "state_id": (
                    f"Tokyo:{row['target_date']}:{row['decision_ts_utc']}:"
                    f"current={current}"
                ),
                "current_bracket": current,
                "final_bracket": int(final_rounded),
                "remaining_rise_class": label,
                "binary_leave_current": (
                    None if label is None else int(label > 0)
                ),
                "historical_lower_bound_violation": violation,
                "jma_current_minus_current_bracket": jma_temp - current,
                "jma_running_max_minus_current_bracket": jma_running - current,
                "jma_pullback_from_running_max_c": jma_temp - jma_running,
                "jma_above_next_boundary_c": jma_temp - (current + 0.5),
                "remaining_to_18h": 18.0 - local_hour,
                "metar_pullback_from_current_bracket_c": prior_temp - current,
            }
        )
        output.append(row)
    return output


def split_name(target_date: str) -> str:
    if target_date <= "2025-06-30":
        return "train"
    if target_date <= "2025-12-31":
        return "validation"
    if target_date <= "2026-06-30":
        return "frozen_weather"
    if target_date <= "2026-07-21":
        return "market_development"
    return "market_holdout"


def fit_logit(
    rows: list[dict[str, Any]], label: str, *, multiclass: bool
) -> Pipeline:
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=0.25,
                    max_iter=3000,
                    solver="lbfgs",
                    random_state=20260731,
                ),
            ),
        ]
    )
    y = np.asarray([int(row[label]) for row in rows])
    model.fit(
        matrix(rows, MODEL_FEATURES),
        y,
        model__sample_weight=date_weights(rows),
    )
    return model


def fit_hgb(
    rows: list[dict[str, Any]],
    label: str,
    *,
    feature_names: tuple[str, ...] = MODEL_FEATURES,
) -> Pipeline:
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    learning_rate=0.035,
                    max_iter=220,
                    max_leaf_nodes=15,
                    min_samples_leaf=120,
                    l2_regularization=5.0,
                    random_state=20260731,
                ),
            ),
        ]
    )
    model.fit(
        matrix(rows, feature_names),
        np.asarray([int(row[label]) for row in rows]),
        model__sample_weight=date_weights(rows),
    )
    return model


def aligned_probabilities(
    model: Any,
    rows: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...] = MODEL_FEATURES,
) -> np.ndarray:
    raw = model.predict_proba(matrix(rows, feature_names))
    classes = [int(value) for value in model.named_steps["model"].classes_]
    output = np.zeros((len(rows), 4), dtype=float)
    for index, value in enumerate(classes):
        output[:, value] = raw[:, index]
    return output


def absolute_probabilities(
    model: Any, rows: list[dict[str, Any]], *, temperature: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    raw = model.predict_proba(matrix(rows, MODEL_FEATURES))
    classes = np.asarray(
        [int(value) for value in model.named_steps["model"].classes_]
    )
    constrained = raw.copy()
    for index, row in enumerate(rows):
        constrained[index, classes < int(row["current_bracket"])] = 0.0
    totals = constrained.sum(axis=1, keepdims=True)
    if np.any(totals <= 0):
        raise RuntimeError("absolute ladder lost all probability after lower-bound mask")
    constrained /= totals
    constrained = apply_temperature(constrained, temperature)
    # Temperature scaling clips zeros for numeric stability. Re-apply the
    # settlement lower bound so already-passed brackets remain exactly zero.
    for index, row in enumerate(rows):
        constrained[index, classes < int(row["current_bracket"])] = 0.0
    constrained /= constrained.sum(axis=1, keepdims=True)
    return classes, constrained


def collapse_absolute(
    classes: np.ndarray,
    probabilities: np.ndarray,
    rows: list[dict[str, Any]],
) -> np.ndarray:
    output = np.zeros((len(rows), 4), dtype=float)
    for row_index, row in enumerate(rows):
        current = int(row["current_bracket"])
        for class_index, bracket in enumerate(classes):
            output[row_index, min(max(int(bracket) - current, 0), 3)] += (
                probabilities[row_index, class_index]
            )
    return output


def absolute_date_equal_logloss(
    rows: list[dict[str, Any]],
    classes: np.ndarray,
    probabilities: np.ndarray,
) -> float:
    lookup = {int(value): index for index, value in enumerate(classes)}
    daily: dict[str, list[float]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        label = int(row["final_bracket"])
        value = (
            -math.log(max(float(probability[lookup[label]]), EPS))
            if label in lookup
            else -math.log(EPS)
        )
        daily[str(row["target_date"])].append(value)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def choose_absolute_temperature(
    model: Any, rows: list[dict[str, Any]]
) -> float:
    return float(
        min(
            np.linspace(0.7, 2.0, 27),
            key=lambda value: absolute_date_equal_logloss(
                rows,
                *absolute_probabilities(
                    model, rows, temperature=float(value)
                ),
            ),
        )
    )


def binary_probability(model: Any, rows: list[dict[str, Any]]) -> np.ndarray:
    classes = list(model.named_steps["model"].classes_)
    raw = model.predict_proba(matrix(rows, MODEL_FEATURES))
    return raw[:, classes.index(1)]


def fit_ordinal_hazards(
    rows: list[dict[str, Any]],
) -> list[tuple[Any, list[dict[str, Any]]]]:
    output = []
    for threshold in (0, 1, 2):
        selected = [
            row
            for row in rows
            if int(row["remaining_rise_class"]) >= threshold
        ]
        enriched = []
        for row in selected:
            item = dict(row)
            item["ordinal_hazard_label"] = int(
                int(row["remaining_rise_class"]) > threshold
            )
            enriched.append(item)
        output.append((fit_hgb(enriched, "ordinal_hazard_label"), enriched))
    return output


def ordinal_probabilities(
    models: list[tuple[Any, list[dict[str, Any]]]],
    rows: list[dict[str, Any]],
) -> np.ndarray:
    hazards = [binary_probability(model, rows) for model, _ in models]
    h0, h1, h2 = hazards
    output = np.column_stack(
        [
            1 - h0,
            h0 * (1 - h1),
            h0 * h1 * (1 - h2),
            h0 * h1 * h2,
        ]
    )
    return output / output.sum(axis=1, keepdims=True)


def apply_temperature(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    powered = np.clip(probabilities, 1e-8, 1) ** (1.0 / temperature)
    return powered / powered.sum(axis=1, keepdims=True)


def date_equal_loss(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    *,
    metric: str,
) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        label = int(row["remaining_rise_class"])
        one_hot = np.eye(4)[label]
        if metric == "logloss":
            value = -math.log(max(float(probability[label]), EPS))
        elif metric == "brier":
            value = float(np.sum((probability - one_hot) ** 2))
        elif metric == "rps":
            value = float(
                np.mean(
                    (
                        np.cumsum(probability)[:-1]
                        - np.cumsum(one_hot)[:-1]
                    )
                    ** 2
                )
            )
        else:
            raise ValueError(metric)
        daily[str(row["target_date"])].append(value)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def choose_temperature(
    rows: list[dict[str, Any]], probabilities: np.ndarray
) -> float:
    grid = np.linspace(0.7, 2.0, 27)
    return float(
        min(
            grid,
            key=lambda value: date_equal_loss(
                rows,
                apply_temperature(probabilities, float(value)),
                metric="logloss",
            ),
        )
    )


def date_bootstrap_delta(
    rows: list[dict[str, Any]],
    candidate: np.ndarray,
    baseline: np.ndarray,
    *,
    metric: str,
) -> tuple[float, float, float]:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, p1, p0 in zip(rows, candidate, baseline):
        label = int(row["remaining_rise_class"])
        one_hot = np.eye(4)[label]
        if metric == "logloss":
            value = -math.log(max(float(p1[label]), EPS)) + math.log(
                max(float(p0[label]), EPS)
            )
        elif metric == "brier":
            value = float(
                np.sum((p1 - one_hot) ** 2) - np.sum((p0 - one_hot) ** 2)
            )
        else:
            raise ValueError(metric)
        daily[str(row["target_date"])].append(value)
    values = np.asarray([np.mean(group) for group in daily.values()])
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


def model_score_rows(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    split: str,
) -> list[dict[str, Any]]:
    output = []
    baseline = predictions["full_v1_multinomial_logit"]
    for name, probabilities in predictions.items():
        record = {
            "split": split,
            "model": name,
            "states": len(rows),
            "target_dates": len({str(row["target_date"]) for row in rows}),
            "multiclass_logloss": date_equal_loss(
                rows, probabilities, metric="logloss"
            ),
            "multiclass_brier": date_equal_loss(
                rows, probabilities, metric="brier"
            ),
            "ranked_probability_score": date_equal_loss(
                rows, probabilities, metric="rps"
            ),
        }
        if name != "full_v1_multinomial_logit":
            delta, low, high = date_bootstrap_delta(
                rows, probabilities, baseline, metric="brier"
            )
            record.update(
                {
                    "brier_delta_vs_v1": delta,
                    "brier_delta_ci_low": low,
                    "brier_delta_ci_high": high,
                }
            )
        output.append(record)
    return output


def binary_metrics(
    rows: list[dict[str, Any]],
    p_current: np.ndarray,
    *,
    split: str,
    model: str,
) -> dict[str, Any]:
    daily_brier: dict[str, list[float]] = defaultdict(list)
    daily_logloss: dict[str, list[float]] = defaultdict(list)
    for row, probability in zip(rows, p_current):
        label = int(int(row["remaining_rise_class"]) == 0)
        probability = min(max(float(probability), EPS), 1 - EPS)
        daily_brier[str(row["target_date"])].append(
            (probability - label) ** 2
        )
        daily_logloss[str(row["target_date"])].append(
            -(label * math.log(probability) + (1 - label) * math.log(1 - probability))
        )
    return {
        "split": split,
        "model": model,
        "states": len(rows),
        "target_dates": len(daily_brier),
        "stay_rate": float(
            np.mean(
                [
                    np.mean(
                        [
                            int(int(row["remaining_rise_class"]) == 0)
                            for row in rows
                            if str(row["target_date"]) == target_date
                        ]
                    )
                    for target_date in daily_brier
                ]
            )
        ),
        "binary_brier": float(
            np.mean([np.mean(values) for values in daily_brier.values()])
        ),
        "binary_logloss": float(
            np.mean([np.mean(values) for values in daily_logloss.values()])
        ),
    }


def load_market_states(path: Path) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_rows(path):
        if (
            row.get("city") != "Tokyo"
            or str(row.get("is_target_day")).lower() not in {"true", "1"}
            or str(row.get("complete_ladder_snapshot")).lower()
            not in {"true", "1"}
        ):
            continue
        row["timestamp"] = parse_ts(row["snapshot_ts_utc"])
        try:
            row["quotes"] = json.loads(str(row["quote_vectors_json"]))
        except json.JSONDecodeError:
            continue
        by_date[str(row["target_date"])].append(row)
    for rows in by_date.values():
        rows.sort(key=lambda row: row["timestamp"])
    return by_date


def load_winners(root: Path, start: date, end: date) -> dict[str, str]:
    output = {}
    cursor = start
    while cursor <= end:
        path = root / f"Tokyo_{cursor.isoformat()}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
            winners = [
                str(row["label"])
                for row in payload.get("brackets", [])
                if finite(row.get("final_price")) == 1.0
            ]
            if len(winners) == 1:
                output[cursor.isoformat()] = winners[0]
        cursor += timedelta(days=1)
    return output


def label_anchor(label: str) -> int | None:
    match = re.search(r"-?\d+", str(label))
    return int(match.group(0)) if match else None


def normalized_yes_quotes(quotes: dict[str, Any]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    ask = quotes.get("ask", {})
    bid = quotes.get("bid", {})
    mid = quotes.get("mid", {})
    for bracket in set(ask) | set(bid) | set(mid):
        bracket_ask = finite(ask.get(bracket))
        bracket_bid = finite(bid.get(bracket))
        bracket_mid = finite(mid.get(bracket))
        if bracket_mid is None and bracket_ask is not None and bracket_bid is not None:
            bracket_mid = (bracket_ask + bracket_bid) / 2
        if bracket_mid is None:
            bracket_mid = bracket_ask if bracket_ask is not None else bracket_bid
        output[str(bracket)] = {
            key: value
            for key, value in {
                "ask": bracket_ask,
                "bid": bracket_bid,
                "mid": bracket_mid,
            }.items()
            if value is not None
        }
    return output


def conditional_market_distribution(
    quotes: dict[str, dict[str, float]], current: int
) -> tuple[np.ndarray | None, float]:
    anchored = [
        (label_anchor(bracket), values.get("mid"))
        for bracket, values in quotes.items()
    ]
    anchored = [
        (anchor, value)
        for anchor, value in anchored
        if anchor is not None and value is not None
    ]
    total = sum(float(value) for _, value in anchored)
    below = sum(
        float(value) for anchor, value in anchored if int(anchor) < current
    )
    feasible = [
        (int(anchor), float(value))
        for anchor, value in anchored
        if int(anchor) >= current
    ]
    denominator = sum(value for _, value in feasible)
    if denominator <= 0:
        return None, below / total if total else math.nan
    distribution = np.zeros(4)
    for anchor, value in feasible:
        distribution[min(max(anchor - current, 0), 3)] += value / denominator
    return distribution, below / total if total else math.nan


def join_market(
    rows: list[dict[str, Any]],
    predictions: dict[str, np.ndarray],
    absolute_classes: np.ndarray,
    absolute_predictions: np.ndarray,
    exact: dict[str, datetime],
    markets: dict[str, list[dict[str, Any]]],
    winners: dict[str, str],
) -> list[dict[str, Any]]:
    output = []
    for index, row in enumerate(rows):
        target_date = str(row["target_date"])
        winner = winners.get(target_date)
        winner_anchor = label_anchor(winner) if winner else None
        if winner_anchor is None:
            continue
        observed = parse_ts(row["decision_ts_utc"])
        exact_available = exact.get(observed.isoformat())
        available = (
            exact_available
            if exact_available is not None
            else observed + timedelta(minutes=15)
        )
        market_rows = markets.get(target_date, [])
        timestamps = [candidate["timestamp"] for candidate in market_rows]
        market_index = bisect_left(timestamps, available)
        if market_index >= len(market_rows):
            continue
        market = market_rows[market_index]
        if market["timestamp"] > available + timedelta(minutes=45):
            continue
        current = int(row["current_bracket"])
        actual_delta = winner_anchor - current
        violation = int(actual_delta < 0)
        quotes = normalized_yes_quotes(market["quotes"])
        market_distribution, stale_mass = conditional_market_distribution(
            quotes, current
        )
        if market_distribution is None:
            continue
        record = {
            "state_id": row["state_id"],
            "target_date": target_date,
            "decision_ts_utc": row["decision_ts_utc"],
            "availability_ts_utc": available.isoformat(),
            "availability_clock_class": (
                "collector_exact_hash_verified"
                if exact_available is not None
                else "archive_reconstructed_plus_15m"
            ),
            "snapshot_ts_utc": market["timestamp"].isoformat(),
            "availability_to_book_min": (
                market["timestamp"] - available
            ).total_seconds()
            / 60,
            "current_bracket": current,
            "winning_bracket": winner,
            "actual_delta": actual_delta,
            "settlement_lower_bound_violation": violation,
            "stale_market_mass_below_current": stale_mass,
            "quotes_json": json.dumps(quotes, sort_keys=True),
            "market_distribution_json": json.dumps(
                market_distribution.tolist()
            ),
            "local_hour": row["local_hour"],
            "jma_temp_c": row["jma_temp_c"],
            "jma_temp_slope_30m_cph": row["jma_temp_slope_30m_cph"],
            "jma_temp_slope_60m_cph": row["jma_temp_slope_60m_cph"],
            "jma_wind_speed_kt": row["jma_wind_speed_kt"],
            "jma_wind_dir_deg": row["jma_wind_dir_deg"],
            "metar_dewpoint_depression_c": row[
                "metar_dewpoint_depression_c"
            ],
            "metar_cloud_cover_fraction": row[
                "metar_cloud_cover_fraction"
            ],
            "minutes_since_jma_strict_high": row[
                "minutes_since_jma_strict_high"
            ],
        }
        for name, probability in predictions.items():
            record[f"{name}_distribution_json"] = json.dumps(
                probability[index].tolist()
            )
            record[f"{name}_p_current"] = float(probability[index, 0])
        record["full_v4_absolute_ladder_hgb_absolute_json"] = json.dumps(
            {
                str(int(bracket)): float(probability)
                for bracket, probability in zip(
                    absolute_classes, absolute_predictions[index]
                )
                if probability > 0
            },
            sort_keys=True,
        )
        output.append(record)
    return output


def market_score_rows(
    joined: list[dict[str, Any]], model_names: Iterable[str], split: str
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in joined
        if row["settlement_lower_bound_violation"] == 0
    ]
    if not rows:
        return []
    market_probabilities = np.asarray(
        [json.loads(row["market_distribution_json"]) for row in rows]
    )
    proxy_rows = [
        {
            "target_date": row["target_date"],
            "remaining_rise_class": min(int(row["actual_delta"]), 3),
        }
        for row in rows
    ]
    output = []
    for name in list(model_names) + ["conditional_market"]:
        probabilities = (
            market_probabilities
            if name == "conditional_market"
            else np.asarray(
                [
                    json.loads(row[f"{name}_distribution_json"])
                    for row in rows
                ]
            )
        )
        output.append(
            {
                "split": split,
                "model": name,
                "states": len(rows),
                "target_dates": len(
                    {str(row["target_date"]) for row in rows}
                ),
                "multiclass_logloss": date_equal_loss(
                    proxy_rows, probabilities, metric="logloss"
                ),
                "multiclass_brier": date_equal_loss(
                    proxy_rows, probabilities, metric="brier"
                ),
                "ranked_probability_score": date_equal_loss(
                    proxy_rows, probabilities, metric="rps"
                ),
            }
        )
    for record in output:
        if record["model"] == "conditional_market":
            continue
        model_probabilities = np.asarray(
            [
                json.loads(row[f"{record['model']}_distribution_json"])
                for row in rows
            ]
        )
        delta, low, high = date_bootstrap_delta(
            proxy_rows,
            model_probabilities,
            market_probabilities,
            metric="brier",
        )
        record.update(
            {
                "brier_delta_vs_market": delta,
                "brier_delta_ci_low": low,
                "brier_delta_ci_high": high,
            }
        )
    return output


def market_binary_scores(
    joined: list[dict[str, Any]],
    model_names: Iterable[str],
    split: str,
) -> list[dict[str, Any]]:
    usable = [
        row
        for row in joined
        if row["settlement_lower_bound_violation"] == 0
    ]
    output = []
    probability_sets: dict[str, list[float]] = {
        name: [float(row[f"{name}_p_current"]) for row in usable]
        for name in model_names
    }
    probability_sets["conditional_market"] = [
        float(json.loads(row["market_distribution_json"])[0])
        for row in usable
    ]
    proxy = [
        {
            "target_date": row["target_date"],
            "remaining_rise_class": min(int(row["actual_delta"]), 3),
        }
        for row in usable
    ]
    market_values = np.asarray(probability_sets["conditional_market"])
    for model, values in probability_sets.items():
        record = binary_metrics(
            proxy,
            np.asarray(values),
            split=split,
            model=model,
        )
        if model != "conditional_market":
            daily: dict[str, list[float]] = defaultdict(list)
            for row, candidate, baseline in zip(
                proxy, values, market_values
            ):
                label = int(int(row["remaining_rise_class"]) == 0)
                daily[str(row["target_date"])].append(
                    (float(candidate) - label) ** 2
                    - (float(baseline) - label) ** 2
                )
            blocks = np.asarray([np.mean(group) for group in daily.values()])
            rng = np.random.default_rng(20260731)
            draws = np.asarray(
                [
                    float(np.mean(rng.choice(blocks, len(blocks), replace=True)))
                    for _ in range(5000)
                ]
            )
            record.update(
                {
                    "brier_delta_vs_market": float(np.mean(blocks)),
                    "brier_delta_ci_low": float(np.quantile(draws, 0.025)),
                    "brier_delta_ci_high": float(np.quantile(draws, 0.975)),
                }
            )
        output.append(record)
    return output


def map_absolute_to_market(
    absolute: dict[str, float],
    quotes: dict[str, dict[str, float]],
    current: int,
) -> tuple[list[str], np.ndarray, np.ndarray] | None:
    labels = sorted(
        (
            bracket
            for bracket, values in quotes.items()
            if label_anchor(bracket) is not None and values.get("mid") is not None
        ),
        key=lambda bracket: int(label_anchor(bracket) or 0),
    )
    if not labels:
        return None
    anchors = [int(label_anchor(label) or 0) for label in labels]
    market = np.asarray([float(quotes[label]["mid"]) for label in labels])
    market[np.asarray(anchors) < current] = 0.0
    if market.sum() <= 0:
        return None
    market /= market.sum()
    model = np.zeros(len(labels))
    minimum = min(anchors)
    maximum = max(anchors)
    index_by_anchor = {anchor: index for index, anchor in enumerate(anchors)}
    for bracket, probability in absolute.items():
        parsed = label_anchor(bracket)
        if parsed is None:
            continue
        value = int(parsed)
        if value <= minimum:
            index = index_by_anchor[minimum]
        elif value >= maximum:
            index = index_by_anchor[maximum]
        elif value in index_by_anchor:
            index = index_by_anchor[value]
        else:
            continue
        model[index] += float(probability)
    model[np.asarray(anchors) < current] = 0.0
    if model.sum() <= 0:
        return None
    model /= model.sum()
    return labels, model, market


def exact_score(
    rows: list[dict[str, Any]],
    probability_field: str,
    *,
    split: str,
    model: str,
) -> dict[str, Any] | None:
    daily_logloss: dict[str, list[float]] = defaultdict(list)
    daily_brier: dict[str, list[float]] = defaultdict(list)
    daily_brier_delta: dict[str, list[float]] = defaultdict(list)
    used = 0
    for row in rows:
        quotes = json.loads(row["quotes_json"])
        probabilities = json.loads(row[probability_field])
        mapped = map_absolute_to_market(
            probabilities, quotes, int(row["current_bracket"])
        )
        if mapped is None:
            continue
        labels, candidate, market = mapped
        winner = str(row["winning_bracket"])
        if winner not in labels:
            continue
        label_index = labels.index(winner)
        one_hot = np.eye(len(labels))[label_index]
        daily_logloss[str(row["target_date"])].append(
            -math.log(max(float(candidate[label_index]), EPS))
        )
        candidate_brier = float(np.sum((candidate - one_hot) ** 2))
        market_brier = float(np.sum((market - one_hot) ** 2))
        daily_brier[str(row["target_date"])].append(candidate_brier)
        daily_brier_delta[str(row["target_date"])].append(
            candidate_brier - market_brier
        )
        used += 1
    if not used:
        return None
    daily_deltas = np.asarray(
        [np.mean(values) for values in daily_brier_delta.values()]
    )
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(
                np.mean(
                    rng.choice(
                        daily_deltas,
                        len(daily_deltas),
                        replace=True,
                    )
                )
            )
            for _ in range(5000)
        ]
    )
    return {
        "split": split,
        "model": model,
        "states": used,
        "target_dates": len(daily_logloss),
        "exact_logloss": float(
            np.mean([np.mean(values) for values in daily_logloss.values()])
        ),
        "exact_brier": float(
            np.mean([np.mean(values) for values in daily_brier.values()])
        ),
        "brier_delta_vs_conditional_market": float(np.mean(daily_deltas)),
        "brier_delta_ci_low": float(np.quantile(draws, 0.025)),
        "brier_delta_ci_high": float(np.quantile(draws, 0.975)),
    }


def exact_market_score(
    rows: list[dict[str, Any]], *, split: str
) -> dict[str, Any] | None:
    daily_logloss: dict[str, list[float]] = defaultdict(list)
    daily_brier: dict[str, list[float]] = defaultdict(list)
    used = 0
    for row in rows:
        quotes = json.loads(row["quotes_json"])
        absolute = json.loads(
            row["full_v4_absolute_ladder_hgb_absolute_json"]
        )
        mapped = map_absolute_to_market(
            absolute, quotes, int(row["current_bracket"])
        )
        if mapped is None:
            continue
        labels, _model, market = mapped
        winner = str(row["winning_bracket"])
        if winner not in labels:
            continue
        label_index = labels.index(winner)
        one_hot = np.eye(len(labels))[label_index]
        daily_logloss[str(row["target_date"])].append(
            -math.log(max(float(market[label_index]), EPS))
        )
        daily_brier[str(row["target_date"])].append(
            float(np.sum((market - one_hot) ** 2))
        )
        used += 1
    if not used:
        return None
    return {
        "split": split,
        "model": "conditional_market",
        "states": used,
        "target_dates": len(daily_logloss),
        "exact_logloss": float(
            np.mean([np.mean(values) for values in daily_logloss.values()])
        ),
        "exact_brier": float(
            np.mean([np.mean(values) for values in daily_brier.values()])
        ),
    }


def choose_market_blend_weight(rows: list[dict[str, Any]]) -> float:
    candidates = np.linspace(0.0, 1.0, 21)

    def loss(weight: float) -> float:
        daily: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            quotes = json.loads(row["quotes_json"])
            absolute = json.loads(
                row["full_v4_absolute_ladder_hgb_absolute_json"]
            )
            mapped = map_absolute_to_market(
                absolute, quotes, int(row["current_bracket"])
            )
            if mapped is None:
                continue
            labels, model, market = mapped
            winner = str(row["winning_bracket"])
            if winner not in labels:
                continue
            one_hot = np.eye(len(labels))[labels.index(winner)]
            fused = weight * model + (1 - weight) * market
            daily[str(row["target_date"])].append(
                float(np.sum((fused - one_hot) ** 2))
            )
        return float(
            np.mean([np.mean(values) for values in daily.values()])
        )

    return float(min(candidates, key=lambda weight: loss(float(weight))))


def add_market_blend(rows: list[dict[str, Any]], weight: float) -> None:
    for row in rows:
        quotes = json.loads(row["quotes_json"])
        absolute = json.loads(
            row["full_v4_absolute_ladder_hgb_absolute_json"]
        )
        mapped = map_absolute_to_market(
            absolute, quotes, int(row["current_bracket"])
        )
        if mapped is None:
            continue
        labels, model, market = mapped
        fused = weight * model + (1 - weight) * market
        row["full_v5_market_anchored_absolute_json"] = json.dumps(
            {
                label: float(probability)
                for label, probability in zip(labels, fused)
            },
            sort_keys=True,
        )


def expression_candidates(
    joined: list[dict[str, Any]], model_names: Iterable[str]
) -> list[dict[str, Any]]:
    output = []
    for row in joined:
        if row["settlement_lower_bound_violation"]:
            continue
        quotes = json.loads(row["quotes_json"])
        current = int(row["current_bracket"])
        for model_name in model_names:
            if model_name == "full_v4_absolute_ladder_hgb":
                exact_probabilities = json.loads(
                    row["full_v4_absolute_ladder_hgb_absolute_json"]
                )
                bracket_probabilities = {
                    bracket: float(probability)
                    for bracket, probability in exact_probabilities.items()
                    if int(bracket) >= current
                }
            elif model_name == "full_v5_market_anchored":
                bracket_probabilities = {
                    bracket: float(probability)
                    for bracket, probability in json.loads(
                        row["full_v5_market_anchored_absolute_json"]
                    ).items()
                    if label_anchor(bracket) is not None
                    and int(label_anchor(bracket) or 0) >= current
                }
            else:
                probabilities = json.loads(
                    row[f"{model_name}_distribution_json"]
                )
                allowed_deltas = (
                    (0,) if model_name == "binary_v2_hgb" else (0, 1, 2)
                )
                bracket_probabilities = {
                    str(current + delta): float(probabilities[delta])
                    for delta in allowed_deltas
                }
            for bracket, p_yes in bracket_probabilities.items():
                quote = quotes.get(bracket)
                if not quote:
                    continue
                for side, p_win, ask in (
                    ("YES", p_yes, finite(quote.get("ask"))),
                    (
                        "NO",
                        1 - p_yes,
                        (
                            None
                            if finite(quote.get("bid")) is None
                            else 1 - float(quote["bid"])
                        ),
                    ),
                ):
                    if ask is None:
                        continue
                    fee = FEE_RATE * ask * (1 - ask)
                    candidate = dict(row)
                    candidate.update(
                        {
                            "model": model_name,
                            "expression_bracket": bracket,
                            "side": side,
                            "p_win": p_win,
                            "ask": ask,
                            "fee_per_share": fee,
                            "fee_adjusted_edge": p_win - ask - fee,
                        }
                    )
                    output.append(candidate)
    return output


def raw_ask_size(
    raw_root: Path,
    target_date: str,
    snapshot_ts: str,
    bracket: str,
    side: str,
) -> float | None:
    timestamp = parse_ts(snapshot_ts)
    local = timestamp.astimezone(BEIJING)
    path = (
        raw_root
        / target_date
        / f"orderbook_snapshot_{local.strftime('%Y%m%d_%H%M')}.jsonl.gz"
    )
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = []
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                row.get("city") == "Tokyo"
                and str(row.get("event_date")) == target_date
                and str(row.get("bracket")) == bracket
            ):
                rows.append(row)
    by_side = {str(row.get("outcome")).upper(): row for row in rows}
    if side == "YES":
        return finite(by_side.get("YES", {}).get("summary", {}).get("ask_size"))
    direct = finite(by_side.get("NO", {}).get("summary", {}).get("ask_size"))
    if direct is not None:
        return direct
    return finite(by_side.get("YES", {}).get("summary", {}).get("bid_size"))


def select_trades(
    candidates: list[dict[str, Any]], raw_root: Path
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in candidates
        if float(row["fee_adjusted_edge"]) >= 0.02
    ]
    best_by_state: dict[tuple[str, str], dict[str, Any]] = {}
    for row in eligible:
        key = (str(row["model"]), str(row["state_id"]))
        current = best_by_state.get(key)
        if current is None or float(row["fee_adjusted_edge"]) > float(
            current["fee_adjusted_edge"]
        ):
            best_by_state[key] = row
    first_by_day: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(
        best_by_state.values(), key=lambda item: str(item["availability_ts_utc"])
    ):
        first_by_day.setdefault(
            (str(row["model"]), str(row["target_date"])), row
        )
    output = []
    for row in first_by_day.values():
        size = raw_ask_size(
            raw_root,
            str(row["target_date"]),
            str(row["snapshot_ts_utc"]),
            str(row["expression_bracket"]),
            str(row["side"]),
        )
        trade = dict(row)
        trade["ask_size"] = size
        trade["five_share_executable"] = int(size is not None and size >= SHARES)
        if not trade["five_share_executable"]:
            output.append(trade)
            continue
        winner = str(row["winning_bracket"])
        won = (
            winner == str(row["expression_bracket"])
            if row["side"] == "YES"
            else winner != str(row["expression_bracket"])
        )
        cost = SHARES * (
            float(row["ask"]) + float(row["fee_per_share"])
        )
        payout = SHARES if won else 0.0
        trade.update(
            {
                "shares": SHARES,
                "won": int(won),
                "entry_cost_usd": cost,
                "payout_usd": payout,
                "fee_adjusted_pnl_usd": payout - cost,
                "trade_class": "research_counterfactual",
            }
        )
        output.append(trade)
    return output


def trade_summary(
    trades: list[dict[str, Any]], split: str
) -> list[dict[str, Any]]:
    output = []
    for model in sorted({str(row["model"]) for row in trades}):
        selected = [
            row
            for row in trades
            if row["model"] == model
            and row.get("five_share_executable") == 1
        ]
        cost = sum(float(row["entry_cost_usd"]) for row in selected)
        pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in selected)
        blocks = [
            (
                float(row["fee_adjusted_pnl_usd"]),
                float(row["entry_cost_usd"]),
            )
            for row in selected
        ]
        if blocks:
            rng = np.random.default_rng(20260731)
            draws = []
            for _ in range(5000):
                sampled = rng.integers(0, len(blocks), len(blocks))
                draw_pnl = sum(blocks[index][0] for index in sampled)
                draw_cost = sum(blocks[index][1] for index in sampled)
                draws.append(draw_pnl / draw_cost if draw_cost else math.nan)
            ci_low = float(np.nanquantile(draws, 0.025))
            ci_high = float(np.nanquantile(draws, 0.975))
        else:
            ci_low = None
            ci_high = None
        output.append(
            {
                "split": split,
                "model": model,
                "trades": len(selected),
                "target_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "wins": sum(int(row["won"]) for row in selected),
                "cost_usd": cost,
                "fee_adjusted_pnl_usd": pnl,
                "fee_adjusted_roi": pnl / cost if cost else None,
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
            }
        )
    return output


def trade_side_summary(
    trades: list[dict[str, Any]], split: str
) -> list[dict[str, Any]]:
    output = []
    models = sorted({str(row["model"]) for row in trades})
    for model in models:
        for side in ("YES", "NO"):
            selected = [
                row
                for row in trades
                if row["model"] == model
                and row["side"] == side
                and row.get("five_share_executable") == 1
            ]
            if not selected:
                continue
            cost = sum(float(row["entry_cost_usd"]) for row in selected)
            pnl = sum(
                float(row["fee_adjusted_pnl_usd"]) for row in selected
            )
            output.append(
                {
                    "split": split,
                    "model": model,
                    "side": side,
                    "trades": len(selected),
                    "target_dates": len(
                        {str(row["target_date"]) for row in selected}
                    ),
                    "wins": sum(int(row["won"]) for row in selected),
                    "cost_usd": cost,
                    "fee_adjusted_pnl_usd": pnl,
                    "fee_adjusted_roi": pnl / cost if cost else None,
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=FEATURE_ROWS)
    parser.add_argument("--exact-first-seen", type=Path, default=EXACT_FIRST_SEEN)
    parser.add_argument("--market-states", type=Path, default=MARKET_STATES)
    parser.add_argument("--pm-history", type=Path, default=PM_HISTORY)
    parser.add_argument("--raw-books", type=Path, default=RAW_BOOKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    raw_rows = read_rows(args.features)
    continuous = build_continuous_rows(raw_rows)
    eligible = [
        row
        for row in continuous
        if row["remaining_rise_class"] is not None
    ]
    groups = {
        split: [
            row
            for row in eligible
            if split_name(str(row["target_date"])) == split
        ]
        for split in (
            "train",
            "validation",
            "frozen_weather",
            "market_development",
            "market_holdout",
        )
    }
    train = groups["train"]
    validation = groups["validation"]

    binary_v1 = fit_logit(train, "binary_leave_current", multiclass=False)
    binary_v2 = fit_hgb(train, "binary_leave_current")
    full_v1 = fit_logit(train, "remaining_rise_class", multiclass=True)
    full_v2 = fit_hgb(train, "remaining_rise_class")
    full_v3 = fit_ordinal_hazards(train)
    full_v4 = fit_hgb(train, "final_bracket")

    validation_v2 = aligned_probabilities(full_v2, validation)
    validation_v3 = ordinal_probabilities(full_v3, validation)
    temperature_v2 = choose_temperature(validation, validation_v2)
    temperature_v3 = choose_temperature(validation, validation_v3)
    temperature_v4 = choose_absolute_temperature(full_v4, validation)

    historical_scores: list[dict[str, Any]] = []
    historical_binary_scores: list[dict[str, Any]] = []
    predictions_by_split: dict[str, dict[str, np.ndarray]] = {}
    absolute_by_split: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    prediction_rows: list[dict[str, Any]] = []
    for split, rows in groups.items():
        if split == "train" or not rows:
            continue
        p_binary_v1 = binary_probability(binary_v1, rows)
        p_binary_v2 = binary_probability(binary_v2, rows)
        p_full_v1 = aligned_probabilities(full_v1, rows)
        p_full_v2 = apply_temperature(
            aligned_probabilities(full_v2, rows), temperature_v2
        )
        p_full_v3 = apply_temperature(
            ordinal_probabilities(full_v3, rows), temperature_v3
        )
        absolute_classes, p_full_v4_absolute = absolute_probabilities(
            full_v4, rows, temperature=temperature_v4
        )
        p_full_v4 = collapse_absolute(
            absolute_classes, p_full_v4_absolute, rows
        )
        # Binary only identifies current-stay vs leave; the leave mass is
        # intentionally kept in the 3+ bucket for full-score diagnostics.
        p_binary_distribution = np.column_stack(
            [
                1 - p_binary_v2,
                np.zeros(len(rows)),
                np.zeros(len(rows)),
                p_binary_v2,
            ]
        )
        predictions = {
            "binary_v2_hgb": p_binary_distribution,
            "full_v1_multinomial_logit": p_full_v1,
            "full_v2_multinomial_hgb": p_full_v2,
            "full_v3_ordinal_hazard": p_full_v3,
            "full_v4_absolute_ladder_hgb": p_full_v4,
        }
        predictions_by_split[split] = predictions
        absolute_by_split[split] = (
            absolute_classes,
            p_full_v4_absolute,
        )
        historical_scores.extend(model_score_rows(rows, predictions, split))
        for model_name, probability in (
            ("binary_v1_logit", 1 - p_binary_v1),
            ("binary_v2_hgb", 1 - p_binary_v2),
            ("full_v1_multinomial_logit", p_full_v1[:, 0]),
            ("full_v2_multinomial_hgb", p_full_v2[:, 0]),
            ("full_v3_ordinal_hazard", p_full_v3[:, 0]),
            ("full_v4_absolute_ladder_hgb", p_full_v4[:, 0]),
        ):
            historical_binary_scores.append(
                binary_metrics(
                    rows,
                    probability,
                    split=split,
                    model=model_name,
                )
            )
        for row_index, row in enumerate(rows):
            for model_name, probability in predictions.items():
                prediction_rows.append(
                    {
                        "city": "Tokyo",
                        "state_id": row["state_id"],
                        "target_date": row["target_date"],
                        "decision_ts_utc": row["decision_ts_utc"],
                        "model_id": model_name,
                        "state_grain": "continuous_10minute_checkpoint",
                        "current_bracket": row["current_bracket"],
                        "p_delta_0": float(probability[row_index, 0]),
                        "p_delta_1": float(probability[row_index, 1]),
                        "p_delta_2": float(probability[row_index, 2]),
                        "p_delta_3plus": float(probability[row_index, 3]),
                        "label_delta": row["remaining_rise_class"],
                        "split": split,
                        "pit_provenance": row["pit_provenance"],
                        "absolute_distribution_json": (
                            json.dumps(
                                {
                                    str(int(bracket)): float(value)
                                    for bracket, value in zip(
                                        absolute_classes,
                                        p_full_v4_absolute[row_index],
                                    )
                                    if value > 0
                                },
                                sort_keys=True,
                            )
                            if model_name == "full_v4_absolute_ladder_hgb"
                            else None
                        ),
                    }
                )

    exact = exact_first_seen(args.exact_first_seen)
    markets = load_market_states(args.market_states)
    winners = load_winners(
        args.pm_history, date(2026, 7, 15), date(2026, 7, 30)
    )
    market_joined: list[dict[str, Any]] = []
    market_scores: list[dict[str, Any]] = []
    market_binary_score_rows: list[dict[str, Any]] = []
    market_exact_scores: list[dict[str, Any]] = []
    collector_exact_market_scores: list[dict[str, Any]] = []
    collector_exact_binary_scores: list[dict[str, Any]] = []
    collector_exact_ladder_scores: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    trade_summaries: list[dict[str, Any]] = []
    trade_side_summaries: list[dict[str, Any]] = []
    collector_exact_trades: list[dict[str, Any]] = []
    collector_exact_trade_summaries: list[dict[str, Any]] = []
    base_model_names = (
        "binary_v2_hgb",
        "full_v1_multinomial_logit",
        "full_v2_multinomial_hgb",
        "full_v3_ordinal_hazard",
        "full_v4_absolute_ladder_hgb",
    )
    joined_by_split: dict[str, list[dict[str, Any]]] = {}
    for split in ("market_development", "market_holdout"):
        absolute_classes, absolute_probabilities_for_split = absolute_by_split[
            split
        ]
        joined = join_market(
            groups[split],
            predictions_by_split[split],
            absolute_classes,
            absolute_probabilities_for_split,
            exact,
            markets,
            winners,
        )
        for row in joined:
            row["market_split"] = split
        joined_by_split[split] = joined
        market_joined.extend(joined)

    market_blend_weight = choose_market_blend_weight(
        joined_by_split["market_development"]
    )
    for split, joined in joined_by_split.items():
        add_market_blend(joined, market_blend_weight)
        market_scores.extend(
            market_score_rows(joined, base_model_names, split)
        )
        market_binary_score_rows.extend(
            market_binary_scores(joined, base_model_names, split)
        )
        for field, model_name in (
            (
                "full_v4_absolute_ladder_hgb_absolute_json",
                "full_v4_absolute_ladder_hgb",
            ),
            (
                "full_v5_market_anchored_absolute_json",
                "full_v5_market_anchored",
            ),
        ):
            score = exact_score(
                joined, field, split=split, model=model_name
            )
            if score is not None:
                market_exact_scores.append(score)
        market_baseline_score = exact_market_score(joined, split=split)
        if market_baseline_score is not None:
            market_exact_scores.append(market_baseline_score)
        model_names = base_model_names + ("full_v5_market_anchored",)
        selected = select_trades(
            expression_candidates(joined, model_names), args.raw_books
        )
        for row in selected:
            row["market_split"] = split
        trades.extend(selected)
        trade_summaries.extend(trade_summary(selected, split))
        trade_side_summaries.extend(trade_side_summary(selected, split))
        collector_exact = [
            row
            for row in joined
            if row["availability_clock_class"]
            == "collector_exact_hash_verified"
        ]
        if collector_exact:
            exact_split = f"{split}_collector_exact"
            collector_exact_market_scores.extend(
                market_score_rows(
                    collector_exact, base_model_names, exact_split
                )
            )
            collector_exact_binary_scores.extend(
                market_binary_scores(
                    collector_exact, base_model_names, exact_split
                )
            )
            for field, model_name in (
                (
                    "full_v4_absolute_ladder_hgb_absolute_json",
                    "full_v4_absolute_ladder_hgb",
                ),
                (
                    "full_v5_market_anchored_absolute_json",
                    "full_v5_market_anchored",
                ),
            ):
                score = exact_score(
                    collector_exact,
                    field,
                    split=exact_split,
                    model=model_name,
                )
                if score is not None:
                    collector_exact_ladder_scores.append(score)
            baseline = exact_market_score(
                collector_exact, split=exact_split
            )
            if baseline is not None:
                collector_exact_ladder_scores.append(baseline)
            exact_selected = select_trades(
                expression_candidates(collector_exact, model_names),
                args.raw_books,
            )
            for row in exact_selected:
                row["market_split"] = exact_split
            collector_exact_trades.extend(exact_selected)
            collector_exact_trade_summaries.extend(
                trade_summary(exact_selected, exact_split)
            )

    error_cases = sorted(
        [
            row
            for row in trades
            if row.get("five_share_executable") == 1
        ],
        key=lambda row: (
            int(row.get("won", 0)),
            float(row.get("fee_adjusted_pnl_usd", 0)),
        ),
    )
    funnel = [
        {
            "funnel": "signal",
            "stage": "continuous_daytime_checkpoints",
            "unit": "state",
            "count": len(continuous),
            "target_dates": len(
                {str(row["target_date"]) for row in continuous}
            ),
        },
        {
            "funnel": "signal",
            "stage": "market_window_checkpoints",
            "unit": "state",
            "count": sum(
                len(groups[split])
                for split in ("market_development", "market_holdout")
            ),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for split in ("market_development", "market_holdout")
                    for row in groups[split]
                }
            ),
        },
        {
            "funnel": "evidence",
            "stage": "joined_pit_book_and_settlement",
            "unit": "state",
            "count": len(market_joined),
            "target_dates": len(
                {str(row["target_date"]) for row in market_joined}
            ),
        },
        {
            "funnel": "evidence",
            "stage": "collector_exact_joined",
            "unit": "state",
            "count": sum(
                int(
                    row["availability_clock_class"]
                    == "collector_exact_hash_verified"
                )
                for row in market_joined
            ),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in market_joined
                    if row["availability_clock_class"]
                    == "collector_exact_hash_verified"
                }
            ),
        },
        {
            "funnel": "evidence",
            "stage": "five_share_counterfactual",
            "unit": "trade",
            "count": sum(
                int(row.get("five_share_executable") == 1)
                for row in trades
            ),
            "target_dates": len(
                {
                    str(row["target_date"])
                    for row in trades
                    if row.get("five_share_executable") == 1
                }
            ),
        },
    ]

    write_rows(args.out / "continuous_feature_rows.csv.gz", continuous)
    write_rows(args.out / "predictions.csv.gz", prediction_rows)
    write_rows(args.out / "historical_scores.csv", historical_scores)
    write_rows(
        args.out / "historical_binary_scores.csv",
        historical_binary_scores,
    )
    write_rows(args.out / "market_join_rows.csv.gz", market_joined)
    write_rows(args.out / "market_scores.csv", market_scores)
    write_rows(
        args.out / "market_binary_scores.csv",
        market_binary_score_rows,
    )
    write_rows(args.out / "market_exact_scores.csv", market_exact_scores)
    write_rows(
        args.out / "collector_exact_market_scores.csv",
        collector_exact_market_scores,
    )
    write_rows(
        args.out / "collector_exact_binary_scores.csv",
        collector_exact_binary_scores,
    )
    write_rows(
        args.out / "collector_exact_ladder_scores.csv",
        collector_exact_ladder_scores,
    )
    write_rows(args.out / "counterfactual_trades.csv", trades)
    write_rows(args.out / "trade_summary.csv", trade_summaries)
    write_rows(args.out / "trade_side_summary.csv", trade_side_summaries)
    write_rows(
        args.out / "collector_exact_counterfactual_trades.csv",
        collector_exact_trades,
    )
    write_rows(
        args.out / "collector_exact_trade_summary.csv",
        collector_exact_trade_summaries,
    )
    write_rows(args.out / "error_cases.csv", error_cases)
    write_rows(args.out / "funnel.csv", funnel)
    summary = {
        "schema_version": "tokyo_continuous_ladder_probability_v1",
        "state_grain": "every daytime JMA 10-minute checkpoint",
        "hard_lower_bound": (
            "strictly prior RJTT METAR running maximum across all reports; "
            "JMA never hard-zeros a bracket"
        ),
        "raw_feature_rows": len(raw_rows),
        "continuous_rows": len(continuous),
        "continuous_dates": len(
            {str(row["target_date"]) for row in continuous}
        ),
        "historical_lower_bound_violations": sum(
            int(row["historical_lower_bound_violation"])
            for row in continuous
        ),
        "temperature_scaling": {
            "full_v2_multinomial_hgb": temperature_v2,
            "full_v3_ordinal_hazard": temperature_v3,
            "full_v4_absolute_ladder_hgb": temperature_v4,
        },
        "market_anchor_weight_on_weather": market_blend_weight,
        "market_winner_dates": len(winners),
        "market_join_rows": len(market_joined),
        "market_join_dates": len(
            {str(row["target_date"]) for row in market_joined}
        ),
        "market_lower_bound_violations": sum(
            int(row["settlement_lower_bound_violation"])
            for row in market_joined
        ),
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
