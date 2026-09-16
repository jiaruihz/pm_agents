"""Shared contracts and PIT-safe feature extraction for dispute repricing.

This module contains no exchange writes.  It turns a dispute case into stable,
auditable issue features that can be reused by historical research and the
zero-notional forward collector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, log
import re
from typing import Any, Iterable
from urllib.parse import urlparse


URL_RE = re.compile(r"https?://[^\s,;)]+", re.IGNORECASE)
NON_EVIDENCE_DOMAINS = {
    "polygonscan.com",
    "docs.polymarket.com",
    "polymarket.com",
    "oracle.uma.xyz",
    "docs.uma.xyz",
}
NUMERIC_RE = re.compile(r"(?:\b\d+(?:\.\d+)?\b|[$€£]\s*\d)")
PRECISE_TIME_RE = re.compile(
    r"\b(?:\d{1,2}:\d{2}|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)|midnight|noon|"
    r"utc|gmt|et|est|edt|pt|pst|pdt|candle|close price)\b",
    re.IGNORECASE,
)
AMBIGUOUS_VERB_RE = re.compile(
    r"\b(?:say|said|mention|cry|cries|cried|insult|meet|talk|reconcile|control|"
    r"announce|wear|endorse|recognize|strike|attack|deploy|enter|release)\b",
    re.IGNORECASE,
)
STAT_RE = re.compile(
    r"\b(?:assists?|rebounds?|kills?|corners?|shots?|saves?|goals?|runs?|innings|"
    r"rounds?|points?|yards?|touchdowns?|aces?|double faults?)\b",
    re.IGNORECASE,
)
SPORTS_RE = re.compile(
    r"\b(?:vs\.?|match|game|spread|handicap|draw|o/u|over/under|mlb|nba|nfl|nhl|"
    r"wta|atp|f1|grand prix|league of legends|counter-strike|valorant|cricket|soccer|"
    r"football|tennis|baseball|basketball|esports?|halftime|half-time|leading at the half)\b",
    re.IGNORECASE,
)
SPORTS_SOURCE_DOMAINS = (
    "thefa.com",
    "hltv.org",
    "flashscore.com",
    "sofascore.com",
    "espn.com",
    "espncricinfo.com",
    "mlb.com",
    "nba.com",
    "nfl.com",
    "nhl.com",
    "atptour.com",
    "wtatennis.com",
    "uefa.com",
    "fifa.com",
    "vlr.gg",
    "liquipedia.net",
)
SPORTS_RULES_RE = re.compile(
    r"\b(?:official final (?:score|result)|final match statistics|first 90 minutes|"
    r"regular play plus stoppage time|map \d|set \d|governing body or event organizers)\b",
    re.IGNORECASE,
)
SPORTS_SLUG_DATE_RE = re.compile(r"^(.+?-\d{4}-\d{2}-\d{2})(?:-|$)", re.IGNORECASE)
CRYPTO_RE = re.compile(
    r"\b(?:bitcoin|ethereum|solana|xrp|dogecoin|crypto|btc|eth|usdt|binance|coinbase)\b",
    re.IGNORECASE,
)
MACRO_RE = re.compile(
    r"\b(?:gdp|cpi|unemployment|payrolls?|interest rate|fed|fomc|inflation|economic release)\b",
    re.IGNORECASE,
)
SPEECH_RE = re.compile(
    r"\b(?:say|mention|speech|press conference|briefing|transcript|tweet|post on x|"
    r"tweets?|posts?|posted|announcers?|youtube views?)\b",
    re.IGNORECASE,
)
POLITICS_EVENT_RE = re.compile(
    r"\b(?:strike|attack|deploy|meet|talk|reconcile|announce|recognize|sanction|"
    r"ceasefire|invade|enter|arrest|resign|shutdown)\b",
    re.IGNORECASE,
)
PROCEDURE_RE = re.compile(
    r"\b(?:election|vote share|certif|inaugurat|nominate|confirm|most seats|turnout|"
    r"official result|court ruling|legislation|bill passes)\b",
    re.IGNORECASE,
)
EXACT_RE = re.compile(r"\b(?:exactly|between|range|odd or even|margin|score|\d+[-–]\d+)\b", re.IGNORECASE)
THRESHOLD_RE = re.compile(r"(?:>=|<=|>|<|at least|more than|less than|over|under|\+)", re.IGNORECASE)
TOUCH_RE = re.compile(r"\b(?:hit|touch|dip to|reach|trade at)\b", re.IGNORECASE)
MODEL_CATEGORICAL_FIELDS = (
    "track",
    "mechanism",
    "source_class",
    "outcome_structure",
    "market_theme_v1",
    "proposed_side",
    "top_source_domain",
)
MODEL_NUMERIC_FIELDS = (
    "log_dispute_latency",
    "has_designated_source",
    "has_credible_reporting",
    "has_precise_time",
    "has_numeric_condition",
    "has_ambiguity_marker",
)


@dataclass(frozen=True)
class DisputeIssueFeatures:
    track: str
    mechanism: str
    source_class: str
    outcome_structure: str
    has_designated_source: bool
    has_credible_reporting: bool
    has_precise_time: bool
    has_numeric_condition: bool
    has_ambiguity_marker: bool
    source_domains: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["source_domains"] = list(self.source_domains)
        return row


def _case_text(row: dict[str, Any]) -> tuple[str, str, str]:
    title = str(row.get("title") or "")
    rules = str(row.get("ancillary_text") or row.get("rules") or "")
    source = str(row.get("resolution_source") or "")
    return title, rules, " ".join((title, rules, source))


def extract_source_domains(row: dict[str, Any]) -> tuple[str, ...]:
    _, _, text = _case_text(row)
    domains: set[str] = set()
    for url in URL_RE.findall(text):
        host = (urlparse(url.rstrip(".\"'")).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in NON_EVIDENCE_DOMAINS:
            domains.add(host)
    return tuple(sorted(domains))


def classify_dispute_case(row: dict[str, Any]) -> DisputeIssueFeatures:
    title, rules, text = _case_text(row)
    lower = text.lower()
    theme = str(row.get("market_theme_v1") or "")
    domains = extract_source_domains(row)
    has_credible = "credible reporting" in lower or "preponderance" in lower
    has_designated = bool(domains) or "resolution source" in lower
    has_precise_time = bool(PRECISE_TIME_RE.search(text))
    has_numeric = bool(NUMERIC_RE.search(title) or THRESHOLD_RE.search(title) or EXACT_RE.search(title))
    has_ambiguity = bool(AMBIGUOUS_VERB_RE.search(title)) or has_credible
    is_sports = (
        bool(SPORTS_RE.search(title))
        or theme == "sports_esports"
        or any(domain == marker or domain.endswith("." + marker) for domain in domains for marker in SPORTS_SOURCE_DOMAINS)
        or bool(SPORTS_RULES_RE.search(rules))
    )

    if SPEECH_RE.search(title):
        mechanism = "speech_or_social_count"
        track = "semantic" if has_ambiguity else "mixed"
    elif STAT_RE.search(title) and is_sports:
        mechanism = "sports_stat_scope"
        track = "mechanical"
    elif is_sports:
        mechanism = "sports_result_scope"
        track = "mechanical"
    elif CRYPTO_RE.search(text) and has_precise_time:
        mechanism = "financial_timestamp_price"
        track = "mechanical"
    elif CRYPTO_RE.search(title) or theme == "crypto_financial_markets":
        mechanism = "financial_touch_range_source"
        track = "mechanical" if has_designated else "mixed"
    elif MACRO_RE.search(text):
        mechanism = "official_macro_release"
        track = "mechanical" if has_designated else "mixed"
    elif PROCEDURE_RE.search(title):
        mechanism = "political_procedure"
        track = "mechanical" if has_designated and not has_credible else "mixed"
    elif POLITICS_EVENT_RE.search(title):
        mechanism = "event_occurrence_boundary"
        track = "semantic"
    elif theme == "weather_natural":
        mechanism = "measurement_source_finality"
        track = "mechanical"
    elif theme == "entertainment_awards_media":
        mechanism = "culture_definition_or_award"
        track = "semantic" if has_ambiguity or has_credible else "mixed"
    elif has_credible or has_ambiguity:
        mechanism = "general_semantic_boundary"
        track = "semantic"
    elif has_designated:
        mechanism = "general_designated_source"
        track = "mechanical"
    else:
        mechanism = "insufficiently_structured"
        track = "mixed"

    if has_credible:
        source_class = "credible_reporting"
    elif domains:
        source_class = "designated_web_source"
    elif "official" in lower:
        source_class = "official_unspecified"
    else:
        source_class = "source_unspecified"

    if TOUCH_RE.search(title):
        outcome_structure = "touch"
    elif EXACT_RE.search(title):
        outcome_structure = "exact_or_range"
    elif THRESHOLD_RE.search(title):
        outcome_structure = "threshold"
    else:
        outcome_structure = "binary_occurrence"

    return DisputeIssueFeatures(
        track=track,
        mechanism=mechanism,
        source_class=source_class,
        outcome_structure=outcome_structure,
        has_designated_source=has_designated,
        has_credible_reporting=has_credible,
        has_precise_time=has_precise_time,
        has_numeric_condition=has_numeric,
        has_ambiguity_marker=has_ambiguity,
        source_domains=domains,
    )


def opportunity_cluster_id(row: dict[str, Any]) -> str:
    """Return a conservative risk cluster for sibling markets on one event.

    Gamma sports slugs use a stable ``league-away-home-YYYY-MM-DD`` prefix and
    append the individual market expression.  Collapsing only that documented
    shape avoids pretending a spread, total and moneyline are independent bets.
    Other themes remain market-scoped until a similarly stable event identifier
    is captured.
    """
    market_id = str(row.get("market_id") or row.get("id") or "unknown")
    slug = str(row.get("slug") or row.get("market_slug") or "").strip().lower()
    theme = str(row.get("market_theme_v1") or row.get("topic") or "")
    if theme in {"sports_esports", "sports"} and slug:
        match = SPORTS_SLUG_DATE_RE.match(slug)
        if match:
            return "sports_event:" + match.group(1)
    return "market:" + market_id


def first_clear_non_p4_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        row for row in rows
        if row.get("final_binary") in (0, 1) and row.get("proposed_binary") in (0, 1)
    ]
    first: dict[str, dict[str, Any]] = {}
    for row in sorted(eligible, key=lambda item: (int(item["dispute_ts"]), str(item["signal_id"]))):
        first.setdefault(str(row["market_id"]), row)
    return [
        row for row in first.values()
        if row.get("request_settlement_class") in {"binary_flip", "upheld"}
    ]


def executable_vwap(levels: Iterable[dict[str, Any]], quantity: float) -> float | None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    asks: list[tuple[float, float]] = []
    for level in levels:
        if str(level.get("side") or "").lower() != "ask":
            continue
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if isfinite(price) and isfinite(size) and price >= 0 and size > 0:
            asks.append((price, size))
    asks.sort()
    remaining = quantity
    cost = 0.0
    for price, size in asks:
        taken = min(remaining, size)
        cost += taken * price
        remaining -= taken
        if remaining <= 1e-12:
            return cost / quantity
    return None


def executable_bid_vwap(levels: Iterable[dict[str, Any]], quantity: float) -> float | None:
    """Average executable sale price for ``quantity`` shares against bids."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    bids: list[tuple[float, float]] = []
    for level in levels:
        if str(level.get("side") or "").lower() != "bid":
            continue
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if isfinite(price) and isfinite(size) and price >= 0 and size > 0:
            bids.append((price, size))
    bids.sort(reverse=True)
    remaining = quantity
    proceeds = 0.0
    for price, size in bids:
        taken = min(remaining, size)
        proceeds += taken * price
        remaining -= taken
        if remaining <= 1e-12:
            return proceeds / quantity
    return None


