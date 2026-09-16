"""Deterministic evidence resolvers for dispute RuleVerdicts.

A verified verdict is stronger than a dispute prior: it recomputes the market
outcome from the resolution source named in the rules.  Unsupported domains
fail closed and remain ``unverified``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import hashlib
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
ET = ZoneInfo("America/New_York")
MONTH_TIME_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:,\s*(\d{4}))?,?\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*ET\b",
    re.IGNORECASE,
)
ASSET_SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "eth": "ETHUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "doge": "DOGEUSDT",
    "bnb": "BNBUSDT",
    "hype": "HYPEUSDT",
}
HTML_TAG_RE = re.compile(r"<[^>]+>")
GOLGG_GAME_URL = "https://gol.gg/game/stats/{game_id}/page-game/"
GOLGG_TIMELINE_URL = "https://gol.gg/game/stats/{game_id}/page-timeline/"
ESPN_SOCCER_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"


def _base_verdict(status: str, reason: str, *, observed_at_utc: str) -> dict[str, Any]:
    return {
        "schema_version": "dispute_rule_verdict_v1",
        "status": status,
        "winning_outcome": None,
        "source_url": None,
        "observed_at_utc": observed_at_utc,
        "resolver_id": None,
        "reason": reason,
    }


def _outcome_value(outcomes: list[str], wanted: str) -> str | None:
    return next((value for value in outcomes if value.casefold() == wanted.casefold()), None)


def _html_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(HTML_TAG_RE.sub(" ", value))).strip()


def parse_golgg_kill_timeline(page_html: str) -> list[dict[str, Any]]:
    """Parse champion kills from a Gol.gg game timeline without JS or DOM dependencies."""
    events: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.IGNORECASE | re.DOTALL):
        if "kill-icon" not in row_html:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cells) < 7:
            continue
        clock = _html_text(cells[0])
        match = re.fullmatch(r"(\d{1,3}):(\d{2})", clock)
        if not match:
            continue
        killer = _html_text(cells[2])
        victim = _html_text(cells[6])
        if not killer or not victim:
            continue
        events.append(
            {
                "clock": clock,
                "seconds": int(match.group(1)) * 60 + int(match.group(2)),
                "killer": killer,
                "victim": victim,
            }
        )
    return events


def parse_golgg_inhibitor_timeline(page_html: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.IGNORECASE | re.DOTALL):
        if "inhib-icon" not in row_html:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if not cells:
            continue
        clock = _html_text(cells[0])
        match = re.fullmatch(r"(\d{1,3}):(\d{2})", clock)
        side_match = re.search(r"(?:blue|red)side-icon", row_html, re.IGNORECASE)
        if not match or not side_match:
            continue
        side = "blue" if side_match.group(0).lower().startswith("blue") else "red"
        events.append(
            {
                "clock": clock,
                "seconds": int(match.group(1)) * 60 + int(match.group(2)),
                "side": side,
                "row_text": _html_text(row_html),
            }
        )
    return events


def golgg_penta_negative_summary(
    events: list[dict[str, Any]], *, generous_window_seconds: int = 60
) -> dict[str, Any]:
    """Prove a negative only when no player has five kills even in a generous window.

    Sixty seconds is deliberately broader than normal LoL multi-kill timing.  A
    positive window is not treated as a penta; it only makes this resolver
    abstain because exact in-game multi-kill state is then required.
    """
    by_player: dict[str, list[int]] = {}
    for event in events:
        by_player.setdefault(str(event["killer"]), []).append(int(event["seconds"]))
    max_rapid = 0
    closest_player = None
    closest_span = None
    for player, values in by_player.items():
        times = sorted(values)
        left = 0
        for right, right_ts in enumerate(times):
            while right_ts - times[left] > generous_window_seconds:
                left += 1
            count = right - left + 1
            span = right_ts - times[left]
            if count > max_rapid or (count == max_rapid and (closest_span is None or span < closest_span)):
                max_rapid = count
                closest_player = player
                closest_span = span
    return {
        "kill_events": len(events),
        "player_kill_totals": {player: len(values) for player, values in sorted(by_player.items())},
        "generous_window_seconds": generous_window_seconds,
        "max_kills_in_window": max_rapid,
        "closest_player": closest_player,
        "closest_span_seconds": closest_span,
        "verified_no_penta": bool(events) and max_rapid < 5,
    }


def resolve_golgg_penta_negative(
    snapshot: dict[str, Any],
    *,
    game_id: int,
    get_text: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Resolve a Gol.gg penta market to No when a complete timeline proves it.

    Game discovery is intentionally outside this resolver.  The caller must
    supply the source game id after matching teams/date/game number; this keeps
    a fuzzy name match from silently becoming settlement truth.
    """
    rules_snapshot = snapshot.get("rules_snapshot", {})
    title = str(snapshot.get("title") or "")
    rules = "\n".join(str(rules_snapshot.get(key) or "") for key in ("description", "rules", "resolution_source"))
    if "gol.gg" not in rules.lower() or "penta kill" not in f"{title}\n{rules}".lower():
        return None
    game_number_match = re.search(r"\bgame\s*(\d+)\b", title, re.IGNORECASE)
    observed = now or datetime.now(timezone.utc)
    if not game_number_match:
        return _base_verdict("unverified", "Gol.gg penta market detected but game number was not parsed", observed_at_utc=observed.isoformat())
    game_number = int(game_number_match.group(1))
    game_url = GOLGG_GAME_URL.format(game_id=game_id)
    timeline_url = GOLGG_TIMELINE_URL.format(game_id=game_id)
    if get_text is None:
        def get_text(url: str) -> str:
            response = requests.get(url, timeout=15, headers={"User-Agent": "pm-agents-dispute-research/1.0"})
            response.raise_for_status()
            return response.text
    game_html = get_text(game_url)
    timeline_html = get_text(timeline_url)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", game_html, re.IGNORECASE | re.DOTALL)
    source_title = _html_text(title_match.group(1)) if title_match else ""
    if not re.search(rf"\bgame\s*{game_number}\b", source_title, re.IGNORECASE):
        return _base_verdict("unverified", "Gol.gg source game number did not match the market", observed_at_utc=observed.isoformat())
    kill_totals = [int(value) for value in re.findall(r"alt=['\"]Kills['\"][^>]*?/?>\s*(\d+)", game_html, re.IGNORECASE)]
    duration_match = re.search(r"Game Time\s*<br\s*/?>\s*<h1>\s*(\d{1,3}:\d{2})\s*</h1>", game_html, re.IGNORECASE)
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", game_html)
    events = parse_golgg_kill_timeline(timeline_html)
    summary = golgg_penta_negative_summary(events)
    if len(kill_totals) < 2 or sum(kill_totals[:2]) != len(events):
        return _base_verdict("unverified", "Gol.gg timeline kill count did not reconcile to the game summary", observed_at_utc=observed.isoformat())
    if not summary["verified_no_penta"]:
        return _base_verdict("unverified", "timeline contains a possible five-kill window; exact in-game penta state is required", observed_at_utc=observed.isoformat())
    outcomes = [str(value) for value in snapshot.get("outcomes") or []]
    winner = _outcome_value(outcomes, "No")
    if winner is None:
        return _base_verdict("unverified", "verified Gol.gg negative could not be mapped to outcome No", observed_at_utc=observed.isoformat())
    evidence = {
        "game_id": game_id,
        "source_title": source_title,
        "source_date": date_match.group(1) if date_match else None,
        "duration": duration_match.group(1) if duration_match else None,
        "team_kills": kill_totals[:2],
        **summary,
    }
    return {
        "schema_version": "dispute_rule_verdict_v1",
        "status": "verified",
        "winning_outcome": winner,
        "confidence": 0.995,
        "source_url": timeline_url,
        "observed_at_utc": observed.isoformat(),
        "resolver_id": "sports_golgg_penta_negative_v1",
        "reason": "complete Gol.gg kill timeline reconciles to the game total and no player has five kills even within a permissive 60-second window",
        "evidence": {"game_url": game_url, "timeline_url": timeline_url, **evidence},
        "evidence_sha256": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    }


