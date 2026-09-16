#!/usr/bin/env python3
"""Incrementally materialize WCIR shadow candidates into canonical facts."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_city_runtime import (  # noqa: E402
    CanonicalCandidateBridge,
    DecisionBundle,
    TradeIntent,
    TemporaryCanonicalBridge,
)
from weather_data_feed.information_events import canonical_json_hash  # noqa: E402
from weather_data_feed.cursor_epochs import cursor_epoch_identity  # noqa: E402


CURSOR_SCHEMA_VERSION = "weather_jsonl_cursor_epoch_v1"
LEGACY_CURSOR_SCHEMA_VERSION = "wcir_canonical_materializer_cursor_v1"
CURSOR_GUARD_BYTES = 4096
CURSOR_RECOVERY_OWNER = "canonical_refresh"
CURSOR_STUCK_SLO_SECONDS = 900


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _input_snapshot(paths: Iterable[Path]) -> list[dict[str, Any]]:
    snapshot = []
    for path in paths:
        resolved = path.resolve()
        stat = resolved.stat()
        snapshot.append(
            {
                "path": str(resolved),
                "device": stat.st_dev,
                "inode": stat.st_ino,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return snapshot


def _bundle_from_line(line: str, *, path: Path, row_number: int) -> DecisionBundle:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSONL at {path}:{row_number}") from exc
    if not isinstance(row, dict):
        raise ValueError(f"JSONL row must be an object at {path}:{row_number}")
    return DecisionBundle.from_dict(row)


def _intent_from_line(line: str, *, path: Path, row_number: int) -> TradeIntent:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid TradeIntent JSONL at {path}:{row_number}") from exc
    if not isinstance(row, dict):
        raise ValueError(f"TradeIntent JSONL row must be an object at {path}:{row_number}")
    return TradeIntent.from_dict(row)


def load_bundles(paths: Iterable[Path]) -> list[DecisionBundle]:
    bundles: list[DecisionBundle] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                bundles.append(
                    _bundle_from_line(line, path=path, row_number=line_number)
                )
    return bundles


def _load_cursor_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") not in {
        CURSOR_SCHEMA_VERSION,
        LEGACY_CURSOR_SCHEMA_VERSION,
    }:
        raise ValueError(f"invalid WCIR materializer cursor: {state_path}")
    if raw.get("schema_version") == CURSOR_SCHEMA_VERSION:
        required = {
            "cursor_epoch_id",
            "epoch_started_at_utc",
            "recovery_owner",
            "stuck_slo_seconds",
        }
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(
                f"WCIR cursor epoch metadata is incomplete: {state_path}: {missing}"
            )
        if int(raw["stuck_slo_seconds"]) <= 0:
            raise ValueError("WCIR cursor stuck SLO must be positive")
        predecessor = raw.get("predecessor_epoch_id")
        if predecessor:
            receipt_path = Path(str(raw.get("recovery_receipt_path") or ""))
            receipt_hash = str(raw.get("recovery_receipt_sha256") or "")
            if not receipt_path.is_absolute() or not receipt_path.is_file():
                raise ValueError("recovered WCIR cursor requires an immutable receipt")
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_hash:
                raise ValueError("WCIR cursor recovery receipt hash mismatch")
    return raw


def _epoch_id(path: Path, device: int, inode: int) -> str:
    return cursor_epoch_identity(path, device=device, inode=inode)


def _epoch_metadata(
    state: dict[str, Any] | None,
    *,
    resolved: Path,
    device: int,
    inode: int,
) -> dict[str, Any]:
    if state is None:
        return {
            "cursor_epoch_id": _epoch_id(resolved, device, inode),
            "predecessor_epoch_id": None,
            "recovery_receipt_path": None,
            "recovery_receipt_sha256": None,
            "epoch_started_at_utc": _utc_now(),
            "recovery_owner": CURSOR_RECOVERY_OWNER,
            "stuck_slo_seconds": CURSOR_STUCK_SLO_SECONDS,
            "migrated_from_schema_version": None,
        }
    if state["schema_version"] == LEGACY_CURSOR_SCHEMA_VERSION:
        return {
            "cursor_epoch_id": _epoch_id(resolved, device, inode),
            "predecessor_epoch_id": None,
            "recovery_receipt_path": None,
            "recovery_receipt_sha256": None,
            "epoch_started_at_utc": state.get("updated_at_utc") or _utc_now(),
            "recovery_owner": CURSOR_RECOVERY_OWNER,
            "stuck_slo_seconds": CURSOR_STUCK_SLO_SECONDS,
            "migrated_from_schema_version": LEGACY_CURSOR_SCHEMA_VERSION,
        }
    return {
        key: state.get(key)
        for key in (
            "cursor_epoch_id",
            "predecessor_epoch_id",
            "recovery_receipt_path",
            "recovery_receipt_sha256",
            "epoch_started_at_utc",
            "recovery_owner",
            "stuck_slo_seconds",
            "migrated_from_schema_version",
        )
    }


def _guard_hash(handle, offset: int) -> tuple[int, str]:
    guard_start = max(0, offset - CURSOR_GUARD_BYTES)
    handle.seek(guard_start)
    payload = handle.read(offset - guard_start)
    return guard_start, hashlib.sha256(payload).hexdigest()


def _load_cursor_batch(
    path: Path,
    state_path: Path,
    *,
    max_rows: int,
    max_bytes: int,
    parse_line: Callable[..., Any] = _bundle_from_line,
) -> tuple[list[Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if max_rows <= 0 or max_bytes <= 0:
        raise ValueError("cursor batch limits must be positive")
    resolved = path.resolve()
    state = _load_cursor_state(state_path)
    with resolved.open("rb") as handle:
        source_stat = os.fstat(handle.fileno())
        snapshot = {
            "path": str(resolved),
            "device": source_stat.st_dev,
            "inode": source_stat.st_ino,
            "size_bytes": source_stat.st_size,
            "mtime_ns": source_stat.st_mtime_ns,
        }
        epoch = _epoch_metadata(
            state,
            resolved=resolved,
            device=source_stat.st_dev,
            inode=source_stat.st_ino,
        )
        if (
            state is not None
            and state.get("schema_version") == CURSOR_SCHEMA_VERSION
            and state.get("cursor_epoch_id")
            != _epoch_id(resolved, source_stat.st_dev, source_stat.st_ino)
        ):
            raise ValueError("WCIR cursor epoch identity does not match its input inode")
        if state is None:
            offset_before = 0
            processed_rows_before = 0
        else:
            if state.get("input_path") != str(resolved):
                raise ValueError("WCIR cursor input path changed")
            if (
                int(state.get("device", -1)) != source_stat.st_dev
                or int(state.get("inode", -1)) != source_stat.st_ino
            ):
                raise ValueError("WCIR cursor input inode changed; explicit recovery required")
            offset_before = int(state.get("offset_bytes", -1))
            processed_rows_before = int(state.get("processed_rows", -1))
            if offset_before < 0 or processed_rows_before < 0:
                raise ValueError("WCIR cursor contains invalid negative values")
            if source_stat.st_size < offset_before:
                raise ValueError("WCIR journal truncated behind the canonical cursor")
            guard_start, guard_sha = _guard_hash(handle, offset_before)
            if (
                int(state.get("guard_start", -1)) != guard_start
                or state.get("guard_sha256") != guard_sha
            ):
                raise ValueError("WCIR journal prefix changed behind the canonical cursor")

        handle.seek(offset_before)
        records: list[Any] = []
        batch_bytes = 0
        while len(records) < max_rows and handle.tell() < source_stat.st_size:
            line_start = handle.tell()
            raw_line = handle.readline()
            line_end = handle.tell()
            if line_end > source_stat.st_size or not raw_line.endswith(b"\n"):
                handle.seek(line_start)
                break
            if batch_bytes + len(raw_line) > max_bytes:
                if not records:
                    raise ValueError(
                        "next complete WCIR JSONL row exceeds --max-new-bytes"
                    )
                handle.seek(line_start)
                break
            batch_bytes += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"invalid UTF-8 JSONL at {resolved} byte {line_start}"
                ) from exc
            records.append(
                parse_line(
                    line,
                    path=resolved,
                    row_number=processed_rows_before + len(records) + 1,
                )
            )
        offset_after = handle.tell()

    cursor_before = {
        "offset_bytes": offset_before,
        "processed_rows": processed_rows_before,
        **epoch,
    }
    cursor_after = {
        "offset_bytes": offset_after,
        "processed_rows": processed_rows_before + len(records),
        "batch_rows": len(records),
        "batch_bytes": offset_after - offset_before,
        "remaining_bytes_at_snapshot": source_stat.st_size - offset_after,
        **epoch,
    }
    return records, snapshot, cursor_before, cursor_after


def _cursor_state_payload(
    source_path: Path,
    cursor_after: dict[str, Any],
    expected_snapshot: dict[str, Any],
) -> dict[str, Any]:
    resolved = source_path.resolve()
    with resolved.open("rb") as handle:
        source_stat = os.fstat(handle.fileno())
        if (
            source_stat.st_dev != int(expected_snapshot["device"])
            or source_stat.st_ino != int(expected_snapshot["inode"])
        ):
            raise RuntimeError("WCIR journal rotated before cursor commit")
        offset = int(cursor_after["offset_bytes"])
        if source_stat.st_size < offset:
            raise RuntimeError("WCIR journal truncated before cursor commit")
        guard_start, guard_sha = _guard_hash(handle, offset)
    return {
        "schema_version": CURSOR_SCHEMA_VERSION,
        "input_path": str(resolved),
        "device": source_stat.st_dev,
        "inode": source_stat.st_ino,
        "offset_bytes": offset,
        "processed_rows": int(cursor_after["processed_rows"]),
        "guard_start": guard_start,
        "guard_sha256": guard_sha,
        "source_size_observed": source_stat.st_size,
        "source_mtime_ns_observed": source_stat.st_mtime_ns,
        "updated_at_utc": _utc_now(),
        **{
            key: cursor_after.get(key)
            for key in (
                "cursor_epoch_id",
                "predecessor_epoch_id",
                "recovery_receipt_path",
                "recovery_receipt_sha256",
                "epoch_started_at_utc",
                "recovery_owner",
                "stuck_slo_seconds",
                "migrated_from_schema_version",
            )
        },
    }


def _write_cursor_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_cursor_state(state_path)
    if existing is not None and existing.get("schema_version") == CURSOR_SCHEMA_VERSION:
        if existing.get("cursor_epoch_id") != payload.get("cursor_epoch_id"):
            raise RuntimeError(
                "refusing to replace a cursor with a different epoch in place"
            )
        if int(payload["offset_bytes"]) < int(existing.get("offset_bytes", -1)):
            raise RuntimeError("refusing to move a cursor epoch backwards")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, state_path)
    finally:
        temp_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", action="append", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument(
        "--expected-db",
        type=Path,
        default=Path("/Volumes/jrs/pm_agents/runtime/weather.db"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--max-new-rows", type=int, default=5000)
    parser.add_argument("--max-new-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--trade-intents", type=Path)
    parser.add_argument("--trade-intent-state", type=Path)
    parser.add_argument("--max-new-intents", type=int, default=5000)
    parser.add_argument(
        "--max-new-intent-bytes", type=int, default=64 * 1024 * 1024
    )
    parser.add_argument("--max-settlement-updates", type=int, default=5000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.trade_intents) != bool(args.trade_intent_state):
        raise ValueError(
            "--trade-intents and --trade-intent-state must be provided together"
        )
    cursor_before = None
    cursor_after = None
    intent_cursor_before = None
    intent_cursor_after = None
    intent_input_snapshot: list[dict[str, Any]] = []
    intents: list[TradeIntent] = []
    if args.state:
        if len(args.bundles) != 1:
            raise ValueError("cursor mode requires exactly one --bundles path")
        bundles, snapshot, cursor_before, cursor_after = _load_cursor_batch(
            args.bundles[0],
            args.state,
            max_rows=args.max_new_rows,
            max_bytes=args.max_new_bytes,
        )
        input_snapshot = [snapshot]
    else:
        input_snapshot = _input_snapshot(args.bundles)
        bundles = load_bundles(args.bundles)
        if _input_snapshot(args.bundles) != input_snapshot:
            raise RuntimeError(
                "decision bundle journal changed while reading; retry with a stable snapshot"
            )
    if not bundles and not args.state:
        raise ValueError("no decision bundles found")
    if args.trade_intents:
        intents, intent_snapshot, intent_cursor_before, intent_cursor_after = (
            _load_cursor_batch(
                args.trade_intents,
                args.trade_intent_state,
                max_rows=args.max_new_intents,
                max_bytes=args.max_new_intent_bytes,
                parse_line=_intent_from_line,
            )
        )
        intent_input_snapshot = [intent_snapshot]
    if args.apply:
        bridge = CanonicalCandidateBridge(
            args.db, expected_db_path=args.expected_db
        )
        mode = "canonical_incremental_apply"
        result = bridge.append(
            bundles,
            attach_candidate_settlements=True,
            max_candidate_settlement_updates=args.max_settlement_updates,
        )
        intent_result = bridge.append_trade_intents(intents) if args.trade_intents else None
        db_path = bridge.db_path
        settlements_attached = result["settlements_attached"]
        if bundles:
            funnels = bridge.candidate_funnels(
                bundle.signal_candidate.candidate_id for bundle in bundles
            )
        else:
            funnels = {
                "signal_funnel": {
                    "unit": "expression_checkpoint",
                    "raw_candidates": 0,
                    "scored_candidates": 0,
                    "blocked_candidates": 0,
                    "policy_selected": 0,
                },
                "evidence_funnel": {
                    "unit": "expression_checkpoint",
                    "pit_market": 0,
                    "mapped_expression": 0,
                    "executable_expression": 0,
                    "intent": "reported_from_intent_journal",
                    "fill": "not_available_phase2",
                },
            }
    else:
        with tempfile.TemporaryDirectory(prefix="wcir-canonical-dry-run-") as tmp:
            bridge = TemporaryCanonicalBridge(Path(tmp) / "weather.db")
            result = bridge.append(bundles)
            intent_result = (
                bridge.append_trade_intents(intents) if args.trade_intents else None
            )
            funnels = bridge.candidate_funnels(
                bundle.signal_candidate.candidate_id for bundle in bundles
            )
        mode = "isolated_dry_run"
        db_path = None
        settlements_attached = 0
    candidate_ids = sorted(
        {bundle.signal_candidate.candidate_id for bundle in bundles}
    )
    target_dates = Counter(bundle.signal_candidate.target_date for bundle in bundles)
    decision_timestamps = [
        bundle.signal_candidate.decision_ts_utc for bundle in bundles
    ]
    summary: dict[str, Any] = {
        "schema_version": "weather_city_canonical_materialization_v1",
        "mode": mode,
        "input_paths": [str(path.resolve()) for path in args.bundles],
        "input_snapshot": input_snapshot,
        "input_bundle_rows": len(bundles),
        "input_unique_candidate_id_hash": canonical_json_hash(candidate_ids),
        "input_target_date_counts": dict(sorted(target_dates.items())),
        "input_target_date_min": min(target_dates) if target_dates else None,
        "input_target_date_max": max(target_dates) if target_dates else None,
        "input_decision_ts_utc_min": min(decision_timestamps)
        if decision_timestamps
        else None,
        "input_decision_ts_utc_max": max(decision_timestamps)
        if decision_timestamps
        else None,
        "cities": dict(sorted(Counter(
            bundle.signal_candidate.city for bundle in bundles
        ).items())),
        "canonical_reconciliation": result,
        "trade_intent_reconciliation": intent_result,
        "input_trade_intent_rows": len(intents),
        "input_trade_intent_paths": (
            [str(args.trade_intents.resolve())] if args.trade_intents else []
        ),
        "input_trade_intent_snapshot": intent_input_snapshot,
        "settlements_attached": settlements_attached,
        "settlement_update_limit": result["settlement_update_limit"],
        "settlement_update_limit_reached": result[
            "settlement_update_limit_reached"
        ],
        **funnels,
        "execution_projection": {
            "plan": "not_created_shadow",
            "order": "not_created_shadow",
            "fill": "not_created_shadow",
            "pnl": "not_computed_without_fill",
            "settlement_label": "canonical_condition_join_only",
        },
    }
    if db_path is not None:
        stat = db_path.stat()
        summary["canonical_db"] = {
            "requested_path": str(args.db),
            "resolved_path": str(db_path),
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
    if args.state:
        summary["cursor"] = {
            "state_path": str(args.state.resolve()),
            "before": cursor_before,
            "after": cursor_after,
            "delivery_semantics": "at_least_once_idempotent",
        }
    if args.trade_intents:
        summary["trade_intent_cursor"] = {
            "state_path": str(args.trade_intent_state.resolve()),
            "before": intent_cursor_before,
            "after": intent_cursor_after,
            "delivery_semantics": "at_least_once_idempotent",
        }
    summary["summary_hash"] = canonical_json_hash(summary)
    summary["generated_at_utc"] = _utc_now()
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.apply and args.state:
        _write_cursor_state(
            args.state,
            _cursor_state_payload(
                args.bundles[0], cursor_after or {}, input_snapshot[0]
            ),
        )
    if args.apply and args.trade_intents:
        _write_cursor_state(
            args.trade_intent_state,
            _cursor_state_payload(
                args.trade_intents,
                intent_cursor_after or {},
                intent_input_snapshot[0],
            ),
        )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
