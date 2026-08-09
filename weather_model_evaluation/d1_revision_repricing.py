#!/usr/bin/env python3
"""Build the D-1 exact-run forecast-revision to market-repricing denominator.

This is a coverage and mechanism study.  It preserves bootstrap runs, partial
batch completions, missing ladders, and unsettled dates as evidence blockers.
It never creates a plan, order, fill, or execution recommendation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, median
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.assigned_forecast_models import assigned_single_run_model_key  # noqa: E402
from weather_data_feed import load_city_configs  # noqa: E402
from weather_data_feed.forecast_run_contract import (  # noqa: E402
    materialize_full_ladder_checkpoint,
    parse_utc,
    stable_content_hash,
)
from weather_data_feed.market_book_contract import classify_orderbook_clock  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


PRODUCTION_SPEC = load_production_spec()
DEFAULT_CAPTURE_DIR = PRODUCTION_SPEC.data_feed_output_root() / "forecast_run_capture"
DEFAULT_SNAPSHOT_DIR = PRODUCTION_SPEC.historical_full_ladder_root() / "paper_snapshots"
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-08/generated/d1_forecast_revision_market_repricing"
)
DEFAULT_REPORT = DEFAULT_OUT / "report.md"
DEFAULT_DB = ROOT / "runtime/weather.db"
BOOTSTRAP_WINDOW_MINUTES = 30
# Short horizons distinguish a thin/stale first book from genuine absorption;
# longer horizons measure whether the market keeps repricing the same run.
MARKOUT_MINUTES = (5, 10, 30, 60, 90)


def default_snapshot_dirs() -> list[Path]:
    roots = [load_production_spec().historical_full_ladder_root() / "paper_snapshots"]
    return list(dict.fromkeys(path for path in roots if path.exists()))


def default_canonical_market_roots() -> tuple[Path, Path]:
    spec = load_production_spec()
    return (
        spec.resolved_market_books_root(),
        spec.resolved_market_ladder_snapshot_root(),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def material_forecast_batches(
    rows: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows_by_batch: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_batch.setdefault(str(row.get("batch_capture_id") or ""), []).append(row)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for source_batch in batches:
        batch = dict(source_batch)
        batch_id = str(batch.get("batch_capture_id") or "")
        members = rows_by_batch.get(batch_id, [])
        run_timestamps = sorted(
            {str(row.get("forecast_run_at_utc")) for row in members if row.get("forecast_run_at_utc")}
        )
        forecast_run_at_utc = run_timestamps[0] if len(run_timestamps) == 1 else None
        available_values = [
            str(row.get("available_at_utc")) for row in members if row.get("available_at_utc")
        ]
        batch_available_at_utc = max(available_values) if available_values else None
        material_batch_key = stable_content_hash(
            {
                "city": batch.get("city"),
                "target_date": batch.get("target_date"),
                "forecast_run_at_utc": forecast_run_at_utc,
                "batch_content_hash": batch.get("batch_content_hash"),
            }
        )
        batch.update(
            {
                "material_batch_key": material_batch_key,
                "forecast_run_at_utc": forecast_run_at_utc,
                "batch_available_at_utc": batch_available_at_utc,
            }
        )
        grouped.setdefault(material_batch_key, []).append(batch)
    material: list[dict[str, Any]] = []
    material_members: dict[str, list[dict[str, Any]]] = {}
    for material_batch_key, deliveries in grouped.items():
        selected = min(
            deliveries,
            key=lambda item: (
                str(item.get("batch_available_at_utc") or "9999"),
                str(item.get("batch_capture_id") or ""),
            ),
        )
        selected = dict(selected)
        selected["delivery_count"] = len(deliveries)
        selected["delivery_batch_capture_ids"] = sorted(
            str(item.get("batch_capture_id") or "") for item in deliveries
        )
        material.append(selected)
        material_members[str(selected.get("batch_capture_id") or "")] = rows_by_batch.get(
            str(selected.get("batch_capture_id") or ""), []
        )
    material.sort(
        key=lambda item: (
            str(item.get("batch_available_at_utc") or ""),
            str(item.get("city") or ""),
            str(item.get("target_date") or ""),
        )
    )
    return material, material_members


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assigned_value(batch: dict[str, Any]) -> float | None:
    existing = batch.get("assigned_model_value_f")
    if existing is not None:
        return float(existing)
    values = dict(batch.get("model_values") or {})
    assigned_key = assigned_single_run_model_key(str(batch.get("city") or ""))
    value = values.get(assigned_key)
    return float(value) if value is not None else None


def d1_checkpoint_policy(
    target_date: str,
    event_available_at_utc: str,
    timezone_name: str,
) -> tuple[str, float]:
    event_local = parse_utc(
        event_available_at_utc, field="event_available_at_utc"
    ).astimezone(ZoneInfo(timezone_name))
    target_midnight = datetime.fromisoformat(target_date).replace(
        tzinfo=ZoneInfo(timezone_name)
    )
    hours = (event_local - target_midnight).total_seconds() / 3600.0
    if -6.0 <= hours < 0.0:
        return "D-1_18_24", hours
    if -12.0 <= hours < -6.0:
        return "D-1_12_18", hours
    return "outside_frozen_d1_checkpoint", hours


def lineage_impact(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    source = [dict(row) for row in rows]
    by_provider_run: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in source:
        key = (
            str(row.get("model_key") or ""),
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("forecast_run_at_utc") or ""),
        )
        by_provider_run.setdefault(key, []).append(row)
    repeated_provider_runs = {
        key: deliveries
        for key, deliveries in by_provider_run.items()
        if len(deliveries) > 1
    }
    first_seen_drift_keys = sum(
        len({str(row.get("first_seen_at_utc") or "") for row in deliveries}) > 1
        for deliveries in repeated_provider_runs.values()
    )
    content_revision_rows = [
        row for row in source if row.get("previous_content_hash")
    ]
    zero_delta_content_revision_rows = sum(
        float(row.get("content_revision_delta_f") or 0.0) == 0.0
        for row in content_revision_rows
    )
    with_previous = [row for row in source if row.get("previous_run_ts")]
    backward = [
        row
        for row in with_previous
        if str(row["previous_run_ts"]) > str(row.get("forecast_run_at_utc") or "")
    ]
    forward = [
        row
        for row in with_previous
        if str(row["previous_run_ts"]) < str(row.get("forecast_run_at_utc") or "")
    ]
    transition_keys = {
        (
            str(row.get("model_key")),
            str(row.get("city")),
            str(row.get("target_date")),
            str(row.get("previous_run_ts")),
            str(row.get("forecast_run_at_utc")),
        )
        for row in forward
    }
    return {
        "raw_forecast_rows": len(source),
        "rows_with_previous_run": len(with_previous),
        "backward_previous_run_rows": len(backward),
        "forward_previous_run_rows": len(forward),
        "unique_forward_transition_keys": len(transition_keys),
        "repeated_forward_transition_rows": len(forward) - len(transition_keys),
        "unique_provider_run_keys": len(by_provider_run),
        "multi_delivery_provider_run_keys": len(repeated_provider_runs),
        "provider_run_keys_with_first_seen_drift": first_seen_drift_keys,
        "duplicate_delivery_rows": len(source) - len(by_provider_run),
        "content_revision_rows": len(content_revision_rows),
        "zero_delta_content_revision_rows": zero_delta_content_revision_rows,
        "affected_window_start_utc": min(
            (str(row.get("available_at_utc")) for row in source), default=None
        ),
        "affected_window_end_utc": max(
            (str(row.get("available_at_utc")) for row in source), default=None
        ),
    }


def build_revision_events(
    rows: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    material, _ = material_forecast_batches(rows, batches)
    timezone_by_city = {
        config.city: config.timezone_name
        for config in load_city_configs(include_station_diff=False)
    }
    available = [
        parse_utc(item.get("batch_available_at_utc"), field="batch_available_at_utc")
        for item in material
        if item.get("batch_available_at_utc")
    ]
    monitor_start = min(available) if available else None
    bootstrap_end = (
        monitor_start + timedelta(minutes=BOOTSTRAP_WINDOW_MINUTES)
        if monitor_start
        else None
    )
    by_sequence: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in material:
        if not item.get("forecast_run_at_utc"):
            continue
        by_sequence.setdefault(
            (str(item.get("city") or ""), str(item.get("target_date") or "")), []
        ).append(item)

    events: list[dict[str, Any]] = []
    for (city, target_date), sequence in sorted(by_sequence.items()):
        complete = [item for item in sequence if not item.get("missing_model_keys")]
        complete.sort(
            key=lambda item: (
                str(item.get("forecast_run_at_utc")),
                str(item.get("batch_available_at_utc")),
            )
        )
        for previous, current in zip(complete, complete[1:]):
            current_available = parse_utc(
                current.get("batch_available_at_utc"), field="batch_available_at_utc"
            )
            had_earlier_partial = any(
                item.get("missing_model_keys")
                and item.get("forecast_run_at_utc") == current.get("forecast_run_at_utc")
                and str(item.get("batch_available_at_utc") or "")
                < str(current.get("batch_available_at_utc") or "")
                for item in sequence
            )
            if bootstrap_end and current_available <= bootstrap_end:
                event_class = "bootstrap_existing_run"
            elif had_earlier_partial:
                event_class = "complete_batch_after_partial"
            else:
                event_class = "forward_new_complete_run"
            previous_values = dict(previous.get("model_values") or {})
            current_values = dict(current.get("model_values") or {})
            common_models = sorted(set(previous_values) & set(current_values))
            revisions = [
                float(current_values[model]) - float(previous_values[model])
                for model in common_models
            ]
            previous_assigned = _assigned_value(previous)
            current_assigned = _assigned_value(current)
            checkpoint_policy, local_hours = d1_checkpoint_policy(
                target_date,
                str(current.get("batch_available_at_utc")),
                timezone_by_city[city],
            )
            events.append(
                {
                    "revision_event_id": stable_content_hash(
                        {
                            "city": city,
                            "target_date": target_date,
                            "previous_run": previous.get("forecast_run_at_utc"),
                            "current_run": current.get("forecast_run_at_utc"),
                            "current_batch": current.get("material_batch_key"),
                        }
                    ),
                    "city": city,
                    "target_date": target_date,
                    "horizon_days_local": 1
                    if any(
                        int(row.get("horizon_days_local") or -1) == 1
                        and row.get("batch_capture_id") == current.get("batch_capture_id")
                        for row in rows
                    )
                    else 2,
                    "previous_run_at_utc": previous.get("forecast_run_at_utc"),
                    "forecast_run_at_utc": current.get("forecast_run_at_utc"),
                    "event_available_at_utc": current.get("batch_available_at_utc"),
                    "event_class": event_class,
                    "checkpoint_policy": checkpoint_policy,
                    "local_hours_from_target_midnight": local_hours,
                    "common_model_count": len(common_models),
                    "consensus_mean_revision_f": (
                        float(current.get("mean_f")) - float(previous.get("mean_f"))
                    ),
                    "consensus_median_revision_f": (
                        float(current.get("median_f")) - float(previous.get("median_f"))
                    ),
                    "mean_absolute_model_revision_f": (
                        sum(abs(value) for value in revisions) / len(revisions)
                        if revisions
                        else None
                    ),
                    "assigned_model_revision_f": (
                        current_assigned - previous_assigned
                        if current_assigned is not None and previous_assigned is not None
                        else None
                    ),
                    "previous_batch_content_hash": previous.get("batch_content_hash"),
                    "current_batch_content_hash": current.get("batch_content_hash"),
                }
            )
    return events, {
        "raw_forecast_batches": len(batches),
        "material_forecast_batches": len(material),
        "duplicate_poll_batches_collapsed": len(batches) - len(material),
        "complete_material_batches": sum(not item.get("missing_model_keys") for item in material),
        "monitor_start_utc": _utc_text(monitor_start) if monitor_start else None,
        "bootstrap_end_utc": _utc_text(bootstrap_end) if bootstrap_end else None,
    }


def build_provider_run_events(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build asynchronous provider-run first-seen events.

    Global models do not publish a common run atomically.  Requiring an entire
    five-model batch to be complete at its first poll makes the formal forward
    denominator structurally empty.  The market-observable event is one
    provider model's new run becoming available.  Consensus features are the
    rolling as-of vector immediately before and after that one-model update.

    Legacy v2 rows did not persist run first-seen across polls, but their
    append-only journal still supports an auditable earliest-observed clock.
    Those events remain development-only.  V3 collector-exact rows are the
    only events eligible for untouched forward.
    """

    timezone_by_city = {
        config.city: config.timezone_name
        for config in load_city_configs(include_station_diff=False)
    }
    by_run: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    delivery_counts: Counter[tuple[str, str, str, str]] = Counter()
    for source in rows:
        run_at = str(source.get("forecast_run_at_utc") or "")
        available = str(
            source.get("run_first_seen_at_utc")
            or source.get("first_seen_at_utc")
            or source.get("available_at_utc")
            or ""
        )
        if not run_at or not available:
            continue
        key = (
            str(source.get("model_key") or ""),
            str(source.get("city") or ""),
            str(source.get("target_date") or ""),
            run_at,
        )
        delivery_counts[key] += 1
        candidate = dict(source)
        candidate["observed_run_first_seen_at_utc"] = available
        prior = by_run.get(key)
        if prior is None or available < str(prior["observed_run_first_seen_at_utc"]):
            by_run[key] = candidate

    arrivals = sorted(
        by_run.values(),
        key=lambda item: (
            str(item["observed_run_first_seen_at_utc"]),
            str(item.get("model_key") or ""),
            str(item.get("city") or ""),
            str(item.get("target_date") or ""),
            str(item.get("forecast_run_at_utc") or ""),
        ),
    )
    latest_by_model_city_target: dict[tuple[str, str, str], dict[str, Any]] = {}
    asof_values: dict[tuple[str, str], dict[str, float]] = {}
    events: list[dict[str, Any]] = []
    out_of_order = 0
    bootstrap = 0
    for current in arrivals:
        model = str(current.get("model_key") or "")
        city = str(current.get("city") or "")
        target_date = str(current.get("target_date") or "")
        run_at = str(current.get("forecast_run_at_utc") or "")
        event_time = str(current["observed_run_first_seen_at_utc"])
        sequence_key = (model, city, target_date)
        previous = latest_by_model_city_target.get(sequence_key)
        if previous and str(previous.get("forecast_run_at_utc") or "") >= run_at:
            out_of_order += 1
            continue

        vector_key = (city, target_date)
        before_values = dict(asof_values.get(vector_key) or {})
        previous_model_value = before_values.get(model)
        current_value = float(current["forecast_max_f"])
        after_values = {**before_values, model: current_value}
        asof_values[vector_key] = after_values
        latest_by_model_city_target[sequence_key] = current
        if previous is None or previous_model_value is None:
            bootstrap += 1
            continue

        previous_run_at = str(previous.get("forecast_run_at_utc") or "")
        if previous_run_at >= run_at:
            out_of_order += 1
            continue
        checkpoint_policy, local_hours = d1_checkpoint_policy(
            target_date,
            event_time,
            timezone_by_city[city],
        )
        schema_version = str(current.get("schema_version") or "")
        first_seen_status = str(current.get("run_first_seen_status") or "")
        collector_exact = (
            schema_version == "weather_forecast_run_row_v3"
            and first_seen_status == "collector_exact"
        )
        event_class = (
            "forward_provider_run_first_seen"
            if collector_exact
            else "legacy_provider_run_earliest_observed"
        )
        before_median = median(before_values.values()) if before_values else None
        after_median = median(after_values.values()) if after_values else None
        before_mean = mean(before_values.values()) if before_values else None
        after_mean = mean(after_values.values()) if after_values else None
        assigned = bool(current.get("assigned_model"))
        events.append(
            {
                "revision_event_id": stable_content_hash(
                    {
                        "event_type": "provider_run_first_seen",
                        "model": model,
                        "city": city,
                        "target_date": target_date,
                        "previous_run": previous_run_at,
                        "current_run": run_at,
                        "event_time": event_time,
                    }
                ),
                "event_type": "provider_run_first_seen",
                "event_class": event_class,
                "model_key": model,
                "city": city,
                "target_date": target_date,
                "horizon_days_local": int(current.get("horizon_days_local") or -1),
                "previous_run_at_utc": previous_run_at,
                "forecast_run_at_utc": run_at,
                "event_available_at_utc": event_time,
                "run_first_seen_status": (
                    first_seen_status or "legacy_earliest_observed"
                ),
                "checkpoint_policy": checkpoint_policy,
                "local_hours_from_target_midnight": local_hours,
                "common_model_count": len(before_values),
                "model_revision_f": current_value - float(previous_model_value),
                "consensus_mean_revision_f": (
                    float(after_mean) - float(before_mean)
                    if before_mean is not None and after_mean is not None
                    else None
                ),
                "consensus_median_revision_f": (
                    float(after_median) - float(before_median)
                    if before_median is not None and after_median is not None
                    else None
                ),
                "mean_absolute_model_revision_f": abs(
                    current_value - float(previous_model_value)
                ),
                "assigned_model_revision_f": (
                    current_value - float(previous_model_value) if assigned else None
                ),
                "assigned_model_event": assigned,
                "source_capture_id": current.get("capture_id"),
                "source_batch_capture_id": current.get("batch_capture_id"),
            }
        )
    return events, {
        "raw_forecast_rows": len(rows),
        "unique_provider_run_keys": len(by_run),
        "duplicate_provider_run_deliveries_collapsed": sum(delivery_counts.values())
        - len(by_run),
        "provider_run_bootstrap_states": bootstrap,
        "provider_run_out_of_order_backfills": out_of_order,
        "provider_run_transition_events": len(events),
        "legacy_provider_run_events": sum(
            event["event_class"] == "legacy_provider_run_earliest_observed"
            for event in events
        ),
        "forward_provider_run_events": sum(
            event["event_class"] == "forward_provider_run_first_seen"
            for event in events
        ),
    }


