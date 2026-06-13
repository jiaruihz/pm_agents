from datetime import datetime, timezone

from scripts.ops.all_yes_underround_fresh_paper_cycle_v0 import snapshot_freshness


NOW = datetime(2026, 6, 13, 18, 33, 0, tzinfo=timezone.utc)


def test_snapshot_freshness_accepts_snapshot_within_ttl():
    result = snapshot_freshness("2026-06-13T18:30:53Z", NOW, 180)

    assert result["fresh"] is True
    assert result["reason"] == "fresh"
    assert result["snapshot_age_seconds"] == 127.0


def test_snapshot_freshness_rejects_stale_snapshot():
    result = snapshot_freshness("2026-06-13T18:29:59Z", NOW, 180)

    assert result["fresh"] is False
    assert result["reason"] == "snapshot_too_old"
    assert result["snapshot_age_seconds"] == 181.0


def test_snapshot_freshness_fails_closed_on_missing_timestamp():
    result = snapshot_freshness(None, NOW, 180)

    assert result["fresh"] is False
    assert result["reason"] == "missing_snapshot_ts"
