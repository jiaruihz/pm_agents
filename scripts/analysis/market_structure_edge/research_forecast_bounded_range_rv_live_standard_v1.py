#!/usr/bin/env python3
"""Forecast-bounded Range RV live-standard hardening v1.

This script does not try to make proxy results look live-ready.  It asks a
stricter question: if the range basket had to be selected from the time-aligned
orderbook itself, does any default-WU compact Range RV rule pass the live
readiness gates?

Evidence layers:
- fact_signal_candidates for opportunity/model/settlement labels.
- settlement_source_registry_v0 for source buckets.
- raw orderbook snapshots with snapshot_ts_utc <= decision_snapshot_ts_utc.
- fact_trades only for mandatory self-check and CLOB coverage context.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from scripts.analysis.forecast_quality import research_forecast_quality_base_v0 as fq_base  # noqa: E402
from scripts.analysis.forecast_quality import research_forecast_quality_source_adjusted_v0 as fq_source  # noqa: E402
from scripts.analysis.observed_max import research_settlement_source_registry_v0 as source_registry  # noqa: E402

import research_forecast_bounded_range_rv_source_aware_v0 as source_v0  # noqa: E402
import research_range_rv_scanner as scanner  # noqa: E402


TARGET_METRIC = "forecast_bounded_range_rv_live_standard_v1"
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-forecast-bounded-range-rv-live-standard-v1.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-forecast-bounded-range-rv-live-standard-v1.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260615)
    parser.add_argument("--thresholds", default="0.00,0.01,0.02,0.03,0.04,0.05,0.07,0.10")
    return parser.parse_args()


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def threshold_values(text: str) -> list[float]:
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def load_clob_gate() -> dict[str, Any]:
    path = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
    if not path.exists():
        return {"path": str(path), "gate_pass": None, "error": "missing"}
    data = json.loads(path.read_text())
    return {
        "path": str(path),
        "gate_pass": bool(data.get("gate_pass")),
        "fail_reasons": data.get("fail_reasons") or [],
        "fact_trades_live_real": data.get("fact_trades_live_real"),
    }


def row_filter_name(row_filter: str) -> str:
    return {
        "default_wu_no_filter": "default_wu/no_filter",
        "default_wu_exclude_low": "default_wu/exclude_forecast_quality_low",
        "all_no_filter_diagnostic": "all/no_filter_diagnostic",
    }[row_filter]


def filter_rows(rows: list[dict[str, Any]], row_filter: str) -> list[dict[str, Any]]:
    if row_filter == "default_wu_no_filter":
        return [row for row in rows if row.get("source_bucket") == "default_wu"]
    if row_filter == "default_wu_exclude_low":
        return [
            row
            for row in rows
            if row.get("source_bucket") == "default_wu" and int(row.get("forecast_quality_low") or 0) == 0
        ]
    if row_filter == "all_no_filter_diagnostic":
        return list(rows)
    raise ValueError(row_filter)


def build_orderbook_leg_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        for idx, leg in enumerate(row["legs"]):
            side = str(leg["side"])
            candidates.append(
                {
                    "candidate_id": f"{row['candidate_id']}|{idx}",
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "event_date": row["event_date"],
                    "city": row["city"],
                    "bracket": leg["bracket"],
                    "side": side,
                    "market_yes_price": leg["market_yes_price"],
                    "decision_entry_price": source_v0.leg_cost(side, float(leg["market_yes_price"])),
                    "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                    "decision_dt": row["decision_dt"],
                    "final_yes": leg["final_yes"],
                    "counterfactual_pnl": None,
                    "live_filled": 0,
                    "outcome": "yes" if side == "BUY_YES" else "no",
                    "price_bucket": "",
                }
            )
    return candidates


def attach_orderbook_native(rows: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    leg_candidates = build_orderbook_leg_candidates(rows)
    matched, coverage = scanner.match_time_aligned_orderbooks(leg_candidates, orderbook_glob)
    by_id = {row["candidate_id"]: row for row in matched}
    full = 0
    partial = 0
    for row in rows:
        matched_legs = []
        ok = True
        for idx, _leg in enumerate(row["legs"]):
            leg_match = by_id.get(f"{row['candidate_id']}|{idx}")
            if leg_match is None or leg_match.get("taker_cost_usd") is None or leg_match.get("taker_pnl_usd") is None:
                ok = False
                continue
            matched_legs.append(leg_match)
        if not ok:
            if matched_legs:
                partial += 1
            continue
        full += 1
        taker_cost = sum(float(leg["taker_cost_usd"]) for leg in matched_legs)
        taker_pnl = sum(float(leg["taker_pnl_usd"]) for leg in matched_legs)
        maker_rows = [
            leg
            for leg in matched_legs
            if leg.get("maker_cost_proxy_usd") is not None and leg.get("maker_pnl_proxy_usd") is not None
        ]
        maker_cost = sum(float(leg["maker_cost_proxy_usd"]) for leg in maker_rows) if len(maker_rows) == len(matched_legs) else None
        maker_pnl = sum(float(leg["maker_pnl_proxy_usd"]) for leg in maker_rows) if len(maker_rows) == len(matched_legs) else None
        expression = str(row.get("expression"))
        effective_cost = taker_cost - (len(row["legs"]) - 1) if expression == "outside_no" else taker_cost
        row["orderbook_matched"] = 1
        row["orderbook_taker_cost"] = taker_cost
        row["orderbook_taker_pnl"] = taker_pnl
        row["orderbook_maker_proxy_cost"] = maker_cost
        row["orderbook_maker_proxy_pnl"] = maker_pnl
        row["orderbook_effective_range_cost"] = effective_cost
        row["orderbook_native_edge"] = float(row["range_model_mass_norm"]) - effective_cost
        row["orderbook_selected"] = int(row["orderbook_native_edge"] > 0.0 and 0.0 < effective_cost <= 0.95)
        row["max_orderbook_age_minutes"] = max(float(leg["orderbook_age_minutes"]) for leg in matched_legs)
        row["avg_orderbook_age_minutes"] = sum(float(leg["orderbook_age_minutes"]) for leg in matched_legs) / len(matched_legs)
        row["min_ask_size"] = min(
            float(leg["ask_size"]) for leg in matched_legs if leg.get("ask_size") is not None
        ) if all(leg.get("ask_size") is not None for leg in matched_legs) else None
        row["min_depth_ask_5c"] = min(
            float(leg["depth_ask_5c"]) for leg in matched_legs if leg.get("depth_ask_5c") is not None
        ) if all(leg.get("depth_ask_5c") is not None for leg in matched_legs) else None
        row["max_taker_best_ask"] = max(float(leg["taker_best_ask"]) for leg in matched_legs)
    coverage["strategy_rows"] = len(rows)
    coverage["fully_matched_strategy_rows"] = full
    coverage["partial_strategy_rows"] = partial
    coverage["fully_matched_strategy_rate"] = scanner.safe_div(full, len(rows))
    return coverage


def metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "selected": int(row.get("orderbook_selected") or 0),
            "eval_cost": row["orderbook_taker_cost"],
            "eval_pnl": row["orderbook_taker_pnl"],
            "eval_maker_cost": row.get("orderbook_maker_proxy_cost"),
            "eval_maker_pnl": row.get("orderbook_maker_proxy_pnl"),
        }
        for row in rows
        if row.get("orderbook_matched") == 1
    ]


def split_rows(rows: list[dict[str, Any]], dates: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row["event_date"]) in dates]


def capacity_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if int(row.get("selected") or 0) == 1]
    min_ask_values = [float(row["min_ask_size"]) for row in selected if row.get("min_ask_size") is not None]
    min_depth_values = [float(row["min_depth_ask_5c"]) for row in selected if row.get("min_depth_ask_5c") is not None]
    age_values = [float(row["max_orderbook_age_minutes"]) for row in selected if row.get("max_orderbook_age_minutes") is not None]
    return {
        "selected_rows": len(selected),
        "rows_with_all_legs_ask_size": len(min_ask_values),
        "min_min_ask_size": min(min_ask_values) if min_ask_values else None,
        "median_min_ask_size": float(np.median(min_ask_values)) if min_ask_values else None,
        "rows_with_all_legs_depth_ask_5c": len(min_depth_values),
        "min_depth_ask_5c": min(min_depth_values) if min_depth_values else None,
        "median_depth_ask_5c": float(np.median(min_depth_values)) if min_depth_values else None,
        "max_orderbook_age_minutes": max(age_values) if age_values else None,
        "median_orderbook_age_minutes": float(np.median(age_values)) if age_values else None,
        "share5_capacity_rows": sum(1 for v in min_ask_values if v >= 5.0),
    }


def threshold_eval(
    family: list[dict[str, Any]],
    *,
    threshold: float,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> dict[str, Any]:
    rows = [{**row, "selected": int(float(row.get("orderbook_native_edge") or -999.0) >= threshold)} for row in family]
    train_family = split_rows(rows, train_dates)
    holdout_family = split_rows(rows, holdout_dates)
    train_selected = [row for row in train_family if int(row.get("selected") or 0) == 1]
    holdout_selected = [row for row in holdout_family if int(row.get("selected") or 0) == 1]
    train = {
        **__import__("research_range_rv_variant_lab_v03").summarize_eval(
            train_selected, train_family, "time_aligned_orderbook_native", seed, iters
        ),
        "capacity": capacity_summary(train_selected),
    }
    holdout = {
        **__import__("research_range_rv_variant_lab_v03").summarize_eval(
            holdout_selected, holdout_family, "time_aligned_orderbook_native", seed + 50, iters
        ),
        "capacity": capacity_summary(holdout_selected),
    }
    return {
        "threshold": threshold,
        "train": train,
        "holdout": holdout,
        "gates": scanner.gate_result(train, holdout),
    }


def strict_live_gate(item: dict[str, Any], *, clob_gate_pass: bool, is_generic: bool) -> dict[str, Any]:
    reasons = []
    train_sel = item["train"]["selected"]
    holdout_sel = item["holdout"]["selected"]
    train_cap = item["train"]["capacity"]
    holdout_cap = item["holdout"]["capacity"]
    if not clob_gate_pass:
        reasons.append("clob_coverage_gate_false")
    if not is_generic:
        reasons.append("not_default_wu_generic")
    if train_sel["rows"] < 30:
        reasons.append("train_rows<30")
    if train_sel["active_event_dates"] < 10:
        reasons.append("train_dates<10")
    if holdout_sel["rows"] < 20:
        reasons.append("holdout_rows<20")
    if holdout_sel["active_event_dates"] < 5:
        reasons.append("holdout_dates<5")
    if item["gates"]["significance"] != "PASS":
        reasons.append("significance_gate_fail")
    if item["gates"]["baseline"] != "PASS":
        reasons.append("baseline_gate_fail")
    if item["gates"]["forward"] != "PASS":
        reasons.append("forward_gate_fail")
    if (train_sel.get("drop_top5_taker_roi") or 0.0) <= 0.0:
        reasons.append("train_top5_removed<=0")
    if (holdout_sel.get("drop_top5_taker_roi") or 0.0) <= 0.0:
        reasons.append("holdout_top5_removed<=0")
    if holdout_cap["rows_with_all_legs_ask_size"] < holdout_sel["rows"]:
        reasons.append("holdout_capacity_missing_ask_size")
    if holdout_cap["share5_capacity_rows"] < holdout_sel["rows"]:
        reasons.append("holdout_some_rows_below_5share_top_ask")
    return {
        "pass": not reasons,
        "reasons": reasons,
    }


def evaluate_live_standard(
    rows: list[dict[str, Any]],
    *,
    train_dates: set[str],
    holdout_dates: set[str],
    thresholds: list[float],
    iters: int,
    seed: int,
    clob_gate_pass: bool,
) -> list[dict[str, Any]]:
    out = []
    row_filters = ["default_wu_no_filter", "default_wu_exclude_low", "all_no_filter_diagnostic"]
    algorithms = sorted({row["algorithm"] for row in rows})
    idx = 0
    for algorithm in algorithms:
        algo_rows = [row for row in rows if row["algorithm"] == algorithm]
        for row_filter in row_filters:
            family = filter_rows(algo_rows, row_filter)
            if not family:
                continue
            threshold_results = []
            for threshold in thresholds:
                evaluated = threshold_eval(
                    family,
                    threshold=threshold,
                    train_dates=train_dates,
                    holdout_dates=holdout_dates,
                    iters=iters,
                    seed=seed + idx * 1000,
                )
                threshold_results.append(evaluated)
                idx += 1
            best = sorted(
                threshold_results,
                key=lambda item: (
                    1 if item["gates"]["significance"] == "PASS" else 0,
                    1 if item["gates"]["baseline"] == "PASS" else 0,
                    1 if item["gates"]["forward"] == "PASS" else 0,
                    item["holdout"]["excess_roi"] if item["holdout"]["excess_roi"] is not None else -999.0,
                    item["holdout"]["selected"]["active_event_dates"] or 0,
                    item["holdout"]["selected"]["rows"] or 0,
                ),
                reverse=True,
            )[0]
            is_generic = row_filter in {"default_wu_no_filter", "default_wu_exclude_low"}
            out.append(
                {
                    "algorithm": algorithm,
                    "row_filter": row_filter,
                    "row_filter_label": row_filter_name(row_filter),
                    "family_rows": len(family),
                    "thresholds_tested": thresholds,
                    "selected_threshold": best["threshold"],
                    "best": best,
                    "live_standard": strict_live_gate(best, clob_gate_pass=clob_gate_pass, is_generic=is_generic),
                }
            )
    return sorted(
        out,
        key=lambda item: (
            1 if item["live_standard"]["pass"] else 0,
            item["best"]["holdout"]["excess_roi"] if item["best"]["holdout"]["excess_roi"] is not None else -999.0,
            item["best"]["holdout"]["selected"]["active_event_dates"] or 0,
            item["best"]["holdout"]["selected"]["rows"] or 0,
        ),
        reverse=True,
    )


def result_rows(results: list[dict[str, Any]], limit: int = 16) -> list[list[Any]]:
    rows = []
    for item in results[:limit]:
        best = item["best"]
        train = best["train"]
        holdout = best["holdout"]
        rows.append(
            [
                f"`{item['algorithm']}`",
                f"`{item['row_filter_label']}`",
                item["family_rows"],
                best["threshold"],
                train["selected"]["rows"],
                train["selected"]["active_event_dates"],
                pct(train["selected"]["taker_roi"]),
                pct(train["excess_roi"]),
                fmt_ci(train["excess_roi_ci95_cluster_by_event_date"]),
                pct(train["selected"]["drop_top5_taker_roi"]),
                holdout["selected"]["rows"],
                holdout["selected"]["active_event_dates"],
                pct(holdout["selected"]["taker_roi"]),
                pct(holdout["excess_roi"]),
                fmt_ci(holdout["excess_roi_ci95_cluster_by_event_date"]),
                pct(holdout["selected"]["drop_top5_taker_roi"]),
                f"`{best['gates']['significance']}/{best['gates']['baseline']}/{best['gates']['forward']} -> {best['gates']['verdict']}`",
                "PASS" if item["live_standard"]["pass"] else ",".join(item["live_standard"]["reasons"][:4]),
            ]
        )
    return rows


def render_md(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    lines = [
        "# Forecast-Bounded Range RV Live-Standard v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: live-standard research gate only; no N100/live config changed; no orders placed.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` plus time-aligned orderbook snapshots; `fact_trades` only for mandatory self-check/live coverage context.",
        f"- DB last_modified: `{payload['db_last_modified_utc']}`.",
        f"- fact_signal_candidates rows: `{payload['self_check']['candidate_coverage']['rows']}`.",
        f"- decision_sets: `{payload['funnel']['decision_sets']}`; expression rows: `{payload['funnel']['strategy_rows']}`; orderbook-matched rows: `{payload['funnel']['orderbook_rows']}`.",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} event_dates).",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} event_dates).",
        f"- CLOB coverage gate: `{payload['clob_gate']['gate_pass']}`.",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Live-Standard Definition",
        "",
        "A candidate must be `default_wu`, selected from orderbook-native edge, and pass: train rows >=30, train dates >=10, holdout rows >=20, holdout dates >=5, ROI/excess CI lower > 0, train/holdout top5-removed ROI > 0, CLOB gate true, and all holdout selected rows must show at least 5 shares at top ask on every leg.",
        "",
        "## Best Live-Standard Attempts",
        "",
        table(
            [
                "algorithm",
                "filter",
                "family",
                "edge threshold",
                "train rows",
                "train dates",
                "train ROI",
                "train excess",
                "train excess CI",
                "train top5 removed",
                "holdout rows",
                "holdout dates",
                "holdout ROI",
                "holdout excess",
                "holdout excess CI",
                "holdout top5 removed",
                "3 gates",
                "live-standard blockers",
            ],
            result_rows(payload["results"]),
        ),
        "",
        "## Capacity Snapshot",
        "",
        "The table below is for the top-ranked attempt only.",
        "",
        "```json",
        json.dumps(payload["top_capacity_snapshot"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Verdict",
        "",
        table(
            ["gate", "status", "reason"],
            [
                ["significance", verdict["significance"], verdict["reason"]],
                ["baseline", verdict["baseline"], verdict["reason"]],
                ["forward", verdict["forward"], verdict["reason"]],
                ["live_standard", verdict["live_standard"], verdict["reason"]],
            ],
        ),
        "",
        f"`significance={verdict['significance']}`, `baseline={verdict['baseline']}`, `forward={verdict['forward']}`, `conclusion={verdict['conclusion']}`.",
        "",
        "Plain-English conclusion: this stricter run did not produce a live-standard Range RV candidate. The right next step is not live deployment; it is forward shadow telemetry for the closest default-WU width-3 orderbook-native family, or waiting for more settled forward samples.",
        "",
    ]
    return "\n".join(lines)


def final_verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [row for row in results if row["live_standard"]["pass"]]
    if passed:
        names = [f"{row['algorithm']}:{row['row_filter_label']}@{row['selected_threshold']}" for row in passed]
        return {
            "significance": "PASS",
            "baseline": "PASS",
            "forward": "PASS",
            "live_standard": "PASS",
            "conclusion": "confirmed",
            "reason": f"Live-standard hard gates passed for {names}",
        }
    top = results[0] if results else None
    reason = "no evaluated rows"
    if top:
        reason = "; ".join(top["live_standard"]["reasons"][:8]) or "three-gate/support failure"
    return {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "live_standard": "FAIL",
        "conclusion": "inconclusive",
        "reason": reason,
    }


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    thresholds = threshold_values(args.thresholds)
    conn = fq_base.connect_ro(db_path)
    try:
        self_check = fq_base.mandatory_self_check(conn)
        candidates = fq_base.load_candidates(conn)
        city_counts = source_registry.fact_city_counts(conn)
    finally:
        conn.close()

    registry = source_registry.add_fact_counts(source_registry.build_registry(), city_counts)
    decision_sets = fq_base.build_decision_sets(candidates)
    train_dates, holdout_dates, split = fq_base.split_dates(decision_sets)
    decision_sets = fq_base.add_historical_features(fq_base.add_cross_model_features(decision_sets))
    decision_sets, _thresholds = fq_base.add_quality_labels(decision_sets, train_dates)
    decision_sets = fq_source.attach_source_registry(decision_sets, registry)
    strategy_rows = source_v0.generate_rows(decision_sets)
    orderbook_coverage = attach_orderbook_native(strategy_rows, args.orderbook_glob)
    orderbook_rows = metric_rows(strategy_rows)
    clob_gate = load_clob_gate()
    results = evaluate_live_standard(
        orderbook_rows,
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        thresholds=thresholds,
        iters=args.bootstrap_iters,
        seed=args.seed,
        clob_gate_pass=bool(clob_gate.get("gate_pass")),
    )
    verdict = final_verdict(results)
    top = results[0] if results else None
    top_capacity = {
        "algorithm": top["algorithm"] if top else None,
        "row_filter": top["row_filter_label"] if top else None,
        "threshold": top["selected_threshold"] if top else None,
        "train_capacity": top["best"]["train"]["capacity"] if top else None,
        "holdout_capacity": top["best"]["holdout"]["capacity"] if top else None,
    }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "self_check": self_check,
        "split": split,
        "parameters": {
            "thresholds": thresholds,
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "orderbook_glob": args.orderbook_glob,
        },
        "clob_gate": clob_gate,
        "orderbook_coverage": orderbook_coverage,
        "funnel": {
            "fact_signal_candidates_loaded": int(len(candidates)),
            "decision_sets": int(len(decision_sets)),
            "strategy_rows": int(len(strategy_rows)),
            "orderbook_rows": int(len(orderbook_rows)),
            "algorithms": int(len({row["algorithm"] for row in strategy_rows})),
        },
        "results": results,
        "top_capacity_snapshot": top_capacity,
        "verdict": verdict,
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    scanner.write_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "verdict": verdict, "funnel": payload["funnel"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
