#!/usr/bin/env python3
"""City-day portfolio branch analysis for weather paper/live fills.

V1 focuses on pure-NO baskets. It compares the current baseline against simple
counterfactual branches that select fewer NO legs inside each city-day.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_LEDGER = Path("runtime/weather_edge_v1/market_data/research/t24_paper_ledger_trades.csv")
DEFAULT_DB = Path("runtime/weather.db")
DEFAULT_OUT = Path("runtime/weather_edge_v1/market_data/research/city_day_portfolio")

PURE_NO_BRANCHES = (
    "baseline_pure_no",
    "pure_no_top1_ev",
    "pure_no_top2_ev",
    "pure_no_portfolio_gate",
    "pure_no_hit_loss_guard",
    "pure_no_hit_probability_filter",
)

HIT_LOSS_GUARD_MAX_HIT_LOSS = -2.50
HIT_LOSS_GUARD_MIN_EXPECTED_PNL = 0.25
HIT_LOSS_GUARD_MAX_LEGS = 4
HIT_PROB_FILTER_MAX_SUM_P_HIT = 0.20
HIT_PROB_FILTER_MIN_EDGE_NO = 0.15


@dataclass(frozen=True)
class Trade:
    source: str
    trade_id: str
    event_date: str
    city: str
    city_pool: str
    bracket: str
    side: str
    model: str
    snapshot_ts_utc: str
    entry_price: float
    shares: float
    cost_usd: float
    model_p_yes: float
    settlement_status: str
    final_price: float
    pnl_usd: float

    @property
    def group_key(self) -> tuple[str, str, str]:
        return (self.event_date, self.city, self.city_pool)


def as_float(value: object, default: float = math.nan) -> float:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def standalone_ev(trade: Trade) -> float:
    win_p = trade.model_p_yes if trade.side == "BUY_YES" else 1.0 - trade.model_p_yes
    win_p = min(max(win_p, 0.0), 1.0)
    win_pnl = trade.shares * (1.0 - trade.entry_price)
    lose_pnl = -trade.cost_usd
    return win_p * win_pnl + (1.0 - win_p) * lose_pnl


def load_paper_trades(path: Path) -> list[Trade]:
    trades: list[Trade] = []
    with path.open(newline="") as fh:
        for idx, row in enumerate(csv.DictReader(fh), start=2):
            if row.get("settlement_status") != "settled":
                continue
            side = row.get("side", "")
            if side not in {"BUY_YES", "BUY_NO"}:
                continue
            entry_price = as_float(row.get("entry_price"))
            shares = as_float(row.get("shares"))
            cost_usd = as_float(row.get("cost_usd"), entry_price * shares)
            model_p_yes = as_float(row.get("model_prob"))
            pnl_usd = as_float(row.get("pnl_usd"))
            final_yes = as_float(row.get("final_yes"))
            if any(math.isnan(x) for x in (entry_price, shares, cost_usd, model_p_yes, pnl_usd, final_yes)):
                raise ValueError(f"bad numeric field in {path}:{idx}")
            trades.append(
                Trade(
                    source="paper_ledger",
                    trade_id=row.get("order_id") or f"{path.name}:{idx}",
                    event_date=row.get("event_date", ""),
                    city=row.get("city", ""),
                    city_pool=row.get("city_pool") or "unknown",
                    bracket=row.get("bracket", ""),
                    side=side,
                    model=row.get("model", ""),
                    snapshot_ts_utc=row.get("snapshot_ts_utc", ""),
                    entry_price=entry_price,
                    shares=shares,
                    cost_usd=cost_usd,
                    model_p_yes=model_p_yes,
                    settlement_status="settled",
                    final_price=final_yes,
                    pnl_usd=pnl_usd,
                )
            )
    return trades


def load_live_fills(path: Path) -> list[Trade]:
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            fill_id,
            execution_id,
            side,
            fill_price,
            fill_qty,
            fees_usd,
            cost_usd,
            target_date,
            city,
            city_pool,
            bracket,
            model_version,
            model_p_yes,
            snapshot_ts_utc,
            final_yes,
            settlement_status,
            pnl_usd_at_fill
        FROM fact_trades
        WHERE trade_class = 'live_real'
          AND settlement_status = 'settled'
        """
    ).fetchall()
    trades: list[Trade] = []
    for row in rows:
        trades.append(
            Trade(
                source="live_clob_fills",
                trade_id=row["fill_id"] or row["execution_id"],
                event_date=row["target_date"],
                city=row["city"],
                city_pool=row["city_pool"] or "unknown",
                bracket=row["bracket"],
                side=row["side"],
                model=row["model_version"] or "",
                snapshot_ts_utc=row["snapshot_ts_utc"] or "",
                entry_price=float(row["fill_price"]),
                shares=float(row["fill_qty"]),
                cost_usd=float(row["cost_usd"] or 0.0),
                model_p_yes=float(row["model_p_yes"] or 0.0),
                settlement_status="settled",
                final_price=float(row["final_yes"]),
                pnl_usd=float(row["pnl_usd_at_fill"]),
            )
        )
    return trades


