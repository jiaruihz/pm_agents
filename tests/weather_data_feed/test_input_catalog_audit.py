from __future__ import annotations

import json
from pathlib import Path

import pytest

from weather_data_feed.input_catalog import InputCatalogError, JsonlInputCatalog


def test_invalid_json_utf8_and_non_object_fail_closed_by_default(tmp_path: Path) -> None:
    path = tmp_path / "source.jsonl"
    path.write_bytes(b'{"ok": true}\nnot json\n[1]\n\xff\n')
    with pytest.raises(InputCatalogError):
        list(JsonlInputCatalog._iter_path(path))


def test_explicit_quarantine_persists_physical_identity_and_conserves_scan(tmp_path: Path) -> None:
    path = tmp_path / "source.jsonl"
    path.write_bytes(b'{"ok": true}\nnot json\n[1]\n\xff\n')
    quarantine = tmp_path / "quarantine.jsonl"
    rows, audit = JsonlInputCatalog.audit_path(path, quarantine_path=quarantine)
    records = [json.loads(line) for line in quarantine.read_text().splitlines()]
    assert [row.row for row in rows] == [{"ok": True}]
    assert audit.scanned == audit.valid + audit.quarantined == 4
    assert {record["reason"].split(":")[0] for record in records} == {"invalid_json", "invalid_utf8", "json_value_is_not_object"}
    assert all(record["physical_path"] == str(path) and record["physical_line"] > 0 for record in records)
    assert all(record["physical_byte_offset"] >= 0 and record["file_line_sha256"] for record in records)
    assert all(record["source_snapshot_size_bytes"] == path.stat().st_size for record in records)
    assert all(record["source_snapshot_sha256"] == record["file_sha256"] for record in records)


def test_reverse_journal_reader_has_the_same_fail_closed_contract(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"first": 1}\n["not an object"]\n{"last": 2}\n', encoding="utf-8")
    with pytest.raises(InputCatalogError):
        list(JsonlInputCatalog.iter_path_reverse(path))
    quarantine = tmp_path / "reverse-quarantine.jsonl"
    rows = list(JsonlInputCatalog.iter_path_reverse(path, quarantine_path=quarantine))
    assert [item.row for item in rows] == [{"last": 2}, {"first": 1}]
    assert json.loads(quarantine.read_text())["physical_line"] == 2


def test_valid_forward_and_reverse_reads_are_streaming_and_memory_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "many.jsonl"
    path.write_text(
        "".join(json.dumps({"row": index}) + "\n" for index in range(50)),
        encoding="utf-8",
    )

    def _no_read_bytes(self: Path) -> bytes:
        raise AssertionError("catalog must not materialize a whole valid journal")

    monkeypatch.setattr(Path, "read_bytes", _no_read_bytes)
    forward = list(JsonlInputCatalog._iter_path(path))
    reverse = list(JsonlInputCatalog.iter_path_reverse(path, block_size=17))
    assert [item.row["row"] for item in forward] == list(range(50))
    assert [item.row["row"] for item in reverse] == list(reversed(range(50)))


def test_reverse_reader_fails_closed_when_source_changes_mid_scan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "growing.jsonl"
    path.write_text('{"row": 1}\n{"row": 2}\n', encoding="utf-8")
    iterator = JsonlInputCatalog.iter_path_reverse(path, block_size=8)
    assert next(iterator).row == {"row": 2}
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"row": 3}\n')
    with pytest.raises(InputCatalogError, match="changed during reverse scan"):
        list(iterator)
