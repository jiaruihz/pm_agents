#!/usr/bin/env python3
"""Infer yourthos RKSI entry triggers and automation from wallet transactions.

The script collapses public activity rows to unique Polygon transactions, then
joins transactions to collector-exact Seoul AMOS and routine METAR first-seen
events.  It is descriptive research and does not infer unseen private inputs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import (  # noqa: E402
    research_active_realtime_source_alignment_v1 as alignment,
)


def parse_dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * q)]


def collapse_transactions(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        grouped[str(row["transaction_hash"])].append(row)
    output: list[dict[str, Any]] = []
    for tx_hash, rows in grouped.items():
        rows.sort(key=lambda row: row["timestamp_utc"])
        output.append(
            {
                "transaction_hash": tx_hash,
                "timestamp": parse_dt(rows[0]["timestamp_utc"]),
                "target_date": rows[0]["target_date"],
                "event_slug": rows[0]["event_slug"],
                "activity_rows": len(rows),
                "cash": sum(float(row["cash"]) for row in rows),
                "shares": sum(float(row["shares"]) for row in rows),
                "sides": sorted({str(row["side"]) for row in rows}),
                "outcomes": sorted({str(row["outcome"]) for row in rows}),
                "brackets": sorted(
                    {str(row["bracket"]) for row in rows if row.get("bracket")}
                ),
                "expressions": sorted(
                    {str(row["expression_vs_running_max"]) for row in rows}
                ),
                "prices": [float(row["price"]) for row in rows],
            }
        )
    return sorted(output, key=lambda row: (row["target_date"], row["timestamp"]))


def load_all_runway_fast(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Load each AMOS fetch as one snapshot using the hottest runway sensor."""
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    root = alignment.RUNTIME / "output/high_frequency_observations"
    for path in alignment.partition_paths(
        root, "high_frequency_observations.jsonl", start_date, end_date
    ):
        for raw in alignment.iter_jsonl(path):
            if raw.get("source") != "amos_runway" or raw.get("city") != "Seoul":
                continue
            target_date = str(raw.get("target_date") or "")
            detect = alignment.parse_dt(
                raw.get("local_detect_ts_utc") or raw.get("fetched_at_utc")
            )
            obs = alignment.parse_dt(raw.get("observation_time_utc"))
            temp = alignment.number(raw.get("temp_c"))
            if (
                not detect
                or not obs
                or temp is None
                or not (start_date <= target_date <= end_date)
            ):
                continue
            key = (target_date, detect.isoformat())
            current = snapshots.get(key)
            if current is None or temp > current["temp_c"]:
                snapshots[key] = {
                    "city": "Seoul",
                    "source": "amos_runway_all_runway_max",
                    "target_date": target_date,
                    "detect_ts": detect,
                    "obs_ts": obs,
                    "temp_c": temp,
                }
    return sorted(snapshots.values(), key=lambda row: row["detect_ts"])


def routine_state(
    awc_rows: list[dict[str, Any]],
    target_date: str,
    decision: datetime,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in awc_rows
        if row["target_date"] == target_date and row["detect_ts"] <= decision
    ]
    return candidates[-1] if candidates else None


def fast_state(
    fast_rows: list[dict[str, Any]],
    target_date: str,
    decision: datetime,
    prior_max: int,
) -> dict[str, Any] | None:
    rows = [
        row
        for row in fast_rows
        if row["target_date"] == target_date and row["detect_ts"] <= decision
    ]
    if not rows:
        return None
    threshold = prior_max + 0.5
    episode: list[dict[str, Any]] = []
    for row in rows:
        if row["temp_c"] >= threshold - 1e-12:
            episode.append(row)
        else:
            episode = []
    latest = rows[-1]
    distinct = len({row["obs_ts"] for row in episode})
    trigger = None
    for index, row in enumerate(episode):
        seen = episode[: index + 1]
        if (
            len({item["obs_ts"] for item in seen}) >= 2
            and row["temp_c"] >= prior_max + 0.7 - 1e-12
        ):
            trigger = row
            break
    return {
        "latest_fast_temp_c": latest["temp_c"],
        "latest_fast_detect_ts_utc": latest["detect_ts"].isoformat(),
        "latest_fast_age_sec": round((decision - latest["detect_ts"]).total_seconds(), 3),
        "source_above_half": int(latest["temp_c"] >= threshold - 1e-12),
        "source_strong": int(latest["temp_c"] >= prior_max + 0.7 - 1e-12),
        "continuous_above_distinct_obs": distinct,
        "source_persistent": int(trigger is not None),
        "persistent_trigger_detect_ts_utc": (
            trigger["detect_ts"].isoformat() if trigger else None
        ),
        "seconds_after_persistent_trigger": (
            round((decision - trigger["detect_ts"]).total_seconds(), 3)
            if trigger
            else None
        ),
    }


