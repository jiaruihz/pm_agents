#!/usr/bin/env python3
"""Evaluate first-seen event candidates against the same-row market baseline."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd


FEE_RATE = 0.05


def _clip(series: pd.Series) -> pd.Series:
    return series.astype(float).clip(1e-6, 1.0 - 1e-6)


def _brier(probability: pd.Series, label: pd.Series) -> float:
    return float(((_clip(probability) - label.astype(float)) ** 2).mean())


def _logloss(probability: pd.Series, label: pd.Series) -> float:
    probability = _clip(probability)
    label = label.astype(float)
    return float((-(label * probability.map(math.log) + (1.0 - label) * (1.0 - probability).map(math.log))).mean())


def _policy(rows: pd.DataFrame, threshold: float) -> dict[str, Any]:
    eligible = rows[
        (rows["candidate_status"] == "scored")
        & rows["model_probability_after"].notna()
        & rows["decision_entry_price"].notna()
        & rows["side_win"].notna()
    ].copy()
    if eligible.empty:
        return {"threshold": threshold, "selected_expressions": 0}
    eligible["fee_per_share"] = (
        FEE_RATE
        * eligible["decision_entry_price"]
        * (1.0 - eligible["decision_entry_price"])
    )
    eligible["fee_adjusted_edge"] = (
        eligible["model_probability_after"]
        - eligible["decision_entry_price"]
        - eligible["fee_per_share"]
    )
    eligible = eligible.sort_values(
        ["trigger_event_id", "condition_id", "fee_adjusted_edge"],
        ascending=[True, True, False],
    ).drop_duplicates(["trigger_event_id", "condition_id"])
    selected = eligible[eligible["fee_adjusted_edge"] >= threshold].copy()
    if selected.empty:
        return {"threshold": threshold, "selected_expressions": 0}
    selected["cost"] = selected["decision_entry_price"] + selected["fee_per_share"]
    selected["pnl"] = selected["side_win"] - selected["cost"]
    return {
        "threshold": threshold,
        "selected_expressions": int(len(selected)),
        "target_dates": int(selected["event_date"].nunique()),
        "events": int(selected["trigger_event_id"].nunique()),
        "win_rate": float(selected["side_win"].mean()),
        "average_entry": float(selected["decision_entry_price"].mean()),
        "fees": float(selected["fee_per_share"].sum()),
        "pnl_per_share": float(selected["pnl"].sum()),
        "cost_per_share": float(selected["cost"].sum()),
        "fee_adjusted_roi": float(selected["pnl"].sum() / selected["cost"].sum()),
        "expressions_per_day": float(len(selected) / selected["event_date"].nunique()),
    }


def analyze(conn: sqlite3.Connection) -> dict[str, Any]:
    candidates = pd.read_sql_query(
        """
        SELECT
          candidate.*,
          event.event_kind,
          event.source AS event_source,
          event.pit_lineage_class,
          event.material_state_change,
          checkpoint.checkpoint_status
        FROM fact_signal_candidates AS candidate
        JOIN weather_information_events AS event
          ON event.information_event_id = candidate.trigger_event_id
        JOIN weather_state_checkpoints AS checkpoint
          ON checkpoint.state_checkpoint_id = candidate.state_checkpoint_id
        WHERE candidate.candidate_grain_version = 'v2_event_checkpoint'
        """,
        conn,
    )
    checkpoints = pd.read_sql_query(
        """
        SELECT
          checkpoint.*,
          event.event_kind,
          event.pit_lineage_class
        FROM weather_state_checkpoints AS checkpoint
        JOIN weather_information_events AS event
          ON event.information_event_id = checkpoint.trigger_event_id
        """,
        conn,
    )
    events = pd.read_sql_query(
        "SELECT * FROM weather_information_events",
        conn,
    )
    if candidates.empty:
        raise ValueError("no v2 event-checkpoint candidates")
    candidates["side_win"] = candidates["final_yes"]
    candidates.loc[candidates["side"] == "BUY_NO", "side_win"] = (
        1.0 - candidates.loc[candidates["side"] == "BUY_NO", "final_yes"]
    )
    yes = candidates[
        (candidates["side"] == "BUY_YES")
        & candidates["final_yes"].notna()
        & candidates["model_probability_after"].notna()
        & candidates["market_probability"].notna()
    ].drop_duplicates(["trigger_event_id", "condition_id"])
    proper = {
        "rows": int(len(yes)),
        "target_dates": int(yes["event_date"].nunique()),
        "events": int(yes["trigger_event_id"].nunique()),
        "model_brier": _brier(yes["model_probability_after"], yes["final_yes"]),
        "market_brier": _brier(yes["market_probability"], yes["final_yes"]),
        "brier_delta_model_minus_market": (
            _brier(yes["model_probability_after"], yes["final_yes"])
            - _brier(yes["market_probability"], yes["final_yes"])
        ),
        "model_logloss": _logloss(yes["model_probability_after"], yes["final_yes"]),
        "market_logloss": _logloss(yes["market_probability"], yes["final_yes"]),
        "logloss_delta_model_minus_market": (
            _logloss(yes["model_probability_after"], yes["final_yes"])
            - _logloss(yes["market_probability"], yes["final_yes"])
        ),
    }
    update_rows = yes[
        yes["model_probability_before"].notna()
        & yes["market_probability_before"].notna()
    ].copy()
    update_metrics = {
        "rows": int(len(update_rows)),
        "target_dates": int(update_rows["event_date"].nunique()),
    }
    if not update_rows.empty:
        update_metrics.update(
            {
                "model_brier_before": _brier(
                    update_rows["model_probability_before"],
                    update_rows["final_yes"],
                ),
                "model_brier_after": _brier(
                    update_rows["model_probability_after"],
                    update_rows["final_yes"],
                ),
                "market_brier_before": _brier(
                    update_rows["market_probability_before"],
                    update_rows["final_yes"],
                ),
                "market_brier_after": _brier(
                    update_rows["market_probability"],
                    update_rows["final_yes"],
                ),
                "mean_abs_market_move": float(
                    (
                        update_rows["market_probability"]
                        - update_rows["market_probability_before"]
                    ).abs().mean()
                ),
                "mean_abs_model_move": float(
                    (
                        update_rows["model_probability_after"]
                        - update_rows["model_probability_before"]
                    ).abs().mean()
                ),
            }
        )
    event_kind_rows = []
    for event_kind, group in yes.groupby("event_kind", dropna=False):
        event_kind_rows.append(
            {
                "event_kind": str(event_kind),
                "rows": int(len(group)),
                "target_dates": int(group["event_date"].nunique()),
                "model_brier": _brier(group["model_probability_after"], group["final_yes"]),
                "market_brier": _brier(group["market_probability"], group["final_yes"]),
            }
        )
    signal_funnel = {
        "information_events": int(len(events)),
        "material_events_with_checkpoint": int(checkpoints["trigger_event_id"].nunique()),
        "built_checkpoints": int((checkpoints["checkpoint_status"] == "built").sum()),
        "mapped_candidate_expressions": int(len(candidates)),
        "scored_candidate_expressions": int((candidates["candidate_status"] == "scored").sum()),
        "positive_mid_residual_expressions": int(
            (
                (candidates["candidate_status"] == "scored")
                & (candidates["probability_residual"] > 0)
            ).sum()
        ),
    }
    evidence_funnel = {
        "collector_exact_events": int((events["pit_lineage_class"] == "collector_exact").sum()),
        "archive_known_available_events": int(
            (events["pit_lineage_class"] == "archive_known_available").sum()
        ),
        "fresh_post_event_book_expressions": int(
            (candidates["market_evidence_status"] == "fresh_post_event_book").sum()
        ),
        "settled_expressions": int(candidates["final_yes"].notna().sum()),
        "scored_and_settled_expressions": int(
            (
                (candidates["candidate_status"] == "scored")
                & candidates["final_yes"].notna()
            ).sum()
        ),
    }
    return {
        "target": (
            "Estimate P(exact bracket | PIT event checkpoint) and test it against "
            "same-row market probability; this replay uses the existing paper-snapshot "
            "model as the initial model baseline."
        ),
        "signal_funnel": signal_funnel,
        "evidence_funnel": evidence_funnel,
        "proper_scores": proper,
        "pre_post_update": update_metrics,
        "event_kind_slices": event_kind_rows,
        "policies": [_policy(candidates, threshold) for threshold in (0.0, 0.02, 0.05)],
        "status": "inconclusive",
        "significance": "NA: fewer than the required independent target dates",
    }


def _pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value):.2%}"


def render_markdown(result: dict[str, Any]) -> str:
    proper = result["proper_scores"]
    update = result["pre_post_update"]
    lines = [
        "# First-seen exact-bracket residual v1",
        "",
        "Status: inconclusive; infrastructure replay only",
        "",
        "## Action",
        "",
        "Keep zero-notional collection. Do not create a live selector from this replay.",
        "",
        "## Target",
        "",
        result["target"],
        "",
        "## Signal funnel",
        "",
    ]
    lines.extend(
        f"- {key}: {value}" for key, value in result["signal_funnel"].items()
    )
    lines.extend(["", "## Evidence funnel", ""])
    lines.extend(
        f"- {key}: {value}" for key, value in result["evidence_funnel"].items()
    )
    lines.extend(
        [
            "",
            "## Same-row probability baseline",
            "",
            f"- rows: {proper['rows']} across {proper['target_dates']} target dates",
            f"- model Brier: {proper['model_brier']:.6f}",
            f"- market Brier: {proper['market_brier']:.6f}",
            f"- model − market Brier: {proper['brier_delta_model_minus_market']:+.6f}",
            f"- model logloss: {proper['model_logloss']:.6f}",
            f"- market logloss: {proper['market_logloss']:.6f}",
            f"- model − market logloss: {proper['logloss_delta_model_minus_market']:+.6f}",
            "",
            "## Pre/post event update",
            "",
            f"- rows with both pre/post books and model values: {update['rows']}",
            f"- target dates: {update['target_dates']}",
            f"- mean absolute market move: {update.get('mean_abs_market_move', float('nan')):.6f}",
            f"- mean absolute model move: {update.get('mean_abs_model_move', float('nan')):.6f}",
            f"- market Brier before → after: {update.get('market_brier_before', float('nan')):.6f} → {update.get('market_brier_after', float('nan')):.6f}",
            f"- model Brier before → after: {update.get('model_brier_before', float('nan')):.6f} → {update.get('model_brier_after', float('nan')):.6f}",
            "",
            "## Fee-adjusted taker expression diagnostics",
            "",
            "| min edge | expressions | dates | win rate | avg entry | ROI | expressions/day |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in result["policies"]:
        lines.append(
            "| {threshold:.0%} | {selected_expressions} | {target_dates} | {win_rate} | "
            "{average_entry} | {roi} | {expressions_per_day} |".format(
                threshold=row["threshold"],
                selected_expressions=row.get("selected_expressions", 0),
                target_dates=row.get("target_dates", 0),
                win_rate=_pct(row.get("win_rate")),
                average_entry=_pct(row.get("average_entry")),
                roi=_pct(row.get("fee_adjusted_roi")),
                expressions_per_day=(
                    "NA"
                    if row.get("expressions_per_day") is None
                    else f"{row['expressions_per_day']:.2f}"
                ),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is archive-known availability, not exact collector first-seen latency evidence.",
            "- The replay has too few independent target dates for bootstrap or live promotion.",
            "- Each row above is an event-condition expression, not a deployable trade count; no city-day lock or execution policy is applied.",
            "- Threshold rows are diagnostics on a fixed denominator, not eligibility gates.",
            "- Forward collection must retain every event/checkpoint/expression and missing-book row.",
            "",
            f"significance={result['significance']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--report-out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        result = analyze(conn)
    finally:
        conn.close()
    json_out = Path(args.json_out)
    report_out = Path(args.report_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    report_out.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