def _snapshot_paths(
    snapshot_dirs: Iterable[Path], events: list[dict[str, Any]]
) -> list[Path]:
    if isinstance(snapshot_dirs, Path):
        snapshot_dirs = [snapshot_dirs]
    days: set[str] = set()
    for event in events:
        value = parse_utc(event["event_available_at_utc"], field="event_available_at_utc")
        for offset in (-1, 0, 1):
            days.add((value + timedelta(days=offset)).strftime("%Y%m%d"))
    return sorted(
        {
            path
            for snapshot_dir in snapshot_dirs
            for day in days
            for path in snapshot_dir.glob(f"snapshot_{day}_*.json")
        },
        key=lambda path: (path.name, str(path)),
    )


def load_market_checkpoints(
    snapshot_dirs: Iterable[Path],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_keys = {(event["city"], event["target_date"]) for event in events}
    checkpoints: list[dict[str, Any]] = []
    for path in _snapshot_paths(snapshot_dirs, events):
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for source in payload.get("records") or []:
            if not isinstance(source, dict):
                continue
            key = (str(source.get("city") or ""), str(source.get("target_date") or ""))
            if key not in target_keys:
                continue
            row = dict(source)
            row["book_status"] = row.get("yes_book_status")
            groups.setdefault(key, []).append(row)
        for (city, target_date), group in groups.items():
            row_clock_exact = all(
                bool(row.get("event_time_pit_scorable"))
                and row.get("ladder_available_at_utc")
                for row in group
            )
            if row_clock_exact:
                available_at = max(
                    str(row["ladder_available_at_utc"]) for row in group
                )
                checkpoint_ts = available_at
                clock_lineage_status = "collector_exact_full_ladder_clock"
                clock_lineage_blockers: list[str] = []
            else:
                # Retain legacy rows for terminal probability/coverage work, but
                # never infer a response clock from snapshot_ts or a filename.
                legacy = classify_orderbook_clock(group[0])
                available_at = str(payload.get("available_at_utc") or "") or None
                checkpoint_ts = str(
                    payload.get("ts_utc")
                    or group[0].get("snapshot_ts_utc")
                    or ""
                )
                clock_lineage_status = str(legacy["clock_lineage_status"])
                clock_lineage_blockers = list(legacy["clock_lineage_blockers"])
            feature_book_snapshot_id = stable_content_hash(
                {"path": path.name, "city": city, "target_date": target_date}
            )
            checkpoint = materialize_full_ladder_checkpoint(
                group,
                city=city,
                target_date=target_date,
                event_id=path.name,
                checkpoint_ts_utc=checkpoint_ts,
                feature_book_snapshot_id=feature_book_snapshot_id,
                horizon_days=1,
            )
            checkpoint.update(
                {
                    "available_at_utc": available_at,
                    "event_time_pit_scorable": row_clock_exact,
                    "clock_lineage_status": clock_lineage_status,
                    "clock_lineage_blockers": clock_lineage_blockers,
                    "source_path": str(path),
                    "source_contract": "legacy_paper_snapshot",
                    "probabilities": {
                        str(row["label"]): row["normalized_market_probability"]
                        for row in checkpoint["rung_manifest"]
                    },
                }
            )
            checkpoints.append(checkpoint)
    deduplicated: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for checkpoint in checkpoints:
        key = (
            str(checkpoint.get("city") or ""),
            str(checkpoint.get("target_date") or ""),
            str(checkpoint.get("checkpoint_ts_utc") or ""),
            str(checkpoint.get("ladder_hash") or ""),
        )
        deduplicated.setdefault(key, checkpoint)
    return sorted(
        deduplicated.values(),
        key=lambda item: str(item.get("checkpoint_ts_utc") or ""),
    )


def _read_market_book_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".gz":
        handle = gzip.open(path, "rt", encoding="utf-8")
    else:
        handle = path.open("r", encoding="utf-8")
    with handle:
        return [json.loads(line) for line in handle if line.strip()]


def _canonical_ladder_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("20??-??-??/market_ladder_snapshot_*.json"))


