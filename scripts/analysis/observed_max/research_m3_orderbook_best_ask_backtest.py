#!/usr/bin/env python3
"""M3 orderbook best-ask backtest.

This joins observed running-max rows to historical orderbook snapshots and uses
token-side best ask as entry cost. It is a historical executable-price proxy,
not a fill simulator and not live PnL.
"""

from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from research_m3_observed_max_residual import CITY_TIMEZONE
from research_m3_paper_snapshot_proxy_backtest import Bracket, parse_bracket


DEFAULT_HOURS = (20, 21)


@dataclass(frozen=True)
class BestAsk:
    price: float
    size: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observed-detail",
        default="docs/analysis/2026-06/generated/m3_observed_max_v1/m3_observed_max_residual_detail.csv",
    )
    parser.add_argument(
        "--orderbook-dir",
        default="runtime/weather_edge_v1/market_data/orderbook_snapshots",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_orderbook_best_ask_v0",
    )
    parser.add_argument("--decision-hours", default=",".join(str(x) for x in DEFAULT_HOURS))
    parser.add_argument("--min-best-ask", type=float, default=0.005)
    parser.add_argument("--max-best-ask", type=float, default=0.995)
    return parser.parse_args()


def best_ask(raw: object) -> BestAsk | None:
    if not isinstance(raw, dict):
        return None
    asks = raw.get("asks")
    if not isinstance(asks, list) or not asks:
        return None
    candidates: list[BestAsk] = []
    for ask in asks:
        if not isinstance(ask, dict):
            continue
        try:
            price = float(ask.get("price"))
        except (TypeError, ValueError):
            continue
        size_value = ask.get("size")
        try:
            size = float(size_value) if size_value is not None else None
        except (TypeError, ValueError):
            size = None
        candidates.append(BestAsk(price=price, size=size))
    if not candidates:
        return None
    return min(candidates, key=lambda x: x.price)


def load_observed(path: Path, decision_hours: set[int]) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["decision_hour_local"].isin(decision_hours)].copy()
    keep = [
        "city",
        "icao",
        "target_date",
        "decision_hour_local",
        "running_max_c",
        "final_max_c",
        "residual_c",
        "floor_c_bucket_delta",
    ]
    return df[keep].copy()


