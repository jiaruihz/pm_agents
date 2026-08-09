#!/usr/bin/env python3
"""Read-only D-1 raw coverage census across legacy and run-aware artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed.production_paths import (  # noqa: E402
    historical_full_ladder_root,
    historical_targeted_root,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402


RUNTIME = load_production_spec().data_feed_runtime_root
START = date(2026, 5, 5)
END = date(2026, 8, 6)
SNAPSHOT_RE = re.compile(r"snapshot_(\d{8})_")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def parse_day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def snapshot_day(path: Path) -> date | None:
    match = SNAPSHOT_RE.search(path.name)
    return datetime.strptime(match.group(1), "%Y%m%d").date() if match else None


def complete_ladder(records: list[dict[str, Any]]) -> bool:
    parsed = [
        parse_market_bracket(str(row.get("bracket") or ""), str(row.get("question") or ""))
        for row in records
    ]
    if any(item is None for item in parsed):
        return False
    ordered = sorted(parsed, key=lambda item: float("-inf") if item.bottom else float(item.low))
    if len(ordered) < 3 or sum(item.bottom for item in ordered) != 1 or sum(item.top for item in ordered) != 1:
        return False
    return all(
        left.high is not None
        and right.low is not None
        and abs(float(right.low) - float(left.high) - 1.0) <= 1e-9
        for left, right in zip(ordered, ordered[1:])
    )


def complete_market(records: list[dict[str, Any]], *, strict_mid: bool) -> bool:
    for row in records:
        bid = row.get("yes_best_bid")
        ask = row.get("yes_best_ask")
        fallback = row.get("market_yes_price")
        if bid is not None and ask is not None:
            try:
                if 0 <= float(bid) <= float(ask) <= 1:
                    continue
            except (TypeError, ValueError):
                pass
        if not strict_mid and fallback is not None:
            continue
        return False
    return True


def snapshot_census(root: Path) -> dict[str, Any]:
    paths = []
    if root.exists():
        for entry in os.scandir(root):
            path = Path(entry.path)
            day = snapshot_day(path)
            if entry.is_file() and day is not None and START <= day <= END:
                paths.append(path)
    captures = Counter()
    target_dates = Counter()
    horizons = Counter()
    states = Counter()
    publishable = 0
    top_level_keys = Counter()
    event_keys: set[tuple[str, str, int]] = set()
    complete_event_keys: set[tuple[str, str, int]] = set()
    strict_market_event_keys: set[tuple[str, str, int]] = set()
    legacy_market_event_keys: set[tuple[str, str, int]] = set()
    for path in sorted(paths):
        captures[str(snapshot_day(path))] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            states["parse_failed_files"] += 1
            continue
        top_level_keys.update(payload.keys())
        publishable += int(bool((payload.get("snapshot_publish_quality") or {}).get("publishable")))
        groups: dict[tuple[str, str, int, str], list[dict[str, Any]]] = defaultdict(list)
        for row in payload.get("records") or []:
            target = str(row.get("event_date") or row.get("target_date") or "")
            local = str(row.get("city_local_date_at_snapshot") or "")
            try:
                horizon = (date.fromisoformat(target) - date.fromisoformat(local)).days
            except ValueError:
                states["rows_missing_local_or_target_date"] += 1
                continue
            if horizon not in (1, 2):
                continue
            city = str(row.get("city") or "")
            event = str(row.get("event_slug") or row.get("market_id") or "")
            groups[(city, target, horizon, event)].append(row)
        for (city, target, horizon, _event), records in groups.items():
            key = (city, target, horizon)
            event_keys.add(key)
            target_dates[target] += 1
            horizons[horizon] += 1
            states["raw_checkpoint_states"] += 1
            if complete_ladder(records):
                states["native_complete_checkpoint_states"] += 1
                complete_event_keys.add(key)
                if complete_market(records, strict_mid=False):
                    states["legacy_market_complete_checkpoint_states"] += 1
                    legacy_market_event_keys.add(key)
                if complete_market(records, strict_mid=True):
                    states["strict_mid_market_complete_checkpoint_states"] += 1
                    strict_market_event_keys.add(key)
    return {
        "root": str(root),
        "files": len(paths),
        "capture_dates": sorted(captures),
        "capture_date_count": len(captures),
        "capture_files_by_date": dict(sorted(captures.items())),
        "publishable_files": publishable,
        "raw_checkpoint_states": states["raw_checkpoint_states"],
        "native_complete_checkpoint_states": states["native_complete_checkpoint_states"],
        "legacy_market_complete_checkpoint_states": states["legacy_market_complete_checkpoint_states"],
        "strict_mid_market_complete_checkpoint_states": states["strict_mid_market_complete_checkpoint_states"],
        "unique_city_target_horizon": len(event_keys),
        "unique_native_complete_city_target_horizon": len(complete_event_keys),
        "unique_strict_mid_city_target_horizon": len(strict_market_event_keys),
        "horizon_checkpoint_counts": dict(sorted(horizons.items())),
        "target_date_count": len(target_dates),
        "target_date_start": min(target_dates) if target_dates else None,
        "target_date_end": max(target_dates) if target_dates else None,
        "other_counters": dict(states),
        "top_level_manifest_fields_present": {
            key: top_level_keys[key]
            for key in sorted(top_level_keys)
            if "ladder" in key or "manifest" in key or "publish" in key or "hash" in key
        },
        "event_keys": sorted([list(key) for key in event_keys]),
        "complete_event_keys": sorted([list(key) for key in complete_event_keys]),
        "strict_market_event_keys": sorted([list(key) for key in strict_market_event_keys]),
    }


def dated_tree_census(root: Path) -> dict[str, Any]:
    dates = Counter()
    files = 0
    bytes_total = 0
    if root.exists():
        for entry in os.scandir(root):
            if not entry.is_dir():
                continue
            day = parse_day(entry.name)
            if day is None or not (START <= day <= END):
                continue
            count = 0
            for _, _, names in os.walk(entry.path):
                count += len(names)
            dates[entry.name] = count
            files += count
    return {
        "root": str(root),
        "date_count": len(dates),
        "date_start": min(dates) if dates else None,
        "date_end": max(dates) if dates else None,
        "files": files,
        "files_by_date": dict(sorted(dates.items())),
        "bytes": bytes_total,
    }


def forecast_versions_census(path: Path) -> dict[str, Any]:
    counts = Counter()
    cities: set[str] = set()
    targets: set[str] = set()
    capture_dates: set[str] = set()
    for row in iter_jsonl(path):
        captured = parse_day(row.get("available_at_utc") or row.get("captured_at_utc"))
        if captured is None or not (START <= captured <= END):
            continue
        counts["rows"] += 1
        cities.add(str(row.get("city") or ""))
        targets.add(str(row.get("forecast_target_date") or row.get("target_date") or ""))
        capture_dates.add(str(captured))
        horizon = row.get("forecast_horizon_days_local")
        if horizon is not None:
            counts[f"horizon_{horizon}"] += 1
        for field in (
            "forecast_run_at_utc",
            "available_at_utc",
            "source_fetch_start_utc",
            "source_raw_payload_hash",
            "batch_capture_id",
            "first_seen_at_utc",
        ):
            counts[f"present_{field}"] += int(bool(row.get(field)))
    return {
        "path": str(path),
        "rows": counts["rows"],
        "cities": len(cities),
        "target_dates": len(targets),
        "capture_dates": sorted(capture_dates),
        "capture_date_count": len(capture_dates),
        "counts": dict(counts),
    }


def curves_census(root: Path) -> dict[str, Any]:
    counts = Counter()
    cities: set[str] = set()
    targets: set[str] = set()
    dates: set[str] = set()
    if root.exists():
        for entry in os.scandir(root):
            if not entry.is_dir():
                continue
            day = parse_day(entry.name)
            if day is None or not (START <= day <= END):
                continue
            dates.add(entry.name)
            for _, _, names in os.walk(entry.path):
                pass
            for file_entry in os.scandir(entry.path):
                if not file_entry.is_file() or not file_entry.name.endswith(".jsonl"):
                    continue
                counts["files"] += 1
                for row in iter_jsonl(Path(file_entry.path)):
                    counts["rows"] += 1
                    cities.add(str(row.get("city") or ""))
                    targets.add(str(row.get("target_date") or ""))
                    for field in (
                        "forecast_first_seen_utc",
                        "available_at_utc",
                        "forecast_run_ts_utc",
                        "forecast_target_lead_hours",
                        "forecast_run_age_hours",
                        "previous_run_forecast_max_f",
                        "run_to_run_revision_f",
                        "batch_capture_id",
                    ):
                        counts[f"present_{field}"] += int(row.get(field) is not None)
    return {
        "root": str(root),
        "rows": counts["rows"],
        "files": counts["files"],
        "cities": len(cities),
        "target_dates": len(targets),
        "capture_dates": sorted(dates),
        "capture_date_count": len(dates),
        "counts": dict(counts),
    }


def run_capture_census(root: Path) -> dict[str, Any]:
    row_path = root / "forecast_run_rows.jsonl"
    batch_path = root / "forecast_batches.jsonl"
    blocker_path = root / "blockers.jsonl"
    rows = list(iter_jsonl(row_path))
    batches = list(iter_jsonl(batch_path))
    blockers = list(iter_jsonl(blocker_path))
    row_counts = Counter()
    target_dates: set[str] = set()
    capture_dates: set[str] = set()
    cities: set[str] = set()
    run_times: set[str] = set()
    valid_keys: set[tuple[str, str, int]] = set()
    for row in rows:
        row_counts["rows"] += 1
        target = str(row.get("target_date") or "")
        target_dates.add(target)
        cities.add(str(row.get("city") or ""))
        capture_dates.add(str(parse_day(row.get("available_at_utc"))))
        if row.get("forecast_run_at_utc"):
            run_times.add(str(row["forecast_run_at_utc"]))
        horizon = int(row.get("horizon_days_local") or -1)
        row_counts[f"horizon_{horizon}"] += 1
        identified = row.get("forecast_run_lineage_status") == "identified" and bool(row.get("forecast_run_at_utc"))
        clocks = bool(row.get("first_seen_at_utc") and row.get("available_at_utc"))
        row_counts["identified"] += int(identified)
        row_counts["first_seen_available"] += int(clocks)
        row_counts["lead_hours"] += int(row.get("lead_hours") is not None)
        row_counts["run_age"] += int(row.get("model_run_age_hours") is not None)
        row_counts["previous_run"] += int(row.get("previous_run_ts") is not None)
        row_counts["content_revision"] += int(row.get("previous_content_hash") is not None)
        if identified and clocks and horizon in (1, 2):
            valid_keys.add((str(row.get("city") or ""), target, horizon))
    complete_batch_ids = {
        str(row.get("batch_capture_id"))
        for row in batches
        if not row.get("missing_model_keys") and int(row.get("model_count") or 0) >= 5
    }
    batch_members = defaultdict(list)
    for row in rows:
        batch_members[str(row.get("batch_capture_id"))].append(row)
    complete_keys = {
        (
            str(members[0].get("city") or ""),
            str(members[0].get("target_date") or ""),
            int(members[0].get("horizon_days_local") or -1),
        )
        for batch_id, members in batch_members.items()
        if batch_id in complete_batch_ids and members
    }
    material_complete = {
        (str(batch.get("city")), str(batch.get("target_date")), str(batch.get("batch_content_hash")))
        for batch in batches
        if str(batch.get("batch_capture_id")) in complete_batch_ids
    }
    return {
        "root": str(root),
        "rows": len(rows),
        "batches": len(batches),
        "complete_batches": len(complete_batch_ids),
        "material_complete_city_target_content": len(material_complete),
        "blockers": len(blockers),
        "blocker_codes": dict(Counter(str(row.get("code")) for row in blockers)),
        "cities": len(cities),
        "capture_dates": sorted(capture_dates),
        "target_dates": sorted(target_dates),
        "run_times": sorted(run_times),
        "row_counts": dict(row_counts),
        "valid_event_keys": sorted([list(key) for key in valid_keys]),
        "complete_event_keys": sorted([list(key) for key in complete_keys]),
        "v2_integrated_outputs_present": {
            "forecast_run_rows_v2": (RUNTIME / "output/forecast_enrichment/forecast_run_rows_v2.jsonl").exists(),
            "forecast_batches_v2": (RUNTIME / "output/forecast_enrichment/forecast_batches_v2.jsonl").exists(),
            "forecast_run_contract_state": (RUNTIME / "output/forecast_enrichment/forecast_run_contract_state.json").exists(),
        },
    }


def settlement_keys(db: Path, targets: list[str]) -> set[tuple[str, str]]:
    if not targets:
        return set()
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    placeholders = ",".join("?" for _ in targets)
    rows = connection.execute(
        f"""
        SELECT city, target_date
        FROM settlement_outcomes INDEXED BY idx_settlement_outcomes_date_city
        WHERE target_date IN ({placeholders})
          AND settlement_status='settled' AND final_price > 0.999
        GROUP BY city, target_date
        """,
        targets,
    ).fetchall()
    connection.close()
    return {(str(city), str(target)) for city, target in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path("/Volumes/jrs/pm_agents/runtime/weather.db"))
    args = parser.parse_args()

    targeted_root = historical_targeted_root()
    full_root = historical_full_ladder_root()
    targeted = snapshot_census(targeted_root / "paper_snapshots")
    full = snapshot_census(full_root / "paper_snapshots")
    run = run_capture_census(RUNTIME / "output/forecast_run_capture")
    target_dates = sorted(set(run["target_dates"]))
    settled = settlement_keys(args.db, target_dates)
    run_complete = {tuple(row) for row in run["complete_event_keys"]}
    full_native = {tuple(row) for row in full["complete_event_keys"]}
    full_market = {tuple(row) for row in full["strict_market_event_keys"]}
    targeted_native = {tuple(row) for row in targeted["complete_event_keys"]}
    targeted_market = {tuple(row) for row in targeted["strict_market_event_keys"]}

    def joined(keys: set[tuple[str, str, int]]) -> int:
        return sum((city, target) in settled for city, target, _ in keys)

    summary = {
        "schema_version": "d1_raw_coverage_audit_v1",
        "window": [str(START), str(END)],
        "runtime": str(RUNTIME),
        "forecast_versions": forecast_versions_census(
            RUNTIME / "output/forecast_enrichment/forecast_versions.jsonl"
        ),
        "forecast_hourly_curves": curves_census(
            targeted_root / "forecast_hourly_curves"
        ),
        "exact_run_capture": run,
        "targeted_paper_snapshots": targeted,
        "full_ladder_paper_snapshots": full,
        "orderbook_trees": {
            "targeted": dated_tree_census(targeted_root / "orderbook_snapshots"),
            "full_ladder": dated_tree_census(full_root / "orderbook_snapshots"),
        },
        "intersection": {
            "exact_run_complete_event_keys": len(run_complete),
            "exact_run_complete_with_any_targeted_native_ladder": len(run_complete & targeted_native),
            "exact_run_complete_with_strict_targeted_market": len(run_complete & targeted_market),
            "exact_run_complete_with_any_full_native_ladder": len(run_complete & full_native),
            "exact_run_complete_with_strict_full_market": len(run_complete & full_market),
            "exact_run_complete_with_settlement": joined(run_complete),
            "exact_run_complete_with_settlement_and_strict_full_market": joined(run_complete & full_market),
            "settled_city_target_keys_for_exact_run_target_dates": len(settled),
        },
    }
    # Remove large key lists from snapshot sections after computing joins.
    for section in (targeted, full):
        section.pop("event_keys", None)
        section.pop("complete_event_keys", None)
        section.pop("strict_market_event_keys", None)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
