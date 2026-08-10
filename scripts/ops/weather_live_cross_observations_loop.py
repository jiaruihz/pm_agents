#!/usr/bin/env python3
"""Unified source-aware high-frequency observation producer loop."""

from __future__ import annotations

import argparse
import errno
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_fast_lane import (  # noqa: E402
    ObservationFastLane,
    observation_fast_lane_paths,
    parse_observation_fast_lane,
)
from weather_data_feed_service.high_frequency_observations import (  # noqa: E402
    build_parser as build_observation_parser,
    build_payload,
    update_state,
    write_new_observation_notification,
    write_outputs,
)

DEFAULT_SOURCES = "jma_amedas singapore_mss fmi knmi amos_runway mgm ims_lod noaa_madis_hfmetar"
DEFAULT_CITIES = "Amsterdam Helsinki Busan Singapore Tokyo Seoul TelAviv Ankara Istanbul Atlanta Miami SanFrancisco"
DEFAULT_SOURCE_INTERVALS = "jma_amedas=300 singapore_mss=20 fmi=60 knmi=300 amos_runway=20 mgm=300 ims_lod=300 noaa_madis_hfmetar=300"
DEFAULT_WINDOW_INTERVALS = "jma_amedas=5-8,15-18,25-28,35-38,45-48,55-58:2 fmi=1-6,11-16,21-26,31-36,41-46,51-56:2"
PERMISSION_ERRNOS = {errno.EACCES, errno.EPERM}


@dataclass(frozen=True)
class WindowRule:
    source: str
    windows: tuple[tuple[float, float], ...]
    interval_sec: float


def parse_window_rule(value: str) -> WindowRule:
    source, sep, rest = value.strip().partition("=")
    windows_raw, interval_sep, interval_raw = rest.rpartition(":")
    if not sep or not interval_sep or not source:
        raise ValueError(f"invalid source window rule: {value!r}")
    interval_sec = float(interval_raw)
    if interval_sec <= 0:
        raise ValueError(f"window interval must be positive: {value!r}")
    windows = []
    for item in windows_raw.split(","):
        start_raw, item_sep, end_raw = item.strip().partition("-")
        if not item_sep:
            raise ValueError(f"invalid minute window: {item!r}")
        start, end = float(start_raw), float(end_raw)
        if not 0 <= start <= end < 60:
            raise ValueError(f"invalid minute window: {item!r}")
        windows.append((start, end))
    return WindowRule(source, tuple(windows), interval_sec)


def active_window_rules(now: datetime, rules: list[WindowRule]) -> list[WindowRule]:
    minute = now.minute + now.second / 60 + now.microsecond / 60_000_000
    return [rule for rule in rules if any(start <= minute <= end for start, end in rule.windows)]


def next_poll_interval(now: datetime, rules: list[WindowRule], base: float) -> float:
    active = active_window_rules(now, rules)
    if active:
        return min(rule.interval_sec for rule in active)
    hour = now.replace(minute=0, second=0, microsecond=0)
    candidates = [
        hour + timedelta(hours=hour_offset, minutes=start)
        for hour_offset in (0, 1)
        for rule in rules
        for start, _end in rule.windows
        if hour + timedelta(hours=hour_offset, minutes=start) > now
    ]
    return min(base, (min(candidates) - now).total_seconds()) if candidates else base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--notify-path", required=True)
    parser.add_argument("--state-path", default="")
    parser.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES.split())
    parser.add_argument("--cities", nargs="+", default=DEFAULT_CITIES.split())
    parser.add_argument("--source-min-interval-sec", action="append", default=[])
    parser.add_argument("--source-window-min-interval-sec", action="append", default=[])
    parser.add_argument("--base-loop-interval-sec", type=float, default=20.0)
    parser.add_argument("--active-local-start-hour", type=float, default=6.0)
    parser.add_argument("--active-local-end-hour", type=float, default=22.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--fast-lane", action="append", default=[])
    parser.add_argument("--once", action="store_true")
    return parser


def _identity_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(args).items()
        if key not in {"once"}
    }


