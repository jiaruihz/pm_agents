#!/usr/bin/env python3
"""Replay yourthos Seoul trades against the complete city-day ladder and RKSI path.

Public wallet activity is complete for this wallet at the time of writing.
Weather joins deliberately expose two evidence grades:

* `pit_*` uses observations whose `available_at_utc` is no later than the trade.
* `path_*` uses observation timestamps only and is explanatory, not PIT evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import requests

from research_external_wallet_strategy_v1 import _event_slug, _is_weather, _target_date
from research_wallet_event_portfolios_v1 import latest_activity


SEOUL_TZ = ZoneInfo("Asia/Seoul")
DEFAULT_WALLET = "0x4f1164d1531b8fb77919df285c576628318553b0"
SOURCE_PRIORITY = {
    "amos_runway": 0,
    "aviationweather_metar": 1,
    "aviationweather_cache_csv": 2,
    "noaa_tgftp_station_txt": 3,
    "iem_asos": 4,
}


def iso_utc(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def local_dt(ts: int | float) -> datetime:
    return datetime.fromtimestamp(float(ts), timezone.utc).astimezone(SEOUL_TZ)


def norm_question(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def bracket_from_title(title: str | None) -> dict[str, Any]:
    text = str(title or "")
    match = re.search(
        r"be\s+(-?\d+)\s*°?c(?:\s+(or\s+(below|lower|higher|above)))?\s+on",
        text,
        re.I,
    )
    if not match:
        return {"label": None, "value": None, "tail": None}
    value = int(match.group(1))
    direction = (match.group(2) or "").lower()
    if "below" in direction or "lower" in direction:
        tail = "lower"
        label = f"{value}-"
    elif "higher" in direction or "above" in direction:
        tail = "upper"
        label = f"{value}+"
    else:
        tail = "exact"
        label = str(value)
    return {"label": label, "value": value, "tail": tail}


def get_settlements(
    conn: sqlite3.Connection,
    dates: list[str],
) -> tuple[dict[str, str], dict[tuple[str, str], float]]:
    winner: dict[str, str] = {}
    question_prices: dict[tuple[str, str], float] = {}
    for target_date in dates:
        rows = conn.execute(
            """
            SELECT bracket, final_price, question
            FROM settlement_outcomes
            WHERE city = 'Seoul' AND target_date = ?
            """,
            (target_date,),
        ).fetchall()
        for row in rows:
            question_prices[(target_date, norm_question(row["question"]))] = float(
                row["final_price"]
            )
            if float(row["final_price"]) == 1:
                winner[target_date] = str(row["bracket"])
    return winner, question_prices


def get_observations(
    conn: sqlite3.Connection,
    target_date: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT source_system, obs_ts_utc, available_at_utc, pit_lineage_class,
               temp_c, dewpoint_f, wind_speed_kt, sky_cover
        FROM weather_observation_events
        WHERE city = 'Seoul' AND target_date = ? AND temp_c IS NOT NULL
        ORDER BY obs_ts_utc, source_system
        """,
        (target_date,),
    ).fetchall()
    return [dict(row) for row in rows]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def preferred_by_obs_ts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["obs_ts_utc"])
        old = chosen.get(key)
        rank = SOURCE_PRIORITY.get(str(row["source_system"]), 99)
        old_rank = SOURCE_PRIORITY.get(str(old["source_system"]), 99) if old else 999
        if old is None or rank < old_rank:
            chosen[key] = row
    return sorted(chosen.values(), key=lambda row: str(row["obs_ts_utc"]))


