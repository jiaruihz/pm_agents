#!/usr/bin/env python3
"""Profile peer weather wallets with leaderboard PnL and recent public activity."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from research_external_wallet_strategy_v1 import DATA_API, _is_weather, _price_band


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        backoff_factor=0.8,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def get_rows(
    session: requests.Session,
    path: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    response = session.get(
        f"{DATA_API}{path}",
        params=params,
        headers={"Accept": "application/json", "User-Agent": "pm-agent-weather-peer-scan/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def leaderboard_row(
    session: requests.Session,
    wallet: str,
    period: str,
) -> dict[str, Any] | None:
    rows = get_rows(
        session,
        "/v1/leaderboard",
        {
            "category": "WEATHER",
            "timePeriod": period,
            "orderBy": "PNL",
            "user": wallet,
            "limit": 1,
        },
    )
    if not rows:
        return None
    row = rows[0]
    volume = float(row.get("vol") or 0)
    pnl = float(row.get("pnl") or 0)
    return {
        "rank": int(row["rank"]) if str(row.get("rank") or "").isdigit() else row.get("rank"),
        "name": row.get("userName") or "",
        "volume": round(volume, 6),
        "pnl": round(pnl, 6),
        "pnl_over_volume": round(pnl / volume, 6) if volume else None,
    }


def normalize_wallet(value: Any) -> str:
    wallet = str(value or "").strip().lower()
    return wallet if wallet.startswith("0x") and len(wallet) == 42 else ""


def discover_weather_leaderboard_wallets(
    session: requests.Session,
    *,
    rows_per_period: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return a stable union of current WEATHER leaderboard wallets.

    Discovery stays separate from the later replication screen: every fetched
    leaderboard row is retained so that excluded large/high-frequency wallets
    remain visible in the denominator.
    """

    raw_rows: list[dict[str, Any]] = []
    by_wallet: dict[str, dict[str, Any]] = {}
    for period in ("ALL", "MONTH", "WEEK"):
        for offset in range(0, rows_per_period, 50):
            limit = min(50, rows_per_period - offset)
            page = get_rows(
                session,
                "/v1/leaderboard",
                {
                    "category": "WEATHER",
                    "timePeriod": period,
                    "orderBy": "PNL",
                    "limit": limit,
                    "offset": offset,
                },
            )
            if not page:
                break
            for source in page:
                wallet = normalize_wallet(source.get("proxyWallet") or source.get("wallet"))
                if not wallet:
                    continue
                row = {
                    "period": period.lower(),
                    "wallet": wallet,
                    "rank": source.get("rank"),
                    "name": source.get("userName") or source.get("name") or "",
                    "pnl": float(source.get("pnl") or 0),
                    "volume": float(source.get("vol") or 0),
                }
                raw_rows.append(row)
                state = by_wallet.setdefault(
                    wallet,
                    {"periods": set(), "best_rank": 10**9, "max_pnl": 0.0},
                )
                state["periods"].add(period.lower())
                try:
                    state["best_rank"] = min(state["best_rank"], int(source.get("rank")))
                except (TypeError, ValueError):
                    pass
                state["max_pnl"] = max(state["max_pnl"], float(source.get("pnl") or 0))
            if len(page) < limit:
                break
    wallets = sorted(
        by_wallet,
        key=lambda wallet: (
            -len(by_wallet[wallet]["periods"]),
            by_wallet[wallet]["best_rank"],
            -by_wallet[wallet]["max_pnl"],
            wallet,
        ),
    )
    return wallets, raw_rows


def recent_activity(
    session: requests.Session,
    wallet: str,
    *,
    max_rows: int = 5_500,
) -> tuple[list[dict[str, Any]], int]:
    raw: list[dict[str, Any]] = []
    for offset in range(0, max_rows, 500):
        page = get_rows(
            session,
            "/activity",
            {
                "user": wallet,
                "limit": 500,
                "offset": offset,
                "sortDirection": "DESC",
            },
        )
        if not page:
            break
        raw.extend(page)
        if len(page) < 500:
            break
    return [row for row in raw if _is_weather(row)], len(raw)


