#!/usr/bin/env python3
"""Range RV bracket no-arb scanner v0.9.

Target metric:
    bracket_noarb_range_rv_alpha

Weather temperature brackets for a city-day are mutually exclusive. If the
observed settled bracket set has exactly one winner, then:

- buying YES on every bracket pays 1 and costs sum(YES prices)
- buying NO on every bracket pays n - 1 and costs n - sum(YES prices)

So sum(YES) < 1 is an all-YES underround and sum(YES) > 1 is an all-NO
overround. This script tests that model-free Range RV expression, plus a
model-confirmed center/tail range expression, using fact_signal_candidates and
time-aligned orderbook prices.
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
OUT_JSON_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-noarb-v0-9.json"
OUT_MD_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-noarb-v0-9.md"


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


def make(
    *,
    algorithm: str,
    items: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    score: float,
    threshold: float,
    components: dict[str, Any],
) -> dict[str, Any] | None:
    if not spread_ok(legs, float(components.get("max_spread", 1.0))):
        return None
    return variants.make_candidate(
        algorithm=algorithm,
        decision_items=items,
        legs=legs,
        score=score,
        threshold=threshold,
        components=components,
    )


def winner_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for row in items if float(row["final_yes"]) >= 0.5)


def noarb_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sort_items(items)
    if len(ordered) < 3 or winner_count(ordered) != 1:
        return []
    market_sum = sum(float(row["market_yes_price"]) for row in ordered)
    model_sum = sum(float(row["model_p_yes"]) for row in ordered)
    rows: list[dict[str, Any]] = []
    all_yes = make(
        algorithm="noarb_all_yes_underround_e005",
        items=ordered,
        legs=[dict(row, side="BUY_YES") for row in ordered],
        score=1.0 - market_sum,
        threshold=0.005,
        components={
            "shape": "full_set_underround",
            "market_yes_sum": market_sum,
            "model_yes_sum": model_sum,
            "max_spread": 0.25,
        },
    )
    if all_yes:
        rows.append(all_yes)
    all_no = make(
        algorithm="noarb_all_no_overround_e005",
        items=ordered,
        legs=[dict(row, side="BUY_NO") for row in ordered],
        score=market_sum - 1.0,
        threshold=0.005,
        components={
            "shape": "full_set_overround",
            "market_yes_sum": market_sum,
            "model_yes_sum": model_sum,
            "max_spread": 0.25,
        },
    )
    if all_no:
        rows.append(all_no)
    return rows


def center_tail_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sort_items(items)
    if len(ordered) < 5 or winner_count(ordered) != 1:
        return []
    model = shape.norm(ordered, "model_p_yes")
    market = shape.norm(ordered, "market_yes_price")
    center_idx = len(ordered) // 2
    center = ordered[max(0, center_idx - 1) : min(len(ordered), center_idx + 2)]
    if len(center) < 3:
        return []
    tails = [ordered[0], ordered[-1]]
    model_center = sum(model[str(row["bracket"])] for row in center)
    market_center = sum(market[str(row["bracket"])] for row in center)
    model_tail = sum(model[str(row["bracket"])] for row in tails)
    market_tail = sum(market[str(row["bracket"])] for row in tails)
    rows: list[dict[str, Any]] = []
    center_yes = make(
        algorithm="noarb_center_yes_tail_no_e010",
        items=ordered,
        legs=[dict(row, side="BUY_YES") for row in center] + [dict(row, side="BUY_NO") for row in tails],
        score=(model_center - market_center) + (market_tail - model_tail),
        threshold=0.10,
        components={
            "shape": "center_underround_tail_overround",
            "model_center_mass": model_center,
            "market_center_mass": market_center,
            "model_tail_mass": model_tail,
            "market_tail_mass": market_tail,
            "max_spread": 0.22,
        },
    )
    if center_yes:
        rows.append(center_yes)
    tail_yes = make(
        algorithm="noarb_tail_yes_center_no_e010",
        items=ordered,
        legs=[dict(row, side="BUY_YES") for row in tails] + [dict(row, side="BUY_NO") for row in center],
        score=(model_tail - market_tail) + (market_center - model_center),
        threshold=0.10,
        components={
            "shape": "tail_underround_center_overround",
            "model_center_mass": model_center,
            "market_center_mass": market_center,
            "model_tail_mass": model_tail,
            "market_tail_mass": market_tail,
            "max_spread": 0.22,
        },
    )
    if tail_yes:
        rows.append(tail_yes)
    return rows


def generate_noarb_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for items in decision_sets:
        rows.extend(noarb_rows(items))
        rows.extend(center_tail_rows(items))
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
        "# Range RV Bracket No-Arb Scanner v0.9",
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
        f"- Decision sets: `{report['input']['decision_sets']}`; generated no-arb rows `{report['input']['noarb_rows']}`.",
        f"- Algorithms tested: `{report['input']['algorithms']}`.",
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
            f"Final verdict: `{report['verdict']}`. No live action.",
            "",
            "## Algorithm Definitions",
            "",
            "- `noarb_all_yes_underround_e005`: buy YES on every observed bracket when sum(YES) < 1 by at least 0.005.",
            "- `noarb_all_no_overround_e005`: buy NO on every observed bracket when sum(YES) > 1 by at least 0.005.",
            "- `noarb_center_yes_tail_no_e010`: center band underpriced while tails are overpriced.",
            "- `noarb_tail_yes_center_no_e010`: tails underpriced while center band is overpriced.",
            "",
            f"Forward gate requires holdout active_dates >= {shape.MIN_HOLDOUT_ACTIVE_DATES}, rows >= {shape.MIN_HOLDOUT_ROWS}, and top5-removed ROI > 0.",
            "",
            "## Decision Proxy",
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
            "## Orderbook Executable Subset",
            "",
            "Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; rows require all legs matched.",
            "",
            f"- Fully matched strategy rows: `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`.",
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
            "- Full-set all-YES/all-NO baskets are model-free no-arb tests on the observed settled bracket set.",
            "- Center/tail rows are model-confirmed relative-value range expressions, not pure no-arb.",
            "- Baseline is each algorithm's own top unfiltered no-arb candidate per city-day decision snapshot.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    rows = generate_noarb_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = variants.metric_rows(rows, "proxy")
    orderbook_rows = variants.metric_rows(rows, "orderbook")

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
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "bracket_noarb_range_rv_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "min_holdout_active_dates": shape.MIN_HOLDOUT_ACTIVE_DATES,
            "min_holdout_rows": shape.MIN_HOLDOUT_ROWS,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "noarb_rows": len(rows),
            "event_dates": len(all_dates),
            "algorithms": len({row["algorithm"] for row in rows}),
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": sorted(proxy_results, key=shape.rank_key, reverse=True),
        "orderbook_results": sorted(orderbook_results, key=shape.rank_key, reverse=True),
        "confirmed_algorithms": sorted(confirmed_algorithms),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} confirmed_algorithms={sorted(confirmed_algorithms)}")


if __name__ == "__main__":
    main()
