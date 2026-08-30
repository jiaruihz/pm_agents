"""Append-only cursor epoch rollover contracts for immutable JSONL journals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
from datetime import datetime, timezone


class CursorEpochError(ValueError):
    pass


@dataclass(frozen=True)
class CursorEpoch:
    epoch_id: str
    inode: int
    guard: str
    eof_offset: int
    state_path: str
    receipt_path: str
    owner: str
    slo: str
    input_path: str | None = None
    device: int | None = None


def boundary_hash(lines: Iterable[bytes]) -> str:
    return sha256(b"".join(lines)).hexdigest()


def cursor_epoch_identity(input_path: Path, *, device: int, inode: int) -> str:
    body = {
        "schema_version": "weather_jsonl_cursor_epoch_v1",
        "input_path": str(Path(input_path).resolve()),
        "device": int(device),
        "inode": int(inode),
    }
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def plan_rollover(
    predecessor: CursorEpoch,
    successor: CursorEpoch,
    *,
    predecessor_boundary: Iterable[bytes],
    successor_boundary: Iterable[bytes],
) -> dict[str, Any]:
    """Validate a rollover; this function deliberately never mutates old state."""
    if predecessor.inode == successor.inode:
        raise CursorEpochError("predecessor and successor must not share inode")
    if predecessor.state_path == successor.state_path:
        raise CursorEpochError("predecessor and successor must not share state path")
    if predecessor.receipt_path == successor.receipt_path:
        raise CursorEpochError("predecessor and successor must not share receipt path")
    if successor.eof_offset != 0:
        raise CursorEpochError("new epoch must begin at offset zero")
    if not successor.owner or not successor.slo:
        raise CursorEpochError("successor recovery owner and stuck SLO are required")
    predecessor_rows = tuple(predecessor_boundary)
    successor_rows = tuple(successor_boundary)
    if not predecessor_rows or not successor_rows:
        raise CursorEpochError("cursor rollover requires a non-empty overlap boundary")
    old_hash = boundary_hash(predecessor_rows)
    new_hash = boundary_hash(successor_rows)
    if old_hash != new_hash:
        raise CursorEpochError("boundary overlap is not hash-identical")
    return {
        "schema_version": "weather_cursor_epoch_receipt_v1",
        "predecessor": asdict(predecessor),
        "successor": asdict(successor),
        "boundary_overlap_sha256": old_hash,
        "owner": successor.owner,
        "slo": successor.slo,
    }


def write_immutable_receipt(receipt_path: Path, receipt: dict[str, Any]) -> None:
    """Create, never overwrite, a rollover receipt."""
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=receipt_path.parent,
            prefix=f".{receipt_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, receipt_path)
    except FileExistsError as exc:
        raise CursorEpochError(f"receipt already exists: {receipt_path}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    receipt_path.chmod(0o444)


def _slo_seconds(value: str) -> int:
    text = str(value).strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600}
    if text.isdigit():
        seconds = int(text)
    elif len(text) > 1 and text[-1] in multipliers and text[:-1].isdigit():
        seconds = int(text[:-1]) * multipliers[text[-1]]
    else:
        raise CursorEpochError(f"invalid cursor stuck SLO: {value}")
    if seconds <= 0:
        raise CursorEpochError("cursor stuck SLO must be positive")
    return seconds


def materializer_successor_state(
    predecessor: CursorEpoch,
    successor: CursorEpoch,
    *,
    receipt_path: Path,
) -> dict[str, Any]:
    """Build a create-only zero-offset seed for a reviewed successor epoch."""

    if not successor.input_path or successor.device is None:
        raise CursorEpochError("successor requires input_path and device")
    input_path = Path(successor.input_path)
    if not input_path.is_absolute() or not input_path.is_file():
        raise CursorEpochError("successor input_path must be an existing absolute file")
    stat = input_path.stat()
    if stat.st_dev != successor.device or stat.st_ino != successor.inode:
        raise CursorEpochError("successor input identity does not match the file")
    expected_epoch_id = cursor_epoch_identity(
        input_path, device=stat.st_dev, inode=stat.st_ino
    )
    if successor.epoch_id != expected_epoch_id:
        raise CursorEpochError("successor epoch_id is not content-bound to its input")
    receipt_path = receipt_path.resolve()
    if receipt_path != Path(successor.receipt_path).resolve() or not receipt_path.is_file():
        raise CursorEpochError("successor recovery receipt is missing")
    receipt_hash = sha256(receipt_path.read_bytes()).hexdigest()
    empty_guard = sha256(b"").hexdigest()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "weather_jsonl_cursor_epoch_v1",
        "input_path": str(input_path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "offset_bytes": 0,
        "processed_rows": 0,
        "guard_start": 0,
        "guard_sha256": empty_guard,
        "source_size_observed": stat.st_size,
        "source_mtime_ns_observed": stat.st_mtime_ns,
        "updated_at_utc": now,
        "cursor_epoch_id": successor.epoch_id,
        "predecessor_epoch_id": predecessor.epoch_id,
        "recovery_receipt_path": str(receipt_path),
        "recovery_receipt_sha256": receipt_hash,
        "epoch_started_at_utc": now,
        "recovery_owner": successor.owner,
        "stuck_slo_seconds": _slo_seconds(successor.slo),
        "migrated_from_schema_version": None,
    }
