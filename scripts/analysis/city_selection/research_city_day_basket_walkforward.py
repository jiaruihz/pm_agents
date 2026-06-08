"""
research_city_day_basket_walkforward.py

Walk-forward validation for basket rule selection.

Purpose:
  - Avoid choosing a basket algorithm from the same sample used to evaluate it.
  - Compare a small set of pre-defined rules, not a parameter grid.
  - Use train windows to pick a rule by robust score, then evaluate on the
    next unseen test window.

Offline only. Source is fact_signal_candidates representative T-22~24h
snapshot, so this is still a research diagnostic, not production replay.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    _attribution,
    _decide_basket,
    _decide_blended_single,
    _decide_raw_single,
    _summarize,
)
from scripts.analysis.research_city_day_basket_optimizer import (  # noqa: E402
    _decide_combo_optimizer,
    _load_rows,
)
from weather_dashboard.basket import BasketConfig  # noqa: E402
from weather_dashboard.blend import load_default_config  # noqa: E402


RULES = [
    "blended_single",
    "heuristic_pr2b",
    "combo_risk",
    "combo_market_risk",
    "combo_market_tail",
]


def _filter_dates(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[(df["event_date"] >= start) & (df["event_date"] <= end)].reset_index(drop=True)


def _build_rule_legs(df: pd.DataFrame) -> dict[str, list]:
    blend_cfg = load_default_config()
    basket_cfg = BasketConfig(
        single_leg_notional_small=3.0,
        single_leg_notional_normal=8.0,
        city_day_notional_cap=15.0,
        max_no_legs_per_city_day=4,
        edge_small_threshold=0.03,
        edge_normal_threshold=0.06,
        prefer_no_over_yes=False,
    )
    return {
        "raw_single": _decide_raw_single(df, edge_threshold=0.03, notional=5.0),
        "blended_single": _decide_blended_single(df, blend_cfg, edge_threshold=0.03, notional=5.0),
        "heuristic_pr2b": _decide_basket(df, blend_cfg, basket_cfg),
        "combo_risk": _decide_combo_optimizer(df, mode="risk"),
        "combo_market_risk": _decide_combo_optimizer(df, mode="market_risk"),
        "combo_market_tail": _decide_combo_optimizer(df, mode="market_tail"),
    }


def _side_roi(summary: dict, side: str) -> float:
    st = summary.get("by_side", {}).get(side)
    if not st:
        return 0.0
    cost = st.get("total_cost_usd") or 0.0
    return (st.get("total_pnl_usd") or 0.0) / cost if cost else 0.0


def _robust_score(summary: dict, attr_vs_blended: dict | None) -> float:
    """Train-window selection score.

    This deliberately rewards tail-removed ROI more than headline ROI. It also
    penalizes missed-profit > avoided-loss and BUY_NO losses. It is a fixed
    rule, not fitted on the data.
    """
    roi = summary["roi"]
    roi_top5 = summary["roi_excl_top5"]
    cost = summary["total_cost_usd"] or 1.0
    pnl = summary["total_pnl_usd"]
    score = 2.0 * roi_top5 + 0.5 * roi + min(pnl / cost, 0.25)

    if attr_vs_blended:
        missed = attr_vs_blended["missed_profit_usd"]
        avoided = attr_vs_blended["avoided_loss_usd"]
        if missed > avoided:
            score -= (missed - avoided) / cost

    buy_no_roi = _side_roi(summary, "BUY_NO")
    if buy_no_roi < 0:
        score += buy_no_roi

    if summary["n_legs"] < 20:
        score -= 0.25
    return score


def _evaluate_window(df: pd.DataFrame) -> dict:
    legs_by_rule = _build_rule_legs(df)
    blended_legs = legs_by_rule["blended_single"]
    out = {}
    for rule, legs in legs_by_rule.items():
        summary = asdict(_summarize(rule, legs))
        attr = None if rule in ("raw_single", "blended_single") else _attribution(legs, blended_legs)
        out[rule] = {
            "summary": summary,
            "attr_vs_blended": attr,
            "robust_score": _robust_score(summary, attr),
        }
    return out


def _walkforward(df: pd.DataFrame, train_days: int = 14, test_days: int = 3) -> list[dict]:
    dates = sorted(df["event_date"].unique())
    folds = []
    idx = train_days
    while idx + test_days <= len(dates):
        train_start = dates[idx - train_days]
        train_end = dates[idx - 1]
        test_start = dates[idx]
        test_end = dates[idx + test_days - 1]
        train_df = _filter_dates(df, train_start, train_end)
        test_df = _filter_dates(df, test_start, test_end)
        train_eval = _evaluate_window(train_df)
        test_eval = _evaluate_window(test_df)
        selected = max(RULES, key=lambda r: train_eval[r]["robust_score"])
        folds.append({
            "train_window": [train_start, train_end],
            "test_window": [test_start, test_end],
            "selected_rule": selected,
            "train": train_eval,
            "test": test_eval,
        })
        idx += test_days
    return folds


def _aggregate_folds(folds: list[dict]) -> dict:
    selected_outcomes = []
    benchmark_outcomes = {rule: [] for rule in RULES}
    for fold in folds:
        selected = fold["selected_rule"]
        selected_outcomes.append(fold["test"][selected]["summary"])
        for rule in RULES:
            benchmark_outcomes[rule].append(fold["test"][rule]["summary"])

    def _agg(summaries: list[dict]) -> dict:
        cost = sum(s["total_cost_usd"] for s in summaries)
        pnl = sum(s["total_pnl_usd"] for s in summaries)
        n = sum(s["n_legs"] for s in summaries)
        top5_proxy = sum(s["roi_excl_top5"] * s["total_cost_usd"] for s in summaries)
        return {
            "folds": len(summaries),
            "n_legs": n,
            "total_cost_usd": cost,
            "total_pnl_usd": pnl,
            "roi": pnl / cost if cost else 0.0,
            "weighted_top5_removed_roi": top5_proxy / cost if cost else 0.0,
            "positive_fold_rate": sum(1 for s in summaries if s["total_pnl_usd"] > 0) / len(summaries) if summaries else 0.0,
        }

    return {
        "selected_by_train_score": _agg(selected_outcomes),
        "always_rules": {rule: _agg(vals) for rule, vals in benchmark_outcomes.items()},
        "selection_counts": {
            rule: sum(1 for fold in folds if fold["selected_rule"] == rule)
            for rule in RULES
        },
    }


def _write_md(report: dict, out_path: Path) -> None:
    lines = [
        "# City-Day Basket Walk-Forward Research — 2026-06-06",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: offline walk-forward diagnostic only; no N100/live behavior changed.",
        "",
        "## Method",
        "",
        "- Candidate rules are fixed in advance: `blended_single`, `heuristic_pr2b`, `combo_risk`, `combo_market_risk`, `combo_market_tail`.",
        "- Each fold uses 14 settled target dates as train and the next 3 dates as unseen test.",
        "- Train selection score weights top-5-removed ROI more than headline ROI, penalizes missed-profit > avoided-loss, and penalizes negative BUY_NO ROI.",
        "- `combo_market_tail` is the strict tail-objective candidate: market-normalized EV must stay positive after removing the best single final-temp outcome.",
        "- No parameter grid is searched inside folds.",
        "",
        "## Aggregate Test Results",
        "",
        "| policy | folds | selections | n | cost | pnl | ROI | weighted top5 ROI | positive folds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    agg = report["aggregate"]
    selected = agg["selected_by_train_score"]
    lines.append(
        f"| selected_by_train_score | {selected['folds']} | - | {selected['n_legs']} | "
        f"${selected['total_cost_usd']:.0f} | ${selected['total_pnl_usd']:+.0f} | "
        f"{selected['roi']*100:+.2f}% | {selected['weighted_top5_removed_roi']*100:+.2f}% | "
        f"{selected['positive_fold_rate']*100:.1f}% |"
    )
    for rule, vals in agg["always_rules"].items():
        lines.append(
            f"| always_{rule} | {vals['folds']} | {agg['selection_counts'][rule]} | {vals['n_legs']} | "
            f"${vals['total_cost_usd']:.0f} | ${vals['total_pnl_usd']:+.0f} | "
            f"{vals['roi']*100:+.2f}% | {vals['weighted_top5_removed_roi']*100:+.2f}% | "
            f"{vals['positive_fold_rate']*100:.1f}% |"
        )
    lines.extend([
        "",
        "## Fold Detail",
        "",
        "| fold | train | test | selected | selected test ROI | selected top5 ROI | best test rule by PnL |",
        "|---:|---|---|---|---:|---:|---|",
    ])
    for i, fold in enumerate(report["folds"], 1):
        selected_rule = fold["selected_rule"]
        selected_summary = fold["test"][selected_rule]["summary"]
        best_rule = max(RULES, key=lambda r: fold["test"][r]["summary"]["total_pnl_usd"])
        lines.append(
            f"| {i} | {fold['train_window'][0]} -> {fold['train_window'][1]} | "
            f"{fold['test_window'][0]} -> {fold['test_window'][1]} | {selected_rule} | "
            f"{selected_summary['roi']*100:+.2f}% | "
            f"{selected_summary['roi_excl_top5']*100:+.2f}% | {best_rule} |"
        )
    lines.extend([
        "",
        "## Quant Read",
        "",
        "- If train-selected rules do not beat a simple always-on baseline in unseen test windows, the selector is not yet useful.",
        "- Positive headline ROI with negative top-5-removed ROI is still tail-dependent and should stay shadow-only.",
        "- This is a small sample; use it to reject overfit candidates, not to approve production.",
        "",
        "Production remains unchanged.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    df = _load_rows(DB_DEFAULT)
    folds = _walkforward(df)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(DB_DEFAULT),
        "folds": folds,
        "aggregate": _aggregate_folds(folds),
    }
    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-basket-walkforward.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-basket-walkforward.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(report, md_path)

    agg = report["aggregate"]
    print("selection_counts", agg["selection_counts"])
    print("selected", agg["selected_by_train_score"])
    for rule, vals in agg["always_rules"].items():
        print(rule, vals)
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
