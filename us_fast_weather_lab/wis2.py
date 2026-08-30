"""WIS2 multi-broker origin/cache collector with raw-first callbacks."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import random
import socket
import ssl
import string
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import paho.mqtt.client as mqtt

from us_fast_weather_lab.collectors import PayloadContext, ingest_payload
from us_fast_weather_lab.model import sniff_payload
from us_fast_weather_lab.storage import EvidenceStore, TransportCapture


@dataclass(frozen=True)
class Broker:
    broker_id: str
    hostname: str
    port: int = 8883
    username: str = "everyone"
    password: str = "everyone"
    discovered: bool = True


def _broker_id_from_title(title: str, hostname: str) -> str:
    if "(" in title and title.endswith(")"):
        return title.rsplit("(", 1)[1][:-1]
    return hostname.replace(".", "-")


def discover_brokers(
    discovery_url: str,
    *,
    fallback_brokers: dict[str, str],
    authoritative_brokers: dict[str, str] | None = None,
    username: str = "everyone",
    password: str = "everyone",
    timeout_seconds: float = 10.0,
) -> tuple[list[Broker], dict[str, Any]]:
    evidence: dict[str, Any] = {
        "url": discovery_url,
        "status": "ok",
        "fallback_used": False,
        "authoritative_brokers": authoritative_brokers or {},
    }
    brokers: dict[str, Broker] = {}
    try:
        response = httpx.get(discovery_url, timeout=timeout_seconds, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        evidence["http_status"] = response.status_code
        evidence["payload_sha256"] = hashlib.sha256(response.content).hexdigest()
        for link in payload.get("links") or []:
            href = str(link.get("href") or "")
            if not href.startswith("mqtts://"):
                continue
            parsed = urlparse(href)
            hostname = parsed.hostname
            if not hostname:
                continue
            broker_id = _broker_id_from_title(str(link.get("title") or ""), hostname)
            brokers[hostname] = Broker(
                broker_id=broker_id,
                hostname=hostname,
                port=parsed.port or 8883,
                username=unquote(parsed.username or username),
                password=unquote(parsed.password or password),
                discovered=True,
            )
    except Exception as exc:  # noqa: BLE001
        evidence.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "fallback_used": True})
    # NOAA Service Change Notices are the authority for current operational
    # endpoints when a discovery catalogue has not yet converged.  Keep both
    # the discovered hostname and the dated override so a stale catalogue is
    # evidence, rather than silently replacing it.
    for broker_id, hostname in (authoritative_brokers or {}).items():
        brokers.setdefault(hostname, Broker(broker_id, hostname, 8883, username, password, False))
    for broker_id, hostname in fallback_brokers.items():
        brokers.setdefault(hostname, Broker(broker_id, hostname, 8883, username, password, False))
    # Credentials belong only to the in-memory Broker objects used by MQTT.
    # Discovery URLs can embed non-default userinfo, which must never enter the
    # append-only access ledger or reports.
    evidence["brokers"] = [
        {
            "broker_id": broker.broker_id,
            "hostname": broker.hostname,
            "port": broker.port,
            "discovered": broker.discovered,
        }
        for broker in brokers.values()
    ]
    return sorted(brokers.values(), key=lambda item: item.broker_id), evidence


def _reason_value(reason: Any) -> int:
    return int(getattr(reason, "value", reason))


def _inline_content(notification: dict[str, Any]) -> bytes | None:
    properties = notification.get("properties") or {}
    content = properties.get("content") if properties.get("content") is not None else notification.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        try:
            return base64.b64decode(content, validate=True)
        except Exception:  # noqa: BLE001
            return content.encode("utf-8")
    if not isinstance(content, dict):
        return json.dumps(content, sort_keys=True).encode("utf-8")
    value = content.get("value") or content.get("data")
    if value is None:
        return None
    raw = str(value).encode("utf-8")
    encoding = str(content.get("encoding") or "").lower()
    if "base64" in encoding or encoding in {"gzip", "application/gzip"}:
        try:
            raw = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            pass
    return raw


def _integrity_status(notification: dict[str, Any], payload: bytes) -> str:
    properties = notification.get("properties") or {}
    integrity = properties.get("integrity") or notification.get("integrity")
    if not integrity or not isinstance(integrity, dict):
        return "not_provided"
    method = str(integrity.get("method") or "").lower()
    value = str(integrity.get("value") or "")
    if method not in hashlib.algorithms_available or not value:
        return "unsupported"
    digest = hashlib.new(method, payload).digest()
    try:
        expected = base64.b64decode(value, validate=True)
        return "match" if digest == expected else "mismatch"
    except Exception:  # noqa: BLE001
        return "invalid_declared_value"


class Wis2Collector:
    source_id = "WIS2"

    def __init__(
        self,
        store: EvidenceStore,
        *,
        brokers: list[Broker],
        topic_base: str,
        stations: set[str],
        vantage_id: str,
        fetch_timeout_seconds: float = 10.0,
        worker_count: int = 8,
    ) -> None:
        self.store = store
        self.brokers = brokers
        self.topic_base = topic_base.strip("/")
        self.stations = stations
        self.vantage_id = vantage_id
        self.fetch_timeout_seconds = fetch_timeout_seconds
        self._clients: list[mqtt.Client] = []
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="wis2-payload")
        self._futures: set[concurrent.futures.Future[Any]] = set()
        self._future_lock = threading.Lock()
        self._stop = threading.Event()
        self._connection_states: dict[str, dict[str, Any]] = {}
        self.notifications = 0
        self.actionable_events = 0
        self.payload_errors = 0

    def start(self) -> None:
        for broker in self.brokers:
            for channel in ("origin", "cache"):
                self._start_client(broker, channel)

    def stop(self) -> None:
        self._stop.set()
        for state in self._connection_states.values():
            if not state.get("connect_result_recorded"):
                self.store.record_access(
                    source_family="WIS2",
                    endpoint=str(state["hostname"]),
                    phase="connect_tls",
                    status="timeout",
                    request={"port": state["port"], "channel": state["channel"], "client_id": state["client_id"]},
                    response="no CONNACK before bounded collector shutdown",
                    attempted_at_ns=int(state["started_at_ns"]),
                )
                state["connect_result_recorded"] = True
        for client in self._clients:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _start_client(self, broker: Broker, channel: str) -> None:
        dns_started = time.time_ns()
        try:
            addresses = sorted({row[4][0] for row in socket.getaddrinfo(broker.hostname, broker.port, type=socket.SOCK_STREAM)})
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="dns",
                status="ok",
                request={"port": broker.port, "channel": channel},
                response={"addresses": addresses, "finished_at_ns": time.time_ns()},
                attempted_at_ns=dns_started,
            )
        except Exception as exc:  # noqa: BLE001
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="dns",
                status="error",
                request={"port": broker.port, "channel": channel},
                response=f"{type(exc).__name__}: {exc}",
                attempted_at_ns=dns_started,
            )
        suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        client_id = f"fwlab-{self.vantage_id[:8]}-{hashlib.sha1(broker.broker_id.encode()).hexdigest()[:5]}-{channel[0]}-{suffix}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv5)
        client.username_pw_set(broker.username, broker.password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.user_data_set({"broker": broker, "channel": channel, "connected_at_ns": time.time_ns()})
        state_key = f"{broker.broker_id}:{channel}"
        self._connection_states[state_key] = {
            "hostname": broker.hostname,
            "port": broker.port,
            "channel": channel,
            "client_id": client_id,
            "started_at_ns": time.time_ns(),
            "connect_result_recorded": False,
            "connected_ok": False,
            "suback": False,
        }

        def on_connect(
            inner_client: mqtt.Client,
            userdata: dict[str, Any],
            _flags: Any,
            reason_code: Any,
            _properties: Any = None,
        ) -> None:
            value = _reason_value(reason_code)
            sock = inner_client.socket()
            cipher = sock.cipher() if sock and hasattr(sock, "cipher") else None
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="connect_tls",
                status="ok" if value == 0 else "rejected",
                request={"port": broker.port, "channel": channel, "client_id": client_id},
                response={"reason_code": value, "tls_cipher": cipher},
                attempted_at_ns=userdata["connected_at_ns"],
            )
            self._connection_states[state_key]["connect_result_recorded"] = True
            self._connection_states[state_key]["connected_ok"] = value == 0
            if value == 0:
                base = f"{channel}/{self.topic_base}"
                inner_client.subscribe([(base, 0), (f"{base}/#", 0)])

        def on_subscribe(
            _inner_client: mqtt.Client,
            _userdata: Any,
            mid: int,
            reason_code_list: list[Any],
            _properties: Any = None,
        ) -> None:
            codes = [_reason_value(item) for item in reason_code_list]
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="suback",
                status="ok" if codes and all(code < 128 for code in codes) else "denied",
                request={
                    "channel": channel,
                    "topics": [f"{channel}/{self.topic_base}", f"{channel}/{self.topic_base}/#"],
                    "mid": mid,
                },
                response={"reason_codes": codes},
            )
            self._connection_states[state_key]["suback"] = bool(codes and all(code < 128 for code in codes))

        def on_connect_fail(_inner_client: mqtt.Client, _userdata: Any) -> None:
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="connect_tls",
                status="error",
                request={"port": broker.port, "channel": channel, "client_id": client_id},
                response="paho on_connect_fail",
                attempted_at_ns=int(self._connection_states[state_key]["started_at_ns"]),
            )
            self._connection_states[state_key]["connect_result_recorded"] = True

        def on_message(_inner_client: mqtt.Client, _userdata: Any, message: Any) -> None:
            transport_received_wall_ns = time.time_ns()
            transport_received_monotonic_ns = time.monotonic_ns()
            broker_id = broker.broker_id
            mqtt_topic = str(message.topic)
            capture = self.store.capture_transport(
                source_id=f"WIS2_{channel.upper()}",
                endpoint=broker_id,
                topic=mqtt_topic,
                payload=bytes(message.payload),
                wall_ns=transport_received_wall_ns,
                monotonic_ns=transport_received_monotonic_ns,
            )
            self.notifications += 1
            future = self._executor.submit(
                self._process_notification, capture, bytes(message.payload), broker_id, channel, mqtt_topic
            )
            with self._future_lock:
                self._futures.add(future)
            future.add_done_callback(self._future_done)

        def on_disconnect(
            _inner_client: mqtt.Client,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any = None,
        ) -> None:
            if not self._stop.is_set():
                self.store.record_access(
                    source_family="WIS2",
                    endpoint=broker.hostname,
                    phase="disconnect",
                    status="unexpected",
                    request={"channel": channel},
                    response={"reason_code": _reason_value(reason_code)},
                )

        client.on_connect = on_connect
        client.on_connect_fail = on_connect_fail
        client.on_subscribe = on_subscribe
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        try:
            client.connect_async(broker.hostname, broker.port, keepalive=60)
            client.loop_start()
            self._clients.append(client)
        except Exception as exc:  # noqa: BLE001
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker.hostname,
                phase="connect_start",
                status="error",
                request={"channel": channel, "port": broker.port},
                response=f"{type(exc).__name__}: {exc}",
            )

    def _future_done(self, future: concurrent.futures.Future[Any]) -> None:
        with self._future_lock:
            self._futures.discard(future)
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001
            self.payload_errors += 1
            self.store.record_access(
                source_family="WIS2",
                endpoint="payload_worker",
                phase="worker",
                status="error",
                request={},
                response=f"{type(exc).__name__}: {exc}",
            )

    def _process_notification(
        self,
        capture: TransportCapture,
        raw_notification: bytes,
        broker_id: str,
        channel: str,
        mqtt_topic: str,
    ) -> None:
        try:
            notification = json.loads(raw_notification.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.payload_errors += 1
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker_id,
                phase="notification_parse",
                status="error",
                request={"topic": mqtt_topic, "raw_sha256": capture.raw_payload_sha256},
                response=f"{type(exc).__name__}: {exc}",
            )
            return
        self.store.record_notification(capture, notification)
        inline = _inline_content(notification)
        if inline is not None:
            started_ns = capture.wall_ns
            finished_ns = time.time_ns()
            sniffed, _ = sniff_payload(inline)
            integrity = _integrity_status(notification, inline)
            self.store.record_fetch(
                capture,
                {
                    "relation": "inline",
                    "url": "inline",
                    "started_at_ns": started_ns,
                    "first_byte_at_ns": capture.wall_ns,
                    "finished_at_ns": finished_ns,
                    "content_type": "",
                    "content_encoding": "",
                    "sniffed_format": sniffed,
                    "payload": inline,
                    "integrity_status": integrity,
                    "error_code": "integrity_mismatch" if integrity == "mismatch" else None,
                },
            )
            if integrity != "mismatch":
                _, count = ingest_payload(
                    self.store,
                    capture,
                    inline,
                    stations=self.stations,
                    context=PayloadContext(
                        source_id=f"WIS2_{channel.upper()}:{broker_id}",
                        vantage_id=self.vantage_id,
                        notification_ns=capture.wall_ns,
                        fetch_started_ns=started_ns,
                        fetch_finished_ns=finished_ns,
                    ),
                )
                self.actionable_events += count
            return
        links = notification.get("links") or []
        preferred = next(
            (link for link in links if isinstance(link, dict) and link.get("rel") in {"canonical", "update"} and link.get("href")),
            None,
        )
        if not preferred:
            self.payload_errors += 1
            self.store.record_access(
                source_family="WIS2",
                endpoint=broker_id,
                phase="payload_link",
                status="error",
                request={"topic": mqtt_topic},
                response="missing_canonical_or_update",
            )
            return
        self._fetch_payload(capture, notification, preferred, broker_id, channel)

    def _fetch_payload(
        self,
        capture: TransportCapture,
        notification: dict[str, Any],
        link: dict[str, Any],
        broker_id: str,
        channel: str,
    ) -> None:
        url = str(link["href"])
        started_ns = time.time_ns()
        first_byte_ns: int | None = None
        try:
            with httpx.Client(timeout=self.fetch_timeout_seconds, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    chunks: list[bytes] = []
                    for chunk in response.iter_raw():
                        if first_byte_ns is None:
                            first_byte_ns = time.time_ns()
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    finished_ns = time.time_ns()
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", str(link.get("type") or ""))
                    content_encoding = response.headers.get("Content-Encoding", "")
                    sniffed, _ = sniff_payload(payload, content_type, content_encoding)
                    integrity = _integrity_status(notification, payload)
                    self.store.record_fetch(
                        capture,
                        {
                            "relation": str(link.get("rel")),
                            "url": url,
                            "started_at_ns": started_ns,
                            "first_byte_at_ns": first_byte_ns,
                            "finished_at_ns": finished_ns,
                            "http_status": response.status_code,
                            "content_type": content_type,
                            "content_encoding": content_encoding,
                            "sniffed_format": sniffed,
                            "payload": payload,
                            "integrity_status": integrity,
                            "error_code": "integrity_mismatch" if integrity == "mismatch" else None,
                        },
                    )
                    if integrity != "mismatch":
                        _, count = ingest_payload(
                            self.store,
                            capture,
                            payload,
                            stations=self.stations,
                            context=PayloadContext(
                                source_id=f"WIS2_{channel.upper()}:{broker_id}",
                                vantage_id=self.vantage_id,
                                notification_ns=capture.wall_ns,
                                fetch_started_ns=started_ns,
                                fetch_finished_ns=finished_ns,
                                content_type=content_type,
                                content_encoding=content_encoding,
                            ),
                        )
                        self.actionable_events += count
        except Exception as exc:  # noqa: BLE001
            self.payload_errors += 1
            finished_ns = time.time_ns()
            self.store.record_fetch(
                capture,
                {
                    "relation": str(link.get("rel")),
                    "url": url,
                    "started_at_ns": started_ns,
                    "first_byte_at_ns": first_byte_ns,
                    "finished_at_ns": finished_ns,
                    "sniffed_format": "unknown",
                    "integrity_status": "not_checked",
                    "error_code": f"{type(exc).__name__}: {exc}",
                },
            )

    def summary(self) -> dict[str, Any]:
        return {
            "brokers": [broker.__dict__ for broker in self.brokers],
            "connections": len(self._clients),
            "connect_results": sum(bool(state.get("connect_result_recorded")) for state in self._connection_states.values()),
            "connected_ok": sum(bool(state.get("connected_ok")) for state in self._connection_states.values()),
            "suback_ok": sum(bool(state.get("suback")) for state in self._connection_states.values()),
            "notifications": self.notifications,
            "actionable_events": self.actionable_events,
            "payload_errors": self.payload_errors,
        }
