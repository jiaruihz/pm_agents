#!/usr/bin/env python3
"""Build a PIT-ish Polymarket dispute/repricing research panel from public APIs.

The unit is a DisputePrice event.  Oracle requests are grouped by identical
ancillary data so that an adapter's first-dispute reset is not mistaken for the
market's final settlement.  Price evidence uses the first public trade in the
opposite-to-proposal token after the dispute timestamp.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import re
import random
import statistics
import sys
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SUBGRAPHS = {
    "polygon_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-optimistic-oracle-v2/1.1.0/gn",
    "polygon_managed_oo_v2": "https://api.goldsky.com/api/public/project_clus2fndawbcc01w31192938i/subgraphs/polygon-managed-optimistic-oracle-v2/1.0.5/gn",
}
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
DATA_TRADES_URL = "https://data-api.polymarket.com/trades"
ONE = 10**18
HALF = 5 * 10**17
TOO_EARLY = -(2**255)
MARKET_ID_RE = re.compile(r"(?i)market[_ ]?id\s*[:=]\s*(\d+)")
FEE_SCHEDULE_AS_OF = "2026-08-12"
FEE_SCHEDULE_SOURCE = "https://docs.polymarket.com/trading/fees"
# Fallback for historical rows without market-level fee parameters. The
# official schedule is more granular than this research title classifier, so
# mixed/unknown categories use the conservative 5% rate.
CURRENT_FEE_RATE_BY_TOPIC = {
    "sports_esports": 0.05,
    "crypto": 0.07,
    "economics_finance": 0.05,
    "politics": 0.04,
    "awards_media": 0.05,
    "other": 0.05,
}

SPORTS_SOURCE_MARKERS = (
    "wtatennis.com", "atptour.com", "hltv.org", "formula1.com", "itftennis.com",
    "mlb.com", "nba.com", "vlr.gg", "liquipedia.net", "gol.gg", "dotabuff.com",
    "sofascore.com", "nhl.com", "fifa.com", "uefa.com", "ncaa.com", "nfl.com",
    "nascar.com", "indycar.com", "pdc.tv", "rolandgarros.com", "usopen.org",
    "ausopen.com", "icc-cricket.com", "espncricinfo.com", "premierleague.com",
    "bundesliga.com", "laliga.com", "jleague.jp", "koreabaseball.com", "setkacup.com",
    "nwslsoccer.com", "dimayor.com.co", "tff.org", "legaseriea.it", "mlssoccer.com",
    "owgr.com", "theopen.com", "mutuamadridopen.com",
)

SPORTS_TITLE_RE = re.compile(
    r"\b(vs\.?|o/u|over/under|spread:|handicap|exact score|both teams to score|nrfi|"
    r"map \d|game \d|set \d|total kills|total rounds|total games|grand prix|nascar|f1|"
    r"world cup|playoffs?|innings|goals?|assists?|rebounds?|pole position|match winner|"
    r"race winner|ufc|nba mvp|indianapolis 500|breeders.? cup|rugby league|wta |atp |"
    r"fc win|gaming to win|play for (the )?(miami|dallas|houston|atlanta|buffalo|chicago|"
    r"cleveland|denver|green bay|indianapolis|kansas city|los angeles|new england|new york|"
    r"philadelphia|tampa bay|washington|arizona|baltimore|carolina|cincinnati|detroit|"
    r"minnesota|new orleans|pittsburgh|seattle|tennessee|las vegas|jacksonville|san francisco))\b"
)

TEMPERATURE_TITLE_RE = re.compile(
    r"highest temperature in (.+?) be .*? on "
    r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d+)\?",
    re.IGNORECASE,
)
MONTH_NUMBER = {
    month: index
    for index, month in enumerate(
        ("", "January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December")
    )
    if month
}
WEATHER_LOCATION_TIMEZONE = {
    "Atlanta": "America/New_York",
    "Buenos Aires": "America/Argentina/Buenos_Aires",
    "Dallas": "America/Chicago",
    "Hong Kong": "Asia/Hong_Kong",
    "Istanbul": "Europe/Istanbul",
    "Jakarta": "Asia/Jakarta",
    "Kuala Lumpur": "Asia/Kuala_Lumpur",
    "London": "Europe/London",
    "Manila": "Asia/Manila",
    "Moscow": "Europe/Moscow",
    "Munich": "Europe/Berlin",
    "New York City": "America/New_York",
    "Paris": "Europe/Paris",
    "Seattle": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Shenzhen": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Tel Aviv": "Asia/Jerusalem",
    "Wellington": "Pacific/Auckland",
}


def contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def market_theme(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").lower()
    blob = " ".join(
        (title, str(row.get("resolution_source") or "").lower(), str(row.get("ancillary_text") or "").lower())
    )
    if (
        contains_any(blob, SPORTS_SOURCE_MARKERS)
        or SPORTS_TITLE_RE.search(title)
        or ("win the 20" in title and contains_any(title, ("open", "championship", "cup", "prix", "tournament", "masters")))
        or contains_any(title, (
            " beat ", "another team in 20", "series: most nashors", "speedrun record",
            "qualify for the 2026 world chess championship", "virtus.pro to win",
            "team yandex to win", "inner circle to win", "1win to win", "og to win",
        ))
    ):
        return "sports_esports"
    if contains_any(blob, (
        "wunderground.com", "weather.gov", "weather.gov.hk", "nhc.noaa.gov",
        "earthquake.usgs.gov", "data.giss.nasa.gov", "swpc.noaa.gov",
    )) or contains_any(title, (
        "highest temperature", "temperature in ", "hottest on record", "earthquake",
        "megaquake", "hurricane", "tropical storm", "solar flare", "space weather",
        "rainfall", "snowfall",
    )):
        return "weather_natural"
    if contains_any(blob, (
        "binance.com", "finance.yahoo.com", "wsj.com/market-data", "pythdata.app",
        "cmegroup.com", "coinbase.com", "defillama.com", "blackrock.com", "sec.gov",
        "pricecharting.com",
    )) or contains_any(title, (
        "price of bitcoin", "price of ethereum", "price of solana", "price of xrp",
        "bitcoin up or down", "ethereum up or down", "solana up or down", "dogecoin up or down",
        "crypto", "market cap", "stock price", " close at $", " close above $", " close over ",
        "settle at $", "ipo before", "oil price", "silver (si)", "dow jones",
        "rookie card be above", "microstrategy purchase bitcoin", "opec hike", "megaeth",
        "quarterly earnings",
    )):
        return "crypto_financial_markets"
    if contains_any(blob, (
        "the-numbers.com", "boxofficemojo.com", "goldenglobes.com", "producersguild.org",
        "oscars.org", "goodreads.com",
    )) or contains_any(title, (
        "box office", "opening weekend", "netflix", "golden globes", "pga awards", "oscars",
        "best actress", "best actor", "highest grossing movie", "album", "movie", "book of the year",
        "grammy", "emmy", "time 2025 person of the year shortlist", "next james bond",
        "survivor season", "rotten tomatoes", "mrbeast", "video get ", "video confirmed real",
        "taylor swift's wedding", "steam awards", "super bowl halftime show video",
        "macy's thanksgiving", "person of the year be leaked",
    )):
        return "entertainment_awards_media"
    if contains_any(title, (
        "post on x", "x posts", "truth social posts", "tweets from", "say \"", "speak to",
        "mention ", "mentions ", "youtube views", "google trends", "polymarket mindshare",
    )) or contains_any(blob, ("truthsocial.com", "xtracker.polymarket.com", "xtracker.io")):
        return "speech_social_media"
    if contains_any(blob, (
        "election.gov", "elections.am", "registraduria.gov.co", "cne.pt", "cne.hn", "valg.no",
        "senate.gov", "whitehouse.gov", "rollcall.com", "federalregister.gov",
    )) or contains_any(title, (
        "election", "presidential", "prime minister", "most seats", "vote share", "turnout",
        "nominate", "next fed chair", "government shutdown", "trump", "iran", "israel",
        "ceasefire", "war ", "strike ", "strikes ", "diplomatic", "sanctions", "nato",
        "congress", "senate", "party ", "minister", "airspace", "strait of hormuz", "military",
        "forces enter", "ukraine", "houthis", "north korea", "governor", "cartel", "dhs shutdown",
        "jail by", "us recognize", "u.s. anti-", "fordow", "mamdani", "xi jinping",
        "redistricting", "hezbollah", "epstein", "gov shutdown", "us forces", "u.s. forces",
        "french forces", "iraq", "hamas", "taiwan", "ilhan omar", "kidnapper arrested",
        "nothing ever happens",
    )):
        return "politics_geopolitics"
    if contains_any(title, (
        "fed ", "interest rates", "gdp", "cpi", "unemployment", "company", "launch a token",
        "launch ", "release ", "ceo", "acquire", "merger", "spacex", "openai", "apple ",
        "app store", "internet access", "cloudflare", "ipo", "claude ", "tesla ",
        "polymarket us", "gofundme", "donation campaign", "relaunches vine", "iphone 17 pro cost",
    )):
        return "business_macro_tech"
    return "other"


def weather_subtype(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").lower()
    if "highest temperature" in title:
        return "daily_high_temperature"
    if "earthquake" in title:
        return "earthquake_count_window"
    if "space weather" in title or "solar flare" in title:
        return "space_weather_count_window"
    if "hurricane" in title or "tropical storm" in title:
        return "tropical_cyclone"
    if "rainfall" in title or "snowfall" in title:
        return "precipitation"
    return "other_weather_natural"


def temperature_title_parts(row: dict[str, Any]) -> tuple[str, str, int] | None:
    match = TEMPERATURE_TITLE_RE.search(str(row.get("title") or ""))
    if not match:
        return None
    return match.group(1), match.group(2).title(), int(match.group(3))


def weather_resolution_source_class(row: dict[str, Any]) -> str:
    text = " ".join((str(row.get("resolution_source") or ""), str(row.get("ancillary_text") or ""))).lower()
    if "wunderground.com" in text:
        return "wunderground"
    if "earthquake.usgs.gov" in text or "united states geological survey" in text:
        return "usgs_earthquake"
    if "swpc.noaa.gov" in text or "space weather prediction center" in text:
        return "noaa_space_weather"
    if "weather.gov" in text or "national oceanic and atmospheric administration" in text:
        return "noaa_weather_gov"
    return "other_or_unparsed"


def weather_window_key(row: dict[str, Any]) -> str:
    parts = temperature_title_parts(row)
    if parts:
        location, month, day = parts
        year = datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).year
        return f"daily_high_temperature|{location}|{year:04d}-{MONTH_NUMBER[month]:02d}-{day:02d}"
    title = str(row.get("title") or "")
    if "earthquake" in title.lower():
        ancillary = str(row.get("ancillary_text") or "")
        match = re.search(r"occur anywhere on Earth between (.+?)\.\n", ancillary, re.IGNORECASE | re.DOTALL)
        if match:
            return "earthquake_count_window|" + " ".join(match.group(1).lower().split())
    return f"{weather_subtype(row)}|market:{row['market_id']}"


def temperature_proposal_timing(row: dict[str, Any]) -> str:
    parts = temperature_title_parts(row)
    if not parts:
        return "not_temperature"
    location, month, day = parts
    timezone_name = WEATHER_LOCATION_TIMEZONE.get(location)
    if not timezone_name:
        return "timezone_unmapped"
    year = datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).year
    target_date = datetime(year, MONTH_NUMBER[month], day).date()
    local_proposal = datetime.fromtimestamp(int(row["proposal_ts"]), timezone.utc).astimezone(ZoneInfo(timezone_name))
    if local_proposal.date() < target_date:
        return "before_target_date"
    if local_proposal.date() > target_date:
        return "after_target_date_before_source_finality"
    if local_proposal.hour < 6:
        return "target_date_00_05"
    if local_proposal.hour < 12:
        return "target_date_06_11"
    if local_proposal.hour < 18:
        return "target_date_12_17"
    return "target_date_18_23"


def market_end_gap_hours(row: dict[str, Any]) -> float | None:
    value = row.get("end_date")
    if not value:
        return None
    try:
        end_ts = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    proposal_ts = row.get("proposal_ts", row.get("proposalTimestamp"))
    return (int(proposal_ts) - end_ts) / 3600 if proposal_ts else None


def too_early_mechanism(row: dict[str, Any]) -> str:
    """Diagnostic inference from rules/title/timestamps, not the DVM's written rationale."""
    title = str(row.get("title") or "").lower()
    theme = market_theme(row)
    if theme == "sports_esports":
        return "live_contest_or_series_not_final"
    if contains_any(title, (
        "highest temperature", "hottest on record", "price of ", "up or down", "close at $",
        "close above $", "close over ", "settle at $", "box office", "opening weekend", "netflix",
        "post", "tweets", "views", "mentions", "mention ", "say \"", "speak to", "earthquake",
        "space weather", "ships transit", "raise less than", "mindshare",
    )):
        return "measurement_or_observation_window_not_final"
    if contains_any(title, (
        "election", "presidential", "prime minister", "most seats", "vote share", "turnout",
        "golden globes", "pga awards", "oscars", "best actor", "best actress", "person of the year",
        "fed ", "interest rates", "gdp", "cpi", "unemployment", "next james bond",
    )):
        return "official_result_release_or_certification_pending"
    gap = market_end_gap_hours(row)
    if gap is not None and gap < -1:
        return "deadline_or_occurrence_window_still_open"
    return "designated_source_or_event_finality_pending"


