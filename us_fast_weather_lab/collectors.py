"""Common collector interface and payload-to-event ingestion helper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from us_fast_weather_lab.model import events_from_json, events_from_tac, sniff_payload
from us_fast_weather_lab.storage import EvidenceStore, TransportCapture


class Collector(Protocol):
    source_id: str

    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class PayloadContext:
    source_id: str
    vantage_id: str
    notification_ns: int | None
    fetch_started_ns: int | None
    fetch_finished_ns: int | None
    content_type: str = ""
    content_encoding: str = ""
    evidence_status: str = "actionable"


def ingest_payload(
    store: EvidenceStore,
    capture: TransportCapture,
    payload: bytes,
    *,
    stations: set[str],
    context: PayloadContext,
) -> tuple[str, int]:
    sniffed, decoded_payload = sniff_payload(payload, context.content_type, context.content_encoding)
    events = []
    try:
        if sniffed == "json":
            events = events_from_json(decoded_payload, reference_ns=capture.wall_ns, stations=stations)
        elif sniffed == "tac_or_wmo_text":
            events = events_from_tac(decoded_payload, reference_ns=capture.wall_ns, stations=stations)
    except Exception:  # noqa: BLE001
        return f"{sniffed}_decode_error", 0
    import time

    decoded_ns = time.time_ns()
    for event in events:
        store.record_observation(
            capture=capture,
            event=event,
            source_id=context.source_id,
            vantage_id=context.vantage_id,
            notification_ns=context.notification_ns,
            fetch_started_ns=context.fetch_started_ns,
            fetch_finished_ns=context.fetch_finished_ns,
            decoded_ns=decoded_ns,
            evidence_status=context.evidence_status,
        )
    return sniffed, len(events)
