from __future__ import annotations

import pytest

from weather_dashboard.ingest.state_checkpoints import build_state_checkpoint
from weather_feature_layer.store import EVENT_CHECKPOINT_KEY_COLUMNS, feature_frame_ref_for_row


def test_event_checkpoint_uses_trigger_aware_feature_identity_and_excludes_future_inputs() -> None:
    row = {
        "city": "Atlanta",
        "target_date": "2026-07-28",
        "trigger_event_id": "event-a",
        "as_of_ts_utc": "2026-07-28T12:00:04Z",
        "feature_schema_version": "feature_frame_v1",
        "feature_grain": "city_date_event_checkpoint",
        "feature_version_manifest": {"weather_state": "weather_state_v4"},
        "source_profile_id": "test",
        "pit_provenance": "live_capture",
        "builder_version": "test",
        "input_snapshot_id": "snapshot",
    }
    ref = feature_frame_ref_for_row(row, key_columns=EVENT_CHECKPOINT_KEY_COLUMNS)
    trigger = {"information_event_id": "event-a", "pit_lineage_class": "collector_exact"}
    checkpoint = build_state_checkpoint(
        city="Atlanta",
        target_date="2026-07-28",
        trigger_event=trigger,
        as_of_ts_utc="2026-07-28T12:00:04Z",
        feature_frame_ref=ref,
        input_events=[{**trigger, "available_at_utc": "2026-07-28T12:00:04Z"}],
        pit_provenance="live_capture",
    )

    assert checkpoint["checkpoint_status"] == "built"
    assert checkpoint["feature_row_id"] == ref["feature_row_id"]
    with pytest.raises(ValueError, match="after as_of"):
        build_state_checkpoint(
            city="Atlanta", target_date="2026-07-28", trigger_event=trigger,
            as_of_ts_utc="2026-07-28T12:00:04Z", feature_frame_ref=ref,
            input_events=[{**trigger, "available_at_utc": "2026-07-28T12:00:05Z"}],
            pit_provenance="live_capture",
        )
