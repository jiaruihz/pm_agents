#!/usr/bin/env python3
"""Replay persisted weather WS frames into bounded public execution evidence.

This is deliberately a *tape derivation*, not an execution reconciliation:
the output contains public trades and reconstructed public books only.  It
requires the immutable subscription epoch journal so a raw frame is never
interpreted under a guessed token map or capture policy.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

# The tool is invoked from launchd/ops by pathname, not necessarily with the
# repository as its working directory.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.platform.market_data.execution_evidence import ExecutionEvidenceRecorder
from weather_clock_contract import parse_utc, utc_text


REPORT_SCHEMA_VERSION = "weather_execution_evidence_backfill_report_v1"


class BackfillError(RuntimeError):
    """A malformed or incomplete tape must not be partially interpreted."""


def _jsonl(path: Path) -> Iterator[tuple[int, bytes, dict[str, Any]]]:
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise BackfillError(f"cannot read {path}: {exc}") from exc
    with handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise BackfillError(
                    f"invalid JSON at {path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise BackfillError(f"non-object JSON at {path}:{line_number}")
            yield line_number, raw, row


def _paths_for_dates(root: Path, dates: Sequence[str], pattern: str) -> list[Path]:
    paths: list[Path] = []
    for date_text in sorted(set(dates)):
        day_root = root / date_text
        if not day_root.is_dir():
            raise BackfillError(f"missing dated input directory: {day_root}")
        paths.extend(sorted(path for path in day_root.glob(pattern) if path.is_file()))
    return paths


def resolve_raw_paths(
    *, raw_paths: Sequence[Path], raw_root: Path | None, dates: Sequence[str]
) -> list[Path]:
    resolved = list(raw_paths)
    if raw_root is not None:
        if dates:
            resolved.extend(_paths_for_dates(raw_root, dates, "*.jsonl"))
        else:
            resolved.extend(sorted(path for path in raw_root.glob("????-??-??/*.jsonl") if path.is_file()))
    unique = sorted({path.resolve() for path in resolved}, key=str)
    if not unique:
        raise BackfillError("no raw WS JSONL files selected")
    return unique


def resolve_epoch_paths(
    *, epoch_paths: Sequence[Path], epoch_root: Path | None, dates: Sequence[str]
) -> list[Path]:
    resolved = list(epoch_paths)
    if epoch_root is not None:
        if dates:
            # A subscription epoch may legitimately span UTC midnight: the raw
            # archive rotates by frame date while the immutable epoch journal is
            # named for the epoch's start date.  Load one preceding journal as
            # causal lineage, but keep failing closed if a frame references an
            # even older/missing epoch (the caller can then supply --epoch-path).
            selected_dates = sorted(set(dates))
            journal_dates = {
                (date.fromisoformat(date_text) - timedelta(days=1)).isoformat()
                for date_text in selected_dates
            }
            journal_dates.update(selected_dates)
            for date_text in sorted(journal_dates):
                path = epoch_root / f"subscription_epochs_{date_text}.jsonl"
                if not path.is_file() and date_text in selected_dates:
                    raise BackfillError(f"missing epoch journal: {path}")
                if path.is_file():
                    resolved.append(path)
        else:
            resolved.extend(sorted(epoch_root.glob("subscription_epochs_????-??-??.jsonl")))
    unique = sorted({path.resolve() for path in resolved}, key=str)
    if not unique:
        raise BackfillError("no subscription epoch journals selected")
    return unique


def load_epochs(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    epochs: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line_number, _raw, epoch in _jsonl(path):
            epoch_id = str(epoch.get("subscription_epoch_id") or "")
            if not epoch_id:
                raise BackfillError(f"epoch without subscription_epoch_id at {path}:{line_number}")
            if epoch_id in epochs:
                raise BackfillError(f"duplicate subscription_epoch_id {epoch_id!r}")
            if not isinstance(epoch.get("token_rows") or {}, Mapping):
                raise BackfillError(f"epoch {epoch_id!r} has invalid token_rows")
            if not isinstance(epoch.get("capture_demands") or [], list):
                raise BackfillError(f"epoch {epoch_id!r} has invalid capture_demands")
            parse_utc(epoch.get("started_at_utc"), field="started_at_utc")
            epochs[epoch_id] = epoch
    return epochs


def _frame_at(row: Mapping[str, Any], *, path: Path, line_number: int) -> datetime:
    try:
        return parse_utc(row.get("received_at_utc"), field="received_at_utc")
    except ValueError as exc:
        raise BackfillError(f"invalid frame clock at {path}:{line_number}: {exc}") from exc


def _preflight(
    raw_paths: Sequence[Path], epochs: Mapping[str, Mapping[str, Any]], *, max_frames: int | None, max_bytes: int | None
) -> dict[str, Any]:
    """Validate the exact bounded prefix before creating append-only output."""

    frames = 0
    raw_bytes = 0
    epoch_ids: set[str] = set()
    truncated = False
    for path in raw_paths:
        for line_number, raw, row in _jsonl(path):
            next_bytes = raw_bytes + len(raw)
            if (max_frames is not None and frames >= max_frames) or (
                max_bytes is not None and next_bytes > max_bytes
            ):
                truncated = True
                break
            epoch_id = str(row.get("subscription_epoch_id") or "")
            if not epoch_id or epoch_id not in epochs:
                raise BackfillError(
                    f"raw frame references undeclared epoch {epoch_id!r} at {path}:{line_number}"
                )
            _frame_at(row, path=path, line_number=line_number)
            frames += 1
            raw_bytes = next_bytes
            epoch_ids.add(epoch_id)
        if truncated:
            break
    if frames == 0:
        raise BackfillError("selected tape contains no replayable raw frames")
    return {
        "frames": frames,
        "raw_bytes": raw_bytes,
        "epoch_ids": sorted(epoch_ids),
        "truncated": truncated,
    }


def _iter_bounded_frames(
    raw_paths: Sequence[Path], *, max_frames: int | None, max_bytes: int | None
) -> Iterator[tuple[Path, int, dict[str, Any], datetime]]:
    frames = 0
    raw_bytes = 0
    for path in raw_paths:
        for line_number, raw, row in _jsonl(path):
            if (max_frames is not None and frames >= max_frames) or (
                max_bytes is not None and raw_bytes + len(raw) > max_bytes
            ):
                return
            at_utc = _frame_at(row, path=path, line_number=line_number)
            row = dict(row)
            # This is the physical replay locator, never a mutable logical alias.
            row["_raw_path"] = str(path)
            row["_line_number"] = line_number
            yield path, line_number, row, at_utc
            frames += 1
            raw_bytes += len(raw)


def run_backfill(
    *,
    raw_paths: Sequence[Path],
    epoch_paths: Sequence[Path],
    output_root: Path,
    max_frames: int | None = None,
    max_bytes: int | None = None,
    daily_evidence_budget_bytes: int = 1_000_000_000,
    min_periodic_interval_sec: float = 10.0,
    checkpoint_grace_sec: float = 20.0,
) -> dict[str, Any]:
    """Replay a validated tape prefix and return a stable, machine-readable report."""

    if max_frames is not None and max_frames <= 0:
        raise BackfillError("max_frames must be positive")
    if max_bytes is not None and max_bytes <= 0:
        raise BackfillError("max_bytes must be positive")
    epochs = load_epochs(epoch_paths)
    preflight = _preflight(raw_paths, epochs, max_frames=max_frames, max_bytes=max_bytes)
    existing_products = [
        path
        for product in ("public_books", "market_trade_prints")
        for path in (output_root / product).glob("*/*.jsonl")
        if path.is_file() and path.stat().st_size > 0
    ]
    if existing_products:
        raise BackfillError(
            "output_root already contains execution evidence; choose a new root "
            "to preserve replay idempotence"
        )
    recorder = ExecutionEvidenceRecorder(
        output_root,
        daily_budget_bytes=daily_evidence_budget_bytes,
        min_periodic_interval_sec=min_periodic_interval_sec,
        checkpoint_grace_sec=checkpoint_grace_sec,
    )
    activated: list[str] = []
    registered_demands: set[str] = set()
    current_epoch_id: str | None = None
    last_at: datetime | None = None
    try:
        for _path, _line, frame, at_utc in _iter_bounded_frames(
            raw_paths, max_frames=max_frames, max_bytes=max_bytes
        ):
            epoch_id = str(frame["subscription_epoch_id"])
            if epoch_id != current_epoch_id:
                epoch = epochs[epoch_id]
                recorder.activate_epoch(epoch)
                if epoch_id not in registered_demands:
                    recorder.register_capture_demands(epoch.get("capture_demands") or ())
                    registered_demands.add(epoch_id)
                activated.append(epoch_id)
                current_epoch_id = epoch_id
            recorder.ingest(frame, now_utc=at_utc)
            last_at = at_utc
        # Flush/checkpoint expiry against the latest tape clock, rather than wall clock.
        if last_at is None:
            raise BackfillError("no replayable raw frame")
        health = recorder.health(last_at)
    finally:
        recorder.close()
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "budget_truncated" if preflight["truncated"] else "complete",
        "raw_paths": [str(path) for path in raw_paths],
        "epoch_paths": [str(path) for path in epoch_paths],
        "output_root": str(output_root),
        "input_frames": preflight["frames"],
        "input_bytes": preflight["raw_bytes"],
        "input_epoch_ids": preflight["epoch_ids"],
        "activated_epoch_ids": activated,
        "max_frames": max_frames,
        "max_bytes": max_bytes,
        "daily_evidence_budget_bytes": daily_evidence_budget_bytes,
        "recorder_health": health,
    }
    # A content ID makes reports comparable even when the caller chooses a
    # different report filename or runs on a different host.
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-path", action="append", default=[], type=Path)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--epoch-path", action="append", default=[], type=Path)
    parser.add_argument("--epoch-root", type=Path)
    parser.add_argument("--date", action="append", default=[])
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--daily-evidence-budget-bytes", type=int, default=1_000_000_000)
    parser.add_argument("--min-periodic-interval-sec", type=float, default=10.0)
    parser.add_argument("--checkpoint-grace-sec", type=float, default=20.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw_paths = resolve_raw_paths(raw_paths=args.raw_path, raw_root=args.raw_root, dates=args.date)
        epoch_paths = resolve_epoch_paths(epoch_paths=args.epoch_path, epoch_root=args.epoch_root, dates=args.date)
        report = run_backfill(
            raw_paths=raw_paths,
            epoch_paths=epoch_paths,
            output_root=args.output_root,
            max_frames=args.max_frames,
            max_bytes=args.max_bytes,
            daily_evidence_budget_bytes=args.daily_evidence_budget_bytes,
            min_periodic_interval_sec=args.min_periodic_interval_sec,
            checkpoint_grace_sec=args.checkpoint_grace_sec,
        )
        _write_report(args.report_path or args.output_root / "backfill_report.json", report)
    except BackfillError as exc:
        print(f"backfill failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
