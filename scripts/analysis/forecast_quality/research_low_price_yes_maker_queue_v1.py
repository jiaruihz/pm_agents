#!/usr/bin/env python3
"""Replay HeadA low-price YES maker fill/queue behavior.

The goal is intentionally narrow: use real live order + CLOB fill records to
estimate what happens if the same HeadA candidate is posted as a maker order at
$1/$3/$5 notional. It separates settled PnL from still-open orders and reports
both a conservative absolute-fill-cap model and a proportional-fill model.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
ORDERS_PATH = ROOT / "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"
FILLS_PATH = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"
DB_PATH = ROOT / "runtime/weather.db"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-maker-queue-v1.md"
GENERATED_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1"
SCENARIOS = (1.0, 3.0, 5.0)
STRATEGY_ID = "low_price_yes_lottery_tiny_live_v1"


def parse_ts(value: Any) -> datetime | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "NA"
    return "NA" if not math.isfinite(x) else f"{x * 100:.1f}%"


def fmt_usd(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "NA"
    return "NA" if not math.isfinite(x) else f"${x:.2f}"


def fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "NA"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "NA"
    return "NA" if not math.isfinite(x) else f"{x:.{digits}f}"


def order_id(row: dict[str, Any]) -> str:
    exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = exchange.get("place") if isinstance(exchange.get("place"), dict) else {}
    return str(row.get("order_id") or place.get("orderID") or "").strip()


def settlement_map(db_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not db_path.exists():
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    for row in conn.execute(
        """
        SELECT city, target_date, bracket, final_price, settlement_status, created_at_utc
        FROM settlement_outcomes
        ORDER BY created_at_utc
        """
    ):
        out[(str(row["city"]), str(row["target_date"]), str(row["bracket"]))] = dict(row)
    conn.close()
    return out


def build_order_rows(now: datetime) -> list[dict[str, Any]]:
    orders = [
        row
        for row in read_jsonl(ORDERS_PATH)
        if str(row.get("strategy_id") or row.get("strategy_instance")) == STRATEGY_ID
        and str(row.get("record_type")) == "weather_edge_live_order"
    ]
    fills_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fill in read_jsonl(FILLS_PATH):
        fills_by_order[str(fill.get("order_id") or "")].append(fill)
    settlements = settlement_map(DB_PATH)

    rows: list[dict[str, Any]] = []
    for row in orders:
        oid = order_id(row)
        created = parse_ts(row.get("created_at_utc"))
        fills = sorted(fills_by_order.get(oid, []), key=lambda r: str(r.get("filled_at_utc") or ""))
        fill_times = [parse_ts(fill.get("filled_at_utc")) for fill in fills]
        fill_times = [ts for ts in fill_times if ts is not None]
        posted_price = as_float(row.get("posted_price") or row.get("limit_price") or row.get("requested_price"))
        posted_shares = as_float(row.get("size") or row.get("max_order_shares") or row.get("order_shares"))
        posted_notional = as_float(row.get("posted_notional") or row.get("notional"), posted_price * posted_shares)
        filled_shares = sum(as_float(fill.get("filled_shares")) for fill in fills)
        fill_cost = sum(as_float(fill.get("filled_shares")) * as_float(fill.get("filled_price"), posted_price) + as_float(fill.get("fees_usd")) for fill in fills)
        first_fill_min = (fill_times[0] - created).total_seconds() / 60 if created and fill_times else None
        last_fill_min = (fill_times[-1] - created).total_seconds() / 60 if created and fill_times else None
        age_hours = (now - created).total_seconds() / 3600 if created else None
        settlement = settlements.get((str(row.get("city")), str(row.get("target_date")), str(row.get("bracket"))), {})
        final_yes = as_float(settlement.get("final_price"), math.nan)
        settled = str(settlement.get("settlement_status") or "") == "settled" and math.isfinite(final_yes)
        rows.append(
            {
                "order_id": oid,
                "created_at_utc": row.get("created_at_utc"),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "sizing_mode": row.get("sizing_mode"),
                "posted_price": posted_price,
                "posted_shares": posted_shares,
                "posted_notional": posted_notional,
                "quote_best_bid": as_float(row.get("quote_best_bid") or row.get("best_bid")),
                "quote_best_ask": as_float(row.get("quote_best_ask") or row.get("best_ask")),
                "quote_spread": as_float(row.get("quote_spread") or row.get("spread")),
                "model_p_yes": as_float(row.get("model_p_yes_used") or row.get("model_token_probability")),
                "edge": as_float(row.get("edge_used_yes") or row.get("quote_edge")),
                "fills": len(fills),
                "filled_shares": filled_shares,
                "fill_cost_usd": fill_cost,
                "fill_fraction": filled_shares / posted_shares if posted_shares > 0 else None,
                "any_fill": filled_shares > 1e-9,
                "full_fill": posted_shares > 0 and filled_shares >= posted_shares - 1e-6,
                "first_fill_wait_min": first_fill_min,
                "last_fill_wait_min": last_fill_min,
                "order_age_hours": age_hours,
                "settled": settled,
                "final_yes": final_yes if settled else None,
                "settlement_status": settlement.get("settlement_status") or "unsettled_or_missing",
                "realized_pnl_actual_usd": (filled_shares * final_yes - fill_cost) if settled else None,
                "missed_winner_shares_actual": max(posted_shares - filled_shares, 0.0) if settled and final_yes >= 0.999 else 0.0,
                "missed_winner_cost_actual_usd": max(posted_shares - filled_shares, 0.0) * (1.0 - posted_price) if settled and final_yes >= 0.999 else 0.0,
            }
        )
    return rows


def scenario_rows(order_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in order_rows:
        price = as_float(order.get("posted_price"))
        if price <= 0:
            continue
        actual_filled = as_float(order.get("filled_shares"))
        actual_fraction = max(0.0, min(1.0, as_float(order.get("fill_fraction"))))
        final_yes = order.get("final_yes")
        settled = bool(order.get("settled"))
        for notional in SCENARIOS:
            intended_shares = notional / price
            conservative_filled = min(intended_shares, actual_filled)
            proportional_filled = intended_shares * actual_fraction
            for model, filled in [
                ("absolute_fill_cap", conservative_filled),
                ("proportional_fill_fraction", proportional_filled),
            ]:
                unfilled = max(intended_shares - filled, 0.0)
                pnl = filled * (as_float(final_yes) - price) if settled else None
                rows.append(
                    {
                        "city": order.get("city"),
                        "target_date": order.get("target_date"),
                        "bracket": order.get("bracket"),
                        "order_id": order.get("order_id"),
                        "sizing_mode": order.get("sizing_mode"),
                        "posted_price": price,
                        "settled": settled,
                        "final_yes": final_yes,
                        "scenario_notional_usd": notional,
                        "scenario_model": model,
                        "intended_shares": intended_shares,
                        "filled_shares": filled,
                        "fill_fraction": filled / intended_shares if intended_shares > 0 else None,
                        "unfilled_shares": unfilled,
                        "cost_usd": filled * price,
                        "pnl_usd": pnl,
                        "missed_winner_cost_usd": unfilled * (1.0 - price) if settled and as_float(final_yes) >= 0.999 else 0.0,
                        "first_fill_wait_min": order.get("first_fill_wait_min"),
                        "last_fill_wait_min": order.get("last_fill_wait_min"),
                        "any_fill": filled > 1e-9,
                        "full_fill": filled >= intended_shares - 1e-6,
                    }
                )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def med(values: list[Any]) -> float | None:
        vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
        return median(vals) if vals else None

    settled = [r for r in rows if r.get("settled")]
    cost = sum(as_float(r.get("fill_cost_usd")) for r in settled)
    pnl = sum(as_float(r.get("realized_pnl_actual_usd")) for r in settled)
    return {
        "orders": len(rows),
        "settled_orders": len(settled),
        "open_orders": len(rows) - len(settled),
        "filled_orders": sum(1 for r in rows if r.get("any_fill")),
        "full_fill_orders": sum(1 for r in rows if r.get("full_fill")),
        "any_fill_rate": sum(1 for r in rows if r.get("any_fill")) / len(rows) if rows else None,
        "full_fill_rate": sum(1 for r in rows if r.get("full_fill")) / len(rows) if rows else None,
        "median_fill_fraction": med([r.get("fill_fraction") for r in rows]),
        "median_first_fill_wait_min": med([r.get("first_fill_wait_min") for r in rows]),
        "median_last_fill_wait_min": med([r.get("last_fill_wait_min") for r in rows]),
        "total_posted_notional": sum(as_float(r.get("posted_notional")) for r in rows),
        "total_fill_cost_usd": sum(as_float(r.get("fill_cost_usd")) for r in rows),
        "settled_cost_usd": cost,
        "settled_pnl_usd": pnl,
        "settled_roi": pnl / cost if cost else None,
        "settled_wins": sum(1 for r in settled if as_float(r.get("final_yes")) >= 0.999),
        "actual_missed_winner_cost_usd": sum(as_float(r.get("missed_winner_cost_actual_usd")) for r in settled),
        "sizing_mode_counts": dict(Counter(str(r.get("sizing_mode") or "") for r in rows)),
    }


def summarize_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups: dict[tuple[float, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(as_float(row.get("scenario_notional_usd")), str(row.get("scenario_model")))].append(row)
    for (notional, model), group in sorted(groups.items()):
        settled = [r for r in group if r.get("settled")]
        cost = sum(as_float(r.get("cost_usd")) for r in settled)
        pnl = sum(as_float(r.get("pnl_usd")) for r in settled)
        waits_first = [r.get("first_fill_wait_min") for r in group if r.get("first_fill_wait_min") is not None]
        waits_last = [r.get("last_fill_wait_min") for r in group if r.get("last_fill_wait_min") is not None]
        out.append(
            {
                "scenario_notional_usd": notional,
                "scenario_model": model,
                "orders": len(group),
                "settled_orders": len(settled),
                "any_fill_rate": sum(1 for r in group if r.get("any_fill")) / len(group) if group else None,
                "full_fill_rate": sum(1 for r in group if r.get("full_fill")) / len(group) if group else None,
                "mean_fill_fraction": sum(as_float(r.get("fill_fraction")) for r in group) / len(group) if group else None,
                "median_first_fill_wait_min": median(waits_first) if waits_first else None,
                "median_last_fill_wait_min": median(waits_last) if waits_last else None,
                "settled_cost_usd": cost,
                "settled_pnl_usd": pnl,
                "settled_roi": pnl / cost if cost else None,
                "missed_winner_cost_usd": sum(as_float(r.get("missed_winner_cost_usd")) for r in settled),
                "intended_notional_total_usd": sum(as_float(r.get("scenario_notional_usd")) for r in group),
                "filled_cost_total_usd": sum(as_float(r.get("cost_usd")) for r in group),
            }
        )
    return out


def write_report(order_rows: list[dict[str, Any]], scenario_summary: list[dict[str, Any]], output: dict[str, Any]) -> None:
    summary = output["summary"]
    price_tier_rows = [r for r in order_rows if r.get("sizing_mode") == "price_tier_6_8_10_shares"]
    old_rows = [r for r in order_rows if r.get("sizing_mode") != "price_tier_6_8_10_shares"]
    lines = [
        "# Low-Price YES Maker Queue v1",
        "",
        f"Generated: `{output['generated_at_utc']}`",
        "",
        "## Verdict",
        "",
        "`forecast_tail_low_price_yes` stays live at tiny size; do not size up to $3/$5 yet.",
        "",
        "The current evidence says maker entry can fill small $0.8-style orders, but it does not prove deeper capacity. "
        "The two new price-tier orders are still unfilled/open, so they are useful telemetry, not a sizing verdict.",
        "",
        "## Data",
        "",
        f"- Live order rows: {summary['orders']} ({summary['settled_orders']} settled, {summary['open_orders']} open).",
        f"- Fill coverage: any-fill {fmt_pct(summary['any_fill_rate'])}; full-fill {fmt_pct(summary['full_fill_rate'])}; median fill fraction {fmt_pct(summary['median_fill_fraction'])}.",
        f"- Wait: median first fill {fmt_num(summary['median_first_fill_wait_min'], 1)} min; median complete/last fill {fmt_num(summary['median_last_fill_wait_min'], 1)} min.",
        f"- Settled actual ROI on filled shares: {fmt_pct(summary['settled_roi'])} ({fmt_usd(summary['settled_pnl_usd'])} on {fmt_usd(summary['settled_cost_usd'])}).",
        f"- Actual missed-winner cost from unfilled winning shares: {fmt_usd(summary['actual_missed_winner_cost_usd'])}.",
        f"- Sizing modes: `{summary['sizing_mode_counts']}`.",
        "",
        "## $1/$3/$5 Scenarios",
        "",
        "Two queue models are shown:",
        "",
        "- `absolute_fill_cap`: conservative; assumes we could only have filled the absolute number of shares we actually observed on that order.",
        "- `proportional_fill_fraction`: optimistic; assumes larger orders get the same fill percentage as the actual order.",
        "",
        "| Notional | Model | Orders | Any Fill | Full Fill | Mean Fill | Settled ROI | Missed Winner Cost | Filled Cost |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scenario_summary:
        lines.append(
            f"| ${row['scenario_notional_usd']:.0f} | {row['scenario_model']} | {row['orders']} | "
            f"{fmt_pct(row['any_fill_rate'])} | {fmt_pct(row['full_fill_rate'])} | {fmt_pct(row['mean_fill_fraction'])} | "
            f"{fmt_pct(row['settled_roi'])} | {fmt_usd(row['missed_winner_cost_usd'])} | {fmt_usd(row['filled_cost_total_usd'])} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- The old fixed-notional maker sample mostly filled, but that sample is only 15 orders and one settled winner. It supports continuing tiny maker-first probing, not increasing ticket size.",
            f"- The current price-tier sample is {len(price_tier_rows)} orders and {sum(1 for r in price_tier_rows if r.get('any_fill'))} fills so far. Keep it running until it has enough elapsed local time and settlement labels.",
            "- $3/$5 needs a real partial-fill model before promotion: conservative absolute-cap fill collapses as notional rises, while proportional fill is an optimistic upper bound.",
            "- Missed-winner cost is currently zero in the settled order sample because the only settled winner was filled. That is good news, but too early to trust.",
            "",
            "## Order Rows",
            "",
            "| Created UTC | City | Date | Bracket | Mode | Price | Shares | Filled | Fill Frac | First Fill min | Last Fill min | Settled | Final YES |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for row in order_rows:
        lines.append(
            f"| {row['created_at_utc']} | {row['city']} | {row['target_date']} | {row['bracket']} | {row['sizing_mode']} | "
            f"{fmt_num(row['posted_price'], 3)} | {fmt_num(row['posted_shares'], 2)} | {fmt_num(row['filled_shares'], 2)} | "
            f"{fmt_pct(row['fill_fraction'])} | {fmt_num(row['first_fill_wait_min'], 1)} | {fmt_num(row['last_fill_wait_min'], 1)} | "
            f"{row['settlement_status']} | {fmt_num(row['final_yes'], 0)} |"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/order_queue_rows.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/scenario_rows.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_queue_v1/summary.json`",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    global ORDERS_PATH, FILLS_PATH, DB_PATH, REPORT_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=ORDERS_PATH)
    parser.add_argument("--fills", type=Path, default=FILLS_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    ORDERS_PATH = args.orders
    FILLS_PATH = args.fills
    DB_PATH = args.db
    REPORT_PATH = args.report

    now = datetime.now(timezone.utc)
    orders = build_order_rows(now)
    scenarios = scenario_rows(orders)
    scenario_summary = summarize_scenarios(scenarios)
    output = {
        "generated_at_utc": now.isoformat(),
        "source_files": {
            "orders": str(ORDERS_PATH.relative_to(ROOT)),
            "fills": str(FILLS_PATH.relative_to(ROOT)),
            "db": str(DB_PATH.relative_to(ROOT)),
        },
        "summary": summarize(orders),
        "scenario_summary": scenario_summary,
    }

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(GENERATED_DIR / "order_queue_rows.csv", orders)
    write_csv(GENERATED_DIR / "scenario_rows.csv", scenarios)
    write_csv(GENERATED_DIR / "scenario_summary.csv", scenario_summary)
    (GENERATED_DIR / "summary.json").write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    write_report(orders, scenario_summary, output)
    print(json.dumps({"report": str(REPORT_PATH), "orders": len(orders), "scenario_rows": len(scenarios)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
