from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.strategies.pmm.backtest.scenario_generator import generate_all_from_catalog


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _base_from_zone(base_zone: str) -> float:
    mapping = {
        "around_30": 0.30,
        "around_50": 0.50,
        "around_70": 0.70,
    }
    return mapping.get(base_zone, 0.50)


def _pattern_from_regime(regime: str, shock_event: str) -> str:
    if shock_event == "spike_up":
        return "shock_up"
    if shock_event == "spike_down":
        return "shock_down"
    if shock_event == "spike_then_revert":
        return "spike_revert"
    if shock_event == "drop_then_revert":
        return "drop_revert"

    mapping = {
        "stable": "stable",
        "oscillating": "oscillating",
        "single_side_up": "trend_up",
        "single_side_down": "trend_down",
        "whipsaw": "whipsaw",
    }
    return mapping.get(regime, "stable")


def _fillability_from_expectation(fill_expectation: str) -> str:
    mapping = {
        "mostly_fill": "high",
        "partial_fill": "medium",
        "mostly_no_fill": "low",
    }
    return mapping.get(fill_expectation, "medium")


def _spread_depth_from_liquidity(liquidity: str) -> tuple[float, float]:
    if liquidity == "high":
        return 0.015, 2200.0
    if liquidity == "low":
        return 0.028, 900.0
    return 0.02, 1500.0


def _intent_catalog(
    base_zone: str,
    market_regime: str,
    liquidity: str,
    fill_expectation: str,
    shock_event: str,
    horizon_ticks: int,
    count: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    base_mid_center = _base_from_zone(base_zone)
    pattern = _pattern_from_regime(market_regime, shock_event)
    fillability = _fillability_from_expectation(fill_expectation)
    spread_base, depth_base = _spread_depth_from_liquidity(liquidity)

    cases: list[dict[str, Any]] = []
    n = max(1, count)
    for i in range(n):
        base_mid = _clip(base_mid_center + rng.uniform(-0.03, 0.03), 0.05, 0.95)
        spread = _clip(spread_base + rng.uniform(-0.004, 0.004), 0.008, 0.05)
        depth = max(100.0, depth_base * (0.75 + 0.5 * rng.random()))
        vol = _clip(0.0015 + rng.random() * 0.0025, 0.001, 0.005)
        drift = _clip(rng.uniform(0.00012, 0.0005), 0.00008, 0.0006)
        drift = drift if pattern in {"trend_up", "shock_up", "spike_revert"} else drift

        case = {
            "name": f"intent_{base_zone}_{market_regime}_{shock_event}_{i+1:02d}",
            "description": (
                f"Intent-generated: zone={base_zone}, regime={market_regime}, "
                f"liquidity={liquidity}, fill={fill_expectation}, shock={shock_event}"
            ),
            "base_mid": round(base_mid, 4),
            "ticks": max(120, horizon_ticks),
            "pattern": pattern,
            "volatility": round(vol, 6),
            "drift_per_tick": round(drift, 6),
            "spread_base": round(spread, 4),
            "spread_jitter": round(_clip(spread * 0.18, 0.001, 0.008), 4),
            "depth_base": round(depth, 2),
            "fillability": fillability,
        }

        if pattern in {"oscillating"}:
            case["osc_amp"] = round(_clip(0.02 + rng.random() * 0.02, 0.01, 0.05), 4)
            case["osc_period"] = int(36 + rng.randint(0, 60))
        if pattern in {"shock_up", "shock_down", "spike_revert", "drop_revert"}:
            case["shock_tick"] = int(max(30, horizon_ticks * 0.35))
            case["shock_jump"] = round(_clip(0.1 + rng.random() * 0.12, 0.08, 0.25), 4)
            case["shock_len"] = int(40 + rng.randint(0, 80))
            case["shock_spread_mult"] = round(_clip(2.0 + rng.random() * 1.5, 1.5, 4.0), 3)
            case["shock_depth_mult"] = round(_clip(0.25 + rng.random() * 0.25, 0.15, 0.6), 3)
        if pattern == "liquidity_dryup":
            case["dryup_start"] = int(max(20, horizon_ticks * 0.55))
            case["dryup_spread_mult"] = round(_clip(2.0 + rng.random() * 1.5, 1.5, 4.0), 3)
            case["dryup_depth_mult"] = round(_clip(0.2 + rng.random() * 0.3, 0.1, 0.6), 3)

        cases.append(case)

    return {
        "version": "v1",
        "default_token_ids": ["YES", "NO"],
        "cases": cases,
    }


def _print_initial(files: list[str]) -> None:
    for file_path in files:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "scenario_id": data.get("scenario_id"),
                    "initial_state": data.get("initial_state", {}),
                    "token_ids": data.get("token_ids", []),
                },
                ensure_ascii=False,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PMM mock scenarios")
    parser.add_argument("--mode", default="catalog", choices=["catalog", "intent"])
    parser.add_argument(
        "--catalog", default="src/strategies/pmm/backtest/case_catalog.json"
    )
    parser.add_argument("--out-dir", default="runtime/pmm/backtest/scenarios")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--show-initial", action="store_true")
    parser.add_argument("--show-sample", default="")

    # intent params
    parser.add_argument("--base-zone", default="around_50")
    parser.add_argument("--market-regime", default="stable")
    parser.add_argument("--liquidity", default="medium")
    parser.add_argument("--fill-expectation", default="partial_fill")
    parser.add_argument("--shock-event", default="none")
    parser.add_argument("--horizon-ticks", type=int, default=600)
    parser.add_argument("--count", type=int, default=10)

    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    if args.mode == "catalog":
        result = generate_all_from_catalog(args.catalog, args.out_dir, seed=args.seed)
    else:
        catalog_obj = _intent_catalog(
            base_zone=args.base_zone,
            market_regime=args.market_regime,
            liquidity=args.liquidity,
            fill_expectation=args.fill_expectation,
            shock_event=args.shock_event,
            horizon_ticks=args.horizon_ticks,
            count=args.count,
            seed=args.seed,
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fp:
            fp.write(json.dumps(catalog_obj, ensure_ascii=False, indent=2))
            tmp_catalog = fp.name
        result = generate_all_from_catalog(tmp_catalog, args.out_dir, seed=args.seed)
        Path(tmp_catalog).unlink(missing_ok=True)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    files = result.get("files", [])
    if args.show_initial:
        _print_initial(files)
    if args.show_sample:
        target = next((x for x in files if Path(x).stem == args.show_sample), "")
        if target:
            data = json.loads(Path(target).read_text(encoding="utf-8"))
            first_tick = (data.get("ticks") or [{}])[0]
            print(
                json.dumps(
                    {"scenario_id": data.get("scenario_id"), "first_tick": first_tick},
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
