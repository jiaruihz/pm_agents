#!/usr/bin/env python3
"""Run one canonical refresh stage with time, WAL, lock, and audit budgets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Mapping


STAGE_REPORT_SCHEMA_VERSION = "weather_canonical_refresh_stage_report_v1"
_SAFE_STAGE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _wal_bytes(db_path: Path) -> int:
    wal = Path(str(db_path) + "-wal")
    try:
        return wal.stat().st_size
    except FileNotFoundError:
        return 0


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _tail(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "")[-8000:]


def run_stage(
    *,
    stage: str,
    command: list[str],
    db_path: Path,
    report_path: Path,
    max_seconds: float,
    max_wal_bytes: int,
    max_wal_growth_bytes: int,
) -> tuple[int, dict[str, Any]]:
    if not stage or not _SAFE_STAGE.fullmatch(stage):
        raise ValueError("stage name must be path-safe")
    if not command or max_seconds <= 0 or max_wal_bytes <= 0:
        raise ValueError("command and positive time/absolute WAL budgets are required")
    if max_wal_growth_bytes < 0:
        raise ValueError("WAL growth budget must be non-negative")
    lock = report_path.parent / f"{stage}.lock"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        owner_path = lock / "owner.json"
        try:
            lock_owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lock_owner = None
        report = {
            "schema_version": STAGE_REPORT_SCHEMA_VERSION,
            "stage": stage,
            "status": "blocked_stage_lock_held",
            "returncode": 73,
            "lock_path": str(lock),
            "lock_owner": lock_owner,
        }
        _write_atomic(report_path, report)
        return 73, report
    wal_before = _wal_bytes(db_path)
    started_monotonic = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lock_owner_path = lock / "owner.json"
    timed_out = False
    try:
        _write_atomic(
            lock_owner_path,
            {
                "schema_version": "weather_canonical_refresh_stage_lock_v1",
                "stage": stage,
                "pid": os.getpid(),
                "started_at_utc": started_at,
                "max_seconds": max_seconds,
                "report_path": str(report_path),
            },
        )
        if wal_before > max_wal_bytes:
            report = {
                "schema_version": STAGE_REPORT_SCHEMA_VERSION,
                "stage": stage,
                "status": "blocked_preexisting_wal_budget",
                "returncode": 75,
                "command": command,
                "started_at_utc": started_at,
                "duration_seconds": 0.0,
                "max_seconds": max_seconds,
                "db_path": str(db_path),
                "wal_bytes_before": wal_before,
                "wal_bytes_after": wal_before,
                "wal_growth_bytes": 0,
                "max_wal_bytes": max_wal_bytes,
                "max_wal_growth_bytes": max_wal_growth_bytes,
                "busy_error_count": 0,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            _write_atomic(report_path, report)
            return 75, report
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max_seconds,
                check=False,
            )
            returncode = result.returncode
            stdout = _tail(result.stdout)
            stderr = _tail(result.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            stdout = _tail(exc.stdout)
            stderr = _tail(exc.stderr)
        duration = time.monotonic() - started_monotonic
        wal_after = _wal_bytes(db_path)
        wal_growth = wal_after - wal_before
        wal_exceeded = (
            wal_after > max_wal_bytes or wal_growth > max_wal_growth_bytes
        )
        busy_errors = sum(
            (stdout + "\n" + stderr).lower().count(marker)
            for marker in ("database is locked", "database is busy", "sqlite_busy")
        )
        status = (
            "timeout"
            if timed_out
            else "wal_budget_exceeded"
            if wal_exceeded
            else "failed"
            if returncode != 0
            else "passed"
        )
        if wal_exceeded and returncode == 0:
            returncode = 75
        report = {
            "schema_version": STAGE_REPORT_SCHEMA_VERSION,
            "stage": stage,
            "status": status,
            "returncode": returncode,
            "command": command,
            "started_at_utc": started_at,
            "duration_seconds": round(duration, 6),
            "max_seconds": max_seconds,
            "db_path": str(db_path),
            "wal_bytes_before": wal_before,
            "wal_bytes_after": wal_after,
            "wal_growth_bytes": wal_growth,
            "max_wal_bytes": max_wal_bytes,
            "max_wal_growth_bytes": max_wal_growth_bytes,
            "busy_error_count": busy_errors,
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        }
        _write_atomic(report_path, report)
        return returncode, report
    finally:
        lock_owner_path.unlink(missing_ok=True)
        try:
            lock.rmdir()
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, required=True)
    parser.add_argument("--max-wal-bytes", type=int, required=True)
    parser.add_argument("--max-wal-growth-bytes", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    returncode, report = run_stage(
        stage=args.stage,
        command=command,
        db_path=args.db,
        report_path=args.report,
        max_seconds=args.max_seconds,
        max_wal_bytes=args.max_wal_bytes,
        max_wal_growth_bytes=args.max_wal_growth_bytes,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
