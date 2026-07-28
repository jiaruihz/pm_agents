#!/usr/bin/env python3
"""Backfill conservative historical model runs for the frozen D1 holdout."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.historical_forecast_runs import (
    DEFAULT_GLOBAL_SINGLE_RUN_MODELS,
    ModelRunUnavailable,
    conservative_available_run,
    daily_max_rows,
    fetch_single_run_batch,
    parse_utc,
)
from weather_data_feed_service.legacy_weather_predict.city_pools import (
    FULL_CITY_CONFIGS,
)


DEFAULT_BASKETS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_single_runs_backfill_v1"
)
DEFAULT_CACHE = (
    ROOT / "runtime/analysis_snapshots/d1_single_runs_backfill_v1/raw"
)
HOLDOUT_START = "2026-06-17"
HOLDOUT_END = "2026-07-23"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--holdout-start", default=HOLDOUT_START)
    parser.add_argument("--holdout-end", default=HOLDOUT_END)
    parser.add_argument("--batch-size", type=int, default=20)
    return parser.parse_args()


def build_jobs(
    baskets: pd.DataFrame, holdout_start: str, holdout_end: str
) -> pd.DataFrame:
    rows = baskets[
        baskets["target_date"].between(holdout_start, holdout_end)
    ][["snapshot_key", "city", "target_date", "decision_ts_utc"]].copy()
    rows = rows.drop_duplicates(
        ["city", "target_date", "decision_ts_utc"]
    ).reset_index(drop=True)
    rows["requested_run"] = rows["decision_ts_utc"].map(
        conservative_available_run
    )
    rows["latitude"] = rows["city"].map(
        lambda city: (FULL_CITY_CONFIGS.get(str(city)) or {}).get("lat")
    )
    rows["longitude"] = rows["city"].map(
        lambda city: (FULL_CITY_CONFIGS.get(str(city)) or {}).get("lon")
    )
    missing = rows[rows["latitude"].isna() | rows["longitude"].isna()]
    if not missing.empty:
        raise RuntimeError(
            "missing city coordinates: "
            + ",".join(sorted(missing["city"].astype(str).unique()))
        )
    return rows


def run_backfill(args: argparse.Namespace) -> dict[str, Any]:
    baskets = pd.read_csv(args.baskets)
    jobs = build_jobs(baskets, args.holdout_start, args.holdout_end)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    forecast_records: list[dict[str, Any]] = []
    request_records: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    grouped = list(jobs.groupby("requested_run", sort=True))
    total_batches = sum(
        (len(group) + args.batch_size - 1) // args.batch_size
        for _, group in grouped
    )
    batch_number = 0
    for run, group in grouped:
        records = group.to_dict("records")
        for start in range(0, len(records), args.batch_size):
            batch_number += 1
            chunk = records[start : start + args.batch_size]
            locations = [
                {
                    "city": row["city"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                }
                for row in chunk
            ]
            requested_run = str(run)
            actual_run = requested_run
            for fallback_cycles in range(5):
                try:
                    responses, metadata = fetch_single_run_batch(
                        locations,
                        run=actual_run,
                        cache_dir=args.cache_dir,
                    )
                    break
                except ModelRunUnavailable:
                    actual_run = (
                        parse_utc(f"{actual_run}:00Z") - timedelta(hours=6)
                    ).strftime("%Y-%m-%dT%H:00")
            else:
                raise RuntimeError(
                    f"no common model run within 24h before {requested_run}"
                )
            if not metadata["cache_hit"]:
                time.sleep(0.5)
            counters["requests"] += 1
            counters["cache_hits"] += int(metadata["cache_hit"])
            for job, payload in zip(chunk, responses):
                normalized = daily_max_rows(
                    payload,
                    city=str(job["city"]),
                    target_date=str(job["target_date"]),
                    run=actual_run,
                    decision_time_utc=str(job["decision_ts_utc"]),
                )
                for row in normalized:
                    row["snapshot_key"] = job["snapshot_key"]
                    row["request_key"] = metadata["request_key"]
                    row["raw_hash"] = metadata["raw_hash"]
                forecast_records.extend(normalized)
                counters["jobs"] += 1
                counters["forecast_rows"] += len(normalized)
                counters["complete_jobs"] += int(
                    len(normalized)
                    == len(DEFAULT_GLOBAL_SINGLE_RUN_MODELS)
                )
            request_records.append(
                {
                    "requested_run": run,
                    "actual_run": actual_run,
                    "fallback_cycles": fallback_cycles,
                    "request_key": metadata["request_key"],
                    "raw_hash": metadata["raw_hash"],
                    "cache_hit": metadata["cache_hit"],
                    "location_count": len(chunk),
                    "models": ",".join(DEFAULT_GLOBAL_SINGLE_RUN_MODELS),
                }
            )
            if batch_number % 10 == 0 or batch_number == total_batches:
                print(
                    f"backfill {batch_number}/{total_batches}: "
                    f"jobs={counters['jobs']} rows={counters['forecast_rows']} "
                    f"cache_hits={counters['cache_hits']}",
                    flush=True,
                )

    forecasts = pd.DataFrame(forecast_records).sort_values(
        ["target_date", "city", "model_key"]
    )
    requests = pd.DataFrame(request_records)
    forecasts.to_csv(args.output_dir / "forecast_rows.csv", index=False)
    requests.to_csv(args.output_dir / "request_manifest.csv", index=False)
    expected_rows = len(jobs) * len(DEFAULT_GLOBAL_SINGLE_RUN_MODELS)
    summary = {
        "contract": {
            "holdout_start": args.holdout_start,
            "holdout_end": args.holdout_end,
            "run_rule": "floor_6h(decision_time_utc - 12h)",
            "availability_interpretation": (
                "conservative reconstruction; initialization is not "
                "public first-seen"
            ),
            "models": list(DEFAULT_GLOBAL_SINGLE_RUN_MODELS),
            "temperature_unit": "fahrenheit",
        },
        "input_baskets": int(
            baskets["target_date"]
            .between(args.holdout_start, args.holdout_end)
            .sum()
        ),
        "unique_jobs": len(jobs),
        "target_dates": int(jobs["target_date"].nunique()),
        "cities": int(jobs["city"].nunique()),
        "run_timestamps": int(jobs["requested_run"].nunique()),
        "requests": int(counters["requests"]),
        "cache_hits": int(counters["cache_hits"]),
        "complete_jobs": int(counters["complete_jobs"]),
        "forecast_rows": len(forecasts),
        "expected_forecast_rows": expected_rows,
        "coverage": len(forecasts) / expected_rows if expected_rows else 0.0,
        "model_rows": {
            str(key): int(value)
            for key, value in forecasts.groupby("model_key").size().items()
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    run_backfill(parse_args())


if __name__ == "__main__":
    main()
