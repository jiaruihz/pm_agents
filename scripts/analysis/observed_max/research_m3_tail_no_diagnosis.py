#!/usr/bin/env python3
"""M3 tail-NO retail diagnosis.

Builds on committed artifacts (settlement alignment v1, observed max v1,
orderbook best-ask v1) and answers four questions:

1. Which cities have WU/IEM <-> pm_history settlement alignment good enough
   to trade on the WU/IEM proxy at all (whitelist)?
2. Is the per-city misalignment a fixable constant station offset, or a
   different official station (per-city modal-offset correction test)?
3. What is the physical actuarial loss rate for selling the high-temperature
   tail (bucket-jump probabilities after 18/19/20/21 local)?
4. Does buying NO on brackets ABOVE the running max (the user's M3 tail-NO
   theta expression) make money at historical 20/21h best asks under
   OFFICIAL pm_history settlement, and what is top-of-book capacity?

All inputs are committed CSVs; no mirror data is required, so this runs on
any checkout.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

WHITELIST_MIN_DAYS = 20
PREMIUM_ASK_CAP = 0.97


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--alignment-city-days",
        default="docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv",
    )
    parser.add_argument(
        "--observed-detail",
        default="docs/analysis/2026-06/generated/m3_observed_max_v1/m3_observed_max_residual_detail.csv",
    )
    parser.add_argument(
        "--best-ask-joined",
        default="docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/m3_orderbook_best_ask_joined.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_tail_no_diagnosis_v0",
    )
    return parser.parse_args()


def label_num(value: object) -> float | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    return float(nums[0]) if nums else None


def load_valid_alignment(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].eq(True) & df["winner_count"].eq(1)].copy()
    valid["winner_num"] = valid["winner_labels"].map(label_num)
    valid["delta"] = valid["winner_num"] - valid["observed_value_round"]
    return valid


def build_city_alignment(valid: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    per_city = (
        valid.groupby(["city", "unit"])["match_round"]
        .agg(valid_days="count", match_rate="mean")
        .reset_index()
    )
    per_city["whitelisted"] = per_city["match_rate"].eq(1.0) & (
        per_city["valid_days"] >= WHITELIST_MIN_DAYS
    )
    whitelist = set(per_city[per_city["whitelisted"]]["city"])
    return per_city, whitelist


def offset_correction_test(valid: pd.DataFrame) -> pd.DataFrame:
    rows = []
    c_markets = valid[valid["unit"].eq("C")]
    for city, group in c_markets.groupby("city"):
        if group["match_round"].mean() >= 1.0:
            continue
        group = group.sort_values("target_date")
        mode = group["delta"].mode().iloc[0]
        monthly = (
            group.assign(month=group["target_date"].astype(str).str[:7])
            .groupby("month")["delta"]
            .agg(lambda s: s.value_counts().index[0])
        )
        rows.append(
            {
                "city": city,
                "valid_days": len(group),
                "raw_match_rate": float(group["delta"].eq(0).mean()),
                "modal_offset": float(mode),
                "modal_offset_match_rate": float(group["delta"].eq(mode).mean()),
                "monthly_modal_delta": json.dumps({k: int(v) for k, v in monthly.items()}),
            }
        )
    return pd.DataFrame(rows).sort_values("raw_match_rate").reset_index(drop=True)


def actuarial_jump_table(detail_path: Path, whitelist: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail = pd.read_csv(
        detail_path,
        usecols=["city", "target_date", "decision_hour_local", "running_max_c", "final_max_c"],
    )
    detail["jump"] = detail["final_max_c"].round().astype(int) - detail["running_max_c"].round().astype(int)
    detail["in_whitelist"] = detail["city"].isin(whitelist)

    by_hour = (
        detail.groupby(["in_whitelist", "decision_hour_local"])["jump"]
        .agg(
            city_days="count",
            p_jump_ge_1=lambda s: float((s >= 1).mean()),
            p_jump_ge_2=lambda s: float((s >= 2).mean()),
            p_jump_ge_3=lambda s: float((s >= 3).mean()),
        )
        .reset_index()
    )
    by_city_h20 = (
        detail[detail["decision_hour_local"].eq(20)]
        .groupby(["city", "in_whitelist"])["jump"]
        .agg(
            city_days="count",
            p_jump_ge_1=lambda s: float((s >= 1).mean()),
            p_jump_ge_2=lambda s: float((s >= 2).mean()),
        )
        .reset_index()
        .sort_values("p_jump_ge_1", ascending=False)
    )
    return by_hour, by_city_h20


def tail_no_backtest(
    joined_path: Path, valid: pd.DataFrame, whitelist: set[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    joined = pd.read_csv(joined_path)
    joined["run_bucket"] = joined["running_max_c"].round().astype(int)
    joined["bracket_num"] = pd.to_numeric(joined["bracket"], errors="coerce")
    joined["in_whitelist"] = joined["city"].isin(whitelist)

    winners = valid.set_index(["city", "target_date"])["winner_num"]
    tail = joined[
        joined["outcome"].str.upper().eq("NO") & (joined["bracket_num"] > joined["run_bucket"])
    ].copy()
    tail["winner_num"] = [
        winners.get((c, d), np.nan) for c, d in zip(tail["city"], tail["target_date"])
    ]
    tail = tail[tail["winner_num"].notna()].copy()
    tail["tail_dist"] = tail["bracket_num"] - tail["run_bucket"]
    tail["no_wins"] = (tail["winner_num"] != tail["bracket_num"]).astype(int)
    tail["official_pnl"] = tail["no_wins"] - tail["best_ask"]
    tail["top_of_book_notional"] = tail["best_ask"] * tail["best_ask_size"]
    tail["top_of_book_max_profit"] = (1.0 - tail["best_ask"]) * tail["best_ask_size"]

    executable = tail[tail["best_ask"].le(PREMIUM_ASK_CAP)]
    summary = (
        executable.groupby("in_whitelist")
        .agg(
            trades=("official_pnl", "count"),
            cities=("city", "nunique"),
            cost=("best_ask", "sum"),
            official_pnl=("official_pnl", "sum"),
            win_rate=("no_wins", "mean"),
            top_of_book_notional=("top_of_book_notional", "sum"),
        )
        .reset_index()
    )
    summary["official_roi"] = summary["official_pnl"] / summary["cost"]
    return tail, summary


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    valid = load_valid_alignment(Path(args.alignment_city_days))
    per_city, whitelist = build_city_alignment(valid)
    offsets = offset_correction_test(valid)
    jump_by_hour, jump_by_city = actuarial_jump_table(Path(args.observed_detail), whitelist)
    tail_trades, tail_summary = tail_no_backtest(Path(args.best_ask_joined), valid, whitelist)

    paths = {
        "city_alignment": output_dir / "m3_city_alignment_whitelist.csv",
        "offset_correction": output_dir / "m3_offset_correction_test.csv",
        "jump_by_hour": output_dir / "m3_bucket_jump_by_hour.csv",
        "jump_by_city_h20": output_dir / "m3_bucket_jump_by_city_h20.csv",
        "tail_no_trades": output_dir / "m3_tail_no_official_trades.csv",
        "tail_no_summary": output_dir / "m3_tail_no_official_summary.csv",
        "manifest": output_dir / "manifest.json",
    }
    per_city.to_csv(paths["city_alignment"], index=False)
    offsets.to_csv(paths["offset_correction"], index=False)
    jump_by_hour.to_csv(paths["jump_by_hour"], index=False)
    jump_by_city.to_csv(paths["jump_by_city_h20"], index=False)
    tail_trades.to_csv(paths["tail_no_trades"], index=False)
    tail_summary.to_csv(paths["tail_no_summary"], index=False)

    manifest = {
        "experiment": "m3_tail_no_diagnosis_v0",
        "inputs": {
            "alignment_city_days": args.alignment_city_days,
            "observed_detail": args.observed_detail,
            "best_ask_joined": args.best_ask_joined,
        },
        "whitelist_min_days": WHITELIST_MIN_DAYS,
        "premium_ask_cap": PREMIUM_ASK_CAP,
        "whitelist_cities": sorted(whitelist),
        "whitelist_size": len(whitelist),
        "tail_no_quote_rows": int(len(tail_trades)),
        "outputs": {k: str(v) for k, v in paths.items() if k != "manifest"},
        "notes": [
            "Whitelist = cities whose round(WU/IEM final max) matched pm_history winner on 100% of valid days (min 20 days).",
            "Tail NO = BUY_NO on brackets strictly above round(running max) at local hour 20/21, official pm_history settlement.",
            "This is an orderbook best-ask backtest, not live CLOB fill PnL.",
        ],
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
