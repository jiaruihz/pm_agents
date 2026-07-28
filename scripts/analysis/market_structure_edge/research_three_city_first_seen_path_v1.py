#!/usr/bin/env python3
"""Three-city first-seen temperature-path probability study.

Research only.  This script reads collector-exact fast-source journals for
Helsinki/FMI, Amsterdam/KNMI, and Tokyo/JMA, joins only weather context that
was available at each first-seen timestamp, and evaluates expanding-date OOF
probabilities for a new strict high within 30/60/120 minutes.

It never creates plans, orders, fills, exits, or live-policy output.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_DB = ROOT / "runtime" / "weather.db"
DEFAULT_OUT_DIR = (
    ROOT
    / "docs"
    / "analysis"
    / "2026-07"
    / "generated"
    / "three_city_first_seen_path_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs"
    / "analysis"
    / "2026-07"
    / "2026-07-29-three-city-first-seen-path-probability-v1.md"
)

CITY_CONFIG = {
    "Helsinki": {
        "source": "fmi",
        "station": "100968",
        "timezone": ZoneInfo("Europe/Helsinki"),
    },
    "Amsterdam": {
        "source": "knmi",
        "station": "0-20000-0-06240",
        "timezone": ZoneInfo("Europe/Amsterdam"),
    },
    "Tokyo": {
        "source": "jma_amedas",
        "station": "44166",
        "timezone": ZoneInfo("Asia/Tokyo"),
    },
}
HORIZONS_MIN = (30, 60, 120)
MAX_FIRST_SEEN_AGE_MIN = 30.0
MAX_CADENCE_GAP_MIN = 25.0
BOUNDARY_GRACE_MIN = 20.0
EPS = 1e-6

PATH_FEATURES = [
    "source_temp_c",
    "source_delta_c",
    "slope_30m_c_per_hour",
    "slope_60m_c_per_hour",
    "minutes_since_previous_source",
    "source_running_max_margin_c",
    "minutes_since_strict_high",
    "new_source_high",
    "warming_run_count",
    "local_hour_sin",
    "local_hour_cos",
    "source_wind_speed_kt",
    "source_pressure_hpa",
]
METAR_CONTEXT_FEATURES = [
    "source_to_metar_temp_gap_c",
    "metar_dewpoint_c",
    "metar_relative_humidity_pct",
    "metar_wind_speed_kt",
    "metar_wind_dir_sin",
    "metar_wind_dir_cos",
    "metar_ceiling_ft_agl",
    "metar_cloud_layer_count",
    "metar_precip_observed",
    "metar_age_min",
]
MODEL_FEATURES = {
    "m0_prior": [],
    "m1_fast_path": PATH_FEATURES,
    "m2_fast_path_plus_metar": PATH_FEATURES + METAR_CONTEXT_FEATURES,
}


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def local_date(ts: datetime, city: str) -> str:
    return ts.astimezone(CITY_CONFIG[city]["timezone"]).date().isoformat()


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def load_fast_events(
    runtime: Path,
    start: date,
    end: date,
    *,
    max_first_seen_age_min: float = MAX_FIRST_SEEN_AGE_MIN,
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[dict[str, Any]]]:
    """Load only genuinely collector-timestamped temperature events.

    ``fetched_at_utc`` is deliberately not a fallback for first-seen.  Rows
    without ``source_first_seen_at_utc`` remain visible in the coverage audit
    but cannot enter the PIT research denominator.
    """

    earliest: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    audit: dict[tuple[str, str], Counter[str]] = {
        (city, str(config["source"])): Counter()
        for city, config in CITY_CONFIG.items()
    }
    wanted_pairs = {
        (city, str(config["source"])) for city, config in CITY_CONFIG.items()
    }
    for shard in date_range(start - timedelta(days=1), end + timedelta(days=1)):
        path = (
            runtime
            / "output"
            / "high_frequency_observations"
            / shard.isoformat()
            / "high_frequency_observations.jsonl"
        )
        for raw in iter_jsonl(path):
            city = str(raw.get("city") or "")
            source = str(raw.get("source") or "")
            if (city, source) not in wanted_pairs:
                continue
            counts = audit[(city, source)]
            counts["raw_matching_rows"] += 1
            if raw.get("source_status") != "ok":
                counts["non_ok_rows"] += 1
                continue
            obs_ts = parse_dt(raw.get("observation_time_utc"))
            first_seen = parse_dt(raw.get("source_first_seen_at_utc"))
            temp_c = number(raw.get("temp_c"))
            if first_seen is None:
                counts["missing_exact_first_seen"] += 1
                continue
            if obs_ts is None or temp_c is None:
                counts["invalid_identity_or_temperature"] += 1
                continue
            age_min = (first_seen - obs_ts).total_seconds() / 60.0
            if age_min < -2.0 or age_min > max_first_seen_age_min:
                counts["outside_first_seen_age_contract"] += 1
                continue
            target_date = local_date(obs_ts, city)
            if target_date < start.isoformat() or target_date > end.isoformat():
                counts["outside_target_window"] += 1
                continue
            key = (city, source, obs_ts.isoformat(), float(temp_c))
            row = {
                "city": city,
                "source": source,
                "station": str(raw.get("station") or CITY_CONFIG[city]["station"]),
                "target_date": target_date,
                "obs_ts": obs_ts,
                "first_seen_ts": first_seen,
                "first_seen_age_min": age_min,
                "temp_c": float(temp_c),
                "wind_speed_kt": number(raw.get("wind_speed_kt")),
                "pressure_hpa": number(raw.get("pressure_hpa")),
                "payload_hash": str(raw.get("payload_hash") or ""),
                "pit_lineage_class": "collector_exact",
            }
            old = earliest.get(key)
            if old is None or first_seen < old["first_seen_ts"]:
                earliest[key] = row

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in earliest.values():
        grouped[(row["city"], row["target_date"])].append(row)
        audit[(row["city"], row["source"])]["distinct_collector_exact_events"] += 1
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["first_seen_ts"], row["obs_ts"], row["temp_c"]))
    audit_rows = []
    for (city, source), counts in sorted(audit.items()):
        dates = {
            target_date
            for (row_city, target_date), rows in grouped.items()
            if row_city == city and rows
        }
        audit_rows.append(
            {
                "city": city,
                "source": source,
                "collector_exact_dates": len(dates),
                **dict(counts),
            }
        )
    return grouped, audit_rows


def load_metar_context(
    db_path: Path, start: date, end: date
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    placeholders = ",".join("?" for _ in CITY_CONFIG)
    rows = conn.execute(
        f"""
        SELECT city, target_date, source_system, obs_ts_utc, available_at_utc,
               temp_c, dewpoint_f, wind_speed_kt, wind_dir_deg, sky_cover,
               raw_payload, pit_lineage_class
        FROM weather_observation_events
        WHERE city IN ({placeholders})
          AND target_date BETWEEN ? AND ?
          AND source_system IN ('aviationweather_metar', 'aviationweather_cache_csv')
          AND pit_lineage_class IN ('collector_exact', 'archive_known_available')
          AND available_at_utc IS NOT NULL
        """,
        (*CITY_CONFIG.keys(), start.isoformat(), end.isoformat()),
    ).fetchall()
    conn.close()
    priority = {"aviationweather_metar": 0, "aviationweather_cache_csv": 1}
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (
        city,
        target_date,
        source,
        obs_text,
        available_text,
        temp_c,
        dewpoint_f,
        wind_speed_kt,
        wind_dir_deg,
        sky_cover,
        raw_payload,
        lineage,
    ) in rows:
        obs_ts = parse_dt(obs_text)
        available_ts = parse_dt(available_text)
        if obs_ts is None or available_ts is None:
            continue
        try:
            raw = json.loads(raw_payload or "{}")
        except json.JSONDecodeError:
            raw = {}
        key = (str(city), str(target_date), obs_ts.isoformat())
        row = {
            "city": str(city),
            "target_date": str(target_date),
            "source": str(source),
            "priority": priority[str(source)],
            "obs_ts": obs_ts,
            "available_ts": available_ts,
            "temp_c": number(temp_c),
            "dewpoint_c": (
                (float(dewpoint_f) - 32.0) * 5.0 / 9.0
                if number(dewpoint_f) is not None
                else number(raw.get("dewpoint_c"))
            ),
            "relative_humidity_pct": number(raw.get("relative_humidity_pct")),
            "wind_speed_kt": number(wind_speed_kt),
            "wind_dir_deg": number(wind_dir_deg),
            "ceiling_ft_agl": number(raw.get("ceiling_ft_agl")),
            "cloud_layer_count": number(raw.get("cloud_layer_count")),
            "precip_observed": int(bool(raw.get("precip_observed"))),
            "sky_cover": str(sky_cover or raw.get("sky_code_now") or ""),
            "pit_lineage_class": str(lineage),
        }
        old = unique.get(key)
        if old is None or (row["priority"], row["available_ts"]) < (
            old["priority"],
            old["available_ts"],
        ):
            unique[key] = row
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in unique.values():
        grouped[(row["city"], row["target_date"])].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: (row["available_ts"], row["obs_ts"]))
    return grouped


def load_settlement_winners(
    db_path: Path, start: date, end: date
) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    placeholders = ",".join("?" for _ in CITY_CONFIG)
    rows = conn.execute(
        f"""
        SELECT city, target_date, bracket, final_price, settlement_status
        FROM settlement_outcomes
        WHERE city IN ({placeholders}) AND target_date BETWEEN ? AND ?
        """,
        (*CITY_CONFIG.keys(), start.isoformat(), end.isoformat()),
    ).fetchall()
    conn.close()
    grouped: dict[tuple[str, str], list[tuple[str, float, str]]] = defaultdict(list)
    for city, target_date, bracket, final_price, status in rows:
        grouped[(str(city), str(target_date))].append(
            (str(bracket), float(final_price), str(status))
        )
    winners: dict[tuple[str, str], str] = {}
    for key, values in grouped.items():
        if values and all(status == "settled" for _, _, status in values):
            won = [bracket for bracket, price, _ in values if price >= 0.99]
            if len(won) == 1:
                winners[key] = won[0]
    return winners


def bracket_bounds(bracket: str) -> tuple[float, float] | None:
    values = [
        float(value)
        for value in re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", bracket)
    ]
    if not values:
        return None
    lowered = bracket.lower()
    if any(token in lowered for token in ("or below", "or lower", "and below")):
        return -math.inf, values[0]
    if any(token in lowered for token in ("or higher", "or above", "and above")):
        return values[0], math.inf
    if len(values) >= 2:
        return min(values), max(values)
    return values[0], values[0]


def binary_final_above(bracket: str, current_lattice: int) -> int | None:
    bounds = bracket_bounds(bracket)
    if bounds is None:
        return None
    low, high = bounds
    if low > current_lattice:
        return 1
    if high <= current_lattice:
        return 0
    return None


def slope(history: list[dict[str, Any]], current: dict[str, Any], minutes: int) -> float | None:
    candidates = [
        row
        for row in history
        if 0
        < (current["first_seen_ts"] - row["first_seen_ts"]).total_seconds()
        <= minutes * 60
    ]
    if not candidates:
        return None
    base = candidates[0]
    hours = (current["first_seen_ts"] - base["first_seen_ts"]).total_seconds() / 3600.0
    return (float(current["temp_c"]) - float(base["temp_c"])) / hours if hours > 0 else None


def latest_available(
    rows: list[dict[str, Any]], as_of: datetime
) -> dict[str, Any] | None:
    known = [row for row in rows if row["available_ts"] <= as_of]
    if not known:
        return None
    return max(known, key=lambda row: (row["obs_ts"], -row["priority"], row["available_ts"]))


def build_states(
    fast: dict[tuple[str, str], list[dict[str, Any]]],
    metar: dict[tuple[str, str], list[dict[str, Any]]],
    winners: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for (city, target_date), rows in sorted(fast.items()):
        history: list[dict[str, Any]] = []
        running_max = -math.inf
        last_high_ts: datetime | None = None
        warming_run = 0
        previous: dict[str, Any] | None = None
        winner = winners.get((city, target_date), "")
        for event in rows:
            temp = float(event["temp_c"])
            delta = None if previous is None else temp - float(previous["temp_c"])
            warming_run = warming_run + 1 if delta is not None and delta > 0 else 0
            prior_max = running_max
            is_new_high = prior_max == -math.inf or temp > prior_max + EPS
            if is_new_high:
                running_max = temp
                last_high_ts = event["first_seen_ts"]
            routine = latest_available(metar.get((city, target_date), []), event["first_seen_ts"])
            local = event["first_seen_ts"].astimezone(CITY_CONFIG[city]["timezone"])
            hour = local.hour + local.minute / 60.0 + local.second / 3600.0
            wind_dir = number(routine.get("wind_dir_deg")) if routine else None
            metar_temp = number(routine.get("temp_c")) if routine else None
            row: dict[str, Any] = {
                "event_id": (
                    f"{city}|{event['source']}|{event['obs_ts'].isoformat()}|"
                    f"{event['temp_c']}"
                ),
                "city": city,
                "source": event["source"],
                "station": event["station"],
                "target_date": target_date,
                "source_observation_ts_utc": event["obs_ts"].isoformat(),
                "source_first_seen_ts_utc": event["first_seen_ts"].isoformat(),
                "pit_lineage_class": event["pit_lineage_class"],
                "source_first_seen_age_min": round(event["first_seen_age_min"], 4),
                "source_temp_c": temp,
                "source_delta_c": delta,
                "slope_30m_c_per_hour": slope(history, event, 30),
                "slope_60m_c_per_hour": slope(history, event, 60),
                "minutes_since_previous_source": (
                    None
                    if previous is None
                    else (event["first_seen_ts"] - previous["first_seen_ts"]).total_seconds()
                    / 60.0
                ),
                "source_running_max_c": running_max,
                "source_running_max_lattice": round_half_up(running_max),
                "source_running_max_margin_c": temp - running_max,
                "minutes_since_strict_high": (
                    0.0
                    if last_high_ts is None
                    else (event["first_seen_ts"] - last_high_ts).total_seconds() / 60.0
                ),
                "new_source_high": int(is_new_high),
                "warming_run_count": warming_run,
                "local_hour": hour,
                "local_hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
                "local_hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
                "source_wind_speed_kt": event["wind_speed_kt"],
                "source_pressure_hpa": event["pressure_hpa"],
                "metar_available": int(routine is not None),
                "metar_source": routine["source"] if routine else "",
                "metar_observation_ts_utc": routine["obs_ts"].isoformat() if routine else "",
                "metar_available_at_utc": (
                    routine["available_ts"].isoformat() if routine else ""
                ),
                "metar_age_min": (
                    None
                    if routine is None
                    else (event["first_seen_ts"] - routine["obs_ts"]).total_seconds() / 60.0
                ),
                "source_to_metar_temp_gap_c": (
                    None if metar_temp is None else temp - metar_temp
                ),
                "metar_dewpoint_c": routine["dewpoint_c"] if routine else None,
                "metar_relative_humidity_pct": (
                    routine["relative_humidity_pct"] if routine else None
                ),
                "metar_wind_speed_kt": routine["wind_speed_kt"] if routine else None,
                "metar_wind_dir_sin": (
                    None
                    if wind_dir is None
                    else math.sin(2.0 * math.pi * wind_dir / 360.0)
                ),
                "metar_wind_dir_cos": (
                    None
                    if wind_dir is None
                    else math.cos(2.0 * math.pi * wind_dir / 360.0)
                ),
                "metar_ceiling_ft_agl": routine["ceiling_ft_agl"] if routine else None,
                "metar_cloud_layer_count": (
                    routine["cloud_layer_count"] if routine else None
                ),
                "metar_precip_observed": routine["precip_observed"] if routine else None,
                "winner_bracket": winner,
                "final_above_current_lattice": (
                    binary_final_above(winner, round_half_up(running_max))
                    if winner
                    else None
                ),
            }
            states.append(row)
            history.append(event)
            previous = event

    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in states:
        by_day[(row["city"], row["target_date"])].append(row)
    for rows in by_day.values():
        rows.sort(key=lambda row: row["source_first_seen_ts_utc"])
        for index, row in enumerate(rows):
            current_ts = parse_dt(row["source_first_seen_ts_utc"])
            current_obs = parse_dt(row["source_observation_ts_utc"])
            assert current_ts is not None and current_obs is not None
            threshold = float(row["source_running_max_c"])
            later = rows[index + 1 :]
            for horizon in HORIZONS_MIN:
                deadline = current_ts + timedelta(minutes=horizon)
                boundary = next(
                    (
                        candidate
                        for candidate in later
                        if parse_dt(candidate["source_first_seen_ts_utc"]) >= deadline
                    ),
                    None,
                )
                boundary_ts = (
                    parse_dt(boundary["source_first_seen_ts_utc"]) if boundary else None
                )
                chain = [
                    candidate
                    for candidate in later
                    if parse_dt(candidate["source_first_seen_ts_utc"]) <= deadline
                ]
                clock_points = [current_ts] + [
                    parse_dt(candidate["source_first_seen_ts_utc"]) for candidate in chain
                ]
                if boundary_ts is not None:
                    clock_points.append(boundary_ts)
                gaps = [
                    (right - left).total_seconds() / 60.0
                    for left, right in zip(clock_points, clock_points[1:])
                    if left is not None and right is not None
                ]
                complete = bool(
                    boundary_ts is not None
                    and boundary_ts <= deadline + timedelta(minutes=BOUNDARY_GRACE_MIN)
                    and gaps
                    and max(gaps) <= MAX_CADENCE_GAP_MIN
                )
                eligible_future = [
                    candidate
                    for candidate in chain
                    if (parse_dt(candidate["source_observation_ts_utc"]) or current_obs)
                    > current_obs
                ]
                row[f"coverage_complete_{horizon}m"] = int(complete)
                row[f"new_high_within_{horizon}m"] = (
                    int(
                        any(
                            float(candidate["source_temp_c"]) > threshold + EPS
                            for candidate in eligible_future
                        )
                    )
                    if complete
                    else None
                )
    states.sort(
        key=lambda row: (
            row["city"],
            row["target_date"],
            row["source_first_seen_ts_utc"],
        )
    )
    return states


def varying_features(rows: list[dict[str, Any]], features: list[str]) -> list[str]:
    result = []
    for feature in features:
        values = [number(row.get(feature)) for row in rows]
        present = [value for value in values if value is not None]
        if len(present) >= 2 and max(present) - min(present) > EPS:
            result.append(feature)
    return result


def fit_model(
    rows: list[dict[str, Any]], label: str, features: list[str]
) -> tuple[Pipeline, list[str]] | None:
    usable = varying_features(rows, features)
    labels = np.asarray([int(row[label]) for row in rows], dtype=int)
    if not usable or len(set(labels.tolist())) < 2:
        return None
    x = np.asarray(
        [
            [
                np.nan if number(row.get(feature)) is None else number(row.get(feature))
                for feature in usable
            ]
            for row in rows
        ],
        dtype=float,
    )
    date_counts = Counter(str(row["target_date"]) for row in rows)
    weights = np.asarray(
        [1.0 / date_counts[str(row["target_date"])] for row in rows], dtype=float
    )
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=1.0,
                    solver="lbfgs",
                    max_iter=2000,
                    random_state=20260729,
                ),
            ),
        ]
    )
    model.fit(x, labels, model__sample_weight=weights)
    return model, usable


def predict_model(
    fitted: tuple[Pipeline, list[str]], rows: list[dict[str, Any]]
) -> np.ndarray:
    model, features = fitted
    x = np.asarray(
        [
            [
                np.nan if number(row.get(feature)) is None else number(row.get(feature))
                for feature in features
            ]
            for row in rows
        ],
        dtype=float,
    )
    return model.predict_proba(x)[:, 1]


def expanding_oof(
    states: list[dict[str, Any]], *, min_prior_dates: int
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for city in CITY_CONFIG:
        city_rows = [row for row in states if row["city"] == city]
        dates = sorted({str(row["target_date"]) for row in city_rows})
        for horizon in HORIZONS_MIN:
            label = f"new_high_within_{horizon}m"
            for date_index, target_date in enumerate(dates):
                if date_index < min_prior_dates:
                    continue
                train = [
                    row
                    for row in city_rows
                    if str(row["target_date"]) < target_date and row.get(label) is not None
                ]
                test = [
                    row
                    for row in city_rows
                    if str(row["target_date"]) == target_date and row.get(label) is not None
                ]
                if not train or not test:
                    continue
                train_dates = sorted({str(row["target_date"]) for row in train})
                if len(train_dates) < min_prior_dates:
                    continue
                date_counts = Counter(str(row["target_date"]) for row in train)
                weights = np.asarray(
                    [1.0 / date_counts[str(row["target_date"])] for row in train],
                    dtype=float,
                )
                labels = np.asarray([int(row[label]) for row in train], dtype=float)
                prior = float(np.average(labels, weights=weights))
                fitted = {
                    model_name: fit_model(train, label, features)
                    for model_name, features in MODEL_FEATURES.items()
                    if model_name != "m0_prior"
                }
                for model_name in MODEL_FEATURES:
                    if model_name == "m0_prior":
                        values = np.repeat(prior, len(test))
                    elif fitted[model_name] is None:
                        continue
                    else:
                        values = predict_model(fitted[model_name], test)
                    for row, probability in zip(test, values):
                        predictions.append(
                            {
                                "event_id": row["event_id"],
                                "city": city,
                                "source": row["source"],
                                "target_date": target_date,
                                "source_first_seen_ts_utc": row[
                                    "source_first_seen_ts_utc"
                                ],
                                "horizon_min": horizon,
                                "model": model_name,
                                "p_new_high": float(
                                    min(1.0 - EPS, max(EPS, probability))
                                ),
                                "y_new_high": int(row[label]),
                                "train_start_date": train_dates[0],
                                "train_end_date": train_dates[-1],
                                "train_dates": len(train_dates),
                                "train_events": len(train),
                            }
                        )
    return predictions


def score_predictions(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        grouped[(row["city"], int(row["horizon_min"]), row["model"])].append(row)
    scores = []
    for (city, horizon, model), rows in sorted(grouped.items()):
        date_counts = Counter(str(row["target_date"]) for row in rows)
        weights = np.asarray(
            [1.0 / date_counts[str(row["target_date"])] for row in rows], dtype=float
        )
        weights /= weights.sum()
        y = np.asarray([int(row["y_new_high"]) for row in rows], dtype=float)
        p = np.asarray([float(row["p_new_high"]) for row in rows], dtype=float)
        brier = float(np.sum(weights * (p - y) ** 2))
        logloss = float(
            -np.sum(weights * (y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
        )
        auc = (
            float(roc_auc_score(y, p, sample_weight=weights))
            if len(set(y.tolist())) == 2
            else None
        )
        scores.append(
            {
                "city": city,
                "horizon_min": horizon,
                "model": model,
                "events": len(rows),
                "target_dates": len(date_counts),
                "positive_rate_date_weighted": float(np.sum(weights * y)),
                "brier_date_weighted": brier,
                "logloss_date_weighted": logloss,
                "auc_date_weighted": auc,
            }
        )
    baselines = {
        (row["city"], row["horizon_min"]): row
        for row in scores
        if row["model"] == "m0_prior"
    }
    for row in scores:
        baseline = baselines.get((row["city"], row["horizon_min"]))
        row["brier_delta_vs_prior"] = (
            None
            if baseline is None
            else row["brier_date_weighted"] - baseline["brier_date_weighted"]
        )
        row["logloss_delta_vs_prior"] = (
            None
            if baseline is None
            else row["logloss_date_weighted"] - baseline["logloss_date_weighted"]
        )
    return scores


def source_basis_audit(
    states: list[dict[str, Any]], winners: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in states:
        grouped[(row["city"], row["target_date"])].append(row)
    output = []
    for key, rows in sorted(grouped.items()):
        city, target_date = key
        winner = winners.get(key, "")
        source_max = max(float(row["source_temp_c"]) for row in rows)
        lattice = round_half_up(source_max)
        bounds = bracket_bounds(winner) if winner else None
        first_seen = parse_dt(rows[0]["source_first_seen_ts_utc"])
        last_seen = parse_dt(rows[-1]["source_first_seen_ts_utc"])
        span_hours = (
            0.0
            if first_seen is None or last_seen is None
            else (last_seen - first_seen).total_seconds() / 3600.0
        )
        basis_coverage_complete = len(rows) >= 48 and span_hours >= 8.0
        aligned = (
            None
            if bounds is None or not basis_coverage_complete
            else int(bounds[0] <= lattice <= bounds[1])
        )
        output.append(
            {
                "city": city,
                "source": rows[0]["source"],
                "target_date": target_date,
                "source_distinct_events": len(rows),
                "source_first_seen_span_hours": span_hours,
                "basis_coverage_complete": int(basis_coverage_complete),
                "source_daily_max_c": source_max,
                "source_daily_max_lattice": lattice,
                "winner_bracket": winner,
                "source_lattice_in_winner_bracket": aligned,
                "terminal_false_basis": (
                    None if aligned is None else int(not bool(aligned))
                ),
            }
        )
    return output


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


def canonical_fast_event_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    placeholders = ",".join("?" for _ in CITY_CONFIG)
    sources = [str(config["source"]) for config in CITY_CONFIG.values()]
    rows = conn.execute(
        f"""
        SELECT source, COUNT(*)
        FROM weather_information_events
        WHERE source IN ({placeholders})
        GROUP BY source
        """,
        sources,
    ).fetchall()
    conn.close()
    return {str(source): int(count) for source, count in rows}


def render_report(
    path: Path,
    *,
    start: date,
    end: date,
    source_audit: list[dict[str, Any]],
    states: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    basis: list[dict[str, Any]],
    canonical_counts: dict[str, int],
) -> None:
    score_lookup = {
        (row["city"], row["horizon_min"], row["model"]): row for row in scores
    }
    best_lines = []
    for city in CITY_CONFIG:
        for horizon in HORIZONS_MIN:
            prior = score_lookup.get((city, horizon, "m0_prior"))
            path_score = score_lookup.get((city, horizon, "m1_fast_path"))
            context_score = score_lookup.get(
                (city, horizon, "m2_fast_path_plus_metar")
            )
            if prior is None:
                continue
            best_lines.append(
                "| "
                + " | ".join(
                    [
                        city,
                        str(horizon),
                        str(prior["target_dates"]),
                        str(prior["events"]),
                        f"{prior['brier_date_weighted']:.4f}",
                        (
                            "NA"
                            if path_score is None
                            else f"{path_score['brier_date_weighted']:.4f} "
                            f"({path_score['brier_delta_vs_prior']:+.4f})"
                        ),
                        (
                            "NA"
                            if context_score is None
                            else f"{context_score['brier_date_weighted']:.4f} "
                            f"({context_score['brier_delta_vs_prior']:+.4f})"
                        ),
                    ]
                )
                + " |"
            )
    audit_map = {row["city"]: row for row in source_audit}
    basis_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in basis:
        basis_by_city[row["city"]].append(row)
    lines = [
        "# Helsinki / Amsterdam / Tokyo First-Seen Path Probability v1",
        "",
        "Status: `research_probability_head_v1`; no plan/order/fill/exit/live change",
        "",
        "## 结论",
        "",
        "第一版已实现为温度主导、PIT METAR context 辅助的 expanding-date OOF "
        "`P(new strict high within 30/60/120m)`。所有 fast rows 必须有显式 "
        "`source_first_seen_at_utc`；脚本不使用 fetched/issue time 冒充 first-seen。",
        "",
        "本轮仍是 probability-layer research，不是 residual trading policy：当前 DB 中三城 "
        "fast-source 事件尚未进入 canonical `weather_information_events → "
        "weather_state_checkpoints`，因此没有同 checkpoint 的 PIT market baseline，"
        "不能产生可交易 residual。本次代码已经补上 high-frequency producer event header、"
        "历史 exact/late-backfill rebuild 分流和 zero-notional forward input；新采集/重建后"
        "才能形成 checkpoint。",
        "",
        "## Frozen target",
        "",
        f"- window: `{start.isoformat()}..{end.isoformat()}`",
        "- cities/sources: `Helsinki/FMI`, `Amsterdam/KNMI`, `Tokyo/JMA AMeDAS`",
        "- labels: future strict source high at 30/60/120m; canonical settlement is used "
        "only for source-basis audit, never backfilled as a feature",
        "- validation: expanding OOF by whole target_date; every training date receives equal weight",
        "",
        "## Source coverage",
        "",
        "| city | source | exact events | exact dates | missing exact first-seen | canonical fast events | settlement basis aligned/evaluable days | terminal false days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for city, config in CITY_CONFIG.items():
        audit = audit_map.get(city, {})
        city_basis = basis_by_city.get(city, [])
        evaluable = sum(row["basis_coverage_complete"] == 1 for row in city_basis)
        aligned = sum(row["source_lattice_in_winner_bracket"] == 1 for row in city_basis)
        false = sum(row["terminal_false_basis"] == 1 for row in city_basis)
        source = str(config["source"])
        lines.append(
            f"| {city} | `{source}` | "
            f"{audit.get('distinct_collector_exact_events', 0)} | "
            f"{audit.get('collector_exact_dates', 0)} | "
            f"{audit.get('missing_exact_first_seen', 0)} | "
            f"{canonical_counts.get(source, 0)} | {aligned}/{evaluable} | {false} |"
        )
    lines.extend(
        [
            "",
            "## Same-denominator probability score",
            "",
            "括号内为 Brier 相对 expanding historical prior 的差值，负数才是改善。",
            "",
            "| city | horizon min | OOF dates | events | prior Brier | fast path Brier (Δ) | +METAR Brier (Δ) |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *best_lines,
            "",
            "## Signal funnel",
            "",
            f"- raw matching rows: `{sum(int(row.get('raw_matching_rows', 0)) for row in source_audit)}`",
            f"- distinct collector-exact temperature events: `{sum(int(row.get('distinct_collector_exact_events', 0)) for row in source_audit)}`",
            f"- PIT path states: `{len(states)}`",
            f"- OOF predictions (model × horizon): `{len(predictions)}`",
            "- policy-selected expressions: `0`",
            "",
            "## Evidence funnel",
            "",
            f"- canonical settlement basis city-days: `{sum(bool(row['winner_bracket']) for row in basis)}`",
            f"- canonical fast information events/checkpoints: `{sum(canonical_counts.values())}` / `0`",
            "- same-checkpoint PIT market ladder: `0`",
            "- executable expressions / fills: `0 / 0`",
            "",
            "## Interpretation boundary",
            "",
            "- fast-source future-high label measures path information; it is not settlement truth.",
            "- a lower OOF Brier is evidence that the first-seen print changes path probability, "
            "not evidence of fee-adjusted alpha.",
            "- Amsterdam remains in the frozen universe even if KNMI key/collector coverage is zero; "
            "that is a coverage gap, not a strategy filter.",
            "- canonical ingest/rebuild support is implemented, but the current canonical DB/zero-notional "
            "process has not been rebuilt or restarted in this research run.",
            "- next evidence step is to accumulate/rebuild these fast events and attach pre/post event "
            "full-ladder market probabilities; only then is `model probability - market probability` "
            "a residual that can be tested.",
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
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--start-date", default="2026-07-08")
    parser.add_argument("--end-date", default="2026-07-27")
    parser.add_argument("--min-prior-dates", type=int, default=5)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    runtime = Path(args.runtime_root)
    db_path = Path(args.db_path)
    fast, source_audit = load_fast_events(runtime, start, end)
    metar = load_metar_context(db_path, start, end)
    winners = load_settlement_winners(db_path, start, end)
    states = build_states(fast, metar, winners)
    predictions = expanding_oof(states, min_prior_dates=args.min_prior_dates)
    scores = score_predictions(predictions)
    basis = source_basis_audit(states, winners)
    canonical_counts = canonical_fast_event_counts(db_path)

    out_dir = Path(args.out_dir)
    write_csv(out_dir / "source_coverage.csv", source_audit)
    write_csv(out_dir / "pit_path_states.csv", states)
    write_csv(out_dir / "oof_predictions.csv", predictions)
    write_csv(out_dir / "probability_scores.csv", scores)
    write_csv(out_dir / "source_settlement_basis.csv", basis)
    summary = {
        "schema_version": "three_city_first_seen_path_probability_research_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "cities": list(CITY_CONFIG),
        "states": len(states),
        "oof_predictions": len(predictions),
        "source_coverage": source_audit,
        "canonical_fast_event_counts": canonical_counts,
        "market_baseline_rows": 0,
        "policy_selected": 0,
        "scope": "research_zero_notional_no_execution",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_report(
        Path(args.report),
        start=start,
        end=end,
        source_audit=source_audit,
        states=states,
        predictions=predictions,
        scores=scores,
        basis=basis,
        canonical_counts=canonical_counts,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
