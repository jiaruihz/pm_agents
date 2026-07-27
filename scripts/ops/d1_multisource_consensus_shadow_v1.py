#!/usr/bin/env python3
"""Zero-notional D-1 endpoint-NO shadow for assigned-vs-consensus residual."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import (
    CITY_MODEL,
)


RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_VERSIONS = (
    RUNTIME_ROOT / "output/forecast_enrichment/forecast_versions.jsonl"
)
DEFAULT_BOOK_ROOT = RUNTIME_ROOT / "full_ladder_output/orderbook_snapshots"
DEFAULT_POLICY = Path(
    "configs/weather/d1_multisource_consensus_shadow_v1.json"
)
DEFAULT_OUTPUT = (
    RUNTIME_ROOT / "output/d1_multisource_consensus_shadow_v1"
)


def utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.astimezone(timezone.utc)
        if parsed.tzinfo
        else parsed.replace(tzinfo=timezone.utc)
    )


def latest_file(root: Path, pattern: str) -> Path | None:
    paths = list(root.glob(pattern))
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def bracket_center(text: Any) -> float | None:
    values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", str(text))]
    if not values:
        return None
    return sum(values[:2]) / min(2, len(values))


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def load_policy(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    payload = read_json(path, {})
    index = {
        (str(row["city"]), str(row["model_label"])): row
        for row in payload.get("records") or []
        if row.get("city") and row.get("model_label")
    }
    return index, payload


def version_history(
    path: Path, asof: datetime
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    history: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    if not path.exists():
        return history
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            available = utc(row.get("available_at_utc"))
            if available is None or available > asof:
                continue
            key = (
                str(row.get("city") or ""),
                str(row.get("forecast_target_date") or ""),
                str(row.get("model_label") or ""),
            )
            history[key].append(row)
    for rows in history.values():
        rows.sort(key=lambda row: str(row.get("available_at_utc") or ""))
    return history


def latest_version(
    history: dict[tuple[str, str, str], list[dict[str, Any]]],
    key: tuple[str, str, str],
    asof: datetime,
) -> dict[str, Any] | None:
    eligible = [
        row
        for row in history.get(key, [])
        if (utc(row.get("available_at_utc")) or datetime.max.replace(tzinfo=timezone.utc))
        <= asof
    ]
    return eligible[-1] if eligible else None


def load_books(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("outcome") or "").lower() == "no":
                rows.append(row)
    return rows


def build_cycle(
    *,
    book_path: Path,
    versions_path: Path,
    policy_path: Path,
) -> dict[str, Any]:
    books = load_books(book_path)
    book_asof = max(
        (utc(row.get("fetched_at_utc")) for row in books),
        default=None,
    )
    if book_asof is None:
        return {"status": "missing_book_timestamp", "records": []}
    versions = version_history(versions_path, book_asof)
    policy, policy_payload = load_policy(policy_path)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in books:
        groups[(str(row.get("city")), str(row.get("event_date")))].append(row)

    opportunities: list[dict[str, Any]] = []
    d1_groups = 0
    versioned_groups = 0
    for (city, target_date), group in sorted(groups.items()):
        city_version_rows = [
            row
            for key, history_rows in versions.items()
            if key[0] == city and key[1] == target_date
            for row in history_rows
        ]
        timezone_name = next(
            (
                str(row.get("timezone_name"))
                for row in city_version_rows
                if row.get("timezone_name")
            ),
            "",
        )
        if not timezone_name:
            continue
        local = book_asof.astimezone(ZoneInfo(timezone_name))
        try:
            target = datetime.fromisoformat(target_date).date()
        except ValueError:
            continue
        if (target - local.date()).days != 1 or not (12 <= local.hour < 24):
            continue
        d1_groups += 1
        ordered = sorted(
            (
                (bracket_center(row.get("bracket")), row)
                for row in group
                if bracket_center(row.get("bracket")) is not None
            ),
            key=lambda item: item[0],
        )
        if len(ordered) < 2:
            continue
        endpoints = [("low_no", ordered[0][1]), ("high_no", ordered[-1][1])]
        endpoint_times = [
            utc(row.get("fetched_at_utc")) for _, row in endpoints
        ]
        if any(value is None for value in endpoint_times):
            continue
        decision_asof = min(value for value in endpoint_times if value is not None)
        forecast_rows = [
            row
            for key in versions
            if key[0] == city and key[1] == target_date
            for row in [latest_version(versions, key, decision_asof)]
            if row is not None
        ]
        corrected: list[tuple[str, float, dict[str, Any]]] = []
        for forecast in forecast_rows:
            model_label = str(forecast.get("model_label") or "")
            calibration = policy.get((city, model_label))
            if calibration is None:
                continue
            value = float(forecast["forecast_max_f"]) + float(
                calibration["bias_correction_f"]
            )
            corrected.append((model_label, value, forecast))
        if len(corrected) < 3:
            continue
        assigned_label = "ECMWF" if CITY_MODEL.get(city) == "ecmwf" else "GFS"
        assigned = next(
            (value for label, value, _ in corrected if label == assigned_label),
            None,
        )
        if assigned is None:
            continue
        versioned_groups += 1
        values = [value for _, value, _ in corrected]
        consensus = median(values)
        spread = percentile(values, 0.75) - percentile(values, 0.25)
        delta = assigned - consensus
        for leg, book in endpoints:
            summary = book.get("summary") if isinstance(book.get("summary"), dict) else {}
            score = delta if leg == "high_no" else -delta
            opportunities.append(
                {
                    "schema_version": "d1_multisource_consensus_shadow_v1",
                    "strategy_instance": "d1_multisource_consensus_shadow_v1",
                    "execution_mode": "zero_notional_shadow",
                    "orders_submitted": 0,
                    "city": city,
                    "target_date": target_date,
                    "leg": leg,
                    "bracket": book.get("bracket"),
                    "condition_id": book.get("condition_id"),
                    "token_id": book.get("token_id"),
                    "book_fetched_at_utc": book.get("fetched_at_utc"),
                    "decision_asof_utc": decision_asof.isoformat(),
                    "book_snapshot_path": str(book_path),
                    "no_best_ask": summary.get("best_ask"),
                    "no_ask_size": summary.get("ask_size"),
                    "book_status": book.get("status"),
                    "local_decision_hour": local.hour + local.minute / 60,
                    "assigned_model_label": assigned_label,
                    "assigned_corrected_f": assigned,
                    "consensus_corrected_f": consensus,
                    "assigned_minus_consensus_f": delta,
                    "ensemble_spread_iqr_f": spread,
                    "eligible_model_count": len(corrected),
                    "directional_no_score_f": score,
                    "hypothesized_underpriced_no": score > 0,
                    "forecast_available_max_utc": max(
                        str(row.get("available_at_utc") or "")
                        for _, _, row in corrected
                    ),
                    "policy_training_cutoff": policy_payload.get(
                        "training_cutoff"
                    ),
                }
            )
    return {
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_instance": "d1_multisource_consensus_shadow_v1",
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "book_path": str(book_path),
        "book_asof_utc": book_asof.isoformat(),
        "signal_funnel": {
            "book_city_dates": len(groups),
            "d1_12_24_city_dates": d1_groups,
            "versioned_consensus_city_dates": versioned_groups,
            "endpoint_legs": len(opportunities),
        },
        "evidence_funnel": {
            "pit_forecast_version_legs": len(opportunities),
            "fresh_book_legs": sum(
                row.get("no_best_ask") is not None for row in opportunities
            ),
            "settled_legs": 0,
            "actual_fills": 0,
        },
        "records": opportunities,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    book_path = latest_file(args.book_root, "**/*.jsonl.gz")
    if book_path is None:
        return {"status": "missing_orderbook", "records": []}
    state = read_json(args.output_dir / "state.json", {})
    if args.dedup and state.get("last_book_path") == str(book_path):
        return {"status": "unchanged", "records": []}
    payload = build_cycle(
        book_path=book_path,
        versions_path=args.versions,
        policy_path=args.policy,
    )
    if payload.get("status") == "ok":
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / "latest.json", payload)
        append_jsonl(
            args.output_dir / "opportunities.jsonl",
            list(payload.get("records") or []),
        )
        write_json(
            args.output_dir / "state.json",
            {
                "last_book_path": str(book_path),
                "last_generated_at_utc": payload.get("generated_at_utc"),
            },
        )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versions", type=Path, default=DEFAULT_VERSIONS)
    parser.add_argument("--book-root", type=Path, default=DEFAULT_BOOK_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60)
    parser.add_argument("--dedup", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        payload = run_once(args)
        print(
            json.dumps(
                {key: value for key, value in payload.items() if key != "records"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if not args.loop:
            return 0
        time.sleep(max(1, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
