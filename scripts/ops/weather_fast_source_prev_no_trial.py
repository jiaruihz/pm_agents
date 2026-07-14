#!/usr/bin/env python3
"""Fast-source previous-NO trial runner.

Consumes high-frequency airport/reference observations and source-events METAR
history. When the fast source has rounded above the current METAR running max,
it records the previous bracket NO opportunity. Optional live mode is restricted
by explicit city allowlist and share caps.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_fast_source_stale_book_observer import (  # noqa: E402
    PM_CLOB_URL,
    bracket_lookup,
    build_market_index,
    fetch_fresh_book,
    latest_orderbook_snapshot,
    latest_paper_snapshot,
    market_city,
    metar_running_max,
    parse_dt,
    safe_float,
)
from scripts.ops.weather_market_proxy import market_proxy_url  # noqa: E402
from weather_data_feed.fast_event_source_policy import load_fast_event_source_profiles  # noqa: E402
from weather_data_feed.observation_sources.fetchers import arith_round  # noqa: E402


RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/fast_source_prev_no_trial"
DEFAULT_HIGH_FREQUENCY_LATEST = RUNTIME_ROOT / "output/high_frequency_observations/latest.json"
DEFAULT_SOURCE_EVENTS_JSONL = RUNTIME_ROOT / "output/source_events/sources.jsonl"

PERSISTENT_CROSS_SOURCES = {
    ("Busan", "amos_runway"),
    ("Helsinki", "fmi"),
    ("Singapore", "singapore_mss"),
}
PERSISTENT_CROSS_POLICY = "two_above_half_latest_above_seven_candidate_v3"


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> dict[str, Any]:
    return read_json(path, {"seen_event_keys": [], "live_order_keys": []})


def source_cross_confirmation(
    *,
    city: str,
    source: str,
    target_date: str,
    source_temp_c: float,
    source_obs_ts_utc: str,
    metar_running_max_c: int,
    candidate_no_bracket_c: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Apply source-specific evidence requirements before declaring a cross."""
    if (city, source) not in PERSISTENT_CROSS_SOURCES:
        return {
            "policy": "arithmetic_round_v1",
            "required_margin_c": 0.5,
            "required_distinct_observations": 1,
            "qualifying_distinct_observations": 1 if arith_round(source_temp_c) > metar_running_max_c else 0,
            "confirmed": arith_round(source_temp_c) > metar_running_max_c,
            "blocker": "" if arith_round(source_temp_c) > metar_running_max_c else "source_not_above_metar_running_max",
        }

    if candidate_no_bracket_c < metar_running_max_c:
        return {
            "policy": PERSISTENT_CROSS_POLICY,
            "basis": "candidate_no_bracket",
            "basis_c": candidate_no_bracket_c,
            "required_margin_c": 0.5,
            "required_distinct_observations": 2,
            "qualifying_distinct_observations": 0,
            "confirmed": False,
            "blocker": "source_not_above_metar_running_max",
        }

    qualifying_margin_c = 0.5
    strong_margin_c = 0.7
    required_observations = 2
    qualifying_threshold_c = candidate_no_bracket_c + qualifying_margin_c
    strong_threshold_c = candidate_no_bracket_c + strong_margin_c
    qualifies = source_temp_c >= qualifying_threshold_c - 1e-9
    latest_is_strong = source_temp_c >= strong_threshold_c - 1e-9
    key = f"{city}|{target_date}|{source}|{candidate_no_bracket_c}"
    previous = dict(state.get(key) or {})
    if previous.get("policy") != PERSISTENT_CROSS_POLICY:
        previous = {}
    if source_obs_ts_utc != previous.get("last_source_obs_ts_utc"):
        previous_count = int(previous.get("qualifying_distinct_observations") or 0)
        count = previous_count + 1 if qualifies and previous.get("last_observation_qualified") else (1 if qualifies else 0)
        previous = {
            "policy": PERSISTENT_CROSS_POLICY,
            "last_source_obs_ts_utc": source_obs_ts_utc,
            "last_observation_qualified": qualifies,
            "qualifying_distinct_observations": count,
            "source_temp_c": source_temp_c,
            "qualifying_threshold_c": qualifying_threshold_c,
            "strong_threshold_c": strong_threshold_c,
            "latest_observation_strong": latest_is_strong,
        }
        state[key] = previous
    count = int(previous.get("qualifying_distinct_observations") or 0)
    confirmed = qualifies and count >= required_observations and latest_is_strong
    blocker = ""
    if not qualifies:
        blocker = "source_cross_margin_not_met"
    elif count < required_observations:
        blocker = "source_cross_persistence_not_met"
    elif not latest_is_strong:
        blocker = "latest_source_cross_strength_not_met"
    return {
        "policy": PERSISTENT_CROSS_POLICY,
        "basis": "candidate_no_bracket",
        "basis_c": candidate_no_bracket_c,
        "required_margin_c": qualifying_margin_c,
        "threshold_c": qualifying_threshold_c,
        "strong_margin_c": strong_margin_c,
        "strong_threshold_c": strong_threshold_c,
        "strong_observation_seen": latest_is_strong,
        "required_distinct_observations": required_observations,
        "qualifying_distinct_observations": count,
        "confirmed": confirmed,
        "blocker": blocker,
    }


