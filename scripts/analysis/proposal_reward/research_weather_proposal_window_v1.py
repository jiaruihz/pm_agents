#!/usr/bin/env python3
"""Audit Polymarket temperature-market rules and UMA proposal timing.

This is a read-only research runner.  It joins public Gamma weather-market
metadata to public UMA OOV2 subgraph requests using the ``market_id`` embedded
in ancillary data.  The principal timing grain is one UMA request (one binary
temperature bracket); event summaries group the sibling brackets for a city,
temperature kind, and target date.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.source_policy import canonical_city_name  # noqa: E402


UTC = timezone.utc
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
WEATHER_TAG_ID = 84
SUBGRAPHS = {
    "polygon_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-optimistic-oracle-v2/1.1.0/gn",
    "polygon_managed_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-managed-optimistic-oracle-v2/1.0.5/gn",
}
REQUEST_FIELDS = """
id identifier ancillaryData requester currency reward finalFee bond
customLiveness proposer proposedPrice settlementPrice state
requestTimestamp proposalTimestamp disputeTimestamp settlementTimestamp
proposalHash disputeHash settlementHash
"""
MARKET_ID_RE = re.compile(r"(?i)market[_ ]?id\s*[:=]\s*(\d+)")
TEMP_TITLE_RE = re.compile(
    r"(?i)^(highest|lowest) temperature in (.+?) on "
    r"([A-Z][a-z]+ \d{1,2})(?:,? (\d{4}))?\?$"
)
TEMP_MARKET_QUESTION_RE = re.compile(
    r"(?i)^Will the (highest|lowest) temperature in (.+?) be .+ on "
    r"([A-Z][a-z]+ \d{1,2})\?$"
)
DESCRIPTION_DATE_RE = re.compile(r"\bon (\d{1,2}) ([A-Z][a-z]{2}) ['’](\d{2})\b")
WU_STATION_RE = re.compile(r"wunderground\.com/history/daily/[^\s.]+?/([A-Z0-9]{4})(?:\.|\s|$)", re.I)
WRH_STATION_RE = re.compile(r"[?&]site=([A-Z0-9]{4})\b", re.I)
FIRST_NEXT_DATE_TEXT = "can not resolve until the first data point for the following date has been published"
DATE_DATA_TEXT = "can not resolve until data for this date has been published"
TEMPERATURE_HEX = "74656d7065726174757265"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-proposal", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cutoff", default=datetime.now(UTC).isoformat())
    parser.add_argument("--out-dir", default="runtime/proposal_reward/weather_proposal_window_v1")
    parser.add_argument(
        "--wu-first-seen-root",
        default="/Volumes/jrs/weather_data_feed_service_runtime/output/wu_history_latency",
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_iso(ts: int | str | None) -> str:
    if ts in (None, ""):
        return ""
    return datetime.fromtimestamp(int(ts), UTC).isoformat()


def decode_ancillary(value: str) -> str:
    try:
        return bytes.fromhex(value.removeprefix("0x")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def extract_question(text: str) -> str:
    patterns = (
        r"(?is)q:\s*title:\s*(.*?)(?:,\s*description:)",
        r"(?is)q:\s*(.*?)(?:,\s*description:)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,")
    return ""


def extract_description(text: str) -> str:
    match = re.search(
        r"(?is),\s*description:\s*(.*?)(?:\s+market[_ ]?id\s*[:=]\s*\d+)",
        text,
    )
    return match.group(1).strip(" ,") if match else ""


def get_json(url: str, *, params: dict[str, Any], attempts: int = 6) -> Any:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=90)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # network boundary
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GET failed url={url}: {error}")


def post_graphql(url: str, query: str, attempts: int = 8) -> dict[str, Any]:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(url, json={"query": query}, timeout=90)
            if response.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"][:1]))
            return payload["data"]
        except Exception as exc:  # network boundary
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GraphQL failed: {error}")


def fetch_gamma_weather_events(*, closed: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = get_json(
            GAMMA_EVENTS_URL,
            params={
                "tag_id": WEATHER_TAG_ID,
                "closed": str(closed).lower(),
                "limit": 100,
                "offset": offset,
                "order": "id",
                "ascending": "true",
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected Gamma payload type: {type(batch).__name__}")
        rows.extend(batch)
        print(f"Gamma closed={closed} offset={offset} rows={len(batch)}", flush=True)
        if len(batch) < 100:
            break
        offset += 100
    return rows


def fetch_requests_page(url: str, *, lower_ts: int, cutoff_ts: int) -> list[dict[str, Any]]:
    query = (
        "{optimisticPriceRequests(first:1000,"
        f"where:{{ancillaryData_contains:\"{TEMPERATURE_HEX}\",proposalTimestamp_gte:{lower_ts},proposalTimestamp_lte:{cutoff_ts}}},"
        f"orderBy:proposalTimestamp,orderDirection:asc){{{REQUEST_FIELDS}}}}}"
    )
    return post_graphql(url, query)["optimisticPriceRequests"]


def fetch_requests_at_timestamp(url: str, *, ts: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skip = 0
    while True:
        query = (
            "{optimisticPriceRequests(first:1000,"
            f"skip:{skip},where:{{ancillaryData_contains:\"{TEMPERATURE_HEX}\",proposalTimestamp:{ts}}}){{{REQUEST_FIELDS}}}}}"
        )
        batch = post_graphql(url, query)["optimisticPriceRequests"]
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        skip += 1000


def fetch_proposed_requests(url: str, *, min_ts: int, cutoff_ts: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    cursor = min_ts
    pages = 0
    while cursor <= cutoff_ts:
        batch = fetch_requests_page(url, lower_ts=cursor, cutoff_ts=cutoff_ts)
        pages += 1
        if not batch:
            break
        for row in batch:
            by_id[str(row["id"])] = row
        last_ts = int(batch[-1]["proposalTimestamp"])
        if len(batch) == 1000:
            for row in fetch_requests_at_timestamp(url, ts=last_ts):
                by_id[str(row["id"])] = row
        print(f"oracle page={pages} cursor={cursor} batch={len(batch)} unique={len(by_id)}", flush=True)
        cursor = last_ts + 1
    return sorted(by_id.values(), key=lambda row: (int(row["proposalTimestamp"]), str(row["id"])))


def load_or_fetch(path: Path, *, refresh: bool, fetcher: Any) -> Any:
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    value = fetcher()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return value


def rule_class(description: str) -> str:
    lowered = description.lower()
    if FIRST_NEXT_DATE_TEXT in lowered:
        return "first_next_date_point"
    if DATE_DATA_TEXT in lowered:
        return "date_data_published"
    return "other"


def parse_event_identity(title: str) -> tuple[str, str, date] | None:
    match = TEMP_TITLE_RE.match(title.strip())
    if not match:
        return None
    kind = match.group(1).lower()
    raw_city = re.sub(r"\s*\([^)]*\)\s*$", "", match.group(2)).strip()
    raw_city = {"Los Angeles": "LA", "New York City": "NYC"}.get(raw_city, raw_city)
    city = canonical_city_name(raw_city)
    year = match.group(4) or "2026"
    target_date = datetime.strptime(f"{match.group(3)} {year}", "%B %d %Y").date()
    return kind, city, target_date


def canonical_market_city(raw_city: str) -> str:
    raw_city = re.sub(r"\s*\([^)]*\)\s*$", "", raw_city).strip()
    raw_city = {"Los Angeles": "LA", "New York City": "NYC"}.get(raw_city, raw_city)
    return canonical_city_name(raw_city)


def parse_ancillary_temperature_identity(question: str, description: str) -> tuple[str, str, date] | None:
    match = TEMP_MARKET_QUESTION_RE.match(question.strip())
    if not match:
        return None
    date_match = DESCRIPTION_DATE_RE.search(description)
    if not date_match:
        return None
    kind = match.group(1).lower()
    city = canonical_market_city(match.group(2))
    target_date = datetime.strptime(
        f"{date_match.group(1)} {date_match.group(2)} 20{date_match.group(3)}",
        "%d %b %Y",
    ).date()
    return kind, city, target_date


def source_station(description: str) -> str:
    for pattern in (WU_STATION_RE, WRH_STATION_RE):
        match = pattern.search(description)
        if match:
            return match.group(1).upper()
    return ""


def as_usdc(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    return int(raw) / 1_000_000


def safe_float(raw: Any) -> float | None:
    try:
        return None if raw in (None, "") else float(raw)
    except (TypeError, ValueError):
        return None


def quantile(values: Iterable[float], q: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    pos = (len(clean) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def summarize(values: Iterable[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return {
        "n": len(clean),
        "p10": quantile(clean, 0.10),
        "p25": quantile(clean, 0.25),
        "median": statistics.median(clean) if clean else None,
        "p75": quantile(clean, 0.75),
        "p90": quantile(clean, 0.90),
    }


def jsonl_write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_wu_first_seen(root: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return first collector sighting for each station and local date.

    A key is retained only when the monitor was already running before that
    local date began.  This avoids treating the monitor's initial backfill as
    point-in-time first-seen evidence.
    """
    first: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not root.exists():
        return first
    for path in sorted(root.glob("*/wu_history_latency.jsonl")):
        with path.open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") != "ok" or row.get("source") != "weather_com_history_hourly":
                    continue
                target_date = str(row.get("target_date") or "")
                timezone_name = str(row.get("timezone_name") or "")
                valid_local = str(row.get("valid_time_local") or "")
                station = str(row.get("station") or "").upper()
                if not target_date or not timezone_name or valid_local[:10] != target_date or not station:
                    continue
                local_midnight = datetime.combine(
                    date.fromisoformat(target_date), datetime.min.time(), tzinfo=ZoneInfo(timezone_name)
                ).astimezone(UTC)
                monitor_started = parse_utc(str(row.get("monitor_started_at_utc") or ""))
                if monitor_started > local_midnight:
                    continue
                first_seen = str(row.get("first_seen_at_utc") or row.get("ts_utc") or "")
                source_valid = str(row.get("valid_time_utc") or "")
                if not first_seen or not source_valid:
                    continue
                city = canonical_city_name(str(row.get("city") or ""))
                key = (city, target_date, station)
                candidate = {
                    "city": city,
                    "target_date": target_date,
                    "station": station,
                    "source_first_seen_utc": first_seen,
                    "source_valid_utc": source_valid,
                    "fetch_latency_ms": safe_float(row.get("fetch_latency_ms")),
                    "first_seen_lag_sec": safe_float(row.get("first_seen_lag_sec")),
                    "source_path": str(path),
                }
                if key not in first or first_seen < first[key]["source_first_seen_utc"]:
                    first[key] = candidate
    return first


def main() -> None:
    args = parse_args()
    out_dir = ROOT / args.out_dir
    cache_dir = out_dir / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    min_ts = int(parse_utc(args.min_proposal).timestamp())
    cutoff_dt = parse_utc(args.cutoff)
    cutoff_ts = int(cutoff_dt.timestamp())

    gamma_open = load_or_fetch(
        cache_dir / "gamma_weather_open.json",
        refresh=args.refresh,
        fetcher=lambda: fetch_gamma_weather_events(closed=False),
    )
    events_by_id: dict[str, dict[str, Any]] = {}
    for event in gamma_open:
        events_by_id[str(event.get("id"))] = event

    temp_events: dict[str, dict[str, Any]] = {}
    markets_by_id: dict[str, dict[str, Any]] = {}
    for event_id, event in events_by_id.items():
        identity = parse_event_identity(str(event.get("title") or ""))
        if identity is None:
            continue
        kind, city, target_date = identity
        description = str(event.get("description") or "")
        event_row = {
            "event_id": event_id,
            "slug": str(event.get("slug") or ""),
            "title": str(event.get("title") or ""),
            "kind": kind,
            "city": city,
            "target_date": target_date.isoformat(),
            "timezone_name": CITY_TIMEZONE.get(city, ""),
            "description": description,
            "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "rule_class": rule_class(description),
            "source_station": source_station(description),
            "active": bool(event.get("active")),
            "closed": bool(event.get("closed")),
            "market_count": len(event.get("markets") or []),
        }
        temp_events[event_id] = event_row
        for market in event.get("markets") or []:
            row = dict(market)
            row["event_id"] = event_id
            markets_by_id[str(market.get("id"))] = row

    oracle_rows: list[dict[str, Any]] = []
    for subgraph, url in SUBGRAPHS.items():
        cached = load_or_fetch(
            cache_dir / f"{subgraph}_{min_ts}_{cutoff_ts}.json",
            refresh=args.refresh,
            fetcher=lambda url=url: fetch_proposed_requests(url, min_ts=min_ts, cutoff_ts=cutoff_ts),
        )
        for row in cached:
            item = dict(row)
            item["subgraph"] = subgraph
            oracle_rows.append(item)

    request_rows: list[dict[str, Any]] = []
    unmatched_temperature_ancillary = 0
    for row in oracle_rows:
        ancillary_text = decode_ancillary(str(row.get("ancillaryData") or ""))
        match = MARKET_ID_RE.search(ancillary_text)
        if not match:
            continue
        market_id = match.group(1)
        market = markets_by_id.get(market_id)
        if market is not None:
            event = temp_events[str(market["event_id"])]
            market_question = str(market.get("question") or "")
        else:
            market_question = extract_question(ancillary_text)
            description = extract_description(ancillary_text)
            identity = parse_ancillary_temperature_identity(market_question, description)
            if identity is None:
                if "temperature" in ancillary_text.lower():
                    unmatched_temperature_ancillary += 1
                continue
            kind, city, target_date = identity
            timezone_name = CITY_TIMEZONE.get(city, "")
            if not timezone_name:
                unmatched_temperature_ancillary += 1
                continue
            synthetic_event_id = "ancillary:" + hashlib.sha256(
                f"{kind}|{city}|{target_date.isoformat()}".encode()
            ).hexdigest()
            event = temp_events.setdefault(
                synthetic_event_id,
                {
                    "event_id": synthetic_event_id,
                    "slug": "",
                    "title": f"{kind.title()} temperature in {city} on {target_date.isoformat()}",
                    "kind": kind,
                    "city": city,
                    "target_date": target_date.isoformat(),
                    "timezone_name": timezone_name,
                    "description": description,
                    "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
                    "rule_class": rule_class(description),
                    "source_station": source_station(description),
                    "active": False,
                    "closed": True,
                    "market_count": 0,
                },
            )
        timezone_name = str(event["timezone_name"])
        if not timezone_name:
            continue
        tz = ZoneInfo(timezone_name)
        proposal_dt = datetime.fromtimestamp(int(row["proposalTimestamp"]), UTC)
        request_dt = datetime.fromtimestamp(int(row["requestTimestamp"]), UTC)
        target_date = date.fromisoformat(str(event["target_date"]))
        next_midnight_local = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        proposal_local = proposal_dt.astimezone(tz)
        request_local = request_dt.astimezone(tz)
        request_rows.append(
            {
                "request_id": str(row["id"]),
                "subgraph": str(row["subgraph"]),
                "event_id": str(event["event_id"]),
                "market_id": market_id,
                "market_question": market_question,
                "kind": str(event["kind"]),
                "city": str(event["city"]),
                "target_date": str(event["target_date"]),
                "timezone_name": timezone_name,
                "source_station": str(event["source_station"]),
                "rule_class": str(event["rule_class"]),
                "request_utc": request_dt.isoformat(),
                "request_local": request_local.isoformat(),
                "proposal_utc": proposal_dt.isoformat(),
                "proposal_local": proposal_local.isoformat(),
                "proposal_local_hour": proposal_local.hour + proposal_local.minute / 60 + proposal_local.second / 3600,
                "request_minutes_from_next_midnight": (request_dt - next_midnight_local.astimezone(UTC)).total_seconds() / 60,
                "proposal_minutes_from_next_midnight": (proposal_dt - next_midnight_local.astimezone(UTC)).total_seconds() / 60,
                "proposal_minutes_after_request": (proposal_dt - request_dt).total_seconds() / 60,
                "proposer": str(row.get("proposer") or "").lower(),
                "proposed_price": str(row.get("proposedPrice") or ""),
                "settlement_price": str(row.get("settlementPrice") or ""),
                "dispute_utc": utc_iso(row.get("disputeTimestamp")),
                "settlement_utc": utc_iso(row.get("settlementTimestamp")),
                "reward_usdc": as_usdc(row.get("reward")),
                "bond_usdc": as_usdc(row.get("bond")),
                "final_fee_usdc": as_usdc(row.get("finalFee")),
                "total_deposit_usdc": (
                    (as_usdc(row.get("bond")) or 0) + (as_usdc(row.get("finalFee")) or 0)
                ),
                "custom_liveness_seconds": int(row.get("customLiveness") or 0),
                "proposal_hash": str(row.get("proposalHash") or ""),
                "description_sha256": str(event["description_sha256"]),
            }
        )

    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in request_rows:
        by_event[str(row["event_id"])].append(row)
    event_rows: list[dict[str, Any]] = []
    for event_id, rows in by_event.items():
        event = temp_events[event_id]
        first = min(rows, key=lambda row: row["proposal_utc"])
        last = max(rows, key=lambda row: row["proposal_utc"])
        event_rows.append(
            {
                "event_id": event_id,
                "slug": event["slug"],
                "title": event["title"],
                "kind": event["kind"],
                "city": event["city"],
                "target_date": event["target_date"],
                "timezone_name": event["timezone_name"],
                "source_station": event["source_station"],
                "rule_class": event["rule_class"],
                "market_count": event["market_count"],
                "proposed_request_count": len(rows),
                "unique_proposers": len({row["proposer"] for row in rows}),
                "first_proposal_utc": first["proposal_utc"],
                "last_proposal_utc": last["proposal_utc"],
                "first_proposal_local": first["proposal_local"],
                "last_proposal_local": last["proposal_local"],
                "first_proposal_minutes_from_next_midnight": first["proposal_minutes_from_next_midnight"],
                "last_proposal_minutes_from_next_midnight": last["proposal_minutes_from_next_midnight"],
                "batch_span_seconds": (
                    parse_utc(last["proposal_utc"]) - parse_utc(first["proposal_utc"])
                ).total_seconds(),
                "reward_usdc_values": sorted({row["reward_usdc"] for row in rows}),
                "total_deposit_usdc_values": sorted({row["total_deposit_usdc"] for row in rows}),
                "custom_liveness_values": sorted({row["custom_liveness_seconds"] for row in rows}),
                "proposers": sorted({row["proposer"] for row in rows}),
            }
        )
    event_rows.sort(key=lambda row: (row["target_date"], row["city"], row["kind"]))

    source_first_seen = load_wu_first_seen(Path(args.wu_first_seen_root))
    source_overlap_rows: list[dict[str, Any]] = []
    for event in event_rows:
        if event["rule_class"] != "first_next_date_point" or not event["source_station"]:
            continue
        next_date = (date.fromisoformat(event["target_date"]) + timedelta(days=1)).isoformat()
        source = source_first_seen.get((event["city"], next_date, event["source_station"]))
        if source is None:
            continue
        proposal_dt = parse_utc(event["first_proposal_utc"])
        first_seen_dt = parse_utc(source["source_first_seen_utc"])
        valid_dt = parse_utc(source["source_valid_utc"])
        source_overlap_rows.append(
            {
                "event_id": event["event_id"],
                "city": event["city"],
                "kind": event["kind"],
                "target_date": event["target_date"],
                "source_station": event["source_station"],
                "first_proposal_utc": event["first_proposal_utc"],
                "source_first_seen_utc": source["source_first_seen_utc"],
                "source_valid_utc": source["source_valid_utc"],
                "fetch_latency_ms": source["fetch_latency_ms"],
                "first_seen_lag_sec": source["first_seen_lag_sec"],
                "proposal_minus_collector_first_seen_minutes": (proposal_dt - first_seen_dt).total_seconds() / 60,
                "proposal_minus_source_valid_minutes": (proposal_dt - valid_dt).total_seconds() / 60,
                "source_path": source["source_path"],
            }
        )

    standardized = [row for row in request_rows if row["rule_class"] == "first_next_date_point"]
    current_cohort = [
        row for row in standardized
        if row["reward_usdc"] == 0.6 and row["total_deposit_usdc"] == 500.0
    ]
    current_event_ids = {row["event_id"] for row in current_cohort}
    current_events = [row for row in event_rows if row["event_id"] in current_event_ids]
    latest_target = max((row["target_date"] for row in current_events), default="")
    latest_events = [row for row in current_events if row["target_date"] == latest_target]
    current_settlement_lags = []
    for row in current_cohort:
        if row["settlement_utc"]:
            current_settlement_lags.append(
                (parse_utc(row["settlement_utc"]) - parse_utc(row["proposal_utc"])).total_seconds()
            )
    current_city_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current_events:
        current_city_events[row["city"]].append(row)
    current_proposer_counts = Counter(row["proposer"] for row in current_cohort)

    overlap_by_city_date: dict[tuple[str, str], dict[str, Any]] = {}
    for row in source_overlap_rows:
        overlap_by_city_date.setdefault((row["city"], row["target_date"]), row)
    overlap_city_dates = list(overlap_by_city_date.values())

    event_rule_counts = Counter(event["rule_class"] for event in temp_events.values())
    current_open = [
        event for event in temp_events.values()
        if event["active"]
        and not event["closed"]
        and date.fromisoformat(event["target_date"]) >= cutoff_dt.date() - timedelta(days=1)
    ]
    summary = {
        "schema_version": "weather_proposal_window_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "denominator_scope": {
            "min_proposal_utc": args.min_proposal,
            "cutoff_utc": cutoff_dt.isoformat(),
            "gamma_weather_events": len(events_by_id),
            "gamma_temperature_events": len(temp_events),
            "oracle_proposed_requests": len(oracle_rows),
            "joined_temperature_requests": len(request_rows),
            "joined_temperature_events": len(event_rows),
            "unmatched_temperature_ancillary": unmatched_temperature_ancillary,
            "sources": {"gamma": GAMMA_EVENTS_URL, "oracle_subgraphs": SUBGRAPHS},
        },
        "rule_counts_all_temperature_events": dict(sorted(event_rule_counts.items())),
        "current_open_temperature_events": {
            "count": len(current_open),
            "by_target_date": dict(sorted(Counter(event["target_date"] for event in current_open).items())),
            "by_rule_class": dict(sorted(Counter(event["rule_class"] for event in current_open).items())),
        },
        "standardized_request_timing_minutes_from_next_midnight": summarize(
            row["proposal_minutes_from_next_midnight"] for row in standardized
        ),
        "standardized_event_first_proposal_minutes_from_next_midnight": summarize(
            row["first_proposal_minutes_from_next_midnight"]
            for row in event_rows if row["rule_class"] == "first_next_date_point"
        ),
        "current_0_60_reward_500_deposit": {
            "requests": len(current_cohort),
            "events": len(current_events),
            "target_date_min": min((row["target_date"] for row in current_events), default=""),
            "target_date_max": latest_target,
            "event_first_proposal_minutes_from_next_midnight": summarize(
                row["first_proposal_minutes_from_next_midnight"] for row in current_events
            ),
            "event_batch_span_seconds": summarize(row["batch_span_seconds"] for row in current_events),
            "event_first_proposal_local_minute_buckets": dict(
                Counter(
                    "00-10" if row["first_proposal_minutes_from_next_midnight"] < 10
                    else "10-30" if row["first_proposal_minutes_from_next_midnight"] < 30
                    else "30-45" if row["first_proposal_minutes_from_next_midnight"] < 45
                    else "45-60" if row["first_proposal_minutes_from_next_midnight"] < 60
                    else ">=60"
                    for row in current_events
                )
            ),
            "event_first_proposal_minutes_by_city": {
                city: summarize(row["first_proposal_minutes_from_next_midnight"] for row in rows)
                for city, rows in sorted(current_city_events.items())
            },
            "proposer_request_counts": dict(current_proposer_counts.most_common(20)),
            "top_3_proposer_request_share": (
                sum(count for _, count in current_proposer_counts.most_common(3)) / len(current_cohort)
                if current_cohort else None
            ),
            "liveness_seconds": dict(Counter(str(row["custom_liveness_seconds"]) for row in current_cohort)),
            "proposal_to_settlement_seconds": summarize(current_settlement_lags),
            "disputed_requests": sum(bool(row["dispute_utc"]) for row in current_cohort),
            "latest_target_date_event_first_proposal_minutes_from_next_midnight": summarize(
                row["first_proposal_minutes_from_next_midnight"] for row in latest_events
            ),
        },
        "wu_collector_first_seen_overlap": {
            "event_rows": len(source_overlap_rows),
            "unique_city_dates": len(overlap_city_dates),
            "target_date_min": min((row["target_date"] for row in overlap_city_dates), default=""),
            "target_date_max": max((row["target_date"] for row in overlap_city_dates), default=""),
            "proposal_minus_collector_first_seen_minutes": summarize(
                row["proposal_minus_collector_first_seen_minutes"] for row in overlap_city_dates
            ),
            "proposal_minus_source_valid_minutes": summarize(
                row["proposal_minus_source_valid_minutes"] for row in overlap_city_dates
            ),
            "proposal_before_collector_first_seen_city_dates": sum(
                row["proposal_minus_collector_first_seen_minutes"] < 0 for row in overlap_city_dates
            ),
            "collector_before_or_same_time_as_proposal_city_dates": sum(
                row["proposal_minus_collector_first_seen_minutes"] >= 0 for row in overlap_city_dates
            ),
            "collector_deficit_city_date_buckets": {
                "behind_0_10_seconds": sum(
                    -10 / 60 <= row["proposal_minus_collector_first_seen_minutes"] < 0
                    for row in overlap_city_dates
                ),
                "behind_10_30_seconds": sum(
                    -30 / 60 <= row["proposal_minus_collector_first_seen_minutes"] < -10 / 60
                    for row in overlap_city_dates
                ),
                "behind_30_60_seconds": sum(
                    -1 <= row["proposal_minus_collector_first_seen_minutes"] < -30 / 60
                    for row in overlap_city_dates
                ),
                "behind_over_60_seconds": sum(
                    row["proposal_minus_collector_first_seen_minutes"] < -1 for row in overlap_city_dates
                ),
            },
            "matched_fetch_latency_ms": summarize(
                row["fetch_latency_ms"] for row in overlap_city_dates
            ),
            "matched_first_seen_lag_from_observation_valid_seconds": summarize(
                row["first_seen_lag_sec"] for row in overlap_city_dates
            ),
            "interpretation": "collector first-seen is a polling upper bound on source publication; a negative delta means a competing proposer observed/submitted before this collector's next poll, not necessarily before source publication",
        },
    }

    jsonl_write(out_dir / "requests.jsonl", request_rows)
    jsonl_write(out_dir / "events.jsonl", event_rows)
    jsonl_write(out_dir / "current_open_rules.jsonl", sorted(current_open, key=lambda row: (row["target_date"], row["city"], row["kind"])))
    jsonl_write(out_dir / "wu_first_seen_overlap.jsonl", source_overlap_rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
