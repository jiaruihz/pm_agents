from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from scripts.ops.materialize_weather_historical_replay_manifest import (
    _read_registered_input,
)
from weather_data_feed.historical_adapter import HistoricalAdapter


def _base(path: Path, identity: str = "d1") -> dict:
    stat = path.stat()
    return {
        "decision_id": identity,
        "source_snapshot_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "physical_ref": {
            "path": str(path.resolve()),
            "line": 1,
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
    }


def test_adapter_preserves_evidence_and_never_upgrades_gaps_or_missing_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"source": "sealed"}\n', encoding="utf-8")
    rows = [
        {**_base(path), "event_envelope": {}, "decision_context": {}, "model_output": {}},
        {**_base(path, "model"), "model_output": {}},
        {**_base(path, "summary"), "summary": "old"},
        {**_base(path, "gap"), "model_output": {}, "ws_gap": True},
        {"source_snapshot_hash": hashlib.sha256(path.read_bytes()).hexdigest(), "physical_ref": {}},
    ]
    output, audit = HistoricalAdapter(allowed_roots=(tmp_path,)).adapt(rows)
    assert [item.classification for item in output] == ["full_decision", "model_only", "legacy_summary_only", "blocked", "blocked"]
    assert output[0].source_snapshot_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output[0].physical_ref == _base(path)["physical_ref"]
    assert audit.scanned == audit.canonical + audit.duplicate + audit.blocked == 5


def test_duplicate_conflict_and_unparented_revision_are_not_canonical(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"source": "sealed"}\n', encoding="utf-8")
    full = {**_base(path), "event_envelope": {}, "decision_context": {}, "model_output": {}}
    rows = [full, dict(full), {**_base(path, "badrev"), "revision": 2, "model_output": {}}]
    output, audit = HistoricalAdapter(allowed_roots=(tmp_path,)).adapt(rows)
    assert output[1].blocker == "duplicate_identity"
    assert output[2].blocker == "missing_revision_parent"
    assert audit.canonical == 1 and audit.duplicate == 1 and audit.blocked == 1


def test_registered_historical_json_documents_preserve_record_index_and_quarantine(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([{"decision_id": "d1"}, "bad-record"]), encoding="utf-8")
    quarantine = tmp_path / "quarantine.jsonl"
    rows, audit = _read_registered_input(path, quarantine_path=quarantine)
    assert audit.scanned == audit.valid + audit.quarantined == 2
    assert rows[0].physical_record_index == 0
    assert json.loads(quarantine.read_text())["physical_record_index"] == 1

    enriched = dict(rows[0].row)
    stat = path.stat()
    enriched["source_snapshot_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    enriched["physical_ref"] = {
        "path": str(path.resolve()),
        "record_index": 0,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    enriched["summary"] = "legacy"
    output, _ = HistoricalAdapter(allowed_roots=(tmp_path,)).adapt([enriched])
    assert output[0].classification == "legacy_summary_only"


def test_registered_gzip_jsonl_is_streamed_and_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.jsonl.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(b'{"decision_id":"d1","summary":"legacy"}\n')
        handle.write(b'not-json\n')
    quarantine = tmp_path / "quarantine.jsonl"

    rows, audit = _read_registered_input(path, quarantine_path=quarantine)

    assert [row.row["decision_id"] for row in rows] == ["d1"]
    assert audit.scanned == audit.valid + audit.quarantined == 2
    record = json.loads(quarantine.read_text())
    assert record["compression"] == "gzip"
    assert record["physical_line"] == 2
    assert record["physical_byte_offset"] is None


def test_adapter_blocks_unverified_or_mismatched_physical_lineage(
    tmp_path: Path,
) -> None:
    registered = tmp_path / "registered"
    registered.mkdir()
    path = registered / "legacy.jsonl"
    path.write_text('{"source": "sealed"}\n', encoding="utf-8")
    valid = {**_base(path), "model_output": {}}

    relative = {**valid, "physical_ref": {**valid["physical_ref"], "path": "legacy.jsonl"}}
    bad_hash = {**valid, "decision_id": "bad-hash", "source_snapshot_hash": "0" * 64}
    unregistered = HistoricalAdapter().adapt([valid])[0][0]
    output, audit = HistoricalAdapter(allowed_roots=(registered,)).adapt(
        [relative, bad_hash]
    )

    assert unregistered.blocker == "unregistered_physical_root"
    assert [item.blocker for item in output] == [
        "physical_path_not_absolute",
        "source_snapshot_hash_mismatch",
    ]
    assert audit.blocked == 2 and audit.canonical == 0


def test_stream_adapter_can_drop_large_rows_and_resolve_forward_parent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"source": "sealed"}\n', encoding="utf-8")
    adapter = HistoricalAdapter(allowed_roots=(tmp_path,))
    stat = path.stat()
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    adapter.register_verified_source(
        path,
        source_hash,
        device=stat.st_dev,
        inode=stat.st_ino,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    child = {
        **_base(path, "child"),
        "revision": 1,
        "revision_parent_id": "parent",
        "model_output": {},
        "large": "x" * 1000,
    }
    parent = {**_base(path, "parent"), "model_output": {}}

    output, audit = adapter.adapt_stream(iter((child, parent)), retain_rows=False)

    assert [item.classification for item in output] == ["model_only", "model_only"]
    assert [item.row for item in output] == [{}, {}]
    assert audit.canonical == 2 and audit.blocked == 0


def test_stream_adapter_rejects_child_of_blocked_parent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"source": "sealed"}\n', encoding="utf-8")
    child = {
        **_base(path, "child"),
        "revision": 1,
        "revision_parent_id": "blocked-parent",
        "model_output": {},
    }
    blocked_parent = {
        **_base(path, "blocked-parent"),
        "model_output": {},
        "ws_gap": True,
    }

    output, audit = HistoricalAdapter(allowed_roots=(tmp_path,)).adapt_stream(
        iter((child, blocked_parent)), retain_rows=False
    )

    assert [item.blocker for item in output] == [
        "unknown_revision_parent",
        "websocket_gap",
    ]
    assert audit.canonical == 0 and audit.blocked == 2
