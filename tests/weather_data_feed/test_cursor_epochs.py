from __future__ import annotations

import json
from pathlib import Path

import pytest

from weather_data_feed.cursor_epochs import (
    CursorEpoch,
    CursorEpochError,
    cursor_epoch_identity,
    materializer_successor_state,
    plan_rollover,
    write_immutable_receipt,
)


def _epoch(name: str, inode: int, root: Path, offset: int = 0) -> CursorEpoch:
    return CursorEpoch(name, inode, "guard", offset, str(root / f"{name}.state"), str(root / f"{name}.receipt"), "weather-data", "15m")


def test_rollover_requires_new_inode_new_paths_zero_offset_and_identical_overlap(tmp_path: Path) -> None:
    receipt = plan_rollover(_epoch("old", 1, tmp_path, 99), _epoch("new", 2, tmp_path), predecessor_boundary=[b"a\n"], successor_boundary=[b"a\n"])
    assert receipt["boundary_overlap_sha256"]
    with pytest.raises(CursorEpochError, match="inode"):
        plan_rollover(_epoch("old", 1, tmp_path), _epoch("new", 1, tmp_path), predecessor_boundary=[], successor_boundary=[])
    with pytest.raises(CursorEpochError, match="hash-identical"):
        plan_rollover(_epoch("old", 1, tmp_path), _epoch("new", 2, tmp_path), predecessor_boundary=[b"a"], successor_boundary=[b"b"])


def test_receipt_is_create_only_and_immutable(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    write_immutable_receipt(path, {"ok": True})
    assert json.loads(path.read_text()) == {"ok": True}
    with pytest.raises(CursorEpochError, match="already exists"):
        write_immutable_receipt(path, {"ok": False})


def test_reviewed_rollover_builds_a_distinct_zero_offset_materializer_state(
    tmp_path: Path,
) -> None:
    old_input = tmp_path / "old.jsonl"
    new_input = tmp_path / "new.jsonl"
    old_input.write_bytes(b'{"id": 1}\n')
    new_input.write_bytes(b'{"id": 1}\n')
    old_stat = old_input.stat()
    new_stat = new_input.stat()
    predecessor = CursorEpoch(
        epoch_id=cursor_epoch_identity(
            old_input, device=old_stat.st_dev, inode=old_stat.st_ino
        ),
        inode=old_stat.st_ino,
        guard="guard",
        eof_offset=old_stat.st_size,
        state_path=str(tmp_path / "old.state"),
        receipt_path=str(tmp_path / "old.receipt"),
        owner="canonical_refresh",
        slo="15m",
        input_path=str(old_input),
        device=old_stat.st_dev,
    )
    successor = CursorEpoch(
        epoch_id=cursor_epoch_identity(
            new_input, device=new_stat.st_dev, inode=new_stat.st_ino
        ),
        inode=new_stat.st_ino,
        guard="guard",
        eof_offset=0,
        state_path=str(tmp_path / "new.state"),
        receipt_path=str(tmp_path / "new.receipt"),
        owner="canonical_refresh",
        slo="15m",
        input_path=str(new_input),
        device=new_stat.st_dev,
    )
    receipt = plan_rollover(
        predecessor,
        successor,
        predecessor_boundary=[old_input.read_bytes()],
        successor_boundary=[new_input.read_bytes()],
    )
    write_immutable_receipt(Path(successor.receipt_path), receipt)
    state = materializer_successor_state(
        predecessor,
        successor,
        receipt_path=Path(successor.receipt_path),
    )
    assert state["offset_bytes"] == 0
    assert state["cursor_epoch_id"] == successor.epoch_id
    assert state["predecessor_epoch_id"] == predecessor.epoch_id
    assert state["stuck_slo_seconds"] == 900
