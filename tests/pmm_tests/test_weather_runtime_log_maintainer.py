from __future__ import annotations

from scripts.ops.weather_runtime_log_maintainer import compact_log, maintain


def test_compact_log_keeps_recent_complete_lines(tmp_path) -> None:
    path = tmp_path / "runner.log"
    path.write_bytes(b"old-line\n" + b"middle-line\n" + b"recent-line\n")

    result = compact_log(path, max_bytes=20, retain_bytes=18)

    assert result is not None
    assert path.read_bytes() == b"recent-line\n"
    assert result["before_bytes"] > result["after_bytes"]


def test_maintain_only_bounds_plain_log_files(tmp_path) -> None:
    log = tmp_path / "runner.log"
    raw = tmp_path / "orders.jsonl"
    log.write_bytes(b"x" * 100)
    raw.write_bytes(b"y" * 100)

    summary = maintain([tmp_path], max_bytes=50, retain_bytes=10)

    assert summary["status"] == "ok"
    assert log.stat().st_size == 10
    assert raw.stat().st_size == 100
    assert summary["reclaimed_bytes"] == 90
