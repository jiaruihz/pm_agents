#!/usr/bin/env python3
"""Zero-notional WU first-seen and UMA proposal-race shadow.

The runner never imports an order/signing client and never constructs or sends
a transaction.  It discovers current Polymarket WU temperature events, polls
only configured local hot windows, computes the final native-unit extreme once
the next local date first appears, and later reconciles public UMA proposal
timestamps for the same market requests.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_sources import (  # noqa: E402
    FetchSettings,
    ObservationSourceRequest,
    fetch_observation_source,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.source_policy import canonical_city_name  # noqa: E402
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json  # noqa: E402


UTC = timezone.utc
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
SUBGRAPHS = {
    "polygon_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-optimistic-oracle-v2/1.1.0/gn",
    "polygon_managed_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-managed-optimistic-oracle-v2/1.0.5/gn",
}
TEMPERATURE_HEX = "74656d7065726174757265"
FIRST_NEXT_DATE_TEXT = "can not resolve until the first data point for the following date has been published"
TEMP_TITLE_RE = re.compile(
    r"(?i)^(highest|lowest) temperature in (.+?) on ([A-Z][a-z]+ \d{1,2})(?:,? (\d{4}))?\?$"
)
WU_URL_RE = re.compile(
    r"wunderground\.com/history/daily/([^/\s]+)/[^\s]*?/([A-Z0-9]{4})(?:[.\s]|$)", re.I
)
NATIVE_UNIT_RE = re.compile(r"degrees (Celsius|Fahrenheit)", re.I)
QUESTION_RE = re.compile(
    r"(?i)\bbe\s+(-?\d+(?:\.\d+)?)°([CF])(?:\s+(or below|or higher))?\s+on\b"
)
MARKET_ID_RE = re.compile(r"(?i)market[_ ]?id\s*[:=]\s*(\d+)")
REQUEST_FIELDS = """
id ancillaryData proposer proposedPrice proposalTimestamp proposalHash
customLiveness reward finalFee bond state settlementTimestamp disputeTimestamp
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def producer_build_id() -> str:
    explicit = os.environ.get("WEATHER_PRODUCER_BUILD_ID", "").strip()
    if explicit:
        return explicit
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=2
        ).strip()
    except Exception:
        return "unknown"


