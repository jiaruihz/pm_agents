"""Bounded, append-only METAR.ws -> Polymarket capture-demand adapter.

This module is deliberately only a *demand producer*.  It reads immutable lab
source-event rows and a REST market map, then appends requests for the existing
market-book owner.  It neither opens a market WebSocket nor creates orders,
intents, fills, or credentials.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path
from typing import Any

from src.platform.market_data.capture_demand import CaptureDemand


TARGET_CITIES = frozenset(
    {"Miami", "LA", "NYC", "Houston", "Dallas", "Seattle", "SanFrancisco", "Chicago"}
)
TARGET_SOURCES = frozenset({"metar_ws_metar", "metar_ws_hfmetar", "metar_ws_datis"})
CHECKPOINTS = (0, 15, 30, 60, 120, 300)
CONSUMER_ID = "weather_metar_ws_research"
STRATEGY_KEY = "weather.metar_ws_event_repricing"
TTL_SECONDS = 330


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return ()
    def read() -> Iterable[dict[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, Mapping):
                    yield dict(value)
    return read()


def _source_rows(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return ()
    def read() -> Iterable[dict[str, Any]]:
        for path in sorted(root.glob("*/sources.jsonl")):
            yield from _rows(path)
    return read()


def _append(path: Path, values: Iterable[Mapping[str, Any]]) -> int:
    rows = [json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False) for value in values]
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n")
        handle.flush()
    return len(rows)


@contextmanager
def _exclusive_materializer_lock(path: Path) -> Iterable[None]:
    """Fail closed when a second demand materializer targets the same journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"capture demand materializer already active: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _existing(path: Path, key: str) -> set[str]:
    return {str(row[key]) for row in _rows(path) if row.get(key)}


def _bracket_number(value: Any) -> float | None:
    text = str(value or "").strip().replace("°F", "").replace("F", "")
    if not text:
        return None
    token = text.split("-")[0].replace("+", "").strip()
    try:
        return float(token)
    except ValueError:
        return None


