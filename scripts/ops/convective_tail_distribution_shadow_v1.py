#!/usr/bin/env python3
"""Run the independent Convective Tail full-ladder candidate at zero notional.

The runner consumes only shared PIT collectors.  It journals full ModelOutput
distributions and the three preregistered expression candidates.  It cannot
emit TradeIntent, plans, orders, or fills and contains no exchange client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.market_implied_tail_residual_shadow_v1 import (
    append_jsonl,
    atomic_json,
    direct_quote,
    iso_utc,
    parse_utc,
    read_json,
    repo_sha,
    utc_now,
)
from src.strategies.weather_edge_v1.tools.convective_tail_distribution import (
    normalize_market,
    score_from_artifact,
)
from weather_data_feed.city_calendar import city_local_datetime, city_timezone_name


SCHEMA_VERSION = "convective_tail_distribution_shadow_v1"
STRATEGY_FAMILY = "weather.convective_tail_distribution"
INSTANCE_ID = "convective_tail_distribution_shadow_v1"
RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_MODEL = ROOT / "src/strategies/weather_edge_v1/config/convective_tail_distribution_v1.json"
FEE_RATE = 0.05


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("once", "loop"), nargs="?", default="once")
    parser.add_argument("--market-books", type=Path, default=RUNTIME_ROOT / "market_books/latest.json")
    parser.add_argument("--ladder-snapshot", type=Path, default=RUNTIME_ROOT / "market_ladder_snapshots/latest.json")
    parser.add_argument(
        "--strategy-snapshot-dir",
        type=Path,
        default=RUNTIME_ROOT / "strategy_snapshots/paper_snapshots",
    )
    parser.add_argument("--observation-cache", type=Path, default=RUNTIME_ROOT / "output/observations/latest.json")
    parser.add_argument("--source-events-latest", type=Path, default=RUNTIME_ROOT / "output/source_events/latest.json")
    parser.add_argument("--source-events-daily-root", type=Path, default=RUNTIME_ROOT / "output/source_events")
    parser.add_argument("--forecast-root", type=Path, default=RUNTIME_ROOT / "forecast/forecast_hourly_curves")
    parser.add_argument("--model-artifact", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=RUNTIME_ROOT / "output/convective_tail_distribution_shadow_v1")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    return parser.parse_args()


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bracket_center(value: Any) -> float | None:
    numbers = [float(item) for item in re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", str(value or ""))]
    if not numbers:
        return None
    if len(numbers) >= 2 and "-" in str(value):
        return (numbers[0] + numbers[1]) / 2.0
    return numbers[0]


def fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def candidate_build_id(model_artifact: Path) -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).resolve(),
        ROOT / "src/strategies/weather_edge_v1/tools/convective_tail_distribution.py",
        model_artifact.resolve(),
    ):
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return f"{repo_sha()}+working-{digest.hexdigest()[:16]}"


def newest_strategy_snapshot(directory: Path) -> Path:
    paths = sorted(directory.glob("snapshot_*.json"))
    if not paths:
        raise FileNotFoundError(f"no strategy snapshots in {directory}")
    return paths[-1]


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                yield item


def load_curve_vintages(
    forecast_root: Path,
    decision: datetime,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for offset in (0, 1):
        directory = forecast_root / (decision.date() - timedelta(days=offset)).isoformat()
        for path in sorted(directory.glob("*.jsonl")):
            for item in read_jsonl(path):
                city = str(item.get("city") or "")
                target_date = str(item.get("target_date") or "")
                available_raw = item.get("available_at_utc") or item.get("forecast_first_seen_utc")
                if not city or not target_date or not available_raw:
                    continue
                available = parse_utc(available_raw)
                if decision - timedelta(hours=24) <= available <= decision:
                    result.setdefault((city, target_date), []).append(item)
    for key, values in result.items():
        deduped: dict[str, dict[str, Any]] = {}
        for item in sorted(values, key=lambda row: parse_utc(row.get("available_at_utc") or row.get("forecast_first_seen_utc"))):
            deduped[str(item.get("forecast_values_hash") or item.get("content_key"))] = item
        result[key] = list(deduped.values())
    return result


def curve_point(curve: list[dict[str, Any]], target_date: str, local_hour: float, field: str) -> float | None:
    points: list[tuple[float, float]] = []
    for item in curve:
        local = str(item.get("time_local") or item.get("valid_time_local") or "")
        value = finite(item.get(field))
        if local[:10] != target_date or value is None:
            continue
        points.append((int(local[11:13]) + int(local[14:16]) / 60.0, value))
    points.sort()
    if not points:
        return None
    for hour, value in points:
        if abs(hour - local_hour) < 1e-9:
            return value
    for (left_hour, left), (right_hour, right) in zip(points, points[1:]):
        if left_hour < local_hour < right_hour:
            weight = (local_hour - left_hour) / (right_hour - left_hour)
            return left + weight * (right - left)
    return None


def peak_context(curve_record: dict[str, Any]) -> dict[str, float | None]:
    curve = [item for item in curve_record.get("hourly_curve", []) if isinstance(item, dict)]
    temperatures = np.array([finite(item.get("temperature_f")) or math.nan for item in curve], dtype=float)
    if not len(curve) or not np.isfinite(temperatures).any():
        return {name: None for name in ("forecast_max_f", "peak_pop_pct", "peak_cloud_pct", "peak_wind_kt")}
    peak = int(np.nanargmax(temperatures))
    window = curve[max(0, peak - 2) : min(len(curve), peak + 3)]

    def aggregate(field: str, operation: str) -> float | None:
        values = np.array([finite(item.get(field)) or math.nan for item in window], dtype=float)
        if not np.isfinite(values).any():
            return None
        return float(np.nanmax(values) if operation == "max" else np.nanmean(values))

    return {
        "forecast_max_f": float(np.nanmax(temperatures)),
        "peak_pop_pct": aggregate("precipitation_probability_pct", "max"),
        "peak_cloud_pct": aggregate("cloud_cover_pct", "mean"),
        "peak_wind_kt": aggregate("wind_speed_10m_kt", "mean"),
    }


def hydrate_first_observations(
    state: dict[str, Any],
    daily_root: Path,
    city_dates: set[tuple[str, str]],
    decision: datetime,
) -> dict[str, dict[str, Any]]:
    first = dict(state.get("first_observation_by_city_date") or {})
    missing = {(city, date) for city, date in city_dates if f"{city}|{date}" not in first}
    for target_date in sorted({date for _, date in missing}):
        path = daily_root / target_date / "sources.jsonl"
        for item in read_jsonl(path):
            key_tuple = (str(item.get("city") or ""), str(item.get("target_date") or ""))
            if key_tuple not in missing or item.get("event_kind") != "observation":
                continue
            available_raw = item.get("first_seen_at_utc") or item.get("available_at_utc")
            observed_raw = item.get("source_event_ts_utc") or item.get("source_report_ts_utc")
            temp_c = finite(item.get("temp_c"))
            if not available_raw or not observed_raw or temp_c is None or parse_utc(available_raw) > decision:
                continue
            key = f"{key_tuple[0]}|{key_tuple[1]}"
            candidate = {
                "obs_ts_utc": iso_utc(parse_utc(observed_raw)),
                "first_seen_at_utc": iso_utc(parse_utc(available_raw)),
                "temp_f": temp_c * 9.0 / 5.0 + 32.0,
                "source": item.get("source"),
                "information_event_id": item.get("information_event_id"),
            }
            prior = first.get(key)
            if prior is None or parse_utc(candidate["obs_ts_utc"]) < parse_utc(prior["obs_ts_utc"]):
                first[key] = candidate
    return first


def build_feature(
    city: str,
    target_date: str,
    decision: datetime,
    curve_vintages: list[dict[str, Any]],
    observation: dict[str, Any],
    first_observation: dict[str, Any] | None,
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    eligible_curves = [
        item for item in curve_vintages
        if parse_utc(item.get("available_at_utc") or item.get("forecast_first_seen_utc")) <= decision
    ]
    if not eligible_curves:
        raise ValueError("no PIT forecast curve")
    current = max(eligible_curves, key=lambda item: parse_utc(item.get("available_at_utc") or item.get("forecast_first_seen_utc")))
    context = peak_context(current)
    forecast_max = finite(context["forecast_max_f"])
    current_temp = finite(observation.get("tmpf_now"))
    obs_ts_raw = observation.get("last_obs_utc")
    if forecast_max is None or current_temp is None or not obs_ts_raw:
        raise ValueError("forecast max or current target-day observation missing")
    timezone_name = str(current.get("forecast_timezone") or city_timezone_name(city) or "")
    obs_local = city_local_datetime(city, parse_utc(obs_ts_raw), timezone_name)
    local_hour = obs_local.hour + obs_local.minute / 60.0
    curve = current.get("hourly_curve") or []
    model_now = curve_point(curve, target_date, local_hour, "temperature_f")
    first_model = None
    warming_innovation = None
    if first_observation:
        first_local = city_local_datetime(city, parse_utc(first_observation["obs_ts_utc"]), timezone_name)
        first_hour = first_local.hour + first_local.minute / 60.0
        first_model = curve_point(curve, target_date, first_hour, "temperature_f")
        if first_model is not None and model_now is not None:
            warming_innovation = (
                current_temp - float(first_observation["temp_f"])
            ) - (model_now - first_model)
    forecast_maxes = [finite(peak_context(item)["forecast_max_f"]) for item in eligible_curves]
    forecast_maxes = [value for value in forecast_maxes if value is not None]
    bias = artifact["bias_lookup"]
    model_name = str(current.get("forecast_model") or "")
    feature = {
        "city_bias_pit_f": finite(bias.get("city_f", {}).get(city)) or finite(bias.get("global_f")) or 0.0,
        "source_bias_pit_f": finite(bias.get("source_f", {}).get(model_name)) or finite(bias.get("global_f")) or 0.0,
        **context,
        "relative_humidity_pct": finite(observation.get("relative_humidity_pct") or observation.get("relh_now")),
        "forecast_dispersion_f": float(np.std(forecast_maxes, ddof=1)) if len(forecast_maxes) >= 2 else 0.0,
        "warming_innovation_f": warming_innovation,
        "instant_innovation_f": current_temp - model_now if model_now is not None else None,
        "forecast_remaining_warming_f": forecast_max - model_now if model_now is not None else None,
        "local_hour_sin": math.sin(2.0 * math.pi * local_hour / 24.0),
        "local_hour_cos": math.cos(2.0 * math.pi * local_hour / 24.0),
    }
    lineage = {
        "forecast_capture_id": current.get("capture_id"),
        "forecast_values_hash": current.get("forecast_values_hash"),
        "forecast_available_at_utc": current.get("available_at_utc"),
        "forecast_first_seen_at_utc": current.get("forecast_first_seen_utc"),
        "forecast_model": model_name,
        "forecast_source": current.get("forecast_source"),
        "forecast_vintages_24h": len(eligible_curves),
        "observation_source": observation.get("source"),
        "observation_ts_utc": obs_ts_raw,
        "observation_cache_generated_at_utc": observation.get("fetched_at_utc"),
        "target_day_first_observation": first_observation,
        "feature_decision_ts_utc": iso_utc(decision),
    }
    return feature, lineage


def candidate_rows(
    model_output_id: str,
    city: str,
    target_date: str,
    decision: datetime,
    rungs: list[dict[str, Any]],
    forecast_native: float,
    build_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for anchor, rung in enumerate(rungs):
        ask = finite(rung.get("yes_ask"))
        center = finite(rung.get("bracket_center_native"))
        if ask is None or center is None or not (0.05 <= ask <= 0.20) or center <= forecast_native:
            continue
        for policy, width in (("single_yes", 1), ("adjacent_hot_strip", 2), ("bounded_hot_tail_basket", 3)):
            legs = rungs[anchor : anchor + width]
            if len(legs) != width or any(finite(leg.get("yes_ask")) is None for leg in legs):
                continue
            cost = sum(float(leg["yes_ask"]) + fee(float(leg["yes_ask"])) for leg in legs)
            probability = sum(float(leg["model_probability"]) for leg in legs)
            edge = probability - cost
            candidate_id = hashlib.sha256(f"{model_output_id}|{policy}|{anchor}".encode()).hexdigest()
            rows.append({
                "schema_version": SCHEMA_VERSION,
                "record_kind": "SignalCandidate",
                "candidate_id": candidate_id,
                "model_output_id": model_output_id,
                "strategy_family": STRATEGY_FAMILY,
                "strategy_instance_id": INSTANCE_ID,
                "execution_mode": "zero_notional_shadow",
                "notional_usd": 0.0,
                "city": city,
                "target_date": target_date,
                "decision_ts_utc": iso_utc(decision),
                "expression_policy": policy,
                "expression_width": width,
                "anchor_rank": anchor,
                "condition_ids": [leg.get("condition_id") for leg in legs],
                "brackets": [leg.get("bracket") for leg in legs],
                "predicted_probability": probability,
                "observed_ask_fee_adjusted_cost": cost,
                "continuous_tail_residual": edge,
                "eligible": edge > 0.0,
                "signal_status": "shadow_ticket" if edge > 0.0 else "no_positive_edge",
                "top_ask_capacity_shares": min(float(leg.get("yes_ask_size") or 0.0) for leg in legs),
                "depth_5c_capacity_shares": min(float(leg.get("yes_depth_ask_5c") or 0.0) for leg in legs),
                "producer_build_id": build_id,
            })
    return rows


def build_outputs(
    books: dict[str, Any],
    ladders: dict[str, Any],
    strategy_snapshot: dict[str, Any],
    observations: dict[str, Any],
    curve_index: dict[tuple[str, str], list[dict[str, Any]]],
    first_observations: dict[str, dict[str, Any]],
    artifact: dict[str, Any],
    seen: set[str],
    decision: datetime,
    build_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    books_batch = str(books.get("batch_capture_id") or "")
    if not books_batch or books_batch != str(ladders.get("batch_capture_id") or ""):
        raise ValueError("market books and ladder snapshot batch mismatch")
    book_index = {str(row.get("book_capture_id")): row for row in books.get("records", []) if isinstance(row, dict)}
    strategy_index = {
        (str(row.get("city") or ""), str(row.get("target_date") or "")): row
        for row in strategy_snapshot.get("records", []) if isinstance(row, dict)
    }
    observation_index = {
        (str(row.get("city") or ""), str(row.get("target_date") or "")): row
        for row in observations.get("records", []) if isinstance(row, dict) and row.get("status") == "ok"
    }
    outputs: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    counters = {"events_seen": 0, "events_scored": 0, "events_blocked": 0, "candidates": 0, "tail_tickets": 0, "fresh_books": 0}
    for event in ladders.get("records", []):
        if not isinstance(event, dict):
            continue
        counters["events_seen"] += 1
        city, target_date = str(event.get("city") or ""), str(event.get("target_date") or "")
        local = city_local_datetime(city, decision, city_timezone_name(city))
        checkpoint = math.floor((local.hour + local.minute / 60.0) / 2.0)
        checkpoint_key = f"{city}|{target_date}|{checkpoint}|{books_batch}"
        if checkpoint_key in seen:
            continue
        strategy_row = strategy_index.get((city, target_date))
        observation = observation_index.get((city, target_date))
        curves = curve_index.get((city, target_date), [])
        blocker_reasons = []
        if strategy_row is None:
            blocker_reasons.append("strategy_snapshot_missing")
        if observation is None:
            blocker_reasons.append("target_day_observation_missing")
        if not curves:
            blocker_reasons.append("pit_forecast_curve_missing")
        if blocker_reasons:
            counters["events_blocked"] += 1
            blockers.append({
                "schema_version": SCHEMA_VERSION, "record_kind": "checkpoint_blocker",
                "strategy_family": STRATEGY_FAMILY, "city": city, "target_date": target_date,
                "decision_ts_utc": iso_utc(decision), "checkpoint_key": checkpoint_key,
                "blocker_reasons": blocker_reasons, "notional_usd": 0.0, "producer_build_id": build_id,
            })
            seen.add(checkpoint_key)
            continue
        try:
            feature, lineage = build_feature(
                city, target_date, decision, curves, observation,
                first_observations.get(f"{city}|{target_date}"), artifact,
            )
        except (KeyError, TypeError, ValueError) as exc:
            counters["events_blocked"] += 1
            blockers.append({
                "schema_version": SCHEMA_VERSION, "record_kind": "checkpoint_blocker",
                "strategy_family": STRATEGY_FAMILY, "city": city, "target_date": target_date,
                "decision_ts_utc": iso_utc(decision), "checkpoint_key": checkpoint_key,
                "blocker_reasons": [f"feature_contract:{type(exc).__name__}:{exc}"],
                "notional_usd": 0.0, "producer_build_id": build_id,
            })
            seen.add(checkpoint_key)
            continue
        unit = str(strategy_row.get("unit") or "F").upper()
        forecast_native = float(feature["forecast_max_f"]) if unit == "F" else (float(feature["forecast_max_f"]) - 32.0) * 5.0 / 9.0
        rung_rows = []
        for rung in sorted(event.get("rungs", []), key=lambda row: bracket_center(row.get("bracket")) or math.inf):
            quote = direct_quote(book_index.get(str(rung.get("yes_book_capture_id") or "")))
            center = bracket_center(rung.get("bracket"))
            rung_rows.append({
                **rung,
                "bracket_center_native": center,
                "settlement_native_bracket_distance": center - forecast_native if center is not None else None,
                "yes_mid": quote.get("mid"), "yes_ask": quote.get("ask"),
                "yes_ask_size": quote.get("ask_size"), "yes_depth_ask_5c": quote.get("depth_ask_5c"),
                "book_fetched_at_utc": (book_index.get(str(rung.get("yes_book_capture_id") or "")) or {}).get("fetched_at_utc"),
            })
        quoted = [row for row in rung_rows if finite(row.get("yes_mid")) is not None]
        quote_fraction = len(quoted) / len(rung_rows) if rung_rows else 0.0
        if quote_fraction < 0.80 or any(row.get("bracket_center_native") is None for row in rung_rows):
            counters["events_blocked"] += 1
            blockers.append({
                "schema_version": SCHEMA_VERSION, "record_kind": "checkpoint_blocker",
                "strategy_family": STRATEGY_FAMILY, "city": city, "target_date": target_date,
                "decision_ts_utc": iso_utc(decision), "checkpoint_key": checkpoint_key,
                "blocker_reasons": ["full_ladder_direct_quote_fraction_below_80_or_bracket_unparseable"],
                "direct_quote_fraction": quote_fraction, "notional_usd": 0.0, "producer_build_id": build_id,
            })
            seen.add(checkpoint_key)
            continue
        market = normalize_market([finite(row.get("yes_mid")) or 5e-4 for row in rung_rows])
        distances = [float(row["settlement_native_bracket_distance"]) for row in rung_rows]
        model = np.asarray(score_from_artifact(artifact, feature, market, distances), dtype=float)
        if len(model):
            model[-1] = 1.0 - float(model[:-1].sum())
        for row, market_probability, model_probability in zip(rung_rows, market, model, strict=True):
            row["market_probability"] = float(market_probability)
            row["model_probability"] = float(model_probability)
            row["tail_probability_residual"] = float(model_probability - market_probability)
        model_output_id = hashlib.sha256(f"{INSTANCE_ID}|{checkpoint_key}".encode()).hexdigest()
        market_mean = float(np.sum(market * np.asarray(distances)))
        model_mean = float(np.sum(model * np.asarray(distances)))
        market_variance = float(np.sum(market * (np.asarray(distances) - market_mean) ** 2))
        model_variance = float(np.sum(model * (np.asarray(distances) - model_mean) ** 2))
        variance_ratio = model_variance / market_variance if market_variance > 0 else None
        book_times = [parse_utc(row["book_fetched_at_utc"]) for row in rung_rows if row.get("book_fetched_at_utc")]
        median_age = float(np.median([(decision - value).total_seconds() / 60.0 for value in book_times])) if book_times else math.inf
        fresh = quote_fraction >= 0.90 and median_age <= 5.0
        counters["fresh_books"] += int(fresh)
        outputs.append({
            "schema_version": SCHEMA_VERSION, "record_kind": "ModelOutput",
            "model_output_id": model_output_id, "strategy_family": STRATEGY_FAMILY,
            "strategy_instance_id": INSTANCE_ID, "model_version": artifact.get("model_version"),
            "selected_challenger": artifact.get("selected_challenger"),
            "execution_mode": "zero_notional_shadow", "notional_usd": 0.0,
            "city": city, "target_date": target_date, "unit": unit,
            "decision_ts_utc": iso_utc(decision), "checkpoint_key": checkpoint_key,
            "market_batch_capture_id": books_batch, "strategy_snapshot_ts_utc": strategy_snapshot.get("ts_utc"),
            "direct_quote_fraction": quote_fraction, "median_book_age_min": median_age,
            "fresh_book": fresh, "features": feature, "lineage": lineage, "ladder": rung_rows,
            "distribution_action": None if variance_ratio is None else "widen" if variance_ratio > 1.0 else "sharpen",
            "market_variance_native": market_variance, "model_variance_native": model_variance,
            "variance_ratio": variance_ratio,
            "hotter_tail_mass_residual": float(model[np.asarray(distances) > 0].sum() - market[np.asarray(distances) > 0].sum()),
            "producer_build_id": build_id,
        })
        event_candidates = candidate_rows(
            model_output_id, city, target_date, decision, rung_rows, forecast_native, build_id
        )
        candidates.extend(event_candidates)
        counters["events_scored"] += 1
        counters["candidates"] += len(event_candidates)
        counters["tail_tickets"] += sum(bool(row["eligible"]) for row in event_candidates)
        seen.add(checkpoint_key)
    return outputs, candidates, blockers, counters


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    started = utc_now()
    books, ladders = read_json(args.market_books), read_json(args.ladder_snapshot)
    strategy_path = newest_strategy_snapshot(args.strategy_snapshot_dir)
    strategy_snapshot = read_json(strategy_path)
    observations = read_json(args.observation_cache)
    artifact = read_json(args.model_artifact)
    decision = parse_utc(ladders.get("available_at_utc") or books.get("available_at_utc"))
    state_path = args.output_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {}
    city_dates = {(str(row.get("city") or ""), str(row.get("target_date") or "")) for row in ladders.get("records", []) if isinstance(row, dict)}
    first = hydrate_first_observations(state, args.source_events_daily_root, city_dates, decision)
    curves = load_curve_vintages(args.forecast_root, decision)
    seen = set(state.get("seen_checkpoint_keys") or [])
    build_id = candidate_build_id(args.model_artifact)
    outputs, candidates, blockers, counters = build_outputs(
        books, ladders, strategy_snapshot, observations, curves, first, artifact, seen, decision, build_id
    )
    append_jsonl(args.output_dir / "model_outputs.jsonl", outputs)
    append_jsonl(args.output_dir / "signal_candidates.jsonl", candidates)
    append_jsonl(args.output_dir / "blockers.jsonl", blockers)
    atomic_json(state_path, {
        "schema_version": SCHEMA_VERSION, "updated_at_utc": iso_utc(started),
        "seen_checkpoint_keys": sorted(seen), "first_observation_by_city_date": first,
    })
    scored = counters["events_scored"]
    summary = {
        "schema_version": SCHEMA_VERSION, "status": "ok", "strategy_family": STRATEGY_FAMILY,
        "strategy_instance_id": INSTANCE_ID, "execution_mode": "zero_notional_shadow",
        "orders_enabled": False, "notional_usd": 0.0,
        "model_artifact": str(args.model_artifact), "selected_challenger": artifact.get("selected_challenger"),
        "source_market_books": str(args.market_books), "source_ladder_snapshot": str(args.ladder_snapshot),
        "source_strategy_snapshot": str(strategy_path), "source_observation_cache": str(args.observation_cache),
        "decision_ts_utc": iso_utc(decision), "generated_at_utc": iso_utc(utc_now()),
        "fresh_book_coverage": counters["fresh_books"] / scored if scored else 0.0,
        "producer_build_id": build_id, "repository_head_sha": repo_sha(), **counters,
    }
    atomic_json(args.output_dir / "latest_summary.json", summary)
    return summary


def main() -> int:
    args = parse_args()
    if args.command == "once":
        print(json.dumps(run_once(args), sort_keys=True))
        return 0
    while True:
        try:
            print(json.dumps(run_once(args), sort_keys=True), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}", "generated_at_utc": iso_utc(utc_now())}, sort_keys=True), flush=True)
        time.sleep(max(5.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
