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

from src.strategies.weather_edge_v1.tools.weather_edge_paper import (
    append_jsonl_dedup,
    build_paper_decisions,
    compute_profile_probabilities,
    extract_snapshot_brackets,
    load_snapshot,
)
from src.strategies.weather_edge_v1.tools.weather_predict_bridge import (
    DEFAULT_WEATHER_PREDICT_ROOT,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record weather_edge_v1 side-by-side paper decisions.")
    parser.add_argument("--target-market", default="", help="Polymarket event/market URL, slug, or condition id.")
    parser.add_argument("--snapshot-json", default="", help="Use an existing market snapshot JSON file.")
    parser.add_argument("--city", required=True, help="weather-predict city key, e.g. NYC, Chicago, Paris, Seoul.")
    parser.add_argument("--date", required=True, help="Target local weather date, YYYY-MM-DD.")
    parser.add_argument("--unit", default="", choices=["", "F", "C"], help="Override bracket unit. Default: infer from markets.")
    parser.add_argument("--min-edge", type=float, default=0.10)
    parser.add_argument("--orderbook-top-n", type=int, default=10)
    parser.add_argument("--weather-predict-root", default=str(DEFAULT_WEATHER_PREDICT_ROOT))
    parser.add_argument("--cache-dir", default="", help="Default: weather-predict/cache_global_full")
    parser.add_argument(
        "--orderbook-jsonl",
        action="append",
        default=[],
        help="Local live orderbook JSONL/JSONL.GZ path. May be repeated. Overrides snapshot prices.",
    )
    parser.add_argument("--out", default="runtime/weather_edge_v1/paper_decisions.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Print rows instead of appending JSONL.")
    return parser


def _infer_unit(snapshot: dict, fallback: str = "F") -> str:
    for market in snapshot.get("markets", []) or []:
        question = str(market.get("question") or "")
        if "°C" in question or "°c" in question:
            return "C"
        if "°F" in question or "°f" in question:
            return "F"
    return fallback


def main() -> int:
    args = _parser().parse_args()
    root = Path(args.weather_predict_root).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve() if args.cache_dir.strip() else root / "cache_global_full"

    snapshot = load_snapshot(
        target_market=args.target_market,
        snapshot_json=args.snapshot_json,
        orderbook_top_n=args.orderbook_top_n,
    )
    unit = args.unit or _infer_unit(snapshot)
    brackets = extract_snapshot_brackets(
        snapshot,
        weather_predict_root=root,
        orderbook_paths=args.orderbook_jsonl,
    )
    profile_result = compute_profile_probabilities(
        city=args.city,
        target_date=args.date,
        unit=unit,
        brackets=brackets,
        weather_predict_root=root,
        cache_dir=cache_dir,
    )
    rows = build_paper_decisions(
        snapshot=snapshot,
        city=args.city,
        target_date=args.date,
        unit=unit,
        profile_result=profile_result,
        brackets=brackets,
        min_edge=args.min_edge,
    )
    summary = {
        "ok": True,
        "city": args.city,
        "date": args.date,
        "unit": unit,
        "brackets": len(brackets),
        "rows": len(rows),
        "profiles": {
            name: {
                "ok": bool(item.get("ok")),
                "reason": item.get("reason", ""),
                "n_probabilities": len(item.get("probabilities", []) or []),
            }
            for name, item in (profile_result.get("profiles") or {}).items()
        },
    }
    if args.dry_run:
        print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    write_summary = append_jsonl_dedup(Path(args.out), rows)
    summary.update(write_summary)
    summary["out"] = str(Path(args.out).expanduser())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