def utc_iso(ts: int | str | None) -> str:
    if ts in (None, ""):
        return ""
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def median(values: Iterable[float]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(clean) if clean else None


def percentile(values: Iterable[float], q: float) -> float | None:
    clean = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not clean:
        return None
    pos = (len(clean) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def decode_ancillary(value: str) -> str:
    try:
        return bytes.fromhex(value.removeprefix("0x")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def extract_title(text: str) -> str:
    patterns = (
        r"(?is)q:\s*title:\s*(.*?)(?:,\s*description:|,\s*market_id:|;marketId:)",
        r"(?is)q:(.*?)(?:;marketId:|,\s*market_id:)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ,")
    return ""


def settlement_class(value: str | int | None, proposed: str | int | None = None) -> str:
    if value in (None, ""):
        return "unsettled"
    settled = int(value)
    proposal = int(proposed) if proposed not in (None, "") else None
    if settled == TOO_EARLY:
        return "too_early"
    if settled == HALF:
        return "unknown_50_50"
    if settled in (0, ONE):
        if proposal in (0, ONE):
            return "upheld" if settled == proposal else "binary_flip"
        return "binary"
    return "other"


def topic(title: str) -> str:
    text = title.lower()
    if any(x in text for x in (" vs.", "game ", "match ", "corners", "goals", "kills", "map ", "innings", "t20")):
        return "sports_esports"
    if any(x in text for x in ("bitcoin", "ethereum", "crypto", "solana", "xrp", "doge")):
        return "crypto"
    if any(x in text for x in ("election", "president", "primary", "senate", "congress", "endorse", "minister")):
        return "politics"
    if any(x in text for x in ("gdp", "cpi", "unemployment", "fed ", "interest rate", "settle at $", "index hit")):
        return "economics_finance"
    if any(x in text for x in ("oscar", "grammy", "emmy", "movie", "album", "box office", "winner")):
        return "awards_media"
    return "other"


def reverse_outcome_index(proposed_binary: int) -> int:
    if proposed_binary not in (0, 1):
        raise ValueError(f"proposed_binary must be 0 or 1, got {proposed_binary}")
    # UMA p1=0 maps to Gamma outcome index 1 and p2=1 maps to index 0.
    # The token opposite the proposal is therefore index 0 for p1 and index 1
    # for p2: numerically equal to proposed_binary.
    return proposed_binary


def post_graphql(url: str, query: str, attempts: int = 8) -> dict[str, Any]:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(url, json={"query": query}, timeout=90)
            if response.status_code == 429:
                time.sleep(3.0 * (attempt + 1))
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"][:1]))
            return payload["data"]
        except Exception as exc:  # network boundary
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GraphQL request failed: {error}")


REQUEST_FIELDS = """
id requester identifier ancillaryData proposedPrice settlementPrice
requestTimestamp proposalTimestamp disputeTimestamp settlementTimestamp
proposalHash disputeHash settlementHash
proposer disputer proposalExpirationTimestamp disputeBlockNumber bond currency
"""

def fetch_disputed_requests(name: str, url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skip = 0
    while True:
        query = (
            "{optimisticPriceRequests(first:1000,skip:%d,"
            "where:{disputeTimestamp_gt:0},orderBy:disputeTimestamp,orderDirection:asc){%s}}"
            % (skip, REQUEST_FIELDS)
        )
        batch = post_graphql(url, query)["optimisticPriceRequests"]
        for row in batch:
            row["subgraph"] = name
        rows.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
    return rows


def fetch_rounds(url: str, ancillary: str) -> list[dict[str, Any]]:
    query = (
        "{optimisticPriceRequests(first:20,where:{ancillaryData:\"%s\"},"
        "orderBy:requestTimestamp,orderDirection:asc){%s}}" % (ancillary, REQUEST_FIELDS)
    )
    return post_graphql(url, query)["optimisticPriceRequests"]


def fetch_rounds_cached(cache_dir: Path, name: str, url: str, ancillary: str) -> list[dict[str, Any]]:
    digest = hashlib.sha256(f"{name}:{ancillary}".encode()).hexdigest()
    path = cache_dir / "oracle_rounds" / f"{digest}.json"
    value = load_or_fetch(path, lambda: fetch_rounds(url, ancillary))
    return value if isinstance(value, list) else []


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    attempts: int = 4,
    allow_404: bool = False,
) -> Any:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=90)
            if allow_404 and response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # network boundary
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GET failed url={url}: {error}")


def load_or_fetch(path: Path, fn: Any) -> Any:
    if path.exists():
        return json.loads(path.read_text())
    value = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return value


def fetch_market(cache_dir: Path, market_id: str) -> dict[str, Any]:
    path = cache_dir / "markets" / f"{market_id}.json"
    value = load_or_fetch(
        path,
        lambda: get_json(GAMMA_MARKET_URL.format(market_id=market_id), allow_404=True),
    )
    return value if isinstance(value, dict) else {}


def fetch_trades(
    cache_dir: Path,
    market_id: str,
    condition_id: str,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    path = cache_dir / "trades" / f"{market_id}.json"
    value = load_or_fetch(
        path,
        lambda: get_json(
            DATA_TRADES_URL,
            params={"market": condition_id, "limit": 10000, "offset": 0, "takerOnly": "false"},
        ),
    )
    rows = value if isinstance(value, list) else []
    if len(rows) < 10000:
        return rows
    # The default endpoint is capped at 10k most-recent rows.  Re-query only
    # capped markets inside the dispute-to-settlement window so early dispute
    # prints are not silently lost behind later activity.
    window_path = cache_dir / "trades_window" / f"{market_id}-{start_ts}-{end_ts}.json"
    window = load_or_fetch(
        window_path,
        lambda: get_json(
            DATA_TRADES_URL,
            params={
                "market": condition_id,
                "limit": 10000,
                "offset": 0,
                "takerOnly": "false",
                "start": max(0, start_ts - 86400),
                "end": end_ts,
            },
        ),
    )
    return window if isinstance(window, list) else []


def normalize_market(market: dict[str, Any]) -> dict[str, Any]:
    def array_value(key: str) -> list[Any]:
        value = market.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return []

    return {
        "condition_id": str(market.get("conditionId") or ""),
        "slug": str(market.get("slug") or ""),
        "question": str(market.get("question") or ""),
        "outcomes": [str(x) for x in array_value("outcomes")],
        "token_ids": [str(x) for x in array_value("clobTokenIds")],
        "volume": float(market.get("volumeNum") or market.get("volume") or 0),
        "liquidity": float(market.get("liquidityNum") or market.get("liquidity") or 0),
        "end_date": str(market.get("endDate") or ""),
        "closed": bool(market.get("closed")),
        "uma_resolution_status": str(market.get("umaResolutionStatus") or ""),
        "uma_resolution_statuses": array_value("umaResolutionStatuses"),
        "description": str(market.get("description") or ""),
        "resolution_source": str(market.get("resolutionSource") or ""),
    }


def summarize_group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    result: dict[str, Any] = {}
    for group, items in sorted(groups.items()):
        settled = [x for x in items if x.get("final_binary") in (0, 1)]
        priced = [x for x in settled if x.get("entry_price") is not None]
        reversed_rows = [x for x in settled if x.get("reversed")]
        priced_cost = sum(float(x["entry_price"]) for x in priced)
        priced_payout = sum(1.0 if x.get("reversed") else 0.0 for x in priced)
        result[group] = {
            "signals": len(items),
            "settled_binary": len(settled),
            "reversed": len(reversed_rows),
            "reversal_rate": len(reversed_rows) / len(settled) if settled else None,
            "priced_signals": len(priced),
            "median_entry_price": median(x["entry_price"] for x in priced),
            "gross_hold_pnl_per_share": priced_payout - priced_cost if priced else None,
            "gross_hold_roi_on_cost": (priced_payout - priced_cost) / priced_cost if priced_cost else None,
        }
    return result


def date_block_edge_ci(rows: list[dict[str, Any]], *, samples: int = 5000) -> list[float] | None:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        date = datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).date().isoformat()
        by_date.setdefault(date, []).append(row)
    dates = sorted(by_date)
    if not dates:
        return None
    rng = random.Random(20260811)
    results: list[float] = []
    for _ in range(samples):
        sampled = [rng.choice(dates) for _ in dates]
        replay = [row for date in sampled for row in by_date[date]]
        cost = sum(float(row["entry_price"]) for row in replay)
        fee = sum(
            CURRENT_FEE_RATE_BY_TOPIC[row["topic"]]
            * float(row["entry_price"])
            * (1 - float(row["entry_price"]))
            for row in replay
        )
        payout = sum(1.0 if row["reversed"] else 0.0 for row in replay)
        results.append((payout - cost - fee) / len(replay))
    results.sort()
    return [results[int((samples - 1) * q)] for q in (0.025, 0.5, 0.975)]


def address_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("disputer") or "").lower() for row in rows if row.get("disputer"))
    total = sum(counts.values())
    ranked = counts.most_common()
    self_disputes = sum(
        1
        for row in rows
        if row.get("proposer")
        and row.get("disputer")
        and str(row["proposer"]).lower() == str(row["disputer"]).lower()
    )
    return {
        "events_with_disputer": total,
        "unique_disputers": len(counts),
        "same_proposer_disputer_events": self_disputes,
        "same_proposer_disputer_share": self_disputes / total if total else None,
        "top_1_share": sum(count for _, count in ranked[:1]) / total if total else None,
        "top_3_share": sum(count for _, count in ranked[:3]) / total if total else None,
        "top_5_share": sum(count for _, count in ranked[:5]) / total if total else None,
        "top_10_share": sum(count for _, count in ranked[:10]) / total if total else None,
        "hhi": sum((count / total) ** 2 for count in counts.values()) if total else None,
        "top_disputers": [{"address": address, "events": count, "share": count / total} for address, count in ranked[:10]],
    }


