#!/usr/bin/env python3
"""Trading-distribution and utility diagnostics for repeat-weekend v1."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import research_repeat_weekend_v0 as v0
import research_repeat_weekend_v1 as v1


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IN = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v1/market_full_comparison.csv"
DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v1/utility"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def load_scored(path: Path) -> list[dict[str, Any]]:
    frame = pd.read_csv(path)
    rows = []
    for row in frame.to_dict("records"):
        for field in ("brackets", "model_probs", "market_raw", "market_probs"):
            row[field] = json.loads(row[field])
        row["winner"] = int(row["winner"])
        rows.append(row)
    return rows


def bracket_type(label: str) -> str:
    label = label.strip()
    if label.startswith("<"):
        return "lower_tail"
    if label.startswith(">") or label.endswith("+"):
        return "upper_tail"
    return "middle"


def price_bucket(price: float) -> str:
    if price <= 0.05:
        return "00-05c"
    if price <= 0.15:
        return "05-15c"
    if price <= 0.35:
        return "15-35c"
    return "35c+"


def enrich(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in ledger:
        rows.append(
            {
                **row,
                "hit": row["won"],
                "decimal_payout_odds": 1 / row["cost"],
                "profit_if_win": 1 - row["cost"],
                "bracket_type": bracket_type(row["bracket"]),
                "entry_price_bucket": price_bucket(row["historical_price_proxy"]),
            }
        )
    return rows


def max_drawdown_from_blocks(ledger: list[dict[str, Any]]) -> float:
    if not ledger:
        return 0.0
    frame = pd.DataFrame(ledger)
    block_pnl = frame.groupby("target_friday").pnl.sum().sort_index()
    wealth = np.r_[0.0, block_pnl.cumsum().to_numpy(float)]
    peaks = np.maximum.accumulate(wealth)
    return float(np.max(peaks - wealth))


def bootstrap_ledger(
    ledger: list[dict[str, Any]], block_field: str = "target_friday", draws: int = 20_000
) -> dict[str, Any]:
    if not ledger:
        return {}
    blocks = {}
    for row in ledger:
        blocks.setdefault(str(row[block_field]), []).append(row)
    values = list(blocks.values())
    rng = np.random.default_rng(20260808 + len(values))
    rois, pnls = [], []
    for _ in range(draws):
        sampled = [values[index] for index in rng.integers(0, len(values), len(values))]
        capital = sum(row["cost"] for block in sampled for row in block)
        pnl = sum(row["pnl"] for block in sampled for row in block)
        rois.append(pnl / capital)
        pnls.append(pnl)
    return {
        "blocks": len(values),
        "roi_ci_low": float(np.quantile(rois, 0.025)),
        "roi_median": float(np.quantile(rois, 0.5)),
        "roi_ci_high": float(np.quantile(rois, 0.975)),
        "probability_pnl_positive": float(np.mean(np.array(pnls) > 0)),
        "pnl_cvar_5pct": float(np.mean(np.sort(pnls)[: max(1, int(0.05 * len(pnls)))])),
    }


def policy_summary(
    ledger: list[dict[str, Any]], edge_cutoff: float, spread_stress: float
) -> dict[str, Any]:
    if not ledger:
        return {"edge_cutoff": edge_cutoff, "spread_stress": spread_stress, "trades": 0}
    frame = pd.DataFrame(ledger).sort_values(["target_friday", "movie", "week"])
    capital = float(frame.cost.sum())
    pnl = float(frame.pnl.sum())
    wins = int(frame.won.sum())
    positive = frame.loc[frame.pnl > 0, "pnl"].sum()
    negative = -frame.loc[frame.pnl < 0, "pnl"].sum()
    target_counts = frame.groupby("target_friday").size()
    dates = pd.to_datetime(sorted(frame.target_friday.unique()))
    gaps = np.diff(dates).astype("timedelta64[D]").astype(int) if len(dates) > 1 else np.array([])
    abs_contrib = frame.groupby("movie").pnl.apply(lambda values: float(np.abs(values).sum()))
    abs_share = abs_contrib / abs_contrib.sum()
    leave_one_out = []
    for movie in frame.movie.unique():
        remaining = frame[frame.movie != movie]
        leave_one_out.append(float(remaining.pnl.sum() / remaining.cost.sum()))
    return {
        "edge_cutoff": edge_cutoff,
        "spread_stress": spread_stress,
        "trades": len(frame),
        "wins": wins,
        "hit_rate": wins / len(frame),
        "breakeven_hit_rate": capital / len(frame),
        "hit_rate_minus_breakeven": wins / len(frame) - capital / len(frame),
        "first_target_friday": frame.target_friday.min(),
        "last_target_friday": frame.target_friday.max(),
        "calendar_span_days": int((dates.max() - dates.min()).days),
        "target_weekends": int(frame.target_friday.nunique()),
        "active_movies": int(frame.movie.nunique()),
        "trades_per_target_weekend": len(frame) / frame.target_friday.nunique(),
        "max_trades_one_target_weekend": int(target_counts.max()),
        "median_gap_days": float(np.median(gaps)) if len(gaps) else None,
        "historical_price_mean": float(frame.historical_price_proxy.mean()),
        "historical_price_median": float(frame.historical_price_proxy.median()),
        "historical_price_q10": float(frame.historical_price_proxy.quantile(0.10)),
        "historical_price_q90": float(frame.historical_price_proxy.quantile(0.90)),
        "all_in_cost_mean": float(frame.cost.mean()),
        "all_in_cost_median": float(frame.cost.median()),
        "decimal_odds_median": float(frame.decimal_payout_odds.median()),
        "decimal_odds_q10": float(frame.decimal_payout_odds.quantile(0.10)),
        "decimal_odds_q90": float(frame.decimal_payout_odds.quantile(0.90)),
        "capital": capital,
        "net_pnl": pnl,
        "roi": pnl / capital,
        "average_pnl_per_trade": float(frame.pnl.mean()),
        "median_pnl_per_trade": float(frame.pnl.median()),
        "pnl_q10": float(frame.pnl.quantile(0.10)),
        "pnl_q90": float(frame.pnl.quantile(0.90)),
        "pnl_std": float(frame.pnl.std(ddof=1)),
        "profit_factor": float(positive / negative) if negative else math.inf,
        "max_drawdown_one_share": max_drawdown_from_blocks(ledger),
        "max_drawdown_over_capital": max_drawdown_from_blocks(ledger) / capital,
        "absolute_contribution_hhi": float(np.sum(abs_share**2)),
        "largest_movie_absolute_contribution": float(abs_share.max()),
        "leave_one_movie_out_roi_min": min(leave_one_out),
        "leave_one_movie_out_roi_max": max(leave_one_out),
        "bootstrap_target_weekend": bootstrap_ledger(ledger, "target_friday"),
        "bootstrap_movie": bootstrap_ledger(ledger, "movie"),
    }


def contribution_rows(ledger: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    frame = pd.DataFrame(ledger)
    result = []
    total_abs = float(np.abs(frame.pnl).sum())
    for dimension in ("movie", "week", "bracket_type", "entry_price_bucket"):
        for value, group in frame.groupby(dimension):
            result.append(
                {
                    "policy": policy,
                    "dimension": dimension,
                    "value": value,
                    "trades": len(group),
                    "wins": int(group.won.sum()),
                    "hit_rate": float(group.won.mean()),
                    "capital": float(group.cost.sum()),
                    "pnl": float(group.pnl.sum()),
                    "roi": float(group.pnl.sum() / group.cost.sum()),
                    "absolute_pnl_contribution": float(np.abs(group.pnl).sum() / total_abs),
                }
            )
    return result


def filter_experiments(ledger: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    frame = pd.DataFrame(ledger)
    filters = {
        "all": pd.Series(True, index=frame.index),
        "price_at_least_5c": frame.historical_price_proxy >= 0.05,
        "price_at_least_10c": frame.historical_price_proxy >= 0.10,
        "not_lower_tail": frame.bracket_type != "lower_tail",
        "upper_tail_only": frame.bracket_type == "upper_tail",
        "exclude_week4": frame.week != 4,
    }
    rows = []
    for label, mask in filters.items():
        subset = frame[mask]
        fixed_notional_pnl = float((subset.won / subset.cost - 1).sum())
        rows.append(
            {
                "policy": policy,
                "filter": label,
                "posthoc_exploratory": label != "all",
                "trades": len(subset),
                "wins": int(subset.won.sum()),
                "hit_rate": float(subset.won.mean()),
                "average_cost": float(subset.cost.mean()),
                "one_share_roi": float(subset.pnl.sum() / subset.cost.sum()),
                "fixed_$1_notional_roi": fixed_notional_pnl / len(subset),
            }
        )
    return rows


def simulate_sizing_blocks(
    blocks: list[list[dict[str, Any]]], config: dict[str, Any]
) -> dict[str, Any]:
    bankroll = 100.0
    peak = bankroll
    max_drawdown = 0.0
    total_staked = 0.0
    for block in blocks:
        proposed = []
        for row in block:
            if config["kind"] == "one_share":
                stake = row["cost"]
            elif config["kind"] == "fixed_notional":
                stake = config["notional"]
            elif config["kind"] == "fixed_fraction":
                stake = bankroll * config["fraction"]
            else:
                used_probability = row["cost"] + config["probability_shrink"] * (
                    row["model_probability"] - row["cost"]
                )
                full_kelly = max(
                    0.0, (used_probability - row["cost"]) / (1 - row["cost"])
                )
                fraction = min(config["kelly_fraction"] * full_kelly, config["per_trade_cap"])
                stake = bankroll * fraction
            proposed.append((row, stake))
        total = sum(stake for _, stake in proposed)
        day_cap = bankroll * config.get("day_cap", 1.0)
        scale = min(1.0, day_cap / total) if total > 0 else 1.0
        block_pnl = 0.0
        for row, stake in proposed:
            stake *= scale
            trade_return = (1 / row["cost"] - 1) if row["won"] else -1.0
            block_pnl += stake * trade_return
            total_staked += stake
        bankroll += block_pnl
        peak = max(peak, bankroll)
        max_drawdown = max(max_drawdown, (peak - bankroll) / peak)
    return {
        "sizing_policy": config["name"],
        "initial_bankroll": 100.0,
        "terminal_bankroll": bankroll,
        "return": bankroll / 100 - 1,
        "max_drawdown": max_drawdown,
        "total_staked": total_staked,
        "turnover_on_initial_bankroll": total_staked / 100,
        "log_utility_change": math.log(bankroll / 100) if bankroll > 0 else -math.inf,
    }


def ledger_blocks(ledger: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    frame = pd.DataFrame(ledger).sort_values(["target_friday", "movie", "week"])
    return [group.to_dict("records") for _, group in frame.groupby("target_friday", sort=True)]


def simulate_sizing(ledger: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    return simulate_sizing_blocks(ledger_blocks(ledger), config)


SIZING_CONFIGS = [
    {"name": "one_share", "kind": "one_share"},
    {"name": "fixed_$1_notional", "kind": "fixed_notional", "notional": 1.0, "day_cap": 0.05},
    {"name": "fixed_1pct_bankroll", "kind": "fixed_fraction", "fraction": 0.01, "day_cap": 0.03},
    {
        "name": "kelly_10pct_shrink50_cap1pct",
        "kind": "kelly",
        "kelly_fraction": 0.10,
        "probability_shrink": 0.50,
        "per_trade_cap": 0.01,
        "day_cap": 0.03,
    },
    {
        "name": "kelly_25pct_shrink50_cap2pct",
        "kind": "kelly",
        "kelly_fraction": 0.25,
        "probability_shrink": 0.50,
        "per_trade_cap": 0.02,
        "day_cap": 0.05,
    },
    {
        "name": "kelly_25pct_full_model_cap2pct",
        "kind": "kelly",
        "kelly_fraction": 0.25,
        "probability_shrink": 1.0,
        "per_trade_cap": 0.02,
        "day_cap": 0.05,
    },
]


def bootstrap_sizing(
    ledger: list[dict[str, Any]], config: dict[str, Any], draws: int = 3_000
) -> dict[str, Any]:
    blocks = ledger_blocks(ledger)
    rng = np.random.default_rng(20260808 + len(blocks) + len(config["name"]))
    returns, drawdowns = [], []
    for _ in range(draws):
        sampled = [blocks[index] for index in rng.integers(0, len(blocks), len(blocks))]
        result = simulate_sizing_blocks(sampled, config)
        returns.append(result["return"])
        drawdowns.append(result["max_drawdown"])
    return {
        "return_ci_low": float(np.quantile(returns, 0.025)),
        "return_median": float(np.quantile(returns, 0.5)),
        "return_ci_high": float(np.quantile(returns, 0.975)),
        "probability_positive_return": float(np.mean(np.array(returns) > 0)),
        "max_drawdown_median": float(np.quantile(drawdowns, 0.5)),
        "max_drawdown_q95": float(np.quantile(drawdowns, 0.95)),
    }


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    scored = load_scored(args.input.resolve())

    summaries = []
    ledgers = {}
    for edge in (0.05, 0.08, 0.10, 0.15):
        for spread in (0.02, 0.05, 0.10, 0.15):
            label = f"edge_{int(edge*100):02d}pp_spread_{int(spread*100):02d}c"
            ledger = enrich(v1.trade_ledger(scored, edge, spread))
            ledgers[label] = ledger
            summaries.append(policy_summary(ledger, edge, spread))

    primary_label = "edge_10pp_spread_02c"
    strict_label = "edge_15pp_spread_10c"
    contribution = contribution_rows(ledgers[primary_label], primary_label)
    contribution += contribution_rows(ledgers[strict_label], strict_label)
    filters = filter_experiments(ledgers[primary_label], primary_label)
    filters += filter_experiments(ledgers[strict_label], strict_label)

    sizing_rows = []
    for label in (primary_label, strict_label):
        for config in SIZING_CONFIGS:
            result = simulate_sizing(ledgers[label], config)
            result.update({"trade_policy": label, "bootstrap": bootstrap_sizing(ledgers[label], config)})
            sizing_rows.append(result)

    v0.write_csv(out / "policy_summary.csv", summaries)
    v0.write_csv(out / "primary_ledger.csv", ledgers[primary_label])
    v0.write_csv(out / "strict_ledger.csv", ledgers[strict_label])
    v0.write_csv(out / "contribution.csv", contribution)
    v0.write_csv(out / "filter_experiments_posthoc.csv", filters)
    v0.write_csv(out / "sizing_utility.csv", sizing_rows)
    result = {
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "primary_policy": primary_label,
        "strict_policy": strict_label,
        "primary_summary": next(row for row in summaries if row["edge_cutoff"] == 0.10 and row["spread_stress"] == 0.02),
        "strict_summary": next(row for row in summaries if row["edge_cutoff"] == 0.15 and row["spread_stress"] == 0.10),
        "sizing_utility": sizing_rows,
    }
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