def activity_profile(rows: list[dict[str, Any]], raw_rows: int) -> dict[str, Any]:
    trades = [row for row in rows if row.get("type") == "TRADE"]
    buys = [row for row in trades if row.get("side") == "BUY"]
    cost = sum(float(row.get("usdcSize") or 0) for row in buys)
    bands: dict[str, float] = defaultdict(float)
    outcomes: dict[str, float] = defaultdict(float)
    for row in buys:
        value = float(row.get("usdcSize") or 0)
        bands[_price_band(float(row.get("price") or 0))] += value
        outcomes[str(row.get("outcome") or "unknown")] += value
    events = {
        str(row.get("eventSlug") or row.get("slug") or "")
        for row in rows
        if row.get("eventSlug") or row.get("slug")
    }
    buy_sizes = [float(row.get("usdcSize") or 0) for row in buys]
    timestamps = [int(row.get("timestamp") or 0) for row in rows if row.get("timestamp")]
    return {
        "raw_wallet_rows_examined": raw_rows,
        "weather_rows": len(rows),
        "weather_trade_rows": len(trades),
        "weather_events": len(events),
        "weather_trades_per_event": round(len(trades) / len(events), 6) if events else None,
        "weather_buy_rows": len(buys),
        "buy_cost_in_sample": round(cost, 6),
        "median_buy_cost": round(statistics.median(buy_sizes), 6) if buy_sizes else None,
        "max_buy_cost": round(max(buy_sizes), 6) if buy_sizes else None,
        "activity_window_start_utc": (
            datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat() if timestamps else None
        ),
        "activity_window_end_utc": (
            datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat() if timestamps else None
        ),
        "sell_trade_share": (
            round(sum(row.get("side") == "SELL" for row in trades) / len(trades), 6)
            if trades
            else None
        ),
        "buy_cost_share_by_price": {
            key: round(value / cost, 6) if cost else None
            for key, value in sorted(bands.items())
        },
        "buy_cost_share_by_outcome": {
            key: round(value / cost, 6) if cost else None
            for key, value in sorted(outcomes.items())
        },
        "caveat": (
            "latest-wallet-activity sample is truncated at 5,500 rows and filtered "
            "client-side because the API title parameter is not reliable"
        ),
    }


