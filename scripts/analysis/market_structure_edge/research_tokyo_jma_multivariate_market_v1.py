#!/usr/bin/env python3
"""Join frozen Tokyo JMA event probabilities to archived exact-bracket books.

The join uses collector-exact first-seen timestamps when a hash-verified row
exists.  Older archive-only JMA rows receive a conservative +15 minute
availability clock and remain explicitly classified as reconstructed, never
as exact first-seen.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PREDICTIONS = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v2"
    / "predictions.csv.gz"
)
DEFAULT_EXACT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_feature_timestamp_audit_v1"
    / "tokyo_jma_exact_enriched.csv"
)
DEFAULT_BOOKS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/full_ladder_output/"
    "orderbook_snapshots"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_market_v1"
)
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
FEE_RATE = 0.05
SHARES = 5.0


def parse_ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def finite(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def read_csv(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def exact_first_seen(path: Path) -> dict[str, datetime]:
    mapping: dict[str, datetime] = {}
    for row in read_csv(path):
        if str(row.get("hash_verified_against_jma_point_archive")) != "1":
            continue
        observed = parse_ts(row["observation_time_utc"]).isoformat()
        available = parse_ts(row["source_first_seen_at_utc"])
        current = mapping.get(observed)
        if current is None or available < current:
            mapping[observed] = available
    return mapping


def load_books(
    root: Path, requests: list[tuple[str, datetime]]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by_snapshot: dict[
        tuple[str, str, datetime], dict[str, dict[str, Any]]
    ] = defaultdict(dict)
    selected_paths: set[Path] = set()
    by_day: dict[str, list[datetime]] = defaultdict(list)
    for day, available in requests:
        by_day[day].append(available)
    for day, available_times in by_day.items():
        candidates: list[tuple[datetime, Path]] = []
        for path in (root / day).glob("orderbook_snapshot_*.jsonl.gz"):
            try:
                stamp = datetime.strptime(
                    path.name.removeprefix("orderbook_snapshot_").removesuffix(
                        ".jsonl.gz"
                    ),
                    "%Y%m%d_%H%M",
                ).replace(tzinfo=BEIJING).astimezone(UTC)
            except ValueError:
                continue
            candidates.append((stamp, path))
        candidates.sort()
        stamps = [stamp for stamp, _ in candidates]
        for available in available_times:
            index = bisect_left(stamps, available)
            if index < len(candidates) and stamps[index] <= available + timedelta(
                minutes=45
            ):
                selected_paths.add(candidates[index][1])
    for path in sorted(selected_paths):
        day = path.parent.name
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    row.get("city") != "Tokyo"
                    or str(row.get("event_date")) != day
                    or row.get("status") != "ok"
                ):
                    continue
                bracket = str(row.get("bracket"))
                observed = parse_ts(row["snapshot_ts_utc"])
                by_snapshot[(day, bracket, observed)][
                    str(row.get("outcome"))
                ] = row
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (day, bracket, observed), sides in by_snapshot.items():
        yes = sides.get("yes", {}).get("summary", {})
        no = sides.get("no", {}).get("summary", {})
        yes_ask = finite(yes.get("best_ask"))
        yes_bid = finite(yes.get("best_bid"))
        no_ask = finite(no.get("best_ask"))
        no_bid = finite(no.get("best_bid"))
        if yes_ask is None and no_bid is not None:
            yes_ask = 1 - no_bid
        if yes_bid is None and no_ask is not None:
            yes_bid = 1 - no_ask
        if no_ask is None and yes_bid is not None:
            no_ask = 1 - yes_bid
        if no_bid is None and yes_ask is not None:
            no_bid = 1 - yes_ask
        no_mid = (
            (no_bid + no_ask) / 2
            if no_bid is not None and no_ask is not None
            else no_ask
            if no_ask is not None
            else no_bid
        )
        no_size = finite(no.get("ask_size"))
        if no_size is None:
            no_size = finite(yes.get("bid_size"))
        output[(day, bracket)].append(
            {
                "snapshot_ts_utc": observed,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_mid": no_mid,
                "no_ask_size": no_size,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
            }
        )
    for rows in output.values():
        rows.sort(key=lambda row: row["snapshot_ts_utc"])
    return output


def load_winners(db_path: Path, start_date: str, end_date: str) -> dict[str, str]:
    connection = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2.0
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        rows = connection.execute(
            """
            SELECT target_date, bracket
            FROM settlement_outcomes
            WHERE target_date >= ? AND target_date <= ?
              AND city = 'Tokyo'
              AND settlement_status = 'settled'
              AND final_price = 1.0
            ORDER BY target_date
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        connection.close()
    return {str(target_date): str(bracket) for target_date, bracket in rows}


def quote_after(
    rows: list[dict[str, Any]], available: datetime
) -> dict[str, Any] | None:
    timestamps = [row["snapshot_ts_utc"] for row in rows]
    index = bisect_left(timestamps, available)
    if index >= len(rows):
        return None
    quote = rows[index]
    if quote["snapshot_ts_utc"] > available + timedelta(minutes=45):
        return None
    return quote


