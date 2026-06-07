#!/usr/bin/env python3
"""Inspect live_real timing distribution from fact_trades."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "runtime" / "weather.db"
V1_STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
CATCHUP_DEPLOYED_AT_UTC = datetime.fromisoformat("2026-05-26T15:37:07+00:00")


def parse_utc(value: Any) -> datetime | None:
    text = "" if value is None else str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hours_bin(hours: float | None) -> str:
    if hours is None:
        return "[missing]"
    if hours < 22:
        return "<T-22"
    if hours <= 24:
        return "T-22-24"
    if hours <= 26:
        return "T-24-26"
    if hours <= 28:
        return "T-26-28"
    return ">T-28"


def period_of(target_date: str) -> str:
    return "post_2026_06_01" if target_date >= "2026-06-01" else "pre_2026_06_01"


def live_phase(row: sqlite3.Row) -> str:
    order_ts = parse_utc(row["order_ts_utc"])
    if row["target_date"] >= "2026-06-01":
        return "post_june_target"
    if order_ts is not None and order_ts >= CATCHUP_DEPLOYED_AT_UTC:
        return "post_catchup_pre_june"
    return "pre_catchup_deploy"


def roi(pnl: float, cost: float) -> float | None:
    return None if cost == 0 else pnl / cost


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def print_table(title: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    print(f"\n## {title}")
    print("\t".join(cols))
    for row in rows:
        print("\t".join(fmt(row.get(c)) for c in cols))


def summarize(rows: list[sqlite3.Row]) -> dict[str, Any]:
    fills = len(rows)
    settled_rows = [r for r in rows if r["settlement_status"] == "settled"]
    cost = sum(float(r["cost_usd"] or 0) for r in settled_rows)
    pnls = [float(r["pnl_usd_at_fill"] or 0) for r in settled_rows]
    pnl = sum(pnls)
    settled = sum(1 for r in rows if r["settlement_status"] == "settled")
    wins = [1 if x > 0 else 0 for x in pnls]
    city_day_pnl: dict[tuple[str, str], float] = defaultdict(float)
    for row in settled_rows:
        city_day_pnl[(str(row["target_date"]), str(row["city"]))] += float(row["pnl_usd_at_fill"] or 0)
    best_city_day = max(city_day_pnl.values()) if city_day_pnl else None
    worst_city_day = min(city_day_pnl.values()) if city_day_pnl else None
    active_days = len({r["target_date"] for r in rows})
    city_days = len({(r["target_date"], r["city"]) for r in rows})
    return {
        "fills": fills,
        "settled": settled,
        "open_or_unsettled": fills - settled,
        "active_days": active_days,
        "city_days": city_days,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": None if cost == 0 else round(pnl / cost, 6),
        "win_rate": None if not wins else round(sum(wins) / len(wins), 6),
        "gross_win": round(sum(x for x in pnls if x > 0), 4),
        "gross_loss": round(sum(x for x in pnls if x < 0), 4),
        "max_fill_win": None if not pnls else round(max(pnls), 4),
        "max_fill_loss": None if not pnls else round(min(pnls), 4),
        "best_city_day": None if best_city_day is None else round(best_city_day, 4),
        "worst_city_day": None if worst_city_day is None else round(worst_city_day, 4),
    }


def grouped(rows: list[sqlite3.Row], keys: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = []
        for k in keys:
            if k == "hours_bin":
                key.append(hours_bin(row["hours_to_settle"]))
            elif k == "period":
                key.append(period_of(row["target_date"]))
            elif k == "live_phase":
                key.append(live_phase(row))
            else:
                key.append(row[k])
        buckets[tuple(key)].append(row)
    out = []
    for key, vals in buckets.items():
        item = {name: value for name, value in zip(keys, key)}
        item.update(summarize(vals))
        out.append(item)
    order = {"<T-22": 0, "T-22-24": 1, "T-24-26": 2, "T-26-28": 3, ">T-28": 4, "[missing]": 9}
    return sorted(out, key=lambda r: tuple(order.get(str(r.get(k)), str(r.get(k))) for k in keys))


def best_city_support(rows: list[sqlite3.Row], min_settled: int = 3) -> list[dict[str, Any]]:
    city_buckets: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        city_buckets[str(row["city"])].append(row)
    out = []
    for city, vals in city_buckets.items():
        timing = grouped(vals, ["hours_bin"])
        timing = [r for r in timing if int(r["settled"]) >= min_settled]
        if not timing:
            continue
        best = max(timing, key=lambda r: float(r["roi"] if r["roi"] is not None else -999))
        t2224 = next((r for r in timing if r["hours_bin"] == "T-22-24"), None)
        out.append(
            {
                "city": city,
                "best_hours_bin": best["hours_bin"],
                "best_roi": best["roi"],
                "best_pnl": best["pnl_usd"],
                "best_fills": best["fills"],
                "t22_24_roi": None if t2224 is None else t2224["roi"],
                "t22_24_pnl": None if t2224 is None else t2224["pnl_usd"],
                "t22_24_fills": 0 if t2224 is None else t2224["fills"],
                "supports_t22_24": best["hours_bin"] == "T-22-24",
            }
        )
    return sorted(out, key=lambda r: (not bool(r["supports_t22_24"]), str(r["city"])))


def gt28_by_date(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    by_date: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    out = []
    for target_date, vals in sorted(by_date.items()):
        gt28 = [r for r in vals if hours_bin(r["hours_to_settle"]) == ">T-28"]
        settled_gt28 = [r for r in gt28 if r["settlement_status"] == "settled"]
        out.append(
            {
                "target_date": target_date,
                "fills": len(vals),
                "gt28_fills": len(gt28),
                "gt28_share": None if not vals else round(len(gt28) / len(vals), 6),
                "gt28_settled": len(settled_gt28),
                "gt28_pnl": round(sum(float(r["pnl_usd_at_fill"] or 0) for r in settled_gt28), 4),
                "gt28_cities": len({r["city"] for r in gt28}),
            }
        )
    return out


def gt28_by_city(rows: list[sqlite3.Row], *, period: str) -> list[dict[str, Any]]:
    by_city: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if period_of(str(row["target_date"])) == period:
            by_city[str(row["city"])].append(row)
    out = []
    for city, vals in by_city.items():
        gt28 = [r for r in vals if hours_bin(r["hours_to_settle"]) == ">T-28"]
        if not gt28:
            continue
        settled_gt28 = [r for r in gt28 if r["settlement_status"] == "settled"]
        out.append(
            {
                "city": city,
                "fills": len(vals),
                "gt28_fills": len(gt28),
                "gt28_share": round(len(gt28) / len(vals), 6),
                "gt28_settled": len(settled_gt28),
                "gt28_pnl": round(sum(float(r["pnl_usd_at_fill"] or 0) for r in settled_gt28), 4),
            }
        )
    return sorted(out, key=lambda r: (-int(r["gt28_fills"]), float(r["gt28_pnl"])))


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print_table(
        "self_check",
        [dict(conn.execute("SELECT MAX(fact_built_at_utc) fact_built_at_utc FROM fact_trades").fetchone())],
        ["fact_built_at_utc"],
    )
    rows = conn.execute(
        """
        SELECT *
        FROM fact_trades
        WHERE trade_class='live_real'
          AND hours_to_settle IS NOT NULL
        """
    ).fetchall()
    v1 = [r for r in rows if r["strategy_id"] == V1_STRATEGY_ID and r["execution_policy"] == "mid_price_core_v1" and r["entry_price_window"] == "0.25-0.75"]
    all_settled = [r for r in rows if r["settlement_status"] == "settled"]
    v1_settled = [r for r in v1 if r["settlement_status"] == "settled"]

    cols = [
        "period",
        "hours_bin",
        "fills",
        "settled",
        "open_or_unsettled",
        "active_days",
        "city_days",
        "cost_usd",
        "pnl_usd",
        "roi",
        "win_rate",
        "gross_win",
        "gross_loss",
        "max_fill_win",
        "max_fill_loss",
        "best_city_day",
        "worst_city_day",
    ]
    print_table("v1_25_75 live_real timing by period", grouped(v1, ["period", "hours_bin"]), cols)
    phase_cols = [("live_phase" if c == "period" else c) for c in cols]
    print_table("v1_25_75 live_real timing by catchup deployment phase", grouped(v1, ["live_phase", "hours_bin"]), phase_cols)
    print_table("all live_real timing by period", grouped(rows, ["period", "hours_bin"]), cols)
    all_cols = [c for c in cols if c != "period"]
    print_table("v1_25_75 all-history timing", grouped(v1, ["hours_bin"]), all_cols)
    print_table("all live_real all-history timing", grouped(rows, ["hours_bin"]), all_cols)

    city = best_city_support(v1_settled)
    print_table(
        "v1_25_75 city best timing among bins with >=3 settled fills",
        city,
        ["city", "best_hours_bin", "best_roi", "best_pnl", "best_fills", "t22_24_roi", "t22_24_pnl", "t22_24_fills", "supports_t22_24"],
    )
    summary = {
        "cities_tested": len(city),
        "cities_support_t22_24": sum(1 for r in city if r["supports_t22_24"]),
    }
    print_table("city_support_summary", [summary], ["cities_tested", "cities_support_t22_24"])
    print_table(
        "v1_25_75 >T-28 by target_date",
        gt28_by_date(v1),
        ["target_date", "fills", "gt28_fills", "gt28_share", "gt28_settled", "gt28_pnl", "gt28_cities"],
    )
    print_table(
        "v1_25_75 post-6/1 >T-28 by city",
        gt28_by_city(v1, period="post_2026_06_01"),
        ["city", "fills", "gt28_fills", "gt28_share", "gt28_settled", "gt28_pnl"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
