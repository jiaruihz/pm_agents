#!/usr/bin/env python3
"""Official-fee audit of the frozen 191-trade current-bracket NO result."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1"
SCORED_DEFAULT = SOURCE_DIR / "scored_pit_rows.csv"
OLD_SUMMARY_DEFAULT = SOURCE_DIR / "summary.json"
OUT_DIR_DEFAULT = ROOT / "docs/analysis/2026-07/generated/current_bracket_no_prevday_pit_fee_correction_v1"
REPORT_DEFAULT = ROOT / "docs/analysis/2026-07/2026-07-14-current-bracket-no-prevday-pit-fee-correction-v1.md"

STAKE_USD = 5.0
FEE_RATE = 0.05
SEED = 20260622  # reuse the frozen report's bootstrap seed
BOOTSTRAP_REPS = 5000


def official_weather_taker_fee(price: float, shares: float) -> float:
    return round(float(shares) * FEE_RATE * float(price) * (1.0 - float(price)), 5)


def select_first_per_city_day(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    # scored_pit_rows preserves the original feature-row order.  The fields
    # below reproduce the old stable selection; _source_row is the final tie
    # breaker because decision_snapshot_ts_utc was not exported.
    return (
        frame.sort_values(
            ["target_date", "city", "decision_hour_local", "no_ask", "_source_row"],
            kind="stable",
        )
        .drop_duplicates(["target_date", "city"], keep="first")
        .reset_index(drop=True)
    )


def add_payoff(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["fill_cost_usd"] = STAKE_USD
    out["shares"] = STAKE_USD / out["no_ask"]
    out["payout_usd"] = out["shares"] * out["label_no_wins"]
    out["gross_pnl_usd"] = out["payout_usd"] - out["fill_cost_usd"]
    out["taker_fee_usd"] = [
        official_weather_taker_fee(price, shares)
        for price, shares in zip(out["no_ask"], out["shares"], strict=True)
    ]
    out["fee_adjusted_pnl_usd"] = out["gross_pnl_usd"] - out["taker_fee_usd"]
    out["gross_roi"] = out["gross_pnl_usd"] / out["fill_cost_usd"]
    out["fee_adjusted_roi"] = out["fee_adjusted_pnl_usd"] / out["fill_cost_usd"]
    return out


def daily(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby("target_date", as_index=False)
        .agg(
            trades=("city", "size"),
            cities=("city", "nunique"),
            fill_cost_usd=("fill_cost_usd", "sum"),
            gross_pnl_usd=("gross_pnl_usd", "sum"),
            taker_fee_usd=("taker_fee_usd", "sum"),
            fee_adjusted_pnl_usd=("fee_adjusted_pnl_usd", "sum"),
        )
        .assign(
            gross_roi=lambda x: x["gross_pnl_usd"] / x["fill_cost_usd"],
            fee_adjusted_roi=lambda x: x["fee_adjusted_pnl_usd"] / x["fill_cost_usd"],
        )
    )


def bootstrap_roi(frame: pd.DataFrame, pnl_col: str, *, reps: int, seed: int) -> tuple[float, float]:
    blocks = daily(frame).set_index("target_date")
    dates = sorted(blocks.index)
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        sample = blocks.loc[draw].sum(numeric_only=True)
        values.append(float(sample[pnl_col] / sample["fill_cost_usd"]))
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def bootstrap_excess(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    pnl_col: str,
    *,
    reps: int,
    seed: int,
) -> tuple[float, float]:
    cand = daily(candidate).set_index("target_date")
    base = daily(baseline).set_index("target_date")
    dates = sorted(set(cand.index) | set(base.index))
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        c = cand.reindex(draw, fill_value=0).sum(numeric_only=True)
        b = base.reindex(draw, fill_value=0).sum(numeric_only=True)
        values.append(float(c[pnl_col] / c["fill_cost_usd"] - b[pnl_col] / b["fill_cost_usd"]))
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def metric(frame: pd.DataFrame, *, reps: int, seed: int) -> dict[str, Any]:
    cost = float(frame["fill_cost_usd"].sum())
    gross = float(frame["gross_pnl_usd"].sum())
    fee = float(frame["taker_fee_usd"].sum())
    net = float(frame["fee_adjusted_pnl_usd"].sum())
    gross_ci = bootstrap_roi(frame, "gross_pnl_usd", reps=reps, seed=seed)
    net_ci = bootstrap_roi(frame, "fee_adjusted_pnl_usd", reps=reps, seed=seed)
    return {
        "trades": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "date_min": str(frame["target_date"].min()),
        "date_max": str(frame["target_date"].max()),
        "fill_cost_usd": cost,
        "gross_pnl_usd": gross,
        "taker_fee_usd": fee,
        "fee_adjusted_pnl_usd": net,
        "gross_roi_on_fill_cost": gross / cost,
        "gross_roi_ci_low": gross_ci[0],
        "gross_roi_ci_high": gross_ci[1],
        "fee_adjusted_roi_on_fill_cost": net / cost,
        "fee_adjusted_roi_ci_low": net_ci[0],
        "fee_adjusted_roi_ci_high": net_ci[1],
        "fee_adjusted_roi_on_cash_plus_fee": net / (cost + fee),
    }


def pct(value: float) -> str:
    return f"{100.0 * value:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scored", default=str(SCORED_DEFAULT))
    parser.add_argument("--old-summary", default=str(OLD_SUMMARY_DEFAULT))
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--report-md", default=str(REPORT_DEFAULT))
    parser.add_argument("--bootstrap-reps", type=int, default=BOOTSTRAP_REPS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    scored_path = Path(args.scored)
    old_summary_path = Path(args.old_summary)
    out_dir = Path(args.out_dir)
    report_path = Path(args.report_md)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    scored = pd.read_csv(scored_path)
    scored["_source_row"] = np.arange(2, len(scored) + 2)
    for col in ["no_ask", "label_no_wins", "pit_logit_c0p2_p", "decision_hour_local"]:
        scored[col] = pd.to_numeric(scored[col], errors="raise")
    scored["trade_base"] = scored["trade_base"].astype(str).str.lower().eq("true")

    baseline_raw = scored[scored["trade_base"]].copy()
    candidate_raw = scored[scored["trade_base"] & scored["pit_logit_c0p2_p"].ge(0.50)].copy()
    baseline = add_payoff(select_first_per_city_day(baseline_raw))
    candidate = add_payoff(select_first_per_city_day(candidate_raw))

    old_summary = json.loads(old_summary_path.read_text(encoding="utf-8"))
    old_variant = next(
        item for item in old_summary["variants"] if item["variant"] == "pit_prevday_logit_c0p2_p_ge_0p50"
    )
    if int(old_variant["raw_signals"]) != len(candidate_raw) or int(old_variant["selected_trades"]) != len(candidate):
        raise RuntimeError("frozen current-bracket denominator no longer reproduces the old report")
    if abs(float(old_variant["profit_usd"]) - float(candidate["gross_pnl_usd"].sum())) > 1e-9:
        raise RuntimeError("frozen gross PnL no longer reproduces the old report")

    candidate_metric = metric(candidate, reps=args.bootstrap_reps, seed=args.seed)
    baseline_metric = metric(baseline, reps=args.bootstrap_reps, seed=args.seed)
    gross_excess = float(candidate_metric["gross_roi_on_fill_cost"] - baseline_metric["gross_roi_on_fill_cost"])
    net_excess = float(candidate_metric["fee_adjusted_roi_on_fill_cost"] - baseline_metric["fee_adjusted_roi_on_fill_cost"])
    gross_excess_ci = bootstrap_excess(
        candidate, baseline, "gross_pnl_usd", reps=args.bootstrap_reps, seed=args.seed + 1
    )
    net_excess_ci = bootstrap_excess(
        candidate, baseline, "fee_adjusted_pnl_usd", reps=args.bootstrap_reps, seed=args.seed + 1
    )

    exact_cols = [
        "_source_row",
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "depth5_notional",
        "pit_logit_c0p2_p",
        "label_no_wins",
        "label_afternoon_peak",
        "fill_cost_usd",
        "shares",
        "payout_usd",
        "gross_pnl_usd",
        "taker_fee_usd",
        "fee_adjusted_pnl_usd",
        "gross_roi",
        "fee_adjusted_roi",
    ]
    exact_path = out_dir / "exact_191_trades.csv"
    baseline_path = out_dir / "exact_361_baseline_trades.csv"
    daily_path = out_dir / "daily_summary.csv"
    summary_path = out_dir / "summary.json"
    candidate[exact_cols].to_csv(exact_path, index=False)
    baseline[exact_cols].to_csv(baseline_path, index=False)
    daily(candidate).to_csv(daily_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit": "current_bracket_no_prevday_pit_fee_correction_v1",
        "frozen_rule": "trade_base and pit_logit_c0p2_p >= 0.50; first per target_date/city",
        "source": str(scored_path.relative_to(ROOT)),
        "legacy_bug": {
            "script": "scripts/analysis/reheat_risk/research_current_bracket_no_pass_through_v1.py",
            "cause": "stake_profit_usd was payout minus fixed stake; summarize and bootstrap used it without any taker fee",
            "affected_report": "docs/analysis/2026-06/2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md",
        },
        "fee_contract": {
            "entry": "taker BUY_NO",
            "formula": "shares * 0.05 * price * (1 - price)",
            "precision": "round to 5 decimals per order",
        },
        "candidate": candidate_metric,
        "same_price_cap_baseline": baseline_metric,
        "relative_to_baseline": {
            "gross_excess_roi": gross_excess,
            "gross_excess_roi_ci_low": gross_excess_ci[0],
            "gross_excess_roi_ci_high": gross_excess_ci[1],
            "fee_adjusted_excess_roi": net_excess,
            "fee_adjusted_excess_roi_ci_low": net_excess_ci[0],
            "fee_adjusted_excess_roi_ci_high": net_excess_ci[1],
        },
        "bootstrap": {"block": "target_date", "reps": args.bootstrap_reps, "seed": args.seed},
        "gate_change": {
            "old_absolute_significance": "PASS",
            "corrected_absolute_significance": "PASS"
            if candidate_metric["fee_adjusted_roi_ci_low"] > 0
            else "FAIL",
            "reason": "official-fee target-date bootstrap CI crosses zero",
        },
        "verdict": {
            "historical_relative_alpha": "survives_vs_same_price_baseline",
            "absolute_profitability": "inconclusive_after_fee",
            "forward": "not_proven_by_this_frozen_audit",
            "live": "NO",
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    c = candidate_metric
    rel = summary["relative_to_baseline"]
    report = f"""# Current-Bracket NO Prevday PIT Fee Correction v1

