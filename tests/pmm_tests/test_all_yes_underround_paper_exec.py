from scripts.ops.all_yes_underround_paper_exec_v0 import recording_ttl_audit


def _basket(recorded_at_utc="2026-06-13T18:32:00+00:00", snapshot_ts_utc="2026-06-13T18:30:53Z"):
    return {
        "recorded_at_utc": recorded_at_utc,
        "snapshot_ts_utc": snapshot_ts_utc,
    }


def test_recording_ttl_audit_marks_fresh_basket_live_equivalent():
    audit = recording_ttl_audit(_basket(), 180)

    assert audit["ttl_equivalent"] is True
    assert audit["ttl_status"] == "live_equivalent"
    assert audit["recording_age_seconds"] == 67.0


def test_recording_ttl_audit_marks_stale_basket_observation_only():
    audit = recording_ttl_audit(_basket(recorded_at_utc="2026-06-13T18:53:26.208864+00:00"), 180)

    assert audit["ttl_equivalent"] is False
    assert audit["ttl_status"] == "stale_recording"
    assert audit["recording_age_seconds"] == 1353.209


def test_recording_ttl_audit_fails_closed_on_missing_timestamp():
    audit = recording_ttl_audit(_basket(snapshot_ts_utc=None), 180)

    assert audit["ttl_equivalent"] is False
    assert audit["ttl_status"] == "missing_timestamp"


def test_recording_ttl_audit_prefers_orderbook_fetched_at():
    basket = _basket(
        recorded_at_utc="2026-06-13T18:35:30+00:00",
        snapshot_ts_utc="2026-06-13T18:30:53Z",
    )
    basket["orderbook_fetched_at_utc_max"] = "2026-06-13T18:35:00Z"

    audit = recording_ttl_audit(basket, 180)

    assert audit["ttl_equivalent"] is True
    assert audit["ttl_status"] == "live_equivalent"
    assert audit["recording_age_seconds"] == 30.0
