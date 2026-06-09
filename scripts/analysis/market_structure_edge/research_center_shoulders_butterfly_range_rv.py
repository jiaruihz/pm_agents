#!/usr/bin/env python3
"""Forecast-first center/shoulders butterfly Range RV research.

Target metric:
    forecast_center_shoulders_range_rv_alpha

This is counterfactual research only. It reads `runtime/weather.db`
`fact_signal_candidates` as the opportunity grain and uses `fact_trades` only
for the mandatory weather analysis self-check. Executable prices reuse the
existing time-aligned orderbook matcher, which enforces
`orderbook_snapshot_ts <= decision_snapshot_ts_utc`.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


TARGET_METRIC = "forecast_center_shoulders_range_rv_alpha"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-center-shoulders-butterfly-range-rv.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-center-shoulders-butterfly-range-rv.md"

MIN_HOLDOUT_ACTIVE_DATES = 5
MIN_HOLDOUT_ROWS = 10

STRATEGIES: dict[str, dict[str, Any]] = {
    "center_cheap_butterfly": {
        "label": "center cheap: BUY_YES center + BUY_NO adjacent shoulders",
        "score_min": 0.08,
        "min_mode_probability": 0.24,
        "min_adjacent3_mass": 0.55,
        "max_tail_model_mass": 0.30,
        "max_loss_probability": 0.50,
        "max_large_loss_probability": 0.35,
        "max_spread": 0.22,
        "min_mode_gap": 0.03,
    },
    "shoulders_cheap_butterfly": {
        "label": "shoulders cheap: BUY_YES two shoulders + BUY_NO center",
        "score_min": 0.08,
        "min_mode_probability": 0.24,
        "min_adjacent3_mass": 0.55,
        "max_tail_model_mass": 0.30,
        "max_loss_probability": 0.55,
        "max_large_loss_probability": 0.40,
        "max_spread": 0.22,
        "min_mode_gap": 0.03,
    },
    "center_band_cheap_neighbors": {
        "label": "center band cheap: BUY_YES adjacent3 center band + BUY_NO outside neighbors",
        "score_min": 0.10,
        "min_mode_probability": 0.24,
        "min_adjacent3_mass": 0.55,
        "max_tail_model_mass": 0.30,
        "max_loss_probability": 0.55,
        "max_large_loss_probability": 0.40,
        "max_spread": 0.22,
        "min_mode_gap": 0.03,
    },
    "tail_center_contrast": {
        "label": "tail/center contrast: BUY_YES center band + BUY_NO tails",
        "score_min": 0.15,
        "min_mode_probability": 0.24,
        "min_adjacent3_mass": 0.55,
        "max_tail_model_mass": 0.28,
        "max_loss_probability": 0.55,
        "max_large_loss_probability": 0.40,
        "max_spread": 0.22,
        "min_mode_gap": 0.03,
    },
}


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


def num(value: float | None, digits: int = 3) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def entropy(probs: list[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0)


def normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        return {key: 0.0 for key in values}
    return {key: value / total for key, value in values.items()}


def hts_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 22:
        return "<22"
    if value < 24:
        return "22-24"
    if value < 28:
        return "24-28"
    return ">=28"


def leg_cost(side: str, yes_price: float) -> float:
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def leg_payout(side: str, final_yes: float) -> float:
    return final_yes if side == "BUY_YES" else 1.0 - final_yes


def score_edge(row: dict[str, Any]) -> float:
    return float(row["model_p_yes"]) - float(row["market_yes_price"])


def side_spread_ok(legs: list[dict[str, Any]], max_spread: float) -> bool:
    for leg in legs:
        value = scanner.side_spread(leg, str(leg["side"]))
        if value is not None and value > max_spread:
            return False
    return True


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def load_all_candidate_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          side,
          event_date,
          bracket,
          city,
          city_pool,
          icao,
          forecast_source,
          model_version,
          decision_window_label,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          decision_window_missing,
          model_p_yes,
          market_yes_price,
          decision_entry_price,
          yes_spread,
          no_spread,
          eligible,
          paper_ordered,
          live_filled,
          settlement_status,
          final_yes,
          fact_built_at_utc
        FROM fact_signal_candidates
        """,
    )