def is_definitive_fok_unfilled_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "couldn't be fully filled" in message and "fok" in message


def submit_fok_with_immediate_retries(
    order_row: dict[str, Any],
    *,
    place: Any,
    fetch_book_fn: Any,
    market_proxy: str,
    book_timeout_sec: float,
    max_no_ask: float,
    limit_price_cushion: float,
    immediate_retries: int,
) -> dict[str, Any]:
    """Retry only definitive FOK rejections; ambiguous transport errors must not duplicate orders."""
    working = dict(order_row)
    attempts: list[dict[str, Any]] = []
    total_attempts = 1 + max(0, int(immediate_retries))
    last_error = ""

    for attempt_number in range(1, total_attempts + 1):
        if attempt_number > 1:
            book = fetch_book_fn(
                str(working["token_id"]),
                proxy=market_proxy,
                timeout_sec=float(book_timeout_sec),
                top_n=5,
            )
            summary = book.get("summary") or {}
            best_ask = safe_float(summary.get("best_ask"))
            ask_size = safe_float(summary.get("ask_size"))
            retry_blockers: list[str] = []
            if book.get("status") != "ok":
                retry_blockers.append("fresh_book_not_ok")
            if best_ask is None:
                retry_blockers.append("missing_best_ask")
            elif best_ask > float(max_no_ask):
                retry_blockers.append("ask_above_max")
            if ask_size is None or ask_size < float(working["size"]):
                retry_blockers.append("insufficient_top_ask_size")
            if retry_blockers:
                last_error = "immediate_retry_blocked:" + ",".join(retry_blockers)
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "attempt_ts_utc": iso(),
                        "status": "retry_blocked",
                        "best_ask": best_ask,
                        "ask_size": ask_size,
                        "blockers": retry_blockers,
                        "fresh_book_status": book.get("status"),
                        "fresh_book_error": book.get("error", ""),
                    }
                )
                break
            limit_price = round(min(float(max_no_ask), float(best_ask) + float(limit_price_cushion)), 2)
            working.update(
                {
                    "best_ask": best_ask,
                    "ask_size": ask_size,
                    "fresh_book_status": book.get("status"),
                    "fresh_book_error": book.get("error", ""),
                    "fresh_book_http_status": book.get("http_status"),
                    "fresh_book_proxy_used": book.get("proxy_used", ""),
                    "limit_price": limit_price,
                    "planned_notional_usd": round(float(working["size"]) * float(best_ask), 6),
                    "submitted_notional_usd": round(float(working["size"]) * limit_price, 6),
                }
            )

        attempt = {
            "attempt": attempt_number,
            "attempt_ts_utc": iso(),
            "best_ask": working.get("best_ask"),
            "ask_size": working.get("ask_size"),
            "limit_price": working.get("limit_price"),
        }
        try:
            response = place(working)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            attempt.update(
                {
                    "status": "submit_failed",
                    "error": last_error,
                    "definitive_fok_unfilled": is_definitive_fok_unfilled_error(exc),
                }
            )
            attempts.append(attempt)
            if attempt["definitive_fok_unfilled"] and attempt_number < total_attempts:
                continue
            break
        attempt.update({"status": "submitted", "order_id": response.get("order_id")})
        attempts.append(attempt)
        return {
            "order_row": working,
            "attempts": attempts,
            "exchange_response": response,
            "live_submit_status": "submitted",
            "error": "",
        }

    return {
        "order_row": working,
        "attempts": attempts,
        "exchange_response": None,
        "live_submit_status": "submit_failed",
        "error": last_error,
    }


