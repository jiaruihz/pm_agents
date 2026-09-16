#!/usr/bin/env python3
"""Join five-city ladder states to the earliest visible fast-source events."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta
import json
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.execution_quality.research_five_city_weather_microstructure_v1 import (  # noqa: E402
    ObservationIndex,
    city_key,
    finalize_observation_index,
    finite,
    iter_jsonl,
    load_korea_observations,
    utc,
    weather_state,
)


SIMPLE_FAST_SOURCES = {
    "Tokyo": {"jma_amedas"},
    "Helsinki": {"fmi"},
    "Amsterdam": {"knmi"},
}


def load_simple_fast_observations(
    paths: list[Path],
    *,
    start_date: str | None,
    end_date: str | None,
) -> ObservationIndex:
    raw_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_events: set[tuple[str, str, str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        for raw in iter_jsonl(path):
            city = str(raw.get("city") or "")
            target_date = str(raw.get("target_date") or "")
            source = str(raw.get("source") or "")
            if city not in SIMPLE_FAST_SOURCES:
                continue
            if source not in SIMPLE_FAST_SOURCES[city]:
                continue
            if start_date and target_date < start_date:
                continue
            if end_date and target_date > end_date:
                continue
            if raw.get("source_status") not in {None, "ok"}:
                continue
            available = (
                raw.get("source_first_seen_at_utc")
                or raw.get("knmi_first_seen_at_utc")
                or raw.get("fetched_at_utc")
            )
            observed = raw.get("observation_time_utc")
            temp_c = finite(raw.get("temp_c"))
            if not available or not observed or temp_c is None:
                continue
            event_key = (city, target_date, source, str(observed))
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            raw_groups[(city_key(city), target_date)].append(
                {
                    "available_at": utc(str(available)),
                    "observed_at": utc(str(observed)),
                    "city": city,
                    "target_date": target_date,
                    "source": source,
                    "station": str(raw.get("station") or ""),
                    "temp_c": temp_c,
                    "peak_temp_c": (
                        finite(raw.get("max_temp_c_past_10m"))
                        if finite(raw.get("max_temp_c_past_10m")) is not None
                        else temp_c
                    ),
                    "wind_speed_kt": finite(raw.get("wind_speed_kt")),
                    "epoch_key": "|".join(event_key),
                }
            )

    normalized: dict[
        tuple[str, str], list[tuple[Any, dict[str, Any]]]
    ] = defaultdict(list)
    for key, values in raw_groups.items():
        values.sort(key=lambda row: row["available_at"])
        visible: list[dict[str, Any]] = []
        max_value = -math.inf
        max_observed_at = None
        for value in values:
            visible.append(value)
            if value["peak_temp_c"] > max_value:
                max_value = value["peak_temp_c"]
                max_observed_at = value["observed_at"]

            def delta(minutes: int) -> float | None:
                cutoff = value["observed_at"] - timedelta(minutes=minutes)
                eligible = [
                    row
                    for row in visible
                    if row["observed_at"] <= cutoff
                ]
                if not eligible:
                    return None
                anchor = max(eligible, key=lambda row: row["observed_at"])
                return value["temp_c"] - anchor["temp_c"]

            row = {
                "available_at_utc": value["available_at"].isoformat(),
                "epoch_key": value["epoch_key"],
                "source": value["source"],
                "station": value["station"],
                "current_temp_c": value["temp_c"],
                "running_max_c": max_value,
                "decline_c": max_value - value["temp_c"],
                "delta_30m_c": delta(30),
                "delta_60m_c": delta(60),
                "minutes_since_running_max": (
                    (value["observed_at"] - max_observed_at).total_seconds() / 60
                    if max_observed_at
                    else None
                ),
                "precip_state": "",
                "wind_speed_kt": value["wind_speed_kt"],
                "dewpoint_depression_c": None,
            }
            normalized[key].append((value["available_at"], row))
    return finalize_observation_index(normalized)


def align(
    index: ObservationIndex,
    city: str,
    target_date: str,
    snapshot: pd.Timestamp,
) -> dict[str, Any] | None:
    values = index.get((city_key(city), target_date))
    if not values:
        return None
    timestamps, rows = values
    position = bisect_right(timestamps, snapshot.to_pydatetime()) - 1
    return rows[position] if position >= 0 else None


def summary(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(groups, dropna=False)
        .agg(
            snapshots=("event_slug", "size"),
            events=("event_slug", "nunique"),
            target_dates=("target_date", "nunique"),
            first_book_after_fast_epoch_rate=("first_book_after_fast_epoch", "mean"),
            median_fast_source_age_min=("fast_source_age_at_book_min", "median"),
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
    parser.add_argument("--state-rows", type=Path, required=True)
    parser.add_argument(
        "--high-frequency-observations", type=Path, required=True
    )
    parser.add_argument("--knmi-observations", type=Path, required=True)
    parser.add_argument("--korea-checkpoint-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-07-29")
    args = parser.parse_args()

    simple_index = load_simple_fast_observations(
        [args.high_frequency_observations, args.knmi_observations],
        start_date=args.start_date,
        end_date=args.end_date,
    )
    korea_index = load_korea_observations(
        args.korea_checkpoint_root,
        {city_key("Seoul"), city_key("Busan")},
        args.start_date,
        args.end_date,
    )
    frame = pd.read_csv(args.state_rows)
    frame["snapshot_ts_utc"] = pd.to_datetime(
        frame["snapshot_ts_utc"], utc=True
    )
    rows: list[dict[str, Any] | None] = []
    for row in frame.itertuples(index=False):
        index = korea_index if row.city in {"Seoul", "Busan"} else simple_index
        rows.append(
            align(index, row.city, row.target_date, row.snapshot_ts_utc)
        )

    frame["fast_source_available"] = [row is not None for row in rows]
    frame["fast_source"] = [
        row.get("source") if row else None for row in rows
    ]
    frame["fast_station"] = [
        row.get("station") if row else None for row in rows
    ]
    frame["fast_epoch_key"] = [
        row.get("epoch_key") if row else None for row in rows
    ]
    frame["fast_available_at_utc"] = [
        row.get("available_at_utc") if row else None for row in rows
    ]
    frame["fast_weather_state"] = [
        weather_state(row) if row else "missing" for row in rows
    ]
    for source_name, output_name in [
        ("current_temp_c", "fast_current_temp_c"),
        ("running_max_c", "fast_running_max_c"),
        ("decline_c", "fast_decline_c"),
        ("delta_30m_c", "fast_delta_30m_c"),
        ("delta_60m_c", "fast_delta_60m_c"),
        ("minutes_since_running_max", "fast_minutes_since_running_max"),
    ]:
        frame[output_name] = [
            finite(row.get(source_name)) if row else None for row in rows
        ]
    available_ts = pd.to_datetime(frame["fast_available_at_utc"], utc=True)
    frame["fast_source_age_at_book_min"] = (
        frame["snapshot_ts_utc"] - available_ts
    ).dt.total_seconds() / 60
    frame["fast_source_age_band"] = pd.cut(
        frame["fast_source_age_at_book_min"],
        bins=[-1e-9, 5, 15, 30, 60, math.inf],
        labels=["0-5m", "5-15m", "15-30m", "30-60m", "60m+"],
        include_lowest=True,
        right=True,
    )
    frame["prior_fast_epoch_key"] = frame.groupby("event_slug")[
        "fast_epoch_key"
    ].shift()
    frame["has_prior_book"] = frame.groupby("event_slug").cumcount().gt(0)
    frame["first_book_after_fast_epoch"] = (
        frame["has_prior_book"]
        & frame["fast_epoch_key"].notna()
        & frame["fast_epoch_key"].ne(frame["prior_fast_epoch_key"])
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        args.output_dir / "fast_source_state_rows.csv.gz",
        index=False,
        compression="gzip",
    )
    target_day = (
        frame["is_target_day"]
        if pd.api.types.is_bool_dtype(frame["is_target_day"])
        else frame["is_target_day"].astype(str).str.lower().eq("true")
    )
    primary = frame[
        target_day
        & frame["local_hour"].between(6, 18, inclusive="left")
        & frame["fast_source_available"]
        & frame["complete_ladder_snapshot"]
    ].copy()
    summary(primary, ["city"]).to_csv(
        args.output_dir / "fast_source_city_summary.csv", index=False
    )
    summary(primary, ["city", "fast_weather_state"]).to_csv(
        args.output_dir / "fast_source_city_weather_summary.csv", index=False
    )
    summary(primary, ["city", "first_book_after_fast_epoch"]).to_csv(
        args.output_dir / "fast_source_epoch_repricing_summary.csv", index=False
    )
    summary(primary, ["city", "fast_source_age_band"]).to_csv(
        args.output_dir / "fast_source_age_band_summary.csv", index=False
    )
    coverage = {
        "schema_version": "five_city_fast_source_repricing_v1",
        "state_rows": int(len(frame)),
        "primary_fast_source_states": int(len(primary)),
        "city_fast_source_states": {
            city: int(len(group)) for city, group in primary.groupby("city")
        },
        "city_fast_source_target_dates": {
            city: int(group["target_date"].nunique())
            for city, group in primary.groupby("city")
        },
        "note": (
            "first_book_after_fast_epoch is the first archived full-ladder "
            "snapshot after a source epoch; archive cadence may be much wider "
            "than the source-to-market reaction"
        ),
        "feasible_strip_note": (
            "source-implied only; AMOS/JMA/FMI/KNMI values are not assumed "
            "identical to the market settlement source"
        ),
    }
    (args.output_dir / "fast_source_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
