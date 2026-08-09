#!/usr/bin/env python3
"""Link Helsinki FMI/METAR first-seen events to reconstructed WS books."""

from __future__ import annotations

import argparse
import bisect
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.ws_incremental_book import (  # noqa: E402
    BookReconstructionError,
    IncrementalBookReconstructor,
)
from src.strategies.runtime.production import load_production_spec  # noqa: E402


SCHEMA_VERSION = "helsinki_fmi_metar_ws_linkage_v1"
MARKOUT_HORIZONS_SEC = (10, 30, 60)
TRANSPORT_PROOF_GRACE_SEC = 30


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(row, dict):
                yield row


def _load_epochs(root: Path) -> list[dict[str, Any]]:
    epochs: list[dict[str, Any]] = []
    for path in sorted((root / "subscription_epochs").glob("*.jsonl")):
        for row in _iter_jsonl(path):
            started = _timestamp(row.get("started_at_utc"))
            if started is None:
                continue
            token_rows = {
                str(token): metadata
                for token, metadata in (row.get("token_rows") or {}).items()
                if metadata.get("city") == "Helsinki"
            }
            epochs.append({**row, "_started": started, "_helsinki_tokens": token_rows})
    return sorted(epochs, key=lambda row: row["_started"])


def _load_events(
    source_event_path: Path, fmi_path: Path, *, target_date: str
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    declarations = (
        ("FMI", fmi_path, "fmi", "entry_eligible"),
        ("METAR", source_event_path, "aviationweather_metar", "correction_only"),
    )
    for kind, path, expected_source, role in declarations:
        for row in _iter_jsonl(path):
            if row.get("city") != "Helsinki" or row.get("target_date") != target_date:
                continue
            if row.get("event_role") != "new_content":
                continue
            if row.get("information_event_status") != "material":
                continue
            if kind == "FMI" and row.get("source") != expected_source:
                continue
            if kind == "METAR" and row.get("station") != "EFHK":
                continue
            content_key = str(row.get("content_key") or "")
            identity = (kind, content_key)
            if not content_key or identity in seen:
                continue
            seen.add(identity)
            first_seen = str(row.get("first_seen_at_utc") or "")
            first_seen_ts = _timestamp(first_seen)
            if first_seen_ts is None:
                continue
            events.append(
                {
                    "event_source": kind,
                    "source_role": role,
                    "content_key": content_key,
                    "information_event_id": row.get("information_event_id"),
                    "report_ts_utc": row.get("source_event_ts_utc")
                    or row.get("observation_time_utc"),
                    "first_seen_at_utc": first_seen,
                    "_first_seen": first_seen_ts,
                    "temp_c": row.get("temp_c"),
                }
            )
    return sorted(events, key=lambda row: row["_first_seen"])


def _physical_frame_paths(root: Path, start_ts: float, end_ts: float) -> list[Path]:
    """Resolve UTC storage shards from event clocks, never business target_date."""

    start_day = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
    paths: list[Path] = []
    current = start_day
    while current <= end_day:
        paths.extend(sorted((root / current.isoformat()).glob("*.jsonl*")))
        current += timedelta(days=1)
    return paths


def _load_frames(root: Path, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in _physical_frame_paths(root, start_ts, end_ts):
        for row in _iter_jsonl(path):
            received = _timestamp(row.get("received_at_utc"))
            if (
                received is None
                or received < start_ts
                or received > end_ts
                or not row.get("subscription_epoch_id")
            ):
                continue
            identity = hashlib.sha256(
                json.dumps(
                    {
                        "epoch": row.get("subscription_epoch_id"),
                        "received_ns": row.get("received_at_ns"),
                        "message": row.get("message"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if identity in seen:
                continue
            seen.add(identity)
            frames.append({**row, "_received": received})
    return sorted(frames, key=lambda row: (row["_received"], row.get("received_at_ns") or 0))


def _build_timelines(
    epochs: list[dict[str, Any]], frames: list[dict[str, Any]]
) -> tuple[
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[str, list[float]],
    dict[str, int],
]:
    epoch_by_id = {str(row["subscription_epoch_id"]): row for row in epochs}
    engines: dict[str, IncrementalBookReconstructor] = {}
    timelines: dict[tuple[str, str], list[dict[str, Any]]] = {}
    epoch_frame_times: dict[str, list[float]] = {}
    errors = 0
    for frame in frames:
        epoch_id = str(frame["subscription_epoch_id"])
        declaration = epoch_by_id.get(epoch_id)
        if declaration is None:
            continue
        epoch_frame_times.setdefault(epoch_id, []).append(frame["_received"])
        engine = engines.get(epoch_id)
        if engine is None:
            engine = IncrementalBookReconstructor()
            engine.activate_epoch(epoch_id, declaration.get("token_ids") or ())
            engines[epoch_id] = engine
        try:
            updated = engine.apply_envelope(frame)
        except BookReconstructionError:
            errors += 1
            continue
        for token_id in updated:
            metadata = declaration["_helsinki_tokens"].get(token_id)
            if not metadata:
                continue
            try:
                snapshot = engine.snapshot(
                    token_id,
                    observed_at_utc=str(frame["received_at_utc"]),
                    requested_shares=5.0,
                )
            except BookReconstructionError:
                continue
            timelines.setdefault((epoch_id, token_id), []).append(
                {**snapshot.to_dict(), "_observed": frame["_received"]}
            )
    for values in timelines.values():
        values.sort(key=lambda row: row["_observed"])
    return timelines, epoch_frame_times, {
        "reconstruction_errors": errors,
        "timeline_count": len(timelines),
    }


def _asof(values: list[dict[str, Any]], timestamp: float) -> dict[str, Any] | None:
    times = [row["_observed"] for row in values]
    index = bisect.bisect_right(times, timestamp) - 1
    return values[index] if index >= 0 else None


def _mid(row: dict[str, Any] | None) -> float | None:
    if not row or row.get("best_bid") is None or row.get("best_ask") is None:
        return None
    return (float(row["best_bid"]) + float(row["best_ask"])) / 2.0


def _transport_covers_horizon(
    *,
    epoch_id: str,
    horizon_ts: float,
    next_epoch_started: float | None,
    epoch_frame_times: dict[str, list[float]],
) -> bool:
    """Require a nearby later frame proving capture crossed the markout clock."""

    if next_epoch_started is not None and next_epoch_started <= horizon_ts:
        return False
    proof_deadline = horizon_ts + TRANSPORT_PROOF_GRACE_SEC
    return any(
        horizon_ts <= ts <= proof_deadline
        for ts in epoch_frame_times.get(epoch_id, ())
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    epochs = _load_epochs(args.ws_root)
    events = _load_events(args.source_event_path, args.fmi_path, target_date=args.target_date)
    epoch_times = [row["_started"] for row in epochs]
    active_indexes = [
        index
        for event in events
        if (index := bisect.bisect_right(epoch_times, event["_first_seen"]) - 1) >= 0
    ]
    if active_indexes:
        frame_start = min(epochs[index]["_started"] for index in active_indexes)
        frame_end = (
            max(event["_first_seen"] for event in events)
            + max(MARKOUT_HORIZONS_SEC)
            + TRANSPORT_PROOF_GRACE_SEC
        )
        frames = _load_frames(args.ws_root, frame_start, frame_end)
    else:
        frames = []
    timelines, epoch_frame_times, reconstruction = _build_timelines(epochs, frames)
    active_event_counts = {"FMI": 0, "METAR": 0}
    for event in events:
        index = bisect.bisect_right(epoch_times, event["_first_seen"]) - 1
        if index >= 0 and epochs[index]["_helsinki_tokens"]:
            active_event_counts[event["event_source"]] += 1
    rows: list[dict[str, Any]] = []
    for event in events:
        index = bisect.bisect_right(epoch_times, event["_first_seen"]) - 1
        if index < 0:
            continue
        epoch = epochs[index]
        epoch_id = str(epoch["subscription_epoch_id"])
        next_epoch_started = epochs[index + 1]["_started"] if index + 1 < len(epochs) else None
        for token_id, metadata in epoch["_helsinki_tokens"].items():
            if metadata.get("outcome") != "no":
                continue
            timeline = timelines.get((epoch_id, token_id), [])
            base = _asof(timeline, event["_first_seen"])
            if base is None:
                continue
            row = {
                **{key: value for key, value in event.items() if not key.startswith("_")},
                "subscription_epoch_id": epoch["subscription_epoch_id"],
                "bracket": metadata.get("bracket"),
                "condition_id": metadata.get("condition_id"),
                "token_id": token_id,
                "feature_book_snapshot_id": base["snapshot_id"],
                "book_state_age_sec": event["_first_seen"] - base["_observed"],
                "no_best_bid": base["best_bid"],
                "no_best_ask": base["best_ask"],
                "no_sell_proceeds_5": base["sell_proceeds"],
                "no_buy_cost_5": base["buy_cost"],
                "depth_status": base["depth_status"],
            }
            initial_mid = _mid(base)
            for seconds in MARKOUT_HORIZONS_SEC:
                horizon_ts = event["_first_seen"] + seconds
                covered = _transport_covers_horizon(
                    epoch_id=epoch_id,
                    horizon_ts=horizon_ts,
                    next_epoch_started=next_epoch_started,
                    epoch_frame_times=epoch_frame_times,
                )
                later = _asof(timeline, horizon_ts) if covered else None
                later_mid = _mid(later)
                row[f"markout_coverage_{seconds}s"] = (
                    "transport_verified" if covered else "transport_not_verified"
                )
                row[f"no_mid_change_{seconds}s"] = (
                    later_mid - initial_mid
                    if initial_mid is not None and later_mid is not None
                    else None
                )
            rows.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    row_path = args.output_dir / "event_bracket_linkage.csv"
    fieldnames = sorted({key for row in rows for key in row})
    with row_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    source_summary: dict[str, Any] = {}
    for source in ("FMI", "METAR"):
        source_rows = [row for row in rows if row["event_source"] == source]
        window: dict[str, Any] = {}
        for seconds in MARKOUT_HORIZONS_SEC:
            values = [
                float(row[f"no_mid_change_{seconds}s"])
                for row in source_rows
                if row[f"no_mid_change_{seconds}s"] not in (None, "")
            ]
            window[f"{seconds}s"] = {
                "rows": len(values),
                "changed_rows": sum(abs(value) > 1e-12 for value in values),
                "mean_signed_change": statistics.mean(values) if values else None,
                "mean_absolute_change": statistics.mean(map(abs, values)) if values else None,
                "max_absolute_change": max(map(abs, values)) if values else None,
            }
        source_summary[source] = {
            "events": len({row["information_event_id"] for row in source_rows}),
            "bracket_rows": len(source_rows),
            "five_share_two_way_depth_rows": sum(
                row["depth_status"] == "five_share_executable" for row in source_rows
            ),
            "reaction": window,
        }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "target_date": args.target_date,
        "causal_contract": {
            "FMI": "may create entry candidates",
            "METAR": "may only update an existing position and produce HOLD/EXIT",
            "exit_rule": "SELL held NO only when fee-adjusted executable bid exceeds updated P(NO)",
            "orders_submitted": 0,
        },
        "input_coverage": {
            "material_events": {
                source: len([row for row in events if row["event_source"] == source])
                for source in ("FMI", "METAR")
            },
            "ws_frames": len(frames),
            "subscription_epochs": len(epochs),
            **reconstruction,
        },
        "signal_funnel": {
            "unit": "material first-seen weather event",
            "material_events": {
                source: len([row for row in events if row["event_source"] == source])
                for source in ("FMI", "METAR")
            },
            "events_in_active_helsinki_subscription_epoch": active_event_counts,
            "source_role_assignment": {
                "FMI": "entry_eligible",
                "METAR": "correction_only",
            },
        },
        "evidence_funnel": {
            "unit": "event and event-bracket evidence row",
            "linked_events": {
                source: source_summary[source]["events"] for source in ("FMI", "METAR")
            },
            "linked_event_bracket_rows": {
                source: source_summary[source]["bracket_rows"]
                for source in ("FMI", "METAR")
            },
            "five_share_two_way_depth_rows": {
                source: source_summary[source]["five_share_two_way_depth_rows"]
                for source in ("FMI", "METAR")
            },
            "coverage_gaps_are_not_strategy_filters": True,
        },
        "linked": source_summary,
        "interpretation": (
            "single-day microstructure evidence only; it validates source roles and capture clocks, "
            "not profitability or model promotion"
        ),
        "outputs": {"event_bracket_linkage": str(row_path)},
        "inputs": {
            "ws_root": str(args.ws_root),
            "source_event_path": str(args.source_event_path),
            "fmi_path": str(args.fmi_path),
        },
        "producer": {
            "entrypoint": str(Path(__file__).resolve()),
            "entrypoint_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "reconstructor": str(
                (ROOT / "weather_data_feed" / "ws_incremental_book.py").resolve()
            ),
            "reconstructor_sha256": hashlib.sha256(
                (ROOT / "weather_data_feed" / "ws_incremental_book.py").read_bytes()
            ).hexdigest(),
        },
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    production = load_production_spec()
    runtime = production.data_feed_runtime_root
    parser.add_argument("--target-date", required=True)
    parser.add_argument(
        "--ws-root", type=Path, default=runtime / "market_books" / "ws_incremental"
    )
    parser.add_argument(
        "--source-event-path",
        type=Path,
        default=production.source_events_root() / "sources.jsonl",
    )
    parser.add_argument(
        "--fmi-path",
        type=Path,
        default=production.live_cross_observations_root()
        / "high_frequency_observations.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
