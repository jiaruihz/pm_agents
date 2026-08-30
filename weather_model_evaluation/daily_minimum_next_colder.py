"""PIT development model for the Tmin next-colder NO expression.

The market expression and the physical carry event are deliberately separate:

* ``no_next_colder_touch_to_eod`` means the local day never prints a lower
  native tick after the checkpoint;
* ``next_colder_exact_no`` means the final exact Tmin is anything except the
  immediately colder rung.  A two-rung overshoot loses the physical carry
  target but wins the exact-bracket NO expression.

Observation-cache minima are development proxy labels, not exchange
settlement truth.  This workflow therefore emits OOF research predictions and
coverage blockers only; it never emits a production model artifact, candidate,
intent, order, or fill.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .daily_minimum import _jsonl_rows, _parse_ts
from .exact_bracket_adapters import (
    ExactBracketAdapterBlocked,
    market_prior_from_book_rows,
    settlement_ladder_from_rungs,
)
from .probability import (
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
    ordinal_loss_values,
    ordinal_score,
)
from weather_clock_contract import local_wall_time_to_utc, parse_utc_or_none


SCHEMA_VERSION = "weather_daily_minimum_next_colder_no_development_v2"
MECHANISM_ID = "daily_low_temperature_next_colder_no_v1"
FEATURE_SET_ID = "tmin_next_colder_dual_window_features_v1"
MODEL_ID = "tmin_next_colder_regularized_logit_v1"
DEPTH_MODEL_ID = "tmin_remaining_cooling_depth_hurdle_v1"
DEPTH_CLASSES = (0, 1, 2, 3)
LABEL_BASIS = "observation_cache_intraday_min_proxy_not_settlement"
DEFAULT_CHECKPOINT_HOURS = (6, 9, 12, 18, 21, 23)
WEATHER_TAKER_FEE_RATE = 0.05
WEATHER_FEE_SOURCE = "https://docs.polymarket.com/trading/fees"


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def _cooling_window(hour: int) -> str:
    if hour <= 6:
        return "morning_cooling"
    if hour <= 9:
        return "post_sunrise_provisional_low"
    if hour < 18:
        return "daytime_warming"
    if hour < 21:
        return "evening_reopening"
    return "late_evening_finalizing"


def _window_family(hour: int) -> str:
    if hour <= 9:
        return "morning"
    if hour >= 18:
        return "evening"
    return "daytime"


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_seen_observations(
    observation_root: Path, *, cities: set[str]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Return one earliest-available row per source observation identity."""

    first: dict[tuple[str, str, str, str], tuple[datetime, dict[str, Any]]] = {}
    for row in _jsonl_rows(
        sorted(observation_root.glob("????-??-??/observations.jsonl"))
    ):
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        timezone_name = str(row.get("timezone_name") or "")
        observed = _parse_ts(row.get("last_obs_utc"))
        available = _parse_ts(
            row.get("available_at_utc")
            or row.get("ingested_at_utc")
            or row.get("fetched_at_utc")
        )
        temperature_c = _float(row.get("current_temp_c"))
        if (
            city not in cities
            or not target_date
            or not timezone_name
            or observed is None
            or available is None
            or temperature_c is None
        ):
            continue
        try:
            if observed.astimezone(ZoneInfo(timezone_name)).date().isoformat() != target_date:
                continue
        except (ValueError, KeyError):
            continue
        source = str(row.get("source") or "unknown")
        key = (city, target_date, source, observed.isoformat())
        candidate = {
            **row,
            "event_ts_utc": observed.isoformat(),
            "first_seen_ts_utc": available.isoformat(),
            "available_at_utc": available.isoformat(),
            "ingested_at_utc": str(row.get("ingested_at_utc") or available.isoformat()),
            "current_temp_c": temperature_c,
        }
        if key not in first or available < first[key][0]:
            first[key] = (available, candidate)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (_city, _target_date, _source, _observed), (_available, row) in first.items():
        grouped[(str(row["city"]), str(row["target_date"]))].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["available_at_utc"], row["event_ts_utc"]))
    return dict(grouped)


