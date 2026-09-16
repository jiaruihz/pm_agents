#!/usr/bin/env python3
"""Range RV market-shape anomaly scanner v0.5.

Target metric:
    market_shape_range_rv_alpha

This scanner changes the starting point from model edge to market-implied
distribution shape. It pre-registers several local shape anomalies, then uses
the model only as confirmation/veto:

- local trough center cheap vs shoulders
- local peak center rich vs shoulders
- adjacent inversion between market and model shape
- below/above tail inversion vs the inner neighbor
- single-leg local trough/peak opportunities

The grain is still one candidate per algorithm per city-day decision snapshot.
Orderbook execution uses the same time-aligned rule as the earlier Range RV
scripts: latest orderbook snapshot_ts_utc <= decision_snapshot_ts_utc.
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


OUT_JSON_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-market-shape-v0-5.json"
OUT_MD_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-market-shape-v0-5.md"
MIN_HOLDOUT_ACTIVE_DATES = 5
MIN_HOLDOUT_ROWS = 10


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


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def norm(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    total = sum(float(row[key]) for row in items)
    if total <= 0:
        return {str(row["bracket"]): 0.0 for row in items}
    return {str(row["bracket"]): float(row[key]) / total for row in items}


def edge(row: dict[str, Any]) -> float:
    return float(row["model_p_yes"]) - float(row["market_yes_price"])


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
    require_eligible: bool = True,
) -> dict[str, Any] | None:
    if require_eligible and not all(int(leg.get("eligible") or 0) for leg in legs):
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


def local_trough_center(items: list[dict[str, Any]], *, require_eligible: bool = True) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates: list[dict[str, Any]] = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        trough = shoulder_market - market[c]
        model_confirm = model[c] - shoulder_model
        score = trough + model_confirm
        row = make(
            algorithm="shape_trough_center_yes_pair_e012",
            items=items,
            legs=[dict(center, side="BUY_YES"), dict(left, side="BUY_NO"), dict(right, side="BUY_NO")],
            score=score,
            threshold=0.12,
            components={
                "shape": "local_trough_center",
                "market_trough": trough,
                "model_confirm": model_confirm,
                "max_spread": 0.20,
            },
            require_eligible=require_eligible,
        )
        if row:
            candidates.append(row)
    return top(candidates)


def local_peak_center(items: list[dict[str, Any]], *, require_eligible: bool = True) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates: list[dict[str, Any]] = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        peak = market[c] - shoulder_market
        model_confirm = shoulder_model - model[c]
        score = peak + model_confirm
        row = make(
            algorithm="shape_peak_center_no_pair_e012",
            items=items,
            legs=[dict(center, side="BUY_NO"), dict(left, side="BUY_YES"), dict(right, side="BUY_YES")],
            score=score,
            threshold=0.12,
            components={
                "shape": "local_peak_center",
                "market_peak": peak,
                "model_confirm": model_confirm,
                "max_spread": 0.20,
            },
            require_eligible=require_eligible,
        )
        if row:
            candidates.append(row)
    return top(candidates)


def adjacent_inversion(items: list[dict[str, Any]], *, require_eligible: bool = True) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates: list[dict[str, Any]] = []
    for left, right in zip(items, items[1:]):
        lb = str(left["bracket"])
        rb = str(right["bracket"])
        market_gap = market[rb] - market[lb]
        model_gap = model[rb] - model[lb]
        gap_error = market_gap - model_gap
        if gap_error > 0:
            cheap, rich = left, right
            score = gap_error
        else:
            cheap, rich = right, left
            score = -gap_error
        row = make(
            algorithm="shape_adjacent_inversion_pair_e010",
            items=items,
            legs=[dict(cheap, side="BUY_YES"), dict(rich, side="BUY_NO")],
            score=score,
            threshold=0.10,
            components={
                "shape": "adjacent_market_model_inversion",
                "market_gap": market_gap,
                "model_gap": model_gap,
                "max_spread": 0.18,
            },
            require_eligible=require_eligible,
        )
        if row:
            candidates.append(row)
    return top(candidates)


def tail_inversion(items: list[dict[str, Any]], *, require_eligible: bool = True) -> dict[str, Any] | None:
    if len(items) < 3:
        return None
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates: list[dict[str, Any]] = []
    tail_pairs = [
        ("below", items[0], items[1]),
        ("above", items[-1], items[-2]),
    ]
    for side_name, tail, inner in tail_pairs:
        tb = str(tail["bracket"])
        ib = str(inner["bracket"])
        market_tail_premium = market[tb] - market[ib]
        model_tail_discount = model[ib] - model[tb]
        score = market_tail_premium + model_tail_discount
        row = make(
            algorithm=f"shape_{side_name}_tail_inversion_pair_e010",
            items=items,
            legs=[dict(tail, side="BUY_NO"), dict(inner, side="BUY_YES")],
            score=score,
            threshold=0.10,
            components={
                "shape": f"{side_name}_tail_market_inversion",
                "market_tail_premium": market_tail_premium,
                "model_tail_discount": model_tail_discount,
                "max_spread": 0.18,
            },
            require_eligible=require_eligible,
        )
        if row:
            candidates.append(row)
    return top(candidates)


def single_shape(items: list[dict[str, Any]], *, require_eligible: bool = True) -> list[dict[str, Any]]:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates: list[dict[str, Any]] = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        trough_score = (shoulder_market - market[c]) + (model[c] - shoulder_model)
        peak_score = (market[c] - shoulder_market) + (shoulder_model - model[c])
        trough = make(
            algorithm="shape_single_trough_yes_e010",
            items=items,
            legs=[dict(center, side="BUY_YES")],
            score=trough_score,
            threshold=0.10,
            components={
                "shape": "single_local_trough",
                "max_spread": 0.16,
            },
            require_eligible=require_eligible,
        )
        if trough:
            candidates.append(trough)
        peak = make(
            algorithm="shape_single_peak_no_e010",
            items=items,
            legs=[dict(center, side="BUY_NO")],
            score=peak_score,
            threshold=0.10,
            components={
                "shape": "single_local_peak",
                "max_spread": 0.16,
            },
            require_eligible=require_eligible,
        )
        if peak:
            candidates.append(peak)
    best_by_algo: dict[str, dict[str, Any]] = {}
    for row in candidates:
        existing = best_by_algo.get(row["algorithm"])
        if existing is None or (row["score"], row["expected_pnl"]) > (existing["score"], existing["expected_pnl"]):
            best_by_algo[row["algorithm"]] = row
    return list(best_by_algo.values())


def generate_shape_rows(decision_sets: list[list[dict[str, Any]]], *, require_eligible: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for items in decision_sets:
        ordered = sorted(items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        candidates = [
            local_trough_center(ordered, require_eligible=require_eligible),
            local_peak_center(ordered, require_eligible=require_eligible),
            adjacent_inversion(ordered, require_eligible=require_eligible),
            tail_inversion(ordered, require_eligible=require_eligible),
            *single_shape(ordered, require_eligible=require_eligible),
        ]
        rows.extend(row for row in candidates if row is not None)
    return rows


def tighten_gate(item: dict[str, Any]) -> None:
    holdout = item["holdout"]["selected"]
    reasons: list[str] = []
    if holdout["active_event_dates"] < MIN_HOLDOUT_ACTIVE_DATES:
        item["gates"]["forward"] = "FAIL"
        reasons.append(f"holdout_active_dates<{MIN_HOLDOUT_ACTIVE_DATES}")
    if holdout["rows"] < MIN_HOLDOUT_ROWS:
        item["gates"]["forward"] = "FAIL"
        reasons.append(f"holdout_rows<{MIN_HOLDOUT_ROWS}")
    drop_top5 = holdout.get("drop_top5_taker_roi")
    if drop_top5 is None or drop_top5 <= 0:
        item["gates"]["forward"] = "FAIL"
        reasons.append("holdout_top5_removed_roi<=0_or_not_available")
    item["gate_reasons"] = reasons
    item["gates"]["verdict"] = (
        "confirmed"
        if item["gates"]["significance"] == "PASS"
        and item["gates"]["baseline"] == "PASS"
        and item["gates"]["forward"] == "PASS"
        else "inconclusive"
    )


def evaluate(
    rows: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    results = variants.evaluate_algorithms(
        rows,
        source=source,
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=iters,
        seed=seed,
    )
    for item in results:
        tighten_gate(item)
    return results


def passed(item: dict[str, Any]) -> bool:
    gates = item["gates"]
    return gates["significance"] == "PASS" and gates["baseline"] == "PASS" and gates["forward"] == "PASS"


def rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(item["holdout"]["excess_roi"] if item["holdout"]["excess_roi"] is not None else -999.0),
        float(item["holdout"]["selected"]["drop_top5_taker_roi"] if item["holdout"]["selected"]["drop_top5_taker_roi"] is not None else -999.0),
        float(item["holdout"]["selected"]["active_event_dates"] or 0),
    )


def row_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    reasons = ",".join(item.get("gate_reasons") or [])
    return (
        f"| `{item['algorithm']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {item['holdout_family_rows']} | {holdout['selected']['rows']} | "
        f"{holdout['selected']['active_event_dates']} | {pct(train['selected']['taker_roi'])} | "
        f"{fmt_ci(train['roi_ci95_cluster_by_event_date'])} | {pct(train['excess_roi'])} | "
        f"{fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['taker_roi'])} | "
        f"{fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | {pct(holdout['excess_roi'])} | "
        f"{fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` | `{reasons}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# {report.get('title', 'Range RV Market Shape Scanner v0.5')}",
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
        f"- Decision sets: `{report['input']['decision_sets']}`; generated strategy rows `{report['input']['shape_rows']}`.",
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
            "- `shape_trough_center_yes_pair_e012`: center bracket is cheap vs shoulders; buy YES center and BUY_NO shoulders.",
            "- `shape_peak_center_no_pair_e012`: center bracket is rich vs shoulders; buy NO center and BUY_YES shoulders.",
            "- `shape_adjacent_inversion_pair_e010`: adjacent market slope disagrees with model slope; buy YES cheap side and BUY_NO rich side.",
            "- `shape_below_tail_inversion_pair_e010` / `shape_above_tail_inversion_pair_e010`: tail richer than inner neighbor while model prefers inner; buy NO tail and YES inner.",
            "- `shape_single_trough_yes_e010` / `shape_single_peak_no_e010`: single-leg version of local trough/peak when expression as one leg is cleaner.",
            "",
            f"Forward gate requires holdout active_dates >= {MIN_HOLDOUT_ACTIVE_DATES}, rows >= {MIN_HOLDOUT_ROWS}, and top5-removed ROI > 0.",
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
            "- Baseline is each algorithm's own top unfiltered shape candidate per city-day decision snapshot.",
            "- The selected rule is fixed by anomaly thresholds; no city/date/model post-selection is used.",
            "- This is intentionally different from v0.3/v0.4: market shape anomaly first, model confirmation second.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    shape_rows = generate_shape_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(shape_rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in shape_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = variants.metric_rows(shape_rows, "proxy")
    orderbook_rows = variants.metric_rows(shape_rows, "orderbook")

    proxy_results = evaluate(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = evaluate(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )

    confirmed_algorithms = {
        item["algorithm"] for item in proxy_results if passed(item)
    } & {item["algorithm"] for item in orderbook_results if passed(item)}
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if confirmed_algorithms
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "market_shape_range_rv_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "split_date": split_date,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "min_holdout_active_dates": MIN_HOLDOUT_ACTIVE_DATES,
            "min_holdout_rows": MIN_HOLDOUT_ROWS,
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
        "decision_proxy_results": sorted(proxy_results, key=rank_key, reverse=True),
        "orderbook_results": sorted(orderbook_results, key=rank_key, reverse=True),
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