def replication_screen(
    wallet_row: dict[str, Any],
    *,
    max_lifetime_volume: float,
    max_recent_trade_rows: int,
    max_trades_per_event: float,
    max_near_binary_share: float,
    min_weather_events: int,
) -> dict[str, Any]:
    leaderboard = wallet_row["leaderboard"]
    profile = wallet_row["recent_activity"]
    all_row = leaderboard.get("all") or {}
    lifetime_volume = float(all_row.get("volume") or 0)
    lifetime_pnl = float(all_row.get("pnl") or 0)
    trades = int(profile.get("weather_trade_rows") or 0)
    events = int(profile.get("weather_events") or 0)
    trades_per_event = float(profile.get("weather_trades_per_event") or 0)
    near_binary_share = float(
        (profile.get("buy_cost_share_by_price") or {}).get(">=95c") or 0
    )
    reasons: list[str] = []
    if not all_row:
        reasons.append("missing_all_period_leaderboard")
    elif lifetime_pnl <= 0:
        reasons.append("non_positive_lifetime_pnl")
    if lifetime_volume > max_lifetime_volume:
        reasons.append("large_lifetime_turnover_proxy")
    if trades >= max_recent_trade_rows:
        reasons.append("recent_activity_truncated_or_high_frequency")
    if trades_per_event > max_trades_per_event:
        reasons.append("high_trades_per_event")
    if near_binary_share > max_near_binary_share:
        reasons.append("near_binary_buy_dependence")
    if events < min_weather_events:
        reasons.append("insufficient_recent_weather_events")

    positive_periods = sum(
        float((leaderboard.get(period) or {}).get("pnl") or 0) > 0
        for period in ("all", "month", "week")
    )
    efficiency = lifetime_pnl / lifetime_volume if lifetime_volume else 0.0
    scale_score = 1.0 / (1.0 + abs(math.log10(max(lifetime_volume, 1.0)) - 5.0))
    score = (
        positive_periods * 3.0
        + min(4.0, max(0.0, efficiency) * 100.0)
        + scale_score * 2.0
        + max(0.0, 1.0 - near_binary_share) * 2.0
        + max(0.0, 1.0 - min(1.0, trades_per_event / max_trades_per_event)) * 2.0
    )
    return {
        "eligible_for_full_history": not reasons,
        "replicability_score": round(score, 6),
        "exclusion_reasons": reasons,
        "screen_inputs": {
            "lifetime_volume": lifetime_volume,
            "lifetime_pnl": lifetime_pnl,
            "recent_weather_trade_rows": trades,
            "recent_weather_events": events,
            "recent_trades_per_event": trades_per_event,
            "near_binary_buy_cost_share": near_binary_share,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", action="append", default=[])
    parser.add_argument("--exclude-wallet", action="append", default=[])
    parser.add_argument(
        "--exclude-wallet-file",
        type=Path,
        help="Optional newline-delimited known-wallet list.",
    )
    parser.add_argument("--discover-leaderboard-rows", type=int, default=0)
    parser.add_argument("--max-profile-wallets", type=int, default=100)
    parser.add_argument("--select-count", type=int, default=20)
    parser.add_argument("--max-lifetime-volume", type=float, default=2_000_000)
    parser.add_argument("--max-recent-trade-rows", type=int, default=4_000)
    parser.add_argument("--max-trades-per-event", type=float, default=20.0)
    parser.add_argument("--max-near-binary-share", type=float, default=0.25)
    parser.add_argument("--min-weather-events", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    session = build_session()
    discovered_wallets: list[str] = []
    leaderboard_discovery: list[dict[str, Any]] = []
    if args.discover_leaderboard_rows:
        discovered_wallets, leaderboard_discovery = discover_weather_leaderboard_wallets(
            session,
            rows_per_period=args.discover_leaderboard_rows,
        )
    exclude_values = list(args.exclude_wallet)
    if args.exclude_wallet_file:
        exclude_values.extend(
            line.strip()
            for line in args.exclude_wallet_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    excluded = {normalize_wallet(value) for value in exclude_values}
    requested = [normalize_wallet(value) for value in args.wallet]
    wallet_universe = []
    for wallet in [*requested, *discovered_wallets]:
        if wallet and wallet not in excluded and wallet not in wallet_universe:
            wallet_universe.append(wallet)
    wallet_universe = wallet_universe[: args.max_profile_wallets]
    if not wallet_universe:
        parser.error("provide --wallet or --discover-leaderboard-rows")
    existing_payload: dict[str, Any] = {}
    if args.output.exists():
        try:
            existing_payload = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_payload = {}
    wallets = [
        row
        for row in existing_payload.get("wallets") or []
        if isinstance(row, dict) and normalize_wallet(row.get("wallet")) in wallet_universe
    ]
    completed_wallets = {normalize_wallet(row.get("wallet")) for row in wallets}
    blockers = [
        row for row in existing_payload.get("profile_blockers") or [] if isinstance(row, dict)
    ]

    def payload(status: str) -> dict[str, Any]:
        selected = sorted(
            [
                row
                for row in wallets
                if (row.get("replication_screen") or {}).get("eligible_for_full_history")
            ],
            key=lambda row: (
                -float((row.get("replication_screen") or {}).get("replicability_score") or 0),
                row["wallet"],
            ),
        )[: args.select_count]
        return {
            "schema_version": "weather_wallet_peer_scan_v2",
            "status": status,
            "snapshot_utc": datetime.now(timezone.utc).isoformat(),
            "denominator_scope": {
                "leaderboard_rows_per_period": args.discover_leaderboard_rows,
                "wallet_universe": len(wallet_universe),
                "profiled_wallets": len(wallets),
                "profile_blockers": len(blockers),
                "known_wallets_excluded": sorted(excluded),
                "selection_count_requested": args.select_count,
            },
            "screen_config": {
                "max_lifetime_volume": args.max_lifetime_volume,
                "max_recent_trade_rows": args.max_recent_trade_rows,
                "max_trades_per_event": args.max_trades_per_event,
                "max_near_binary_share": args.max_near_binary_share,
                "min_weather_events": args.min_weather_events,
            },
            "leaderboard_discovery": leaderboard_discovery,
            "wallets": wallets,
            "profile_blockers": blockers,
            "selected_wallets": [row["wallet"] for row in selected],
            "notes": [
                "Leaderboard PnL and volume are Polymarket WEATHER-category fields.",
                "PnL/volume is a comparison ratio, not return on capital.",
                "Recent activity is used only to classify execution style and 99-cent dependence.",
                "Replication screen thresholds prioritize research collection; they are not alpha eligibility gates.",
            ],
        }

    for wallet in wallet_universe:
        if wallet in completed_wallets:
            continue
        try:
            weather_rows, raw_rows = recent_activity(session, wallet)
            wallet_row = {
                "wallet": wallet,
                "leaderboard": {
                    period.lower(): leaderboard_row(session, wallet, period)
                    for period in ("ALL", "MONTH", "WEEK")
                },
                "recent_activity": activity_profile(weather_rows, raw_rows),
            }
        except requests.RequestException as exc:
            blockers.append(
                {
                    "wallet": wallet,
                    "code": "public_profile_fetch_failed_after_retries",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                }
            )
            atomic_write_json(args.output, payload("running_with_blockers"))
            print(wallet, blockers[-1], flush=True)
            continue
        wallet_row["replication_screen"] = replication_screen(
            wallet_row,
            max_lifetime_volume=args.max_lifetime_volume,
            max_recent_trade_rows=args.max_recent_trade_rows,
            max_trades_per_event=args.max_trades_per_event,
            max_near_binary_share=args.max_near_binary_share,
            min_weather_events=args.min_weather_events,
        )
        wallets.append(wallet_row)
        completed_wallets.add(wallet)
        atomic_write_json(args.output, payload("running"))
        print(wallet, wallets[-1]["leaderboard"], flush=True)
    atomic_write_json(args.output, payload("complete" if not blockers else "complete_with_blockers"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