def usable_decision_rows(all_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in all_rows:
        price = row.get("market_yes_price")
        if (
            row.get("settlement_status") == "settled"
            and int(row.get("decision_window_missing") or 0) == 0
            and row.get("decision_snapshot_ts_utc") is not None
            and row.get("final_yes") is not None
            and row.get("model_p_yes") is not None
            and price is not None
            and 0.0 < float(price) < 1.0
        ):
            out.append(row)
    return out


def aggregate_decision_sets(rows_in: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows_in:
        grouped[(str(row["city"]), str(row["event_date"]), str(row["decision_snapshot_ts_utc"]))].append(row)

    decision_sets: list[list[dict[str, Any]]] = []
    for group_rows in grouped.values():
        by_bracket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in group_rows:
            by_bracket[str(row["bracket"])].append(row)

        model_versions = sorted({str(row["model_version"]) for row in group_rows if row.get("model_version")})
        model_mode_by_version: dict[str, str] = {}
        for model_version in model_versions:
            model_rows = [row for row in group_rows if str(row.get("model_version")) == model_version]
            if not model_rows:
                continue
            by_model_bracket: dict[str, float] = defaultdict(float)
            for row in model_rows:
                by_model_bracket[str(row["bracket"])] += float(row["model_p_yes"])
            model_norm = normalize(by_model_bracket)
            model_mode_by_version[model_version] = max(model_norm, key=model_norm.get)

        stable_mode = None
        mode_stability_ratio = 0.0
        if model_mode_by_version:
            counts = Counter(model_mode_by_version.values())
            stable_mode, count = counts.most_common(1)[0]
            mode_stability_ratio = count / len(model_mode_by_version)

        aggregated: list[dict[str, Any]] = []
        for bracket, items in by_bracket.items():
            rep = sorted(items, key=lambda row: (str(row.get("model_version") or ""), str(row.get("candidate_id") or "")))[0]
            model_avg = sum(float(row["model_p_yes"]) for row in items) / len(items)
            market_avg = sum(float(row["market_yes_price"]) for row in items) / len(items)
            decision_entry_values = [row.get("decision_entry_price") for row in items if row.get("decision_entry_price") is not None]
            aggregated.append(
                {
                    **rep,
                    "bracket": bracket,
                    "model_p_yes": model_avg,
                    "market_yes_price": market_avg,
                    "decision_entry_price": (
                        sum(float(value) for value in decision_entry_values) / len(decision_entry_values)
                        if decision_entry_values
                        else market_avg
                    ),
                    "model_versions_present": model_versions,
                    "model_mode_by_version": model_mode_by_version,
                    "stable_mode_bracket": stable_mode,
                    "mode_stability_ratio": mode_stability_ratio,
                }
            )
        ordered = sorted(aggregated, key=lambda row: scanner.bracket_sort_value(str(row["bracket"])))
        if len(ordered) >= 3:
            decision_sets.append(ordered)
    return decision_sets


def distribution_state(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    market = normalize({str(row["bracket"]): float(row["market_yes_price"]) for row in items})
    model = normalize({str(row["bracket"]): float(row["model_p_yes"]) for row in items})
    if not model or not market:
        return None

    mode_bracket = str(items[0].get("stable_mode_bracket") or max(model, key=model.get))
    mode_idx = next((idx for idx, row in enumerate(items) if str(row["bracket"]) == mode_bracket), None)
    if mode_idx is None or mode_idx <= 0 or mode_idx >= len(items) - 1:
        return None

    band_idxs = [mode_idx - 1, mode_idx, mode_idx + 1]
    band = [items[idx] for idx in band_idxs]
    outside_neighbors = []
    if mode_idx - 2 >= 0:
        outside_neighbors.append(items[mode_idx - 2])
    if mode_idx + 2 < len(items):
        outside_neighbors.append(items[mode_idx + 2])
    tails = [row for idx, row in enumerate(items) if idx not in band_idxs]

    model_adj3_mass = sum(model[str(row["bracket"])] for row in band)
    market_adj3_mass = sum(market[str(row["bracket"])] for row in band)
    tail_model_mass = sum(model[str(row["bracket"])] for row in tails)
    tail_market_mass = sum(market[str(row["bracket"])] for row in tails)
    ranked_model_probs = sorted(model.values(), reverse=True)
    mode_gap = ranked_model_probs[0] - ranked_model_probs[1] if len(ranked_model_probs) >= 2 else ranked_model_probs[0]
    return {
        "model_dist": model,
        "market_dist": market,
        "mode_idx": mode_idx,
        "mode_bracket": mode_bracket,
        "mode_probability": model[mode_bracket],
        "mode_gap": mode_gap,
        "adjacent3": band,
        "outside_neighbors": outside_neighbors,
        "tails": tails,
        "model_adjacent3_mass": model_adj3_mass,
        "market_adjacent3_mass": market_adj3_mass,
        "tail_model_mass": tail_model_mass,
        "tail_market_mass": tail_market_mass,
        "model_entropy": entropy(list(model.values())),
        "market_entropy": entropy(list(market.values())),
        "mode_stability_ratio": float(items[0].get("mode_stability_ratio") or 0.0),
        "model_versions_present": items[0].get("model_versions_present") or [],
        "model_mode_by_version": items[0].get("model_mode_by_version") or {},
        "decision_hours_to_settle_bucket": hts_bucket(
            sum(float(row["decision_hours_to_settle"]) for row in items if row.get("decision_hours_to_settle") is not None)
            / len([row for row in items if row.get("decision_hours_to_settle") is not None])
            if any(row.get("decision_hours_to_settle") is not None for row in items)
            else None
        ),
    }


def payoff_diagnostics(
    universe: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    model_dist: dict[str, float],
    cost_by_leg: dict[str, float],
) -> dict[str, Any]:
    cost = sum(cost_by_leg[f"{leg['side']}:{leg['bracket']}"] for leg in legs)
    actual_payout = sum(leg_payout(str(leg["side"]), float(leg["final_yes"])) for leg in legs)
    payoff_by_final_temp: dict[str, float] = {}
    for final_row in universe:
        final_bracket = str(final_row["bracket"])
        payout = 0.0
        for leg in legs:
            final_yes = 1.0 if str(leg["bracket"]) == final_bracket else 0.0
            payout += leg_payout(str(leg["side"]), final_yes)
        payoff_by_final_temp[final_bracket] = payout - cost
    expected_pnl = sum(model_dist.get(bracket, 0.0) * pnl for bracket, pnl in payoff_by_final_temp.items())
    return {
        "taker_cost": cost,
        "settled_payout": actual_payout,
        "taker_pnl": actual_payout - cost,
        "payoff_by_final_temp": payoff_by_final_temp,
        "expected_pnl": expected_pnl,
        "worst_case_loss": min(payoff_by_final_temp.values()) if payoff_by_final_temp else 0.0,
        "loss_probability": sum(model_dist.get(bracket, 0.0) for bracket, pnl in payoff_by_final_temp.items() if pnl < 0),
        "large_loss_probability": sum(model_dist.get(bracket, 0.0) for bracket, pnl in payoff_by_final_temp.items() if pnl <= -1.0),
    }


def make_row(
    *,
    strategy: str,
    items: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    score: float,
    state: dict[str, Any],
    components: dict[str, Any],
) -> dict[str, Any]:
    base = items[0]
    cost_by_leg = {
        f"{leg['side']}:{leg['bracket']}": leg_cost(str(leg["side"]), float(leg["market_yes_price"]))
        for leg in legs
    }
    diag = payoff_diagnostics(items, legs, state["model_dist"], cost_by_leg)
    yes_edges = [score_edge(leg) for leg in legs if leg["side"] == "BUY_YES"]
    no_edges = [-score_edge(leg) for leg in legs if leg["side"] == "BUY_NO"]
    return {
        "candidate_id": f"{strategy}|{base['city']}|{base['event_date']}|{base['decision_snapshot_ts_utc']}|"
        + ",".join(f"{leg['side']}:{leg['bracket']}" for leg in legs),
        "algorithm": strategy,
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy]["label"],
        "city": base["city"],
        "city_pool": base["city_pool"],
        "event_date": base["event_date"],
        "target_date": base["event_date"],
        "decision_snapshot_ts_utc": base["decision_snapshot_ts_utc"],
        "decision_dt": scanner.parse_ts(str(base["decision_snapshot_ts_utc"])),
        "decision_hours_to_settle": safe_div(
            sum(float(row["decision_hours_to_settle"]) for row in items if row.get("decision_hours_to_settle") is not None),
            len([row for row in items if row.get("decision_hours_to_settle") is not None]),
        ),
        "decision_hours_to_settle_bucket": state["decision_hours_to_settle_bucket"],
        "n_legs": len(legs),
        "brackets": [leg["bracket"] for leg in legs],
        "sides": [leg["side"] for leg in legs],
        "condition_ids": [leg["condition_id"] for leg in legs],
        "market_prob_sum": sum(state["market_dist"].get(str(leg["bracket"]), 0.0) for leg in legs if leg["side"] == "BUY_YES"),
        "model_prob_sum": sum(state["model_dist"].get(str(leg["bracket"]), 0.0) for leg in legs if leg["side"] == "BUY_YES"),
        "range_edge": sum(yes_edges) + sum(no_edges),
        "abs_range_edge": abs(sum(yes_edges) + sum(no_edges)),
        "score": score,
        "threshold": STRATEGIES[strategy]["score_min"],
        "selected": 0,
        "all_legs_eligible": int(all(int(leg.get("eligible") or 0) for leg in legs)),
        "paper_ordered_legs": sum(int(leg.get("paper_ordered") or 0) for leg in legs),
        "live_filled_legs": sum(int(leg.get("live_filled") or 0) for leg in legs),
        "model_mode_probability": state["mode_probability"],
        "model_mode_gap": state["mode_gap"],
        "model_adjacent3_mass": state["model_adjacent3_mass"],
        "market_adjacent3_mass": state["market_adjacent3_mass"],
        "model_entropy": state["model_entropy"],
        "market_entropy": state["market_entropy"],
        "tail_model_mass": state["tail_model_mass"],
        "tail_market_mass": state["tail_market_mass"],
        "mode_stability_ratio": state["mode_stability_ratio"],
        "model_versions_present": state["model_versions_present"],
        "model_mode_by_version": state["model_mode_by_version"],
        "components": components,
        "legs": [
            {
                "condition_id": leg["condition_id"],
                "market_id": leg["market_id"],
                "bracket": leg["bracket"],
                "side": leg["side"],
                "market_yes_price": leg["market_yes_price"],
                "final_yes": leg["final_yes"],
                "yes_spread": leg.get("yes_spread"),
                "no_spread": leg.get("no_spread"),
                "eligible": leg.get("eligible"),
            }
            for leg in legs
        ],
        "price_source": "decision_market_proxy",
        **diag,
    }


def raw_strategy_candidates(items: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    left, center, right = state["adjacent3"]
    candidates: list[dict[str, Any]] = []

    center_score = score_edge(center) + 0.5 * (-score_edge(left) - score_edge(right))
    candidates.append(
        make_row(
            strategy="center_cheap_butterfly",
            items=items,
            legs=[dict(center, side="BUY_YES"), dict(left, side="BUY_NO"), dict(right, side="BUY_NO")],
            score=center_score,
            state=state,
            components={"center": center["bracket"], "shoulders": [left["bracket"], right["bracket"]]},
        )
    )

    shoulders_score = 0.5 * (score_edge(left) + score_edge(right)) + (-score_edge(center))
    candidates.append(
        make_row(
            strategy="shoulders_cheap_butterfly",
            items=items,
            legs=[dict(left, side="BUY_YES"), dict(right, side="BUY_YES"), dict(center, side="BUY_NO")],
            score=shoulders_score,
            state=state,
            components={"center": center["bracket"], "shoulders": [left["bracket"], right["bracket"]]},
        )
    )

    if state["outside_neighbors"]:
        outside = state["outside_neighbors"]
        score = sum(score_edge(row) for row in state["adjacent3"]) + sum(-score_edge(row) for row in outside)
        candidates.append(
            make_row(
                strategy="center_band_cheap_neighbors",
                items=items,
                legs=[dict(row, side="BUY_YES") for row in state["adjacent3"]]
                + [dict(row, side="BUY_NO") for row in outside],
                score=score,
                state=state,
                components={
                    "center_band": [row["bracket"] for row in state["adjacent3"]],
                    "outside_neighbors": [row["bracket"] for row in outside],
                },
            )
        )

    if state["tails"]:
        score = sum(score_edge(row) for row in state["adjacent3"]) + sum(-score_edge(row) for row in state["tails"])
        candidates.append(
            make_row(
                strategy="tail_center_contrast",
                items=items,
                legs=[dict(row, side="BUY_YES") for row in state["adjacent3"]]
                + [dict(row, side="BUY_NO") for row in state["tails"]],
                score=score,
                state=state,
                components={
                    "center_band": [row["bracket"] for row in state["adjacent3"]],
                    "tails": [row["bracket"] for row in state["tails"]],
                },
            )
        )
    return candidates


def generate_strategy_rows(decision_sets: list[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    funnel: dict[str, Any] = {
        strategy: {
            "raw": 0,
            "model_mode_stable": 0,
            "mode_probability": 0,
            "adjacent3_mass": 0,
            "tail_model_mass": 0,
            "shape_score": 0,
            "risk": 0,
            "spread": 0,
        }
        for strategy in STRATEGIES
    }
    out: list[dict[str, Any]] = []
    for items in decision_sets:
        state = distribution_state(items)
        if state is None:
            continue
        raw_candidates = raw_strategy_candidates(items, state)
        for row in raw_candidates:
            strategy = str(row["strategy"])
            cfg = STRATEGIES[strategy]
            funnel[strategy]["raw"] += 1
            if not (len(state["model_versions_present"]) >= 2 and state["mode_stability_ratio"] >= 1.0):
                single_model_unique_mode = (
                    len(state["model_versions_present"]) == 1
                    and state["mode_gap"] >= cfg["min_mode_gap"]
                )
                if not single_model_unique_mode:
                    continue
            funnel[strategy]["model_mode_stable"] += 1
            if row["model_mode_probability"] < cfg["min_mode_probability"]:
                continue
            funnel[strategy]["mode_probability"] += 1
            if row["model_adjacent3_mass"] < cfg["min_adjacent3_mass"]:
                continue
            funnel[strategy]["adjacent3_mass"] += 1
            if row["tail_model_mass"] > cfg["max_tail_model_mass"]:
                continue
            funnel[strategy]["tail_model_mass"] += 1
            if (
                row["loss_probability"] > cfg["max_loss_probability"]
                or row["large_loss_probability"] > cfg["max_large_loss_probability"]
            ):
                continue
            funnel[strategy]["risk"] += 1
            if not side_spread_ok(row["legs"], cfg["max_spread"]):
                continue
            funnel[strategy]["spread"] += 1
            row["selected"] = int(row["score"] >= cfg["score_min"])
            if row["selected"]:
                funnel[strategy]["shape_score"] += 1
            out.append(row)
    return out, funnel


def metric_rows(rows_in: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return variants.metric_rows(rows_in, "orderbook" if source == "orderbook" else "proxy")


def avg_fields(rows_in: list[dict[str, Any]]) -> dict[str, float | None]:
    keys = [
        "model_mode_probability",
        "model_adjacent3_mass",
        "market_adjacent3_mass",
        "model_entropy",
        "market_entropy",
        "tail_model_mass",
        "tail_market_mass",
        "expected_pnl",
        "worst_case_loss",
        "loss_probability",
        "large_loss_probability",
    ]
    return {
        f"avg_{key}": safe_div(sum(float(row.get(key) or 0.0) for row in rows_in), len(rows_in))
        for key in keys
    }


def summarize_eval(selected: list[dict[str, Any]], baseline: list[dict[str, Any]], source: str, seed: int, iters: int) -> dict[str, Any]:
    selected_summary = scanner.summarize(selected, source=source)
    baseline_summary = scanner.summarize(baseline, source=source)
    selected_summary.update(avg_fields(selected))
    baseline_summary.update(avg_fields(baseline))
    selected_roi = selected_summary["taker_roi"]
    baseline_roi = baseline_summary["taker_roi"]
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    boot = scanner.bootstrap_delta(selected, baseline, iters=iters, seed=seed)
    return {
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": excess,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }


def tighten_gate(item: dict[str, Any]) -> None:
    gates = item["gates"]
    holdout_selected = item["holdout"]["selected"]
    reasons: list[str] = []
    if holdout_selected["rows"] < MIN_HOLDOUT_ROWS:
        gates["forward"] = "FAIL"
        reasons.append(f"holdout_rows<{MIN_HOLDOUT_ROWS}")
    if holdout_selected["active_event_dates"] < MIN_HOLDOUT_ACTIVE_DATES:
        gates["forward"] = "FAIL"
        reasons.append(f"holdout_active_dates<{MIN_HOLDOUT_ACTIVE_DATES}")
    if holdout_selected.get("drop_top5_taker_roi") is None or holdout_selected["drop_top5_taker_roi"] <= 0:
        gates["forward"] = "FAIL"
        reasons.append("holdout_top5_removed_roi<=0_or_na")
    gates["verdict"] = (
        "confirmed"
        if gates["significance"] == "PASS" and gates["baseline"] == "PASS" and gates["forward"] == "PASS"
        else "inconclusive"
    )
    item["gate_reasons"] = reasons


def evaluate_strategies(
    rows_in: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    out = []
    for idx, strategy in enumerate(STRATEGIES):
        family = [row for row in rows_in if row["strategy"] == strategy]
        train_family = [row for row in family if row["event_date"] in train_dates]
        holdout_family = [row for row in family if row["event_date"] in holdout_dates]
        train_selected = [row for row in train_family if int(row.get("selected") or 0) == 1]
        holdout_selected = [row for row in holdout_family if int(row.get("selected") or 0) == 1]
        train_eval = summarize_eval(train_selected, train_family, source, seed + idx * 100, iters)
        holdout_eval = summarize_eval(holdout_selected, holdout_family, source, seed + idx * 100 + 50, iters)
        item = {
            "strategy": strategy,
            "strategy_label": STRATEGIES[strategy]["label"],
            "source": source,
            "train_family_rows": len(train_family),
            "holdout_family_rows": len(holdout_family),
            "train": train_eval,
            "holdout": holdout_eval,
            "gates": scanner.gate_result(train_eval, holdout_eval),
        }
        tighten_gate(item)
        out.append(item)
    return out


def passed(item: dict[str, Any]) -> bool:
    gates = item["gates"]
    return gates["significance"] == "PASS" and gates["baseline"] == "PASS" and gates["forward"] == "PASS"


def rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(item["holdout"]["excess_roi"] if item["holdout"]["excess_roi"] is not None else -999.0),
        float(item["holdout"]["selected"]["drop_top5_taker_roi"] if item["holdout"]["selected"]["drop_top5_taker_roi"] is not None else -999.0),
        float(item["holdout"]["selected"]["active_event_dates"] or 0),
    )


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    skip = {"decision_dt"}
    return {key: value for key, value in row.items() if key not in skip}


def sample_candidates(rows_in: list[dict[str, Any]], limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for strategy in STRATEGIES:
        items = [row for row in rows_in if row["strategy"] == strategy]
        items = sorted(items, key=lambda row: (row["score"], row["expected_pnl"]), reverse=True)
        out[strategy] = [compact_row(row) for row in items[:limit]]
    return out


def hts_distribution(rows_in: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(str(row.get("decision_hours_to_settle_bucket") or "unknown") for row in rows_in)
    return [{"bucket": key, "rows": counter[key]} for key in sorted(counter)]


def conclusion_shape(proxy_results: list[dict[str, Any]], orderbook_results: list[dict[str, Any]]) -> dict[str, Any]:
    all_results = proxy_results + orderbook_results
    if not any(item["train"]["selected"]["rows"] for item in all_results):
        return {"verdict": "fail", "reason": "no_selected_rows_after_forecast_first_filters", "sample_or_structure": "structural_unavailable"}
    if any(passed(item) for item in proxy_results) and any(passed(item) for item in orderbook_results):
        return {"verdict": "confirmed", "reason": "proxy_and_orderbook_have_passing_strategy", "sample_or_structure": "usable"}
    positive_rows = [
        item
        for item in all_results
        if (item["holdout"]["selected"].get("taker_roi") or 0.0) > 0
        or (item["holdout"].get("excess_roi") or 0.0) > 0
    ]
    low_sample = any(
        item["holdout"]["selected"]["rows"] < MIN_HOLDOUT_ROWS
        or item["holdout"]["selected"]["active_event_dates"] < MIN_HOLDOUT_ACTIVE_DATES
        for item in all_results
        if item["holdout"]["selected"]["rows"] > 0
    )
    if positive_rows and low_sample:
        return {"verdict": "inconclusive", "reason": "some positive point estimates but holdout/orderbook sample too small or unstable", "sample_or_structure": "sample_limited"}
    return {"verdict": "inconclusive", "reason": "three_gate_failure_with_weak_or_negative_forward_excess", "sample_or_structure": "structurally_unconfirmed"}


def strategy_assessment(proxy_results: list[dict[str, Any]], orderbook_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_proxy = {item["strategy"]: item for item in proxy_results}
    by_orderbook = {item["strategy"]: item for item in orderbook_results}
    out = []
    for strategy in STRATEGIES:
        proxy = by_proxy.get(strategy)
        orderbook = by_orderbook.get(strategy)
        proxy_holdout_excess = proxy["holdout"]["excess_roi"] if proxy else None
        orderbook_holdout_excess = orderbook["holdout"]["excess_roi"] if orderbook else None
        proxy_selected = proxy["train"]["selected"]["rows"] + proxy["holdout"]["selected"]["rows"] if proxy else 0
        orderbook_train_selected = orderbook["train"]["selected"]["rows"] if orderbook else 0
        orderbook_holdout_selected = orderbook["holdout"]["selected"]["rows"] if orderbook else 0
        if proxy_selected >= 30 and (proxy_holdout_excess or 0.0) <= 0:
            assessment = "structurally_weak_proxy"
            reason = "decision-proxy holdout excess is non-positive versus same-family baseline; executable coverage is still a separate limitation"
        elif proxy_selected < MIN_HOLDOUT_ROWS or orderbook_train_selected < MIN_HOLDOUT_ROWS:
            assessment = "sample_limited"
            reason = "selected rows or executable train coverage below gate"
        elif (proxy_holdout_excess or 0.0) <= 0 and (orderbook_holdout_excess or 0.0) <= 0:
            assessment = "structurally_weak"
            reason = "holdout excess is non-positive versus same-family baseline"
        elif proxy and orderbook and passed(proxy) and passed(orderbook):
            assessment = "confirmed"
            reason = "proxy and executable orderbook both pass three gates"
        else:
            assessment = "unconfirmed"
            reason = "positive point estimate does not pass CI/baseline/forward gates"
        out.append(
            {
                "strategy": strategy,
                "assessment": assessment,
                "reason": reason,
                "proxy_holdout_excess_roi": proxy_holdout_excess,
                "orderbook_holdout_excess_roi": orderbook_holdout_excess,
                "proxy_selected_rows": proxy_selected,
                "orderbook_train_selected_rows": orderbook_train_selected,
                "orderbook_holdout_selected_rows": orderbook_holdout_selected,
            }
        )
    return out


def line_for_result(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    reasons = ",".join(item.get("gate_reasons") or [])
    return (
        f"| `{item['strategy']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {item['holdout_family_rows']} | {holdout['selected']['rows']} | "
        f"{holdout['selected']['active_event_dates']} | {pct(train['selected']['taker_roi'])} | "
        f"{fmt_ci(train['roi_ci95_cluster_by_event_date'])} | {pct(train['excess_roi'])} | "
        f"{fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['taker_roi'])} | "
        f"{fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | {pct(holdout['excess_roi'])} | "
        f"{fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"{pct(holdout['selected'].get('avg_model_mode_probability'))} | {pct(holdout['selected'].get('avg_model_adjacent3_mass'))} | "
        f"{pct(holdout['selected'].get('avg_tail_model_mass'))} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` | `{reasons}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Center vs Shoulders / Butterfly Range RV",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: counterfactual research only; no N100/live config changed.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates`; `fact_trades` 只用于强制自检。",
        f"- DB last_modified: `{report['db_last_modified_utc']}`",
        f"- fact built at: `{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`",
        f"- sync/rebuild: `{report['input']['sync_rebuild_note']}`",
        f"- unsettled used in research: `0`; missing_bracket used in research: `0`.",
            "- 不使用旧单腿策略 `eligible` 作为 Range RV 硬门；仅在 JSON 中保留 `all_legs_eligible` 诊断字段。",
            "- Mode stable 定义：同 decision snapshot 有多模型时要求 mode 一致；只有单模型时要求 mode 与第二名概率 gap >= 0.03，并标为 single-model unique-mode proxy。",
            "",
        "## Target Metric",
        "",
        "`forecast_center_shoulders_range_rv_alpha` = forecast-first center/shoulder/butterfly 表达的 selected ROI 减同表达 family baseline ROI。先锁模型分布，再看市场中心/肩部/尾部相对形状；baseline 是同 family、同 train/holdout 窗口内通过 forecast-first 结构但未按 score 过滤的候选全集。",
        "",
        "## Filter Funnel",
        "",
        "| step | count |",
        "|---|---:|",
        f"| fact_signal_candidates rows | {report['filter_funnel']['fact_signal_candidates_rows']} |",
        f"| settled + decision_window present rows | {report['filter_funnel']['settled_decision_window_present_rows']} |",
        f"| decision_sets | {report['filter_funnel']['decision_sets_count']} |",
        f"| center/shoulder/butterfly raw candidate count | {report['filter_funnel']['raw_expression_candidates_count']} |",
        f"| selected strategy rows after all filters | {report['filter_funnel']['selected_strategy_rows']} |",
        f"| orderbook fully matched selected rows | {report['filter_funnel']['orderbook_fully_matched_selected_rows']} |",
        "",
        "### Strategy Filter Counts",
        "",
        "| strategy | raw | mode stable | mode prob | adj3 mass | tail mass | risk | spread baseline | score selected |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, counts in report["filter_funnel"]["strategy_filters"].items():
        lines.append(
            f"| `{strategy}` | {counts['raw']} | {counts['model_mode_stable']} | {counts['mode_probability']} | "
            f"{counts['adjacent3_mass']} | {counts['tail_model_mass']} | {counts['risk']} | "
            f"{counts['spread']} | {counts['shape_score']} |"
        )
    lines.extend(
        [
            "",
            "## Train / Holdout",
            "",
            f"- Split field: `event_date`; split_date: `{report['split']['split_date']}`.",
            f"- Train: `{report['split']['train_start']}` to `{report['split']['train_end']}`, active_dates `{report['split']['train_active_dates']}`, family_rows `{report['split']['train_family_rows']}`, selected_rows `{report['split']['train_selected_rows']}`.",
            f"- Holdout: `{report['split']['holdout_start']}` to `{report['split']['holdout_end']}`, active_dates `{report['split']['holdout_active_dates']}`, family_rows `{report['split']['holdout_family_rows']}`, selected_rows `{report['split']['holdout_selected_rows']}`.",
            f"- Bootstrap: cluster by `event_date`, iters `{report['parameters']['bootstrap_iters']}`.",
            "",
            "## Verdict",
            "",
            "| gate | status |",
            "|---|---|",
        ]
    )
    for key, value in report["gates"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            f"Final conclusion: `{report['conclusion']['verdict']}`. `{report['conclusion']['reason']}`; classification `{report['conclusion']['sample_or_structure']}`. No live action.",
            "",
            "## Structure vs Sample",
            "",
            "| strategy | assessment | proxy selected rows | orderbook train selected | orderbook holdout selected | proxy holdout excess | orderbook holdout excess | reason |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["strategy_assessment"]:
        lines.append(
            f"| `{item['strategy']}` | `{item['assessment']}` | {item['proxy_selected_rows']} | "
            f"{item['orderbook_train_selected_rows']} | {item['orderbook_holdout_selected_rows']} | "
            f"{pct(item['proxy_holdout_excess_roi'])} | {pct(item['orderbook_holdout_excess_roi'])} | {item['reason']} |"
        )
    lines.extend(
        [
            "",
            "## Decision Proxy Results",
            "",
            "| strategy | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout mode p | holdout adj3 mass | holdout tail mass | gates | gate reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["decision_proxy_results"]:
        lines.append(line_for_result(item))
    lines.extend(
        [
            "",
            "## Executable Orderbook Subset",
            "",
            "Orderbook 匹配复用既有逻辑：同 condition/token 取 latest `snapshot_ts_utc <= decision_snapshot_ts_utc`，并要求该 strategy 所有 leg 都匹配成功。",
            "",
            f"- Leg matched: `{report['orderbook_coverage'].get('matched_candidate_rows')}` / `{report['orderbook_coverage'].get('candidate_rows')}`.",
            f"- Fully matched family rows: `{report['orderbook_coverage'].get('fully_matched_strategy_rows')}` / `{report['orderbook_coverage'].get('strategy_rows')}`.",
            f"- Fully matched selected rows: `{report['filter_funnel']['orderbook_fully_matched_selected_rows']}` / `{report['filter_funnel']['selected_strategy_rows']}`.",
            "",
            "| strategy | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | holdout mode p | holdout adj3 mass | holdout tail mass | gates | gate reasons |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in report["orderbook_results"]:
        lines.append(line_for_result(item))
    lines.extend(
        [
            "",
            "## Forecast / Market Diagnostics",
            "",
            "| bucket | rows |",
            "|---|---:|",
        ]
    )
    for item in report["diagnostics"]["decision_hours_to_settle_buckets"]:
        lines.append(f"| `{item['bucket']}` | {item['rows']} |")
    lines.extend(
        [
            "",
            "| strategy | rows | avg mode p | avg model adj3 | avg market adj3 | avg model entropy | avg market entropy | avg tail model | avg tail market | avg expected pnl | avg worst loss | avg loss prob | avg large loss prob |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy, item in report["diagnostics"]["by_strategy"].items():
        lines.append(
            f"| `{strategy}` | {item['rows']} | {pct(item.get('avg_model_mode_probability'))} | "
            f"{pct(item.get('avg_model_adjacent3_mass'))} | {pct(item.get('avg_market_adjacent3_mass'))} | "
            f"{num(item.get('avg_model_entropy'))} | {num(item.get('avg_market_entropy'))} | "
            f"{pct(item.get('avg_tail_model_mass'))} | {pct(item.get('avg_tail_market_mass'))} | "
            f"{money(item.get('avg_expected_pnl'))} | {money(item.get('avg_worst_case_loss'))} | "
            f"{pct(item.get('avg_loss_probability'))} | {pct(item.get('avg_large_loss_probability'))} |"
        )
    lines.extend(
        [
            "",
            "## Payoff Examples",
            "",
            "完整 `payoff_by_final_temp`、`expected_pnl`、`worst_case_loss`、`loss_probability`、`large_loss_probability` 写入 JSON 的 `candidate_samples_by_strategy`。",
            "",
            "## 8 环覆盖自检",
            "",
            "| 环 | 覆盖 | 说明 |",
            "|---|---|---|",
            "| 1 描述性绩效切片 | yes | opportunity-grain counterfactual from fact_signal_candidates |",
            "| 2 统计推断 | yes | event_date cluster bootstrap |",
            "| 3 信号判别 | partial | forecast-first mode/adj3/tail filters, no independent calibration proof |",
            "| 4 概率分布评估 | partial | entropy/mass diagnostics only |",
            "| 5 执行微结构 | partial | time-aligned orderbook executable subset |",
            "| 6 容量 | no | no size/depth sweep |",
            "| 7 组合相关性 | partial | event_date cluster bootstrap, no rho model |",
            "| 8 基准/反事实 | yes | same-family baseline and excess ROI |",
            "",
            "## Notes",
            "",
            "- 四个表达是预注册固定定义；没有按城市/日期/model post-pick winner。",
            "- 三门不过即 `inconclusive`；不允许改 live、city_pool、paper_policy 或 N100 配置。",
            "- `eligible` 没有参与过滤；这是 full opportunity counterfactual 研究。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = scanner.connect(args.db_path)
    all_rows = load_all_candidate_rows(conn)
    usable_rows = usable_decision_rows(all_rows)
    decision_sets = aggregate_decision_sets(usable_rows)
    strategy_rows, strategy_funnel = generate_strategy_rows(decision_sets)

    coverage: dict[str, Any] = {"status": "skipped", "fully_matched_strategy_rows": 0, "strategy_rows": len(strategy_rows)}
    if not args.skip_orderbook:
        coverage = variants.attach_orderbook(strategy_rows, args.orderbook_glob)

    all_dates = sorted({str(row["event_date"]) for row in strategy_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    selected_strategy_rows = [row for row in strategy_rows if int(row.get("selected") or 0) == 1]
    orderbook_selected_rows = [
        row
        for row in selected_strategy_rows
        if row.get("orderbook_taker_cost") is not None and row.get("orderbook_taker_pnl") is not None
    ]
    proxy_rows = metric_rows(strategy_rows, "proxy")
    orderbook_rows = metric_rows(strategy_rows, "orderbook")

    proxy_results = evaluate_strategies(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = evaluate_strategies(
        orderbook_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )
    proxy_pass = {item["strategy"] for item in proxy_results if passed(item)}
    orderbook_pass = {item["strategy"] for item in orderbook_results if passed(item)}
    confirmed = sorted(proxy_pass & orderbook_pass)
    gates = (
        {"significance": "PASS", "baseline": "PASS", "forward": "PASS", "verdict": "confirmed"}
        if confirmed
        else {"significance": "FAIL", "baseline": "FAIL", "forward": "FAIL", "verdict": "inconclusive"}
    )
    conclusion = conclusion_shape(proxy_results, orderbook_results)
    by_strategy_diag = {
        strategy: {
            "rows": len([row for row in selected_strategy_rows if row["strategy"] == strategy]),
            **avg_fields([row for row in selected_strategy_rows if row["strategy"] == strategy]),
        }
        for strategy in STRATEGIES
    }
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "train_frac": args.train_frac,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "strategies": STRATEGIES,
            "min_holdout_active_dates": MIN_HOLDOUT_ACTIVE_DATES,
            "min_holdout_rows": MIN_HOLDOUT_ROWS,
        },
        "data_self_check": scanner.data_self_check(conn),
        "filter_funnel": {
            "fact_signal_candidates_rows": len(all_rows),
            "settled_decision_window_present_rows": len(usable_rows),
            "decision_sets_count": len(decision_sets),
            "raw_expression_candidates_count": sum(item["raw"] for item in strategy_funnel.values()),
            "strategy_filters": strategy_funnel,
            "selected_strategy_rows": len(selected_strategy_rows),
            "orderbook_fully_matched_selected_rows": len(orderbook_selected_rows),
        },
        "input": {
            "sync_rebuild_note": "ran scripts/ops/sync_weather_remote.sh and scripts/weather_dashboard/run_stack.sh --api-only before research",
            "candidate_rows": len(all_rows),
            "usable_rows": len(usable_rows),
            "decision_sets": len(decision_sets),
            "strategy_family_rows": len(strategy_rows),
            "strategy_selected_rows": len(selected_strategy_rows),
            "event_dates": len(all_dates),
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "train_active_dates": len(train_dates),
            "holdout_active_dates": len(holdout_dates),
            "train_family_rows": len([row for row in strategy_rows if row["event_date"] in train_dates]),
            "holdout_family_rows": len([row for row in strategy_rows if row["event_date"] in holdout_dates]),
            "train_selected_rows": len([row for row in selected_strategy_rows if row["event_date"] in train_dates]),
            "holdout_selected_rows": len([row for row in selected_strategy_rows if row["event_date"] in holdout_dates]),
        },
        "diagnostics": {
            "decision_hours_to_settle_buckets": hts_distribution(strategy_rows),
            "by_strategy": by_strategy_diag,
        },
        "orderbook_coverage": coverage,
        "decision_proxy_results": sorted(proxy_results, key=rank_key, reverse=True),
        "orderbook_results": sorted(orderbook_results, key=rank_key, reverse=True),
        "confirmed_strategies": confirmed,
        "gates": gates,
        "conclusion": conclusion,
        "strategy_assessment": strategy_assessment(proxy_results, orderbook_results),
        "candidate_samples_by_strategy": sample_candidates(selected_strategy_rows, limit=5),
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={conclusion['verdict']} confirmed={confirmed}")


if __name__ == "__main__":
    main()
