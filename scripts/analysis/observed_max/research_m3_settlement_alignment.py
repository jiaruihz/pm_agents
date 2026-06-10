#!/usr/bin/env python3
"""M3 observed max to pm_history settlement alignment.

This checks whether observed final max maps to the Polymarket winning bracket,
and recomputes M3 best-ask trades using official pm_history winners.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


WIN_THRESHOLD = 0.99


@dataclass(frozen=True)
class MarketBracket:
    label: str
    low: float | None
    high: float | None

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observed-detail",
        default="docs/analysis/2026-06/generated/m3_observed_max_v1/m3_observed_max_residual_detail.csv",
    )
    parser.add_argument(
        "--best-ask-trades",
        default="docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/m3_orderbook_best_ask_trades.csv",
    )
    parser.add_argument(
        "--pm-history-dir",
        default="runtime/weather_edge_v1/market_data/cache/pm_history",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_settlement_alignment_v1",
    )
    return parser.parse_args()


def parse_market_bracket(label_value: object, question_value: object) -> MarketBracket | None:
    label = str(label_value or "").replace("°", "").strip()
    question = str(question_value or "").lower()
    if not label:
        return None
    if "-" in label:
        parts = label.replace("+", "").split("-", 1)
        try:
            nums = [float(parts[0]), float(parts[1])]
        except (TypeError, ValueError):
            nums = []
    else:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label)]
    if not nums:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", question)]
    if not nums:
        return None
    if "or below" in question or "or lower" in question:
        return MarketBracket(label=label, low=None, high=nums[0])
    if "or higher" in question or "or above" in question or label.endswith("+"):
        return MarketBracket(label=label, low=nums[0], high=None)
    if "-" in label and len(nums) >= 2:
        return MarketBracket(label=label, low=nums[0], high=nums[1])
    return MarketBracket(label=label, low=nums[0], high=nums[0])


def load_pm_history(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    brackets = data.get("brackets")
    if not isinstance(brackets, list):
        return None
    parsed = []
    winners = []
    for bracket in brackets:
        if not isinstance(bracket, dict):
            continue
        parsed_bracket = parse_market_bracket(bracket.get("label"), bracket.get("question"))
        if parsed_bracket is None:
            continue
        final_price = pd.to_numeric(bracket.get("final_price"), errors="coerce")
        row = {
            "label": parsed_bracket.label,
            "low": parsed_bracket.low,
            "high": parsed_bracket.high,
            "final_price": float(final_price) if pd.notna(final_price) else None,
            "closed": bracket.get("closed"),
            "question": bracket.get("question"),
        }
        parsed.append(row)
        if row["final_price"] is not None and row["final_price"] >= WIN_THRESHOLD:
            winners.append(row)
    return {
        "unit": data.get("unit"),
        "brackets": parsed,
        "winners": winners,
        "winner_labels": [w["label"] for w in winners],
    }


def predicted_label(brackets: list[dict[str, Any]], value: float) -> str | None:
    for bracket in brackets:
        mb = MarketBracket(label=bracket["label"], low=bracket["low"], high=bracket["high"])
        if mb.contains(value):
            return mb.label
    return None


def load_observed_city_days(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    keep = ["city", "target_date", "final_max_f", "final_max_c"]
    return df[keep].drop_duplicates(["city", "target_date"]).copy()


def build_alignment(observed: pd.DataFrame, pm_history_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in observed.itertuples(index=False):
        city = str(row.city)
        target_date = str(row.target_date)
        path = pm_history_dir / f"{city}_{target_date}.json"
        history = load_pm_history(path)
        out: dict[str, Any] = {
            "city": city,
            "target_date": target_date,
            "pm_history_file": str(path),
            "pm_history_exists": path.exists(),
            "pm_history_valid": history is not None,
            "unit": None,
            "winner_count": 0,
            "winner_labels": "",
            "final_max_f": float(row.final_max_f),
            "final_max_c": float(row.final_max_c),
        }
        if history is None:
            rows.append(out)
            continue
        unit = str(history.get("unit") or "").upper()
        value = float(row.final_max_c if unit == "C" else row.final_max_f)
        brackets = history["brackets"]
        out.update(
            {
                "unit": unit,
                "winner_count": len(history["winners"]),
                "winner_labels": "|".join(history["winner_labels"]),
                "observed_value_raw": value,
                "observed_value_floor": math.floor(value + 1e-9),
                "observed_value_round": round(value),
                "observed_value_ceil": math.ceil(value - 1e-9),
            }
        )
        for rule in ["raw", "floor", "round", "ceil"]:
            candidate = out[f"observed_value_{rule}"]
            label = predicted_label(brackets, float(candidate))
            out[f"predicted_label_{rule}"] = label
            out[f"match_{rule}"] = int(label is not None and label in history["winner_labels"])
        rows.append(out)
    return pd.DataFrame(rows)


def summarize_alignment(alignment: pd.DataFrame) -> pd.DataFrame:
    if alignment.empty:
        return pd.DataFrame()
    rows = []
    scopes = [("all", alignment)]
    if "unit" in alignment.columns:
        for unit, group in alignment.groupby("unit", dropna=False):
            scopes.append((f"unit_{unit}", group))
    for scope, df in scopes:
        valid = df[df["pm_history_valid"].eq(True) & df["winner_count"].eq(1)].copy()
        row = {
            "scope": scope,
            "city_days": len(df),
            "valid_single_winner_city_days": len(valid),
            "pm_history_missing": int((~df["pm_history_exists"]).sum()),
            "pm_history_invalid": int((df["pm_history_exists"] & ~df["pm_history_valid"]).sum()),
            "multi_or_zero_winner": int((df["pm_history_valid"] & ~df["winner_count"].eq(1)).sum()),
        }
        for rule in ["raw", "floor", "round", "ceil"]:
            row[f"{rule}_matches"] = int(valid.get(f"match_{rule}", pd.Series(dtype=int)).sum())
            row[f"{rule}_match_rate"] = (
                float(valid[f"match_{rule}"].mean()) if not valid.empty and f"match_{rule}" in valid else float("nan")
            )
        rows.append(row)
    return pd.DataFrame(rows)


def recompute_trades_with_official(trades: pd.DataFrame, alignment: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return trades, pd.DataFrame()
    key = ["city", "target_date"]
    joined = trades.merge(
        alignment[
            [
                "city",
                "target_date",
                "pm_history_valid",
                "winner_count",
                "winner_labels",
                "unit",
                "predicted_label_floor",
                "match_floor",
            ]
        ],
        on=key,
        how="left",
    )
    joined["official_settled"] = joined["pm_history_valid"].eq(True) & joined["winner_count"].eq(1)
    joined["official_final_hit"] = joined.apply(
        lambda r: int(str(r.get("bracket")) in str(r.get("winner_labels") or "").split("|"))
        if bool(r.get("official_settled"))
        else pd.NA,
        axis=1,
    )
    yes = joined["side"].eq("BUY_YES")
    no = joined["side"].eq("BUY_NO")
    joined["official_payout"] = pd.NA
    joined.loc[yes & joined["official_settled"], "official_payout"] = joined.loc[
        yes & joined["official_settled"], "official_final_hit"
    ].astype(float)
    joined.loc[no & joined["official_settled"], "official_payout"] = (
        1.0 - joined.loc[no & joined["official_settled"], "official_final_hit"].astype(float)
    )
    joined["official_pnl"] = pd.to_numeric(joined["official_payout"], errors="coerce") - joined["entry_cost"]
    joined["official_roi"] = joined["official_pnl"] / joined["entry_cost"]
    joined["observed_vs_official_payout_match"] = (
        pd.to_numeric(joined["payout"], errors="coerce") == pd.to_numeric(joined["official_payout"], errors="coerce")
    )

    settled = joined[joined["official_settled"].eq(True)].copy()
    if settled.empty:
        return joined, pd.DataFrame()
    settled["city_day_key"] = settled["city"].astype(str) + "|" + settled["target_date"].astype(str)
    summary = (
        settled.groupby(["strategy", "decision_hour_local"])
        .agg(
            trades=("official_pnl", "count"),
            city_days=("city_day_key", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            official_pnl=("official_pnl", "sum"),
            observed_pnl=("pnl", "sum"),
            payout_mismatches=("observed_vs_official_payout_match", lambda s: int((~s).sum())),
            win_rate=("official_payout", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean())),
        )
        .reset_index()
    )
    summary["official_roi"] = summary["official_pnl"] / summary["cost"]
    summary["observed_roi"] = summary["observed_pnl"] / summary["cost"]
    total = (
        settled.groupby("strategy")
        .agg(
            trades=("official_pnl", "count"),
            city_days=("city_day_key", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            official_pnl=("official_pnl", "sum"),
            observed_pnl=("pnl", "sum"),
            payout_mismatches=("observed_vs_official_payout_match", lambda s: int((~s).sum())),
            win_rate=("official_payout", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean())),
        )
        .reset_index()
    )
    total["decision_hour_local"] = "ALL"
    total["official_roi"] = total["official_pnl"] / total["cost"]
    total["observed_roi"] = total["observed_pnl"] / total["cost"]
    return joined, pd.concat([summary, total[summary.columns]], ignore_index=True)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    observed = load_observed_city_days(Path(args.observed_detail))
    alignment = build_alignment(observed, Path(args.pm_history_dir))
    alignment_summary = summarize_alignment(alignment)
    trades = pd.read_csv(args.best_ask_trades) if Path(args.best_ask_trades).exists() else pd.DataFrame()
    official_trades, official_summary = recompute_trades_with_official(trades, alignment)

    paths = {
        "alignment": output_dir / "m3_settlement_alignment_city_days.csv",
        "alignment_summary": output_dir / "m3_settlement_alignment_summary.csv",
        "official_trades": output_dir / "m3_orderbook_best_ask_trades_official_settlement.csv",
        "official_summary": output_dir / "m3_orderbook_best_ask_official_settlement_summary.csv",
        "manifest": output_dir / "manifest.json",
    }
    alignment.to_csv(paths["alignment"], index=False)
    alignment_summary.to_csv(paths["alignment_summary"], index=False)
    official_trades.to_csv(paths["official_trades"], index=False)
    official_summary.to_csv(paths["official_summary"], index=False)

    manifest = {
        "experiment": "m3_settlement_alignment_v1",
        "observed_detail": args.observed_detail,
        "best_ask_trades": args.best_ask_trades,
        "pm_history_dir": args.pm_history_dir,
        "output_dir": str(output_dir),
        "observed_city_days": int(len(observed)),
        "alignment_rows": int(len(alignment)),
        "best_ask_trade_rows": int(len(trades)),
        "official_trade_rows": int(len(official_trades)),
        "official_summary_rows": int(len(official_summary)),
        "win_threshold": WIN_THRESHOLD,
        "outputs": {k: str(v) for k, v in paths.items() if k != "manifest"},
        "notes": [
            "Alignment tests raw/floor/round/ceil observed values against pm_history winner labels.",
            "Official trade PnL uses pm_history winner labels, not observed final max payout.",
            "This is still an orderbook backtest, not live CLOB fill PnL.",
        ],
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