def weather_asof(rows: list[dict[str, Any]], trade_ts: int) -> dict[str, Any]:
    decision = datetime.fromtimestamp(trade_ts, timezone.utc)
    path_rows = [
        row
        for row in preferred_by_obs_ts(rows)
        if (parse_dt(row["obs_ts_utc"]) or decision) <= decision
    ]
    pit_candidates = [
        row
        for row in rows
        if (parse_dt(row["obs_ts_utc"]) or decision) <= decision
        and (parse_dt(row["available_at_utc"]) or datetime.max.replace(tzinfo=timezone.utc))
        <= decision
        and str(row.get("pit_lineage_class") or "")
        != "late_backfill_first_seen_unknown"
    ]
    pit_rows = preferred_by_obs_ts(pit_candidates)

    def summarize(sample: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
        if not sample:
            return {
                f"{prefix}_available": False,
                f"{prefix}_latest_temp_c": None,
                f"{prefix}_running_max_c": None,
                f"{prefix}_latest_obs_utc": None,
                f"{prefix}_source": None,
                f"{prefix}_obs_age_min": None,
                f"{prefix}_temp_change_60m_c": None,
                f"{prefix}_minutes_since_running_max": None,
                f"{prefix}_pullback_from_running_max_c": None,
                f"{prefix}_dewpoint_f": None,
                f"{prefix}_wind_speed_kt": None,
                f"{prefix}_sky_cover": None,
            }
        latest = sample[-1]
        latest_ts = parse_dt(latest["obs_ts_utc"])
        running_max = max(float(row["temp_c"]) for row in sample)
        first_max = next(
            row for row in sample if float(row["temp_c"]) == running_max
        )
        first_max_ts = parse_dt(first_max["obs_ts_utc"])
        reference = None
        if latest_ts:
            candidates = [
                row
                for row in sample
                if parse_dt(row["obs_ts_utc"])
                and (latest_ts - parse_dt(row["obs_ts_utc"])).total_seconds() >= 3600
            ]
            reference = candidates[-1] if candidates else None
        return {
            f"{prefix}_available": True,
            f"{prefix}_latest_temp_c": float(latest["temp_c"]),
            f"{prefix}_running_max_c": running_max,
            f"{prefix}_latest_obs_utc": latest["obs_ts_utc"],
            f"{prefix}_source": latest["source_system"],
            f"{prefix}_obs_age_min": (
                round((decision - latest_ts).total_seconds() / 60, 3)
                if latest_ts
                else None
            ),
            f"{prefix}_temp_change_60m_c": (
                round(float(latest["temp_c"]) - float(reference["temp_c"]), 3)
                if reference
                else None
            ),
            f"{prefix}_minutes_since_running_max": (
                round((decision - first_max_ts).total_seconds() / 60, 3)
                if first_max_ts
                else None
            ),
            f"{prefix}_pullback_from_running_max_c": round(
                running_max - float(latest["temp_c"]), 3
            ),
            f"{prefix}_dewpoint_f": latest.get("dewpoint_f"),
            f"{prefix}_wind_speed_kt": latest.get("wind_speed_kt"),
            f"{prefix}_sky_cover": latest.get("sky_cover"),
        }

    return {**summarize(path_rows, "path"), **summarize(pit_rows, "pit")}


def minutes_to_next_higher_observation(
    rows: list[dict[str, Any]],
    trade_ts: int,
    bracket_value: int | None,
) -> float | None:
    if bracket_value is None:
        return None
    decision = datetime.fromtimestamp(trade_ts, timezone.utc)
    for row in preferred_by_obs_ts(rows):
        obs_ts = parse_dt(row["obs_ts_utc"])
        if (
            obs_ts
            and obs_ts > decision
            and float(row["temp_c"]) > float(bracket_value)
        ):
            return round((obs_ts - decision).total_seconds() / 60, 3)
    return None


def expression(row: dict[str, Any], weather: dict[str, Any]) -> str:
    bracket = bracket_from_title(row.get("title"))
    value = bracket["value"]
    # Use the timestamped physical path to describe the economic expression.
    # PIT availability is reported separately and must not be inferred from it.
    running = weather.get("path_running_max_c")
    if value is None or running is None:
        return "unclassified_no_weather"
    delta = value - round(float(running))
    outcome = str(row.get("outcome") or "").lower()
    if outcome == "yes":
        if delta == 0:
            return "current_yes"
        if delta == 1:
            return "next_yes"
        if delta >= 2:
            return "upper_tail_yes"
        return "already_crossed_yes"
    if outcome == "no":
        if delta == 0:
            return "current_no"
        if delta == -1:
            return "previous_no"
        if delta < -1:
            return "lower_crossed_no"
        return "future_bracket_no"
    return "unknown_outcome"


def event_summary(
    slug: str,
    rows: list[dict[str, Any]],
    target_date: str,
    winner: str | None,
    question_prices: dict[tuple[str, str], float],
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades = sorted(
        [row for row in rows if row.get("type") == "TRADE"],
        key=lambda row: int(row.get("timestamp") or 0),
    )
    redeems = [row for row in rows if row.get("type") == "REDEEM"]
    buys = [row for row in trades if row.get("side") == "BUY"]
    sells = [row for row in trades if row.get("side") == "SELL"]
    first_buy_ts = int(buys[0]["timestamp"]) if buys else None
    first_sell_ts = int(sells[0]["timestamp"]) if sells else None

    asset_book: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "buy_shares": 0.0,
            "sell_shares": 0.0,
            "buy_cash": 0.0,
            "sell_cash": 0.0,
        }
    )
    detailed: list[dict[str, Any]] = []
    expression_cost: dict[str, float] = defaultdict(float)
    for index, row in enumerate(trades, start=1):
        asset = str(row.get("asset") or "")
        size = float(row.get("size") or 0)
        cash = float(row.get("usdcSize") or 0)
        side = str(row.get("side") or "")
        if side == "BUY":
            asset_book[asset]["buy_shares"] += size
            asset_book[asset]["buy_cash"] += cash
        elif side == "SELL":
            asset_book[asset]["sell_shares"] += size
            asset_book[asset]["sell_cash"] += cash
        wx = weather_asof(observations, int(row["timestamp"]))
        expr = expression(row, wx)
        if side == "BUY":
            expression_cost[expr] += cash
        bracket = bracket_from_title(row.get("title"))
        running = wx.get("path_running_max_c")
        detailed.append(
            {
                "event_slug": slug,
                "target_date": target_date,
                "trade_index": index,
                "timestamp_utc": iso_utc(row.get("timestamp")),
                "timestamp_local": local_dt(int(row["timestamp"])).isoformat(),
                "transaction_hash": row.get("transactionHash"),
                "side": side,
                "outcome": row.get("outcome"),
                "bracket": bracket["label"],
                "bracket_value": bracket["value"],
                "shares": size,
                "cash": cash,
                "price": float(row.get("price") or 0),
                "expression_vs_running_max": expr,
                "bracket_minus_running_max": (
                    round(float(bracket["value"]) - float(running), 3)
                    if bracket["value"] is not None and running is not None
                    else None
                ),
                "minutes_to_next_higher_observation": (
                    minutes_to_next_higher_observation(
                        observations,
                        int(row["timestamp"]),
                        bracket["value"],
                    )
                    if side == "BUY" and str(row.get("outcome") or "") == "No"
                    else None
                ),
                **wx,
            }
        )

    buy_cash = sum(float(row.get("usdcSize") or 0) for row in buys)
    sell_cash = sum(float(row.get("usdcSize") or 0) for row in sells)
    redeem_cash = sum(float(row.get("usdcSize") or 0) for row in redeems)
    buy_shares = sum(float(row.get("size") or 0) for row in buys)
    sell_shares = sum(float(row.get("size") or 0) for row in sells)
    remaining_shares = sum(
        max(0.0, values["buy_shares"] - values["sell_shares"])
        for values in asset_book.values()
    )
    settled = winner is not None
    inventory_ratio = remaining_shares / buy_shares if buy_shares else 0.0
    sell_share_ratio = sell_shares / buy_shares if buy_shares else 0.0

    # Value the complete mutually-exclusive ladder.  A bracket NO pays on every
    # other bracket, which is also why NegRisk can convert it into the other
    # YES tokens.  Asset-by-asset inventory would double count converted NO.
    theoretical_payoff = 0.0
    for row in trades:
        yes_price = question_prices.get((target_date, norm_question(row.get("title"))))
        if yes_price is None:
            continue
        token_wins = yes_price if row.get("outcome") == "Yes" else 1 - yes_price
        sign = 1.0 if row.get("side") == "BUY" else -1.0
        theoretical_payoff += sign * float(row.get("size") or 0) * token_wins

    converted_short_assets = sum(
        values["sell_shares"] > values["buy_shares"] + 1e-6
        for values in asset_book.values()
    )
    settlement_over_buy_cost = theoretical_payoff / buy_cash if buy_cash else 0.0

    if not settled:
        style = "unsettled"
    elif settlement_over_buy_cost >= 0.5:
        style = "hold_to_settlement"
    elif settlement_over_buy_cost <= 0.1 and sell_cash > 0:
        style = "round_trip_exit"
    else:
        style = "trade_then_partial_hold"

    initial_end = first_buy_ts + 15 * 60 if first_buy_ts else 0
    initial_buys = [
        row for row in buys if int(row.get("timestamp") or 0) <= initial_end
    ]
    initial_cash = sum(float(row.get("usdcSize") or 0) for row in initial_buys)
    initial_shares = sum(float(row.get("size") or 0) for row in initial_buys)
    initial_price = initial_cash / initial_shares if initial_shares else None
    first_weather = (
        weather_asof(observations, first_buy_ts) if first_buy_ts else {}
    )
    first_bracket = bracket_from_title(buys[0].get("title")) if buys else {}
    first_running = first_weather.get("path_running_max_c")

    direction_cost = defaultdict(float)
    price_cost = defaultdict(float)
    for row in buys:
        cash = float(row.get("usdcSize") or 0)
        direction_cost[str(row.get("outcome") or "unknown")] += cash
        price = float(row.get("price") or 0)
        if price < 0.2:
            band = "<20c"
        elif price < 0.5:
            band = "20-50c"
        elif price < 0.8:
            band = "50-80c"
        elif price < 0.95:
            band = "80-95c"
        else:
            band = ">=95c"
        price_cost[band] += cash

    return (
        {
            "event_slug": slug,
            "target_date": target_date,
            "winner_bracket": winner,
            "settled": settled,
            "trade_rows": len(trades),
            "buy_rows": len(buys),
            "sell_rows": len(sells),
            "redeem_rows": len(redeems),
            "first_buy_utc": iso_utc(first_buy_ts),
            "first_buy_local": local_dt(first_buy_ts).isoformat() if first_buy_ts else None,
            "first_sell_utc": iso_utc(first_sell_ts),
            "first_sell_local": local_dt(first_sell_ts).isoformat() if first_sell_ts else None,
            "minutes_to_first_sell": (
                round((first_sell_ts - first_buy_ts) / 60, 3)
                if first_sell_ts and first_buy_ts
                else None
            ),
            "last_trade_local": (
                local_dt(int(trades[-1]["timestamp"])).isoformat() if trades else None
            ),
            "buy_cash": round(buy_cash, 6),
            "sell_cash": round(sell_cash, 6),
            "redeem_cash_observed": round(redeem_cash, 6),
            "theoretical_payoff_from_net_inventory": round(theoretical_payoff, 6),
            "cash_pnl_using_theoretical_settlement": (
                round(sell_cash + theoretical_payoff - buy_cash, 6)
                if settled
                else None
            ),
            "buy_shares": round(buy_shares, 6),
            "sell_shares": round(sell_shares, 6),
            "remaining_shares": round(remaining_shares, 6),
            "inventory_ratio": round(inventory_ratio, 6),
            "sell_share_ratio": round(sell_share_ratio, 6),
            "settlement_over_buy_cost": round(settlement_over_buy_cost, 6),
            "converted_short_assets": converted_short_assets,
            "neg_risk_conversion_visible": converted_short_assets > 0,
            "style": style,
            "first_buy_outcome": buys[0].get("outcome") if buys else None,
            "first_buy_bracket": first_bracket.get("label"),
            "first_buy_price": float(buys[0].get("price") or 0) if buys else None,
            "initial_15m_vwap": round(initial_price, 6) if initial_price else None,
            "total_buy_vwap": round(buy_cash / buy_shares, 6) if buy_shares else None,
            "direction_buy_cost": dict(direction_cost),
            "price_band_buy_cost": dict(price_cost),
            "expression_buy_cost": dict(expression_cost),
            "first_bracket_minus_running_max": (
                round(float(first_bracket["value"]) - float(first_running), 3)
                if first_bracket.get("value") is not None and first_running is not None
                else None
            ),
            **first_weather,
        },
        detailed,
    )


