#!/usr/bin/env python3
"""Build automatic first-seen checkpoints and zero-execution signal candidates."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.etl.materialize_weather_event_signal_candidates import (
    attach_settlements,
    materialize_candidate_rows,
)
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_dashboard.ingest.state_checkpoints import (
    build_state_checkpoint,
    ingest_state_checkpoints,
)
from weather_data_feed import city_timezone_name
from weather_data_feed.information_events import canonical_json_hash
from weather_data_feed.observation_cache import parse_utc
from weather_feature_layer.builders import build_weather_state_frame
from weather_feature_layer.contracts import (
    PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION,
    PIT_PROVENANCE_LIVE_CAPTURE,
)
from weather_feature_layer.store import (
    EVENT_CHECKPOINT_KEY_COLUMNS,
    write_feature_frame_store,
)


STRATEGY_KEY = "first_seen_exact_bracket_residual_v1"
MODEL_ARTIFACT_PREFIX = "paper_snapshot_exact_bracket"
EVENT_FEATURE_GRAIN = "city_date_event_checkpoint"
EVENT_FEATURE_BUILDER_VERSION = "weather_first_seen_checkpoint_builder_v1"


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    source_path: str
    records: tuple[dict[str, Any], ...]


def _utc(value: Any) -> datetime | None:
    parsed = parse_utc(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_files(paths: Iterable[Path], *, allow_missing: bool) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("snapshot_*.json"))
        elif not allow_missing:
            raise FileNotFoundError(f"required paper snapshot path does not exist: {path}")
    if not files and not allow_missing:
        raise FileNotFoundError("no paper snapshot JSON files found")
    return sorted(set(files))


def load_snapshots(
    paths: Iterable[Path],
    *,
    allow_missing: bool = False,
    skip_invalid: bool = False,
    audit: dict[str, int] | None = None,
) -> list[Snapshot]:
    snapshots: list[Snapshot] = []
    for path in _json_files(paths, allow_missing=allow_missing):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            if skip_invalid:
                if audit is not None:
                    audit["invalid_snapshot_files"] = audit.get("invalid_snapshot_files", 0) + 1
                continue
            raise ValueError(f"invalid snapshot JSON: {path}") from exc
        records = payload.get("records") if isinstance(payload, Mapping) else None
        if not isinstance(records, list) or not records:
            continue
        timestamp = _utc(payload.get("ts_utc") or records[0].get("snapshot_ts_utc"))
        if timestamp is None:
            continue
        snapshots.append(
            Snapshot(
                timestamp=timestamp,
                source_path=str(path),
                records=tuple(dict(row) for row in records if isinstance(row, Mapping)),
            )
        )
    return sorted(snapshots, key=lambda item: (item.timestamp, item.source_path))


def _event_target_date(event: Mapping[str, Any]) -> str:
    target_date = str(event.get("target_date") or "")
    if target_date:
        return target_date
    available = _utc(event.get("available_at_utc"))
    city = str(event.get("city") or "")
    if available is None or not city:
        return ""
    from zoneinfo import ZoneInfo

    return available.astimezone(ZoneInfo(city_timezone_name(city))).date().isoformat()


def load_material_events(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    only_without_built_checkpoint: bool = True,
) -> list[dict[str, Any]]:
    query = """
        SELECT
          event.*,
          COALESCE(observation.target_date, forecast.target_date) AS typed_target_date
        FROM weather_information_events AS event
        LEFT JOIN weather_observation_events AS observation
          ON observation.information_event_id = event.information_event_id
        LEFT JOIN fact_forecast_hourly_curves AS forecast
          ON forecast.information_event_id = event.information_event_id
        WHERE event.available_at_utc IS NOT NULL
          AND event.pit_lineage_class <> 'late_backfill_first_seen_unknown'
          AND event.material_state_change = 1
          AND (
            ? = 0
            OR NOT EXISTS (
              SELECT 1
              FROM weather_state_checkpoints AS checkpoint
              WHERE checkpoint.trigger_event_id = event.information_event_id
                AND checkpoint.checkpoint_status = 'built'
            )
          )
        ORDER BY
          CASE WHEN EXISTS (
            SELECT 1
            FROM weather_state_checkpoints AS prior_checkpoint
            WHERE prior_checkpoint.trigger_event_id = event.information_event_id
          ) THEN 1 ELSE 0 END,
          event.available_at_utc,
          event.information_event_id
    """
    rows = []
    for source in conn.execute(query, (int(only_without_built_checkpoint),)):
        row = dict(source)
        row["target_date"] = str(row.pop("typed_target_date") or "")
        row["target_date"] = _event_target_date(row)
        if row["target_date"]:
            rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _city_date_records(snapshot: Snapshot, city: str, target_date: str) -> list[dict[str, Any]]:
    return [
        row
        for row in snapshot.records
        if str(row.get("city") or "") == city
        and str(row.get("target_date") or row.get("event_date") or "") == target_date
    ]


def _snapshot_index(
    snapshots: Iterable[Snapshot],
) -> dict[tuple[str, str], list[Snapshot]]:
    indexed: dict[tuple[str, str], list[Snapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in snapshot.records:
            key = (
                str(row.get("city") or ""),
                str(row.get("target_date") or row.get("event_date") or ""),
            )
            if all(key):
                grouped[key].append(row)
        for key, records in grouped.items():
            indexed[key].append(
                Snapshot(
                    timestamp=snapshot.timestamp,
                    source_path=snapshot.source_path,
                    records=tuple(records),
                )
            )
    return indexed


def _match_snapshots(
    snapshot_index: Mapping[tuple[str, str], list[Snapshot]],
    event: Mapping[str, Any],
    *,
    max_lag_minutes: float,
) -> tuple[Snapshot | None, Snapshot | None]:
    available = _utc(event.get("available_at_utc"))
    city = str(event.get("city") or "")
    target_date = str(event.get("target_date") or "")
    if available is None:
        return None, None
    previous = None
    post = None
    deadline = available + timedelta(minutes=max_lag_minutes)
    for snapshot in snapshot_index.get((city, target_date), []):
        if snapshot.timestamp < available:
            previous = snapshot
            continue
        if snapshot.timestamp <= deadline:
            post = snapshot
        break
    return previous, post


def _observation_rows(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    as_of: str,
) -> list[dict[str, Any]]:
    rows = []
    for source in conn.execute(
        """
        SELECT *
        FROM weather_observation_events
        WHERE city = ?
          AND target_date = ?
          AND available_at_utc <= ?
        ORDER BY available_at_utc, obs_ts_utc
        """,
        (city, target_date, as_of),
    ):
        row = dict(source)
        try:
            raw = json.loads(row.get("raw_payload") or "{}")
        except json.JSONDecodeError:
            raw = {}
        merged = {
            **raw,
            **row,
            "source": row.get("source_system"),
            "station": row.get("icao"),
            "source_report_ts_utc": row.get("source_report_ts_utc") or row.get("obs_ts_utc"),
            "current_temp_c": row.get("temp_c"),
            "temp_c": row.get("temp_c"),
            "tmpf": row.get("temp_f"),
            "dwpf": row.get("dewpoint_f"),
            "fetched_at_utc": row.get("available_at_utc") or row.get("fetched_at_utc"),
        }
        rows.append(merged)
    if not rows:
        return []
    running_max = None
    first_max_ts = None
    last_strict_ts = None
    for row in rows:
        temp = _float(row.get("temp_c"))
        if temp is not None and (running_max is None or temp > running_max):
            running_max = temp
            first_max_ts = row.get("source_report_ts_utc")
            last_strict_ts = row.get("source_report_ts_utc")
        row["running_max_c"] = running_max
        row["running_max_obs_utc"] = first_max_ts
        row["first_running_max_obs_utc"] = first_max_ts
        row["last_running_max_obs_utc"] = last_strict_ts
    return rows


def _forecast_rows(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    as_of: str,
) -> list[dict[str, Any]]:
    rows = []
    for source in conn.execute(
        """
        SELECT *
        FROM fact_forecast_hourly_curves
        WHERE city = ?
          AND target_date = ?
          AND available_at_utc <= ?
        ORDER BY available_at_utc
        """,
        (city, target_date, as_of),
    ):
        row = dict(source)
        try:
            row["hourly_curve"] = json.loads(row.get("hourly_curve_json") or "[]")
        except json.JSONDecodeError:
            row["hourly_curve"] = []
        rows.append(row)
    return rows


def _input_events(
    conn: sqlite3.Connection,
    city: str,
    as_of: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM weather_information_events
            WHERE city = ?
              AND available_at_utc <= ?
            ORDER BY available_at_utc, information_event_id
            """,
            (city, as_of),
        )
    ]


