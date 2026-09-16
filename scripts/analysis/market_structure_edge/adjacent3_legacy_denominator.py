"""Shared reconstruction of the historical BUY_YES-only adjacent3 denominator.

This module exists only so later union-denominator audits can quantify the old
coverage defect without depending on the retired report producers.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Any

import research_range_rv_scanner as scanner


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _normalize(values: list[float]) -> list[float]:
    total = sum(value for value in values if value > 0)
    if total <= 0:
        return [0.0 for _ in values]
    return [max(value, 0.0) / total for value in values]


def _entropy(probs: list[float]) -> float:
    positive = [probability for probability in probs if probability > 0]
    if not positive:
        return 0.0
    denominator = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(probability * math.log(probability) for probability in positive) / denominator


def load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load the intentionally historical BUY_YES-only universe."""
    return _rows(
        conn,
        """
        SELECT
          candidate_id, condition_id, market_id, side, event_date, city,
          city_pool, bracket, forecast_source, model_version,
          decision_hours_to_settle, decision_snapshot_ts_utc, model_p_yes,
          market_yes_price, yes_spread, live_filled, final_yes,
          settlement_status, decision_window_missing, fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side='BUY_YES'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL
          AND market_id IS NOT NULL
          AND event_date IS NOT NULL
          AND city IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
        """,
    )


def build_decision_sets(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reproduce the old three-leg mode basket for coverage comparison only."""
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
            str(row["decision_snapshot_ts_utc"]),
        )
        grouped[key][str(row["bracket"])] = row

    decision_sets: list[dict[str, Any]] = []
    for key, by_bracket in grouped.items():
        legs = sorted(
            by_bracket.values(),
            key=lambda row: scanner.bracket_sort_value(str(row["bracket"])),
        )
        if len(legs) < 3:
            continue
        model = _normalize([float(row["model_p_yes"]) for row in legs])
        market = _normalize([float(row["market_yes_price"]) for row in legs])
        if not any(model):
            continue
        mode_i = max(range(len(model)), key=lambda index: model[index])
        start = max(0, min(mode_i - 1, len(legs) - 3))
        selected = legs[start : start + 3]
        labels_ready = all(
            row.get("settlement_status") == "settled" and row.get("final_yes") is not None
            for row in selected
        )
        final_hit = (
            int(any(float(row["final_yes"]) >= 0.5 for row in selected))
            if labels_ready
            else None
        )
        model_mass = sum(model[start : start + 3])
        market_cost = sum(float(row["market_yes_price"]) for row in selected)
        decision_sets.append(
            {
                "decision_set_id": "|".join(key),
                "city": key[0],
                "event_date": key[1],
                "target_date": key[1],
                "forecast_source": key[2],
                "model_version": key[3],
                "decision_snapshot_ts_utc": key[4],
                "decision_hours_to_settle": selected[0]["decision_hours_to_settle"],
                "settlement_status": "settled" if labels_ready else "unsettled_or_unusable",
                "n_brackets": len(legs),
                "mode_i": mode_i,
                "selected_legs": selected,
                "selected_brackets": [str(row["bracket"]) for row in selected],
                "condition_ids": [str(row["condition_id"]) for row in selected],
                "model_adjacent3_mass": model_mass,
                "market_adjacent3_cost": market_cost,
                "range_edge": model_mass - market_cost,
                "model_entropy": _entropy(model),
                "model_mode_probability": model[mode_i],
                "model_tail_mass_outside_adjacent3": 1.0 - model_mass,
                "model_market_l1_gap": sum(abs(a - b) for a, b in zip(model, market)),
                "final_hit": final_hit,
                "settled_payout": None if final_hit is None else float(final_hit),
                "decision_proxy_cost": market_cost,
                "decision_proxy_pnl": None if final_hit is None else float(final_hit) - market_cost,
            }
        )
    return decision_sets
