#!/usr/bin/env python3
"""Independent, fail-closed Cross NO V2 (METAR) probe runner.

This runner consumes append-only lab source events and an already-owned market
book snapshot.  It never starts a collector or a market websocket.  A source
cross is only an *information proxy*, never a settlement invalidation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Mapping
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
MAX_ORDER_PRINCIPAL_USD = 5.0
MAX_NO_ASK = 0.99
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
DEFAULT_CITY_MARKET_UNITS = {city: "F" for city in DEFAULT_CITY_STATIONS}
SQLITE_SOURCE_MAP = {
    "METAR_WS_METAR": "metar_ws_metar", "METAR_WS_HFMETAR": "metar_ws_hfmetar", "METAR_WS_DATIS": "metar_ws_datis",
}
SOURCE_TOPIC_TEMPLATES = {
    "metar_ws_metar": "metar.obs.<icao>",
    "metar_ws_hfmetar": "metar.obs10.<icao>",
    "metar_ws_datis": "metar.atis.<icao>",
}
ATTRIBUTION_REFRESH_SEC = 30.0
DEFAULT_MAX_OBSERVATION_DELAY_SEC = 3600.0
DEFAULT_MAX_STREAM_SILENCE_SEC = 120.0
HEALTH_HISTORY_INTERVAL_SEC = 30.0
LIVE_CROSS_POLICY = "single_current_gt_0p7_v1"
SHADOW_CROSS_POLICY = "single_current_gt_0p5_v1"
COMPARISON_CROSS_POLICY = "two_consecutive_current_gt_0p5_v1"
STRONG_SINGLE_CROSS_MARGIN = 0.7
CONFIRMED_CROSS_MARGIN = 0.5
CONFIRMED_CROSS_OBSERVATIONS = 2
MARGIN_EPSILON = 1e-9
PRESTART_TRANSITION_HEALTH_FIELDS = frozenset(
    {
        "code_identity",
        "source_attribution_schema_version",
        "market_unit_contract",
        "native_lattice_contract",
        "live_cross_policy",
        "shadow_cross_policy",
        "comparison_cross_policy",
    }
)
_BOOK_RECORD_CACHE: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
_LAST_HEALTH_HISTORY_AT: dict[str, datetime] = {}


def load_universe_config(
    path: Path,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], frozenset[str]]:
    """Load the frozen collection universe and its independent live eligibility."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "cross_no_v2_metar_universe_v1":
        raise ValueError("cross NO V2 universe schema mismatch")
    targets = raw.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("cross NO V2 universe targets are required")
    allowlist: dict[str, str] = {}
    timezones: dict[str, str] = {}
    market_units: dict[str, str] = {}
    live_eligible: set[str] = set()
    seen_stations: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            raise ValueError("cross NO V2 universe target must be a mapping")
        city = str(target.get("city") or "").strip()
        station = str(target.get("station") or "").strip().upper()
        timezone_name = str(target.get("timezone") or "").strip()
        market_unit = str(target.get("market_unit") or "").strip().upper()
        if (
            not city
            or len(station) != 4
            or not station.isalnum()
            or not timezone_name
            or market_unit not in {"C", "F"}
        ):
            raise ValueError(
                "cross NO V2 universe city/station/timezone/market_unit is invalid"
            )
        if city in allowlist or station in seen_stations:
            raise ValueError("cross NO V2 universe city and station must be unique")
        ZoneInfo(timezone_name)
        allowlist[city] = station
        timezones[city] = timezone_name
        market_units[city] = market_unit
        seen_stations.add(station)
        if target.get("live_eligible") is True:
            live_eligible.add(city)
    return allowlist, timezones, market_units, frozenset(live_eligible)


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


def source_topic(source: Any, station: Any, explicit_topic: Any = None) -> str | None:
    """Return an explicit topic or derive one for tests/replay fixtures."""
    observed = str(explicit_topic or "").strip()
    if observed:
        return observed
    template = SOURCE_TOPIC_TEMPLATES.get(str(source or ""))
    icao = str(station or "").strip().lower()
    return template.replace("<icao>", icao) if template and icao else None


def source_topic_valid(source: Any, station: Any, observed_topic: Any) -> bool:
    """Require the raw subscription topic to agree with the decoded source arm."""
    template = SOURCE_TOPIC_TEMPLATES.get(str(source or ""))
    icao = str(station or "").strip().lower()
    observed = str(observed_topic or "").strip()
    if not template or not icao or not observed:
        return False
    return observed == template.replace("<icao>", icao)


def source_event_identity(row: Mapping[str, Any]) -> str:
    """Namespace producer ids by source so equal ids cannot erase another arm."""
    source = str(row.get("source") or "")
    event_id = str(row.get("information_event_id") or "")
    return f"{source}|{event_id}" if source and event_id else ""


def economic_cross_id(race_key: str) -> str:
    return "cross_no_v2:" + hashlib.sha256(race_key.encode("utf-8")).hexdigest()


def validate_isolated_prestart_manifest(payload: Mapping[str, Any]) -> None:
    """Allow only this runtime's narrow old-health-to-new-health transition."""
    critical = [
        row
        for row in payload.get("findings", [])
        if isinstance(row, Mapping) and row.get("severity") == "critical"
    ]
    if not critical:
        raise ValueError("strict manifest failed without a declared critical finding")
    for row in critical:
        if row.get("kind") != "runtime_health_contract_mismatch":
            raise ValueError(f"unrelated prestart manifest critical: {row.get('kind')}")
        runtimes = (row.get("detail") or {}).get("runtimes") or []
        if not runtimes:
            raise ValueError("prestart health exception has no runtime detail")
        for item in runtimes:
            if item.get("instance_id") != STRATEGY_ID:
                raise ValueError("prestart health exception is not isolated to cross_no_v2_metar_v1")
            status = str(item.get("status") or "")
            if status == "unreadable":
                continue
            mismatches = item.get("mismatches") or []
            mismatch_fields = {
                str(mismatch.get("field") or "")
                for mismatch in mismatches
                if isinstance(mismatch, Mapping)
            }
            if (
                status != "mismatch"
                or not mismatch_fields
                or not mismatch_fields.issubset(PRESTART_TRANSITION_HEALTH_FIELDS)
            ):
                raise ValueError(
                    "prestart health mismatch is outside the source-attribution transition contract"
                )


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


