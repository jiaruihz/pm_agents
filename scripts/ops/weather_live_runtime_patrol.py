#!/usr/bin/env python3
"""Watch the fast-source live runner and stop repeated deterministic submit failures."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
RUNNER_PATTERN = "scripts/ops/weather_fast_source_prev_no_trial.py --loop"
DETERMINISTIC_SUBMIT_ERRORS = (
    "invalid expiration value",
    "invalid signature",
    "invalid api key",
    "not enough balance / allowance",
)


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_recent_orders(path: Path, *, cutoff: datetime) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = parse_utc(row.get("live_attempt_ts_utc") or row.get("ts_utc"))
        if ts is not None and ts >= cutoff:
            rows.append(row)
    return rows


def runner_pids() -> list[int]:
    result = subprocess.run(["pgrep", "-f", RUNNER_PATTERN], capture_output=True, text=True, check=False)
    return [int(value) for value in result.stdout.split() if value.isdigit() and int(value) != os.getpid()]


def deterministic_error(row: dict[str, Any]) -> str:
    message = str(row.get("error") or row.get("live_submit_error") or "").lower()
    return next((needle for needle in DETERMINISTIC_SUBMIT_ERRORS if needle in message), "")


def evaluate(
    *,
    now: datetime,
    latest: dict[str, Any],
    recent_orders: list[dict[str, Any]],
    pids: list[int],
    max_latest_age_sec: float,
    failure_threshold: int,
) -> dict[str, Any]:
    generated = parse_utc(latest.get("generated_at_utc"))
    latest_age_sec = (now - generated).total_seconds() if generated else None
    failures = [row for row in recent_orders if str(row.get("live_submit_status") or "") == "submit_failed"]
    deterministic = [row for row in failures if deterministic_error(row)]
    error_counts: dict[str, int] = {}
    for row in deterministic:
        key = deterministic_error(row)
        error_counts[key] = error_counts.get(key, 0) + 1

    reasons: list[str] = []
    if not pids:
        reasons.append("runner_process_missing")
    if latest_age_sec is None or latest_age_sec > max_latest_age_sec:
        reasons.append("runner_latest_stale")
    repeated_error = next((key for key, count in error_counts.items() if count >= failure_threshold), "")
    if repeated_error:
        reasons.append(f"repeated_deterministic_submit_failure:{repeated_error}")
    return {
        "status": "critical" if reasons else "ok",
        "checked_at_utc": now.isoformat(),
        "runner_pids": pids,
        "latest_age_sec": round(latest_age_sec, 3) if latest_age_sec is not None else None,
        "recent_order_attempts": len(recent_orders),
        "recent_submit_failures": len(failures),
        "deterministic_submit_error_counts": error_counts,
        "reasons": reasons,
        "stop_runner_required": bool(repeated_error and pids),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    latest = read_json(Path(args.runner_dir) / "latest.json")
    orders = read_recent_orders(Path(args.runner_dir) / "orders.jsonl", cutoff=now - timedelta(minutes=args.lookback_min))
    health = evaluate(
        now=now,
        latest=latest,
        recent_orders=orders,
        pids=runner_pids(),
        max_latest_age_sec=args.max_latest_age_sec,
        failure_threshold=args.failure_threshold,
    )
    stopped: list[int] = []
    if args.stop_runner_on_submit_failure and health["stop_runner_required"]:
        for pid in health["runner_pids"]:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except ProcessLookupError:
                pass
        health["runner_stopped_pids"] = stopped
    write_json(Path(args.output), health)
    return health


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-dir", default=str(DEFAULT_RUNTIME_ROOT / "output/fast_source_prev_no_trial"))
    parser.add_argument("--output", default=str(DEFAULT_RUNTIME_ROOT / "output/live_runtime_patrol/latest.json"))
    parser.add_argument("--lookback-min", type=float, default=15.0)
    parser.add_argument("--max-latest-age-sec", type=float, default=180.0)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--stop-runner-on-submit-failure", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    while True:
        health = run_once(args)
        print(json.dumps(health, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0 if health["status"] == "ok" else 1
        time.sleep(max(10.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