def latest_source_by_city(path: Path, target_date: str, allowed_sources: set[str], allowed_cities: set[str]) -> dict[str, dict[str, Any]]:
    payload = read_json(path, {})
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("records") or []:
        source = str(row.get("source") or "")
        if allowed_sources and source not in allowed_sources:
            continue
        if str(row.get("target_date") or "") != target_date:
            continue
        city = market_city(str(row.get("city") or ""))
        if allowed_cities and city not in allowed_cities:
            continue
        temp = safe_float(row.get("temp_c"))
        obs_dt = parse_dt(row.get("observation_time_utc"))
        detect_dt = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        if temp is None or obs_dt is None or detect_dt is None:
            continue
        enriched = {
            **row,
            "market_city": city,
            "source": source,
            "source_temp_round_c": arith_round(temp),
            "source_detect_ts_utc": detect_dt.isoformat(),
            "source_obs_ts_utc": obs_dt.isoformat(),
        }
        old = out.get(city)
        old_obs = parse_dt(old.get("source_obs_ts_utc")) if old else None
        old_detect = parse_dt(old.get("source_detect_ts_utc")) if old else None
        if old is None or obs_dt > (old_obs or datetime.min.replace(tzinfo=timezone.utc)) or (
            obs_dt == old_obs and detect_dt > (old_detect or datetime.min.replace(tzinfo=timezone.utc))
        ):
            out[city] = enriched
    return out


def metar_report_clocks(path: Path, target_date: str) -> dict[str, dict[str, Any]]:
    """Infer each city's routine METAR cadence; SPECI does not move the schedule."""
    routine_reports: dict[str, set[datetime]] = {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("target_date") or "") != target_date:
                continue
            report_dt = parse_dt(row.get("source_report_ts_utc"))
            raw_metar = str(row.get("raw_metar") or "").strip().upper()
            if report_dt is None or not raw_metar.startswith("METAR "):
                continue
            city = market_city(str(row.get("city") or ""))
            routine_reports.setdefault(city, set()).add(report_dt)

    clocks: dict[str, dict[str, Any]] = {}
    for city, report_set in routine_reports.items():
        reports = sorted(report_set)
        gaps = [
            (current - previous).total_seconds() / 60.0
            for previous, current in zip(reports, reports[1:])
            if 15.0 <= (current - previous).total_seconds() / 60.0 <= 90.0
        ]
        if len(reports) < 3 or len(gaps) < 2:
            continue
        cadence_min = float(median(gaps[-8:]))
        latest_report = reports[-1]
        clocks[city] = {
            "routine_metar_cadence_min": round(cadence_min, 3),
            "latest_routine_metar_report_ts_utc": latest_report.isoformat(),
            "next_expected_metar_report_ts_utc": (latest_report + timedelta(minutes=cadence_min)).isoformat(),
            "routine_metar_report_count": len(reports),
        }
    return clocks


def next_metar_window_status(
    clock: dict[str, Any] | None,
    now: datetime,
    *,
    window_min: float,
) -> dict[str, Any]:
    next_report = parse_dt((clock or {}).get("next_expected_metar_report_ts_utc"))
    if next_report is None:
        return {
            **(clock or {}),
            "next_metar_window_min": float(window_min),
            "next_metar_window_eligible": False,
            "next_metar_window_blocker": "metar_report_clock_missing",
        }
    minutes_to_next = (next_report - now).total_seconds() / 60.0
    eligible = abs(minutes_to_next) <= float(window_min) + 1e-9
    return {
        **(clock or {}),
        "minutes_to_next_expected_metar": round(minutes_to_next, 3),
        "next_metar_window_distance_min": round(abs(minutes_to_next), 3),
        "next_metar_window_min": float(window_min),
        "next_metar_window_eligible": eligible,
        "next_metar_window_blocker": "" if eligible else "outside_next_metar_execution_window",
    }


