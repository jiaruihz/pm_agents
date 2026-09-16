#!/usr/bin/env python3
"""WCIR Stage 2/3 rev2 archive-wide evidence closure.

This runner is deliberately research-only.  It reads frozen Stage 3 events and
raw immutable archives, but never writes to the runtime root, canonical DB,
collector configuration, selector, order path, or production configuration.

The main contract is intentionally strict:

* every requested event/book checkpoint receives one primary status;
* reconstructability (``book_valid``) is separate from executable feasibility;
* REST establishes identity and independent parity only; it never repairs WS;
* the primary oracle fixes 5 shares and official+30s and chooses an action
  without seeing any future price;
* all large raw inputs stay outside the review package and are frozen by hash.
"""

from __future__ import annotations

import argparse
import bisect
import collections
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
import statistics
import sys
from types import SimpleNamespace
from typing import Any, BinaryIO, Iterable, Iterator, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.market_data.executable_book_truth import (  # noqa: E402
    ExecutableBookTruth,
    executable_book_truth,
)
from src.platform.market_data.ws_incremental_book import (  # noqa: E402
    BookReconstructionError,
    IncrementalBookReconstructor,
    ReconstructedBook,
    canonical_ws_frame_id,
    compare_rest_ws_parity,
)
from weather_data_feed.market_brackets import bracket_center  # noqa: E402


CITIES = ("Amsterdam", "Helsinki", "Tokyo", "Seoul", "Busan")
ARCHIVE_START = "2026-08-08"
EVENT_START = "2026-08-09"
CUTOFF_DATE = "2026-08-26"
MAX_BOOK_AGE_SECONDS = 120.0
PRIMARY_SHARES = 5.0
PRIMARY_HORIZON_SECONDS = 30
SECONDARY_HORIZONS = (1, 3, 5, 15, 60, 120, 300)
SWEEP_SHARES = (1.0, 5.0, 10.0)
FAILURE_REASONS = {
    "ARCHIVE_MISSING",
    "CLOCK_UNCERTAINTY",
    "IDENTITY_MISMATCH",
    "NO_BASELINE",
    "OPEN_GAP",
    "STALE_BOOK",
    "VALID_BUT_ONE_SIDED",
    "VALID_BUT_INSUFFICIENT_DEPTH",
    "VALID_TWO_SIDED_DEPTH",
}


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def ts_ns(value: Any) -> int | None:
    parsed = parse_ts(value)
    return int(parsed.timestamp() * 1_000_000_000) if parsed else None


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path, *, row_count: int | None = None) -> dict[str, Any]:
    identity = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if row_count is not None:
        identity["row_count"] = row_count
    return identity


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"non-object JSONL {path}:{line_number}")
            yield row


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            for row in rows:
                compressed.write(canonical_json(row) + b"\n")
                count += 1
    return count


