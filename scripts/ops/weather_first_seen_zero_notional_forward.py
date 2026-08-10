#!/usr/bin/env python3
"""Run the first-seen data/signal pipeline with permanently zero notional."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.etl.build_weather_signal_candidates import FORECAST_CURVE_DDL
from scripts.etl.materialize_weather_first_seen_pipeline import (
    load_snapshots,
    materialize_pipeline,
)
from scripts.etl.materialize_weather_information_events import materialize_rows
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema


TELEMETRY_VERSION = "weather_first_seen_zero_notional_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "offsets": {}, "seen_files": [], "exported_candidates": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid forward state: {path}")
    return value


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _raw_files(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.jsonl"))
        else:
            raise FileNotFoundError(f"required forward raw path does not exist: {path}")
    return sorted(set(files))


def _incremental_rows(
    paths: list[Path],
    state: dict[str, Any],
    *,
    bootstrap_at_end: bool,
) -> Iterator[tuple[dict[str, Any], Path]]:
    offsets = dict(state.get("offsets") or {})
    seen_files = set(state.get("seen_files") or [])
    initial_bootstrap = bootstrap_at_end and not bool(state.get("bootstrap_complete"))
    for path in _raw_files(paths):
        key = str(path.resolve())
        immutable_file = path.parent.name[:4].isdigit() or path.name.startswith("forecast_hourly_curves_")
        if immutable_file and key in seen_files:
            continue
        size = path.stat().st_size
        if key not in offsets and initial_bootstrap and not immutable_file:
            offsets[key] = size
            continue
        if immutable_file and initial_bootstrap and key not in seen_files:
            seen_files.add(key)
            continue
        offset = int(offsets.get(key) or 0)
        if size < offset:
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            while True:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    handle.seek(line_start)
                    break
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid forward JSONL at {path}:{line_start}") from exc
                if isinstance(value, dict):
                    yield value, path
            offsets[key] = handle.tell()
        if immutable_file:
            seen_files.add(key)
    state["offsets"] = offsets
    state["seen_files"] = sorted(seen_files)
    state["bootstrap_complete"] = True


def _recent_snapshot_files(path: Path, limit: int) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"paper snapshot path does not exist: {path}")
    return sorted(path.rglob("snapshot_*.json"))[-max(1, limit) :]


def export_new_candidates(
    conn: sqlite3.Connection,
    out: Path,
    state: dict[str, Any],
) -> int:
    exported = set(state.get("exported_candidates") or [])
    rows = conn.execute(
        """
        SELECT
          candidate.*,
          event.event_kind AS trigger_event_kind,
          event.event_role AS trigger_event_role,
          event.source AS trigger_event_source,
          event.pit_lineage_class AS pit_lineage_class,
          event.first_seen_at_utc AS trigger_first_seen_at_utc,
          event.available_at_utc AS trigger_available_at_utc
        FROM fact_signal_candidates AS candidate
        LEFT JOIN weather_information_events AS event
          ON event.information_event_id = candidate.trigger_event_id
        WHERE candidate.candidate_grain_version = 'v2_event_checkpoint'
        ORDER BY candidate.decision_ts_utc, candidate.candidate_id
        """
    ).fetchall()
    new_rows = [dict(row) for row in rows if str(row["candidate_id"]) not in exported]
    if new_rows:
        out.parent.mkdir(parents=True, exist_ok=True)
        generated = _utc_now()
        with out.open("a", encoding="utf-8") as handle:
            for source in new_rows:
                row = {
                    **source,
                    "record_type": "weather_first_seen_candidate_v2",
                    "telemetry_version": TELEMETRY_VERSION,
                    "generated_at_utc": generated,
                    "zero_notional": True,
                    "no_order_placed": True,
                    "notional_usd": 0.0,
                }
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                exported.add(str(source["candidate_id"]))
    state["exported_candidates"] = sorted(exported)
    return len(new_rows)


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    raw_paths = [
        Path(value)
        for value in [
            *args.source_events,
            *args.high_frequency_observations,
            *args.forecast_curves,
            *args.forecast_enrichment,
        ]
    ]
    if not raw_paths:
        raise ValueError("forward runner requires raw information-event inputs")
    conn = sqlite3.connect(args.db, timeout=float(args.db_lock_timeout_seconds))
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"PRAGMA busy_timeout={max(1, int(float(args.db_lock_timeout_seconds) * 1000))}"
    )
    try:
        apply_schema_canonical(conn)
        conn.execute(FORECAST_CURVE_DDL)
        apply_first_seen_schema(conn)
        initial_bootstrap = bool(args.bootstrap_at_end) and not bool(state.get("raw_bootstrapped"))
        information = materialize_rows(
            conn,
            _incremental_rows(
                raw_paths,
                state,
                bootstrap_at_end=initial_bootstrap,
            ),
            batch_size=max(1, int(args.db_write_batch_size)),
        )
        state["raw_bootstrapped"] = True
        snapshot_files = []
        for value in args.paper_snapshots:
            snapshot_files.extend(
                _recent_snapshot_files(Path(value), int(args.snapshot_lookback_files))
            )
        snapshots = load_snapshots(snapshot_files)
        pipeline = materialize_pipeline(
            conn,
            snapshots,
            feature_store=Path(args.feature_store),
            max_snapshot_lag_minutes=float(args.max_snapshot_lag_minutes),
            event_limit=args.event_limit,
            candidate_batch_size=max(1, int(args.db_write_batch_size)),
            attach_candidate_settlements=False,
        )
        exported = export_new_candidates(conn, Path(args.out), state)
    finally:
        conn.close()
    state["last_cycle_at_utc"] = _utc_now()
    return {
        "information": information,
        "pipeline": pipeline,
        "telemetry_rows_exported": exported,
        "zero_notional": True,
        "no_order_placed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--source-events", action="append", default=[])
    parser.add_argument("--high-frequency-observations", action="append", default=[])
    parser.add_argument("--forecast-curves", action="append", default=[])
    parser.add_argument("--forecast-enrichment", action="append", default=[])
    parser.add_argument("--paper-snapshots", action="append", required=True)
    parser.add_argument("--feature-store", required=True)
    parser.add_argument("--max-snapshot-lag-minutes", type=float, default=20.0)
    parser.add_argument("--snapshot-lookback-files", type=int, default=48)
    parser.add_argument("--event-limit", type=int, default=500)
    parser.add_argument("--db-lock-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--db-lock-retries", type=int, default=5)
    parser.add_argument("--db-lock-retry-delay-seconds", type=float, default=5.0)
    parser.add_argument("--db-write-batch-size", type=int, default=25)
    parser.add_argument("--bootstrap-at-end", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    return parser


def run_cycle_with_lock_retry(
    args: argparse.Namespace,
    state: dict[str, Any],
) -> dict[str, Any]:
    for attempt in range(max(0, int(args.db_lock_retries)) + 1):
        working_state = deepcopy(state)
        try:
            result = run_cycle(args, working_state)
            state.clear()
            state.update(working_state)
            return result
        except sqlite3.OperationalError as exc:
            lock_error = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if not lock_error or attempt >= int(args.db_lock_retries):
                raise
            delay = max(0.0, float(args.db_lock_retry_delay_seconds)) * (attempt + 1)
            print(
                json.dumps(
                    {
                        "status": "db_lock_retry",
                        "attempt": attempt + 1,
                        "max_retries": int(args.db_lock_retries),
                        "delay_seconds": delay,
                        "error": str(exc),
                        "state_offsets_advanced": False,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("unreachable lock retry loop")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_path = Path(args.state)
    state = _read_state(state_path)
    while True:
        result = run_cycle_with_lock_retry(args, state)
        _write_state(state_path, state)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
