#!/usr/bin/env python3
"""Range dual-expression experiment v0.

Compare equivalent range expressions:
- buy YES inside a contiguous range
- buy NO outside the same range
- choose the cheaper effective expression per range

The key accounting identity is:

    payoff(BUY_NO outside range) = (outside_legs - 1) + payoff(range YES)

So the effective range price of outside-NO is:

    sum(NO costs outside) - (outside_legs - 1)

The script reports actual gross-cost ROI for execution realism, while selection
uses effective range edge. Settlements are inferred within a city-day bracket
set when exactly one bracket has final_yes=1 in fact_signal_candidates.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_adjacent3_union_flexible_v02 as union_v02  # noqa: E402
import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


TARGET_METRIC = "range_dual_expression_alpha_v0"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-range-dual-expression-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-range-dual-expression-v0.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260611)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def infer_settlement(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    winners = [row for row in items if row.get("final_yes") is not None and float(row["final_yes"]) >= 0.5]
    if len(winners) != 1:
        if all(row.get("final_yes") is not None for row in items):
            return [dict(row) for row in items], "complete_direct"
        return [dict(row) for row in items], "incomplete"
    winner_bracket = str(winners[0]["bracket"])
    out = []
    for row in items:
        cloned = dict(row)
        cloned["final_yes"] = 1.0 if str(row["bracket"]) == winner_bracket else 0.0
        cloned["settlement_status"] = "settled"
        cloned["settlement_inferred_from_city_day_winner"] = int(row.get("final_yes") is None)
        out.append(cloned)
    return out, "inferred_from_one_winner"


def leg_cost(side: str, row: dict[str, Any]) -> float:
    yes_price = float(row["market_yes_price"])
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def range_model_mass(items: list[dict[str, Any]], inside: list[dict[str, Any]]) -> float:
    total = sum(float(row["model_p_yes"]) for row in items)
    if total <= 0:
        return 0.0
    return sum(float(row["model_p_yes"]) for row in inside) / total


def range_market_mass(items: list[dict[str, Any]], inside: list[dict[str, Any]]) -> float:
    total = sum(float(row["market_yes_price"]) for row in items)
    if total <= 0:
        return 0.0
    return sum(float(row["market_yes_price"]) for row in inside) / total


def yes_cost(inside: list[dict[str, Any]]) -> float:
    return sum(leg_cost("BUY_YES", row) for row in inside)


def outside_no_cost(outside: list[dict[str, Any]]) -> float:
    return sum(leg_cost("BUY_NO", row) for row in outside)


def outside_no_effective_cost(outside: list[dict[str, Any]]) -> float:
    if not outside:
        return float("inf")
    return outside_no_cost(outside) - (len(outside) - 1)


def settled_ready(legs: list[dict[str, Any]]) -> bool:
    return bool(legs) and all(leg.get("final_yes") is not None for leg in legs)


def make_range_candidate(
    *,
    algorithm: str,
    items: list[dict[str, Any]],
    inside: list[dict[str, Any]],
    expression: str,
    score: float,
    effective_cost: float,
    selected: bool,
) -> dict[str, Any] | None:
    inside_brackets = {str(row["bracket"]) for row in inside}
    outside = [row for row in items if str(row["bracket"]) not in inside_brackets]
    if expression == "inside_yes":
        legs = [dict(row, side="BUY_YES") for row in inside]
    elif expression == "outside_no":
        legs = [dict(row, side="BUY_NO") for row in outside]
    else:
        raise ValueError(f"unknown expression {expression}")
    if not settled_ready(legs):
        return None
    row = variants.make_candidate(
        algorithm=algorithm,
        decision_items=items,
        legs=legs,
        score=score,
        threshold=0.0,
        components={
            "expression": expression,
            "inside": [row["bracket"] for row in inside],
            "outside_legs": len(outside),
            "effective_range_cost": effective_cost,
            "range_model_mass_norm": range_model_mass(items, inside),
            "range_market_mass_norm": range_market_mass(items, inside),
        },
    )
    row["selected"] = int(selected)
    row["effective_range_cost"] = effective_cost
    row["range_model_mass_norm"] = range_model_mass(items, inside)
    row["range_market_mass_norm"] = range_market_mass(items, inside)
    row["expression"] = expression
    row["settlement_inferred_legs"] = sum(int(leg.get("settlement_inferred_from_city_day_winner") or 0) for leg in legs)
    return row


def best_for_width(items: list[dict[str, Any]], width: int, mode_only: bool, expression_policy: str) -> dict[str, Any] | None:
    if len(items) < width:
        return None
    ranges: list[list[dict[str, Any]]] = []
    if mode_only:
        mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
        start = min(max(0, mode_idx - width // 2), len(items) - width)
        ranges = [items[start : start + width]]
    else:
        ranges = [items[start : start + width] for start in range(0, len(items) - width + 1)]

    candidates = []
    for inside in ranges:
        inside_brackets = {str(row["bracket"]) for row in inside}
        outside = [row for row in items if str(row["bracket"]) not in inside_brackets]
        model_mass = range_model_mass(items, inside)
        cost_yes = yes_cost(inside)
        cost_no_eff = outside_no_effective_cost(outside)
        score_yes = model_mass - cost_yes
        score_no = model_mass - cost_no_eff
        if expression_policy == "inside_yes":
            chosen_expression = "inside_yes"
            chosen_score = score_yes
            chosen_effective_cost = cost_yes
        elif expression_policy == "outside_no":
            if not outside:
                continue
            chosen_expression = "outside_no"
            chosen_score = score_no
            chosen_effective_cost = cost_no_eff
        elif expression_policy == "cheaper":
            if outside and cost_no_eff < cost_yes:
                chosen_expression = "outside_no"
                chosen_score = score_no
                chosen_effective_cost = cost_no_eff
            else:
                chosen_expression = "inside_yes"
                chosen_score = score_yes
                chosen_effective_cost = cost_yes
        else:
            raise ValueError(expression_policy)
        row = make_range_candidate(
            algorithm=f"{'mode' if mode_only else 'best'}_width{width}_{expression_policy}",
            items=items,
            inside=inside,
            expression=chosen_expression,
            score=chosen_score,
            effective_cost=chosen_effective_cost,
            selected=chosen_score > 0,
        )
        if row is not None:
            candidates.append(row)
    return variants.top_or_none(candidates)


def generate_rows(decision_sets: list[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows = []
    settlement_modes: Counter[str] = Counter()
    for raw_items in decision_sets:
        inferred_items, mode = infer_settlement(raw_items)
        settlement_modes[mode] += 1
        if mode == "incomplete":
            continue
        items = sorted(inferred_items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        for width in (1, 2, 3):
            for mode_only in (False, True):
                for expression_policy in ("inside_yes", "outside_no", "cheaper"):
                    row = best_for_width(items, width, mode_only, expression_policy)
                    if row is not None and row.get("decision_dt") is not None:
                        rows.append(row)
    return rows, settlement_modes


def tighten_gate(item: dict[str, Any]) -> None:
    holdout = item["holdout"]["selected"]
    reasons = []
    if (holdout.get("active_event_dates") or 0) < 5:
        item["gates"]["forward"] = "FAIL"
        reasons.append("holdout_dates<5")
    if (holdout.get("rows") or 0) < 20:
        item["gates"]["forward"] = "FAIL"
        reasons.append("holdout_rows<20")
    drop_top5 = holdout.get("drop_top5_taker_roi")
    if drop_top5 is None or drop_top5 <= 0:
        item["gates"]["forward"] = "FAIL"
        reasons.append("top5_removed_not_positive_or_NA")
    item["gate_reasons"] = reasons
    item["gates"]["verdict"] = "confirmed" if (
        item["gates"]["significance"] == "PASS"
        and item["gates"]["baseline"] == "PASS"
        and item["gates"]["forward"] == "PASS"
    ) else "inconclusive"


def evaluate(rows: list[dict[str, Any]], *, source: str, train_dates: set[str], holdout_dates: set[str], iters: int, seed: int) -> list[dict[str, Any]]:
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
    return sorted(
        results,
        key=lambda item: (
            item["holdout"].get("excess_roi") if item["holdout"].get("excess_roi") is not None else -999,
            item["holdout"]["selected"].get("active_event_dates") or 0,
            item["holdout"]["selected"].get("rows") or 0,
        ),
        reverse=True,
    )


def result_rows(results: list[dict[str, Any]], limit: int = 18) -> list[list[Any]]:
    out = []
    for item in results[:limit]:
        tr = item["train"]["selected"]
        ho = item["holdout"]["selected"]
        gates = item["gates"]
        out.append([
            f"`{item['algorithm']}`",
            item["train_family_rows"] + item["holdout_family_rows"],
            tr["rows"],
            tr["active_event_dates"],
            pct(tr["taker_roi"]),
            pct(item["train"].get("excess_roi")),
            fmt_ci(item["train"].get("excess_roi_ci95_cluster_by_event_date")),
            ho["rows"],
            ho["active_event_dates"],
            pct(ho["taker_roi"]),
            pct(item["holdout"].get("excess_roi")),
            fmt_ci(item["holdout"].get("excess_roi_ci95_cluster_by_event_date")),
            pct(ho.get("drop_top5_taker_roi")),
            f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}`",
            ",".join(item.get("gate_reasons") or []),
        ])
    return out


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Range Dual Expression v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 一句话",
        "",
        f"- 这次把同一个温度区间同时用 inside YES、outside NO、choose-cheaper 三种方式表达；verdict=`{report['verdict']}`。",
        f"- generated rows: `{report['input']['strategy_rows']}`; algorithms: `{report['input']['algorithms']}`; orderbook matched: `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`。",
        "- outside NO 与 inside YES 是等效区间表达，但 outside NO 通常腿数更多、gross cost 更大；本报告同时保留 gross ROI 和 effective range cost 选择逻辑。",
        "",
        "## 数据快照",
        "",
        f"- fact_signal_candidates rows: `{report['data_self_check']['fact_signal_candidate_coverage']['rows']}`; fact built: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- union rows: `{report['input']['union_rows']}`; decision sets: `{report['input']['decision_sets']}`; settled/inferred decision sets: `{report['input']['usable_decision_sets']}`。",
        f"- train: `{report['split']['train_start']}` -> `{report['split']['train_end']}`; holdout: `{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`。",
        f"- settlement inference modes: `{report['settlement_modes']}`。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["fact_trades_max_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["fact_trades_by_class"],
                "settlement_status_distribution": report["data_self_check"]["fact_trades_by_settlement_status"],
                "candidate_coverage": report["data_self_check"]["fact_signal_candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["clob_order_fill_join"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Decision Proxy Results",
        "",
        table(
            ["algorithm", "family", "train rows", "train dates", "train ROI", "train excess", "train excess CI", "holdout rows", "holdout dates", "holdout ROI", "holdout excess", "holdout excess CI", "top5 removed", "gates", "reasons"],
            result_rows(report["decision_proxy_results"]),
        ),
        "",
        "## Time-Aligned Orderbook Results",
        "",
        "Orderbook uses latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; all selected legs must match.",
        "",
        table(
            ["algorithm", "family", "train rows", "train dates", "train ROI", "train excess", "train excess CI", "holdout rows", "holdout dates", "holdout ROI", "holdout excess", "holdout excess CI", "top5 removed", "gates", "reasons"],
            result_rows(report["orderbook_results"]),
        ),
        "",
        "## 三门状态",
        "",
        table(
            ["gate", "status", "reason"],
            [
                ["significance", report["gates"]["significance"], "没有表达在 proxy/orderbook 两层同时给出稳定正超额 CI。"],
                ["baseline", report["gates"]["baseline"], "outside NO/choose-cheaper 没有稳定超过同 family baseline。"],
                ["forward", report["gates"]["forward"], "holdout/top5/orderbook 后仍不稳定。"],
            ],
        ),
        "",
        f"结论：`{report['verdict']}`。不改 live、不加 size、不扩池。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    union_rows = union_v02.load_union_rows(conn)
    decision_sets = union_v02.group_union_decision_sets(union_rows)
    strategy_rows, settlement_modes = generate_rows(decision_sets)
    all_dates = sorted({row["event_date"] for row in strategy_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(strategy_rows, args.orderbook_glob)
    proxy_results = evaluate(
        variants.metric_rows(strategy_rows, "proxy"),
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = evaluate(
        variants.metric_rows(strategy_rows, "orderbook"),
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )
    confirmed = {
        item["algorithm"]
        for item in proxy_results
        if item["gates"]["verdict"] == "confirmed"
    } & {
        item["algorithm"]
        for item in orderbook_results
        if item["gates"]["verdict"] == "confirmed"
    }
    gates = {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"} if confirmed else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    db_path = Path(args.db_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
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
            "union_rows": len(union_rows),
            "decision_sets": len(decision_sets),
            "usable_decision_sets": sum(v for k, v in settlement_modes.items() if k != "incomplete"),
            "strategy_rows": len(strategy_rows),
            "algorithms": len({row["algorithm"] for row in strategy_rows}),
            "event_dates": len(all_dates),
        },
        "settlement_modes": dict(settlement_modes),
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": proxy_results,
        "orderbook_results": orderbook_results,
        "confirmed_algorithms": sorted(confirmed),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(render_md(report), encoding="utf-8")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} strategy_rows={len(strategy_rows)} algorithms={report['input']['algorithms']}")


if __name__ == "__main__":
    main()