def iter_orderbook_rows(
    orderbook_dir: Path,
    decision_hours: set[int],
    min_best_ask: float,
    max_best_ask: float,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    files_seen = 0
    records_seen = 0
    records_missing_tz = 0
    records_wrong_hour_or_day = 0
    records_no_best_ask = 0
    records_bad_bracket = 0

    for path in sorted(orderbook_dir.glob("*/*.jsonl.gz")):
        files_seen += 1
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                records_seen += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                city = record.get("city")
                timezone = CITY_TIMEZONE.get(str(city))
                if not timezone:
                    records_missing_tz += 1
                    continue
                snapshot_ts = pd.to_datetime(record.get("snapshot_ts_utc"), utc=True, errors="coerce")
                if pd.isna(snapshot_ts):
                    continue
                local_ts = snapshot_ts.tz_convert(ZoneInfo(timezone))
                event_date = str(record.get("event_date") or "")
                local_date = local_ts.date().isoformat()
                local_hour = int(local_ts.hour)
                if local_date != event_date or local_hour not in decision_hours:
                    records_wrong_hour_or_day += 1
                    continue

                bracket = parse_bracket(record.get("bracket"))
                if bracket is None:
                    records_bad_bracket += 1
                    continue
                ask = best_ask(record.get("raw"))
                if ask is None or not (min_best_ask <= ask.price <= max_best_ask):
                    records_no_best_ask += 1
                    continue
                outcome = str(record.get("outcome") or "").lower()
                if outcome not in {"yes", "no"}:
                    continue
                rows.append(
                    {
                        "orderbook_file": str(path),
                        "snapshot_ts_utc": snapshot_ts.isoformat(),
                        "snapshot_ts_local": local_ts.isoformat(),
                        "decision_hour_local": local_hour,
                        "city": city,
                        "target_date": event_date,
                        "bracket": bracket.raw,
                        "bracket_low_c": bracket.low_f,
                        "bracket_high_c": bracket.high_f,
                        "outcome": outcome,
                        "best_ask": ask.price,
                        "best_ask_size": ask.size,
                        "condition_id": record.get("condition_id"),
                        "market_id": record.get("market_id"),
                        "token_id": record.get("token_id"),
                    }
                )

    meta = {
        "orderbook_files_seen": files_seen,
        "orderbook_records_seen": records_seen,
        "records_missing_tz": records_missing_tz,
        "records_wrong_hour_or_day": records_wrong_hour_or_day,
        "records_no_best_ask_or_outside_price_band": records_no_best_ask,
        "records_bad_bracket": records_bad_bracket,
        "orderbook_rows_kept": len(rows),
    }
    return pd.DataFrame(rows), meta


def last_quote_per_hour(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    rows = rows.copy()
    rows["snapshot_sort"] = pd.to_datetime(rows["snapshot_ts_utc"], utc=True, errors="coerce")
    rows = rows.dropna(subset=["snapshot_sort"])
    rows = rows.sort_values("snapshot_sort")
    key = ["city", "target_date", "decision_hour_local", "bracket", "outcome"]
    return rows.groupby(key, as_index=False).tail(1).drop(columns=["snapshot_sort"])


def bracket_contains(row: pd.Series, temp_c: float) -> bool:
    low = row["bracket_low_c"]
    high = row["bracket_high_c"]
    if pd.notna(low) and temp_c < low:
        return False
    if pd.notna(high) and temp_c > high:
        return False
    return True


def bracket_below_temp(row: pd.Series, temp_c: float) -> bool:
    high = row["bracket_high_c"]
    return bool(pd.notna(high) and high < temp_c)


def final_hit(row: pd.Series) -> int:
    return int(bracket_contains(row, float(row["final_max_c"])))


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    group_cols = ["strategy", "decision_hour_local"]
    summary = (
        trades.groupby(group_cols)
        .agg(
            trades=("pnl", "count"),
            city_days=("city_day_key", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            avg_entry_cost=("entry_cost", "mean"),
            median_entry_cost=("entry_cost", "median"),
            median_best_ask_size=("best_ask_size", "median"),
            win_rate=("payout", lambda s: float((s > 0).mean())),
        )
        .reset_index()
    )
    summary["roi"] = summary["pnl"] / summary["cost"]

    total = (
        trades.groupby("strategy")
        .agg(
            trades=("pnl", "count"),
            city_days=("city_day_key", "nunique"),
            cities=("city", "nunique"),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            avg_entry_cost=("entry_cost", "mean"),
            median_entry_cost=("entry_cost", "median"),
            median_best_ask_size=("best_ask_size", "median"),
            win_rate=("payout", lambda s: float((s > 0).mean())),
        )
        .reset_index()
    )
    total["decision_hour_local"] = "ALL"
    total["roi"] = total["pnl"] / total["cost"]
    return pd.concat([summary, total[summary.columns]], ignore_index=True)


def run_backtest(observed: pd.DataFrame, quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    joined = quotes.merge(
        observed,
        on=["city", "target_date", "decision_hour_local"],
        how="inner",
        suffixes=("", "_obs"),
    )
    if joined.empty:
        return joined, pd.DataFrame(), pd.DataFrame()

    joined["final_hit"] = joined.apply(final_hit, axis=1)
    joined["observed_bucket"] = joined.apply(
        lambda r: int(bracket_contains(r, float(r["running_max_c"]))),
        axis=1,
    )
    joined["below_running_max"] = joined.apply(
        lambda r: int(bracket_below_temp(r, float(r["running_max_c"]))),
        axis=1,
    )

    trades: list[pd.DataFrame] = []
    yes = joined[(joined["outcome"].eq("yes")) & (joined["observed_bucket"].eq(1))].copy()
    if not yes.empty:
        yes["strategy"] = "observed_bucket_buy_yes"
        yes["side"] = "BUY_YES"
        yes["entry_cost"] = yes["best_ask"]
        yes["payout"] = yes["final_hit"].astype(float)
        yes["pnl"] = yes["payout"] - yes["entry_cost"]
        trades.append(yes)

    no = joined[(joined["outcome"].eq("no")) & (joined["below_running_max"].eq(1))].copy()
    if not no.empty:
        no["strategy"] = "below_running_max_buy_no"
        no["side"] = "BUY_NO"
        no["entry_cost"] = no["best_ask"]
        no["payout"] = 1.0 - no["final_hit"].astype(float)
        no["pnl"] = no["payout"] - no["entry_cost"]
        trades.append(no)

    if not trades:
        return joined, pd.DataFrame(), pd.DataFrame()
    trade_df = pd.concat(trades, ignore_index=True)
    trade_df["roi"] = trade_df["pnl"] / trade_df["entry_cost"]
    trade_df["city_day_key"] = trade_df["city"].astype(str) + "|" + trade_df["target_date"].astype(str)
    summary = summarize(trade_df)
    return joined, trade_df, summary


def main() -> int:
    args = parse_args()
    decision_hours = {int(x) for x in args.decision_hours.split(",") if x.strip()}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    observed = load_observed(Path(args.observed_detail), decision_hours)
    orderbook_rows, orderbook_meta = iter_orderbook_rows(
        Path(args.orderbook_dir),
        decision_hours,
        args.min_best_ask,
        args.max_best_ask,
    )
    quotes = last_quote_per_hour(orderbook_rows)
    joined, trades, summary = run_backtest(observed, quotes)

    quotes_path = output_dir / "m3_orderbook_best_ask_quotes.csv"
    joined_path = output_dir / "m3_orderbook_best_ask_joined.csv"
    trades_path = output_dir / "m3_orderbook_best_ask_trades.csv"
    summary_path = output_dir / "m3_orderbook_best_ask_summary.csv"
    manifest_path = output_dir / "manifest.json"

    quotes.to_csv(quotes_path, index=False)
    joined.to_csv(joined_path, index=False)
    trades.to_csv(trades_path, index=False)
    summary.to_csv(summary_path, index=False)

    manifest = {
        "experiment": "m3_orderbook_best_ask_v0",
        "observed_detail": args.observed_detail,
        "orderbook_dir": args.orderbook_dir,
        "output_dir": str(output_dir),
        "decision_hours": sorted(decision_hours),
        "min_best_ask": args.min_best_ask,
        "max_best_ask": args.max_best_ask,
        "observed_rows": int(len(observed)),
        "quote_rows": int(len(quotes)),
        "joined_rows": int(len(joined)),
        "trade_rows": int(len(trades)),
        "summary_rows": int(len(summary)),
        **orderbook_meta,
        "outputs": {
            "quotes": str(quotes_path),
            "joined": str(joined_path),
            "trades": str(trades_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Uses orderbook token-side raw.asks best ask; no paper market_yes_price proxy.",
            "Interprets Polymarket weather bracket labels as Celsius and compares to observed *_max_c.",
            "Keeps only target-date snapshots whose city-local hour is in decision_hours.",
            "This does not model queue position, partial fills, fees, or live execution latency.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
