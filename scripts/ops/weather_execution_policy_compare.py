#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    DEFAULT_RUNTIME_ROOT,
    PlannerConfig,
    build_trade_plans_for_signal,
    read_jsonl,
    safe_str,
    to_float,
)


def _summarize(plans: List[Dict[str, Any]]) -> Dict[str, Any]:
    accepted = [row for row in plans if safe_str(row.get("status")) == "accepted"]
    rejected = [row for row in plans if safe_str(row.get("status")) == "rejected"]
    reasons = Counter(safe_str(row.get("risk_reason")) or "accepted" for row in plans)

    def values(field: str) -> List[float]:
        return [to_float(row.get(field), 0.0) for row in accepted if to_float(row.get(field), 0.0) > 0]

    prices = values("limit_price")
    quote_edges = values("quote_edge")
    spreads = values("quote_spread")
    return {
        "plans": len(plans),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "reject_reasons": dict(sorted(reasons.items())),
        "avg_limit_price": round(statistics.mean(prices), 6) if prices else 0.0,
        "avg_quote_edge": round(statistics.mean(quote_edges), 6) if quote_edges else 0.0,
        "avg_quote_spread": round(statistics.mean(spreads), 6) if spreads else 0.0,
        "quote_modes": dict(sorted(Counter(safe_str(row.get("quote_mode")) for row in accepted).items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare weather execution policies on the same signal file.")
    parser.add_argument(
        "--signals",
        default=str(DEFAULT_RUNTIME_ROOT / "signals" / "signals.jsonl"),
        help="Input weather_edge_signal JSONL.",
    )
    parser.add_argument(
        "--policies",
        default="mid_price_core_v1,maker_queue_v2,mid_price_core_v2",
        help="Comma-separated policies to compare.",
    )
    parser.add_argument(
        "--profiles",
        default="taker_now_v1,single_side_maker_v1",
        help="Comma-separated strategy-selectable execution profiles to compare on the same signals.",
    )
    parser.add_argument("--max-order-notional", type=float, default=5.0)
    parser.add_argument("--sizing-mode", choices=("notional", "fixed_shares"), default="notional")
    parser.add_argument("--fixed-order-shares", type=float, default=10.0)
    parser.add_argument("--max-order-shares", type=float, default=25.0)
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--min-entry-price", type=float, default=0.25)
    parser.add_argument("--max-entry-price", type=float, default=0.75)
    parser.add_argument("--min-quote-edge", type=float, default=0.03)
    parser.add_argument("--max-quote-spread", type=float, default=0.12)
    parser.add_argument("--max-mid-drift", type=float, default=0.10)
    parser.add_argument("--quote-improvement-ticks", type=int, default=1)
    parser.add_argument("--wide-spread-shade-ticks", type=int, default=1)
    parser.add_argument("--adverse-selection-spread-fraction", type=float, default=0.50)
    parser.add_argument("--low-band-ceiling", type=float, default=0.40)
    parser.add_argument("--high-band-floor", type=float, default=0.55)
    parser.add_argument("--split-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--taker-fraction", type=float, default=0.50)
    parser.add_argument("--split-min-edge", type=float, default=0.10)
    parser.add_argument("--high-band-shade-narrow", type=int, default=1)
    parser.add_argument("--high-band-shade-wide", type=int, default=2)
    parser.add_argument("--high-band-min-edge", type=float, default=0.15)
    parser.add_argument("--high-band-size-mult", type=float, default=0.60)
    parser.add_argument("--sample-rejections", type=int, default=8)
    return parser


def main() -> int:
    args = _parser().parse_args()
    signals = [
        row
        for row in read_jsonl(Path(args.signals))
        if safe_str(row.get("record_type")) == "weather_edge_signal"
    ]
    result: Dict[str, Any] = {
        "signals": len(signals),
        "signal_file": str(args.signals),
        "policies": {},
        "profiles": {},
    }
    for policy in [item.strip() for item in str(args.policies).split(",") if item.strip()]:
        config = PlannerConfig(
            max_order_notional=float(args.max_order_notional),
            sizing_mode=str(args.sizing_mode),
            fixed_order_shares=float(args.fixed_order_shares),
            max_order_shares=float(args.max_order_shares),
            min_edge=float(args.min_edge),
            min_entry_price=float(args.min_entry_price),
            max_entry_price=float(args.max_entry_price),
            live_enabled=False,
            execution_policy=policy,
            min_quote_edge=float(args.min_quote_edge),
            max_quote_spread=float(args.max_quote_spread),
            max_mid_drift=float(args.max_mid_drift),
            quote_improvement_ticks=int(args.quote_improvement_ticks),
            wide_spread_shade_ticks=int(args.wide_spread_shade_ticks),
            adverse_selection_spread_fraction=float(args.adverse_selection_spread_fraction),
            low_band_ceiling=float(args.low_band_ceiling),
            high_band_floor=float(args.high_band_floor),
            split_enabled=bool(args.split_enabled),
            taker_fraction=float(args.taker_fraction),
            split_min_edge=float(args.split_min_edge),
            high_band_shade_narrow=int(args.high_band_shade_narrow),
            high_band_shade_wide=int(args.high_band_shade_wide),
            high_band_min_edge=float(args.high_band_min_edge),
            high_band_size_mult=float(args.high_band_size_mult),
        )
        plans = [plan for signal in signals for plan in build_trade_plans_for_signal(signal, config)]
        summary = _summarize(plans)
        rejected = [row for row in plans if safe_str(row.get("status")) == "rejected"]
        summary["sample_rejections"] = [
            {
                "city": row.get("city"),
                "bracket": row.get("bracket"),
                "reason": row.get("risk_reason"),
                "bid": row.get("best_bid"),
                "ask": row.get("best_ask"),
                "quote_edge": row.get("quote_edge"),
                "required_quote_edge": row.get("required_quote_edge"),
            }
            for row in rejected[: max(0, int(args.sample_rejections))]
        ]
        result["policies"][policy] = summary

    for profile in [item.strip() for item in str(args.profiles).split(",") if item.strip()]:
        config = PlannerConfig(
            max_order_notional=float(args.max_order_notional),
            sizing_mode=str(args.sizing_mode),
            fixed_order_shares=float(args.fixed_order_shares),
            max_order_shares=float(args.max_order_shares),
            min_edge=float(args.min_edge),
            min_entry_price=float(args.min_entry_price),
            max_entry_price=float(args.max_entry_price),
            live_enabled=False,
            execution_profile=profile,
            min_quote_edge=float(args.min_quote_edge),
            max_quote_spread=float(args.max_quote_spread),
            max_mid_drift=float(args.max_mid_drift),
            quote_improvement_ticks=int(args.quote_improvement_ticks),
            wide_spread_shade_ticks=int(args.wide_spread_shade_ticks),
            adverse_selection_spread_fraction=float(args.adverse_selection_spread_fraction),
        )
        plans = [plan for signal in signals for plan in build_trade_plans_for_signal(signal, config)]
        result["profiles"][profile] = _summarize(plans)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
