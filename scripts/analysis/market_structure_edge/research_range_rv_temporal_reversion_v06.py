#!/usr/bin/env python3
"""Range RV temporal reversion scanner v0.6.

Target metric:
    temporal_range_rv_reversion_alpha

This experiment asks whether the market distribution overreacts between two
consecutive snapshots for the same city-day. It compares normalized market
probability movement with normalized model probability movement and expresses
the largest residual as single-leg, adjacent pair, adjacent range, or tail
relative-value trades.

Only information available at the current decision snapshot is used. The prior
snapshot must be strictly earlier; orderbook execution still requires
orderbook_snapshot_ts <= decision_snapshot_ts_utc.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_market_shape_v05 as shape  # noqa: E402
import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-temporal-reversion-v0-6.json"
OUT_MD_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-temporal-reversion-v0-6.md"


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


def norm_for(rows: list[dict[str, Any]], key: str, brackets: set[str]) -> dict[str, float]:
    filtered = [row for row in rows if str(row["bracket"]) in brackets]
    total = sum(float(row[key]) for row in filtered)
    if total <= 0:
        return {str(row["bracket"]): 0.0 for row in filtered}
    return {str(row["bracket"]): float(row[key]) / total for row in filtered}


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
    if not all(int(leg.get("eligible") or 0) for leg in legs):
        return None
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


def top(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: (row["score"], row["expected_pnl"]), reverse=True)[0]


def residual_rows(prev_items: list[dict[str, Any]], cur_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev_by_bracket = {str(row["bracket"]): row for row in prev_items}
    cur_by_bracket = {str(row["bracket"]): row for row in cur_items}
    common = set(prev_by_bracket) & set(cur_by_bracket)
    if len(common) < 3:
        return []
    prev_market = norm_for(prev_items, "market_yes_price", common)
    cur_market = norm_for(cur_items, "market_yes_price", common)
    prev_model = norm_for(prev_items, "model_p_yes", common)
    cur_model = norm_for(cur_items, "model_p_yes", common)
    out: list[dict[str, Any]] = []
    for bracket in common:
        current = cur_by_bracket[bracket]
        market_delta = cur_market[bracket] - prev_market[bracket]
        model_delta = cur_model[bracket] - prev_model[bracket]
        enriched = dict(current)
        enriched["temporal_market_delta"] = market_delta
        enriched["temporal_model_delta"] = model_delta
        enriched["temporal_residual"] = market_delta - model_delta
        out.append(enriched)
    return sorted(out, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))


def single_reversion(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in items:
        residual = float(row["temporal_residual"])
        side = "BUY_NO" if residual > 0 else "BUY_YES"
        candidate = make(
            algorithm="temporal_single_reversion_e012",
            items=items,
            legs=[dict(row, side=side)],
            score=abs(residual),
            threshold=0.12,
            components={
                "shape": "single_temporal_residual_reversion",
                "residual": residual,
                "max_spread": 0.16,
            },
        )
        if candidate:
            candidates.append(candidate)
    return top(candidates)


def adjacent_spread_reversion(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for left, right in zip(items, items[1:]):
        left_res = float(left["temporal_residual"])
        right_res = float(right["temporal_residual"])
        if left_res <= right_res:
            cheap, rich = left, right
            score = right_res - left_res
        else:
            cheap, rich = right, left
            score = left_res - right_res
        candidate = make(
            algorithm="temporal_adjacent_spread_reversion_e015",
            items=items,
            legs=[dict(cheap, side="BUY_YES"), dict(rich, side="BUY_NO")],
            score=score,
            threshold=0.15,
            components={
                "shape": "adjacent_temporal_residual_spread",
                "left_residual": left_res,
                "right_residual": right_res,
                "max_spread": 0.18,
            },
        )
        if candidate:
            candidates.append(candidate)
    return top(candidates)


def adjacent_range_reversion(items: list[dict[str, Any]], width: int, threshold: float) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for start in range(0, len(items) - width + 1):
        block = items[start : start + width]
        residual_sum = sum(float(row["temporal_residual"]) for row in block)
        side = "BUY_NO" if residual_sum > 0 else "BUY_YES"
        candidate = make(
            algorithm=f"temporal_adj{width}_range_reversion_e{int(threshold * 1000):03d}",
            items=items,
            legs=[dict(row, side=side) for row in block],
            score=abs(residual_sum),
            threshold=threshold,
            components={
                "shape": f"adjacent_{width}_temporal_residual_range",
                "residual_sum": residual_sum,
                "max_spread": 0.20,
            },
        )
        if candidate:
            candidates.append(candidate)
    return top(candidates)


def tail_reversion(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for tail_name, tail, inner in (("below", items[0], items[1]), ("above", items[-1], items[-2])):
        spread = float(tail["temporal_residual"]) - float(inner["temporal_residual"])
        if spread > 0:
            legs = [dict(tail, side="BUY_NO"), dict(inner, side="BUY_YES")]
        else:
            legs = [dict(tail, side="BUY_YES"), dict(inner, side="BUY_NO")]
        candidate = make(
            algorithm=f"temporal_{tail_name}_tail_reversion_e010",
            items=items,
            legs=legs,
            score=abs(spread),
            threshold=0.10,
            components={
                "shape": f"{tail_name}_tail_temporal_residual_spread",
                "tail_inner_spread": spread,
                "max_spread": 0.18,
            },
        )
        if candidate:
            candidates.append(candidate)
    return top(candidates)


def generate_temporal_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_city_day: dict[tuple[str, str], list[list[dict[str, Any]]]] = defaultdict(list)
    for items in decision_sets:
        first = items[0]
        by_city_day[(str(first["city"]), str(first["event_date"]))].append(items)
    rows: list[dict[str, Any]] = []
    for snapshots in by_city_day.values():
        ordered_snapshots = sorted(snapshots, key=lambda items: scanner.parse_ts(str(items[0]["decision_snapshot_ts_utc"])))
        for prev_items, cur_items in zip(ordered_snapshots, ordered_snapshots[1:]):
            items = residual_rows(prev_items, cur_items)
            if not items:
                continue
            candidates = [
                single_reversion(items),
                adjacent_spread_reversion(items),
                adjacent_range_reversion(items, 2, 0.12),
                adjacent_range_reversion(items, 3, 0.15),
                tail_reversion(items),
            ]
            rows.extend(row for row in candidates if row is not None)
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
        "# Range RV Temporal Reversion Scanner v0.6",
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
        f"- Decision sets: `{report['input']['decision_sets']}`; generated temporal rows `{report['input']['temporal_rows']}`.",
        f"- Algorithms tested: `{report['input']['algorithms']}`.",
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
            "- `temporal_single_reversion_e012`: largest bracket residual; BUY_NO if market over-moved up, BUY_YES if it over-moved down.",
            "- `temporal_adjacent_spread_reversion_e015`: adjacent residual spread; BUY_YES cheap residual side and BUY_NO rich residual side.",
            "- `temporal_adj2_range_reversion_e120` / `temporal_adj3_range_reversion_e150`: residual over/under-move across contiguous ranges.",
            "- `temporal_below_tail_reversion_e010` / `temporal_above_tail_reversion_e010`: tail residual vs inner neighbor residual.",
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
            "- This experiment uses only the previous snapshot of the same city-day plus the current decision snapshot.",
            "- Baseline is each algorithm's own top unfiltered temporal candidate per city-day decision snapshot.",
            "- The result is evaluated with the same train/holdout split, cluster bootstrap, and executable orderbook subset as the prior Range RV scripts.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    temporal_rows = generate_temporal_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(temporal_rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in temporal_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = variants.metric_rows(temporal_rows, "proxy")
    orderbook_rows = variants.metric_rows(temporal_rows, "orderbook")

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
        "target_metric": "temporal_range_rv_reversion_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "temporal_rows": len(temporal_rows),
            "event_dates": len(all_dates),
            "algorithms": len({row["algorithm"] for row in temporal_rows}),
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
