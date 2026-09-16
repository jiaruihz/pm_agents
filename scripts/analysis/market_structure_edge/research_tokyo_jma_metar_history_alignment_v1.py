#!/usr/bin/env python3
"""Compare historical Haneda JMA 10-minute paths with RJTT METAR.

Historical archives carry observation clocks, not exact publication/first-seen
clocks.  They are therefore suitable for learning source-to-METAR physical
basis and future METAR labels, but never for measuring live detection latency.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.build_three_city_official_path_history_v1 import (  # noqa: E402
    fetch_jma,
    write_csv,
)


UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")
DEFAULT_IEM = (
    ROOT
    / "runtime/weather_edge_v1/market_data/cache/iem"
    / "iem_v2_RJTT_2024-04-30_2026-06-10.csv"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_metar_history_alignment_v1"
)
HORIZONS_MIN = (30, 60, 120)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def round_native_c(value: float) -> int:
    """METAR temperatures are integer Celsius; use explicit half-up rounding."""

    return math.floor(value + 0.5)


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    output: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
        chunk_end = min(end, next_month - timedelta(days=1))
        output.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return output


def fetch_jma_chunk(chunk: tuple[date, date]) -> list[dict[str, Any]]:
    start, end = chunk
    with httpx.Client(
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": "pm-agents-research/1"},
    ) as client:
        return fetch_jma(start, end, client)


def fetch_jma_history(
    start: date,
    end: date,
    *,
    workers: int,
) -> list[dict[str, Any]]:
    chunks = month_ranges(start, end)
    output: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_jma_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                output.extend(future.result())
            except Exception as exc:
                raise RuntimeError(
                    f"JMA history fetch failed for {chunk[0]}..{chunk[1]}"
                ) from exc
    return sorted(output, key=lambda row: row["observation_time_utc"])


def load_jma(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_metar(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("station") != "RJTT" or raw.get("tmpc") in {"", "M", None}:
                continue
            try:
                observed = parse_utc(str(raw["valid"]))
                temp_c = float(raw["tmpc"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(
                {
                    "observation_time_utc": observed,
                    "temp_c": temp_c,
                    "routine_clock": observed.minute in {0, 30},
                    "local_date": observed.astimezone(TOKYO).date().isoformat(),
                }
            )
    unique = {
        (row["observation_time_utc"], row["temp_c"]): row
        for row in rows
    }
    return sorted(unique.values(), key=lambda row: row["observation_time_utc"])


def prior_index(times: list[datetime], decision: datetime) -> int | None:
    # Historical archives do not preserve publication order for equal
    # observation timestamps.  Treat a same-clock METAR as unavailable.
    idx = bisect_left(times, decision) - 1
    return idx if idx >= 0 else None


def future_rows(
    rows: list[dict[str, Any]],
    times: list[datetime],
    decision: datetime,
    *,
    horizon_min: int,
    local_date: str,
) -> list[dict[str, Any]]:
    start = bisect_right(times, decision)
    end = bisect_right(times, decision + timedelta(minutes=horizon_min))
    return [row for row in rows[start:end] if row["local_date"] == local_date]


def build_alignment_rows(
    jma_rows: list[dict[str, Any]],
    metar_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metar_times = [row["observation_time_utc"] for row in metar_rows]
    routine_rows = [row for row in metar_rows if row["routine_clock"]]
    routine_times = [row["observation_time_utc"] for row in routine_rows]
    jma_by_day: dict[str, list[tuple[datetime, float]]] = {}
    comparison_rows: list[dict[str, Any]] = []
    horizon_rows: list[dict[str, Any]] = []

    for raw in jma_rows:
        observed = parse_utc(str(raw["observation_time_utc"]))
        local_date = observed.astimezone(TOKYO).date().isoformat()
        temp_c = float(raw["temp_c"])
        day_history = jma_by_day.setdefault(local_date, [])
        prior_jma_temp = day_history[-1][1] if day_history else None
        prior_jma_max = max((value for _ts, value in day_history), default=None)
        day_history.append((observed, temp_c))

        metar_idx = prior_index(metar_times, observed)
        if metar_idx is None:
            continue
        prior_metar = metar_rows[metar_idx]
        if prior_metar["local_date"] != local_date:
            continue
        metar_age_min = (
            observed - prior_metar["observation_time_utc"]
        ).total_seconds() / 60.0
        if metar_age_min > 90:
            continue

        rounded_jma = round_native_c(temp_c)
        innovation_c = rounded_jma - prior_metar["temp_c"]
        is_jma_update = (
            prior_jma_temp is not None and round_native_c(prior_jma_temp) != rounded_jma
        )
        is_new_jma_high = prior_jma_max is None or temp_c > prior_jma_max
        local_hour = (
            observed.astimezone(TOKYO).hour
            + observed.astimezone(TOKYO).minute / 60.0
        )

        next_routine_idx = bisect_right(routine_times, observed)
        next_routine = (
            routine_rows[next_routine_idx]
            if next_routine_idx < len(routine_rows)
            else None
        )
        if next_routine and next_routine["local_date"] != local_date:
            next_routine = None
        comparison_rows.append(
            {
                "local_date": local_date,
                "jma_observation_time_utc": observed.isoformat(),
                "jma_temp_c": temp_c,
                "jma_rounded_c": rounded_jma,
                "prior_metar_time_utc": prior_metar[
                    "observation_time_utc"
                ].isoformat(),
                "prior_metar_temp_c": prior_metar["temp_c"],
                "prior_metar_age_min": round(metar_age_min, 3),
                "jma_minus_prior_metar_c": innovation_c,
                "jma_temp_update": int(is_jma_update),
                "jma_strict_new_high": int(is_new_jma_high),
                "local_hour": local_hour,
                "next_routine_metar_time_utc": (
                    next_routine["observation_time_utc"].isoformat()
                    if next_routine
                    else ""
                ),
                "next_routine_metar_temp_c": (
                    next_routine["temp_c"] if next_routine else ""
                ),
                "next_routine_confirms_jma_lattice": (
                    int(next_routine["temp_c"] >= rounded_jma)
                    if next_routine
                    else ""
                ),
            }
        )

        remaining_day_metar = future_rows(
            metar_rows,
            metar_times,
            observed,
            horizon_min=24 * 60,
            local_date=local_date,
        )
        remaining_day_max = (
            max(row["temp_c"] for row in remaining_day_metar)
            if remaining_day_metar
            else None
        )
        for horizon in HORIZONS_MIN:
            future = future_rows(
                metar_rows,
                metar_times,
                observed,
                horizon_min=horizon,
                local_date=local_date,
            )
            if not future:
                continue
            future_max = max(row["temp_c"] for row in future)
            horizon_rows.append(
                {
                    "local_date": local_date,
                    "jma_observation_time_utc": observed.isoformat(),
                    "horizon_min": horizon,
                    "jma_temp_c": temp_c,
                    "jma_rounded_c": rounded_jma,
                    "prior_metar_temp_c": prior_metar["temp_c"],
                    "jma_minus_prior_metar_c": innovation_c,
                    "jma_temp_update": int(is_jma_update),
                    "jma_strict_new_high": int(is_new_jma_high),
                    "daytime_06_18": int(6 <= local_hour < 18),
                    "future_metar_count": len(future),
                    "future_metar_max_c": future_max,
                    "future_metar_upgrade": int(
                        future_max > prior_metar["temp_c"]
                    ),
                    "future_metar_confirms_jma_lattice": int(
                        future_max >= rounded_jma
                    ),
                    "same_day_future_metar_count": len(remaining_day_metar),
                    "same_day_future_metar_max_c": (
                        remaining_day_max if remaining_day_max is not None else ""
                    ),
                    "same_day_metar_confirms_jma_lattice": (
                        int(remaining_day_max >= rounded_jma)
                        if remaining_day_max is not None
                        else ""
                    ),
                }
            )
    return comparison_rows, horizon_rows


def ratio(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = [int(row[key]) for row in rows if row.get(key) not in {"", None}]
    return sum(values) / len(values) if values else None


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return ordered[lo]
    weight = index - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def first_by(
    rows: Iterable[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(row)
    return output


def summarize(
    jma_rows: list[dict[str, Any]],
    metar_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    horizon_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    routine_pairs = [
        row
        for row in comparison_rows
        if row["next_routine_metar_temp_c"] != ""
        and (
            parse_utc(str(row["next_routine_metar_time_utc"]))
            - parse_utc(str(row["jma_observation_time_utc"]))
        ).total_seconds()
        <= 60 * 60
    ]
    routine_deltas = [
        float(row["next_routine_metar_temp_c"]) - float(row["jma_temp_c"])
        for row in routine_pairs
    ]
    horizon_summary: list[dict[str, Any]] = []
    for horizon in HORIZONS_MIN:
        all_rows = [
            row
            for row in horizon_rows
            if row["horizon_min"] == horizon and row["daytime_06_18"] == 1
        ]
        lead_rows = [
            row
            for row in all_rows
            if row["jma_temp_update"] == 1
            and float(row["jma_minus_prior_metar_c"]) >= 1
        ]
        new_high_leads = [
            row for row in lead_rows if row["jma_strict_new_high"] == 1
        ]
        first_day_leads = first_by(lead_rows, ("local_date",))
        first_lattice_leads = first_by(
            lead_rows, ("local_date", "jma_rounded_c")
        )
        horizon_summary.append(
            {
                "horizon_min": horizon,
                "all_events": len(all_rows),
                "all_dates": len({row["local_date"] for row in all_rows}),
                "base_metar_upgrade_rate": ratio(
                    all_rows, "future_metar_upgrade"
                ),
                "jma_lead_events": len(lead_rows),
                "jma_lead_dates": len({row["local_date"] for row in lead_rows}),
                "jma_lead_metar_upgrade_rate": ratio(
                    lead_rows, "future_metar_upgrade"
                ),
                "jma_lead_confirmation_rate": ratio(
                    lead_rows, "future_metar_confirms_jma_lattice"
                ),
                "new_high_lead_events": len(new_high_leads),
                "new_high_lead_confirmation_rate": ratio(
                    new_high_leads, "future_metar_confirms_jma_lattice"
                ),
                "same_day_confirmation_rate": ratio(
                    lead_rows, "same_day_metar_confirms_jma_lattice"
                ),
                "terminal_false_cross_events": sum(
                    row.get("same_day_metar_confirms_jma_lattice") not in {"", None}
                    and int(row["same_day_metar_confirms_jma_lattice"]) == 0
                    for row in lead_rows
                ),
                "first_city_day_lead_events": len(first_day_leads),
                "first_city_day_confirmation_rate": ratio(
                    first_day_leads, "future_metar_confirms_jma_lattice"
                ),
                "unique_lattice_lead_events": len(first_lattice_leads),
                "unique_lattice_confirmation_rate": ratio(
                    first_lattice_leads, "future_metar_confirms_jma_lattice"
                ),
                "unique_lattice_same_day_confirmation_rate": ratio(
                    first_lattice_leads, "same_day_metar_confirms_jma_lattice"
                ),
                "unique_lattice_terminal_false_crosses": sum(
                    row.get("same_day_metar_confirms_jma_lattice") not in {"", None}
                    and int(row["same_day_metar_confirms_jma_lattice"]) == 0
                    for row in first_lattice_leads
                ),
            }
        )
    by_year: list[dict[str, Any]] = []
    for horizon in HORIZONS_MIN:
        for year in ("2024", "2025", "2026"):
            rows = [
                row
                for row in horizon_rows
                if row["horizon_min"] == horizon
                and row["daytime_06_18"] == 1
                and row["jma_temp_update"] == 1
                and float(row["jma_minus_prior_metar_c"]) >= 1
                and str(row["local_date"]).startswith(year)
            ]
            by_year.append(
                {
                    "year": year,
                    "horizon_min": horizon,
                    "events": len(rows),
                    "dates": len({row["local_date"] for row in rows}),
                    "confirmation_rate": ratio(
                        rows, "future_metar_confirms_jma_lattice"
                    ),
                }
            )
    return {
        "schema_version": "tokyo_jma_metar_history_alignment_v1",
        "clock_class": "historical_observation_clock_not_first_seen",
        "jma_rows": len(jma_rows),
        "jma_dates": len(
            {
                parse_utc(str(row["observation_time_utc"]))
                .astimezone(TOKYO)
                .date()
                .isoformat()
                for row in jma_rows
            }
        ),
        "metar_rows": len(metar_rows),
        "metar_dates": len({row["local_date"] for row in metar_rows}),
        "comparison_rows": len(comparison_rows),
        "routine_pairs_within_60m": len(routine_pairs),
        "next_routine_minus_jma_c": {
            "median": quantile(routine_deltas, 0.5),
            "p05": quantile(routine_deltas, 0.05),
            "p95": quantile(routine_deltas, 0.95),
            "mae": (
                sum(abs(value) for value in routine_deltas) / len(routine_deltas)
                if routine_deltas
                else None
            ),
            "within_0_5c_rate": (
                sum(abs(value) <= 0.5 for value in routine_deltas)
                / len(routine_deltas)
                if routine_deltas
                else None
            ),
            "within_1_0c_rate": (
                sum(abs(value) <= 1.0 for value in routine_deltas)
                / len(routine_deltas)
                if routine_deltas
                else None
            ),
        },
        "horizons": horizon_summary,
        "lead_confirmation_by_year": by_year,
        "pit_rule": (
            "Archive rows may train physical source-to-METAR relations. "
            "Only collector source_first_seen_at_utc may define a live decision."
        ),
    }


def write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iem-csv", type=Path, default=DEFAULT_IEM)
    parser.add_argument("--start-date", default="2024-04-30")
    parser.add_argument("--end-date", default="2026-06-10")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refresh-jma", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    jma_cache = args.out / (
        f"jma_haneda_10m_{args.start_date}_{args.end_date}.csv"
    )
    if args.refresh_jma or not jma_cache.exists():
        jma_rows = fetch_jma_history(
            date.fromisoformat(args.start_date),
            date.fromisoformat(args.end_date),
            workers=args.workers,
        )
        write_csv(jma_cache, jma_rows)
    else:
        jma_rows = load_jma(jma_cache)
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    jma_rows = [
        row
        for row in jma_rows
        if start_date
        <= parse_utc(str(row["observation_time_utc"])).astimezone(TOKYO).date()
        <= end_date
    ]
    metar_rows = load_metar(args.iem_csv)
    comparison_rows, horizon_rows = build_alignment_rows(jma_rows, metar_rows)
    summary = summarize(jma_rows, metar_rows, comparison_rows, horizon_rows)
    summary["window"] = {
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    summary["inputs"] = {
        "iem_csv": str(args.iem_csv),
        "iem_csv_sha256": sha256_file(args.iem_csv),
        "jma_cache": str(jma_cache),
        "jma_cache_sha256": sha256_file(jma_cache),
    }

    write_dict_rows(args.out / "jma_metar_pairs.csv", comparison_rows)
    write_dict_rows(args.out / "horizon_labels.csv", horizon_rows)
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
