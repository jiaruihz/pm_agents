#!/usr/bin/env python3
"""M3 paper-snapshot proxy backtest.

This is not an executable orderbook backtest. It uses legacy paper snapshot
`market_yes_price` as a price proxy because current orderbook snapshots and WU
observed-cache windows do not overlap.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


DEFAULT_HOURS = (20, 21)


@dataclass(frozen=True)
class Bracket:
    raw: str
    low_f: float | None
    high_f: float | None

    def contains(self, temp_f: float) -> bool:
        if self.low_f is not None and temp_f < self.low_f:
            return False
        if self.high_f is not None and temp_f > self.high_f:
            return False
        return True

    def below_temp(self, temp_f: float) -> bool:
        return self.high_f is not None and self.high_f < temp_f


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observed-detail",
        default="docs/analysis/2026-06/generated/m3_observed_max_v0/m3_observed_max_residual_detail.csv",
    )
    parser.add_argument(
        "--paper-snapshot-dir",
        default="runtime/weather_edge_v1/remote_pm_agent/market_data/paper_snapshots",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_paper_snapshot_proxy_v0",
    )
    parser.add_argument("--decision-hours", default=",".join(str(x) for x in DEFAULT_HOURS))
    parser.add_argument("--min-market-yes-price", type=float, default=0.005)
    parser.add_argument("--max-market-yes-price", type=float, default=0.995)
    return parser.parse_args()


def parse_bracket(value: object) -> Bracket | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    s = raw.replace("°", "").replace("F", "").strip()
    if s.endswith("+"):
        try:
            return Bracket(raw=raw, low_f=float(s[:-1]), high_f=None)
        except ValueError:
            return None
    if s.startswith("<="):
        try:
            return Bracket(raw=raw, low_f=None, high_f=float(s[2:]))
        except ValueError:
            return None
    if s.startswith("<"):
        try:
            return Bracket(raw=raw, low_f=None, high_f=float(s[1:]) - 1e-9)
        except ValueError:
            return None
    if "-" in s:
        left, right = s.split("-", 1)
        try:
            return Bracket(raw=raw, low_f=float(left), high_f=float(right))
        except ValueError:
            return None
    try:
        x = float(s)
        return Bracket(raw=raw, low_f=x, high_f=x)
    except ValueError:
        return None


def load_observed(path: Path, decision_hours: set[int]) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["decision_hour_local"].isin(decision_hours)].copy()
    keep = [
        "city",
        "icao",
        "target_date",
        "decision_hour_local",
        "running_max_f",
        "final_max_f",
        "residual_c",
        "floor_c_bucket_delta",
    ]
    return df[keep].copy()


def iter_snapshot_records(snapshot_dir: Path, decision_hours: set[int]) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        records = data.get("records") or []
        for r in records:
            ts_local = pd.to_datetime(r.get("ts_local"), errors="coerce")
            if pd.isna(ts_local):
                continue
            hour = int(ts_local.hour)
            if hour not in decision_hours:
                continue
            market_yes = pd.to_numeric(r.get("market_yes_price"), errors="coerce")
            if pd.isna(market_yes):
                continue
            bracket = parse_bracket(r.get("bracket"))
            if bracket is None:
                continue
            rows.append(
                {
                    "snapshot_file": str(path),
                    "snapshot_ts_utc": r.get("ts_utc") or data.get("ts_utc"),
                    "snapshot_ts_local": str(r.get("ts_local")),
                    "decision_hour_local": hour,
                    "city": r.get("city"),
                    "target_date": r.get("event_date"),
                    "icao": r.get("icao"),
                    "bracket": bracket.raw,
                    "bracket_low_f": bracket.low_f,
                    "bracket_high_f": bracket.high_f,
                    "market_yes_price": float(market_yes),
                    "condition_id": r.get("condition_id"),
                    "market_id": r.get("market_id"),
                }
            )
    return rows


def build_price_grid(rows: pd.DataFrame, min_price: float, max_price: float) -> pd.DataFrame:
    if rows.empty:
        return rows
    rows = rows[
        rows["market_yes_price"].between(min_price, max_price, inclusive="both")
        & rows["city"].notna()
        & rows["target_date"].notna()
        & rows["bracket"].notna()
    ].copy()
    rows["snapshot_ts_utc_sort"] = pd.to_datetime(rows["snapshot_ts_utc"], utc=True, errors="coerce")
    rows = rows.dropna(subset=["snapshot_ts_utc_sort"])
    rows = rows.sort_values("snapshot_ts_utc_sort")
    # Last seen quote within the local decision hour, one row per bracket.
    return (
        rows.groupby(["city", "target_date", "decision_hour_local", "bracket"], as_index=False)
        .tail(1)
        .drop(columns=["snapshot_ts_utc_sort"])
    )


def final_hit(row: pd.Series) -> int:
    low = row["bracket_low_f"]
    high = row["bracket_high_f"]
    final_f = row["final_max_f"]
    if pd.notna(low) and final_f < low:
        return 0
    if pd.notna(high) and final_f > high:
        return 0
    return 1


def run_backtest(observed: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    joined = prices.merge(
        observed,
        on=["city", "target_date", "decision_hour_local"],
        how="inner",
        suffixes=("", "_obs"),
    )
    if joined.empty:
        return joined, pd.DataFrame()
    joined["final_hit"] = joined.apply(final_hit, axis=1)
    joined["observed_bucket"] = joined.apply(
        lambda r: int(
            (pd.isna(r["bracket_low_f"]) or r["running_max_f"] >= r["bracket_low_f"])
            and (pd.isna(r["bracket_high_f"]) or r["running_max_f"] <= r["bracket_high_f"])
        ),
        axis=1,
    )
    joined["below_running_max"] = joined.apply(
        lambda r: int(pd.notna(r["bracket_high_f"]) and r["bracket_high_f"] < r["running_max_f"]),
        axis=1,
    )

    trades: list[pd.DataFrame] = []
    yes = joined[joined["observed_bucket"].eq(1)].copy()
    if not yes.empty:
        yes["strategy"] = "observed_bucket_buy_yes"
        yes["side"] = "BUY_YES"
        yes["entry_cost"] = yes["market_yes_price"]
        yes["payout"] = yes["final_hit"].astype(float)
        yes["pnl"] = yes["payout"] - yes["entry_cost"]
        trades.append(yes)

    no = joined[joined["below_running_max"].eq(1)].copy()
    if not no.empty:
        no["strategy"] = "below_running_max_buy_no"
        no["side"] = "BUY_NO"
        no["entry_cost"] = 1.0 - no["market_yes_price"]
        no["payout"] = 1.0 - no["final_hit"].astype(float)
        no["pnl"] = no["payout"] - no["entry_cost"]
        trades.append(no)

    if not trades:
        return joined, pd.DataFrame()
    trade_df = pd.concat(trades, ignore_index=True)
    trade_df["roi"] = trade_df["pnl"] / trade_df["entry_cost"]
    group_cols = ["strategy", "decision_hour_local"]
    summary = (
        trade_df.groupby(group_cols)
        .agg(
            trades=("pnl", "count"),
            city_days=("target_date", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            avg_price=("entry_cost", "mean"),
            win_rate=("payout", lambda s: float((s > 0).mean())),
        )
        .reset_index()
    )
    summary["roi"] = summary["pnl"] / summary["cost"]

    total = (
        trade_df.groupby(["strategy"])
        .agg(
            trades=("pnl", "count"),
            city_days=("target_date", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            avg_price=("entry_cost", "mean"),
            win_rate=("payout", lambda s: float((s > 0).mean())),
        )
        .reset_index()
    )
    total["decision_hour_local"] = "ALL"
    total["roi"] = total["pnl"] / total["cost"]
    summary = pd.concat([summary, total[summary.columns]], ignore_index=True)
    return joined, summary


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_hours = {int(x) for x in args.decision_hours.split(",") if x.strip()}

    observed = load_observed(Path(args.observed_detail), decision_hours)
    raw_rows = pd.DataFrame(iter_snapshot_records(Path(args.paper_snapshot_dir), decision_hours))
    prices = build_price_grid(raw_rows, args.min_market_yes_price, args.max_market_yes_price)
    joined, summary = run_backtest(observed, prices)

    raw_rows.to_csv(output_dir / "m3_paper_snapshot_proxy_raw_quotes.csv", index=False)
    prices.to_csv(output_dir / "m3_paper_snapshot_proxy_price_grid.csv", index=False)
    joined.to_csv(output_dir / "m3_paper_snapshot_proxy_joined_market.csv", index=False)
    if not summary.empty:
        summary.to_csv(output_dir / "m3_paper_snapshot_proxy_summary.csv", index=False)
    manifest = {
        "experiment": "m3_paper_snapshot_proxy_v0",
        "notes": [
            "Proxy only: uses paper snapshot market_yes_price, not orderbook best ask.",
            "Snapshot records may be strategy-candidate filtered and may not include full market universe.",
            "No spread/depth/fill probability is modeled.",
        ],
        "observed_detail": args.observed_detail,
        "paper_snapshot_dir": args.paper_snapshot_dir,
        "decision_hours": sorted(decision_hours),
        "raw_quote_rows": int(len(raw_rows)),
        "price_grid_rows": int(len(prices)),
        "joined_market_rows": int(len(joined)),
        "summary_rows": int(len(summary)),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if not summary.empty:
        print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
