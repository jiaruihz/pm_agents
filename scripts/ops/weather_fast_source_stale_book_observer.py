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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_market_proxy import market_httpx_client, market_proxy_url  # noqa: E402

RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
HIGH_FREQUENCY_LATEST = RUNTIME_ROOT / "output/high_frequency_observations/latest.json"
SOURCE_EVENTS_JSONL = RUNTIME_ROOT / "output/source_events/sources.jsonl"
PAPER_SNAPSHOT_DIR = RUNTIME_ROOT / "targeted_output/paper_snapshots"
ORDERBOOK_SNAPSHOT_ROOT = RUNTIME_ROOT / "targeted_output/orderbook_snapshots"
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/fast_source_stale_book"
PM_CLOB_URL = os.environ.get("WEATHER_STALE_BOOK_CLOB_URL", "https://clob.polymarket.com")

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


def arith_round(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


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


def build_market_index(snapshot_path: Path | None, target_date: str) -> dict[tuple[str, str], MarketToken]:
    if snapshot_path is None:
        return {}
    payload = read_json(snapshot_path)
    out: dict[tuple[str, str], MarketToken] = {}
    for row in payload.get("records") or []:
        if str(row.get("target_date") or "") != target_date:
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
        out[(city, bracket)] = token
    return out


def bracket_lookup(index: dict[tuple[str, str], MarketToken], city: str, bracket: int) -> MarketToken | None:
    exact = index.get((city, str(bracket)))
    if exact:
        return exact
    # Endpoint markets can be labelled "25 or below" in the question while the
    # bracket field remains "25"; keep a conservative fallback for "34+".
    for (row_city, row_bracket), token in index.items():
        if row_city == city and row_bracket.rstrip("+") == str(bracket):
            return token
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
            "raw": {"bids": summary["bids"], "asks": summary["asks"]},
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


def build_snapshot_quote_index(path: Path | None, target_date: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    if path is None:
        return out
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("target_date") or row.get("market_local_date") or row.get("event_date") or "") != target_date:
                continue
            city = str(row.get("city") or "")
            bracket = str(row.get("bracket") or "")
            outcome = str(row.get("outcome") or "").lower()
            if city and bracket and outcome in {"yes", "no"}:
                out[(city, bracket, outcome)] = row
    return out


def source_latest_by_city(path: Path, target_date: str, allowed_sources: set[str]) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("records") or []:
        if allowed_sources and str(row.get("source") or "") not in allowed_sources:
            continue
        if str(row.get("target_date") or "") != target_date:
            continue
        temp = safe_float(row.get("temp_c"))
        if temp is None:
            continue
        city = market_city(str(row.get("city") or ""))
        detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        obs_dt = parse_dt(row.get("observation_time_utc"))
        if detect_dt is None or obs_dt is None:
            continue
        enriched = {
            **row,
            "market_city": city,
            "source_temp_round_c": arith_round(temp),
            "source_detect_ts_utc": detect_dt.isoformat(),
            "source_obs_ts_utc": obs_dt.isoformat(),
        }
        old = out.get(city)
        if old is None:
            out[city] = enriched
            continue
        old_round = int(old.get("source_temp_round_c") or -999)
        new_round = int(enriched["source_temp_round_c"])
        old_detect = parse_dt(old.get("source_detect_ts_utc")) or datetime.min.replace(tzinfo=timezone.utc)
        if new_round > old_round or (new_round == old_round and detect_dt > old_detect):
            out[city] = enriched
    return out


def metar_running_max(path: Path, target_date: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source") not in METAR_LIKE_SOURCES or str(row.get("target_date") or "") != target_date:
                continue
            temp = safe_float(row.get("temp_c"))
            if temp is None:
                continue
            city = market_city(str(row.get("city") or ""))
            report_dt = parse_dt(row.get("source_report_ts_utc"))
            detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("ts_utc"))
            temp_round = arith_round(temp)
            cur = out.get(city)
            latest_report = parse_dt(cur.get("latest_report_ts_utc")) if cur else None
            if cur is None:
                out[city] = {
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
    snapshot_quotes: dict[tuple[str, str, str], dict[str, Any]],
    market_proxy: str,
    fetch_fresh: bool,
) -> dict[str, Any]:
    if token is None:
        return {"status": "missing_market", "outcome": outcome}
    token_id = token.yes_token_id if outcome == "yes" else token.no_token_id
    snap = snapshot_quotes.get((token.city, token.bracket, outcome)) or {}
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


def build_quotes(
    *,
    event: dict[str, Any],
    market_index: dict[tuple[str, str], MarketToken],
    snapshot_quotes: dict[tuple[str, str, str], dict[str, Any]],
    market_proxy: str,
    fetch_fresh: bool,
    fresh_scope: str,
) -> dict[str, Any]:
    city = str(event["city"])
    brackets = {
        "t_minus_1": int(event["t_minus_1_no_bracket_c"]),
        "source_round": int(event["source_round_c"]),
        "source_plus_1": int(event["source_round_c"]) + 1,
    }
    out: dict[str, Any] = {}
    for label, bracket in brackets.items():
        token = bracket_lookup(market_index, city, bracket)
        fetch_yes = fetch_fresh and fresh_scope == "all"
        fetch_no = fetch_fresh and (fresh_scope == "all" or label == "t_minus_1")
        out[label] = {
            "bracket_c": bracket,
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


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.output_dir)
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    seen = set(state.get("seen_event_keys") or [])
    now = datetime.now(timezone.utc)

    sources = set(args.sources or [])
    source_rows = source_latest_by_city(Path(args.high_frequency_latest), args.target_date, sources)
    metar_rows = metar_running_max(Path(args.source_events_jsonl), args.target_date)
    paper_path = latest_paper_snapshot()
    orderbook_path = latest_orderbook_snapshot()
    market_index = build_market_index(paper_path, args.target_date)
    snapshot_quotes = build_snapshot_quote_index(orderbook_path, args.target_date)

    new_events = []
    quote_rows = []
    active_events = []

    for city, src in sorted(source_rows.items()):
        metar = metar_rows.get(city)
        if not metar:
            continue
        source_round = int(src["source_temp_round_c"])
        metar_max = int(metar["metar_running_max_round_c"])
        if source_round <= metar_max:
            continue
        t_minus_1 = source_round - 1
        key = "|".join(
            [
                city,
                args.target_date,
                str(src.get("source")),
                str(src.get("source_obs_ts_utc")),
                str(source_round),
                str(metar_max),
                str(t_minus_1),
            ]
        )
        event = {
            "schema_version": "fast_source_stale_book_event_v1",
            "event_key": key,
            "created_at_utc": iso(now),
            "expires_at_utc": iso(now + timedelta(minutes=float(args.follow_minutes))),
            "city": city,
            "target_date": args.target_date,
            "source": src.get("source"),
            "station": src.get("station"),
            "source_obs_ts_utc": src.get("source_obs_ts_utc"),
            "source_detect_ts_utc": src.get("source_detect_ts_utc"),
            "source_temp_c": src.get("temp_c"),
            "source_round_c": source_round,
            "metar_running_max_round_c": metar_max,
            "metar_running_max_temp_c": metar.get("metar_running_max_temp_c"),
            "latest_metar_report_ts_utc": metar.get("latest_report_ts_utc"),
            "latest_metar_detect_ts_utc": metar.get("latest_detect_ts_utc"),
            "latest_metar_temp_c": metar.get("latest_metar_temp_c"),
            "t_minus_1_no_bracket_c": t_minus_1,
            "crossed_brackets_c": list(range(metar_max, source_round)),
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
        expires_at = parse_dt(old.get("expires_at_utc"))
        if expires_at and expires_at > now:
            active_events.append(old)

    # De-dupe active events by key and keep the newest copy.
    active_by_key = {str(row.get("event_key")): row for row in active_events if row.get("event_key")}
    active_events = list(active_by_key.values())

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
        quote_row = {
            "schema_version": "fast_source_stale_book_quote_snapshot_v1",
            "ts_utc": iso(),
            "event_key": event.get("event_key"),
            "city": event.get("city"),
            "target_date": event.get("target_date"),
            "source": event.get("source"),
            "source_obs_ts_utc": event.get("source_obs_ts_utc"),
            "source_detect_ts_utc": event.get("source_detect_ts_utc"),
            "source_temp_c": event.get("source_temp_c"),
            "source_round_c": event.get("source_round_c"),
            "metar_running_max_round_c": event.get("metar_running_max_round_c"),
            "t_minus_1_no_bracket_c": event.get("t_minus_1_no_bracket_c"),
            "quotes": quotes,
            **classification,
            "mode": "telemetry_only_no_orders",
        }
        append_jsonl(out_dir / "quote_snapshots.jsonl", quote_row)
        quote_rows.append(quote_row)

    state = {
        "updated_at_utc": iso(),
        "target_date": args.target_date,
        "seen_event_keys": sorted(seen)[-5000:],
        "active_events": active_events,
    }
    save_state(state_path, state)
    latest = {
        "status": "ok",
        "schema_version": "fast_source_stale_book_latest_v1",
        "generated_at_utc": iso(),
        "target_date": args.target_date,
        "source_cities": sorted(source_rows),
        "metar_cities": sorted(metar_rows),
        "new_events": len(new_events),
        "active_events": len(active_events),
        "quote_snapshots": len(quote_rows),
        "fresh_orderbook_enabled": not args.no_fresh_orderbook,
        "fresh_scope": args.fresh_scope,
        "paper_snapshot_path": str(paper_path) if paper_path else "",
        "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        "events_path": str(out_dir / "events.jsonl"),
        "quote_snapshots_path": str(out_dir / "quote_snapshots.jsonl"),
        "latest_quote_rows": quote_rows[-20:],
    }
    (out_dir / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return latest


def default_target_date() -> str:
    # Keep the always-on observer attached to the active local market day.
    # Use --target-date explicitly for pre-market dry runs.
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default=default_target_date())
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--high-frequency-latest", default=str(HIGH_FREQUENCY_LATEST))
    parser.add_argument("--source-events-jsonl", default=str(SOURCE_EVENTS_JSONL))
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
    parser.add_argument("--stale-no-ask-max", type=float, default=0.35)
    parser.add_argument("--bot-priced-no-bid-min", type=float, default=0.70)
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