def burst_stats(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    intervals: list[float] = []
    max_60s = 0
    max_300s = 0
    for target_date in sorted({row["target_date"] for row in transactions}):
        sample = [row for row in transactions if row["target_date"] == target_date]
        times = sorted(row["timestamp"] for row in sample)
        intervals.extend(
            (later - earlier).total_seconds()
            for earlier, later in zip(times, times[1:])
        )
        for index, start in enumerate(times):
            max_60s = max(
                max_60s,
                sum(0 <= (item - start).total_seconds() <= 60 for item in times[index:]),
            )
            max_300s = max(
                max_300s,
                sum(0 <= (item - start).total_seconds() <= 300 for item in times[index:]),
            )
    return {
        "within_event_intervals": len(intervals),
        "median_intertransaction_sec": (
            round(statistics.median(intervals), 3) if intervals else None
        ),
        "p10_intertransaction_sec": percentile(intervals, 0.10),
        "p90_intertransaction_sec": percentile(intervals, 0.90),
        "interval_share_le_2s": (
            round(sum(value <= 2 for value in intervals) / len(intervals), 6)
            if intervals
            else None
        ),
        "interval_share_le_10s": (
            round(sum(value <= 10 for value in intervals) / len(intervals), 6)
            if intervals
            else None
        ),
        "interval_share_le_60s": (
            round(sum(value <= 60 for value in intervals) / len(intervals), 6)
            if intervals
            else None
        ),
        "max_unique_transactions_in_60s": max_60s,
        "max_unique_transactions_in_300s": max_300s,
    }


def summarize_alignment(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current_no = [
        row
        for row in rows
        if "BUY" in row["sides"] and "current_no" in row["expressions"]
    ]
    first_by_date: list[dict[str, Any]] = []
    for target_date in sorted({row["target_date"] for row in current_no}):
        first_by_date.append(
            min(
                (row for row in current_no if row["target_date"] == target_date),
                key=lambda row: row["timestamp_utc"],
            )
        )

    def stats(sample: list[dict[str, Any]]) -> dict[str, Any]:
        with_fast = [row for row in sample if row.get("fast_available")]
        persistent = [row for row in with_fast if row.get("source_persistent")]
        lags = [
            float(row["seconds_after_persistent_trigger"])
            for row in persistent
            if row.get("seconds_after_persistent_trigger") is not None
        ]
        return {
            "rows": len(sample),
            "dates": len({row["target_date"] for row in sample}),
            "cash": round(sum(float(row["cash"]) for row in sample), 6),
            "fast_available_rows": len(with_fast),
            "source_above_half_rows": sum(
                bool(row.get("source_above_half")) for row in with_fast
            ),
            "source_persistent_rows": len(persistent),
            "source_persistent_cash_share_of_fast_available": (
                round(
                    sum(float(row["cash"]) for row in persistent)
                    / sum(float(row["cash"]) for row in with_fast),
                    6,
                )
                if with_fast and sum(float(row["cash"]) for row in with_fast)
                else None
            ),
            "median_seconds_after_persistent_trigger": (
                round(statistics.median(lags), 3) if lags else None
            ),
            "p10_seconds_after_persistent_trigger": percentile(lags, 0.10),
            "p90_seconds_after_persistent_trigger": percentile(lags, 0.90),
        }

    return {
        "all_current_no_buy_transactions": stats(current_no),
        "first_current_no_buy_transaction_per_date": stats(first_by_date),
        "first_current_no_fresh_all_runway_snapshot": {
            "rows": len(first_by_date),
            "age_le_5s": sum(
                row.get("all_runway_latest_age_sec") is not None
                and row["all_runway_latest_age_sec"] <= 5
                for row in first_by_date
            ),
            "age_le_30s": sum(
                row.get("all_runway_latest_age_sec") is not None
                and row["all_runway_latest_age_sec"] <= 30
                for row in first_by_date
            ),
            "median_age_sec": (
                round(
                    statistics.median(
                        row["all_runway_latest_age_sec"]
                        for row in first_by_date
                        if row.get("all_runway_latest_age_sec") is not None
                    ),
                    3,
                )
                if any(
                    row.get("all_runway_latest_age_sec") is not None
                    for row in first_by_date
                )
                else None
            ),
        },
        "first_current_no_rows": first_by_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", default="2026-07-08")
    parser.add_argument("--end-date", default="2026-07-28")
    args = parser.parse_args()

    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    transactions = collapse_transactions(replay["trades"])
    fast = [
        row
        for row in alignment.load_fast(args.start_date, args.end_date)
        if row["city"] == "Seoul"
    ]
    all_runway_fast = load_all_runway_fast(args.start_date, args.end_date)
    awc = alignment.load_awc(args.start_date, args.end_date).get("Seoul", [])

    aligned: list[dict[str, Any]] = []
    for tx in transactions:
        decision = tx["timestamp"]
        target_date = tx["target_date"]
        routine = routine_state(awc, target_date, decision)
        source = (
            fast_state(fast, target_date, decision, int(routine["running_max_c"]))
            if routine
            else None
        )
        all_runway_source = (
            fast_state(
                all_runway_fast,
                target_date,
                decision,
                int(routine["running_max_c"]),
            )
            if routine
            else None
        )
        aligned.append(
            {
                **{key: value for key, value in tx.items() if key != "timestamp"},
                "timestamp_utc": decision.isoformat(),
                "routine_available": routine is not None,
                "routine_running_max_c": (
                    int(routine["running_max_c"]) if routine else None
                ),
                "routine_latest_detect_ts_utc": (
                    routine["detect_ts"].isoformat() if routine else None
                ),
                "fast_available": source is not None,
                **(source or {}),
                "all_runway_fast_available": all_runway_source is not None,
                "all_runway_latest_temp_c": (
                    all_runway_source.get("latest_fast_temp_c")
                    if all_runway_source
                    else None
                ),
                "all_runway_latest_age_sec": (
                    all_runway_source.get("latest_fast_age_sec")
                    if all_runway_source
                    else None
                ),
                "all_runway_source_above_half": (
                    all_runway_source.get("source_above_half")
                    if all_runway_source
                    else None
                ),
                "all_runway_source_persistent": (
                    all_runway_source.get("source_persistent")
                    if all_runway_source
                    else None
                ),
            }
        )

    payload = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "source_replay_snapshot_utc": replay.get("snapshot_utc"),
        "coverage": {
            "trade_activity_rows": len(replay["trades"]),
            "unique_transactions": len(transactions),
            "target_dates": len({row["target_date"] for row in transactions}),
            "amos_rows": len(fast),
            "amos_dates": len({row["target_date"] for row in fast}),
            "amos_all_runway_snapshots": len(all_runway_fast),
            "routine_metar_rows": len(awc),
            "routine_metar_dates": len({row["target_date"] for row in awc}),
        },
        "automation": burst_stats(transactions),
        "source_alignment": summarize_alignment(aligned),
        "transactions": aligned,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "coverage": payload["coverage"],
                "automation": payload["automation"],
                "source_alignment": {
                    key: value
                    for key, value in payload["source_alignment"].items()
                    if key != "first_current_no_rows"
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