def next_metar_burst_cities(opportunity_rows: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(row.get("city") or "")
            for row in opportunity_rows
            if row.get("next_metar_window_eligible") and row.get("city")
        }
    )


def floor_to_places(value: float, places: int) -> float:
    factor = 10**places
    return math.floor(float(value) * factor + 1e-12) / factor


def extract_order_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("order_id", "orderID", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("order_id", "orderID", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def build_live_fok_limit_place_fn(proxy_url: str):
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderType
        from py_clob_client_v2.constants import POLYGON
        import py_clob_client_v2.http_helpers.helpers as clob_http_helpers

        order_args_cls = OrderArgsV2
        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.constants import POLYGON
        import py_clob_client.http_helpers.helpers as clob_http_helpers

        order_args_cls = OrderArgs
        clob_v2 = False

    if proxy_url:
        clob_http_helpers._http_client = httpx.Client(http2=True, proxy=proxy_url, timeout=5.0)  # noqa: SLF001
    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or PM_CLOB_URL
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    funder = os.getenv("PM_ADDRESS", "").strip()
    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0
    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(host, chain_id=chain_id, key=private_key, creds=creds, signature_type=signature_type, funder=funder or None)
    if creds is None:
        if clob_v2:
            client.set_api_creds(client.derive_api_key())
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

    def place(row: dict[str, Any]) -> dict[str, Any]:
        signed_order = client.create_order(
            order_args_cls(
                token_id=str(row["token_id"]),
                price=float(row["limit_price"]),
                size=float(row["size"]),
                side="BUY",
            )
        )
        if clob_v2:
            response = client.post_order(signed_order, order_type=OrderType.FOK)
        else:
            response = client.post_order(signed_order, orderType=OrderType.FOK)
        return {
            "place": response,
            "order_id": extract_order_id(response),
            "clob_client": "py_clob_client_v2" if clob_v2 else "py_clob_client",
            "order_type": "FOK",
            "order_mode": "limit_buy_shares",
            "signature_type": signature_type,
            "funder": funder,
            "signer": signer_addr,
            "clob_proxy_enabled": bool(proxy_url),
        }

    return place


def spent_market_shares(path: Path, *, target_date: str, token_id: str) -> float:
    total = 0.0
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            row.get("target_date") == target_date
            and str(row.get("token_id") or "") == token_id
            and row.get("live_submit_status") == "submitted"
        ):
            total += float(row.get("size") or 0.0)
    return round(total, 6)


def parse_city_float_overrides(raw_items: list[str] | None, *, arg_name: str) -> dict[str, float]:
    overrides: dict[str, float] = {}
    for raw in raw_items or []:
        for item in str(raw).replace(",", " ").split():
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"{arg_name} entries must be City=value, got {item!r}")
            city_raw, value_raw = item.split("=", 1)
            city = market_city(city_raw.strip())
            if not city:
                raise ValueError(f"{arg_name} entry has empty city: {item!r}")
            try:
                value = float(value_raw)
            except ValueError as exc:
                raise ValueError(f"{arg_name} entry has invalid value: {item!r}") from exc
            if value <= 0:
                raise ValueError(f"{arg_name} entry must be positive: {item!r}")
            overrides[city] = value
    return overrides


