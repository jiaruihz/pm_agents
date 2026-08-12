"""Shared D-1/D-2 exact-ladder weather probability calibration.

The incumbent weather model already emits a coherent probability vector for
each native ladder.  This module keeps that physical distribution and learns
only four interpretable corrections from settled weather outcomes:

* temperature (over/under confidence),
* ordinal location shift,
* adjacent-rung diffusion (tail/scale), and
* a small uniform tail floor.

D-1 and D-2 share the same implementation and training prior, while their
lead-specific parameters are fitted separately.  Optional city corrections
only move the centre and are strongly shrunk; cities never learn an
independent tail shape.  Market probabilities are accepted for scoring only
and never enter fitting or prediction.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import ndtr


EPS = 1e-9


@dataclass(frozen=True)
class CalibrationParameters:
    temperature: float = 1.0
    shift: float = 0.0
    diffusion: float = 0.0
    tail_floor: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "temperature": float(self.temperature),
            "shift": float(self.shift),
            "diffusion": float(self.diffusion),
            "tail_floor": float(self.tail_floor),
        }


def _parse_vector(value: Any) -> np.ndarray:
    raw = json.loads(value) if isinstance(value, str) else value
    vector = np.asarray(raw, dtype=float)
    if vector.ndim != 1 or len(vector) < 3 or not np.isfinite(vector).all():
        raise ValueError("probability vector must be finite with at least three rungs")
    vector = np.clip(vector, EPS, None)
    return vector / vector.sum()


def _native_ladder_valid(brackets: list[Any]) -> bool:
    labels = [str(value).replace("°F", "").replace("°C", "").replace("°", "").strip() for value in brackets]
    if len(labels) < 3 or not labels[-1].endswith("+"):
        return False
    ranges: list[tuple[int, int]] = []
    for label in labels:
        values = [int(value) for value in re.findall(r"(?<![\d.])-?\d+", label)]
        if not values:
            return False
        if label.endswith("+"):
            ranges.append((values[0], values[0]))
        elif len(values) >= 2:
            ranges.append((values[0], values[1]))
        else:
            ranges.append((values[0], values[0]))
    internal_widths = [high - low + 1 for low, high in ranges[1:-1]]
    step = 2 if internal_widths and max(internal_widths) == 2 else 1
    if any(width != step for width in internal_widths):
        return False
    return all(right[0] == left[1] + 1 for left, right in zip(ranges, ranges[1:]))


def prepare_probability_rows(rows: pd.DataFrame) -> pd.DataFrame:
    required = {
        "city",
        "target_date",
        "lead_days",
        "brackets_json",
        "model_probs_json",
        "market_probs_json",
        "winner_bracket",
    }
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"probability input missing columns: {missing}")
    records: list[dict[str, Any]] = []
    for source_index, row in rows.reset_index(drop=True).iterrows():
        brackets = json.loads(row["brackets_json"])
        if not _native_ladder_valid(brackets):
            continue
        model = _parse_vector(row["model_probs_json"])
        market = _parse_vector(row["market_probs_json"])
        if len(brackets) != len(model) or len(model) != len(market):
            continue
        matches = [i for i, bracket in enumerate(brackets) if str(bracket) == str(row["winner_bracket"])]
        if len(matches) != 1:
            continue
        records.append(
            {
                "source_index": int(source_index),
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "lead_days": int(row["lead_days"]),
                "winner_index": int(matches[0]),
                "model_probs": model,
                "market_probs": market,
                "rung_count": len(model),
                "physical_probs": (
                    _parse_vector(row["physical_probs_json"])
                    if "physical_probs_json" in rows.columns
                    and pd.notna(row.get("physical_probs_json"))
                    else None
                ),
            }
        )
    result = pd.DataFrame(records)
    if result.empty:
        raise ValueError("no scoreable probability rows")
    return result.sort_values(["target_date", "lead_days", "city", "source_index"]).reset_index(drop=True)


def _diffuse(vector: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 1e-8:
        return vector.copy()
    # A bounded adjacent-rung random walk.  ``amount`` is the mass moved to
    # neighbours; open tails absorb edge mass instead of leaking it.
    weight = min(float(amount), 0.49)
    out = (1.0 - 2.0 * weight) * vector
    out[:-1] += weight * vector[1:]
    out[1:] += weight * vector[:-1]
    out[0] += weight * vector[0]
    out[-1] += weight * vector[-1]
    return out


def _shift(vector: np.ndarray, amount: float) -> np.ndarray:
    if abs(amount) <= 1e-10:
        return vector.copy()
    out = np.zeros_like(vector)
    last = len(vector) - 1
    for index, mass in enumerate(vector):
        destination = min(float(last), max(0.0, index + float(amount)))
        lower = int(math.floor(destination))
        upper = min(last, lower + 1)
        upper_weight = destination - lower
        out[lower] += mass * (1.0 - upper_weight)
        out[upper] += mass * upper_weight
    return out


def transform_probability(
    vector: np.ndarray,
    parameters: CalibrationParameters,
    *,
    city_shift: float = 0.0,
) -> np.ndarray:
    powered = np.power(np.clip(vector, EPS, 1.0), 1.0 / parameters.temperature)
    powered /= powered.sum()
    diffused = _diffuse(powered, parameters.diffusion)
    shifted = _shift(diffused, parameters.shift + city_shift)
    if parameters.tail_floor > 0:
        shifted = (1.0 - parameters.tail_floor) * shifted + parameters.tail_floor / len(shifted)
    shifted = np.clip(shifted, EPS, None)
    return shifted / shifted.sum()


def date_lead_equal_weights(rows: pd.DataFrame) -> np.ndarray:
    group_size = rows.groupby(["lead_days", "target_date"])["target_date"].transform("size").astype(float)
    dates_per_lead = rows.groupby("lead_days")["target_date"].transform("nunique").astype(float)
    lead_count = float(rows["lead_days"].nunique())
    weights = 1.0 / (group_size * dates_per_lead * lead_count)
    return (weights / weights.sum()).to_numpy(dtype=float)


def _transform_matrix(
    matrix: np.ndarray,
    parameters: CalibrationParameters,
) -> np.ndarray:
    powered = np.power(np.clip(matrix, EPS, 1.0), 1.0 / parameters.temperature)
    powered /= powered.sum(axis=1, keepdims=True)
    amount = min(float(parameters.diffusion), 0.49)
    if amount > 1e-8:
        diffused = (1.0 - 2.0 * amount) * powered
        diffused[:, :-1] += amount * powered[:, 1:]
        diffused[:, 1:] += amount * powered[:, :-1]
        diffused[:, 0] += amount * powered[:, 0]
        diffused[:, -1] += amount * powered[:, -1]
    else:
        diffused = powered
    size = matrix.shape[1]
    transport = np.zeros((size, size), dtype=float)
    for index in range(size):
        destination = min(float(size - 1), max(0.0, index + parameters.shift))
        lower = int(math.floor(destination))
        upper = min(size - 1, lower + 1)
        upper_weight = destination - lower
        transport[index, lower] += 1.0 - upper_weight
        transport[index, upper] += upper_weight
    shifted = diffused @ transport
    if parameters.tail_floor > 0:
        shifted = (1.0 - parameters.tail_floor) * shifted + parameters.tail_floor / size
    shifted = np.clip(shifted, EPS, None)
    return shifted / shifted.sum(axis=1, keepdims=True)


def _loss(rows: pd.DataFrame, parameters: CalibrationParameters) -> float:
    weights = date_lead_equal_weights(rows)
    losses = np.empty(len(rows), dtype=float)
    for _, positions in rows.groupby("rung_count", sort=False).groups.items():
        indexes = np.asarray(list(positions), dtype=int)
        matrix = np.vstack(rows.loc[indexes, "model_probs"].to_numpy())
        probability = _transform_matrix(matrix, parameters)
        winners = rows.loc[indexes, "winner_index"].to_numpy(dtype=int)
        losses[indexes] = -np.log(np.maximum(EPS, probability[np.arange(len(indexes)), winners]))
    return float(np.dot(weights, losses))


def fit_calibration(rows: pd.DataFrame) -> CalibrationParameters:
    if rows.empty:
        raise ValueError("cannot fit an empty calibration frame")
    rows = rows.reset_index(drop=True)

    def objective(values: np.ndarray) -> float:
        return _loss(
            rows,
            CalibrationParameters(
                temperature=float(values[0]),
                shift=float(values[1]),
                diffusion=float(values[2]),
                tail_floor=float(values[3]),
            ),
        )

    starts = (
        np.asarray([1.0, 0.0, 0.0, 0.0]),
        np.asarray([1.25, 0.0, 0.10, 0.01]),
        np.asarray([1.0, -0.25, 0.05, 0.01]),
        np.asarray([1.0, 0.25, 0.05, 0.01]),
    )
    bounds = ((0.50, 3.00), (-1.50, 1.50), (0.0, 0.49), (0.0, 0.10))
    results = [
        minimize(
            objective,
            start,
            method="Nelder-Mead",
            bounds=bounds,
            options={"maxiter": 250, "xatol": 1e-4, "fatol": 1e-6},
        )
        for start in starts
    ]
    selected = min(results, key=lambda result: float(result.fun))
    return CalibrationParameters(
        temperature=float(selected.x[0]),
        shift=float(selected.x[1]),
        diffusion=float(selected.x[2]),
        tail_floor=float(selected.x[3]),
    )


def fit_lead_calibrations(rows: pd.DataFrame) -> dict[int, CalibrationParameters]:
    return {
        int(lead): fit_calibration(group)
        for lead, group in rows.groupby("lead_days", sort=True)
    }


def fit_city_shifts(
    rows: pd.DataFrame,
    lead_parameters: dict[int, CalibrationParameters],
    *,
    shrinkage: float,
) -> dict[tuple[int, str], float]:
    residual_rows: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        probability = transform_probability(row.model_probs, lead_parameters[int(row.lead_days)])
        expected = float(np.dot(np.arange(len(probability), dtype=float), probability))
        residual_rows.append(
            {
                "lead_days": int(row.lead_days),
                "city": str(row.city),
                "residual": float(row.winner_index) - expected,
            }
        )
    residuals = pd.DataFrame(residual_rows)
    output: dict[tuple[int, str], float] = {}
    for (lead, city), group in residuals.groupby(["lead_days", "city"]):
        n = float(len(group))
        output[(int(lead), str(city))] = float(n / (n + shrinkage) * group["residual"].mean())
    return output


def predict_rows(
    rows: pd.DataFrame,
    *,
    arm: str,
    shared_parameters: CalibrationParameters | None = None,
    lead_parameters: dict[int, CalibrationParameters] | None = None,
    city_shifts: dict[tuple[int, str], float] | None = None,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        if arm == "raw_weather":
            probability = row.model_probs
        elif arm == "market":
            probability = row.market_probs
        else:
            parameters = (
                lead_parameters[int(row.lead_days)]
                if lead_parameters is not None
                else shared_parameters
            )
            if parameters is None:
                raise ValueError(f"missing parameters for arm={arm}")
            city_shift = (city_shifts or {}).get((int(row.lead_days), str(row.city)), 0.0)
            probability = transform_probability(row.model_probs, parameters, city_shift=city_shift)
        winner = int(row.winner_index)
        target = np.zeros(len(probability), dtype=float)
        target[winner] = 1.0
        cumulative_error = np.cumsum(probability)[:-1] - np.cumsum(target)[:-1]
        records.append(
            {
                "source_index": int(row.source_index),
                "city": str(row.city),
                "target_date": str(row.target_date),
                "lead_days": int(row.lead_days),
                "arm": arm,
                "logloss": -math.log(max(EPS, float(probability[winner]))),
                "brier": float(np.square(probability - target).mean()),
                "rps": float(np.square(cumulative_error).mean()),
                "winner_probability": float(probability[winner]),
                "top1_accuracy": float(int(np.argmax(probability) == winner)),
                "probabilities_json": json.dumps(probability.tolist(), separators=(",", ":")),
                "winner_index": winner,
            }
        )
    return pd.DataFrame(records)


def summarize_scores(scored: pd.DataFrame) -> pd.DataFrame:
    metrics = ("logloss", "brier", "rps", "winner_probability", "top1_accuracy")
    daily = scored.groupby(["lead_days", "target_date", "arm"], as_index=False)[list(metrics)].mean()
    summary = daily.groupby(["lead_days", "arm"], as_index=False)[list(metrics)].mean()
    counts = scored.groupby(["lead_days", "arm"]).agg(
        states=("source_index", "size"),
        dates=("target_date", "nunique"),
        cities=("city", "nunique"),
    ).reset_index()
    return summary.merge(counts, on=["lead_days", "arm"])


def block_bootstrap_delta(
    scored: pd.DataFrame,
    *,
    left: str,
    right: str,
    metric: str = "logloss",
    draws: int = 20_000,
    seed: int = 20260812,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for lead, group in scored.groupby("lead_days"):
        daily = group[group["arm"].isin([left, right])].groupby(["target_date", "arm"])[metric].mean().unstack().dropna()
        delta = (daily[left] - daily[right]).to_numpy(dtype=float)
        rng = np.random.default_rng(seed + int(lead))
        sampled = rng.choice(delta, size=(draws, len(delta)), replace=True).mean(axis=1)
        output.append(
            {
                "lead_days": int(lead),
                "left": left,
                "right": right,
                "metric": metric,
                "dates": int(len(delta)),
                "delta": float(delta.mean()),
                "ci_low": float(np.quantile(sampled, 0.025)),
                "ci_high": float(np.quantile(sampled, 0.975)),
            }
        )
    return output


def fit_model_bundle(
    train: pd.DataFrame,
    *,
    city_shrinkage: float = 60.0,
) -> dict[str, Any]:
    shared = fit_calibration(train)
    by_lead = fit_lead_calibrations(train)
    city = fit_city_shifts(train, by_lead, shrinkage=city_shrinkage)
    return {
        "shared_parameters": shared,
        "lead_parameters": by_lead,
        "city_shifts": city,
        "city_shrinkage": float(city_shrinkage),
    }


def score_model_bundle(rows: pd.DataFrame, bundle: dict[str, Any]) -> pd.DataFrame:
    frames = [
        predict_rows(rows, arm="raw_weather"),
        predict_rows(rows, arm="market"),
        predict_rows(rows, arm="shared_calibration", shared_parameters=bundle["shared_parameters"]),
        predict_rows(rows, arm="lead_calibration", lead_parameters=bundle["lead_parameters"]),
        predict_rows(
            rows,
            arm="lead_partial_city",
            lead_parameters=bundle["lead_parameters"],
            city_shifts=bundle["city_shifts"],
        ),
    ]
    return pd.concat(frames, ignore_index=True)


def serialize_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "d1_d2_shared_weather_probability_v1",
        "model_identity": "d1_d2_weather_only_probability",
        "market_features_used": False,
        "shared_parameters": bundle["shared_parameters"].as_dict(),
        "lead_parameters": {
            str(lead): parameters.as_dict()
            for lead, parameters in bundle["lead_parameters"].items()
        },
        "city_shrinkage": bundle["city_shrinkage"],
        "city_shifts": {
            f"{lead}|{city}": float(value)
            for (lead, city), value in sorted(bundle["city_shifts"].items())
        },
    }


def _model_family(value: Any) -> str:
    text = str(value or "").lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text or "ncep" in text:
        return "gfs"
    return text


def build_historical_error_bank(history: pd.DataFrame) -> dict[str, Any]:
    required = {"city", "model", "error_f_actual_minus_forecast"}
    missing = sorted(required - set(history.columns))
    if missing:
        raise ValueError(f"history missing columns: {missing}")
    frame = history.copy()
    frame["model_family"] = frame["model"].map(_model_family)
    frame["error_f"] = pd.to_numeric(frame["error_f_actual_minus_forecast"], errors="coerce")
    frame = frame[np.isfinite(frame["error_f"])].copy()
    return {
        "city_model": {
            (str(city), str(model)): group["error_f"].to_numpy(dtype=float)
            for (city, model), group in frame.groupby(["city", "model_family"])
        },
        "model": {
            str(model): group["error_f"].to_numpy(dtype=float)
            for model, group in frame.groupby("model_family")
        },
        "rows": int(len(frame)),
        "cities": int(frame["city"].nunique()),
    }


def _bracket_ranges(brackets: list[Any]) -> tuple[list[tuple[float | None, float | None]], str]:
    labels = [str(value).replace("°F", "").replace("°C", "").replace("°", "").strip() for value in brackets]
    unit = "F" if any("-" in label[1:] for label in labels[1:-1]) else "C"
    output: list[tuple[float | None, float | None]] = []
    for index, label in enumerate(labels):
        values = [float(value) for value in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", label)]
        if index == 0:
            output.append((None, values[-1]))
        elif index == len(labels) - 1:
            output.append((values[0], None))
        elif len(values) >= 2:
            output.append((values[0], values[1]))
        else:
            output.append((values[0], values[0]))
    return output, unit


def empirical_forecast_probability(
    *,
    forecast_max_f: float,
    errors_f: np.ndarray,
    brackets: list[Any],
    kernel_sd_f: float = 0.75,
) -> np.ndarray:
    ranges, unit = _bracket_ranges(brackets)
    values_f = float(forecast_max_f) + np.asarray(errors_f, dtype=float)
    values = (values_f - 32.0) * 5.0 / 9.0 if unit == "C" else values_f
    kernel = kernel_sd_f * (5.0 / 9.0 if unit == "C" else 1.0)
    probabilities: list[float] = []
    for low, high in ranges:
        if low is None:
            probabilities.append(float(np.mean(ndtr((float(high) + 0.5 - values) / kernel))))
        elif high is None:
            probabilities.append(float(np.mean(1.0 - ndtr((float(low) - 0.5 - values) / kernel))))
        else:
            probabilities.append(
                float(
                    np.mean(
                        ndtr((float(high) + 0.5 - values) / kernel)
                        - ndtr((float(low) - 0.5 - values) / kernel)
                    )
                )
            )
    vector = np.clip(np.asarray(probabilities, dtype=float), EPS, None)
    return vector / vector.sum()


def attach_empirical_physical_prior(
    raw_probability_rows: pd.DataFrame,
    event_rungs: pd.DataFrame,
    history: pd.DataFrame,
) -> pd.DataFrame:
    required = {"snapshot_id", "forecast_max_f", "forecast_model"}
    missing = sorted(required - set(event_rungs.columns))
    if missing:
        raise ValueError(f"event rungs missing columns: {missing}")
    lookup_columns = ["snapshot_id", "forecast_max_f"]
    if "forecast_model" not in raw_probability_rows.columns:
        lookup_columns.append("forecast_model")
    lookup = (
        event_rungs[lookup_columns]
        .drop_duplicates("snapshot_id")
        .copy()
    )
    merged = raw_probability_rows.merge(lookup, on="snapshot_id", how="left", validate="many_to_one")
    bank = build_historical_error_bank(history)
    physical: list[str | None] = []
    for row in merged.itertuples(index=False):
        forecast = getattr(row, "forecast_max_f", None)
        model = _model_family(getattr(row, "forecast_model", None))
        errors = bank["city_model"].get((str(row.city), model))
        if errors is None:
            errors = bank["model"].get(model)
        if forecast is None or not math.isfinite(float(forecast)) or errors is None or len(errors) < 30:
            physical.append(None)
            continue
        vector = empirical_forecast_probability(
            forecast_max_f=float(forecast),
            errors_f=errors,
            brackets=json.loads(row.brackets_json),
        )
        physical.append(json.dumps(vector.tolist(), separators=(",", ":")))
    merged["physical_probs_json"] = physical
    merged.attrs["historical_error_bank"] = {"rows": bank["rows"], "cities": bank["cities"]}
    return merged