def _load_forecast_curves(
    forecast_root: Path, *, cities: set[str]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    curves: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for row in _jsonl_rows(sorted(forecast_root.glob("????-??-??/*.jsonl"))):
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        available = _parse_ts(row.get("available_at_utc"))
        if city not in cities or not target_date or available is None:
            continue
        identity = (
            city,
            target_date,
            available.isoformat(),
            str(row.get("forecast_values_hash") or ""),
        )
        if identity in seen:
            continue
        seen.add(identity)
        curves[(city, target_date)].append({**row, "_available": available})
    for rows in curves.values():
        rows.sort(key=lambda row: row["_available"])
    return dict(curves)


def _latest_forecast(
    rows: list[dict[str, Any]], decision: datetime
) -> dict[str, Any] | None:
    eligible = [row for row in rows if row["_available"] <= decision]
    return eligible[-1] if eligible else None


def _future_forecast_features(
    forecast: dict[str, Any] | None,
    *,
    decision_local: datetime,
    next_colder_native: float,
) -> dict[str, Any]:
    empty = {
        "forecast_remaining_min_c": None,
        "forecast_morning_remaining_min_c": None,
        "forecast_evening_remaining_min_c": None,
        "forecast_remaining_floor_margin_c": None,
        "forecast_morning_floor_margin_c": None,
        "forecast_evening_floor_margin_c": None,
        "forecast_available_at_utc": None,
        "forecast_values_hash": None,
        "forecast_model": None,
        "forecast_model_fallback": None,
    }
    if forecast is None:
        return empty
    values: list[tuple[datetime, float]] = []
    timezone = decision_local.tzinfo
    for item in forecast.get("hourly_curve") or []:
        raw_time = str(item.get("time_local") or "")
        temperature_f = _float(item.get("temperature_f"))
        if not raw_time or temperature_f is None:
            continue
        parsed_utc = parse_utc_or_none(raw_time, field="tmin_forecast_curve_time")
        if parsed_utc is not None:
            local = parsed_utc.astimezone(timezone)
        else:
            timezone_name = getattr(timezone, "key", None)
            if not timezone_name:
                continue
            try:
                local = local_wall_time_to_utc(
                    raw_time,
                    timezone_name=timezone_name,
                    field="tmin_forecast_curve_time_local",
                ).astimezone(timezone)
            except ValueError:
                continue
        if local.date() != decision_local.date() or local < decision_local:
            continue
        values.append((local, (temperature_f - 32.0) * 5.0 / 9.0))
    if not values:
        return {**empty, "forecast_available_at_utc": forecast["_available"].isoformat()}

    def minimum(subset: Iterable[tuple[datetime, float]]) -> float | None:
        temperatures = [temperature for _clock, temperature in subset]
        return min(temperatures) if temperatures else None

    remaining = minimum(values)
    morning = minimum(item for item in values if item[0].hour <= 9)
    evening = minimum(item for item in values if item[0].hour >= 18)
    return {
        "forecast_remaining_min_c": remaining,
        "forecast_morning_remaining_min_c": morning,
        "forecast_evening_remaining_min_c": evening,
        "forecast_remaining_floor_margin_c": None if remaining is None else remaining - next_colder_native,
        "forecast_morning_floor_margin_c": None if morning is None else morning - next_colder_native,
        "forecast_evening_floor_margin_c": None if evening is None else evening - next_colder_native,
        "forecast_available_at_utc": forecast["_available"].isoformat(),
        "forecast_values_hash": forecast.get("forecast_values_hash"),
        "forecast_model": forecast.get("forecast_model"),
        "forecast_model_fallback": bool(forecast.get("forecast_model_fallback")),
    }


def build_next_colder_panel(
    *,
    forecast_root: Path,
    observation_root: Path,
    cities: list[str],
    checkpoint_hours_local: tuple[int, ...] = DEFAULT_CHECKPOINT_HOURS,
    complete_label_hour_local: int = 23,
) -> pd.DataFrame:
    """Build the fixed scheduled PIT denominator, retaining coverage gaps."""

    city_set = set(cities)
    observations = _first_seen_observations(observation_root, cities=city_set)
    curves = _load_forecast_curves(forecast_root, cities=city_set)
    rows: list[dict[str, Any]] = []
    universe = sorted(set(curves) | set(observations))
    for city, target_date in universe:
        source_rows = observations.get((city, target_date), [])
        forecast_rows = curves.get((city, target_date), [])
        timezone_name = ""
        if source_rows:
            timezone_name = str(source_rows[0].get("timezone_name") or "")
        if not timezone_name and forecast_rows:
            timezone_name = str(forecast_rows[0].get("forecast_timezone") or "")
        if not timezone_name:
            continue
        timezone = ZoneInfo(timezone_name)
        final_events = sorted(source_rows, key=lambda row: row["event_ts_utc"])
        last_local_hour = (
            _parse_ts(final_events[-1]["event_ts_utc"]).astimezone(timezone).hour
            if final_events
            else None
        )
        final_min_c = (
            min(float(row["current_temp_c"]) for row in final_events)
            if final_events and last_local_hour is not None and last_local_hour >= complete_label_hour_local
            else None
        )
        final_tick = None if final_min_c is None else int(round(final_min_c))
        for hour in checkpoint_hours_local:
            decision_local = datetime.combine(
                date.fromisoformat(target_date), time(hour=hour), timezone
            )
            decision = decision_local.astimezone(UTC)
            pit = [
                row
                for row in source_rows
                if _parse_ts(row["available_at_utc"]) <= decision
            ]
            latest = pit[-1] if pit else None
            checkpoint_id = _sha256_json(
                {
                    "mechanism_id": MECHANISM_ID,
                    "city": city,
                    "target_date": target_date,
                    "decision_ts_utc": decision.isoformat(),
                }
            )
            base = {
                "schema_version": SCHEMA_VERSION,
                "framework_id": "weather_city_intraday_runtime_v1",
                "strategy_family": "weather.city_intraday_probability",
                "mechanism_id": MECHANISM_ID,
                "city": city,
                "target_date": target_date,
                "checkpoint_id": checkpoint_id,
                "checkpoint_name": f"d0_{hour:02d}00_local",
                "decision_ts_utc": decision.isoformat(),
                "timezone_name": timezone_name,
                "local_hour": hour,
                "hours_remaining": 24 - hour,
                "cooling_window_state": _cooling_window(hour),
                "cooling_window_family": _window_family(hour),
                "source_lattice_anchor": "observation_cache_native_c_integer_proxy",
                "settlement_lattice_anchor": "city_market_rule_not_joined",
                "expression_lattice_anchor": "polymarket_minimum_exact_bracket",
                "label_basis": LABEL_BASIS,
                "pit_provenance": "live_capture",
            }
            if latest is None:
                rows.append(
                    {
                        **base,
                        "scorable_status": "not_scorable_missing_pit_observation",
                        "label_no_next_colder_touch": None,
                        "label_next_colder_exact_no_proxy": None,
                        "remaining_cooling_ticks_proxy": None,
                        "remaining_cooling_depth_class_proxy": None,
                    }
                )
                continue
            temperatures = [float(row["current_temp_c"]) for row in pit]
            running_min_c = min(temperatures)
            running_tick = int(round(running_min_c))
            next_colder = running_tick - 1
            current_temp_c = float(latest["current_temp_c"])
            event_clock = _parse_ts(latest["event_ts_utc"])
            running_event = min(pit, key=lambda row: float(row["current_temp_c"]))
            running_clock = _parse_ts(running_event["event_ts_utc"])
            prior_60 = [
                row
                for row in pit
                if event_clock is not None
                and _parse_ts(row["event_ts_utc"]) <= event_clock - timedelta(minutes=60)
            ]
            slope_60 = (
                current_temp_c - float(prior_60[-1]["current_temp_c"])
                if prior_60
                else None
            )
            forecast = _latest_forecast(forecast_rows, decision)
            forecast_features = _future_forecast_features(
                forecast,
                decision_local=decision_local,
                next_colder_native=float(next_colder),
            )
            label_no_touch = (
                None if final_tick is None else int(final_tick >= running_tick)
            )
            label_exact_no = (
                None if final_tick is None else int(final_tick != next_colder)
            )
            remaining_cooling_ticks = (
                None if final_tick is None else max(0, running_tick - final_tick)
            )
            remaining_cooling_depth_class = (
                None
                if remaining_cooling_ticks is None
                else min(remaining_cooling_ticks, DEPTH_CLASSES[-1])
            )
            rows.append(
                {
                    **base,
                    "scorable_status": (
                        "scorable_proxy_label"
                        if final_tick is not None
                        else "not_scorable_incomplete_eod_proxy_label"
                    ),
                    "source": latest.get("source"),
                    "station": latest.get("station"),
                    "event_ts_utc": latest["event_ts_utc"],
                    "first_seen_ts_utc": latest["first_seen_ts_utc"],
                    "available_at_utc": latest["available_at_utc"],
                    "ingested_at_utc": latest["ingested_at_utc"],
                    "source_age_minutes": (
                        (decision - event_clock).total_seconds() / 60.0
                        if event_clock is not None
                        else None
                    ),
                    "current_temp_c": current_temp_c,
                    "running_min_c": running_min_c,
                    "running_min_native": running_tick,
                    "next_colder_native": next_colder,
                    "rebound_c": current_temp_c - running_min_c,
                    "minutes_since_running_min": (
                        (decision - running_clock).total_seconds() / 60.0
                        if running_clock is not None
                        else None
                    ),
                    "temperature_change_60m_c": slope_60,
                    "dewpoint_depression_f": _float(latest.get("dewpoint_depression_f")),
                    "relative_humidity_pct": _float(latest.get("relative_humidity_pct")),
                    "wind_speed_kt": _float(latest.get("wind_speed_kt")),
                    "cloud_cover_change_1h_code": _float(latest.get("cloud_cover_change_1h_code")),
                    "precip_intensity_code": _float(latest.get("precip_intensity_code")),
                    "final_min_proxy_c": final_min_c,
                    "final_min_proxy_native": final_tick,
                    "label_no_next_colder_touch": label_no_touch,
                    "label_next_colder_exact_no_proxy": label_exact_no,
                    "remaining_cooling_ticks_proxy": remaining_cooling_ticks,
                    "remaining_cooling_depth_class_proxy": remaining_cooling_depth_class,
                    **forecast_features,
                }
            )
    return pd.DataFrame(rows)


def _book_batches(
    ladder_root: Path, *, cities: set[str]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    market_root = ladder_root.parent / "market_books" / "batches"
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted(market_root.glob("????-??-??/*.jsonl.gz")):
        try:
            handle = gzip.open(path, mode="rt", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                city = str(row.get("city") or "")
                target_date = str(row.get("event_date") or row.get("target_date") or "")
                if city not in cities or str(row.get("extreme_kind") or "max") != "min":
                    continue
                snapshot_ts = str(row.get("snapshot_ts_utc") or "")
                available = _parse_ts(row.get("available_at_utc") or row.get("fetched_at_utc"))
                if not target_date or not snapshot_ts or available is None:
                    continue
                # One gzip archive is the canonical complete collector cycle.
                # ``request_batch_capture_id`` is only a transport sub-batch and
                # would split a full exact ladder into invalid fragments.
                batch_id = _sha256_json(
                    {"archive_path": str(path), "snapshot_ts_utc": snapshot_ts}
                )
                key = (city, target_date, batch_id)
                payload = grouped.setdefault(
                    key,
                    {"batch_id": batch_id, "available": available, "rows": [], "path": str(path)},
                )
                payload["available"] = max(payload["available"], available)
                payload["rows"].append(row)
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (city, target_date, _batch), payload in grouped.items():
        output[(city, target_date)].append(payload)
    for batches in output.values():
        batches.sort(key=lambda item: item["available"])
    return dict(output)


def attach_same_checkpoint_market(
    panel: pd.DataFrame, *, ladder_root: Path, cities: list[str]
) -> pd.DataFrame:
    """Attach the latest complete PIT Tmin ladder and exact NO evidence."""

    if panel.empty:
        return panel.copy()
    batches = _book_batches(ladder_root, cities=set(cities))
    output = panel.copy()
    fields = [
        "market_p_next_colder_exact_no",
        "market_next_colder_no_best_ask",
        "market_snapshot_ts_utc",
        "feature_book_snapshot_id",
        "execution_book_snapshot_id",
        "market_scorable_status",
        "market_book_source_path",
    ]
    for field in fields:
        output[field] = None
    for index, row in output.iterrows():
        if pd.isna(row.get("next_colder_native")):
            output.at[index, "market_scorable_status"] = "missing_physical_state"
            continue
        decision = _parse_ts(row["decision_ts_utc"])
        eligible = [
            batch
            for batch in batches.get((str(row["city"]), str(row["target_date"])), [])
            if batch["available"] <= decision
        ]
        if not eligible:
            output.at[index, "market_scorable_status"] = "missing_pit_tmin_full_ladder"
            continue
        batch = eligible[-1]
        book_rows = batch["rows"]
        rungs_by_condition: dict[str, dict[str, Any]] = {}
        for book in book_rows:
            condition_id = str(book.get("condition_id") or "")
            if condition_id:
                rungs_by_condition.setdefault(condition_id, book)
        def sort_key(item: dict[str, Any]) -> float:
            label = str(item.get("bracket") or "")
            number = "".join(char for char in label if char.isdigit() or char in ".-")
            try:
                return float(number)
            except ValueError:
                return math.inf
        rungs = sorted(rungs_by_condition.values(), key=sort_key)
        try:
            ladder = settlement_ladder_from_rungs(
                city=str(row["city"]),
                target_date=str(row["target_date"]),
                settlement_source="city_market_rule_pending_census",
                native_unit="C",
                native_step=1.0,
                rungs=rungs,
                allow_legacy_outer_tail_inference=True,
            )
            distribution, _meta = market_prior_from_book_rows(
                ladder,
                book_rows,
                source_snapshot_id=batch["batch_id"],
                observed_at_utc=batch["available"].isoformat(),
            )
        except ExactBracketAdapterBlocked as exc:
            output.at[index, "market_scorable_status"] = exc.reason
            continue
        target = int(row["next_colder_native"])
        condition_id = None
        for rung in rungs:
            try:
                numeric = int(float(str(rung.get("bracket") or "")))
            except ValueError:
                continue
            if numeric == target:
                condition_id = str(rung.get("condition_id") or "")
                break
        if not condition_id or condition_id not in distribution.bracket_ids:
            output.at[index, "market_scorable_status"] = "next_colder_exact_rung_missing"
            continue
        yes_probability = distribution.probabilities[
            distribution.bracket_ids.index(condition_id)
        ]
        no_rows = [
            item
            for item in book_rows
            if str(item.get("condition_id") or "") == condition_id
            and str(item.get("outcome") or "").lower() == "no"
        ]
        no_ask = None
        if no_rows:
            no_ask = _float((no_rows[-1].get("summary") or {}).get("best_ask"))
        output.at[index, "market_p_next_colder_exact_no"] = 1.0 - yes_probability
        output.at[index, "market_next_colder_no_best_ask"] = no_ask
        output.at[index, "market_snapshot_ts_utc"] = batch["available"].isoformat()
        output.at[index, "feature_book_snapshot_id"] = batch["batch_id"]
        output.at[index, "execution_book_snapshot_id"] = batch["batch_id"]
        output.at[index, "market_scorable_status"] = "scorable"
        output.at[index, "market_book_source_path"] = batch["path"]
    return output


NUMERIC_FEATURES = [
    "local_hour",
    "hours_remaining",
    "running_min_native",
    "rebound_c",
    "minutes_since_running_min",
    "temperature_change_60m_c",
    "source_age_minutes",
    "forecast_remaining_floor_margin_c",
    "forecast_morning_floor_margin_c",
    "forecast_evening_floor_margin_c",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "cloud_cover_change_1h_code",
    "precip_intensity_code",
]
CATEGORICAL_FEATURES = ["city", "cooling_window_state", "source"]


def _pipeline() -> Pipeline:
    numeric = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    return Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    [
                        ("numeric", numeric, NUMERIC_FEATURES),
                        ("categorical", categorical, CATEGORICAL_FEATURES),
                    ]
                ),
            ),
            (
                "classifier",
                LogisticRegression(C=0.20, max_iter=2000, random_state=20260811),
            ),
        ]
    )