def run_once(args: argparse.Namespace, live_place_cache: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out_dir = Path(args.output_dir)
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    seen = set(state.get("seen_event_keys") or [])
    live_order_keys = set(state.get("live_order_keys") or [])
    source_cross_confirmation_state = dict(state.get("source_cross_confirmation") or {})
    target_date = args.target_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    all_cities = set(args.live_cities or []) | set(args.shadow_cities or [])
    allowed_sources = set(args.sources or [])
    market_proxy = market_proxy_url(args.market_proxy or None)
    max_shares_per_trade_by_city = parse_city_float_overrides(
        args.max_shares_per_trade_by_city,
        arg_name="--max-shares-per-trade-by-city",
    )
    max_shares_per_market_by_city = parse_city_float_overrides(
        args.max_shares_per_market_by_city,
        arg_name="--max-shares-per-market-by-city",
    )

    source_rows = latest_source_by_city(Path(args.high_frequency_latest), target_date, allowed_sources, all_cities)
    fast_profiles = load_fast_event_source_profiles()
    city_profiles = {
        profile.city: profile
        for profile in fast_profiles.values()
        if profile.collector_enabled and profile.city in all_cities
    }
    metar_rows_by_date = metar_running_max(Path(args.source_events_jsonl), target_date, city_profiles, now)
    metar_rows = {
        city: row
        for (city, row_target_date), row in metar_rows_by_date.items()
        if row_target_date == target_date
    }
    metar_clocks = metar_report_clocks(Path(args.source_events_jsonl), target_date)
    paper_path = latest_paper_snapshot()
    orderbook_path = latest_orderbook_snapshot()
    market_index = build_market_index(paper_path, {target_date})

    event_rows: list[dict[str, Any]] = []
    opportunity_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []

    for city in sorted(all_cities):
        src = source_rows.get(city)
        metar = metar_rows.get(city)
        base = {
            "schema_version": "fast_source_prev_no_trial_v1",
            "ts_utc": iso(now),
            "city": city,
            "target_date": target_date,
            "mode": "live" if city in set(args.live_cities or []) else "shadow",
            "high_frequency_latest": str(args.high_frequency_latest),
            "source_events_jsonl": str(args.source_events_jsonl),
            "paper_snapshot_path": str(paper_path) if paper_path else "",
            "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        }
        if not src:
            opportunity_rows.append({**base, "status": "source_missing"})
            continue
        if not metar:
            opportunity_rows.append({**base, "status": "metar_missing", "source": src.get("source")})
            continue
        source_obs_dt = parse_dt(src.get("source_obs_ts_utc"))
        source_detect_dt = parse_dt(src.get("source_detect_ts_utc"))
        latest_metar_report_dt = parse_dt(metar.get("latest_report_ts_utc"))
        source_obs_lag_min = (now - source_obs_dt).total_seconds() / 60.0 if source_obs_dt else None
        source_detect_age_min = (now - source_detect_dt).total_seconds() / 60.0 if source_detect_dt else None
        source_round = int(src["source_temp_round_c"])
        metar_max = int(metar["metar_running_max_round_c"])
        t_minus_1 = source_round - 1
        next_metar_window = next_metar_window_status(
            metar_clocks.get(city),
            now,
            window_min=float(args.next_metar_window_min),
        )
        cross_confirmation = source_cross_confirmation(
            city=city,
            source=str(src.get("source") or ""),
            target_date=target_date,
            source_temp_c=float(src["temp_c"]),
            source_obs_ts_utc=str(src.get("source_obs_ts_utc") or ""),
            metar_running_max_c=metar_max,
            candidate_no_bracket_c=t_minus_1,
            state=source_cross_confirmation_state,
        )
        common = {
            **base,
            "source": src.get("source"),
            "station": src.get("station"),
            "source_kind": src.get("source_kind"),
            "source_obs_ts_utc": src.get("source_obs_ts_utc"),
            "source_detect_ts_utc": src.get("source_detect_ts_utc"),
            "source_age_min": round(source_obs_lag_min, 3) if source_obs_lag_min is not None else None,
            "source_obs_lag_min": round(source_obs_lag_min, 3) if source_obs_lag_min is not None else None,
            "source_detect_age_min": round(source_detect_age_min, 3) if source_detect_age_min is not None else None,
            "source_temp_c": src.get("temp_c"),
            "source_round_c": source_round,
            "latest_metar_report_ts_utc": metar.get("latest_report_ts_utc"),
            "latest_metar_detect_ts_utc": metar.get("latest_detect_ts_utc"),
            "latest_metar_temp_c": metar.get("latest_metar_temp_c"),
            "latest_metar_round_c": metar.get("latest_metar_round_c"),
            "metar_running_max_round_c": metar_max,
            "metar_running_max_temp_c": metar.get("metar_running_max_temp_c"),
            "t_minus_1_no_bracket_c": t_minus_1,
            **next_metar_window,
            "source_cross_policy": cross_confirmation["policy"],
            "source_cross_confirmation_basis": cross_confirmation.get("basis", "metar_running_max"),
            "source_cross_confirmation_basis_c": cross_confirmation.get("basis_c", metar_max),
            "source_cross_required_margin_c": cross_confirmation["required_margin_c"],
            "source_cross_threshold_c": cross_confirmation.get("threshold_c"),
            "source_cross_strong_margin_c": cross_confirmation.get("strong_margin_c"),
            "source_cross_strong_threshold_c": cross_confirmation.get("strong_threshold_c"),
            "source_cross_strong_observation_seen": cross_confirmation.get("strong_observation_seen"),
            "source_cross_required_distinct_observations": cross_confirmation["required_distinct_observations"],
            "source_cross_qualifying_distinct_observations": cross_confirmation["qualifying_distinct_observations"],
            "source_cross_confirmed": cross_confirmation["confirmed"],
        }
        blockers: list[str] = []
        if cross_confirmation["blocker"]:
            blockers.append(cross_confirmation["blocker"])
        if source_detect_age_min is None or source_detect_age_min > float(args.max_source_age_min):
            blockers.append("source_too_old")
        if source_obs_dt and latest_metar_report_dt and source_obs_dt <= latest_metar_report_dt:
            blockers.append("source_not_after_latest_metar")
        if next_metar_window["next_metar_window_blocker"]:
            blockers.append(str(next_metar_window["next_metar_window_blocker"]))
        if source_detect_dt and latest_metar_report_dt:
            common["source_obs_after_latest_metar_report_sec"] = round((source_obs_dt - latest_metar_report_dt).total_seconds(), 3) if source_obs_dt else None
        if blockers:
            opportunity_rows.append({**common, "status": "blocked", "blockers": blockers})
            continue

        event_key = "|".join([city, target_date, str(src.get("source")), str(src.get("source_obs_ts_utc")), str(source_round), str(metar_max), str(t_minus_1)])
        token = bracket_lookup(market_index, city, target_date, t_minus_1)
        if token is None:
            opportunity_rows.append({**common, "status": "missing_t_minus_1_market", "event_key": event_key})
            continue
        city_max_shares_per_trade = max_shares_per_trade_by_city.get(city, float(args.max_shares_per_trade))
        city_max_shares_per_market = max_shares_per_market_by_city.get(city, float(args.max_shares_per_market))
        book = fetch_fresh_book(token.no_token_id, proxy=market_proxy, timeout_sec=float(args.book_timeout_sec), top_n=5)
        summary = book.get("summary") or {}
        best_ask = safe_float(summary.get("best_ask"))
        ask_size = safe_float(summary.get("ask_size"))
        live_blockers: list[str] = []
        if book.get("status") != "ok":
            live_blockers.append("fresh_book_not_ok")
        if best_ask is None:
            live_blockers.append("missing_best_ask")
        elif best_ask > float(args.max_no_ask):
            live_blockers.append("ask_above_max")
        if ask_size is None or ask_size < city_max_shares_per_trade:
            live_blockers.append("insufficient_top_ask_size")
        market_spent = spent_market_shares(out_dir / "orders.jsonl", target_date=target_date, token_id=token.no_token_id)
        if market_spent + city_max_shares_per_trade > city_max_shares_per_market + 1e-9:
            live_blockers.append("market_share_cap")
        if args.live and city in set(args.live_cities or []) and not args.confirm_live:
            live_blockers.append("confirm_live_missing")
        opportunity = {
            **common,
            "status": "cross_candidate",
            "event_key": event_key,
            "question": token.question,
            "market_id": token.market_id,
            "condition_id": token.condition_id,
            "token_id": token.no_token_id,
            "best_ask": best_ask,
            "ask_size": ask_size,
            "fresh_book_status": book.get("status"),
            "fresh_book_error": book.get("error", ""),
            "fresh_book_http_status": book.get("http_status"),
            "fresh_book_proxy_used": book.get("proxy_used", ""),
            "max_no_ask": float(args.max_no_ask),
            "planned_shares": city_max_shares_per_trade,
            "market_spent_shares": market_spent,
            "max_shares_per_market": city_max_shares_per_market,
            "planned_notional_usd": round(city_max_shares_per_trade * float(best_ask or 0.0), 6),
            "live_requested": bool(args.live and city in set(args.live_cities or [])),
            "live_enabled": bool(args.live and args.confirm_live and city in set(args.live_cities or [])),
            "live_blockers": live_blockers,
        }
        opportunity_rows.append(opportunity)
        if event_key not in seen:
            append_jsonl(out_dir / "events.jsonl", opportunity)
            seen.add(event_key)
            event_rows.append(opportunity)
        live_key = "|".join([city, target_date, str(t_minus_1), str(token.no_token_id), str(src.get("source_obs_ts_utc"))])
        if args.live and args.confirm_live and city in set(args.live_cities or []) and not live_blockers and live_key not in live_order_keys:
            # Polymarket FOK BUY conserves USDC (makerAmount = size * limit_price), so pricing
            # the order at the loose max_no_ask ceiling overspends and overbuys shares whenever
            # the book is cheaper than the ceiling. Price at the current best ask plus a small
            # cushion (so the FOK still clears on a minor uptick), capped by max_no_ask, to buy
            # ~size shares instead of ceiling/best_ask times as many.
            limit_price = round(min(float(args.max_no_ask), float(best_ask) + float(args.limit_price_cushion)), 2)
            order_row = {
                **opportunity,
                "order_side": "BUY",
                "limit_price": limit_price,
                "size": floor_to_places(city_max_shares_per_trade, 2),
                "submitted_notional_usd": round(city_max_shares_per_trade * limit_price, 6),
                "limit_price_policy": "best_ask_plus_cushion_capped_by_max_no_ask",
                "live_attempted": True,
                "live_attempt_ts_utc": iso(),
            }
            if "place" not in live_place_cache:
                live_place_cache["place"] = build_live_fok_limit_place_fn(market_proxy)
            result = submit_fok_with_immediate_retries(
                order_row,
                place=live_place_cache["place"],
                fetch_book_fn=fetch_fresh_book,
                market_proxy=market_proxy,
                book_timeout_sec=float(args.book_timeout_sec),
                max_no_ask=float(args.max_no_ask),
                limit_price_cushion=float(args.limit_price_cushion),
                immediate_retries=int(args.fok_immediate_retries),
            )
            order_row = result["order_row"]
            order_row["fok_retry_policy"] = "immediate_definitive_unfilled_only_v1"
            order_row["fok_immediate_retries_configured"] = int(args.fok_immediate_retries)
            order_row["fok_attempt_count"] = len(result["attempts"])
            order_row["fok_attempts"] = result["attempts"]
            order_row["live_submit_status"] = result["live_submit_status"]
            if result["exchange_response"] is not None:
                response = result["exchange_response"]
                order_row["exchange_response"] = response
                order_row["order_id"] = response.get("order_id")
                live_order_keys.add(live_key)
            if result["error"]:
                order_row["error"] = result["error"]
            append_jsonl(out_dir / "orders.jsonl", order_row)
            order_rows.append(order_row)

    for row in opportunity_rows:
        append_jsonl(out_dir / "opportunities.jsonl", row)
    state = {
        "updated_at_utc": iso(),
        "target_date": target_date,
        "seen_event_keys": sorted(seen)[-5000:],
        "live_order_keys": sorted(live_order_keys)[-5000:],
        "source_cross_confirmation": source_cross_confirmation_state,
    }
    write_json(state_path, state)
    burst_cities = next_metar_burst_cities(opportunity_rows)
    effective_interval_sec = float(args.burst_interval_sec) if burst_cities else float(args.interval_sec)
    latest = {
        "status": "ok",
        "schema_version": "fast_source_prev_no_trial_latest_v1",
        "generated_at_utc": iso(),
        "strategy_id": "fast_source_prev_no_trial_v1",
        "strategy_instance": "fast_source_prev_no_trial_v1",
        "target_date": target_date,
        "sources": sorted(allowed_sources),
        "live_cities": args.live_cities or [],
        "shadow_cities": args.shadow_cities or [],
        "live_enabled": bool(args.live and args.confirm_live and args.live_cities),
        "caps": {
            "max_shares_per_trade": float(args.max_shares_per_trade),
            "max_shares_per_market": float(args.max_shares_per_market),
            "max_shares_per_trade_by_city": max_shares_per_trade_by_city,
            "max_shares_per_market_by_city": max_shares_per_market_by_city,
            "max_no_ask": float(args.max_no_ask),
            "max_source_age_min": float(args.max_source_age_min),
            "next_metar_window_min": float(args.next_metar_window_min),
            "fok_immediate_retries": int(args.fok_immediate_retries),
        },
        "source_cities": sorted(source_rows),
        "metar_cities": sorted(metar_rows),
        "events": len(event_rows),
        "opportunities": len(opportunity_rows),
        "candidate_rows": len(opportunity_rows),
        "execution_eligible": len(order_rows),
        "live_orders_attempted": len(order_rows),
        "live_orders_submitted": sum(1 for row in order_rows if row.get("live_submit_status") == "submitted"),
        "polling": {
            "base_interval_sec": float(args.interval_sec),
            "burst_interval_sec": float(args.burst_interval_sec),
            "effective_interval_sec": effective_interval_sec,
            "burst_active": bool(burst_cities),
            "burst_cities": burst_cities,
        },
        "paper_snapshot_path": str(paper_path) if paper_path else "",
        "orderbook_snapshot_path": str(orderbook_path) if orderbook_path else "",
        "latest_opportunities": opportunity_rows[-20:],
    }
    write_json(out_dir / "latest.json", latest)
    write_json(out_dir / "latest_summary.json", latest)
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--high-frequency-latest", default=str(DEFAULT_HIGH_FREQUENCY_LATEST))
    parser.add_argument("--source-events-jsonl", default=str(DEFAULT_SOURCE_EVENTS_JSONL))
    parser.add_argument(
        "--sources",
        nargs="*",
        default=[
            "jma_amedas",
            "singapore_mss",
            "fmi",
            "amos_runway",
            "noaa_madis_hfmetar",
            "hko_obs",
            "cowin_obs",
            "mgm",
            "ims_lod",
        ],
    )
    parser.add_argument("--live-cities", nargs="*", default=[])
    parser.add_argument(
        "--shadow-cities",
        nargs="*",
        default=[
            "Tokyo",
            "Singapore",
            "Helsinki",
            "Busan",
            "Seoul",
            "HongKong",
            "Shenzhen",
            "TelAviv",
            "Ankara",
            "Istanbul",
            "LA",
            "Dallas",
            "Houston",
            "SanFrancisco",
            "NYC",
            "Atlanta",
            "Austin",
            "Chicago",
            "Miami",
            "Seattle",
        ],
    )
    parser.add_argument("--max-shares-per-trade", type=float, default=5.0)
    parser.add_argument("--max-shares-per-market", type=float, default=5.0)
    parser.add_argument("--max-shares-per-trade-by-city", action="append", default=[])
    parser.add_argument("--max-shares-per-market-by-city", action="append", default=[])
    parser.add_argument("--max-shares-per-city-day", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--max-no-ask", type=float, default=0.92)
    parser.add_argument(
        "--limit-price-cushion",
        type=float,
        default=0.02,
        help="Added to best_ask to form the FOK BUY limit price (capped by --max-no-ask). Keeps filled shares near size instead of ceiling/ask times as many.",
    )
    parser.add_argument("--max-source-age-min", type=float, default=15.0)
    parser.add_argument("--next-metar-window-min", type=float, default=20.0)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument("--fok-immediate-retries", type=int, default=2)
    parser.add_argument("--market-proxy", default=market_proxy_url(None))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--prebuild-live-client", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--burst-interval-sec", type=float, default=10.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    live_place_cache: dict[str, Any] = {}
    if args.live and args.confirm_live and args.prebuild_live_client:
        live_place_cache["place"] = build_live_fok_limit_place_fn(market_proxy_url(args.market_proxy or None))
    while True:
        started = time.monotonic()
        latest = run_once(args, live_place_cache)
        print(json.dumps({k: v for k, v in latest.items() if k != "latest_opportunities"}, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        effective_interval_sec = float((latest.get("polling") or {}).get("effective_interval_sec") or args.interval_sec)
        time.sleep(max(5.0, effective_interval_sec - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
