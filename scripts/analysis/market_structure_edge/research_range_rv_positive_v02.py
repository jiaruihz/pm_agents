#!/usr/bin/env python3
"""Pre-registered positive-profile test for Range RV v0.2.

This is intentionally narrower than the v0/v0.1 scanner. It does not search
the full range universe. The thesis under test is:

    eligible-only adjacent_3 long/inside_range_yes can express city-day range
    relative value when model probability covers both the market cost and the
    out-of-range loss risk.

The profiles below are fixed before evaluation and are all compared to the same
baseline: eligible adjacent_3 long ranges in the same split. No city/date/model
selection is allowed in this script.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-positive-v0-2.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-positive-v0-2.md"


ProfilePredicate = Callable[[dict[str, Any]], bool]


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


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def is_adj3_long(row: dict[str, Any]) -> bool:
    return (
        int(row.get("all_legs_eligible") or 0) == 1
        and row.get("range_type") == "adjacent_3"
        and row.get("direction") == "long_range"
        and row.get("range_shape") == "inside_range_yes"
    )


def profiles() -> list[dict[str, Any]]:
    return [
        {
            "profile": "base_e015",
            "definition": "adjacent_3 long, all legs eligible, abs_range_edge >= 0.15",
            "predicate": lambda row: is_adj3_long(row) and float(row["abs_range_edge"]) >= 0.15,
        },
        {
            "profile": "model_cover_070",
            "definition": "base_e015 plus model_prob_sum >= 0.70",
            "predicate": lambda row: is_adj3_long(row)
            and float(row["abs_range_edge"]) >= 0.15
            and float(row["model_prob_sum"]) >= 0.70,
        },
        {
            "profile": "cheap_cost_075",
            "definition": "base_e015 plus market_prob_sum/taker cost <= 0.75",
            "predicate": lambda row: is_adj3_long(row)
            and float(row["abs_range_edge"]) >= 0.15
            and float(row["market_prob_sum"]) <= 0.75,
        },
        {
            "profile": "model_cover_and_cost",
            "definition": "base_e015 plus model_prob_sum >= 0.70 and market_prob_sum <= 0.75",
            "predicate": lambda row: is_adj3_long(row)
            and float(row["abs_range_edge"]) >= 0.15
            and float(row["model_prob_sum"]) >= 0.70
            and float(row["market_prob_sum"]) <= 0.75,
        },
        {
            "profile": "low_loss_prob_035",
            "definition": "base_e015 plus model-implied loss_probability <= 0.35",
            "predicate": lambda row: is_adj3_long(row)
            and float(row["abs_range_edge"]) >= 0.15
            and float(row["loss_probability"]) <= 0.35,
        },
        {
            "profile": "high_edge_020",
            "definition": "adjacent_3 long, all legs eligible, abs_range_edge >= 0.20",
            "predicate": lambda row: is_adj3_long(row) and float(row["abs_range_edge"]) >= 0.20,
        },
        {
            "profile": "positive_model_ev",
            "definition": "base_e015 plus expected_pnl_unit_notional > 0",
            "predicate": lambda row: is_adj3_long(row)
            and float(row["abs_range_edge"]) >= 0.15
            and float(row["expected_pnl"]) > 0.0,
        },
    ]


def bootstrap_eval(
    selected: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    *,
    source: str,
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    selected_summary = scanner.summarize(selected, source=source)
    baseline_summary = scanner.summarize(baseline, source=source)
    selected_roi = selected_summary["taker_roi"]
    baseline_roi = baseline_summary["taker_roi"]
    excess_roi = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    boot = scanner.bootstrap_delta(selected, baseline, iters=bootstrap_iters, seed=seed)
    return {
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": excess_roi,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }


def profile_gates(train_eval: dict[str, Any], holdout_eval: dict[str, Any]) -> dict[str, str]:
    return scanner.gate_result(train_eval, holdout_eval)


def evaluate_profiles(
    rows: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    bootstrap_iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    baseline_train = [row for row in rows if is_adj3_long(row) and row["event_date"] in train_dates]
    baseline_holdout = [row for row in rows if is_adj3_long(row) and row["event_date"] in holdout_dates]
    out: list[dict[str, Any]] = []
    for idx, profile in enumerate(profiles()):
        pred: ProfilePredicate = profile["predicate"]
        train_selected = [row for row in baseline_train if pred(row)]
        holdout_selected = [row for row in baseline_holdout if pred(row)]
        train_eval = bootstrap_eval(
            train_selected,
            baseline_train,
            source=source,
            bootstrap_iters=bootstrap_iters,
            seed=seed + idx * 100,
        )
        holdout_eval = bootstrap_eval(
            holdout_selected,
            baseline_holdout,
            source=source,
            bootstrap_iters=bootstrap_iters,
            seed=seed + idx * 100 + 50,
        )
        out.append(
            {
                "profile": profile["profile"],
                "definition": profile["definition"],
                "source": source,
                "train": train_eval,
                "holdout": holdout_eval,
                "gates": profile_gates(train_eval, holdout_eval),
            }
        )
    return out


def passed(gates: dict[str, str]) -> bool:
    return gates.get("significance") == "PASS" and gates.get("baseline") == "PASS" and gates.get("forward") == "PASS"


def best_rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
    holdout = item["holdout"]
    selected = holdout["selected"]
    return (
        float(holdout["excess_roi"] if holdout["excess_roi"] is not None else -999.0),
        float(selected["drop_top5_taker_roi"] if selected["drop_top5_taker_roi"] is not None else -999.0),
        float(selected["active_event_dates"] or 0),
    )


def row_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    return (
        f"| `{item['profile']}` | {train['selected']['rows']} | {train['selected']['active_event_dates']} | "
        f"{holdout['selected']['rows']} | {holdout['selected']['active_event_dates']} | "
        f"{pct(train['selected']['taker_roi'])} | {fmt_ci(train['roi_ci95_cluster_by_event_date'])} | "
        f"{pct(train['excess_roi'])} | {fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['selected']['taker_roi'])} | {fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['excess_roi'])} | {fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | "
        f"{pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"`{item['gates']['significance']}/{item['gates']['baseline']}/{item['gates']['forward']} -> {item['gates']['verdict']}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Range RV Positive Profiles v0.2",
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
        f"- Scanner rows: candidates `{report['input']['candidate_rows']}`, ranges `{report['input']['range_rows']}`, eligible adjacent_3 long ranges `{report['input']['eligible_adjacent3_long_rows']}`.",
        "- No live_real PnL is published; CLOB coverage gate is not required for this counterfactual opportunity test.",
        "",
        "## Pre-Registered Thesis",
        "",
        "`eligible-only adjacent_3 long/inside_range_yes` is tested as a positive Range RV profile. The baseline is the full eligible adjacent_3 long universe in the same train/holdout split. Profiles are structural filters only: no city/date/model selector and no post-hoc threshold search.",
        "",
        "## Gates",
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
            "## Train / Holdout",
            "",
            f"- Split field: `event_date`.",
            f"- Train dates: `{report['split']['train_start']}` to `{report['split']['train_end']}`.",
            f"- Holdout dates: `{report['split']['holdout_start']}` to `{report['split']['holdout_end']}`.",
            f"- Profiles tested: `{report['profile_count']}`; no multiple-comparison winner is promoted unless all three gates pass.",
            "",
            "## Decision Proxy Profiles",
            "",
            "| profile | train rows | train dates | holdout rows | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["decision_proxy_profiles"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Orderbook Executable Subset",
            "",
            "Orderbook prices use latest `snapshot_ts_utc <= decision_snapshot_ts_utc`. This is an execution reality check, not the primary profile selector.",
            "",
            f"- Fully matched ranges: `{report['orderbook_coverage'].get('fully_matched_range_rows')}` / `{report['orderbook_coverage'].get('range_rows')}`.",
            "",
            "| profile | train rows | train dates | holdout rows | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["orderbook_profiles"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A profile must pass significance, baseline, and forward gates before it can be called confirmed.",
            "- Positive holdout point estimates are not enough when the bootstrap CI crosses zero or excess CI crosses zero.",
            "- `top5 removed ROI` is reported as a tail-dependency diagnostic and blocks escalation when it flips materially negative.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    range_rows = scanner.enumerate_ranges(decision_sets)
    orderbook_coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        orderbook_coverage = scanner.attach_orderbook_costs(range_rows, args.orderbook_glob)
    clean_rows = scanner.strip_private(range_rows)

    dates = sorted({row["event_date"] for row in clean_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(dates, args.train_frac)
    proxy_rows = scanner.metric_rows(clean_rows, source="proxy")
    orderbook_rows = scanner.metric_rows(clean_rows, source="orderbook")

    proxy_profiles = evaluate_profiles(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_profiles = evaluate_profiles(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )
    any_confirmed = any(passed(item["gates"]) for item in proxy_profiles) and any(
        passed(item["gates"]) for item in orderbook_profiles
    )
    gates = {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    if any_confirmed:
        gates = {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}

    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "eligible_adjacent3_inside_range_yes_relative_value_alpha",
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "candidate_rows": len(candidates),
            "decision_sets": len(decision_sets),
            "range_rows": len(clean_rows),
            "eligible_adjacent3_long_rows": sum(1 for row in clean_rows if is_adj3_long(row)),
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "train_event_dates": len(train_dates),
            "holdout_event_dates": len(holdout_dates),
        },
        "profile_count": len(profiles()),
        "decision_proxy_profiles": proxy_profiles,
        "orderbook_coverage": orderbook_coverage,
        "orderbook_profiles": orderbook_profiles,
        "gates": gates,
        "verdict": gates["verdict"],
        "best_decision_proxy_by_holdout_excess": sorted(proxy_profiles, key=best_rank_key, reverse=True)[0],
        "best_orderbook_by_holdout_excess": sorted(orderbook_profiles, key=best_rank_key, reverse=True)[0]
        if orderbook_profiles
        else None,
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} profiles={len(profiles())}")


if __name__ == "__main__":
    main()
