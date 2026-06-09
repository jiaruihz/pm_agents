#!/usr/bin/env python3
"""Range RV market-shape full-opportunity scanner v0.7.

This reruns the v0.5 market-shape anomaly definitions on the full
fact_signal_candidates opportunity set. It intentionally does not require the
old single-leg strategy's `eligible` flag, because Range RV is a different
strategy expression. Price/spread and time-aligned orderbook checks remain in
place.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_range_rv_market_shape_v05 as shape
import research_range_rv_scanner as scanner
import research_range_rv_variant_lab_v03 as variants


ROOT = Path(__file__).resolve().parents[3]
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-market-shape-fullop-v0-7.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-market-shape-fullop-v0-7.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    shape_rows = shape.generate_shape_rows(decision_sets, require_eligible=False)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(shape_rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in shape_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = variants.metric_rows(shape_rows, "proxy")
    orderbook_rows = variants.metric_rows(shape_rows, "orderbook")

    proxy_results = shape.evaluate(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = shape.evaluate(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )

    confirmed_algorithms = {
        item["algorithm"] for item in proxy_results if shape.passed(item)
    } & {item["algorithm"] for item in orderbook_results if shape.passed(item)}
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if confirmed_algorithms
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    db_path = Path(args.db_path)
    report = {
        "title": "Range RV Market Shape Full-Opportunity Scanner v0.7",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "market_shape_fullop_range_rv_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "require_eligible": False,
            "min_holdout_active_dates": shape.MIN_HOLDOUT_ACTIVE_DATES,
            "min_holdout_rows": shape.MIN_HOLDOUT_ROWS,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "shape_rows": len(shape_rows),
            "event_dates": len(all_dates),
            "algorithms": len({row["algorithm"] for row in shape_rows}),
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": sorted(proxy_results, key=shape.rank_key, reverse=True),
        "orderbook_results": sorted(orderbook_results, key=shape.rank_key, reverse=True),
        "confirmed_algorithms": sorted(confirmed_algorithms),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    shape.write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} confirmed_algorithms={sorted(confirmed_algorithms)}")


if __name__ == "__main__":
    main()
