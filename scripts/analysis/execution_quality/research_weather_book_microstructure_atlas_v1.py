#!/usr/bin/env python3
"""Build a PIT weather-market order-book microstructure atlas.

Grain:
    one complete mutually-exclusive event ladder at one archived snapshot.

The atlas is descriptive execution research.  It does not infer maker fills,
does not treat displayed depth as traded volume, and does not produce a live
selector.  Weather observations are joined strictly as-of their fetched time.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from weather_data_feed.city_calendar import city_local_datetime


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalized_city(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def iter_jsonl_gz(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def load_observations(
    root: Path, start_date: str | None, end_date: str | None
) -> dict[tuple[str, str], tuple[list[datetime], list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = (
        defaultdict(list)
    )
    for path in sorted(root.glob("*/observations.jsonl")):
        folder_date = path.parent.name
        if start_date and folder_date < start_date:
            continue
        if end_date and folder_date > end_date:
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") != "ok":
                    continue
                fetched = row.get("fetched_at_utc")
                target_date = str(row.get("target_date") or "")
                city = normalized_city(row.get("city"))
                if not fetched or not target_date or not city:
                    continue
                grouped[(city, target_date)].append((utc(str(fetched)), row))
    output = {}
    for key, values in grouped.items():
        values.sort(key=lambda item: item[0])
        output[key] = (
            [item[0] for item in values],
            [item[1] for item in values],
        )
    return output


def align_observation(
    index: dict[tuple[str, str], tuple[list[datetime], list[dict[str, Any]]]],
    city: str,
    target_date: str,
    snapshot: datetime,
) -> dict[str, Any] | None:
    values = index.get((normalized_city(city), target_date))
    if not values:
        return None
    timestamps, rows = values
    position = bisect_right(timestamps, snapshot) - 1
    return rows[position] if position >= 0 else None


def effective_yes_book(
    yes: dict[str, Any] | None, no: dict[str, Any] | None
) -> dict[str, float | str | None]:
    yes_summary = (yes or {}).get("summary") or {}
    no_summary = (no or {}).get("summary") or {}
    direct_bid = finite(yes_summary.get("best_bid"))
    direct_ask = finite(yes_summary.get("best_ask"))
    no_bid = finite(no_summary.get("best_bid"))
    no_ask = finite(no_summary.get("best_ask"))
    complement_bid = 1.0 - no_ask if no_ask is not None else None
    complement_ask = 1.0 - no_bid if no_bid is not None else None

    bid_candidates = [
        ("direct_yes", direct_bid, finite(yes_summary.get("bid_size"))),
        ("complement_no", complement_bid, finite(no_summary.get("ask_size"))),
    ]
    ask_candidates = [
        ("direct_yes", direct_ask, finite(yes_summary.get("ask_size"))),
        ("complement_no", complement_ask, finite(no_summary.get("bid_size"))),
    ]
    bid_candidates = [item for item in bid_candidates if item[1] is not None]
    ask_candidates = [item for item in ask_candidates if item[1] is not None]
    best_bid = max(bid_candidates, key=lambda item: item[1]) if bid_candidates else None
    best_ask = min(ask_candidates, key=lambda item: item[1]) if ask_candidates else None
    bid = best_bid[1] if best_bid else None
    ask = best_ask[1] if best_ask else None
    spread = ask - bid if ask is not None and bid is not None else None
    midpoint = (ask + bid) / 2 if spread is not None and spread >= -1e-9 else None
    return {
        "best_bid": bid,
        "best_ask": ask,
        "bid_size": best_bid[2] if best_bid else None,
        "ask_size": best_ask[2] if best_ask else None,
        "bid_source": best_bid[0] if best_bid else None,
        "ask_source": best_ask[0] if best_ask else None,
        "spread": spread,
        "mid": midpoint,
    }


def weather_state(row: dict[str, Any] | None) -> str:
    if not row:
        return "missing"
    decline = finite(row.get("decline_c")) or 0.0
    delta_1h = finite(row.get("d_tmpf_1h"))
    minutes_since_max = finite(row.get("minutes_since_running_max"))
    if decline >= 0.5:
        return "pullback"
    if delta_1h is not None and delta_1h >= 0.9:
        return "fresh_runway" if (minutes_since_max or 0.0) <= 45 else "warming"
    if delta_1h is not None and delta_1h <= -0.9:
        return "cooling"
    return "plateau"


def entropy(values: list[float]) -> float | None:
    positive = np.asarray([max(0.0, value) for value in values], dtype=float)
    total = float(positive.sum())
    if total <= 0 or len(positive) <= 1:
        return None
    probabilities = positive / total
    probabilities = probabilities[probabilities > 0]
    return float(-(probabilities * np.log(probabilities)).sum() / math.log(len(positive)))


def summarize_snapshot(rows: list[dict[str, Any]], observation_index: dict) -> dict[str, Any]:
    first = rows[0]
    snapshot = utc(str(first["snapshot_ts_utc"]))
    city = str(first.get("city") or "")
    target_date = str(
        first.get("market_local_date")
        or first.get("event_date")
        or first.get("city_local_date_at_snapshot")
        or ""
    )
    by_condition: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row.get("status") != "ok":
            continue
        condition = str(row.get("condition_id") or "")
        outcome = str(row.get("outcome") or "").lower()
        if condition and outcome in {"yes", "no"}:
            by_condition[condition][outcome] = row

    brackets = []
    for condition, outcomes in by_condition.items():
        yes = outcomes.get("yes")
        no = outcomes.get("no")
        source = yes or no
        if not source:
            continue
        quote = effective_yes_book(yes, no)
        brackets.append(
            {
                "condition_id": condition,
                "bracket": str(source.get("bracket") or ""),
                **quote,
            }
        )

    bids = [float(row["best_bid"]) for row in brackets if row["best_bid"] is not None]
    asks = [float(row["best_ask"]) for row in brackets if row["best_ask"] is not None]
    mids = [float(row["mid"]) for row in brackets if row["mid"] is not None]
    spreads = [
        float(row["spread"])
        for row in brackets
        if row["spread"] is not None and float(row["spread"]) >= -1e-9
    ]
    ask_depth_usd = [
        float(row["best_ask"]) * float(row["ask_size"])
        for row in brackets
        if row["best_ask"] is not None and row["ask_size"] is not None
    ]
    bid_depth_usd = [
        float(row["best_bid"]) * float(row["bid_size"])
        for row in brackets
        if row["best_bid"] is not None and row["bid_size"] is not None
    ]
    favorite = max(
        (row for row in brackets if row["mid"] is not None),
        key=lambda row: float(row["mid"]),
        default=None,
    )
    ranked = sorted(
        (row for row in brackets if row["mid"] is not None),
        key=lambda row: float(row["mid"]),
        reverse=True,
    )
    center_two = ranked[:2]

    obs = align_observation(observation_index, city, target_date, snapshot)
    local = city_local_datetime(city, snapshot)
    obs_fetched = utc(str(obs["fetched_at_utc"])) if obs else None
    return {
        "snapshot_ts_utc": snapshot.isoformat(),
        "event_slug": str(first.get("event_slug") or ""),
        "city": city,
        "target_date": target_date,
        "local_datetime": local.isoformat(),
        "local_hour": local.hour + local.minute / 60 + local.second / 3600,
        "local_hour_band": (
            "00-06"
            if local.hour < 6
            else "06-10"
            if local.hour < 10
            else "10-12"
            if local.hour < 12
            else "12-14"
            if local.hour < 14
            else "14-16"
            if local.hour < 16
            else "16-18"
            if local.hour < 18
            else "18-24"
        ),
        "bracket_count": len(brackets),
        "two_sided_quote_share": len(spreads) / len(brackets) if brackets else None,
        "all_ask_quoted": len(asks) == len(brackets) and bool(brackets),
        "all_bid_quoted": len(bids) == len(brackets) and bool(brackets),
        "yes_ask_sum": sum(asks) if len(asks) == len(brackets) and brackets else None,
        "yes_bid_sum": sum(bids) if len(bids) == len(brackets) and brackets else None,
        "yes_mid_sum": sum(mids) if len(mids) == len(brackets) and brackets else None,
        "median_spread": float(np.median(spreads)) if spreads else None,
        "p90_spread": float(np.quantile(spreads, 0.9)) if spreads else None,
        "total_top_ask_depth_usd": sum(ask_depth_usd),
        "median_top_ask_depth_usd": (
            float(np.median(ask_depth_usd)) if ask_depth_usd else None
        ),
        "total_top_bid_depth_usd": sum(bid_depth_usd),
        "favorite_bracket": favorite["bracket"] if favorite else None,
        "favorite_mid": favorite["mid"] if favorite else None,
        "favorite_spread": favorite["spread"] if favorite else None,
        "favorite_ask_depth_usd": (
            float(favorite["best_ask"]) * float(favorite["ask_size"])
            if favorite
            and favorite["best_ask"] is not None
            and favorite["ask_size"] is not None
            else None
        ),
        "center_two_mid_mass": (
            sum(float(row["mid"]) for row in center_two)
            if len(center_two) == 2
            else None
        ),
        "center_two_ask_sum": (
            sum(float(row["best_ask"]) for row in center_two)
            if len(center_two) == 2
            and all(row["best_ask"] is not None for row in center_two)
            else None
        ),
        "normalized_mid_entropy": entropy(mids) if len(mids) == len(brackets) else None,
        "observation_available": obs is not None,
        "observation_age_at_book_min": (
            (snapshot - obs_fetched).total_seconds() / 60 if obs_fetched else None
        ),
        "weather_state": weather_state(obs),
        "source": obs.get("source") if obs else None,
        "station": obs.get("station") if obs else None,
        "current_temp_c": finite(obs.get("current_temp_c")) if obs else None,
        "running_max_c": finite(obs.get("running_max_c")) if obs else None,
        "decline_c": finite(obs.get("decline_c")) if obs else None,
        "d_tmpf_1h": finite(obs.get("d_tmpf_1h")) if obs else None,
        "d_tmpf_3h": finite(obs.get("d_tmpf_3h")) if obs else None,
        "minutes_since_running_max": (
            finite(obs.get("minutes_since_running_max")) if obs else None
        ),
        "sky_cover_code": obs.get("sky_cover_code") if obs else None,
        "precip_state": obs.get("precip_state") if obs else None,
        "wind_speed_kt": finite(obs.get("wind_speed_kt")) if obs else None,
        "dewpoint_depression_f": (
            finite(obs.get("dewpoint_depression_f")) if obs else None
        ),
    }


def grouped_summary(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(columns, dropna=False)
        .agg(
            snapshots=("event_slug", "size"),
            events=("event_slug", "nunique"),
            target_dates=("target_date", "nunique"),
            observation_coverage=("observation_available", "mean"),
            two_sided_quote_share=("two_sided_quote_share", "median"),
            median_spread=("median_spread", "median"),
            p90_spread=("p90_spread", "median"),
            median_favorite_spread=("favorite_spread", "median"),
            median_top_ask_depth_usd=("median_top_ask_depth_usd", "median"),
            median_favorite_ask_depth_usd=("favorite_ask_depth_usd", "median"),
            median_center_two_ask_sum=("center_two_ask_sum", "median"),
            median_mid_entropy=("normalized_mid_entropy", "median"),
            favorite_change_rate=("favorite_changed", "mean"),
            median_favorite_mid_abs_change=("favorite_mid_abs_change", "median"),
        )
        .reset_index()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orderbook-root", type=Path, required=True)
    parser.add_argument("--observation-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()

    paths = []
    for path in sorted(args.orderbook_root.glob("*/*.jsonl.gz")):
        folder_date = path.parent.name
        if args.start_date and folder_date < args.start_date:
            continue
        if args.end_date and folder_date > args.end_date:
            continue
        paths.append(path)
    if not paths:
        raise SystemExit("no orderbook files selected")

    observation_index = load_observations(
        args.observation_root, args.start_date, args.end_date
    )
    raw_rows = 0
    rows: list[dict[str, Any]] = []
    for path in paths:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in iter_jsonl_gz([path]):
            snapshot = str(row.get("snapshot_ts_utc") or "")
            event_slug = str(row.get("event_slug") or "")
            if snapshot and event_slug:
                grouped[(snapshot, event_slug)].append(row)
                raw_rows += 1
        rows.extend(
            summarize_snapshot(snapshot_rows, observation_index)
            for snapshot_rows in grouped.values()
        )
    frame = pd.DataFrame(rows)
    frame["snapshot_ts_utc"] = pd.to_datetime(frame["snapshot_ts_utc"], utc=True)
    frame = frame.sort_values(["event_slug", "snapshot_ts_utc"]).reset_index(drop=True)
    frame["prior_favorite_bracket"] = frame.groupby("event_slug")[
        "favorite_bracket"
    ].shift()
    frame["favorite_changed"] = (
        frame["prior_favorite_bracket"].notna()
        & frame["favorite_bracket"].notna()
        & frame["favorite_bracket"].ne(frame["prior_favorite_bracket"])
    )
    frame["prior_favorite_mid"] = frame.groupby("event_slug")["favorite_mid"].shift()
    frame["favorite_mid_abs_change"] = (
        frame["favorite_mid"] - frame["prior_favorite_mid"]
    ).abs()
    frame["minutes_since_prior_book"] = (
        frame.groupby("event_slug")["snapshot_ts_utc"].diff().dt.total_seconds() / 60
    )
    frame["is_target_day"] = frame["local_datetime"].str[:10].eq(frame["target_date"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / "state_rows.csv.gz", index=False, compression="gzip")
    city_hour = grouped_summary(frame, ["city", "local_hour_band"])
    city_hour.to_csv(args.output_dir / "city_hour_summary.csv", index=False)
    weather = grouped_summary(frame, ["weather_state", "local_hour_band"])
    weather.to_csv(args.output_dir / "weather_state_summary.csv", index=False)
    city_weather = grouped_summary(frame, ["city", "weather_state"])
    city_weather.to_csv(args.output_dir / "city_weather_summary.csv", index=False)

    snapshot_dates = frame["snapshot_ts_utc"].dt.date.astype(str)
    coverage = {
        "schema_version": "weather_book_microstructure_atlas_v1",
        "grain": "event_slug_x_archived_snapshot",
        "start_snapshot_date_utc": snapshot_dates.min(),
        "end_snapshot_date_utc": snapshot_dates.max(),
        "orderbook_files": len(paths),
        "raw_orderbook_rows": raw_rows,
        "state_rows": len(frame),
        "events": int(frame["event_slug"].nunique()),
        "cities": int(frame["city"].nunique()),
        "target_dates": int(frame["target_date"].nunique()),
        "observation_coverage": float(frame["observation_available"].mean()),
        "target_day_state_rows": int(frame["is_target_day"].sum()),
        "target_day_observation_coverage": float(
            frame.loc[frame["is_target_day"], "observation_available"].mean()
        ),
        "two_sided_quote_share_median": float(
            frame["two_sided_quote_share"].median()
        ),
        "actual_market_trade_volume_available": False,
        "volume_note": (
            "displayed order-book depth and quote churn are available; "
            "market-wide trade prints are not archived and are not imputed"
        ),
        "limitations": [
            "descriptive raw PIT coverage only; no strategy selection",
            "maker queue and unfilled/cancelled orders are unavailable",
            "displayed depth is not traded volume",
            "snapshot cadence is not a continuous order-book feed",
        ],
    }
    (args.output_dir / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(coverage, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