def modeled_taker_fee_per_share(price: float, fee_rate: float) -> float:
    if not (0 <= price <= 1):
        raise ValueError("price must be between 0 and 1")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")
    return fee_rate * price * (1 - price)


def build_model_feature_row(row: dict[str, Any], *, market_price: float | None) -> dict[str, Any]:
    result = {name: row[name] for name in MODEL_CATEGORICAL_FIELDS + MODEL_NUMERIC_FIELDS}
    if market_price is not None:
        price = min(max(float(market_price), 1e-5), 1 - 1e-5)
        result["market_logit"] = log(price / (1 - price))
    return result


def model_theme_for_issue(issue: dict[str, Any], title: str = "") -> str:
    mechanism = str(issue.get("mechanism") or "")
    if mechanism.startswith("sports_"):
        return "sports_esports"
    if mechanism.startswith("financial_"):
        return "crypto_financial_markets"
    if mechanism == "official_macro_release":
        return "business_macro_tech"
    if mechanism == "speech_or_social_count":
        return "speech_social_media"
    if mechanism in {"political_procedure", "event_occurrence_boundary"}:
        return "politics_geopolitics"
    if mechanism == "measurement_source_finality" and re.search(
        r"temperature|weather|earthquake|hurricane|rainfall|snowfall", title, re.IGNORECASE
    ):
        return "weather_natural"
    if mechanism == "culture_definition_or_award":
        return "entertainment_awards_media"
    return "other"
