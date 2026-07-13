#!/usr/bin/env python3
"""Unified replay for tmax_distribution_v3 (offline research; never touches live).

One run produces every baseline/ablation/execution/stability table, the
frozen shadow artifact, the shadow-event source for the existing
``tmax_distribution_edge_shadow_v1`` runtime, the Lucknow 2026-07-05 fixed
case, and the comprehensive report (md + json).

Usage:
  .venv/bin/python scripts/analysis/reheat_risk/research_tmax_distribution_v3_model_v1.py
  ... --smoke-lines 20000   (debug subset; artifacts not persisted)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.tmax_distribution_v3 import (  # noqa: E402
    BUCKETS,
    ROUTE_SPECS,
)
from scripts.analysis.reheat_risk.tmax_distribution_v3 import common, enrich, execution, scoring, shadow, states, walkforward  # noqa: E402
from scripts.analysis.reheat_risk.tmax_distribution_v3 import report as report_mod  # noqa: E402

KEY = scoring.KEY
STRICT_ROUTES = (
    "market_recal_global", "market_recal_path",
    "weather_only", "market_path", "market_path_source", "strict_pit_full",
)
ABLATION_PAIRS = [
    ("market_recal_global", "market"),
    ("market_recal_path", "market_recal_global"),
    ("market_recal_path", "market"),
    ("weather_only", "market"),
    ("market_path", "market"),
    ("market_path_source", "market_path"),
    ("strict_pit_full", "market_path_source"),
    ("strict_pit_full", "market"),
    ("archive_upper_bound", "strict_pit_full"),
    ("archive_upper_bound", "market"),
]


def freeze_date_from_settlement() -> str:
    winners = common.load_settlement_winners()
    return max(date for _, date in winners)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-lines", type=int, default=None)
    args = parser.parse_args()
    common.OUT_DIR.mkdir(parents=True, exist_ok=True)

    freeze_date = freeze_date_from_settlement()
    frame = states.materialize_states(freeze_date, max_lines=args.smoke_lines)
    frame, remeasured = enrich.apply_enrichment(frame)
    labeled = frame[frame["labeled"] & (frame["target_date"] <= freeze_date)].copy()
    unlabeled = frame[~frame["labeled"] | (frame["target_date"] > freeze_date)].copy()
    hourly = states.hourly_last(labeled)
    counts = {
        "freeze_date": freeze_date,
        "state_min_date": str(labeled["target_date"].min()),
        "state_max_date": str(labeled["target_date"].max()),
        "labeled_exec_states": int(len(labeled)),
        "unlabeled_forward_states": int(len(unlabeled)),
        "hourly_states": int(len(hourly)),
        "hourly_market_ready": int(hourly["market_score_ready"].sum()),
        "below_label_rows": int(labeled["actual_bucket"].eq("below").sum()),
        "v2_parity_expected_exec_states": 8094,
    }

    route_probs: dict[str, pd.DataFrame] = {}
    for route in ROUTE_SPECS:
        route_probs[route] = walkforward.run_route(route, hourly, labeled)
        route_probs[route].to_csv(common.OUT_DIR / f"route_probs_{route}.csv", index=False)

    paired = scoring.paired_denominator(hourly, route_probs)
    counts["paired_rows"] = int(len(paired))
    counts["paired_dates"] = int(paired["target_date"].nunique()) if not paired.empty else 0
    rows = scoring.score_rows(paired, route_probs)
    summary = scoring.score_summary(rows)
    deltas = scoring.ablation_deltas(rows, ABLATION_PAIRS)
    calibration = scoring.calibration_bins(paired, route_probs)
    rows.to_csv(common.OUT_DIR / "paired_score_rows.csv", index=False)
    summary.to_csv(common.OUT_DIR / "paired_score_summary.csv", index=False)
    deltas.to_csv(common.OUT_DIR / "ablation_deltas.csv", index=False)
    calibration.to_csv(common.OUT_DIR / "calibration_bins.csv", index=False)

    # Primary selection: best strict-PIT route by paired date-equal logloss.
    strict = summary[summary["route"].isin(STRICT_ROUTES)]
    primary_route = (
        strict.sort_values("logloss_date_equal").iloc[0]["route"] if not strict.empty else "strict_pit_full"
    )

    # Trailing-window robustness for the primary route.
    trailing = walkforward.run_route(primary_route, hourly, labeled, train_window_dates=common.TRAILING_WINDOW_DATES)
    trailing_rows = scoring.score_rows(
        scoring.paired_denominator(hourly, {primary_route: trailing}), {primary_route: trailing}
    )
    trailing_summary = scoring.score_summary(trailing_rows)
    trailing_summary.to_csv(common.OUT_DIR / "trailing_window_summary.csv", index=False)

    # Execution replays on the frozen policy.
    exec_results: dict[str, pd.DataFrame] = {}
    market_exec = labeled.copy()
    for bucket in BUCKETS:
        market_exec[f"route_p_{bucket}"] = market_exec[f"market_p_{bucket}"]
    market_exec = market_exec[market_exec["market_score_ready"]]
    exec_results["market"] = execution.first_lock_replay(market_exec, "market")
    exec_with_probs: dict[str, pd.DataFrame] = {}
    for route in ROUTE_SPECS:
        probs = route_probs[route]
        if probs.empty:
            exec_with_probs[route] = pd.DataFrame(columns=labeled.columns)
            exec_results[route] = pd.DataFrame()
            continue
        merged = labeled.merge(
            probs.drop(columns=["actual_bucket", "labeled", "market_score_ready", "unit"], errors="ignore"),
            on=KEY,
            how="inner",
        )
        exec_with_probs[route] = merged
        exec_results[route] = execution.first_lock_replay(merged, route)
    first_lock_all = pd.concat([f.assign(route=r) for r, f in exec_results.items() if not f.empty], ignore_index=True)
    first_lock_all.to_csv(common.OUT_DIR / "execution_first_lock_rows.csv", index=False)
    exec_summary = execution.execution_summary(first_lock_all, ["route"])
    exec_side = execution.execution_summary(first_lock_all, ["route", "side"])
    exec_expr = execution.execution_summary(first_lock_all, ["route", "expression"])
    exec_unit = execution.execution_summary(first_lock_all, ["route", "unit"])
    exec_source = execution.execution_summary(first_lock_all[first_lock_all["route"] == primary_route], ["forecast_source"])
    exec_family = execution.execution_summary(first_lock_all[first_lock_all["route"] == primary_route], ["family"])
    exec_daily = (
        first_lock_all.dropna(subset=["pnl"]).groupby(["route", "target_date"], as_index=False)
        .agg(rows=("city", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    )
    for name, table in [
        ("execution_summary", exec_summary), ("execution_side_summary", exec_side),
        ("execution_expression_summary", exec_expr), ("execution_unit_summary", exec_unit),
        ("execution_source_summary", exec_source), ("execution_family_summary", exec_family),
        ("execution_daily", exec_daily),
    ]:
        table.to_csv(common.OUT_DIR / f"{name}.csv", index=False)
    stability = execution.stability_slices(first_lock_all[first_lock_all["route"] == primary_route], primary_route)

    ledger, positions = execution.target_book_replay(exec_with_probs[primary_route], primary_route)
    ledger.to_csv(common.OUT_DIR / "target_book_ledger.csv", index=False)
    positions.to_csv(common.OUT_DIR / "target_book_positions.csv", index=False)
    target_book_stats = report_mod.target_book_stats(ledger, positions)

    lucknow = report_mod.lucknow_case(exec_with_probs[primary_route], ledger, first_lock_all, primary_route)

    audit = scoring.full_ladder_secondary_audit(paired, route_probs, primary_route)

    final_alpha, final_coherence = report_mod.final_selection(route_probs[primary_route])
    final_c = report_mod.final_calibrator_c(route_probs[primary_route])
    artifact = shadow.freeze_primary_artifact(hourly, primary_route, freeze_date, final_alpha, final_coherence, final_c)
    shadow_events = shadow.build_shadow_events(
        exec_with_probs[primary_route], unlabeled, __import__("joblib").load(shadow.ARTIFACT_JOBLIB),
        primary_route, final_alpha, freeze_date,
    )
    shadow_events.to_csv(common.OUT_DIR / "shadow_events.csv", index=False)

    payload = report_mod.write_report(
        counts=counts, remeasured=remeasured, summary=summary, deltas=deltas,
        trailing_summary=trailing_summary, exec_summary=exec_summary, exec_side=exec_side,
        exec_expr=exec_expr, exec_unit=exec_unit, exec_source=exec_source, exec_family=exec_family,
        stability=stability, target_book=target_book_stats, audit=audit, lucknow=lucknow,
        primary_route=primary_route, artifact=artifact, shadow_rows=len(shadow_events),
        route_probs=route_probs, paired=paired,
    )
    print(json.dumps(common.json_ready({"verdict": payload["three_gates"], "primary_route": primary_route, "counts": counts}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
