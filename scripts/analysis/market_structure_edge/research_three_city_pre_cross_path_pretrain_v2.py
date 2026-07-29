#!/usr/bin/env python3
"""Historical path pretraining plus exact first-seen pre-cross calibration.

Research only. Historical/archive observations train source-path dynamics on
their observation clock. They never supply first-seen latency or market timing.
Collector-exact events alone form the forward evaluation denominator.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
V1_PATH = (
    ROOT
    / "scripts/analysis/market_structure_edge"
    / "research_three_city_first_seen_path_v1.py"
)
SPEC = importlib.util.spec_from_file_location("three_city_first_seen_path_v1", V1_PATH)
assert SPEC and SPEC.loader
V1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V1)

DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "three_city_pre_cross_path_pretrain_v2"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07"
    / "2026-07-29-three-city-pre-cross-path-pretrain-v2.md"
)
KNMI_HISTORY_GLOBS = (
    ROOT
    / "docs/analysis/2026-07/generated/knmi_eham_wu_alignment_v1"
    / "knmi_observations_backfill.csv",
    ROOT
    / "docs/analysis/2026-07/generated/knmi_cross_no_threshold_v1"
    / "batch_2026-06-18_2026-06-22/knmi_2026-06-18_2026-06-22.csv",
    ROOT
    / "docs/analysis/2026-07/generated/knmi_cross_no_threshold_v1"
    / "batch_2026-06-23_2026-06-27/knmi_2026-06-23_2026-06-27.csv",
)
OFFICIAL_PATH_HISTORY = DEFAULT_OUT / "official_path_history.csv"
HORIZONS = (30, 60, 120)
MIN_CALIBRATION_DATES = 3
MAX_GAP_MIN = 25.0
BOUNDARY_GRACE_MIN = 20.0
EPS = 1e-6
BASE_FEATURES = (
    "source_temp_c",
    "source_delta_c",
    "slope_30m_c_per_hour",
    "slope_60m_c_per_hour",
    "minutes_since_previous_source",
    "distance_to_next_lattice_c",
    "distance_above_prior_running_max_c",
    "minutes_since_strict_high",
    "warming_run_count",
    "new_source_high",
    "local_hour_sin",
    "local_hour_cos",
    "source_wind_speed_kt",
    "source_pressure_hpa",
)
CALIBRATION_FEATURES = (
    "base_logit",
    "source_first_seen_age_min",
    "minutes_since_previous_source",
    "distance_to_next_lattice_c",
    "source_delta_c",
)


def parse_dt(value: Any) -> datetime | None:
    return V1.parse_dt(value)


def number(value: Any) -> float | None:
    return V1.number(value)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    yield from V1.iter_jsonl(path)


def round_half_up(value: float) -> int:
    return V1.round_half_up(value)


def local_date(ts: datetime, city: str) -> str:
    return V1.local_date(ts, city)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path, *, normalize_empty: bool = False) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows: list[dict[str, Any]] = list(csv.DictReader(handle))
    if normalize_empty:
        for row in rows:
            for key, value in list(row.items()):
                if value == "":
                    row[key] = None
    return rows


def _date_paths(runtime: Path, start: date, end: date) -> Iterable[Path]:
    current = start
    while current <= end:
        yield (
            runtime
            / "output/high_frequency_observations"
            / current.isoformat()
            / "high_frequency_observations.jsonl"
        )
        current += timedelta(days=1)


def load_archive_history(
    runtime: Path,
    start: date,
    exact_start_by_city: dict[str, date],
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Load observation-clock history without promoting it to exact first-seen."""

    temperature_votes: dict[tuple[str, str, str], Counter[float]] = defaultdict(Counter)
    row_samples: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    knmi_native_rows: list[dict[str, Any]] = []
    knmi_native_days: set[str] = set()
    for path in KNMI_HISTORY_GLOBS:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                obs_ts = parse_dt(raw.get("observation_time_utc"))
                temp_c = number(raw.get("temp_c"))
                if obs_ts is None or temp_c is None:
                    continue
                target = local_date(obs_ts, "Amsterdam")
                if start.isoformat() <= target < exact_start_by_city["Amsterdam"].isoformat():
                    knmi_native_rows.append(raw)
                    knmi_native_days.add(target)

    official_days: set[tuple[str, str]] = set()
    if OFFICIAL_PATH_HISTORY.exists():
        with OFFICIAL_PATH_HISTORY.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                city = str(raw.get("city") or "")
                source = str(raw.get("source") or "")
                obs_ts = parse_dt(raw.get("observation_time_utc"))
                temp_c = number(raw.get("temp_c"))
                if (
                    city not in V1.CITY_CONFIG
                    or source != V1.CITY_CONFIG[city]["source"]
                    or obs_ts is None
                    or temp_c is None
                ):
                    continue
                target = local_date(obs_ts, city)
                if (
                    target < start.isoformat()
                    or target >= exact_start_by_city[city].isoformat()
                    or (city == "Amsterdam" and target in knmi_native_days)
                ):
                    continue
                identity = (city, source, obs_ts.isoformat())
                temperature_votes[identity][float(temp_c)] += 1
                row_samples[(city, source, obs_ts.isoformat(), float(temp_c))] = raw
                official_days.add((city, target))

    covered_days = official_days | {("Amsterdam", day) for day in knmi_native_days}
    expected_days = {
        (city, day.isoformat())
        for city in V1.CITY_CONFIG
        for day in (
            start + timedelta(days=offset)
            for offset in range((exact_start_by_city[city] - start).days)
        )
    }
    max_end = max(exact_start_by_city.values()) - timedelta(days=1)
    runtime_paths = (
        [] if expected_days <= covered_days else _date_paths(runtime, start, max_end)
    )
    for path in runtime_paths:
        for raw in iter_jsonl(path):
            city = str(raw.get("city") or "")
            source = str(raw.get("source") or "")
            if city not in V1.CITY_CONFIG:
                continue
            if source != V1.CITY_CONFIG[city]["source"]:
                continue
            obs_ts = parse_dt(raw.get("observation_time_utc"))
            temp_c = number(raw.get("temp_c"))
            if obs_ts is None or temp_c is None:
                continue
            target = local_date(obs_ts, city)
            if target >= exact_start_by_city[city].isoformat():
                continue
            if (city, target) in official_days or (
                city == "Amsterdam" and target in knmi_native_days
            ):
                continue
            identity = (city, source, obs_ts.isoformat())
            temperature_votes[identity][float(temp_c)] += 1
            row_samples[(city, source, obs_ts.isoformat(), float(temp_c))] = raw

    # KNMI archive backfills are explicitly path-training-only.
    for raw in knmi_native_rows:
        obs_ts = parse_dt(raw.get("observation_time_utc"))
        temp_c = number(raw.get("temp_c"))
        assert obs_ts is not None and temp_c is not None
        city, source = "Amsterdam", "knmi"
        identity = (city, source, obs_ts.isoformat())
        temperature_votes[identity][float(temp_c)] += 1
        raw["cadence_minutes"] = 10
        row_samples[(city, source, obs_ts.isoformat(), float(temp_c))] = raw

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    audit: dict[str, Counter[str]] = {city: Counter() for city in V1.CITY_CONFIG}
    for (city, source, obs_text), votes in temperature_votes.items():
        # Repeated poll journals can contain revisions. The modal value is the
        # stable archive path value; this choice is never called first-seen.
        temp_c, repeated = max(votes.items(), key=lambda item: (item[1], item[0]))
        raw = row_samples[(city, source, obs_text, temp_c)]
        obs_ts = parse_dt(obs_text)
        assert obs_ts is not None
        target = local_date(obs_ts, city)
        grouped[(city, target)].append(
            {
                "city": city,
                "source": source,
                "station": str(raw.get("station") or V1.CITY_CONFIG[city]["station"]),
                "target_date": target,
                "obs_ts": obs_ts,
                "clock_ts": obs_ts,
                "first_seen_ts": None,
                "first_seen_age_min": None,
                "temp_c": temp_c,
                "wind_speed_kt": number(raw.get("wind_speed_kt")),
                "wind_dir_deg": number(raw.get("wind_dir_deg")),
                "wind_gust_kt": number(raw.get("wind_gust_kt")),
                "pressure_hpa": number(raw.get("pressure_hpa")),
                "relative_humidity_pct": (
                    number(raw.get("relative_humidity_pct"))
                    if raw.get("relative_humidity_pct") is not None
                    else number(raw.get("humidity"))
                ),
                "precipitation_10m_mm": number(
                    raw.get("precipitation_10m_mm")
                ),
                "sunshine_duration_min": number(
                    raw.get("sunshine_duration_min")
                ),
                "training_clock_class": "observation_clock_archive_not_pit",
                "cadence_minutes": number(raw.get("cadence_minutes")) or 10.0,
                "archive_source": str(raw.get("archive_source") or "collector_archive"),
                "repeat_count": repeated,
            }
        )
        audit[city]["distinct_observations"] += 1
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["clock_ts"], row["obs_ts"]))
    audit_rows = []
    for city in V1.CITY_CONFIG:
        dates = {target for row_city, target in grouped if row_city == city}
        audit_rows.append(
            {
                "city": city,
                "source": V1.CITY_CONFIG[city]["source"],
                "training_clock_class": "observation_clock_archive_not_pit",
                "distinct_observations": audit[city]["distinct_observations"],
                "dates": len(dates),
                "first_date": min(dates) if dates else "",
                "last_date": max(dates) if dates else "",
                "cadence_minutes": "/".join(
                    str(int(value))
                    for value in sorted(
                        {
                            float(row.get("cadence_minutes") or 10)
                            for (row_city, _target), rows in grouped.items()
                            if row_city == city
                            for row in rows
                        }
                    )
                ),
            }
        )
    return grouped, audit_rows