def resolve_golgg_both_teams_inhibitors(
    snapshot: dict[str, Any],
    *,
    game_id: int,
    get_text: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Resolve whether both sides destroyed an inhibitor from a complete Gol.gg timeline."""
    rules_snapshot = snapshot.get("rules_snapshot", {})
    title = str(snapshot.get("title") or "")
    rules = "\n".join(str(rules_snapshot.get(key) or "") for key in ("description", "rules", "resolution_source"))
    text = f"{title}\n{rules}"
    if "gol.gg" not in rules.lower() or "both teams destroy" not in text.lower() or "inhibitor" not in text.lower():
        return None
    game_number_match = re.search(r"\bgame\s*(\d+)\b", title, re.IGNORECASE)
    observed = now or datetime.now(timezone.utc)
    if not game_number_match:
        return _base_verdict("unverified", "Gol.gg inhibitor market detected but game number was not parsed", observed_at_utc=observed.isoformat())
    game_number = int(game_number_match.group(1))
    game_url = GOLGG_GAME_URL.format(game_id=game_id)
    timeline_url = GOLGG_TIMELINE_URL.format(game_id=game_id)
    if get_text is None:
        def get_text(url: str) -> str:
            response = requests.get(url, timeout=15, headers={"User-Agent": "pm-agents-dispute-research/1.0"})
            response.raise_for_status()
            return response.text
    game_html = get_text(game_url)
    timeline_html = get_text(timeline_url)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", game_html, re.IGNORECASE | re.DOTALL)
    source_title = _html_text(title_match.group(1)) if title_match else ""
    duration_match = re.search(r"Game Time\s*<br\s*/?>\s*<h1>\s*(\d{1,3}:\d{2})\s*</h1>", game_html, re.IGNORECASE)
    if not re.search(rf"\bgame\s*{game_number}\b", source_title, re.IGNORECASE):
        return _base_verdict("unverified", "Gol.gg source game number did not match the market", observed_at_utc=observed.isoformat())
    if duration_match is None or "Game Time" not in timeline_html:
        return _base_verdict("unverified", "Gol.gg game/timeline did not expose a completed game duration", observed_at_utc=observed.isoformat())
    events = parse_golgg_inhibitor_timeline(timeline_html)
    sides = sorted({str(event["side"]) for event in events})
    winner = _outcome_value([str(value) for value in snapshot.get("outcomes") or []], "Yes" if len(sides) == 2 else "No")
    if winner is None:
        return _base_verdict("unverified", "Gol.gg inhibitor result could not be mapped to Yes/No", observed_at_utc=observed.isoformat())
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", game_html)
    evidence = {
        "game_id": game_id,
        "source_title": source_title,
        "source_date": date_match.group(1) if date_match else None,
        "duration": duration_match.group(1),
        "inhibitor_events": events,
        "destroying_sides": sides,
    }
    return {
        "schema_version": "dispute_rule_verdict_v1",
        "status": "verified",
        "winning_outcome": winner,
        "confidence": 0.995,
        "source_url": timeline_url,
        "observed_at_utc": observed.isoformat(),
        "resolver_id": "sports_golgg_both_teams_inhibitors_v1",
        "reason": f"complete Gol.gg Game {game_number} timeline records inhibitor destruction by {len(sides)} side(s)",
        "evidence": {"game_url": game_url, "timeline_url": timeline_url, **evidence},
        "evidence_sha256": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    }


def resolve_espn_total_corners(
    snapshot: dict[str, Any],
    *,
    event_id: int,
    league: str,
    get_json: Callable[[str, dict[str, Any]], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Resolve an O/U total-corners market from a completed ESPN match boxscore.

    ESPN is used only after any explicit designated-source waiting period.  The
    caller supplies a matched event id; fuzzy team/date discovery is kept out
    of the settlement resolver.
    """
    title = str(snapshot.get("title") or "")
    rules_snapshot = snapshot.get("rules_snapshot", {})
    rules = "\n".join(str(rules_snapshot.get(key) or "") for key in ("description", "rules", "resolution_source"))
    if "corner" not in f"{title}\n{rules}".lower() or not re.search(r"\bO/U\s*\d", title, re.IGNORECASE):
        return None
    line_match = re.search(r"\bO/U\s*(\d+(?:\.\d+)?)", title, re.IGNORECASE)
    observed = now or datetime.now(timezone.utc)
    if not line_match:
        return _base_verdict("unverified", "total-corners market detected but O/U line was not parsed", observed_at_utc=observed.isoformat())
    line = float(line_match.group(1))
    market_end = parse_market_ts(snapshot.get("market_status", {}).get("end_date"))
    wait_hours_match = re.search(r"within\s+(\d+)\s+hours?", rules, re.IGNORECASE)
    wait_hours = int(wait_hours_match.group(1)) if wait_hours_match else 0
    if market_end is not None and observed < market_end + timedelta(hours=wait_hours):
        return _base_verdict("pending_source_finality", "credible-reporting fallback window has not opened", observed_at_utc=observed.isoformat())
    endpoint = ESPN_SOCCER_SUMMARY_URL.format(league=league)
    params = {"event": str(event_id)}
    if get_json is None:
        def get_json(url: str, query: dict[str, Any]) -> Any:
            response = requests.get(url, params=query, timeout=15, headers={"User-Agent": "curl/8.0", "Accept": "application/json"})
            response.raise_for_status()
            return response.json()
    payload = get_json(endpoint, params)
    header = payload.get("header", {}) if isinstance(payload, dict) else {}
    competition = (header.get("competitions") or [{}])[0]
    completed = bool(competition.get("status", {}).get("type", {}).get("completed"))
    teams = payload.get("boxscore", {}).get("teams", []) if isinstance(payload, dict) else []
    if not completed or len(teams) != 2:
        return _base_verdict("unverified", "ESPN match was not complete or lacked two team boxscores", observed_at_utc=observed.isoformat())
    corners: list[dict[str, Any]] = []
    for team_row in teams:
        stat = next((item for item in team_row.get("statistics", []) if item.get("name") == "wonCorners"), None)
        try:
            value = int(stat["displayValue"])
        except (KeyError, TypeError, ValueError):
            return _base_verdict("unverified", "ESPN boxscore lacked integer wonCorners", observed_at_utc=observed.isoformat())
        corners.append(
            {
                "team": team_row.get("team", {}).get("displayName"),
                "home_away": team_row.get("homeAway"),
                "corners": value,
            }
        )
    total = sum(row["corners"] for row in corners)
    wanted = "Over" if total > line else "Under"
    winner = _outcome_value([str(value) for value in snapshot.get("outcomes") or []], wanted)
    if winner is None:
        return _base_verdict("unverified", "ESPN corner total could not be mapped to Over/Under", observed_at_utc=observed.isoformat())
    source_url = endpoint + "?" + urlencode(params)
    evidence = {
        "event_id": event_id,
        "league": league,
        "event_name": header.get("shortName"),
        "event_date": competition.get("date"),
        "completed": completed,
        "corner_line": line,
        "team_corners": corners,
        "total_corners": total,
        "fallback_wait_hours": wait_hours,
    }
    return {
        "schema_version": "dispute_rule_verdict_v1",
        "status": "verified",
        "winning_outcome": winner,
        "confidence": 0.99,
        "source_url": source_url,
        "observed_at_utc": observed.isoformat(),
        "resolver_id": "sports_espn_total_corners_v1",
        "reason": f"completed ESPN boxscore reports {total} total corners against an O/U {line:g} line after the fallback wait",
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    }


def parse_market_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _market_year(snapshot: dict[str, Any]) -> int:
    title = str(snapshot.get("title") or "")
    explicit = re.search(r"\b(20\d{2})\b", title)
    if explicit:
        return int(explicit.group(1))
    proposal_ts = int(snapshot.get("proposal", {}).get("proposal_ts") or 0)
    return datetime.fromtimestamp(proposal_ts, timezone.utc).year


def _specified_et_time(snapshot: dict[str, Any]) -> datetime | None:
    title = str(snapshot.get("title") or "")
    match = MONTH_TIME_RE.search(title)
    if not match:
        return None
    month = datetime.strptime(match.group(1)[:3].title(), "%b").month
    day = int(match.group(2))
    year = int(match.group(3) or _market_year(snapshot))
    hour = int(match.group(4)) % 12 + (12 if match.group(6).upper() == "PM" else 0)
    minute = int(match.group(5) or 0)
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def _binance_symbol(text: str) -> str | None:
    pair = re.search(r"\b(BTC|ETH|SOL|XRP|DOGE|BNB|HYPE)\s*/?\s*USDT\b", text, re.IGNORECASE)
    if pair:
        return pair.group(1).upper() + "USDT"
    lower = text.lower()
    return next((symbol for name, symbol in ASSET_SYMBOLS.items() if re.search(rf"\b{re.escape(name)}\b", lower)), None)


def _winning_outcome(title: str, rules: str, outcomes: list[str], *, open_price: float, close_price: float) -> str | None:
    text = f"{title}\n{rules}"
    if re.search(r"\bup or down\b", title, re.IGNORECASE):
        return _outcome_value(outcomes, "Up" if close_price >= open_price else "Down")
    above = re.search(r"\babove\s+\$?([\d,]+(?:\.\d+)?)", title, re.IGNORECASE)
    below = re.search(r"\bbelow\s+\$?([\d,]+(?:\.\d+)?)", title, re.IGNORECASE)
    if above:
        threshold = float(above.group(1).replace(",", ""))
        inclusive = bool(re.search(r"greater than or equal|at least", text, re.IGNORECASE))
        won = close_price >= threshold if inclusive else close_price > threshold
        return _outcome_value(outcomes, "Yes" if won else "No")
    if below:
        threshold = float(below.group(1).replace(",", ""))
        inclusive = bool(re.search(r"less than or equal|at most", text, re.IGNORECASE))
        won = close_price <= threshold if inclusive else close_price < threshold
        return _outcome_value(outcomes, "Yes" if won else "No")
    return None


def resolve_binance_timestamp(
    snapshot: dict[str, Any],
    *,
    get_json: Callable[[str, dict[str, Any]], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    rules_snapshot = snapshot.get("rules_snapshot", {})
    title = str(snapshot.get("title") or "")
    rules = "\n".join(
        str(rules_snapshot.get(key) or "")
        for key in ("description", "rules", "ancillary_text", "resolution_source")
    )
    if "binance.com" not in rules.lower() or "1 hour candle" not in rules.lower() and '"1h"' not in rules.lower():
        return None
    specified = _specified_et_time(snapshot)
    symbol = _binance_symbol(f"{title}\n{rules}")
    if specified is None or symbol is None:
        return _base_verdict(
            "unverified",
            "Binance market detected but title time or symbol was not parsed",
            observed_at_utc=(now or datetime.now(timezone.utc)).isoformat(),
        )
    candle_start = specified - timedelta(hours=1) if re.search(r"candle that ends", rules, re.IGNORECASE) else specified
    start_ms = int(candle_start.astimezone(timezone.utc).timestamp() * 1000)
    close_ms = start_ms + 3_600_000 - 1
    observed = now or datetime.now(timezone.utc)
    if int(observed.timestamp() * 1000) <= close_ms:
        return _base_verdict(
            "pending_source_finality",
            "specified Binance 1h candle has not finalized",
            observed_at_utc=observed.isoformat(),
        )
    endpoint = BINANCE_FUTURES_KLINES_URL if "/futures/" in rules.lower() else BINANCE_KLINES_URL
    market_kind = "futures" if endpoint == BINANCE_FUTURES_KLINES_URL else "spot"
    params = {"symbol": symbol, "interval": "1h", "startTime": start_ms, "limit": 1}
    source_url = endpoint + "?" + urlencode(params)
    if get_json is None:
        def get_json(url: str, query: dict[str, Any]) -> Any:
            response = requests.get(url, params=query, timeout=10)
            response.raise_for_status()
            return response.json()
    payload = get_json(endpoint, params)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list) or len(payload[0]) < 7:
        return _base_verdict("unverified", "Binance kline response was invalid", observed_at_utc=observed.isoformat())
    candle = payload[0]
    if int(candle[0]) != start_ms or int(candle[6]) != close_ms:
        return _base_verdict("unverified", "Binance returned a different candle boundary", observed_at_utc=observed.isoformat())
    open_price = float(candle[1])
    close_price = float(candle[4])
    outcomes = [str(value) for value in snapshot.get("outcomes") or []]
    winner = _winning_outcome(title, rules, outcomes, open_price=open_price, close_price=close_price)
    if winner is None:
        return _base_verdict("unverified", "market comparison could not be mapped to an outcome", observed_at_utc=observed.isoformat())
    evidence = {
        "symbol": symbol,
        "interval": "1h",
        "open_time_ms": start_ms,
        "close_time_ms": close_ms,
        "open": str(candle[1]),
        "close": str(candle[4]),
    }
    return {
        "schema_version": "dispute_rule_verdict_v1",
        "status": "verified",
        "winning_outcome": winner,
        "source_url": source_url,
        "observed_at_utc": observed.isoformat(),
        "resolver_id": f"binance_{market_kind}_1h_candle_v1",
        "reason": f"recomputed from the finalized Binance {market_kind} 1h candle named by the market rules",
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
    }


def resolve_rule_verdict(snapshot: dict[str, Any]) -> dict[str, Any]:
    current = snapshot.get("rule_verdict")
    if isinstance(current, dict) and current.get("status") == "prospective_too_early":
        return current
    resolved = resolve_binance_timestamp(snapshot)
    if resolved is not None:
        return resolved
    return current if isinstance(current, dict) else _base_verdict(
        "unverified",
        "no deterministic resolver supports the designated source and rule shape",
        observed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
