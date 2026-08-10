from __future__ import annotations

import json

from scripts.ops.weather_korea_first_seen_collector import read_appended_rows


def _write_row(path, sequence: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sequence": sequence}) + "\n", encoding="utf-8")


def test_read_appended_rows_follows_dated_shard_rollover(tmp_path) -> None:
    root = tmp_path / "live_cross_observations"
    first = root / "2026-08-09" / "high_frequency_observations.jsonl"
    _write_row(first, 1)

    rows, cursor, audit = read_appended_rows(root, None)
    assert [row["sequence"] for row in rows] == [1]
    assert audit["physical_path"] == str(first)

    with first.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"sequence": 2}) + "\n")
    rows, cursor, _audit = read_appended_rows(root, cursor)
    assert [row["sequence"] for row in rows] == [2]

    second = root / "2026-08-10" / "high_frequency_observations.jsonl"
    _write_row(second, 3)
    rows, cursor, audit = read_appended_rows(root, cursor)
    assert [row["sequence"] for row in rows] == [3]
    assert cursor["source_path"] == str(second)
    assert audit["reset_reason"] == "source_path_changed"
