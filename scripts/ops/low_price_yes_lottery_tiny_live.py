#!/usr/bin/env python3
"""Low-price YES lottery tiny-live head.

This is an independent live/shadow head for the refined low-price YES lottery
selector. It reads canonical fact_signal_candidates, rechecks the live CLOB
book, writes shadow telemetry for every candidate, and hands accepted guarded
taker plans to weather_order_executor.py.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.weather_edge_v1.tools.weather_edge_market_data import weather_event_slug
from weather_data_feed.source_policy import city_slug

DB_DEFAULT = ROOT / "runtime/weather.db"
SNAPSHOT_DIR_DEFAULT = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
RUNTIME_DIR = ROOT / os.environ.get(
    "LOW_PRICE_YES_LOTTERY_RUNTIME_DIR",
    "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1",
)
LIVE_DIR = ROOT / "runtime/weather_edge_v1/live"

SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
SHADOW_OUT = RUNTIME_DIR / "shadow_decisions.jsonl"
BLOCKED_OUT = RUNTIME_DIR / "blocked_candidates.jsonl"
LATEST_CANDIDATES_OUT = RUNTIME_DIR / "latest_candidates.json"
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
TOKEN_CACHE_OUT = RUNTIME_DIR / "token_cache.json"
LIVE_OUT = LIVE_DIR / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"

STRATEGY_INSTANCE = "low_price_yes_lottery_tiny_live_v1"
STRATEGY_ID = "low_price_yes_lottery_tiny_live_v1"
STRATEGY_FAMILY = "forecast_quality.low_price_yes_lottery"
RULE_ID = "buy_yes_edge20_ask05_20_fixed150_guarded_taker_v1"
SOURCE_REPORT = "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md"

CLOB_BASE_URL = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"


def market_proxy_url() -> str:
    proxy = os.getenv("LOW_PRICE_YES_LOTTERY_MARKET_PROXY", "").strip()
    if proxy.lower() in {"", "direct", "none", "off", "0"}:
        return ""
    return proxy


def now_utc_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> str:
    return now_utc_dt().isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def parse_utc(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HardTimeoutError(TimeoutError):
    pass


@contextlib.contextmanager
def hard_timeout(seconds: float, label: str):
    if seconds <= 0:
        yield
        return
    old_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum, _frame):
        raise HardTimeoutError(f"{label} exceeded {seconds:.1f}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def load_token_cache(path: Path = TOKEN_CACHE_OUT) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_token_cache(cache: dict[str, Any], path: Path = TOKEN_CACHE_OUT) -> None:
    write_json(path, cache)


def signal_id_for_row(row: dict[str, Any]) -> str:
    return stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "rule_id": RULE_ID,
            "city": safe_str(row.get("city")),
            "target_date": safe_str(row.get("event_date")),
            "bracket": safe_str(row.get("bracket")),
            "condition_id": safe_str(row.get("condition_id")),
        }
    )


def existing_submitted_signal_ids(path: Path = LIVE_OUT) -> set[str]:
    out: set[str] = set()
    for row in read_jsonl(path):
        if safe_str(row.get("record_type")) != "weather_edge_live_order":
            continue
        if safe_str(row.get("status")) != "submitted":
            continue
        signal_id = safe_str(row.get("signal_id"))
        if signal_id:
            out.add(signal_id)
    return out


def effective_min_event_date(conn: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if args.min_event_date:
        return safe_str(args.min_event_date)
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    row = conn.execute(
        f"""
        SELECT MAX(event_date)
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          {status_filter}
        """,
        {"min_ask": args.min_ask, "max_ask": args.max_ask, "min_edge": args.min_edge},
    ).fetchone()
    return None if row is None else row[0]


def count_raw(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> dict[str, Any]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    row = conn.execute(
        f"""
        SELECT
          COUNT(*) AS rows,
          COUNT(DISTINCT event_date) AS dates,
          COUNT(DISTINCT city) AS cities,
          MIN(event_date) AS min_event_date,
          MAX(event_date) AS max_event_date,
          AVG(decision_entry_price) AS avg_ask,
          AVG(edge) AS avg_edge,
          MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          AND (:min_event_date IS NULL OR event_date >= :min_event_date)
          {max_filter}
          {status_filter}
        """,
        {
            "min_ask": args.min_ask,
            "max_ask": args.max_ask,
            "min_edge": args.min_edge,
            "min_event_date": min_event_date,
            "max_event_date": args.max_event_date,
        },
    ).fetchone()
    return dict(row) if row is not None else {}


def load_candidates(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> list[dict[str, Any]]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    rows = conn.execute(
        f"""
        WITH base AS (
          SELECT
            candidate_id,
            condition_id,
            market_id,
            side,
            event_date,
            bracket,
            city,
            city_pool,
            icao,
            unit,
            forecast_source,
            forecast_max_f,
            forecast_max_native,
            forecast_peak_hour_local,
            forecast_peak_time_local,
            forecast_peak_hour_utc,
            forecast_peak_time_utc,
            forecast_hourly_count,
            forecast_peak_source,
            forecast_timezone,
            forecast_peak_delta_hours_local,
            forecast_max_in_bracket,
            forecast_max_above_bracket_f,
            forecast_max_below_bracket_f,
            model_version,
            time_bucket,
            window,
            decision_window_label,
            decision_hours_to_settle,
            decision_snapshot_ts_utc,
            model_p_yes,
            market_yes_price,
            edge,
            abs_edge,
            decision_entry_price,
            yes_spread,
            no_spread,
            yes_depth_ask_5c,
            no_depth_ask_5c,
            first_seen_ts_utc,
            last_seen_ts_utc,
            n_snapshots,
            edge_max,
            edge_mean,
            best_entry_price,
            settlement_status,
            final_yes,
            bracket_hit,
            fact_built_at_utc,
            ROW_NUMBER() OVER (
              PARTITION BY event_date, city
              ORDER BY decision_snapshot_ts_utc ASC, decision_entry_price ASC, edge DESC, bracket ASC, candidate_id ASC
            ) AS city_date_rank
          FROM fact_signal_candidates
          WHERE side = 'BUY_YES'
            AND decision_entry_price BETWEEN :min_ask AND :max_ask
            AND edge >= :min_edge
            AND (:min_event_date IS NULL OR event_date >= :min_event_date)
            {max_filter}
            {status_filter}
        )
        SELECT *
        FROM base
        WHERE city_date_rank = 1
        ORDER BY event_date ASC, decision_snapshot_ts_utc ASC, city ASC
        LIMIT :limit
        """,
        {
            "min_ask": args.min_ask,
            "max_ask": args.max_ask,
            "min_edge": args.min_edge,
            "min_event_date": min_event_date,
            "max_event_date": args.max_event_date,
            "limit": args.max_candidates_per_run,
        },
    ).fetchall()
    return [dict(row) for row in rows]


def _field_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [safe_str(x) for x in value if safe_str(x)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return [value] if value else []
        if isinstance(parsed, list):
            return [safe_str(x) for x in parsed if safe_str(x)]
    return []


def _event_date(value: Any):
    text = safe_str(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _yes_token_from_market(market: dict[str, Any]) -> str:
    outcomes = _field_list(market.get("outcomes"))
    token_ids = _field_list(market.get("clobTokenIds"))
    for outcome, token_id in zip(outcomes, token_ids):
        if outcome.lower() == "yes":
            return token_id
    return token_ids[0] if token_ids else ""


def _token_payload_from_market(
    *,
    market: dict[str, Any],
    event: dict[str, Any],
    condition_id: str,
    source: str,
) -> dict[str, Any]:
    outcomes = _field_list(market.get("outcomes"))
    token_ids = _field_list(market.get("clobTokenIds"))
    return {
        "condition_id": safe_str(market.get("conditionId")) or condition_id,
        "gamma_market_id": safe_str(market.get("id") or market.get("marketId") or market.get("market_id")),
        "market_slug": safe_str(market.get("slug")),
        "event_id": safe_str(event.get("id") or event.get("eventId") or event.get("event_id") or market.get("eventId")),
        "event_title": safe_str(event.get("title")),
        "question": safe_str(market.get("question")),
        "outcomes": outcomes,
        "token_ids": token_ids,
        "yes_token_id": _yes_token_from_market(market),
        "active": bool(market.get("active", event.get("active", True))),
        "closed": bool(market.get("closed", event.get("closed", False))),
        "end_date": safe_str(market.get("endDate") or event.get("endDate")),
        "resolved_at_utc": now_utc(),
        "source": source,
    }


def resolve_yes_token_from_event(row: dict[str, Any]) -> dict[str, Any]:
    target_date = _event_date(row.get("event_date"))
    city = safe_str(row.get("city"))
    condition_id = safe_str(row.get("condition_id"))
    market_id = safe_str(row.get("market_id"))
    if target_date is None or not city:
        return {"condition_id": condition_id, "yes_token_id": "", "source": "event_fallback_missing_city_date"}
    slug = weather_event_slug(city_slug(city), target_date)
    event = PolymarketGammaClient().fetch_event_by_id_or_slug(slug=slug)
    if not isinstance(event, dict) or not event:
        return {"condition_id": condition_id, "yes_token_id": "", "source": "event_fallback_not_found", "event_slug": slug}
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_condition_id = safe_str(market.get("conditionId"))
        gamma_market_id = safe_str(market.get("id") or market.get("marketId") or market.get("market_id"))
        if condition_id and market_condition_id == condition_id:
            return {
                **_token_payload_from_market(
                    market=market,
                    event=event,
                    condition_id=condition_id,
                    source="gamma_event_condition_match",
                ),
                "event_slug": slug,
            }
        if market_id and gamma_market_id == market_id:
            return {
                **_token_payload_from_market(
                    market=market,
                    event=event,
                    condition_id=condition_id,
                    source="gamma_event_market_id_match",
                ),
                "event_slug": slug,
            }
    return {"condition_id": condition_id, "yes_token_id": "", "source": "event_fallback_market_not_found", "event_slug": slug}


def resolve_yes_token(row: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    condition_id = safe_str(row.get("condition_id"))
    cached = cache.get(condition_id) if condition_id else None
    if isinstance(cached, dict) and cached.get("yes_token_id"):
        fallback = dict(cached)
    else:
        fallback = {}
    errors: list[str] = []
    targets = [condition_id, safe_str(row.get("market_id"))]
    resolved = None
    for target in [x for x in dict.fromkeys(targets) if x]:
        try:
            resolved = resolve_market(target)
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{target}: {type(exc).__name__}: {exc}")
    if resolved is not None:
        outcomes = list(resolved.outcomes or [])
        token_ids = list(resolved.token_ids or [])
        yes_token_id = ""
        for outcome, token_id in zip(outcomes, token_ids):
            if safe_str(outcome).lower() == "yes":
                yes_token_id = safe_str(token_id)
                break
        if not yes_token_id and token_ids:
            yes_token_id = safe_str(token_ids[0])
        payload = {
            "condition_id": safe_str(resolved.condition_id) or condition_id,
            "gamma_market_id": safe_str(resolved.market_id),
            "market_slug": safe_str(resolved.slug),
            "event_id": safe_str(resolved.event_id),
            "event_title": safe_str(resolved.event_title),
            "question": safe_str(resolved.question),
            "outcomes": outcomes,
            "token_ids": token_ids,
            "yes_token_id": yes_token_id,
            "active": bool(resolved.active),
            "closed": bool(resolved.closed),
            "end_date": safe_str(resolved.end_date),
            "resolved_at_utc": now_utc(),
            "source": "gamma_live",
        }
        if yes_token_id:
            cache[condition_id] = payload
            save_token_cache(cache)
        return payload
    event_payload = resolve_yes_token_from_event(row)
    if event_payload.get("yes_token_id"):
        cache[condition_id] = event_payload
        save_token_cache(cache)
        return {**event_payload, "direct_gamma_errors": errors}
    if fallback:
        return {
            **fallback,
            "active": fallback.get("active"),
            "closed": fallback.get("closed"),
            "source": "token_cache_gamma_failed",
            "gamma_error": "; ".join(errors),
        }
    return {
        "condition_id": condition_id,
        "yes_token_id": "",
        "source": "gamma_failed",
        "gamma_error": "; ".join(errors),
    }


def cached_yes_token_only(row: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    condition_id = safe_str(row.get("condition_id"))
    cached = cache.get(condition_id) if condition_id else None
    if isinstance(cached, dict) and cached.get("yes_token_id"):
        return {**cached, "source": "token_cache_only"}
    snapshot_payload = resolve_yes_token_from_local_snapshots(row)
    if snapshot_payload.get("yes_token_id"):
        cache[condition_id] = snapshot_payload
        save_token_cache(cache)
        return snapshot_payload
    return {
        "condition_id": condition_id,
        "yes_token_id": "",
        "source": "live_token_resolution_disabled",
    }


def resolve_yes_token_from_local_snapshots(row: dict[str, Any]) -> dict[str, Any]:
    condition_id = safe_str(row.get("condition_id"))
    event_date = safe_str(row.get("event_date"))
    if not condition_id or not event_date:
        return {"condition_id": condition_id, "yes_token_id": "", "source": "local_snapshot_missing_key"}
    ymd = event_date.replace("-", "")
    for path in sorted(SNAPSHOT_DIR_DEFAULT.glob(f"snapshot_{ymd}_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if safe_str(rec.get("condition_id") or rec.get("conditionId")) != condition_id:
                continue
            yes_token_id = safe_str(rec.get("yes_token_id"))
            if not yes_token_id:
                token_ids = _field_list(rec.get("clobTokenIds"))
                outcomes = _field_list(rec.get("outcomes"))
                for outcome, token_id in zip(outcomes, token_ids):
                    if outcome.lower() == "yes":
                        yes_token_id = token_id
                        break
                if not yes_token_id and token_ids:
                    yes_token_id = token_ids[0]
            if not yes_token_id:
                continue
            return {
                "condition_id": condition_id,
                "gamma_market_id": safe_str(rec.get("market_id") or rec.get("marketId")),
                "market_slug": safe_str(rec.get("market_slug") or rec.get("slug")),
                "event_id": safe_str(rec.get("event_id") or rec.get("eventId")),
                "event_title": safe_str(rec.get("event_title") or rec.get("title")),
                "question": safe_str(rec.get("question")),
                "outcomes": _field_list(rec.get("outcomes")) or ["Yes", "No"],
                "token_ids": [yes_token_id, safe_str(rec.get("no_token_id"))],
                "yes_token_id": yes_token_id,
                "no_token_id": safe_str(rec.get("no_token_id")),
                "active": rec.get("active", True),
                "closed": rec.get("closed", False),
                "end_date": safe_str(rec.get("end_date") or rec.get("endDate")),
                "resolved_at_utc": now_utc(),
                "source": "local_paper_snapshot",
                "source_snapshot_file": rel(path),
            }
    return {
        "condition_id": condition_id,
        "yes_token_id": "",
        "source": "local_snapshot_not_found",
    }


def fetch_book(token_id: str, *, timeout_sec: float = 5.0) -> dict[str, Any]:
    url = f"{CLOB_BASE_URL.rstrip('/')}/book"
    proxy = market_proxy_url()
    with httpx.Client(proxy=proxy or None, timeout=timeout_sec, trust_env=False) as client:
        response = client.get(
            url,
            params={"token_id": token_id},
            headers={"Accept": "application/json"},
        )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def book_levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    rows = book.get(f"{side}s") if isinstance(book, dict) else []
    out: list[tuple[float, float]] = []
    for item in rows or []:
        if isinstance(item, dict):
            price = item.get("price") or item.get("p")
            size = item.get("size") or item.get("q") or item.get("quantity")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price, size = item[0], item[1]
        else:
            continue
        price_f = to_float(price, 0.0)
        size_f = to_float(size, 0.0)
        if price_f > 0 and size_f > 0:
            out.append((price_f, size_f))
    return sorted(out, key=lambda x: x[0], reverse=(side == "bid"))


def fee_metrics(*, price: float, shares: float, taker_fee_rate: float, maker_rebate_rate: float) -> dict[str, Any]:
    notional = price * shares
    taker_fee_per_share = taker_fee_rate * price * (1.0 - price)
    maker_rebate_per_share = maker_rebate_rate * price * (1.0 - price)
    return {
        "notional_usd": round(notional, 6),
        "shares": round(shares, 6),
        "estimated_taker_fee_usd": round(taker_fee_per_share * shares, 6),
        "estimated_taker_fee_per_share": round(taker_fee_per_share, 6),
        "estimated_maker_rebate_usd": round(maker_rebate_per_share * shares, 6),
        "estimated_maker_rebate_per_share": round(maker_rebate_per_share, 6),
    }


def sizing_shadow(price: float, p_yes: float, args: argparse.Namespace) -> dict[str, Any]:
    def one(cost: float) -> dict[str, Any]:
        shares = cost / price if price > 0 else 0.0
        fees = fee_metrics(
            price=price,
            shares=shares,
            taker_fee_rate=args.taker_fee_rate,
            maker_rebate_rate=args.maker_rebate_rate,
        )
        ev_usd_before_fee = p_yes * shares - cost
        ev_usd_after_fee = ev_usd_before_fee - fees["estimated_taker_fee_usd"]
        return {
            **fees,
            "cost_usd": round(cost, 6),
            "max_payout_usd": round(shares, 6),
            "model_ev_usd_before_fee": round(ev_usd_before_fee, 6),
            "model_ev_usd_after_taker_fee": round(ev_usd_after_fee, 6),
            "model_roi_after_taker_fee": round(ev_usd_after_fee / max(cost, 1e-9), 6),
        }

    ask_scaled_cost = max(0.0, min(5.0, 5.0 * max(0.25, min(1.0, (price - 0.05) / 0.15))))
    return {
        "fixed_1_5": one(float(args.order_notional_usd)),
        "fixed_2": one(2.0),
        "fixed_5": one(5.0),
        "payout25_cap5": one(min(5.0, price * 25.0)),
        "payout50_cap5": one(min(5.0, price * 50.0)),
        "ask_scaled_05_20": one(ask_scaled_cost),
    }


def validate_candidate(row: dict[str, Any], args: argparse.Namespace, cache: dict[str, Any], submitted_signal_ids: set[str]) -> dict[str, Any]:
    created_at = now_utc()
    signal_id = signal_id_for_row(row)
    snapshot_ts = parse_utc(row.get("decision_snapshot_ts_utc"))
    snapshot_age_hours = None
    if snapshot_ts is not None:
        snapshot_age_hours = max(0.0, (now_utc_dt() - snapshot_ts).total_seconds() / 3600.0)
    base = {
        "record_type": "low_price_yes_lottery_tiny_live_decision",
        "created_at_utc": created_at,
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "rule_id": RULE_ID,
        "source_report": SOURCE_REPORT,
        "signal_id": signal_id,
        "candidate_id": safe_str(row.get("candidate_id")),
        "city": safe_str(row.get("city")),
        "city_pool": safe_str(row.get("city_pool")),
        "icao": safe_str(row.get("icao")),
        "target_date": safe_str(row.get("event_date")),
        "event_date": safe_str(row.get("event_date")),
        "bracket": safe_str(row.get("bracket")),
        "unit": safe_str(row.get("unit")),
        "side": "BUY_YES",
        "condition_id": safe_str(row.get("condition_id")),
        "fact_market_id": safe_str(row.get("market_id")),
        "decision_snapshot_ts_utc": safe_str(row.get("decision_snapshot_ts_utc")),
        "decision_snapshot_age_hours": None if snapshot_age_hours is None else round(snapshot_age_hours, 4),
        "decision_hours_to_settle_at_snapshot": to_float(row.get("decision_hours_to_settle"), 0.0),
        "decision_entry_price": to_float(row.get("decision_entry_price"), 0.0),
        "snapshot_ask": to_float(row.get("decision_entry_price"), 0.0),
        "model_p_yes": to_float(row.get("model_p_yes"), 0.0),
        "edge": to_float(row.get("edge"), 0.0),
        "forecast_source": safe_str(row.get("forecast_source")),
        "forecast_peak_source": safe_str(row.get("forecast_peak_source")),
        "forecast_timezone": safe_str(row.get("forecast_timezone")),
        "forecast_max_f": row.get("forecast_max_f"),
        "forecast_max_native": row.get("forecast_max_native"),
        "forecast_peak_hour_local": row.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": safe_str(row.get("forecast_peak_time_local")),
        "forecast_peak_hour_utc": row.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": safe_str(row.get("forecast_peak_time_utc")),
        "forecast_peak_delta_hours_local": row.get("forecast_peak_delta_hours_local"),
        "forecast_max_in_bracket": row.get("forecast_max_in_bracket"),
        "forecast_max_above_bracket_f": row.get("forecast_max_above_bracket_f"),
        "forecast_max_below_bracket_f": row.get("forecast_max_below_bracket_f"),
        "model_version": safe_str(row.get("model_version")),
        "time_bucket": safe_str(row.get("time_bucket")),
        "window": safe_str(row.get("window")),
        "decision_window_label": safe_str(row.get("decision_window_label")),
        "first_seen_ts_utc": safe_str(row.get("first_seen_ts_utc")),
        "last_seen_ts_utc": safe_str(row.get("last_seen_ts_utc")),
        "n_snapshots": row.get("n_snapshots"),
        "fact_built_at_utc": safe_str(row.get("fact_built_at_utc")),
        "config": {
            "min_ask": args.min_ask,
            "max_ask": args.max_ask,
            "min_edge": args.min_edge,
            "max_taker_cushion": args.max_taker_cushion,
            "min_fee_adjusted_edge": args.min_fee_adjusted_edge,
            "order_notional_usd": args.order_notional_usd,
            "min_order_shares": args.min_order_shares,
            "max_decision_snapshot_age_hours": args.max_decision_snapshot_age_hours,
            "min_decision_hours_to_settle": args.min_decision_hours_to_settle,
            "dedupe": "one_live_order_per_city_date_bracket_condition_signal_id",
            "daily_cap": None,
        },
    }

    if signal_id in submitted_signal_ids:
        return {**base, "decision_status": "blocked", "blocker": "duplicate_submitted_signal"}
    if snapshot_ts is None:
        return {**base, "decision_status": "blocked", "blocker": "missing_decision_snapshot_ts"}
    if snapshot_age_hours is not None and snapshot_age_hours > args.max_decision_snapshot_age_hours:
        return {**base, "decision_status": "blocked", "blocker": "decision_snapshot_too_stale"}
    if to_float(row.get("decision_hours_to_settle"), 0.0) < args.min_decision_hours_to_settle:
        return {**base, "decision_status": "blocked", "blocker": "decision_hours_to_settle_below_min"}

    if args.disable_live_token_resolution:
        token_meta = cached_yes_token_only(row, cache)
    else:
        try:
            with hard_timeout(args.token_resolution_timeout_sec, "token resolution"):
                token_meta = resolve_yes_token(row, cache)
        except HardTimeoutError as exc:
            return {
                **base,
                "decision_status": "blocked",
                "blocker": "token_resolution_timeout",
                "token_resolution_error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **base,
                "decision_status": "blocked",
                "blocker": "token_resolution_failed",
                "token_resolution_error": f"{type(exc).__name__}: {exc}",
            }
    enriched = {**base, "token_meta": token_meta}
    token_id = safe_str(token_meta.get("yes_token_id"))
    if not token_id:
        return {**enriched, "decision_status": "blocked", "blocker": "missing_yes_token_id"}
    if token_meta.get("closed") is True:
        return {**enriched, "decision_status": "blocked", "blocker": "market_closed"}
    if token_meta.get("active") is False:
        return {**enriched, "decision_status": "blocked", "blocker": "market_inactive"}

    try:
        book = fetch_book(token_id, timeout_sec=args.book_timeout_sec)
    except Exception as exc:  # noqa: BLE001
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "fresh_book_fetch_failed",
            "token_id": token_id,
            "book_error": f"{type(exc).__name__}: {exc}",
        }
    asks = book_levels(book, "ask")
    bids = book_levels(book, "bid")
    if not asks:
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "fresh_book_no_asks",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
        }

    snapshot_ask = to_float(row.get("decision_entry_price"), 0.0)
    p_yes = to_float(row.get("model_p_yes"), 0.0)
    max_taker_price = min(float(args.max_ask), snapshot_ask + float(args.max_taker_cushion))
    executable = [(price, size) for price, size in asks if price <= max_taker_price + 1e-9]
    if not executable:
        fresh_ask, fresh_size = asks[0]
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "fresh_ask_exceeds_cushion_or_band",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": fresh_ask,
            "fresh_best_ask_size": fresh_size,
            "max_taker_price": max_taker_price,
            "fresh_edge": p_yes - fresh_ask,
        }

    limit_price = max(price for price, _size in executable)
    shares = round(float(args.order_notional_usd) / limit_price, 6) if limit_price > 0 else 0.0
    if shares < float(args.min_order_shares):
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "shares_below_exchange_min",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "limit_price": limit_price,
            "planned_shares": shares,
        }
    cumulative_shares = 0.0
    cumulative_notional = 0.0
    for price, size in executable:
        take = min(size, max(0.0, shares - cumulative_shares))
        cumulative_shares += take
        cumulative_notional += take * price
        if cumulative_shares + 1e-9 >= shares:
            break
    if cumulative_shares + 1e-9 < shares:
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "insufficient_executable_depth",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "max_taker_price": max_taker_price,
            "planned_shares": shares,
            "available_shares_within_limit": round(cumulative_shares, 6),
            "available_notional_within_limit": round(cumulative_notional, 6),
        }

    fees = fee_metrics(
        price=limit_price,
        shares=shares,
        taker_fee_rate=args.taker_fee_rate,
        maker_rebate_rate=args.maker_rebate_rate,
    )
    fee_adjusted_edge = p_yes - limit_price - fees["estimated_taker_fee_per_share"]
    if fee_adjusted_edge < float(args.min_fee_adjusted_edge):
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "fee_adjusted_edge_below_min",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "limit_price": limit_price,
            "fresh_edge": p_yes - limit_price,
            "fee_adjusted_edge": fee_adjusted_edge,
            **fees,
        }

    shadow = sizing_shadow(limit_price, p_yes, args)
    return {
        **enriched,
        "decision_status": "planned",
        "blocker": "",
        "token_id": token_id,
        "gamma_market_id": safe_str(token_meta.get("gamma_market_id")),
        "market_slug": safe_str(token_meta.get("market_slug")),
        "event_id": safe_str(token_meta.get("event_id")),
        "question": safe_str(token_meta.get("question")),
        "fresh_best_bid": bids[0][0] if bids else 0.0,
        "fresh_best_ask": asks[0][0],
        "fresh_best_ask_size": asks[0][1],
        "fresh_spread": round(max(0.0, asks[0][0] - bids[0][0]), 6) if bids else 0.0,
        "max_taker_price": round(max_taker_price, 6),
        "limit_price": round(limit_price, 6),
        "planned_shares": shares,
        "planned_notional_usd": round(shares * limit_price, 6),
        "fresh_edge": round(p_yes - limit_price, 6),
        "fee_adjusted_edge": round(fee_adjusted_edge, 6),
        "available_shares_within_limit": round(cumulative_shares, 6),
        "available_notional_within_limit": round(cumulative_notional, 6),
        "execution_mode": "tiny_live_guarded_taker_cancel_after",
        "shadow_sizing_variants": shadow,
        **fees,
    }


def build_plan(decision: dict[str, Any], *, live_enabled: bool) -> dict[str, Any]:
    price = to_float(decision.get("limit_price"), 0.0)
    shares = to_float(decision.get("planned_shares"), 0.0)
    p_yes = to_float(decision.get("model_p_yes"), 0.0)
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "probability_source": "fact_signal_candidates_model_p_yes",
        "decision_mode": "forecast_bias_low_price_tail_yes_lottery",
        "execution_mode": "tiny_live_guarded_taker_cancel_after",
        "profile": "edge20_ask05_20_fixed150",
        "combo": RULE_ID,
        "signal_id": safe_str(decision.get("signal_id")),
        "city": safe_str(decision.get("city")),
        "city_pool": safe_str(decision.get("city_pool")),
        "target_date": safe_str(decision.get("target_date")),
        "market_id": safe_str(decision.get("condition_id")),
        "market_slug": safe_str(decision.get("market_slug")),
        "event_slug": safe_str(decision.get("market_slug")),
        "event_id": safe_str(decision.get("event_id")),
        "question": safe_str(decision.get("question")),
        "bracket": safe_str(decision.get("bracket")),
        "token_id": safe_str(decision.get("token_id")),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(price, 6),
        "best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "spread": round(to_float(decision.get("fresh_spread"), 0.0), 6),
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": "low_price_yes_lottery_fresh_book_guarded_taker",
        "quote_edge": round(p_yes - price, 6),
        "required_quote_edge": round(to_float(decision.get("fee_adjusted_edge"), 0.0), 6),
        "model_token_probability": round(p_yes, 6),
        "quote_best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "quote_best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "quote_spread": round(to_float(decision.get("fresh_spread"), 0.0), 6),
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_book_guarded_taker",
        "child_order_role": "single",
        "maker_only": False,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(to_float(decision.get("planned_notional_usd"), 0.0), 6),
        "size": round(shares, 6),
        "notional": round(shares * price, 6),
        "execution_policy": "low_price_yes_lottery_guarded_taker_v1",
        "tick_size": 0.001,
        "entry_price_window": "0.05-0.20",
        "sizing_mode": "notional",
        "fixed_order_shares": 0.0,
        "max_order_shares": round(max(5.0, shares), 6),
        "edge": round(p_yes - price, 6),
        "min_edge": 0.20,
        "model_p_yes_used": round(p_yes, 6),
        "model_p_yes_raw": round(p_yes, 6),
        "market_implied_p_yes": round(price, 6),
        "edge_raw_yes": round(p_yes - price, 6),
        "edge_used_yes": round(p_yes - price, 6),
        "shadow_decision": "low_price_yes_lottery_tiny_live_v1",
        "shadow_reason": "user_approved_fixed_1_5_forward_probe_refined_selector",
        "obs_source": "not_used_forecast_fact_selector",
        "model_version": safe_str(decision.get("model_version")),
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "condition_id": safe_str(decision.get("condition_id")),
        "fact_market_id": safe_str(decision.get("fact_market_id")),
        "gamma_market_id": safe_str(decision.get("gamma_market_id")),
        "snapshot_ask": round(to_float(decision.get("snapshot_ask"), 0.0), 6),
        "fresh_best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "fresh_best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "max_taker_price": round(to_float(decision.get("max_taker_price"), 0.0), 6),
        "fee_adjusted_edge": round(to_float(decision.get("fee_adjusted_edge"), 0.0), 6),
        "estimated_taker_fee_usd": round(to_float(decision.get("estimated_taker_fee_usd"), 0.0), 6),
        "estimated_maker_rebate_usd": round(to_float(decision.get("estimated_maker_rebate_usd"), 0.0), 6),
        "expected_profit_usd_model": round(
            to_float(decision.get("shadow_sizing_variants", {}).get("fixed_1_5", {}).get("model_ev_usd_after_taker_fee"), 0.0),
            6,
        ),
        "forecast_source": safe_str(decision.get("forecast_source")),
        "forecast_max_f": decision.get("forecast_max_f"),
        "forecast_max_native": decision.get("forecast_max_native"),
        "forecast_peak_hour_local": decision.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": safe_str(decision.get("forecast_peak_time_local")),
        "forecast_peak_hour_utc": decision.get("forecast_peak_hour_utc"),
        "forecast_peak_time_utc": safe_str(decision.get("forecast_peak_time_utc")),
        "forecast_peak_delta_hours_local": decision.get("forecast_peak_delta_hours_local"),
        "decision_snapshot_ts_utc": safe_str(decision.get("decision_snapshot_ts_utc")),
        "source_snapshot_path": "runtime/weather.db:fact_signal_candidates",
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "created_at_utc": now_utc(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def run_executor(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.live:
        return None
    if not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    cmd = [
        sys.executable,
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(PLAN_OUT),
        "--paper-out",
        str(PAPER_OUT),
        "--live-out",
        str(LIVE_OUT),
        "--live",
        "--confirm-live",
        "--allow-taker",
        "--cancel-after",
        "--no-telegram",
    ]
    env = os.environ.copy()
    proxy = market_proxy_url()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=args.executor_timeout_sec,
    )
    payload: dict[str, Any] = {
        "executor_cmd": cmd,
        "executor_returncode": proc.returncode,
        "executor_output": proc.stdout[-8000:],
    }
    try:
        start = proc.stdout.find("{")
        end = proc.stdout.rfind("}")
        payload["executor_result"] = json.loads(proc.stdout[start : end + 1]) if start >= 0 and end > start else None
    except Exception:
        payload["executor_result"] = None
    return payload


def send_telegram_summary(summary: dict[str, Any], *, args: argparse.Namespace) -> None:
    if args.no_telegram:
        return
    planned = int(summary.get("planned_count") or 0)
    executor_result = summary.get("executor_result") if isinstance(summary.get("executor_result"), dict) else {}
    live_written = int(executor_result.get("live_written") or 0)
    live_errors = int(executor_result.get("live_errors") or 0)
    if planned <= 0 and live_errors <= 0:
        return
    try:
        from src.platform.notification.telegram import send_telegram_message_sync
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram helper unavailable: {type(exc).__name__}: {exc}")
        return
    lines = [
        "【Low-price YES lottery tiny-live】",
        f"planned={planned} live_written={live_written} live_errors={live_errors}",
        f"notional=${args.order_notional_usd:g}/order ask={args.min_ask:.2f}-{args.max_ask:.2f} edge>={args.min_edge:.2f}",
    ]
    for row in summary.get("planned_preview", [])[:8]:
        lines.append(
            "- "
            f"{safe_str(row.get('city'))} {safe_str(row.get('target_date'))} {safe_str(row.get('bracket'))} "
            f"ask={to_float(row.get('limit_price'), 0.0):.3f} p={to_float(row.get('model_p_yes'), 0.0):.3f} "
            f"fee_edge={to_float(row.get('fee_adjusted_edge'), 0.0):.3f}"
        )
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram send failed: {type(exc).__name__}: {exc}")


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = now_utc()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_token_cache()
    submitted_signal_ids = existing_submitted_signal_ids(LIVE_OUT)
    with connect(Path(args.db)) as conn:
        min_event_date = effective_min_event_date(conn, args)
        raw_counts = count_raw(conn, args, min_event_date)
        raw_candidates = load_candidates(conn, args, min_event_date)

    decisions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for row in raw_candidates:
        decision = validate_candidate(row, args, cache, submitted_signal_ids)
        decisions.append(decision)
        if decision.get("decision_status") == "planned":
            planned.append(decision)
        else:
            blocked.append(decision)

    live_enabled = bool(args.live and args.confirm_live)
    plans = [build_plan(decision, live_enabled=live_enabled) for decision in planned]
    write_jsonl(PLAN_OUT, plans)
    for decision in decisions:
        append_jsonl(SHADOW_OUT, decision)
    for decision in blocked:
        append_jsonl(BLOCKED_OUT, decision)
    write_json(
        LATEST_CANDIDATES_OUT,
        {
            "generated_at_utc": generated_at,
            "strategy_instance": STRATEGY_INSTANCE,
            "rule_id": RULE_ID,
            "decisions": decisions,
        },
    )

    executor_payload = run_executor(args)
    summary: dict[str, Any] = {
        "generated_at_utc": generated_at,
        "status": "planned",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "rule_id": RULE_ID,
        "source_report": SOURCE_REPORT,
        "db": rel(Path(args.db)),
        "runtime_dir": rel(RUNTIME_DIR),
        "effective_min_event_date": min_event_date,
        "effective_max_event_date": args.max_event_date,
        "raw_matching_rows": raw_counts,
        "raw_candidate_rows_after_city_date_dedupe": len(raw_candidates),
        "decision_count": len(decisions),
        "planned_count": len(planned),
        "blocked_count": len(blocked),
        "plans": len(plans),
        "live_requested": bool(args.live),
        "live_enabled": live_enabled,
        "daily_cap": None,
        "order_notional_usd": float(args.order_notional_usd),
        "planned_notional_usd": round(sum(to_float(row.get("planned_notional_usd"), 0.0) for row in planned), 6),
        "config": {
            "min_ask": float(args.min_ask),
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "max_taker_cushion": float(args.max_taker_cushion),
            "min_fee_adjusted_edge": float(args.min_fee_adjusted_edge),
            "max_decision_snapshot_age_hours": float(args.max_decision_snapshot_age_hours),
            "min_decision_hours_to_settle": float(args.min_decision_hours_to_settle),
            "min_order_shares": float(args.min_order_shares),
            "max_candidates_per_run": int(args.max_candidates_per_run),
            "allow_settled": bool(args.allow_settled),
            "cancel_after": True,
        },
        "files": {
            "summary": rel(SUMMARY_OUT),
            "summary_history": rel(HISTORY_OUT),
            "shadow_decisions": rel(SHADOW_OUT),
            "blocked_candidates": rel(BLOCKED_OUT),
            "latest_candidates": rel(LATEST_CANDIDATES_OUT),
            "trade_plans": rel(PLAN_OUT),
            "paper_orders": rel(PAPER_OUT),
            "live_orders": rel(LIVE_OUT),
            "token_cache": rel(TOKEN_CACHE_OUT),
        },
        "blocker_counts": {},
        "planned_preview": [
            {
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "snapshot_ask": row.get("snapshot_ask"),
                "fresh_best_ask": row.get("fresh_best_ask"),
                "limit_price": row.get("limit_price"),
                "model_p_yes": row.get("model_p_yes"),
                "edge": row.get("edge"),
                "fee_adjusted_edge": row.get("fee_adjusted_edge"),
                "planned_shares": row.get("planned_shares"),
                "planned_notional_usd": row.get("planned_notional_usd"),
                "estimated_taker_fee_usd": row.get("estimated_taker_fee_usd"),
            }
            for row in planned[:20]
        ],
    }
    counts: dict[str, int] = {}
    for row in blocked:
        key = safe_str(row.get("blocker")) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    summary["blocker_counts"] = counts
    if executor_payload:
        summary.update(executor_payload)
        if isinstance(executor_payload.get("executor_result"), dict):
            summary.update({"executor_result": executor_payload["executor_result"]})
    write_json(SUMMARY_OUT, summary)
    append_jsonl(HISTORY_OUT, summary)
    send_telegram_summary(summary, args=args)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--min-event-date", default=None, help="Default: latest unsettled matching event_date.")
    parser.add_argument("--max-event-date", default=None)
    parser.add_argument("--min-ask", type=float, default=0.05)
    parser.add_argument("--max-ask", type=float, default=0.20)
    parser.add_argument("--min-edge", type=float, default=0.20)
    parser.add_argument("--max-taker-cushion", type=float, default=0.01)
    parser.add_argument("--min-fee-adjusted-edge", type=float, default=0.15)
    parser.add_argument("--order-notional-usd", type=float, default=1.0)
    parser.add_argument("--min-order-shares", type=float, default=5.0)
    parser.add_argument("--max-decision-snapshot-age-hours", type=float, default=6.0)
    parser.add_argument("--min-decision-hours-to-settle", type=float, default=1.0)
    parser.add_argument("--max-candidates-per-run", type=int, default=80)
    parser.add_argument("--taker-fee-rate", type=float, default=0.06)
    parser.add_argument("--maker-rebate-rate", type=float, default=0.0125)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument("--token-resolution-timeout-sec", type=float, default=15.0)
    parser.add_argument("--disable-live-token-resolution", action="store_true")
    parser.add_argument("--executor-timeout-sec", type=float, default=180.0)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--allow-settled", action="store_true", help="Debug only; never use for live.")
    parser.add_argument("--live", action="store_true", help="Submit accepted plans to CLOB.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    os.chdir(ROOT)
    args = parse_args()
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if args.allow_settled and args.live:
        raise RuntimeError("--allow-settled cannot be used with --live")
    if args.command == "run":
        summary = run_once(args)
        print(json.dumps(json_ready(summary), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    while True:
        started = now_utc()
        print(f"[low_price_yes_lottery] cycle_start_utc={started}", flush=True)
        try:
            summary = run_once(args)
            print(
                "[low_price_yes_lottery] "
                f"planned={summary.get('planned_count')} blocked={summary.get('blocked_count')} "
                f"live={summary.get('live_enabled')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[low_price_yes_lottery] runner_failed {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(5.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
