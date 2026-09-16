"""Append-only raw evidence and SQLite index storage."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "schema.sql"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{sha256_bytes(canonical_json(value).encode('utf-8'))}"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


@dataclass(frozen=True)
class ClockState:
    offset_ms: float | None
    valid: bool


@dataclass(frozen=True)
class TransportCapture:
    transport_message_id: str
    raw_payload_sha256: str
    raw_payload_path: str
    wall_ns: int
    monotonic_ns: int
    clock_offset_ms: float | None
    clock_valid: bool


class EvidenceStore:
    """Thread-safe append-only evidence writer.

    Raw envelopes are created with exclusive file creation before their index
    row is committed. Content-addressed payloads are never overwritten.
    """

    def __init__(self, runtime_root: Path, *, run_id: str | None = None) -> None:
        self.runtime_root = runtime_root.resolve()
        self.raw_root = self.runtime_root / "raw"
        self.payload_root = self.runtime_root / "raw_payloads"
        self.db_path = self.runtime_root / "evidence.sqlite3"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.payload_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.run_id = run_id
        self.clock_state = ClockState(None, False)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def start_run(self, *, config_hash: str, vantage_id: str) -> str:
        run_id = f"run_{time.time_ns()}_{uuid.uuid4().hex[:8]}"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO collector_run VALUES (?,?,?,?,?,?)",
                (run_id, git_commit(), config_hash, vantage_id, socket.getfqdn(), time.time_ns()),
            )
        self.run_id = run_id
        return run_id

    def end_run(self, reason: str) -> None:
        assert self.run_id
        now = time.time_ns()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO collector_run_end VALUES (?,?,?,?)",
                (stable_id("run_end", [self.run_id, now, reason]), self.run_id, now, reason),
            )

    def set_clock_state(self, state: ClockState) -> None:
        self.clock_state = state

    def record_clock(self, sample: dict[str, Any]) -> None:
        assert self.run_id
        sample_id = stable_id("clock", [self.run_id, sample["sampled_at_ns"], sample["raw_probe"]])
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO clock_health VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample_id,
                    self.run_id,
                    sample["sampled_at_ns"],
                    sample["sampled_monotonic_ns"],
                    sample["probe_kind"],
                    sample.get("offset_ms"),
                    sample.get("uncertainty_ms"),
                    sample.get("stratum"),
                    sample.get("leap_status"),
                    int(sample["clock_valid"]),
                    sample["raw_probe"],
                ),
            )
        self.set_clock_state(ClockState(sample.get("offset_ms"), bool(sample["clock_valid"])))

    def record_access(
        self,
        *,
        source_family: str,
        endpoint: str,
        phase: str,
        status: str,
        request: Any,
        response: Any = None,
        evidence_path: str = "",
        attempted_at_ns: int | None = None,
    ) -> None:
        assert self.run_id
        attempted_at_ns = attempted_at_ns or time.time_ns()
        row = [self.run_id, source_family, endpoint, attempted_at_ns, phase, status, request, response]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO access_attempt VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    stable_id("access", row + [uuid.uuid4().hex]),
                    self.run_id,
                    source_family,
                    endpoint,
                    attempted_at_ns,
                    phase,
                    status,
                    canonical_json(request),
                    canonical_json(response) if response is not None else None,
                    evidence_path or None,
                ),
            )

    def _write_payload(self, payload: bytes) -> tuple[str, str]:
        digest = sha256_bytes(payload)
        path = self.payload_root / digest[:2] / f"{digest}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if sha256_bytes(path.read_bytes()) != digest:
                raise RuntimeError(f"content-addressed payload hash mismatch: {path}")
        return digest, str(path)

    def capture_transport(
        self,
        *,
        source_id: str,
        endpoint: str,
        topic: str,
        payload: bytes,
        wall_ns: int,
        monotonic_ns: int,
        source_message_id: str | None = None,
        source_data_id: str | None = None,
        source_pubtime: str | None = None,
    ) -> TransportCapture:
        assert self.run_id
        digest, payload_path = self._write_payload(payload)
        capture_uuid = uuid.uuid4().hex
        transport_id = stable_id(
            "transport", [self.run_id, source_id, endpoint, topic, wall_ns, monotonic_ns, digest, capture_uuid]
        )
        stamp = time.gmtime(wall_ns // 1_000_000_000)
        raw_dir = self.raw_root / time.strftime("%Y-%m-%d", stamp) / source_id / endpoint / time.strftime("%H", stamp)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{wall_ns}_{capture_uuid}.json"
        envelope = {
            "schema_version": "us_fast_weather_raw_envelope_v1",
            "transport_received_wall_ns": wall_ns,
            "transport_received_monotonic_ns": monotonic_ns,
            "broker_or_endpoint": endpoint,
            "mqtt_topic_or_channel": topic,
            "source_id": source_id,
            "raw_payload_sha256": digest,
            "raw_payload_path": payload_path,
            "payload_size_bytes": len(payload),
        }
        with raw_path.open("x", encoding="utf-8") as handle:
            handle.write(canonical_json(envelope) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        state = self.clock_state
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO transport_message VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    transport_id,
                    self.run_id,
                    source_id,
                    endpoint,
                    topic or None,
                    source_message_id,
                    source_data_id,
                    source_pubtime,
                    wall_ns,
                    monotonic_ns,
                    state.offset_ms,
                    int(state.valid),
                    digest,
                    str(raw_path),
                    len(payload),
                ),
            )
        return TransportCapture(
            transport_id, digest, str(raw_path), wall_ns, monotonic_ns, state.offset_ms, state.valid
        )

    def record_notification(self, capture: TransportCapture, notification: dict[str, Any]) -> None:
        properties = notification.get("properties") or {}
        temporal = {key: properties.get(key) for key in ("datetime", "start_datetime", "end_datetime")}
        parse_status = "ok" if properties.get("pubtime") and properties.get("data_id") else "invalid_minimum_fields"
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO wis2_notification VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stable_id("wnm", [capture.transport_message_id, notification.get("id")]),
                    capture.transport_message_id,
                    notification.get("id"),
                    properties.get("pubtime"),
                    properties.get("data_id"),
                    properties.get("metadata_id"),
                    int(properties.get("cache", True)),
                    canonical_json(properties.get("global-cache") or properties.get("global_cache")),
                    canonical_json(temporal),
                    canonical_json(notification.get("links") or []),
                    canonical_json(properties.get("content") or notification.get("content"))
                    if properties.get("content") is not None or notification.get("content") is not None
                    else None,
                    canonical_json(properties.get("integrity") or notification.get("integrity"))
                    if properties.get("integrity") is not None or notification.get("integrity") is not None
                    else None,
                    parse_status,
                    None if parse_status == "ok" else "missing_pubtime_or_data_id",
                ),
            )

    def record_fetch(self, capture: TransportCapture, fetch: dict[str, Any]) -> None:
        payload = fetch.get("payload")
        payload_digest = None
        payload_path = None
        if isinstance(payload, bytes):
            payload_digest, payload_path = self._write_payload(payload)
        url = str(fetch.get("url") or "inline")
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO payload_fetch VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stable_id("fetch", [capture.transport_message_id, fetch.get("relation"), url, fetch["started_at_ns"]]),
                    capture.transport_message_id,
                    fetch.get("relation") or "unknown",
                    sha256_bytes(url.encode("utf-8")),
                    fetch["started_at_ns"],
                    fetch.get("first_byte_at_ns"),
                    fetch.get("finished_at_ns"),
                    fetch.get("http_status"),
                    fetch.get("content_type"),
                    fetch.get("content_encoding"),
                    fetch.get("sniffed_format"),
                    payload_digest,
                    payload_path,
                    fetch.get("integrity_status"),
                    fetch.get("error_code"),
                ),
            )

    def record_observation(
        self,
        *,
        capture: TransportCapture,
        event: dict[str, Any],
        source_id: str,
        vantage_id: str,
        notification_ns: int | None,
        fetch_started_ns: int | None,
        fetch_finished_ns: int | None,
        decoded_ns: int,
        evidence_status: str = "actionable",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO observation_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["observation_version_id"],
                    event["event_family_id"],
                    event.get("raw_report_id"),
                    event["semantic_version_id"],
                    event["station_id"],
                    event["report_kind"],
                    event["observation_time"],
                    int(event["is_correction"]),
                    event.get("correction_marker"),
                    event.get("air_temperature_c"),
                    event.get("dewpoint_c"),
                    event.get("wind_direction_deg"),
                    event.get("wind_speed_kt"),
                    event.get("visibility_m"),
                    event.get("altimeter_hpa"),
                    event.get("normalized_raw_text"),
                    event["normalized_fields_json"],
                ),
            )
            source_seen_id = stable_id(
                "seen", [event["observation_version_id"], capture.transport_message_id, source_id, vantage_id]
            )
            self._conn.execute(
                """INSERT OR IGNORE INTO source_observation_seen VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_seen_id,
                    event["observation_version_id"],
                    capture.transport_message_id,
                    source_id,
                    vantage_id,
                    notification_ns,
                    fetch_started_ns,
                    fetch_finished_ns,
                    decoded_ns,
                    decoded_ns,
                    capture.clock_offset_ms,
                    int(capture.clock_valid),
                    capture.raw_payload_sha256,
                    evidence_status,
                ),
            )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def record_replay_audit(self, details: dict[str, Any]) -> None:
        assert self.run_id
        replayed_at_ns = time.time_ns()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO replay_audit VALUES (?,?,?,?,?,?,?)",
                (
                    stable_id("replay", [self.run_id, replayed_at_ns, details]),
                    self.run_id,
                    replayed_at_ns,
                    int(details.get("raw_records", 0)),
                    int(details.get("observations", 0)),
                    int(bool(details.get("stable_identity"))),
                    canonical_json(details),
                ),
            )