def weighted_share(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        for key, value in row.get(field, {}).items():
            totals[key] += float(value)
    denominator = sum(totals.values())
    return {
        key: round(value / denominator, 6) if denominator else 0.0
        for key, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--db", type=Path, default=Path("runtime/weather.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    activity = latest_activity(requests.Session(), args.wallet.lower())
    weather_rows = [
        row
        for row in activity
        if _is_weather(row) and "seoul" in _event_slug(row).lower()
    ]
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_by_event: dict[str, str] = {}
    for row in weather_rows:
        slug = _event_slug(row)
        target = _target_date(slug, int(row.get("timestamp") or 0))
        if not slug or not target:
            continue
        by_event[slug].append(row)
        target_by_event[slug] = target.isoformat()

    conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    dates = sorted(set(target_by_event.values()))
    winners, question_prices = get_settlements(conn, dates)

    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for slug in sorted(by_event, key=lambda key: target_by_event[key]):
        target_date = target_by_event[slug]
        observations = get_observations(conn, target_date)
        event, detail = event_summary(
            slug,
            by_event[slug],
            target_date,
            winners.get(target_date),
            question_prices,
            observations,
        )
        events.append(event)
        trades.extend(detail)
    conn.close()

    settled = [row for row in events if row["settled"]]
    pnl_rows = [
        row
        for row in settled
        if row["cash_pnl_using_theoretical_settlement"] is not None
    ]
    target_day_cost = 0.0
    total_cost = 0.0
    local_hour_cost: dict[str, float] = defaultdict(float)
    for row in trades:
        if row["side"] != "BUY":
            continue
        cost = float(row["cash"])
        total_cost += cost
        local = datetime.fromisoformat(row["timestamp_local"])
        if local.date().isoformat() == row["target_date"]:
            target_day_cost += cost
        band = (
            "00-06"
            if local.hour < 6
            else "06-10"
            if local.hour < 10
            else "10-14"
            if local.hour < 14
            else "14-18"
            if local.hour < 18
            else "18-24"
        )
        local_hour_cost[band] += cost

    entry_prices = [
        float(row["initial_15m_vwap"])
        for row in events
        if row.get("initial_15m_vwap") is not None
    ]
    sell_delays = [
        float(row["minutes_to_first_sell"])
        for row in events
        if row.get("minutes_to_first_sell") is not None
    ]
    payload = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "wallet": args.wallet.lower(),
        "coverage": {
            "raw_activity_rows": len(activity),
            "activity_sample_truncated": len(activity) >= 5_500,
            "seoul_weather_rows": len(weather_rows),
            "events": len(events),
            "settled_events": len(settled),
            "independent_target_dates": len(set(target_by_event.values())),
            "strict_pit_events_at_first_buy": sum(
                bool(row.get("pit_available")) for row in events
            ),
            "weather_path_events_at_first_buy": sum(
                bool(row.get("path_available")) for row in events
            ),
        },
        "portfolio_summary": {
            "style_event_counts": dict(Counter(row["style"] for row in events)),
            "buy_direction_cost_share": weighted_share(events, "direction_buy_cost"),
            "buy_price_band_cost_share": weighted_share(events, "price_band_buy_cost"),
            "buy_expression_cost_share": weighted_share(events, "expression_buy_cost"),
            "classified_buy_expression_cost_share": weighted_share(
                [
                    {
                        **row,
                        "expression_buy_cost": {
                            key: value
                            for key, value in row["expression_buy_cost"].items()
                            if key != "unclassified_no_weather"
                        },
                    }
                    for row in events
                ],
                "expression_buy_cost",
            ),
            "events_with_neg_risk_conversion_visible": sum(
                row["neg_risk_conversion_visible"] for row in events
            ),
            "target_day_buy_cost_share": (
                round(target_day_cost / total_cost, 6) if total_cost else None
            ),
            "local_hour_buy_cost_share": {
                key: round(value / total_cost, 6) if total_cost else 0.0
                for key, value in sorted(local_hour_cost.items())
            },
            "total_buy_cash": round(sum(row["buy_cash"] for row in events), 6),
            "total_sell_cash": round(sum(row["sell_cash"] for row in events), 6),
            "total_theoretical_settlement": round(
                sum(row["theoretical_payoff_from_net_inventory"] for row in settled),
                6,
            ),
            "settled_cash_pnl": round(
                sum(row["cash_pnl_using_theoretical_settlement"] for row in pnl_rows),
                6,
            ),
            "settled_roi_on_buy_cash": round(
                sum(row["cash_pnl_using_theoretical_settlement"] for row in pnl_rows)
                / sum(row["buy_cash"] for row in pnl_rows),
                6,
            )
            if pnl_rows and sum(row["buy_cash"] for row in pnl_rows)
            else None,
            "profitable_settled_events": sum(
                row["cash_pnl_using_theoretical_settlement"] > 0 for row in pnl_rows
            ),
            "losing_settled_events": sum(
                row["cash_pnl_using_theoretical_settlement"] < 0 for row in pnl_rows
            ),
            "median_initial_15m_vwap": (
                sorted(entry_prices)[len(entry_prices) // 2] if entry_prices else None
            ),
            "median_minutes_to_first_sell": (
                sorted(sell_delays)[len(sell_delays) // 2] if sell_delays else None
            ),
        },
        "events": events,
        "trades": trades,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: payload[k] for k in ("coverage", "portfolio_summary")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
