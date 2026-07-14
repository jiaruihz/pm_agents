#!/usr/bin/env python3
"""Live HKO official-source T-1 NO executor.

Hong Kong resolves from HKO's Daily Extract using floor(decimal daily max).
Once the HKO Observatory has printed a temperature whose floor is T, the
T-1 exact-high NO contract is structurally locked. This runner intentionally
does not use VHHH/METAR and only trades fresh, capped CLOB asks.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_fast_source_execution import (
    build_live_fok_limit_place_fn,
    spent_market_shares,
    submit_fok_with_immediate_retries,
)
from scripts.ops.weather_fast_source_stale_book_observer import (
    HIGH_FREQUENCY_JSONL,
    augment_market_index_from_gamma,
    bracket_lookup,
    build_market_index,
    fetch_fresh_book,
    latest_paper_snapshot,
    market_city,
    parse_dt,
    safe_float,
)
from scripts.ops.weather_market_proxy import market_proxy_url


RUNTIME_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_RUNTIME_ROOT", "/Volumes/jrs/weather_data_feed_service_runtime"))
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/hko_official_tminus1_no_live"
STRATEGY_ID = "hko_official_tminus1_no_live_v1"


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def default_target_date() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()


def event_slug(target_date: str) -> str:
    day = datetime.fromisoformat(target_date).date()
    return f"highest-temperature-in-hong-kong-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"


def first_seen_hko_crosses(path: Path, target_date: str) -> list[dict[str, Any]]:
    """Return first-seen observations that establish a new floor bracket."""
    first_seen: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            market_city(str(row.get("city") or "")) != "HongKong"
            or row.get("source") != "hko_obs"
            or str(row.get("target_date") or "") != target_date
            or str(row.get("station") or "") != "HK Observatory"
        ):
            continue
        obs = parse_dt(row.get("observation_time_utc"))
        detect = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        temp = safe_float(row.get("temp_c"))
        if obs is None or detect is None or temp is None:
            continue
        key = obs.isoformat()
        old = first_seen.get(key)
        old_detect = parse_dt(old.get("local_detect_ts_utc") or old.get("fetched_at_utc")) if old else None
        if old is None or (old_detect is not None and detect < old_detect):
            first_seen[key] = row

    running_floor: int | None = None
    crosses: list[dict[str, Any]] = []
    for row in sorted(first_seen.values(), key=lambda item: parse_dt(item.get("local_detect_ts_utc") or item.get("fetched_at_utc")) or datetime.max.replace(tzinfo=timezone.utc)):
        temp = float(row["temp_c"])
        bracket = int(math.floor(temp))
        if running_floor is None or bracket > running_floor:
            crosses.append({
                **row,
                "market_city": "HongKong",
                "source_floor_bracket_c": bracket,
                "t_minus_1_no_bracket_c": bracket - 1,
                "source_obs_ts_utc": row["observation_time_utc"],
                "source_detect_ts_utc": row.get("local_detect_ts_utc") or row.get("fetched_at_utc"),
            })
            running_floor = bracket
    return crosses


def run_once(args: argparse.Namespace, live_client: dict[str, Any]) -> dict[str, Any]:
    target_date = args.target_date or default_target_date()
    out_dir = Path(args.output_dir)
    state_path = out_dir / "state.json"
    state = read_json(state_path, {"seen_event_keys": [], "live_order_keys": []})
    seen = set(state.get("seen_event_keys") or [])
    live_order_keys = set(state.get("live_order_keys") or [])
    now = datetime.now(timezone.utc)
    proxy = market_proxy_url(args.market_proxy or None)
    paper_path = latest_paper_snapshot()
    market_index = build_market_index(paper_path, {target_date})
    market_index = augment_market_index_from_gamma(
        market_index,
        target_dates={target_date},
        cities={"HongKong"},
        event_slugs={"HongKong": event_slug(target_date)},
        market_proxy=proxy,
    )

    events: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    for source in first_seen_hko_crosses(Path(args.high_frequency_jsonl), target_date):
        detect = parse_dt(source["source_detect_ts_utc"])
        obs = parse_dt(source["source_obs_ts_utc"])
        source_age_min = (now - obs).total_seconds() / 60.0 if obs else None
        detect_age_min = (now - detect).total_seconds() / 60.0 if detect else None
        source_observation_lag_min = (detect - obs).total_seconds() / 60.0 if obs and detect else None
        bracket = int(source["source_floor_bracket_c"])
        t_minus_1 = int(source["t_minus_1_no_bracket_c"])
        event_key = "|".join(["HongKong", target_date, "hko_obs", str(source["source_obs_ts_utc"]), str(bracket), str(t_minus_1)])
        base = {
            "schema_version": "hko_official_tminus1_no_live_v1",
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_ID,
            "execution_policy": "hko_official_tminus1_no_fok",
            "created_at_utc": iso(),
            "target_date": target_date,
            "city": "HongKong",
            "unit": "C",
            "source": "hko_obs",
            "station": "HK Observatory",
            "settlement_source_class": "special_source_confirmed",
            "settlement_source": "HKO Daily Extract Absolute Daily Max",
            "mapping_rule": "floor decimal daily max to integer bracket",
            "signal_type": "official_hko_tminus1_no_lock",
            "source_obs_ts_utc": source["source_obs_ts_utc"],
            "source_detect_ts_utc": source["source_detect_ts_utc"],
            "source_temp_c": source.get("temp_c"),
            "source_floor_bracket_c": bracket,
            "t_minus_1_no_bracket_c": t_minus_1,
            "event_key": event_key,
            "source_obs_age_min": round(source_age_min, 3) if source_age_min is not None else None,
            "source_detect_age_min": round(detect_age_min, 3) if detect_age_min is not None else None,
            "source_observation_lag_min": round(source_observation_lag_min, 3) if source_observation_lag_min is not None else None,
            "lock_reason": "HKO observed floor(T); final HKO daily max cannot be below T, so exact T-1 NO cannot lose",
        }
        blockers: list[str] = []
        if detect_age_min is None or detect_age_min > args.max_source_detect_age_min:
            blockers.append("source_detect_too_old")
        if source_observation_lag_min is None or source_observation_lag_min > args.max_source_observation_lag_min:
            blockers.append("source_observation_delay_too_high")
        token = bracket_lookup(market_index, "HongKong", target_date, t_minus_1)
        if token is None:
            blockers.append("missing_t_minus_1_market")
            opportunity = {**base, "status": "blocked", "blockers": blockers}
        else:
            book = fetch_fresh_book(token.no_token_id, proxy=proxy, timeout_sec=args.book_timeout_sec, top_n=5)
            summary = book.get("summary") or {}
            bid = safe_float(summary.get("best_bid"))
            bid_size = safe_float(summary.get("bid_size"))
            ask = safe_float(summary.get("best_ask"))
            ask_size = safe_float(summary.get("ask_size"))
            if book.get("status") != "ok":
                blockers.append("fresh_book_not_ok")
            if ask is None:
                blockers.append("missing_best_ask")
            elif ask > args.max_no_ask:
                blockers.append("ask_above_max")
            if ask_size is None or ask_size < args.shares:
                blockers.append("insufficient_top_ask_size")
            already_spent = spent_market_shares(
                out_dir / "orders.jsonl",
                target_date=target_date,
                token_id=token.no_token_id,
            )
            if already_spent + args.shares > args.max_shares_per_market + 1e-9:
                blockers.append("market_share_cap")
            opportunity = {
                **base,
                "status": "lock_candidate",
                "question": token.question,
                "market_id": token.market_id,
                "condition_id": token.condition_id,
                "token_id": token.no_token_id,
                "order_side": "BUY",
                "outcome": "NO",
                "best_ask": ask,
                "ask_size": ask_size,
                "best_bid": bid,
                "bid_size": bid_size,
                "book_liquidity_state": (
                    "fetch_failed"
                    if book.get("status") != "ok"
                    else "empty_no_ask_side"
                    if ask is None
                    else "two_sided"
                    if bid is not None
                    else "ask_only"
                ),
                "fresh_book_status": book.get("status"),
                "fresh_book_http_status": book.get("http_status"),
                "fresh_book_error": book.get("error", ""),
                "fresh_book_proxy_used": book.get("proxy_used", ""),
                "max_no_ask": args.max_no_ask,
                "planned_shares": args.shares,
                "planned_notional_usd": round(args.shares * float(ask or 0.0), 6),
                "market_spent_shares": already_spent,
                "max_shares_per_market": args.max_shares_per_market,
                "live_requested": bool(args.live),
                "live_enabled": bool(args.live and args.confirm_live),
                "live_blockers": blockers,
            }

        opportunities.append(opportunity)
        if event_key not in seen:
            append_jsonl(out_dir / "events.jsonl", opportunity)
            seen.add(event_key)
            events.append(opportunity)

        if token is None:
            continue
        live_key = "|".join([target_date, str(t_minus_1), token.no_token_id])
        if not (args.live and args.confirm_live) or blockers or live_key in live_order_keys:
            continue
        order_row = {
            **opportunity,
            "limit_price": float(ask),
            "size": args.shares,
            "desired_shares": args.shares,
            "submitted_notional_usd": round(args.shares * float(ask), 6),
            "limit_price_policy": "exact_live_best_ask_v1",
            "live_attempted": True,
            "live_attempt_ts_utc": iso(),
        }
        if "place" not in live_client:
            live_client["place"] = build_live_fok_limit_place_fn(proxy)
        result = submit_fok_with_immediate_retries(
            order_row,
            place=live_client["place"],
            fetch_book_fn=fetch_fresh_book,
            market_proxy=proxy,
            book_timeout_sec=args.book_timeout_sec,
            max_no_ask=args.max_no_ask,
            immediate_retries=args.fok_immediate_retries,
        )
        order_row = result["order_row"]
        order_row.update(
            {
                "fok_retry_policy": "immediate_definitive_unfilled_only_v2",
                "fok_immediate_retries_configured": args.fok_immediate_retries,
                "fok_attempt_count": len(result["attempts"]),
                "fok_attempts": result["attempts"],
                "live_submit_status": result["live_submit_status"],
                "actual_fill_shares": result["actual_fill_shares"],
                "actual_fill_cost_usd": result["actual_fill_cost_usd"],
            }
        )
        if result["exchange_response"] is not None:
            response = result["exchange_response"]
            order_row["exchange_response"] = response
            order_row["order_id"] = response.get("order_id")
            live_order_keys.add(live_key)
        if result["error"]:
            order_row["error"] = result["error"]
        append_jsonl(out_dir / "orders.jsonl", order_row)
        orders.append(order_row)

    for row in opportunities:
        append_jsonl(out_dir / "opportunities.jsonl", row)
    state = {"updated_at_utc": iso(), "target_date": target_date, "seen_event_keys": sorted(seen)[-5000:], "live_order_keys": sorted(live_order_keys)[-5000:]}
    write_json(state_path, state)
    latest = {
        "status": "ok",
        "schema_version": "hko_official_tminus1_no_live_latest_v1",
        "generated_at_utc": iso(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_ID,
        "target_date": target_date,
        "city": "HongKong",
        "source": "hko_obs",
        "settlement_source": "HKO Daily Extract Absolute Daily Max",
        "execution_mode": "live" if args.live and args.confirm_live else "shadow",
        "live_enabled": bool(args.live and args.confirm_live),
        "caps": {"shares_per_trade": args.shares, "max_shares_per_market": args.max_shares_per_market, "max_no_ask": args.max_no_ask, "max_source_detect_age_min": args.max_source_detect_age_min, "max_source_observation_lag_min": args.max_source_observation_lag_min, "fok_immediate_retries": args.fok_immediate_retries},
        "events": len(events),
        "candidate_rows": len(opportunities),
        "live_orders_attempted": len(orders),
        "live_orders_submitted": sum(row.get("live_submit_status") == "submitted" for row in orders),
        "latest_opportunities": opportunities[-20:],
    }
    write_json(out_dir / "latest.json", latest)
    write_json(out_dir / "latest_summary.json", latest)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default="")
    parser.add_argument("--high-frequency-jsonl", default=str(HIGH_FREQUENCY_JSONL))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--market-proxy", default="")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--max-source-detect-age-min", type=float, default=5.0)
    parser.add_argument("--max-source-observation-lag-min", type=float, default=30.0)
    parser.add_argument("--book-timeout-sec", type=float, default=5.0)
    parser.add_argument("--fok-immediate-retries", type=int, default=2)
    parser.add_argument("--shares", type=float, default=5.0)
    parser.add_argument("--max-shares-per-market", type=float, default=5.0)
    parser.add_argument("--max-no-ask", type=float, default=0.93)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if args.live and not args.confirm_live:
        raise SystemExit("--live requires --confirm-live")
    if args.shares <= 0 or args.max_shares_per_market < args.shares:
        raise SystemExit("shares must be positive and not exceed max-shares-per-market")
    live_client: dict[str, Any] = {}
    while True:
        started = time.monotonic()
        latest = run_once(args, live_client)
        print(json.dumps({key: value for key, value in latest.items() if key != "latest_opportunities"}, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(5.0, args.interval_sec - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