def _market_rows(path: Path, *, city: str, target_date: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records", []) if isinstance(payload, Mapping) else []
    return [
        dict(row) for row in records
        if isinstance(row, Mapping)
        and str(row.get("city")) == city
        and str(row.get("event_date")) == target_date
        and str(row.get("outcome", "")).lower() in {"yes", "no"}
    ]


def _hot_strip(rows: list[dict[str, Any]], running_max_f: float) -> tuple[list[dict[str, Any]], str | None]:
    by_bracket: dict[str, list[dict[str, Any]]] = {}
    values: list[tuple[float, str]] = []
    for row in rows:
        bracket = str(row.get("bracket") or "")
        value = _bracket_number(bracket)
        if not bracket or value is None:
            continue
        by_bracket.setdefault(bracket, []).append(row)
        if (value, bracket) not in values:
            values.append((value, bracket))
    if not values:
        return [], "market_brackets_unparseable"
    ordered = sorted(values)
    current_index = min(range(len(ordered)), key=lambda index: abs(ordered[index][0] - running_max_f))
    selected_brackets = {bracket for _, bracket in ordered[max(0, current_index - 1): current_index + 2]}
    selected = [row for row in rows if str(row.get("bracket")) in selected_brackets]
    for bracket in selected_brackets:
        bracket_rows = [row for row in selected if str(row.get("bracket")) == bracket]
        outcomes = [str(row.get("outcome") or "").lower() for row in bracket_rows]
        condition_ids = {str(row.get("condition_id") or "") for row in bracket_rows}
        if sorted(outcomes) != ["no", "yes"] or len(condition_ids) != 1 or "" in condition_ids:
            return [], "hot_strip_yes_no_pair_incomplete"
    # A demand requires both the condition and token.  Do not create a partial
    # subscription request if the market map cannot prove either identity.
    if any(not row.get("token_id") or not row.get("condition_id") for row in selected):
        return [], "market_token_or_condition_missing"
    if len(selected) > 6:
        return [], "hot_strip_token_bound_exceeded"
    return sorted(selected, key=lambda row: (str(row["bracket"]), str(row["outcome"]))), None


def _resolution_id(event_id: str, kind: str) -> str:
    return f"{event_id}:{kind}"


def _event_candidate(row: Mapping[str, Any]) -> tuple[bool, str | None]:
    if str(row.get("city")) not in TARGET_CITIES:
        return False, "city_not_in_fixed_cohort"
    if str(row.get("source")) not in TARGET_SOURCES:
        return False, "source_not_metrar_ws_target"  # stable ledger spelling retained for identity
    if str(row.get("event_role")) not in {"new_content", "revision"}:
        return False, "event_role_not_content_or_revision"
    try:
        float(row.get("temp_c"))
    except (TypeError, ValueError):
        return False, "temperature_missing"
    if _utc(str(row.get("transport_received_at_utc") or "")) is None:
        return False, "transport_time_invalid"
    if row.get("transport_received_monotonic_ns") is None:
        return False, "transport_monotonic_missing"
    return True, None


def _materialize_capture_demands_unlocked(
    source_events_root: Path,
    market_books_latest: Path,
    demand_jsonl: Path,
    *,
    resolution_jsonl: Path | None = None,
) -> dict[str, int]:
    """Append bounded direct-token demands and a complete resolution ledger.

    The function is restart safe: both demand IDs and per-event resolution IDs
    are immutable and are checked before append.  A source event is always
    represented in the resolution journal, including events that cannot lead
    to a market capture.
    """
    resolution_jsonl = resolution_jsonl or demand_jsonl.with_name(
        f"{demand_jsonl.stem}_resolution.jsonl"
    )
    existing_demands = _existing(demand_jsonl, "demand_id")
    existing_resolutions = _existing(resolution_jsonl, "resolution_id")
    existing_event_ids = _existing(demand_jsonl, "trigger_event_id")
    running_max: dict[tuple[str, str], float] = {}
    demands: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    scheduled = 0

    for event in _source_rows(source_events_root):
        event_id = str(event.get("information_event_id") or "")
        if not event_id:
            continue
        accepted, blocker = _event_candidate(event)
        if not accepted:
            rid = _resolution_id(event_id, "event")
            if rid not in existing_resolutions:
                resolutions.append({
                    "schema_version": "metar_market_capture_resolution_v1",
                    "resolution_id": rid,
                    "information_event_id": event_id,
                    "status": "blocked",
                    "blocker": blocker,
                    "transport_received_monotonic_ns": event.get("transport_received_monotonic_ns"),
                })
                existing_resolutions.add(rid)
            continue

        city, target_date = str(event["city"]), str(event.get("target_date") or "")
        key = (city, target_date)
        temperature_f = float(event["temp_c"]) * 9.0 / 5.0 + 32.0
        running_max[key] = max(running_max.get(key, -math.inf), temperature_f)
        market_rows = _market_rows(market_books_latest, city=city, target_date=target_date)
        strip, market_blocker = _hot_strip(market_rows, running_max[key]) if market_rows else ([], "market_not_found")
        received = _utc(str(event["transport_received_at_utc"]))
        assert received is not None
        rid = _resolution_id(event_id, "event")
        if market_blocker:
            if rid not in existing_resolutions:
                resolutions.append({
                    "schema_version": "metar_market_capture_resolution_v1",
                    "resolution_id": rid,
                    "information_event_id": event_id,
                    "status": "blocked",
                    "blocker": market_blocker,
                    "running_max_f": running_max[key],
                    "transport_received_monotonic_ns": event.get("transport_received_monotonic_ns"),
                })
                existing_resolutions.add(rid)
            continue

        for market in strip:
            demand = CaptureDemand.create(
                consumer_id=CONSUMER_ID,
                strategy_key=STRATEGY_KEY,
                condition_id=str(market["condition_id"]),
                token_id=str(market["token_id"]),
                reason="metar_ws_first_seen_hot_strip",
                priority="P0",
                requested_at_utc=_utc_text(received),
                expires_at_utc=_utc_text(received + timedelta(seconds=TTL_SECONDS)),
                desired_transport="REST_WS",
                requested_checkpoints_seconds=CHECKPOINTS,
                trigger_event_id=event_id,
                metadata={
                    "city": city, "target_date": target_date, "bracket": str(market["bracket"]),
                    "outcome": str(market["outcome"]).lower(), "running_max_f": running_max[key],
                    "transport_received_monotonic_ns": event.get("transport_received_monotonic_ns"),
                    "event_role": event.get("event_role"), "source": event.get("source"),
                    "clock_valid": bool(event.get("clock_valid")),
                    "pit_eligible": bool(event.get("pit_eligible")),
                    "formal_latency_eligible": bool(event.get("clock_valid")) and bool(event.get("pit_eligible")),
                },
            ).to_dict()
            if demand["demand_id"] not in existing_demands:
                demands.append(demand)
                existing_demands.add(str(demand["demand_id"]))
        if rid not in existing_resolutions:
            resolutions.append({
                "schema_version": "metar_market_capture_resolution_v1",
                "resolution_id": rid, "information_event_id": event_id,
                "status": (
                    "emitted" if bool(event.get("clock_valid")) and bool(event.get("pit_eligible"))
                    else "emitted_clock_invalid_exploratory"
                ),
                "demand_count": len(strip), "running_max_f": running_max[key],
                "formal_latency_eligible": bool(event.get("clock_valid")) and bool(event.get("pit_eligible")),
                "transport_received_monotonic_ns": event.get("transport_received_monotonic_ns"),
            })
            existing_resolutions.add(rid)

        # Official reports also schedule a pre-window for the expected next
        # hourly METAR, so the owner can establish a WS baseline before arrival.
        if (
            str(event.get("source")) == "metar_ws_metar"
            and str(event.get("report_kind") or "METAR").upper() == "METAR"
        ):
            report_at = _utc(str(event.get("source_report_ts_utc") or ""))
            pre_id = _resolution_id(event_id, "prewindow")
            if report_at is None:
                if pre_id not in existing_resolutions:
                    resolutions.append({"schema_version": "metar_market_capture_resolution_v1", "resolution_id": pre_id,
                        "information_event_id": event_id, "status": "blocked", "blocker": "report_time_invalid"})
                    existing_resolutions.add(pre_id)
            else:
                requested = report_at + timedelta(hours=1, seconds=-120)
                expires = report_at + timedelta(hours=1, seconds=360)
                for market in strip:
                    demand = CaptureDemand.create(
                        consumer_id=CONSUMER_ID, strategy_key=STRATEGY_KEY,
                        condition_id=str(market["condition_id"]), token_id=str(market["token_id"]),
                        reason="metar_ws_next_report_prewindow_hot_strip", priority="P0",
                        requested_at_utc=_utc_text(requested), expires_at_utc=_utc_text(expires),
                        desired_transport="REST_WS", requested_checkpoints_seconds=CHECKPOINTS,
                        trigger_event_id=f"{event_id}:next_report_prewindow",
                        metadata={"city": city, "target_date": target_date, "bracket": str(market["bracket"]),
                            "outcome": str(market["outcome"]).lower(), "running_max_f": running_max[key],
                            "prewindow_for_information_event_id": event_id,
                            "schedule_basis": "prior_routine_metar_report_plus_1h",
                            "clock_valid": bool(event.get("clock_valid")),
                            "pit_eligible": bool(event.get("pit_eligible")),
                            "formal_latency_eligible": bool(event.get("clock_valid")) and bool(event.get("pit_eligible"))},
                    ).to_dict()
                    if demand["demand_id"] not in existing_demands:
                        demands.append(demand); existing_demands.add(str(demand["demand_id"])); scheduled += 1
                if pre_id not in existing_resolutions:
                    resolutions.append({"schema_version": "metar_market_capture_resolution_v1", "resolution_id": pre_id,
                        "information_event_id": event_id, "status": "scheduled", "demand_count": len(strip),
                        "schedule_basis": "prior_routine_metar_report_plus_1h"})
                    existing_resolutions.add(pre_id)

    return {
        "capture_demands_written": _append(demand_jsonl, demands),
        "resolution_rows_written": _append(resolution_jsonl, resolutions),
        "scheduled_prewindow_demands_written": scheduled,
    }


def materialize_capture_demands(
    source_events_root: Path,
    market_books_latest: Path,
    demand_jsonl: Path,
    *,
    resolution_jsonl: Path | None = None,
) -> dict[str, int]:
    lock_path = demand_jsonl.with_name(f".{demand_jsonl.name}.materializer.lock")
    with _exclusive_materializer_lock(lock_path):
        return _materialize_capture_demands_unlocked(
            source_events_root,
            market_books_latest,
            demand_jsonl,
            resolution_jsonl=resolution_jsonl,
        )
