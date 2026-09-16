from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
DEFAULT_DB = "runtime/db/research.db"
OUT_DIR = Path("runtime/copy_trade")

SPORTS_RE = re.compile(
    r"\b(nba|nfl|mlb|nhl|epl|uefa|fifa|ufc|tennis|soccer|football|basketball|baseball|hockey|cricket|golf|fight|vs\.?|over [0-9]|under [0-9]|spread)\b",
    re.I,
)
ARBITRAGE_RE = re.compile(
    r"\b(spread|over [0-9]+(?:\.[0-9]+)?|under [0-9]+(?:\.[0-9]+)?|handicap|moneyline|parlay|points?|goals?|sets?|map [0-9]+|round [0-9]+)\b",
    re.I,
)
WEATHER_RE = re.compile(r"\btemperature|weather|rain|snow|hurricane|tornado|degrees|°f|highest-temperature\b", re.I)
ELECTION_RE = re.compile(
    r"\btrump|biden|president|election|senate|house|governor|mayor|primary|democrat|republican|gop|electoral college|popular vote\b",
    re.I,
)
GEOPOL_RE = re.compile(r"\bukraine|russia|iran|israel|gaza|china|taiwan|nato|war|ceasefire|missile|nuclear|tariff\b", re.I)
CRYPTO_RE = re.compile(r"\bbitcoin|btc|ethereum|eth|solana|sol|crypto|token|xrp|doge|stablecoin|defi\b", re.I)
TECH_RE = re.compile(r"\bai|openai|nvidia|tesla|apple|google|meta|microsoft|spacex|starship|ipo\b", re.I)
ECON_RE = re.compile(r"\bfed|rate|inflation|cpi|gdp|recession|unemployment|tariff|treasury\b", re.I)
CULTURE_RE = re.compile(r"\boscar|grammy|movie|album|song|celebrity|taylor|kendrick|rihanna|gta\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def norm_addr(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def build_session() -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    return session


def get_json(session: requests.Session, base: str, path: str, params: dict[str, Any], timeout: float = 25.0) -> Any:
    resp = session.get(
        base + path,
        params={k: v for k, v in params.items() if v is not None and v != ""},
        headers={"Accept": "application/json", "User-Agent": "pm-agent-copytrade-research/1.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def topic_for_text(text: str) -> str:
    if SPORTS_RE.search(text):
        return "sports"
    if WEATHER_RE.search(text):
        return "weather"
    if GEOPOL_RE.search(text):
        return "geopolitics"
    if ELECTION_RE.search(text):
        return "politics"
    if CRYPTO_RE.search(text):
        return "crypto"
    if ECON_RE.search(text):
        return "economics"
    if TECH_RE.search(text):
        return "tech"
    if CULTURE_RE.search(text):
        return "culture"
    return "other"


def title_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("title", "question", "slug", "eventSlug", "category"))


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS copy_trade_wallets (
          wallet_address TEXT PRIMARY KEY,
          display_name TEXT,
          status TEXT NOT NULL DEFAULT 'candidate',
          tags_json TEXT NOT NULL DEFAULT '[]',
          traits_json TEXT NOT NULL DEFAULT '{}',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          notes TEXT
        );

        CREATE TABLE IF NOT EXISTS copy_trade_discovery_runs (
          run_id TEXT PRIMARY KEY,
          run_type TEXT NOT NULL,
          run_name TEXT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          config_json TEXT,
          summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS copy_trade_wallet_discoveries (
          discovery_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          wallet_address TEXT NOT NULL,
          method TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          discovered_at TEXT NOT NULL,
          strength REAL NOT NULL DEFAULT 1.0,
          tags_json TEXT NOT NULL DEFAULT '[]',
          UNIQUE(run_id, wallet_address, method, source_ref)
        );

        CREATE TABLE IF NOT EXISTS copy_trade_wallet_reviews (
          review_id TEXT PRIMARY KEY,
          wallet_address TEXT NOT NULL,
          reviewed_at TEXT NOT NULL,
          score REAL NOT NULL,
          verdict TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          reasons_json TEXT NOT NULL DEFAULT '[]',
          summary TEXT
        );

        CREATE TABLE IF NOT EXISTS copy_trade_wallet_signals (
          signal_id TEXT PRIMARY KEY,
          wallet_address TEXT NOT NULL,
          detected_at TEXT NOT NULL,
          signal_type TEXT NOT NULL,
          condition_id TEXT,
          token_id TEXT,
          market_slug TEXT,
          title TEXT,
          outcome TEXT,
          delta_size REAL,
          delta_notional REAL,
          wallet_avg_price REAL,
          observed_price REAL,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS copy_trade_signal_research_reports (
          report_id TEXT PRIMARY KEY,
          signal_id TEXT NOT NULL,
          researched_at TEXT NOT NULL,
          decision TEXT NOT NULL,
          confidence REAL NOT NULL,
          max_entry_price REAL,
          suggested_notional REAL,
          address_entity_risk TEXT NOT NULL,
          report_json TEXT NOT NULL,
          summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_copy_trade_discoveries_wallet
          ON copy_trade_wallet_discoveries(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_copy_trade_discoveries_method
          ON copy_trade_wallet_discoveries(method, source_ref);
        CREATE INDEX IF NOT EXISTS idx_copy_trade_reviews_wallet_time
          ON copy_trade_wallet_reviews(wallet_address, reviewed_at);
        """
    )
    conn.commit()


def upsert_wallet(conn: sqlite3.Connection, wallet: str, display_name: str | None, tags: list[str], traits: dict[str, Any]) -> None:
    now = utc_now()
    existing = conn.execute(
        "SELECT tags_json, traits_json, display_name FROM copy_trade_wallets WHERE wallet_address = ?", (wallet,)
    ).fetchone()
    if existing:
        old_tags = set(json.loads(existing["tags_json"] or "[]"))
        old_traits = json.loads(existing["traits_json"] or "{}")
        old_traits.update({k: v for k, v in traits.items() if v is not None})
        merged_tags = sorted(old_tags | set(tags))
        conn.execute(
            """
            UPDATE copy_trade_wallets
            SET display_name = COALESCE(NULLIF(?, ''), display_name),
                tags_json = ?,
                traits_json = ?,
                last_seen_at = ?
            WHERE wallet_address = ?
            """,
            (display_name or "", json_dumps(merged_tags), json_dumps(old_traits), now, wallet),
        )
    else:
        conn.execute(
            """
            INSERT INTO copy_trade_wallets
              (wallet_address, display_name, status, tags_json, traits_json, first_seen_at, last_seen_at)
            VALUES (?, ?, 'candidate', ?, ?, ?, ?)
            """,
            (wallet, display_name, json_dumps(sorted(set(tags))), json_dumps(traits), now, now),
        )


def insert_discovery(
    conn: sqlite3.Connection,
    run_id: str,
    wallet: str,
    method: str,
    source_ref: str,
    evidence: dict[str, Any],
    strength: float,
    tags: list[str],
) -> None:
    discovery_id = stable_id(run_id, wallet, method, source_ref)
    conn.execute(
        """
        INSERT OR IGNORE INTO copy_trade_wallet_discoveries
          (discovery_id, run_id, wallet_address, method, source_ref, evidence_json, discovered_at, strength, tags_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (discovery_id, run_id, wallet, method, source_ref, json_dumps(evidence), utc_now(), strength, json_dumps(tags)),
    )


def discover(conn: sqlite3.Connection, max_leaderboard_offset: int, market_limit: int) -> dict[str, Any]:
    session = build_session()
    run_id = f"discover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    started_at = utc_now()
    config = {"max_leaderboard_offset": max_leaderboard_offset, "market_limit": market_limit}
    conn.execute(
        """
        INSERT INTO copy_trade_discovery_runs
          (run_id, run_type, run_name, started_at, status, config_json)
        VALUES (?, 'wallet_discovery', ?, ?, 'running', ?)
        """,
        (run_id, run_id, started_at, json_dumps(config)),
    )
    conn.commit()

    counts = Counter()
    categories = ["OVERALL", "POLITICS", "CRYPTO", "ECONOMICS", "TECH", "SPORTS"]
    periods = ["ALL", "MONTH", "WEEK"]

    for category in categories:
        for period in periods:
            for offset in range(0, max_leaderboard_offset + 1, 50):
                rows = get_json(
                    session,
                    DATA_API,
                    "/v1/leaderboard",
                    {"category": category, "timePeriod": period, "limit": 50, "offset": offset},
                )
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    wallet = norm_addr(row.get("proxyWallet"))
                    if not wallet:
                        continue
                    tags = [category.lower(), f"period_{period.lower()}", "leaderboard"]
                    if category == "SPORTS":
                        tags.append("sports_noise")
                    upsert_wallet(
                        conn,
                        wallet,
                        str(row.get("userName") or row.get("name") or ""),
                        tags,
                        {
                            "leaderboard_max_pnl": num(row.get("pnl")),
                            "leaderboard_max_vol": num(row.get("vol")),
                            "address_entity_warning": "single wallet only; related wallets not fully known",
                        },
                    )
                    insert_discovery(
                        conn,
                        run_id,
                        wallet,
                        "leaderboard",
                        f"leaderboard:{category}:{period}",
                        row,
                        strength=2.0 if category != "SPORTS" else 0.5,
                        tags=tags,
                    )
                    counts["leaderboard_wallet_rows"] += 1
                conn.commit()
                if len(rows) < 50:
                    break
                time.sleep(0.03)

    markets = get_json(
        session,
        GAMMA_API,
        "/markets",
        {"limit": market_limit, "closed": "false", "active": "true", "order": "volume24hr", "ascending": "false"},
    )
    markets = markets if isinstance(markets, list) else []
    for market in markets:
        if not isinstance(market, dict):
            continue
        condition_id = str(market.get("conditionId") or "")
        if not condition_id:
            continue
        topic = topic_for_text(title_text(market))
        if topic == "weather":
            continue
        market_tags = [topic, "active_market"]
        if topic == "sports":
            market_tags.append("sports_noise")
        source_ref = f"market:{condition_id}"
        try:
            holders_payload = get_json(session, DATA_API, "/holders", {"market": condition_id, "limit": 50})
        except Exception:
            holders_payload = []
        holder_rows: list[dict[str, Any]] = []
        if isinstance(holders_payload, list):
            for token_block in holders_payload:
                if isinstance(token_block, dict) and isinstance(token_block.get("holders"), list):
                    holder_rows.extend(token_block["holders"])
        for holder in holder_rows:
            wallet = norm_addr(holder.get("proxyWallet"))
            if not wallet:
                continue
            tags = market_tags + ["holder"]
            upsert_wallet(conn, wallet, str(holder.get("name") or holder.get("pseudonym") or ""), tags, {})
            evidence = {"market": market, "holder": holder}
            insert_discovery(conn, run_id, wallet, "active_market_holder", source_ref, evidence, 1.5, tags)
            counts["holder_wallet_rows"] += 1

        try:
            trades = get_json(session, DATA_API, "/trades", {"market": condition_id, "limit": 100, "offset": 0})
        except Exception:
            trades = []
        if isinstance(trades, list):
            for trade in trades:
                wallet = norm_addr(trade.get("proxyWallet"))
                if not wallet:
                    continue
                tags = market_tags + ["recent_trade"]
                upsert_wallet(conn, wallet, str(trade.get("name") or trade.get("pseudonym") or ""), tags, {})
                evidence = {"market": market, "trade": trade}
                insert_discovery(conn, run_id, wallet, "active_market_trade", source_ref, evidence, 1.0, tags)
                counts["trade_wallet_rows"] += 1
        counts["markets_scanned"] += 1
        conn.commit()
        time.sleep(0.05)

    unique_wallets = conn.execute("SELECT COUNT(*) AS n FROM copy_trade_wallets").fetchone()["n"]
    summary = {**counts, "unique_wallets_total": unique_wallets}
    conn.execute(
        "UPDATE copy_trade_discovery_runs SET finished_at = ?, status = 'completed', summary_json = ? WHERE run_id = ?",
        (utc_now(), json_dumps(summary), run_id),
    )
    conn.commit()
    return {"run_id": run_id, "summary": summary}


def iter_closed_positions(session: requests.Session, wallet: str, max_rows: int) -> list[dict[str, Any]]:
    rows_all: list[dict[str, Any]] = []
    for offset in range(0, max_rows, 50):
        rows = get_json(session, DATA_API, "/closed-positions", {"user": wallet, "limit": 50, "offset": offset}, timeout=30)
        if not isinstance(rows, list) or not rows:
            break
        rows_all.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 50:
            break
        time.sleep(0.04)
    return rows_all


def get_open_positions(session: requests.Session, wallet: str) -> list[dict[str, Any]]:
    rows = get_json(session, DATA_API, "/positions", {"user": wallet, "limit": 200, "offset": 0}, timeout=30)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def pnl_value(row: dict[str, Any]) -> float:
    for key in ("realizedPnl", "realizedPnlUsd", "pnl", "cashPnl"):
        if key in row:
            return num(row.get(key))
    return num(row.get("curPrice")) * num(row.get("size")) - num(row.get("avgPrice")) * num(row.get("size"))


def analyze_rows(
    wallet: str,
    closed_rows: list[dict[str, Any]],
    open_rows: list[dict[str, Any]],
    max_closed_rows: int,
) -> tuple[float, str, dict[str, Any], list[str], str]:
    topic_counts = Counter()
    topic_pnl = defaultdict(float)
    event_pnl = defaultdict(float)
    positive_event_pnl = defaultdict(float)
    wins = losses = 0
    win_pnl = loss_abs = 0.0
    election_pnl = sports_count = arbitrage_like_count = 0.0
    price_sum = price_count = 0.0
    low_price_count = mid_price_count = high_price_count = 0
    condition_ids: set[str] = set()
    total_pnl = 0.0
    for row in closed_rows:
        text = title_text(row)
        topic = topic_for_text(text)
        pnl = pnl_value(row)
        avg_price = num(row.get("avgPrice") or row.get("avg_price"))
        if avg_price > 0:
            price_sum += avg_price
            price_count += 1
            if avg_price < 0.15:
                low_price_count += 1
            elif avg_price > 0.85:
                high_price_count += 1
            else:
                mid_price_count += 1
        topic_counts[topic] += 1
        topic_pnl[topic] += pnl
        event_key = str(row.get("conditionId") or row.get("slug") or row.get("title") or "")[:180]
        condition_id = str(row.get("conditionId") or "").strip()
        if condition_id:
            condition_ids.add(condition_id)
        event_pnl[event_key] += abs(pnl)
        if pnl > 0:
            positive_event_pnl[event_key] += pnl
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            win_pnl += pnl
        elif pnl < 0:
            losses += 1
            loss_abs += abs(pnl)
        if ELECTION_RE.search(text):
            election_pnl += pnl
        if topic == "sports":
            sports_count += 1
        if topic == "sports" or ARBITRAGE_RE.search(text):
            arbitrage_like_count += 1

    count = len(closed_rows)
    history_truncated = count >= max_closed_rows
    profit_factor = win_pnl / loss_abs if loss_abs > 0 else (999.0 if win_pnl > 0 else 0.0)
    win_rate = wins / count if count else 0.0
    sports_ratio = sports_count / count if count else 0.0
    arbitrage_like_ratio = arbitrage_like_count / count if count else 0.0
    non_election_pnl = total_pnl - election_pnl
    single_event_dependency = max(event_pnl.values()) / sum(event_pnl.values()) if event_pnl else 0.0
    positive_single_event_dependency = (
        max(positive_event_pnl.values()) / sum(positive_event_pnl.values()) if positive_event_pnl else 0.0
    )
    election_dependency = abs(election_pnl) / abs(total_pnl) if total_pnl else 0.0
    avg_entry_price = price_sum / price_count if price_count else 0.0
    low_price_ratio = low_price_count / price_count if price_count else 0.0
    high_price_ratio = high_price_count / price_count if price_count else 0.0
    market_diversity_ratio = len(condition_ids) / count if count else 0.0
    open_value = sum(num(row.get("currentValue")) for row in open_rows)
    open_initial = sum(num(row.get("initialValue")) for row in open_rows)
    open_roi = (open_value - open_initial) / open_initial if open_initial > 0 else 0.0

    tags: list[str] = []
    if count < 30:
        tags.append("low_sample")
    if history_truncated:
        tags.append("history_truncated")
    if sports_ratio >= 0.5:
        tags.append("sports_noise")
    if arbitrage_like_ratio >= 0.35:
        tags.append("arbitrage_like")
    if non_election_pnl <= 0:
        tags.append("non_election_unproven")
    if single_event_dependency >= 0.7:
        tags.append("single_event_dependent")
    if election_dependency >= 0.5 or positive_single_event_dependency >= 0.5:
        tags.append("event_outlier_risk")
    if low_price_ratio >= 0.5:
        tags.append("lottery_like")
    if high_price_ratio >= 0.5:
        tags.append("favorite_grinder")
    if market_diversity_ratio >= 0.8 and count >= 300:
        tags.append("highly_fragmented")
    if open_rows:
        tags.append("active_recently")
    else:
        tags.append("inactive")
    for topic, _ in topic_counts.most_common(3):
        tags.append(topic)

    reasons: list[str] = []
    score = 0.0
    score += min(25, count / 4)
    score += min(25, math.log10(max(total_pnl, 0) + 1) * 3) if total_pnl > 0 else -20
    score += min(20, profit_factor * 4)
    score += min(15, non_election_pnl / 10000) if non_election_pnl > 0 else -15
    score += 10 if open_rows else -5
    score -= min(25, sports_ratio * 40)
    score -= min(25, single_event_dependency * 20)
    score -= min(25, arbitrage_like_ratio * 30)
    score -= min(20, positive_single_event_dependency * 20)
    score -= min(12, election_dependency * 12)
    if low_price_ratio >= 0.5 or high_price_ratio >= 0.6:
        score -= 8
    if market_diversity_ratio >= 0.85 and count >= 300:
        score -= 8

    if count < 30:
        reasons.append("closed_positions_count_lt_30")
    if history_truncated:
        reasons.append("history_truncated_needs_full_pagination")
    if total_pnl <= 0:
        reasons.append("net_pnl_non_positive")
    if non_election_pnl <= 0:
        reasons.append("non_election_pnl_non_positive")
    if profit_factor < 1.5:
        reasons.append("profit_factor_lt_1_5")
    if sports_ratio >= 0.5:
        reasons.append("sports_ratio_high")
    if arbitrage_like_ratio >= 0.35:
        reasons.append("arbitrage_or_sports_market_pattern")
    if single_event_dependency >= 0.7:
        reasons.append("single_event_dependency_high")
    if positive_single_event_dependency >= 0.5:
        reasons.append("positive_pnl_single_event_outlier")
    if election_dependency >= 0.5:
        reasons.append("election_dependency_high")
    if low_price_ratio >= 0.65:
        reasons.append("lottery_like_entry_distribution")
    if high_price_ratio >= 0.75:
        reasons.append("favorite_grinder_entry_distribution")
    if market_diversity_ratio >= 0.9 and count >= 500:
        reasons.append("too_fragmented_likely_systematic_or_arbitrage")

    blocking_reasons = [reason for reason in reasons if reason != "history_truncated_needs_full_pagination"]
    hard_reject_reasons = {
        "sports_ratio_high",
        "arbitrage_or_sports_market_pattern",
        "positive_pnl_single_event_outlier",
        "election_dependency_high",
        "non_election_pnl_non_positive",
        "net_pnl_non_positive",
        "too_fragmented_likely_systematic_or_arbitrage",
    }
    if hard_reject_reasons & set(reasons):
        verdict = "reject"
    elif not blocking_reasons and not history_truncated and score >= 45:
        verdict = "paper_candidate"
    elif total_pnl > 0 and non_election_pnl > 0 and profit_factor >= 1.2 and sports_ratio < 0.5:
        verdict = "watch"
    else:
        verdict = "reject"

    metrics = {
        "wallet_address": wallet,
        "closed_positions_count": count,
        "open_positions_count": len(open_rows),
        "net_pnl": round(total_pnl, 2),
        "non_election_pnl": round(non_election_pnl, 2),
        "election_pnl": round(election_pnl, 2),
        "profit_factor": round(profit_factor, 3),
        "win_rate": round(win_rate, 3),
        "sports_ratio": round(sports_ratio, 3),
        "arbitrage_like_ratio": round(arbitrage_like_ratio, 3),
        "single_event_dependency": round(single_event_dependency, 3),
        "positive_single_event_dependency": round(positive_single_event_dependency, 3),
        "election_dependency": round(election_dependency, 3),
        "avg_entry_price": round(avg_entry_price, 4),
        "low_price_ratio": round(low_price_ratio, 3),
        "high_price_ratio": round(high_price_ratio, 3),
        "market_diversity_ratio": round(market_diversity_ratio, 3),
        "open_unrealized_roi": round(open_roi, 3),
        "topic_counts": dict(topic_counts.most_common()),
        "topic_pnl": {key: round(value, 2) for key, value in sorted(topic_pnl.items(), key=lambda item: -abs(item[1]))},
        "address_entity_risk": "unknown",
        "history_truncated": history_truncated,
        "max_closed_rows": max_closed_rows,
    }
    summary = (
        f"{verdict}: {count} closed, pnl={total_pnl:.0f}, non_election={non_election_pnl:.0f}, "
        f"pf={profit_factor:.2f}, sports={sports_ratio:.0%}, single_event={single_event_dependency:.0%}"
    )
    return round(score, 2), verdict, metrics, reasons, summary


def shortlist_wallets(conn: sqlite3.Connection, limit: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT
          w.wallet_address,
          SUM(d.strength) AS strength_sum,
          COUNT(*) AS discovery_count,
          SUM(CASE WHEN d.tags_json LIKE '%sports_noise%' THEN 1 ELSE 0 END) AS sports_hits
        FROM copy_trade_wallets w
        JOIN copy_trade_wallet_discoveries d ON d.wallet_address = w.wallet_address
        WHERE w.status != 'rejected'
          AND NOT EXISTS (
            SELECT 1
            FROM copy_trade_wallet_reviews r
            WHERE r.wallet_address = w.wallet_address
          )
        GROUP BY w.wallet_address
        ORDER BY (strength_sum - sports_hits * 2.0) DESC, discovery_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row["wallet_address"] for row in rows]


def analyze(conn: sqlite3.Connection, limit: int, max_closed_rows: int) -> dict[str, Any]:
    session = build_session()
    wallets = shortlist_wallets(conn, limit)
    counts = Counter()
    reviewed: list[dict[str, Any]] = []
    for wallet in wallets:
        try:
            closed_rows = iter_closed_positions(session, wallet, max_closed_rows)
            open_rows = get_open_positions(session, wallet)
        except Exception as exc:
            reviewed.append({"wallet": wallet, "error": str(exc)})
            counts["errors"] += 1
            continue
        score, verdict, metrics, reasons, summary = analyze_rows(wallet, closed_rows, open_rows, max_closed_rows)
        review_id = stable_id(wallet, utc_now(), len(closed_rows), score)
        conn.execute(
            """
            INSERT INTO copy_trade_wallet_reviews
              (review_id, wallet_address, reviewed_at, score, verdict, metrics_json, reasons_json, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (review_id, wallet, utc_now(), score, verdict, json_dumps(metrics), json_dumps(reasons), summary),
        )
        conn.execute(
            """
            UPDATE copy_trade_wallets
            SET status = CASE
                  WHEN ? = 'paper_candidate' THEN 'paper'
                  WHEN ? = 'watch' THEN 'watch'
                  WHEN ? = 'reject' THEN 'rejected'
                  ELSE status
                END,
                tags_json = ?,
                traits_json = ?,
                last_seen_at = ?
            WHERE wallet_address = ?
            """,
            (
                verdict,
                verdict,
                verdict,
                json_dumps(sorted(set(metrics["topic_counts"].keys()) | set(["reviewed"]))),
                json_dumps(metrics),
                utc_now(),
                wallet,
            ),
        )
        conn.commit()
        reviewed.append({"wallet": wallet, "score": score, "verdict": verdict, "summary": summary})
        counts[verdict] += 1
        time.sleep(0.06)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {"generated_at": utc_now(), "counts": dict(counts), "reviews": reviewed}
    (OUT_DIR / "wallet_analysis_latest.json").write_text(json_dumps(output), encoding="utf-8")
    lines = ["# Copy Trade Wallet Analysis", "", f"generated_at: {output['generated_at']}", "", "## Counts", ""]
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Reviews", "", "| score | verdict | wallet | summary |", "|---:|---|---|---|"])
    for item in reviewed:
        lines.append(f"| {item.get('score', '')} | {item.get('verdict', 'error')} | {item['wallet']} | {item.get('summary', item.get('error', ''))} |")
    (OUT_DIR / "wallet_analysis_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def latest_reviews(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, w.display_name
        FROM copy_trade_wallet_reviews r
        LEFT JOIN copy_trade_wallets w ON w.wallet_address = r.wallet_address
        ORDER BY r.reviewed_at DESC
        """
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        wallet = row["wallet_address"]
        if wallet in latest:
            continue
        metrics = json.loads(row["metrics_json"] or "{}")
        latest[wallet] = {
            "wallet_address": wallet,
            "display_name": row["display_name"] or "",
            "score": row["score"],
            "verdict": row["verdict"],
            "reviewed_at": row["reviewed_at"],
            "metrics": metrics,
            "summary": row["summary"],
        }
    return list(latest.values())


def full_scan_targets(conn: sqlite3.Connection, limit: int, wallets: list[str]) -> list[str]:
    if wallets:
        return [norm_addr(wallet) for wallet in wallets if norm_addr(wallet)]

    candidates = []
    for row in latest_reviews(conn):
        metrics = row["metrics"]
        verdict = row["verdict"]
        if metrics.get("scan_mode") == "full_scan":
            continue
        if verdict not in {"watch", "paper_candidate"}:
            continue
        sports_ratio = float(metrics.get("sports_ratio") or 0)
        non_election_pnl = float(metrics.get("non_election_pnl") or 0)
        single_event = float(metrics.get("single_event_dependency") or 0)
        election_dependency = float(metrics.get("election_dependency") or 0)
        positive_event_dependency = float(metrics.get("positive_single_event_dependency") or single_event or 0)
        arbitrage_like_ratio = float(metrics.get("arbitrage_like_ratio") or sports_ratio or 0)
        market_diversity_ratio = float(metrics.get("market_diversity_ratio") or 0)
        low_price_ratio = float(metrics.get("low_price_ratio") or 0)
        high_price_ratio = float(metrics.get("high_price_ratio") or 0)
        closed_count = int(metrics.get("closed_positions_count") or 0)
        profit_factor = float(metrics.get("profit_factor") or 0)
        if (
            sports_ratio >= 0.35
            or arbitrage_like_ratio >= 0.35
            or non_election_pnl <= 0
            or single_event >= 0.7
            or election_dependency >= 0.5
            or positive_event_dependency >= 0.5
            or low_price_ratio >= 0.65
            or high_price_ratio >= 0.75
            or (market_diversity_ratio >= 0.9 and closed_count >= 500)
            or profit_factor < 1.5
        ):
            continue
        priority = 0.0
        priority += min(20.0, max(0.0, non_election_pnl) / 100000.0)
        priority += min(20.0, profit_factor * 3.0)
        priority += 10.0 if 30 <= closed_count <= 800 else 0.0
        priority += 8.0 if not metrics.get("history_truncated") else 0.0
        priority += 6.0 if verdict == "paper_candidate" else 0.0
        priority += 5.0 if 0.15 <= float(metrics.get("avg_entry_price") or 0) <= 0.75 else 0.0
        priority -= sports_ratio * 30.0
        priority -= single_event * 15.0
        priority -= election_dependency * 20.0
        priority -= market_diversity_ratio * 8.0
        candidates.append((priority, row["wallet_address"]))
    candidates.sort(reverse=True)
    return [wallet for _, wallet in candidates[:limit]]


def full_scan(conn: sqlite3.Connection, limit: int, max_closed_rows: int, wallets: list[str]) -> dict[str, Any]:
    session = build_session()
    targets = full_scan_targets(conn, limit, wallets)
    counts = Counter()
    reviewed: list[dict[str, Any]] = []
    for wallet in targets:
        try:
            closed_rows = iter_closed_positions(session, wallet, max_closed_rows)
            open_rows = get_open_positions(session, wallet)
        except Exception as exc:
            reviewed.append({"wallet": wallet, "error": str(exc)})
            counts["errors"] += 1
            continue
        score, verdict, metrics, reasons, summary = analyze_rows(wallet, closed_rows, open_rows, max_closed_rows)
        metrics["scan_mode"] = "full_scan"
        review_id = stable_id("full_scan", wallet, utc_now(), len(closed_rows), score)
        conn.execute(
            """
            INSERT INTO copy_trade_wallet_reviews
              (review_id, wallet_address, reviewed_at, score, verdict, metrics_json, reasons_json, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (review_id, wallet, utc_now(), score, verdict, json_dumps(metrics), json_dumps(reasons), summary),
        )
        conn.execute(
            """
            UPDATE copy_trade_wallets
            SET status = CASE
                  WHEN ? = 'paper_candidate' THEN 'paper'
                  WHEN ? = 'watch' THEN 'watch'
                  WHEN ? = 'reject' THEN 'rejected'
                  ELSE status
                END,
                tags_json = ?,
                traits_json = ?,
                last_seen_at = ?
            WHERE wallet_address = ?
            """,
            (
                verdict,
                verdict,
                verdict,
                json_dumps(sorted(set(metrics["topic_counts"].keys()) | set(["reviewed", "full_scan"]))),
                json_dumps(metrics),
                utc_now(),
                wallet,
            ),
        )
        conn.commit()
        reviewed.append(
            {
                "wallet": wallet,
                "score": score,
                "verdict": verdict,
                "summary": summary,
                "history_truncated": metrics.get("history_truncated"),
            }
        )
        counts[verdict] += 1
        time.sleep(0.08)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": utc_now(),
        "max_closed_rows": max_closed_rows,
        "counts": dict(counts),
        "reviews": reviewed,
    }
    (OUT_DIR / "wallet_full_scan_latest.json").write_text(json_dumps(output), encoding="utf-8")
    lines = [
        "# Copy Trade Wallet Full Scan",
        "",
        f"generated_at: {output['generated_at']}",
        f"max_closed_rows: {max_closed_rows}",
        "",
        "## Counts",
        "",
    ]
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Reviews", "", "| score | verdict | truncated | wallet | summary |", "|---:|---|---|---|---|"])
    for item in reviewed:
        lines.append(
            f"| {item.get('score', '')} | {item.get('verdict', 'error')} | {item.get('history_truncated', '')} | {item['wallet']} | {item.get('summary', item.get('error', ''))} |"
        )
    (OUT_DIR / "wallet_full_scan_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy-trade wallet discovery and analysis framework.")
    parser.add_argument("command", choices=["init-db", "discover", "analyze", "full-scan", "run"])
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--leaderboard-offset", type=int, default=200)
    parser.add_argument("--market-limit", type=int, default=30)
    parser.add_argument("--analyze-limit", type=int, default=40)
    parser.add_argument("--max-closed-rows", type=int, default=1000)
    parser.add_argument("--wallet", action="append", default=[], help="Wallet address to full-scan; can repeat.")
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)

    if args.command == "init-db":
        print(f"initialized {args.db}")
        return
    if args.command in {"discover", "run"}:
        result = discover(conn, args.leaderboard_offset, args.market_limit)
        print(json_dumps(result))
    if args.command in {"analyze", "run"}:
        result = analyze(conn, args.analyze_limit, args.max_closed_rows)
        print(json_dumps({"analysis_counts": result["counts"], "output": "runtime/copy_trade/wallet_analysis_latest.md"}))
    if args.command == "full-scan":
        result = full_scan(conn, args.analyze_limit, args.max_closed_rows, args.wallet)
        print(json_dumps({"full_scan_counts": result["counts"], "output": "runtime/copy_trade/wallet_full_scan_latest.md"}))


if __name__ == "__main__":
    main()
