"""Zero-notional Amsterdam WCIR frozen-shadow materializer.

This module deliberately has no order, plan, venue or credential imports.  It
turns immutable score rows into equally immutable evidence rows; all writes are
JSONL research artifacts and every logical identity is fail-closed on drift.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable

from src.platform.market_data.capture_demand import CaptureDemand
from weather_city_runtime.next_print_contracts import CITY_CONTRACTS

UTC = timezone.utc
STRATEGY_KEY = "weather_amsterdam_wcir_frozen_v2"
CHECKPOINTS = (0, 5, 15, 30, 60, 120)
ENTRY_LATENCY_SECONDS = 49.772999999999996
ENTRY_LATENCY_CONTRACT_ID = "8a7f53b0991ee069d5c9c9ec0e5ef10d3eb9499cc1f823b2b847e03cc0e1f64e"
OFFICIAL_MATCH_WINDOW_SECONDS = CITY_CONTRACTS["Amsterdam"].matching_window_seconds


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return parsed.astimezone(UTC)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"non-object journal row: {path}")
                rows.append(row)
    return rows


def _write_json_atomic(path: Path, value: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if pretty:
                handle.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str) + "\n")
            else:
                handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_source_events(
    path: Path | None, *, target_dates: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """Read a fixture file or the daily source-events tree, without guessing."""
    if path is None:
        return []
    if path.is_file():
        return read_jsonl(path)
    if not path.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    dates = sorted({str(value) for value in (target_dates or ()) if value})
    items = (
        [path / value / "sources.jsonl" for value in dates]
        if target_dates is not None
        else sorted(path.glob("*/sources.jsonl"))
    )
    for item in items:
        if not item.is_file():
            continue
        rows.extend(read_jsonl(item))
    return rows


class ImmutableJsonl:
    """A small append-only JSONL store keyed by a stable identity field."""
    def __init__(self, path: Path, identity: str) -> None:
        self.path, self.identity = path, identity
        loaded = read_jsonl(path)
        self.rows = {str(row[identity]): row for row in loaded}
        if len(self.rows) != len(loaded):
            # Duplicate identities are allowed only when byte-equivalent.
            seen: dict[str, dict[str, Any]] = {}
            for row in loaded:
                key = str(row[identity])
                if key in seen and seen[key] != row:
                    raise ValueError(f"immutable payload drift for {identity}: {key}")
                seen[key] = row
            self.rows = seen

    def put(self, row: Mapping[str, Any]) -> bool:
        payload = json.loads(json.dumps(
            dict(row),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ))
        key = str(payload.get(self.identity) or "")
        if not key:
            raise ValueError(f"missing immutable identity {self.identity}")
        previous = self.rows.get(key)
        if previous is not None:
            if previous != payload:
                raise ValueError(f"immutable payload drift for {self.identity}: {key}")
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, encoded.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.rows[key] = payload
        return True


def prediction_identity(row: Mapping[str, Any]) -> str:
    return str(row.get("prediction_row_id") or _hash({k: v for k, v in row.items() if k != "prediction_row_id"}))


def _market_rows(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("market_identity") or ()
    items: Sequence[Any] = (raw,) if isinstance(raw, Mapping) else raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else ()
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        required = ("market_id", "condition_id", "token_id", "side", "native_bracket")
        if any(item.get(name) in (None, "") for name in required):
            continue
        output.append({
            "market_id": str(item["market_id"]),
            "condition_id": str(item["condition_id"]),
            "token_id": str(item["token_id"]),
            "side": str(item["side"]).upper(),
            "native_bracket": str(item["native_bracket"]),
            "target_date": str(item.get("target_date") or row.get("raw_source_lineage", {}).get("target_date") or ""),
            "identity_source": "FROZEN_PREDICTION_EXACT_IDENTITY",
        })
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    token_contracts: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for item in output:
        key = (item["market_id"], item["condition_id"], item["token_id"], item["side"])
        previous = unique.get(key)
        if previous is not None and previous != item:
            raise ValueError(
                "conflicting exact market identity for the same market/condition/token/side"
            )
        token_key = (item["market_id"], item["condition_id"], item["token_id"])
        token_contract = (item["side"], item["native_bracket"], item["target_date"])
        previous_contract = token_contracts.get(token_key)
        if previous_contract is not None and previous_contract != token_contract:
            raise ValueError(
                "exact token identity drift across side/native_bracket/target_date"
            )
        unique[key] = item
        token_contracts[token_key] = token_contract
    return sorted(unique.values(), key=lambda item: (item["market_id"], item["side"], item["token_id"]))


def resolve_market_identities(
    prediction: Mapping[str, Any], market_payload: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Resolve immutable token identity without using a post-event price.

    Frozen prediction identities win.  The current REST ladder may fill only
    identity metadata for a previously-null row; it is explicitly not feature,
    entry, or executable-book evidence.
    """
    pid = prediction_identity(prediction)
    identities = _market_rows(prediction)
    source = "FROZEN_PREDICTION_EXACT_IDENTITY"
    reason: str | None = None
    if not identities:
        lineage = prediction.get("raw_source_lineage") or {}
        target_date = str(lineage.get("target_date") or "")
        running_max = (prediction.get("feature_vector") or {}).get("official_running_max")
        if not target_date or running_max is None:
            reason = "TARGET_DATE_OR_RUNNING_MAX_MISSING"
        else:
            bracket = str(int(round(float(running_max))))
            candidates: list[dict[str, Any]] = []
            for raw in (market_payload or {}).get("records") or ():
                if not isinstance(raw, Mapping):
                    continue
                if (
                    str(raw.get("city") or "") != "Amsterdam"
                    or str(raw.get("event_date") or "") != target_date
                    or str(raw.get("extreme_kind") or "max") != "max"
                    or str(raw.get("bracket") or "") != bracket
                ):
                    continue
                if any(raw.get(name) in (None, "") for name in ("market_id", "condition_id", "token_id", "outcome")):
                    continue
                candidates.append({
                    "market_id": str(raw["market_id"]),
                    "condition_id": str(raw["condition_id"]),
                    "token_id": str(raw["token_id"]),
                    "side": str(raw["outcome"]).upper(),
                    "native_bracket": bracket,
                    "target_date": target_date,
                    "identity_source": "POST_DECISION_REST_IDENTITY_ONLY_NOT_BOOK_EVIDENCE",
                })
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for item in candidates:
                groups.setdefault((item["market_id"], item["condition_id"]), []).append(item)
            complete = [
                rows for rows in groups.values()
                if {item["side"] for item in rows} == {"YES", "NO"}
                and len({item["token_id"] for item in rows}) == 2
            ]
            if len(complete) == 1:
                identities = sorted(complete[0], key=lambda item: (item["side"], item["token_id"]))
                source = "POST_DECISION_REST_IDENTITY_ONLY_NOT_BOOK_EVIDENCE"
            else:
                reason = "EXACT_MARKET_IDENTITY_NOT_FOUND" if not complete else "AMBIGUOUS_EXACT_MARKET_IDENTITY"
    status = "resolved" if identities else "missing"
    body = {
        "prediction_row_id": pid,
        "status": status,
        "missing_reason": reason,
        "identity_source": source if identities else None,
        "identities": identities,
        "orders": 0,
        "fills": 0,
        "notional": 0,
    }
    body["market_identity_row_id"] = _hash({"kind": "market_identity", **body})
    return body