def _date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("target_date")["target_date"].transform("size").to_numpy(float)
    weights = 1.0 / counts
    return weights * len(weights) / weights.sum()


def _clock_baseline(train: pd.DataFrame, test: pd.DataFrame, label: str) -> np.ndarray:
    y = train[label].astype(int)
    pooled = (float(y.sum()) + 2.0) / (len(y) + 4.0)
    values: dict[str, float] = {}
    for window, subset in train.groupby("cooling_window_family"):
        local = subset[label].astype(int)
        values[str(window)] = (float(local.sum()) + 2.0 * pooled) / (len(local) + 2.0)
    return np.asarray(
        [values.get(str(window), pooled) for window in test["cooling_window_family"]],
        dtype=float,
    )


def _conditional_depth_baseline(
    train: pd.DataFrame, test: pd.DataFrame
) -> np.ndarray:
    """P(depth=1/2/3+ | any colder), using prior-date rows only."""

    conditional = train[train["remaining_cooling_depth_class_proxy"].astype(int) > 0]
    pooled_counts = np.asarray(
        [
            int((conditional["remaining_cooling_depth_class_proxy"].astype(int) == depth).sum())
            for depth in DEPTH_CLASSES[1:]
        ],
        dtype=float,
    )
    pooled = (pooled_counts + 1.0) / (pooled_counts.sum() + len(pooled_counts))
    values: dict[str, np.ndarray] = {}
    for window, subset in conditional.groupby("cooling_window_family"):
        counts = np.asarray(
            [
                int((subset["remaining_cooling_depth_class_proxy"].astype(int) == depth).sum())
                for depth in DEPTH_CLASSES[1:]
            ],
            dtype=float,
        )
        values[str(window)] = (counts + 2.0 * pooled) / (counts.sum() + 2.0)
    return np.vstack(
        [values.get(str(window), pooled) for window in test["cooling_window_family"]]
    )