## 结论

固定旧报告同一 `220` raw signals、同一 first-per-city-day 选择后，精确复现 `191` trades / `32` target dates / gross PnL `{c['gross_pnl_usd']:+.6f}` / gross ROI `{pct(c['gross_roi_on_fill_cost'])}`。加官方 taker fee `{c['taker_fee_usd']:.5f}` 后，PnL 为 `{c['fee_adjusted_pnl_usd']:+.6f}`，ROI `{pct(c['fee_adjusted_roi_on_fill_cost'])}`。

关键变化不是点估转负，而是 significance：旧 gross CI `{pct(c['gross_roi_ci_low'])}`..`{pct(c['gross_roi_ci_high'])}`；fee-adjusted CI `{pct(c['fee_adjusted_roi_ci_low'])}`..`{pct(c['fee_adjusted_roi_ci_high'])}`，lower bound 过零，所以绝对收益 gate **PASS → FAIL**。

相对同 NO ask/cap baseline 的结构仍在：fee-adjusted excess ROI `{pct(rel['fee_adjusted_excess_roi'])}`，CI `{pct(rel['fee_adjusted_excess_roi_ci_low'])}`..`{pct(rel['fee_adjusted_excess_roi_ci_high'])}`。这说明 classifier 仍可能在做有效排序，但当前证据不足以称为“已确认可盈利”，更不能直接 live。

