#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    DEFAULT_RUNTIME_ROOT,
    PlannerConfig,
    plan_trades,
)
from src.strategies.weather_edge_v1.execution.profiles import execution_profile_names


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build weather_edge_v1 paper/live trade plans from imported signals.")
    parser.add_argument(
        "--signals",
        default=str(DEFAULT_RUNTIME_ROOT / "signals" / "signals.jsonl"),
        help="Input signal JSONL.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_RUNTIME_ROOT / "plans" / "trade_plans.jsonl"),
        help="Output trade-plan JSONL.",
    )
    parser.add_argument("--strategy-instance", default=os.getenv("WEATHER_STRATEGY_INSTANCE", ""))
    parser.add_argument("--max-order-notional", type=float, default=1.0)
    parser.add_argument(
        "--sizing-mode",
        choices=("notional", "fixed_shares"),
        default="notional",
        help="Order sizing mode. notional uses max-order-notional / price; fixed_shares uses fixed-order-shares.",
    )
    parser.add_argument("--fixed-order-shares", type=float, default=10.0)
    parser.add_argument("--max-order-shares", type=float, default=None)
    parser.add_argument("--min-order-shares", type=float, default=5.0)
    parser.add_argument("--max-position", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--min-entry-price", type=float, default=0.25)
    parser.add_argument("--max-entry-price", type=float, default=0.75)
    # Per-side entry band / edge overrides (None -> use global above).
    parser.add_argument("--yes-min-entry-price", type=float, default=None)
    parser.add_argument("--yes-max-entry-price", type=float, default=None)
    parser.add_argument("--yes-min-edge", type=float, default=None)
    parser.add_argument("--no-min-entry-price", type=float, default=None)
    parser.add_argument("--no-max-entry-price", type=float, default=None)
    parser.add_argument("--no-min-edge", type=float, default=None)
    parser.add_argument("--price-offset", type=float, default=0.0)
    parser.add_argument(
        "--execution-policy",
        choices=("mid_price_core_v1", "maker_queue_v2", "mid_price_core_v2", "taker_top_ask_v1"),
        default="mid_price_core_v1",
    )
    parser.add_argument(
        "--execution-profile",
        choices=execution_profile_names(),
        default="",
        help="Strategy-selectable execution profile. When set, it owns quote and lifecycle policy selection.",
    )
    parser.add_argument("--cancel-buffer-sec", type=int, default=0)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--min-quote-edge", type=float, default=0.03)
    parser.add_argument("--max-quote-spread", type=float, default=0.12)
    parser.add_argument("--max-mid-drift", type=float, default=0.10)
    parser.add_argument("--quote-improvement-ticks", type=int, default=1)
    parser.add_argument("--wide-spread-shade-ticks", type=int, default=1)
    parser.add_argument("--narrow-quote-spread", type=float, default=0.03)
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
    parser.add_argument("--enable-live", action="store_true", help="Mark accepted plans as live-enabled. Executor still requires --live --confirm-live.")
    parser.add_argument("--accepted-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = PlannerConfig(
        strategy_instance=str(args.strategy_instance),
        max_order_notional=float(args.max_order_notional),
        sizing_mode=str(args.sizing_mode),
        fixed_order_shares=float(args.fixed_order_shares),
        max_order_shares=(
            float(args.max_order_shares)
            if args.max_order_shares is not None
            else (float(args.max_position) if args.max_position is not None else 25.0)
        ),
        min_order_shares=float(args.min_order_shares),
        min_edge=float(args.min_edge),
        min_entry_price=float(args.min_entry_price),
        max_entry_price=float(args.max_entry_price),
        yes_min_entry_price=(float(args.yes_min_entry_price) if args.yes_min_entry_price is not None else None),
        yes_max_entry_price=(float(args.yes_max_entry_price) if args.yes_max_entry_price is not None else None),
        yes_min_edge=(float(args.yes_min_edge) if args.yes_min_edge is not None else None),
        no_min_entry_price=(float(args.no_min_entry_price) if args.no_min_entry_price is not None else None),
        no_max_entry_price=(float(args.no_max_entry_price) if args.no_max_entry_price is not None else None),
        no_min_edge=(float(args.no_min_edge) if args.no_min_edge is not None else None),
        price_offset=float(args.price_offset),
        live_enabled=bool(args.enable_live),
        execution_policy=str(args.execution_policy),
        execution_profile=str(args.execution_profile),
        cancel_buffer_sec=max(0, int(args.cancel_buffer_sec)),
        tick_size=float(args.tick_size),
        min_quote_edge=float(args.min_quote_edge),
        max_quote_spread=float(args.max_quote_spread),
        max_mid_drift=float(args.max_mid_drift),
        quote_improvement_ticks=int(args.quote_improvement_ticks),
        wide_spread_shade_ticks=int(args.wide_spread_shade_ticks),
        narrow_quote_spread=float(args.narrow_quote_spread),
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
    result = plan_trades(
        signal_path=Path(args.signals),
        out_path=Path(args.out),
        config=config,
        include_rejected=not bool(args.accepted_only),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
