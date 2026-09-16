"""Credential-gated commercial WebSocket collectors.

Credentials are accepted only in memory and are never written to evidence,
logs, configuration, URLs stored in SQLite, or summaries.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import websockets

from us_fast_weather_lab.collectors import PayloadContext, ingest_payload
from us_fast_weather_lab.storage import EvidenceStore, canonical_json


@dataclass(frozen=True)
class MetarWsConfig:
    endpoint: str
    reconnect_seconds: float
    channels: tuple[str, ...]


@dataclass(frozen=True)
class SynopticPushConfig:
    endpoint_base: str
    reconnect_seconds: float
    stations: tuple[str, ...]
    variables: tuple[str, ...]
    units: str = "metric"


def metar_ws_source_id(channel: str) -> str:
    if channel.startswith("metar.atis."):
        return "METAR_WS_DATIS"
    if channel.startswith("metar.obs10."):
        return "METAR_WS_HFMETAR"
    if channel.startswith("metar.obs."):
        return "METAR_WS_METAR"
    return "METAR_WS_CONTROL"


def _metar_ws_ingest_payload(message: dict[str, Any]) -> bytes:
    prepared = dict(message)
    data = dict(message.get("data") or {})
    channel = str(message.get("channel") or "")
    if channel.startswith("metar.obs10."):
        data["force_report_kind"] = "HF_METAR"
    elif channel.startswith("metar.atis."):
        data["force_report_kind"] = "DATIS_DERIVED"
    prepared["data"] = data
    return canonical_json(prepared).encode("utf-8")


def _synoptic_datetime(value: Any) -> str | None:
    digits = str(value or "")
    if len(digits) != 12 or not digits.isdigit():
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d%H%M").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def synoptic_temperature_payload(message: dict[str, Any], stations: set[str]) -> bytes | None:
    rows: list[dict[str, Any]] = []
    for row in message.get("data") or []:
        if not isinstance(row, dict) or str(row.get("sensor")) != "air_temp":
            continue
        station = str(row.get("stid") or "").upper()
        observation_time = _synoptic_datetime(row.get("date"))
        if station not in stations or observation_time is None or row.get("value") is None:
            continue
        rows.append(
            {
                "station_id": station,
                "observation_time": observation_time,
                "report_kind": "HF_TEMPERATURE",
                "air_temperature_c": row["value"],
                "sensor_set": row.get("set"),
                "qc": row.get("qc") or [],
            }
        )
    return canonical_json(rows).encode("utf-8") if rows else None


class _ThreadedWebSocketCollector:
    source_id = "COMMERCIAL_WS"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connections = 0
        self.frames = 0
        self.actionable_events = 0
        self.cached_events = 0
        self.errors = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name=self.source_id.lower(), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=20.0)
            if self._thread.is_alive():
                raise RuntimeError(f"{self.source_id} collector did not stop cleanly")

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        raise NotImplementedError

    async def _backoff(self, seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while not self._stop.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.25, remaining))

    def summary(self) -> dict[str, int]:
        return {
            "connections": self.connections,
            "frames": self.frames,
            "actionable_events": self.actionable_events,
            "cached_events": self.cached_events,
            "errors": self.errors,
        }


class MetarWsCollector(_ThreadedWebSocketCollector):
    source_id = "METAR_WS"

    def __init__(
        self,
        store: EvidenceStore,
        *,
        config: MetarWsConfig,
        api_key: str,
        stations: set[str],
        vantage_id: str,
    ) -> None:
        super().__init__()
        if not api_key:
            raise ValueError("METAR.ws API key is required")
        self.store = store
        self.config = config
        self.api_key = api_key
        self.stations = stations
        self.vantage_id = vantage_id

    def process_frame(self, raw: str | bytes, *, wall_ns: int, monotonic_ns: int) -> None:
        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = {}
        channel = str(message.get("channel") or "websocket.control")
        source_id = metar_ws_source_id(channel)
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        capture = self.store.capture_transport(
            source_id=source_id,
            endpoint="stream.metar.ws",
            topic=channel,
            payload=payload,
            wall_ns=wall_ns,
            monotonic_ns=monotonic_ns,
            source_pubtime=str(data.get("report_time")) if data.get("report_time") else None,
        )
        self.frames += 1
        if message.get("type") != "publication":
            return
        cached = bool(message.get("cached"))
        _, count = ingest_payload(
            self.store,
            capture,
            _metar_ws_ingest_payload(message),
            stations=self.stations,
            context=PayloadContext(
                source_id=source_id,
                vantage_id=self.vantage_id,
                notification_ns=wall_ns,
                fetch_started_ns=None,
                fetch_finished_ns=None,
                content_type="application/json",
                evidence_status="cached_replay_non_latency" if cached else "actionable",
            ),
        )
        if cached:
            self.cached_events += count
        else:
            self.actionable_events += count

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.config.endpoint,
                    additional_headers={"Authorization": f"Bearer {self.api_key}"},
                    open_timeout=10,
                    close_timeout=5,
                    max_size=4 * 1024 * 1024,
                ) as websocket:
                    self.connections += 1
                    ack = await asyncio.wait_for(websocket.recv(), timeout=10)
                    ack_wall_ns = time.time_ns()
                    ack_monotonic_ns = time.monotonic_ns()
                    self.process_frame(ack, wall_ns=ack_wall_ns, monotonic_ns=ack_monotonic_ns)
                    ack_value = json.loads(ack)
                    if ack_value.get("type") != "ack":
                        raise RuntimeError("METAR.ws did not return an ack frame")
                    self.store.record_access(
                        source_family="METAR_WS",
                        endpoint="stream.metar.ws",
                        phase="websocket_auth",
                        status="SUCCESS",
                        request={"auth": "bearer_env", "channel_count": len(self.config.channels)},
                        response={"type": "ack", "plan": ack_value.get("plan"), "limits": ack_value.get("limits")},
                    )
                    await websocket.send(
                        canonical_json({"action": "subscribe", "channels": list(self.config.channels)})
                    )
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        wall_ns = time.time_ns()
                        monotonic_ns = time.monotonic_ns()
                        self.process_frame(raw, wall_ns=wall_ns, monotonic_ns=monotonic_ns)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                self.store.record_access(
                    source_family="METAR_WS",
                    endpoint="stream.metar.ws",
                    phase="websocket_session",
                    status="ERROR",
                    request={"channel_count": len(self.config.channels)},
                    response={"error_type": type(exc).__name__},
                )
                await self._backoff(self.config.reconnect_seconds)


class SynopticPushCollector(_ThreadedWebSocketCollector):
    source_id = "SYNOPTIC_PUSH"

    def __init__(
        self,
        store: EvidenceStore,
        *,
        config: SynopticPushConfig,
        api_token: str,
        vantage_id: str,
    ) -> None:
        super().__init__()
        if not api_token:
            raise ValueError("Synoptic API token is required")
        self.store = store
        self.config = config
        self.api_token = api_token
        self.stations = {station.upper() for station in config.stations}
        self.vantage_id = vantage_id
        self.session_id: str | None = None

    def process_frame(self, raw: str | bytes, *, wall_ns: int, monotonic_ns: int) -> None:
        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = {}
        message_type = str(message.get("type") or "unknown")
        capture = self.store.capture_transport(
            source_id="SYNOPTIC_PUSH_HF_TEMP" if message_type == "data" else "SYNOPTIC_PUSH_CONTROL",
            endpoint="push.synopticdata.com",
            topic=message_type,
            payload=payload,
            wall_ns=wall_ns,
            monotonic_ns=monotonic_ns,
        )
        self.frames += 1
        if message_type == "auth" and message.get("code") == "success" and message.get("session"):
            self.session_id = str(message["session"])
        if message_type != "data":
            return
        normalized = synoptic_temperature_payload(message, self.stations)
        if normalized is None:
            return
        _, count = ingest_payload(
            self.store,
            capture,
            normalized,
            stations=self.stations,
            context=PayloadContext(
                source_id="SYNOPTIC_PUSH_HF_TEMP",
                vantage_id=self.vantage_id,
                notification_ns=wall_ns,
                fetch_started_ns=None,
                fetch_finished_ns=None,
                content_type="application/json",
            ),
        )
        self.actionable_events += count

    def _connection_url(self) -> str:
        if self.session_id:
            return f"{self.config.endpoint_base.rstrip('/')}/{self.api_token}/{self.session_id}"
        query = urlencode(
            {
                "stid": ",".join(self.config.stations),
                "vars": ",".join(self.config.variables),
                "units": self.config.units,
                "metadata": "1",
            }
        )
        return f"{self.config.endpoint_base.rstrip('/')}/{self.api_token}/?{query}"

    def _invalidate_failed_resume(self, resume_attempted: bool) -> None:
        if resume_attempted:
            self.session_id = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            resume_attempted = self.session_id is not None
            try:
                async with websockets.connect(
                    self._connection_url(),
                    open_timeout=10,
                    close_timeout=5,
                    max_size=8 * 1024 * 1024,
                ) as websocket:
                    self.connections += 1
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        wall_ns = time.time_ns()
                        monotonic_ns = time.monotonic_ns()
                        self.process_frame(raw, wall_ns=wall_ns, monotonic_ns=monotonic_ns)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                self._invalidate_failed_resume(resume_attempted)
                self.store.record_access(
                    source_family="SYNOPTIC_PUSH",
                    endpoint="push.synopticdata.com",
                    phase="websocket_session",
                    status="ERROR",
                    request={"station_count": len(self.config.stations), "variables": self.config.variables},
                    response={"error_type": type(exc).__name__},
                )
                await self._backoff(self.config.reconnect_seconds)