def _build_feature_ref(
    conn: sqlite3.Connection,
    event: Mapping[str, Any],
    snapshot: Snapshot,
    *,
    feature_store: Path,
) -> dict[str, Any] | None:
    city = str(event["city"])
    target_date = str(event["target_date"])
    as_of = _iso(snapshot.timestamp)
    records = _city_date_records(snapshot, city, target_date)
    observations = _observation_rows(conn, city, target_date, as_of)
    if not records or not observations:
        return None
    provenance = (
        PIT_PROVENANCE_LIVE_CAPTURE
        if event.get("pit_lineage_class") == "collector_exact"
        else PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION
    )
    frame = build_weather_state_frame(
        records,
        {"records": observations},
        forecast_curve_rows=_forecast_rows(conn, city, target_date, as_of),
        as_of_ts_utc=as_of,
        source_profile_id=str(records[0].get("source_profile_registry_class") or "paper_snapshot"),
        input_snapshot_id=snapshot.source_path,
        pit_provenance=provenance,
        builder_version=EVENT_FEATURE_BUILDER_VERSION,
    )
    if frame.empty:
        return None
    frame = frame.iloc[[0]].copy()
    frame["trigger_event_id"] = event["information_event_id"]
    frame["as_of_ts_utc"] = as_of
    metadata = dict(frame.attrs.get("feature_metadata") or {})
    metadata["feature_grain"] = EVENT_FEATURE_GRAIN
    metadata["as_of_ts_utc"] = as_of
    metadata["builder_version"] = EVENT_FEATURE_BUILDER_VERSION
    frame.attrs["feature_metadata"] = metadata
    for key, value in metadata.items():
        frame[key] = json.dumps(value, sort_keys=True) if isinstance(value, Mapping) else value
    stored = write_feature_frame_store(
        frame,
        feature_store,
        key_columns=EVENT_CHECKPOINT_KEY_COLUMNS,
    )
    return stored.row_refs[0]