def capture_demands_for_prediction(
    row: Mapping[str, Any],
    identities: Iterable[Mapping[str, Any]] | None = None,
    *,
    requested_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Declare one demand per exact YES/NO token; never infer a complement."""
    identity_rows = list(identities if identities is not None else _market_rows(row))
    if not identity_rows:
        return []
    requested = _parse(requested_at_utc or row.get("raw_source_lineage", {}).get("available_at_utc") or _now())
    expires = requested + timedelta(minutes=120)
    trigger = str(row.get("decision_vintage_id") or row.get("event_id") or prediction_identity(row))
    output: list[dict[str, Any]] = []
    for identity in identity_rows:
        condition_id = identity.get("condition_id")
        token_id = identity.get("token_id")
        if not condition_id or not token_id:
            continue
        demand = CaptureDemand.create(
            consumer_id=STRATEGY_KEY,
            strategy_key=STRATEGY_KEY,
            condition_id=str(condition_id),
            token_id=str(token_id),
            reason="next_print_previous_running_max",
            priority="P1",
            requested_at_utc=requested.isoformat().replace("+00:00", "Z"),
            expires_at_utc=expires.isoformat().replace("+00:00", "Z"),
            desired_transport="REST_WS",
            requested_checkpoints_seconds=CHECKPOINTS,
            trigger_event_id=trigger,
            metadata={
                "prediction_row_id": prediction_identity(row),
                "forward_epoch_id": row.get("forward_epoch_id"),
                "city": "Amsterdam",
                "target_date": identity.get("target_date"),
                "bracket": identity.get("native_bracket"),
                "side": identity.get("side"),
                "identity_source": identity.get("identity_source"),
                "orders": 0,
                "fills": 0,
                "notional": 0,
            },
        )
        output.append(demand.to_dict())
    return output


def capture_demand_for_prediction(
    row: Mapping[str, Any], *, requested_at_utc: str | None = None
) -> dict[str, Any] | None:
    """Backward-compatible single-token helper used only by focused tests."""
    demands = capture_demands_for_prediction(row, requested_at_utc=requested_at_utc)
    return demands[0] if demands else None


def _event_time(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("source_event_ts_utc") or row.get("source_report_ts_utc") or row.get("observation_time_utc") or row.get("ts_utc")
    try:
        return _parse(str(value)) if value else None
    except ValueError:
        return None


def _routine_eham(row: Mapping[str, Any]) -> bool:
    if str(row.get("station") or row.get("station_id") or "") != "EHAM":
        return False
    raw = str(row.get("raw_metar") or "").upper()
    report_kind = str(row.get("report_type") or row.get("report_kind") or "").upper()
    return not (raw.startswith("SPECI") or " SPECI " in raw or report_kind == "SPECI")


def _official_payload_hash(row: Mapping[str, Any]) -> str:
    """Hash official content, never ingestion clocks, for duplicate collapse."""
    supplied = row.get("payload_hash") or row.get("raw_payload_hash")
    if supplied:
        return str(supplied)
    stable = {
        name: row.get(name)
        for name in (
            "station",
            "station_id",
            "source_report_ts_utc",
            "source_event_ts_utc",
            "raw_metar",
            "temp_c",
            "dewpoint_c",
            "wind_dir_deg",
            "wind_speed_kt",
            "pressure_hpa",
            "present_weather_codes",
            "report_type",
            "report_kind",
        )
        if row.get(name) is not None
    }
    return _hash({"official_payload_without_arrival_clock": stable})


def label_for_prediction(prediction: Mapping[str, Any], source_events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Strictly link the next *distinct routine* EHAM METAR after a score."""
    prediction_id = prediction_identity(prediction)
    lineage = prediction.get("raw_source_lineage") or {}
    prior = (lineage.get("prior_official") or {}).get("last_official_native_value")
    source_observed_at = lineage.get("observation_time_utc")
    source_first_seen_at = lineage.get("available_at_utc")
    target_date = str(lineage.get("target_date") or "")
    base = {"prediction_row_id": prediction_id, "orders": 0, "fills": 0, "notional": 0}
    if prior is None or not source_observed_at or not source_first_seen_at or not target_date:
        return {**base, "label_row_id": _hash({"kind": "label", "prediction_row_id": prediction_id, "reason": "MISSING_PRIOR_OFFICIAL_OR_DECISION_CLOCK"}), "status": "missing", "missing_reason": "MISSING_PRIOR_OFFICIAL_OR_DECISION_CLOCK", "future_next_print_label": None}
    try:
        source_observed = _parse(str(source_observed_at))
        source_first_seen = _parse(str(source_first_seen_at))
        prior_value = float(prior)
    except (ValueError, TypeError):
        return {**base, "label_row_id": _hash({"kind": "label", "prediction_row_id": prediction_id, "reason": "INVALID_PRIOR_OFFICIAL_OR_DECISION_CLOCK"}), "status": "missing", "missing_reason": "INVALID_PRIOR_OFFICIAL_OR_DECISION_CLOCK", "future_next_print_label": None}
    future_candidates: list[tuple[datetime, datetime, Mapping[str, Any]]] = []
    for event in source_events:
        if (
            str(event.get("city") or "Amsterdam") != "Amsterdam"
            or str(event.get("source") or "aviationweather_metar") != "aviationweather_metar"
            or str(event.get("target_date") or "") != target_date
            or not _routine_eham(event)
        ):
            continue
        observed = _event_time(event)
        first_seen_raw = event.get("first_seen_at_utc") or event.get("local_detect_ts_utc")
        try:
            first_seen = _parse(str(first_seen_raw)) if first_seen_raw else None
        except ValueError:
            first_seen = None
        if (
            observed is None
            or first_seen is None
            or observed <= source_observed
            or first_seen <= source_first_seen
            or event.get("temp_c") is None
        ):
            continue
        future_candidates.append((observed, first_seen, event))
    candidates = [
        item for item in future_candidates
        if (item[0] - source_observed).total_seconds() <= OFFICIAL_MATCH_WINDOW_SECONDS
    ]
    if not candidates:
        reason = (
            "NEXT_ROUTINE_OUTSIDE_FROZEN_MATCH_WINDOW"
            if future_candidates
            else "NEXT_ROUTINE_EHAM_METAR_NOT_YET_CAPTURED"
        )
        status = "missing" if future_candidates else "pending"
        return {**base, "label_row_id": _hash({"kind": "label", "prediction_row_id": prediction_id, "reason": reason}), "status": status, "missing_reason": reason, "future_next_print_label": None,
                "matching_window_seconds": OFFICIAL_MATCH_WINDOW_SECONDS}
    candidates.sort(key=lambda item: (item[0], item[1], _official_payload_hash(item[2])))
    observed, first_seen, event = candidates[0]
    same_print = [item for item in candidates if item[0] == observed]
    payloads = {
        _official_payload_hash(item[2])
        for item in same_print
    }
    if len(payloads) > 1:
        reason = "AMBIGUOUS_OFFICIAL_PRINT"
        return {
            **base,
            "label_row_id": _hash({"kind": "label", "prediction_row_id": prediction_id, "reason": reason, "official_observed_at": observed.isoformat()}),
            "status": "missing",
            "missing_reason": reason,
            "future_next_print_label": None,
        }
    native = float(event["temp_c"])
    delta = int(math.floor(native + 0.5)) - int(math.floor(prior_value + 0.5))
    payload_hash = _official_payload_hash(event)
    official_key = str(event.get("content_key") or event.get("provider_item_id") or f"EHAM|{observed.isoformat()}|{payload_hash}")
    return {**base, "label_row_id": _hash({"kind": "label", "prediction_row_id": prediction_id, "official_print_key": official_key}), "status": "linked", "missing_reason": None, "next_official_delta_native_tick": delta,
            "future_next_print_label": delta, "prior_official_native_value": prior_value,
            "next_official_native_value": native, "official_print_time_utc": observed.isoformat().replace("+00:00", "Z"),
            "official_first_seen_at_utc": first_seen.isoformat().replace("+00:00", "Z"),
            "official_print_key": official_key,
            "official_raw_payload_hash": payload_hash,
            "matching_window_seconds": OFFICIAL_MATCH_WINDOW_SECONDS}


def settlement_for_identity(
    prediction: Mapping[str, Any], identity: Mapping[str, Any], db_path: Path | None
) -> dict[str, Any]:
    pid = prediction_identity(prediction)
    base = {"prediction_row_id": pid, "orders": 0, "fills": 0, "notional": 0}
    condition_id, token_id = identity.get("condition_id"), identity.get("token_id")
    identity_key = {"condition_id": condition_id, "token_id": token_id}
    if not condition_id or not token_id:
        return {**base, "settlement_row_id": _hash({"kind": "settlement", "prediction_row_id": pid, **identity_key, "reason": "EXACT_CONDITION_OR_TOKEN_NOT_CAPTURED"}), "status": "missing", "missing_reason": "EXACT_CONDITION_OR_TOKEN_NOT_CAPTURED", "settlement": None}
    if db_path is None or not db_path.is_file():
        return {**base, **identity_key, "settlement_row_id": _hash({"kind": "settlement", "prediction_row_id": pid, **identity_key, "reason": "CANONICAL_SETTLEMENT_DB_UNAVAILABLE"}), "status": "pending", "missing_reason": "CANONICAL_SETTLEMENT_DB_UNAVAILABLE", "settlement": None}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(settlement_outcomes)")}
        if not {"condition_id", "token_id"}.issubset(columns):
            return {**base, **identity_key, "settlement_row_id": _hash({"kind": "settlement", "prediction_row_id": pid, **identity_key, "reason": "SETTLEMENT_EXACT_JOIN_COLUMNS_UNAVAILABLE"}), "status": "missing", "missing_reason": "SETTLEMENT_EXACT_JOIN_COLUMNS_UNAVAILABLE", "settlement": None}
        rows = conn.execute("SELECT * FROM settlement_outcomes WHERE condition_id=? AND token_id=?", (str(condition_id), str(token_id))).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        reason = "EXACT_SETTLEMENT_NOT_FOUND" if not rows else "AMBIGUOUS_EXACT_SETTLEMENT_JOIN"
        return {**base, "settlement_row_id": _hash({"kind": "settlement", "prediction_row_id": pid, **identity_key, "reason": reason}), "status": "pending" if not rows else "missing", "missing_reason": reason, "settlement": None,
                "condition_id": condition_id, "token_id": token_id}
    settlement = dict(rows[0])
    return {**base, "settlement_row_id": _hash({"kind": "settlement", "prediction_row_id": pid, **identity_key, "settlement_outcome_id": settlement.get("settlement_outcome_id")}), "status": "linked", "missing_reason": None, "condition_id": condition_id, "token_id": token_id, "settlement": settlement}


def replay_markout_rows(
    predictions: Iterable[Mapping[str, Any]],
    identity_by_prediction: Mapping[str, Sequence[Mapping[str, Any]]],
    label_by_prediction: Mapping[str, Mapping[str, Any]],
    *,
    ws_runtime_root: Path | None,
    now_utc: datetime | None = None,
    existing_rows: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize exact-token checkpoints with the accepted Stage-2 replay.

    Raw WS is never promoted directly.  Each returned row has gone through the
    deterministic reconstructor and explicit valid/stale/gap/depth classifier.
    """
    if ws_runtime_root is None or not ws_runtime_root.is_dir():
        return [], {"status": "UNAVAILABLE", "reason": "WS_RUNTIME_ROOT_UNAVAILABLE"}
    from scripts.analysis.forecast_quality.research_wcir_stage02_stage03_rev2 import (
        CheckpointQuery,
        build_checkpoint_queries,
        load_subscription_epochs,
        replay_ws_archive,
        ts_ns,
    )

    events: list[dict[str, Any]] = []
    universes: list[dict[str, Any]] = []
    for prediction in predictions:
        pid = prediction_identity(prediction)
        label = label_by_prediction.get(pid)
        identities = list(identity_by_prediction.get(pid) or ())
        lineage = prediction.get("raw_source_lineage") or {}
        if not label or label.get("status") != "linked" or not identities:
            continue
        decision_at = lineage.get("available_at_utc")
        official_first_seen = label.get("official_first_seen_at_utc")
        if not decision_at or not official_first_seen:
            continue
        events.append({
            "event_id": pid,
            "source_detect_ts_utc": str(decision_at),
            "official_first_seen_at_utc": str(official_first_seen),
        })
        universes.append({
            "event_id": pid,
            "tokens": [
                {
                    "token_id": str(item["token_id"]),
                    "condition_id": str(item["condition_id"]),
                    "outcome": str(item["side"]).lower(),
                    "bracket": str(item["native_bracket"]),
                    "roles": ["previous_running_max_exact_token"],
                }
                for item in identities
            ],
        })
    if not events:
        return [], {"status": "NO_MATURE_EVENTS", "event_count": 0, "query_count": 0}
    queries = build_checkpoint_queries(events, universes, ENTRY_LATENCY_SECONDS)
    allowed = {
        "source_t0",
        "entry_after_p95_latency",
        *(f"official_plus_{seconds}s" for seconds in CHECKPOINTS if seconds),
    }
    queries = [query for query in queries if query.checkpoint in allowed]
    universe_by_event = {row["event_id"]: row["tokens"] for row in universes}
    for event in events:
        at_ns = ts_ns(event["official_first_seen_at_utc"])
        if at_ns is None:
            continue
        for token in universe_by_event[event["event_id"]]:
            queries.append(CheckpointQuery(
                at_ns=at_ns,
                event_id=event["event_id"],
                checkpoint="official_plus_0s",
                checkpoint_at_utc=event["official_first_seen_at_utc"],
                token_id=token["token_id"],
                condition_id=token["condition_id"],
                outcome=token["outcome"],
                bracket=token["bracket"],
                roles=tuple(token["roles"]),
            ))
    now = now_utc or datetime.now(UTC)
    maturity_ns = int(now.timestamp() * 1_000_000_000) - 5_000_000_000
    frozen_before_ns = int(now.timestamp() * 1_000_000_000) - 300_000_000_000
    completed = {
        (
            str(row.get("prediction_row_id") or row.get("event_id") or ""),
            str(row.get("token_id") or ""),
            str(row.get("checkpoint") or ""),
            str(row.get("checkpoint_at_utc") or ""),
        )
        for row in existing_rows
        if row.get("materializer_version") == "amsterdam_frozen_ws_checkpoint_v1"
    }
    queries = sorted(
        (
            query for query in queries
            if query.at_ns <= maturity_ns
            and not (
                query.at_ns <= frozen_before_ns
                and (
                    query.event_id,
                    query.token_id,
                    query.checkpoint,
                    query.checkpoint_at_utc,
                ) in completed
            )
        ),
        key=lambda query: (query.at_ns, query.event_id, query.token_id, query.checkpoint),
    )
    if not queries:
        return [], {"status": "NO_NEW_MATURE_CHECKPOINTS", "event_count": len(events), "query_count": 0}
    days = [datetime.fromtimestamp(query.at_ns / 1e9, tz=UTC).date().isoformat() for query in queries]
    query_start_date, end_date = min(days), max(days)
    start_date = (datetime.fromisoformat(query_start_date).date() - timedelta(days=1)).isoformat()
    epochs, epoch_files = load_subscription_epochs(
        ws_runtime_root,
        start_date=start_date,
        end_date=end_date,
    )
    replay, replay_summary, raw_lineage = replay_ws_archive(
        ws_runtime_root,
        epochs,
        queries,
        start_date=start_date,
        end_date=end_date,
    )
    rows: list[dict[str, Any]] = []
    for item in replay:
        body = {
            **item,
            "prediction_row_id": str(item["event_id"]),
            "materializer_version": "amsterdam_frozen_ws_checkpoint_v1",
            "evidence_tier": (
                "TIER_A_WS_DETERMINISTIC"
                if bool(item.get("book_valid"))
                else "TIER_A_WS_REPLAY_FAILURE"
            ),
            "orders": 0,
            "fills": 0,
            "notional": 0,
        }
        body["markout_row_id"] = _hash({
            "kind": "deterministic_ws_checkpoint",
            "prediction_row_id": body["prediction_row_id"],
            "condition_id": body.get("condition_id"),
            "token_id": body.get("token_id"),
            "checkpoint": body.get("checkpoint"),
            "checkpoint_at_utc": body.get("checkpoint_at_utc"),
            "primary_status": body.get("primary_status"),
            "book_snapshot_id": body.get("book_snapshot_id"),
        })
        rows.append(body)
    status_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("primary_status") or "UNKNOWN")
        status_counts[key] = status_counts.get(key, 0) + 1
    summary = {
        "status": "OK",
        "event_count": len(events),
        "query_count": len(queries),
        "row_count": len(rows),
        "query_start_date": query_start_date,
        "archive_start_date": start_date,
        "end_date": end_date,
        "primary_status_counts": status_counts,
        "subscription_epoch_file_count": len(epoch_files),
        "raw_lineage_row_count": len(raw_lineage),
        "replay_summary": replay_summary,
    }
    return rows, summary


def next_print_metrics(
    predictions: Mapping[str, Mapping[str, Any]],
    linked_labels: Mapping[str, Mapping[str, Any]],
    *,
    opportunity_only: bool = False,
) -> dict[str, Any]:
    support = list(range(-10, 11))
    roles = {"B2": "REFERENCE", "M1": "CHALLENGER", "M2": "CHALLENGER"}
    output: dict[str, Any] = {}
    for model, role in roles.items():
        scored: list[dict[str, Any]] = []
        for pid, label_row in linked_labels.items():
            prediction = predictions.get(pid)
            if prediction is None:
                continue
            if opportunity_only and "NOT_OPPORTUNITY_MATCHED" in (prediction.get("abstain_reasons") or ()):
                continue
            try:
                label = int(label_row["future_next_print_label"])
                pmf = [float(value) for value in prediction[f"{model}_PMF"]]
            except (KeyError, TypeError, ValueError):
                continue
            if label not in support or len(pmf) != len(support) or not math.isclose(sum(pmf), 1.0, abs_tol=1e-9):
                continue
            predicted = support[max(range(len(pmf)), key=pmf.__getitem__)]
            cumulative = 0.0
            squared = 0.0
            for tick, probability in zip(support, pmf):
                cumulative += probability
                observed_cdf = 1.0 if tick >= label else 0.0
                squared += (cumulative - observed_cdf) ** 2
            target_date = str((prediction.get("raw_source_lineage") or {}).get("target_date") or "")
            scored.append({
                "target_date": target_date,
                "exact": float(predicted == label),
                "rps": squared / (len(support) - 1),
                "logloss": -math.log(max(pmf[label - support[0]], 1e-12)),
            })
        if not scored:
            output[model] = {"role": role, "rows": 0, "target_dates": 0, "status": "NOT_ESTIMABLE"}
            continue
        raw = {
            name: sum(row[name] for row in scored) / len(scored)
            for name in ("exact", "rps", "logloss")
        }
        by_date: dict[str, list[dict[str, Any]]] = {}
        for row in scored:
            by_date.setdefault(row["target_date"], []).append(row)
        date_means = {
            date: {
                name: sum(row[name] for row in rows) / len(rows)
                for name in ("exact", "rps", "logloss")
            }
            for date, rows in by_date.items()
        }
        date_equal = {
            name: sum(row[name] for row in date_means.values()) / len(date_means)
            for name in ("exact", "rps", "logloss")
        }
        output[model] = {
            "role": role,
            "rows": len(scored),
            "target_dates": len(by_date),
            "status": "OK",
            "raw_row_weighted": {
                "exact_accuracy": raw["exact"],
                "rps": raw["rps"],
                "logloss": raw["logloss"],
            },
            "target_date_equal_weighted": {
                "exact_accuracy": date_equal["exact"],
                "rps": date_equal["rps"],
                "logloss": date_equal["logloss"],
            },
        }
    return output


class AmsterdamFrozenShadowRuntime:
    def __init__(
        self,
        root: Path,
        *,
        source_events_path: Path | None = None,
        settlement_db: Path | None = None,
        market_latest_path: Path | None = None,
        ws_runtime_root: Path | None = None,
    ) -> None:
        self.root = root
        self.source_events_path = source_events_path
        self.settlement_db = settlement_db
        self.market_latest_path = market_latest_path
        self.ws_runtime_root = ws_runtime_root
        self.predictions = ImmutableJsonl(root / "predictions.jsonl", "prediction_row_id")
        self.market_identities = ImmutableJsonl(root / "market_identities.jsonl", "market_identity_row_id")
        self.demands = ImmutableJsonl(root / "capture_demands.jsonl", "demand_id")
        self.labels = ImmutableJsonl(root / "labels.jsonl", "label_row_id")
        self.settlements = ImmutableJsonl(root / "settlements.jsonl", "settlement_row_id")
        self.markouts = ImmutableJsonl(root / "markouts.jsonl", "markout_row_id")

    def ingest_predictions(self, rows: Iterable[Mapping[str, Any]]) -> int:
        count = 0
        for item in rows:
            row = dict(item)
            row.setdefault("prediction_row_id", prediction_identity(row))
            if [row.get("orders"), row.get("fills"), row.get("notional")] != [0, 0, 0]:
                raise ValueError("frozen shadow must remain zero-notional")
            count += int(self.predictions.put(row))
        return count

    def materialize(self) -> dict[str, int]:
        linked_label_groups: dict[str, list[dict[str, Any]]] = {}
        for row in self.labels.rows.values():
            if row.get("status") == "linked":
                linked_label_groups.setdefault(str(row["prediction_row_id"]), []).append(row)
        conflicts = [key for key, rows in linked_label_groups.items() if len(rows) > 1]
        if conflicts:
            raise ValueError(f"multiple terminal labels for prediction: {conflicts[:5]}")
        existing_linked_labels = {key: rows[0] for key, rows in linked_label_groups.items()}
        target_dates = {
            str((row.get("raw_source_lineage") or {}).get("target_date") or "")
            for key, row in self.predictions.rows.items()
            if key not in existing_linked_labels
        }
        source_events = read_source_events(self.source_events_path, target_dates=target_dates)
        market_payload: Mapping[str, Any] = {}
        if self.market_latest_path and self.market_latest_path.is_file():
            loaded = json.loads(self.market_latest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                market_payload = loaded
        result = {"market_identities": 0, "demands": 0, "labels": 0, "settlements": 0, "markouts": 0, "ledger": 0}
        identity_by_prediction: dict[str, list[dict[str, Any]]] = {}
        linked_label_by_prediction: dict[str, dict[str, Any]] = {}
        for prediction in self.predictions.rows.values():
            identity_row = resolve_market_identities(prediction, market_payload)
            result["market_identities"] += int(self.market_identities.put(identity_row))
            identities = identity_row["identities"]
            identity_by_prediction[prediction_identity(prediction)] = list(identities)
            for demand in capture_demands_for_prediction(prediction, identities):
                result["demands"] += int(self.demands.put(demand))
            pid = prediction_identity(prediction)
            label = existing_linked_labels.get(pid) or label_for_prediction(prediction, source_events)
            if pid not in existing_linked_labels:
                result["labels"] += int(self.labels.put(label))
            if label.get("status") == "linked":
                linked_label_by_prediction[pid] = label
            for identity in identities:
                settlement = settlement_for_identity(prediction, identity, self.settlement_db)
                result["settlements"] += int(self.settlements.put(settlement))
                markout = {
                    "prediction_row_id": prediction_identity(prediction),
                    "condition_id": identity["condition_id"],
                    "token_id": identity["token_id"],
                    "markout_row_id": _hash({
                        "kind": "markout_pending",
                        "prediction_row_id": prediction_identity(prediction),
                        "condition_id": identity["condition_id"],
                        "token_id": identity["token_id"],
                    }),
                    "status": "pending",
                    "missing_reason": "CONTINUOUS_REPLAY_CHECKPOINT_MATERIALIZER_NOT_YET_AVAILABLE",
                    "checkpoints_seconds": list(CHECKPOINTS),
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                }
                result["markouts"] += int(self.markouts.put(markout))
        replay_rows, replay_summary = replay_markout_rows(
            self.predictions.rows.values(),
            identity_by_prediction,
            linked_label_by_prediction,
            ws_runtime_root=self.ws_runtime_root,
            existing_rows=self.markouts.rows.values(),
        )
        for row in replay_rows:
            result["markouts"] += int(self.markouts.put(row))
        replay_path = self.root / "markout_materializer_latest.json"
        _write_json_atomic(replay_path, replay_summary)
        # A joined view is a replaceable snapshot precisely because pending
        # evidence may later gain separate immutable terminal rows.
        snapshot = self.root / "common_ledger_snapshot.json"
        joined = [
            {
                "prediction_row_id": key,
                "prediction": value,
                "market_identities": [row for row in self.market_identities.rows.values() if row["prediction_row_id"] == key],
                "labels": [row for row in self.labels.rows.values() if row["prediction_row_id"] == key],
                "demands": [row for row in self.demands.rows.values() if row.get("metadata", {}).get("prediction_row_id") == key],
                "settlements": [row for row in self.settlements.rows.values() if row["prediction_row_id"] == key],
                "markouts": [row for row in self.markouts.rows.values() if row["prediction_row_id"] == key],
                "orders": 0,
                "fills": 0,
                "notional": 0,
            }
            for key, value in self.predictions.rows.items()
        ]
        _write_json_atomic(snapshot, joined, pretty=False)
        result["ledger"] = len(joined)
        return result

    def health(self, *, epoch: str | None = None, frozen_hashes: Mapping[str, Any] | None = None) -> dict[str, Any]:
        total = len(self.predictions.rows)
        labels = list(self.labels.rows.values())
        settlements = list(self.settlements.rows.values())
        markouts = list(self.markouts.rows.values())
        linked_label_rows = {
            str(row["prediction_row_id"]): row
            for row in labels if row.get("status") == "linked"
        }
        linked_label_predictions = set(linked_label_rows)
        label_distribution: dict[str, int] = {}
        for row in linked_label_rows.values():
            key = str(row.get("future_next_print_label"))
            label_distribution[key] = label_distribution.get(key, 0) + 1
        label_dates = sorted({
            str((self.predictions.rows[key].get("raw_source_lineage") or {}).get("target_date") or "")
            for key in linked_label_predictions
            if key in self.predictions.rows
        })
        model_metrics = next_print_metrics(self.predictions.rows, linked_label_rows)
        opportunity_model_metrics = next_print_metrics(
            self.predictions.rows,
            linked_label_rows,
            opportunity_only=True,
        )
        identity_rows = list(self.market_identities.rows.values())
        exact_tokens = sum(len(row.get("identities") or ()) for row in identity_rows if row.get("status") == "resolved")
        linked_settlement_tokens = {
            (row["prediction_row_id"], row.get("condition_id"), row.get("token_id"))
            for row in settlements if row.get("status") == "linked"
        }
        terminal_markouts = [row for row in markouts if row.get("materializer_version") == "amsterdam_frozen_ws_checkpoint_v1"]
        valid_markouts = [row for row in terminal_markouts if row.get("book_valid") is True]
        valid_official_30 = [row for row in valid_markouts if row.get("checkpoint") == "official_plus_30s"]
        executable_official_30 = [
            row for row in valid_official_30
            if row.get("primary_status") == "VALID_TWO_SIDED_DEPTH"
        ]
        executable_keys = {
            (row["prediction_row_id"], row.get("condition_id"), row.get("token_id"))
            for row in executable_official_30
        }
        entry_keys = {
            (row["prediction_row_id"], row.get("condition_id"), row.get("token_id"))
            for row in terminal_markouts
            if row.get("checkpoint") == "entry_after_p95_latency"
            and row.get("primary_status") == "VALID_TWO_SIDED_DEPTH"
        }
        layer_b_keys = executable_keys & entry_keys
        target_date_by_prediction = {
            key: str((row.get("raw_source_lineage") or {}).get("target_date") or "")
            for key, row in self.predictions.rows.items()
        }
        layer_b_dates = sorted({target_date_by_prediction[key[0]] for key in layer_b_keys if target_date_by_prediction.get(key[0])})
        opportunity_predictions = {
            key for key, row in self.predictions.rows.items()
            if "NOT_OPPORTUNITY_MATCHED" not in (row.get("abstain_reasons") or ())
        }
        if len(layer_b_dates) < 2:
            layer_b_status = "NOT_ESTIMABLE"
            layer_b_reason = "fewer_than_two_nonoverlapping_target_dates"
        elif len(layer_b_keys) <= 3:
            layer_b_status = "NOT_ESTIMABLE"
            layer_b_reason = "insufficient_rows_for_fixed_design_rank"
        else:
            layer_b_status = "READY_FOR_FROZEN_NONOVERLAPPING_FIT"
            layer_b_reason = None
        return {"status": "ok", "execution_mode": "zero_notional_shadow", "live_authority": False,
                "strategy_key": STRATEGY_KEY, "generated_at_utc": _now(), "forward_epoch_id": epoch,
                "frozen_hashes": dict(frozen_hashes or {}), "prediction_count": total,
                "label_linked": len(linked_label_predictions), "label_coverage": (len(linked_label_predictions) / total if total else 0.0),
                "label_target_dates": label_dates,
                "label_distribution": label_distribution,
                "next_print_model_metrics": model_metrics,
                "opportunity_next_print_model_metrics": opportunity_model_metrics,
                "market_identity_prediction_count": sum(row.get("status") == "resolved" for row in identity_rows),
                "exact_token_identity_count": exact_tokens,
                "demand_count": len(self.demands.rows), "settlement_linked": len(linked_settlement_tokens),
                "settlement_coverage": (len(linked_settlement_tokens) / exact_tokens if exact_tokens else 0.0),
                "markout_terminal_count": len(terminal_markouts),
                "markout_valid": len(valid_markouts),
                "markout_official_plus_30_book_valid": len(valid_official_30),
                "markout_official_plus_30_two_sided_depth": len(executable_official_30),
                "markout_missing_reason": (None if terminal_markouts else "NO_MATURE_DETERMINISTIC_WS_CHECKPOINTS"),
                "entry_latency_seconds": ENTRY_LATENCY_SECONDS,
                "entry_latency_contract_id": ENTRY_LATENCY_CONTRACT_ID,
                "opportunity_prediction_count": len(opportunity_predictions),
                "layer_b_pairwise_entry_markout_rows": len(layer_b_keys),
                "layer_b_target_dates": layer_b_dates,
                "layer_b_status": layer_b_status,
                "layer_b_reason": layer_b_reason,
                "orders": 0, "fills": 0, "notional": 0, "actual_notional": 0.0}

    def write_health(self, *, epoch: str | None = None, frozen_hashes: Mapping[str, Any] | None = None) -> dict[str, Any]:
        health = self.health(epoch=epoch, frozen_hashes=frozen_hashes)
        path = self.root / "latest.json"
        _write_json_atomic(path, health)
        return health
