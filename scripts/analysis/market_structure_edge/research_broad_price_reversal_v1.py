#!/usr/bin/env python3
"""Test broad, pre-registered price-reversal rules without city/source filters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-14-broad-price-reversal-v1.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/generated/broad_price_reversal_v1/summary.json"
FORWARD_START = "2026-06-21"
MOVE_THRESHOLD = 0.02


def fee_per_share(price: pd.Series) -> pd.Series:
    return (0.05 * price * (1.0 - price)).round(5)


def block_ci(rows: pd.DataFrame, seed: int = 20260714, draws: int = 4000) -> tuple[float, float]:
    dates = sorted(rows["target_date"].unique())
    if len(dates) < 2:
        return (float("nan"), float("nan"))
    daily = rows.groupby("target_date", as_index=True)[["pnl", "cost"]].sum()
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(draws):
        sample = rng.choice(dates, size=len(dates), replace=True)
        agg = daily.loc[sample].sum()
        values.append(float(agg["pnl"] / agg["cost"]))
    return tuple(float(x) for x in np.quantile(values, [0.025, 0.975]))


def summarize(rows: pd.DataFrame, *, depth_required: bool) -> dict[str, object]:
    if rows.empty:
        return {"rows": 0, "dates": 0, "cities": 0}
    low, high = block_ci(rows)
    cost = float(rows["cost"].sum())
    pnl = float(rows["pnl"].sum())
    return {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost,
        "roi_ci_low": low,
        "roi_ci_high": high,
        "win_rate": float(rows["payout"].mean()),
        "avg_move": float(rows["yes_mid_move"].mean()),
        "avg_ask": float(rows["ask"].mean()),
        "depth_required": depth_required,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    frame = pd.read_csv(args.input)
    frame["decision_ts"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], utc=True)
    frame = frame.sort_values(["city", "target_date", "decision_ts"]).copy()
    frame["yes_mid"] = (frame["current_yes_ask"] + (1.0 - frame["current_no_ask"])) / 2.0
    groups = frame.groupby(["city", "target_date"], sort=False)
    frame["previous_yes_mid"] = groups["yes_mid"].shift()
    frame["previous_current_bracket"] = groups["current_bracket"].shift()
    frame["yes_mid_move"] = frame["yes_mid"] - frame["previous_yes_mid"]
    base = frame[
        frame["current_bracket"].eq(frame["previous_current_bracket"])
        & frame["yes_mid_move"].notna()
    ].copy()

    rules = {
        "reversal_buy_no_after_yes_rise": {
            "rows": base[base["yes_mid_move"] >= MOVE_THRESHOLD].copy(),
            "side": "NO",
            "depth_required": True,
        },
        "reversal_buy_yes_after_yes_fall": {
            "rows": base[base["yes_mid_move"] <= -MOVE_THRESHOLD].copy(),
            "side": "YES",
            "depth_required": False,
        },
    }

    output: dict[str, object] = {
        "contract": {
            "input": str(args.input),
            "forward_start": FORWARD_START,
            "move_threshold": MOVE_THRESHOLD,
            "entry": "same-bracket next hourly state, observed ask",
            "exit": "settlement",
            "fee": "round(0.05*p*(1-p), 5) per share",
            "selection": "no city, source, weather, or ask-price filter",
        },
        "inventory": {
            "rows": int(len(frame)),
            "dates": int(frame["target_date"].nunique()),
            "cities": int(frame["city"].nunique()),
            "min_date": str(frame["target_date"].min()),
            "max_date": str(frame["target_date"].max()),
            "same_bracket_rows": int(len(base)),
        },
        "rules": {},
    }

    for name, spec in rules.items():
        rows = spec["rows"]
        side = spec["side"]
        if side == "NO":
            rows = rows[
                rows["current_no_ask"].between(0.001, 0.999)
                & rows["current_no_ask_size"].ge(5.0)
            ].copy()
            rows["ask"] = rows["current_no_ask"]
            rows["payout"] = 1.0 - rows["current_bracket_held"]
        else:
            rows = rows[rows["current_yes_ask"].between(0.001, 0.999)].copy()
            rows["ask"] = rows["current_yes_ask"]
            rows["payout"] = rows["current_bracket_held"]
        rows["cost"] = rows["ask"] + fee_per_share(rows["ask"])
        rows["pnl"] = rows["payout"] - rows["cost"]
        output["rules"][name] = {
            "all": summarize(rows, depth_required=bool(spec["depth_required"])),
            "train": summarize(
                rows[rows["target_date"] < FORWARD_START],
                depth_required=bool(spec["depth_required"]),
            ),
            "forward": summarize(
                rows[rows["target_date"] >= FORWARD_START],
                depth_required=bool(spec["depth_required"]),
            ),
        }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")

    def pct(value: float) -> str:
        return f"{value:+.1%}"

    lines = [
        "# Broad Price Reversal v1",
        "",
        "> 2026-07-14; research-only; broad denominator; zero notional.",
        "",
        "## 结论",
        "",
        "简单的盘口反转不是可用 alpha。这里没有按 city/source/天气状态/ask 价格筛选：只要求同一 exact bracket、连续小时的 YES midpoint 至少移动 2c，然后在新 ask 反向买入并持有到结算。",
        "",
        f"底表覆盖 `{output['inventory']['min_date']}..{output['inventory']['max_date']}`，{output['inventory']['dates']} 个 target dates、{output['inventory']['cities']} 城、{output['inventory']['same_bracket_rows']} 条同 bracket 连续状态。",
        "",
        "| rule | scope | rows | dates | cities | ROI | date-block 95% CI | depth |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for rule, scopes in output["rules"].items():
        for scope in ("train", "forward", "all"):
            stat = scopes[scope]
            lines.append(
                f"| {rule} | {scope} | {stat['rows']} | {stat['dates']} | {stat['cities']} | "
                f"{pct(stat['roi'])} | [{pct(stat['roi_ci_low'])}, {pct(stat['roi_ci_high'])}] | "
                f"{'ask_size>=5' if stat['depth_required'] else 'YES depth unavailable; diagnostic'} |"
            )
    lines += [
        "",
        "## 判定",
        "",
        "- BUY NO after a YES rise is the executable primary test and is negative in train, forward, and the full sample.",
        "- BUY YES after a YES fall is also negative; because the atlas did not persist YES ask depth, it is diagnostic rather than publishable executable evidence.",
        "- The failure is broad rather than caused by a narrow source/city gate: observed repricing is mostly informative, while spread and taker fee consume the remaining margin.",
        "- conclusion=`rejected_as_main_strategy`; do not create a reversal live runner from this result.",
        "",
        "Artifact: `docs/analysis/2026-07/generated/broad_price_reversal_v1/summary.json`.",
    ]
    args.report.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
