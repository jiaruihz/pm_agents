from __future__ import annotations

import time
from datetime import datetime, timezone

from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.reports import _paired_rows
from us_fast_weather_lab.storage import ClockState, EvidenceStore


def test_invalid_true_first_seen_cannot_be_replaced_by_valid_duplicate(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "runtime")
    store.start_run(config_hash="abc", vantage_id="TEST")
    reference_ns = int(datetime(2026, 8, 28, 11, tzinfo=timezone.utc).timestamp() * 1e9)
    event = metar_event("METAR KJFK 281051Z 05003KT 10SM 22/21 A2998", reference_ns=reference_ns)
    assert event

    def capture_and_record(source_id: str, *, valid: bool) -> None:
        store.set_clock_state(ClockState(1.0 if valid else 80.0, valid))
        capture = store.capture_transport(
            source_id=source_id,
            endpoint="example.test",
            topic=source_id,
            payload=f"{source_id}-{time.time_ns()}".encode(),
            wall_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
        )
        store.record_observation(
            capture=capture,
            event=event,
            source_id=source_id,
            vantage_id="TEST",
            notification_ns=None,
            fetch_started_ns=None,
            fetch_finished_ns=None,
            decoded_ns=time.time_ns(),
        )

    capture_and_record("SOURCE_A", valid=False)
    capture_and_record("SOURCE_A", valid=True)
    capture_and_record("SOURCE_B", valid=True)

    assert len(_paired_rows(store._conn, valid_only=False)) == 1  # noqa: SLF001
    assert _paired_rows(store._conn, valid_only=True) == []  # noqa: SLF001
    store.close()
