#!/usr/bin/env python3
"""Bridge: station-basis risk-approved orders -> weather_order_executor plans.

Converts the executor-agnostic orders produced by weather_station_basis_exec.py
into the plan schema consumed by the existing, battle-tested
weather_order_executor.py (which owns py_clob_client + private key + maker/taker
placement). We reuse that money path rather than writing new key-handling code.

Safety:
- ``live_enabled`` is False unless --arm is passed (a pilot must opt in).
- Pilot caps are intentionally tiny and separate from shadow-parity caps.
- This script only WRITES plan files. It never places orders; the executor
  does, and only with its own --live --confirm-live and --allow-taker flags.

The station-basis edge is a TAKER edge (buy at ask, immediate fill). The
existing executor defaults to maker-only and discourages --allow-taker for the
old strategy; a station-basis pilot must consciously pass --allow-taker. Plans
are emitted with limit_price = ask and maker_only=False to express taker intent.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from station_basis_guards import RiskConfig, DayState, check  # noqa: E402

EXEC_DIR = ROOT / "runtime/weather_edge_v1/station_basis_exec"
SHADOW_DIR = ROOT / "runtime/weather_edge_v1/station_basis_shadow"
ENTRIES = SHADOW_DIR / "entries.jsonl"
PLAN_OUT = EXEC_DIR / "station_basis_plans.jsonl"

# Pilot caps — deliberately tiny, separate from the $5/$50 shadow-parity config.
PILOT_RISK = RiskConfig(
    per_trade_max_notional_usd=1.0,
    per_city_daily_max_notional_usd=3.0,
    daily_max_deployed_usd=10.0,
    daily_loss_stop_usd=-5.0,
    max_open_positions=12,
    max_open_positions_per_city=3,
    kill_switch_path="runtime/weather_edge_v1/station_basis_shadow/PAUSE",
)
# Pilot starts with the two cities whose market most strongly anchors the
# official station (cleanest edge, liquid): Milan, London.
PILOT_CITIES = {"Milan", "London"}

# Maker pricing (D5, calibrated 2026-06-15): the resting bid is computed at
# PLACEMENT time from the live book via station_basis_guards.maker_resting_price
# (= best_bid + 1 tick, capped below ask). The plan only carries the maker
# intent + a price ceiling (the decision-time ask); the placement layer must
# recompute against the live book, never trust this stale ceiling as the price.
MAKER_PRICE_RULE = "best_bid_plus_tick"
TICK = 0.01


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def to_plan(entry: dict, shares: float, *, live_enabled: bool) -> dict:
    ask = float(entry["ask"])
    side = entry["side"]  # BUY_YES / BUY_NO
    base = {
        "record_type": "weather_edge_trade_plan",
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "station_basis_pilot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "weather_station_basis_pilot_v0",
        "city": entry["city"],
        "target_date": entry["target_date"],
        "bracket": entry["bracket"],
        "token_id": entry["token_id"],
        "market_id": entry.get("market_id"),
        "event_slug": entry.get("event_slug"),
        "signal_side": side,
        "order_side": "BUY",
        # MAKER mode (D5): the actual resting price = best_bid + 1 tick computed
        # from the LIVE book at placement (station_basis_guards.maker_resting_price),
        # NOT this stale ceiling. limit_price here is only the max we'd ever pay
        # (decision-time ask); notional uses it as a conservative upper bound.
        "maker_price_rule": MAKER_PRICE_RULE,
        "limit_price": round(ask, 4),     # ceiling only; placement reprices to bid+tick
        "size": round(shares, 4),
        "notional": round(ask * shares, 4),  # upper-bound notional (real fill is cheaper)
        "maker_only": True,               # this pilot is maker-only by decision
        "quote_best_ask": ask,
        "quote_tick_size": TICK,
        "rule": entry["rule"],
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
    }
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="store_true", help="set live_enabled=true (pilot opt-in)")
    ap.add_argument("--all-cities", action="store_true", help="ignore PILOT_CITIES restriction")
    ap.add_argument("--since-shadow-index", type=int, default=None,
                    help="only convert shadow entries at/after this index (default: today only)")
    args = ap.parse_args()

    EXEC_DIR.mkdir(parents=True, exist_ok=True)
    entries = read_jsonl(ENTRIES)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    state = DayState()
    plans = []
    skipped = {"not_today": 0, "city_filter": 0, "guard_denied": 0}
    for i, e in enumerate(entries):
        if args.since_shadow_index is not None and i < args.since_shadow_index:
            continue
        if args.since_shadow_index is None and e.get("target_date") != today:
            skipped["not_today"] += 1
            continue
        if not args.all_cities and e["city"] not in PILOT_CITIES:
            skipped["city_filter"] += 1
            continue
        d = check(cfg=PILOT_RISK, state=state, repo_root=ROOT,
                  city=e["city"], ask=float(e["ask"]), shares=float(e["shares"]))
        if not d.allow:
            skipped["guard_denied"] += 1
            continue
        shares = d.capped_shares
        plans.append(to_plan(e, shares, live_enabled=args.arm))
        state.record_fill(e["city"], float(e["ask"]) * shares)

    with PLAN_OUT.open("w") as f:
        for p in plans:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    armed = "ARMED (live_enabled=true)" if args.arm else "SAFE (live_enabled=false)"
    print(f"wrote {len(plans)} plans -> {PLAN_OUT}  [{armed}]")
    print(f"  cities: {'ALL' if args.all_cities else sorted(PILOT_CITIES)}  skipped={skipped}")
    print(f"  pilot caps: ${PILOT_RISK.per_trade_max_notional_usd}/trade "
          f"${PILOT_RISK.daily_max_deployed_usd}/day loss-stop ${PILOT_RISK.daily_loss_stop_usd}")
    if plans:
        dep = sum(p["notional"] for p in plans)
        print(f"  total notional this batch: ${dep:.2f}")
        for p in plans:
            print(f"    {p['city']:8} {p['rule']:11} {p['signal_side']:8} {p['bracket']:6} "
                  f"ask={p['limit_price']:.3f} sz={p['size']:.2f} ${p['notional']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