def _combine_depth_hurdle(
    p_no_touch: np.ndarray, conditional_depth: np.ndarray
) -> np.ndarray:
    p_zero = np.clip(np.asarray(p_no_touch, dtype=float), 0.0, 1.0)
    conditional = np.asarray(conditional_depth, dtype=float)
    conditional /= conditional.sum(axis=1, keepdims=True)
    output = np.column_stack([p_zero, (1.0 - p_zero)[:, None] * conditional])
    output /= output.sum(axis=1, keepdims=True)
    return output


def walk_forward_next_colder(
    panel: pd.DataFrame, *, min_train_dates: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    labels = {
        "no_touch": "label_no_next_colder_touch",
        "exact_no": "label_next_colder_exact_no_proxy",
    }
    eligible = panel[
        panel["scorable_status"].eq("scorable_proxy_label")
        & panel["forecast_remaining_min_c"].notna()
    ].copy()
    predictions: list[pd.DataFrame] = []
    for target_date in sorted(eligible["target_date"].astype(str).unique()):
        train = eligible[eligible["target_date"].astype(str) < target_date].copy()
        test = eligible[eligible["target_date"].astype(str) == target_date].copy()
        if train["target_date"].nunique() < min_train_dates or test.empty:
            continue
        result = test.copy()
        result["split"] = "expanding_oof_development_proxy"
        result["train_dates"] = int(train["target_date"].nunique())
        for name, label in labels.items():
            baseline = _clock_baseline(train, test, label)
            result[f"p_clock_{name}"] = baseline
            if train[label].nunique() < 2:
                result[f"p_model_{name}"] = baseline
                result[f"model_status_{name}"] = "baseline_only_single_train_class"
                continue
            model = _pipeline()
            model.fit(
                train[NUMERIC_FEATURES + CATEGORICAL_FEATURES],
                train[label].astype(int),
                classifier__sample_weight=_date_weights(train),
            )
            result[f"p_model_{name}"] = model.predict_proba(
                test[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
            )[:, 1]
            result[f"model_status_{name}"] = "regularized_logit"

        conditional_train = train[
            train["remaining_cooling_depth_class_proxy"].astype(int) > 0
        ].copy()
        conditional_clock = _conditional_depth_baseline(train, test)
        conditional_model = conditional_clock.copy()
        severity_status = "baseline_only_insufficient_conditional_classes"
        severity_classes = sorted(
            conditional_train["remaining_cooling_depth_class_proxy"]
            .astype(int)
            .unique()
            .tolist()
        )
        if len(severity_classes) >= 2:
            severity = _pipeline()
            severity.fit(
                conditional_train[NUMERIC_FEATURES + CATEGORICAL_FEATURES],
                conditional_train["remaining_cooling_depth_class_proxy"].astype(int),
                classifier__sample_weight=_date_weights(conditional_train),
            )
            predicted = severity.predict_proba(
                test[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
            )
            conditional_model = np.zeros((len(test), 3), dtype=float)
            for index, depth in enumerate(severity.named_steps["classifier"].classes_):
                conditional_model[:, int(depth) - 1] = predicted[:, index]
            # A three-class Dirichlet prior gives unseen severity classes
            # non-zero mass without introducing a tuned mixing coefficient.
            effective_dates = float(conditional_train["target_date"].nunique())
            prior_strength = float(len(DEPTH_CLASSES) - 1)
            conditional_model = (
                effective_dates * conditional_model
                + prior_strength * conditional_clock
            ) / (effective_dates + prior_strength)
            conditional_model /= conditional_model.sum(axis=1, keepdims=True)
            severity_status = "regularized_multinomial_logit_with_clock_shrinkage"

        clock_depth = _combine_depth_hurdle(
            result["p_clock_no_touch"].astype(float).to_numpy(), conditional_clock
        )
        model_depth = _combine_depth_hurdle(
            result["p_model_no_touch"].astype(float).to_numpy(), conditional_model
        )
        for index, depth in enumerate(DEPTH_CLASSES):
            result[f"p_clock_depth_{depth}"] = clock_depth[:, index]
            result[f"p_model_depth_{depth}"] = model_depth[:, index]
        result["p_clock_exact_no_structured"] = 1.0 - clock_depth[:, 1]
        result["p_model_exact_no_structured"] = 1.0 - model_depth[:, 1]
        result["model_status_depth_severity"] = severity_status
        predictions.append(result)
    scored = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    summary: dict[str, Any] = {
        "status": "development_proxy_only" if not scored.empty else "blocked_insufficient_walk_forward_dates",
        "rows": int(len(scored)),
        "target_dates": int(scored["target_date"].nunique()) if not scored.empty else 0,
        "min_train_dates": min_train_dates,
        "heads": {},
    }
    if scored.empty:
        return scored, summary
    for name, label in labels.items():
        model_probability = scored[f"p_model_{name}"].astype(float).to_numpy()
        baseline_probability = scored[f"p_clock_{name}"].astype(float).to_numpy()
        model_score = binary_score(scored, model_probability, label_column=label)
        baseline_score = binary_score(scored, baseline_probability, label_column=label)
        y = scored[label].astype(int).to_numpy()
        log_bootstrap = date_block_bootstrap_delta(
            scored,
            binary_loss_values(y, model_probability, metric="logloss"),
            binary_loss_values(y, baseline_probability, metric="logloss"),
        )
        brier_bootstrap = date_block_bootstrap_delta(
            scored,
            binary_loss_values(y, model_probability, metric="brier"),
            binary_loss_values(y, baseline_probability, metric="brier"),
        )
        by_window = {}
        for window, subset in scored.groupby("cooling_window_family"):
            by_window[str(window)] = {
                "model": binary_score(
                    subset,
                    subset[f"p_model_{name}"].astype(float).to_numpy(),
                    label_column=label,
                ),
                "clock_baseline": binary_score(
                    subset,
                    subset[f"p_clock_{name}"].astype(float).to_numpy(),
                    label_column=label,
                ),
            }
        summary["heads"][name] = {
            "target_id": (
                "no_next_colder_touch_to_eod"
                if name == "no_touch"
                else "next_colder_exact_no_proxy"
            ),
            "target_kind": "physical_path" if name == "no_touch" else "market_expression",
            "model": model_score,
            "clock_baseline": baseline_score,
            "model_minus_clock_logloss": log_bootstrap,
            "model_minus_clock_brier": brier_bootstrap,
            "by_cooling_window": by_window,
        }

    depth_label = "remaining_cooling_depth_class_proxy"
    model_depth = scored[[f"p_model_depth_{depth}" for depth in DEPTH_CLASSES]].to_numpy(float)
    clock_depth = scored[[f"p_clock_depth_{depth}" for depth in DEPTH_CLASSES]].to_numpy(float)
    depth_y = scored[depth_label].astype(int).to_numpy()
    depth_summary = {
        "target_id": "remaining_cooling_depth_proxy_0_1_2_3plus",
        "target_kind": "settlement_outcome_proxy",
        "class_counts": {
            str(depth): int((depth_y == depth).sum()) for depth in DEPTH_CLASSES
        },
        "class_target_dates": {
            str(depth): int(scored.loc[depth_y == depth, "target_date"].nunique())
            for depth in DEPTH_CLASSES
        },
        "model": ordinal_score(scored, model_depth, label_column=depth_label),
        "clock_baseline": ordinal_score(scored, clock_depth, label_column=depth_label),
        "model_minus_clock_logloss": date_block_bootstrap_delta(
            scored,
            ordinal_loss_values(depth_y, model_depth, metric="logloss"),
            ordinal_loss_values(depth_y, clock_depth, metric="logloss"),
        ),
        "model_minus_clock_rps": date_block_bootstrap_delta(
            scored,
            ordinal_loss_values(depth_y, model_depth, metric="rps"),
            ordinal_loss_values(depth_y, clock_depth, metric="rps"),
        ),
    }
    structured_probability = scored["p_model_exact_no_structured"].to_numpy(float)
    direct_probability = scored["p_model_exact_no"].to_numpy(float)
    clock_probability = scored["p_clock_exact_no_structured"].to_numpy(float)
    exact_y = scored["label_next_colder_exact_no_proxy"].astype(int).to_numpy()
    depth_summary["exact_no_expression"] = {
        "identity": "P(exact_next_colder_NO)=1-P(remaining_cooling_depth=1)",
        "structured": binary_score(
            scored,
            structured_probability,
            label_column="label_next_colder_exact_no_proxy",
        ),
        "direct_binary": binary_score(
            scored,
            direct_probability,
            label_column="label_next_colder_exact_no_proxy",
        ),
        "structured_minus_direct_logloss": date_block_bootstrap_delta(
            scored,
            binary_loss_values(exact_y, structured_probability, metric="logloss"),
            binary_loss_values(exact_y, direct_probability, metric="logloss"),
        ),
        "structured_minus_direct_brier": date_block_bootstrap_delta(
            scored,
            binary_loss_values(exact_y, structured_probability, metric="brier"),
            binary_loss_values(exact_y, direct_probability, metric="brier"),
        ),
        "structured_minus_clock_logloss": date_block_bootstrap_delta(
            scored,
            binary_loss_values(exact_y, structured_probability, metric="logloss"),
            binary_loss_values(exact_y, clock_probability, metric="logloss"),
        ),
    }
    summary["remaining_cooling_depth_head"] = depth_summary
    return scored, summary


def _prediction_table(scored: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in scored.to_dict("records"):
        for name, label, target_id, target_kind in (
            (
                "no_touch",
                "label_no_next_colder_touch",
                "no_next_colder_touch_to_eod",
                "physical_path",
            ),
            (
                "exact_no",
                "label_next_colder_exact_no_proxy",
                "next_colder_exact_no_proxy",
                "market_expression",
            ),
            (
                "exact_no_structured",
                "label_next_colder_exact_no_proxy",
                "next_colder_exact_no_proxy_structured_depth",
                "market_expression",
            ),
        ):
            rows.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "checkpoint_id": row["checkpoint_id"],
                    "target_id": target_id,
                    "target_kind": target_kind,
                    "p_model": row[f"p_model_{name}"],
                    "p_simple_baseline": row[f"p_clock_{name}"],
                    "label": row[label],
                    "split": row["split"],
                    "model_id": DEPTH_MODEL_ID if name == "exact_no_structured" else MODEL_ID,
                    "feature_set_id": FEATURE_SET_ID,
                    "pit_provenance": row["pit_provenance"],
                    "scorable_status": "scorable_proxy_label_not_settlement",
                    "market_p": (
                        row.get("market_p_next_colder_exact_no")
                        if name in {"exact_no", "exact_no_structured"}
                        else None
                    ),
                    "market_feature_role": "none",
                    "market_feature_clock": "decision_current" if name in {"exact_no", "exact_no_structured"} else "none",
                    "feature_book_snapshot_id": row.get("feature_book_snapshot_id"),
                    "execution_book_snapshot_id": row.get("execution_book_snapshot_id"),
                    "expression_side": "NO" if name in {"exact_no", "exact_no_structured"} else None,
                    "executable_cost": row.get("market_next_colder_no_best_ask") if name in {"exact_no", "exact_no_structured"} else None,
                    "taker_fee_per_share": row.get("weather_taker_fee_per_share") if name in {"exact_no", "exact_no_structured"} else None,
                    "model_net_edge_at_ask": (
                        row.get("structured_net_edge_at_no_ask")
                        if name == "exact_no_structured"
                        else row.get("direct_net_edge_at_no_ask")
                        if name == "exact_no"
                        else None
                    ),
                    "market_snapshot_ts_utc": row.get("market_snapshot_ts_utc") if name in {"exact_no", "exact_no_structured"} else None,
                    "label_basis": LABEL_BASIS,
                }
            )
        for depth in DEPTH_CLASSES:
            rows.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "checkpoint_id": row["checkpoint_id"],
                    "target_id": f"remaining_cooling_depth_proxy_{depth if depth < 3 else '3plus'}",
                    "target_kind": "settlement_outcome_proxy",
                    "p_model": row[f"p_model_depth_{depth}"],
                    "p_simple_baseline": row[f"p_clock_depth_{depth}"],
                    "label": int(row["remaining_cooling_depth_class_proxy"] == depth),
                    "split": row["split"],
                    "model_id": DEPTH_MODEL_ID,
                    "feature_set_id": FEATURE_SET_ID,
                    "pit_provenance": row["pit_provenance"],
                    "scorable_status": "scorable_proxy_label_not_settlement",
                    "market_p": None,
                    "market_feature_role": "none",
                    "market_feature_clock": "none",
                    "feature_book_snapshot_id": row.get("feature_book_snapshot_id"),
                    "execution_book_snapshot_id": row.get("execution_book_snapshot_id"),
                    "expression_side": None,
                    "executable_cost": None,
                    "taker_fee_per_share": None,
                    "model_net_edge_at_ask": None,
                    "market_snapshot_ts_utc": None,
                    "label_basis": LABEL_BASIS,
                }
            )
    return pd.DataFrame(rows)