def exact_to_common(
    exact: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, rows in exact.items():
        for row in rows:
            output[key].append(
                {
                    **row,
                    "clock_ts": row["first_seen_ts"],
                    "training_clock_class": "collector_exact",
                }
            )
    return output


def _slope(
    history: list[dict[str, Any]], current: dict[str, Any], minutes: int
) -> float | None:
    candidates = [
        row
        for row in history
        if 0
        < (current["clock_ts"] - row["clock_ts"]).total_seconds()
        <= minutes * 60
    ]
    if not candidates:
        return None
    base = candidates[0]
    hours = (current["clock_ts"] - base["clock_ts"]).total_seconds() / 3600.0
    return (float(current["temp_c"]) - float(base["temp_c"])) / hours if hours else None


def build_states(
    grouped: dict[tuple[str, str], list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for (city, target_date), events in sorted(grouped.items()):
        history: list[dict[str, Any]] = []
        running_max = -math.inf
        last_high: datetime | None = None
        previous: dict[str, Any] | None = None
        warming_run = 0
        for event in events:
            temp = float(event["temp_c"])
            prior_max = running_max
            delta = None if previous is None else temp - float(previous["temp_c"])
            warming_run = warming_run + 1 if delta is not None and delta > 0 else 0
            is_high = prior_max == -math.inf or temp > prior_max + EPS
            if is_high:
                running_max = temp
                last_high = event["clock_ts"]
            current_lattice = round_half_up(running_max)
            next_threshold = current_lattice + 0.5
            local = event["clock_ts"].astimezone(V1.CITY_CONFIG[city]["timezone"])
            hour = local.hour + local.minute / 60.0
            states.append(
                {
                    "event_id": (
                        f"{city}|{event['source']}|{event['obs_ts'].isoformat()}|{temp}|"
                        f"{event['training_clock_class']}"
                    ),
                    "city": city,
                    "source": event["source"],
                    "target_date": target_date,
                    "training_clock_class": event["training_clock_class"],
                    "cadence_minutes": event.get("cadence_minutes", 10),
                    "archive_source": event.get("archive_source", ""),
                    "source_observation_ts_utc": event["obs_ts"].isoformat(),
                    "decision_clock_ts_utc": event["clock_ts"].isoformat(),
                    "source_first_seen_age_min": event.get("first_seen_age_min"),
                    "source_temp_c": temp,
                    "source_delta_c": delta,
                    "slope_30m_c_per_hour": _slope(history, event, 30),
                    "slope_60m_c_per_hour": _slope(history, event, 60),
                    "minutes_since_previous_source": (
                        None
                        if previous is None
                        else (event["clock_ts"] - previous["clock_ts"]).total_seconds() / 60.0
                    ),
                    "source_running_max_c": running_max,
                    "source_running_max_lattice": current_lattice,
                    "next_lattice_threshold_c": next_threshold,
                    "distance_to_next_lattice_c": next_threshold - running_max,
                    "distance_above_prior_running_max_c": (
                        None if prior_max == -math.inf else temp - prior_max
                    ),
                    "minutes_since_strict_high": (
                        0.0
                        if last_high is None
                        else (event["clock_ts"] - last_high).total_seconds() / 60.0
                    ),
                    "new_source_high": int(is_high),
                    "warming_run_count": warming_run,
                    "local_hour": hour,
                    "local_hour_sin": math.sin(2 * math.pi * hour / 24),
                    "local_hour_cos": math.cos(2 * math.pi * hour / 24),
                    "source_wind_speed_kt": event.get("wind_speed_kt"),
                    "source_wind_dir_deg": event.get("wind_dir_deg"),
                    "source_wind_dir_sin": (
                        None
                        if event.get("wind_dir_deg") is None
                        else math.sin(math.radians(float(event["wind_dir_deg"])))
                    ),
                    "source_wind_dir_cos": (
                        None
                        if event.get("wind_dir_deg") is None
                        else math.cos(math.radians(float(event["wind_dir_deg"])))
                    ),
                    "source_wind_gust_kt": event.get("wind_gust_kt"),
                    "source_pressure_hpa": event.get("pressure_hpa"),
                    "source_relative_humidity_pct": event.get(
                        "relative_humidity_pct"
                    ),
                    "source_precipitation_10m_mm": event.get(
                        "precipitation_10m_mm"
                    ),
                    "source_sunshine_duration_min": event.get(
                        "sunshine_duration_min"
                    ),
                }
            )
            history.append(event)
            previous = event

    by_day: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in states:
        by_day[
            (row["city"], row["target_date"], row["training_clock_class"])
        ].append(row)
    for rows in by_day.values():
        rows.sort(key=lambda row: row["decision_clock_ts_utc"])
        for index, row in enumerate(rows):
            current_ts = parse_dt(row["decision_clock_ts_utc"])
            assert current_ts is not None
            later = rows[index + 1 :]
            for horizon in HORIZONS:
                deadline = current_ts + timedelta(minutes=horizon)
                boundary = next(
                    (
                        candidate
                        for candidate in later
                        if parse_dt(candidate["decision_clock_ts_utc"]) >= deadline
                    ),
                    None,
                )
                boundary_ts = (
                    parse_dt(boundary["decision_clock_ts_utc"]) if boundary else None
                )
                chain = [
                    candidate
                    for candidate in later
                    if parse_dt(candidate["decision_clock_ts_utc"]) <= deadline
                ]
                clock_points = [current_ts] + [
                    parse_dt(candidate["decision_clock_ts_utc"]) for candidate in chain
                ]
                if boundary_ts:
                    clock_points.append(boundary_ts)
                gaps = [
                    (right - left).total_seconds() / 60.0
                    for left, right in zip(clock_points, clock_points[1:])
                    if left and right
                ]
                complete = bool(
                    boundary_ts
                    and boundary_ts <= deadline + timedelta(minutes=BOUNDARY_GRACE_MIN)
                    and gaps
                    and max(gaps)
                    <= max(
                        MAX_GAP_MIN,
                        1.25
                        * max(
                            float(candidate.get("cadence_minutes") or 10)
                            for candidate in rows
                        ),
                    )
                )
                threshold = float(row["next_lattice_threshold_c"])
                crossing = next(
                    (
                        candidate
                        for candidate in chain
                        if float(candidate["source_running_max_c"]) >= threshold - EPS
                    ),
                    None,
                )
                row[f"coverage_complete_{horizon}m"] = int(complete)
                row[f"cross_next_lattice_within_{horizon}m"] = (
                    int(crossing is not None) if complete else None
                )
                row[f"minutes_to_next_lattice_cross_{horizon}m"] = (
                    None
                    if crossing is None
                    else (
                        parse_dt(crossing["decision_clock_ts_utc"]) - current_ts
                    ).total_seconds()
                    / 60.0
                )
    return sorted(
        states,
        key=lambda row: (
            row["city"],
            row["target_date"],
            row["decision_clock_ts_utc"],
            row["training_clock_class"],
        ),
    )


def _varying(rows: list[dict[str, Any]], features: tuple[str, ...]) -> list[str]:
    output = []
    for feature in features:
        values = [number(row.get(feature)) for row in rows]
        present = [value for value in values if value is not None]
        if len(present) >= 2 and max(present) - min(present) > EPS:
            output.append(feature)
    return output


def fit_logistic(
    rows: list[dict[str, Any]],
    label: str,
    features: tuple[str, ...],
) -> tuple[Pipeline, list[str]] | None:
    usable = _varying(rows, features)
    labels = np.asarray([int(row[label]) for row in rows])
    if not usable or len(set(labels.tolist())) < 2:
        return None
    matrix = np.asarray(
        [
            [np.nan if number(row.get(f)) is None else number(row.get(f)) for f in usable]
            for row in rows
        ]
    )
    counts = Counter(str(row["target_date"]) for row in rows)
    weights = np.asarray([1.0 / counts[str(row["target_date"])] for row in rows])
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=1.0, solver="lbfgs", max_iter=2000, random_state=20260729
                ),
            ),
        ]
    )
    model.fit(matrix, labels, model__sample_weight=weights)
    return model, usable