def _save_strategy_state_if_changed(path: Path, value: Mapping[str, Any]) -> bool:
    prior = _load_strategy_state(path)
    semantic_prior = {key: item for key, item in prior.items() if key != "updated_at_utc"}
    semantic_next = {key: item for key, item in value.items() if key != "updated_at_utc"}
    if semantic_prior == semantic_next:
        return False
    _save_strategy_state(path, value)
    return True


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
                    "reason": "bounded_probe_deadline_reached",
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
        "record_id": "research:weather:cross_no_v2_metar:cross_no_v2_metar_global_48h_live_probe_v2",
        "run_id": "cross_no_v2_metar_global_48h_live_probe_v2",
        "domain": "weather",
        "family": "cross_no_v2_metar",
        "skill": "weather-strategy-research",
        "lifecycle_status": "running",
        "observed_at_utc": None,
        "question": {
            "hypothesis": "A current METAR.ws source observation with native margin above 0.7 can buy five shares of the prior official bracket NO before the public market fully reprices; the original single-above-0.5 rule remains shadow-only and two consecutive above-0.5 observations remain a comparison label.",
            "decision_target": "Whether METAR.ws merits a longer paid executable-alpha study; 48 hours cannot establish durable alpha.",
            "scope": "One pre-registered 48-hour global station-aligned forward probe over the frozen universe; METAR.ws official, HF-METAR and D-ATIS arms remain separate.",
            "exclusions": [
                "orders above five shares",
                "more than one order per exact condition token or economic cross",
                "treating a source cross as settlement truth",
                "formal cross-source latency ranking while the wall clock contract is invalid",
            ],
        },
        "method": {
            "grain": "post-watermark source information event x city x target_date x prior official value/bracket, with distinct-observation confirmation state",
            "denominator_scope": "Every post-watermark event from the frozen configured station universe, including blocked, no-cross, no-book, no-fill and terminal-false rows.",
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
            "forward_policy": "Freeze the three source arms, market-city universe, single-current-margin-above-0.7 live policy and five-share sizing for the remaining forward window; retain single-current-margin-above-0.5 and consecutive-above-0.5 labels as fixed shadow comparators.",
            "acceptance_gates": [
                "48 hours is probe evidence only and cannot confirm alpha",
                "paid adoption still requires the existing 72h/7d, 500-pair, two-vantage and clock-valid gates",
                "every real order must have a durable pre-submit reservation and fresh five-share depth",
            ],
            "fee_and_execution_basis": "Five-share marketable GTC capped at 0.99 worst ask; no daily order/principal cap; one execution per condition token and per economic cross; Weather taker fee shares*0.05*p*(1-p); no maker child and no retry after a reserved attempt.",
        },
        "inputs": [
            {
                "input_id": "metar_ws_append_only_evidence",
                "kind": "sqlite_append_only_transport_evidence",
                "locator": (
                    f"runtime://{evidence_db.parent.name}/evidence.sqlite3"
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
                "coverage": "frozen Polymarket temperature-market city universe and event-triggered markout demand",
                "observed_at_utc": None,
            },
        ],
        "execution": {
            "producer": "scripts/ops/weather_cross_no_v2_metar.py",
            "code_identity": code_identity,
            "config_locator": "src/strategies/runtime/production.yaml",
            "config_identity": "cross_no_v2_metar_polymarket_48h_native_lattice_v2",
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
            "action": "Run the bounded 48-hour paid-source live economics and depth probe; do not promote the source from this window alone.",
            "superseded_record_ids": [],
        },
    }
    _save_cursor(path, payload)
    return path


def record_research_code_identity_amendment(output_dir: Path, *, code_identity: str) -> None:
    """Append an instrumentation-only release transition without rewriting the record."""
    record = _load_cursor(output_dir / "research_record.json")
    original = str((record.get("execution") or {}).get("code_identity") or "")
    if not original or original == code_identity:
        return
    path = output_dir / "research_record_amendments.jsonl"
    prior = {
        str(row.get("new_code_identity") or "")
        for row in jsonl_rows((path,))
    }
    if code_identity in prior:
        return
    append_jsonl(
        path,
        {
            "schema_version": "cross_no_v2_metar_research_record_amendment_v1",
            "amendment_id": hashlib.sha256(
                f"{STRATEGY_ID}|{original}|{code_identity}|signal_policy_confirmation_v1".encode("utf-8")
            ).hexdigest(),
            "record_id": record.get("record_id"),
            "amended_at_utc": iso(),
            "previous_code_identity": original,
            "new_code_identity": code_identity,
            "change_class": "signal_policy_confirmation_v1",
            "signal_policy_changed": True,
            "sizing_or_execution_policy_changed": False,
            "denominator_changed": False,
            "changes": [
                "live requires one causally current native observation with margin above 0.7",
                "single current native margin above 0.5 is retained as zero-notional shadow evidence",
                "two distinct consecutive current observations each above 0.5 are retained as a non-executing comparison label",
                "running source maximum is descriptive only and cannot trigger an order",
            ],
        },
        durable=True,
    )


