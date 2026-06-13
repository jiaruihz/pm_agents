from datetime import datetime, timezone

from scripts.ops.all_yes_underround_fresh_paper_cycle_v0 import snapshot_freshness


NOW = datetime(2026, 6, 13, 18, 33, 0, tzinfo=timezone.utc)


def _freshness(value, *, file_age=20, rows=1000, max_age=180):
    return snapshot_freshness(
        freshness_ts_utc=value,
        decision_ts=NOW,
        max_age_seconds=max_age,
        file_mtime=NOW.timestamp() - file_age,
        min_file_stable_seconds=10,
        rows=rows,
        min_snapshot_rows=500,
    )


def test_snapshot_freshness_accepts_snapshot_within_ttl():
    result = _freshness("2026-06-13T18:30:53Z")

    assert result["fresh"] is True
    assert result["reason"] == "fresh"
    assert result["snapshot_age_seconds"] == 127.0


def test_snapshot_freshness_rejects_stale_snapshot():
    result = _freshness("2026-06-13T18:29:59Z")

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_too_old"
    assert result["snapshot_age_seconds"] == 181.0


def test_snapshot_freshness_fails_closed_on_missing_timestamp():
    result = _freshness(None)

    assert result["fresh"] is False
    assert result["reason"] == "missing_snapshot_ts"


def test_snapshot_freshness_rejects_file_still_being_written():
    result = _freshness("2026-06-13T18:30:53Z", file_age=2)

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_file_still_writing"


def test_snapshot_freshness_rejects_partial_snapshot_rows():
    result = _freshness("2026-06-13T18:30:53Z", rows=26)

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_rows_below_min"
