"""Bounded AWC batch poller and once-per-minute full-cache verifier."""

from __future__ import annotations

import gzip
import json
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from us_fast_weather_lab.collectors import PayloadContext, ingest_payload
from us_fast_weather_lab.storage import EvidenceStore


@dataclass(frozen=True)
class AwcConfig:
    api_url: str
    cache_url: str
    poll_interval_seconds: float
    cache_interval_seconds: float
    user_agent: str
    max_requests_per_minute: int


def _read_response(response: httpx.Response) -> tuple[int | None, int | None, bytes]:
    first_byte_ns: int | None = None
    first_byte_monotonic_ns: int | None = None
    chunks: list[bytes] = []
    # Preserve the actual wire representation. httpx.iter_bytes() decodes
    # Content-Encoding while retaining the response header, which would make
    # a later raw replay attempt to decompress an already-decoded body.
    for chunk in response.iter_raw():
        if first_byte_ns is None:
            first_byte_ns = time.time_ns()
            first_byte_monotonic_ns = time.monotonic_ns()
        chunks.append(chunk)
    return first_byte_ns, first_byte_monotonic_ns, b"".join(chunks)


class AwcCollector:
    source_id = "AWC"

    def __init__(
        self,
        store: EvidenceStore,
        *,
        config: AwcConfig,
        stations: set[str],
        vantage_id: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        if config.poll_interval_seconds < 60.0 / config.max_requests_per_minute:
            raise ValueError("AWC poll interval would exceed the configured official rate boundary")
        self.store = store
        self.config = config
        self.stations = stations
        self.vantage_id = vantage_id
        self.timeout_seconds = timeout_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.api_requests = 0
        self.cache_requests = 0
        self.errors = 0
        self.actionable_events = 0
        self.client_resets = 0
        self._client_reset_requested = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="awc-bounded-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(10.0, self.timeout_seconds + 2.0))

    def _run(self) -> None:
        next_api = time.monotonic()
        next_cache = time.monotonic()
        client = self._new_client()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_api:
                    self._fetch_api(client)
                    next_api = max(next_api + self.config.poll_interval_seconds, time.monotonic())
                    client = self._reset_client_if_requested(client)
                if now >= next_cache:
                    self._fetch_cache(client)
                    next_cache = max(next_cache + self.config.cache_interval_seconds, time.monotonic())
                    client = self._reset_client_if_requested(client)
                self._stop.wait(timeout=max(0.05, min(next_api, next_cache) - time.monotonic()))
        finally:
            client.close()

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_seconds,
            headers={"User-Agent": self.config.user_agent, "Accept": "application/json, application/xml"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    def _reset_client_if_requested(self, client: httpx.Client) -> httpx.Client:
        if not self._client_reset_requested:
            return client
        client.close()
        self._client_reset_requested = False
        self.client_resets += 1
        self.store.record_access(
            source_family="AWC",
            endpoint="aviationweather.gov",
            phase="http_client_reset",
            status="ok",
            request={"reason": "transport_error", "reset_number": self.client_resets},
            response={"new_pool": True},
        )
        return self._new_client()

    def _request(
        self, client: httpx.Client, *, source_id: str, url: str, params: dict[str, str] | None
    ) -> tuple[Any, bytes, int, int, str, str] | None:
        started_ns = time.time_ns()
        endpoint = httpx.URL(url).host or url
        try:
            with client.stream("GET", url, params=params) as response:
                first_byte_ns, first_byte_monotonic_ns, payload = _read_response(response)
                finished_ns = time.time_ns()
                response.raise_for_status()
                first_byte_ns = first_byte_ns or finished_ns
                capture = self.store.capture_transport(
                    source_id=source_id,
                    endpoint=endpoint,
                    topic=str(response.request.url),
                    payload=payload,
                    wall_ns=first_byte_ns,
                    monotonic_ns=first_byte_monotonic_ns or time.monotonic_ns(),
                    source_pubtime=response.headers.get("Date"),
                )
                content_type = response.headers.get("Content-Type", "")
                content_encoding = response.headers.get("Content-Encoding", "")
                self.store.record_fetch(
                    capture,
                    {
                        "relation": "http_response",
                        "url": str(response.request.url),
                        "started_at_ns": started_ns,
                        "first_byte_at_ns": first_byte_ns,
                        "finished_at_ns": finished_ns,
                        "http_status": response.status_code,
                        "content_type": content_type,
                        "content_encoding": content_encoding,
                        "sniffed_format": "json" if source_id == "AWC_API" else "gzip_xml",
                        "payload": payload,
                        "integrity_status": "not_provided",
                    },
                )
                return capture, payload, started_ns, finished_ns, content_type, content_encoding
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            if isinstance(exc, httpx.TransportError):
                self._client_reset_requested = True
            self.store.record_access(
                source_family=source_id,
                endpoint=endpoint,
                phase="http_request",
                status="error",
                request={"url": url, "params": params, "started_at_ns": started_ns},
                response=f"{type(exc).__name__}: {exc}",
            )
            return None

    def _fetch_api(self, client: httpx.Client) -> None:
        self.api_requests += 1
        result = self._request(
            client,
            source_id="AWC_API",
            url=self.config.api_url,
            params={"ids": ",".join(sorted(self.stations)), "format": "json"},
        )
        if not result:
            return
        capture, payload, started_ns, finished_ns, content_type, content_encoding = result
        sniffed, count = ingest_payload(
            self.store,
            capture,
            payload,
            stations=self.stations,
            context=PayloadContext(
                source_id="AWC_API",
                vantage_id=self.vantage_id,
                notification_ns=None,
                fetch_started_ns=started_ns,
                fetch_finished_ns=finished_ns,
                content_type=content_type or "application/json",
                content_encoding=content_encoding,
            ),
        )
        self.actionable_events += count
        if sniffed.endswith("error"):
            self.errors += 1

    def _fetch_cache(self, client: httpx.Client) -> None:
        self.cache_requests += 1
        result = self._request(client, source_id="AWC_CACHE", url=self.config.cache_url, params=None)
        if not result:
            return
        capture, payload, started_ns, finished_ns, _content_type, _content_encoding = result
        try:
            raw_xml = gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload
            root = ET.fromstring(raw_xml)
            reports = [
                node.text.strip()
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == "raw_text" and node.text and node.text.strip()
            ]
            selected = [row for row in reports if any(station in row for station in self.stations)]
            _, count = ingest_payload(
                self.store,
                capture,
                ("\n".join(selected)).encode("utf-8"),
                stations=self.stations,
                context=PayloadContext(
                    source_id="AWC_CACHE",
                    vantage_id=self.vantage_id,
                    notification_ns=None,
                    fetch_started_ns=started_ns,
                    fetch_finished_ns=finished_ns,
                    content_type="text/plain",
                ),
            )
            self.actionable_events += count
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            self.store.record_access(
                source_family="AWC_CACHE",
                endpoint=httpx.URL(self.config.cache_url).host or self.config.cache_url,
                phase="parse",
                status="error",
                request={"payload_sha256": capture.raw_payload_sha256},
                response=f"{type(exc).__name__}: {exc}",
            )

    def summary(self) -> dict[str, Any]:
        return {
            "api_requests": self.api_requests,
            "cache_requests": self.cache_requests,
            "errors": self.errors,
            "actionable_events": self.actionable_events,
            "client_resets": self.client_resets,
        }