def date_bootstrap_brier_delta(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    daily: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        y = int(row["settlement_no_wins"])
        daily[str(row["target_date"])].append(
            (float(row["p_model_no"]) - y) ** 2
            - (float(row["p_market_no"]) - y) ** 2
        )
    values = np.asarray([np.mean(group) for group in daily.values()])
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(np.mean(rng.choice(values, len(values), replace=True)))
            for _ in range(5000)
        ]
    )
    return (
        float(np.mean(values)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def date_equal_brier(rows: list[dict[str, Any]], field: str) -> float:
    daily: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        y = int(row["settlement_no_wins"])
        daily[str(row["target_date"])].append((float(row[field]) - y) ** 2)
    return float(np.mean([np.mean(values) for values in daily.values()]))


def roi_bootstrap(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not rows:
        return math.nan, math.nan, math.nan
    daily = {
        str(row["target_date"]): (
            float(row["fee_adjusted_pnl_usd"]),
            float(row["entry_cost_usd"]),
        )
        for row in rows
    }
    dates = list(daily)
    pnl = sum(value[0] for value in daily.values())
    cost = sum(value[1] for value in daily.values())
    rng = np.random.default_rng(20260731)
    draws = []
    for _ in range(5000):
        selected = rng.choice(dates, len(dates), replace=True)
        draw_pnl = sum(daily[str(day)][0] for day in selected)
        draw_cost = sum(daily[str(day)][1] for day in selected)
        draws.append(draw_pnl / draw_cost if draw_cost else math.nan)
    return (
        pnl / cost if cost else math.nan,
        float(np.nanquantile(draws, 0.025)),
        float(np.nanquantile(draws, 0.975)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--exact-first-seen", type=Path, default=DEFAULT_EXACT)
    parser.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-07-30")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    raw_predictions = [
        row
        for row in read_csv(args.predictions)
        if row.get("target_id") == "final_break"
        and row.get("split") == "post_audit_replay"
        and args.start_date <= str(row["target_date"]) <= args.end_date
    ]
    exact = exact_first_seen(args.exact_first_seen)
    availability_requests: list[tuple[str, datetime]] = []
    for row in raw_predictions:
        observed = parse_ts(row["decision_ts_utc"])
        availability_requests.append(
            (
                str(row["target_date"]),
                exact.get(observed.isoformat(), observed + timedelta(minutes=15)),
            )
        )
    books = load_books(args.books, availability_requests)
    winners = load_winners(args.db, args.start_date, args.end_date)

    joined: list[dict[str, Any]] = []
    seen_exact = 0
    for row in raw_predictions:
        observed = parse_ts(row["decision_ts_utc"])
        exact_available = exact.get(observed.isoformat())
        if exact_available is not None:
            available = exact_available
            clock_class = "collector_exact_hash_verified"
            seen_exact += 1
        else:
            available = observed + timedelta(minutes=15)
            clock_class = "archive_reconstructed_plus_15m"
        bracket = str(int(float(row["prior_bracket"])))
        quote = quote_after(books.get((str(row["target_date"]), bracket), []), available)
        winner = winners.get(str(row["target_date"]))
        record = dict(row)
        record.update(
            {
                "availability_ts_utc": available.isoformat(),
                "availability_clock_class": clock_class,
                "market_bracket": bracket,
                "winning_bracket": winner,
                "settlement_no_wins": (
                    int(winner != bracket) if winner is not None else None
                ),
                "book_covered": int(quote is not None),
            }
        )
        if quote is not None:
            record.update(
                {
                    "snapshot_ts_utc": quote["snapshot_ts_utc"].isoformat(),
                    "availability_to_book_min": (
                        quote["snapshot_ts_utc"] - available
                    ).total_seconds()
                    / 60,
                    "p_model_no": float(row["p_model"]),
                    "p_market_no": quote["no_mid"],
                    "no_bid": quote["no_bid"],
                    "no_ask": quote["no_ask"],
                    "no_ask_size": quote["no_ask_size"],
                    "fee_per_share": (
                        FEE_RATE * quote["no_ask"] * (1 - quote["no_ask"])
                        if quote["no_ask"] is not None
                        else None
                    ),
                }
            )
            if quote["no_ask"] is not None:
                fee = FEE_RATE * quote["no_ask"] * (1 - quote["no_ask"])
                record["fee_adjusted_edge"] = (
                    float(row["p_model"]) - quote["no_ask"] - fee
                )
                record["five_share_executable"] = int(
                    quote["no_ask_size"] is not None
                    and quote["no_ask_size"] >= SHARES
                )
        joined.append(record)

    scored = [
        row
        for row in joined
        if row.get("settlement_no_wins") is not None
        and finite(row.get("p_market_no")) is not None
    ]
    delta, delta_low, delta_high = (
        date_bootstrap_brier_delta(scored)
        if scored
        else (math.nan, math.nan, math.nan)
    )
    model_brier = date_equal_brier(scored, "p_model_no") if scored else math.nan
    market_brier = date_equal_brier(scored, "p_market_no") if scored else math.nan
    score_slices: list[dict[str, Any]] = []
    for slice_name, selected in (
        ("all", scored),
        (
            "collector_exact_hash_verified",
            [
                row
                for row in scored
                if row["availability_clock_class"]
                == "collector_exact_hash_verified"
            ],
        ),
        (
            "archive_reconstructed_plus_15m",
            [
                row
                for row in scored
                if row["availability_clock_class"]
                == "archive_reconstructed_plus_15m"
            ],
        ),
    ):
        if not selected:
            continue
        slice_delta, slice_low, slice_high = date_bootstrap_brier_delta(selected)
        score_slices.append(
            {
                "slice": slice_name,
                "events": len(selected),
                "target_dates": len(
                    {str(row["target_date"]) for row in selected}
                ),
                "model_brier": date_equal_brier(selected, "p_model_no"),
                "market_brier": date_equal_brier(selected, "p_market_no"),
                "model_minus_market_brier": slice_delta,
                "date_bootstrap_ci_low": slice_low,
                "date_bootstrap_ci_high": slice_high,
            }
        )

    candidates = [
        row
        for row in joined
        if row.get("settlement_no_wins") is not None
        and row.get("five_share_executable") == 1
        and finite(row.get("fee_adjusted_edge")) is not None
        and float(row["fee_adjusted_edge"]) > 0
    ]
    first_by_date: dict[str, dict[str, Any]] = {}
    for row in sorted(candidates, key=lambda item: str(item["availability_ts_utc"])):
        first_by_date.setdefault(str(row["target_date"]), row)
    trades: list[dict[str, Any]] = []
    for row in first_by_date.values():
        trade = dict(row)
        ask = float(row["no_ask"])
        fee = FEE_RATE * ask * (1 - ask)
        payout = SHARES * int(row["settlement_no_wins"])
        cost = SHARES * (ask + fee)
        trade.update(
            {
                "shares": SHARES,
                "entry_cost_usd": cost,
                "gross_payout_usd": payout,
                "fee_adjusted_pnl_usd": payout - cost,
                "trade_result": "correct" if payout > 0 else "wrong",
                "replay_class": "counterfactual_frozen_zero_notional",
            }
        )
        trades.append(trade)
    roi, roi_low, roi_high = roi_bootstrap(trades)

    cases = sorted(
        trades,
        key=lambda row: (
            int(row["trade_result"] == "wrong"),
            abs(float(row["fee_adjusted_pnl_usd"])),
        ),
        reverse=True,
    )
    if len(cases) < 8:
        extra = sorted(
            scored,
            key=lambda row: abs(
                float(row["p_model_no"]) - int(row["settlement_no_wins"])
            ),
            reverse=True,
        )
        used = {str(row["event_id"]) for row in cases}
        cases.extend(row for row in extra if str(row["event_id"]) not in used)
    cases = cases[:12]

    funnel = [
        {"funnel": "signal", "stage": "first_cross_events", "unit": "event", "count": len(raw_predictions)},
        {"funnel": "signal", "stage": "positive_fee_adjusted_edge", "unit": "event", "count": len(candidates)},
        {"funnel": "signal", "stage": "first_city_day_trade", "unit": "city-day", "count": len(trades)},
        {"funnel": "evidence", "stage": "collector_exact_clock", "unit": "event", "count": seen_exact},
        {"funnel": "evidence", "stage": "book_within_45m", "unit": "event", "count": sum(int(row["book_covered"]) for row in joined)},
        {"funnel": "evidence", "stage": "same_row_settled_score", "unit": "event", "count": len(scored)},
        {"funnel": "evidence", "stage": "settled_executable_trade", "unit": "city-day", "count": len(trades)},
    ]
    write_csv(args.out / "market_join_rows.csv", joined)
    write_csv(args.out / "counterfactual_trades.csv", trades)
    write_csv(args.out / "casebook.csv", cases)
    write_csv(args.out / "funnel.csv", funnel)
    write_csv(args.out / "score_slices.csv", score_slices)
    summary = {
        "schema_version": "tokyo_jma_multivariate_market_v1",
        "window": {"start": args.start_date, "end": args.end_date},
        "clock_policy": {
            "exact": "collector exact + hash verified where available",
            "archive": "observation clock + 15 minutes; never exact first-seen",
        },
        "events": len(raw_predictions),
        "exact_clock_events": seen_exact,
        "book_covered_events": sum(int(row["book_covered"]) for row in joined),
        "settled_same_row_scores": len(scored),
        "settled_target_dates": len(
            {str(row["target_date"]) for row in scored}
        ),
        "model_brier": model_brier,
        "market_brier": market_brier,
        "model_minus_market_brier": delta,
        "model_minus_market_brier_ci": [delta_low, delta_high],
        "counterfactual_trades": len(trades),
        "trade_wins": sum(int(row["trade_result"] == "correct") for row in trades),
        "fee_adjusted_pnl_usd": sum(
            float(row["fee_adjusted_pnl_usd"]) for row in trades
        ),
        "fee_adjusted_roi": roi,
        "fee_adjusted_roi_ci": [roi_low, roi_high],
        "fee_formula": "shares * 0.05 * ask * (1-ask)",
        "live_behavior_changed": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
