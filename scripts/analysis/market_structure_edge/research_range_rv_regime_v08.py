#!/usr/bin/env python3
"""Range RV regime-conditioned scanner v0.8.

Target metric:
    regime_conditioned_range_rv_alpha

This experiment pre-registers regime-aware Range RV expressions. The first
step classifies each city-day decision snapshot by the current market/model
distribution shape. The second step emits fixed YES/NO range expressions for
that regime. It uses the full fact_signal_candidates opportunity set and does
not inherit the old single-leg `eligible` gate.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_range_rv_market_shape_v05 as shape
import research_range_rv_scanner as scanner
import research_range_rv_variant_lab_v03 as variants


ROOT = Path(__file__).resolve().parents[3]
OUT_JSON_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-regime-v0-8.json"
OUT_MD_DEFAULT = ROOT / "docs" / "archive" / "analysis" / "2026-06" / "2026-06-09-range-rv-regime-v0-8.md"


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


def entropy(probs: list[float]) -> float:
    values = [p for p in probs if p > 0]
    if not values:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in values) / denom


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))


def norm(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    return shape.norm(items, key)


def max_spread_ok(legs: list[dict[str, Any]], max_spread: float) -> bool:
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
    if not max_spread_ok(legs, float(components.get("max_spread", 1.0))):
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


def window_around(items: list[dict[str, Any]], center_idx: int, width: int) -> list[dict[str, Any]]:
    half = width // 2
    start = max(0, min(center_idx - half, len(items) - width))
    return items[start : start + width]


def distribution_features(items: list[dict[str, Any]]) -> dict[str, Any]:
    model = norm(items, "model_p_yes")
    market = norm(items, "market_yes_price")
    ordered = sort_items(items)
    model_mode = max(ordered, key=lambda row: model[str(row["bracket"])])
    market_mode = max(ordered, key=lambda row: market[str(row["bracket"])])
    model_mode_idx = ordered.index(model_mode)
    market_mode_idx = ordered.index(market_mode)
    model_mode_window = window_around(ordered, model_mode_idx, min(3, len(ordered)))
    market_mode_window = window_around(ordered, market_mode_idx, min(3, len(ordered)))
    below_tail = ordered[0]
    above_tail = ordered[-1]
    inner_below = ordered[1] if len(ordered) > 1 else ordered[0]
    inner_above = ordered[-2] if len(ordered) > 1 else ordered[-1]
    model_probs = [model[str(row["bracket"])] for row in ordered]
    market_probs = [market[str(row["bracket"])] for row in ordered]
    return {
        "ordered": ordered,
        "model": model,
        "market": market,
        "model_entropy": entropy(model_probs),
        "market_entropy": entropy(market_probs),
        "model_mode": model_mode,
        "market_mode": market_mode,
        "model_mode_idx": model_mode_idx,
        "market_mode_idx": market_mode_idx,
        "model_mode_mass": model[str(model_mode["bracket"])],
        "market_mode_mass": market[str(market_mode["bracket"])],
        "model_mode_window": model_mode_window,
        "market_mode_window": market_mode_window,
        "model_mode_window_mass": sum(model[str(row["bracket"])] for row in model_mode_window),
        "market_model_window_mass": sum(market[str(row["bracket"])] for row in model_mode_window),
        "model_market_window_mass": sum(model[str(row["bracket"])] for row in market_mode_window),
        "market_mode_window_mass": sum(market[str(row["bracket"])] for row in market_mode_window),
        "below_tail": below_tail,
        "above_tail": above_tail,
        "inner_below": inner_below,
        "inner_above": inner_above,
        "model_tail_mass": model[str(below_tail["bracket"])] + model[str(above_tail["bracket"])],
        "market_tail_mass": market[str(below_tail["bracket"])] + market[str(above_tail["bracket"])],
    }


def model_confident_mode_cluster(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    legs = [dict(row, side="BUY_YES") for row in f["model_mode_window"]]
    mass_edge = f["model_mode_window_mass"] - f["market_model_window_mass"]
    confidence = max(0.0, f["market_entropy"] - f["model_entropy"])
    score = mass_edge + 0.25 * confidence
    return make(
        algorithm="regime_model_confident_mode_cluster_yes_e010",
        items=items,
        legs=legs,
        score=score,
        threshold=0.10,
        components={
            "regime": "model_confident_market_diffuse",
            "model_mode_window_mass": f["model_mode_window_mass"],
            "market_model_window_mass": f["market_model_window_mass"],
            "model_entropy": f["model_entropy"],
            "market_entropy": f["market_entropy"],
            "max_spread": 0.20,
        },
    )


def market_overconfident_mode_fade(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    mode = f["market_mode"]
    mb = str(mode["bracket"])
    score = (f["market"][mb] - f["model"][mb]) + max(0.0, f["model_entropy"] - f["market_entropy"]) * 0.20
    return make(
        algorithm="regime_market_overconfident_mode_no_e012",
        items=items,
        legs=[dict(mode, side="BUY_NO")],
        score=score,
        threshold=0.12,
        components={
            "regime": "market_overconfident_mode",
            "market_mode_mass": f["market"][mb],
            "model_mode_mass_at_market_mode": f["model"][mb],
            "model_entropy": f["model_entropy"],
            "market_entropy": f["market_entropy"],
            "max_spread": 0.16,
        },
    )


def market_diffuse_model_cluster(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    legs = [dict(row, side="BUY_YES") for row in f["model_mode_window"]]
    score = (f["model_mode_window_mass"] - f["market_model_window_mass"]) + max(
        0.0, f["market_entropy"] - f["model_entropy"]
    ) * 0.35
    return make(
        algorithm="regime_market_diffuse_model_cluster_yes_e014",
        items=items,
        legs=legs,
        score=score,
        threshold=0.14,
        components={
            "regime": "market_diffuse_model_cluster",
            "model_mode_window_mass": f["model_mode_window_mass"],
            "market_model_window_mass": f["market_model_window_mass"],
            "model_entropy": f["model_entropy"],
            "market_entropy": f["market_entropy"],
            "max_spread": 0.18,
        },
    )


def tail_fade_inner_pair(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for side_name, tail_key, inner_key in (
        ("below", "below_tail", "inner_below"),
        ("above", "above_tail", "inner_above"),
    ):
        tail = f[tail_key]
        inner = f[inner_key]
        tb = str(tail["bracket"])
        ib = str(inner["bracket"])
        score = (f["market"][tb] - f["model"][tb]) + (f["model"][ib] - f["market"][ib])
        row = make(
            algorithm=f"regime_{side_name}_tail_fade_inner_pair_e010",
            items=items,
            legs=[dict(tail, side="BUY_NO"), dict(inner, side="BUY_YES")],
            score=score,
            threshold=0.10,
            components={
                "regime": f"{side_name}_tail_overpriced_vs_inner",
                "tail_market_minus_model": f["market"][tb] - f["model"][tb],
                "inner_model_minus_market": f["model"][ib] - f["market"][ib],
                "max_spread": 0.18,
            },
        )
        if row:
            candidates.append(row)
    return top(candidates)


def center_band_vs_tail(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    ordered = f["ordered"]
    if len(ordered) < 5:
        return None
    center_idx = len(ordered) // 2
    center = window_around(ordered, center_idx, 3)
    tails = [ordered[0], ordered[-1]]
    model_center = sum(f["model"][str(row["bracket"])] for row in center)
    market_center = sum(f["market"][str(row["bracket"])] for row in center)
    model_tail = sum(f["model"][str(row["bracket"])] for row in tails)
    market_tail = sum(f["market"][str(row["bracket"])] for row in tails)
    score = (model_center - market_center) + (market_tail - model_tail)
    legs = [dict(row, side="BUY_YES") for row in center] + [dict(row, side="BUY_NO") for row in tails]
    return make(
        algorithm="regime_center_band_yes_tail_no_e012",
        items=items,
        legs=legs,
        score=score,
        threshold=0.12,
        components={
            "regime": "center_underpriced_tail_overpriced",
            "model_center_mass": model_center,
            "market_center_mass": market_center,
            "model_tail_mass": model_tail,
            "market_tail_mass": market_tail,
            "max_spread": 0.20,
        },
    )


def consensus_underpriced_single(items: list[dict[str, Any]], f: dict[str, Any]) -> dict[str, Any] | None:
    if str(f["model_mode"]["bracket"]) != str(f["market_mode"]["bracket"]):
        return None
    mode = f["model_mode"]
    mb = str(mode["bracket"])
    score = f["model"][mb] - f["market"][mb]
    return make(
        algorithm="regime_consensus_underpriced_single_yes_e008",
        items=items,
        legs=[dict(mode, side="BUY_YES")],
        score=score,
        threshold=0.08,
        components={
            "regime": "model_market_mode_consensus_underpriced",
            "model_mode_mass": f["model"][mb],
            "market_mode_mass": f["market"][mb],
            "max_spread": 0.14,
        },
    )


def generate_regime_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_items in decision_sets:
        items = sort_items(raw_items)
        if len(items) < 3:
            continue
        f = distribution_features(items)
        candidates = [
            model_confident_mode_cluster(items, f),
            market_overconfident_mode_fade(items, f),
            market_diffuse_model_cluster(items, f),
            tail_fade_inner_pair(items, f),
            center_band_vs_tail(items, f),
            consensus_underpriced_single(items, f),
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
        "# Range RV Regime-Conditioned Scanner v0.8",
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
        f"- Decision sets: `{report['input']['decision_sets']}`; generated regime rows `{report['input']['regime_rows']}`.",
        f"- Algorithms tested: `{report['input']['algorithms']}`.",
        "- Full opportunity set: old single-leg `eligible` is not used as a hard filter.",
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
            "- `regime_model_confident_mode_cluster_yes_e010`: model distribution is tighter than market; buy YES around model mode cluster.",
            "- `regime_market_overconfident_mode_no_e012`: market mode is richer and more concentrated than model; buy NO market mode.",
            "- `regime_market_diffuse_model_cluster_yes_e014`: market is diffuse while model concentrates around one cluster; buy YES cluster.",
            "- `regime_below/above_tail_fade_inner_pair_e010`: fade overpriced tail and buy adjacent inner bracket.",
            "- `regime_center_band_yes_tail_no_e012`: buy center band YES and fade tails when center is underpriced vs tails.",
            "- `regime_consensus_underpriced_single_yes_e008`: model and market share a mode, but model still prices it higher.",
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
            "- Regimes are computed from current normalized market/model probabilities only.",
            "- Baseline is each algorithm's own top unfiltered regime candidate per city-day decision snapshot.",
            "- This tests whether Range RV needs a distribution regime instead of a global rule.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    regime_rows = generate_regime_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(regime_rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in regime_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = variants.metric_rows(regime_rows, "proxy")
    orderbook_rows = variants.metric_rows(regime_rows, "orderbook")

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
        "target_metric": "regime_conditioned_range_rv_alpha",
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
            "regime_rows": len(regime_rows),
            "event_dates": len(all_dates),
            "algorithms": len({row["algorithm"] for row in regime_rows}),
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
