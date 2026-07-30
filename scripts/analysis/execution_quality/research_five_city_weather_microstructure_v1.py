#!/usr/bin/env python3
"""Research five weather cities at complete-ladder snapshot grain.

The study joins archived books to weather information strictly as-of the book
timestamp.  Seoul and Busan can use the dedicated Korean AMOS first-seen
checkpoints; the remaining cities use the canonical observation archive.

Displayed depth and a later quote crossing a hypothetical maker price are not
treated as traded volume or a fill.  The latter is only an observable
quote-cross proxy.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import city_local_datetime


DEFAULT_CITIES = ("Tokyo", "Busan", "Seoul", "Amsterdam", "Helsinki")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def city_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "")


def market_round_c(value: float | None) -> int | None:
    if value is None:
        return None
    return int(math.floor(value + 0.5))


def bracket_anchor(value: Any) -> float | None:
    match = NUMBER_RE.search(str(value or ""))
    return float(match.group()) if match else None


def local_hour_band(hour: int) -> str:
    if hour < 6:
        return "00-06"
    if hour < 10:
        return "06-10"
    if hour < 12:
        return "10-12"
    if hour < 14:
        return "12-14"
    if hour < 16:
        return "14-16"
    if hour < 18:
        return "16-18"
    return "18-24"


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    handle = (
        gzip.open(path, mode="rt", encoding="utf-8")
        if path.suffix == ".gz"
        else path.open(mode="r", encoding="utf-8")
    )
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def generic_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("status") != "ok" or not row.get("fetched_at_utc"):
        return None
    delta_1h_f = finite(row.get("d_tmpf_1h"))
    delta_30m_f = finite(row.get("d_tmpf_30m"))
    return {
        "available_at_utc": str(row["fetched_at_utc"]),
        "epoch_key": str(
            row.get("observation_time_utc")
            or row.get("observed_at_utc")
            or row["fetched_at_utc"]
        ),
        "source": str(row.get("source") or ""),
        "station": str(row.get("station") or ""),
        "current_temp_c": finite(row.get("current_temp_c")),
        "running_max_c": finite(row.get("running_max_c")),
        "decline_c": finite(row.get("decline_c")),
        "delta_30m_c": delta_30m_f / 1.8 if delta_30m_f is not None else None,
        "delta_60m_c": delta_1h_f / 1.8 if delta_1h_f is not None else None,
        "minutes_since_running_max": finite(row.get("minutes_since_running_max")),
        "precip_state": str(row.get("precip_state") or ""),
        "wind_speed_kt": finite(row.get("wind_speed_kt")),
        "dewpoint_depression_c": (
            finite(row.get("dewpoint_depression_f")) / 1.8
            if finite(row.get("dewpoint_depression_f")) is not None
            else None
        ),
    }


def korea_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    available = row.get("source_available_at_utc") or row.get(
        "source_first_seen_ts_utc"
    )
    if not available:
        return None
    windows = row.get("path_windows") or {}
    window_30m = windows.get("30m") or {}
    window_60m = windows.get("60m") or {}
    return {
        "available_at_utc": str(available),
        "epoch_key": str(
            row.get("source_event_key")
            or row.get("source_observation_ts_utc")
            or available
        ),
        "source": "amos_runway",
        "station": str(row.get("station") or ""),
        "current_temp_c": finite(row.get("source_temp_c")),
        "running_max_c": finite(row.get("source_running_max_c")),
        "decline_c": finite(row.get("distance_below_source_running_max_c")),
        "delta_30m_c": finite(window_30m.get("temp_delta_c")),
        "delta_60m_c": finite(window_60m.get("temp_delta_c")),
        "minutes_since_running_max": finite(
            row.get("minutes_since_source_running_max")
        ),
        "precip_state": str(row.get("precip_state") or ""),
        "wind_speed_kt": finite(
            row.get("source_wind_speed_kt")
            or row.get("metar_wind_speed_kt")
        ),
        "dewpoint_depression_c": finite(row.get("dewpoint_depression_c")),
    }


ObservationIndex = dict[
    tuple[str, str], tuple[list[datetime], list[dict[str, Any]]]
]


def finalize_observation_index(
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]]
) -> ObservationIndex:
    result: ObservationIndex = {}
    for key, values in grouped.items():
        values.sort(key=lambda item: item[0])
        deduped: list[tuple[datetime, dict[str, Any]]] = []
        for timestamp, row in values:
            if deduped and row["epoch_key"] == deduped[-1][1]["epoch_key"]:
                # Preserve the first time this information epoch became
                # observable.  Repeated fetches must not move availability
                # forward and create look-ahead or false staleness.
                continue
            else:
                deduped.append((timestamp, row))
        result[key] = (
            [item[0] for item in deduped],
            [item[1] for item in deduped],
        )
    return result


def load_generic_observations(
    root: Path,
    cities: set[str],
    start_date: str | None,
    end_date: str | None,
) -> ObservationIndex:
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = (
        defaultdict(list)
    )
    for path in sorted(root.glob("*/observations.jsonl")):
        folder_date = path.parent.name
        if start_date and folder_date < start_date:
            continue
        if end_date and folder_date > end_date:
            continue
        for raw in iter_jsonl(path):
            city = city_key(raw.get("city"))
            target_date = str(raw.get("target_date") or "")
            if city not in cities or not target_date:
                continue
            row = generic_observation(raw)
            if row:
                grouped[(city, target_date)].append(
                    (utc(row["available_at_utc"]), row)
                )
    return finalize_observation_index(grouped)


def load_korea_observations(
    root: Path,
    cities: set[str],
    start_date: str | None,
    end_date: str | None,
) -> ObservationIndex:
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = (
        defaultdict(list)
    )
    for path in sorted(root.glob("*.jsonl")):
        folder_date = path.stem
        if start_date and folder_date < start_date:
            continue
        if end_date and folder_date > end_date:
            continue
        for raw in iter_jsonl(path):
            city = city_key(raw.get("city"))
            target_date = str(raw.get("target_date") or "")
            if city not in cities or not target_date:
                continue
            row = korea_observation(raw)
            if row:
                grouped[(city, target_date)].append(
                    (utc(row["available_at_utc"]), row)
                )
    return finalize_observation_index(grouped)


def align_one(
    index: ObservationIndex,
    city: str,
    target_date: str,
    snapshot: datetime,
) -> dict[str, Any] | None:
    values = index.get((city_key(city), target_date))
    if not values:
        return None
    timestamps, rows = values
    position = bisect_right(timestamps, snapshot) - 1
    return rows[position] if position >= 0 else None


def align_observation(
    generic_index: ObservationIndex,
    korea_index: ObservationIndex,
    city: str,
    target_date: str,
    snapshot: datetime,
) -> dict[str, Any] | None:
    if city in {"Seoul", "Busan"}:
        korea = align_one(korea_index, city, target_date, snapshot)
        if korea:
            return korea
    return align_one(generic_index, city, target_date, snapshot)


def effective_yes_book(
    yes: dict[str, Any] | None, no: dict[str, Any] | None
) -> dict[str, float | str | None]:
    yes_summary = (yes or {}).get("summary") or {}
    no_summary = (no or {}).get("summary") or {}
    direct_bid = finite(yes_summary.get("best_bid"))
    direct_ask = finite(yes_summary.get("best_ask"))
    no_bid = finite(no_summary.get("best_bid"))
    no_ask = finite(no_summary.get("best_ask"))
    complement_bid = 1.0 - no_ask if no_ask is not None else None
    complement_ask = 1.0 - no_bid if no_bid is not None else None

    bid_candidates = [
        ("direct_yes", direct_bid, finite(yes_summary.get("bid_size"))),
        ("complement_no", complement_bid, finite(no_summary.get("ask_size"))),
    ]
    ask_candidates = [
        ("direct_yes", direct_ask, finite(yes_summary.get("ask_size"))),
        ("complement_no", complement_ask, finite(no_summary.get("bid_size"))),
    ]
    bids = [item for item in bid_candidates if item[1] is not None]
    asks = [item for item in ask_candidates if item[1] is not None]
    best_bid = max(bids, key=lambda item: float(item[1])) if bids else None
    best_ask = min(asks, key=lambda item: float(item[1])) if asks else None
    bid = float(best_bid[1]) if best_bid else None
    ask = float(best_ask[1]) if best_ask else None
    spread = ask - bid if ask is not None and bid is not None else None
    midpoint = (
        (ask + bid) / 2
        if spread is not None and spread >= -1e-9
        else None
    )
    return {
        "best_bid": bid,
        "best_ask": ask,
        "bid_size": best_bid[2] if best_bid else None,
        "ask_size": best_ask[2] if best_ask else None,
        "spread": spread,
        "mid": midpoint,
    }


def weather_state(observation: dict[str, Any] | None) -> str:
    if not observation:
        return "missing"
    decline = finite(observation.get("decline_c")) or 0.0
    delta_60m = finite(observation.get("delta_60m_c"))
    minutes_since_max = finite(observation.get("minutes_since_running_max"))
    if decline >= 0.5:
        return "pullback"
    if delta_60m is not None and delta_60m >= 0.5:
        return (
            "fresh_runway"
            if minutes_since_max is None or minutes_since_max <= 45
            else "warming"
        )
    if delta_60m is not None and delta_60m <= -0.5:
        return "cooling"
    return "plateau"


def entropy(values: list[float]) -> float | None:
    positive = np.asarray([max(0.0, value) for value in values], dtype=float)
    total = float(positive.sum())
    if total <= 0 or len(positive) <= 1:
        return None
    probabilities = positive / total
    probabilities = probabilities[probabilities > 0]
    return float(
        -(probabilities * np.log(probabilities)).sum()
        / math.log(len(positive))
    )


def summarize_snapshot(
    rows: list[dict[str, Any]],
    generic_index: ObservationIndex,
    korea_index: ObservationIndex,
) -> dict[str, Any]:
    first = rows[0]
    snapshot = utc(str(first["snapshot_ts_utc"]))
    city = str(first.get("city") or "")
    target_date = str(
        first.get("market_local_date")
        or first.get("event_date")
        or first.get("city_local_date_at_snapshot")
        or ""
    )
    outcomes_by_condition: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row.get("status") != "ok":
            continue
        condition = str(row.get("condition_id") or "")
        outcome = str(row.get("outcome") or "").lower()
        if condition and outcome in {"yes", "no"}:
            outcomes_by_condition[condition][outcome] = row

    brackets: list[dict[str, Any]] = []
    for condition, outcomes in outcomes_by_condition.items():
        source = outcomes.get("yes") or outcomes.get("no")
        if not source:
            continue
        quote = effective_yes_book(outcomes.get("yes"), outcomes.get("no"))
        brackets.append(
            {
                "condition_id": condition,
                "bracket": str(source.get("bracket") or ""),
                "anchor": bracket_anchor(source.get("bracket")),
                **quote,
            }
        )
    brackets.sort(
        key=lambda row: (
            row["anchor"] is None,
            row["anchor"] if row["anchor"] is not None else 0,
        )
    )

    mids = [float(row["mid"]) for row in brackets if row["mid"] is not None]
    spreads = [
        float(row["spread"])
        for row in brackets
        if row["spread"] is not None and float(row["spread"]) >= -1e-9
    ]
    asks = [
        float(row["best_ask"])
        for row in brackets
        if row["best_ask"] is not None
    ]
    bids = [
        float(row["best_bid"])
        for row in brackets
        if row["best_bid"] is not None
    ]
    favorite = max(
        (row for row in brackets if row["mid"] is not None),
        key=lambda row: float(row["mid"]),
        default=None,
    )
    observation = align_observation(
        generic_index, korea_index, city, target_date, snapshot
    )
    running_max_c = (
        finite(observation.get("running_max_c")) if observation else None
    )
    running_max_market = market_round_c(running_max_c)
    feasible = [
        row
        for row in brackets
        if running_max_market is not None
        and row["anchor"] is not None
        and float(row["anchor"]) >= running_max_market
    ]
    feasible_asks = [
        float(row["best_ask"])
        for row in feasible
        if row["best_ask"] is not None
    ]
    feasible_bids = [
        float(row["best_bid"])
        for row in feasible
        if row["best_bid"] is not None
    ]
    local = city_local_datetime(city, snapshot)
    available_at = (
        utc(str(observation["available_at_utc"])) if observation else None
    )

    quote_vectors = {
        "mid": {
            row["bracket"]: row["mid"]
            for row in brackets
            if row["mid"] is not None
        },
        "bid": {
            row["bracket"]: row["best_bid"]
            for row in brackets
            if row["best_bid"] is not None
        },
        "ask": {
            row["bracket"]: row["best_ask"]
            for row in brackets
            if row["best_ask"] is not None
        },
    }
    favorite_bid = finite(favorite.get("best_bid")) if favorite else None
    favorite_ask = finite(favorite.get("best_ask")) if favorite else None
    maker_price = None
    maker_mode = ""
    if favorite_bid is not None and favorite_ask is not None:
        inside = round(favorite_bid + 0.001, 3)
        if inside < favorite_ask - 1e-9:
            maker_price = inside
            maker_mode = "improve_bid_1tick"
        else:
            maker_price = favorite_bid
            maker_mode = "join_best_bid"

    return {
        "snapshot_ts_utc": snapshot.isoformat(),
        "event_slug": str(first.get("event_slug") or ""),
        "city": city,
        "target_date": target_date,
        "local_datetime": local.isoformat(),
        "local_hour": local.hour + local.minute / 60,
        "local_hour_band": local_hour_band(local.hour),
        "is_target_day": local.date().isoformat() == target_date,
        "bracket_count": len(brackets),
        "two_sided_quote_share": len(spreads) / len(brackets) if brackets else None,
        "yes_ask_sum": sum(asks) if len(asks) == len(brackets) and brackets else None,
        "yes_bid_sum": sum(bids) if len(bids) == len(brackets) and brackets else None,
        "static_yes_underround": (
            sum(asks) < 1.0 - 1e-9
            if len(asks) == len(brackets) and brackets
            else None
        ),
        "static_yes_underround_edge": (
            1.0 - sum(asks)
            if len(asks) == len(brackets) and brackets and sum(asks) < 1.0
            else 0.0
        ),
        "median_spread": float(np.median(spreads)) if spreads else None,
        "favorite_bracket": favorite["bracket"] if favorite else None,
        "favorite_anchor": favorite["anchor"] if favorite else None,
        "favorite_bid": favorite_bid,
        "favorite_ask": favorite_ask,
        "favorite_mid": finite(favorite.get("mid")) if favorite else None,
        "favorite_spread": finite(favorite.get("spread")) if favorite else None,
        "favorite_ask_depth_usd": (
            favorite_ask * float(favorite["ask_size"])
            if favorite
            and favorite_ask is not None
            and favorite.get("ask_size") is not None
            else None
        ),
        "normalized_mid_entropy": (
            entropy(mids) if len(mids) == len(brackets) else None
        ),
        "running_max_market_value": running_max_market,
        "feasible_strip_width": len(feasible) if running_max_market is not None else None,
        "feasible_strip_ask_sum": (
            sum(feasible_asks)
            if feasible
            and len(feasible_asks) == len(feasible)
            else None
        ),
        "feasible_strip_bid_sum": (
            sum(feasible_bids)
            if feasible
            and len(feasible_bids) == len(feasible)
            else None
        ),
        "feasible_strip_underround": (
            sum(feasible_asks) < 1.0 - 1e-9
            if feasible and len(feasible_asks) == len(feasible)
            else None
        ),
        "feasible_strip_underround_edge": (
            1.0 - sum(feasible_asks)
            if feasible
            and len(feasible_asks) == len(feasible)
            and sum(feasible_asks) < 1.0
            else 0.0
        ),
        "maker_price": maker_price,
        "maker_mode": maker_mode,
        "maker_saving_vs_ask": (
            favorite_ask - maker_price
            if favorite_ask is not None and maker_price is not None
            else None
        ),
        "observation_available": observation is not None,
        "observation_available_at_utc": (
            available_at.isoformat() if available_at else None
        ),
        "observation_epoch_key": (
            observation.get("epoch_key") if observation else None
        ),
        "observation_age_at_book_min": (
            (snapshot - available_at).total_seconds() / 60
            if available_at
            else None
        ),
        "weather_state": weather_state(observation),
        "weather_source": observation.get("source") if observation else None,
        "station": observation.get("station") if observation else None,
        "current_temp_c": (
            finite(observation.get("current_temp_c")) if observation else None
        ),
        "running_max_c": running_max_c,
        "decline_c": finite(observation.get("decline_c")) if observation else None,
        "delta_30m_c": (
            finite(observation.get("delta_30m_c")) if observation else None
        ),
        "delta_60m_c": (
            finite(observation.get("delta_60m_c")) if observation else None
        ),
        "minutes_since_running_max": (
            finite(observation.get("minutes_since_running_max"))
            if observation
            else None
        ),
        "precip_state": observation.get("precip_state") if observation else None,
        "wind_speed_kt": (
            finite(observation.get("wind_speed_kt")) if observation else None
        ),
        "dewpoint_depression_c": (
            finite(observation.get("dewpoint_depression_c"))
            if observation
            else None
        ),
        "quote_vectors_json": json.dumps(
            quote_vectors, ensure_ascii=False, sort_keys=True
        ),
    }


def normalized_tv(left: dict[str, float], right: dict[str, float]) -> float | None:
    keys = set(left) & set(right)
    if len(keys) < 3:
        return None
    left_total = sum(float(left[key]) for key in keys)
    right_total = sum(float(right[key]) for key in keys)
    if left_total <= 0 or right_total <= 0:
        return None
    return 0.5 * sum(
        abs(float(left[key]) / left_total - float(right[key]) / right_total)
        for key in keys
    )


def add_sequence_features(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["event_slug", "snapshot_ts_utc"]).reset_index(drop=True)
    columns = {
        "minutes_since_prior_book": np.nan,
        "new_observation_since_prior_book": False,
        "favorite_changed": False,
        "favorite_same_bracket_mid_change": np.nan,
        "full_ladder_probability_tv": np.nan,
        "favorite_anchor_shift": np.nan,
        "minutes_to_next_book": np.nan,
        "next_same_bracket_mid": np.nan,
        "next_same_bracket_ask": np.nan,
        "next_snapshot_ask_at_or_below_maker": False,
        "maker_markout_to_next_mid": np.nan,
    }
    for name, default in columns.items():
        frame[name] = default

    for _, indices in frame.groupby("event_slug", sort=False).groups.items():
        ordered = list(indices)
        vectors = [
            json.loads(str(frame.at[index, "quote_vectors_json"]))
            for index in ordered
        ]
        for position, index in enumerate(ordered):
            current_ts = frame.at[index, "snapshot_ts_utc"]
            if position:
                prior_index = ordered[position - 1]
                prior_ts = frame.at[prior_index, "snapshot_ts_utc"]
                gap = (current_ts - prior_ts).total_seconds() / 60
                frame.at[index, "minutes_since_prior_book"] = gap
                current_observation_ts = frame.at[
                    index, "observation_available_at_utc"
                ]
                if pd.notna(current_observation_ts):
                    frame.at[index, "new_observation_since_prior_book"] = (
                        pd.Timestamp(current_observation_ts) > prior_ts
                    )
                current_favorite = frame.at[index, "favorite_bracket"]
                prior_favorite = frame.at[prior_index, "favorite_bracket"]
                frame.at[index, "favorite_changed"] = (
                    pd.notna(current_favorite)
                    and pd.notna(prior_favorite)
                    and current_favorite != prior_favorite
                )
                if prior_favorite in vectors[position]["mid"]:
                    current_mid = float(vectors[position]["mid"][prior_favorite])
                    prior_mid = float(vectors[position - 1]["mid"].get(prior_favorite, np.nan))
                    if math.isfinite(prior_mid):
                        frame.at[index, "favorite_same_bracket_mid_change"] = (
                            current_mid - prior_mid
                        )
                frame.at[index, "full_ladder_probability_tv"] = normalized_tv(
                    vectors[position - 1]["mid"], vectors[position]["mid"]
                )
                current_anchor = finite(frame.at[index, "favorite_anchor"])
                prior_anchor = finite(frame.at[prior_index, "favorite_anchor"])
                if current_anchor is not None and prior_anchor is not None:
                    frame.at[index, "favorite_anchor_shift"] = (
                        current_anchor - prior_anchor
                    )
            if position + 1 < len(ordered):
                next_index = ordered[position + 1]
                next_ts = frame.at[next_index, "snapshot_ts_utc"]
                gap = (next_ts - current_ts).total_seconds() / 60
                frame.at[index, "minutes_to_next_book"] = gap
                favorite = frame.at[index, "favorite_bracket"]
                next_mid = finite(vectors[position + 1]["mid"].get(favorite))
                next_ask = finite(vectors[position + 1]["ask"].get(favorite))
                maker_price = finite(frame.at[index, "maker_price"])
                frame.at[index, "next_same_bracket_mid"] = next_mid
                frame.at[index, "next_same_bracket_ask"] = next_ask
                if maker_price is not None and next_ask is not None and gap <= 90:
                    frame.at[
                        index, "next_snapshot_ask_at_or_below_maker"
                    ] = next_ask <= maker_price + 1e-9
                if maker_price is not None and next_mid is not None and gap <= 90:
                    frame.at[index, "maker_markout_to_next_mid"] = (
                        next_mid - maker_price
                    )
    return frame


def summarize(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(group_columns, dropna=False)
        .agg(
            snapshots=("event_slug", "size"),
            events=("event_slug", "nunique"),
            target_dates=("target_date", "nunique"),
            observation_coverage=("observation_available", "mean"),
            median_favorite_spread=("favorite_spread", "median"),
            median_favorite_ask_depth_usd=("favorite_ask_depth_usd", "median"),
            favorite_change_rate=("favorite_changed", "mean"),
            median_full_ladder_probability_tv=(
                "full_ladder_probability_tv",
                "median",
            ),
            median_abs_favorite_same_bracket_move=(
                "favorite_same_bracket_mid_change",
                lambda values: values.abs().median(),
            ),
            new_observation_share=(
                "new_observation_since_prior_book",
                "mean",
            ),
            median_yes_ask_sum=("yes_ask_sum", "median"),
            static_yes_underround_rate=("static_yes_underround", "mean"),
            median_feasible_strip_width=("feasible_strip_width", "median"),
            median_feasible_strip_ask_sum=(
                "feasible_strip_ask_sum",
                "median",
            ),
            feasible_strip_underround_rate=(
                "feasible_strip_underround",
                "mean",
            ),
            median_maker_saving_vs_ask=("maker_saving_vs_ask", "median"),
            next_quote_cross_proxy_rate=(
                "next_snapshot_ask_at_or_below_maker",
                "mean",
            ),
            median_maker_markout_to_next_mid=(
                "maker_markout_to_next_mid",
                "median",
            ),
            quote_cross_conditional_median_markout=(
                "maker_markout_on_quote_cross",
                "median",
            ),
            quote_cross_conditional_negative_markout_rate=(
                "maker_quote_cross_negative_markout",
                "mean",
            ),
        )
        .reset_index()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orderbook-root", type=Path)
    parser.add_argument("--observation-root", type=Path)
    parser.add_argument("--korea-checkpoint-root", type=Path)
    parser.add_argument("--state-rows-input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-07-29")
    parser.add_argument("--cities", nargs="+", default=list(DEFAULT_CITIES))
    args = parser.parse_args()

    previous_coverage: dict[str, Any] = {}
    if args.state_rows_input:
        coverage_path = args.output_dir / "coverage.json"
        if coverage_path.exists():
            try:
                previous_coverage = json.loads(
                    coverage_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                previous_coverage = {}
        frame = pd.read_csv(args.state_rows_input)
        frame["snapshot_ts_utc"] = pd.to_datetime(
            frame["snapshot_ts_utc"], utc=True
        )
        paths: list[Path] = []
        raw_selected_rows = 0
    else:
        required = {
            "--orderbook-root": args.orderbook_root,
            "--observation-root": args.observation_root,
            "--korea-checkpoint-root": args.korea_checkpoint_root,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise SystemExit(
                "required without --state-rows-input: " + ", ".join(missing)
            )
        selected_city_keys = {city_key(city) for city in args.cities}
        generic_index = load_generic_observations(
            args.observation_root,
            selected_city_keys,
            args.start_date,
            args.end_date,
        )
        korea_index = load_korea_observations(
            args.korea_checkpoint_root,
            selected_city_keys,
            args.start_date,
            args.end_date,
        )

        paths = []
        for path in sorted(args.orderbook_root.glob("*/*.jsonl.gz")):
            folder_date = path.parent.name
            if args.start_date and folder_date < args.start_date:
                continue
            if args.end_date and folder_date > args.end_date:
                continue
            paths.append(path)
        if not paths:
            raise SystemExit("no orderbook files selected")

        raw_selected_rows = 0
        states: list[dict[str, Any]] = []
        for path in paths:
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in iter_jsonl(path):
                if city_key(row.get("city")) not in selected_city_keys:
                    continue
                snapshot = str(row.get("snapshot_ts_utc") or "")
                event_slug = str(row.get("event_slug") or "")
                if snapshot and event_slug:
                    grouped[(snapshot, event_slug)].append(row)
                    raw_selected_rows += 1
            states.extend(
                summarize_snapshot(rows, generic_index, korea_index)
                for rows in grouped.values()
            )

        frame = pd.DataFrame(states)
        frame["snapshot_ts_utc"] = pd.to_datetime(
            frame["snapshot_ts_utc"], utc=True
        )
        frame = frame.drop_duplicates(["event_slug", "snapshot_ts_utc"])
        frame = add_sequence_features(frame)

    frame["expected_bracket_count"] = frame.groupby("event_slug")[
        "bracket_count"
    ].transform("max")
    frame["complete_ladder_snapshot"] = frame["bracket_count"].eq(
        frame["expected_bracket_count"]
    )
    quote_cross = frame["next_snapshot_ask_at_or_below_maker"].eq(True)
    frame["maker_markout_on_quote_cross"] = frame[
        "maker_markout_to_next_mid"
    ].where(quote_cross)
    frame["maker_quote_cross_negative_markout"] = (
        frame["maker_markout_to_next_mid"].lt(0).where(quote_cross)
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        args.output_dir / "state_rows.csv.gz",
        index=False,
        compression="gzip",
    )

    primary = frame[
        frame["is_target_day"]
        & frame["local_hour"].between(6, 18, inclusive="left")
        & frame["complete_ladder_snapshot"]
    ].copy()
    observed_primary = primary[primary["observation_available"]].copy()
    summarize(primary, ["city"]).to_csv(
        args.output_dir / "city_summary.csv", index=False
    )
    summarize(primary, ["city", "local_hour_band"]).to_csv(
        args.output_dir / "city_hour_summary.csv", index=False
    )
    summarize(observed_primary, ["city", "weather_state"]).to_csv(
        args.output_dir / "city_weather_summary.csv", index=False
    )
    summarize(
        observed_primary,
        ["city", "new_observation_since_prior_book"],
    ).to_csv(args.output_dir / "city_observation_epoch_summary.csv", index=False)

    coverage = {
        "schema_version": "five_city_weather_microstructure_v1",
        "grain": "complete_event_ladder_x_archived_snapshot",
        "cities": args.cities,
        "start_snapshot_date_utc": args.start_date,
        "end_snapshot_date_utc": args.end_date,
        "orderbook_files": (
            len(paths)
            if paths
            else previous_coverage.get("orderbook_files")
        ),
        "raw_selected_token_book_rows": (
            raw_selected_rows
            or previous_coverage.get("raw_selected_token_book_rows")
        ),
        "state_rows": int(len(frame)),
        "events": int(frame["event_slug"].nunique()),
        "target_dates": int(frame["target_date"].nunique()),
        "primary_target_day_06_18_states": int(len(primary)),
        "incomplete_ladder_states_excluded_from_primary": int(
            (
                frame["is_target_day"]
                & frame["local_hour"].between(6, 18, inclusive="left")
                & ~frame["complete_ladder_snapshot"]
            ).sum()
        ),
        "primary_observation_coverage": float(
            primary["observation_available"].mean()
        ),
        "city_primary_observation_coverage": {
            city: float(group["observation_available"].mean())
            for city, group in primary.groupby("city")
        },
        "korea_amos_checkpoint_start": (
            min(path.stem for path in args.korea_checkpoint_root.glob("*.jsonl"))
            if args.korea_checkpoint_root
            and list(args.korea_checkpoint_root.glob("*.jsonl"))
            else previous_coverage.get("korea_amos_checkpoint_start")
        ),
        "actual_market_trade_prints_available": False,
        "maker_proxy_note": (
            "next_snapshot_ask_at_or_below_maker is a quote-cross proxy, "
            "not an inferred maker fill"
        ),
        "volume_note": "displayed depth is not traded volume",
        "feasible_strip_note": (
            "source-implied only; it is not an arbitrage label unless the "
            "observation source is proven equivalent to settlement"
        ),
    }
    (args.output_dir / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