def classify_group(trades: list[Trade]) -> str:
    sides = {t.side for t in trades}
    by_bracket: dict[str, set[str]] = defaultdict(set)
    for trade in trades:
        by_bracket[trade.bracket].add(trade.side)
    if any({"BUY_YES", "BUY_NO"}.issubset(sides) for sides in by_bracket.values()):
        return "same_bracket_conflict"
    if sides == {"BUY_NO"}:
        return "pure_no_basket"
    if sides == {"BUY_YES"}:
        return "yes_only"
    if sides == {"BUY_YES", "BUY_NO"}:
        return "yes_centered_basket"
    return "unknown"


def group_trades(trades: Iterable[Trade]) -> dict[tuple[str, str, str], list[Trade]]:
    groups: dict[tuple[str, str, str], list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[trade.group_key].append(trade)
    return dict(groups)


def payoff_states(selected: list[Trade]) -> list[tuple[float, float]]:
    """Return (state_probability, payoff) over selected pure-NO brackets plus other."""
    by_bracket: dict[str, list[Trade]] = defaultdict(list)
    for trade in selected:
        by_bracket[trade.bracket].append(trade)

    states: list[tuple[float, float]] = []
    used_p = 0.0
    for bracket, legs in by_bracket.items():
        p_yes_values = [min(max(t.model_p_yes, 0.0), 1.0) for t in legs]
        state_p = max(p_yes_values)
        used_p += state_p
        payoff = 0.0
        for trade in selected:
            if trade.bracket == bracket:
                payoff -= trade.cost_usd
            else:
                payoff += trade.shares * (1.0 - trade.entry_price)
        states.append((state_p, payoff))

    other_p = max(0.0, 1.0 - min(used_p, 1.0))
    other_payoff = sum(t.shares * (1.0 - t.entry_price) for t in selected)
    states.append((other_p, other_payoff))
    return states


def risk_metrics(selected: list[Trade]) -> dict[str, float]:
    if not selected:
        return {
            "expected_pnl": 0.0,
            "worst_case_pnl": 0.0,
            "loss_probability": 0.0,
            "large_loss_probability": 0.0,
        }
    states = payoff_states(selected)
    return {
        "expected_pnl": sum(prob * pnl for prob, pnl in states),
        "worst_case_pnl": min(pnl for _, pnl in states),
        "loss_probability": sum(prob for prob, pnl in states if pnl < 0.0),
        "large_loss_probability": sum(prob for prob, pnl in states if pnl <= -5.0),
    }


def hit_state_pnls(selected: list[Trade]) -> dict[str, float]:
    """PnL for each state where a bought-NO bracket is the final winning bracket."""
    states = {}
    for bracket in {trade.bracket for trade in selected if trade.side == "BUY_NO"}:
        pnl = 0.0
        for trade in selected:
            if trade.side == "BUY_NO" and trade.bracket == bracket:
                pnl -= trade.cost_usd
            else:
                pnl += trade.shares * (1.0 - trade.entry_price)
        states[bracket] = pnl
    return states


def worst_bought_no_hit_pnl(selected: list[Trade]) -> float:
    states = hit_state_pnls(selected)
    if not states:
        return 0.0
    return min(states.values())


def sum_bought_no_hit_probability(selected: list[Trade]) -> float:
    return sum(t.model_p_yes for t in selected if t.side == "BUY_NO")


def min_no_edge(selected: list[Trade]) -> float:
    no_edges = [(1.0 - t.model_p_yes) - t.entry_price for t in selected if t.side == "BUY_NO"]
    if not no_edges:
        return 0.0
    return min(no_edges)


def choose_branch(group: list[Trade], branch: str) -> list[Trade]:
    ranked = sorted(group, key=lambda t: (standalone_ev(t), t.snapshot_ts_utc), reverse=True)
    if branch == "baseline_pure_no":
        return list(group)
    if branch == "pure_no_top1_ev":
        return ranked[:1]
    if branch == "pure_no_top2_ev":
        return ranked[:2]
    if branch == "pure_no_hit_probability_filter":
        if len(group) < 2:
            return list(group)
        if sum_bought_no_hit_probability(group) <= HIT_PROB_FILTER_MAX_SUM_P_HIT and min_no_edge(group) >= HIT_PROB_FILTER_MIN_EDGE_NO:
            return list(group)
        return []
    if branch not in {"pure_no_portfolio_gate", "pure_no_hit_loss_guard"}:
        raise ValueError(f"unknown branch: {branch}")

    best: tuple[float, float, int, float, list[Trade]] | None = None
    max_size = 2 if branch == "pure_no_portfolio_gate" else HIT_LOSS_GUARD_MAX_LEGS
    for size in range(1, min(max_size, len(ranked[:8])) + 1):
        for combo in itertools.combinations(ranked[:8], size):
            selected = list(combo)
            risk = risk_metrics(selected)
            if branch == "pure_no_portfolio_gate":
                if risk["expected_pnl"] <= 0.50:
                    continue
                if risk["worst_case_pnl"] < -7.50:
                    continue
                if risk["loss_probability"] > 0.35:
                    continue
                risk_floor = risk["worst_case_pnl"]
            else:
                risk_floor = worst_bought_no_hit_pnl(selected)
                if risk["expected_pnl"] <= HIT_LOSS_GUARD_MIN_EXPECTED_PNL:
                    continue
                if risk_floor < HIT_LOSS_GUARD_MAX_HIT_LOSS:
                    continue
            cost = sum(t.cost_usd for t in selected)
            score = (
                risk["expected_pnl"],
                risk_floor,
                len(selected) if branch == "pure_no_hit_loss_guard" else -len(selected),
                -cost,
                selected,
            )
            if best is None or score[:4] > best[:4]:
                best = score
    return best[4] if best else []


def summarize_selected(groups: dict[tuple[str, str, str], list[Trade]]) -> dict[str, float]:
    group_pnls = [sum(t.pnl_usd for t in selected) for selected in groups.values()]
    all_trades = [trade for selected in groups.values() for trade in selected]
    cost = sum(t.cost_usd for t in all_trades)
    pnl = sum(t.pnl_usd for t in all_trades)
    groups_with_bought_no_hit = 0
    groups_with_bought_no_hit_and_net_loss = 0
    bought_no_hit_net_loss_pnl = 0.0
    for selected in groups.values():
        if not selected:
            continue
        group_pnl = sum(t.pnl_usd for t in selected)
        bought_no_hit = any(t.side == "BUY_NO" and t.final_price == 1.0 for t in selected)
        if bought_no_hit:
            groups_with_bought_no_hit += 1
            if group_pnl < 0.0:
                groups_with_bought_no_hit_and_net_loss += 1
                bought_no_hit_net_loss_pnl += group_pnl
    return {
        "groups": len(groups),
        "trades_selected": len(all_trades),
        "gross_notional_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": round(pnl / cost, 6) if cost else 0.0,
        "worst_city_day_loss_usd": round(min(group_pnls), 6) if group_pnls else 0.0,
        "avg_city_day_pnl_usd": round(sum(group_pnls) / len(group_pnls), 6) if group_pnls else 0.0,
        "p_city_day_loss": round(sum(1 for pnl in group_pnls if pnl < 0.0) / len(group_pnls), 6) if group_pnls else 0.0,
        "p_city_day_loss_lte_5": round(sum(1 for pnl in group_pnls if pnl <= -5.0) / len(group_pnls), 6) if group_pnls else 0.0,
        "groups_with_bought_no_hit": groups_with_bought_no_hit,
        "groups_with_bought_no_hit_and_net_loss": groups_with_bought_no_hit_and_net_loss,
        "bought_no_hit_net_loss_pnl_usd": round(bought_no_hit_net_loss_pnl, 6),
    }


def group_actual_hit_brackets(selected: list[Trade]) -> list[str]:
    return sorted({t.bracket for t in selected if t.side == "BUY_NO" and t.final_price == 1.0})


def format_trade_legs(selected: list[Trade]) -> str:
    parts = []
    for trade in sorted(selected, key=lambda t: (t.bracket, t.side, t.snapshot_ts_utc)):
        no_edge = (1.0 - trade.model_p_yes) - trade.entry_price if trade.side == "BUY_NO" else trade.model_p_yes - trade.entry_price
        parts.append(
            f"{trade.side} {trade.bracket} p={trade.entry_price:.4f} "
            f"model_p_yes={trade.model_p_yes:.4f} edge={no_edge:.4f} pnl={trade.pnl_usd:.4f}"
        )
    return " | ".join(parts)


def best_drop_one_repair(selected: list[Trade]) -> tuple[list[Trade], float, str]:
    """Return the best ex-post drop-one repair by realized PnL for diagnostics only."""
    if len(selected) <= 1:
        return list(selected), sum(t.pnl_usd for t in selected), ""
    best: tuple[float, list[Trade], str] | None = None
    for trade in selected:
        repaired = [t for t in selected if t.trade_id != trade.trade_id]
        repaired_pnl = sum(t.pnl_usd for t in repaired)
        score = (repaired_pnl, repaired, f"{trade.side} {trade.bracket}")
        if best is None or score[0] > best[0]:
            best = score
    assert best is not None
    return best[1], best[0], best[2]


def pure_no_review_bucket(selected: list[Trade]) -> tuple[str, str]:
    """Human-review bucket for multi-leg pure-NO baskets.

    These buckets are intentionally simple diagnostics, not live parameters.
    """
    sum_p_hit = sum_bought_no_hit_probability(selected)
    edge_floor = min_no_edge(selected)
    if sum_p_hit > 0.25 and edge_floor < 0.15:
        return "hard_reject_candidate", "sum_p_hit > 0.25 and min_no_edge < 0.15"
    if sum_p_hit <= 0.20 and edge_floor >= 0.15:
        return "clear_keep_candidate", "sum_p_hit <= 0.20 and min_no_edge >= 0.15"
    return "gray_review", "mixed signal; inspect hit-state and drop-one repair"


def pure_no_diagnostic_rows(source: str, groups: dict[tuple[str, str, str], list[Trade]]) -> list[dict]:
    rows = []
    for key, selected in sorted(groups.items()):
        if len(selected) < 2 or {t.side for t in selected} != {"BUY_NO"}:
            continue
        event_date, city, city_pool = key
        actual_pnl = sum(t.pnl_usd for t in selected)
        hit_brackets = group_actual_hit_brackets(selected)
        hit_state_map = hit_state_pnls(selected)
        worst_hit_bracket = ""
        worst_hit_pnl = 0.0
        if hit_state_map:
            worst_hit_bracket, worst_hit_pnl = min(hit_state_map.items(), key=lambda kv: kv[1])
        repaired, repaired_pnl, dropped_leg = best_drop_one_repair(selected)
        review_bucket, review_reason = pure_no_review_bucket(selected)
        rows.append(
            {
                "source": source,
                "event_date": event_date,
                "city": city,
                "city_pool": city_pool,
                "legs": len(selected),
                "cost_usd": round(sum(t.cost_usd for t in selected), 6),
                "actual_pnl_usd": round(actual_pnl, 6),
                "actual_hit_bought_no_brackets": ";".join(hit_brackets),
                "is_bought_no_hit_and_net_loss": bool(hit_brackets and actual_pnl < 0.0),
                "sum_bought_no_model_p_yes": round(sum_bought_no_hit_probability(selected), 6),
                "min_no_edge": round(min_no_edge(selected), 6),
                "worst_bought_no_hit_bracket": worst_hit_bracket,
                "worst_bought_no_hit_pnl_usd": round(worst_hit_pnl, 6),
                "best_drop_one_leg": dropped_leg,
                "best_drop_one_actual_pnl_usd": round(repaired_pnl, 6),
                "best_drop_one_delta_pnl_usd": round(repaired_pnl - actual_pnl, 6),
                "review_bucket": review_bucket,
                "review_reason": review_reason,
                "legs_detail": format_trade_legs(selected),
                "repaired_legs_detail": format_trade_legs(repaired),
            }
        )
    return rows


def analyze_source(source: str, trades: list[Trade]) -> tuple[dict, list[dict], list[dict], list[dict]]:
    groups = group_trades(trades)
    classes = {key: classify_group(value) for key, value in groups.items()}
    pure_no_keys = [key for key, tag in classes.items() if tag == "pure_no_basket"]

    branch_groups: dict[str, dict[tuple[str, str, str], list[Trade]]] = {}
    for branch in PURE_NO_BRANCHES:
        branch_groups[branch] = {key: choose_branch(groups[key], branch) for key in pure_no_keys}

    baseline_all = summarize_selected(groups)
    pure_baseline = summarize_selected(branch_groups["baseline_pure_no"])
    branches = {}
    for branch in PURE_NO_BRANCHES:
        metrics = summarize_selected(branch_groups[branch])
        metrics["delta_pnl_vs_baseline_pure_no_usd"] = round(metrics["pnl_usd"] - pure_baseline["pnl_usd"], 6)
        metrics["avoided_notional_vs_baseline_pure_no_usd"] = round(
            pure_baseline["gross_notional_usd"] - metrics["gross_notional_usd"], 6
        )

        combined_groups = {
            key: (branch_groups[branch][key] if key in branch_groups[branch] else selected)
            for key, selected in groups.items()
        }
        total = summarize_selected(combined_groups)
        metrics["total_strategy_pnl_usd"] = total["pnl_usd"]
        metrics["total_strategy_delta_pnl_usd"] = round(total["pnl_usd"] - baseline_all["pnl_usd"], 6)
        metrics["total_strategy_roi"] = total["roi"]
        metrics["total_strategy_worst_city_day_loss_usd"] = total["worst_city_day_loss_usd"]
        branches[branch] = metrics

    by_classification = {}
    for tag in sorted(set(classes.values())):
        tag_groups = {key: groups[key] for key, group_tag in classes.items() if group_tag == tag}
        by_classification[tag] = summarize_selected(tag_groups)

    group_rows: list[dict] = []
    trade_rows: list[dict] = []
    for key in sorted(groups):
        event_date, city, city_pool = key
        base_pnl = sum(t.pnl_usd for t in groups[key])
        row = {
            "source": source,
            "event_date": event_date,
            "city": city,
            "city_pool": city_pool,
            "classification": classes[key],
            "baseline_trades": len(groups[key]),
            "baseline_cost_usd": round(sum(t.cost_usd for t in groups[key]), 6),
            "baseline_pnl_usd": round(base_pnl, 6),
        }
        for branch in PURE_NO_BRANCHES:
            selected = branch_groups.get(branch, {}).get(key, groups[key])
            row[f"{branch}_trades"] = len(selected)
            branch_pnl = sum(t.pnl_usd for t in selected)
            row[f"{branch}_pnl_usd"] = round(branch_pnl, 6)
            row[f"{branch}_delta_pnl_usd"] = round(branch_pnl - base_pnl, 6)
        group_rows.append(row)

    for branch, selected_groups in branch_groups.items():
        for key, selected in selected_groups.items():
            for trade in selected:
                trade_row = asdict(trade)
                trade_row["source"] = source
                trade_row["branch"] = branch
                trade_row["standalone_ev"] = round(standalone_ev(trade), 6)
                trade_rows.append(trade_row)

    dates = sorted({t.event_date for t in trades})
    summary = {
        "source": source,
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "settled_trades": len(trades),
        "baseline_all": baseline_all,
        "by_classification": by_classification,
        "pure_no_branch_comparison": branches,
    }
    return summary, group_rows, trade_rows, pure_no_diagnostic_rows(source, groups)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    analyses = []
    all_group_rows: list[dict] = []
    all_trade_rows: list[dict] = []
    all_pure_no_diagnostic_rows: list[dict] = []

    paper = load_paper_trades(args.ledger)
    paper_views = {
        "paper_ledger_all_settled": paper,
        "paper_ledger_t1_trading": [t for t in paper if t.city_pool == "t1_trading"],
        "paper_ledger_t1_trading_25_75": [
            t for t in paper if t.city_pool == "t1_trading" and 0.25 <= t.entry_price < 0.75
        ],
        "paper_ledger_t2_research": [t for t in paper if t.city_pool == "t2_research"],
    }
    for source, source_trades in paper_views.items():
        source_summary, source_groups, source_trade_rows, source_diagnostic_rows = analyze_source(source, source_trades)
        analyses.append(source_summary)
        all_group_rows.extend(source_groups)
        all_trade_rows.extend(source_trade_rows)
        all_pure_no_diagnostic_rows.extend(source_diagnostic_rows)

    live = load_live_fills(args.db)
    live_summary, live_groups, live_trades, live_diagnostic_rows = analyze_source("live_clob_fills", live)
    analyses.append(live_summary)
    all_group_rows.extend(live_groups)
    all_trade_rows.extend(live_trades)
    all_pure_no_diagnostic_rows.extend(live_diagnostic_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "inputs": {
            "paper_ledger": str(args.ledger),
            "weather_db": str(args.db),
        },
        "parameters": {
            "pure_no_portfolio_gate": {
                "max_no_legs": 2,
                "max_city_day_loss_usd": -7.50,
                "max_loss_probability": 0.35,
                "min_expected_pnl_usd": 0.50,
                "max_candidates_per_city_day": 8,
            },
            "pure_no_hit_loss_guard": {
                "max_bought_no_hit_loss_usd": HIT_LOSS_GUARD_MAX_HIT_LOSS,
                "min_expected_pnl_usd": HIT_LOSS_GUARD_MIN_EXPECTED_PNL,
                "max_no_legs": HIT_LOSS_GUARD_MAX_LEGS,
                "max_candidates_per_city_day": 8,
            },
            "pure_no_hit_probability_filter": {
                "applies_to": "multi-leg pure NO baskets",
                "max_sum_bought_no_model_p_yes": HIT_PROB_FILTER_MAX_SUM_P_HIT,
                "min_no_edge": HIT_PROB_FILTER_MIN_EDGE_NO,
            }
        },
        "analyses": analyses,
    }
    (args.out / "city_day_portfolio_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_csv(args.out / "city_day_portfolio_groups.csv", all_group_rows)
    write_csv(args.out / "city_day_portfolio_trades.csv", all_trade_rows)
    write_csv(args.out / "city_day_portfolio_pure_no_diagnostics.csv", all_pure_no_diagnostic_rows)
    gate_delta = "pure_no_portfolio_gate_delta_pnl_usd"
    pure_no_rows = [row for row in all_group_rows if row.get("classification") == "pure_no_basket"]
    write_csv(
        args.out / "city_day_portfolio_top_winners.csv",
        sorted(pure_no_rows, key=lambda row: float(row[gate_delta]), reverse=True)[:50],
    )
    write_csv(
        args.out / "city_day_portfolio_top_losers.csv",
        sorted(pure_no_rows, key=lambda row: float(row[gate_delta]))[:50],
    )
    write_csv(
        args.out / "city_day_portfolio_conflicts.csv",
        [row for row in all_group_rows if row.get("classification") == "same_bracket_conflict"],
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
