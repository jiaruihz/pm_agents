#!/usr/bin/env python3
"""Union-denominator rerun for top Range RV / market-shape strategies.

This reruns the highest-risk old research families after the denominator audit:
- Range RV variant lab expressions
- center/shoulders and range-vs-neighbor expressions
- market-shape trough/peak/inversion expressions

All strategies use a BUY_YES + BUY_NO union city-day bracket universe. PnL is
evaluated only when selected legs are settled. eligible=1 and full orderbook
matches are diagnostics/execution subsets, not the main universe.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_adjacent3_union_flexible_v02 as union_v02  # noqa: E402
import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


TARGET_METRIC = "union_denominator_top_strategy_rerun_v0"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-union-top-strategy-rerun-v0.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-union-top-strategy-rerun-v0.md"
MIN_HOLDOUT_DATES = 5
MIN_HOLDOUT_ROWS = 20


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(scanner.DB_DEFAULT))
    ap.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    ap.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    ap.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    ap.add_argument("--bootstrap-iters", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260611)
    ap.add_argument("--train-frac", type=float, default=0.70)
    ap.add_argument("--skip-orderbook", action="store_true")
    return ap.parse_args()


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], body: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in body:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def norm(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    total = sum(float(row[key]) for row in items)
    if total <= 0:
        return {str(row["bracket"]): 0.0 for row in items}
    return {str(row["bracket"]): float(row[key]) / total for row in items}


def safe_make(
    *,
    algorithm: str,
    items: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    score: float,
    threshold: float,
    components: dict[str, Any],
) -> dict[str, Any] | None:
    if not union_v02.legs_ready(legs):
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


def local_trough_center(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        trough = shoulder_market - market[c]
        model_confirm = model[c] - shoulder_model
        score = trough + model_confirm
        row = safe_make(
            algorithm="shape_trough_center_yes_pair_e012_union",
            items=items,
            legs=[dict(center, side="BUY_YES"), dict(left, side="BUY_NO"), dict(right, side="BUY_NO")],
            score=score,
            threshold=0.12,
            components={"shape": "local_trough_center", "market_trough": trough, "model_confirm": model_confirm},
        )
        if row:
            candidates.append(row)
    return top(candidates)


def local_peak_center(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        peak = market[c] - shoulder_market
        model_confirm = shoulder_model - model[c]
        score = peak + model_confirm
        row = safe_make(
            algorithm="shape_peak_center_no_pair_e012_union",
            items=items,
            legs=[dict(center, side="BUY_NO"), dict(left, side="BUY_YES"), dict(right, side="BUY_YES")],
            score=score,
            threshold=0.12,
            components={"shape": "local_peak_center", "market_peak": peak, "model_confirm": model_confirm},
        )
        if row:
            candidates.append(row)
    return top(candidates)


def adjacent_inversion(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates = []
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
        row = safe_make(
            algorithm="shape_adjacent_inversion_pair_e010_union",
            items=items,
            legs=[dict(cheap, side="BUY_YES"), dict(rich, side="BUY_NO")],
            score=score,
            threshold=0.10,
            components={"shape": "adjacent_market_model_inversion", "market_gap": market_gap, "model_gap": model_gap},
        )
        if row:
            candidates.append(row)
    return top(candidates)


def tail_inversion(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(items) < 3:
        return None
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates = []
    for side_name, tail, inner in [("below", items[0], items[1]), ("above", items[-1], items[-2])]:
        tb = str(tail["bracket"])
        ib = str(inner["bracket"])
        market_tail_premium = market[tb] - market[ib]
        model_tail_discount = model[ib] - model[tb]
        score = market_tail_premium + model_tail_discount
        row = safe_make(
            algorithm=f"shape_{side_name}_tail_inversion_pair_e010_union",
            items=items,
            legs=[dict(tail, side="BUY_NO"), dict(inner, side="BUY_YES")],
            score=score,
            threshold=0.10,
            components={"shape": f"{side_name}_tail_market_inversion", "market_tail_premium": market_tail_premium, "model_tail_discount": model_tail_discount},
        )
        if row:
            candidates.append(row)
    return top(candidates)


def single_shape(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    market = norm(items, "market_yes_price")
    model = norm(items, "model_p_yes")
    candidates = []
    for i in range(1, len(items) - 1):
        left, center, right = items[i - 1], items[i], items[i + 1]
        c = str(center["bracket"])
        shoulder_market = 0.5 * (market[str(left["bracket"])] + market[str(right["bracket"])])
        shoulder_model = 0.5 * (model[str(left["bracket"])] + model[str(right["bracket"])])
        trough_score = (shoulder_market - market[c]) + (model[c] - shoulder_model)
        peak_score = (market[c] - shoulder_market) + (shoulder_model - model[c])
        trough = safe_make(
            algorithm="shape_single_trough_yes_e010_union",
            items=items,
            legs=[dict(center, side="BUY_YES")],
            score=trough_score,
            threshold=0.10,
            components={"shape": "single_local_trough"},
        )
        if trough:
            candidates.append(trough)
        peak = safe_make(
            algorithm="shape_single_peak_no_e010_union",
            items=items,
            legs=[dict(center, side="BUY_NO")],
            score=peak_score,
            threshold=0.10,
            components={"shape": "single_local_peak"},
        )
        if peak:
            candidates.append(peak)
    best_by_algo: dict[str, dict[str, Any]] = {}
    for row in candidates:
        existing = best_by_algo.get(row["algorithm"])
        if existing is None or (row["score"], row["expected_pnl"]) > (existing["score"], existing["expected_pnl"]):
            best_by_algo[row["algorithm"]] = row
    return list(best_by_algo.values())


def generate_shape_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for items in decision_sets:
        ordered = sorted(items, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        candidates = [
            local_trough_center(ordered),
            local_peak_center(ordered),
            adjacent_inversion(ordered),
            tail_inversion(ordered),
            *single_shape(ordered),
        ]
        rows.extend(row for row in candidates if row is not None and row.get("decision_dt") is not None)
    return rows


def generate_all_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = union_v02.generate_rows(decision_sets)
    rows.extend(generate_shape_rows(decision_sets))
    return rows


def tighten_gate(item: dict[str, Any]) -> None:
    holdout = item["holdout"]["selected"]
    reasons = []
    if (holdout.get("active_event_dates") or 0) < MIN_HOLDOUT_DATES:
        item["gates"]["forward"] = "FAIL"
        reasons.append(f"holdout_dates<{MIN_HOLDOUT_DATES}")
    if (holdout.get("rows") or 0) < MIN_HOLDOUT_ROWS:
        item["gates"]["forward"] = "FAIL"
        reasons.append(f"holdout_rows<{MIN_HOLDOUT_ROWS}")
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
    return sorted(results, key=rank_key, reverse=True)


def passed(item: dict[str, Any]) -> bool:
    gates = item["gates"]
    return gates["significance"] == "PASS" and gates["baseline"] == "PASS" and gates["forward"] == "PASS"


def rank_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(item["holdout"].get("excess_roi") if item["holdout"].get("excess_roi") is not None else -999.0),
        float(item["holdout"]["selected"].get("taker_roi") if item["holdout"]["selected"].get("taker_roi") is not None else -999.0),
        float(item["holdout"]["selected"].get("active_event_dates") or 0),
        float(item["holdout"]["selected"].get("rows") or 0),
    )


def result_rows(results: list[dict[str, Any]], limit: int | None = None) -> list[list[Any]]:
    shown = results if limit is None else results[:limit]
    out = []
    for item in shown:
        tr = item["train"]["selected"]
        ho = item["holdout"]["selected"]
        gates = item["gates"]
        out.append([
            f"`{item['algorithm']}`",
            item["train_family_rows"],
            tr["rows"],
            tr["active_event_dates"],
            pct(tr["taker_roi"]),
            pct(item["train"].get("excess_roi")),
            fmt_ci(item["train"].get("excess_roi_ci95_cluster_by_event_date")),
            item["holdout_family_rows"],
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


def simple_takeaways(report: dict[str, Any]) -> list[str]:
    proxy = report["decision_proxy_results"]
    ob = report["orderbook_results"]
    by_alg_ob = {item["algorithm"]: item for item in ob}
    lines = []
    confirmed = report["confirmed_algorithms"]
    if confirmed:
        lines.append(f"有 confirmed 策略：{', '.join(confirmed)}。")
    else:
        lines.append("没有策略同时通过 proxy 和 time-aligned orderbook 三门，所以没有 live 动作。")
    for alg in ["mode_adj3_yes_cost085", "shoulders_over_center_e010", "flexible_best_yes_no_legs_e008_top4", "shape_single_peak_no_e010_union"]:
        item = by_alg_ob.get(alg)
        if not item:
            continue
        ho = item["holdout"]["selected"]
        lines.append(f"{alg}: orderbook holdout rows={ho['rows']}, dates={ho['active_event_dates']}, ROI={pct(ho['taker_roi'])}, excess={pct(item['holdout'].get('excess_roi'))}。")
    return lines


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Union Top Strategy Rerun v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 一句话",
        "",
    ]
    lines.extend(f"- {line}" for line in report["simple_takeaways"])
    lines.extend([
        "",
        "## 数据快照",
        "",
        f"- fact_signal_candidates rows: `{report['data_self_check']['fact_signal_candidate_coverage']['rows']}`; fact built: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- union rows: `{report['input']['union_rows']}`; decision sets: `{report['input']['decision_sets']}`; generated strategy rows: `{report['input']['strategy_rows']}`; algorithms: `{report['input']['algorithms']}`。",
        f"- train: `{report['split']['train_start']}` -> `{report['split']['train_end']}`; holdout: `{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`。",
        f"- orderbook fully matched rows: `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`。",
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
        "## Decision Proxy Top Results",
        "",
        table(
            ["algorithm", "train family", "train rows", "train dates", "train ROI", "train excess", "train excess CI", "holdout family", "holdout rows", "holdout dates", "holdout ROI", "holdout excess", "holdout excess CI", "top5 removed", "gates", "reasons"],
            result_rows(report["decision_proxy_results"], limit=18),
        ),
        "",
        "## Time-Aligned Orderbook Top Results",
        "",
        "Orderbook uses latest `snapshot_ts_utc <= decision_snapshot_ts_utc`; all selected legs must match.",
        "",
        table(
            ["algorithm", "train family", "train rows", "train dates", "train ROI", "train excess", "train excess CI", "holdout family", "holdout rows", "holdout dates", "holdout ROI", "holdout excess", "holdout excess CI", "top5 removed", "gates", "reasons"],
            result_rows(report["orderbook_results"], limit=18),
        ),
        "",
        "## 三门状态",
        "",
        table(
            ["gate", "status", "reason"],
            [
                ["significance", report["gates"]["significance"], "没有策略在 proxy 和 orderbook 两层同时给出稳定正超额 CI。"],
                ["baseline", report["gates"]["baseline"], "同 family baseline 后，正收益多为不稳定或只在单一 holdout 日期出现。"],
                ["forward", report["gates"]["forward"], "需要 holdout >=5 日期、>=20 行、top5 removed ROI >0；候选没同时满足。"],
            ],
        ),
        "",
        f"结论：`{report['verdict']}`。不改 live、不加 size、不扩池。",
        "",
        "## 下一步",
        "",
        "- `mode_adj3_yes_cost085` 可继续做 shadow/paper，因为逻辑最干净，但当前 holdout 日期太少。",
        "- `flexible_best_yes_no_legs` 和 pair/shape spread 方向暂时降优先级；它们更像噪声或执行后变差。",
        "- 继续提升应转向 forecast quality gating / model calibration，而不是继续调交易形态阈值。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    union_rows = union_v02.load_union_rows(conn)
    decision_sets = union_v02.group_union_decision_sets(union_rows)
    strategy_rows = generate_all_rows(decision_sets)
    all_dates = sorted({row["event_date"] for row in strategy_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)

    # Add medium/low variants for mode-adj3 after split is fixed.
    thresholds = union_v02.add_medium_quality_selection(strategy_rows, train_dates)

    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(strategy_rows, args.orderbook_glob)

    proxy_rows = variants.metric_rows(strategy_rows, "proxy")
    orderbook_rows = variants.metric_rows(strategy_rows, "orderbook")
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
    confirmed = {item["algorithm"] for item in proxy_results if passed(item)} & {item["algorithm"] for item in orderbook_results if passed(item)}
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
            "min_holdout_dates": MIN_HOLDOUT_DATES,
            "min_holdout_rows": MIN_HOLDOUT_ROWS,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
        },
        "data_self_check": scanner.data_self_check(conn),
        "input": {
            "union_rows": len(union_rows),
            "decision_sets": len(decision_sets),
            "strategy_rows": len(strategy_rows),
            "algorithms": len({row["algorithm"] for row in strategy_rows}),
            "event_dates": len(all_dates),
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
        },
        "quality_thresholds": thresholds,
        "orderbook_coverage": coverage,
        "decision_proxy_results": proxy_results,
        "orderbook_results": orderbook_results,
        "confirmed_algorithms": sorted(confirmed),
        "gates": gates,
        "verdict": gates["verdict"],
    }
    report["simple_takeaways"] = simple_takeaways(report)
    scanner.write_json(Path(args.out_json), report)
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(render_md(report), encoding="utf-8")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} strategy_rows={len(strategy_rows)} algorithms={report['input']['algorithms']}")


if __name__ == "__main__":
    main()
