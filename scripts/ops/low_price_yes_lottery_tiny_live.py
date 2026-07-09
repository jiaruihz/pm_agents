#!/usr/bin/env python3
"""Low-price YES lottery tiny-live head.

This is an independent live/shadow head for the refined low-price YES lottery
selector. It reads canonical fact_signal_candidates, rechecks the live CLOB
book, writes shadow telemetry for every candidate, and hands accepted maker-first
plans to weather_order_executor.py.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.services.market_resolver import resolve_market
from src.strategies.weather_edge_v1.tools.weather_edge_market_data import weather_event_slug
from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import (
    TailTelemetryResources,
    build_low_price_yes_tail_telemetry,
    load_tail_telemetry_resources_soft,
)
from src.strategies.weather_edge_v1.runtime import order_runtime
from weather_data_feed.source_policy import city_slug
from weather_feature_layer.runtime_refs import attach_runtime_feature_frame_ref

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
FILLS_IN = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"
LIFECYCLE_OUT = RUNTIME_DIR / "maker_lifecycle_decisions.jsonl"
FEATURE_STORE_DEFAULT = ROOT / os.environ.get("WEATHER_FEATURE_STORE_DIR", "runtime/weather_feature_store")

STRATEGY_INSTANCE = "low_price_yes_lottery_tiny_live_v1"
STRATEGY_ID = "low_price_yes_lottery_tiny_live_v1"
STRATEGY_FAMILY = "forecast_quality.low_price_yes_lottery"
RULE_ID = "buy_yes_edge20_ask05_20_maker_first_v1"
SOURCE_REPORT = "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md"
DIST_BRANCH_REPORT = "docs/analysis/2026-07/2026-07-04-low-price-yes-dist-branch-v1.md"
HEADA_REFINEMENT_REPORT = "docs/analysis/2026-07/2026-07-04-low-price-yes-heada-refinement-v1.md"
SIZING_POLICY_CHOICES = (
    "fixed_cash_order_notional",
    "fixed_5_shares",
    "fixed_8_shares",
    "price_tier_6_8_10_shares",
    "score_tier_0p8_1p2_1p5_shares",
    "score_tier_0p8_1p0_1p2_price_6_8_8_shares",
)

SCORE_DIST_MODEL_V1 = {
    "feature_names": [
        "entry",
        "edge",
        "raw_dist_br",
        "adj_dist_p50_br",
        "bias_n_asof",
        "bias_mean_asof",
        "bias_p90_asof",
        "hot_tail_pct_asof",
        "cold_tail_pct_asof",
    ],
    "medians": {
        "entry": 0.095,
        "edge": 0.2653,
        "raw_dist_br": 0.4999999999999952,
        "adj_dist_p50_br": -0.14999999999999858,
        "bias_n_asof": 356.0,
        "bias_mean_asof": 1.1087078651685394,
        "bias_p90_asof": 3.2,
        "hot_tail_pct_asof": 0.5577464788732395,
        "cold_tail_pct_asof": 0.09550561797752809,
    },
    "mu": [
        0.10471148825065274,
        0.2953960835509138,
        0.44566289527125075,
        -0.07412242529735925,
        441.42297650130547,
        0.9695848237124391,
        3.1449869451697126,
        0.4931696369611768,
        0.1479072559747869,
    ],
    "sd": [
        0.04096702709830627,
        0.08651788966857203,
        0.7271380744250431,
        0.4784559446782197,
        159.0349935859216,
        0.9167324542104754,
        1.197067320388609,
        0.19440370571215948,
        0.13131346898311078,
    ],
    "coef": [
        0.4221917137609502,
        -0.03367663882423542,
        0.05904575858609511,
        0.2177642412425464,
        -0.06172585579345474,
        0.051170438778585875,
        0.07328054075415853,
        -0.19526860022113685,
        -0.07272639764327733,
    ],
    "intercept": -1.789677290933876,
    "hot_train_q1": 0.13126780040457206,
    "hot_train_q2": 0.17781318659023707,
    "source_report": "docs/analysis/2026-07/2026-07-06-low-price-yes-score-dist-sizing-v1.md",
}

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
    return order_runtime.json_ready(value)


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
    order_runtime.write_json(path, payload)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    order_runtime.write_jsonl(path, rows)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    order_runtime.append_jsonl(path, row)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return order_runtime.read_jsonl(path)


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


def submitted_natural_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            safe_str(row.get("target_date") or row.get("event_date")),
            safe_str(row.get("city")),
            safe_str(row.get("bracket")),
            safe_str(row.get("condition_id") or row.get("market_id")),
            "BUY_YES",
        ]
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


def existing_submitted_natural_keys(path: Path = LIVE_OUT) -> set[str]:
    out: set[str] = set()
    for row in read_jsonl(path):
        if safe_str(row.get("record_type")) != "weather_edge_live_order":
            continue
        if safe_str(row.get("status")) != "submitted":
            continue
        key = submitted_natural_key(row)
        if key:
            out.add(key)
    return out


def live_order_id(row: dict[str, Any]) -> str:
    explicit = safe_str(row.get("order_id") or row.get("clob_order_id"))
    if explicit:
        return explicit
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = response.get("place") if isinstance(response.get("place"), dict) else {}
    for key in ("orderID", "order_id", "id"):
        value = safe_str(place.get(key))
        if value:
            return value
    for key in ("orderID", "order_id", "id"):
        value = safe_str(response.get(key))
        if value:
            return value
    return ""


def fills_by_order(path: Path = FILLS_IN) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(path):
        order_id = safe_str(row.get("order_id"))
        if not order_id:
            continue
        out.setdefault(order_id, []).append(row)
    return out


def filled_shares_for_order(order_id: str, fills: dict[str, list[dict[str, Any]]]) -> float:
    return round(sum(to_float(row.get("filled_shares"), 0.0) for row in fills.get(order_id, [])), 6)


def settled_city_date_brackets(db_path: Path) -> set[tuple[str, str, str]]:
    if not db_path.exists():
        return set()
    out: set[tuple[str, str, str]] = set()
    try:
        conn = connect(db_path)
        rows = conn.execute(
            """
            SELECT city, target_date, bracket
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
            """
        ).fetchall()
        conn.close()
    except Exception:
        return set()
    for row in rows:
        out.add((safe_str(row["city"]), safe_str(row["target_date"]), safe_str(row["bracket"])))
    return out


def existing_lifecycle_source_orders(path: Path = LIVE_OUT) -> set[str]:
    out: set[str] = set()
    for row in read_jsonl(path):
        if safe_str(row.get("record_type")) != "weather_edge_live_order":
            continue
        if safe_str(row.get("status")) != "submitted":
            continue
        source_order_id = safe_str(row.get("source_order_id"))
        action = safe_str(row.get("execution_action"))
        if source_order_id and action.startswith("maker_lifecycle_"):
            out.add(source_order_id)
    return out


def existing_lifecycle_keys(path: Path = LIFECYCLE_OUT) -> set[str]:
    return {
        safe_str(row.get("lifecycle_key"))
        for row in read_jsonl(path)
        if safe_str(row.get("lifecycle_key"))
        and bool(row.get("live_enabled", False))
        and safe_str(row.get("decision_status")) == "planned"
    }


def cumulative_ask_depth(asks: list[tuple[float, float]], *, max_price: float, shares: float) -> float:
    total = 0.0
    for price, size in asks:
        if price > max_price + 1e-9:
            break
        total += size
        if total + 1e-9 >= shares:
            break
    return round(total, 6)


def lifecycle_key_for(source_order_id: str, action: str, ttl_bucket_min: int) -> str:
    return stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "source_order_id": source_order_id,
            "action": action,
            "ttl_bucket_min": ttl_bucket_min,
        }
    )


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


def count_date_window_excluded(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> dict[str, Any]:
    if not min_event_date:
        return {
            "rows": 0,
            "city_date_dedupe_rows": 0,
            "dates": 0,
            "cities": 0,
            "min_event_date": None,
            "max_event_date": None,
            "reason": "no_effective_min_event_date",
        }
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    row = conn.execute(
        f"""
        WITH matching AS (
          SELECT
            event_date,
            city,
            ROW_NUMBER() OVER (
              PARTITION BY event_date, city
              ORDER BY decision_snapshot_ts_utc ASC, decision_entry_price ASC, edge DESC, bracket ASC, candidate_id ASC
            ) AS city_date_rank
          FROM fact_signal_candidates
          WHERE side = 'BUY_YES'
            AND decision_entry_price BETWEEN :min_ask AND :max_ask
            AND edge >= :min_edge
            AND event_date < :min_event_date
            {max_filter}
            {status_filter}
        )
        SELECT
          COUNT(*) AS rows,
          SUM(CASE WHEN city_date_rank = 1 THEN 1 ELSE 0 END) AS city_date_dedupe_rows,
          COUNT(DISTINCT event_date) AS dates,
          COUNT(DISTINCT city) AS cities,
          MIN(event_date) AS min_event_date,
          MAX(event_date) AS max_event_date
        FROM matching
        """,
        {
            "min_ask": args.min_ask,
            "max_ask": args.max_ask,
            "min_edge": args.min_edge,
            "min_event_date": min_event_date,
            "max_event_date": args.max_event_date,
        },
    ).fetchone()
    out = dict(row) if row is not None else {}
    out["reason"] = "before_effective_min_event_date"
    out["effective_min_event_date"] = min_event_date
    out["selection_behavior"] = "observability_only_no_selector_change"
    return out


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

    snapshot_payload = resolve_yes_token_from_local_snapshots(row)
    if snapshot_payload.get("yes_token_id"):
        cache[condition_id] = snapshot_payload
        save_token_cache(cache)
        return snapshot_payload

    try:
        event_payload = resolve_yes_token_from_event(row)
        if event_payload.get("yes_token_id"):
            cache[condition_id] = event_payload
            save_token_cache(cache)
            return event_payload
        errors.append(f"event_fallback:{event_payload.get('source')}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"event_fallback:{type(exc).__name__}: {exc}")

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


def maybe_failover_market_proxy(*, timeout_sec: float) -> dict[str, Any]:
    proxy = market_proxy_url()
    if not proxy:
        return {"status": "skipped", "reason": "direct_connection"}
    script = ROOT / "scripts/ops/weather_market_proxy_failover.py"
    if not script.exists():
        return {"status": "skipped", "reason": "missing_failover_script"}
    env = os.environ.copy()
    env.setdefault("WEATHER_DATA_FEED_MARKET_PROXY", proxy)
    proc = subprocess.run(
        [sys.executable, str(script), "--proxy", proxy, "--timeout-sec", str(max(5, int(timeout_sec)))],
        text=True,
        capture_output=True,
        timeout=max(20.0, float(timeout_sec) + 15.0),
        env=env,
    )
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "").strip().splitlines()[-1:] or [],
        "stderr": (proc.stderr or "").strip()[:240],
    }


def fetch_book_with_retry(
    token_id: str,
    *,
    timeout_sec: float,
    retries: int,
    retry_sleep_sec: float,
    failover_on_timeout: bool,
) -> dict[str, Any]:
    attempts = max(1, int(retries) + 1)
    last_exc: Exception | None = None
    did_failover = False
    for attempt in range(1, attempts + 1):
        try:
            return fetch_book(token_id, timeout_sec=timeout_sec)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if failover_on_timeout and not did_failover:
                did_failover = True
                try:
                    failover = maybe_failover_market_proxy(timeout_sec=timeout_sec)
                    print(
                        "[low_price_yes_lottery] book_fetch_failover "
                        f"attempt={attempt} status={failover.get('status')} token_id={token_id}",
                        flush=True,
                    )
                except Exception as failover_exc:  # noqa: BLE001
                    print(
                        "[low_price_yes_lottery] book_fetch_failover_failed "
                        f"{type(failover_exc).__name__}: {failover_exc}",
                        flush=True,
                    )
            if attempt < attempts and retry_sleep_sec > 0:
                time.sleep(float(retry_sleep_sec))
        except Exception:
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("book_fetch_failed_without_exception")


def marketable_shares_for_notional(*, notional_usd: float, limit_price: float, min_shares: float) -> float:
    if limit_price <= 0:
        return 0.0
    target_shares = max(float(min_shares), float(notional_usd) / limit_price, 1.0 / limit_price)
    shares = math.ceil((target_shares - 1e-12) * 100.0) / 100.0
    if shares * limit_price < 1.0:
        shares = math.ceil((shares + 0.01) * 100.0) / 100.0
    return round(shares, 2)


def shares_for_notional(*, notional_usd: float, limit_price: float, min_shares: float) -> float:
    if limit_price <= 0:
        return 0.0
    target_shares = max(float(min_shares), float(notional_usd) / limit_price)
    shares = math.ceil((target_shares - 1e-12) * 100.0) / 100.0
    return round(shares, 2)


def shares_for_fixed_count(*, shares: float, min_shares: float) -> float:
    target_shares = max(float(min_shares), float(shares))
    return round(math.ceil((target_shares - 1e-12) * 100.0) / 100.0, 2)


def shares_for_price_tier_6_8_10(*, price: float, min_shares: float) -> float:
    if price <= 0:
        return 0.0
    if price <= 0.08:
        shares = 6.0
    elif price <= 0.14:
        shares = 8.0
    else:
        shares = 10.0
    return shares_for_fixed_count(shares=shares, min_shares=min_shares)


def shares_for_price_tier_6_8_8(*, price: float, min_shares: float) -> float:
    if price <= 0:
        return 0.0
    shares = 6.0 if price <= 0.08 else 8.0
    return shares_for_fixed_count(shares=shares, min_shares=min_shares)


def shares_for_quality_price_tier_5_8_12(*, price: float, quality: float, min_shares: float) -> float:
    if price <= 0:
        return 0.0
    base = 5.0 if price <= 0.08 else (8.0 if price <= 0.14 else 10.0)
    if not math.isfinite(quality):
        quality = 0.0
    multiplier = 1.2 if quality >= 0.50 else (1.0 if quality >= 0.30 else 0.8)
    shares = min(12.0, max(5.0, base * multiplier))
    return shares_for_fixed_count(shares=shares, min_shares=min_shares)


def bracket_low_f_from_native(*, bracket_low_native: float, unit: str) -> float:
    if not math.isfinite(bracket_low_native):
        return math.nan
    if safe_str(unit).upper().startswith("C"):
        return bracket_low_native * 9.0 / 5.0 + 32.0
    return bracket_low_native


def bracket_width_f_for_unit(unit: str) -> float:
    return 1.8 if safe_str(unit).upper().startswith("C") else 2.0


def logistic(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def score_dist_sizing_features(row: dict[str, Any]) -> dict[str, float]:
    medians = SCORE_DIST_MODEL_V1["medians"]
    unit = safe_str(row.get("unit"))
    bracket_low_native = to_float(row.get("bracket_low_native"), math.nan)
    forecast_max_f = to_float(row.get("forecast_max_f"), math.nan)
    bracket_low_f = bracket_low_f_from_native(bracket_low_native=bracket_low_native, unit=unit)
    width_f = bracket_width_f_for_unit(unit)
    raw_dist = math.nan
    if math.isfinite(bracket_low_f) and math.isfinite(forecast_max_f) and width_f > 0:
        raw_dist = (bracket_low_f - forecast_max_f) / width_f
    bias_p50 = to_float(row.get("bias_p50_asof"), math.nan)
    adj_dist = math.nan
    if math.isfinite(bracket_low_f) and math.isfinite(forecast_max_f) and math.isfinite(bias_p50) and width_f > 0:
        adj_dist = (bracket_low_f - forecast_max_f - bias_p50) / width_f
    values = {
        "entry": to_float(row.get("decision_entry_price") or row.get("snapshot_ask"), medians["entry"]),
        "edge": to_float(row.get("edge"), medians["edge"]),
        "raw_dist_br": raw_dist,
        "adj_dist_p50_br": adj_dist,
        "bias_n_asof": to_float(row.get("bias_n_asof"), medians["bias_n_asof"]),
        "bias_mean_asof": to_float(row.get("bias_mean_asof"), medians["bias_mean_asof"]),
        "bias_p90_asof": to_float(row.get("bias_p90_asof"), medians["bias_p90_asof"]),
        "hot_tail_pct_asof": to_float(row.get("hot_tail_pct_asof"), medians["hot_tail_pct_asof"]),
        "cold_tail_pct_asof": to_float(row.get("cold_tail_pct_asof"), medians["cold_tail_pct_asof"]),
    }
    return {key: (value if math.isfinite(value) else float(medians[key])) for key, value in values.items()}


def score_dist_probability(row: dict[str, Any]) -> float:
    features = score_dist_sizing_features(row)
    score = float(SCORE_DIST_MODEL_V1["intercept"])
    for idx, name in enumerate(SCORE_DIST_MODEL_V1["feature_names"]):
        value = features[name]
        mu = float(SCORE_DIST_MODEL_V1["mu"][idx])
        sd = float(SCORE_DIST_MODEL_V1["sd"][idx]) or 1.0
        coef = float(SCORE_DIST_MODEL_V1["coef"][idx])
        score += coef * ((value - mu) / sd)
    return round(logistic(score), 6)


def score_dist_tier(score: float) -> str:
    if not math.isfinite(score):
        return "missing"
    if score <= float(SCORE_DIST_MODEL_V1["hot_train_q1"]):
        return "low"
    if score <= float(SCORE_DIST_MODEL_V1["hot_train_q2"]):
        return "mid"
    return "high"


def shares_for_score_tier_0p8_1p2_1p5(*, price: float, row: dict[str, Any], min_shares: float) -> float:
    base = shares_for_price_tier_6_8_10(price=price, min_shares=min_shares)
    score = score_dist_probability(row)
    tier = score_dist_tier(score)
    multiplier = {"low": 0.8, "mid": 1.2, "high": 1.5}.get(tier, 1.0)
    shares = min(15.0, base * multiplier)
    return shares_for_fixed_count(shares=shares, min_shares=min_shares)


def shares_for_score_tier_0p8_1p0_1p2_price_6_8_8(
    *, price: float, row: dict[str, Any], min_shares: float
) -> float:
    base = shares_for_price_tier_6_8_8(price=price, min_shares=min_shares)
    score = score_dist_probability(row)
    tier = score_dist_tier(score)
    multiplier = {"low": 0.8, "mid": 1.0, "high": 1.2}.get(tier, 1.0)
    shares = base * multiplier
    return shares_for_fixed_count(shares=shares, min_shares=min_shares)


def score_dist_sizing_meta(row: dict[str, Any]) -> dict[str, Any]:
    score = score_dist_probability(row)
    tier = score_dist_tier(score)
    multiplier = {"low": 0.8, "mid": 1.2, "high": 1.5}.get(tier, 1.0)
    return {
        "score_dist_sizing_model": "score_dist_sizing_v1",
        "score_dist_sizing_source_report": SCORE_DIST_MODEL_V1["source_report"],
        "score_dist_probability": score,
        "score_dist_tier": tier,
        "score_dist_multiplier": multiplier,
        "score_dist_features": score_dist_sizing_features(row),
    }


def sizing_quality(row: dict[str, Any]) -> float:
    pcal_ev = to_float(row.get("p_cal_no_city_ev"), math.nan)
    if math.isfinite(pcal_ev):
        return pcal_ev
    return to_float(row.get("edge"), 0.0)


def shares_for_sizing_policy(
    *,
    policy: str,
    price: float,
    row: dict[str, Any],
    order_notional_usd: float,
    min_shares: float,
) -> float:
    if policy == "fixed_cash_order_notional":
        return shares_for_notional(
            notional_usd=order_notional_usd,
            limit_price=price,
            min_shares=min_shares,
        )
    if policy == "fixed_5_shares":
        return shares_for_fixed_count(shares=5.0, min_shares=min_shares)
    if policy == "fixed_8_shares":
        return shares_for_fixed_count(shares=8.0, min_shares=min_shares)
    if policy == "price_tier_6_8_10_shares":
        return shares_for_price_tier_6_8_10(price=price, min_shares=min_shares)
    if policy == "score_tier_0p8_1p2_1p5_shares":
        return shares_for_score_tier_0p8_1p2_1p5(price=price, row=row, min_shares=min_shares)
    if policy == "score_tier_0p8_1p0_1p2_price_6_8_8_shares":
        return shares_for_score_tier_0p8_1p0_1p2_price_6_8_8(price=price, row=row, min_shares=min_shares)
    raise RuntimeError(f"unknown sizing policy {policy}")


def maker_price_for_buy(*, best_bid: float, best_ask: float, max_price: float, tick_size: float) -> float:
    tick = tick_size if tick_size > 0 else 0.001
    if best_ask <= tick:
        return 0.0
    if best_bid > 0:
        price = best_bid + tick
    else:
        price = best_ask - tick
    price = min(price, best_ask - tick, max_price)
    if price <= 0:
        return 0.0
    return round(math.floor((price + 1e-12) / tick) * tick, 6)


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


def sizing_shadow(price: float, p_yes: float, row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    def one(cost: float) -> dict[str, Any]:
        shares = cost / price if price > 0 else 0.0
        return one_shares(shares, override_cost=cost)

    def one_shares(shares: float, *, override_cost: float | None = None) -> dict[str, Any]:
        cost = float(override_cost) if override_cost is not None else shares * price
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
    price_tier_shares = shares_for_price_tier_6_8_10(price=price, min_shares=args.min_order_shares)
    score_tier_shares = shares_for_score_tier_0p8_1p2_1p5(price=price, row=row, min_shares=args.min_order_shares)
    score_tier_reduced_shares = shares_for_score_tier_0p8_1p0_1p2_price_6_8_8(
        price=price,
        row=row,
        min_shares=args.min_order_shares,
    )
    score_tier_meta = score_dist_sizing_meta(row)
    quality_tier_shares = shares_for_quality_price_tier_5_8_12(
        price=price,
        quality=sizing_quality(row),
        min_shares=args.min_order_shares,
    )
    live_shares = shares_for_sizing_policy(
        policy=args.sizing_policy,
        price=price,
        row=row,
        order_notional_usd=args.order_notional_usd,
        min_shares=args.min_order_shares,
    )
    return {
        "live_selected": {
            **one_shares(live_shares),
            "policy": args.sizing_policy,
        },
        "fixed_cash_order_notional": one(float(args.order_notional_usd)),
        "fixed_cash_0p80": one(0.80),
        "fixed_8_shares": one_shares(shares_for_fixed_count(shares=8.0, min_shares=args.min_order_shares)),
        "price_tier_6_8_10_shares": one_shares(price_tier_shares),
        "score_tier_0p8_1p2_1p5_shares": {
            **one_shares(score_tier_shares),
            **score_tier_meta,
        },
        "score_tier_0p8_1p0_1p2_price_6_8_8_shares": {
            **one_shares(score_tier_reduced_shares),
            **score_tier_meta,
        },
        "quality_price_tier_5_8_12_shares": {
            **one_shares(quality_tier_shares),
            "quality_score": round(sizing_quality(row), 6),
        },
        "fixed_1_5": one(float(args.order_notional_usd)),
        "fixed_2": one(2.0),
        "fixed_5": one(5.0),
        "payout25_cap5": one(min(5.0, price * 25.0)),
        "payout50_cap5": one(min(5.0, price * 50.0)),
        "ask_scaled_05_20": one(ask_scaled_cost),
    }


def validate_candidate(
    row: dict[str, Any],
    args: argparse.Namespace,
    cache: dict[str, Any],
    submitted_signal_ids: set[str],
    submitted_natural_keys: set[str],
    tail_telemetry_resources: TailTelemetryResources | None,
) -> dict[str, Any]:
    created_at = now_utc()
    signal_id = signal_id_for_row(row)
    snapshot_ts = parse_utc(row.get("decision_snapshot_ts_utc"))
    snapshot_age_hours = None
    if snapshot_ts is not None:
        snapshot_age_hours = max(0.0, (now_utc_dt() - snapshot_ts).total_seconds() / 3600.0)
    tail_telemetry = build_low_price_yes_tail_telemetry(row, tail_telemetry_resources)
    base = {
        "record_type": "low_price_yes_lottery_tiny_live_decision",
        "created_at_utc": created_at,
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "rule_id": RULE_ID,
        "source_report": SOURCE_REPORT,
        "dist_branch_report": DIST_BRANCH_REPORT,
        "heada_refinement_report": HEADA_REFINEMENT_REPORT,
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
        **tail_telemetry,
        "config": {
            "min_ask": args.min_ask,
            "max_ask": args.max_ask,
            "min_edge": args.min_edge,
            "max_taker_cushion": args.max_taker_cushion,
            "min_fee_adjusted_edge": args.min_fee_adjusted_edge,
            "order_notional_usd": args.order_notional_usd,
            "sizing_policy": args.sizing_policy,
            "min_order_shares": args.min_order_shares,
            "max_decision_snapshot_age_hours": args.max_decision_snapshot_age_hours,
            "min_decision_hours_to_settle": args.min_decision_hours_to_settle,
            "dedupe": "one_live_order_per_city_date_bracket_condition_signal_id",
            "daily_cap": None,
            "block_dist_le0_v1": not bool(args.allow_dist_le0 or args.allow_dist_lt0),
        },
    }

    allow_dist_le0 = bool(args.allow_dist_le0 or args.allow_dist_lt0)
    forecast_to_bracket_low_native = to_float(base.get("forecast_to_bracket_low_native"), math.nan)
    if (
        not allow_dist_le0
        and bool(base.get("bracket_distance_available"))
        and math.isfinite(forecast_to_bracket_low_native)
        and forecast_to_bracket_low_native <= 0.0
    ):
        if forecast_to_bracket_low_native < 0.0:
            blocker = "dist_lt0_cold_or_inside_forecast_tail_v1"
            reason = "bracket_low_below_decision_forecast_max_not_hot_tail"
        else:
            blocker = "dist_eq0_forecast_boundary_tail_v1"
            reason = "bracket_low_equals_decision_forecast_max_boundary_not_hot_tail"
        return {
            **base,
            "decision_status": "blocked",
            "blocker": blocker,
            "dist_le0_block_reason": reason,
        }

    if signal_id in submitted_signal_ids:
        return {**base, "decision_status": "blocked", "blocker": "duplicate_submitted_signal"}
    natural_key = submitted_natural_key(row)
    if natural_key in submitted_natural_keys:
        return {
            **base,
            "decision_status": "blocked",
            "blocker": "duplicate_submitted_city_date_bracket",
            "duplicate_natural_key": natural_key,
        }
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
        book = fetch_book_with_retry(
            token_id,
            timeout_sec=args.book_timeout_sec,
            retries=args.book_retries,
            retry_sleep_sec=args.book_retry_sleep_sec,
            failover_on_timeout=args.book_failover_on_timeout,
        )
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

    taker_limit_price = max(price for price, _size in executable)
    tick_size = 0.001
    maker_limit_price = maker_price_for_buy(
        best_bid=bids[0][0] if bids else 0.0,
        best_ask=asks[0][0],
        max_price=taker_limit_price,
        tick_size=tick_size,
    )
    if maker_limit_price <= 0:
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "maker_price_unavailable",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "max_taker_price": max_taker_price,
            "taker_limit_price": taker_limit_price,
        }
    shares = shares_for_sizing_policy(
        policy=safe_str(args.sizing_policy),
        price=maker_limit_price,
        row=enriched,
        order_notional_usd=float(args.order_notional_usd),
        min_shares=float(args.min_order_shares),
    )
    if shares < float(args.min_order_shares):
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "shares_below_exchange_min",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "limit_price": maker_limit_price,
            "taker_limit_price": taker_limit_price,
            "planned_shares": shares,
            "sizing_policy": safe_str(args.sizing_policy),
            "sizing_reference_price": maker_limit_price,
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
            "sizing_policy": safe_str(args.sizing_policy),
            "sizing_reference_price": maker_limit_price,
            "available_shares_within_limit": round(cumulative_shares, 6),
            "available_notional_within_limit": round(cumulative_notional, 6),
        }

    fees = fee_metrics(
        price=taker_limit_price,
        shares=shares,
        taker_fee_rate=args.taker_fee_rate,
        maker_rebate_rate=args.maker_rebate_rate,
    )
    fee_adjusted_edge = p_yes - taker_limit_price - fees["estimated_taker_fee_per_share"]
    if fee_adjusted_edge < float(args.min_fee_adjusted_edge):
        return {
            **enriched,
            "decision_status": "blocked",
            "blocker": "fee_adjusted_edge_below_min",
            "token_id": token_id,
            "fresh_best_bid": bids[0][0] if bids else 0.0,
            "fresh_best_ask": asks[0][0],
            "limit_price": maker_limit_price,
            "taker_limit_price": taker_limit_price,
            "fresh_edge": p_yes - taker_limit_price,
            "fee_adjusted_edge": fee_adjusted_edge,
            **fees,
        }

    maker_fees = fee_metrics(
        price=maker_limit_price,
        shares=shares,
        taker_fee_rate=args.taker_fee_rate,
        maker_rebate_rate=args.maker_rebate_rate,
    )
    maker_fee_adjusted_edge = p_yes - maker_limit_price + maker_fees["estimated_maker_rebate_per_share"]
    maker_fraction = max(0.0, min(1.0, float(args.maker_first_fraction)))
    planned_notional = shares * maker_limit_price
    taker_fallback_notional = max(0.0, planned_notional * (1.0 - maker_fraction))
    taker_fallback_status = (
        "disabled"
        if taker_fallback_notional <= 0
        else "skipped_below_min_marketable_notional"
        if taker_fallback_notional < float(args.taker_fallback_min_notional_usd)
        else "available"
    )
    shadow = sizing_shadow(maker_limit_price, p_yes, enriched, args)
    score_sizing_meta = score_dist_sizing_meta(enriched)
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
        "taker_limit_price": round(taker_limit_price, 6),
        "limit_price": round(maker_limit_price, 6),
        "maker_limit_price": round(maker_limit_price, 6),
        "planned_shares": shares,
        "planned_notional_usd": round(planned_notional, 6),
        "maker_planned_notional_usd": round(planned_notional, 6),
        "sizing_policy": safe_str(args.sizing_policy),
        "sizing_reference_price": round(maker_limit_price, 6),
        **score_sizing_meta,
        "taker_fallback_notional_usd": round(taker_fallback_notional, 6),
        "taker_fallback_status": taker_fallback_status,
        "fresh_edge": round(p_yes - taker_limit_price, 6),
        "maker_edge": round(p_yes - maker_limit_price, 6),
        "fee_adjusted_edge": round(fee_adjusted_edge, 6),
        "maker_fee_adjusted_edge": round(maker_fee_adjusted_edge, 6),
        "available_shares_within_limit": round(cumulative_shares, 6),
        "available_notional_within_limit": round(cumulative_notional, 6),
        "execution_mode": "tiny_live_maker_first",
        "shadow_sizing_variants": shadow,
        **fees,
        "estimated_maker_rebate_usd": maker_fees["estimated_maker_rebate_usd"],
        "estimated_maker_rebate_per_share": maker_fees["estimated_maker_rebate_per_share"],
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
        "execution_mode": "tiny_live_maker_first",
        "profile": f"edge20_ask05_20_maker_first_{safe_str(decision.get('sizing_policy')) or 'unknown'}",
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
        "quote_reason": "low_price_yes_lottery_maker_first",
        "quote_edge": round(p_yes - price, 6),
        "required_quote_edge": round(to_float(decision.get("fee_adjusted_edge"), 0.0), 6),
        "model_token_probability": round(p_yes, 6),
        "quote_best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "quote_best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "quote_spread": round(to_float(decision.get("fresh_spread"), 0.0), 6),
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_book_maker_first",
        "child_order_role": "maker_first",
        "maker_only": True,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(to_float(decision.get("planned_notional_usd"), 0.0), 6),
        "size": round(shares, 6),
        "notional": round(shares * price, 6),
        "execution_policy": "low_price_yes_lottery_maker_first_v1",
        "tick_size": 0.001,
        "entry_price_window": "0.05-0.20",
        "sizing_mode": safe_str(decision.get("sizing_policy")) or "unknown",
        "fixed_order_shares": round(shares, 6),
        "max_order_shares": round(max(5.0, shares), 6),
        "edge": round(p_yes - price, 6),
        "min_edge": 0.20,
        "model_p_yes_used": round(p_yes, 6),
        "model_p_yes_raw": round(p_yes, 6),
        "market_implied_p_yes": round(price, 6),
        "edge_raw_yes": round(p_yes - price, 6),
        "edge_used_yes": round(p_yes - price, 6),
        "shadow_decision": "low_price_yes_lottery_tiny_live_v1",
        "shadow_reason": "user_approved_score_tier_0p8_1p2_1p5_maker_first_forward_probe",
        "live_sizing_policy": safe_str(decision.get("sizing_policy")),
        "sizing_reference_price": round(to_float(decision.get("sizing_reference_price"), price), 6),
        "score_dist_sizing_model": safe_str(decision.get("score_dist_sizing_model")),
        "score_dist_sizing_source_report": safe_str(decision.get("score_dist_sizing_source_report")),
        "score_dist_probability": decision.get("score_dist_probability"),
        "score_dist_tier": safe_str(decision.get("score_dist_tier")),
        "score_dist_multiplier": decision.get("score_dist_multiplier"),
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
        "taker_limit_price": round(to_float(decision.get("taker_limit_price"), 0.0), 6),
        "maker_limit_price": round(to_float(decision.get("maker_limit_price"), 0.0), 6),
        "maker_fee_adjusted_edge": round(to_float(decision.get("maker_fee_adjusted_edge"), 0.0), 6),
        "taker_fallback_status": safe_str(decision.get("taker_fallback_status")),
        "taker_fallback_notional_usd": round(to_float(decision.get("taker_fallback_notional_usd"), 0.0), 6),
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
        "tail_telemetry_version": safe_str(decision.get("tail_telemetry_version")),
        "tail_telemetry_status": safe_str(decision.get("tail_telemetry_status")),
        "forecast_model_tail": safe_str(decision.get("forecast_model_tail")),
        "source_aware_v3": bool(decision.get("source_aware_v3")),
        "source_aware_v3_reason": safe_str(decision.get("source_aware_v3_reason")),
        "p_cal_no_city": decision.get("p_cal_no_city"),
        "p_cal_no_city_ev": decision.get("p_cal_no_city_ev"),
        "p_cal_no_city_edge": decision.get("p_cal_no_city_edge"),
        "p_cal_city_diag": decision.get("p_cal_city_diag"),
        "p_cal_city_diag_ev": decision.get("p_cal_city_diag_ev"),
        "p_cal_city_diag_edge": decision.get("p_cal_city_diag_edge"),
        "bias_n_asof": decision.get("bias_n_asof"),
        "bias_mean_asof": decision.get("bias_mean_asof"),
        "bias_p90_asof": decision.get("bias_p90_asof"),
        "hot_tail_pct_asof": decision.get("hot_tail_pct_asof"),
        "hot_tail2_pct_asof": decision.get("hot_tail2_pct_asof"),
        "cold_tail_pct_asof": decision.get("cold_tail_pct_asof"),
        "bias_mae_asof": decision.get("bias_mae_asof"),
        "bracket_low_native": decision.get("bracket_low_native"),
        "bracket_high_native": decision.get("bracket_high_native"),
        "bracket_distance_available": decision.get("bracket_distance_available"),
        "forecast_to_bracket_low_native": decision.get("forecast_to_bracket_low_native"),
        "forecast_above_bracket_high_native": decision.get("forecast_above_bracket_high_native"),
        "forecast_inside_bracket_bounds": decision.get("forecast_inside_bracket_bounds"),
        "decision_hour_local_pit": decision.get("decision_hour_local_pit"),
        "decision_local_bucket": safe_str(decision.get("decision_local_bucket")),
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


def attach_decision_feature_ref(decision: dict[str, Any]) -> dict[str, Any]:
    return attach_runtime_feature_frame_ref(
        decision,
        store_root=FEATURE_STORE_DEFAULT,
        feature_grain="low_price_yes_lottery_tiny_live_decision",
        source_profile_id=STRATEGY_INSTANCE,
        builder_version="low_price_yes_lottery_tiny_live_feature_ref_v1",
        key_columns=(
            "strategy_instance",
            "signal_id",
            "city",
            "target_date",
            "decision_snapshot_ts_utc",
            "bracket",
        ),
    )


def choose_lifecycle_action(
    *,
    order: dict[str, Any],
    age_min: float,
    remaining_shares: float,
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if not asks:
        return {"decision_status": "blocked", "blocker": "lifecycle_no_asks"}
    posted_price = to_float(order.get("posted_price") or order.get("limit_price"), 0.0)
    p_yes = to_float(order.get("model_p_yes_used") or order.get("model_token_probability"), 0.0)
    tick_size = to_float(order.get("quote_tick_size"), 0.001) or 0.001
    best_ask, best_ask_size = asks[0]
    best_bid = bids[0][0] if bids else 0.0
    spread = max(0.0, best_ask - best_bid) if best_bid > 0 else 0.0
    if posted_price <= 0 or remaining_shares < float(args.min_order_shares):
        return {"decision_status": "blocked", "blocker": "lifecycle_remaining_below_min_or_bad_price"}

    taker_max_price = min(float(args.max_ask), posted_price + float(args.maker_lifecycle_taker_max_premium))
    taker_depth = cumulative_ask_depth(asks, max_price=taker_max_price, shares=remaining_shares)
    taker_fee = fee_metrics(
        price=min(best_ask, taker_max_price),
        shares=remaining_shares,
        taker_fee_rate=args.taker_fee_rate,
        maker_rebate_rate=args.maker_rebate_rate,
    )
    taker_fee_adjusted_edge = p_yes - best_ask - to_float(taker_fee.get("estimated_taker_fee_per_share"), 0.0)
    if (
        bool(args.maker_lifecycle_allow_taker_fallback)
        and age_min >= float(args.maker_lifecycle_taker_ttl_min)
        and spread <= float(args.maker_lifecycle_spread_cap) + 1e-9
        and best_ask <= taker_max_price + 1e-9
        and taker_depth + 1e-9 >= remaining_shares
        and taker_fee_adjusted_edge >= float(args.min_fee_adjusted_edge)
    ):
        return {
            "decision_status": "planned",
            "execution_action": "maker_lifecycle_taker_fallback",
            "maker_only": False,
            "limit_price": round(best_ask, 6),
            "quote_mode": "maker_lifecycle_tight_taker",
            "quote_reason": "ttl_tight_spread_no_premium_taker",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "fee_adjusted_edge": round(taker_fee_adjusted_edge, 6),
            "estimated_taker_fee_usd": taker_fee["estimated_taker_fee_usd"],
        }

    downshift = posted_price - best_ask
    if age_min >= float(args.maker_lifecycle_refresh_ttl_min) and downshift >= float(args.maker_lifecycle_downshift_min) - 1e-9:
        new_price = maker_price_for_buy(
            best_bid=best_bid,
            best_ask=best_ask,
            max_price=min(posted_price, float(args.max_ask)),
            tick_size=tick_size,
        )
        if new_price > 0 and new_price < posted_price - 1e-9:
            return {
                "decision_status": "planned",
                "execution_action": "maker_lifecycle_repost_lower",
                "maker_only": True,
                "limit_price": new_price,
                "quote_mode": "maker_lifecycle_repost_lower",
                "quote_reason": "book_moved_down_cancel_stale_overbid",
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "fee_adjusted_edge": round(p_yes - new_price, 6),
                "estimated_taker_fee_usd": 0.0,
            }

    maker_max_price = min(float(args.max_ask), posted_price + float(args.maker_lifecycle_reprice_cushion))
    new_maker_price = maker_price_for_buy(
        best_bid=best_bid,
        best_ask=best_ask,
        max_price=maker_max_price,
        tick_size=tick_size,
    )
    if (
        age_min >= float(args.maker_lifecycle_refresh_ttl_min)
        and new_maker_price > posted_price + float(args.maker_lifecycle_min_reprice_improvement) - 1e-9
        and p_yes - new_maker_price >= float(args.min_edge)
    ):
        return {
            "decision_status": "planned",
            "execution_action": "maker_lifecycle_reprice_maker",
            "maker_only": True,
            "limit_price": new_maker_price,
            "quote_mode": "maker_lifecycle_reprice_maker",
            "quote_reason": "maker_order_behind_current_bid",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "fee_adjusted_edge": round(p_yes - new_maker_price, 6),
            "estimated_taker_fee_usd": 0.0,
        }

    return {
        "decision_status": "blocked",
        "blocker": "lifecycle_no_action",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "fee_adjusted_edge": round(taker_fee_adjusted_edge, 6),
    }


def build_lifecycle_plan(
    order: dict[str, Any],
    action: dict[str, Any],
    *,
    source_order_id: str,
    remaining_shares: float,
    filled_shares: float,
    age_min: float,
    lifecycle_key: str,
    live_enabled: bool,
) -> dict[str, Any]:
    price = to_float(action.get("limit_price"), 0.0)
    p_yes = to_float(order.get("model_p_yes_used") or order.get("model_token_probability"), 0.0)
    execution_action = safe_str(action.get("execution_action"))
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "probability_source": safe_str(order.get("probability_source")) or "fact_signal_candidates_model_p_yes",
        "decision_mode": "forecast_bias_low_price_tail_yes_lottery",
        "execution_mode": "tiny_live_dynamic_maker",
        "profile": f"dynamic_maker_{execution_action}",
        "combo": RULE_ID,
        "signal_id": safe_str(order.get("signal_id")),
        "city": safe_str(order.get("city")),
        "city_pool": safe_str(order.get("city_pool")),
        "target_date": safe_str(order.get("target_date")),
        "market_id": safe_str(order.get("market_id")),
        "market_slug": safe_str(order.get("market_slug")),
        "event_slug": safe_str(order.get("event_slug") or order.get("market_slug")),
        "event_id": safe_str(order.get("event_id")),
        "question": safe_str(order.get("question")),
        "bracket": safe_str(order.get("bracket")),
        "token_id": safe_str(order.get("token_id")),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(price, 6),
        "best_bid": round(to_float(action.get("best_bid"), 0.0), 6),
        "best_ask": round(to_float(action.get("best_ask"), 0.0), 6),
        "spread": round(to_float(action.get("spread"), 0.0), 6),
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": safe_str(action.get("quote_reason")),
        "quote_edge": round(p_yes - price, 6),
        "required_quote_edge": round(to_float(action.get("fee_adjusted_edge"), 0.0), 6),
        "model_token_probability": round(p_yes, 6),
        "quote_best_bid": round(to_float(action.get("best_bid"), 0.0), 6),
        "quote_best_ask": round(to_float(action.get("best_ask"), 0.0), 6),
        "quote_spread": round(to_float(action.get("spread"), 0.0), 6),
        "quote_tick_size": to_float(order.get("quote_tick_size"), 0.001) or 0.001,
        "quote_mode": safe_str(action.get("quote_mode")),
        "child_order_role": execution_action,
        "maker_only": bool(action.get("maker_only", True)),
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(remaining_shares * price, 6),
        "size": round(remaining_shares, 6),
        "notional": round(remaining_shares * price, 6),
        "execution_policy": "low_price_yes_lottery_dynamic_maker_v1",
        "execution_action": execution_action,
        "allow_duplicate_signal_id": True,
        "cancel_before_order_id": source_order_id,
        "source_order_id": source_order_id,
        "source_execution_id": safe_str(order.get("execution_id")),
        "source_plan_id": safe_str(order.get("plan_id")),
        "source_posted_price": round(to_float(order.get("posted_price"), 0.0), 6),
        "source_filled_shares": round(filled_shares, 6),
        "source_remaining_shares": round(remaining_shares, 6),
        "source_order_age_min": round(age_min, 3),
        "lifecycle_key": lifecycle_key,
        "tick_size": to_float(order.get("tick_size"), 0.001) or 0.001,
        "entry_price_window": "0.05-0.20",
        "sizing_mode": safe_str(order.get("sizing_mode")) or "unknown",
        "fixed_order_shares": round(remaining_shares, 6),
        "max_order_shares": round(max(5.0, remaining_shares), 6),
        "edge": round(p_yes - price, 6),
        "min_edge": 0.20,
        "model_p_yes_used": round(p_yes, 6),
        "model_p_yes_raw": round(to_float(order.get("model_p_yes_raw"), p_yes), 6),
        "model_version": safe_str(order.get("model_version")),
        "forecast_source": safe_str(order.get("forecast_source")),
        "market_implied_p_yes": round(price, 6),
        "edge_raw_yes": round(p_yes - price, 6),
        "edge_used_yes": round(p_yes - price, 6),
        "shadow_decision": "low_price_yes_lottery_dynamic_maker_v1",
        "shadow_reason": "user_approved_dynamic_maker_lifecycle_live_probe",
        "live_sizing_policy": safe_str(order.get("live_sizing_policy") or order.get("sizing_mode")),
        "score_dist_sizing_model": safe_str(order.get("score_dist_sizing_model")),
        "score_dist_sizing_source_report": safe_str(order.get("score_dist_sizing_source_report")),
        "score_dist_probability": order.get("score_dist_probability"),
        "score_dist_tier": safe_str(order.get("score_dist_tier")),
        "score_dist_multiplier": order.get("score_dist_multiplier"),
        "fee_adjusted_edge": round(to_float(action.get("fee_adjusted_edge"), 0.0), 6),
        "estimated_taker_fee_usd": round(to_float(action.get("estimated_taker_fee_usd"), 0.0), 6),
        "paper_enabled": False,
        "live_enabled": bool(live_enabled),
        "source_snapshot_path": "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl",
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


def lifecycle_plans(args: argparse.Namespace, *, live_enabled: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bool(args.maker_lifecycle_enabled):
        return [], []
    now = now_utc_dt()
    fills = fills_by_order()
    settled = settled_city_date_brackets(Path(args.db))
    replaced_source_orders = existing_lifecycle_source_orders()
    used_lifecycle_keys = existing_lifecycle_keys()
    decisions: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    max_actions = max(0, int(args.maker_lifecycle_max_actions_per_run))
    for order in read_jsonl(LIVE_OUT):
        if len(plans) >= max_actions:
            break
        if safe_str(order.get("record_type")) != "weather_edge_live_order":
            continue
        if safe_str(order.get("status")) != "submitted":
            continue
        if safe_str(order.get("strategy_id") or order.get("strategy_instance")) != STRATEGY_ID:
            continue
        if safe_str(order.get("execution_policy")) not in {
            "low_price_yes_lottery_maker_first_v1",
            "low_price_yes_lottery_dynamic_maker_v1",
        }:
            continue
        if not bool(order.get("maker_only", False)):
            continue
        source_order_id = live_order_id(order)
        if not source_order_id or source_order_id in replaced_source_orders:
            continue
        if (safe_str(order.get("city")), safe_str(order.get("target_date")), safe_str(order.get("bracket"))) in settled:
            continue
        created = parse_utc(order.get("created_at_utc"))
        if created is None:
            continue
        age_min = (now - created).total_seconds() / 60.0
        if age_min < float(args.maker_lifecycle_refresh_ttl_min):
            continue
        posted_shares = to_float(order.get("size"), 0.0)
        filled_shares = filled_shares_for_order(source_order_id, fills)
        remaining_shares = max(0.0, posted_shares - filled_shares)
        if remaining_shares < float(args.min_order_shares):
            continue
        token_id = safe_str(order.get("token_id"))
        if not token_id:
            continue
        try:
            book = fetch_book_with_retry(
                token_id,
                timeout_sec=float(args.book_timeout_sec),
                retries=int(args.book_retries),
                retry_sleep_sec=float(args.book_retry_sleep_sec),
                failover_on_timeout=bool(args.book_failover_on_timeout),
            )
            asks = book_levels(book, "ask")
            bids = book_levels(book, "bid")
            action = choose_lifecycle_action(
                order=order,
                age_min=age_min,
                remaining_shares=remaining_shares,
                asks=asks,
                bids=bids,
                args=args,
            )
        except Exception as exc:  # noqa: BLE001
            action = {
                "decision_status": "blocked",
                "blocker": "lifecycle_book_fetch_failed",
                "book_error": f"{type(exc).__name__}: {exc}",
            }
        ttl_bucket_min = int(math.floor(age_min / max(1.0, float(args.maker_lifecycle_refresh_ttl_min))) * float(args.maker_lifecycle_refresh_ttl_min))
        lifecycle_key = lifecycle_key_for(source_order_id, safe_str(action.get("execution_action") or action.get("blocker")), ttl_bucket_min)
        decision = {
            "record_type": "low_price_yes_maker_lifecycle_decision",
            "created_at_utc": now_utc(),
            "strategy_instance": STRATEGY_INSTANCE,
            "lifecycle_key": lifecycle_key,
            "live_enabled": bool(live_enabled),
            "source_order_id": source_order_id,
            "source_execution_id": safe_str(order.get("execution_id")),
            "source_plan_id": safe_str(order.get("plan_id")),
            "city": safe_str(order.get("city")),
            "target_date": safe_str(order.get("target_date")),
            "bracket": safe_str(order.get("bracket")),
            "token_id": token_id,
            "age_min": round(age_min, 3),
            "posted_price": to_float(order.get("posted_price"), 0.0),
            "posted_shares": posted_shares,
            "filled_shares": filled_shares,
            "remaining_shares": round(remaining_shares, 6),
            **action,
        }
        if lifecycle_key in used_lifecycle_keys:
            decision = {**decision, "decision_status": "blocked", "blocker": "lifecycle_duplicate_key"}
        decisions.append(decision)
        append_jsonl(LIFECYCLE_OUT, decision)
        used_lifecycle_keys.add(lifecycle_key)
        if decision.get("decision_status") != "planned":
            continue
        plans.append(
            build_lifecycle_plan(
                order,
                decision,
                source_order_id=source_order_id,
                remaining_shares=remaining_shares,
                filled_shares=filled_shares,
                age_min=age_min,
                lifecycle_key=lifecycle_key,
                live_enabled=live_enabled,
            )
        )
    return plans, decisions


def run_executor(args: argparse.Namespace) -> dict[str, Any] | None:
    return order_runtime.run_weather_order_executor(
        root=ROOT,
        plans_path=PLAN_OUT,
        paper_out=PAPER_OUT,
        live_out=LIVE_OUT,
        live=bool(args.live),
        confirm_live=bool(args.confirm_live),
        no_telegram=True,
        timeout_sec=float(args.executor_timeout_sec),
        env=order_runtime.executor_proxy_env(market_proxy_url()),
    )


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
        f"sizing={args.sizing_policy} cash_ref=${args.order_notional_usd:g} ask={args.min_ask:.2f}-{args.max_ask:.2f} edge>={args.min_edge:.2f}",
    ]
    for row in summary.get("planned_preview", [])[:8]:
        lines.append(
            "- "
            f"{safe_str(row.get('city'))} {safe_str(row.get('target_date'))} {safe_str(row.get('bracket'))} "
            f"ask={to_float(row.get('limit_price'), 0.0):.3f} p={to_float(row.get('model_p_yes'), 0.0):.3f} "
            f"shares={to_float(row.get('planned_shares'), 0.0):.2f} fee_edge={to_float(row.get('fee_adjusted_edge'), 0.0):.3f}"
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
    submitted_natural_keys = existing_submitted_natural_keys(LIVE_OUT)
    tail_telemetry_resources = load_tail_telemetry_resources_soft()
    with connect(Path(args.db)) as conn:
        min_event_date = effective_min_event_date(conn, args)
        raw_counts = count_raw(conn, args, min_event_date)
        date_window_excluded_counts = count_date_window_excluded(conn, args, min_event_date)
        raw_candidates = load_candidates(conn, args, min_event_date)

    decisions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for row in raw_candidates:
        decision = validate_candidate(row, args, cache, submitted_signal_ids, submitted_natural_keys, tail_telemetry_resources)
        decision = attach_decision_feature_ref(decision)
        decisions.append(decision)
        if decision.get("decision_status") == "planned":
            planned.append(decision)
        else:
            blocked.append(decision)

    live_enabled = bool(args.live and args.confirm_live)
    entry_plans = [build_plan(decision, live_enabled=live_enabled) for decision in planned]
    maker_lifecycle_plans, maker_lifecycle_decisions = lifecycle_plans(args, live_enabled=live_enabled)
    plans = [*maker_lifecycle_plans, *entry_plans]
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
        "date_window_excluded_matching_rows": date_window_excluded_counts,
        "raw_candidate_rows_after_city_date_dedupe": len(raw_candidates),
        "decision_count": len(decisions),
        "feature_frame_ref_stored_count": sum(1 for row in decisions if safe_str(row.get("feature_frame_ref_status")) == "stored"),
        "feature_frame_ref_error_count": sum(1 for row in decisions if safe_str(row.get("feature_frame_ref_status")) == "error"),
        "planned_count": len(planned),
        "entry_plan_count": len(entry_plans),
        "maker_lifecycle_decision_count": len(maker_lifecycle_decisions),
        "maker_lifecycle_plan_count": len(maker_lifecycle_plans),
        "blocked_count": len(blocked),
        "plans": len(plans),
        "live_requested": bool(args.live),
        "live_enabled": live_enabled,
        "daily_cap": None,
        "order_notional_usd": float(args.order_notional_usd),
        "sizing_policy": safe_str(args.sizing_policy),
        "planned_notional_usd": round(sum(to_float(row.get("planned_notional_usd"), 0.0) for row in planned), 6),
        "tail_telemetry_status_counts": {
            status: sum(1 for row in decisions if safe_str(row.get("tail_telemetry_status")) == status)
            for status in sorted({safe_str(row.get("tail_telemetry_status")) for row in decisions})
        },
        "tail_telemetry_model_artifact": safe_str(decisions[0].get("tail_telemetry_model_artifact")) if decisions else "",
        "tail_telemetry_bias_source": safe_str(decisions[0].get("tail_telemetry_bias_source")) if decisions else "",
        "config": {
            "min_ask": float(args.min_ask),
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "max_taker_cushion": float(args.max_taker_cushion),
            "min_fee_adjusted_edge": float(args.min_fee_adjusted_edge),
            "max_decision_snapshot_age_hours": float(args.max_decision_snapshot_age_hours),
            "min_decision_hours_to_settle": float(args.min_decision_hours_to_settle),
            "min_order_shares": float(args.min_order_shares),
            "sizing_policy": safe_str(args.sizing_policy),
            "maker_first_fraction": float(args.maker_first_fraction),
            "taker_fallback_min_notional_usd": float(args.taker_fallback_min_notional_usd),
            "taker_fee_rate": float(args.taker_fee_rate),
            "maker_lifecycle_enabled": bool(args.maker_lifecycle_enabled),
            "maker_lifecycle_refresh_ttl_min": float(args.maker_lifecycle_refresh_ttl_min),
            "maker_lifecycle_taker_ttl_min": float(args.maker_lifecycle_taker_ttl_min),
            "maker_lifecycle_spread_cap": float(args.maker_lifecycle_spread_cap),
            "maker_lifecycle_taker_max_premium": float(args.maker_lifecycle_taker_max_premium),
            "maker_lifecycle_reprice_cushion": float(args.maker_lifecycle_reprice_cushion),
            "maker_lifecycle_downshift_min": float(args.maker_lifecycle_downshift_min),
            "maker_lifecycle_allow_taker_fallback": bool(args.maker_lifecycle_allow_taker_fallback),
            "maker_lifecycle_max_actions_per_run": int(args.maker_lifecycle_max_actions_per_run),
            "max_candidates_per_run": int(args.max_candidates_per_run),
            "allow_settled": bool(args.allow_settled),
            "cancel_after": False,
            "allow_dist_lt0_deprecated": bool(args.allow_dist_lt0),
            "allow_dist_le0": bool(args.allow_dist_le0 or args.allow_dist_lt0),
            "block_dist_le0_v1": not bool(args.allow_dist_le0 or args.allow_dist_lt0),
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
            "maker_lifecycle_decisions": rel(LIFECYCLE_OUT),
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
                "maker_limit_price": row.get("maker_limit_price"),
                "taker_limit_price": row.get("taker_limit_price"),
                "model_p_yes": row.get("model_p_yes"),
                "edge": row.get("edge"),
                "fee_adjusted_edge": row.get("fee_adjusted_edge"),
                "maker_fee_adjusted_edge": row.get("maker_fee_adjusted_edge"),
                "planned_shares": row.get("planned_shares"),
                "planned_notional_usd": row.get("planned_notional_usd"),
                "sizing_policy": row.get("sizing_policy"),
                "sizing_reference_price": row.get("sizing_reference_price"),
                "taker_fallback_status": row.get("taker_fallback_status"),
                "taker_fallback_notional_usd": row.get("taker_fallback_notional_usd"),
                "estimated_taker_fee_usd": row.get("estimated_taker_fee_usd"),
                "source_aware_v3": row.get("source_aware_v3"),
                "p_cal_no_city": row.get("p_cal_no_city"),
                "p_cal_city_diag": row.get("p_cal_city_diag"),
                "bias_p90_asof": row.get("bias_p90_asof"),
                "hot_tail_pct_asof": row.get("hot_tail_pct_asof"),
                "forecast_to_bracket_low_native": row.get("forecast_to_bracket_low_native"),
                "dist_le0_block_reason": row.get("dist_le0_block_reason"),
            }
            for row in planned[:20]
        ],
        "maker_lifecycle_preview": [
            {
                "source_order_id": row.get("source_order_id"),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "age_min": row.get("age_min"),
                "remaining_shares": row.get("remaining_shares"),
                "decision_status": row.get("decision_status"),
                "execution_action": row.get("execution_action"),
                "blocker": row.get("blocker"),
                "posted_price": row.get("posted_price"),
                "limit_price": row.get("limit_price"),
                "best_bid": row.get("best_bid"),
                "best_ask": row.get("best_ask"),
                "spread": row.get("spread"),
                "fee_adjusted_edge": row.get("fee_adjusted_edge"),
            }
            for row in maker_lifecycle_decisions[:20]
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
    parser.add_argument("--order-notional-usd", type=float, default=0.8)
    parser.add_argument("--sizing-policy", choices=SIZING_POLICY_CHOICES, default="fixed_cash_order_notional")
    parser.add_argument("--min-order-shares", type=float, default=5.0)
    parser.add_argument("--maker-first-fraction", type=float, default=1.0)
    parser.add_argument("--taker-fallback-min-notional-usd", type=float, default=1.0)
    parser.add_argument("--max-decision-snapshot-age-hours", type=float, default=6.0)
    parser.add_argument("--min-decision-hours-to-settle", type=float, default=1.0)
    parser.add_argument("--max-candidates-per-run", type=int, default=80)
    parser.add_argument("--taker-fee-rate", type=float, default=0.05)
    parser.add_argument("--maker-rebate-rate", type=float, default=0.0125)
    parser.add_argument("--maker-lifecycle-enabled", action="store_true", help="Manage stale maker-first orders with cancel/repost/tight taker fallback.")
    parser.add_argument("--maker-lifecycle-refresh-ttl-min", type=float, default=15.0)
    parser.add_argument("--maker-lifecycle-taker-ttl-min", type=float, default=30.0)
    parser.add_argument("--maker-lifecycle-spread-cap", type=float, default=0.01)
    parser.add_argument("--maker-lifecycle-taker-max-premium", type=float, default=0.0)
    parser.add_argument("--maker-lifecycle-reprice-cushion", type=float, default=0.20)
    parser.add_argument("--maker-lifecycle-downshift-min", type=float, default=0.01)
    parser.add_argument("--maker-lifecycle-min-reprice-improvement", type=float, default=0.001)
    parser.add_argument("--maker-lifecycle-max-actions-per-run", type=int, default=3)
    parser.add_argument("--maker-lifecycle-allow-taker-fallback", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument("--book-retries", type=int, default=1)
    parser.add_argument("--book-retry-sleep-sec", type=float, default=0.4)
    parser.add_argument("--book-failover-on-timeout", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--token-resolution-timeout-sec", type=float, default=15.0)
    parser.add_argument("--disable-live-token-resolution", action="store_true")
    parser.add_argument("--allow-dist-le0", action="store_true", help="Debug only; preserve old selector behavior for forecast-boundary/below-forecast tickets.")
    parser.add_argument("--allow-dist-lt0", action="store_true", help="Deprecated debug alias for --allow-dist-le0.")
    parser.add_argument("--executor-timeout-sec", type=float, default=180.0)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--allow-settled", action="store_true", help="Debug only; never use for live.")
    parser.add_argument("--live", action="store_true", help="Submit accepted plans to CLOB.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def enforce_live_safety_args(args: argparse.Namespace) -> None:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if args.allow_settled and args.live:
        raise RuntimeError("--allow-settled cannot be used with --live")
    if args.live and (args.allow_dist_le0 or args.allow_dist_lt0):
        raise RuntimeError("--allow-dist-le0/--allow-dist-lt0 cannot be used with --live")
    if args.live and getattr(args, "maker_lifecycle_enabled", False):
        if args.maker_lifecycle_refresh_ttl_min <= 0:
            raise RuntimeError("--maker-lifecycle-refresh-ttl-min must be positive")
        if args.maker_lifecycle_taker_ttl_min < args.maker_lifecycle_refresh_ttl_min:
            raise RuntimeError("--maker-lifecycle-taker-ttl-min cannot be below refresh ttl")
        if args.maker_lifecycle_taker_max_premium > 0.01 + 1e-9:
            raise RuntimeError("--maker-lifecycle-taker-max-premium cannot exceed 0.01 for live")


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    os.chdir(ROOT)
    args = parse_args()
    enforce_live_safety_args(args)
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
