from __future__ import annotations

import json

from scripts.ops.weather_partition_jsonl_journal import inspect_source, write_shards


def test_partition_writer_preserves_ordered_source_bytes(tmp_path) -> None:
    source = tmp_path / "opportunities.jsonl"
    rows = [
        {"ts_utc": "2026-08-09T23:59:00Z", "value": 1},
        {"ts_utc": "2026-08-10T00:01:00Z", "value": 2},
        {"ts_utc": "2026-08-10T00:02:00Z", "value": 3},
    ]
    raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    source.write_bytes(raw)
    destination = tmp_path / "shards"

    report = inspect_source(source, timestamp_field="ts_utc")
    write = write_shards(
        source,
        destination,
        filename="opportunities.jsonl",
        timestamp_field="ts_utc",
        expected=report,
    )

    paths = [destination / day / "opportunities.jsonl" for day in ("2026-08-09", "2026-08-10")]
    assert b"".join(path.read_bytes() for path in paths) == raw
    assert source.read_bytes() == raw
    assert write["shard_rows"] == 3
    assert write["ordered_shards_sha256"] == report["source_sha256"]


def test_partition_writer_rejects_date_regression(tmp_path) -> None:
    source = tmp_path / "opportunities.jsonl"
    source.write_text(
        '{"ts_utc":"2026-08-10T00:00:00Z"}\n'
        '{"ts_utc":"2026-08-09T23:59:00Z"}\n',
        encoding="utf-8",
    )
    report = inspect_source(source, timestamp_field="ts_utc")
    assert report["date_regressions"] == 1
