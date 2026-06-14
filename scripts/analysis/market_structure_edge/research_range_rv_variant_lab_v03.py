#!/usr/bin/env python3
"""Range RV variant lab v0.3.

This script tries multiple pre-registered Range RV expressions without
city/date/model post-selection:

- contiguous range YES mass
- normalized probability mass
- adjacent pair relative-value spread
- center-vs-shoulders butterflies
- range-vs-neighbor outside brackets
- overpriced tail fade
- model-mode cheap cluster

Each algorithm emits at most one candidate per city-day decision snapshot. The
selected rule is fixed by the algorithm definition; the baseline is the same
algorithm's top unfiltered candidate per city-day. A profile is confirmed only
if decision-proxy and time-aligned orderbook subsets both pass the three gates.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

import research_range_rv_scanner as scanner  # noqa: E402


OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-variant-lab-v0-3.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-09-range-rv-variant-lab-v0-3.md"


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


def leg_cost(side: str, yes_price: float) -> float:
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def leg_payout(side: str, final_yes: float) -> float:
    return final_yes if side == "BUY_YES" else 1.0 - final_yes


def normalized_probs(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    total = sum(float(row[key]) for row in items)
    if total <= 0:
        return {str(row["bracket"]): 0.0 for row in items}
    return {str(row["bracket"]): float(row[key]) / total for row in items}


def score_edge(row: dict[str, Any]) -> float:
    return float(row["model_p_yes"]) - float(row["market_yes_price"])


def state_metrics(
    universe: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    *,
    model_norm: dict[str, float],
) -> dict[str, Any]:
    cost = sum(leg_cost(leg["side"], float(leg["market_yes_price"])) for leg in legs)
    actual_payout = sum(leg_payout(leg["side"], float(leg["final_yes"])) for leg in legs)
    payoff_by_final_temp: dict[str, float] = {}
    for final_row in universe:
        final_bracket = str(final_row["bracket"])
        payout = 0.0
        for leg in legs:
            final_yes = 1.0 if str(leg["bracket"]) == final_bracket else 0.0
            payout += leg_payout(leg["side"], final_yes)
        payoff_by_final_temp[final_bracket] = payout - cost
    expected_pnl = sum(model_norm.get(bracket, 0.0) * pnl for bracket, pnl in payoff_by_final_temp.items())
    loss_probability = sum(model_norm.get(bracket, 0.0) for bracket, pnl in payoff_by_final_temp.items() if pnl < 0)
    return {
        "taker_cost": cost,
        "settled_payout": actual_payout,
        "taker_pnl": actual_payout - cost,
        "payoff_by_final_temp": payoff_by_final_temp,
        "expected_pnl": expected_pnl,
        "worst_case_loss": min(payoff_by_final_temp.values()) if payoff_by_final_temp else 0.0,
        "loss_probability": loss_probability,
        "large_loss_probability": sum(
            model_norm.get(bracket, 0.0) for bracket, pnl in payoff_by_final_temp.items() if pnl <= -1.0
        ),
    }


def make_candidate(
    *,
    algorithm: str,
    decision_items: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    score: float,
    threshold: float,
    components: dict[str, Any],
) -> dict[str, Any]:
    base = decision_items[0]
    model_norm = normalized_probs(decision_items, "model_p_yes")
    market_norm = normalized_probs(decision_items, "market_yes_price")
    metrics = state_metrics(decision_items, legs, model_norm=model_norm)
    model_mass = sum(model_norm[str(leg["bracket"])] for leg in legs if leg["side"] == "BUY_YES")
    market_mass = sum(market_norm[str(leg["bracket"])] for leg in legs if leg["side"] == "BUY_YES")
    yes_edges = [score_edge(leg) for leg in legs if leg["side"] == "BUY_YES"]
    no_edges = [-score_edge(leg) for leg in legs if leg["side"] == "BUY_NO"]
    leg_edge = sum(yes_edges) + sum(no_edges)
    return {
        "candidate_id": (
            f"{algorithm}|{base['city']}|{base['event_date']}|{base.get('forecast_source', '')}|"
            f"{base.get('model_version', '')}|{base['decision_snapshot_ts_utc']}|"
            + ",".join(f"{leg['side']}:{leg['bracket']}" for leg in legs)
        ),
        "algorithm": algorithm,
        "city": base["city"],
        "city_pool": base["city_pool"],
        "event_date": base["event_date"],
        "target_date": base["event_date"],
        "forecast_source": base.get("forecast_source"),
        "model_version": base.get("model_version"),
        "decision_snapshot_ts_utc": base["decision_snapshot_ts_utc"],
        "decision_dt": scanner.parse_ts(str(base["decision_snapshot_ts_utc"])),
        "n_legs": len(legs),
        "brackets": [leg["bracket"] for leg in legs],
        "sides": [leg["side"] for leg in legs],
        "condition_ids": [leg["condition_id"] for leg in legs],
        "market_prob_sum": market_mass,
        "model_prob_sum": model_mass,
        "range_edge": leg_edge,
        "abs_range_edge": abs(leg_edge),
        "score": score,
        "threshold": threshold,
        "selected": int(score >= threshold),
        "all_legs_eligible": int(all(int(leg.get("eligible") or 0) for leg in legs)),
        "paper_ordered_legs": sum(int(leg.get("paper_ordered") or 0) for leg in legs),
        "live_filled_legs": sum(int(leg.get("live_filled") or 0) for leg in legs),
        "components": components,
        "legs": [
            {
                "condition_id": leg["condition_id"],
                "market_id": leg["market_id"],
                "bracket": leg["bracket"],
                "side": leg["side"],
                "market_yes_price": leg["market_yes_price"],
                "final_yes": leg["final_yes"],
            }
            for leg in legs
        ],
        "price_source": "decision_market_proxy",
        **metrics,
    }


def top_or_none(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row["score"], row["expected_pnl"]), reverse=True)[0]


def emit_adjacent_mass(items: list[dict[str, Any]], width: int, *, normalized: bool, threshold: float, algorithm: str) -> dict[str, Any] | None:
    if len(items) < width:
        return None
    model_norm = normalized_probs(items, "model_p_yes")
    market_norm = normalized_probs(items, "market_yes_price")
    candidates = []
    for start in range(0, len(items) - width + 1):
        legs_raw = items[start : start + width]
        if normalized:
            score = sum(model_norm[str(row["bracket"])] - market_norm[str(row["bracket"])] for row in legs_raw)
        else:
            score = sum(score_edge(row) for row in legs_raw)
        legs = [dict(row, side="BUY_YES") for row in legs_raw]
        candidates.append(
            make_candidate(
                algorithm=algorithm,
                decision_items=items,
                legs=legs,
                score=score,
                threshold=threshold,
                components={"width": width, "normalized": normalized},
            )
        )
    return top_or_none(candidates)


def emit_pair_spread(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for left, right in zip(items, items[1:]):
        left_edge = score_edge(left)
        right_edge = score_edge(right)
        if left_edge >= right_edge:
            high, low = left, right
            score = left_edge - right_edge
        else:
            high, low = right, left
            score = right_edge - left_edge
        legs = [dict(high, side="BUY_YES"), dict(low, side="BUY_NO")]
        candidates.append(
            make_candidate(
                algorithm="pair_spread_adjacent_e010",
                decision_items=items,
                legs=legs,
                score=score,
                threshold=threshold,
                components={"high_bracket": high["bracket"], "low_bracket": low["bracket"]},
            )
        )
    return top_or_none(candidates)


def emit_center_shoulders(items: list[dict[str, Any]], *, shoulders: bool, threshold: float) -> dict[str, Any] | None:
    candidates = []
    algorithm = "shoulders_over_center_e010" if shoulders else "center_over_shoulders_e010"
    for start in range(0, len(items) - 2):
        left, center, right = items[start : start + 3]
        center_edge = score_edge(center)
        shoulder_edge = (score_edge(left) + score_edge(right)) / 2.0
        if shoulders:
            score = shoulder_edge - center_edge
            legs = [dict(left, side="BUY_YES"), dict(right, side="BUY_YES"), dict(center, side="BUY_NO")]
        else:
            score = center_edge - shoulder_edge
            legs = [dict(center, side="BUY_YES"), dict(left, side="BUY_NO"), dict(right, side="BUY_NO")]
        candidates.append(
            make_candidate(
                algorithm=algorithm,
                decision_items=items,
                legs=legs,
                score=score,
                threshold=threshold,
                components={"center": center["bracket"], "left": left["bracket"], "right": right["bracket"]},
            )
        )
    return top_or_none(candidates)


def emit_range_vs_neighbors(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for start in range(0, len(items) - 2):
        inside = items[start : start + 3]
        neighbors = []
        if start - 1 >= 0:
            neighbors.append(items[start - 1])
        if start + 3 < len(items):
            neighbors.append(items[start + 3])
        if not neighbors:
            continue
        score = sum(score_edge(row) for row in inside) - sum(score_edge(row) for row in neighbors)
        legs = [dict(row, side="BUY_YES") for row in inside] + [dict(row, side="BUY_NO") for row in neighbors]
        candidates.append(
            make_candidate(
                algorithm="range_vs_neighbors_e010",
                decision_items=items,
                legs=legs,
                score=score,
                threshold=threshold,
                components={"inside": [row["bracket"] for row in inside], "neighbors": [row["bracket"] for row in neighbors]},
            )
        )
    return top_or_none(candidates)


def emit_tail_fade(items: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    candidates = []
    for end in range(1, len(items)):
        below = items[:end]
        score = sum(float(row["market_yes_price"]) - float(row["model_p_yes"]) for row in below)
        candidates.append(
            make_candidate(
                algorithm="tail_fade_overpriced_e015",
                decision_items=items,
                legs=[dict(row, side="BUY_NO") for row in below],
                score=score,
                threshold=threshold,
                components={"tail": "below", "brackets": [row["bracket"] for row in below]},
            )
        )
    for start in range(1, len(items)):
        above = items[start:]
        score = sum(float(row["market_yes_price"]) - float(row["model_p_yes"]) for row in above)
        candidates.append(
            make_candidate(
                algorithm="tail_fade_overpriced_e015",
                decision_items=items,
                legs=[dict(row, side="BUY_NO") for row in above],
                score=score,
                threshold=threshold,
                components={"tail": "above", "brackets": [row["bracket"] for row in above]},
            )
        )
    return top_or_none(candidates)


def emit_mode_cluster(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(items) < 3:
        return None
    mode_idx = max(range(len(items)), key=lambda idx: float(items[idx]["model_p_yes"]))
    start = min(max(0, mode_idx - 1), len(items) - 3)
    cluster = items[start : start + 3]
    model_mass = sum(float(row["model_p_yes"]) for row in cluster)
    cost = sum(float(row["market_yes_price"]) for row in cluster)
    score = model_mass - cost
    threshold = 0.0
    row = make_candidate(
        algorithm="mode_cluster_cheap_060_075",
        decision_items=items,
        legs=[dict(leg, side="BUY_YES") for leg in cluster],
        score=score,
        threshold=threshold,
        components={"model_mass": model_mass, "cost": cost, "mode": items[mode_idx]["bracket"]},
    )
    row["selected"] = int(model_mass >= 0.60 and cost <= 0.75 and score > 0)
    return row


def generate_variant_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_items in decision_sets:
        items = [row for row in raw_items if int(row.get("eligible") or 0) == 1]
        if len(items) < 2:
            continue
        emitters = [
            lambda xs: emit_adjacent_mass(xs, 3, normalized=False, threshold=0.15, algorithm="adj3_raw_mass_long_e015"),
            lambda xs: emit_adjacent_mass(xs, 3, normalized=True, threshold=0.10, algorithm="adj3_norm_mass_long_e010"),
            lambda xs: emit_adjacent_mass(xs, 2, normalized=True, threshold=0.08, algorithm="adj2_norm_mass_long_e008"),
            lambda xs: emit_pair_spread(xs, threshold=0.10),
            lambda xs: emit_center_shoulders(xs, shoulders=False, threshold=0.10),
            lambda xs: emit_center_shoulders(xs, shoulders=True, threshold=0.10),
            lambda xs: emit_range_vs_neighbors(xs, threshold=0.10),
            lambda xs: emit_tail_fade(xs, threshold=0.15),
            emit_mode_cluster,
        ]
        for emit in emitters:
            row = emit(items)
            if row is not None and row["decision_dt"] is not None:
                rows.append(row)
    return rows


def attach_orderbook(rows: list[dict[str, Any]], orderbook_glob: str) -> dict[str, Any]:
    leg_candidates = []
    for row in rows:
        for idx, leg in enumerate(row["legs"]):
            side = leg["side"]
            leg_candidates.append(
                {
                    "candidate_id": f"{row['candidate_id']}|{idx}",
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "event_date": row["event_date"],
                    "city": row["city"],
                    "bracket": leg["bracket"],
                    "side": side,
                    "market_yes_price": leg["market_yes_price"],
                    "decision_entry_price": leg_cost(side, float(leg["market_yes_price"])),
                    "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                    "decision_dt": row["decision_dt"],
                    "final_yes": leg["final_yes"],
                    "counterfactual_pnl": None,
                    "live_filled": 0,
                    "outcome": "yes" if side == "BUY_YES" else "no",
                    "price_bucket": "",
                }
            )
    matched, coverage = scanner.match_time_aligned_orderbooks(leg_candidates, orderbook_glob)
    by_id = {row["candidate_id"]: row for row in matched}
    full = 0
    for row in rows:
        taker_cost = 0.0
        taker_pnl = 0.0
        maker_cost = 0.0
        maker_pnl = 0.0
        ok = True
        for idx, _leg in enumerate(row["legs"]):
            leg_match = by_id.get(f"{row['candidate_id']}|{idx}")
            if leg_match is None or leg_match.get("taker_cost_usd") is None or leg_match.get("taker_pnl_usd") is None:
                ok = False
                break
            taker_cost += float(leg_match["taker_cost_usd"])
            taker_pnl += float(leg_match["taker_pnl_usd"])
            if leg_match.get("maker_cost_proxy_usd") is not None and leg_match.get("maker_pnl_proxy_usd") is not None:
                maker_cost += float(leg_match["maker_cost_proxy_usd"])
                maker_pnl += float(leg_match["maker_pnl_proxy_usd"])
        if ok:
            full += 1
            row["orderbook_taker_cost"] = taker_cost
            row["orderbook_taker_pnl"] = taker_pnl
            row["orderbook_maker_proxy_cost"] = maker_cost
            row["orderbook_maker_proxy_pnl"] = maker_pnl
    coverage["strategy_rows"] = len(rows)
    coverage["fully_matched_strategy_rows"] = full
    coverage["fully_matched_strategy_rate"] = scanner.safe_div(full, len(rows))
    return coverage


def metric_rows(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    if source == "orderbook":
        return [
            {
                **row,
                "eval_cost": row["orderbook_taker_cost"],
                "eval_pnl": row["orderbook_taker_pnl"],
                "eval_maker_cost": row["orderbook_maker_proxy_cost"],
                "eval_maker_pnl": row["orderbook_maker_proxy_pnl"],
            }
            for row in rows
            if row.get("orderbook_taker_cost") is not None and row.get("orderbook_taker_pnl") is not None
        ]
    return [
        {
            **row,
            "eval_cost": row["taker_cost"],
            "eval_pnl": row["taker_pnl"],
            "eval_maker_cost": None,
            "eval_maker_pnl": None,
        }
        for row in rows
    ]


def summarize_eval(selected: list[dict[str, Any]], baseline: list[dict[str, Any]], source: str, seed: int, iters: int) -> dict[str, Any]:
    selected_summary = scanner.summarize(selected, source=source)
    baseline_summary = scanner.summarize(baseline, source=source)
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


def evaluate_algorithms(
    rows: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    algorithms = sorted({row["algorithm"] for row in rows})
    out = []
    for idx, algorithm in enumerate(algorithms):
        family = [row for row in rows if row["algorithm"] == algorithm]
        train_family = [row for row in family if row["event_date"] in train_dates]
        holdout_family = [row for row in family if row["event_date"] in holdout_dates]
        train_selected = [row for row in train_family if int(row.get("selected") or 0) == 1]
        holdout_selected = [row for row in holdout_family if int(row.get("selected") or 0) == 1]
        train_eval = summarize_eval(train_selected, train_family, source, seed + idx * 100, iters)
        holdout_eval = summarize_eval(holdout_selected, holdout_family, source, seed + idx * 100 + 50, iters)
        out.append(
            {
                "algorithm": algorithm,
                "source": source,
                "train_family_rows": len(train_family),
                "holdout_family_rows": len(holdout_family),
                "train": train_eval,
                "holdout": holdout_eval,
                "gates": scanner.gate_result(train_eval, holdout_eval),
            }
        )
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


def row_line(item: dict[str, Any]) -> str:
    train = item["train"]
    holdout = item["holdout"]
    gates = item["gates"]
    return (
        f"| `{item['algorithm']}` | {item['train_family_rows']} | {train['selected']['rows']} | "
        f"{train['selected']['active_event_dates']} | {item['holdout_family_rows']} | {holdout['selected']['rows']} | "
        f"{holdout['selected']['active_event_dates']} | {pct(train['selected']['taker_roi'])} | "
        f"{fmt_ci(train['roi_ci95_cluster_by_event_date'])} | {pct(train['excess_roi'])} | "
        f"{fmt_ci(train['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['taker_roi'])} | "
        f"{fmt_ci(holdout['roi_ci95_cluster_by_event_date'])} | {pct(holdout['excess_roi'])} | "
        f"{fmt_ci(holdout['excess_roi_ci95_cluster_by_event_date'])} | {pct(holdout['selected']['drop_top5_taker_roi'])} | "
        f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}` |"
    )


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Range RV Variant Lab v0.3",
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
        f"- Decision sets: `{report['input']['decision_sets']}`; generated strategy rows `{report['input']['variant_rows']}`.",
        f"- Algorithms tested: `{report['input']['algorithms']}`; each emits at most one candidate per city-day decision snapshot.",
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
            "- `adj3_raw_mass_long_e015`: buy YES on best adjacent 3-bracket raw model-minus-market mass if score >= 0.15.",
            "- `adj3_norm_mass_long_e010`: same, but score uses model/market probabilities normalized within the observed city-day bracket set.",
            "- `adj2_norm_mass_long_e008`: normalized adjacent 2-bracket range YES.",
            "- `pair_spread_adjacent_e010`: buy YES on the higher-edge adjacent bracket and BUY_NO on the lower-edge adjacent bracket.",
            "- `center_over_shoulders_e010`: buy YES center bracket and BUY_NO adjacent shoulders.",
            "- `shoulders_over_center_e010`: buy YES shoulders and BUY_NO center bracket.",
            "- `range_vs_neighbors_e010`: buy YES adjacent 3 range and BUY_NO immediate outside neighbors.",
            "- `tail_fade_overpriced_e015`: buy NO on the most overpriced below/above tail.",
            "- `mode_cluster_cheap_060_075`: buy YES on the 3-bracket model-mode cluster only if model mass >= 0.60 and cost <= 0.75.",
            "",
            "## Decision Proxy",
            "",
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
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
            "| algorithm | train family | train selected | train dates | holdout family | holdout selected | holdout dates | train ROI | train ROI CI | train excess | train excess CI | holdout ROI | holdout ROI CI | holdout excess | holdout excess CI | holdout top5 removed ROI | gates |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["orderbook_results"]:
        lines.append(row_line(item))
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Baseline is each algorithm's own unfiltered top candidate per city-day decision snapshot.",
            "- Passing point estimates are not enough; ROI CI, excess CI, and forward gates must all pass.",
            "- This lab is broader than v0.2 but still pre-registered; it does not choose cities, dates, or models after seeing results.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = scanner.connect(args.db_path)
    candidates = scanner.load_candidates(conn)
    decision_sets = scanner.group_decision_sets(candidates)
    variant_rows = generate_variant_rows(decision_sets)
    coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        coverage = attach_orderbook(variant_rows, args.orderbook_glob)

    all_dates = sorted({row["event_date"] for row in variant_rows})
    train_dates, holdout_dates, split_date = scanner.split_train_holdout(all_dates, args.train_frac)
    proxy_rows = metric_rows(variant_rows, "proxy")
    orderbook_rows = metric_rows(variant_rows, "orderbook")

    proxy_results = evaluate_algorithms(
        proxy_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = evaluate_algorithms(
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
        "target_metric": "range_rv_variant_alpha",
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
            "variant_rows": len(variant_rows),
            "algorithms": len({row["algorithm"] for row in variant_rows}),
            "event_dates": len(all_dates),
        },
        "split": {
            "split_date": split_date,
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
        },
        "decision_proxy_results": proxy_results,
        "orderbook_coverage": coverage,
        "orderbook_results": orderbook_results,
        "confirmed_algorithms": sorted(confirmed_algorithms),
        "best_decision_proxy_by_holdout_excess": sorted(proxy_results, key=rank_key, reverse=True)[0],
        "best_orderbook_by_holdout_excess": sorted(orderbook_results, key=rank_key, reverse=True)[0]
        if orderbook_results
        else None,
        "gates": gates,
        "verdict": gates["verdict"],
    }
    scanner.write_json(Path(args.out_json), report)
    write_md(Path(args.out_md), report)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"verdict={report['verdict']} algorithms={report['input']['algorithms']}")


if __name__ == "__main__":
    main()
