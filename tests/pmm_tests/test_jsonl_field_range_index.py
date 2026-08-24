from __future__ import annotations

import json

from src.platform.storage.jsonl_index import JsonlFieldRangeIndex


def _append(path, rows, *, complete=True):
    with path.open("ab") as handle:
        for row in rows:
            encoded = json.dumps(row).encode()
            handle.write(encoded + (b"\n" if complete else b""))


def test_index_reads_only_requested_key_and_tails_new_rows(tmp_path) -> None:
    journal = tmp_path / "rows.jsonl"
    index_path = tmp_path / "rows.index.json"
    _append(
        journal,
        [
            {"snapshot_file": "old", "value": 1},
            {"snapshot_file": "current", "value": 2},
        ],
    )
    index = JsonlFieldRangeIndex.load(journal, index_path, "snapshot_file")

    assert index.rows_for("current") == [{"snapshot_file": "current", "value": 2}]
    first_offset = index.indexed_offset

    _append(journal, [{"snapshot_file": "current", "value": 3}])
    reloaded = JsonlFieldRangeIndex.load(journal, index_path, "snapshot_file")
    assert reloaded.indexed_offset == first_offset
    assert reloaded.rows_for("current") == [
        {"snapshot_file": "current", "value": 2},
        {"snapshot_file": "current", "value": 3},
    ]


def test_index_does_not_advance_over_partial_tail(tmp_path) -> None:
    journal = tmp_path / "rows.jsonl"
    index_path = tmp_path / "rows.index.json"
    _append(journal, [{"snapshot_file": "ready", "value": 1}])
    index = JsonlFieldRangeIndex.load(journal, index_path, "snapshot_file")
    assert index.rows_for("ready")
    complete_offset = index.indexed_offset

    _append(journal, [{"snapshot_file": "later", "value": 2}], complete=False)
    index.update()
    assert index.indexed_offset == complete_offset
    assert index.rows_for("later") == []


def test_index_rebuilds_after_journal_replacement(tmp_path) -> None:
    journal = tmp_path / "rows.jsonl"
    index_path = tmp_path / "rows.index.json"
    _append(journal, [{"snapshot_file": "old", "value": 1}])
    index = JsonlFieldRangeIndex.load(journal, index_path, "snapshot_file")
    assert index.rows_for("old")

    replacement = tmp_path / "replacement.jsonl"
    _append(replacement, [{"snapshot_file": "new", "value": 2}])
    replacement.replace(journal)

    reloaded = JsonlFieldRangeIndex.load(journal, index_path, "snapshot_file")
    assert reloaded.rows_for("old") == []
    assert reloaded.rows_for("new") == [{"snapshot_file": "new", "value": 2}]