def _canonical_book_path(books_root: Path, ladder_path: Path) -> Path | None:
    stem = ladder_path.stem.replace("market_ladder_snapshot_", "market_books_", 1)
    day = ladder_path.parent.name
    candidates = (
        books_root / "batches" / day / f"{stem}.jsonl.gz",
        books_root / "batches" / day / f"{stem}.jsonl",
    )
    return next((path for path in candidates if path.exists()), None)


def _effective_yes_quote(
    yes_book: dict[str, Any] | None,
    no_book: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    yes_summary = dict((yes_book or {}).get("summary") or {})
    no_summary = dict((no_book or {}).get("summary") or {})
    bid_candidates: list[float] = []
    ask_candidates: list[float] = []
    if yes_summary.get("best_bid") is not None:
        bid_candidates.append(float(yes_summary["best_bid"]))
    if no_summary.get("best_ask") is not None:
        bid_candidates.append(1.0 - float(no_summary["best_ask"]))
    if yes_summary.get("best_ask") is not None:
        ask_candidates.append(float(yes_summary["best_ask"]))
    if no_summary.get("best_bid") is not None:
        ask_candidates.append(1.0 - float(no_summary["best_bid"]))
    bid = max(bid_candidates) if bid_candidates else None
    ask = min(ask_candidates) if ask_candidates else None
    if bid is not None and ask is not None and ask < bid:
        return None, None
    return bid, ask


def _canonical_book_clock_exact(
    row: dict[str, Any] | None,
    *,
    published_at_utc: str,
) -> bool:
    if (
        row is None
        or row.get("status") != "ok"
        or row.get("event_time_pit_scorable") is not True
    ):
        return False
    try:
        request = parse_utc(row.get("request_started_at_utc"), field="request_started_at_utc")
        response = parse_utc(
            row.get("response_received_at_utc"), field="response_received_at_utc"
        )
        parsed = parse_utc(row.get("parsed_at_utc"), field="parsed_at_utc")
        published = parse_utc(published_at_utc, field="published_at_utc")
    except (TypeError, ValueError):
        return False
    return request <= response <= parsed <= published


def load_canonical_market_checkpoints(
    *,
    books_root: Path,
    ladder_root: Path,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join canonical ladder manifests to their immutable market-book batch.

    The current production contract separates raw books from the ladder view.
    Research must consume both artifacts and must not fall back to the retired
    paper-snapshot producer or re-request the exchange.
    """

    target_keys = {(event["city"], event["target_date"]) for event in events}
    checkpoints: list[dict[str, Any]] = []
    for ladder_path in _canonical_ladder_paths(ladder_root):
        payload = json.loads(ladder_path.read_text(encoding="utf-8"))
        relevant = [
            row
            for row in payload.get("records") or []
            if isinstance(row, dict)
            and (str(row.get("city") or ""), str(row.get("target_date") or ""))
            in target_keys
        ]
        if not relevant:
            continue
        book_path = _canonical_book_path(books_root, ladder_path)
        book_rows = _read_market_book_rows(book_path) if book_path else []
        book_index = {
            str(row.get("book_capture_id") or ""): row
            for row in book_rows
            if row.get("book_capture_id")
        }
        published_at = str(payload.get("available_at_utc") or "")
        for event in relevant:
            rungs = list(event.get("rungs") or [])
            material_rows: list[dict[str, Any]] = []
            exact = bool(rungs) and bool(published_at) and book_path is not None
            for index, rung in enumerate(rungs):
                yes = book_index.get(str(rung.get("yes_book_capture_id") or ""))
                no = book_index.get(str(rung.get("no_book_capture_id") or ""))
                referenced = (yes, no)
                exact = exact and all(
                    _canonical_book_clock_exact(
                        row,
                        published_at_utc=published_at,
                    )
                    for row in referenced
                )
                bid, ask = _effective_yes_quote(yes, no)
                bracket = str(rung.get("bracket") or "")
                if index == 0:
                    question = f"Will Tmax be {bracket} or below?"
                elif index == len(rungs) - 1:
                    question = f"Will Tmax be {bracket} or higher?"
                else:
                    question = f"Will Tmax be {bracket}?"
                material_rows.append(
                    {
                        "bracket": bracket,
                        "question": question,
                        "condition_id": rung.get("condition_id"),
                        "token_id": rung.get("yes_token_id"),
                        "yes_best_bid": bid,
                        "yes_best_ask": ask,
                        "book_status": (
                            "effective_yes_two_sided"
                            if bid is not None and ask is not None
                            else "missing_effective_yes_two_sided"
                        ),
                    }
                )
            city = str(event.get("city") or "")
            target_date = str(event.get("target_date") or "")
            feature_book_snapshot_id = stable_content_hash(
                {
                    "batch_capture_id": payload.get("batch_capture_id"),
                    "event_id": event.get("event_id"),
                    "city": city,
                    "target_date": target_date,
                }
            )
            checkpoint = materialize_full_ladder_checkpoint(
                material_rows,
                city=city,
                target_date=target_date,
                event_id=str(event.get("event_id") or event.get("event_slug") or ""),
                checkpoint_ts_utc=published_at,
                feature_book_snapshot_id=feature_book_snapshot_id,
                horizon_days=1,
            )
            checkpoint.update(
                {
                    "available_at_utc": published_at or None,
                    "event_time_pit_scorable": bool(exact),
                    "clock_lineage_status": (
                        "collector_exact_market_books_clock"
                        if exact
                        else "missing_or_incomplete_market_books_clock"
                    ),
                    "clock_lineage_blockers": (
                        []
                        if exact
                        else [
                            "missing_market_book_batch"
                            if book_path is None
                            else "missing_or_inexact_referenced_book"
                        ]
                    ),
                    "source_path": str(ladder_path),
                    "source_book_path": str(book_path) if book_path else None,
                    "source_contract": "canonical_market_books_v1",
                    "probabilities": {
                        str(row["label"]): row["normalized_market_probability"]
                        for row in checkpoint["rung_manifest"]
                    },
                }
            )
            checkpoints.append(checkpoint)
    return sorted(checkpoints, key=lambda item: str(item.get("checkpoint_ts_utc") or ""))


def _probability_markout(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    if not before or not after:
        return {"status": "missing_checkpoint", "total_variation": None, "mean_rung_shift": None}
    if not before.get("market_distribution_complete") or not after.get("market_distribution_complete"):
        return {"status": "incomplete_market_distribution", "total_variation": None, "mean_rung_shift": None}
    left = dict(before.get("probabilities") or {})
    right = dict(after.get("probabilities") or {})
    if list(left) != list(right):
        return {"status": "ladder_changed", "total_variation": None, "mean_rung_shift": None}
    labels = list(left)
    total_variation = 0.5 * sum(abs(float(right[key]) - float(left[key])) for key in labels)
    mean_before = sum(index * float(left[key]) for index, key in enumerate(labels))
    mean_after = sum(index * float(right[key]) for index, key in enumerate(labels))
    return {
        "status": "scoreable",
        "total_variation": total_variation,
        "mean_rung_shift": mean_after - mean_before,
    }


def attach_market_evidence(
    events: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in events:
        event = dict(source)
        event_time = parse_utc(event["event_available_at_utc"], field="event_available_at_utc")
        eligible = [
            item
            for item in checkpoints
            if item.get("city") == event["city"] and item.get("target_date") == event["target_date"]
            # In-memory callers predating v3 may omit the flag; disk loaders
            # always materialize it explicitly for both new and legacy rows.
            and item.get("event_time_pit_scorable") is not False
        ]
        pre_candidates = [
            item
            for item in eligible
            if parse_utc(item.get("available_at_utc"), field="available_at_utc") <= event_time
        ]
        post_candidates = [
            item
            for item in eligible
            if event_time
            <= parse_utc(item.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
            <= parse_utc(item.get("available_at_utc"), field="available_at_utc")
        ]
        pre = max(pre_candidates, key=lambda item: item["checkpoint_ts_utc"]) if pre_candidates else None
        post = min(post_candidates, key=lambda item: item["checkpoint_ts_utc"]) if post_candidates else None
        event["pre_book_snapshot_id"] = pre.get("feature_book_snapshot_id") if pre else None
        event["post_book_snapshot_id"] = post.get("feature_book_snapshot_id") if post else None
        event["post_book_delay_minutes"] = (
            (
                parse_utc(post["checkpoint_ts_utc"], field="checkpoint_ts_utc") - event_time
            ).total_seconds()
            / 60.0
            if post
            else None
        )
        event["post_book_available_delay_minutes"] = (
            (
                parse_utc(post["available_at_utc"], field="available_at_utc")
                - event_time
            ).total_seconds()
            / 60.0
            if post
            else None
        )
        immediate = _probability_markout(pre, post)
        event["immediate_market_status"] = immediate["status"]
        event["immediate_total_variation"] = immediate["total_variation"]
        event["immediate_mean_rung_shift"] = immediate["mean_rung_shift"]
        for minutes in MARKOUT_MINUTES:
            target = event_time + timedelta(minutes=minutes)
            post_time = (
                parse_utc(post.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
                if post
                else None
            )
            if post_time is None:
                event[f"markout_{minutes}m_status"] = "missing_post_checkpoint"
                event[f"markout_{minutes}m_snapshot_id"] = None
                event[f"markout_{minutes}m_total_variation"] = None
                event[f"markout_{minutes}m_mean_rung_shift"] = None
                continue
            if post_time > target:
                event[f"markout_{minutes}m_status"] = (
                    "post_checkpoint_after_markout_horizon"
                )
                event[f"markout_{minutes}m_snapshot_id"] = None
                event[f"markout_{minutes}m_total_variation"] = None
                event[f"markout_{minutes}m_mean_rung_shift"] = None
                continue
            later = [
                item
                for item in eligible
                if target
                <= parse_utc(item.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
                <= parse_utc(item.get("available_at_utc"), field="available_at_utc")
                and parse_utc(
                    item.get("checkpoint_ts_utc"), field="checkpoint_ts_utc"
                )
                > post_time
            ]
            mark = min(later, key=lambda item: item["checkpoint_ts_utc"]) if later else None
            values = _probability_markout(post, mark)
            event[f"markout_{minutes}m_status"] = values["status"]
            event[f"markout_{minutes}m_snapshot_id"] = (
                mark.get("feature_book_snapshot_id") if mark else None
            )
            event[f"markout_{minutes}m_total_variation"] = values["total_variation"]
            event[f"markout_{minutes}m_mean_rung_shift"] = values["mean_rung_shift"]
        result.append(event)
    return result


def directional_repricing_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Measure whether signed forecast revisions lead signed ladder repricing.

    This is a short-horizon mechanism target, deliberately separate from final
    settlement probability.  Positive values mean the market ladder moved in
    the direction implied by the forecast revision.
    """
    horizons: list[tuple[str, str, str]] = [
        ("immediate", "immediate_market_status", "immediate_mean_rung_shift"),
        *[
            (f"{minutes}m", f"markout_{minutes}m_status", f"markout_{minutes}m_mean_rung_shift")
            for minutes in MARKOUT_MINUTES
        ],
    ]
    output: list[dict[str, Any]] = []
    scopes = {
        "all_d1_events": events,
        "legacy_provider_run_development": [
            event
            for event in events
            if event.get("event_class") == "legacy_provider_run_earliest_observed"
        ],
        "forward_provider_run_first_seen": [
            event
            for event in events
            if event.get("event_class") == "forward_provider_run_first_seen"
        ],
        "primary_d1_18_24": [
            event for event in events if event.get("checkpoint_policy") == "D-1_18_24"
        ],
    }
    for scope, scoped in scopes.items():
        for revision_field in (
            "model_revision_f",
            "consensus_median_revision_f",
            "assigned_model_revision_f",
        ):
            for horizon, status_field, shift_field in horizons:
                rows = []
                for event in scoped:
                    revision = event.get(revision_field)
                    shift = event.get(shift_field)
                    if event.get(status_field) != "scoreable" or revision is None or shift is None:
                        continue
                    revision_value = float(revision)
                    if abs(revision_value) <= 1e-12:
                        continue
                    shift_value = float(shift)
                    rows.append(
                        {
                            "target_date": str(event.get("target_date") or ""),
                            "revision": revision_value,
                            "shift": shift_value,
                            "directional_shift": shift_value if revision_value > 0 else -shift_value,
                        }
                    )
                revisions = [row["revision"] for row in rows]
                shifts = [row["shift"] for row in rows]
                directional = [row["directional_shift"] for row in rows]
                denominator = sum(value * value for value in revisions)
                output.append(
                    {
                        "scope": scope,
                        "revision_field": revision_field,
                        "horizon": horizon,
                        "events": len(rows),
                        "target_dates": len({row["target_date"] for row in rows}),
                        "direction_agreement_rate": (
                            sum(value > 0 for value in directional) / len(directional)
                            if directional
                            else None
                        ),
                        "mean_directional_rung_shift": (
                            sum(directional) / len(directional) if directional else None
                        ),
                        "median_directional_rung_shift": (
                            sorted(directional)[len(directional) // 2] if directional else None
                        ),
                        "rung_shift_per_revision_f": (
                            sum(left * right for left, right in zip(revisions, shifts)) / denominator
                            if denominator
                            else None
                        ),
                    }
                )
    return output


def market_transition_repricing_summary(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Score each independent book transition once.

    Several provider runs can arrive between two book checkpoints.  Their
    rolling consensus-mean revisions telescope, so the interval signal is the
    sum of those revisions; the same market move must not be attributed once
    to every provider delivery.
    """
    horizons: list[tuple[str, str, str, str | None]] = [
        (
            "immediate",
            "immediate_market_status",
            "immediate_mean_rung_shift",
            None,
        ),
        *[
            (
                f"{minutes}m",
                f"markout_{minutes}m_status",
                f"markout_{minutes}m_mean_rung_shift",
                f"markout_{minutes}m_snapshot_id",
            )
            for minutes in MARKOUT_MINUTES
        ],
    ]
    output: list[dict[str, Any]] = []
    for horizon, status_field, shift_field, mark_field in horizons:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for event in events:
            if event.get(status_field) != "scoreable":
                continue
            transition_key = (
                str(event.get("event_class") or ""),
                str(event.get("city") or ""),
                str(event.get("target_date") or ""),
                str(event.get("pre_book_snapshot_id") or ""),
                str(event.get("post_book_snapshot_id") or ""),
                str(event.get(mark_field) or "") if mark_field else "",
            )
            grouped.setdefault(transition_key, []).append(event)
        transitions: list[dict[str, Any]] = []
        for key, members in grouped.items():
            revisions = [
                float(event["consensus_mean_revision_f"])
                for event in members
                if event.get("consensus_mean_revision_f") is not None
            ]
            if not revisions:
                continue
            net_revision = sum(revisions)
            shift = members[0].get(shift_field)
            if abs(net_revision) <= 1e-12 or shift is None:
                continue
            shift_value = float(shift)
            provider_signs = {
                1 if float(event["model_revision_f"]) > 0 else -1
                for event in members
                if event.get("model_revision_f") is not None
                and abs(float(event["model_revision_f"])) > 1e-12
            }
            transitions.append(
                {
                    "event_class": key[0],
                    "target_date": key[2],
                    "event_rows": len(members),
                    "conflicting_provider_directions": len(provider_signs) > 1,
                    "net_consensus_mean_revision_f": net_revision,
                    "market_rung_shift": shift_value,
                    "directional_shift": (
                        shift_value if net_revision > 0 else -shift_value
                    ),
                }
            )
        for scope, scoped in (
            ("all", transitions),
            (
                "legacy_development",
                [
                    row
                    for row in transitions
                    if row["event_class"] == "legacy_provider_run_earliest_observed"
                ],
            ),
            (
                "forward_collector_exact",
                [
                    row
                    for row in transitions
                    if row["event_class"] == "forward_provider_run_first_seen"
                ],
            ),
        ):
            directional = [float(row["directional_shift"]) for row in scoped]
            output.append(
                {
                    "scope": scope,
                    "horizon": horizon,
                    "event_rows": sum(int(row["event_rows"]) for row in scoped),
                    "independent_market_transitions": len(scoped),
                    "single_forecast_event_transitions": sum(
                        int(row["event_rows"]) == 1 for row in scoped
                    ),
                    "conflicting_provider_direction_transitions": sum(
                        bool(row["conflicting_provider_directions"])
                        for row in scoped
                    ),
                    "target_dates": len(
                        {str(row["target_date"]) for row in scoped}
                    ),
                    "direction_agreement_rate": (
                        sum(value > 0 for value in directional) / len(directional)
                        if directional
                        else None
                    ),
                    "mean_directional_rung_shift": (
                        mean(directional) if directional else None
                    ),
                    "median_directional_rung_shift": (
                        median(directional) if directional else None
                    ),
                }
            )
    return output


def load_settled_city_dates(
    path: Path,
    target_keys: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    if not target_keys:
        return set()
    target_dates = sorted({target_date for _, target_date in target_keys})
    placeholders = ",".join("?" for _ in target_dates)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        rows = connection.execute(
            f"""
            SELECT city, target_date
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
              AND target_date IN ({placeholders})
            GROUP BY city, target_date
            HAVING SUM(CASE WHEN final_price >= 0.99 THEN 1 ELSE 0 END) = 1
            """,
            target_dates,
        ).fetchall()
    finally:
        connection.close()
    return {(str(city), str(target_date)) for city, target_date in rows}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(summary: dict[str, Any]) -> str:
    signal = summary["signal_funnel"]
    evidence = summary["evidence_funnel"]
    impact = summary["lineage_impact"]
    classes = summary["event_classes"]
    markout_statuses = summary["market_markout_status_counts"]
    transition_rows = [
        row
        for row in summary.get("market_transition_repricing", [])
        if row["scope"] in ("legacy_development", "forward_collector_exact")
    ]
    transition_table = [
        "| {scope} | {horizon} | {events} | {transitions} | {single} | {conflicts} | {dates} | {agreement} | {shift} |".format(
            scope=row["scope"],
            horizon=row["horizon"],
            events=row["event_rows"],
            transitions=row["independent_market_transitions"],
            single=row["single_forecast_event_transitions"],
            conflicts=row["conflicting_provider_direction_transitions"],
            dates=row["target_dates"],
            agreement=(
                f"{row['direction_agreement_rate']:.1%}"
                if row["direction_agreement_rate"] is not None
                else "NA"
            ),
            shift=(
                f"{row['mean_directional_rung_shift']:+.4f}"
                if row["mean_directional_rung_shift"] is not None
                else "NA"
            ),
        )
        for row in transition_rows
    ]
    directional = [
        row
        for row in summary.get("directional_repricing", [])
        if row["revision_field"] == "model_revision_f"
        and row["scope"]
        in (
            "legacy_provider_run_development",
            "forward_provider_run_first_seen",
        )
    ]
    directional_rows = [
        "| {scope} | {horizon} | {events} | {dates} | {agreement} | {shift} |".format(
            scope=row["scope"],
            horizon=row["horizon"],
            events=row["events"],
            dates=row["target_dates"],
            agreement=(
                f"{row['direction_agreement_rate']:.1%}"
                if row["direction_agreement_rate"] is not None
                else "NA"
            ),
            shift=(
                f"{row['mean_directional_rung_shift']:+.4f}"
                if row["mean_directional_rung_shift"] is not None
                else "NA"
            ),
        )
        for row in directional
    ]
    return "\n".join(
        [
            "# D-1 provider-run first-seen × market repricing",
            "",
            "weather-only:",
            "significance=not_run_clean_probability_dataset_not_materialized",
            "calibration=W0_reference_unchanged_W1_training_pending",
            "pooled_baseline=retained_negative_control",
            "forward=clean_development_only_not_frozen",
            "",
            "market residual:",
            "baseline=same-event complete normalized full ladder",
            "forward=collector_exact_repricing_development_low_independent_dates",
            "execution=not_run_no_probability_gate",
            "",
            "production:",
            "live_action=none",
            "orders_changed=0",
            "",
            "## 数据快照",
            "",
            f"- forecast rows={impact['raw_forecast_rows']}，unique provider runs={signal['unique_provider_run_keys']}。",
            f"- collector observed window={impact['affected_window_start_utc']}..{impact['affected_window_end_utc']}。",
            f"- market checkpoints={evidence['market_checkpoints']}；complete={evidence['complete_market_checkpoints']}。",
            f"- settlement-complete revision events={signal['settlement_complete_events']}；其余保持 unsettled coverage，missing_bracket=0。",
            "",
            "## 先修的 lineage 根因",
            "",
            f"旧 journal 有 unique provider runs={impact['unique_provider_run_keys']}；其中 multi-delivery={impact['multi_delivery_provider_run_keys']}，first_seen 漂移={impact['provider_run_keys_with_first_seen_drift']}；重复轮询 rows={impact['duplicate_delivery_rows']}。",
            f"same-run content revision rows={impact['content_revision_rows']}，其中 forecast delta=0 的伪 revision={impact['zero_delta_content_revision_rows']}。根因是旧 state 每轮重置 run first_seen，同时把含 provider 动态元数据的 raw payload hash 当成 forecast content identity。",
            "v3 分开保存 provider-run first_seen 与 same-run content first_seen；逻辑 content hash 只覆盖 target-date 时间温度序列，raw payload hash 仍 append-only 保留审计。旧 JSONL 不重写，只按 earliest-observed 进入 development。",
            "",
            "## 固定研究问题",
            "",
            "在 D-1 单 provider 新 run 的 first available clock 上，比较事件前最后一份完整 ladder、事件后第一份完整 ladder和 5/10/30/60/90m markout。多模型并非原子发布，因此 rolling as-of consensus 只在该 provider 更新时改变；complete same-run batch 仅保留为覆盖诊断，不再充当市场可观察事件。",
            "",
            "静态 forecast level、revision event 和 market residual 分三层：weather-only challenger 不读取市场；revision 只做连续 feature；M2/M3 只在同 rows、同 labels、同 feature-book 时钟下与 M0 比。",
            "正式 weather score 的主 checkpoint 固定为当地 target 前一日 18:00–24:00 的首个 complete batch（`D-1_18_24`）；12:00–18:00 只作 secondary。revision markout 可保留全部 D-1 events，但不得替代主 checkpoint proper score。",
            "",
            "## Revision → market repricing 机制指标",
            "",
            "| scope | horizon | events | dates | direction agreement | mean directional rung shift |",
            "|---|---:|---:|---:|---:|---:|",
            *directional_rows,
            "",
            "该表检验 forecast revision 后市场是否沿同方向移动，是短持策略目标；它不是 settlement accuracy，也不是 executable bid/ask PnL。",
            "",
            "## 独立盘口变化（主口径）",
            "",
            "| scope | horizon | event rows | independent transitions | single-event | direction conflicts | dates | agreement | mean directional shift |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            *transition_table,
            "",
            "主口径把同两个盘口 checkpoint 之间到达的所有 provider updates 合并，使用 rolling consensus mean 的净 revision，并且每次盘口变化只评分一次。raw event-row 表只保留为诊断，不能作 causal/alpha 结论。",
            "",
            "## 首轮双漏斗",
            "",
            "| signal funnel | count |",
            "|---|---:|",
            *[f"| {key} | {value} |" for key, value in signal.items()],
            "",
            "| evidence funnel | count |",
            "|---|---:|",
            *[f"| {key} | {value} |" for key, value in evidence.items()],
            "",
            f"markout status：`{json.dumps(markout_statuses, ensure_ascii=False, sort_keys=True)}`。post checkpoint 本身晚于目标 horizon 时明确 blocked，不再拿同一 post snapshot 自比并记成 0。",
            "",
            f"event classes：`{json.dumps(classes, ensure_ascii=False, sort_keys=True)}`。旧 v2 journal 只能重建 `legacy_provider_run_earliest_observed` development；v3 的 `forward_provider_run_first_seen` 表示 collector-exact lineage，但 freeze 前仍属于 clean development，不能事后改称 untouched forward。",
            "",
            "## 下一阶段与冻结规则",
            "",
            "1. collector v3 已部署并保持 model×city×target×run 的 first-seen；canonical market_books 五分钟盘口已接入本 runner，旧 JSONL 与旧 snapshot 只保留为 development evidence。",
            "2. 先累计 complete D-1 run events、完整 pre/post ladders与 settlement；第一段 clean rows 明确作为 development，不冒充 forward。",
            "3. W0 只作锁定 legacy reference；W1 在 clean development 的 inner train/validation 中选择 revision/spread/lead-age、层级收缩和 tail，先跑出 weather-only 结果再决定是否冻结。",
            "4. W1 评审后，在同一 development rows 上比较 M0/M1/M2/M3并选择 residual 正则；两条线都出结果后才生成 freeze artifact。只有 freeze timestamp 之后的新日期进入 untouched forward，且 M2/M3 必须在其 target-date block bootstrap 的 logloss/RPS/calibration 上优于 M0，才进入 ask/fee/depth EV。",
            "5. 当前不做 ROI、maker、selected price band、城市名单或 live 动作。",
            "",
            "## 8 环与结论",
            "",
            "本轮覆盖 lineage、signal/evidence coverage、market checkpoint contract 与 collector-exact repricing 描述统计；独立 target dates 只有3–4个，尚未进入 target-date block 推断。概率模型、execution、容量、fills 与组合相关性均未覆盖。",
            "",
            "结论：`inconclusive / clean-development-low-independent-dates`。采集与研究 join 已跑通，但当前 revision 对5–90分钟盘口方向没有稳定领先；继续积累并训练 W1/repricing head，不改 live。",
            "",
        ]
    )


def run_study(
    *,
    capture_dir: Path = DEFAULT_CAPTURE_DIR,
    snapshot_dir: Path | None = None,
    snapshot_dirs: Iterable[Path] | None = None,
    market_books_root: Path | None = None,
    market_ladder_snapshot_root: Path | None = None,
    db: Path = DEFAULT_DB,
    output_dir: Path = DEFAULT_OUT,
    report: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    rows = read_jsonl(capture_dir / "forecast_run_rows.jsonl")
    batches = read_jsonl(capture_dir / "forecast_batches.jsonl")
    blockers = read_jsonl(capture_dir / "blockers.jsonl")
    events, provider_summary = build_provider_run_events(rows)
    complete_batch_events, complete_batch_summary = build_revision_events(rows, batches)
    resolved_snapshot_dirs = list(
        snapshot_dirs
        or ([snapshot_dir] if snapshot_dir is not None else default_snapshot_dirs())
    )
    resolved_books_root, resolved_ladder_root = default_canonical_market_roots()
    resolved_books_root = market_books_root or resolved_books_root
    resolved_ladder_root = market_ladder_snapshot_root or resolved_ladder_root
    checkpoints = load_market_checkpoints(resolved_snapshot_dirs, events)
    checkpoints.extend(
        load_canonical_market_checkpoints(
            books_root=resolved_books_root,
            ladder_root=resolved_ladder_root,
            events=events,
        )
    )
    checkpoint_dedup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for checkpoint in checkpoints:
        key = (
            str(checkpoint.get("city") or ""),
            str(checkpoint.get("target_date") or ""),
            str(checkpoint.get("checkpoint_ts_utc") or ""),
            str(checkpoint.get("ladder_hash") or ""),
        )
        checkpoint_dedup[key] = checkpoint
    checkpoints = sorted(
        checkpoint_dedup.values(),
        key=lambda item: str(item.get("checkpoint_ts_utc") or ""),
    )
    scored_events = attach_market_evidence(events, checkpoints)
    d1_events = [event for event in scored_events if event["horizon_days_local"] == 1]
    settled_city_dates = load_settled_city_dates(
        db,
        {(event["city"], event["target_date"]) for event in d1_events},
    )
    for event in d1_events:
        event["settlement_complete"] = (
            event["city"], event["target_date"]
        ) in settled_city_dates
    event_classes = Counter(str(event["event_class"]) for event in d1_events)
    directional_repricing = directional_repricing_summary(d1_events)
    market_transition_repricing = market_transition_repricing_summary(d1_events)
    signal_funnel = {
        **provider_summary,
        "complete_batch_material_forecast_batches": complete_batch_summary[
            "material_forecast_batches"
        ],
        "complete_batch_transition_events_diagnostic_only": len(
            complete_batch_events
        ),
        "d1_provider_run_transition_events": len(d1_events),
        "primary_d1_18_24_transition_events": sum(
            event["checkpoint_policy"] == "D-1_18_24" for event in d1_events
        ),
        "forward_provider_run_first_seen_events": event_classes[
            "forward_provider_run_first_seen"
        ],
        "settlement_complete_events": sum(event["settlement_complete"] for event in d1_events),
        "probability_scoreable_events": 0,
    }
    evidence_funnel = {
        "market_checkpoints": len(checkpoints),
        "legacy_paper_market_checkpoints": sum(
            item.get("source_contract") == "legacy_paper_snapshot" for item in checkpoints
        ),
        "canonical_market_book_checkpoints": sum(
            item.get("source_contract") == "canonical_market_books_v1" for item in checkpoints
        ),
        "event_time_clock_exact_market_checkpoints": sum(
            item.get("event_time_pit_scorable") is True for item in checkpoints
        ),
        "legacy_or_incomplete_clock_market_checkpoints": sum(
            item.get("event_time_pit_scorable") is not True for item in checkpoints
        ),
        "complete_market_checkpoints": sum(
            item.get("rung_completeness") and item.get("market_distribution_complete")
            for item in checkpoints
        ),
        "revision_events_with_pre_book": sum(bool(event.get("pre_book_snapshot_id")) for event in d1_events),
        "revision_events_with_post_book": sum(bool(event.get("post_book_snapshot_id")) for event in d1_events),
        "revision_events_immediate_scoreable": sum(
            event.get("immediate_market_status") == "scoreable" for event in d1_events
        ),
        **{
            f"revision_events_{minutes}m_markout_scoreable": sum(
                event.get(f"markout_{minutes}m_status") == "scoreable" for event in d1_events
            )
            for minutes in MARKOUT_MINUTES
        },
        "executable": 0,
        "actual_fills": 0,
    }
    market_markout_status_counts = {
        "immediate": dict(
            sorted(Counter(str(event.get("immediate_market_status")) for event in d1_events).items())
        ),
        **{
            f"{minutes}m": dict(
                sorted(
                    Counter(
                        str(event.get(f"markout_{minutes}m_status"))
                        for event in d1_events
                    ).items()
                )
            )
            for minutes in MARKOUT_MINUTES
        },
    }
    summary = {
        "schema_version": "d1_forecast_revision_market_repricing_research_v2",
        "generated_at_utc": _utc_text(datetime.now(timezone.utc)),
        "denominator_scope": {
            "event_grain": "provider model run first-seen × city × target_date",
            "forecast_capture_dir": str(capture_dir),
            "snapshot_dirs": [str(path) for path in resolved_snapshot_dirs],
            "market_books_root": str(resolved_books_root),
            "market_ladder_snapshot_root": str(resolved_ladder_root),
            "db_realpath": str(db.resolve()),
            "db_device": db.stat().st_dev,
            "db_inode": db.stat().st_ino,
            "target_dates": sorted(
                {str(event.get("target_date") or "") for event in d1_events}
            ),
            "cities": sorted({str(event.get("city") or "") for event in d1_events}),
        },
        "lineage_impact": lineage_impact(rows),
        "signal_funnel": signal_funnel,
        "evidence_funnel": evidence_funnel,
        "market_markout_status_counts": market_markout_status_counts,
        "event_classes": dict(sorted(event_classes.items())),
        "directional_repricing": directional_repricing,
        "market_transition_repricing": market_transition_repricing,
        "blocker_rows": len(blockers),
        "conclusion": "inconclusive_clean_development_low_independent_dates",
        "production": {"live_action": "none", "orders_changed": 0},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "revision_events.csv", d1_events)
    write_csv(output_dir / "market_checkpoints.csv", checkpoints)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report.write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        action="append",
        dest="snapshot_dirs",
        help="Repeat for hot/archive roots; defaults resolve from production.yaml",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--market-books-root", type=Path)
    parser.add_argument("--market-ladder-snapshot-root", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    run_study(
        capture_dir=args.capture_dir,
        snapshot_dirs=args.snapshot_dirs,
        market_books_root=args.market_books_root,
        market_ladder_snapshot_root=args.market_ladder_snapshot_root,
        db=args.db,
        output_dir=args.output_dir,
        report=args.report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
