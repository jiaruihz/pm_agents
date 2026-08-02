from __future__ import annotations

from scripts.ops.weather_data_feed_prod_health_check import (
    latest_forecast_curve_capture,
    latest_orderbook_snapshot,
    read_jsonl_tail,
)


def test_latest_capture_lookup_prefers_newest_nonempty_partition(tmp_path) -> None:
    old = tmp_path / "2026-08-01"
    new = tmp_path / "2026-08-02"
    empty = tmp_path / "2026-08-03"
    old.mkdir()
    new.mkdir()
    empty.mkdir()
    (old / "orderbook_snapshot_old.jsonl.gz").write_bytes(b"old")
    expected_book = new / "orderbook_snapshot_new.jsonl.gz"
    expected_book.write_bytes(b"new")
    (old / "forecast_hourly_curves_old.jsonl").write_text("old\n")
    expected_curve = new / "forecast_hourly_curves_new.jsonl"
    expected_curve.write_text("new\n")

    assert latest_orderbook_snapshot(tmp_path) == expected_book
    assert latest_forecast_curve_capture(tmp_path) == expected_curve


def test_read_jsonl_tail_reads_only_requested_suffix(tmp_path) -> None:
    journal = tmp_path / "history.jsonl"
    journal.write_text("".join(f'{{"row": {value}}}\n' for value in range(100)))

    rows = read_jsonl_tail(journal, 3)

    assert [row["row"] for row in rows] == [97, 98, 99]
    assert [row["_tail_line_index"] for row in rows] == [1, 2, 3]