def _input_inventory(path: Path, pattern: str) -> dict[str, Any]:
    files = sorted(path.glob(pattern))
    return {
        "root": str(path),
        "file_count": len(files),
        "first_file": str(files[0]) if files else None,
        "last_file": str(files[-1]) if files else None,
    }


def run_daily_minimum_next_colder_development(
    *,
    forecast_root: Path,
    observation_root: Path,
    ladder_root: Path,
    output_dir: Path,
    cities: list[str],
    checkpoint_hours_local: tuple[int, ...] = DEFAULT_CHECKPOINT_HOURS,
    min_train_dates: int = 7,
    promotion_min_dates: int = 30,
    code_revision: str | None = None,
) -> dict[str, Any]:
    panel = build_next_colder_panel(
        forecast_root=forecast_root,
        observation_root=observation_root,
        cities=cities,
        checkpoint_hours_local=checkpoint_hours_local,
    )
    panel = attach_same_checkpoint_market(panel, ladder_root=ladder_root, cities=cities)
    scored, model_summary = walk_forward_next_colder(
        panel, min_train_dates=min_train_dates
    )
    if not scored.empty:
        ask = pd.to_numeric(
            scored["market_next_colder_no_best_ask"], errors="coerce"
        )
        scored["weather_taker_fee_per_share"] = (
            WEATHER_TAKER_FEE_RATE * ask * (1.0 - ask)
        )
        scored["structured_net_edge_at_no_ask"] = (
            scored["p_model_exact_no_structured"]
            - ask
            - scored["weather_taker_fee_per_share"]
        )
        scored["direct_net_edge_at_no_ask"] = (
            scored["p_model_exact_no"]
            - ask
            - scored["weather_taker_fee_per_share"]
        )
    prediction = _prediction_table(scored) if not scored.empty else pd.DataFrame()
    source_rows = panel[panel["running_min_native"].notna()] if not panel.empty else panel
    labeled = panel[panel["scorable_status"].eq("scorable_proxy_label")] if not panel.empty else panel
    market = panel[panel["market_scorable_status"].eq("scorable")] if not panel.empty else panel
    same_row_market = (
        scored[
            scored["market_p_next_colder_exact_no"].notna()
            & scored["label_next_colder_exact_no_proxy"].notna()
        ]
        if not scored.empty
        else scored
    )
    market_baseline: dict[str, Any]
    if same_row_market.empty:
        market_baseline = {
            "status": "blocked_no_same_row_pit_market_and_completed_proxy_label",
            "rows": 0,
            "target_dates": 0,
        }
    else:
        market_probability = same_row_market["market_p_next_colder_exact_no"].astype(float).to_numpy()
        structured_probability = same_row_market["p_model_exact_no_structured"].astype(float).to_numpy()
        direct_probability = same_row_market["p_model_exact_no"].astype(float).to_numpy()
        market_score = binary_score(
            same_row_market,
            market_probability,
            label_column="label_next_colder_exact_no_proxy",
        )
        structured_score = binary_score(
            same_row_market,
            structured_probability,
            label_column="label_next_colder_exact_no_proxy",
        )
        direct_score = binary_score(
            same_row_market,
            direct_probability,
            label_column="label_next_colder_exact_no_proxy",
        )
        selected = same_row_market[
            same_row_market["structured_net_edge_at_no_ask"] > 0
        ].copy()
        if not selected.empty:
            selected["unit_payoff_proxy"] = (
                selected["label_next_colder_exact_no_proxy"].astype(float)
                - selected["market_next_colder_no_best_ask"].astype(float)
                - selected["weather_taker_fee_per_share"].astype(float)
            )
        market_baseline = {
            "status": "proxy_label_only_not_settlement",
            "inference_status": (
                "blocked_less_than_5_target_dates"
                if same_row_market["target_date"].nunique() < 5
                else "date_block_inference_available"
            ),
            "market": market_score,
            "structured_depth": structured_score,
            "direct_binary": direct_score,
            "structured_minus_market_logloss_point": (
                structured_score["logloss"] - market_score["logloss"]
            ),
            "structured_minus_market_brier_point": (
                structured_score["brier"] - market_score["brier"]
            ),
            "quote_level_expression_diagnostic": {
                "fee_rate": WEATHER_TAKER_FEE_RATE,
                "fee_formula": "shares * fee_rate * price * (1-price)",
                "fee_source": WEATHER_FEE_SOURCE,
                "positive_structured_net_edge_rows": int(len(selected)),
                "positive_structured_net_edge_target_dates": int(
                    selected["target_date"].nunique()
                ),
                "proxy_wins": int(
                    selected["label_next_colder_exact_no_proxy"].sum()
                ) if not selected.empty else 0,
                "unit_payoff_proxy_sum": float(selected["unit_payoff_proxy"].sum())
                if not selected.empty
                else 0.0,
                "capacity_status": "blocked_best_ask_without_verified_target_size_depth",
            },
        }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "framework_id": "weather_city_intraday_runtime_v1",
        "strategy_family": "weather.city_intraday_probability",
        "mechanism_id": MECHANISM_ID,
        "status": "research_only_blocked_for_settlement_and_market_forward",
        "hypothesis": (
            "A hurdle model for remaining cooling depth (0/1/2/3+ native ticks) "
            "improves the Tmin next-colder exact NO probability over a direct binary head; "
            "promotion additionally requires beating same-row market on settlement truth."
        ),
        "target_ontology": {
            "physical_path": "no_next_colder_touch_to_eod",
            "market_expression": "next_colder_exact_no",
            "settlement_outcome_proxy": "remaining_cooling_depth_0_1_2_3plus",
            "semantic_warning": (
                "a two-or-more rung overshoot makes physical no-touch false but exact-bracket NO true"
            ),
        },
        "cities": cities,
        "checkpoint_hours_local": list(checkpoint_hours_local),
        "denominator_scope": (
            "registered city-target_dates with forecast or observation raw, crossed with fixed local checkpoints; "
            "missing PIT source/book/label rows retained as structured coverage gaps"
        ),
        "source_anchor": "observation-cache point observations, earliest available row per source observation",
        "settlement_anchor": "not joined; development labels are end-of-day observation-cache minima proxy",
        "expression_anchor": "Polymarket minimum exact bracket, immediately colder numeric native-C rung",
        "market_feature_role": "none_development_weather_head",
        "market_baseline_role": "same-checkpoint exact-NO baseline when both PIT book and label exist",
        "official_fee": {
            "weather_taker_fee_rate": WEATHER_TAKER_FEE_RATE,
            "formula": "shares * fee_rate * price * (1-price)",
            "source": WEATHER_FEE_SOURCE,
        },
        "inputs": {
            "forecast": _input_inventory(forecast_root, "????-??-??/*.jsonl"),
            "observation": _input_inventory(observation_root, "????-??-??/observations.jsonl"),
            "ladder": _input_inventory(ladder_root, "????-??-??/market_ladder_snapshot_*.json"),
            "code_revision": code_revision,
        },
        "signal_funnel": {
            "fixed_checkpoint_rows": int(len(panel)),
            "pit_running_min_rows": int(len(source_rows)),
            "completed_proxy_label_rows": int(len(labeled)),
            "expanding_oof_rows": int(len(scored)),
            "first_city_day_signal": 0,
            "policy_selected": 0,
        },
        "evidence_funnel": {
            "pit_source_rows": int(len(source_rows)),
            "pit_full_ladder_rows": int(len(market)),
            "settlement_truth_rows": 0,
            "same_row_market_and_proxy_label_rows": int(len(same_row_market)),
            "fresh_executable_no_ask_rows": int(
                market["market_next_colder_no_best_ask"].notna().sum()
            ) if not market.empty else 0,
            "fills": 0,
        },
        "coverage": {
            "panel_dates_by_city": {
                str(k): int(v)
                for k, v in panel.groupby("city")["target_date"].nunique().to_dict().items()
            } if not panel.empty else {},
            "proxy_label_dates_by_city": {
                str(k): int(v)
                for k, v in labeled.groupby("city")["target_date"].nunique().to_dict().items()
            } if not labeled.empty else {},
            "pit_market_dates_by_city": {
                str(k): int(v)
                for k, v in market.groupby("city")["target_date"].nunique().to_dict().items()
            } if not market.empty else {},
            "cooling_window_rows": {
                str(k): int(v)
                for k, v in source_rows["cooling_window_family"].value_counts().to_dict().items()
            } if not source_rows.empty else {},
        },
        "development_model": model_summary,
        "same_row_market_baseline": market_baseline,
        "promotion_gate": {
            "minimum_new_settled_target_dates": promotion_min_dates,
            "probability_gate": "model-minus-market logloss and Brier target-date bootstrap CI both below zero",
            "frozen_forward_started": False,
            "promotion_ready": False,
        },
        "blockers": [
            "exchange_settlement_labels_not_joined",
            "same_row_tmin_market_history_started_after_2026_08_11_rollout",
            "source_to_settlement_basis_pending_for_seoul_tokyo",
            "minimum_30_new_settled_target_dates_not_met",
            "remaining_cooling_depth_classes_2_and_3plus_sparse_in_development_proxy",
        ],
        "production": {
            "probability_artifact_emitted": False,
            "signal_candidates": 0,
            "trade_intents": 0,
            "orders_submitted": 0,
            "fills": 0,
            "execution_mode": "research_only",
        },
        "artifacts": {
            "panel": str(output_dir / "next_colder_checkpoint_panel.csv"),
            "oof": str(output_dir / "next_colder_oof.csv"),
            "prediction_table": str(output_dir / "prediction_table.csv"),
            "summary": str(output_dir / "summary.json"),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "next_colder_checkpoint_panel.csv", index=False)
    scored.to_csv(output_dir / "next_colder_oof.csv", index=False)
    prediction.to_csv(output_dir / "prediction_table.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary
