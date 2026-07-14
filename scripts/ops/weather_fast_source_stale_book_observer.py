#!/usr/bin/env python3
"""Observe fast airport/reference sources against exact-temperature orderbooks.

This is telemetry only. It never submits orders.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_market_proxy import market_httpx_client, market_proxy_url  # noqa: E402
from weather_data_feed.city_calendar import city_local_date  # noqa: E402
from weather_data_feed.fast_event_source_policy import (  # noqa: E402
    FastEventSourceProfile,
    fast_event_source_profile_for,
    load_fast_event_source_profiles,
    market_value_from_temp_c,
)
from weather_data_feed.market_brackets import bracket_contains, parse_label_dict  # noqa: E402
from weather_data_feed.source_policy import city_slug  # noqa: E402

RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
HIGH_FREQUENCY_LATEST = RUNTIME_ROOT / "output/high_frequency_observations/latest.json"
HIGH_FREQUENCY_JSONL = RUNTIME_ROOT / "output/high_frequency_observations/high_frequency_observations.jsonl"
SOURCE_EVENTS_JSONL = RUNTIME_ROOT / "output/source_events/sources.jsonl"
PAPER_SNAPSHOT_DIR = RUNTIME_ROOT / "targeted_output/paper_snapshots"
ORDERBOOK_SNAPSHOT_ROOT = RUNTIME_ROOT / "targeted_output/orderbook_snapshots"
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/fast_source_stale_book"
PM_CLOB_URL = os.environ.get("WEATHER_STALE_BOOK_CLOB_URL", "https://clob.polymarket.com")
PM_GAMMA_URL = os.environ.get("WEATHER_STALE_BOOK_GAMMA_URL", "https://gamma-api.polymarket.com")

METAR_LIKE_SOURCES = {
    "aviationweather_metar",
    "aviationweather_cache_csv",
    "synopticdata_timeseries",
    "noaa_tgftp_station_txt",
    "iem_asos",
    "iem_asos_madishf_latest",
}

CITY_ALIASES = {
    "Hong Kong": "HongKong",
    "New York": "NYC",
    "Los Angeles": "LA",
    "San Francisco": "SanFrancisco",
    "Tel Aviv": "TelAviv",
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def market_city(city: str) -> str:
    return CITY_ALIASES.get(city, city.replace(" ", ""))


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_event_keys": [], "active_events": []}
    try:
        return read_json(path)
    except Exception:
        return {"seen_event_keys": [], "active_events": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def latest_file(paths: list[Path]) -> Path | None:
    return sorted(paths)[-1] if paths else None


def latest_paper_snapshot() -> Path | None:
    return latest_file(list(PAPER_SNAPSHOT_DIR.glob("snapshot_*.json")))


def latest_orderbook_snapshot() -> Path | None:
    return latest_file(list(ORDERBOOK_SNAPSHOT_ROOT.glob("*/*.jsonl.gz")) + list(ORDERBOOK_SNAPSHOT_ROOT.glob("*/*.jsonl")))


@dataclass(frozen=True)
class MarketToken:
    city: str
    target_date: str
    bracket: str
    question: str
    event_slug: str
    market_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str


def build_market_index(
    snapshot_path: Path | None,
    target_dates: set[str] | None = None,
) -> dict[tuple[str, str, str], MarketToken]:
    if snapshot_path is None:
        return {}
    payload = read_json(snapshot_path)
    out: dict[tuple[str, str, str], MarketToken] = {}
    for row in payload.get("records") or []:
        target_date = str(row.get("target_date") or "")
        if target_dates and target_date not in target_dates:
            continue
        city = str(row.get("city") or "")
        bracket = str(row.get("bracket") or "")
        if not city or not bracket:
            continue
        token = MarketToken(
            city=city,
            target_date=target_date,
            bracket=bracket,
            question=str(row.get("question") or ""),
            event_slug=str(row.get("event_slug") or ""),
            market_id=str(row.get("market_id") or ""),
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
        )
        out[(city, target_date, bracket)] = token
    return out


def load_market_index_cache(path: Path, target_dates: set[str]) -> dict[tuple[str, str, str], MarketToken]:
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    out: dict[tuple[str, str, str], MarketToken] = {}
    for row in payload.get("markets") or []:
        if not isinstance(row, dict):
            continue
        target_date = str(row.get("target_date") or "")
        if target_date not in target_dates:
            continue
        try:
            token = MarketToken(**{field: str(row.get(field) or "") for field in MarketToken.__dataclass_fields__})
        except TypeError:
            continue
        if token.city and token.target_date and token.bracket:
            out[(token.city, token.target_date, token.bracket)] = token
    return out


def save_market_index_cache(path: Path, index: dict[tuple[str, str, str], MarketToken]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "fast_source_market_index_v1",
        "generated_at_utc": iso(),
        "markets": [asdict(token) for _key, token in sorted(index.items())],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def temperature_event_slug(city: str, target_date: str, extreme_kind: str) -> str:
    target = datetime.fromisoformat(target_date)
    adjective = "lowest" if extreme_kind == "min" else "highest"
    return f"{adjective}-temperature-in-{city_slug(city)}-on-{target.strftime('%B').lower()}-{target.day}-{target.year}"


def source_market_episode_key(city: str, target_date: str, source: str, market_bracket: str) -> str:
    return "|".join([city, target_date, source, market_bracket])


def decode_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_city_string_overrides(raw_items: list[str] | None, *, arg_name: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for raw in raw_items or []:
        for item in str(raw).replace(",", " ").split():
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"{arg_name} entries must be City=value, got {item!r}")
            city_raw, value = item.split("=", 1)
            city = market_city(city_raw.strip())
            if not city or not value.strip():
                raise ValueError(f"{arg_name} entry must be City=value, got {item!r}")
            overrides[city] = value.strip()
    return overrides


def bracket_from_question(question: str) -> str:
    import re

    match = re.search(
        r"be\s+(?:between\s+)?(-?\d+)(?:\s*(?:-|to)\s*(-?\d+))?\s*°?[CF]"
        r"(?:\s+or\s+(higher|above|below|lower))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    bracket = match.group(1)
    upper = match.group(2)
    if upper is not None:
        return f"{bracket}-{upper}"
    qualifier = (match.group(3) or "").lower()
    if qualifier in {"higher", "above"}:
        return f"{bracket}+"
    return bracket


def augment_market_index_from_gamma(
    index: dict[tuple[str, str, str], MarketToken],
    *,
    target_dates: set[str],
    cities: set[str],
    event_slugs: dict[str, str],
    market_proxy: str,
) -> dict[tuple[str, str, str], MarketToken]:
    event_slug_by_city_date: dict[tuple[str, str], str] = {}
    if len(target_dates) == 1:
        explicit_date = next(iter(target_dates))
        event_slug_by_city_date.update({(city, explicit_date): slug for city, slug in event_slugs.items()})
    for (city, target_date, _bracket), token in index.items():
        if cities and city not in cities:
            continue
        if target_date in target_dates and token.event_slug:
            event_slug_by_city_date.setdefault((city, target_date), token.event_slug)
    if not event_slug_by_city_date:
        return index
    proxy_url = market_proxy_url(market_proxy or None)
    out = dict(index)
    with market_httpx_client(proxy_url, timeout=6.0) as client:
        for (city, target_date), event_slug in sorted(event_slug_by_city_date.items()):
            try:
                response = client.get(
                    f"{PM_GAMMA_URL.rstrip('/')}/events",
                    params={"slug": event_slug},
                    headers={"Accept": "application/json", "User-Agent": "pm-agent-weather-stale-book/1.0"},
                )
                if response.status_code != 200:
                    continue
                events = response.json()
            except Exception:
                continue
            if not isinstance(events, list) or not events:
                continue
            for market in events[0].get("markets") or []:
                question = str(market.get("question") or "")
                bracket = bracket_from_question(question)
                if not bracket:
                    continue
                outcomes = [str(x) for x in decode_json_array(market.get("outcomes"))]
                token_ids = [str(x) for x in decode_json_array(market.get("clobTokenIds"))]
                token_by_outcome = {outcome.lower(): token_id for outcome, token_id in zip(outcomes, token_ids)}
                yes_token = token_by_outcome.get("yes", "")
                no_token = token_by_outcome.get("no", "")
                if not yes_token and not no_token:
                    continue
                out[(city, target_date, bracket)] = MarketToken(
                    city=city,
                    target_date=target_date,
                    bracket=bracket,
                    question=question,
                    event_slug=event_slug,
                    market_id=str(market.get("id") or ""),
                    condition_id=str(market.get("conditionId") or market.get("condition_id") or ""),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                )
    return out


def bracket_lookup(
    index: dict[tuple[str, str, str], MarketToken],
    city: str,
    target_date: str,
    bracket: int,
) -> MarketToken | None:
    exact = index.get((city, target_date, str(bracket)))
    if exact:
        return exact
    matches: list[MarketToken] = []
    for (row_city, row_target_date, row_bracket), token in index.items():
        if row_city != city or row_target_date != target_date:
            continue
        try:
            parsed = parse_label_dict(row_bracket, token.question)
        except Exception:
            continue
        if bracket_contains(parsed, bracket):
            matches.append(token)
    return matches[0] if len(matches) == 1 else None


def ordered_market_tokens(
    index: dict[tuple[str, str, str], MarketToken],
    city: str,
    target_date: str,
) -> list[MarketToken]:
    rows: list[tuple[float, float, MarketToken]] = []
    for (row_city, row_target_date, row_bracket), token in index.items():
        if row_city != city or row_target_date != target_date:
            continue
        try:
            parsed = parse_label_dict(row_bracket, token.question)
        except Exception:
            continue
        low = float("-inf") if parsed.get("low") is None else float(parsed["low"])
        high = float("inf") if parsed.get("high") is None else float(parsed["high"])
        rows.append((low, high, token))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [token for _low, _high, token in rows]


def market_date_has_tokens(
    index: dict[tuple[str, str, str], MarketToken],
    city: str,
    target_date: str,
) -> bool:
    return any(row_city == city and row_target_date == target_date for row_city, row_target_date, _bracket in index)


def relative_market_token(
    index: dict[tuple[str, str, str], MarketToken],
    city: str,
    target_date: str,
    market_value: int,
    offset: int,
) -> MarketToken | None:
    current = bracket_lookup(index, city, target_date, market_value)
    if current is None:
        return None
    ladder = ordered_market_tokens(index, city, target_date)
    for idx, token in enumerate(ladder):
        if token.bracket == current.bracket:
            target_idx = idx + offset
            return ladder[target_idx] if 0 <= target_idx < len(ladder) else None
    return None


def normalize_levels(entries: Any, side: str, top_n: int = 20) -> list[dict[str, float]]:
    rows = []
    for entry in entries or []:
        if isinstance(entry, dict):
            price = entry.get("price") or entry.get("p")
            size = entry.get("size") or entry.get("q") or entry.get("quantity")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            price, size = entry[0], entry[1]
        else:
            continue
        price_f = safe_float(price)
        size_f = safe_float(size)
        if price_f is not None and size_f is not None and price_f > 0 and size_f > 0:
            rows.append({"price": price_f, "size": size_f})
    rows.sort(key=lambda x: x["price"], reverse=(side == "bid"))
    return rows[:top_n]


def summarize_book(raw: dict[str, Any], top_n: int = 20) -> dict[str, Any]:
    bids = normalize_levels(raw.get("bids"), "bid", top_n)
    asks = normalize_levels(raw.get("asks"), "ask", top_n)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_size": bids[0]["size"] if bids else None,
        "ask_size": asks[0]["size"] if asks else None,
        "tick_size": safe_float(raw.get("tick_size")),
        "spread": round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None,
        "bids": bids,
        "asks": asks,
    }


def fetch_fresh_book(token_id: str, *, proxy: str = "", timeout_sec: float = 4.0, top_n: int = 20) -> dict[str, Any]:
    fetched_at = iso()
    if not token_id:
        return {"status": "missing_token", "fetched_at_utc": fetched_at, "summary": {}, "raw": {}}
    proxy_url = market_proxy_url(proxy or None)
    url = f"{PM_CLOB_URL.rstrip('/')}/book"
    try:
        with market_httpx_client(proxy_url, timeout=timeout_sec) as client:
            response = client.get(
                url,
                params={"token_id": str(token_id)},
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            return {
                "status": "fetch_failed",
                "fetched_at_utc": fetched_at,
                "http_status": response.status_code,
                "error": response.text[:240],
                "proxy_used": proxy_url,
                "summary": {},
                "raw": {},
            }
        raw = response.json()
        summary = summarize_book(raw, top_n=top_n)
        return {
            "status": "ok",
            "fetched_at_utc": fetched_at,
            "http_status": response.status_code,
            "proxy_used": proxy_url,
            "summary": summary,
            "raw": {"bids": summary["bids"], "asks": summary["asks"], "tick_size": summary["tick_size"]},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "fetch_failed",
            "fetched_at_utc": fetched_at,
            "error": f"{type(exc).__name__}: {exc}",
            "proxy_used": proxy_url,
            "summary": {},
            "raw": {},
        }


def build_snapshot_quote_index(
    path: Path | None,
    target_dates: set[str] | None = None,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if path is None:
        return out
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            target_date = str(row.get("target_date") or row.get("market_local_date") or row.get("event_date") or "")
            if target_dates and target_date not in target_dates:
                continue
            city = str(row.get("city") or "")
            bracket = str(row.get("bracket") or "")
            outcome = str(row.get("outcome") or "").lower()
            if city and bracket and outcome in {"yes", "no"}:
                out[(city, target_date, bracket, outcome)] = row
    return out


def target_date_for_city(city: str, now_utc: datetime, explicit_target_date: str = "") -> str:
    return explicit_target_date or city_local_date(city, now_utc).isoformat()


def profiles_by_city(
    profiles: dict[tuple[str, str], FastEventSourceProfile],
) -> dict[str, FastEventSourceProfile]:
    out: dict[str, FastEventSourceProfile] = {}
    for (city, _source), profile in profiles.items():
        if profile.collector_enabled:
            out.setdefault(city, profile)
    return out


def source_latest_by_city(
    path: Path,
    explicit_target_date: str,
    allowed_sources: set[str],
    profiles: dict[tuple[str, str], FastEventSourceProfile],
    now_utc: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    payload = read_json(path)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in payload.get("records") or []:
        source = str(row.get("source") or "")
        if allowed_sources and source not in allowed_sources:
            continue
        temp = safe_float(row.get("temp_c"))
        if temp is None:
            continue
        city = market_city(str(row.get("city") or ""))
        target_date = str(row.get("target_date") or "")
        if target_date != target_date_for_city(city, now_utc, explicit_target_date):
            continue
        profile = fast_event_source_profile_for(city, source, profiles)
        if profile is None or not profile.collector_enabled:
            continue
        detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        obs_dt = parse_dt(row.get("observation_time_utc"))
        if detect_dt is None or obs_dt is None:
            continue
        enriched = {
            **row,
            "market_city": city,
            "market_unit": profile.market_unit,
            "source_market_value": market_value_from_temp_c(temp, profile),
            "source_temp_round_c": market_value_from_temp_c(temp, profile),
            "source_bracket_mode": profile.bracket_rounding,
            "fast_source_profile": profile.__dict__,
            "source_detect_ts_utc": detect_dt.isoformat(),
            "source_obs_ts_utc": obs_dt.isoformat(),
        }
        key = (city, target_date)
        old = out.get(key)
        if old is None:
            out[key] = enriched
            continue
        old_obs = parse_dt(old.get("source_obs_ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)
        old_detect = parse_dt(old.get("source_detect_ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)
        preferred = bool(enriched.get("is_preferred_temperature_runway"))
        old_preferred = bool(old.get("is_preferred_temperature_runway"))
        if (
            obs_dt > old_obs
            or (obs_dt == old_obs and preferred and not old_preferred)
            or (obs_dt == old_obs and preferred == old_preferred and detect_dt > old_detect)
        ):
            out[key] = enriched
    return out


def source_running_max_by_city(
    path: Path,
    explicit_target_date: str,
    allowed_sources: set[str],
    allowed_cities: set[str],
    profiles: dict[tuple[str, str], FastEventSourceProfile],
    now_utc: datetime,
    extreme_kind: str = "max",
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            source = str(row.get("source") or "")
            if allowed_sources and source not in allowed_sources:
                continue
            city = market_city(str(row.get("city") or ""))
            if allowed_cities and city not in allowed_cities:
                continue
            target_date = str(row.get("target_date") or "")
            if target_date != target_date_for_city(city, now_utc, explicit_target_date):
                continue
            profile = fast_event_source_profile_for(city, source, profiles)
            if profile is None or not profile.collector_enabled:
                continue
            temp = safe_float(row.get("temp_c"))
            obs_dt = parse_dt(row.get("observation_time_utc"))
            detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
            if temp is None or obs_dt is None or detect_dt is None:
                continue
            bracket = market_value_from_temp_c(temp, profile)
            mode = profile.bracket_rounding
            enriched = {
                **row,
                "market_city": city,
                "market_unit": profile.market_unit,
                "source_market_value": bracket,
                "source_temp_round_c": bracket,
                "source_bracket_c": bracket,
                "source_bracket_mode": mode,
                "fast_source_profile": profile.__dict__,
                "source_detect_ts_utc": detect_dt.isoformat(),
                "source_obs_ts_utc": obs_dt.isoformat(),
                "source_running_extreme_kind": extreme_kind,
                "source_running_extreme_temp_c": temp,
                "source_running_extreme_bracket_c": bracket,
                "source_running_extreme_obs_ts_utc": obs_dt.isoformat(),
                "source_running_extreme_detect_ts_utc": detect_dt.isoformat(),
            }
            key = (city, target_date)
            old = out.get(key)
            if old is None:
                out[key] = enriched
                continue
            old_bracket = int(old.get("source_running_extreme_bracket_c") or old.get("source_temp_round_c") or bracket)
            old_temp = safe_float(old.get("source_running_extreme_temp_c"))
            old_temp = old_temp if old_temp is not None else temp
            old_detect = parse_dt(old.get("source_running_extreme_detect_ts_utc") or old.get("source_detect_ts_utc"))
            better_extreme = (
                bracket > old_bracket
                or (bracket == old_bracket and temp > old_temp)
            ) if extreme_kind == "max" else (
                bracket < old_bracket
                or (bracket == old_bracket and temp < old_temp)
            )
            newer_tie = bracket == old_bracket and temp == old_temp and old_detect is not None and detect_dt > old_detect
            if better_extreme or newer_tie:
                out[key] = enriched
    for row in out.values():
        if extreme_kind == "max":
            row["source_running_max_temp_c"] = row.get("source_running_extreme_temp_c")
            row["source_running_max_bracket_c"] = row.get("source_running_extreme_bracket_c")
            row["source_running_max_obs_ts_utc"] = row.get("source_running_extreme_obs_ts_utc")
            row["source_running_max_detect_ts_utc"] = row.get("source_running_extreme_detect_ts_utc")
        else:
            row["source_running_min_temp_c"] = row.get("source_running_extreme_temp_c")
            row["source_running_min_bracket_c"] = row.get("source_running_extreme_bracket_c")
            row["source_running_min_obs_ts_utc"] = row.get("source_running_extreme_obs_ts_utc")
            row["source_running_min_detect_ts_utc"] = row.get("source_running_extreme_detect_ts_utc")
    return out


def metar_running_max(
    path: Path,
    explicit_target_date: str,
    city_profiles: dict[str, FastEventSourceProfile],
    now_utc: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source") not in METAR_LIKE_SOURCES:
                continue
            temp = safe_float(row.get("temp_c"))
            if temp is None:
                continue
            city = market_city(str(row.get("city") or ""))
            profile = city_profiles.get(city)
            if profile is None:
                continue
            target_date = str(row.get("target_date") or "")
            if target_date != target_date_for_city(city, now_utc, explicit_target_date):
                continue
            report_dt = parse_dt(row.get("source_report_ts_utc"))
            detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("ts_utc"))
            temp_round = market_value_from_temp_c(temp, profile)
            key = (city, target_date)
            cur = out.get(key)
            latest_report = parse_dt(cur.get("latest_report_ts_utc")) if cur else None
            if cur is None:
                out[key] = {
                    "market_unit": profile.market_unit,
                    "target_date": target_date,
                    "metar_running_max_market_value": temp_round,
                    "metar_running_max_round_c": temp_round,
                    "metar_running_max_temp_c": temp,
                    "latest_metar_temp_c": temp,
                    "latest_metar_round_c": temp_round,
                    "latest_report_ts_utc": report_dt.isoformat() if report_dt else "",
                    "latest_detect_ts_utc": detect_dt.isoformat() if detect_dt else "",
                    "raw_metar": row.get("raw_metar") or "",
                }
            else:
                if temp_round > int(cur["metar_running_max_round_c"]):
                    cur["metar_running_max_market_value"] = temp_round
                    cur["metar_running_max_round_c"] = temp_round
                    cur["metar_running_max_temp_c"] = temp
                if report_dt and (latest_report is None or report_dt >= latest_report):
                    cur.update(
                        {
                            "latest_metar_temp_c": temp,
                            "latest_metar_round_c": temp_round,
                            "latest_report_ts_utc": report_dt.isoformat(),
                            "latest_detect_ts_utc": detect_dt.isoformat() if detect_dt else "",
                            "raw_metar": row.get("raw_metar") or "",
                        }
                    )
    return out


def quote_for_token(
    token: MarketToken | None,
    *,
    outcome: str,
    snapshot_quotes: dict[tuple[str, str, str, str], dict[str, Any]],
    market_proxy: str,
    fetch_fresh: bool,
) -> dict[str, Any]:
    if token is None:
        return {"status": "missing_market", "outcome": outcome}
    token_id = token.yes_token_id if outcome == "yes" else token.no_token_id
    snap = snapshot_quotes.get((token.city, token.target_date, token.bracket, outcome)) or {}
    fresh = fetch_fresh_book(token_id, proxy=market_proxy) if fetch_fresh else {"status": "disabled", "summary": {}}
    return {
        "status": "ok",
        "outcome": outcome,
        "city": token.city,
        "target_date": token.target_date,
        "bracket": token.bracket,
        "question": token.question,
        "event_slug": token.event_slug,
        "market_id": token.market_id,
        "condition_id": token.condition_id,
        "token_id": token_id,
        "snapshot_status": snap.get("status") or ("missing_snapshot_quote" if not snap else ""),
        "snapshot_ts_utc": snap.get("snapshot_ts_utc", ""),
        "snapshot_best_bid": (snap.get("summary") or {}).get("best_bid"),
        "snapshot_best_ask": (snap.get("summary") or {}).get("best_ask"),
        "snapshot_bid_size": (snap.get("summary") or {}).get("bid_size"),
        "snapshot_ask_size": (snap.get("summary") or {}).get("ask_size"),
        "fresh_status": fresh.get("status"),
        "fresh_fetched_at_utc": fresh.get("fetched_at_utc"),
        "fresh_best_bid": (fresh.get("summary") or {}).get("best_bid"),
        "fresh_best_ask": (fresh.get("summary") or {}).get("best_ask"),
        "fresh_bid_size": (fresh.get("summary") or {}).get("bid_size"),
        "fresh_ask_size": (fresh.get("summary") or {}).get("ask_size"),
        "fresh_error": fresh.get("error", ""),
        "fresh_http_status": fresh.get("http_status"),
        "fresh_proxy_used": fresh.get("proxy_used", ""),
    }


def previous_no_bracket(source_round: int, extreme_kind: str) -> int:
    return source_round - 1 if extreme_kind == "max" else source_round + 1


def next_no_bracket(source_round: int, extreme_kind: str) -> int:
    return source_round + 1 if extreme_kind == "max" else source_round - 1


def build_quotes(
    *,
    event: dict[str, Any],
    market_index: dict[tuple[str, str, str], MarketToken],
    snapshot_quotes: dict[tuple[str, str, str, str], dict[str, Any]],
    market_proxy: str,
    fetch_fresh: bool,
    fresh_scope: str,
) -> dict[str, Any]:
    city = str(event["city"])
    target_date = str(event["target_date"])
    source_round = int(event.get("source_market_value") or event["source_round_c"])
    extreme_kind = str(event.get("extreme_kind") or "max")
    direction = 1 if extreme_kind == "max" else -1
    tokens = {
        "t_minus_1": relative_market_token(market_index, city, target_date, source_round, -direction),
        "source_round": relative_market_token(market_index, city, target_date, source_round, 0),
        "source_plus_1": relative_market_token(market_index, city, target_date, source_round, direction),
    }
    out: dict[str, Any] = {}
    for label, token in tokens.items():
        fetch_yes = fetch_fresh and fresh_scope == "all"
        fetch_no = fetch_fresh and (fresh_scope == "all" or label == "t_minus_1")
        out[label] = {
            "bracket": token.bracket if token else "",
            "market_unit": event.get("market_unit"),
            "yes": quote_for_token(token, outcome="yes", snapshot_quotes=snapshot_quotes, market_proxy=market_proxy, fetch_fresh=fetch_yes),
            "no": quote_for_token(token, outcome="no", snapshot_quotes=snapshot_quotes, market_proxy=market_proxy, fetch_fresh=fetch_no),
        }
    return out


def classify_t_minus_1_no(quotes: dict[str, Any], stale_no_ask_max: float, bot_priced_no_bid_min: float) -> dict[str, Any]:
    no_quote = ((quotes.get("t_minus_1") or {}).get("no") or {})
    ask = safe_float(no_quote.get("fresh_best_ask"))
    bid = safe_float(no_quote.get("fresh_best_bid"))
    quote_basis = "fresh"
    if ask is None and bid is None:
        ask = safe_float(no_quote.get("snapshot_best_ask"))
        bid = safe_float(no_quote.get("snapshot_best_bid"))
        quote_basis = "snapshot"
    if ask is None and bid is None:
        return {
            "stale_book_candidate": False,
            "bot_priced": False,
            "reason": "missing_t_minus_1_no_quote",
            "quote_basis": "missing",
            "t_minus_1_no_best_bid": None,
            "t_minus_1_no_best_ask": None,
        }
    if ask is not None and ask <= stale_no_ask_max:
        return {
            "stale_book_candidate": True,
            "bot_priced": False,
            "reason": f"t_minus_1_no_ask<={stale_no_ask_max}",
            "quote_basis": quote_basis,
            "t_minus_1_no_best_bid": bid,
            "t_minus_1_no_best_ask": ask,
        }
    if bid is not None and bid >= bot_priced_no_bid_min:
        return {
            "stale_book_candidate": False,
            "bot_priced": True,
            "reason": f"t_minus_1_no_bid>={bot_priced_no_bid_min}",
            "quote_basis": quote_basis,
            "t_minus_1_no_best_bid": bid,
            "t_minus_1_no_best_ask": ask,
        }
    return {
        "stale_book_candidate": False,
        "bot_priced": False,
        "reason": "intermediate_quote",
        "quote_basis": quote_basis,
        "t_minus_1_no_best_bid": bid,
        "t_minus_1_no_best_ask": ask,
    }


def quote_best_ask(quotes: dict[str, Any], label: str, outcome: str) -> float | None:
    quote = ((quotes.get(label) or {}).get(outcome) or {})
    ask = safe_float(quote.get("fresh_best_ask"))
    if ask is not None:
        return ask
    return safe_float(quote.get("snapshot_best_ask"))


def classify_official_running_max(
    quotes: dict[str, Any],
    *,
    stale_no_ask_max: float,
    lock_yes_ask_max: float,
    lock_next_no_ask_max: float,
) -> dict[str, Any]:
    prev_no_ask = quote_best_ask(quotes, "t_minus_1", "no")
    current_yes_ask = quote_best_ask(quotes, "source_round", "yes")
    next_no_ask = quote_best_ask(quotes, "source_plus_1", "no")
    candidates: list[str] = []
    if prev_no_ask is not None and prev_no_ask <= stale_no_ask_max:
        candidates.append("cross_prev_no")
    if current_yes_ask is not None and current_yes_ask <= lock_yes_ask_max:
        candidates.append("lock_current_yes")
    if next_no_ask is not None and next_no_ask <= lock_next_no_ask_max:
        candidates.append("lock_next_no")
    return {
        "official_running_max_candidates": candidates,
        "official_running_extreme_candidates": candidates,
        "official_prev_no_best_ask": prev_no_ask,
        "official_current_yes_best_ask": current_yes_ask,
        "official_next_no_best_ask": next_no_ask,
        "official_prev_no_candidate": "cross_prev_no" in candidates,
        "official_current_yes_candidate": "lock_current_yes" in candidates,
        "official_next_no_candidate": "lock_next_no" in candidates,
        "lock_yes_ask_max": lock_yes_ask_max,
        "lock_next_no_ask_max": lock_next_no_ask_max,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.output_dir)
    state_path = out_dir / "state.json"
    market_index_cache_path = out_dir / "market_index.json"
    state = load_state(state_path)
    seen = set(state.get("seen_event_keys") or [])
    now = datetime.now(timezone.utc)

    sources = set(args.sources or [])
    allowed_cities = {market_city(city) for city in (args.cities or [])}
    fast_profiles = load_fast_event_source_profiles()
    fast_profiles = {
        key: profile
        for key, profile in fast_profiles.items()
        if profile.source in sources and profile.collector_enabled and (not allowed_cities or profile.city in allowed_cities)
    }
    city_profiles = profiles_by_city(fast_profiles)
    city_target_dates = {
        city: target_date_for_city(city, now, args.target_date)
        for city in sorted(city_profiles)
    }
    target_dates = set(city_target_dates.values())
    extreme_kind = args.extreme_kind
    gamma_event_slugs = parse_city_string_overrides(args.gamma_event_slug, arg_name="--gamma-event-slug")
    official_extreme_mode = args.signal_basis in {"official-running-max", "official-running-extreme"}
    if official_extreme_mode:
        source_rows = source_running_max_by_city(
            Path(args.high_frequency_jsonl),
            args.target_date,
            sources,
            allowed_cities,
            fast_profiles,
            now,
            extreme_kind=extreme_kind,
        )
        metar_rows = {}
    else:
        source_rows = source_latest_by_city(
            Path(args.high_frequency_latest),
            args.target_date,
            sources,
            fast_profiles,
            now,
        )
        if allowed_cities:
            source_rows = {key: row for key, row in source_rows.items() if key[0] in allowed_cities}
        metar_rows = metar_running_max(Path(args.source_events_jsonl), args.target_date, city_profiles, now)
    paper_path = latest_paper_snapshot()
    orderbook_path = latest_orderbook_snapshot()
    market_index = load_market_index_cache(market_index_cache_path, target_dates)
    market_index.update(build_market_index(paper_path, target_dates))
    if args.gamma_market_index:
        market_index = augment_market_index_from_gamma(
            market_index,
            target_dates=target_dates,
            cities=allowed_cities,
            event_slugs=gamma_event_slugs,
            market_proxy=args.market_proxy or "",
        )
    gamma_on_missing_city_dates: list[str] = []
    for (city, target_date), src in sorted(source_rows.items()):
        if market_date_has_tokens(market_index, city, target_date):
            continue
        market_index = augment_market_index_from_gamma(
            market_index,
            target_dates={target_date},
            cities={city},
            event_slugs={city: temperature_event_slug(city, target_date, extreme_kind)},
            market_proxy=args.market_proxy or "",
        )
        if market_date_has_tokens(market_index, city, target_date):
            gamma_on_missing_city_dates.append(f"{city}|{target_date}")
    snapshot_quotes = build_snapshot_quote_index(orderbook_path, target_dates)

    new_events = []
    quote_rows = []
    active_events = []
    same_market_bracket_rows = 0
    missing_market_context_rows = 0

    for (city, target_date), src in sorted(source_rows.items()):
        metar = metar_rows.get((city, target_date))
        source_round = int(src.get("source_market_value") or src["source_temp_round_c"])
        source = str(src.get("source") or "")
        profile = fast_event_source_profile_for(city, source, fast_profiles)
        if profile is None:
            continue
        prev_no = previous_no_bracket(source_round, extreme_kind)
        nxt_no = next_no_bracket(source_round, extreme_kind)
        if official_extreme_mode:
            metar_max = prev_no
        else:
            if not metar:
                continue
            metar_max = int(metar["metar_running_max_round_c"])
            if source_round <= metar_max:
                continue
        direction = 1 if extreme_kind == "max" else -1
        source_token = relative_market_token(market_index, city, target_date, source_round, 0)
        metar_token = (
            relative_market_token(market_index, city, target_date, source_round, -direction)
            if official_extreme_mode
            else bracket_lookup(market_index, city, target_date, metar_max)
        )
        if source_token is None or metar_token is None:
            missing_market_context_rows += 1
            continue
        if not official_extreme_mode and source_token.bracket == metar_token.bracket:
            same_market_bracket_rows += 1
            continue
        previous_token = relative_market_token(market_index, city, target_date, source_round, -direction)
        next_token = relative_market_token(market_index, city, target_date, source_round, direction)
        t_minus_1 = previous_token.bracket if previous_token else ""
        key = source_market_episode_key(city, target_date, source, source_token.bracket)
        event = {
            "schema_version": "fast_source_stale_book_event_v2",
            "event_key": key,
            "created_at_utc": iso(now),
            "expires_at_utc": iso(now + timedelta(minutes=float(args.follow_minutes))),
            "city": city,
            "target_date": target_date,
            "source": source,
            "station": src.get("station"),
            "source_obs_ts_utc": src.get("source_obs_ts_utc"),
            "source_detect_ts_utc": src.get("source_detect_ts_utc"),
            "source_temp_c": src.get("temp_c"),
            "source_market_value": source_round,
            "source_round_c": source_round,
            "market_unit": profile.market_unit,
            "source_bracket_mode": profile.bracket_rounding,
            "fast_source_profile": profile.__dict__,
            "source_basis_class": profile.source_basis_class,
            "source_calibration_status": profile.calibration_status,
            "source_live_eligible": profile.live_eligible,
            "source_blocked_reason": profile.blocked_reason,
            "extreme_kind": extreme_kind,
            "running_max_basis": args.signal_basis,
            "running_extreme_basis": args.signal_basis,
            "reference_running_max_round_c": metar_max,
            "reference_running_extreme_round_c": metar_max,
            "reference_running_max_source": "official_high_frequency_history" if official_extreme_mode else "metar_like_sources",
            "reference_running_extreme_source": "official_high_frequency_history" if official_extreme_mode else "metar_like_sources",
            "metar_running_max_round_c": metar_max if metar else None,
            "metar_running_max_temp_c": metar.get("metar_running_max_temp_c") if metar else None,
            "latest_metar_report_ts_utc": metar.get("latest_report_ts_utc") if metar else "",
            "latest_metar_detect_ts_utc": metar.get("latest_detect_ts_utc") if metar else "",
            "latest_metar_temp_c": metar.get("latest_metar_temp_c") if metar else None,
            "source_running_max_temp_c": src.get("source_running_max_temp_c"),
            "source_running_max_bracket_c": src.get("source_running_max_bracket_c"),
            "source_running_max_obs_ts_utc": src.get("source_running_max_obs_ts_utc"),
            "source_running_max_detect_ts_utc": src.get("source_running_max_detect_ts_utc"),
            "source_running_min_temp_c": src.get("source_running_min_temp_c"),
            "source_running_min_bracket_c": src.get("source_running_min_bracket_c"),
            "source_running_min_obs_ts_utc": src.get("source_running_min_obs_ts_utc"),
            "source_running_min_detect_ts_utc": src.get("source_running_min_detect_ts_utc"),
            "source_running_extreme_temp_c": src.get("source_running_extreme_temp_c"),
            "source_running_extreme_bracket_c": src.get("source_running_extreme_bracket_c"),
            "source_running_extreme_obs_ts_utc": src.get("source_running_extreme_obs_ts_utc"),
            "source_running_extreme_detect_ts_utc": src.get("source_running_extreme_detect_ts_utc"),
            "t_minus_1_no_bracket_c": t_minus_1,
            "previous_no_bracket_c": t_minus_1,
            "previous_market_bracket": t_minus_1,
            "current_bracket_c": source_token.bracket,
            "current_market_bracket": source_token.bracket,
            "current_bracket_value": source_round,
            "next_no_bracket_c": next_token.bracket if next_token else "",
            "next_market_bracket": next_token.bracket if next_token else "",
            "reference_market_bracket": metar_token.bracket,
            "crossed_brackets_c": [metar_token.bracket, source_token.bracket],
            "crossed_market_brackets": [metar_token.bracket, source_token.bracket],
            "paper_snapshot_path": str(paper_path) if paper_path else "",
            "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
            "mode": "telemetry_only_no_orders",
        }
        if key not in seen:
            append_jsonl(out_dir / "events.jsonl", event)
            seen.add(key)
            new_events.append(event)
        active_events.append(event)

    for old in state.get("active_events") or []:
        # A new local market day must never keep quoting an expired prior-day
        # signal against today's event slug.
        if str(old.get("schema_version") or "") != "fast_source_stale_book_event_v2":
            continue
        old_city = str(old.get("city") or "")
        if str(old.get("target_date") or "") != target_date_for_city(old_city, now, args.target_date):
            continue
        expires_at = parse_dt(old.get("expires_at_utc"))
        if expires_at and expires_at > now:
            active_events.append(old)

    # De-dupe active events by key. Current-cycle events are appended before persisted
    # state rows, so keep the first copy to preserve freshly recomputed fields.
    active_by_key: dict[str, dict[str, Any]] = {}
    for row in active_events:
        key = str(row.get("event_key") or "")
        if key and key not in active_by_key:
            active_by_key[key] = row
    active_events = list(active_by_key.values())

    for event in active_events:
        city = str(event.get("city") or "")
        target_date = str(event.get("target_date") or "")
        if not city or not target_date or market_date_has_tokens(market_index, city, target_date):
            continue
        market_index = augment_market_index_from_gamma(
            market_index,
            target_dates={target_date},
            cities={city},
            event_slugs={city: temperature_event_slug(city, target_date, extreme_kind)},
            market_proxy=args.market_proxy or "",
        )
        if market_date_has_tokens(market_index, city, target_date):
            gamma_on_missing_city_dates.append(f"{city}|{target_date}")
    save_market_index_cache(market_index_cache_path, market_index)

    for event in sorted(active_events, key=lambda row: str(row.get("created_at_utc"))):
        quotes = build_quotes(
            event=event,
            market_index=market_index,
            snapshot_quotes=snapshot_quotes,
            market_proxy=args.market_proxy or "",
            fetch_fresh=not args.no_fresh_orderbook,
            fresh_scope=args.fresh_scope,
        )
        classification = classify_t_minus_1_no(quotes, args.stale_no_ask_max, args.bot_priced_no_bid_min)
        if official_extreme_mode:
            classification = {
                **classification,
                **classify_official_running_max(
                    quotes,
                    stale_no_ask_max=args.stale_no_ask_max,
                    lock_yes_ask_max=args.lock_yes_ask_max,
                    lock_next_no_ask_max=args.lock_next_no_ask_max,
                ),
            }
        fallback_reference_round = None
        if event.get("source_round_c") is not None and official_extreme_mode:
            fallback_reference_round = previous_no_bracket(int(event["source_round_c"]), str(event.get("extreme_kind") or args.extreme_kind))
        quote_row = {
            "schema_version": "fast_source_stale_book_quote_snapshot_v2",
            "ts_utc": iso(),
            "event_key": event.get("event_key"),
            "city": event.get("city"),
            "target_date": event.get("target_date"),
            "source": event.get("source"),
            "source_obs_ts_utc": event.get("source_obs_ts_utc"),
            "source_detect_ts_utc": event.get("source_detect_ts_utc"),
            "source_temp_c": event.get("source_temp_c"),
            "source_market_value": event.get("source_market_value"),
            "source_round_c": event.get("source_round_c"),
            "market_unit": event.get("market_unit"),
            "source_bracket_mode": event.get("source_bracket_mode"),
            "source_basis_class": event.get("source_basis_class"),
            "source_calibration_status": event.get("source_calibration_status"),
            "source_live_eligible": event.get("source_live_eligible"),
            "source_blocked_reason": event.get("source_blocked_reason"),
            "extreme_kind": event.get("extreme_kind"),
            "running_max_basis": event.get("running_max_basis"),
            "running_extreme_basis": event.get("running_extreme_basis") or event.get("running_max_basis"),
            "reference_running_max_round_c": event.get("reference_running_max_round_c", fallback_reference_round),
            "reference_running_extreme_round_c": event.get(
                "reference_running_extreme_round_c",
                event.get("reference_running_max_round_c", fallback_reference_round),
            ),
            "reference_running_max_source": event.get(
                "reference_running_max_source",
                "official_high_frequency_history" if official_extreme_mode else "metar_like_sources",
            ),
            "reference_running_extreme_source": event.get(
                "reference_running_extreme_source",
                event.get("reference_running_max_source", "official_high_frequency_history" if official_extreme_mode else "metar_like_sources"),
            ),
            "source_running_max_temp_c": event.get("source_running_max_temp_c"),
            "source_running_max_bracket_c": event.get("source_running_max_bracket_c"),
            "source_running_max_obs_ts_utc": event.get("source_running_max_obs_ts_utc"),
            "source_running_max_detect_ts_utc": event.get("source_running_max_detect_ts_utc"),
            "source_running_min_temp_c": event.get("source_running_min_temp_c"),
            "source_running_min_bracket_c": event.get("source_running_min_bracket_c"),
            "source_running_min_obs_ts_utc": event.get("source_running_min_obs_ts_utc"),
            "source_running_min_detect_ts_utc": event.get("source_running_min_detect_ts_utc"),
            "source_running_extreme_temp_c": event.get("source_running_extreme_temp_c"),
            "source_running_extreme_bracket_c": event.get("source_running_extreme_bracket_c"),
            "source_running_extreme_obs_ts_utc": event.get("source_running_extreme_obs_ts_utc"),
            "source_running_extreme_detect_ts_utc": event.get("source_running_extreme_detect_ts_utc"),
            "metar_running_max_round_c": event.get("metar_running_max_round_c"),
            "t_minus_1_no_bracket_c": event.get("t_minus_1_no_bracket_c"),
            "previous_no_bracket_c": event.get("previous_no_bracket_c"),
            "previous_market_bracket": event.get("previous_market_bracket"),
            "current_bracket_c": event.get("current_bracket_c"),
            "current_market_bracket": event.get("current_market_bracket"),
            "next_no_bracket_c": event.get("next_no_bracket_c"),
            "next_market_bracket": event.get("next_market_bracket"),
            "reference_market_bracket": event.get("reference_market_bracket"),
            "quotes": quotes,
            **classification,
            "mode": "telemetry_only_no_orders",
        }
        append_jsonl(out_dir / "quote_snapshots.jsonl", quote_row)
        quote_rows.append(quote_row)

    state = {
        "updated_at_utc": iso(),
        "target_date": args.target_date or "per_city_local",
        "city_target_dates": city_target_dates,
        "seen_event_keys": sorted(seen)[-5000:],
        "active_events": active_events,
    }
    save_state(state_path, state)
    latest = {
        "status": "ok",
        "schema_version": "fast_source_stale_book_latest_v2",
        "generated_at_utc": iso(),
        "target_date": args.target_date or "per_city_local",
        "target_dates": sorted(target_dates),
        "city_target_dates": city_target_dates,
        "source_cities": sorted({city for city, _target_date in source_rows}),
        "source_city_dates": [f"{city}|{target_date}" for city, target_date in sorted(source_rows)],
        "metar_cities": sorted({city for city, _target_date in metar_rows}),
        "metar_city_dates": [f"{city}|{target_date}" for city, target_date in sorted(metar_rows)],
        "fast_source_profile_count": len(fast_profiles),
        "fast_source_profile_live_eligible_count": sum(profile.live_eligible for profile in fast_profiles.values()),
        "signal_basis": args.signal_basis,
        "extreme_kind": args.extreme_kind,
        "cities": sorted(allowed_cities),
        "gamma_event_slugs": gamma_event_slugs,
        "new_events": len(new_events),
        "active_events": len(active_events),
        "quote_snapshots": len(quote_rows),
        "fresh_orderbook_enabled": not args.no_fresh_orderbook,
        "fresh_scope": args.fresh_scope,
        "paper_snapshot_path": str(paper_path) if paper_path else "",
        "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        "gamma_market_index": bool(args.gamma_market_index),
        "gamma_on_missing_city_dates": sorted(gamma_on_missing_city_dates),
        "same_market_bracket_rows": same_market_bracket_rows,
        "missing_market_context_rows": missing_market_context_rows,
        "market_index_cache_path": str(market_index_cache_path),
        "events_path": str(out_dir / "events.jsonl"),
        "quote_snapshots_path": str(out_dir / "quote_snapshots.jsonl"),
        "latest_quote_rows": quote_rows[-20:],
    }
    (out_dir / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--high-frequency-latest", default=str(HIGH_FREQUENCY_LATEST))
    parser.add_argument("--high-frequency-jsonl", default=str(HIGH_FREQUENCY_JSONL))
    parser.add_argument("--source-events-jsonl", default=str(SOURCE_EVENTS_JSONL))
    parser.add_argument(
        "--signal-basis",
        choices=["latest-vs-metar", "official-running-max", "official-running-extreme"],
        default="latest-vs-metar",
    )
    parser.add_argument("--extreme-kind", choices=["max", "min"], default="max")
    parser.add_argument("--cities", nargs="*", default=[])
    parser.add_argument("--floor-cities", nargs="*", default=["HongKong"])
    parser.add_argument("--gamma-event-slug", action="append", default=[])
    parser.add_argument(
        "--sources",
        nargs="*",
        default=[
            "amos_runway",
            "noaa_madis_hfmetar",
            "singapore_mss",
            "jma_amedas",
            "hko_obs",
            "cowin_obs",
            "fmi",
            "mgm",
            "ims_lod",
        ],
    )
    parser.add_argument("--follow-minutes", type=float, default=20.0)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument(
        "--market-proxy",
        default=market_proxy_url(os.getenv("WEATHER_STALE_BOOK_MARKET_PROXY") or None),
    )
    parser.add_argument("--fresh-scope", choices=["t_minus_1_no", "all"], default="t_minus_1_no")
    parser.add_argument("--no-fresh-orderbook", action="store_true")
    parser.add_argument("--gamma-market-index", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--stale-no-ask-max", type=float, default=0.35)
    parser.add_argument("--bot-priced-no-bid-min", type=float, default=0.70)
    parser.add_argument("--lock-yes-ask-max", type=float, default=0.93)
    parser.add_argument("--lock-next-no-ask-max", type=float, default=0.93)
    parser.add_argument("--loop", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.loop:
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    while True:
        started = time.monotonic()
        try:
            latest = run_once(args)
            print(json.dumps({k: v for k, v in latest.items() if k != "latest_quote_rows"}, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"status": "error", "ts_utc": iso(), "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True), flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(5.0, float(args.interval_seconds) - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
