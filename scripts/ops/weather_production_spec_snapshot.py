#!/usr/bin/env python3
"""Capture and verify one stable production topology for a bounded refresh."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Iterator, Mapping

import yaml


SNAPSHOT_SCHEMA_VERSION = "weather_production_spec_snapshot_v1"


class ProductionSpecSnapshotError(RuntimeError):
    """The operational production spec could not be frozen safely."""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProductionSpecSnapshotError(
            f"git {' '.join(args)} failed: {detail}"
        )
    return result.stdout.strip()


def _operational_repo_and_lock(source: Path) -> tuple[Path, Path, Path]:
    if not source.is_absolute():
        raise ProductionSpecSnapshotError("production spec source must be absolute")
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise ProductionSpecSnapshotError(
            f"production spec source does not resolve: {source}: {exc}"
        ) from exc
    repo = Path(_git(resolved.parent, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    expected = (repo / "src/strategies/runtime/production.yaml").resolve(
        strict=False
    )
    if resolved != expected:
        raise ProductionSpecSnapshotError(
            "production spec source must be the operational repository "
            f"production.yaml: expected={expected} observed={resolved}"
        )
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    lock_path = common.resolve(strict=True) / "weather-production-release.lock"
    return repo, resolved, lock_path


@contextmanager
def _shared_release_lock(lock_path: Path) -> Iterator[None]:
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ProductionSpecSnapshotError(
            f"cannot open production release lock: {exc}"
        ) from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProductionSpecSnapshotError(
                "production release is changing; retry canonical refresh"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _identity(row: os.stat_result) -> tuple[int, int, int, int]:
    return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)


def _read_stable_regular_file(path: Path) -> tuple[bytes, dict[str, int]]:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise ProductionSpecSnapshotError(
                f"production spec source must not be a symlink: {path}"
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    except OSError as exc:
        raise ProductionSpecSnapshotError(
            f"cannot open production spec source: {path}: {exc}"
        ) from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ProductionSpecSnapshotError(
                f"production spec source must be a regular file: {path}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        current = path.stat()
    except OSError as exc:
        raise ProductionSpecSnapshotError(
            f"production spec source disappeared while reading: {path}: {exc}"
        ) from exc
    if _identity(before) != _identity(after) or _identity(after) != _identity(current):
        raise ProductionSpecSnapshotError(
            "production spec source changed while being read; retry canonical refresh"
        )
    return b"".join(chunks), {
        "device": before.st_dev,
        "inode": before.st_ino,
        "size_bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns,
    }


def _write_atomic(path: Path, data: bytes, *, mode: int) -> None:
    if not path.is_absolute():
        raise ProductionSpecSnapshotError(f"output path must be absolute: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    _write_atomic(path, encoded, mode=0o600)


def capture_production_spec_snapshot(
    *, source: Path, snapshot: Path, report: Path
) -> dict[str, Any]:
    repo, resolved_source, lock_path = _operational_repo_and_lock(source)
    if snapshot.resolve(strict=False) == resolved_source:
        raise ProductionSpecSnapshotError("snapshot must not replace its source")
    with _shared_release_lock(lock_path):
        first, identity = _read_stable_regular_file(resolved_source)
        second, second_identity = _read_stable_regular_file(resolved_source)
        if first != second or identity != second_identity:
            raise ProductionSpecSnapshotError(
                "production spec changed while snapshotting; retry canonical refresh"
            )
        try:
            parsed = yaml.safe_load(first)
        except yaml.YAMLError as exc:
            raise ProductionSpecSnapshotError(
                f"production spec is invalid YAML: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ProductionSpecSnapshotError("production spec must be a mapping")
        sha256 = hashlib.sha256(first).hexdigest()
        _write_atomic(snapshot, first, mode=0o400)
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != sha256:
            raise ProductionSpecSnapshotError("written production snapshot hash mismatch")
        payload: dict[str, Any] = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "ok": True,
            "operational_repo_root": str(repo),
            "source_path": str(resolved_source),
            "snapshot_path": str(snapshot),
            "sha256": sha256,
            "source_identity": identity,
        }
        _write_json_atomic(report, payload)
    return payload


def verify_production_spec_source(
    *, source: Path, expected_sha256: str
) -> dict[str, Any]:
    if len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise ProductionSpecSnapshotError("expected SHA-256 must be lowercase hex")
    repo, resolved_source, lock_path = _operational_repo_and_lock(source)
    with _shared_release_lock(lock_path):
        data, identity = _read_stable_regular_file(resolved_source)
    observed = hashlib.sha256(data).hexdigest()
    if observed != expected_sha256:
        raise ProductionSpecSnapshotError(
            "operational production spec changed during canonical refresh; "
            f"expected={expected_sha256} observed={observed}"
        )
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "ok": True,
        "operational_repo_root": str(repo),
        "source_path": str(resolved_source),
        "sha256": observed,
        "source_identity": identity,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--snapshot", type=Path, required=True)
    capture.add_argument("--report", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--source", type=Path, required=True)
    verify.add_argument("--expected-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            payload = capture_production_spec_snapshot(
                source=args.source,
                snapshot=args.snapshot,
                report=args.report,
            )
        else:
            payload = verify_production_spec_source(
                source=args.source,
                expected_sha256=args.expected_sha256,
            )
    except ProductionSpecSnapshotError as exc:
        print(
            json.dumps(
                {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 75
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