def _record_by_condition(snapshot: Snapshot | None) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    return {
        str(row.get("condition_id")): row
        for row in snapshot.records
        if row.get("condition_id")
    }


def _mid(bid: Any, ask: Any, fallback: Any = None) -> float | None:
    bid_value = _float(bid)
    ask_value = _float(ask)
    if bid_value is not None and ask_value is not None:
        return (bid_value + ask_value) / 2.0
    return _float(fallback)


def _candidate_inputs(
    event: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    previous: Snapshot | None,
    post: Snapshot,
) -> list[dict[str, Any]]:
    previous_by_condition = _record_by_condition(previous)
    rows = []
    for record in _city_date_records(post, str(event["city"]), str(event["target_date"])):
        condition_id = str(record.get("condition_id") or "")
        if not condition_id:
            continue
        before = previous_by_condition.get(condition_id, {})
        p_yes_after = _float(record.get("model_prob"))
        p_yes_before = _float(before.get("model_prob"))
        market_yes = _mid(
            record.get("yes_best_bid"),
            record.get("yes_best_ask"),
            record.get("market_yes_price"),
        )
        market_yes_before = _mid(
            before.get("yes_best_bid"),
            before.get("yes_best_ask"),
            before.get("market_yes_price"),
        )
        model_name = str(record.get("model_version") or record.get("model") or "unknown")
        for side, prefix in (("BUY_YES", "yes"), ("BUY_NO", "no")):
            p_after = p_yes_after if side == "BUY_YES" or p_yes_after is None else 1.0 - p_yes_after
            p_before = p_yes_before if side == "BUY_YES" or p_yes_before is None else 1.0 - p_yes_before
            market_probability = (
                market_yes
                if side == "BUY_YES" or market_yes is None
                else 1.0 - market_yes
            )
            market_probability_before = (
                market_yes_before
                if side == "BUY_YES" or market_yes_before is None
                else 1.0 - market_yes_before
            )
            book_available = str(record.get(f"{prefix}_book_fetched_at_utc") or "")
            book_time = _utc(book_available)
            event_available = _utc(event.get("available_at_utc"))
            before_book_available = str(before.get(f"{prefix}_book_fetched_at_utc") or "")
            before_book_time = _utc(before_book_available)
            if (
                before_book_time is None
                or event_available is None
                or before_book_time > event_available
            ):
                before_book_available = ""
                market_probability_before = None
            ask = _float(record.get(f"{prefix}_best_ask"))
            blockers = []
            if checkpoint.get("checkpoint_status") != "built":
                blockers.append(str(checkpoint.get("checkpoint_blocker") or "checkpoint_not_built"))
            if p_after is None:
                blockers.append("missing_model_probability")
            if market_probability is None:
                blockers.append("missing_market_probability")
            if ask is None:
                blockers.append("missing_executable_ask")
            if book_time is None or event_available is None or book_time < event_available:
                blockers.append("missing_post_event_book")
            status = "blocked" if blockers else "scored"
            rows.append(
                {
                    "state_checkpoint_id": checkpoint["state_checkpoint_id"],
                    "strategy_key": STRATEGY_KEY,
                    "model_artifact_id": f"{MODEL_ARTIFACT_PREFIX}:{model_name}",
                    "condition_id": condition_id,
                    "market_id": record.get("market_id"),
                    "bracket": record.get("bracket"),
                    "side": side,
                    "decision_ts_utc": book_available or checkpoint["as_of_ts_utc"],
                    "book_snapshot_id": canonical_json_hash(
                        {
                            "source_path": post.source_path,
                            "condition_id": condition_id,
                            "side": side,
                            "book_available_at_utc": book_available,
                        }
                    ),
                    "book_snapshot_ts_utc": record.get("snapshot_ts_utc"),
                    "book_available_at_utc": book_available or None,
                    "pre_event_book_snapshot_id": (
                        canonical_json_hash(
                            {
                                "source_path": previous.source_path if previous else None,
                                "condition_id": condition_id,
                                "side": side,
                                "book_available_at_utc": before_book_available,
                            }
                        )
                        if before_book_available
                        else None
                    ),
                    "pre_event_book_available_at_utc": before_book_available or None,
                    "market_evidence_status": (
                        "fresh_post_event_book"
                        if "missing_post_event_book" not in blockers
                        else "missing_post_event_book"
                    ),
                    "model_probability_before": p_before,
                    "model_probability_after": p_after,
                    "market_probability": market_probability,
                    "market_probability_before": market_probability_before,
                    "market_probability_change": (
                        None
                        if market_probability is None or market_probability_before is None
                        else market_probability - market_probability_before
                    ),
                    "candidate_status": status,
                    "candidate_blocker": "|".join(blockers) or None,
                    "policy_selected": 0,
                    "first_city_day_selected": 0,
                    "unit": record.get("unit"),
                    "forecast_source": record.get("forecast_source"),
                    "forecast_max_f": record.get("forecast_max_f"),
                    "forecast_max_native": record.get("forecast_max_native"),
                    "forecast_peak_hour_local": record.get("forecast_peak_hour_local"),
                    "forecast_peak_time_local": record.get("forecast_peak_time_local"),
                    "forecast_values_hash": record.get("forecast_values_hash"),
                    "model_version": model_name,
                    "decision_snapshot_ts_utc": record.get("snapshot_ts_utc"),
                    "model_p_yes": p_yes_after,
                    "market_yes_price": market_yes,
                    "decision_entry_price": ask,
                    "yes_spread": record.get("yes_spread"),
                    "no_spread": record.get("no_spread"),
                    "yes_depth_ask_5c": record.get("yes_depth_ask_5c"),
                    "no_depth_ask_5c": record.get("no_depth_ask_5c"),
                }
            )
    return rows