def percentile(values: Sequence[float], q: float) -> float | None:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0,1]")
    return finite[math.ceil((len(finite) - 1) * q)]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_frozen_events(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise RuntimeError("frozen Stage 3 event input is empty")
    ids = [str(row.get("event_id") or "") for row in rows]
    if any(not event_id for event_id in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("frozen events require unique non-empty event_id")
    for row in rows:
        if row.get("city") not in CITIES:
            raise RuntimeError(f"unexpected city in frozen input: {row.get('city')}")
        if not EVENT_START <= str(row.get("target_date")) <= CUTOFF_DATE:
            raise RuntimeError("frozen event outside declared date boundary")
    return sorted(rows, key=lambda row: (str(row["source_detect_ts_utc"]), row["event_id"]))


def freeze_latency_inputs(
    fast_path: Path, events: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Freeze the first matching raw serialization and a conservative p95."""

    wanted = {str(row["event_key"]): str(row["event_id"]) for row in events}
    found: dict[str, dict[str, Any]] = {}
    duplicate_serializations = collections.Counter()
    serialization_audit: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    immutable_fields = (
        "event_key",
        "city",
        "target_date",
        "source",
        "source_kind",
        "station",
        "source_obs_ts_utc",
        "source_detect_ts_utc",
        "source_temp_c",
        "source_round_c",
        "latest_metar_report_ts_utc",
        "latest_metar_round_c",
        "metar_running_max_round_c",
        "t_minus_1_no_bracket_c",
        "market_id",
        "condition_id",
        "token_id",
    )
    prefix_digest = hashlib.sha256()
    prefix_sha256: str | None = None
    prefix_bytes = 0
    prefix_line_count = 0
    consumed_bytes = 0
    with fast_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            prefix_digest.update(raw_line)
            consumed_bytes += len(raw_line)
            raw = json.loads(raw_line)
            event_key = str(raw.get("event_key") or "")
            if event_key not in wanted:
                continue
            duplicate_serializations[event_key] += 1
            immutable_payload = {field: raw.get(field) for field in immutable_fields}
            serialization_audit[event_key].append(
                {
                    "serialization_order": duplicate_serializations[event_key],
                    "source_line_number": line_number,
                    "full_payload_sha256": canonical_hash(raw),
                    "immutable_field_sha256": canonical_hash(immutable_payload),
                    "selected_state": {
                        "status": raw.get("status"),
                        "planned_shares": raw.get("planned_shares"),
                        "planned_notional_usd": raw.get("planned_notional_usd"),
                    },
                }
            )
            if event_key in found:
                continue
            detect_to_runner = _finite(raw.get("source_detect_to_runner_sec"))
            fresh_fetch = _finite(raw.get("fresh_book_fetch_duration_sec"))
            total = None
            if detect_to_runner is not None and detect_to_runner >= 0:
                total = detect_to_runner + max(0.0, fresh_fetch or 0.0)
            found[event_key] = {
                "event_id": wanted[event_key],
                "event_key": event_key,
                "raw_path": str(fast_path),
                "raw_line_number": line_number,
                "raw_payload_sha256": canonical_hash(raw),
                "source_detect_to_runner_sec": detect_to_runner,
                "fresh_book_fetch_duration_sec": fresh_fetch,
                "observed_pipeline_latency_sec": total,
            }
            prefix_sha256 = prefix_digest.copy().hexdigest()
            prefix_bytes = consumed_bytes
            prefix_line_count = line_number
    rows = [found.get(str(event["event_key"])) for event in events]
    frozen = [row for row in rows if row is not None]
    values = [
        float(row["observed_pipeline_latency_sec"])
        for row in frozen
        if row["observed_pipeline_latency_sec"] is not None
    ]
    p95 = percentile(values, 0.95)
    if p95 is None:
        raise RuntimeError("no observed pipeline latency is available")
    duplicates = []
    drifted = []
    for event_key, serializations in sorted(serialization_audit.items()):
        if len(serializations) < 2:
            continue
        hashes = sorted({row["immutable_field_sha256"] for row in serializations})
        payload_drift = len(hashes) != 1
        row = {
            "event_id": wanted[event_key],
            "event_key": event_key,
            "serialization_count": len(serializations),
            "immutable_field_hashes": hashes,
            "payload_drift": payload_drift,
            "selected_state_transition": [row["selected_state"] for row in serializations],
            "serializations": serializations,
            "disposition": (
                "FAIL_IMMUTABLE_EVENT_PAYLOAD_DRIFT"
                if payload_drift
                else "ACCEPT_IDENTICAL_IMMUTABLE_EVENT_SERIALIZATION"
            ),
        }
        duplicates.append(row)
        if payload_drift:
            drifted.append(event_key)
    duplicate_audit = {
        "audit_scope": "frozen Stage 3 event keys in fast-source journal",
        "immutable_fields": list(immutable_fields),
        "matched_event_key_count": len(serialization_audit),
        "duplicate_event_key_count": len(duplicates),
        "duplicate_extra_serialization_count": sum(
            max(0, count - 1) for count in duplicate_serializations.values()
        ),
        "payload_drift_count": len(drifted),
        "overall_disposition": "PASS" if not drifted else "FAIL_CLOSED",
        "duplicates": duplicates,
    }
    if drifted:
        raise RuntimeError(
            f"immutable duplicate event payload drift: {drifted[:10]}"
        )
    return frozen, {
        "contract_id": canonical_hash(
            {
                "method": "global_empirical_nearest_rank_p95",
                "field": "source_detect_to_runner_sec + max(0, fresh_book_fetch_duration_sec)",
                "value_seconds": p95,
                "event_ids": sorted(row["event_id"] for row in frozen if row["observed_pipeline_latency_sec"] is not None),
            }
        ),
        "method": "global_empirical_nearest_rank_p95",
        "value_seconds": p95,
        "raw_event_count": len(events),
        "matched_event_count": len(frozen),
        "latency_observation_count": len(values),
        "missing_latency_count": len(events) - len(values),
        "p50_seconds": percentile(values, 0.50),
        "p90_seconds": percentile(values, 0.90),
        "p95_seconds": p95,
        "p99_seconds": percentile(values, 0.99),
        "duplicate_serialization_count": sum(max(0, count - 1) for count in duplicate_serializations.values()),
        "raw_journal_observed_identity_not_reproduction_boundary": file_identity(fast_path),
        "journal_prefix_line_count": prefix_line_count,
        "journal_prefix_bytes": prefix_bytes,
        "journal_prefix_sha256": prefix_sha256,
        "reproduction_boundary": "frozen compact latency rows plus verified append-only journal prefix",
    }, duplicate_audit


def load_official_history(runtime: Path) -> dict[str, list[dict[str, Any]]]:
    rows_by_city: dict[str, dict[tuple[str, str], dict[str, Any]]] = collections.defaultdict(dict)
    root = runtime / "output/source_events"
    for path in sorted(root.glob("2026-*/sources.jsonl")):
        if not "2026-08-07" <= path.parent.name <= CUTOFF_DATE:
            continue
        for line_number, raw in enumerate(read_jsonl(path), 1):
            city = str(raw.get("city") or "")
            if city not in CITIES or raw.get("source") != "aviationweather_metar":
                continue
            report = parse_ts(raw.get("source_report_ts_utc"))
            seen = parse_ts(raw.get("first_seen_at_utc") or raw.get("local_detect_ts_utc"))
            value = _finite(raw.get("temp_c"))
            if report is None or seen is None or value is None:
                continue
            key = (report.isoformat(), seen.isoformat())
            rows_by_city[city][key] = {
                "official_print_id": str(raw.get("information_event_id") or canonical_hash({"city": city, "report": iso(report), "seen": iso(seen), "value": value})),
                "report_at_utc": iso(report),
                "first_seen_at_utc": iso(seen),
                "temp_c": value,
                "round_c": math.floor(value + 0.5),
                "raw_path": str(path),
                "raw_line_number": line_number,
                "raw_payload_sha256": canonical_hash(raw),
            }
    output: dict[str, list[dict[str, Any]]] = {}
    for city, mapping in rows_by_city.items():
        output[city] = sorted(mapping.values(), key=lambda row: (row["report_at_utc"], row["first_seen_at_utc"]))
    return output


def recent_slope_for_event(
    event: Mapping[str, Any], official: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    decision = parse_ts(event.get("source_detect_ts_utc"))
    eligible = [
        row
        for row in official.get(str(event.get("city")), ())
        if decision and parse_ts(row.get("first_seen_at_utc")) and parse_ts(row["first_seen_at_utc"]) <= decision
        and str(row.get("report_at_utc", "")) <= str(event.get("latest_metar_report_ts_utc", ""))
    ]
    if len(eligible) < 2:
        return {"status": "unavailable", "reason": "fewer_than_two_prior_official_prints"}
    previous, latest = eligible[-2:]
    predicted = int(latest["round_c"]) + (int(latest["round_c"]) - int(previous["round_c"]))
    result = {
        "status": "available",
        "predicted_next_round_c": predicted,
        "previous_official_print_id": previous["official_print_id"],
        "latest_official_print_id": latest["official_print_id"],
        "previous_round_c": previous["round_c"],
        "latest_round_c": latest["round_c"],
    }
    return result


def freeze_forecasts(
    runtime: Path, events: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join the latest forecast curve actually available by decision time."""

    event_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = collections.defaultdict(list)
    for event in events:
        event_by_key[(str(event["city"]), str(event["target_date"]))].append(event)
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    source_files: list[dict[str, Any]] = []
    root = runtime / "forecast/forecast_hourly_curves"
    for day in sorted(root.glob("2026-08-*")):
        if not ARCHIVE_START <= day.name <= CUTOFF_DATE:
            continue
        for path in sorted(day.glob("forecast_hourly_curves_*.jsonl")):
            count = 0
            for line_number, raw in enumerate(read_jsonl(path), 1):
                count += 1
                key = (str(raw.get("city") or ""), str(raw.get("target_date") or ""))
                if key not in event_by_key:
                    continue
                available = parse_ts(raw.get("available_at_utc"))
                peak_f = _finite(raw.get("forecast_max_f"))
                if available is None or peak_f is None:
                    continue
                candidates[key].append(
                    {
                        "available_at_utc": iso(available),
                        "forecast_max_f": peak_f,
                        "forecast_max_c": (peak_f - 32.0) * 5.0 / 9.0,
                        "forecast_source": raw.get("forecast_source"),
                        "forecast_model": raw.get("forecast_model"),
                        "forecast_assigned_model": raw.get("forecast_assigned_model"),
                        "forecast_run_ts_utc": raw.get("forecast_run_ts_utc"),
                        "forecast_run_lineage_status": raw.get("forecast_run_lineage_status"),
                        "forecast_values_hash": raw.get("forecast_values_hash"),
                        "capture_id": raw.get("capture_id"),
                        "batch_capture_id": raw.get("batch_capture_id"),
                        "raw_path": str(path),
                        "raw_line_number": line_number,
                        "raw_payload_sha256": canonical_hash(raw),
                    }
                )
            source_files.append(file_identity(path, row_count=count))
    output: list[dict[str, Any]] = []
    for event in events:
        decision = parse_ts(event["source_detect_ts_utc"])
        rows = [
            row
            for row in candidates.get((str(event["city"]), str(event["target_date"])), ())
            if decision and parse_ts(row["available_at_utc"]) <= decision
        ]
        chosen = max(rows, key=lambda row: (row["available_at_utc"], str(row["capture_id"]))) if rows else None
        output.append(
            {
                "event_id": event["event_id"],
                "status": "available" if chosen else "unavailable",
                "reason": None if chosen else "no_forecast_available_before_decision",
                **(chosen or {}),
                "predicted_next_round_c": (
                    math.floor(float(chosen["forecast_max_c"]) + 0.5) if chosen else None
                ),
            }
        )
    return output, {
        "source_file_count": len(source_files),
        "source_files": source_files,
        "event_count": len(events),
        "available_event_count": sum(row["status"] == "available" for row in output),
        "identity_contract": "latest forecast curve with available_at_utc <= source decision clock; exact model/capture/value hashes frozen",
    }


@dataclass(frozen=True)
class MarketToken:
    city: str
    target_date: str
    event_id: str
    market_id: str
    condition_id: str
    bracket: str
    bracket_order: float
    outcome: str
    token_id: str
    identity_source_path: str
    identity_source_line: int
    identity_payload_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_rest_market_identity(
    runtime: Path, events: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Resolve exact/complement/neighbor tokens without treating REST as WS."""

    wanted_city_dates = {(str(row["city"]), str(row["target_date"])) for row in events}
    exact_conditions = {str(row["condition_id"]) for row in events}
    token_map: dict[tuple[str, str], MarketToken] = {}
    condition_to_event: dict[str, str] = {}
    event_conditions: dict[str, dict[str, dict[str, MarketToken]]] = collections.defaultdict(
        lambda: collections.defaultdict(dict)
    )
    identity_drift: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    root = runtime / "market_books/batches"
    for day in sorted(root.glob("2026-08-*")):
        if not ARCHIVE_START <= day.name <= CUTOFF_DATE:
            continue
        for path in sorted(day.glob("*.jsonl.gz")):
            count = 0
            for line_number, raw in enumerate(read_jsonl(path), 1):
                count += 1
                city_date = (str(raw.get("city") or ""), str(raw.get("event_date") or ""))
                if city_date not in wanted_city_dates or raw.get("status") != "ok":
                    continue
                token_id = str(raw.get("token_id") or "")
                condition_id = str(raw.get("condition_id") or "")
                outcome = str(raw.get("outcome") or "").lower()
                event_id = str(raw.get("event_id") or "")
                if not token_id or not condition_id or outcome not in {"yes", "no"} or not event_id:
                    continue
                bracket = str(raw.get("bracket") or "")
                order = bracket_center(bracket)
                if not math.isfinite(order):
                    continue
                candidate = MarketToken(
                    city=city_date[0],
                    target_date=city_date[1],
                    event_id=event_id,
                    market_id=str(raw.get("market_id") or ""),
                    condition_id=condition_id,
                    bracket=bracket,
                    bracket_order=order,
                    outcome=outcome,
                    token_id=token_id,
                    identity_source_path=str(path),
                    identity_source_line=line_number,
                    identity_payload_sha256=canonical_hash(
                        {key: raw.get(key) for key in ("city", "event_date", "event_id", "market_id", "condition_id", "bracket", "outcome", "token_id")}
                    ),
                )
                key = (condition_id, outcome)
                existing = token_map.get(key)
                if existing and existing.token_id != candidate.token_id:
                    identity_drift.append({"key": key, "first": existing.to_dict(), "later": candidate.to_dict()})
                    continue
                token_map.setdefault(key, candidate)
                condition_to_event.setdefault(condition_id, event_id)
                event_conditions[event_id][condition_id][outcome] = token_map[key]
            source_files.append(file_identity(path, row_count=count))
    if identity_drift:
        raise RuntimeError(f"REST market identity drift: {len(identity_drift)} rows")

    event_universe: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for event in events:
        condition = str(event["condition_id"])
        event_id = condition_to_event.get(condition)
        if not event_id:
            unresolved.append({"event_id": event["event_id"], "reason": "exact_condition_absent_from_rest"})
            event_universe.append({"event_id": event["event_id"], "tokens": []})
            continue
        ladders = event_conditions[event_id]
        ordered_conditions = sorted(
            ladders,
            key=lambda cond: (
                next(iter(ladders[cond].values())).bracket_order,
                cond,
            ),
        )
        exact_index = ordered_conditions.index(condition) if condition in ordered_conditions else None
        actual_value = float(event["official_round_c"])
        actual_index = min(
            range(len(ordered_conditions)),
            key=lambda index: (
                abs(next(iter(ladders[ordered_conditions[index]].values())).bracket_order - actual_value),
                index,
            ),
        )
        selected_indices: set[int] = {actual_index}
        if exact_index is not None:
            selected_indices.add(exact_index)
        for center in tuple(selected_indices):
            selected_indices.update(index for index in (center - 1, center + 1) if 0 <= index < len(ordered_conditions))
        tokens: list[dict[str, Any]] = []
        for index in sorted(selected_indices):
            selected_condition = ordered_conditions[index]
            roles = []
            if index == exact_index:
                roles.append("prior_exact_bracket")
            if index == actual_index:
                roles.append("actual_next_print_bracket")
            if not roles:
                roles.append("neighbor_settlement_relevant")
            outcomes = ladders[selected_condition]
            if set(outcomes) != {"yes", "no"}:
                unresolved.append(
                    {
                        "event_id": event["event_id"],
                        "condition_id": selected_condition,
                        "reason": "complementary_outcome_identity_missing",
                    }
                )
            for outcome in ("yes", "no"):
                token = outcomes.get(outcome)
                if token:
                    tokens.append({**token.to_dict(), "roles": roles, "ladder_index": index})
        event_universe.append(
            {
                "event_id": event["event_id"],
                "polymarket_event_id": event_id,
                "exact_condition_id": condition,
                "actual_print_condition_id": ordered_conditions[actual_index],
                "actual_print_ladder_index": actual_index,
                "tokens": sorted(tokens, key=lambda row: (row["ladder_index"], row["outcome"])),
            }
        )
    identities = [token.to_dict() for token in sorted(token_map.values(), key=lambda row: (row.city, row.target_date, row.event_id, row.bracket_order, row.outcome))]
    exact_complements = sum(
        set(event_conditions.get(condition_to_event.get(condition, ""), {}).get(condition, {})) == {"yes", "no"}
        for condition in exact_conditions
    )
    return event_universe, identities, {
        "source_file_count": len(source_files),
        "source_files": source_files,
        "unique_token_identities": len(identities),
        "unique_exact_conditions": len(exact_conditions),
        "exact_conditions_with_yes_no_complements": exact_complements,
        "event_universe_rows": len(event_universe),
        "unresolved_rows": unresolved,
        "rest_role": "identity_and_independent_parity_only_never_ws_gap_repair",
    }


def checkpoint_times(event: Mapping[str, Any], latency_seconds: float) -> dict[str, str]:
    source = parse_ts(event.get("source_detect_ts_utc"))
    official = parse_ts(event.get("official_first_seen_at_utc"))
    if source is None:
        return {}
    output: dict[str, datetime] = {
        "source_minus_300s_placebo": source - timedelta(seconds=300),
        "pre_source": source - timedelta(seconds=1),
        "source_t0": source,
        "entry_after_p95_latency": source + timedelta(seconds=latency_seconds),
    }
    if official:
        output["pre_official"] = official - timedelta(seconds=1)
        for seconds in (PRIMARY_HORIZON_SECONDS, *SECONDARY_HORIZONS):
            output[f"official_plus_{seconds}s"] = official + timedelta(seconds=seconds)
    return {name: iso(at) for name, at in output.items() if at is not None}


@dataclass(frozen=True)
class CheckpointQuery:
    at_ns: int
    event_id: str
    checkpoint: str
    checkpoint_at_utc: str
    token_id: str
    condition_id: str
    outcome: str
    bracket: str
    roles: tuple[str, ...]


def build_checkpoint_queries(
    events: Sequence[Mapping[str, Any]],
    universes: Sequence[Mapping[str, Any]],
    latency_seconds: float,
) -> list[CheckpointQuery]:
    universe_by_event = {str(row["event_id"]): row for row in universes}
    output: list[CheckpointQuery] = []
    for event in events:
        times = checkpoint_times(event, latency_seconds)
        for token in universe_by_event.get(str(event["event_id"]), {}).get("tokens", ()):
            for checkpoint, at in times.items():
                at_value = ts_ns(at)
                if at_value is None:
                    continue
                output.append(
                    CheckpointQuery(
                        at_ns=at_value,
                        event_id=str(event["event_id"]),
                        checkpoint=checkpoint,
                        checkpoint_at_utc=at,
                        token_id=str(token["token_id"]),
                        condition_id=str(token["condition_id"]),
                        outcome=str(token["outcome"]),
                        bracket=str(token["bracket"]),
                        roles=tuple(token["roles"]),
                    )
                )
    return sorted(output, key=lambda row: (row.at_ns, row.event_id, row.token_id, row.checkpoint))


@dataclass
class TokenState:
    at_ns: int
    subscription_epoch_id: str | None
    condition_id: str | None
    status: str
    blocker_reason: str | None
    snapshot: ReconstructedBook | None
    truth: ExecutableBookTruth | None


class HashedJsonlReader:
    """Line iterator that freezes the exact bytes while the archive is replayed."""

    def __init__(self, path: Path) -> None:
        if path.suffix == ".gz":
            raise ValueError("WS hashed streaming currently requires plain JSONL")
        self.path = path
        self.handle: BinaryIO = path.open("rb")
        self.digest = hashlib.sha256()
        self.line_number = 0
        self.first_received_at_utc: str | None = None
        self.last_received_at_utc: str | None = None
        self.clock_regressions = 0
        self._last_ns: int | None = None
        self.closed = False

    def next(self) -> dict[str, Any] | None:
        raw_line = self.handle.readline()
        if not raw_line:
            self.close()
            return None
        self.digest.update(raw_line)
        self.line_number += 1
        row = json.loads(raw_line)
        if not isinstance(row, dict):
            raise RuntimeError(f"non-object frame {self.path}:{self.line_number}")
        received = str(row.get("received_at_utc") or "")
        received_ns = row.get("received_at_ns")
        order = int(received_ns) if received_ns is not None else ts_ns(received)
        if order is None:
            raise RuntimeError(f"frame lacks valid receive clock {self.path}:{self.line_number}")
        if self._last_ns is not None and order < self._last_ns:
            self.clock_regressions += 1
            row["_within_file_receive_clock_regression"] = True
        self._last_ns = order
        self.first_received_at_utc = self.first_received_at_utc or received
        self.last_received_at_utc = received
        row["_raw_path"] = str(self.path)
        row["_line_number"] = self.line_number
        row["_receive_order_ns"] = order
        return row

    def close(self) -> None:
        if not self.closed:
            self.handle.close()
            self.closed = True

    def identity(self) -> dict[str, Any]:
        if not self.closed:
            raise RuntimeError("reader identity requested before EOF")
        return {
            "path": str(self.path),
            "size_bytes": self.path.stat().st_size,
            "sha256": self.digest.hexdigest(),
            "row_count": self.line_number,
            "first_received_at_utc": self.first_received_at_utc,
            "last_received_at_utc": self.last_received_at_utc,
            "within_file_receive_clock_regressions": self.clock_regressions,
        }


def load_subscription_epochs(
    runtime: Path,
    *,
    start_date: str = ARCHIVE_START,
    end_date: str = CUTOFF_DATE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    epochs: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    root = runtime / "market_books/ws_incremental/subscription_epochs"
    for date in _date_strings(start_date, end_date):
        path = root / f"subscription_epochs_{date}.jsonl"
        if not path.exists():
            continue
        rows = list(read_jsonl(path))
        identities.append(file_identity(path, row_count=len(rows)))
        for row in rows:
            started = ts_ns(row.get("started_at_utc"))
            epoch_id = str(row.get("subscription_epoch_id") or "")
            if started is None or not epoch_id:
                raise RuntimeError(f"invalid subscription epoch in {path}")
            epochs.append({**row, "_started_ns": started, "_source_path": str(path)})
    by_id = collections.Counter(str(row["subscription_epoch_id"]) for row in epochs)
    duplicates = [key for key, count in by_id.items() if count > 1]
    if duplicates:
        raise RuntimeError(f"duplicate subscription epochs: {duplicates[:10]}")
    return sorted(epochs, key=lambda row: (row["_started_ns"], row["subscription_epoch_id"])), identities


def _date_strings(start: str, end: str) -> list[str]:
    current = datetime.fromisoformat(start).date()
    stop = datetime.fromisoformat(end).date()
    output = []
    while current <= stop:
        output.append(current.isoformat())
        current += timedelta(days=1)
    return output


def _chain_ids(epochs: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    by_id = {str(row["subscription_epoch_id"]): row for row in epochs}
    memo: dict[str, str] = {}

    def resolve(epoch_id: str, visiting: set[str]) -> str:
        if epoch_id in memo:
            return memo[epoch_id]
        if epoch_id in visiting:
            raise RuntimeError("cycle in subscription predecessor graph")
        visiting.add(epoch_id)
        previous = str(by_id[epoch_id].get("previous_subscription_epoch_id") or "")
        root = resolve(previous, visiting) if previous in by_id else epoch_id
        visiting.remove(epoch_id)
        memo[epoch_id] = root
        return root

    for epoch_id in by_id:
        resolve(epoch_id, set())
    return memo


def _message_tokens(message: Any) -> set[str]:
    messages = message if isinstance(message, list) else [message]
    output: set[str] = set()
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        token = row.get("asset_id") or row.get("assetId") or row.get("token_id")
        if token:
            output.add(str(token))
        for change in row.get("price_changes") or ():
            if isinstance(change, Mapping) and change.get("asset_id"):
                output.add(str(change["asset_id"]))
    return output


def _sweep_payload(truth: ExecutableBookTruth | None) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for shares in SWEEP_SHARES:
        for side in ("buy", "sell"):
            sweep = (
                next(
                    (
                        row
                        for row in truth.sweeps
                        if row.shares == shares and row.side == side
                    ),
                    None,
                )
                if truth
                else None
            )
            output[f"{side}_{int(shares)}"] = sweep.to_dict() if sweep else None
    return output


def classify_checkpoint(
    query: CheckpointQuery,
    state: TokenState | None,
    *,
    ever_subscribed: bool,
    expected_condition: str,
) -> dict[str, Any]:
    """Return exactly one primary status and orthogonal feasibility fields."""

    reason: str
    age_seconds: float | None = None
    if not ever_subscribed:
        reason = "ARCHIVE_MISSING"
    elif state is None:
        reason = "NO_BASELINE"
    elif state.status == "clock_uncertainty":
        reason = "CLOCK_UNCERTAINTY"
    elif state.condition_id != expected_condition:
        reason = "IDENTITY_MISMATCH"
    elif state.status == "open_gap":
        reason = "OPEN_GAP"
    elif state.snapshot is None or state.truth is None:
        reason = "NO_BASELINE"
    else:
        last = ts_ns(state.snapshot.last_frame_received_at_utc)
        if last is None or last > query.at_ns:
            reason = "CLOCK_UNCERTAINTY"
        else:
            age_seconds = (query.at_ns - last) / 1_000_000_000
            if age_seconds > MAX_BOOK_AGE_SECONDS:
                reason = "STALE_BOOK"
            else:
                sweeps = _sweep_payload(state.truth)
                buy_available = state.snapshot.best_ask is not None
                sell_available = state.snapshot.best_bid is not None
                if not buy_available or not sell_available:
                    reason = "VALID_BUT_ONE_SIDED"
                elif not (
                    sweeps["buy_5"] and sweeps["buy_5"]["fully_executable"]
                    and sweeps["sell_5"] and sweeps["sell_5"]["fully_executable"]
                ):
                    reason = "VALID_BUT_INSUFFICIENT_DEPTH"
                else:
                    reason = "VALID_TWO_SIDED_DEPTH"
    if reason not in FAILURE_REASONS:
        raise RuntimeError(f"unknown primary status: {reason}")
    valid = reason.startswith("VALID_")
    truth = state.truth if valid and state else None
    snapshot = state.snapshot if valid and state else None
    sweeps = _sweep_payload(truth)
    side_available = {
        "buy": bool(snapshot and snapshot.best_ask is not None),
        "sell": bool(snapshot and snapshot.best_bid is not None),
    }
    result = {
        "event_id": query.event_id,
        "checkpoint": query.checkpoint,
        "checkpoint_at_utc": query.checkpoint_at_utc,
        "token_id": query.token_id,
        "condition_id": query.condition_id,
        "outcome": query.outcome,
        "bracket": query.bracket,
        "roles": list(query.roles),
        "primary_status": reason,
        "book_valid": valid,
        "reconstruction_invalid": not valid,
        "execution_infeasible": valid and reason != "VALID_TWO_SIDED_DEPTH",
        "side_available": side_available,
        "sweep_1_feasible": {
            side: bool(sweeps[f"{side}_1"] and sweeps[f"{side}_1"]["fully_executable"])
            for side in ("buy", "sell")
        },
        "sweep_5_feasible": {
            side: bool(sweeps[f"{side}_5"] and sweeps[f"{side}_5"]["fully_executable"])
            for side in ("buy", "sell")
        },
        "sweep_10_feasible": {
            side: bool(sweeps[f"{side}_10"] and sweeps[f"{side}_10"]["fully_executable"])
            for side in ("buy", "sell")
        },
        "exit_feasible": bool(sweeps["sell_5"] and sweeps["sell_5"]["fully_executable"]),
        "age_seconds": age_seconds,
        "blocker_reason": state.blocker_reason if state else None,
        "subscription_epoch_id": state.subscription_epoch_id if state else None,
        "book_snapshot_id": snapshot.book_snapshot_id if snapshot else None,
        "raw_lineage_id": snapshot.raw_lineage_id if snapshot else None,
        "best_bid": snapshot.best_bid if snapshot else None,
        "best_ask": snapshot.best_ask if snapshot else None,
        "exchange_ts_ms": snapshot.exchange_ts_ms if snapshot else None,
        "last_frame_received_at_utc": snapshot.last_frame_received_at_utc if snapshot else None,
        "sweeps": sweeps,
    }
    if (
        valid
        and snapshot is not None
        and query.checkpoint == "source_t0"
        and query.outcome == "no"
        and "prior_exact_bracket" in query.roles
    ):
        result["parity_reconstructed_book"] = snapshot.to_dict()
    return result


def replay_ws_archive(
    runtime: Path,
    epochs: Sequence[Mapping[str, Any]],
    queries: Sequence[CheckpointQuery],
    *,
    file_order: str = "forward",
    start_date: str = ARCHIVE_START,
    end_date: str = CUTOFF_DATE,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Replay every WS file byte while materializing only requested checkpoints."""

    if file_order not in {"forward", "reverse", "chunked"}:
        raise ValueError("file_order must be forward, reverse, or chunked")
    relevant_tokens = {query.token_id for query in queries}
    query_by_day: dict[str, list[CheckpointQuery]] = collections.defaultdict(list)
    for query in queries:
        day = datetime.fromtimestamp(query.at_ns / 1e9, tz=timezone.utc).date().isoformat()
        query_by_day[day].append(query)
    epoch_by_day: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for epoch in epochs:
        day = datetime.fromtimestamp(int(epoch["_started_ns"]) / 1e9, tz=timezone.utc).date().isoformat()
        epoch_by_day[day].append(epoch)
    chain_ids = _chain_ids(epochs)
    engines: dict[str, IncrementalBookReconstructor] = {}
    engine_current_epoch: dict[str, str] = {}
    epoch_engine: dict[str, IncrementalBookReconstructor] = {}
    current_state: dict[str, TokenState] = {}
    ever_subscribed = {
        str(token)
        for epoch in epochs
        for token in epoch.get("token_ids") or ()
        if str(token) in relevant_tokens
    }
    epoch_token_condition: dict[tuple[str, str], str | None] = {}
    for epoch in epochs:
        epoch_id = str(epoch["subscription_epoch_id"])
        token_rows = epoch.get("token_rows") or {}
        for token in epoch.get("token_ids") or ():
            metadata = token_rows.get(str(token)) or {}
            epoch_token_condition[(epoch_id, str(token))] = (
                str(metadata.get("condition_id")) if metadata.get("condition_id") else None
            )

    output: list[dict[str, Any]] = []
    file_identities: list[dict[str, Any]] = []
    raw_frame_identity_digest = hashlib.sha256()
    raw_frame_count = 0
    raw_lineage_candidates: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    unknown_epoch_frames = 0
    reconstruction_errors = 0
    duplicate_frames = 0
    applied_frames = 0
    blockers = collections.Counter()
    clock_uncertainty_frames = 0
    active_roots_by_day: dict[str, set[str]] = collections.defaultdict(set)

    for day in _date_strings(start_date, end_date):
        paths = sorted((runtime / f"market_books/ws_incremental/{day}").glob("*.jsonl"))
        if file_order == "reverse":
            paths = list(reversed(paths))
        elif file_order == "chunked":
            paths = paths[::2] + paths[1::2]
        readers = [HashedJsonlReader(path) for path in paths]
        frame_heap: list[tuple[int, str, int, dict[str, Any]]] = []
        for index, reader in enumerate(readers):
            row = reader.next()
            if row is not None:
                frame_id = canonical_ws_frame_id(row)
                heapq.heappush(frame_heap, (int(row["_receive_order_ns"]), frame_id, index, row))
        activations = sorted(epoch_by_day.get(day, ()), key=lambda row: (row["_started_ns"], row["subscription_epoch_id"]))
        day_queries = sorted(query_by_day.get(day, ()), key=lambda row: (row.at_ns, row.event_id, row.token_id, row.checkpoint))
        activation_index = 0
        query_index = 0
        day_frames = 0
        day_errors_before = reconstruction_errors
        day_blockers_before = sum(blockers.values())
        day_unknown_before = unknown_epoch_frames
        day_clock_before = clock_uncertainty_frames

        while frame_heap or activation_index < len(activations) or query_index < len(day_queries):
            frame_key = (frame_heap[0][0], 1) if frame_heap else (10**30, 1)
            activation_key = (
                (int(activations[activation_index]["_started_ns"]), 0)
                if activation_index < len(activations)
                else (10**30, 0)
            )
            query_key = (
                (day_queries[query_index].at_ns, 2)
                if query_index < len(day_queries)
                else (10**30, 2)
            )
            next_kind = min((activation_key, "activation"), (frame_key, "frame"), (query_key, "query"))[1]

            if next_kind == "activation":
                epoch = activations[activation_index]
                activation_index += 1
                epoch_id = str(epoch["subscription_epoch_id"])
                root = chain_ids[epoch_id]
                active_roots_by_day[day].add(root)
                engine = engines.get(root)
                previous = str(epoch.get("previous_subscription_epoch_id") or "")
                expected_previous = engine_current_epoch.get(root)
                if engine is None or (expected_previous is not None and previous != expected_previous):
                    engine = IncrementalBookReconstructor(strict_best_parity=True)
                    engines[root] = engine
                    expected_previous = None
                carry = (
                    str(epoch.get("reason") or "") == "selector_reconcile"
                    and expected_previous is not None
                    and previous == expected_previous
                )
                engine.activate_epoch(
                    epoch_id,
                    epoch.get("token_ids") or (),
                    carry_forward=carry,
                    producer_build_id=(str(epoch.get("producer_build_id")) if epoch.get("producer_build_id") else None),
                    selector_version=(str(epoch.get("selector_version")) if epoch.get("selector_version") else None),
                    capture_policy=epoch.get("capture_policy") or {},
                    token_rows=epoch.get("token_rows") or {},
                )
                engine_current_epoch[root] = epoch_id
                epoch_engine[epoch_id] = engine
                for token in set(map(str, epoch.get("token_ids") or ())) & relevant_tokens:
                    if carry and token in engine.books and token not in engine.blocked_tokens:
                        try:
                            snapshot = engine.snapshot(token, observed_at_utc=str(epoch["started_at_utc"]), requested_shares=10.0)
                            truth = executable_book_truth(snapshot, shares=SWEEP_SHARES)
                            current_state[token] = TokenState(
                                at_ns=int(epoch["_started_ns"]),
                                subscription_epoch_id=epoch_id,
                                condition_id=epoch_token_condition.get((epoch_id, token)),
                                status="valid",
                                blocker_reason=None,
                                snapshot=snapshot,
                                truth=truth,
                            )
                            continue
                        except (BookReconstructionError, ValueError):
                            pass
                    current_state[token] = TokenState(
                        at_ns=int(epoch["_started_ns"]),
                        subscription_epoch_id=epoch_id,
                        condition_id=epoch_token_condition.get((epoch_id, token)),
                        status="open_gap" if token in engine.blocked_tokens else "no_baseline",
                        blocker_reason=engine.blocked_tokens.get(token),
                        snapshot=None,
                        truth=None,
                    )
            elif next_kind == "frame":
                _, frame_id, reader_index, frame = heapq.heappop(frame_heap)
                reader = readers[reader_index]
                day_frames += 1
                raw_frame_identity_digest.update(frame_id.encode("ascii"))
                raw_frame_identity_digest.update(b"\n")
                raw_frame_count += 1
                epoch_id = str(frame.get("subscription_epoch_id") or "")
                engine = epoch_engine.get(epoch_id)
                frame_tokens = _message_tokens(frame.get("message")) & relevant_tokens
                if frame.get("_within_file_receive_clock_regression"):
                    clock_uncertainty_frames += 1
                    blockers["within_file_receive_clock_regression"] += 1
                    for token in frame_tokens:
                        current_state[token] = TokenState(
                            at_ns=int(frame["_receive_order_ns"]),
                            subscription_epoch_id=epoch_id or None,
                            condition_id=epoch_token_condition.get((epoch_id, token)),
                            status="clock_uncertainty",
                            blocker_reason="within_file_receive_clock_regression",
                            snapshot=None,
                            truth=None,
                        )
                elif engine is None:
                    unknown_epoch_frames += 1
                    for token in frame_tokens:
                        current_state[token] = TokenState(
                            at_ns=int(frame["_receive_order_ns"]),
                            subscription_epoch_id=epoch_id or None,
                            condition_id=None,
                            status="clock_uncertainty",
                            blocker_reason="frame_epoch_not_activated",
                            snapshot=None,
                            truth=None,
                        )
                else:
                    duplicates_before = engine.duplicate_frame_count
                    applied_before = engine.applied_frame_count
                    try:
                        updated = engine.apply_envelope(frame)
                    except (BookReconstructionError, ValueError) as exc:
                        reconstruction_errors += 1
                        updated = ()
                        for token, blocker in engine.blocked_tokens.items():
                            if token not in relevant_tokens:
                                continue
                            blockers[blocker] += 1
                            current_state[token] = TokenState(
                                at_ns=int(frame["_receive_order_ns"]),
                                subscription_epoch_id=epoch_id,
                                condition_id=epoch_token_condition.get((epoch_id, token)),
                                status="open_gap",
                                blocker_reason=blocker or str(exc),
                                snapshot=None,
                                truth=None,
                            )
                    duplicate_frames += engine.duplicate_frame_count - duplicates_before
                    applied_frames += engine.applied_frame_count - applied_before
                    for token in set(updated) & relevant_tokens:
                        try:
                            snapshot = engine.snapshot(
                                token,
                                observed_at_utc=str(frame.get("received_at_utc")),
                                requested_shares=10.0,
                            )
                            truth = executable_book_truth(snapshot, shares=SWEEP_SHARES)
                        except (BookReconstructionError, ValueError) as exc:
                            blocker = engine.blocked_tokens.get(token, str(exc))
                            blockers[blocker] += 1
                            current_state[token] = TokenState(
                                at_ns=int(frame["_receive_order_ns"]),
                                subscription_epoch_id=epoch_id,
                                condition_id=epoch_token_condition.get((epoch_id, token)),
                                status="open_gap",
                                blocker_reason=blocker,
                                snapshot=None,
                                truth=None,
                            )
                            continue
                        current_state[token] = TokenState(
                            at_ns=int(frame["_receive_order_ns"]),
                            subscription_epoch_id=epoch_id,
                            condition_id=epoch_token_condition.get((epoch_id, token)),
                            status="valid",
                            blocker_reason=None,
                            snapshot=snapshot,
                            truth=truth,
                        )
                        if len(raw_lineage_candidates) < 1000:
                            raw_lineage_candidates.append(
                                {
                                    "token_id": token,
                                    "book_snapshot_id": snapshot.book_snapshot_id,
                                    "raw_lineage_id": snapshot.raw_lineage_id,
                                    "baseline_raw_frame_ref": asdict(snapshot.baseline_raw_frame_ref),
                                    "delta_first_raw_frame_ref": asdict(snapshot.delta_first_raw_frame_ref) if snapshot.delta_first_raw_frame_ref else None,
                                    "delta_last_raw_frame_ref": asdict(snapshot.delta_last_raw_frame_ref) if snapshot.delta_last_raw_frame_ref else None,
                                    "delta_frame_count": snapshot.delta_frame_count,
                                    "delta_chain_hash": snapshot.delta_chain_hash,
                                }
                            )
                next_row = reader.next()
                if next_row is not None:
                    next_id = canonical_ws_frame_id(next_row)
                    heapq.heappush(
                        frame_heap,
                        (int(next_row["_receive_order_ns"]), next_id, reader_index, next_row),
                    )
            else:
                query = day_queries[query_index]
                query_index += 1
                output.append(
                    classify_checkpoint(
                        query,
                        current_state.get(query.token_id),
                        ever_subscribed=query.token_id in ever_subscribed,
                        expected_condition=query.condition_id,
                    )
                )

        for reader in readers:
            reader.close()
            file_identities.append(reader.identity())
        day_summaries.append(
            {
                "date": day,
                "ws_file_count": len(paths),
                "raw_frame_count": day_frames,
                "subscription_epoch_count": len(activations),
                "transport_chain_count": len(active_roots_by_day.get(day, ())),
                "reconstruction_error_count": reconstruction_errors - day_errors_before,
                "unknown_epoch_frame_count": unknown_epoch_frames - day_unknown_before,
                "clock_uncertainty_frame_count": clock_uncertainty_frames - day_clock_before,
                "blocker_transition_count": sum(blockers.values()) - day_blockers_before,
                "normal_day": (
                    reconstruction_errors - day_errors_before == 0
                    and unknown_epoch_frames - day_unknown_before == 0
                    and clock_uncertainty_frames - day_clock_before == 0
                    and len(active_roots_by_day.get(day, ())) == 1
                ),
                "reconnect_or_gap_day": (
                    reconstruction_errors - day_errors_before > 0
                    or unknown_epoch_frames - day_unknown_before > 0
                    or clock_uncertainty_frames - day_clock_before > 0
                    or len(active_roots_by_day.get(day, ())) != 1
                ),
            }
        )
    if len(output) != len(queries):
        raise RuntimeError(f"checkpoint cardinality mismatch: {len(output)} != {len(queries)}")
    raw_identity = raw_frame_identity_digest.hexdigest()
    coverage_identity = canonical_hash(
        [
            {key: row.get(key) for key in ("event_id", "checkpoint", "token_id", "primary_status", "book_snapshot_id", "sweep_5_feasible")}
            for row in sorted(output, key=lambda row: (row["event_id"], row["checkpoint"], row["token_id"]))
        ]
    )
    archive = {
        "archive_start": start_date,
        "cutoff_date_inclusive": end_date,
        "replay_file_order": file_order,
        "ws_file_count": len(file_identities),
        "ws_files": sorted(file_identities, key=lambda row: row["path"]),
        "raw_frame_count": raw_frame_count,
        "raw_frame_ordered_identity": raw_identity,
        "coverage_identity": coverage_identity,
        "query_count": len(queries),
        "relevant_token_count": len(relevant_tokens),
        "ever_subscribed_relevant_token_count": len(ever_subscribed),
        "unknown_epoch_frame_count": unknown_epoch_frames,
        "applied_frame_count": applied_frames,
        "duplicate_frame_count": duplicate_frames,
        "reconstruction_error_count": reconstruction_errors,
        "clock_uncertainty_frame_count": clock_uncertainty_frames,
        "blocker_reason_counts": dict(sorted(blockers.items())),
        "day_summaries": day_summaries,
        "transport_day_count": sum(row["ws_file_count"] > 0 for row in day_summaries),
        "normal_day_count": sum(row["normal_day"] for row in day_summaries),
        "reconnect_or_gap_day_count": sum(row["reconnect_or_gap_day"] for row in day_summaries),
    }
    lineage_sample = random.Random(20260827).sample(
        raw_lineage_candidates, min(25, len(raw_lineage_candidates))
    )
    return sorted(output, key=lambda row: (row["event_id"], row["checkpoint"], row["token_id"])), archive, lineage_sample


def compute_rest_ws_parity(
    runtime: Path, alignments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Compare every valid exact source-t0 book with its nearest REST capture."""

    candidates = [
        row
        for row in alignments
        if row.get("parity_reconstructed_book") is not None
    ]
    by_token: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for row in candidates:
        by_token[str(row["token_id"])].append(row)
    best: dict[tuple[str, str, str], tuple[int, dict[str, Any], str, int]] = {}
    root = runtime / "market_books/batches"
    for day in sorted(root.glob("2026-08-*")):
        if not ARCHIVE_START <= day.name <= CUTOFF_DATE:
            continue
        for path in sorted(day.glob("*.jsonl.gz")):
            for line_number, raw in enumerate(read_jsonl(path), 1):
                token = str(raw.get("token_id") or "")
                if token not in by_token or raw.get("status") != "ok":
                    continue
                rest_ts = raw.get("exchange_book_ts_raw") or (raw.get("raw") or {}).get("timestamp")
                try:
                    rest_ts_ms = int(rest_ts)
                except (TypeError, ValueError):
                    continue
                for candidate in by_token[token]:
                    ws_ts = candidate.get("exchange_ts_ms")
                    if ws_ts is None:
                        continue
                    key = (
                        str(candidate["event_id"]),
                        str(candidate["checkpoint"]),
                        token,
                    )
                    distance = abs(int(ws_ts) - rest_ts_ms)
                    if key not in best or distance < best[key][0]:
                        best[key] = (distance, raw, str(path), line_number)
    comparisons: list[dict[str, Any]] = []
    missing = []
    for candidate in sorted(candidates, key=lambda row: (row["event_id"], row["token_id"])):
        key = (
            str(candidate["event_id"]),
            str(candidate["checkpoint"]),
            str(candidate["token_id"]),
        )
        nearest = best.get(key)
        if nearest is None:
            missing.append(
                {"event_id": key[0], "checkpoint": key[1], "token_id": key[2]}
            )
            continue
        _, rest, path, line_number = nearest
        book_payload = dict(candidate["parity_reconstructed_book"])
        book_payload["bids"] = tuple(tuple(level) for level in book_payload.get("bids") or ())
        book_payload["asks"] = tuple(tuple(level) for level in book_payload.get("asks") or ())
        comparison = compare_rest_ws_parity(SimpleNamespace(**book_payload), rest).to_dict()
        comparisons.append(
            {
                "event_id": candidate["event_id"],
                "checkpoint": candidate["checkpoint"],
                "rest_raw_path": path,
                "rest_raw_line_number": line_number,
                **comparison,
            }
        )
    status_counts = collections.Counter(row["parity_status"] for row in comparisons)
    comparable = [
        row
        for row in comparisons
        if row["parity_status"] != "not_comparable_clock_skew"
    ]
    return {
        "candidate_ws_book_count": len(candidates),
        "nearest_rest_match_count": len(comparisons),
        "missing_rest_match_count": len(missing),
        "missing": missing,
        "comparable_count": len(comparable),
        "not_comparable_clock_skew_count": sum(
            row["parity_status"] == "not_comparable_clock_skew" for row in comparisons
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "comparisons": comparisons,
        "contract": "REST is independent parity evidence only and never mutates WS reconstruction",
    }


def coverage_root_cause(
    events: Sequence[Mapping[str, Any]],
    alignments: Sequence[Mapping[str, Any]],
    archive_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    event_by_id = {str(row["event_id"]): row for row in events}
    source_rows = [
        row
        for row in alignments
        if row["checkpoint"] == "source_t0"
        and row["outcome"] == "no"
        and "prior_exact_bracket" in row.get("roles", ())
    ]
    by_city: dict[str, Any] = {}
    for city in CITIES:
        rows = [row for row in source_rows if event_by_id[str(row["event_id"])]["city"] == city]
        stale_ages = [float(row["age_seconds"]) for row in rows if row["primary_status"] == "STALE_BOOK"]
        by_city[city] = {
            "events": len(rows),
            "primary_status_counts": dict(sorted(collections.Counter(row["primary_status"] for row in rows).items())),
            "stale_age_seconds": {
                "p50": percentile(stale_ages, 0.50),
                "p90": percentile(stale_ages, 0.90),
                "p95": percentile(stale_ages, 0.95),
                "max": max(stale_ages) if stale_ages else None,
            },
        }
    status_counts = collections.Counter(row["primary_status"] for row in source_rows)
    return {
        "headline": "archive-wide source-t0 coverage loss is dominated by selective non-subscription and stale capture windows, not by one-sided/depth feasibility",
        "source_t0_event_count": len(source_rows),
        "primary_status_counts": dict(sorted(status_counts.items())),
        "root_cause_mapping": {
            "ARCHIVE_MISSING": "exact event token never appears in any frozen subscription epoch; dominant for Seoul",
            "STALE_BOOK": "token was subscribed and reconstructable earlier, but last WS frame exceeded the frozen 120s age policy at source_t0",
            "NO_BASELINE": "token was subscribed but no verified full-book baseline existed by source_t0",
            "OPEN_GAP": "a parity/clock/reconstruction blocker remained open at source_t0",
            "VALID_BUT_ONE_SIDED": "reconstruction valid; economic side availability missing",
            "VALID_BUT_INSUFFICIENT_DEPTH": "reconstruction valid and two-sided; 5-share sweep not feasible",
            "VALID_TWO_SIDED_DEPTH": "reconstruction and 5-share two-sided feasibility both valid",
        },
        "by_city": by_city,
        "archive_gate_checks": {
            "at_least_10_transport_days": int(archive_manifest["transport_day_count"]) >= 10,
            "at_least_3_reconnect_or_gap_days": int(archive_manifest["reconnect_or_gap_day_count"]) >= 3,
            "at_least_3_normal_days": int(archive_manifest["normal_day_count"]) >= 3,
            "unknown_primary_reason_zero": all(row["primary_status"] in FAILURE_REASONS for row in source_rows),
            "source_t0_book_valid_ge_90pct": (
                sum(row["book_valid"] for row in source_rows) / len(source_rows) >= 0.90
                if source_rows
                else False
            ),
        },
        "disposition": "RETURN_TO_STAGE2_COLLECTOR_CLOCK_DIAGNOSIS_WITHOUT_CHANGING_COLLECTOR",
        "collector_change_authorized": False,
    }


def _sweep(row: Mapping[str, Any], side: str, shares: int) -> Mapping[str, Any] | None:
    value = (row.get("sweeps") or {}).get(f"{side}_{shares}")
    return value if isinstance(value, Mapping) else None


def select_primary_oracle_action(
    event: Mapping[str, Any],
    universe: Mapping[str, Any],
    entry_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Choose using next-print truth plus t0 cost only; never future prices."""

    if int(event.get("official_round_c")) <= int(event.get("metar_running_max_round_c")):
        return {"action": "NO_TRADE", "reason": "next_print_does_not_raise_running_max"}
    candidate_tokens: list[tuple[float, str, str]] = []
    for token in universe.get("tokens", ()):
        roles = set(token.get("roles") or ())
        desired = (
            ("prior_exact_bracket" in roles and token.get("outcome") == "no")
            or ("actual_next_print_bracket" in roles and token.get("outcome") == "yes")
        )
        if not desired:
            continue
        row = entry_rows.get(str(token["token_id"]))
        sweep = _sweep(row or {}, "buy", int(PRIMARY_SHARES))
        if row and row.get("book_valid") and sweep and sweep.get("fully_executable"):
            candidate_tokens.append(
                (float(sweep["effective_value_usd"]), str(token["token_id"]), str(token["outcome"]))
            )
    if not candidate_tokens:
        return {"action": "NO_TRADE", "reason": "no_semantic_candidate_has_feasible_entry"}
    cost, token_id, outcome = min(candidate_tokens, key=lambda row: (row[0], row[1]))
    return {
        "action": "BUY_OUTCOME_TOKEN",
        "reason": "lowest_t0_effective_cost_among_predeclared_semantic_expressions",
        "token_id": token_id,
        "outcome": outcome,
        "entry_effective_cost_usd": cost,
    }


def independent_net_pnl(entry: Mapping[str, Any], exit_: Mapping[str, Any]) -> float:
    """Independent cents-level recomputation from gross values and both fees."""

    entry_gross = float(entry["gross_value_usd"])
    entry_fee = float(entry["taker_fee_usd"])
    exit_gross = float(exit_["gross_value_usd"])
    exit_fee = float(exit_["taker_fee_usd"])
    return (exit_gross - exit_fee) - (entry_gross + entry_fee)


def build_oracle_results(
    events: Sequence[Mapping[str, Any]],
    universes: Sequence[Mapping[str, Any]],
    alignments: Sequence[Mapping[str, Any]],
    latency_contract: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    event_by_id = {str(row["event_id"]): row for row in events}
    universe_by_id = {str(row["event_id"]): row for row in universes}
    lookup = {
        (str(row["event_id"]), str(row["checkpoint"]), str(row["token_id"])): row
        for row in alignments
    }
    rows: list[dict[str, Any]] = []
    pnl_audit: list[dict[str, Any]] = []
    action_invariance_failures: list[str] = []
    for event_id, event in event_by_id.items():
        universe = universe_by_id.get(event_id, {"tokens": []})
        entry_rows = {
            str(token["token_id"]): lookup.get((event_id, "entry_after_p95_latency", str(token["token_id"])), {})
            for token in universe.get("tokens", ())
        }
        action = select_primary_oracle_action(event, universe, entry_rows)
        action_scrubbed = select_primary_oracle_action(
            {key: value for key, value in event.items() if not str(key).startswith("future_")},
            universe,
            entry_rows,
        )
        if action != action_scrubbed:
            action_invariance_failures.append(event_id)
        token_id = action.get("token_id")
        entry_row = entry_rows.get(str(token_id)) if token_id else None
        exit_row = lookup.get((event_id, f"official_plus_{PRIMARY_HORIZON_SECONDS}s", str(token_id))) if token_id else None
        entry_sweep = _sweep(entry_row or {}, "buy", int(PRIMARY_SHARES))
        exit_sweep = _sweep(exit_row or {}, "sell", int(PRIMARY_SHARES))
        official = parse_ts(event.get("official_first_seen_at_utc"))
        source = parse_ts(event.get("source_detect_ts_utc"))
        effective_lead = (
            (official - source).total_seconds() - float(latency_contract["value_seconds"])
            if official and source
            else None
        )
        executable = bool(
            action.get("action") == "BUY_OUTCOME_TOKEN"
            and effective_lead is not None
            and effective_lead > 0
            and entry_sweep
            and entry_sweep.get("fully_executable")
            and exit_sweep
            and exit_sweep.get("fully_executable")
        )
        pnl = independent_net_pnl(entry_sweep, exit_sweep) if executable else None
        if executable:
            serialized = float(exit_sweep["effective_value_usd"]) - float(entry_sweep["effective_value_usd"])
            if abs(serialized - float(pnl)) > 1e-9:
                raise RuntimeError(f"independent PnL mismatch for event={event_id}")
            pnl_audit.append(
                {
                    "event_id": event_id,
                    "token_id": token_id,
                    "entry_book_snapshot_id": entry_row.get("book_snapshot_id"),
                    "exit_book_snapshot_id": exit_row.get("book_snapshot_id"),
                    "entry_gross_usd": entry_sweep["gross_value_usd"],
                    "entry_fee_usd": entry_sweep["taker_fee_usd"],
                    "exit_gross_usd": exit_sweep["gross_value_usd"],
                    "exit_fee_usd": exit_sweep["taker_fee_usd"],
                    "net_pnl_usd": pnl,
                }
            )
        rows.append(
            {
                "event_id": event_id,
                "city": event["city"],
                "target_date": event["target_date"],
                "official_print_id": event.get("information_event_id"),
                "source_first_seen_at_utc": event.get("source_detect_ts_utc"),
                "official_first_seen_at_utc": event.get("official_first_seen_at_utc"),
                "latency_contract_id": latency_contract["contract_id"],
                "execution_latency_seconds": latency_contract["value_seconds"],
                "effective_lead_seconds": effective_lead,
                "primary_shares": PRIMARY_SHARES,
                "primary_horizon_seconds": PRIMARY_HORIZON_SECONDS,
                "action": action,
                "entry_book_snapshot_id": entry_row.get("book_snapshot_id") if entry_row else None,
                "exit_book_snapshot_id": exit_row.get("book_snapshot_id") if exit_row else None,
                "entry_sweep": entry_sweep,
                "exit_sweep": exit_sweep,
                "paired_executable": executable,
                "net_pnl_usd": pnl,
                "row_id": canonical_hash(
                    {
                        "event_id": event_id,
                        "action": action,
                        "entry": entry_row.get("book_snapshot_id") if entry_row else None,
                        "exit": exit_row.get("book_snapshot_id") if exit_row else None,
                        "latency_contract_id": latency_contract["contract_id"],
                        "shares": PRIMARY_SHARES,
                        "horizon": PRIMARY_HORIZON_SECONDS,
                    }
                ),
            }
        )

    envelope: list[dict[str, Any]] = []
    for event_id, event in event_by_id.items():
        universe = universe_by_id.get(event_id, {"tokens": []})
        candidates = []
        for token in universe.get("tokens", ()):
            token_id = str(token["token_id"])
            entry = lookup.get((event_id, "entry_after_p95_latency", token_id), {})
            exit_ = lookup.get((event_id, f"official_plus_{PRIMARY_HORIZON_SECONDS}s", token_id), {})
            buy = _sweep(entry, "buy", int(PRIMARY_SHARES))
            sell = _sweep(exit_, "sell", int(PRIMARY_SHARES))
            if buy and sell and buy.get("fully_executable") and sell.get("fully_executable"):
                candidates.append(
                    {
                        "token_id": token_id,
                        "outcome": token["outcome"],
                        "bracket": token["bracket"],
                        "net_pnl_usd": independent_net_pnl(buy, sell),
                    }
                )
        best = max(candidates, key=lambda row: (row["net_pnl_usd"], row["token_id"])) if candidates else None
        envelope.append(
            {
                "event_id": event_id,
                "city": event["city"],
                "target_date": event["target_date"],
                "diagnostic_label": "UNATTAINABLE_EX_POST_BEST_CONTRACT_ENVELOPE_NOT_FOR_PROMOTION",
                "candidate_count": len(candidates),
                "best": best,
            }
        )
    audit = {
        "action_future_price_column_deletion_invariance": "pass" if not action_invariance_failures else "fail",
        "action_invariance_failure_event_ids": action_invariance_failures,
        "paired_pnl_row_count": len(pnl_audit),
        "independent_pnl_recalculation_status": "pass",
        "primary_contract": {
            "shares": PRIMARY_SHARES,
            "horizon_seconds": PRIMARY_HORIZON_SECONDS,
            "entry": "fee-aware ask sweep",
            "exit": "fee-aware bid sweep",
            "action_information": "actual next print + prior running state + t0 entry cost only",
        },
    }
    return rows, envelope, {"summary": audit, "rows": pnl_audit}


def date_block_ci(rows: Sequence[Mapping[str, Any]], *, seed: int = 20260827) -> dict[str, Any]:
    by_date: dict[str, list[float]] = collections.defaultdict(list)
    for row in rows:
        if row.get("paired_executable") and row.get("net_pnl_usd") is not None:
            by_date[str(row["target_date"])].append(float(row["net_pnl_usd"]))
    date_values = {date: statistics.mean(values) for date, values in by_date.items()}
    if len(date_values) < 2:
        return {
            "status": "insufficient_target_dates",
            "target_date_count": len(date_values),
            "one_sided_90_lower": None,
            "one_sided_90_upper": None,
        }
    values = list(date_values.values())
    rng = random.Random(seed)
    draws = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(10000)]
    draws.sort()
    return {
        "status": "computed",
        "target_date_count": len(values),
        "date_mean_pnl_usd": statistics.mean(values),
        "one_sided_90_lower": draws[math.floor(0.10 * (len(draws) - 1))],
        "one_sided_90_upper": draws[math.ceil(0.90 * (len(draws) - 1))],
        "bootstrap_draws": len(draws),
        "seed": seed,
    }


def summarize_stage3(
    events: Sequence[Mapping[str, Any]],
    universes: Sequence[Mapping[str, Any]],
    alignments: Sequence[Mapping[str, Any]],
    oracle_rows: Sequence[Mapping[str, Any]],
    forecast_rows: Sequence[Mapping[str, Any]],
    official: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    source_exact = {
        str(row["event_id"]): row
        for row in alignments
        if row["checkpoint"] == "source_t0" and "prior_exact_bracket" in row.get("roles", ()) and row["outcome"] == "no"
    }
    paired_by_event = {str(row["event_id"]): row for row in oracle_rows}
    forecast_by_event = {str(row["event_id"]): row for row in forecast_rows}
    universe_by_event = {str(row["event_id"]): row for row in universes}
    alignment_lookup = {
        (str(row["event_id"]), str(row["checkpoint"]), str(row["token_id"])): row
        for row in alignments
    }
    city_gates: dict[str, Any] = {}
    thresholds = {
        "Amsterdam": (40, 180, 100, 100),
        "Helsinki": (40, 150, 100, 100),
        "Tokyo": (40, 120, 80, 80),
        "Seoul": (40, 300, 150, 120),
        "Busan": (40, 350, 150, 120),
    }
    for city in CITIES:
        city_events = [row for row in events if row["city"] == city and row.get("next_official_linked")]
        source_rows = [source_exact.get(str(row["event_id"])) for row in city_events]
        valid = [row for row in source_rows if row and row["book_valid"]]
        paired = [paired_by_event[str(row["event_id"])] for row in city_events if paired_by_event.get(str(row["event_id"]), {}).get("paired_executable")]
        dates = sorted({str(row["target_date"]) for row in city_events})
        by_date = {}
        for date in dates:
            day_rows = [source_exact.get(str(row["event_id"])) for row in city_events if row["target_date"] == date]
            by_date[date] = (
                sum(bool(row and row["book_valid"]) for row in day_rows) / len(day_rows)
                if day_rows
                else None
            )
        minimum = thresholds[city]
        metrics = {
            "target_dates": len(dates),
            "causal_events": len(city_events),
            "unique_official_print_groups": len({str(row.get("information_event_id")) for row in city_events if row.get("information_event_id")}),
            "paired_5_share_feasible_events": len(paired),
            "source_t0_book_valid_rate": len(valid) / len(source_rows) if source_rows else None,
            "paired_entry_exit_rate": len(paired) / len(city_events) if city_events else None,
            "target_dates_source_t0_book_valid_ge_80pct_rate": (
                sum(value is not None and value >= 0.80 for value in by_date.values()) / len(by_date)
                if by_date
                else None
            ),
            "source_t0_status_counts": dict(sorted(collections.Counter(row["primary_status"] if row else "MISSING_ALIGNMENT" for row in source_rows).items())),
            "by_target_date_source_t0_book_valid_rate": by_date,
        }
        gate_checks = {
            "minimum_target_dates": metrics["target_dates"] >= minimum[0],
            "minimum_causal_events": metrics["causal_events"] >= minimum[1],
            "minimum_unique_official_print_groups": metrics["unique_official_print_groups"] >= minimum[2],
            "minimum_paired_5_share_feasible_events": metrics["paired_5_share_feasible_events"] >= minimum[3],
            "source_t0_book_valid_ge_90pct": (metrics["source_t0_book_valid_rate"] or 0.0) >= 0.90,
            "paired_entry_exit_ge_85pct": (metrics["paired_entry_exit_rate"] or 0.0) >= 0.85,
            "at_least_90pct_dates_source_t0_ge_80pct": (metrics["target_dates_source_t0_book_valid_ge_80pct_rate"] or 0.0) >= 0.90,
        }
        city_oracle = [row for row in oracle_rows if row["city"] == city]
        city_gates[city] = {
            "requirements": {
                "target_dates": minimum[0],
                "causal_events": minimum[1],
                "unique_official_print_groups": minimum[2],
                "paired_5_share_feasible_events": minimum[3],
            },
            "metrics": metrics,
            "gate_checks": gate_checks,
            "date_block_inference": date_block_ci(city_oracle),
            "disposition": "ELIGIBLE_FOR_STAGE3_ACCEPTANCE_REVIEW" if all(gate_checks.values()) else "CONTINUE_COLLECTION_WITHOUT_MODELING",
        }

    intersection_rows = []
    baseline_rows: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        oracle = paired_by_event.get(event_id)
        slope = recent_slope_for_event(event, official)
        forecast = forecast_by_event.get(event_id, {})
        persistence_available = event.get("latest_metar_round_c") is not None
        universe = universe_by_event.get(event_id, {"tokens": []})
        market_rows = [
            alignment_lookup.get((event_id, "pre_source", str(token["token_id"])))
            for token in universe.get("tokens", ())
        ]
        market_only_available = bool(
            market_rows
            and all(
                row
                and row.get("primary_status") == "VALID_TWO_SIDED_DEPTH"
                for row in market_rows
            )
        )
        market_prediction = None
        if market_only_available:
            yes_candidates = []
            for token, row in zip(universe.get("tokens", ()), market_rows):
                if token.get("outcome") != "yes" or not row:
                    continue
                midpoint = (float(row["best_bid"]) + float(row["best_ask"])) / 2.0
                yes_candidates.append((midpoint, float(token["bracket_order"]), str(token["token_id"])))
            if yes_candidates:
                _, predicted_round, predicted_token = max(
                    yes_candidates, key=lambda value: (value[0], -value[1], value[2])
                )
                market_prediction = {
                    "predicted_next_round_c": predicted_round,
                    "highest_implied_yes_token_id": predicted_token,
                }
        all_available = bool(
            oracle
            and oracle.get("paired_executable")
            and persistence_available
            and slope.get("status") == "available"
            and forecast.get("status") == "available"
            and market_only_available
            and market_prediction
        )
        baseline_rows.append(
            {
                "event_id": event_id,
                "city": event["city"],
                "target_date": event["target_date"],
                "official_print_id": event.get("information_event_id"),
                "oracle_paired_executable": bool(oracle and oracle.get("paired_executable")),
                "persistence": {
                    "status": "available" if persistence_available else "unavailable",
                    "predicted_next_round_c": event.get("latest_metar_round_c"),
                },
                "recent_slope": slope,
                "forecast_only": {
                    "status": forecast.get("status"),
                    "predicted_next_round_c": forecast.get("predicted_next_round_c"),
                    "forecast_model": forecast.get("forecast_model"),
                    "capture_id": forecast.get("capture_id"),
                    "forecast_values_hash": forecast.get("forecast_values_hash"),
                    "available_at_utc": forecast.get("available_at_utc"),
                },
                "market_only_features": {
                    "status": "available" if market_only_available else "unavailable",
                    "required_token_count": len(market_rows),
                    "valid_two_sided_token_count": sum(
                        bool(row and row.get("primary_status") == "VALID_TWO_SIDED_DEPTH")
                        for row in market_rows
                    ),
                    "raw_market_prediction": market_prediction,
                },
                "same_row_intersection_eligible": all_available,
            }
        )
        if all_available:
            intersection_rows.append(event_id)
    row_hash = canonical_hash(sorted(intersection_rows))
    intersection_events = [
        event_by_id
        for event_by_id in events
        if str(event_by_id["event_id"]) in set(intersection_rows)
    ]
    intersection_summary = {
        "raw_n": len(intersection_rows),
        "unique_official_print_n": len(
            {str(row.get("information_event_id")) for row in intersection_events if row.get("information_event_id")}
        ),
        "target_date_n": len({str(row["target_date"]) for row in intersection_events}),
        "effective_n": len(
            {
                (str(row["target_date"]), str(row.get("information_event_id")))
                for row in intersection_events
            }
        ),
    }
    baselines = {
        "same_executable_row_intersection": {
            "event_ids": sorted(intersection_rows),
            "row_count": len(intersection_rows),
            "row_hash": row_hash,
            "no_per_model_row_deletion": True,
            **intersection_summary,
        },
        "persistence": {"contract": "predict latest official rounded print", "availability": "event field"},
        "recent_slope": {"contract": "latest + (latest - previous) using official prints available by decision"},
        "forecast_only": {
            "contract": "latest frozen forecast curve available by decision; model/capture/value identities frozen",
            "available_events": sum(row.get("status") == "available" for row in forecast_rows),
        },
        "market_only_blocked_oof": {
            "status": "blocked",
            "reason": (
                "same-row intersection is empty"
                if not intersection_rows
                else "Stage 2 source-t0 coverage gate is not closed; OOF fitting is not authorized"
            ),
            "outer_split": "target_date",
            "inner_group": "official_print_id",
            "full_sample_fit_forbidden": True,
        },
        "stage3_baseline_closure": (
            "blocked_stage2_coverage_gate_and_insufficient_exact_intersection"
            if len(intersection_rows) < len(events)
            else "available"
        ),
    }

    concentration = {}
    for city in CITIES:
        rows = [row for row in oracle_rows if row["city"] == city and row.get("paired_executable")]
        by_date: dict[str, float] = collections.defaultdict(float)
        for row in rows:
            by_date[str(row["target_date"])] += float(row["net_pnl_usd"])
        positives = sorted((value for value in by_date.values() if value > 0), reverse=True)
        total_positive = sum(positives)
        top_count = max(1, math.ceil(len(positives) * 0.2)) if positives else 0
        concentration[city] = {
            "paired_rows": len(rows),
            "target_dates": len(by_date),
            "total_net_pnl_usd": sum(by_date.values()),
            "best_date_removed_net_pnl_usd": (
                sum(by_date.values()) - max(by_date.values()) if by_date else None
            ),
            "largest_positive_date_share": (positives[0] / total_positive if positives and total_positive else None),
            "top_20pct_positive_date_share": (sum(positives[:top_count]) / total_positive if positives and total_positive else None),
        }
    statistics_report = {
        "primary_horizon_seconds": PRIMARY_HORIZON_SECONDS,
        "primary_shares": PRIMARY_SHARES,
        "by_city_concentration": concentration,
        "date_block_ci": {city: city_gates[city]["date_block_inference"] for city in CITIES},
        "pre_source_reaction": "retained in event-book matrix via source_minus_300s_placebo and pre_source checkpoints",
        "reaction_windows": [
            "pre_source_to_source_t0",
            "entry_after_p95_latency_to_pre_official",
            *[f"official_to_plus_{seconds}s" for seconds in (PRIMARY_HORIZON_SECONDS, *SECONDARY_HORIZONS)],
        ],
        "promotion_gate": "not_evaluable_until_paired_rows_and_date_coverage_exist",
    }
    return city_gates, baselines, statistics_report, baseline_rows


def build_reaction_windows(
    oracle_rows: Sequence[Mapping[str, Any]],
    alignments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lookup = {
        (str(row["event_id"]), str(row["checkpoint"]), str(row["token_id"])): row
        for row in alignments
    }
    windows = [
        ("source_minus_300s_placebo", "source_t0", "source_time_shift_placebo"),
        ("pre_source", "source_t0", "pre_source_to_t0"),
        ("entry_after_p95_latency", "pre_official", "post_latency_to_pre_official"),
        *[
            ("entry_after_p95_latency", f"official_plus_{seconds}s", f"entry_to_official_plus_{seconds}s")
            for seconds in (PRIMARY_HORIZON_SECONDS, *SECONDARY_HORIZONS)
        ],
    ]
    rows: list[dict[str, Any]] = []
    for oracle in oracle_rows:
        token = (oracle.get("action") or {}).get("token_id")
        if not token:
            continue
        event_id = str(oracle["event_id"])
        for start_name, end_name, label in windows:
            start = lookup.get((event_id, start_name, str(token)), {})
            end = lookup.get((event_id, end_name, str(token)), {})
            buy = _sweep(start, "buy", int(PRIMARY_SHARES))
            sell = _sweep(end, "sell", int(PRIMARY_SHARES))
            feasible = bool(
                buy
                and sell
                and buy.get("fully_executable")
                and sell.get("fully_executable")
            )
            rows.append(
                {
                    "event_id": event_id,
                    "city": oracle["city"],
                    "target_date": oracle["target_date"],
                    "official_print_id": oracle.get("official_print_id"),
                    "token_id": token,
                    "window": label,
                    "start_checkpoint": start_name,
                    "end_checkpoint": end_name,
                    "start_book_snapshot_id": start.get("book_snapshot_id"),
                    "end_book_snapshot_id": end.get("book_snapshot_id"),
                    "paired_5_share_feasible": feasible,
                    "net_markout_usd": independent_net_pnl(buy, sell) if feasible else None,
                }
            )
    aggregates = []
    for city in CITIES:
        for _, _, label in windows:
            selected = [
                row
                for row in rows
                if row["city"] == city
                and row["window"] == label
                and row["paired_5_share_feasible"]
            ]
            values = [float(row["net_markout_usd"]) for row in selected]
            aggregates.append(
                {
                    "city": city,
                    "window": label,
                    "raw_n": len(selected),
                    "unique_official_print_n": len(
                        {str(row["official_print_id"]) for row in selected if row.get("official_print_id")}
                    ),
                    "target_date_n": len({str(row["target_date"]) for row in selected}),
                    "mean_net_markout_usd": statistics.mean(values) if values else None,
                }
            )
    primary = [
        row
        for row in rows
        if row["window"] == f"entry_to_official_plus_{PRIMARY_HORIZON_SECONDS}s"
        and row["paired_5_share_feasible"]
    ]
    placebo = [
        row
        for row in rows
        if row["window"] == "source_time_shift_placebo"
        and row["paired_5_share_feasible"]
    ]
    return rows, {
        "primary_window": f"entry_to_official_plus_{PRIMARY_HORIZON_SECONDS}s",
        "primary_paired_rows": len(primary),
        "source_time_shift_placebo_paired_rows": len(placebo),
        "aggregates": aggregates,
        "placebo_promotion_status": "not_evaluable_insufficient_coverage",
    }


def summarize_alignment_coverage(
    events: Sequence[Mapping[str, Any]], alignments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    counts = collections.Counter((row["checkpoint"], row["primary_status"]) for row in alignments)
    exact_source = [
        row
        for row in alignments
        if row["checkpoint"] == "source_t0"
        and row["outcome"] == "no"
        and "prior_exact_bracket" in row.get("roles", ())
    ]
    status_counts = collections.Counter(row["primary_status"] for row in exact_source)
    invalid = sum(not row["book_valid"] for row in exact_source)
    infeasible = sum(row["execution_infeasible"] for row in exact_source)
    if any(row["reconstruction_invalid"] and row["execution_infeasible"] for row in exact_source):
        raise RuntimeError("invalid and execution-infeasible must be mutually exclusive")
    return {
        "event_count": len(events),
        "alignment_row_count": len(alignments),
        "unknown_primary_status_count": sum(row["primary_status"] not in FAILURE_REASONS for row in alignments),
        "source_t0_exact_prior_no_row_count": len(exact_source),
        "source_t0_book_valid_count": sum(row["book_valid"] for row in exact_source),
        "source_t0_book_valid_rate": (
            sum(row["book_valid"] for row in exact_source) / len(exact_source) if exact_source else None
        ),
        "source_t0_reconstruction_invalid_count": invalid,
        "source_t0_execution_infeasible_count": infeasible,
        "invalid_and_execution_infeasible_overlap_count": 0,
        "source_t0_primary_status_counts": dict(sorted(status_counts.items())),
        "checkpoint_status_counts": [
            {"checkpoint": checkpoint, "primary_status": status, "count": count}
            for (checkpoint, status), count in sorted(counts.items())
        ],
        "coverage_hash": canonical_hash(
            [
                {key: row[key] for key in ("event_id", "checkpoint", "token_id", "primary_status", "book_snapshot_id")}
                for row in alignments
            ]
        ),
    }


def evidence_manifest(root: Path, *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in excluded:
            continue
        files.append(
            {
                "path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "root": str(root),
        "entry_count": len(files),
        "entries": files,
        "entry_set_sha256": canonical_hash(files),
        "unmanifested_extra_files_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=Path("/Volumes/jrs/weather_data_feed_service_runtime"))
    parser.add_argument(
        "--frozen-events",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz",
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "reviews/wcir_next_print")
    parser.add_argument("--file-order", choices=("forward", "reverse", "chunked"), default="forward")
    args = parser.parse_args()
    run_start_commit = _git_head()

    stage2 = args.output_root / "stage_02_rev2"
    stage3 = args.output_root / "stage_03_rev2"
    evidence2 = stage2 / "evidence"
    evidence3 = stage3 / "evidence"
    evidence2.mkdir(parents=True, exist_ok=True)
    evidence3.mkdir(parents=True, exist_ok=True)

    events = load_frozen_events(args.frozen_events)
    latency_rows, latency_contract, duplicate_event_audit = freeze_latency_inputs(
        args.runtime_root / "output/fast_source_prev_no_trial/events.jsonl", events
    )
    official = load_official_history(args.runtime_root)
    forecast_rows, forecast_manifest = freeze_forecasts(args.runtime_root, events)
    universes, market_identities, rest_manifest = scan_rest_market_identity(args.runtime_root, events)
    epochs, epoch_files = load_subscription_epochs(args.runtime_root)
    queries = build_checkpoint_queries(events, universes, float(latency_contract["value_seconds"]))
    alignments, archive_manifest, lineage_sample = replay_ws_archive(
        args.runtime_root, epochs, queries, file_order=args.file_order
    )
    coverage = summarize_alignment_coverage(events, alignments)
    root_cause = coverage_root_cause(events, alignments, archive_manifest)
    parity = compute_rest_ws_parity(args.runtime_root, alignments)
    oracle_rows, envelope_rows, pnl_audit = build_oracle_results(
        events, universes, alignments, latency_contract
    )
    city_gates, baselines, statistics_report, baseline_rows = summarize_stage3(
        events, universes, alignments, oracle_rows, forecast_rows, official
    )
    reaction_rows, reaction_summary = build_reaction_windows(oracle_rows, alignments)

    latency_path = evidence2 / "FROZEN_PIPELINE_LATENCY_ROWS.jsonl.gz"
    universe_path = evidence2 / "FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz"
    market_identity_path = evidence2 / "FROZEN_MARKET_TOKEN_IDENTITIES.jsonl.gz"
    alignment_path = evidence2 / "EVENT_BOOK_COVERAGE_MATRIX.jsonl.gz"
    oracle_path = evidence3 / "PRIMARY_ORACLE_ROWS.jsonl.gz"
    envelope_path = evidence3 / "EX_POST_ENVELOPE_ROWS.jsonl.gz"
    forecast_path = evidence3 / "FROZEN_FORECAST_BASELINE_ROWS.jsonl.gz"
    baseline_path = evidence3 / "MATCHED_BASELINE_ROWS.jsonl.gz"
    reaction_path = evidence3 / "REACTION_WINDOW_ROWS.jsonl.gz"
    write_jsonl_gz(latency_path, latency_rows)
    write_jsonl_gz(universe_path, universes)
    write_jsonl_gz(market_identity_path, market_identities)
    write_jsonl_gz(alignment_path, alignments)
    write_jsonl_gz(oracle_path, oracle_rows)
    write_jsonl_gz(envelope_path, envelope_rows)
    write_jsonl_gz(forecast_path, forecast_rows)
    write_jsonl_gz(baseline_path, baseline_rows)
    write_jsonl_gz(reaction_path, reaction_rows)

    write_json(stage2 / "COLLECTOR_AND_INPUT_FREEZE.json", {
        "runner_observed_commit_before_input_scan": run_start_commit,
        "frozen_legacy_event_dataset": file_identity(args.frozen_events, row_count=len(events)),
        "latency_contract": latency_contract,
        "latency_rows": file_identity(latency_path, row_count=len(latency_rows)),
        "collector_change_authorized": False,
        "collector_or_production_files_modified_by_runner": False,
    })
    stage0_duplicate_audit = ROOT / "reviews/wcir_next_print/stage_00_rev2/DUPLICATE_CANDIDATE_IMMUTABILITY_AUDIT.json"
    write_json(stage2 / "DUPLICATE_EVENT_IMMUTABILITY_AUDIT.json", {
        **duplicate_event_audit,
        "stage_00_candidate_audit": (
            file_identity(stage0_duplicate_audit)
            if stage0_duplicate_audit.is_file()
            else None
        ),
    })
    write_json(stage2 / "FULL_ARCHIVE_REPLAY_MANIFEST.json", {
        **archive_manifest,
        "subscription_epoch_files": epoch_files,
        "market_identity_manifest": rest_manifest,
        "frozen_market_universe": file_identity(universe_path, row_count=len(universes)),
        "frozen_market_token_identities": file_identity(market_identity_path, row_count=len(market_identities)),
    })
    write_json(stage2 / "EVENT_ALIGNED_BOOK_COVERAGE.json", coverage)
    write_json(stage2 / "COVERAGE_ROOT_CAUSE_DIAGNOSIS.json", root_cause)
    write_json(stage2 / "REST_WS_COMPARABLE_PARITY.json", parity)
    write_json(stage2 / "UNIQUE_FAILURE_REASON_AUDIT.json", {
        "allowed_primary_statuses": sorted(FAILURE_REASONS),
        "unknown_count": coverage["unknown_primary_status_count"],
        "exactly_one_primary_status_per_row": True,
        "invalid_and_execution_infeasible_mutually_exclusive": coverage["invalid_and_execution_infeasible_overlap_count"] == 0,
        "source_t0_primary_status_counts": coverage["source_t0_primary_status_counts"],
    })
    write_json(stage2 / "RAW_LINEAGE_RANDOM_SAMPLE.json", {
        "seed": 20260827,
        "sample_count": len(lineage_sample),
        "rows": lineage_sample,
    })
    write_json(stage2 / "DETERMINISM_IDENTITY_COMPARISON.json", {
        "current_run_file_order": args.file_order,
        "raw_frame_ordered_identity": archive_manifest["raw_frame_ordered_identity"],
        "coverage_identity": archive_manifest["coverage_identity"],
        "required_orders": ["forward", "reverse", "chunked"],
        "status": "pending_cross_run_comparison",
    })
    write_json(stage3 / "TWO_SIDED_PRIMARY_ORACLE_RESULTS.json", {
        "primary_contract": pnl_audit["summary"]["primary_contract"],
        "event_rows": len(oracle_rows),
        "paired_executable_rows": sum(row["paired_executable"] for row in oracle_rows),
        "row_file": file_identity(oracle_path, row_count=len(oracle_rows)),
        "future_price_action_invariance": pnl_audit["summary"]["action_future_price_column_deletion_invariance"],
        "disposition": "CONTINUE_COLLECTION_WITHOUT_MODELING",
    })
    write_json(stage3 / "EX_POST_ENVELOPE_MANIFEST.json", {
        "label": "UNATTAINABLE_EX_POST_BEST_CONTRACT_ENVELOPE_NOT_FOR_PROMOTION",
        "row_file": file_identity(envelope_path, row_count=len(envelope_rows)),
    })
    write_json(stage3 / "INDEPENDENT_PNL_RECALCULATION.json", pnl_audit)
    write_json(stage3 / "MATCHED_BASELINES_AND_ROW_INTERSECTION.json", {
        **baselines,
        "row_file": file_identity(baseline_path, row_count=len(baseline_rows)),
    })
    write_json(stage3 / "REACTION_INFERENCE_AND_CONCENTRATION.json", {
        **statistics_report,
        "reaction_summary": reaction_summary,
        "row_file": file_identity(reaction_path, row_count=len(reaction_rows)),
    })
    write_json(stage3 / "CITY_GATES.json", city_gates)
    write_json(stage3 / "FORECAST_BASELINE_INPUT_MANIFEST.json", {
        **forecast_manifest,
        "frozen_rows": file_identity(forecast_path, row_count=len(forecast_rows)),
    })
    write_json(
        stage2 / "EVIDENCE_MANIFEST.json",
        evidence_manifest(stage2, exclude={"EVIDENCE_MANIFEST.json"}),
    )
    write_json(
        stage3 / "EVIDENCE_MANIFEST.json",
        evidence_manifest(stage3, exclude={"EVIDENCE_MANIFEST.json"}),
    )

    print(
        json.dumps(
            {
                "file_order": args.file_order,
                "ws_files": archive_manifest["ws_file_count"],
                "raw_frames": archive_manifest["raw_frame_count"],
                "events": len(events),
                "alignment_rows": len(alignments),
                "source_t0_book_valid_rate": coverage["source_t0_book_valid_rate"],
                "paired_primary_oracle_rows": sum(row["paired_executable"] for row in oracle_rows),
                "coverage_identity": archive_manifest["coverage_identity"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _git_head() -> str:
    import subprocess

    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(main())
