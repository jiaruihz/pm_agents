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
    parser.add_argument("--max-order-notional", type=float, default=1.0)
    parser.add_argument(
        "--sizing-mode",
        choices=("notional", "fixed_shares"),
        default="notional",
        help="Order sizing mode. notional uses max-order-notional / price; fixed_shares uses fixed-order-shares.",
    )
    parser.add_argument("--fixed-order-shares", type=float, default=10.0)
    parser.add_argument("--max-order-shares", type=float, default=None)
    parser.add_argument("--max-position", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--min-entry-price", type=float, default=0.25)
    parser.add_argument("--max-entry-price", type=float, default=0.75)
    parser.add_argument("--price-offset", type=float, default=0.0)
    parser.add_argument("--execution-policy", choices=("mid_price_core_v1", "maker_queue_v1", "maker_queue_v2"), default="mid_price_core_v1")
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--min-quote-edge", type=float, default=0.03)
    parser.add_argument("--max-quote-spread", type=float, default=0.12)
    parser.add_argument("--max-mid-drift", type=float, default=0.10)
    parser.add_argument("--quote-improvement-ticks", type=int, default=1)
    parser.add_argument("--wide-spread-shade-ticks", type=int, default=1)
    parser.add_argument("--narrow-quote-spread", type=float, default=0.03)
    parser.add_argument("--adverse-selection-spread-fraction", type=float, default=0.50)
    parser.add_argument("--enable-live", action="store_true", help="Mark accepted plans as live-enabled. Executor still requires --live --confirm-live.")
    parser.add_argument("--accepted-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = PlannerConfig(
        max_order_notional=float(args.max_order_notional),
        sizing_mode=str(args.sizing_mode),
        fixed_order_shares=float(args.fixed_order_shares),
        max_order_shares=(
            float(args.max_order_shares)
            if args.max_order_shares is not None
            else (float(args.max_position) if args.max_position is not None else 25.0)
        ),
        min_edge=float(args.min_edge),
        min_entry_price=float(args.min_entry_price),
        max_entry_price=float(args.max_entry_price),
        price_offset=float(args.price_offset),
        live_enabled=bool(args.enable_live),
        execution_policy=str(args.execution_policy),
        tick_size=float(args.tick_size),
        min_quote_edge=float(args.min_quote_edge),
        max_quote_spread=float(args.max_quote_spread),
        max_mid_drift=float(args.max_mid_drift),
        quote_improvement_ticks=int(args.quote_improvement_ticks),
        wide_spread_shade_ticks=int(args.wide_spread_shade_ticks),
        narrow_quote_spread=float(args.narrow_quote_spread),
        adverse_selection_spread_fraction=float(args.adverse_selection_spread_fraction),
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