def latency_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values: list[float] = []
    for row in rows:
        dispute_ts = row.get("disputeTimestamp", row.get("dispute_ts"))
        proposal_ts = row.get("proposalTimestamp", row.get("proposal_ts"))
        if dispute_ts and proposal_ts:
            values.append((int(dispute_ts) - int(proposal_ts)) / 60)
    return {
        "events": len(values),
        "p10_minutes": percentile(values, 0.1),
        "p25_minutes": percentile(values, 0.25),
        "median_minutes": median(values),
        "p75_minutes": percentile(values, 0.75),
        "p90_minutes": percentile(values, 0.9),
        "share_within_1m": sum(value <= 1 for value in values) / len(values) if values else None,
        "share_within_5m": sum(value <= 5 for value in values) / len(values) if values else None,
        "share_within_15m": sum(value <= 15 for value in values) / len(values) if values else None,
        "share_within_30m": sum(value <= 30 for value in values) / len(values) if values else None,
        "share_within_60m": sum(value <= 60 for value in values) / len(values) if values else None,
        "share_after_110m": sum(value > 110 for value in values) / len(values) if values else None,
    }


def monthly_counts(rows: list[dict[str, Any]], ts_field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row.get(ts_field)
        if value:
            counts[datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m")] += 1
    return dict(sorted(counts.items()))


def summarize_too_early_groups(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    result: dict[str, Any] = {}
    unique_market_count = len({row["market_id"] for row in rows})
    for group, items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        external = [
            row
            for row in items
            if not row.get("proposer")
            or not row.get("disputer")
            or str(row["proposer"]).lower() != str(row["disputer"]).lower()
        ]
        gaps = [gap for row in items if (gap := market_end_gap_hours(row)) is not None]
        result[group] = {
            "events": len(items),
            "share": len(items) / len(rows) if rows else None,
            "unique_markets": len({row["market_id"] for row in items}),
            "unique_market_share": (
                len({row["market_id"] for row in items}) / unique_market_count
                if unique_market_count
                else None
            ),
            "external_disputer_events": len(external),
            "median_proposal_to_dispute_minutes": median(row["proposal_to_dispute_minutes"] for row in items),
            "share_disputed_within_5m": sum(row["proposal_to_dispute_minutes"] <= 5 for row in items) / len(items),
            "end_date_coverage": len(gaps),
            "share_proposed_before_gamma_end": sum(gap < 0 for gap in gaps) / len(gaps) if gaps else None,
        }
    return result


def reverse_entry_price_band(row: dict[str, Any]) -> str:
    price = float(row["entry_price"])
    if price < 0.05:
        return "00_05c"
    if price < 0.20:
        return "05_20c"
    if price < 0.50:
        return "20_50c"
    if price < 0.80:
        return "50_80c"
    return "80_100c"


def repricing_slice_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize first-market, clear non-P4 adjudications on one fixed row set."""
    priced = [row for row in rows if row.get("entry_price") is not None]
    flips = sum(row["request_settlement_class"] == "binary_flip" for row in rows)
    priced_flips = sum(row["request_settlement_class"] == "binary_flip" for row in priced)
    cost = sum(float(row["entry_price"]) for row in priced)
    fee = sum(
        CURRENT_FEE_RATE_BY_TOPIC[row["topic"]]
        * float(row["entry_price"])
        * (1 - float(row["entry_price"]))
        for row in priced
    )
    probabilities = [float(row["entry_price"]) for row in priced]
    labels = [1.0 if row["request_settlement_class"] == "binary_flip" else 0.0 for row in priced]
    base_rate = sum(labels) / len(labels) if labels else None
    market_brier = (
        sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)
        if labels else None
    )
    market_logloss = (
        -sum(
            label * math.log(min(max(probability, 1e-6), 1 - 1e-6))
            + (1 - label) * math.log(min(max(1 - probability, 1e-6), 1 - 1e-6))
            for probability, label in zip(probabilities, labels)
        ) / len(labels)
        if labels else None
    )
    preknown = [row for row in priced if row.get("pre_dispute_price") is not None]
    return {
        "markets": len(rows),
        "independent_utc_dates": len({row["dispute_utc"][:10] for row in rows}),
        "binary_flips": flips,
        "binary_flip_rate": flips / len(rows) if rows else None,
        "priced_markets": len(priced),
        "price_coverage": len(priced) / len(rows) if rows else None,
        "priced_binary_flips": priced_flips,
        "priced_binary_flip_rate": priced_flips / len(priced) if priced else None,
        "mean_reverse_entry_price": cost / len(priced) if priced else None,
        "median_reverse_entry_price": median(row["entry_price"] for row in priced),
        "gross_edge_per_priced_market": (priced_flips - cost) / len(priced) if priced else None,
        "gross_roi_on_public_print_cost": (priced_flips - cost) / cost if cost else None,
        "fee_adjusted_edge_per_priced_market": (
            (priced_flips - cost - fee) / len(priced) if priced else None
        ),
        "fee_adjusted_roi_on_public_print_cost_plus_fee": (
            (priced_flips - cost - fee) / (cost + fee) if cost + fee else None
        ),
        "fee_adjusted_date_block_edge_ci_95": date_block_edge_ci(priced),
        "market_probability_brier": market_brier,
        "market_probability_logloss": market_logloss,
        "constant_base_rate_brier": base_rate * (1 - base_rate) if base_rate is not None else None,
        "constant_base_rate_logloss": (
            -base_rate * math.log(base_rate) - (1 - base_rate) * math.log(1 - base_rate)
            if base_rate not in (None, 0, 1) else None
        ),
        "median_first_public_print_delay_minutes": median(row["entry_delay_minutes"] for row in priced),
        "median_first_public_print_size_shares": median(row.get("entry_trade_size") for row in priced),
        "pre_dispute_price_coverage": len(preknown),
        "median_pre_dispute_reverse_price": median(row["pre_dispute_price"] for row in preknown),
        "median_first_print_minus_pre_dispute_price": median(
            float(row["entry_price"]) - float(row["pre_dispute_price"]) for row in preknown
        ),
    }


def summarize_repricing_groups(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    return {
        group: repricing_slice_metrics(items)
        for group, items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", default="2026-08-11T23:59:59+00:00")
    parser.add_argument("--min-date", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--out-dir", default="runtime/dispute_repricing/dispute_repricing_v1")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--refresh", action="store_true")
    return parser


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def main() -> None:
    args = build_parser().parse_args()
    out_dir = ROOT / args.out_dir
    cache_dir = out_dir / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.refresh and cache_dir.exists():
        raise RuntimeError("--refresh requires manually moving the cache; destructive deletion is intentionally not automatic")

    cutoff = parse_ts(args.cutoff)
    min_date = parse_ts(args.min_date)
    disputed: list[dict[str, Any]] = []
    for name, url in SUBGRAPHS.items():
        disputed.extend(fetch_disputed_requests(name, url))
    polymarket_disputes: list[dict[str, Any]] = []
    ancillary_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in disputed:
        text = decode_ancillary(str(row.get("ancillaryData") or ""))
        match = MARKET_ID_RE.search(text)
        dispute_ts = int(row.get("disputeTimestamp") or 0)
        if not match or not (min_date <= dispute_ts <= cutoff):
            continue
        row = dict(row)
        row["market_id"] = match.group(1)
        row["ancillary_text"] = text
        row["title"] = extract_title(text)
        polymarket_disputes.append(row)
        ancillary_groups[(row["subgraph"], row["ancillaryData"])] = row
    print(f"disputed requests={len(disputed)} in-scope polymarket={len(polymarket_disputes)} groups={len(ancillary_groups)}", flush=True)

    round_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_rounds_cached, cache_dir, name, SUBGRAPHS[name], ancillary): (name, ancillary)
            for name, ancillary in ancillary_groups
        }
        for index, future in enumerate(as_completed(futures), 1):
            round_map[futures[future]] = future.result()
            if index % 250 == 0:
                print(f"oracle groups fetched {index}/{len(futures)}", flush=True)

    signals: list[dict[str, Any]] = []
    for row in polymarket_disputes:
        rounds = round_map[(row["subgraph"], row["ancillaryData"])]
        round_index = next((i + 1 for i, x in enumerate(rounds) if x["id"] == row["id"]), None)
        final_round = next(
            (x for x in reversed(rounds) if x.get("settlementPrice") is not None and int(x["settlementPrice"]) in (0, ONE, HALF)),
            None,
        )
        final_binary = None
        if final_round and int(final_round["settlementPrice"]) in (0, ONE):
            final_binary = int(final_round["settlementPrice"]) // ONE
        proposed = int(row["proposedPrice"])
        proposed_binary = proposed // ONE if proposed in (0, ONE) else None
        signals.append(
            {
                "signal_id": f"{row['subgraph']}:{row['id']}",
                "subgraph": row["subgraph"],
                "requester": row["requester"],
                "proposer": row.get("proposer"),
                "disputer": row.get("disputer"),
                "bond_raw": int(row["bond"]) if row.get("bond") is not None else None,
                "currency": row.get("currency"),
                "market_id": row["market_id"],
                "title": row["title"],
                "topic": topic(row["title"]),
                "round_index": round_index,
                "round_count": len(rounds),
                "proposed_binary": proposed_binary,
                "request_settlement_class": settlement_class(row.get("settlementPrice"), proposed),
                "request_settlement_binary": (
                    int(row["settlementPrice"]) // ONE
                    if row.get("settlementPrice") is not None and int(row["settlementPrice"]) in (0, ONE)
                    else None
                ),
                "final_binary": final_binary,
                "reversed": final_binary != proposed_binary if final_binary is not None and proposed_binary is not None else None,
                "request_ts": int(row["requestTimestamp"]),
                "proposal_ts": int(row["proposalTimestamp"]),
                "proposal_expiration_ts": int(row["proposalExpirationTimestamp"]) if row.get("proposalExpirationTimestamp") else None,
                "dispute_ts": int(row["disputeTimestamp"]),
                "request_settlement_ts": int(row["settlementTimestamp"]) if row.get("settlementTimestamp") else None,
                "final_settlement_ts": int(final_round["settlementTimestamp"]) if final_round and final_round.get("settlementTimestamp") else None,
                "proposal_to_dispute_minutes": (int(row["disputeTimestamp"]) - int(row["proposalTimestamp"])) / 60,
                "dispute_utc": utc_iso(row["disputeTimestamp"]),
                "final_settlement_utc": utc_iso(final_round.get("settlementTimestamp") if final_round else None),
                "ancillary_text": row["ancillary_text"],
            }
        )

    settled_market_ids = sorted({x["market_id"] for x in signals if x["final_binary"] in (0, 1)})
    market_map: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_market, cache_dir, mid): mid for mid in settled_market_ids}
        for index, future in enumerate(as_completed(futures), 1):
            market_id = futures[future]
            try:
                market_map[market_id] = normalize_market(future.result())
            except Exception as exc:
                print(f"market fetch failed market_id={market_id}: {exc}", flush=True)
                market_map[market_id] = normalize_market({})
            if index % 250 == 0:
                print(f"markets fetched {index}/{len(futures)}", flush=True)

    trade_map: dict[str, list[dict[str, Any]]] = {}
    signal_windows: dict[str, tuple[int, int]] = {}
    for signal in signals:
        if signal["final_binary"] not in (0, 1):
            continue
        start_ts, end_ts = signal_windows.get(signal["market_id"], (signal["dispute_ts"], signal["final_settlement_ts"] or cutoff))
        signal_windows[signal["market_id"]] = (
            min(start_ts, signal["dispute_ts"]),
            max(end_ts, signal["final_settlement_ts"] or cutoff),
        )
    trade_targets = {
        mid: (market["condition_id"], *signal_windows[mid])
        for mid, market in market_map.items()
        if market.get("condition_id") and len(market.get("token_ids") or []) == 2 and mid in signal_windows
    }
    with ThreadPoolExecutor(max_workers=max(4, min(args.workers, 12))) as pool:
        futures = {
            pool.submit(fetch_trades, cache_dir, mid, condition_id, start_ts, end_ts): mid
            for mid, (condition_id, start_ts, end_ts) in trade_targets.items()
        }
        for index, future in enumerate(as_completed(futures), 1):
            market_id = futures[future]
            try:
                trade_map[market_id] = future.result()
            except Exception as exc:
                print(f"trade fetch failed market_id={market_id}: {exc}", flush=True)
                trade_map[market_id] = []
            if index % 100 == 0:
                print(f"trade histories fetched {index}/{len(futures)}", flush=True)

    for signal in signals:
        market = market_map.get(signal["market_id"], {})
        signal.update({key: market.get(key) for key in (
            "condition_id", "slug", "outcomes", "token_ids", "volume", "liquidity", "end_date",
            "closed", "uma_resolution_status", "uma_resolution_statuses", "resolution_source",
        )})
        if signal["final_binary"] not in (0, 1) or signal["proposed_binary"] not in (0, 1):
            signal["price_coverage_status"] = "no_binary_label"
            continue
        token_ids = market.get("token_ids") or []
        if len(token_ids) != 2:
            signal["price_coverage_status"] = "missing_tokens"
            continue
        reverse_index = reverse_outcome_index(int(signal["proposed_binary"]))
        reverse_token = token_ids[reverse_index]
        trades = [
            x for x in trade_map.get(signal["market_id"], [])
            if str(x.get("asset") or "") == reverse_token and x.get("timestamp") is not None
        ]
        trades.sort(key=lambda x: (int(x["timestamp"]), str(x.get("transactionHash") or "")))
        before = [x for x in trades if int(x["timestamp"]) <= int(signal["dispute_ts"])]
        after = [
            x for x in trades
            if int(x["timestamp"]) > int(signal["dispute_ts"])
            and (signal["final_settlement_ts"] is None or int(x["timestamp"]) <= int(signal["final_settlement_ts"]))
        ]
        signal["reverse_outcome_index"] = reverse_index
        signal["reverse_outcome"] = (market.get("outcomes") or ["", ""])[reverse_index]
        signal["reverse_token_id"] = reverse_token
        signal["trade_rows_returned"] = len(trade_map.get(signal["market_id"], []))
        signal["reverse_token_trade_rows"] = len(trades)
        signal["pre_dispute_price"] = float(before[-1]["price"]) if before else None
        signal["entry_price"] = float(after[0]["price"]) if after else None
        signal["entry_trade_side"] = str(after[0].get("side") or "") if after else ""
        signal["entry_trade_size"] = float(after[0].get("size") or 0) if after else None
        signal["entry_trade_ts"] = int(after[0]["timestamp"]) if after else None
        signal["entry_delay_minutes"] = (
            (int(after[0]["timestamp"]) - int(signal["dispute_ts"])) / 60 if after else None
        )
        signal["price_coverage_status"] = "priced" if after else "no_post_dispute_trade"
        signal["gross_pnl_per_share"] = (
            (1.0 if signal["reversed"] else 0.0) - float(after[0]["price"]) if after else None
        )
        signal["gross_roi_on_cost"] = (
            signal["gross_pnl_per_share"] / float(after[0]["price"])
            if after and float(after[0]["price"]) > 0 else None
        )
        for threshold in (0.5, 0.75, 0.9):
            cross = next((x for x in after if float(x["price"]) >= threshold), None)
            signal[f"hours_to_{int(threshold * 100)}c"] = (
                (int(cross["timestamp"]) - int(signal["dispute_ts"])) / 3600 if cross else None
            )

    binary = [x for x in signals if x["final_binary"] in (0, 1) and x["proposed_binary"] in (0, 1)]
    priced = [x for x in binary if x.get("entry_price") is not None]
    priced_cost = sum(float(x["entry_price"]) for x in priced)
    priced_payout = sum(1.0 if x["reversed"] else 0.0 for x in priced)
    reversal = [x for x in binary if x["reversed"]]
    first_by_market: dict[str, dict[str, Any]] = {}
    for row in sorted(binary, key=lambda x: (x["dispute_ts"], x["signal_id"])):
        first_by_market.setdefault(row["market_id"], row)
    first_rows = list(first_by_market.values())
    first_priced = [x for x in first_rows if x.get("entry_price") is not None]
    first_cost = sum(float(x["entry_price"]) for x in first_priced)
    first_fee = sum(
        CURRENT_FEE_RATE_BY_TOPIC[x["topic"]]
        * float(x["entry_price"])
        * (1 - float(x["entry_price"]))
        for x in first_priced
    )
    first_payout = sum(1.0 if x["reversed"] else 0.0 for x in first_priced)
    conditional_crossing: dict[str, Any] = {}
    first_true_reversal = [x for x in first_priced if x["reversed"]]
    for threshold in (0.5, 0.75, 0.9):
        field = f"hours_to_{int(threshold * 100)}c"
        preknown = [x for x in first_true_reversal if x.get("pre_dispute_price") is not None]
        crossed = [
            x for x in preknown
            if float(x["pre_dispute_price"]) < threshold and x.get(field) is not None
        ]
        conditional_crossing[f"{int(threshold * 100)}c"] = {
            "pre_price_known": len(preknown),
            "already_above_before_dispute": sum(float(x["pre_dispute_price"]) >= threshold for x in preknown),
            "post_dispute_crosses": len(crossed),
            "median_hours": median(x[field] for x in crossed),
            "p90_hours": percentile((x[field] for x in crossed), 0.9),
        }
    for row in signals:
        row["market_theme_v1"] = market_theme(row)
    too_early_disputes = [row for row in signals if row["request_settlement_class"] == "too_early"]
    for row in too_early_disputes:
        row["too_early_mechanism_v1"] = too_early_mechanism(row)
    clear_non_p4_first = [
        row for row in first_rows
        if row["request_settlement_class"] in {"binary_flip", "upheld"}
    ]
    clear_non_p4_priced = [row for row in clear_non_p4_first if row.get("entry_price") is not None]
    for row in clear_non_p4_priced:
        row["reverse_entry_price_band_v1"] = reverse_entry_price_band(row)
    true_non_p4_flips = [
        row for row in clear_non_p4_priced
        if row["request_settlement_class"] == "binary_flip"
    ]
    non_p4_crossing: dict[str, Any] = {}
    for threshold in (0.5, 0.75, 0.9):
        field = f"hours_to_{int(threshold * 100)}c"
        eligible = [
            row for row in true_non_p4_flips
            if row.get("pre_dispute_price") is None or float(row["pre_dispute_price"]) < threshold
        ]
        crossed = [row for row in eligible if row.get(field) is not None]
        non_p4_crossing[f"{int(threshold * 100)}c"] = {
            "eligible_true_flips": len(eligible),
            "crossed": len(crossed),
            "median_hours": median(row[field] for row in crossed),
            "p90_hours": percentile((row[field] for row in crossed), 0.9),
        }
    recent_start = cutoff - 365 * 86400
    calendar_2026_start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    calendar_2026_too_early = [
        row for row in too_early_disputes
        if calendar_2026_start <= int(row["dispute_ts"]) <= cutoff
    ]
    calendar_2026_weather = [
        row for row in calendar_2026_too_early
        if row["market_theme_v1"] == "weather_natural"
    ]
    for row in calendar_2026_weather:
        row["weather_subtype_v1"] = weather_subtype(row)
        row["weather_resolution_source_v1"] = weather_resolution_source_class(row)
        row["weather_window_key_v1"] = weather_window_key(row)
        row["temperature_proposal_timing_v1"] = temperature_proposal_timing(row)
    calendar_2026_temperature = [
        row for row in calendar_2026_weather
        if row["weather_subtype_v1"] == "daily_high_temperature"
    ]
    recent_disputes = [row for row in signals if int(row["dispute_ts"]) >= recent_start]
    recent_too_early = [row for row in too_early_disputes if int(row["dispute_ts"]) >= recent_start]
    recent_external_too_early = [
        row
        for row in recent_too_early
        if not row.get("proposer")
        or not row.get("disputer")
        or str(row["proposer"]).lower() != str(row["disputer"]).lower()
    ]
    summary = {
        "schema_version": "dispute_repricing_research_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": {
            "min_dispute_utc": args.min_date,
            "max_dispute_utc": args.cutoff,
            "sources": SUBGRAPHS,
            "unit": "UMA DisputePrice event mapped to a Polymarket market_id",
            "price_expression": "first public Data API trade in the token opposite the proposal after dispute",
            "trade_api_limit": 10000,
        },
        "fee_model": {
            "schedule_as_of": FEE_SCHEDULE_AS_OF,
            "source": FEE_SCHEDULE_SOURCE,
            "formula": "shares * fee_rate * price * (1-price)",
            "rates_by_research_topic": CURRENT_FEE_RATE_BY_TOPIC,
            "limitation": "topic-level fallback, not historical market-level fee parameters or realized fees",
        },
        "funnel": {
            "all_subgraph_dispute_requests": len(disputed),
            "in_scope_polymarket_dispute_events": len(signals),
            "settled_binary_dispute_events": len(binary),
            "unique_settled_markets": len({x["market_id"] for x in binary}),
            "priced_dispute_events": len(priced),
            "reversed_settled_binary_events": len(reversal),
        },
        "headline": {
            "reversal_rate": len(reversal) / len(binary) if binary else None,
            "median_proposal_to_dispute_minutes": median(x["proposal_to_dispute_minutes"] for x in binary),
            "p90_proposal_to_dispute_minutes": percentile((x["proposal_to_dispute_minutes"] for x in binary), 0.9),
            "median_reverse_entry_price": median(x["entry_price"] for x in priced),
            "gross_hold_pnl_per_share": priced_payout - priced_cost if priced else None,
            "gross_hold_roi_on_cost": (priced_payout - priced_cost) / priced_cost if priced_cost else None,
            "median_hours_to_50c_on_true_reversals": median(x.get("hours_to_50c") for x in reversal),
            "median_hours_to_75c_on_true_reversals": median(x.get("hours_to_75c") for x in reversal),
            "median_hours_to_90c_on_true_reversals": median(x.get("hours_to_90c") for x in reversal),
        },
        "unique_first_dispute": {
            "settled_binary_markets": len(first_rows),
            "reversed_markets": sum(1 for x in first_rows if x["reversed"]),
            "reversal_rate": sum(1 for x in first_rows if x["reversed"]) / len(first_rows) if first_rows else None,
            "priced_markets": len(first_priced),
            "priced_reversal_rate": first_payout / len(first_priced) if first_priced else None,
            "mean_reverse_entry_price": first_cost / len(first_priced) if first_priced else None,
            "gross_edge_per_share": (first_payout - first_cost) / len(first_priced) if first_priced else None,
            "gross_roi_on_cost": (first_payout - first_cost) / first_cost if first_cost else None,
            "current_fee_schedule_edge_per_share": (
                (first_payout - first_cost - first_fee) / len(first_priced) if first_priced else None
            ),
            "current_fee_schedule_roi_on_cost_plus_fee": (
                (first_payout - first_cost - first_fee) / (first_cost + first_fee)
                if first_cost + first_fee else None
            ),
            "current_fee_schedule_date_block_edge_ci_95": date_block_edge_ci(first_priced),
            "median_first_print_size_shares": median(x.get("entry_trade_size") for x in first_priced),
            "conditional_repricing_time": conditional_crossing,
        },
        "rule_adjudication_repricing": {
            "status": "historical_public_print_evidence_only; execution readiness not established",
            "scope": {
                "unit": "first dispute per unique Polymarket market",
                "included_request_outcomes": ["binary_flip", "upheld"],
                "excluded_request_outcomes": ["too_early", "unknown_50_50", "unsettled"],
                "entry_expression": "first public reverse-token trade strictly after dispute",
            },
            "signal_funnel": {
                "first_unique_settled_binary_markets": len(first_rows),
                "clear_non_p4_first_disputes": len(clear_non_p4_first),
                "binary_flip_labels": sum(
                    row["request_settlement_class"] == "binary_flip" for row in clear_non_p4_first
                ),
                "upheld_labels": sum(
                    row["request_settlement_class"] == "upheld" for row in clear_non_p4_first
                ),
            },
            "evidence_funnel": {
                "clear_non_p4_first_disputes": len(clear_non_p4_first),
                "post_dispute_public_print_covered": len(clear_non_p4_priced),
                "pre_dispute_public_print_covered": sum(
                    row.get("pre_dispute_price") is not None for row in clear_non_p4_priced
                ),
                "fresh_executable_book_covered": 0,
                "actual_strategy_fills": 0,
            },
            "overall": repricing_slice_metrics(clear_non_p4_first),
            "by_market_theme_v1": summarize_repricing_groups(
                clear_non_p4_first, "market_theme_v1"
            ),
            "by_reverse_entry_price_band_v1": summarize_repricing_groups(
                clear_non_p4_priced, "reverse_entry_price_band_v1"
            ),
            "by_dispute_year": summarize_repricing_groups(
                [dict(row, dispute_year=row["dispute_utc"][:4]) for row in clear_non_p4_first],
                "dispute_year",
            ),
            "by_proposed_outcome": summarize_repricing_groups(
                [
                    dict(row, proposed_outcome="NO" if row["proposed_binary"] == 0 else "YES")
                    for row in clear_non_p4_first
                ],
                "proposed_outcome",
            ),
            "true_flip_repricing_time": non_p4_crossing,
            "limitations": [
                "Public prints are not contemporaneous executable asks or depth.",
                "The historical panel observes successful onchain disputes, not failed dispute races.",
                "Theme labels are deterministic diagnostics; no PIT rule-research model has been scored yet.",
                "Positive pooled public-print edge is not a deployable strategy until frozen-forward book and fill evidence exist.",
            ],
        },
        "too_early_opportunity": {
            "dispute_events": len(signals),
            "too_early_events": len(too_early_disputes),
            "too_early_share_of_disputes": len(too_early_disputes) / len(signals) if signals else None,
            "unique_too_early_markets": len({row["market_id"] for row in too_early_disputes}),
            "independent_utc_dates": len({datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).date() for row in too_early_disputes}),
            "monthly_counts": monthly_counts(too_early_disputes, "dispute_ts"),
            "calendar_2026_through_cutoff": {
                "scope": f"2026-01-01T00:00:00+00:00 through {args.cutoff}",
                "too_early_events": len(calendar_2026_too_early),
                "unique_too_early_markets": len({row["market_id"] for row in calendar_2026_too_early}),
                "independent_utc_dates": len({
                    datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).date()
                    for row in calendar_2026_too_early
                }),
                "monthly_counts": monthly_counts(calendar_2026_too_early, "dispute_ts"),
                "by_market_theme_v1": summarize_too_early_groups(
                    calendar_2026_too_early, "market_theme_v1"
                ),
                "by_mechanism_v1": summarize_too_early_groups(
                    calendar_2026_too_early, "too_early_mechanism_v1"
                ),
                "weather_natural_detail_v1": {
                    "events": len(calendar_2026_weather),
                    "unique_markets": len({row["market_id"] for row in calendar_2026_weather}),
                    "independent_measurement_windows": len({
                        row["weather_window_key_v1"] for row in calendar_2026_weather
                    }),
                    "independent_utc_dates": len({
                        datetime.fromtimestamp(int(row["dispute_ts"]), timezone.utc).date()
                        for row in calendar_2026_weather
                    }),
                    "monthly_counts": monthly_counts(calendar_2026_weather, "dispute_ts"),
                    "by_subtype_v1": summarize_too_early_groups(
                        calendar_2026_weather, "weather_subtype_v1"
                    ),
                    "by_resolution_source_v1": summarize_too_early_groups(
                        calendar_2026_weather, "weather_resolution_source_v1"
                    ),
                    "latency": latency_profile(calendar_2026_weather),
                    "disputer_concentration": address_concentration(calendar_2026_weather),
                    "proposed_outcomes": dict(sorted(Counter(
                        "NO" if row["proposed_binary"] == 0 else "YES"
                        for row in calendar_2026_weather
                    ).items())),
                    "bond_raw_counts": dict(sorted(Counter(
                        str(row["bond_raw"]) for row in calendar_2026_weather
                    ).items())),
                    "daily_high_temperature": {
                        "events": len(calendar_2026_temperature),
                        "independent_city_dates": len({
                            row["weather_window_key_v1"] for row in calendar_2026_temperature
                        }),
                        "by_location": dict(sorted(Counter(
                            temperature_title_parts(row)[0]
                            for row in calendar_2026_temperature
                            if temperature_title_parts(row)
                        ).items(), key=lambda item: (-item[1], item[0]))),
                        "by_source": summarize_too_early_groups(
                            calendar_2026_temperature, "weather_resolution_source_v1"
                        ),
                        "proposal_local_timing": dict(sorted(Counter(
                            row["temperature_proposal_timing_v1"]
                            for row in calendar_2026_temperature
                        ).items())),
                        "proposed_outcomes": dict(sorted(Counter(
                            "NO" if row["proposed_binary"] == 0 else "YES"
                            for row in calendar_2026_temperature
                        ).items())),
                    },
                },
            },
            "last_365d": {
                "dispute_events": len(recent_disputes),
                "too_early_events": len(recent_too_early),
                "too_early_share_of_disputes": len(recent_too_early) / len(recent_disputes) if recent_disputes else None,
            },
            "competition_all_history": {
                "latency": latency_profile(too_early_disputes),
                "disputer_concentration": address_concentration(too_early_disputes),
            },
            "competition_last_365d": {
                "latency": latency_profile(recent_too_early),
                "disputer_concentration": address_concentration(recent_too_early),
            },
            "external_disputer_competition_last_365d": {
                "events": len(recent_external_too_early),
                "latency": latency_profile(recent_external_too_early),
                "disputer_concentration": address_concentration(recent_external_too_early),
            },
            "by_market_theme_v1": summarize_too_early_groups(too_early_disputes, "market_theme_v1"),
            "by_mechanism_v1": summarize_too_early_groups(too_early_disputes, "too_early_mechanism_v1"),
            "adjudicated_dispute_outcomes": {
                "too_early_disputer_wins": sum(row["request_settlement_class"] == "too_early" for row in signals),
                "binary_flip_disputer_wins": sum(row["request_settlement_class"] == "binary_flip" for row in signals),
                "unknown_50_50_disputer_wins": sum(row["request_settlement_class"] == "unknown_50_50" for row in signals),
                "upheld_proposer_wins": sum(row["request_settlement_class"] == "upheld" for row in signals),
                "clear_outcome_disputer_loss_rate": (
                    sum(row["request_settlement_class"] == "upheld" for row in signals)
                    / sum(row["request_settlement_class"] in {"too_early", "binary_flip", "unknown_50_50", "upheld"} for row in signals)
                ),
            },
            "observable_competition_limit": "Only the successful onchain disputer is indexed; reverted, outbid, or abandoned attempts are not observable in this subgraph.",
        },
        "by_round": summarize_group(binary, "round_index"),
        "by_request_settlement_class": summarize_group(binary, "request_settlement_class"),
        "by_topic": summarize_group(binary, "topic"),
        "limitations": [
            "Public trades are prints, not contemporaneous executable ask/depth; gross ROI is not a backtest.",
            "Data API returns at most 10,000 rows per market; very active markets can lack the dispute window.",
            "Topic classification is a title-keyword diagnostic, not a trained taxonomy.",
            "Clarification/voter-discussion evidence is not yet encoded, so this panel estimates base rates, not case merits.",
            "This run enumerates disputed requests, not every proposal; Too Early share is conditional on a dispute and is not a platform-wide proposal rate.",
            "Disputer concentration is a wallet-level lower bound on competition; one operator may use multiple wallets and failed races are absent.",
            "Theme and Too Early mechanism labels are deterministic diagnostics from title/rules/source/timestamps, not DVM-written rationales.",
        ],
    }

    signals_path = out_dir / "signals.jsonl"
    with signals_path.open("w") as handle:
        for row in signals:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"signals={signals_path}", flush=True)


if __name__ == "__main__":
    main()
