#!/usr/bin/env python3
"""Range RV all-YES underround robustness scanner v1.0.

Target metric:
    all_yes_underround_range_rv_alpha

v0.9 found a near-confirmed model-free Range RV candidate: buy YES on every
observed settled bracket when the total executable YES cost is below 1. This
script stress-tests that candidate with a fixed threshold grid and two
decision inputs:

- decision proxy: threshold on 1 - sum(fact market_yes_price)
- executable: threshold on 1 - sum(time-aligned orderbook taker YES cost)

The executable input is still available at decision time because orderbook
matching enforces orderbook_snapshot_ts <= decision_snapshot_ts_utc.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_range_rv_market_shape_v05 as shape
import research_range_rv_scanner as scanner
import research_range_rv_variant_lab_v03 as variants


ROOT = Path(__file__).resolve().parents[3]
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-underround-robust-v1-0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-underround-robust-v1-0.md"
THRESHOLDS = (0.005, 0.01, 0.02, 0.03, 0.05)


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


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))


def spread_ok(legs: list[dict[str, Any]], max_spread: float) -> bool:
    for leg in legs:
        value = scanner.side_spread(leg, leg["side"])
        if value is not None and value > max_spread:
            return False
    return True


def winner_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for row in items if float(row["final_yes"]) >= 0.5)


def build_base_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_items in decision_sets:
        items = sort_items(raw_items)
        if len(items) < 3 or winner_count(items) != 1:
            continue
        legs = [dict(row, side="BUY_YES") for row in items]
        if not spread_ok(legs, 0.25):
            continue
        market_sum = sum(float(row["market_yes_price"]) for row in items)
        model_sum = sum(float(row["model_p_yes"]) for row in items)
        row = variants.make_candidate(
            algorithm="underround_all_yes_base",
            decision_items=items,
            legs=legs,
            score=1.0 - market_sum,
            threshold=0.0,
            components={
                "shape": "full_set_all_yes_underround",
                "market_yes_sum": market_sum,
                "model_yes_sum": model_sum,
                "max_spread": 0.25,
            },
        )
        row["proxy_underround_score"] = 1.0 - market_sum
        rows.append(row)
    return rows


def clone_with_algorithm(row: dict[str, Any], *, algorithm: str, score: float, threshold: float) -> dict[str, Any]:
    cloned = deepcopy(row)
    cloned["algorithm"] = algorithm
    cloned["candidate_id"] = f"{algorithm}|{row['city']}|{row['event_date']}|{row['decision_snapshot_ts_utc']}"
    cloned["score"] = score
    cloned["threshold"] = threshold
    cloned["selected"] = int(score >= threshold)
    cloned["components"] = {**cloned.get("components", {}), "selection_score": score, "threshold": threshold}
    return cloned


def expand_proxy_rows(base_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in base_rows:
        score = float(row["proxy_underround_score"])
        for threshold in THRESHOLDS:
            suffix = int(threshold * 1000)
            rows.append(
                clone_with_algorithm(
                    row,
                    algorithm=f"underround_proxy_all_yes_e{suffix:03d}",
                    score=score,
                    threshold=threshold,
                )
            )
    return rows


def expand_executable_rows(base_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in base_rows:
        if row.get("orderbook_taker_cost") is None:
            continue
        score = 1.0 - float(row["orderbook_taker_cost"])
        for threshold in THRESHOLDS:
            suffix = int(threshold * 1000)
            rows.append(
                clone_with_algorithm(
                    row,
                    algorithm=f"underround_exec_all_yes_e{suffix:03d}",
                    score=score,
                    threshold=threshold,
                )
            )
    return rows


def row_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    reasons = ",".join(item.get("gate_reasons") or [])
    return (
        f"| `{item['algorithm']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {item['holdout_family_rows']} | {holdout['selected']['rows']} | "
        f"{holdout['selected']['active_event_dates']} | {shape.pct(train['selected']['taker_roi'])} | "
        f"{shape.fmt_ci(train['roi_ci95_cluster_by_event_date'])} | {shape.pct(train['excess_roi'])} | "
        f"{shape.fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {shape.pct(holdout['selected']['taker_roi'])} | "
        f"{shape.fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | {shape.pct(holdout['excess_roi'])} | "
        f"{shape.fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | {shape.pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` | `{reasons}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Range RV All-YES Underround Robustness v1.0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed.",
        "",
        "## Data Snapshot",
        "",
        "- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.",
        f"- DB last_modified: `{report['db_last_modified_utc']}`",
        f"- fact built at: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- Base all-YES rows: `{report['input']['base_rows']}`; orderbook matched base rows `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`.",
        f"- Thresholds: `{report['parameters']['thresholds']}`.",
        "- Evaluation requires exactly one settled winner in the observed bracket set.",
        "- No live_real PnL is published; this is opportunity-grain counterfactual research.",
        "",
        "## Verdict",
        "",
        "| gate | status |",
        "|---|---|",
    ]
    for key, value in report["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            f"Final verdict: `{report['verdict']}`. No live action unless both proxy and executable families are confirmed.",
            "",
            "## Decision Proxy Thresholds",
            "",
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["decision_proxy_results"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Executable Orderbook Thresholds",
            "",
            "Executable thresholds use `1 - orderbook_taker_cost` and only rows with all legs matched by `snapshot_ts <= decision_ts`.",
            "",
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates | gate reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["orderbook_results"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a threshold robustness test, not a threshold optimizer.",
            "- Passing requires a threshold family to survive cluster bootstrap, baseline excess, forward holdout, and top5-removed stress.",
            "- The expression is model-free: model probabilities are recorded but not used for selection.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def confirmed_algorithms(results: list[dict[str, Any]]) -> set[str]:
    return {item["algorithm"] for item in results if shape.passed(item)}


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    base_rows = build_base_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(base_rows, args.orderbook_glob)

    proxy_rows = expand_proxy_rows(base_rows)
    executable_rows = expand_executable_rows(base_rows)
    all_dates = sorted({row["event_date"] for row in base_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)

    proxy_results = shape.evaluate(
        variants.metric_rows(proxy_rows, "proxy"),
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = shape.evaluate(
        variants.metric_rows(executable_rows, "orderbook"),
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )

    proxy_pass = confirmed_algorithms(proxy_results)
    orderbook_pass = confirmed_algorithms(orderbook_results)
    # Consider the family confirmed when at least one pre-registered proxy
    # threshold and one pre-registered executable threshold pass. The exact
    # threshold may differ because the price source differs.
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if proxy_pass and orderbook_pass
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "all_yes_underround_range_rv_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "thresholds": list(THRESHOLDS),
            "min_holdout_active_dates": shape.MIN_HOLDOUT_ACTIVE_DATES,
            "min_holdout_rows": shape.MIN_HOLDOUT_ROWS,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "base_rows": len(base_rows),
            "event_dates": len(all_dates),
            "proxy_rows": len(proxy_rows),
            "executable_rows": len(executable_rows),
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": sorted(proxy_results, key=shape.rank_key, reverse=True),
        "orderbook_results": sorted(orderbook_results, key=shape.rank_key, reverse=True),
        "confirmed_proxy_algorithms": sorted(proxy_pass),
        "confirmed_orderbook_algorithms": sorted(orderbook_pass),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(
        f"verdict={report['verdict']} proxy_pass={sorted(proxy_pass)} "
        f"orderbook_pass={sorted(orderbook_pass)}"
    )


if __name__ == "__main__":
    main()