def observation_args(args: argparse.Namespace, lane: ObservationFastLane | None = None) -> tuple[argparse.Namespace, Path, Path]:
    output_dir = Path(args.output_dir)
    notify_path = Path(args.notify_path)
    sources, cities = args.sources, args.cities
    state_path = Path(args.state_path) if args.state_path else output_dir / "state.json"
    source_intervals = args.source_min_interval_sec or DEFAULT_SOURCE_INTERVALS.split()
    window_intervals = args.source_window_min_interval_sec or DEFAULT_WINDOW_INTERVALS.split()
    if lane is not None:
        paths = observation_fast_lane_paths(output_dir, notify_path, lane.name)
        output_dir, notify_path, state_path = paths.output_dir, paths.notify_path, paths.state_path
        sources, cities = [lane.source], list(lane.cities)
        source_intervals, window_intervals = [f"{lane.source}={lane.interval_sec}"], []
    raw = [
        "--output-dir", str(output_dir), "--state-path", str(state_path),
        "--active-local-start-hour", str(args.active_local_start_hour),
        "--active-local-end-hour", str(args.active_local_end_hour),
        "--max-workers", str(
            args.max_workers if lane is None else min(args.max_workers, max(1, len(cities)))
        ),
        "--timeout-sec", str(args.timeout_sec), "--sources", *sources, "--cities", *cities,
    ]
    for item in source_intervals:
        raw.extend(("--source-min-interval-sec", item))
    for item in window_intervals:
        raw.extend(("--source-minute-window-min-interval-sec", item))
    parsed = build_observation_parser().parse_args(raw)
    parsed._producer_entrypoint_path = str(Path(__file__).resolve())
    parsed._producer_config_payload = {
        **_identity_config(args),
        "route_kind": "fast_lane" if lane else "main",
        "fast_lane": None if lane is None else lane.name,
    }
    return parsed, output_dir, notify_path


def run_cycle(args: argparse.Namespace, lane: ObservationFastLane | None = None) -> dict[str, object]:
    obs_args, output_dir, notify_path = observation_args(args, lane)
    payload = build_payload(obs_args)
    # The main compatibility journal is still consumed by live strategies.
    # Fast lanes are internal fan-outs whose consumers use latest.json or
    # dated evidence, so a second aggregate only duplicates every shard byte.
    write_outputs(payload, output_dir, write_aggregate=lane is None)
    notified = write_new_observation_notification(notify_path, payload)
    update_state(payload, Path(obs_args.state_path))
    return {
        "schema_version": payload["schema_version"],
        "schema_fingerprint": payload["schema_fingerprint"],
        "producer_identity": payload["producer_identity"],
        "generated_at_utc": payload.get("generated_at_utc"),
        "rows": payload.get("rows"),
        "new_observation_count": payload.get("new_observation_count"),
        "notified": notified,
        "route_kind": "fast_lane" if lane else "main",
        "fast_lane": None if lane is None else lane.name,
    }


def _lane_loop(args: argparse.Namespace, lane: ObservationFastLane, failures: queue.Queue) -> None:
    try:
        while True:
            started = time.monotonic()
            print(json.dumps(run_cycle(args, lane), sort_keys=True), flush=True)
            time.sleep(max(.05, lane.interval_sec - (time.monotonic() - started)))
    except BaseException as exc:
        failures.put((lane.name, exc))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.base_loop_interval_sec <= 0:
        raise ValueError("base loop interval must be positive")
    rules = [parse_window_rule(value) for value in (args.source_window_min_interval_sec or DEFAULT_WINDOW_INTERVALS.split())]
    lanes = [parse_observation_fast_lane(value) for value in args.fast_lane]
    if len({lane.name for lane in lanes}) != len(lanes):
        raise ValueError("duplicate fast lane names")
    if args.once:
        result = run_cycle(args)
        result["fast_lanes"] = [run_cycle(args, lane) for lane in lanes]
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    failures: queue.Queue = queue.Queue()
    for lane in lanes:
        threading.Thread(target=_lane_loop, args=(args, lane, failures), daemon=True).start()
    permission_failures = 0
    while True:
        if not failures.empty():
            name, failure = failures.get_nowait()
            raise RuntimeError(f"fast lane {name!r} stopped") from failure
        started = time.monotonic()
        try:
            result = run_cycle(args)
        except OSError as exc:
            if exc.errno not in PERMISSION_ERRNOS:
                raise
            permission_failures += 1
            time.sleep(min(60.0, 5.0 * permission_failures))
            continue
        permission_failures = 0
        interval = next_poll_interval(datetime.now(timezone.utc), rules, args.base_loop_interval_sec)
        result["next_poll_interval_sec"] = round(interval, 3)
        print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(max(.05, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
