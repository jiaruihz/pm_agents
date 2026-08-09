from __future__ import annotations

from pathlib import Path

from weather_data_feed.jsonl_partitions import recent_jsonl_lines


def _write_lines(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")


def test_recent_jsonl_lines_spans_only_required_newest_shards(tmp_path) -> None:
    oldest = tmp_path / "2026-07-01" / "events.jsonl"
    middle = tmp_path / "2026-07-02" / "events.jsonl"
    newest = tmp_path / "2026-07-03" / "events.jsonl"
    _write_lines(oldest, ["old-1", "old-2"])
    _write_lines(middle, ["mid-1", "mid-2"])
    _write_lines(newest, ["new-1", "new-2"])

    assert recent_jsonl_lines((oldest, middle, newest), max_lines=3) == (
        "mid-2\n",
        "new-1\n",
        "new-2\n",
    )
    assert recent_jsonl_lines((oldest, middle, newest), max_lines=0) == ()