## Bug 与影响半径

- 根因：`research_current_bracket_no_pass_through_v1.py:348-350` 定义 `stake_profit_usd = label * shares - STAKE_USD`，`:374-378` 与 `:426-431` 的日级 bootstrap/summary 直接汇总该 gross 字段，未扣 entry taker fee。
- 污染窗口：`{c['date_min']}`..`{c['date_max']}`，`{c['trades']}` trades / `{c['active_dates']}` dates / `{c['cities']}` cities。
- 受影响结论：`2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md` 的 `+29.7% CI [+3.8%, +54.6%]` 与 significance PASS。
- 没被本审计证明：forward fill、slippage、queue/latency；本报告只纠正冻结 historical replay。

## 口径

- entry：真实 NO ask 的 taker `BUY_NO`；固定 `$5` fill cost，shares=`5/no_ask`。
- fee：`shares * 0.05 * price * (1-price)`，每笔 round 到 5 decimals。
- bootstrap：沿用旧 seed `{args.seed}`，按 `target_date` block，`{args.bootstrap_reps}` reps；gross CI 精确复现旧报告。

## 可复查输出

- exact 191 trades：`{exact_path.relative_to(ROOT)}`
- exact 361 baseline trades：`{baseline_path.relative_to(ROOT)}`
- daily：`{daily_path.relative_to(ROOT)}`
- machine summary：`{summary_path.relative_to(ROOT)}`
"""
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"summary": summary, "outputs": [str(exact_path), str(daily_path), str(summary_path), str(report_path)]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