def direct_evidence_events(
    evidence_db: Path, *, cursor_path: Path, allowlist: Mapping[str, str],
    city_timezones: Mapping[str, str] = CITY_TIMEZONES,
    collector_run_start_wall_ns: int | None = None, initialize_at_current: bool = True,
    allow_ended_run_for_replay: bool = False,
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
        if ended and not allow_ended_run_for_replay:
            raise ValueError("collector run has ended; rotate through a new explicit evidence DB")
        latest_clock = conn.execute(
            "SELECT uncertainty_ms FROM clock_health WHERE run_id = ? ORDER BY sampled_at_ns DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        clock_uncertainty_ms = safe_float(latest_clock[0]) if latest_clock else None
        maximum = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM source_observation_seen").fetchone()[0])
        latest_transport_wall_ns = conn.execute(
            "SELECT MAX(received_wall_ns) FROM transport_message WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        latest_transport_at_utc = (
            iso(
                datetime.fromtimestamp(
                    int(latest_transport_wall_ns) / 1_000_000_000,
                    tz=timezone.utc,
                )
            )
            if latest_transport_wall_ns is not None
            else None
        )
        if "source_seen_rowid" not in state:
            watermark = maximum if initialize_at_current else 0
            return [], {"source_seen_rowid": watermark, "collector_run_start_wall_ns": collector_run_start_wall_ns,
                        "collector_run_id": run_id, "initialized_at_utc": iso(),
                        "initialization": "current_max_rowid" if initialize_at_current else "from_start",
                        "latest_transport_received_at_utc": latest_transport_at_utc}
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
                   t.channel_or_topic AS transport_topic,
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
        timezone_name = city_timezones.get(city)
        if not timezone_name:
            continue
        target_date = observation.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        clock_valid = bool(row.get("seen_clock_valid")) and bool(row.get("transport_clock_valid"))
        events.append({
            "information_event_id": f"sqlite:{row['source_seen_id']}", "event_role": "revision" if row.get("is_correction") else "new_content",
            "source": source, "city": city, "target_date": target_date, "station": row["station_id"],
            # Presence of this field distinguishes a raw missing topic from a
            # replay fixture that asks _base_row() to derive the expected one.
            "source_topic": str(row.get("transport_topic") or "").strip() or None,
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
    next_state["latest_transport_received_at_utc"] = latest_transport_at_utc
    return events, next_state


def _book_records(path: Path) -> list[dict[str, Any]]:
    try:
        stat = path.stat()
        cache_key = str(path.resolve())
        cached = _BOOK_RECORD_CACHE.get(cache_key)
        if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = value.get("records", value) if isinstance(value, Mapping) else value
    records = (
        [dict(row) for row in rows if isinstance(row, Mapping)]
        if isinstance(rows, list)
        else []
    )
    _BOOK_RECORD_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, records)
    return records


def _event_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    mono = row.get("transport_received_monotonic_ns")
    try:
        return int(mono), str(row.get("information_event_id") or "")
    except (TypeError, ValueError):
        return (2**63 - 1, str(row.get("information_event_id") or ""))


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _market_value(temp_c: float, market_unit: str) -> int | None:
    """Map raw Celsius evidence onto the integer settlement-native lattice."""
    unit = str(market_unit or "").upper()
    if unit == "C":
        return _round_half_up(temp_c)
    if unit == "F":
        return _round_half_up(temp_c * 9.0 / 5.0 + 32.0)
    return None


def _native_value(temp_c: float, market_unit: str) -> float | None:
    """Return the unrounded observation in the market's settlement unit."""
    unit = str(market_unit or "").upper()
    if unit == "C":
        return float(temp_c)
    if unit == "F":
        return float(temp_c) * 9.0 / 5.0 + 32.0
    return None


def _strictly_above(value: float, threshold: float) -> bool:
    return float(value) - float(threshold) > MARGIN_EPSILON


def _advance_confirmation(
    prior: Mapping[str, Any] | None,
    *,
    observation_ts: str,
    baseline_value: float,
    baseline_bracket: str,
    qualifies: bool,
) -> tuple[dict[str, Any], bool]:
    """Advance a same-source streak using distinct, newer observations only."""
    current = dict(prior or {})
    prior_ts = str(current.get("last_observation_ts") or "")
    if prior_ts and observation_ts < prior_ts:
        return current, False
    same_baseline = bool(
        safe_float(current.get("baseline_value")) == float(baseline_value)
        and str(current.get("baseline_bracket") or "") == baseline_bracket
    )
    if prior_ts == observation_ts:
        streak = int(current.get("streak") or 0)
        if not qualifies:
            streak = 0
        elif not same_baseline or not bool(current.get("last_qualifies")):
            streak = 1
        return {
            "last_observation_ts": observation_ts,
            "baseline_value": float(baseline_value),
            "baseline_bracket": baseline_bracket,
            "last_qualifies": bool(qualifies),
            "streak": streak,
        }, False
    streak = (
        int(current.get("streak") or 0) + 1
        if qualifies and same_baseline and bool(current.get("last_qualifies"))
        else 1
        if qualifies
        else 0
    )
    return {
        "last_observation_ts": observation_ts,
        "baseline_value": float(baseline_value),
        "baseline_bracket": baseline_bracket,
        "last_qualifies": bool(qualifies),
        "streak": streak,
    }, True


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
        book.get("full_depth_valid") is True
        or summary.get("full_depth_valid") is True
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
    *,
    row: Mapping[str, Any],
    condition_id: str,
    token_id: str,
    now: datetime,
    reason: str = "cross_no_v2_metar_execution_markout",
    capture_role: str = "execution_candidate",
) -> dict[str, Any]:
    return CaptureDemand.create(
        consumer_id="cross_no_v2_metar_v1",
        strategy_key="weather.cross_no_v2_metar_v1",
        condition_id=condition_id,
        token_id=token_id,
        reason=reason,
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
            "source_arm": row.get("attribution_source_arm") or row.get("source"),
            "source_topic": row.get("attribution_source_topic") or row.get("source_topic"),
            "source_topic_template": row.get("attribution_source_topic_template"),
            "attribution_role": row.get("attribution_role"),
            "economic_cross_id": row.get("economic_cross_id"),
            "execution_race_key": row.get("execution_race_key"),
            "capture_role": capture_role,
            "shadow_zero_notional": bool(row.get("shadow_zero_notional")),
            "event_family_id": row.get("event_family_id"),
            "semantic_version_id": row.get("semantic_version_id"),
            "raw_report_id": row.get("raw_report_id"),
            "station": row.get("station"),
            "temp_c": row.get("temp_c"),
            "source_event_ts_utc": row.get("source_event_ts_utc"),
            "transport_received_at_utc": row.get("transport_received_at_utc"),
            "transport_received_monotonic_ns": row.get("transport_received_monotonic_ns"),
            "formal_latency_rank_eligible": bool(row.get("formal_latency_rank_eligible")),
            "execution_clock_mode": row.get("execution_clock_mode"),
            "public_trade_is_own_fill": False,
        },
    ).to_dict()


def _source_age_seconds(row: Mapping[str, Any], now: datetime) -> float | None:
    received = parse_utc(row.get("transport_received_at_utc"))
    return None if received is None else (now - received).total_seconds()


def _observation_delay_seconds(row: Mapping[str, Any]) -> float | None:
    received = parse_utc(row.get("transport_received_at_utc"))
    observed = parse_utc(row.get("source_event_ts_utc"))
    if received is None or observed is None:
        return None
    return (received - observed).total_seconds()


def _base_row(row: Mapping[str, Any], *, now: datetime, execution_clock_mode: str = "formal_clock_valid_v1") -> dict[str, Any]:
    source = str(row.get("source") or "")
    station = row.get("station") or row.get("station_id")
    topic = (
        str(row.get("source_topic") or "").strip() or None
        if "source_topic" in row
        else source_topic(source, station)
    )
    return {
        "schema_version": "cross_no_v2_metar_opportunity_v1",
        "strategy_id": STRATEGY_ID,
        "created_at_utc": iso(now),
        "created_at_monotonic_ns": time.monotonic_ns(),
        "city": row.get("city"), "target_date": row.get("target_date"),
        "station": station, "source": source,
        "temp_c": row.get("temp_c"),
        "source_topic": topic,
        "source_topic_valid": source_topic_valid(source, station, topic),
        "attribution_source_arm": source,
        "attribution_source_topic": topic,
        "attribution_source_topic_template": SOURCE_TOPIC_TEMPLATES.get(source),
        "information_event_id": row.get("information_event_id"),
        "event_family_id": row.get("event_family_id"),
        "semantic_version_id": row.get("semantic_version_id"),
        "raw_report_id": row.get("raw_report_id"),
        "transport_received_at_utc": row.get("transport_received_at_utc"),
        "transport_received_monotonic_ns": row.get("transport_received_monotonic_ns"),
        "source_event_ts_utc": row.get("source_event_ts_utc"),
        "observation_delay_seconds": _observation_delay_seconds(row),
        "source_cross_is_proxy_only": True,
        "settlement_hard_invalidation": False,
        "economic_cross_id": None,
        "attribution_role": "denominator_event",
        "terminal_false_cross": bool(row.get("terminal_false_cross", False)),
        "formal_latency_rank_eligible": execution_clock_mode == "formal_clock_valid_v1",
        "execution_clock_mode": execution_clock_mode,
    }


def _eligible_event(
    row: Mapping[str, Any], *, allowlist: Mapping[str, str], now: datetime, max_source_age_sec: float,
    max_observation_delay_sec: float,
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
    observation_delay = _observation_delay_seconds(row)
    if (
        observation_delay is None
        or observation_delay < -300.0
        or observation_delay > max_observation_delay_sec
    ):
        blockers.append("source_observation_delay_out_of_bounds")
    if row.get("transport_received_monotonic_ns") is None:
        blockers.append("transport_monotonic_missing")
    return blockers, clock_mode


def _existing_keys(output_dir: Path) -> tuple[set[str], set[str], dict[str, int], dict[str, float]]:
    seen: set[str] = set()
    executed_tokens: set[str] = set()
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
        token_id = str(row.get("token_id") or "")
        if token_id:
            executed_tokens.add(token_id)
        attempt_id = str(row.get("execution_attempt_id") or event_key)
        if not attempt_id or attempt_id in counted_attempts:
            continue
        counted_attempts.add(attempt_id)
        day = str(row.get("created_at_utc") or "")[:10]
        order_count[day] = order_count.get(day, 0) + 1
        principal[day] = principal.get(day, 0.0) + float(
            row.get("reserved_principal_usd") or row.get("submitted_notional_usd") or 0.0
        )
    return seen, executed_tokens, order_count, principal


def write_runtime_source_attribution(output_dir: Path, *, now: datetime) -> dict[str, Any]:
    """Materialize a compact derived view without rewriting append-only evidence."""
    arms: dict[str, dict[str, Any]] = {
        source: {
            "source_arm": source,
            "source_topic_template": SOURCE_TOPIC_TEMPLATES[source],
            "denominator_events": 0,
            "formal_latency_eligible_events": 0,
            "source_cross_events": 0,
            "candidate_events": 0,
            "blocked_events": 0,
            "five_share_executable_events": 0,
            "execution_race_winner_events": 0,
            "later_race_blocked_events": 0,
            "execution_attempts": 0,
            "orders": 0,
            "fills": 0,
            "actual_fill_shares": 0.0,
            "actual_fill_cost_usd": 0.0,
            "expected_entry_fees_usd": 0.0,
            "blockers": {},
            "markout_status": "pending_or_join_via_capture_demand_id",
            "canonical_settlement_pnl_status": "pending_canonical_fact_join",
        }
        for source in sorted(SOURCE_ARMS)
    }
    opportunities = list(jsonl_rows((output_dir / "opportunities.jsonl",)))
    for row in opportunities:
        source = str(row.get("attribution_source_arm") or row.get("source") or "")
        if source not in arms:
            continue
        target = arms[source]
        target["denominator_events"] += 1
        target["formal_latency_eligible_events"] += int(bool(row.get("formal_latency_rank_eligible")))
        blockers = [str(value) for value in row.get("blockers") or ()]
        if str(row.get("status")) == "candidate":
            target["candidate_events"] += 1
        else:
            target["blocked_events"] += 1
        for blocker in blockers:
            target["blockers"][blocker] = target["blockers"].get(blocker, 0) + 1
        if (
            row.get("previous_official_bracket") is not None
            and row.get("new_source_bracket") is not None
            and str(row.get("previous_official_bracket")) != str(row.get("new_source_bracket"))
        ):
            target["source_cross_events"] += 1
        if (
            bool(row.get("book_full_depth_valid"))
            and safe_float(row.get("expected_five_share_vwap")) is not None
            and safe_float(row.get("worst_ask_for_five_shares")) is not None
            and float(row["worst_ask_for_five_shares"]) <= MAX_NO_ASK
        ):
            target["five_share_executable_events"] += 1
        role = str(row.get("attribution_role") or "")
        target["later_race_blocked_events"] += int(role == "later_race_blocked")

    journal_specs = (
        ("execution_attempts.jsonl", "execution_attempts", "execution_attempt_id"),
        ("orders.jsonl", "orders", "order_id"),
        ("fills.jsonl", "fills", "order_id"),
    )
    for filename, metric, identity_field in journal_specs:
        seen_ids: set[str] = set()
        for row in jsonl_rows((output_dir / filename,)):
            source = str(row.get("attribution_source_arm") or row.get("source") or "")
            if source not in arms:
                continue
            identity = str(row.get(identity_field) or row.get("execution_attempt_id") or "")
            if identity and identity in seen_ids:
                continue
            if identity:
                seen_ids.add(identity)
            arms[source][metric] += 1
            if metric == "execution_attempts":
                arms[source]["execution_race_winner_events"] += 1
            if metric == "orders":
                arms[source]["expected_entry_fees_usd"] += float(
                    safe_float(row.get("expected_taker_fee_usd")) or 0.0
                )
            if metric == "fills":
                arms[source]["actual_fill_shares"] += float(
                    safe_float(row.get("actual_fill_shares")) or 0.0
                )
                arms[source]["actual_fill_cost_usd"] += float(
                    safe_float(row.get("actual_fill_cost_usd")) or 0.0
                )

    for target in arms.values():
        target["actual_fill_shares"] = round(float(target["actual_fill_shares"]), 8)
        target["actual_fill_cost_usd"] = round(float(target["actual_fill_cost_usd"]), 8)
        target["expected_entry_fees_usd"] = round(float(target["expected_entry_fees_usd"]), 8)
        target["blockers"] = dict(sorted(target["blockers"].items()))
    payload = {
        "schema_version": "cross_no_v2_metar_source_attribution_runtime_v1",
        "strategy_id": STRATEGY_ID,
        "generated_at_utc": iso(now),
        "attribution_contract": {
            "realized_pnl_owner": "first source arm whose economic cross produced the execution attempt",
            "later_source_role": "counterfactual_later_race_only_never_duplicate_realized_pnl",
            "public_trade_semantics": "public market trade is not own fill",
            "settlement_pnl_semantics": "must join canonical fact_trades; absent here is pending not zero",
        },
        "source_arms": arms,
    }
    _save_strategy_state(output_dir / "source_attribution_latest.json", payload)
    return payload


def run_probe(
    *, source_events_root: Path, market_books_latest: Path, output_dir: Path,
    live: bool = False, confirm_live: bool = False, place_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    now: datetime | None = None, max_source_age_sec: float = 180.0,
    official_fee_rate: float | None = WEATHER_TAKER_FEE_RATE,
    official_fee_bps: float | None = None,
    allowlist: Mapping[str, str] = DEFAULT_CITY_STATIONS, pause_file: Path | None = None,
    market_units: Mapping[str, str] = DEFAULT_CITY_MARKET_UNITS,
    live_eligible_cities: Collection[str] | None = None,
    capture_demands_jsonl: Path | None = None,
    events_override: Iterable[Mapping[str, Any]] | None = None,
    allow_clock_invalid_same_boot_monotonic_probe: bool = False, clock_uncertainty_ms: float | None = None,
    boot_monotonic_start_ns: int | None = None, monotonic_now_ns: int | None = None,
    max_observation_delay_sec: float = DEFAULT_MAX_OBSERVATION_DELAY_SEC,
    source_stream_last_received_at_utc: str | None = None,
    max_stream_silence_sec: float = DEFAULT_MAX_STREAM_SILENCE_SEC,
    monitor_source_stream: bool = False,
    fetch_book_fn: Callable[..., dict[str, Any]] | None = None,
    market_proxy: str = "", book_timeout_sec: float = 5.0,
    code_identity: str = "unversioned_test",
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
    if max_observation_delay_sec <= 0 or max_stream_silence_sec <= 0:
        raise ValueError("observation delay and stream silence limits must be positive")
    books = _book_records(market_books_latest)
    by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for market in books:
        key = (str(market.get("city") or ""), str(market.get("event_date") or market.get("target_date") or ""))
        by_city_date.setdefault(key, []).append(market)
    seen_races, executed_tokens, daily_counts, daily_principal = _existing_keys(output_dir)
    eligible_cities = frozenset(
        allowlist.keys() if live_eligible_cities is None else live_eligible_cities
    )
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
    confirmation_state = {
        tuple(key.split("|", 2)): dict(value)
        for key, value in dict(strategy_state.get("confirmation_state") or {}).items()
        if key.count("|") == 2 and isinstance(value, Mapping)
    }
    processed_keys = set(map(str, strategy_state.get("processed_event_keys") or []))
    legacy_processed_ids = (
        set(map(str, strategy_state.get("processed_event_ids") or []))
        if not bool(strategy_state.get("processed_event_key_migration_complete"))
        else set()
    )
    # Rebuild the namespaced denominator from append-only evidence on first
    # migration, and self-heal a prior partial migration whose key set is empty.
    if legacy_processed_ids or not bool(
        strategy_state.get("processed_event_journal_recovery_complete")
    ):
        processed_keys.update(
            source_event_identity(row)
            for row in jsonl_rows((output_dir / "opportunities.jsonl",))
            if source_event_identity(row)
            and (
                not legacy_processed_ids
                or str(row.get("information_event_id") or "") in legacy_processed_ids
            )
        )
    emitted = orders = fills = blocked = 0

    for event in events:
        event_id = str(event.get("information_event_id") or "")
        event_key = source_event_identity(event)
        if not event_key or event_key in processed_keys:
            continue
        processed_keys.add(event_key)
        blockers, clock_mode = _eligible_event(
            event, allowlist=allowlist, now=now, max_source_age_sec=max_source_age_sec,
            max_observation_delay_sec=max_observation_delay_sec,
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
        if not bool(common.get("source_topic_valid")):
            blockers.append("source_topic_mismatch_or_missing")
        city_date = (str(event.get("city") or ""), str(event.get("target_date") or ""))
        markets = by_city_date.get(city_date, [])
        temp_c = safe_float(event.get("temp_c"))
        market_unit = str(market_units.get(city_date[0]) or "").upper()
        if market_unit not in {"C", "F"}:
            blockers.append("market_unit_missing_or_invalid")
        market_value = (
            _market_value(temp_c, market_unit)
            if temp_c is not None and market_unit in {"C", "F"}
            else None
        )
        current_native_value = (
            _native_value(temp_c, market_unit)
            if temp_c is not None and market_unit in {"C", "F"}
            else None
        )
        common.update(
            {
                "market_unit": market_unit or None,
                "settlement_native_value": market_value,
                "source_current_native_value": current_native_value,
                "native_lattice_contract": "round_half_up_integer_market_unit_v1",
                "live_cross_policy": LIVE_CROSS_POLICY,
                "shadow_cross_policy": SHADOW_CROSS_POLICY,
                "comparison_cross_policy": COMPARISON_CROSS_POLICY,
            }
        )
        if not blockers and not markets:
            blockers.append("market_not_found")
        if not blockers and market_value is None:
            blockers.append("market_unit_unsupported")

        # Preserve the previous official value for this event's cross decision,
        # but update the official accumulator independently of market/book and
        # decision freshness blockers. A valid late official observation still
        # belongs in the running-max state; it must not itself become a trade.
        prior_official = official_max.get(city_date)
        official_state_eligible = bool(
            str(event.get("source")) == "metar_ws_metar"
            and str(event.get("event_role")) in {"new_content", "revision"}
            and allowlist.get(city_date[0])
            == str(event.get("station") or event.get("station_id") or "").upper()
            and common.get("source_topic_valid") is True
            and market_value is not None
            and city_date[1]
        )
        if official_state_eligible:
            official_max[city_date] = max(
                official_max.get(city_date, -math.inf), market_value
            )
        if blockers:
            append_jsonl(output_dir / "opportunities.jsonl", {**common, "status": "blocked", "blockers": blockers})
            emitted += 1; blocked += 1
            continue
        assert market_value is not None
        source_key = (city_date[0], city_date[1], str(event["source"]))
        prior_source = source_max.get(source_key, -math.inf)
        source_max[source_key] = max(prior_source, market_value)
        if prior_official is None:
            append_jsonl(output_dir / "opportunities.jsonl", {**common, "status": "blocked", "blockers": ["prior_official_running_max_missing"]})
            emitted += 1; blocked += 1
            continue
        prior_market = _no_market_for_value(markets, prior_official)
        source_bracket = _no_market_for_value(markets, market_value)
        if prior_market is None or source_bracket is None:
            row = {**common, "status": "blocked", "prior_official_running_max": prior_official,
                   "source_running_max": source_max[source_key], "blockers": ["exact_condition_or_no_token_unverified"]}
            append_jsonl(output_dir / "opportunities.jsonl", row); emitted += 1; blocked += 1
            continue
        bracket = str(prior_market.get("bracket"))
        candidate_blockers: list[str] = []
        if city_date[0] not in eligible_cities:
            candidate_blockers.append("city_not_live_eligible")
        current_crossed_new_bracket = bool(
            current_native_value is not None
            and current_native_value > prior_official
            and str(source_bracket.get("bracket")) != bracket
        )
        current_cross_margin = (
            float(current_native_value) - float(prior_official)
            if current_native_value is not None
            else None
        )
        shadow_cross = bool(
            current_crossed_new_bracket
            and current_cross_margin is not None
            and _strictly_above(current_cross_margin, CONFIRMED_CROSS_MARGIN)
        )
        observation_ts = str(event.get("source_event_ts_utc") or "")
        prior_confirmation = confirmation_state.get(source_key)
        prior_confirmation_ts = str(
            (prior_confirmation or {}).get("last_observation_ts") or ""
        )
        observation_is_causally_current = bool(
            observation_ts
            and (
                not prior_confirmation_ts
                or observation_ts >= prior_confirmation_ts
            )
        )
        next_confirmation, observation_advanced = _advance_confirmation(
            prior_confirmation,
            observation_ts=observation_ts,
            baseline_value=prior_official,
            baseline_bracket=bracket,
            qualifies=shadow_cross,
        )
        confirmation_state[source_key] = next_confirmation
        confirmation_count = int(next_confirmation.get("streak") or 0)
        strong_single_cross = bool(
            current_crossed_new_bracket
            and current_cross_margin is not None
            and observation_is_causally_current
            and _strictly_above(current_cross_margin, STRONG_SINGLE_CROSS_MARGIN)
        )
        consecutive_cross = bool(
            shadow_cross
            and observation_advanced
            and confirmation_count >= CONFIRMED_CROSS_OBSERVATIONS
        )
        live_cross_confirmed = strong_single_cross
        if not current_crossed_new_bracket:
            candidate_blockers.append("current_observation_did_not_cross_new_bracket")
        elif not shadow_cross:
            candidate_blockers.append("current_cross_margin_not_above_shadow_threshold")
        elif not live_cross_confirmed:
            candidate_blockers.append("live_cross_confirmation_not_met")
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
            full_depth = (
                book_status == "ok"
                and execution_book.get("full_depth_valid") is True
                and isinstance((execution_book.get("raw") or {}).get("asks"), list)
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
        race_already_executed = race_key in seen_races
        if race_already_executed:
            candidate_blockers.append("cross_source_race_already_executed")
        if token_id in executed_tokens:
            candidate_blockers.append("condition_token_already_executed")
        if paused:
            candidate_blockers.append("pause_file_present")
        if live and not confirm_live:
            candidate_blockers.append("confirm_live_missing")
        attribution_role = (
            "later_race_blocked"
            if race_already_executed
            else "no_cross"
            if not shadow_cross
            else "shadow_single_cross"
            if not live_cross_confirmed
            else "cross_blocked"
            if candidate_blockers
            else "execution_candidate"
            if live_enabled
            else "first_eligible_cross"
        )
        row = {**common, "status": "candidate" if not candidate_blockers else "blocked", "blockers": candidate_blockers,
               "prior_official_running_max": prior_official, "source_running_max": source_max[source_key],
               "source_running_max_descriptive_only": True,
               "current_cross_margin_native": current_cross_margin,
               "current_crossed_new_bracket": current_crossed_new_bracket,
               "shadow_single_cross": shadow_cross,
               "strong_single_cross": strong_single_cross,
               "confirmation_observation_advanced": observation_advanced,
               "confirmation_observation_causally_current": observation_is_causally_current,
               "consecutive_cross_count": confirmation_count,
               "consecutive_cross_confirmed": consecutive_cross,
               "live_cross_confirmed": live_cross_confirmed,
               "live_cross_policy": LIVE_CROSS_POLICY,
               "shadow_cross_policy": SHADOW_CROSS_POLICY,
               "comparison_cross_policy": COMPARISON_CROSS_POLICY,
               "confirmation_sequence_contract": "consecutive_eligible_causally_current_observations_same_source_baseline_v1",
               "bracket": bracket, "unit": market_unit,
               "previous_official_bracket": bracket, "new_source_bracket": source_bracket.get("bracket"),
               "condition_id": condition_id, "market_id": condition_id,
               "question": prior_market.get("question"),
               "icao": str(event.get("station") or event.get("station_id") or "").upper(),
               "token_id": token_id, "best_ask": best_ask, "ask_size": ask_size,
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
               "execution_race_key": race_key, "economic_cross_id": economic_cross_id(race_key),
               "attribution_role": attribution_role,
               "shadow_zero_notional": bool(shadow_cross and not live_cross_confirmed),
               "execution_authority": live_cross_confirmed,
               "planned_shares": SHARES_PER_ORDER if live_cross_confirmed else 0.0,
               "hypothetical_shares": SHARES_PER_ORDER,
               "max_shares_per_market": SHARES_PER_ORDER,
               "planned_principal_usd": principal if live_cross_confirmed else 0.0,
               "hypothetical_principal_usd": principal,
               "live_requested": bool(live), "live_enabled": live_enabled}
        append_jsonl(output_dir / "opportunities.jsonl", row); emitted += 1
        shadow_capture_eligible = bool(
            shadow_cross
            and city_date[0] in eligible_cities
            and condition_id
            and token_id
            and (
                not expected_station
                or expected_station
                == str(event.get("station") or event.get("station_id") or "").upper()
            )
        )
        if candidate_blockers:
            blocked += 1
        if not candidate_blockers:
            append_jsonl(
                capture_demands_jsonl or (output_dir / "capture_demands.jsonl"),
                _capture_demand(row=row, condition_id=condition_id, token_id=token_id, now=now),
                durable=True,
            )
        elif shadow_capture_eligible:
            append_jsonl(
                capture_demands_jsonl or (output_dir / "capture_demands.jsonl"),
                _capture_demand(
                    row=row,
                    condition_id=condition_id,
                    token_id=token_id,
                    now=now,
                    reason="cross_no_v2_metar_shadow_markout",
                    capture_role="single_current_gt_0p5_zero_notional_shadow",
                ),
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
                "attribution_role": "execution_race_winner",
                "execution_attempt_id": execution_attempt_id,
                "reserved_at_utc": iso(),
                "reserved_at_monotonic_ns": time.monotonic_ns(),
                "reserved_principal_usd": float(intent["submitted_notional_usd"]),
                "crash_semantics": "reservation_consumes_caps_before_external_place",
            }
            append_jsonl(output_dir / "intents.jsonl", intent_row, durable=True)
            append_jsonl(output_dir / "execution_attempts.jsonl", reservation, durable=True)
            seen_races.add(race_key)
            executed_tokens.add(token_id)
            daily_counts[day] = daily_counts.get(day, 0) + 1
            daily_principal[day] = daily_principal.get(day, 0.0) + float(intent["submitted_notional_usd"])
            result = submit_marketable_gtc(intent_row, place=place_fn)  # type: ignore[arg-type]
            order = {**result["order_row"], "status": "order", "live_attempted_at_utc": iso(),
                     "attribution_role": "execution_race_winner",
                     "live_attempted_monotonic_ns": time.monotonic_ns(), "exchange_response": result.get("exchange_response"),
                     "order_id": extract_order_id(result.get("exchange_response") or {}),
                     "execution_attempt_id": execution_attempt_id,
                     "live_submit_status": result.get("live_submit_status"),
                     "exchange_order_status": result.get("exchange_order_status"),
                     "immediate_cancel_response": result.get("immediate_cancel_response"),
                     "immediate_cancel_confirmed": result.get("immediate_cancel_confirmed"),
                     "actual_fill_shares": result.get("actual_fill_shares"),
                     "actual_fill_cost_usd": result.get("actual_fill_cost_usd"), "public_trade_is_own_fill": False}
            append_jsonl(output_dir / "orders.jsonl", order, durable=True); orders += 1
            if order.get("actual_fill_shares") is not None:
                append_jsonl(output_dir / "fills.jsonl", {**order, "status": "fill", "fill_source": "exchange_response_not_public_trade"}, durable=True); fills += 1
            if result.get("error"):
                append_jsonl(
                    output_dir / "errors.jsonl",
                    {
                        **common,
                        "error_type": "order_lifecycle_failed",
                        "error": result.get("error"),
                        "execution_attempt_id": execution_attempt_id,
                        "order_id": order.get("order_id"),
                    },
                    durable=True,
                )
                if pause_file is not None and result.get("live_order_posted"):
                    _save_cursor(
                        pause_file,
                        {
                            "reason": "live_order_lifecycle_uncertain",
                            "paused_at_utc": iso(),
                            "execution_attempt_id": execution_attempt_id,
                            "order_id": order.get("order_id"),
                            "error": result.get("error"),
                        },
                    )
                    paused = True
    state_changed = _save_strategy_state_if_changed(
        strategy_state_path,
        {
            "schema_version": "cross_no_v2_metar_state_v2",
            "strategy_id": STRATEGY_ID,
            "live_cross_policy": LIVE_CROSS_POLICY,
            "shadow_cross_policy": SHADOW_CROSS_POLICY,
            "comparison_cross_policy": COMPARISON_CROSS_POLICY,
            "updated_at_utc": iso(),
            "official_max": {"|".join(key): value for key, value in sorted(official_max.items())},
            "source_max": {"|".join(key): value for key, value in sorted(source_max.items())},
            "confirmation_state": {
                "|".join(key): value
                for key, value in sorted(confirmation_state.items())
            },
            "processed_event_ids": sorted(
                {
                    key.split("|", 1)[1]
                    for key in processed_keys
                    if "|" in key
                }
            )[-100_000:],
            "processed_event_keys": sorted(processed_keys)[-100_000:],
            "processed_event_key_migration_complete": True,
            "processed_event_journal_recovery_complete": True,
        },
    )
    attribution_path = output_dir / "source_attribution_latest.json"
    prior_attribution = _load_strategy_state(attribution_path)
    prior_generated = parse_utc(prior_attribution.get("generated_at_utc"))
    attribution_due = (
        not attribution_path.exists()
        or prior_generated is None
        or (now - prior_generated).total_seconds() >= ATTRIBUTION_REFRESH_SEC
    )
    if attribution_due:
        source_attribution = write_runtime_source_attribution(output_dir, now=now)
    else:
        source_attribution = prior_attribution
    stream_received = parse_utc(source_stream_last_received_at_utc)
    source_stream_age_sec = (
        (now - stream_received).total_seconds() if stream_received is not None else None
    )
    source_stream_stale = bool(
        monitor_source_stream
        and (
            source_stream_age_sec is None
            or source_stream_age_sec > max_stream_silence_sec
        )
    )
    health = {
        "schema_version": "cross_no_v2_metar_health_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_ID,
        "code_identity": code_identity,
        "status": "source_stream_stale" if source_stream_stale else "ok",
        "generated_at_utc": iso(now),
        "generated_at_monotonic_ns": time.monotonic_ns(),
        "events_seen_total": len(processed_keys),
        "events_seen": len(processed_keys),
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
        "daily_order_cap_enabled": False,
        "daily_principal_cap_enabled": False,
        "max_no_ask": MAX_NO_ASK,
        "weather_taker_fee_rate": WEATHER_TAKER_FEE_RATE,
        "max_observation_delay_sec": max_observation_delay_sec,
        "source_stream_last_received_at_utc": source_stream_last_received_at_utc,
        "source_stream_age_sec": source_stream_age_sec,
        "max_stream_silence_sec": max_stream_silence_sec,
        "source_stream_stale": source_stream_stale,
        "source_stream_monitor_enabled": monitor_source_stream,
        "market_unit_contract": "universe_config_required_v1",
        "native_lattice_contract": "round_half_up_integer_market_unit_v1",
        "live_cross_policy": LIVE_CROSS_POLICY,
        "shadow_cross_policy": SHADOW_CROSS_POLICY,
        "comparison_cross_policy": COMPARISON_CROSS_POLICY,
        "strong_single_cross_margin": STRONG_SINGLE_CROSS_MARGIN,
        "confirmed_cross_margin": CONFIRMED_CROSS_MARGIN,
        "confirmed_cross_observations": CONFIRMED_CROSS_OBSERVATIONS,
        "source_running_max_execution_authority": False,
        "confirmation_sequence_contract": "consecutive_eligible_causally_current_observations_same_source_baseline_v1",
        "strategy_state_changed_this_cycle": state_changed,
        "clock_invalid_same_boot_override_enabled": bool(allow_clock_invalid_same_boot_monotonic_probe),
        "settlement_hard_invalidation": False,
        "source_attribution_schema_version": source_attribution.get("schema_version"),
        "source_attribution_path": str(attribution_path),
        "pause_file": str(pause_file) if pause_file else "",
        "paused": paused,
    }
    health_history_key = str((output_dir / "health.jsonl").resolve())
    last_health_history_at = _LAST_HEALTH_HISTORY_AT.get(health_history_key)
    if (
        last_health_history_at is None
        or (now - last_health_history_at).total_seconds()
        >= HEALTH_HISTORY_INTERVAL_SEC
        or emitted
        or orders
        or fills
        or source_stream_stale
    ):
        append_jsonl(output_dir / "health.jsonl", health, durable=True)
        _LAST_HEALTH_HISTORY_AT[health_history_key] = now
    _save_strategy_state(output_dir / "latest.json", health)
    return health


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_input = parser.add_mutually_exclusive_group(required=True)
    source_input.add_argument("--source-events-root", type=Path)
    source_input.add_argument("--evidence-db", type=Path)
    parser.add_argument("--market-books-latest", type=Path, required=True)
    parser.add_argument("--universe-config", type=Path)
    parser.add_argument("--capture-demands-jsonl", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-source-age-sec", type=float, default=30.0)
    parser.add_argument(
        "--max-observation-delay-sec",
        type=float,
        default=DEFAULT_MAX_OBSERVATION_DELAY_SEC,
    )
    parser.add_argument(
        "--max-stream-silence-sec",
        type=float,
        default=DEFAULT_MAX_STREAM_SILENCE_SEC,
    )
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
    allowlist = DEFAULT_CITY_STATIONS
    city_timezones = CITY_TIMEZONES
    market_units = DEFAULT_CITY_MARKET_UNITS
    live_eligible_cities: Collection[str] = frozenset(DEFAULT_CITY_STATIONS)
    if args.universe_config:
        (
            allowlist,
            city_timezones,
            market_units,
            live_eligible_cities,
        ) = load_universe_config(args.universe_config)
    ensure_research_record(
        args.output_dir,
        code_identity=args.code_identity,
        evidence_db=args.evidence_db,
        market_books_latest=args.market_books_latest,
    )
    record_research_code_identity_amendment(
        args.output_dir,
        code_identity=args.code_identity,
    )
    place_fn = build_live_taker_gtc_place_fn(args.market_proxy or os.environ.get("WEATHER_MARKET_PROXY_URL", "")) if args.live and args.confirm_live else None
    while True:
        cycle_started = time.monotonic()
        try:
            experiment_control = enforce_experiment_deadline(
                args.output_dir,
                pause_file=args.pause_file,
                stop_after_sec=args.stop_after_sec,
            )
            direct_events = None
            cursor_state = None
            if args.evidence_db and not experiment_control["expired"]:
                direct_events, cursor_state = direct_evidence_events(
                    args.evidence_db,
                    cursor_path=args.output_dir / "evidence_cursor.json",
                    allowlist=allowlist,
                    city_timezones=city_timezones,
                    collector_run_start_wall_ns=args.collector_run_start_wall_ns,
                    initialize_at_current=not args.initialize_evidence_from_start,
                )
            elif experiment_control["expired"]:
                direct_events = []
            result = run_probe(
                source_events_root=args.source_events_root or Path("."),
                market_books_latest=args.market_books_latest,
                output_dir=args.output_dir,
                live=args.live,
                confirm_live=args.confirm_live,
                place_fn=place_fn,
                max_source_age_sec=args.max_source_age_sec,
                max_observation_delay_sec=args.max_observation_delay_sec,
                official_fee_rate=args.official_fee_rate,
                allowlist=allowlist,
                market_units=market_units,
                live_eligible_cities=live_eligible_cities,
                capture_demands_jsonl=args.capture_demands_jsonl,
                pause_file=args.pause_file,
                events_override=direct_events,
                allow_clock_invalid_same_boot_monotonic_probe=args.allow_clock_invalid_same_boot_monotonic_probe,
                clock_uncertainty_ms=args.clock_uncertainty_ms,
                boot_monotonic_start_ns=(
                    args.boot_monotonic_start_ns
                    if args.boot_monotonic_start_ns is not None else 0
                ),
                source_stream_last_received_at_utc=(
                    cursor_state.get("latest_transport_received_at_utc")
                    if cursor_state is not None
                    else None
                ),
                max_stream_silence_sec=args.max_stream_silence_sec,
                monitor_source_stream=bool(args.evidence_db),
                fetch_book_fn=fetch_fresh_book,
                market_proxy=args.market_proxy or os.environ.get("WEATHER_MARKET_PROXY_URL", ""),
                book_timeout_sec=args.book_timeout_sec,
                code_identity=args.code_identity,
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
