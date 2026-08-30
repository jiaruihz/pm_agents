#!/usr/bin/env python3
"""Independent, fail-closed Cross NO V2 (METAR) probe runner.

This runner consumes append-only lab source events and an already-owned market
book snapshot.  It never starts a collector or a market websocket.  A source
cross is only an *information proxy*, never a settlement invalidation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_fast_source_execution import (
    build_live_taker_gtc_place_fn,
    exact_share_taker_intent,
    extract_order_id,
    submit_marketable_gtc,
)
from scripts.ops.weather_fast_source_stale_book_observer import fetch_fresh_book
from src.platform.market_data.capture_demand import CaptureDemand
from weather_data_feed.market_brackets import parse_market_bracket


STRATEGY_ID = "cross_no_v2_metar_v1"
SOURCE_ARMS = frozenset({"metar_ws_datis", "metar_ws_hfmetar", "metar_ws_metar"})
SHARES_PER_ORDER = 5.0
MAX_ORDERS_PER_UTC_DAY = 5
MAX_DAILY_SHARES = 25.0
MAX_ORDER_PRINCIPAL_USD = 5.0
MAX_DAILY_PRINCIPAL_USD = 25.0
MAX_NO_ASK = 0.97
WEATHER_TAKER_FEE_RATE = 0.05
CAPTURE_CHECKPOINTS = (0, 15, 30, 60, 120, 300)
# Deliberately omit KORD: the current Chicago contract basis is not KORD.
DEFAULT_CITY_STATIONS = {
    "Atlanta": "KATL", "Austin": "KAUS", "Boston": "KBOS", "Dallas": "KDAL",
    "Houston": "KHOU", "LA": "KLAX", "Miami": "KMIA", "NYC": "KLGA",
    "SanFrancisco": "KSFO", "Seattle": "KSEA",
}
CITY_TIMEZONES = {
    "Atlanta": "America/New_York", "Austin": "America/Chicago", "Boston": "America/New_York",
    "Dallas": "America/Chicago", "Houston": "America/Chicago", "LA": "America/Los_Angeles",
    "Miami": "America/New_York", "NYC": "America/New_York", "SanFrancisco": "America/Los_Angeles",
    "Seattle": "America/Los_Angeles",
}
SQLITE_SOURCE_MAP = {
    "METAR_WS_METAR": "metar_ws_metar", "METAR_WS_HFMETAR": "metar_ws_hfmetar", "METAR_WS_DATIS": "metar_ws_datis",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def append_jsonl(path: Path, row: Mapping[str, Any], *, durable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        if durable:
            os.fsync(handle.fileno())


def jsonl_rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                yield dict(value)


def event_paths(root: Path) -> tuple[Path, ...]:
    # Day-partitioned source rows carry the transport/clock and temperature
    # fields required for a live decision.  The generic information ledger is
    # retained only as a replay fallback when that projection is unavailable.
    partitions = tuple(sorted(root.glob("*/sources.jsonl")))
    if partitions:
        return partitions
    direct = root / "information_events.jsonl"
    return (direct,) if direct.exists() else ()


def _load_cursor(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(value), sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_strategy_state(path: Path) -> dict[str, Any]:
    return _load_cursor(path)


def _save_strategy_state(path: Path, value: Mapping[str, Any]) -> None:
    _save_cursor(path, value)


def enforce_experiment_deadline(
    output_dir: Path, *, pause_file: Path, stop_after_sec: float
) -> dict[str, Any]:
    control_path = output_dir / "experiment_control.json"
    control = _load_cursor(control_path)
    if not control:
        started = utc_now()
        control = {
            "schema_version": "cross_no_v2_metar_experiment_control_v1",
            "started_at_utc": iso(started),
            "stop_after_sec": float(stop_after_sec),
            "ends_at_utc": iso(started + timedelta(seconds=float(stop_after_sec))),
        }
        _save_cursor(control_path, control)
    ends = parse_utc(control.get("ends_at_utc"))
    expired = ends is None or utc_now() >= ends
    if expired and not pause_file.exists():
        pause_file.parent.mkdir(parents=True, exist_ok=True)
        pause_file.write_text(
            json.dumps(
                {
                    "reason": "one_day_probe_deadline_reached",
                    "paused_at_utc": iso(),
                    "experiment_control": str(control_path),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return {**control, "expired": expired, "pause_file": str(pause_file)}


def ensure_research_record(
    output_dir: Path,
    *,
    code_identity: str,
    evidence_db: Path | None,
    market_books_latest: Path,
) -> Path:
    path = output_dir / "research_record.json"
    if path.exists():
        return path
    payload = {
        "schema_version": "pm_agents_research_record_v1",
        "record_id": "research:weather:cross_no_v2_metar:cross_no_v2_metar_one_day_live_probe_v1",
        "run_id": "cross_no_v2_metar_one_day_live_probe_v1",
        "domain": "weather",
        "family": "cross_no_v2_metar",
        "skill": "weather-strategy-research",
        "lifecycle_status": "running",
        "observed_at_utc": None,
        "question": {
            "hypothesis": "The first eligible METAR.ws source cross can buy five shares of the prior official bracket NO before the public market fully reprices.",
            "decision_target": "Whether METAR.ws merits a longer paid executable-alpha study; one day cannot establish durable alpha.",
            "scope": "One pre-registered 24-hour U.S. station-aligned forward probe; METAR.ws official, HF-METAR and D-ATIS arms remain separate.",
            "exclusions": [
                "orders above five shares",
                "more than one order per city-target-date",
                "more than five orders or 25 USD reserved principal per UTC day",
                "treating a source cross as settlement truth",
                "formal cross-source latency ranking while the wall clock contract is invalid",
            ],
        },
        "method": {
            "grain": "first post-watermark source information event x city x target_date x prior official bracket",
            "denominator_scope": "Every post-watermark event from the fixed ten-city station allowlist, including blocked, no-cross, no-book, no-fill and terminal-false rows.",
            "evidence_layers": [
                "append-only METAR.ws transport/observation SQLite",
                "decision-time fresh CLOB REST full-depth sweep",
                "shared-owner public WS markout capture demand",
                "private order response and later authenticated fill reconciliation",
            ],
            "pit_or_asof_policy": "No pre-start backlog. Formal clock-valid events are eligible; the explicit same-boot monotonic override remains exploratory and is never formal latency evidence.",
            "label_contract": "METAR.ws/D-ATIS/HF crosses are proxy source events, not WU settlement invalidations; Atlanta 2026-07-17 remains a terminal-false control.",
            "primary_metrics": [
                "signals and blockers by source arm",
                "five-share executable coverage and fill rate",
                "15/30/60/120/300 second fee-adjusted markout",
                "terminal-false rate and eventual settlement PnL",
            ],
            "baselines": [
                "later METAR.ws official arm",
                "AWC paired receipt",
                "same-token decision-time executable market price",
            ],
            "forward_policy": "Freeze source arms, allowlist, five-share sizing and caps for 24 hours; do not tune from intraday anecdotes.",
            "acceptance_gates": [
                "one day is probe evidence only and cannot confirm alpha",
                "paid adoption still requires the existing 72h/7d, 500-pair, two-vantage and clock-valid gates",
                "every real order must have a durable pre-submit reservation and fresh five-share depth",
            ],
            "fee_and_execution_basis": "Five-share marketable GTC capped at 0.97 worst ask; Weather taker fee shares*0.05*p*(1-p); no maker child and no retry after a reserved attempt.",
        },
        "inputs": [
            {
                "input_id": "metar_ws_append_only_evidence",
                "kind": "sqlite_append_only_transport_evidence",
                "locator": (
                    "runtime://us_fast_weather_lab_metarws_24h_20260830_r2/evidence.sqlite3"
                    if evidence_db else "runtime://cross_no_v2_metar/source_events_replay"
                ),
                "identity": "explicit single collector run fenced by run_id/start and rowid watermark",
                "coverage": "post-runner-start events only",
                "observed_at_utc": None,
            },
            {
                "input_id": "polymarket_market_books",
                "kind": "rest_map_plus_fresh_book_and_shared_ws",
                "locator": "production://weather_market_books/latest.json",
                "identity": "production weather_market_books sole owner",
                "coverage": "fixed U.S. event ladders and event-triggered markout demand",
                "observed_at_utc": None,
            },
        ],
        "execution": {
            "producer": "scripts/ops/weather_cross_no_v2_metar.py",
            "code_identity": code_identity,
            "config_locator": "src/strategies/runtime/production.yaml",
            "config_identity": "cross_no_v2_metar_live_probe_caps_v1",
            "reproduce_command": "scripts/ops/start_weather_cross_no_v2_metar.sh (controller-managed registered release only)",
        },
        "outputs": {
            "artifact_root_contract": "production://research_artifact_root",
            "artifact_manifest": "production://cross_no_v2_metar_v1/artifact_manifest.json",
            "canonical_machine_format": "jsonl",
            "compact_summary_locator": "production://cross_no_v2_metar_v1/latest.json",
        },
        "knowledge": {
            "family_living_doc": "docs/WEATHER_FIRST_SEEN_INFORMATION_LINEAGE.md",
            "registry_or_index": "docs/WEATHER_STRATEGY_REGISTRY.md",
            "dated_snapshot": None,
            "durable_conclusion": None,
            "action": "Run one bounded five-share live probe; do not promote or purchase from one-day evidence alone.",
            "superseded_record_ids": [],
        },
    }
    _save_cursor(path, payload)
    return path


def direct_evidence_events(
    evidence_db: Path, *, cursor_path: Path, allowlist: Mapping[str, str],
    collector_run_start_wall_ns: int | None = None, initialize_at_current: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read only new METAR.ws arrivals after a durable SQLite rowid watermark.

    The first invocation deliberately starts at the then-current maximum rowid.
    That prevents historical/cached startup rows from becoming live candidates.
    A collector launch wall-clock is mandatory: it is a second fence against an
    accidental old-run attachment.
    """
    state = _load_cursor(cursor_path)
    db_uri = f"file:{evidence_db.resolve()}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run_rows = conn.execute(
            "SELECT run_id, started_at_ns FROM collector_run ORDER BY started_at_ns DESC"
        ).fetchall()
        if len(run_rows) != 1:
            raise ValueError(f"direct evidence requires exactly one collector_run, got {len(run_rows)}")
        run_id = str(run_rows[0]["run_id"])
        observed_run_start_ns = int(run_rows[0]["started_at_ns"])
        if collector_run_start_wall_ns is None:
            collector_run_start_wall_ns = observed_run_start_ns
        if int(collector_run_start_wall_ns) != observed_run_start_ns:
            raise ValueError("collector_run_start_wall_ns does not match evidence DB")
        ended = int(
            conn.execute(
                "SELECT COUNT(*) FROM collector_run_end WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
        if ended:
            raise ValueError("collector run has ended; rotate through a new explicit evidence DB")
        latest_clock = conn.execute(
            "SELECT uncertainty_ms FROM clock_health WHERE run_id = ? ORDER BY sampled_at_ns DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        clock_uncertainty_ms = safe_float(latest_clock[0]) if latest_clock else None
        maximum = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM source_observation_seen").fetchone()[0])
        if "source_seen_rowid" not in state:
            watermark = maximum if initialize_at_current else 0
            return [], {"source_seen_rowid": watermark, "collector_run_start_wall_ns": collector_run_start_wall_ns,
                        "collector_run_id": run_id, "initialized_at_utc": iso(),
                        "initialization": "current_max_rowid" if initialize_at_current else "from_start"}
        if int(state.get("collector_run_start_wall_ns") or 0) != int(collector_run_start_wall_ns):
            raise ValueError("direct evidence cursor belongs to a different collector run")
        if str(state.get("collector_run_id") or "") != run_id:
            raise ValueError("direct evidence cursor run_id mismatch")
        cursor = int(state["source_seen_rowid"])
        query = """
            SELECT s.rowid AS source_seen_rowid, s.source_seen_id, s.source_id AS seen_source_id,
                   s.first_actionable_seen_at_ns, s.clock_offset_ms AS seen_clock_offset_ms, s.clock_valid AS seen_clock_valid,
                   s.raw_payload_sha256 AS seen_raw_payload_sha256, e.event_family_id, e.raw_report_id,
                   e.semantic_version_id, e.station_id, e.report_kind, e.observation_time, e.is_correction,
                   e.air_temperature_c, t.transport_message_id, t.received_wall_ns, t.received_monotonic_ns,
                   t.clock_valid AS transport_clock_valid, t.clock_offset_ms AS transport_clock_offset_ms,
                   t.raw_payload_sha256, t.raw_payload_path
              FROM source_observation_seen s
              JOIN observation_event e ON e.observation_version_id=s.observation_version_id
              JOIN transport_message t ON t.transport_message_id=s.transport_message_id
             WHERE s.rowid > ? AND s.evidence_status='actionable'
               AND UPPER(s.source_id) IN ('METAR_WS_METAR','METAR_WS_HFMETAR','METAR_WS_DATIS')
               AND t.received_wall_ns >= ? AND t.run_id = ?
             ORDER BY s.rowid
        """
        rows = [
            dict(row)
            for row in conn.execute(
                query, (cursor, int(collector_run_start_wall_ns), run_id)
            )
        ]
    finally:
        conn.close()
    station_cities = {station.upper(): city for city, station in allowlist.items()}
    events: list[dict[str, Any]] = []
    for row in rows:
        city = station_cities.get(str(row.get("station_id") or "").upper())
        source = SQLITE_SOURCE_MAP.get(str(row.get("seen_source_id") or "").upper())
        if not city or not source:
            continue
        received_ns = int(row["received_wall_ns"])
        received = datetime.fromtimestamp(received_ns / 1_000_000_000, tz=timezone.utc)
        observation = parse_utc(row.get("observation_time")) or received
        target_date = observation.astimezone(ZoneInfo(CITY_TIMEZONES[city])).date().isoformat()
        clock_valid = bool(row.get("seen_clock_valid")) and bool(row.get("transport_clock_valid"))
        events.append({
            "information_event_id": f"sqlite:{row['source_seen_id']}", "event_role": "revision" if row.get("is_correction") else "new_content",
            "source": source, "city": city, "target_date": target_date, "station": row["station_id"],
            "temp_c": row.get("air_temperature_c"), "event_family_id": row["event_family_id"],
            "semantic_version_id": row["semantic_version_id"], "raw_report_id": row.get("raw_report_id"),
            "transport_received_at_utc": iso(received), "transport_received_monotonic_ns": row["received_monotonic_ns"],
            "source_event_ts_utc": row.get("observation_time"), "clock_valid": clock_valid, "pit_eligible": clock_valid,
            "clock_offset_ms": row.get("seen_clock_offset_ms"), "raw_payload_hash": row.get("raw_payload_sha256") or row.get("seen_raw_payload_sha256"),
            "raw_source_path": row.get("raw_payload_path"), "source_seen_rowid": row["source_seen_rowid"],
            "transport_message_id": row["transport_message_id"],
            "collector_run_id": run_id,
            "clock_uncertainty_ms": clock_uncertainty_ms,
            "evidence_incremental_after_start_watermark": True,
        })
    next_state = {"source_seen_rowid": max([int(row["source_seen_rowid"]) for row in rows], default=cursor),
                  "collector_run_start_wall_ns": collector_run_start_wall_ns,
                  "collector_run_id": run_id, "updated_at_utc": iso(), "initialization": "incremental"}
    return events, next_state


def _book_records(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = value.get("records", value) if isinstance(value, Mapping) else value
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _event_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    mono = row.get("transport_received_monotonic_ns")
    try:
        return int(mono), str(row.get("information_event_id") or "")
    except (TypeError, ValueError):
        return (2**63 - 1, str(row.get("information_event_id") or ""))


def _market_value(temp_c: float, market: Mapping[str, Any]) -> float | None:
    unit = str(market.get("market_unit") or market.get("unit") or "F").upper()
    if unit == "C":
        return temp_c
    if unit == "F":
        return temp_c * 9.0 / 5.0 + 32.0
    return None


def _no_market_for_value(records: Iterable[Mapping[str, Any]], value: float) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for raw in records:
        if str(raw.get("outcome") or "").lower() != "no":
            continue
        bracket = parse_market_bracket(str(raw.get("bracket") or ""), str(raw.get("question") or ""))
        if bracket is not None and bracket.contains(value):
            candidates.append(dict(raw))
    return candidates[0] if len(candidates) == 1 else None


def _book_summary(market: Mapping[str, Any]) -> tuple[float | None, float | None, bool]:
    book = market.get("book") if isinstance(market.get("book"), Mapping) else market
    summary = book.get("summary") if isinstance(book.get("summary"), Mapping) else book
    raw = book.get("raw") if isinstance(book.get("raw"), Mapping) else market.get("raw")
    asks = raw.get("asks") if isinstance(raw, Mapping) else None
    parsed_asks: list[tuple[float, float]] = []
    if isinstance(asks, list):
        for level in asks:
            if not isinstance(level, Mapping):
                continue
            price, size = safe_float(level.get("price")), safe_float(level.get("size"))
            if price is not None and size is not None and 0 < price < 1 and size > 0:
                parsed_asks.append((price, size))
        parsed_asks.sort()
    best_ask = safe_float(summary.get("best_ask"))
    ask_size = safe_float(summary.get("ask_size"))
    if parsed_asks:
        best_ask, ask_size = parsed_asks[0]
    full_depth = bool(
        book.get("full_depth_valid", summary.get("full_depth_valid", False))
        or isinstance(asks, list)
    )
    return best_ask, ask_size, full_depth


def _five_share_sweep(book: Mapping[str, Any]) -> dict[str, Any]:
    summary = book.get("summary") if isinstance(book.get("summary"), Mapping) else {}
    raw = book.get("raw") if isinstance(book.get("raw"), Mapping) else {}
    asks = raw.get("asks") if isinstance(raw.get("asks"), list) else []
    levels: list[tuple[float, float]] = []
    for level in asks:
        if not isinstance(level, Mapping):
            continue
        price, size = safe_float(level.get("price")), safe_float(level.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    levels.sort()
    if not levels:
        best_ask = safe_float(summary.get("best_ask"))
        ask_size = safe_float(summary.get("ask_size"))
        if best_ask is not None and ask_size is not None:
            levels = [(best_ask, ask_size)]
    remaining = SHARES_PER_ORDER
    cost = 0.0
    used: list[dict[str, float]] = []
    for price, size in levels:
        take = min(remaining, size)
        if take <= 0:
            continue
        used.append({"price": price, "shares": take})
        cost += take * price
        remaining -= take
        if remaining <= 1e-9:
            break
    return {
        "covered": remaining <= 1e-9,
        "best_ask": levels[0][0] if levels else None,
        "top_ask_size": levels[0][1] if levels else None,
        "worst_ask": used[-1]["price"] if used else None,
        "vwap": cost / SHARES_PER_ORDER if remaining <= 1e-9 else None,
        "principal_usd": cost if remaining <= 1e-9 else None,
        "levels": used,
    }


def _expected_weather_taker_fee(shares: float, price: float) -> float:
    return round(float(shares) * WEATHER_TAKER_FEE_RATE * float(price) * (1.0 - float(price)), 5)


def _capture_demand(
    *, row: Mapping[str, Any], condition_id: str, token_id: str, now: datetime
) -> dict[str, Any]:
    return CaptureDemand.create(
        consumer_id="cross_no_v2_metar_v1",
        strategy_key="weather.cross_no_v2_metar_v1",
        condition_id=condition_id,
        token_id=token_id,
        reason="cross_no_v2_metar_execution_markout",
        priority="P0",
        requested_at_utc=iso(now),
        expires_at_utc=iso(now + timedelta(seconds=360)),
        desired_transport="REST_WS",
        requested_checkpoints_seconds=CAPTURE_CHECKPOINTS,
        trigger_event_id=str(row.get("information_event_id") or ""),
        metadata={
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "source": row.get("source"),
            "event_family_id": row.get("event_family_id"),
            "semantic_version_id": row.get("semantic_version_id"),
            "formal_latency_rank_eligible": bool(row.get("formal_latency_rank_eligible")),
            "execution_clock_mode": row.get("execution_clock_mode"),
            "public_trade_is_own_fill": False,
        },
    ).to_dict()


def _source_age_seconds(row: Mapping[str, Any], now: datetime) -> float | None:
    received = parse_utc(row.get("transport_received_at_utc"))
    return None if received is None else (now - received).total_seconds()


def _base_row(row: Mapping[str, Any], *, now: datetime, execution_clock_mode: str = "formal_clock_valid_v1") -> dict[str, Any]:
    return {
        "schema_version": "cross_no_v2_metar_opportunity_v1",
        "strategy_id": STRATEGY_ID,
        "created_at_utc": iso(now),
        "created_at_monotonic_ns": time.monotonic_ns(),
        "city": row.get("city"), "target_date": row.get("target_date"),
        "station": row.get("station") or row.get("station_id"), "source": row.get("source"),
        "information_event_id": row.get("information_event_id"),
        "event_family_id": row.get("event_family_id"),
        "semantic_version_id": row.get("semantic_version_id"),
        "raw_report_id": row.get("raw_report_id"),
        "transport_received_at_utc": row.get("transport_received_at_utc"),
        "transport_received_monotonic_ns": row.get("transport_received_monotonic_ns"),
        "source_event_ts_utc": row.get("source_event_ts_utc"),
        "source_cross_is_proxy_only": True,
        "settlement_hard_invalidation": False,
        "terminal_false_cross": bool(row.get("terminal_false_cross", False)),
        "formal_latency_rank_eligible": execution_clock_mode == "formal_clock_valid_v1",
        "execution_clock_mode": execution_clock_mode,
    }


def _eligible_event(
    row: Mapping[str, Any], *, allowlist: Mapping[str, str], now: datetime, max_source_age_sec: float,
    allow_clock_invalid_same_boot_monotonic_probe: bool, live: bool, confirm_live: bool,
    clock_uncertainty_ms: float | None, boot_monotonic_start_ns: int | None, monotonic_now_ns: int,
) -> tuple[list[str], str]:
    blockers: list[str] = []
    if str(row.get("source")) not in SOURCE_ARMS:
        blockers.append("source_not_in_v2_arms")
    if str(row.get("event_role")) not in {"new_content", "revision"}:
        blockers.append("event_role_not_content_or_revision")
    formal_clock = bool(row.get("clock_valid")) and bool(row.get("pit_eligible"))
    clock_mode = "formal_clock_valid_v1"
    if not formal_clock:
        if not allow_clock_invalid_same_boot_monotonic_probe:
            blockers.append("clock_or_pit_not_eligible")
        elif not (live and confirm_live):
            blockers.append("clock_invalid_override_requires_live_confirmation")
        else:
            clock_mode = "same_boot_monotonic_exploratory_v1"
            received_mono = row.get("transport_received_monotonic_ns")
            try:
                mono_age = monotonic_now_ns - int(received_mono)
            except (TypeError, ValueError):
                mono_age = -1
            if not bool(row.get("evidence_incremental_after_start_watermark")):
                blockers.append("clock_override_requires_direct_post_watermark_event")
            if boot_monotonic_start_ns is None or int(received_mono or -1) < int(boot_monotonic_start_ns) or int(received_mono or -1) > monotonic_now_ns:
                blockers.append("clock_override_not_current_boot")
            if not (0 <= mono_age <= 15_000_000_000):
                blockers.append("clock_override_monotonic_age_out_of_bounds")
            wall_age = _source_age_seconds(row, now)
            if wall_age is None or not (-1.0 <= wall_age < 30.0):
                blockers.append("clock_override_wall_age_out_of_bounds")
            offset = safe_float(row.get("clock_offset_ms"))
            if offset is None or abs(offset) >= 500.0:
                blockers.append("clock_override_offset_out_of_bounds")
            if clock_uncertainty_ms is None or not (0 <= clock_uncertainty_ms < 500.0):
                blockers.append("clock_override_uncertainty_unverified_or_out_of_bounds")
    city, station = str(row.get("city") or ""), str(row.get("station") or row.get("station_id") or "").upper()
    if not city or allowlist.get(city) != station:
        blockers.append("city_station_not_in_fixed_allowlist")
    if safe_float(row.get("temp_c")) is None:
        blockers.append("temperature_missing")
    age = _source_age_seconds(row, now)
    if age is None or age < -1.0 or age > max_source_age_sec:
        blockers.append("source_age_out_of_bounds")
    if row.get("transport_received_monotonic_ns") is None:
        blockers.append("transport_monotonic_missing")
    return blockers, clock_mode


def _existing_keys(output_dir: Path) -> tuple[set[str], set[tuple[str, str]], dict[str, int], dict[str, float]]:
    seen: set[str] = set()
    city_dates: set[tuple[str, str]] = set()
    order_count: dict[str, int] = {}
    principal: dict[str, float] = {}
    paths = (
        tuple(sorted(output_dir.glob("*/execution_attempts.jsonl")))
        + tuple(sorted(output_dir.glob("*/orders.jsonl")))
        + (output_dir / "execution_attempts.jsonl", output_dir / "orders.jsonl")
    )
    counted_attempts: set[str] = set()
    for row in jsonl_rows(paths):
        if str(row.get("strategy_id")) != STRATEGY_ID:
            continue
        event_key = str(row.get("execution_race_key") or "")
        if event_key:
            seen.add(event_key)
        city_dates.add((str(row.get("city")), str(row.get("target_date"))))
        attempt_id = str(row.get("execution_attempt_id") or event_key)
        if not attempt_id or attempt_id in counted_attempts:
            continue
        counted_attempts.add(attempt_id)
        day = str(row.get("created_at_utc") or "")[:10]
        order_count[day] = order_count.get(day, 0) + 1
        principal[day] = principal.get(day, 0.0) + float(
            row.get("reserved_principal_usd") or row.get("submitted_notional_usd") or 0.0
        )
    return seen, city_dates, order_count, principal


def run_probe(
    *, source_events_root: Path, market_books_latest: Path, output_dir: Path,
    live: bool = False, confirm_live: bool = False, place_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None, max_source_age_sec: float = 180.0,
    official_fee_rate: float | None = WEATHER_TAKER_FEE_RATE,
    official_fee_bps: float | None = None,
    allowlist: Mapping[str, str] = DEFAULT_CITY_STATIONS, pause_file: Path | None = None,
    events_override: Iterable[Mapping[str, Any]] | None = None,
    allow_clock_invalid_same_boot_monotonic_probe: bool = False, clock_uncertainty_ms: float | None = None,
    boot_monotonic_start_ns: int | None = None, monotonic_now_ns: int | None = None,
    fetch_book_fn: Callable[..., dict[str, Any]] | None = None,
    market_proxy: str = "", book_timeout_sec: float = 5.0,
) -> dict[str, Any]:
    """Process a finite replay once; all rejects are emitted as opportunities."""
    now = now or utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    live_enabled = bool(live and confirm_live)
    if live_enabled and place_fn is None:
        raise ValueError("live execution requires injected place_fn")
    paused = bool(pause_file and pause_file.exists())
    monotonic_now_ns = monotonic_now_ns if monotonic_now_ns is not None else time.monotonic_ns()
    if official_fee_bps is not None:
        official_fee_rate = float(official_fee_bps) / 10_000.0
    if official_fee_rate is None or abs(float(official_fee_rate) - WEATHER_TAKER_FEE_RATE) > 1e-12:
        raise ValueError(f"official weather taker fee rate must equal {WEATHER_TAKER_FEE_RATE}")
    books = _book_records(market_books_latest)
    by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for market in books:
        key = (str(market.get("city") or ""), str(market.get("event_date") or market.get("target_date") or ""))
        by_city_date.setdefault(key, []).append(market)
    seen_races, city_orders, daily_counts, daily_principal = _existing_keys(output_dir)
    events = sorted((dict(row) for row in events_override) if events_override is not None else jsonl_rows(event_paths(source_events_root)), key=_event_sort_key)
    strategy_state_path = output_dir / "state.json"
    strategy_state = _load_strategy_state(strategy_state_path)
    official_max = {
        tuple(key.split("|", 1)): float(value)
        for key, value in dict(strategy_state.get("official_max") or {}).items()
        if "|" in key and safe_float(value) is not None
    }
    source_max = {
        tuple(key.split("|", 2)): float(value)
        for key, value in dict(strategy_state.get("source_max") or {}).items()
        if key.count("|") == 2 and safe_float(value) is not None
    }
    source_crossed_brackets = {
        tuple(str(value).split("|", 3))
        for value in strategy_state.get("source_crossed_brackets") or []
        if str(value).count("|") == 3
    }
    processed_ids = set(map(str, strategy_state.get("processed_event_ids") or []))
    emitted = orders = fills = blocked = 0

    for event in events:
        event_id = str(event.get("information_event_id") or "")
        if not event_id or event_id in processed_ids:
            continue
        processed_ids.add(event_id)
        blockers, clock_mode = _eligible_event(
            event, allowlist=allowlist, now=now, max_source_age_sec=max_source_age_sec,
            allow_clock_invalid_same_boot_monotonic_probe=allow_clock_invalid_same_boot_monotonic_probe,
            live=live, confirm_live=confirm_live,
            clock_uncertainty_ms=(
                safe_float(event.get("clock_uncertainty_ms"))
                if safe_float(event.get("clock_uncertainty_ms")) is not None
                else clock_uncertainty_ms
            ),
            boot_monotonic_start_ns=boot_monotonic_start_ns, monotonic_now_ns=monotonic_now_ns,
        )
        common = _base_row(event, now=now, execution_clock_mode=clock_mode)
        city_date = (str(event.get("city") or ""), str(event.get("target_date") or ""))
        markets = by_city_date.get(city_date, [])
        temp_c = safe_float(event.get("temp_c"))
        if not blockers and not markets:
            blockers.append("market_not_found")
        market_value = _market_value(temp_c, markets[0]) if temp_c is not None and markets else None
        if not blockers and market_value is None:
            blockers.append("market_unit_unsupported")

        # METAR arm defines the official running-max basis after any candidate
        # is evaluated, preserving the previous official value for the cross.
        prior_official = official_max.get(city_date)
        if blockers:
            append_jsonl(output_dir / "opportunities.jsonl", {**common, "status": "blocked", "blockers": blockers})
            emitted += 1; blocked += 1
            continue
        assert market_value is not None
        source_key = (city_date[0], city_date[1], str(event["source"]))
        prior_source = source_max.get(source_key, -math.inf)
        source_max[source_key] = max(prior_source, market_value)
        if prior_official is None:
            if str(event["source"]) == "metar_ws_metar":
                official_max[city_date] = max(official_max.get(city_date, -math.inf), market_value)
            append_jsonl(output_dir / "opportunities.jsonl", {**common, "status": "blocked", "blockers": ["prior_official_running_max_missing"]})
            emitted += 1; blocked += 1
            continue
        prior_market = _no_market_for_value(markets, prior_official)
        source_bracket = _no_market_for_value(markets, source_max[source_key])
        if prior_market is None or source_bracket is None:
            row = {**common, "status": "blocked", "prior_official_running_max": prior_official,
                   "source_running_max": source_max[source_key], "blockers": ["exact_condition_or_no_token_unverified"]}
            append_jsonl(output_dir / "opportunities.jsonl", row); emitted += 1; blocked += 1
            continue
        bracket = str(prior_market.get("bracket"))
        cross_key = (source_key[0], source_key[1], source_key[2], bracket)
        candidate_blockers: list[str] = []
        source_crossed = bool(
            source_max[source_key] > prior_official
            and str(source_bracket.get("bracket")) != bracket
        )
        if not source_crossed:
            candidate_blockers.append("source_running_max_did_not_cross_new_bracket")
        if source_crossed and cross_key in source_crossed_brackets:
            candidate_blockers.append("source_bracket_already_seen")
        if source_crossed:
            source_crossed_brackets.add(cross_key)
        condition_id, token_id = str(prior_market.get("condition_id") or ""), str(prior_market.get("token_id") or prior_market.get("no_token_id") or "")
        if not condition_id or not token_id:
            candidate_blockers.append("exact_condition_or_no_token_unverified")
        expected_station = str(prior_market.get("station") or "").upper()
        if expected_station and expected_station != str(event.get("station") or event.get("station_id") or "").upper():
            candidate_blockers.append("market_station_mismatch")
        static_best_ask, static_ask_size, static_full_depth = _book_summary(prior_market)
        if fetch_book_fn is not None and token_id:
            execution_book = fetch_book_fn(
                token_id, proxy=market_proxy, timeout_sec=book_timeout_sec, top_n=20
            )
            book_status = str(execution_book.get("status") or "")
            if book_status != "ok":
                candidate_blockers.append("fresh_execution_book_not_ok")
            sweep = _five_share_sweep(execution_book)
            full_depth = book_status == "ok" and isinstance(
                (execution_book.get("raw") or {}).get("asks"), list
            )
            book_started_at = execution_book.get("request_started_at_utc")
            book_fetched_at = execution_book.get("fetched_at_utc")
        else:
            static_book = (
                prior_market.get("book")
                if isinstance(prior_market.get("book"), Mapping)
                else {
                    "summary": {"best_ask": static_best_ask, "ask_size": static_ask_size},
                    "raw": prior_market.get("raw") or {},
                }
            )
            sweep = _five_share_sweep(static_book)
            full_depth = static_full_depth
            book_status = "fixture_or_snapshot"
            book_started_at = None
            book_fetched_at = prior_market.get("fetched_at_utc") or prior_market.get("available_at_utc")
        best_ask = safe_float(sweep.get("best_ask"))
        ask_size = safe_float(sweep.get("top_ask_size"))
        worst_ask = safe_float(sweep.get("worst_ask"))
        expected_vwap = safe_float(sweep.get("vwap"))
        principal = safe_float(sweep.get("principal_usd")) or 0.0
        if not full_depth:
            candidate_blockers.append("full_depth_execution_book_missing")
        if best_ask is None or ask_size is None:
            candidate_blockers.append("fresh_book_top_missing")
        if not bool(sweep.get("covered")) or worst_ask is None or expected_vwap is None:
            candidate_blockers.append("insufficient_five_share_ask_sweep")
        elif worst_ask > MAX_NO_ASK:
            candidate_blockers.append("ask_above_max_no_ask")
        if principal > MAX_ORDER_PRINCIPAL_USD + 1e-9:
            candidate_blockers.append("per_order_principal_cap")
        race_key = "|".join(
            [
                city_date[0], city_date[1], bracket,
                str(source_bracket.get("bracket") or ""), f"{prior_official:.6f}",
            ]
        )
        day = now.date().isoformat()
        if race_key in seen_races:
            candidate_blockers.append("cross_source_race_already_executed")
        if city_date in city_orders:
            candidate_blockers.append("city_target_date_order_cap")
        if daily_counts.get(day, 0) >= MAX_ORDERS_PER_UTC_DAY or daily_principal.get(day, 0.0) + principal > MAX_DAILY_PRINCIPAL_USD + 1e-9:
            candidate_blockers.append("daily_order_or_principal_cap")
        if paused:
            candidate_blockers.append("pause_file_present")
        if live and not confirm_live:
            candidate_blockers.append("confirm_live_missing")
        row = {**common, "status": "candidate" if not candidate_blockers else "blocked", "blockers": candidate_blockers,
               "prior_official_running_max": prior_official, "source_running_max": source_max[source_key],
               "previous_official_bracket": bracket, "new_source_bracket": source_bracket.get("bracket"),
               "condition_id": condition_id, "token_id": token_id, "best_ask": best_ask, "ask_size": ask_size,
               "worst_ask_for_five_shares": worst_ask, "expected_five_share_vwap": expected_vwap,
               "book_full_depth_valid": full_depth, "execution_book_status": book_status,
               "execution_book_request_started_at_utc": book_started_at,
               "execution_book_fetched_at_utc": book_fetched_at,
               "execution_book_levels": sweep.get("levels"),
               "official_fee_rate": official_fee_rate,
               "expected_taker_fee_usd": (
                   _expected_weather_taker_fee(SHARES_PER_ORDER, float(expected_vwap))
                   if expected_vwap is not None else None
               ),
               "execution_race_key": race_key, "planned_shares": SHARES_PER_ORDER,
               "max_shares_per_market": SHARES_PER_ORDER, "planned_principal_usd": principal,
               "live_requested": bool(live), "live_enabled": live_enabled}
        append_jsonl(output_dir / "opportunities.jsonl", row); emitted += 1
        if candidate_blockers:
            blocked += 1
        else:
            append_jsonl(
                output_dir / "capture_demands.jsonl",
                _capture_demand(row=row, condition_id=condition_id, token_id=token_id, now=now),
                durable=True,
            )
        if not candidate_blockers and live_enabled:
            assert worst_ask is not None
            intent = exact_share_taker_intent(best_ask=float(worst_ask), desired_shares=SHARES_PER_ORDER)
            intent_row = {**row, **intent, "status": "intent", "order_side": "BUY", "execution_policy": "taker_only_marketable_gtc_5_share_v1"}
            execution_attempt_id = f"{STRATEGY_ID}|{race_key}"
            reservation = {
                **intent_row,
                "status": "reserved_before_submit",
                "execution_attempt_id": execution_attempt_id,
                "reserved_at_utc": iso(),
                "reserved_at_monotonic_ns": time.monotonic_ns(),
                "reserved_principal_usd": float(intent["submitted_notional_usd"]),
                "crash_semantics": "reservation_consumes_caps_before_external_place",
            }
            append_jsonl(output_dir / "intents.jsonl", intent_row, durable=True)
            append_jsonl(output_dir / "execution_attempts.jsonl", reservation, durable=True)
            seen_races.add(race_key)
            city_orders.add(city_date)
            daily_counts[day] = daily_counts.get(day, 0) + 1
            daily_principal[day] = daily_principal.get(day, 0.0) + float(intent["submitted_notional_usd"])
            result = submit_marketable_gtc(intent_row, place=place_fn)  # type: ignore[arg-type]
            order = {**result["order_row"], "status": "order", "live_attempted_at_utc": iso(),
                     "live_attempted_monotonic_ns": time.monotonic_ns(), "exchange_response": result.get("exchange_response"),
                     "order_id": extract_order_id(result.get("exchange_response") or {}),
                     "execution_attempt_id": execution_attempt_id,
                     "live_submit_status": result.get("live_submit_status"), "actual_fill_shares": result.get("actual_fill_shares"),
                     "actual_fill_cost_usd": result.get("actual_fill_cost_usd"), "public_trade_is_own_fill": False}
            append_jsonl(output_dir / "orders.jsonl", order, durable=True); orders += 1
            if order.get("actual_fill_shares") is not None:
                append_jsonl(output_dir / "fills.jsonl", {**order, "status": "fill", "fill_source": "exchange_response_not_public_trade"}, durable=True); fills += 1
            if result.get("error"):
                append_jsonl(output_dir / "errors.jsonl", {**common, "error_type": "order_submit_failed"}, durable=True)
        if str(event.get("source")) == "metar_ws_metar":
            official_max[city_date] = max(official_max.get(city_date, -math.inf), market_value)
    _save_strategy_state(
        strategy_state_path,
        {
            "schema_version": "cross_no_v2_metar_state_v1",
            "strategy_id": STRATEGY_ID,
            "updated_at_utc": iso(),
            "official_max": {"|".join(key): value for key, value in sorted(official_max.items())},
            "source_max": {"|".join(key): value for key, value in sorted(source_max.items())},
            "source_crossed_brackets": ["|".join(key) for key in sorted(source_crossed_brackets)],
            "processed_event_ids": sorted(processed_ids)[-100_000:],
        },
    )
    health = {
        "schema_version": "cross_no_v2_metar_health_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_ID,
        "status": "ok",
        "generated_at_utc": iso(now),
        "generated_at_monotonic_ns": time.monotonic_ns(),
        "events_seen_total": len(processed_ids),
        "events_seen": len(processed_ids),
        "events_processed_this_cycle": emitted,
        "opportunities_emitted": emitted,
        "blocked": blocked,
        "orders_attempted_this_cycle": orders,
        "fills_observed_this_cycle": fills,
        "orders": orders,
        "fills": fills,
        "orders_reserved_utc_day": daily_counts.get(now.date().isoformat(), 0),
        "principal_reserved_utc_day": round(daily_principal.get(now.date().isoformat(), 0.0), 6),
        "live_enabled": live_enabled,
        "execution_mode": "live_probe" if live_enabled else "shadow",
        "shares_per_order": SHARES_PER_ORDER,
        "max_orders_per_utc_day": MAX_ORDERS_PER_UTC_DAY,
        "max_daily_principal_usd": MAX_DAILY_PRINCIPAL_USD,
        "max_no_ask": MAX_NO_ASK,
        "weather_taker_fee_rate": WEATHER_TAKER_FEE_RATE,
        "clock_invalid_same_boot_override_enabled": bool(allow_clock_invalid_same_boot_monotonic_probe),
        "settlement_hard_invalidation": False,
        "pause_file": str(pause_file) if pause_file else "",
        "paused": paused,
    }
    append_jsonl(output_dir / "health.jsonl", health, durable=True)
    _save_strategy_state(output_dir / "latest.json", health)
    return health


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_input = parser.add_mutually_exclusive_group(required=True)
    source_input.add_argument("--source-events-root", type=Path)
    source_input.add_argument("--evidence-db", type=Path)
    parser.add_argument("--market-books-latest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-source-age-sec", type=float, default=30.0)
    parser.add_argument("--official-fee-rate", type=float, default=WEATHER_TAKER_FEE_RATE)
    parser.add_argument("--pause-file", type=Path)
    parser.add_argument("--collector-run-start-wall-ns", type=int)
    parser.add_argument("--initialize-evidence-from-start", action="store_true", help="replay-only; never use for a live attachment")
    parser.add_argument("--allow-clock-invalid-same-boot-monotonic-probe", action="store_true")
    parser.add_argument("--clock-uncertainty-ms", type=float)
    parser.add_argument("--boot-monotonic-start-ns", type=int)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=0.5)
    parser.add_argument("--stop-after-sec", type=float, default=86_400.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--market-proxy")
    parser.add_argument("--code-identity", required=True)
    args = parser.parse_args()
    if args.live and args.source_events_root:
        raise SystemExit("live mode requires --evidence-db incremental input")
    if args.live and not args.confirm_live:
        raise SystemExit("--live requires --confirm-live")
    if args.interval_sec < 0.1:
        raise SystemExit("--interval-sec must be >= 0.1")
    if args.stop_after_sec <= 0:
        raise SystemExit("--stop-after-sec must be positive")
    args.pause_file = args.pause_file or (args.output_dir / "PAUSE")
    ensure_research_record(
        args.output_dir,
        code_identity=args.code_identity,
        evidence_db=args.evidence_db,
        market_books_latest=args.market_books_latest,
    )
    place_fn = build_live_taker_gtc_place_fn(args.market_proxy or os.environ.get("WEATHER_MARKET_PROXY_URL", "")) if args.live and args.confirm_live else None
    while True:
        cycle_started = time.monotonic()
        try:
            enforce_experiment_deadline(
                args.output_dir,
                pause_file=args.pause_file,
                stop_after_sec=args.stop_after_sec,
            )
            direct_events = None
            cursor_state = None
            if args.evidence_db:
                direct_events, cursor_state = direct_evidence_events(
                    args.evidence_db,
                    cursor_path=args.output_dir / "evidence_cursor.json",
                    allowlist=DEFAULT_CITY_STATIONS,
                    collector_run_start_wall_ns=args.collector_run_start_wall_ns,
                    initialize_at_current=not args.initialize_evidence_from_start,
                )
            result = run_probe(
                source_events_root=args.source_events_root or Path("."),
                market_books_latest=args.market_books_latest,
                output_dir=args.output_dir,
                live=args.live,
                confirm_live=args.confirm_live,
                place_fn=place_fn,
                max_source_age_sec=args.max_source_age_sec,
                official_fee_rate=args.official_fee_rate,
                pause_file=args.pause_file,
                events_override=direct_events,
                allow_clock_invalid_same_boot_monotonic_probe=args.allow_clock_invalid_same_boot_monotonic_probe,
                clock_uncertainty_ms=args.clock_uncertainty_ms,
                boot_monotonic_start_ns=(
                    args.boot_monotonic_start_ns
                    if args.boot_monotonic_start_ns is not None else 0
                ),
                fetch_book_fn=fetch_fresh_book,
                market_proxy=args.market_proxy or os.environ.get("WEATHER_MARKET_PROXY_URL", ""),
                book_timeout_sec=args.book_timeout_sec,
            )
            if cursor_state is not None:
                _save_cursor(args.output_dir / "evidence_cursor.json", cursor_state)
                result["direct_evidence_events"] = len(direct_events or [])
            print(json.dumps(result, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            failure = {
                "schema_version": "cross_no_v2_metar_health_v1",
                "strategy_id": STRATEGY_ID,
                "strategy_instance": STRATEGY_ID,
                "status": "blocked",
                "generated_at_utc": iso(),
                "error_type": type(exc).__name__,
                "error": "runner_cycle_failed",
                "live_enabled": bool(args.live and args.confirm_live),
            }
            append_jsonl(args.output_dir / "errors.jsonl", failure, durable=True)
            _save_strategy_state(args.output_dir / "latest.json", failure)
            print(json.dumps(failure, sort_keys=True), flush=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(max(0.1, args.interval_sec - (time.monotonic() - cycle_started)))


if __name__ == "__main__":
    raise SystemExit(main())
