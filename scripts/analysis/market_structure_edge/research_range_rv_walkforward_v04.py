#!/usr/bin/env python3
"""Range RV walk-forward algorithm selector v0.4.

Uses the v0.3 pre-registered algorithm rows, then chooses one algorithm per
event_date using only prior dates. This tests whether multiple Range RV
expressions can be combined without post-hoc picking.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-walkforward-v0-4.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-walkforward-v0-4.md"
MIN_WALKFORWARD_ACTIVE_DATES = 5
MIN_WALKFORWARD_ROWS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--warmup-dates", type=int, default=10)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def rows_for(rows: list[dict[str, Any]], algorithm: str, dates: set[str], *, selected: bool) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["algorithm"] == algorithm
        and row["event_date"] in dates
        and (not selected or int(row.get("selected") or 0) == 1)
    ]


def prior_stats(rows: list[dict[str, Any]], algorithm: str, prior_dates: set[str], *, source: str) -> dict[str, Any]:
    selected = rows_for(rows, algorithm, prior_dates, selected=True)
    family = rows_for(rows, algorithm, prior_dates, selected=False)
    if not selected or not family:
        return {
            "algorithm": algorithm,
            "selected": scanner.summarize([], source=source),
            "baseline": scanner.summarize([], source=source),
            "excess_roi": None,
        }
    selected_summary = scanner.summarize(selected, source=source)
    family_summary = scanner.summarize(family, source=source)
    selected_roi = selected_summary["taker_roi"]
    family_roi = family_summary["taker_roi"]
    return {
        "algorithm": algorithm,
        "selected": selected_summary,
        "baseline": family_summary,
        "excess_roi": None if selected_roi is None or family_roi is None else selected_roi - family_roi,
    }


def choose_algorithm(stats: list[dict[str, Any]], selector: str) -> str | None:
    eligible = []
    for item in stats:
        selected = item["selected"]
        excess = item.get("excess_roi")
        roi = selected.get("taker_roi")
        if selected.get("active_event_dates", 0) < 5 or selected.get("rows", 0) < 10:
            continue
        if roi is None or roi <= 0 or excess is None or excess <= 0:
            continue
        if selector == "strict_tail":
            drop = selected.get("drop_top5_taker_roi")
            if drop is None or drop <= 0:
                continue
        eligible.append(item)
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (
            item.get("excess_roi") if item.get("excess_roi") is not None else -999.0,
            item["selected"].get("drop_top5_taker_roi") if item["selected"].get("drop_top5_taker_roi") is not None else -999.0,
            item["selected"].get("active_event_dates", 0),
        ),
        reverse=True,
    )
    return str(eligible[0]["algorithm"])


def walk_forward(rows: list[dict[str, Any]], *, source: str, warmup_dates: int) -> list[dict[str, Any]]:
    dates = sorted({row["event_date"] for row in rows})
    algorithms = sorted({row["algorithm"] for row in rows})
    decisions = []
    for selector in ("balanced", "strict_tail"):
        for idx, date in enumerate(dates):
            if idx < warmup_dates:
                continue
            prior = set(dates[:idx])
            stats = [prior_stats(rows, algorithm, prior, source=source) for algorithm in algorithms]
            chosen = choose_algorithm(stats, selector)
            current_dates = {date}
            if chosen is None:
                selected_rows: list[dict[str, Any]] = []
                baseline_rows: list[dict[str, Any]] = []
            else:
                selected_rows = rows_for(rows, chosen, current_dates, selected=True)
                baseline_rows = rows_for(rows, chosen, current_dates, selected=False)
            decisions.append(
                {
                    "selector": selector,
                    "event_date": date,
                    "chosen_algorithm": chosen,
                    "selected_rows": selected_rows,
                    "baseline_rows": baseline_rows,
                    "prior_stats": {item["algorithm"]: item for item in stats},
                }
            )
    return decisions


def flatten_decisions(decisions: list[dict[str, Any]], selector: str, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for decision in decisions:
        if decision["selector"] == selector:
            out.extend(decision[key])
    return out


def evaluate_selector(
    decisions: list[dict[str, Any]],
    *,
    selector: str,
    source: str,
    iters: int,
    seed: int,
) -> dict[str, Any]:
    selected = flatten_decisions(decisions, selector, "selected_rows")
    baseline = flatten_decisions(decisions, selector, "baseline_rows")
    selected_summary = scanner.summarize(selected, source=source)
    baseline_summary = scanner.summarize(baseline, source=source)
    selected_roi = selected_summary["taker_roi"]
    baseline_roi = baseline_summary["taker_roi"]
    boot = scanner.bootstrap_delta(selected, baseline, iters=iters, seed=seed)
    train_like_eval = {
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }
    # Walk-forward is already forward-only, so reuse the same eval as the
    # forward evidence for gate purposes.
    gates = scanner.gate_result(train_like_eval, train_like_eval)
    chosen_counts: dict[str, int] = {}
    active_dates = 0
    no_trade_dates = 0
    for decision in decisions:
        if decision["selector"] != selector:
            continue
        chosen = decision["chosen_algorithm"]
        if chosen is None or not decision["selected_rows"]:
            no_trade_dates += 1
            continue
        active_dates += 1
        chosen_counts[chosen] = chosen_counts.get(chosen, 0) + 1
    gate_reasons: list[str] = []
    if active_dates < MIN_WALKFORWARD_ACTIVE_DATES:
        gates["forward"] = "FAIL"
        gate_reasons.append(f"active_dates<{MIN_WALKFORWARD_ACTIVE_DATES}")
    if selected_summary["rows"] < MIN_WALKFORWARD_ROWS:
        gates["forward"] = "FAIL"
        gate_reasons.append(f"rows<{MIN_WALKFORWARD_ROWS}")
    drop_top5_roi = selected_summary.get("drop_top5_taker_roi")
    if drop_top5_roi is None or drop_top5_roi <= 0:
        gates["forward"] = "FAIL"
        gate_reasons.append("top5_removed_roi<=0_or_not_available")
    gates["verdict"] = (
        "confirmed"
        if gates["significance"] == "PASS"
        and gates["baseline"] == "PASS"
        and gates["forward"] == "PASS"
        else "inconclusive"
    )
    return {
        "selector": selector,
        "source": source,
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": train_like_eval["excess_roi"],
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
        "gates": gates,
        "gate_reasons": gate_reasons,
        "active_dates": active_dates,
        "no_trade_dates": no_trade_dates,
        "chosen_algorithm_counts": chosen_counts,
    }


def row_line(item: dict[str, Any]) -> str:
    gates = item["gates"]
    return (
        f"| `{item['selector']}` | {item['active_dates']} | {item['no_trade_dates']} | "
        f"{item['selected']['rows']} | {pct(item['selected']['taker_roi'])} | "
        f"{fmt_ci(item['roi_ci95_cluster_by_event_date'])} | {pct(item['baseline']['taker_roi'])} | "
        f"{pct(item['excess_roi'])} | {fmt_ci(item['excess_roi_ci95_cluster_by_event_date'])} | "
        f"{pct(item['selected']['drop_top5_taker_roi'])} | `{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` | "
        f"`{item['chosen_algorithm_counts']}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Range RV Walk-Forward Selector v0.4",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed.",
        "",
        "## Data Snapshot",
        "",
        f"- Data source: `runtime/weather.db.fact_signal_candidates`; `fact_trades` only for mandatory self-check.",
        f"- DB last_modified: `{report['db_last_modified_utc']}`",
        f"- fact built at: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- Warmup dates: `{report['parameters']['warmup_dates']}`; walk-forward dates after warmup: `{report['input']['walkforward_dates']}`.",
        f"- Variant rows: `{report['input']['variant_rows']}`; orderbook matched rows `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`.",
        "",
        "## Selector Rules",
        "",
        "- `balanced`: before each date, pick the algorithm with prior active_dates >= 5, rows >= 10, prior ROI > 0, and prior excess ROI > 0; rank by prior excess ROI.",
        "- `strict_tail`: same as balanced, but prior top5-removed ROI must also be > 0.",
        "- Only prior dates are used to choose the algorithm for the current date.",
        f"- Forward gate additionally requires active_dates >= {MIN_WALKFORWARD_ACTIVE_DATES}, rows >= {MIN_WALKFORWARD_ROWS}, and top5-removed ROI > 0.",
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
            "## Decision Proxy Walk-Forward",
            "",
            "| selector | active dates | no-trade dates | rows | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | gates | algorithm counts |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["decision_proxy_results"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Orderbook Walk-Forward",
            "",
            "| selector | active dates | no-trade dates | rows | ROI | ROI CI | baseline ROI | excess ROI | excess CI | top5 removed ROI | gates | algorithm counts |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["orderbook_results"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is an expanding-window test over fixed v0.3 algorithm definitions.",
            "- It can trade different algorithms over time, but it cannot inspect the current/future date outcome before selecting.",
            "- Passing requires both proxy and executable orderbook versions to pass significance, baseline, and forward gates.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    variant_rows = variants.generate_variant_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(variant_rows, args.orderbook_glob)
    proxy_rows = variants.metric_rows(variant_rows, "proxy")
    orderbook_rows = variants.metric_rows(variant_rows, "orderbook")

    proxy_decisions = walk_forward(proxy_rows, source="decision_market_proxy", warmup_dates=args.warmup_dates)
    orderbook_decisions = walk_forward(orderbook_rows, source="time_aligned_orderbook", warmup_dates=args.warmup_dates)
    proxy_results = [
        evaluate_selector(proxy_decisions, selector=selector, source="decision_market_proxy", iters=args.bootstrap_iters, seed=args.seed + idx * 1000)
        for idx, selector in enumerate(("balanced", "strict_tail"))
    ]
    orderbook_results = [
        evaluate_selector(orderbook_decisions, selector=selector, source="time_aligned_orderbook", iters=args.bootstrap_iters, seed=args.seed + 3000 + idx * 1000)
        for idx, selector in enumerate(("balanced", "strict_tail"))
    ]
    confirmed_selectors = {
        item["selector"] for item in proxy_results if item["gates"]["verdict"] == "confirmed"
    } & {item["selector"] for item in orderbook_results if item["gates"]["verdict"] == "confirmed"}
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if confirmed_selectors
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    all_dates = sorted({row["event_date"] for row in variant_rows})
    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "range_rv_walkforward_selector_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "warmup_dates": args.warmup_dates,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "variant_rows": len(variant_rows),
            "event_dates": len(all_dates),
            "walkforward_dates": max(0, len(all_dates) - args.warmup_dates),
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": proxy_results,
        "orderbook_results": orderbook_results,
        "confirmed_selectors": sorted(confirmed_selectors),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} confirmed_selectors={sorted(confirmed_selectors)}")


if __name__ == "__main__":
    main()