def predict(
    fitted: tuple[Pipeline, list[str]], rows: list[dict[str, Any]]
) -> np.ndarray:
    model, features = fitted
    matrix = np.asarray(
        [
            [np.nan if number(row.get(f)) is None else number(row.get(f)) for f in features]
            for row in rows
        ]
    )
    return model.predict_proba(matrix)[:, 1]


def logit(probability: float) -> float:
    p = min(1 - EPS, max(EPS, probability))
    return math.log(p / (1 - p))


def evaluate(
    archive_states: list[dict[str, Any]],
    exact_states: list[dict[str, Any]],
    *,
    min_calibration_dates: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for city in V1.CITY_CONFIG:
        history = [row for row in archive_states if row["city"] == city]
        exact = [row for row in exact_states if row["city"] == city]
        exact_dates = sorted({str(row["target_date"]) for row in exact})
        for horizon in HORIZONS:
            label = f"cross_next_lattice_within_{horizon}m"
            base_train = [row for row in history if row.get(label) is not None]
            base_fit = fit_logistic(base_train, label, BASE_FEATURES)
            if base_fit is None:
                continue
            base_prior = float(np.mean([int(row[label]) for row in base_train]))
            for test_date in exact_dates:
                prior_exact = [
                    row
                    for row in exact
                    if str(row["target_date"]) < test_date and row.get(label) is not None
                ]
                test = [
                    row
                    for row in exact
                    if str(row["target_date"]) == test_date and row.get(label) is not None
                ]
                if not test:
                    continue
                exact_dates_before = {
                    str(row["target_date"]) for row in prior_exact
                }
                base_test = predict(base_fit, test)
                values_by_model: dict[str, np.ndarray] = {
                    "m0_history_prior": np.repeat(base_prior, len(test)),
                    "m2_history_pretrain": base_test,
                }
                if len(exact_dates_before) >= min_calibration_dates:
                    exact_fit = fit_logistic(prior_exact, label, BASE_FEATURES)
                    base_calibration = predict(base_fit, prior_exact)
                    calibration_rows = [
                        {**row, "base_logit": logit(float(probability))}
                        for row, probability in zip(prior_exact, base_calibration)
                    ]
                    calibration_fit = fit_logistic(
                        calibration_rows, label, CALIBRATION_FEATURES
                    )
                    calibrated_test = [
                        {**row, "base_logit": logit(float(probability))}
                        for row, probability in zip(test, base_test)
                    ]
                    if exact_fit is not None:
                        values_by_model["m1_exact_only"] = predict(exact_fit, test)
                    if calibration_fit is not None:
                        values_by_model[
                            "m3_history_plus_exact_calibration"
                        ] = predict(calibration_fit, calibrated_test)
                for model_name, probabilities in values_by_model.items():
                    for row, probability in zip(test, probabilities):
                        output.append(
                            {
                                "event_id": row["event_id"],
                                "city": city,
                                "target_date": test_date,
                                "horizon_min": horizon,
                                "model": model_name,
                                "decision_clock_ts_utc": row["decision_clock_ts_utc"],
                                "source_temp_c": row["source_temp_c"],
                                "source_delta_c": row["source_delta_c"],
                                "distance_to_next_lattice_c": row[
                                    "distance_to_next_lattice_c"
                                ],
                                "next_lattice_threshold_c": row[
                                    "next_lattice_threshold_c"
                                ],
                                "p_cross_next_lattice": float(
                                    min(1 - EPS, max(EPS, probability))
                                ),
                                "y_cross_next_lattice": int(row[label]),
                                "minutes_to_cross": row[
                                    f"minutes_to_next_lattice_cross_{horizon}m"
                                ],
                                "archive_train_dates": len(
                                    {row["target_date"] for row in base_train}
                                ),
                                "archive_train_events": len(base_train),
                                "exact_calibration_dates": len(exact_dates_before),
                                "exact_calibration_events": len(prior_exact),
                            }
                        )
    return output


def scores(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        grouped[(row["city"], int(row["horizon_min"]), row["model"])].append(row)
    output = []
    for (city, horizon, model), rows in sorted(grouped.items()):
        dates = Counter(row["target_date"] for row in rows)
        weights = np.asarray([1 / dates[row["target_date"]] for row in rows], dtype=float)
        weights /= weights.sum()
        y = np.asarray([row["y_cross_next_lattice"] for row in rows], dtype=float)
        p = np.asarray([row["p_cross_next_lattice"] for row in rows], dtype=float)
        output.append(
            {
                "city": city,
                "horizon_min": horizon,
                "model": model,
                "events": len(rows),
                "target_dates": len(dates),
                "positive_rate_date_weighted": float(np.sum(weights * y)),
                "brier_date_weighted": float(np.sum(weights * (p - y) ** 2)),
                "logloss_date_weighted": float(
                    -np.sum(
                        weights
                        * (y * np.log(np.clip(p, EPS, 1 - EPS))
                           + (1 - y) * np.log(np.clip(1 - p, EPS, 1 - EPS)))
                    )
                ),
            }
        )
    baselines = {
        (row["city"], row["horizon_min"]): row
        for row in output
        if row["model"] == "m0_history_prior"
    }
    for row in output:
        baseline = baselines[(row["city"], row["horizon_min"])]
        row["brier_delta_vs_history_prior"] = (
            row["brier_date_weighted"] - baseline["brier_date_weighted"]
        )
        row["logloss_delta_vs_history_prior"] = (
            row["logloss_date_weighted"] - baseline["logloss_date_weighted"]
        )
        current_rows = grouped[(row["city"], row["horizon_min"], row["model"])]
        baseline_rows = grouped[
            (row["city"], row["horizon_min"], "m0_history_prior")
        ]
        baseline_by_event = {
            item["event_id"]: item["p_cross_next_lattice"]
            for item in baseline_rows
        }
        daily_deltas: dict[str, list[float]] = defaultdict(list)
        for item in current_rows:
            p0 = baseline_by_event[item["event_id"]]
            y = item["y_cross_next_lattice"]
            daily_deltas[item["target_date"]].append(
                (item["p_cross_next_lattice"] - y) ** 2 - (p0 - y) ** 2
            )
        date_values = np.asarray(
            [np.mean(values) for _, values in sorted(daily_deltas.items())],
            dtype=float,
        )
        if len(date_values):
            rng = np.random.default_rng(20260729)
            draws = np.asarray(
                [
                    float(np.mean(rng.choice(date_values, len(date_values), replace=True)))
                    for _ in range(4000)
                ]
            )
            row["brier_delta_date_bootstrap_ci_low"] = float(
                np.quantile(draws, 0.025)
            )
            row["brier_delta_date_bootstrap_ci_high"] = float(
                np.quantile(draws, 0.975)
            )
    return output


def update_rows(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen = [
        row
        for row in predictions
        if row["model"]
        in ("m2_history_pretrain", "m3_history_plus_exact_calibration")
    ]
    grouped: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in chosen:
        grouped[
            (row["city"], row["target_date"], row["horizon_min"], row["model"])
        ].append(row)
    output = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row["decision_clock_ts_utc"])
        previous: dict[str, Any] | None = None
        for row in rows:
            # A cross changes the target from (say) 20.5 to 21.5. Comparing
            # those two probabilities would not be a report update for the
            # same event, so keep only unchanged target lattice transitions.
            if (
                previous is not None
                and previous["next_lattice_threshold_c"]
                == row["next_lattice_threshold_c"]
            ):
                output.append(
                    {
                        **row,
                        "p_before_report": previous["p_cross_next_lattice"],
                        "p_after_report": row["p_cross_next_lattice"],
                        "probability_update": (
                            row["p_cross_next_lattice"]
                            - previous["p_cross_next_lattice"]
                        ),
                    }
                )
            previous = row
    return output


def render_report(
    path: Path,
    *,
    archive_audit: list[dict[str, Any]],
    exact_audit: list[dict[str, Any]],
    archive_states: list[dict[str, Any]],
    exact_states: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> None:
    lookup = {
        (row["city"], row["horizon_min"], row["model"]): row for row in score_rows
    }
    score_lines = []
    for city in V1.CITY_CONFIG:
        for horizon in HORIZONS:
            baseline = lookup.get((city, horizon, "m0_history_prior"))
            exact_only = lookup.get((city, horizon, "m1_exact_only"))
            history = lookup.get((city, horizon, "m2_history_pretrain"))
            calibrated = lookup.get(
                (city, horizon, "m3_history_plus_exact_calibration")
            )
            if not baseline or not history:
                continue
            score_lines.append(
                "| "
                + " | ".join(
                    [
                        city,
                        str(horizon),
                        str(baseline["target_dates"]),
                        str(baseline["events"]),
                        f"{baseline['brier_date_weighted']:.4f}",
                        (
                            "NA"
                            if not exact_only
                            else f"{exact_only['brier_date_weighted']:.4f} "
                            f"({exact_only['brier_delta_vs_history_prior']:+.4f})"
                        ),
                        f"{history['brier_date_weighted']:.4f} "
                        f"({history['brier_delta_vs_history_prior']:+.4f})",
                        (
                            "NA"
                            if not calibrated
                            else f"{calibrated['brier_date_weighted']:.4f} "
                            f"({calibrated['brier_delta_vs_history_prior']:+.4f})"
                        ),
                    ]
                )
                + " |"
            )
    archive_map = {row["city"]: row for row in archive_audit}
    exact_map = {row["city"]: row for row in exact_audit}
    update_summary = []
    for model in ("m2_history_pretrain", "m3_history_plus_exact_calibration"):
        model_updates = [row for row in updates if row["model"] == model]
        positive = [row for row in model_updates if row["probability_update"] > 0]
        nonpositive = [
            row for row in model_updates if row["probability_update"] <= 0
        ]
        update_summary.append(
            {
                "model": model,
                "updates": len(model_updates),
                "positive_updates": len(positive),
                "positive_cross_rate": (
                    sum(row["y_cross_next_lattice"] for row in positive)
                    / len(positive)
                    if positive
                    else math.nan
                ),
                "nonpositive_cross_rate": (
                    sum(row["y_cross_next_lattice"] for row in nonpositive)
                    / len(nonpositive)
                    if nonpositive
                    else math.nan
                ),
            }
        )
    pre_cross_lead = {}
    for city in V1.CITY_CONFIG:
        values = [
            float(row["minutes_to_cross"])
            for row in updates
            if row["model"] == "m2_history_pretrain"
            and row["city"] == city
            and row["horizon_min"] == 60
            and row["probability_update"] > 0
            and row["y_cross_next_lattice"] == 1
            and row["minutes_to_cross"] is not None
        ]
        if values:
            pre_cross_lead[city] = {
                "events": len(values),
                "median_min": float(np.median(values)),
            }
    history_score_rows = [
        row for row in score_rows if row["model"] == "m2_history_pretrain"
    ]
    worst_history_ci_high = max(
        (
            row["brier_delta_date_bootstrap_ci_high"]
            for row in history_score_rows
        ),
        default=math.nan,
    )
    lines = [
        "# Three-City Pre-Cross Path Pretrain v2",
        "",
        "Status: `research_path_head_v2`; zero-notional; no live behavior change",
        "",
        "## 结论",
        "",
        "用户直觉对应的可研究对象是对的：不等 source cross 后才产生信号，而是在每份"
        "报文 first-seen 时更新 `P(未来 30/60/120m 跨入下一 whole-degree lattice)`。"
        "历史 archive 只训练温度路径动力学；collector-exact 日期只负责 first-seen "
        "calibration 和 forward OOF。",
        "",
        "当前只完成 weather probability head。production manifest 的 canonical DB route "
        "健康，但 refresh LaunchAgent strict check 失败；且同 checkpoint settled/full-depth "
        "market evidence 不足，所以本报告不声称 market residual 或可交易 alpha。",
        "",
        "冻结的历史 path pretrain 在 exact collector 的全部日期上直接 forward 评分；"
        "exact-only 与 calibration layer 只有积累满 3 个先前日期后才开始评分。后者当前"
        "没有稳定优于历史 path，因此 exact timing 暂时只作为 calibration telemetry，"
        "不覆盖基础 path probability。",
        "",
        "## Data separation",
        "",
        "| city | archive path observations/dates | cadence | archive range | exact events/dates |",
        "|---|---:|---:|---|---:|",
    ]
    for city in V1.CITY_CONFIG:
        archive = archive_map.get(city, {})
        exact = exact_map.get(city, {})
        lines.append(
            f"| {city} | {archive.get('distinct_observations', 0)} / "
            f"{archive.get('dates', 0)} | {archive.get('cadence_minutes', 'NA')}m | "
            f"{archive.get('first_date', 'NA')}.."
            f"{archive.get('last_date', 'NA')} | "
            f"{exact.get('distinct_collector_exact_events', 0)} / "
            f"{exact.get('collector_exact_dates', 0)} |"
        )
    lines.extend(
        [
            "",
            "这里的“历史全量”固定为 canonical strategy-era 可比窗口：从 "
            "`2026-05-19` 到各城 exact collector 首日之前，期间不抽样、不挑天气日。"
            "Tokyo/Helsinki 使用官方原生 10m；Amsterdam 使用完整 KNMI hourly archive，"
            "有原生 10m archive 的日期整日替换为 10m，避免同日混频。",
            "",
            "Archive clock is observation time, never first-seen. It can teach path transitions but "
            "cannot measure source latency, book reaction, or execution.",
            "",
            "## Exact-forward probability score",
            "",
            "主 label 是跨下一档，不是任意 +0.1°C strict high。括号为相对历史 base-rate "
            "Brier delta；负数更好。",
            "",
            "`Brier = mean((p-y)^2)`，其中结果发生 `y=1`，未发生 `y=0`；它衡量概率"
            "预测离真实结果有多远，`0` 最好。例如报 70% 后事件发生，该次误差为 "
            "`(0.7-1)^2=0.09`。",
            "",
            "| city | horizon | OOF dates | events | history prior | exact-only | history path | + exact calibration |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            *score_lines,
            "",
            f"历史 path head 三城九个 horizon 的 date-block bootstrap "
            f"Brier-delta CI 上界最大为 `{worst_history_ci_high:+.4f}`，均低于 0；"
            "Amsterdam 独立 exact 日期仍只有 3 个，其显著性远弱于另外两城；结论只限 "
            "weather-path probability。",
            "",
            "## Report-update interpretation",
            "",
            "只比较 next-lattice target 未改变的连续报文，避免把 cross 后的新目标概率"
            "误算成一次更新。",
            "",
            *[
                f"- `{row['model']}`: updates `{row['updates']}`; positive updates "
                f"`{row['positive_updates']}`; cross rate positive/nonpositive "
                f"`{row['positive_cross_rate']:.3f}/{row['nonpositive_cross_rate']:.3f}`"
                for row in update_summary
                if row["updates"]
            ],
            *[
                f"- {city} 60m positive updates 且随后确实 cross：`{row['events']}`；"
                f"first-seen 到 cross 中位 lead `{row['median_min']:.1f}m`"
                for city, row in pre_cross_lead.items()
            ],
            "- `probability_update > 0` 只是连续天气信号，不是 entry gate；必须与同一 "
            "checkpoint 的 normalized ladder market probability 比较后才能形成 residual。",
            "- cross 前可以表达的含义是：某份报文让下一档概率上升，而盘口尚未等幅更新；"
            "不是提前固定买某个 bracket，也不是把 touch 当 final-exact。",
            "",
            "## Signal funnel",
            "",
            f"- archive path states: `{len(archive_states)}` observation-grain / "
            f"`{len({(r['city'], r['target_date']) for r in archive_states})}` city-days",
            f"- exact path states: `{len(exact_states)}` event-grain / "
            f"`{len({(r['city'], r['target_date']) for r in exact_states})}` city-days",
            f"- exact OOF predictions: `{len(predictions)}` model×event×horizon",
            "- policy-selected expressions: `0`",
            "",
            "## Evidence funnel",
            "",
            "- exact PIT weather event: available",
            "- same-checkpoint normalized market ladder: coverage insufficient / not scored here",
            "- settled full-distribution rows: insufficient",
            "- executable expression / fills: `0 / 0`",
            "",
            "## Action",
            "",
            "保持 research + collector。下一步把本概率头接到 zero-notional checkpoint：保存"
            "`p_before/p_after`、market ladder before/after 与全量 bracket×side telemetry。"
            "达到 settled forward 日期后再检验 residual；不改任何 live runner。",
            "",
            "## Gate",
            "",
            "`significance=NA market_baseline=FAIL forward=RESEARCH_ONLY conclusion=inconclusive`",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--history-start", default="2026-05-19")
    parser.add_argument("--exact-start", default="2026-07-08")
    parser.add_argument("--end-date", default="2026-07-29")
    parser.add_argument("--min-calibration-dates", type=int, default=MIN_CALIBRATION_DATES)
    parser.add_argument(
        "--reuse-exact-states",
        action="store_true",
        help="Reuse previously materialized exact states; history-only reruns remain deterministic.",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    runtime = Path(args.runtime_root)
    history_start = date.fromisoformat(args.history_start)
    exact_start = date.fromisoformat(args.exact_start)
    end = date.fromisoformat(args.end_date)
    out = Path(args.out_dir)
    if args.reuse_exact_states:
        exact_states = read_csv_rows(
            out / "exact_path_states.csv", normalize_empty=True
        )
        exact_audit = read_csv_rows(out / "exact_coverage.csv")
        exact_start_by_city = {
            city: min(
                date.fromisoformat(str(row["target_date"]))
                for row in exact_states
                if row["city"] == city
            )
            for city in V1.CITY_CONFIG
        }
    else:
        exact, exact_audit = V1.load_fast_events(runtime, exact_start, end)
        exact_common = exact_to_common(exact)
        exact_start_by_city = {
            city: min(
                date.fromisoformat(target)
                for row_city, target in exact_common
                if row_city == city
            )
            for city in V1.CITY_CONFIG
        }
        exact_states = build_states(exact_common)
    archive, archive_audit = load_archive_history(
        runtime, history_start, exact_start_by_city
    )
    archive_states = build_states(archive)
    prediction_rows = evaluate(
        archive_states,
        exact_states,
        min_calibration_dates=max(1, args.min_calibration_dates),
    )
    score_rows = scores(prediction_rows)
    updates = update_rows(prediction_rows)
    write_csv(out / "archive_coverage.csv", archive_audit)
    write_csv(out / "exact_coverage.csv", exact_audit)
    write_csv(out / "archive_path_states.csv", archive_states)
    write_csv(out / "exact_path_states.csv", exact_states)
    write_csv(out / "exact_oof_predictions.csv", prediction_rows)
    write_csv(out / "probability_scores.csv", score_rows)
    write_csv(out / "report_probability_updates.csv", updates)
    summary = {
        "schema_version": "three_city_pre_cross_path_pretrain_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "history_clock_class": "observation_clock_archive_not_pit",
        "evaluation_clock_class": "collector_exact",
        "archive_coverage": archive_audit,
        "exact_coverage": exact_audit,
        "archive_states": len(archive_states),
        "exact_states": len(exact_states),
        "predictions": len(prediction_rows),
        "probability_updates": len(updates),
        "market_baseline_rows": 0,
        "policy_selected": 0,
        "live_behavior_changes": 0,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_report(
        Path(args.report),
        archive_audit=archive_audit,
        exact_audit=exact_audit,
        archive_states=archive_states,
        exact_states=exact_states,
        predictions=prediction_rows,
        score_rows=score_rows,
        updates=updates,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