def arith_round(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def parse_event_identity(title: str) -> tuple[str, str, date] | None:
    match = TEMP_TITLE_RE.match(str(title or "").strip())
    if not match:
        return None
    raw_city = re.sub(r"\s*\([^)]*\)\s*$", "", match.group(2)).strip()
    raw_city = {"Los Angeles": "LA", "New York City": "NYC"}.get(raw_city, raw_city)
    city = canonical_city_name(raw_city)
    year = match.group(4) or str(utc_now().year)
    target_date = datetime.strptime(f"{match.group(3)} {year}", "%B %d %Y").date()
    return match.group(1).lower(), city, target_date


def parse_wu_contract(description: str) -> tuple[str, str, str] | None:
    text = str(description or "")
    url_match = WU_URL_RE.search(text)
    unit_match = NATIVE_UNIT_RE.search(text)
    if not url_match or not unit_match or FIRST_NEXT_DATE_TEXT not in text.lower():
        return None
    native_unit = "C" if unit_match.group(1).lower() == "celsius" else "F"
    return url_match.group(2).upper(), url_match.group(1).upper(), native_unit


def parse_market_question(question: str) -> dict[str, Any] | None:
    match = QUESTION_RE.search(str(question or ""))
    if not match:
        return None
    relation = str(match.group(3) or "exact").lower().replace(" ", "_")
    return {
        "threshold": arith_round(float(match.group(1))),
        "unit": match.group(2).upper(),
        "relation": relation,
    }


def outcome_for_market(question: str, *, final_value: int, native_unit: str) -> dict[str, Any]:
    parsed = parse_market_question(question)
    if parsed is None:
        raise ValueError(f"unsupported temperature market question: {question!r}")
    if parsed["unit"] != native_unit:
        raise ValueError(
            f"question/native unit mismatch question={parsed['unit']} source={native_unit}"
        )
    threshold = int(parsed["threshold"])
    relation = parsed["relation"]
    if relation == "or_below":
        yes = final_value <= threshold
    elif relation == "or_higher":
        yes = final_value >= threshold
    else:
        yes = final_value == threshold
    return {**parsed, "yes": yes, "proposed_price": 1.0 if yes else 0.0}


def choose_single_request(candidates: list[dict[str, Any]], final_value: int) -> str:
    exact_no = [
        row
        for row in candidates
        if row["relation"] == "exact" and row["proposed_price"] == 0.0
    ]
    plus_two = [row for row in exact_no if row["threshold"] == final_value + 2]
    if plus_two:
        return str(plus_two[0]["candidate_id"])
    distance_two = [row for row in exact_no if abs(row["threshold"] - final_value) >= 2]
    if distance_two:
        distance_two.sort(key=lambda row: (abs(row["threshold"] - final_value), row["threshold"]))
        return str(distance_two[0]["candidate_id"])
    return str(exact_no[0]["candidate_id"]) if exact_no else str(candidates[0]["candidate_id"])


def make_session(proxy: str) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    session.headers.update({"Accept": "application/json", "User-Agent": "pm-agent-proposal-shadow/1.0"})
    return session


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    timeout: float,
) -> Any:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_gamma_events(
    session: requests.Session,
    *,
    tag_id: int,
    timeout: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = get_json(
            session,
            GAMMA_EVENTS_URL,
            params={"tag_id": tag_id, "closed": "false", "limit": 100, "offset": offset},
            timeout=timeout,
        )
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected Gamma payload: {type(batch).__name__}")
        rows.extend(row for row in batch if isinstance(row, dict))
        if len(batch) < 100:
            return rows
        offset += 100


def build_watches(
    events: Iterable[dict[str, Any]],
    *,
    config: dict[str, Any],
    discovered_at: datetime,
) -> dict[str, dict[str, Any]]:
    windows = config["city_windows"]
    grouped: dict[str, dict[str, Any]] = {}
    for raw_event in events:
        identity = parse_event_identity(str(raw_event.get("title") or ""))
        if identity is None:
            continue
        kind, city, target_date = identity
        if city not in windows or not raw_event.get("active", True) or raw_event.get("closed", False):
            continue
        markets = [row for row in raw_event.get("markets") or [] if isinstance(row, dict)]
        if not markets:
            continue
        description = str(markets[0].get("description") or raw_event.get("description") or "")
        contract = parse_wu_contract(description)
        if contract is None:
            continue
        station, country, native_unit = contract
        timezone_name = str(CITY_TIMEZONE.get(city) or "")
        if not timezone_name:
            continue
        market_rows = []
        for market in markets:
            parsed = parse_market_question(str(market.get("question") or ""))
            market_rows.append(
                {
                    "market_id": str(market.get("id") or ""),
                    "question": str(market.get("question") or ""),
                    "question_id": str(market.get("questionID") or ""),
                    "neg_risk_request_id": str(market.get("negRiskRequestID") or ""),
                    "uma_bond_usdc": float(market.get("umaBond") or 0),
                    "uma_reward_usdc": float(market.get("umaReward") or 0),
                    "custom_liveness_seconds": int(market.get("customLiveness") or 0),
                    "question_parse_ok": parsed is not None,
                }
            )
        next_midnight = datetime.combine(
            target_date + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo(timezone_name)
        ).astimezone(UTC)
        window = windows[city]
        watch_id = stable_id("wu_watch", city, station, target_date.isoformat(), native_unit)
        grouped.setdefault(
            watch_id,
            {
                "watch_id": watch_id,
                "city": city,
                "station": station,
                "weather_com_country": country,
                "native_unit": native_unit,
                "timezone_name": timezone_name,
                "target_date": target_date.isoformat(),
                "next_date": (target_date + timedelta(days=1)).isoformat(),
                "hot_start_utc": iso(next_midnight + timedelta(minutes=float(window["start_minute"]))),
                "hot_end_utc": iso(next_midnight + timedelta(minutes=float(window["end_minute"]))),
                "poll_seconds": float(window["poll_seconds"]),
                "events": [],
                "discovered_at_utc": iso(discovered_at),
            },
        )
        grouped[watch_id]["events"].append(
            {
                "event_id": str(raw_event.get("id") or ""),
                "event_slug": str(raw_event.get("slug") or ""),
                "title": str(raw_event.get("title") or ""),
                "kind": kind,
                "description": description,
                "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
                "markets": market_rows,
            }
        )
    return grouped


def merge_watches(
    state: dict[str, Any], watches: dict[str, dict[str, Any]], *, runtime_started_at: datetime
) -> list[dict[str, Any]]:
    stored = state.setdefault("watches", {})
    discoveries: list[dict[str, Any]] = []
    for watch_id, incoming in watches.items():
        existing = stored.get(watch_id, {})
        dynamic = {
            key: existing[key]
            for key in (
                "first_empty_poll_at_utc",
                "source_first_seen_at_utc",
                "source_observation_utc",
                "hypothetical_ready_at_utc",
                "final_values",
                "selected_candidate_ids",
                "candidate_market_ids",
                "next_poll_at_utc",
                "proposal_by_market",
            )
            if key in existing
        }
        incoming.update(dynamic)
        incoming["runtime_started_at_utc"] = iso(runtime_started_at)
        stored[watch_id] = incoming
        if not existing:
            discoveries.append(incoming)
    return discoveries


def fetch_source_watch(
    watch: dict[str, Any], *, timeout_sec: float, http_client: httpx.Client
) -> dict[str, Any]:
    started = utc_now()
    request = ObservationSourceRequest(
        city=watch["city"],
        station_or_feed=watch["station"],
        target_date=watch["next_date"],
        timezone_name=watch["timezone_name"],
        source_key="weather_com_history_hourly",
        metadata={
            "native_unit": watch["native_unit"],
            "weather_com_country": watch["weather_com_country"],
        },
    )
    settings = FetchSettings(
        timeout_sec=timeout_sec,
        proxy_candidates=(None,),
        http_client=http_client,
    )
    try:
        next_result = fetch_observation_source(request, settings)
    except Exception as exc:
        return {
            "watch_id": watch["watch_id"],
            "status": "fetch_failed",
            "fetch_started_at_utc": iso(started),
            "fetch_finished_at_utc": iso(utc_now()),
            "error": f"{type(exc).__name__}: {exc}",
        }
    next_records = sorted(next_result.records, key=lambda row: row.obs_ts_utc)
    finished = utc_now()
    base = {
        "watch_id": watch["watch_id"],
        "status": "empty" if not next_records else "source_first_seen",
        "fetch_started_at_utc": iso(started),
        "fetch_finished_at_utc": iso(finished),
        "fetch_latency_ms": next_result.latency_ms,
        "source_payload_hash": next_result.metadata.get("raw_payload_hash"),
    }
    if not next_records:
        return base
    first = next_records[0]
    base.update(
        {
            "source_observation_utc": first.obs_ts_utc,
            "source_first_seen_at_utc": next_result.fetched_at_utc or iso(finished),
            "source_native_value": first.metadata.get("native_temp"),
        }
    )
    target_request = ObservationSourceRequest(
        city=watch["city"],
        station_or_feed=watch["station"],
        target_date=watch["target_date"],
        timezone_name=watch["timezone_name"],
        source_key="weather_com_history_hourly",
        metadata={
            "native_unit": watch["native_unit"],
            "weather_com_country": watch["weather_com_country"],
        },
    )
    try:
        target_result = fetch_observation_source(target_request, settings)
    except Exception as exc:
        base["status"] = "target_fetch_failed"
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base
    values = [
        float(row.metadata["native_temp"])
        for row in target_result.records
        if row.metadata.get("native_temp") is not None
    ]
    if not values:
        base["status"] = "target_empty"
        return base
    base.update(
        {
            "status": "hypothetical_ready",
            "target_record_count": len(values),
            "target_payload_hash": target_result.metadata.get("raw_payload_hash"),
            "target_fetch_latency_ms": target_result.latency_ms,
            "final_values": {
                "highest": arith_round(max(values)),
                "lowest": arith_round(min(values)),
            },
            "hypothetical_ready_at_utc": iso(utc_now()),
        }
    )
    return base


def decode_ancillary(value: str) -> str:
    try:
        return bytes.fromhex(str(value).removeprefix("0x")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def fetch_proposals(
    session: requests.Session,
    *,
    lower_timestamp: int,
    timeout: float,
) -> list[dict[str, Any]]:
    by_market: dict[str, dict[str, Any]] = {}
    query = (
        "{optimisticPriceRequests(first:1000,"
        f"where:{{ancillaryData_contains:\"{TEMPERATURE_HEX}\",proposalTimestamp_gte:{lower_timestamp}}},"
        f"orderBy:proposalTimestamp,orderDirection:asc){{{REQUEST_FIELDS}}}}}"
    )
    for subgraph_name, url in SUBGRAPHS.items():
        response = session.post(url, json={"query": query}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"{subgraph_name}: {payload['errors'][:1]}")
        for row in payload.get("data", {}).get("optimisticPriceRequests", []):
            text = decode_ancillary(str(row.get("ancillaryData") or ""))
            match = MARKET_ID_RE.search(text)
            if not match:
                continue
            market_id = match.group(1)
            candidate = {**row, "market_id": market_id, "subgraph": subgraph_name}
            if market_id not in by_market or int(candidate["proposalTimestamp"]) < int(
                by_market[market_id]["proposalTimestamp"]
            ):
                by_market[market_id] = candidate
    return list(by_market.values())


def quantiles(values: list[float]) -> dict[str, float | int | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"n": 0, "median": None, "p90": None}

    def q(frac: float) -> float:
        pos = (len(clean) - 1) * frac
        lo, hi = math.floor(pos), math.ceil(pos)
        return clean[lo] if lo == hi else clean[lo] * (hi - pos) + clean[hi] * (pos - lo)

    return {"n": len(clean), "median": statistics.median(clean), "p90": q(0.9)}


def append_partitioned(output_dir: Path, filename: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    append_jsonl(output_dir / filename, rows)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = str(row.get("observed_at_utc") or row.get("generated_at_utc") or iso(utc_now()))[:10]
        by_day.setdefault(day, []).append(row)
    for day, day_rows in by_day.items():
        append_jsonl(output_dir / day / filename, day_rows)


def emit_once(
    output_dir: Path,
    state: dict[str, Any],
    filename: str,
    row: dict[str, Any],
) -> bool:
    record_id = str(row["record_id"])
    emitted = state.setdefault("emitted_record_ids", {})
    if record_id in emitted:
        return False
    append_partitioned(output_dir, filename, [row])
    emitted[record_id] = str(row.get("observed_at_utc") or iso(utc_now()))
    return True


def build_lineage_rows(
    watch: dict[str, Any],
    result: dict[str, Any],
    *,
    config: dict[str, Any],
    build_id: str,
) -> dict[str, list[dict[str, Any]]]:
    observed_at = result["hypothetical_ready_at_utc"]
    pit_scorable = bool(watch.get("first_empty_poll_at_utc"))
    envelope_id = stable_id("event_envelope", watch["watch_id"], result["source_first_seen_at_utc"])
    event_row = {
        "schema_version": "polymarket_weather_proposal_event_envelope_v1",
        "record_id": envelope_id,
        "event_envelope_id": envelope_id,
        "event_type": "wu_next_date_first_seen",
        "strategy_family": config["strategy_family"],
        "strategy_instance": config["strategy_instance"],
        "watch_id": watch["watch_id"],
        "city": watch["city"],
        "station": watch["station"],
        "target_date": watch["target_date"],
        "source_observation_utc": result["source_observation_utc"],
        "source_first_seen_at_utc": result["source_first_seen_at_utc"],
        "observed_at_utc": observed_at,
        "ingested_at_utc": iso(utc_now()),
        "pit_scorable": pit_scorable,
        "pit_blocker": "" if pit_scorable else "no_prior_empty_poll_in_hot_window",
        "source_payload_hash": result.get("source_payload_hash"),
        "producer_build_id": build_id,
    }
    contexts: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    intents: list[dict[str, Any]] = []
    expected = config["expected_contract"]
    for event in watch["events"]:
        final_value = int(result["final_values"][event["kind"]])
        context_id = stable_id("decision_context", envelope_id, event["event_id"])
        model_output_id = stable_id("model_output", context_id, final_value)
        contexts.append(
            {
                "schema_version": "polymarket_weather_proposal_decision_context_v1",
                "record_id": context_id,
                "decision_context_id": context_id,
                "event_envelope_id": envelope_id,
                "event_id": event["event_id"],
                "event_slug": event["event_slug"],
                "kind": event["kind"],
                "native_unit": watch["native_unit"],
                "description_sha256": event["description_sha256"],
                "rules_exact_match": True,
                "pit_scorable": pit_scorable,
                "observed_at_utc": observed_at,
                "producer_build_id": build_id,
            }
        )
        outputs.append(
            {
                "schema_version": "polymarket_weather_proposal_model_output_v1",
                "record_id": model_output_id,
                "model_output_id": model_output_id,
                "decision_context_id": context_id,
                "model_type": "deterministic_wu_daily_extreme",
                "final_native_value": final_value,
                "native_unit": watch["native_unit"],
                "target_record_count": result["target_record_count"],
                "target_payload_hash": result.get("target_payload_hash"),
                "hypothetical_ready_at_utc": observed_at,
                "producer_build_id": build_id,
            }
        )
        event_candidates: list[dict[str, Any]] = []
        for market in event["markets"]:
            outcome = outcome_for_market(
                market["question"], final_value=final_value, native_unit=watch["native_unit"]
            )
            candidate_id = stable_id("signal_candidate", model_output_id, market["market_id"])
            contract_match = (
                market["uma_bond_usdc"] == float(expected["bond_usdc"])
                and market["uma_reward_usdc"] == float(expected["reward_usdc"])
                and market["custom_liveness_seconds"] == int(expected["custom_liveness_seconds"])
            )
            candidate = {
                "schema_version": "polymarket_weather_proposal_signal_candidate_v1",
                "record_id": candidate_id,
                "candidate_id": candidate_id,
                "candidate_grain_version": "uma_request_v1",
                "model_output_id": model_output_id,
                "decision_context_id": context_id,
                "event_id": event["event_id"],
                "market_id": market["market_id"],
                "question_id": market["question_id"],
                "neg_risk_request_id": market["neg_risk_request_id"],
                "question": market["question"],
                **outcome,
                "contract_match": contract_match,
                "pit_scorable": pit_scorable,
                "hypothetical_ready_at_utc": observed_at,
                "execution_mode": "zero_notional_shadow",
                "submission_enabled": False,
                "actual_deposit_usdc": 0.0,
                "producer_build_id": build_id,
            }
            candidates.append(candidate)
            event_candidates.append(candidate)
        selected_id = choose_single_request(event_candidates, final_value)
        selected = next(row for row in event_candidates if row["candidate_id"] == selected_id)
        intent_id = stable_id("proposal_intent", selected_id)
        intents.append(
            {
                "schema_version": "polymarket_weather_proposal_intent_v1",
                "record_id": intent_id,
                "proposal_intent_id": intent_id,
                "candidate_id": selected_id,
                "selection_rule": "exact_plus_2_no_then_nearest_safe_no",
                "market_id": selected["market_id"],
                "would_propose_price": selected["proposed_price"],
                "required_total_deposit_usdc": float(expected["total_deposit_usdc"]),
                "potential_reward_usdc": float(expected["reward_usdc"]),
                "execution_mode": "zero_notional_shadow",
                "submission_enabled": False,
                "private_key_access": False,
                "actual_deposit_usdc": 0.0,
                "observed_at_utc": observed_at,
                "producer_build_id": build_id,
            }
        )
    return {
        "event_envelopes.jsonl": [event_row],
        "decision_contexts.jsonl": contexts,
        "model_outputs.jsonl": outputs,
        "signal_candidates.jsonl": candidates,
        "proposal_intents.jsonl": intents,
    }


def process_poll_result(
    result: dict[str, Any],
    *,
    state: dict[str, Any],
    output_dir: Path,
    config: dict[str, Any],
    build_id: str,
) -> None:
    watch = state["watches"][result["watch_id"]]
    metrics = state.setdefault("metrics", {})
    metrics["source_poll_count"] = int(metrics.get("source_poll_count", 0)) + 1
    poll_day = str(result["fetch_started_at_utc"])[:10]
    daily_counts = state.setdefault("daily_source_poll_counts", {})
    daily_counts[poll_day] = int(daily_counts.get(poll_day, 0)) + 1
    if result.get("fetch_latency_ms") is not None:
        metrics.setdefault("source_fetch_latencies_ms", []).append(float(result["fetch_latency_ms"]))
        metrics["source_fetch_latencies_ms"] = metrics["source_fetch_latencies_ms"][-5000:]
    if result["status"] == "fetch_failed":
        metrics["source_poll_error_count"] = int(metrics.get("source_poll_error_count", 0)) + 1
        state["last_source_error"] = result
        return
    if result["status"] == "empty":
        watch.setdefault("first_empty_poll_at_utc", result["fetch_finished_at_utc"])
        return
    if watch.get("hypothetical_ready_at_utc"):
        return
    watch.setdefault("source_first_seen_at_utc", result.get("source_first_seen_at_utc"))
    watch.setdefault("source_observation_utc", result.get("source_observation_utc"))
    if result.get("hypothetical_ready_at_utc"):
        watch["hypothetical_ready_at_utc"] = result["hypothetical_ready_at_utc"]
    if result.get("final_values"):
        watch["final_values"] = result["final_values"]
    trigger_id = stable_id(
        "source_trigger", watch["watch_id"], watch["source_first_seen_at_utc"]
    )
    trigger_row = {
        "schema_version": "polymarket_weather_proposal_source_trigger_v1",
        "record_id": trigger_id,
        "watch_id": watch["watch_id"],
        "city": watch["city"],
        "station": watch["station"],
        "target_date": watch["target_date"],
        "status": result["status"],
        "source_observation_utc": watch.get("source_observation_utc"),
        "source_first_seen_at_utc": watch.get("source_first_seen_at_utc"),
        "hypothetical_ready_at_utc": result.get("hypothetical_ready_at_utc"),
        "source_fetch_latency_ms": result.get("fetch_latency_ms"),
        "target_fetch_latency_ms": result.get("target_fetch_latency_ms"),
        "first_empty_poll_at_utc": watch.get("first_empty_poll_at_utc"),
        "pit_scorable": bool(watch.get("first_empty_poll_at_utc")),
        "observed_at_utc": result.get("hypothetical_ready_at_utc") or iso(utc_now()),
        "producer_build_id": build_id,
    }
    emit_once(output_dir, state, "source_triggers.jsonl", trigger_row)
    if result["status"] != "hypothetical_ready":
        return
    result = {
        **result,
        "source_first_seen_at_utc": watch["source_first_seen_at_utc"],
        "source_observation_utc": watch["source_observation_utc"],
    }
    lineage = build_lineage_rows(watch, result, config=config, build_id=build_id)
    for filename, rows in lineage.items():
        for row in rows:
            emit_once(output_dir, state, filename, row)
    watch["selected_candidate_ids"] = [
        row["candidate_id"] for row in lineage["proposal_intents.jsonl"]
    ]
    watch["candidate_market_ids"] = [
        row["market_id"] for row in lineage["signal_candidates.jsonl"]
    ]
    metrics["hypothetical_ready_count"] = int(metrics.get("hypothetical_ready_count", 0)) + 1


def reconcile_proposals(
    proposals: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    output_dir: Path,
    build_id: str,
) -> None:
    proposal_by_market = {str(row["market_id"]): row for row in proposals}
    for watch in state.get("watches", {}).values():
        if not watch.get("hypothetical_ready_at_utc"):
            continue
        for event in watch["events"]:
            for market in event["markets"]:
                market_id = market["market_id"]
                proposal = proposal_by_market.get(market_id)
                if proposal is None:
                    continue
                observed_map = watch.setdefault("proposal_by_market", {})
                if market_id in observed_map:
                    continue
                proposal_at = datetime.fromtimestamp(int(proposal["proposalTimestamp"]), UTC)
                ready_at = parse_dt(watch["hypothetical_ready_at_utc"])
                first_seen_at = parse_dt(watch["source_first_seen_at_utc"])
                row_id = stable_id("proposal_observation", market_id, proposal["proposalTimestamp"])
                row = {
                    "schema_version": "polymarket_weather_proposal_observation_v1",
                    "record_id": row_id,
                    "watch_id": watch["watch_id"],
                    "event_id": event["event_id"],
                    "market_id": market_id,
                    "proposer": proposal.get("proposer"),
                    "proposed_price_raw": proposal.get("proposedPrice"),
                    "proposal_block_timestamp_utc": iso(proposal_at),
                    "proposal_observed_at_utc": iso(utc_now()),
                    "proposal_hash": proposal.get("proposalHash"),
                    "subgraph": proposal.get("subgraph"),
                    "source_first_seen_to_proposal_block_seconds": (
                        proposal_at - first_seen_at
                    ).total_seconds()
                    if first_seen_at
                    else None,
                    "hypothetical_ready_to_proposal_block_seconds": (
                        proposal_at - ready_at
                    ).total_seconds()
                    if ready_at
                    else None,
                    "observed_at_utc": iso(utc_now()),
                    "producer_build_id": build_id,
                }
                emit_once(output_dir, state, "proposal_observations.jsonl", row)
                observed_map[market_id] = row


def write_health(
    output_dir: Path,
    state: dict[str, Any],
    *,
    config: dict[str, Any],
    build_id: str,
    runtime_started_at: datetime,
) -> None:
    now = utc_now()
    watches = list(state.get("watches", {}).values())
    hot = [
        row
        for row in watches
        if (parse_dt(row["hot_start_utc"]) or now) <= now <= (parse_dt(row["hot_end_utc"]) or now)
        and not row.get("source_first_seen_at_utc")
    ]
    latencies = [float(value) for value in state.get("metrics", {}).get("source_fetch_latencies_ms", [])]
    compact_metrics = {
        key: value
        for key, value in state.get("metrics", {}).items()
        if key != "source_fetch_latencies_ms"
    }
    status = "ok"
    if state.get("last_discovery_error") or not state.get("last_discovery_at_utc"):
        status = "warning"
    payload = {
        "schema_version": "polymarket_weather_proposal_shadow_health_v1",
        "status": status,
        "generated_at_utc": iso(now),
        "runtime_started_at_utc": iso(runtime_started_at),
        "strategy_family": config["strategy_family"],
        "strategy_instance": config["strategy_instance"],
        "execution_mode": "zero_notional_shadow",
        "submission_enabled": False,
        "live_enabled": False,
        "private_key_access": False,
        "actual_orders": 0,
        "actual_deposit_usdc": 0.0,
        "producer_build_id": build_id,
        "configured_cities": sorted(config["city_windows"]),
        "source_poll_budget_per_day": config["source_poll_budget_per_day"],
        "retention_days": config["retention_days"],
        "watch_count": len(watches),
        "hot_watch_count": len(hot),
        "hot_watches": [row["watch_id"] for row in hot],
        "first_seen_count": sum(bool(row.get("source_first_seen_at_utc")) for row in watches),
        "hypothetical_ready_count": sum(bool(row.get("hypothetical_ready_at_utc")) for row in watches),
        "proposal_observed_market_count": sum(
            len(row.get("proposal_by_market", {})) for row in watches
        ),
        "daily_source_poll_counts": state.get("daily_source_poll_counts", {}),
        "metrics": {**compact_metrics, "source_fetch_latency_ms": quantiles(latencies)},
        "last_discovery_at_utc": state.get("last_discovery_at_utc"),
        "last_discovery_error": state.get("last_discovery_error"),
        "last_proposal_reconcile_at_utc": state.get("last_proposal_reconcile_at_utc"),
        "last_proposal_error": state.get("last_proposal_error"),
        "last_source_error": state.get("last_source_error"),
    }
    write_json(output_dir / "latest.json", payload)


def load_config(path: Path, *, cities: list[str] | None) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if cities:
        wanted = {canonical_city_name(city) for city in cities}
        config["city_windows"] = {
            city: window for city, window in config["city_windows"].items() if city in wanted
        }
    if not config["city_windows"]:
        raise ValueError("no configured cities remain")
    return config


def run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), cities=args.cities)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state = read_json(state_path, {})
    state.setdefault("schema_version", "polymarket_weather_proposal_shadow_state_v1")
    runtime_started_at = utc_now()
    build_id = producer_build_id()
    market_session = make_session(args.market_proxy)
    source_http = httpx.Client(
        timeout=float(config["wu_timeout_seconds"]),
        trust_env=False,
        limits=httpx.Limits(max_connections=int(config["max_source_workers"]), max_keepalive_connections=int(config["max_source_workers"])),
    )

    def discover() -> dict[str, dict[str, Any]]:
        events = fetch_gamma_events(
            market_session,
            tag_id=int(config["weather_tag_id"]),
            timeout=float(config["gamma_timeout_seconds"]),
        )
        return build_watches(events, config=config, discovered_at=utc_now())

    try:
        discoveries = merge_watches(state, discover(), runtime_started_at=runtime_started_at)
        state["last_discovery_at_utc"] = iso(utc_now())
        state["last_discovery_error"] = None
        for watch in discoveries:
            row = {
                "schema_version": "polymarket_weather_proposal_market_discovery_v1",
                "record_id": stable_id("market_discovery", watch["watch_id"]),
                "watch_id": watch["watch_id"],
                "city": watch["city"],
                "station": watch["station"],
                "target_date": watch["target_date"],
                "hot_start_utc": watch["hot_start_utc"],
                "hot_end_utc": watch["hot_end_utc"],
                "poll_seconds": watch["poll_seconds"],
                "events": watch["events"],
                "observed_at_utc": watch["discovered_at_utc"],
                "producer_build_id": build_id,
            }
            emit_once(output_dir, state, "market_discoveries.jsonl", row)
    except Exception as exc:
        state["last_discovery_error"] = f"{type(exc).__name__}: {exc}"

    write_json(state_path, state)
    write_health(
        output_dir,
        state,
        config=config,
        build_id=build_id,
        runtime_started_at=runtime_started_at,
    )
    if args.command == "cycle":
        source_http.close()
        market_session.close()
        return 0 if state.get("last_discovery_at_utc") and state.get("watches") else 2

    started_monotonic = time.monotonic()
    next_discovery = time.monotonic() + float(config["discovery_interval_seconds"])
    next_proposal = time.monotonic()
    next_health = time.monotonic()
    with ThreadPoolExecutor(max_workers=int(config["max_source_workers"])) as source_pool:
        while True:
            now = utc_now()
            due: list[dict[str, Any]] = []
            poll_day = now.date().isoformat()
            daily_poll_count = int(state.get("daily_source_poll_counts", {}).get(poll_day, 0))
            budget_available = daily_poll_count < int(config["source_poll_budget_per_day"])
            for watch in state.get("watches", {}).values():
                hot_start = parse_dt(watch["hot_start_utc"])
                hot_end = parse_dt(watch["hot_end_utc"])
                if not hot_start or not hot_end or not (hot_start <= now <= hot_end):
                    continue
                if watch.get("hypothetical_ready_at_utc"):
                    continue
                next_poll = parse_dt(watch.get("next_poll_at_utc"))
                if not budget_available or (next_poll and now < next_poll):
                    continue
                due.append(watch)
                watch["next_poll_at_utc"] = iso(now + timedelta(seconds=float(watch["poll_seconds"])))
            remaining_budget = max(
                0, int(config["source_poll_budget_per_day"]) - daily_poll_count
            )
            due = due[:remaining_budget]
            futures = [
                source_pool.submit(
                    fetch_source_watch,
                    watch,
                    timeout_sec=float(config["wu_timeout_seconds"]),
                    http_client=source_http,
                )
                for watch in due
            ]
            for future in futures:
                process_poll_result(
                    future.result(),
                    state=state,
                    output_dir=output_dir,
                    config=config,
                    build_id=build_id,
                )

            monotonic_now = time.monotonic()
            hot_or_imminent = any(
                (parse_dt(watch["hot_start_utc"]) or now) - timedelta(seconds=30)
                <= now
                <= (parse_dt(watch["hot_end_utc"]) or now)
                and not watch.get("source_first_seen_at_utc")
                for watch in state.get("watches", {}).values()
            )
            if monotonic_now >= next_discovery and not hot_or_imminent:
                try:
                    discoveries = merge_watches(
                        state, discover(), runtime_started_at=runtime_started_at
                    )
                    state["last_discovery_at_utc"] = iso(utc_now())
                    state["last_discovery_error"] = None
                    for watch in discoveries:
                        emit_once(
                            output_dir,
                            state,
                            "market_discoveries.jsonl",
                            {
                                "schema_version": "polymarket_weather_proposal_market_discovery_v1",
                                "record_id": stable_id("market_discovery", watch["watch_id"]),
                                "watch_id": watch["watch_id"],
                                "city": watch["city"],
                                "station": watch["station"],
                                "target_date": watch["target_date"],
                                "hot_start_utc": watch["hot_start_utc"],
                                "hot_end_utc": watch["hot_end_utc"],
                                "poll_seconds": watch["poll_seconds"],
                                "events": watch["events"],
                                "observed_at_utc": watch["discovered_at_utc"],
                                "producer_build_id": build_id,
                            },
                        )
                except Exception as exc:
                    state["last_discovery_error"] = f"{type(exc).__name__}: {exc}"
                next_discovery = monotonic_now + float(config["discovery_interval_seconds"])

            ready_times = [
                parse_dt(watch.get("source_first_seen_at_utc"))
                for watch in state.get("watches", {}).values()
                if watch.get("source_first_seen_at_utc")
                and len(watch.get("proposal_by_market", {})) < sum(
                    len(event["markets"]) for event in watch["events"]
                )
            ]
            ready_times = [value for value in ready_times if value is not None]
            if monotonic_now >= next_proposal and ready_times and not hot_or_imminent:
                try:
                    proposals = fetch_proposals(
                        market_session,
                        lower_timestamp=int(min(ready_times).timestamp()) - 600,
                        timeout=float(config["gamma_timeout_seconds"]),
                    )
                    reconcile_proposals(
                        proposals, state=state, output_dir=output_dir, build_id=build_id
                    )
                    state["last_proposal_reconcile_at_utc"] = iso(utc_now())
                    state["last_proposal_error"] = None
                except Exception as exc:
                    state["last_proposal_error"] = f"{type(exc).__name__}: {exc}"
                next_proposal = monotonic_now + float(
                    config["proposal_reconcile_interval_seconds"]
                )

            if monotonic_now >= next_health:
                write_json(state_path, state)
                write_health(
                    output_dir,
                    state,
                    config=config,
                    build_id=build_id,
                    runtime_started_at=runtime_started_at,
                )
                next_health = monotonic_now + float(config["health_interval_seconds"])
            if args.run_seconds and monotonic_now - started_monotonic >= args.run_seconds:
                break
            time.sleep(float(config["loop_interval_seconds"]))
    write_json(state_path, state)
    write_health(
        output_dir,
        state,
        config=config,
        build_id=build_id,
        runtime_started_at=runtime_started_at,
    )
    source_http.close()
    market_session.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("cycle", "loop"), nargs="?", default="cycle")
    parser.add_argument(
        "--config", default=str(ROOT / "configs/weather/proposal_reward_shadow_v1.json")
    )
    parser.add_argument(
        "--output-dir",
        default="/Volumes/jrs/weather_data_feed_service_runtime/output/proposal_reward_shadow_v1",
    )
    parser.add_argument("--market-proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--run-seconds", type=float, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
