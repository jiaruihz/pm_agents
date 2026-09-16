from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from weather_data_feed.models import ObservationRecord
from weather_data_feed.observation_sources.aliases import normalize_source_name


class ObservationSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservationSourceRequest:
    city: str
    station_or_feed: str
    target_date: str
    timezone_name: str
    source_key: str
    max_age_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationSourceResult:
    source_key: str
    status: str
    records: tuple[ObservationRecord, ...] = ()
    fetched_at_utc: str = ""
    latency_ms: float | None = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ObservationSourceAdapter(Protocol):
    source_key: str

    def fetch(self, request: ObservationSourceRequest) -> ObservationSourceResult:
        ...


class SourceRouter:
    def __init__(self) -> None:
        self._adapters: dict[str, ObservationSourceAdapter] = {}

    def register(self, adapter: ObservationSourceAdapter) -> None:
        source_key = normalize_source_name(adapter.source_key)
        if not source_key:
            raise ObservationSourceError("adapter source_key is blank")
        self._adapters[source_key] = adapter

    def adapter_names(self) -> list[str]:
        return sorted(self._adapters)

    def fetch(self, request: ObservationSourceRequest) -> ObservationSourceResult:
        source_key = normalize_source_name(request.source_key)
        adapter = self._adapters.get(source_key)
        if adapter is None:
            raise ObservationSourceError(f"no observation source adapter registered for {source_key!r}")
        normalized_request = ObservationSourceRequest(
            city=request.city,
            station_or_feed=request.station_or_feed,
            target_date=request.target_date,
            timezone_name=request.timezone_name,
            source_key=source_key,
            max_age_seconds=request.max_age_seconds,
            metadata=request.metadata,
        )
        return adapter.fetch(normalized_request)