def materialize_pipeline(
    conn: sqlite3.Connection,
    snapshots: list[Snapshot],
    *,
    feature_store: Path,
    max_snapshot_lag_minutes: float,
    event_limit: int | None = None,
) -> dict[str, int]:
    apply_first_seen_schema(conn)
    events = load_material_events(conn, limit=event_limit)
    indexed_snapshots = _snapshot_index(snapshots)
    counters = defaultdict(int)
    candidate_inputs = []
    for event in events:
        counters["events"] += 1
        previous, post = _match_snapshots(
            indexed_snapshots,
            event,
            max_lag_minutes=max_snapshot_lag_minutes,
        )
        feature_ref = (
            _build_feature_ref(conn, event, post, feature_store=feature_store)
            if post is not None
            else None
        )
        as_of = _iso(post.timestamp) if post is not None else str(event["available_at_utc"])
        checkpoint = build_state_checkpoint(
            city=str(event["city"]),
            target_date=str(event["target_date"]),
            trigger_event=event,
            as_of_ts_utc=as_of,
            feature_frame_ref=feature_ref,
            input_events=_input_events(conn, str(event["city"]), as_of),
            pit_provenance=(
                PIT_PROVENANCE_LIVE_CAPTURE
                if event.get("pit_lineage_class") == "collector_exact"
                else PIT_PROVENANCE_ARCHIVE_RECONSTRUCTION
            ),
        )
        counters["checkpoints_inserted"] += ingest_state_checkpoints(conn, [checkpoint])
        counters[f"checkpoint_{checkpoint['checkpoint_status']}"] += 1
        if post is None:
            counters["missing_post_snapshot"] += 1
            continue
        candidate_inputs.extend(_candidate_inputs(event, checkpoint, previous, post))
    candidate_result = materialize_candidate_rows(conn, candidate_inputs)
    counters.update({f"candidates_{key}": value for key, value in candidate_result.items()})
    counters["settlements_attached"] = attach_settlements(conn)
    return dict(counters)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--paper-snapshots", action="append", required=True)
    parser.add_argument(
        "--feature-store",
        default=str(ROOT / "runtime" / "weather_edge_v1" / "feature_store"),
    )
    parser.add_argument("--max-snapshot-lag-minutes", type=float, default=20.0)
    parser.add_argument("--event-limit", type=int)
    parser.add_argument("--allow-missing-snapshots", action="store_true")
    parser.add_argument("--skip-invalid-snapshots", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot_audit: dict[str, int] = {}
    snapshots = load_snapshots(
        [Path(value) for value in args.paper_snapshots],
        allow_missing=bool(args.allow_missing_snapshots),
        skip_invalid=bool(args.skip_invalid_snapshots),
        audit=snapshot_audit,
    )
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        result = materialize_pipeline(
            conn,
            snapshots,
            feature_store=Path(args.feature_store),
            max_snapshot_lag_minutes=float(args.max_snapshot_lag_minutes),
            event_limit=args.event_limit,
        )
    finally:
        conn.close()
    print(json.dumps({**snapshot_audit, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
