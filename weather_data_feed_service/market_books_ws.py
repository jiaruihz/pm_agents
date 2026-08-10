"""Selective CLOB WebSocket capture for five weather-city ladders.

The canonical five-minute REST ladder remains the complete denominator.  This
collector adds event-time microstructure for a bounded set of current-day
weather markets.  The socket is idle outside scheduled report windows and
genuine first-seen source events; active windows subscribe only the physically
plausible hot strip.  Complete ladder context remains owned by the REST layer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import websockets
from websockets.asyncio.client import ClientConnection

from weather_data_feed.market_brackets import MarketBracket, parse_market_bracket
from weather_data_feed.source_lineage import producer_build_id
from weather_data_feed.ws_incremental_book import canonical_ws_frame_id


SCHEMA_VERSION = "weather_market_books_ws_increment_v1"
HEALTH_SCHEMA_VERSION = "weather_market_books_combined_health_v1"
SELECTOR_VERSION = "tiered_hot_strip_v4"
PRODUCER = "weather_data_feed_service.market_books_ws"
PRODUCER_BUILD_ID, PRODUCER_BUILD_ID_BASIS = producer_build_id(
    Path(__file__).resolve().parents[1]
)
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DEFAULT_CITIES = ("Amsterdam", "Tokyo", "Helsinki", "Busan")


class PreTransportSafeClientConnection(ClientConnection):
    """Close cleanly when a proxy resets before ``connection_made``.

    websockets 15.0.1 initializes ``recv_messages`` only in connection_made(),
    but asyncio can deliver connection_lost() first when a CONNECT/TLS proxy
    path is reset.  Its default callback then raises AttributeError and hides
    the actual transport failure.  ``create_connection`` is the documented
    customization hook; remove this guard after the dependency moves to a
    version whose base class handles the pre-transport close itself.
    """

    def connection_lost(self, exc: Exception | None) -> None:
        if hasattr(self, "recv_messages"):
            super().connection_lost(exc)
            return
        self.protocol.receive_eof()
        self.set_recv_exc(exc)
        if self.keepalive_task is not None:
            self.keepalive_task.cancel()
        if not self.connection_lost_waiter.done():
            self.connection_lost_waiter.set_result(None)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    current = value or _utc_now()
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _publish_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _observation_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = _load_json(path)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in payload.get("records") or []:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if city and target_date and row.get("status", "ok") == "ok":
            result[(city, target_date)] = row
    return result


def _running_max_native(row: dict[str, Any] | None, unit: str) -> float | None:
    if not row:
        return None
    try:
        if unit == "F":
            if row.get("running_max_f") is not None:
                return float(row["running_max_f"])
            if row.get("running_max_c") is not None:
                return float(row["running_max_c"]) * 9.0 / 5.0 + 32.0
        else:
            if row.get("running_max_c") is not None:
                return float(row["running_max_c"])
            if row.get("running_max_f") is not None:
                return (float(row["running_max_f"]) - 32.0) * 5.0 / 9.0
    except (TypeError, ValueError):
        return None
    return None


def _unit_for_records(records: list[dict[str, Any]]) -> str:
    labels = [str(row.get("bracket") or "") for row in records]
    numeric: list[float] = []
    for label in labels:
        parsed = parse_market_bracket(label)
        if parsed:
            numeric.extend(value for value in (parsed.low, parsed.high) if value is not None)
    # All currently selected five-city markets are Celsius.  The inference keeps
    # this reader safe if a range-form Fahrenheit city is added later.
    return "F" if numeric and max(numeric) > 55 else "C"


def _bracket_sort_key(bracket: MarketBracket) -> tuple[float, float]:
    low = float("-inf") if bracket.low is None else bracket.low
    high = float("inf") if bracket.high is None else bracket.high
    return low, high


class SourceEventCursor:
    """Incrementally fold recent first-seen source events without rescanning raw."""

    def __init__(self, path: Path, *, bootstrap_bytes: int = 2_000_000) -> None:
        self.path = path
        self.bootstrap_bytes = bootstrap_bytes
        self.offset = 0
        self.physical_path: Path | None = None
        self.recent: dict[tuple[str, str, str], datetime] = {}

    def _resolve_path(self) -> Path:
        if not self.path.is_dir():
            return self.path
        candidates = sorted(self.path.glob("????-??-??/sources.jsonl"))
        if not candidates:
            raise FileNotFoundError(f"no dated source-event shard under {self.path}")
        return candidates[-1]

    def read(
        self,
        *,
        cities: set[str],
        now_utc: datetime,
        burst_sec: float,
    ) -> set[tuple[str, str]]:
        try:
            physical_path = self._resolve_path()
            if self.physical_path != physical_path:
                self.offset = 0
            self.physical_path = physical_path
            size = physical_path.stat().st_size
            with physical_path.open("rb") as handle:
                if self.offset <= 0 or self.offset > size:
                    start = max(0, size - self.bootstrap_bytes)
                    handle.seek(start)
                    if start:
                        handle.readline()
                else:
                    handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
        except OSError:
            data = b""
        for line in data.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            if city not in cities or not target_date:
                continue
            if row.get("event_role") != "new_content":
                continue
            if row.get("information_event_status") != "material":
                continue
            if not bool(row.get("material_state_change")):
                continue
            first_seen = _parse_utc(row.get("first_seen_at_utc"))
            if first_seen is None:
                continue
            event_id = str(
                row.get("information_event_id")
                or row.get("content_key")
                or row.get("payload_hash")
                or first_seen.isoformat()
            )
            self.recent[(city, target_date, event_id)] = first_seen
        self.recent = {
            key: first_seen
            for key, first_seen in self.recent.items()
            if 0 <= (now_utc - first_seen).total_seconds() <= burst_sec
        }
        return {(city, target_date) for city, target_date, _ in self.recent}


def scheduled_report_windows(
    observations: dict[tuple[str, str], dict[str, Any]],
    *,
    now_utc: datetime,
    before_sec: float,
    after_sec: float,
    extended_before_sec: float,
    research_sample_modulus: int,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict[str, str]]:
    """Return active report windows and the next expected report per city."""

    active: set[tuple[str, str]] = set()
    research: set[tuple[str, str]] = set()
    next_reports: dict[str, str] = {}
    for key, row in observations.items():
        city, _ = key
        last_report = _parse_utc(row.get("last_obs_utc"))
        try:
            cadence_min = float(
                row.get("estimated_cadence_min") or row.get("cadence_min") or 0
            )
        except (TypeError, ValueError):
            cadence_min = 0.0
        if last_report is None or not 0 < cadence_min <= 180:
            continue
        cadence_sec = cadence_min * 60.0
        report_at = last_report
        if now_utc > report_at:
            elapsed_sec = (now_utc - report_at).total_seconds()
            report_at += timedelta(
                seconds=int(elapsed_sec // cadence_sec) * cadence_sec
            )
            if now_utc > report_at + timedelta(seconds=after_sec):
                report_at += timedelta(seconds=cadence_sec)
        sample_key = f"{city}|{_utc_text(report_at)}"
        extended = (
            research_sample_modulus > 0
            and int.from_bytes(hashlib.sha256(sample_key.encode()).digest()[:8], "big")
            % research_sample_modulus
            == 0
        )
        window_before = extended_before_sec if extended else before_sec
        if (
            report_at - timedelta(seconds=window_before)
            <= now_utc
            <= report_at + timedelta(seconds=after_sec)
        ):
            active.add(key)
            if extended:
                research.add(key)
        next_reports[city] = _utc_text(report_at)
    return active, research, next_reports


@dataclass
class Selection:
    tokens: set[str]
    token_rows: dict[str, dict[str, Any]]
    city_token_counts: dict[str, int]
    active_brackets: dict[str, list[str]]
    grace_brackets: dict[str, list[str]]
    scheduled_cities: list[str]
    research_cities: list[str]
    burst_cities: list[str]
    missing_observation_cities: list[str]
    invalidation_state: dict[str, float]


def select_tokens(
    *,
    market_payload: dict[str, Any],
    observations: dict[tuple[str, str], dict[str, Any]],
    cities: Iterable[str],
    now_utc: datetime,
    active_bracket_count: int,
    research_bracket_count: int,
    event_bracket_count: int,
    post_invalidation_sec: float,
    scheduled_keys: set[tuple[str, str]],
    research_keys: set[tuple[str, str]],
    burst_keys: set[tuple[str, str]],
    invalidation_state: dict[str, float],
) -> Selection:
    city_set = set(cities)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in market_payload.get("records") or []:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("event_date") or "")
        if city not in city_set or not target_date:
            continue
        if target_date != str(row.get("city_local_date_at_snapshot") or ""):
            continue
        grouped.setdefault((city, target_date), []).append(row)

    tokens: set[str] = set()
    token_rows: dict[str, dict[str, Any]] = {}
    city_token_counts: dict[str, int] = {}
    active_brackets: dict[str, list[str]] = {}
    grace_brackets: dict[str, list[str]] = {}
    burst_cities: list[str] = []
    missing_observation_cities: list[str] = []
    now_epoch = now_utc.timestamp()
    live_state_keys: set[str] = set()
    next_invalidation_state: dict[str, float] = {}

    for (city, target_date), rows in grouped.items():
        by_label: dict[str, list[dict[str, Any]]] = {}
        parsed_by_label: dict[str, MarketBracket] = {}
        for row in rows:
            label = str(row.get("bracket") or "")
            parsed = parse_market_bracket(label)
            token_id = str(row.get("token_id") or "")
            if not label or parsed is None or not token_id:
                continue
            by_label.setdefault(label, []).append(row)
            parsed_by_label[label] = parsed
        ordered = sorted(
            parsed_by_label,
            key=lambda label: _bracket_sort_key(parsed_by_label[label]),
        )
        selected_labels: set[str] = set()
        grace_labels: set[str] = set()

        unit = _unit_for_records(rows)
        running_max = _running_max_native(observations.get((city, target_date)), unit)
        window_active = (city, target_date) in scheduled_keys or (
            city,
            target_date,
        ) in burst_keys
        if running_max is None:
            # The complete REST ladder remains available.  Missing physical
            # state must not turn a selective WebSocket into an unbounded feed.
            missing_observation_cities.append(city)
            for label in ordered:
                state_key = f"{city}|{target_date}|{label}"
                live_state_keys.add(state_key)
                next_invalidation_state[state_key] = 0.0
        else:
            possible = [
                label
                for label in ordered
                if parsed_by_label[label].top
                or parsed_by_label[label].high is None
                or float(parsed_by_label[label].high) >= running_max
            ]
            if window_active:
                bracket_count = active_bracket_count
                if (city, target_date) in research_keys:
                    bracket_count = max(bracket_count, research_bracket_count)
                if (city, target_date) in burst_keys:
                    bracket_count = max(bracket_count, event_bracket_count)
                selected_labels.update(possible[:bracket_count])
            if (city, target_date) in burst_keys:
                burst_cities.append(city)
            for label in ordered:
                parsed = parsed_by_label[label]
                state_key = f"{city}|{target_date}|{label}"
                live_state_keys.add(state_key)
                if parsed.top or parsed.high is None or float(parsed.high) >= running_max:
                    next_invalidation_state[state_key] = 0.0
                    continue
                previous_state = invalidation_state.get(state_key)
                if previous_state is None:
                    # Already-impossible on the first observed selector state:
                    # don't spend a synthetic five-minute grace window on it.
                    invalidated_at = -1.0
                elif previous_state == 0.0:
                    invalidated_at = now_epoch
                else:
                    invalidated_at = previous_state
                next_invalidation_state[state_key] = invalidated_at
                if (
                    window_active
                    and invalidated_at > 0
                    and now_epoch - invalidated_at <= post_invalidation_sec
                ):
                    selected_labels.add(label)
                    grace_labels.add(label)

        for label in selected_labels:
            for row in by_label.get(label, []):
                token_id = str(row.get("token_id") or "")
                if token_id:
                    tokens.add(token_id)
                    token_rows[token_id] = row
        city_token_counts[city] = sum(
            1 for token_id in tokens if token_rows[token_id].get("city") == city
        )
        active_brackets[city] = sorted(selected_labels)
        grace_brackets[city] = sorted(grace_labels)

    invalidation_state = {
        key: value for key, value in next_invalidation_state.items() if key in live_state_keys
    }
    return Selection(
        tokens=tokens,
        token_rows=token_rows,
        city_token_counts=city_token_counts,
        active_brackets=active_brackets,
        grace_brackets=grace_brackets,
        scheduled_cities=sorted(
            city for city, target_date in grouped if (city, target_date) in scheduled_keys
        ),
        research_cities=sorted(
            city for city, target_date in grouped if (city, target_date) in research_keys
        ),
        burst_cities=sorted(set(burst_cities)),
        missing_observation_cities=sorted(set(missing_observation_cities)),
        invalidation_state=invalidation_state,
    )


def _message_token_ids(message: Any) -> set[str]:
    items = message if isinstance(message, list) else [message]
    result: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("asset_id", "assetId", "token_id"):
            if item.get(key):
                result.add(str(item[key]))
        for change in item.get("price_changes") or []:
            if isinstance(change, dict) and change.get("asset_id"):
                result.add(str(change["asset_id"]))
    return result


class HourlyWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.stream_id = f"{time.time_ns()}_{os.getpid()}"
        self.hour = ""
        self.path: Path | None = None
        self.fd: int | None = None

    def write(self, payload: dict[str, Any], now_utc: datetime) -> Path:
        hour = now_utc.strftime("%Y%m%d_%H")
        if hour != self.hour:
            self.close()
            day_root = self.root / now_utc.strftime("%Y-%m-%d")
            day_root.mkdir(parents=True, exist_ok=True)
            self.path = day_root / f"market_books_ws_{hour}_{self.stream_id}.jsonl"
            self.fd = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o644,
            )
            self.hour = hour
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        pending = memoryview(encoded)
        while pending:
            written = os.write(self.fd, pending)
            pending = pending[written:]
        return self.path

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.fd = None


class SubscriptionEpochWriter:
    """Append subscription/capture-policy epochs to their UTC physical shard."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, payload: dict[str, Any], now_utc: datetime) -> Path:
        path = (
            self.root
            / "subscription_epochs"
            / f"subscription_epochs_{now_utc:%Y-%m-%d}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            pending = memoryview(encoded)
            while pending:
                written = os.write(fd, pending)
                pending = pending[written:]
        finally:
            os.close(fd)
        return path


def _rest_health(path: Path, *, now_utc: datetime, max_age_sec: float) -> dict[str, Any]:
    payload = _load_json(path)
    available = _parse_utc(payload.get("available_at_utc"))
    age = None if available is None else max(0.0, (now_utc - available).total_seconds())
    ok = payload.get("status") == "ok" and age is not None and age <= max_age_sec
    return {
        "status": payload.get("status") or "missing",
        "available_at_utc": payload.get("available_at_utc"),
        "age_sec": age,
        "healthy": ok,
        "batch_capture_id": payload.get("batch_capture_id"),
    }


class Collector:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.market_latest = Path(args.market_books_latest)
        self.observation_cache = Path(args.observation_cache)
        self.source_events = Path(args.source_events_jsonl)
        self.output_root = Path(args.output_root)
        self.health_path = Path(args.health_path)
        self.state_path = self.output_root / "selector_state.json"
        state = _load_json(self.state_path)
        self.invalidation_state = {
            str(key): float(value)
            for key, value in (state.get("invalidated_at_epoch") or {}).items()
        }
        previous_health = _load_json(self.health_path)
        today = _utc_now().date().isoformat()
        self.day = today
        self.day_payload_bytes = (
            int(previous_health.get("day_payload_bytes") or 0)
            if previous_health.get("counter_date_utc") == today
            else 0
        )
        self.session_payload_bytes = 0
        self.session_frames = 0
        self.session_events = 0
        self.last_message_at: str | None = None
        self.archive_path: str | None = None
        self.writer = HourlyWriter(self.output_root)
        self.subscription_writer = SubscriptionEpochWriter(self.output_root)
        self.connection_sequence = 0
        self.subscription_epoch_id: str | None = None
        self.subscription_manifest_path: str | None = None
        self.source_event_cursor = SourceEventCursor(self.source_events)
        self.next_report_at_utc: dict[str, str] = {}
        self.selection = Selection(
            tokens=set(),
            token_rows={},
            city_token_counts={},
            active_brackets={},
            grace_brackets={},
            scheduled_cities=[],
            research_cities=[],
            burst_cities=[],
            missing_observation_cities=[],
            invalidation_state=self.invalidation_state,
        )
        self.connected = False
        self.connection_error: str | None = None

    def _reset_day(self, now_utc: datetime) -> None:
        today = now_utc.date().isoformat()
        if today != self.day:
            self.day = today
            self.day_payload_bytes = 0

    def refresh_selection(self, now_utc: datetime) -> Selection:
        observations = _observation_index(self.observation_cache)
        configured_cities = set(self.args.cities)
        scheduled, research, self.next_report_at_utc = scheduled_report_windows(
            {
                key: row
                for key, row in observations.items()
                if key[0] in configured_cities
            },
            now_utc=now_utc,
            before_sec=self.args.report_window_before_sec,
            after_sec=self.args.report_window_after_sec,
            extended_before_sec=self.args.research_window_before_sec,
            research_sample_modulus=self.args.research_sample_modulus,
        )
        bursts = self.source_event_cursor.read(
            cities=configured_cities,
            now_utc=now_utc,
            burst_sec=self.args.event_burst_sec,
        )
        self.selection = select_tokens(
            market_payload=_load_json(self.market_latest),
            observations=observations,
            cities=self.args.cities,
            now_utc=now_utc,
            active_bracket_count=self.args.active_bracket_count,
            research_bracket_count=self.args.research_bracket_count,
            event_bracket_count=self.args.event_bracket_count,
            post_invalidation_sec=self.args.post_invalidation_sec,
            scheduled_keys=scheduled,
            research_keys=research,
            burst_keys=bursts,
            invalidation_state=self.invalidation_state,
        )
        self.invalidation_state = self.selection.invalidation_state
        _publish_json_atomic(
            self.state_path,
            {
                "schema_version": "weather_market_books_ws_selector_state_v1",
                "updated_at_utc": _utc_text(now_utc),
                "invalidated_at_epoch": self.invalidation_state,
            },
        )
        return self.selection

    def publish_subscription_epoch(
        self,
        tokens: set[str],
        now_utc: datetime,
        *,
        reason: str,
    ) -> dict[str, Any]:
        self.connection_sequence += 1
        token_rows = {
            token: {
                key: self.selection.token_rows.get(token, {}).get(key)
                for key in ("city", "event_date", "bracket", "outcome", "condition_id")
            }
            for token in sorted(tokens)
        }
        capture_policy = {
            "ladder_scope": "subscription_hot_strip",
            "active_bracket_count": self.args.active_bracket_count,
            "research_bracket_count": self.args.research_bracket_count,
            "event_bracket_count": self.args.event_bracket_count,
            "post_invalidation_sec": self.args.post_invalidation_sec,
            "event_burst_sec": self.args.event_burst_sec,
            "report_window_before_sec": self.args.report_window_before_sec,
            "report_window_after_sec": self.args.report_window_after_sec,
            "research_window_before_sec": self.args.research_window_before_sec,
            "research_sample_modulus": self.args.research_sample_modulus,
        }
        token_map_id = hashlib.sha256(
            json.dumps(token_rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        capture_policy_id = hashlib.sha256(
            json.dumps(capture_policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        subscription_set_id = hashlib.sha256(
            json.dumps(sorted(tokens), separators=(",", ":")).encode()
        ).hexdigest()
        identity_basis = {
            "producer_build_id": PRODUCER_BUILD_ID,
            "selector_version": SELECTOR_VERSION,
            "started_at_utc": _utc_text(now_utc),
            "connection_sequence": self.connection_sequence,
            "token_map_id": token_map_id,
            "capture_policy_id": capture_policy_id,
            "subscription_set_id": subscription_set_id,
        }
        epoch_id = hashlib.sha256(
            json.dumps(identity_basis, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload = {
            "schema_version": "weather_market_books_ws_subscription_epoch_v2",
            "producer": PRODUCER,
            "producer_build_id": PRODUCER_BUILD_ID,
            "selector_version": SELECTOR_VERSION,
            "subscription_epoch_id": epoch_id,
            "previous_subscription_epoch_id": self.subscription_epoch_id,
            "started_at_utc": _utc_text(now_utc),
            "reason": reason,
            "connection_sequence": self.connection_sequence,
            "token_ids": sorted(tokens),
            "token_rows": token_rows,
            "token_map_id": token_map_id,
            "capture_policy": capture_policy,
            "capture_policy_id": capture_policy_id,
            "subscription_set_id": subscription_set_id,
            "active_brackets": self.selection.active_brackets,
            "scheduled_cities": self.selection.scheduled_cities,
            "research_cities": self.selection.research_cities,
            "burst_cities": self.selection.burst_cities,
        }
        path = self.subscription_writer.write(payload, now_utc)
        self.subscription_epoch_id = epoch_id
        self.subscription_manifest_path = str(path)
        return payload

    def publish_health(self, now_utc: datetime, *, status_override: str | None = None) -> None:
        rest = _rest_health(
            self.market_latest,
            now_utc=now_utc,
            max_age_sec=self.args.rest_max_age_sec,
        )
        budget_exhausted = self.day_payload_bytes >= self.args.daily_payload_budget_bytes
        if status_override:
            status = status_override
        elif budget_exhausted or not rest["healthy"]:
            status = "degraded"
        elif self.selection.tokens and not self.connected:
            status = "degraded"
        else:
            status = "ok"
        _publish_json_atomic(
            self.health_path,
            {
                "schema_version": HEALTH_SCHEMA_VERSION,
                "status": status,
                "producer": PRODUCER,
                "producer_build_id": PRODUCER_BUILD_ID,
                "producer_build_id_basis": PRODUCER_BUILD_ID_BASIS,
                "selector_version": SELECTOR_VERSION,
                "websockets_version": websockets.__version__,
                "pre_transport_connection_guard": True,
                "generated_at_utc": _utc_text(now_utc),
                "available_at_utc": _utc_text(now_utc),
                "connected": self.connected,
                "connection_error": self.connection_error,
                "subscription_epoch_id": self.subscription_epoch_id,
                "subscription_manifest_path": self.subscription_manifest_path,
                "configured_cities": list(self.args.cities),
                "subscribed_tokens": len(self.selection.tokens),
                "city_token_counts": self.selection.city_token_counts,
                "active_brackets": self.selection.active_brackets,
                "grace_brackets": self.selection.grace_brackets,
                "scheduled_cities": self.selection.scheduled_cities,
                "research_cities": self.selection.research_cities,
                "burst_cities": self.selection.burst_cities,
                "missing_observation_cities": self.selection.missing_observation_cities,
                "post_invalidation_sec": self.args.post_invalidation_sec,
                "event_burst_sec": self.args.event_burst_sec,
                "report_window_before_sec": self.args.report_window_before_sec,
                "report_window_after_sec": self.args.report_window_after_sec,
                "research_window_before_sec": self.args.research_window_before_sec,
                "research_sample_modulus": self.args.research_sample_modulus,
                "next_report_at_utc": self.next_report_at_utc,
                "active_bracket_count": self.args.active_bracket_count,
                "research_bracket_count": self.args.research_bracket_count,
                "event_bracket_count": self.args.event_bracket_count,
                "counter_date_utc": self.day,
                "day_payload_bytes": self.day_payload_bytes,
                "daily_payload_budget_bytes": self.args.daily_payload_budget_bytes,
                "budget_exhausted": budget_exhausted,
                "session_payload_bytes": self.session_payload_bytes,
                "session_frames": self.session_frames,
                "session_events": self.session_events,
                "last_message_at_utc": self.last_message_at,
                "archive_path": self.archive_path,
                "rest_collector": rest,
                "market_proxy_configured": bool(self.args.market_proxy),
            },
        )

    async def run(self) -> None:
        retry_sec = 1.0
        try:
            while True:
                now_utc = _utc_now()
                self._reset_day(now_utc)
                selection = self.refresh_selection(now_utc)
                if self.day_payload_bytes >= self.args.daily_payload_budget_bytes:
                    self.connected = False
                    self.connection_error = "daily_payload_budget_exhausted"
                    self.publish_health(now_utc)
                    await asyncio.sleep(min(60.0, self.args.reconcile_sec))
                    continue
                if not selection.tokens:
                    self.connected = False
                    self.connection_error = None
                    self.publish_health(now_utc)
                    await asyncio.sleep(self.args.reconcile_sec)
                    continue
                try:
                    await self._run_connection(selection.tokens)
                    retry_sec = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.connected = False
                    self.connection_error = f"{type(exc).__name__}: {exc}"
                    self.publish_health(_utc_now())
                    await asyncio.sleep(retry_sec)
                    retry_sec = min(30.0, retry_sec * 2.0)
        finally:
            self.writer.close()

    async def _run_connection(self, initial_tokens: set[str]) -> None:
        connect_kwargs: dict[str, Any] = {
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": None,
            "open_timeout": 20,
            "close_timeout": 5,
            "create_connection": PreTransportSafeClientConnection,
        }
        if self.args.market_proxy:
            connect_kwargs["proxy"] = self.args.market_proxy
        async with websockets.connect(CLOB_WS_URL, **connect_kwargs) as ws:
            await ws.send(
                json.dumps(
                    {
                        "assets_ids": sorted(initial_tokens),
                        "type": "market",
                        "custom_feature_enabled": True,
                    },
                    separators=(",", ":"),
                )
            )
            subscribed = set(initial_tokens)
            self.publish_subscription_epoch(subscribed, _utc_now(), reason="connect")
            self.connected = True
            self.connection_error = None
            next_reconcile = time.monotonic() + self.args.reconcile_sec
            next_health = time.monotonic()
            while True:
                timeout = max(0.05, min(next_reconcile, next_health) - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    raw = None
                now_utc = _utc_now()
                self._reset_day(now_utc)
                if raw is not None:
                    raw_bytes = raw if isinstance(raw, bytes) else raw.encode()
                    try:
                        message = json.loads(raw_bytes)
                    except json.JSONDecodeError:
                        message = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
                    message_tokens = _message_token_ids(message)
                    if not message_tokens or message_tokens.intersection(subscribed):
                        received_ns = time.time_ns()
                        record = {
                            "schema_version": SCHEMA_VERSION,
                            "producer": PRODUCER,
                            "producer_build_id": PRODUCER_BUILD_ID,
                            "received_at_utc": _utc_text(now_utc),
                            "received_at_ns": received_ns,
                            "subscription_token_count": len(subscribed),
                            "subscription_epoch_id": self.subscription_epoch_id,
                            "selector_version": SELECTOR_VERSION,
                            "message_token_ids": sorted(message_tokens),
                            "message": message,
                        }
                        record["raw_frame_id"] = canonical_ws_frame_id(record)
                        archive_path = self.writer.write(record, now_utc)
                        self.archive_path = str(archive_path)
                        self.session_payload_bytes += len(raw_bytes)
                        self.day_payload_bytes += len(raw_bytes)
                        self.session_frames += 1
                        self.session_events += len(message) if isinstance(message, list) else 1
                        self.last_message_at = record["received_at_utc"]
                if time.monotonic() >= next_reconcile:
                    selection = self.refresh_selection(now_utc)
                    desired = selection.tokens
                    if self.day_payload_bytes >= self.args.daily_payload_budget_bytes:
                        self.publish_health(now_utc)
                        return
                    new_tokens = desired - subscribed
                    expired_tokens = subscribed - desired
                    if new_tokens:
                        await ws.send(
                            json.dumps(
                                {
                                    "assets_ids": sorted(new_tokens),
                                    "operation": "subscribe",
                                    "custom_feature_enabled": True,
                                },
                                separators=(",", ":"),
                            )
                        )
                    if expired_tokens:
                        await ws.send(
                            json.dumps(
                                {
                                    "assets_ids": sorted(expired_tokens),
                                    "operation": "unsubscribe",
                                },
                                separators=(",", ":"),
                            )
                        )
                    subscribed = set(desired)
                    if new_tokens or expired_tokens:
                        self.publish_subscription_epoch(
                            subscribed,
                            now_utc,
                            reason="selector_reconcile",
                        )
                    if not subscribed:
                        self.publish_health(now_utc)
                        return
                    next_reconcile = time.monotonic() + self.args.reconcile_sec
                if time.monotonic() >= next_health:
                    self.publish_health(now_utc)
                    next_health = time.monotonic() + self.args.health_interval_sec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-books-latest", required=True)
    parser.add_argument("--observation-cache", required=True)
    parser.add_argument("--source-events-jsonl", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--health-path", required=True)
    parser.add_argument("--market-proxy", default=os.environ.get("WEATHER_DATA_FEED_MARKET_PROXY", ""))
    parser.add_argument("--cities", nargs="+", default=list(DEFAULT_CITIES))
    parser.add_argument("--active-bracket-count", type=int, default=2)
    parser.add_argument("--research-bracket-count", type=int, default=3)
    parser.add_argument("--event-bracket-count", type=int, default=3)
    parser.add_argument("--post-invalidation-sec", type=float, default=0.0)
    parser.add_argument("--event-burst-sec", type=float, default=120.0)
    parser.add_argument("--report-window-before-sec", type=float, default=45.0)
    parser.add_argument("--report-window-after-sec", type=float, default=120.0)
    parser.add_argument("--research-window-before-sec", type=float, default=125.0)
    parser.add_argument("--research-sample-modulus", type=int, default=6)
    parser.add_argument("--reconcile-sec", type=float, default=5.0)
    parser.add_argument("--health-interval-sec", type=float, default=10.0)
    parser.add_argument("--rest-max-age-sec", type=float, default=420.0)
    parser.add_argument("--daily-payload-budget-bytes", type=int, default=3_000_000_000)
    parser.add_argument("--select-once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    collector = Collector(args)
    if args.select_once:
        selection = collector.refresh_selection(_utc_now())
        print(
            json.dumps(
                {
                    "status": "ok",
                    "selector_version": SELECTOR_VERSION,
                    "tokens": len(selection.tokens),
                    "city_token_counts": selection.city_token_counts,
                    "active_brackets": selection.active_brackets,
                    "grace_brackets": selection.grace_brackets,
                    "scheduled_cities": selection.scheduled_cities,
                    "research_cities": selection.research_cities,
                    "burst_cities": selection.burst_cities,
                    "missing_observation_cities": selection.missing_observation_cities,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    asyncio.run(_run_until_stopped(collector))
    return 0


async def _run_until_stopped(collector: Collector) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(collector.run())
    installed: list[signal.Signals] = []
    for stop_signal in (signal.SIGTERM, signal.SIGHUP):
        try:
            loop.add_signal_handler(stop_signal, task.cancel)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(stop_signal)
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for stop_signal in installed:
            loop.remove_signal_handler(stop_signal)


if __name__ == "__main__":
    raise SystemExit(main())
