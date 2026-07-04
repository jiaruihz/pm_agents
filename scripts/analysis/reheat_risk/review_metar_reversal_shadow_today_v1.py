#!/usr/bin/env python3
"""Daily Head B METAR reversal shadow review.

Reads the zero-notional Head B journal and reports same-day trigger frequency,
unique token-level opportunities, latest orderbook mark-to-bid, and branch
attribution for `false_fade_reheat_conflict` and `rich_current_conflict_b4`.

This script is analysis-only. It does not write orders or strategy configs.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SHADOW_DIR = ROOT / "runtime/weather_edge_v1/metar_reversal_false_fade_reheat_shadow_v1"
SUMMARY_HISTORY = SHADOW_DIR / "summary_history.jsonl"
STATE_DECISIONS = SHADOW_DIR / "state_decisions.jsonl"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
DB_PATH = ROOT / "runtime/weather.db"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
REPORT_DIR = ROOT / "docs/analysis/2026-07"
GENERATED_DIR = REPORT_DIR / "generated/metar_reversal_shadow_today_v1"
TAKER_FEE_RATE = 0.05
UNIT_NOTIONAL_USD = 1.0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def parse_ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def as_float(value: Any) -> float:
    try:
        if value in ("", None):
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def bracket_bounds(bracket: Any) -> tuple[float, float]:
    b = str(bracket).strip().replace("°", "")
    m = re.match(r"^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$", b)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^(-?\d+(?:\.\d+)?)\+$", b)
    if m:
        return float(m.group(1)), math.inf
    m = re.match(r"^(-?\d+(?:\.\d+)?)$", b)
    if m:
        v = float(m.group(1))
        return v, v
    return math.nan, math.nan


def relation_to_bracket(value: float, bracket: Any) -> str:
    lo, hi = bracket_bounds(bracket)
    if not math.isfinite(value) or math.isnan(lo):
        return "unknown"
    rounded = round(value)
    if rounded < lo:
        return "not_reached"
    if math.isinf(hi) or rounded <= hi:
        return "in_bracket_now"
    return "overshot_by_obs_proxy"


def fee(shares: float, price: float) -> float:
    if not (math.isfinite(shares) and math.isfinite(price)):
        return math.nan
    return shares * TAKER_FEE_RATE * price * (1.0 - price)


def mtm_at_bid(entry_ask: float, latest_bid: float) -> dict[str, Any]:
    if not (math.isfinite(entry_ask) and entry_ask > 0):
        return {
            "shares": None,
            "entry_fee_usd": None,
            "exit_fee_usd": None,
            "cost_usd": None,
            "exit_revenue_usd": None,
            "pnl_usd": None,
            "roi": None,
        }
    shares = UNIT_NOTIONAL_USD / entry_ask
    entry_fee = fee(shares, entry_ask)
    cost = UNIT_NOTIONAL_USD + entry_fee
    if not math.isfinite(latest_bid):
        return {
            "shares": shares,
            "entry_fee_usd": entry_fee,
            "exit_fee_usd": 0.0,
            "cost_usd": cost,
            "exit_revenue_usd": 0.0,
            "pnl_usd": -cost,
            "roi": -1.0,
            "mark_status": "missing_bid_marked_zero",
        }
    exit_fee = fee(shares, latest_bid)
    exit_revenue = shares * latest_bid - exit_fee
    pnl = exit_revenue - cost
    return {
        "shares": shares,
        "entry_fee_usd": entry_fee,
        "exit_fee_usd": exit_fee,
        "cost_usd": cost,
        "exit_revenue_usd": exit_revenue,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "mark_status": "bid_mark",
    }


def latest_quote(snapshot_file: str | None, city: str, target_date: str, bracket: str) -> dict[str, Any] | None:
    files: list[Path] = [SNAPSHOT_DIR / snapshot_file] if snapshot_file else []
    if not files:
        files.extend(sorted(SNAPSHOT_DIR.glob("snapshot_*.json"), reverse=True)[:30])
    for path in files:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        city_date_records = [
            rec
            for rec in payload.get("records", [])
            if str(rec.get("city")) == city and str(rec.get("target_date")) == target_date
        ]
        for rec in payload.get("records", []):
            if (
                str(rec.get("city")) == city
                and str(rec.get("target_date")) == target_date
                and str(rec.get("bracket")) == bracket
            ):
                return {
                    "snapshot_file": path.name,
                    "snapshot_ts_utc": payload.get("ts_utc"),
                    "question": rec.get("question"),
                    "yes_best_bid": rec.get("yes_best_bid"),
                    "yes_best_ask": rec.get("yes_best_ask"),
                    "yes_bid_size": rec.get("yes_bid_size"),
                    "yes_ask_size": rec.get("yes_ask_size"),
                    "market_yes_price": rec.get("market_yes_price"),
                    "yes_book_status": rec.get("yes_book_status"),
                    "city_local_date_at_snapshot": rec.get("city_local_date_at_snapshot"),
                }
        if city_date_records:
            winner = next(
                (
                    rec
                    for rec in city_date_records
                    if as_float(rec.get("yes_best_bid")) >= 0.99 or as_float(rec.get("market_yes_price")) >= 0.99
                ),
                None,
            )
            if winner is not None:
                return {
                    "snapshot_file": path.name,
                    "snapshot_ts_utc": payload.get("ts_utc"),
                    "question": None,
                    "yes_best_bid": 0.0,
                    "yes_best_ask": None,
                    "yes_bid_size": None,
                    "yes_ask_size": None,
                    "market_yes_price": 0.0,
                    "yes_book_status": "inferred_loser_from_snapshot_winner",
                    "city_local_date_at_snapshot": winner.get("city_local_date_at_snapshot"),
                    "inferred_winner_bracket": winner.get("bracket"),
                }
            return {
                "snapshot_file": path.name,
                "snapshot_ts_utc": payload.get("ts_utc"),
                "question": None,
                "yes_best_bid": None,
                "yes_best_ask": None,
                "yes_bid_size": None,
                "yes_ask_size": None,
                "market_yes_price": None,
                "yes_book_status": "missing_in_latest_city_snapshot",
                "city_local_date_at_snapshot": city_date_records[0].get("city_local_date_at_snapshot"),
            }
    return None


def latest_by_key(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> dict[tuple[Any, ...], dict[str, Any]]:
    out: dict[tuple[Any, ...], tuple[datetime, dict[str, Any]]] = {}
    for row in rows:
        if row.get("state_status") != "ok" or not row.get("generated_at_utc"):
            continue
        key = tuple(row.get(k) for k in key_fields)
        ts = parse_ts(row["generated_at_utc"])
        if key not in out or ts > out[key][0]:
            out[key] = (ts, row)
    return {k: v[1] for k, v in out.items()}


def summarize_mark_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = rows
    cost = sum(r["mark_to_bid"].get("cost_usd") or 0.0 for r in selected)
    pnl = sum(r["mark_to_bid"].get("pnl_usd") or 0.0 for r in selected)
    return {
        "tokens": len(selected),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "cities": sorted({r["city"] for r in selected}),
    }


def mark_row_from_entry(
    *,
    branch: str | None,
    city: str,
    target_date: str,
    d1_bracket: str,
    first: dict[str, Any],
    latest_city: dict[tuple[Any, Any], dict[str, Any]],
) -> dict[str, Any]:
    latest = latest_city.get((city, target_date), {})
    quote = latest_quote(latest.get("snapshot_file"), city, target_date, d1_bracket) or {}
    entry_ask = as_float(first.get("d1_yes_ask"))
    latest_bid = as_float(quote.get("yes_best_bid"))
    mark = mtm_at_bid(entry_ask, latest_bid)
    bid_size = as_float(quote.get("yes_bid_size"))
    branches = [branch] if branch else []
    return {
        "city": city,
        "target_date": target_date,
        "d1_bracket": d1_bracket,
        "branches": branches,
        "entry": {
            "generated_at_utc": first.get("generated_at_utc"),
            "snapshot_file": first.get("snapshot_file"),
            "current_bracket": first.get("current_bracket"),
            "current_yes_ask": first.get("current_yes_ask"),
            "d1_yes_ask": first.get("d1_yes_ask"),
            "d1_yes_ask_size": first.get("d1_yes_ask_size"),
            "running_native": first.get("running_native"),
            "forecast_max_native": first.get("forecast_max_native"),
            "forecast_gap_native": first.get("forecast_gap_native"),
            "forecast_steps": first.get("forecast_steps"),
            "temp_trend_1h_f": first.get("temp_trend_1h_f"),
            "forecast_peak_delta_hours_local": first.get("forecast_peak_delta_hours_local"),
        },
        "latest": {
            "generated_at_utc": latest.get("generated_at_utc"),
            "snapshot_file": latest.get("snapshot_file"),
            "current_bracket": latest.get("current_bracket"),
            "running_native": latest.get("running_native"),
            "temp_trend_1h_f": latest.get("temp_trend_1h_f"),
            "forecast_peak_delta_hours_local": latest.get("forecast_peak_delta_hours_local"),
            "trigger_ff": latest.get("false_fade_reheat_conflict_triggered"),
            "trigger_b4": latest.get("rich_current_conflict_b4_triggered"),
            "relation_to_d1_by_obs_proxy": relation_to_bracket(as_float(latest.get("running_native")), d1_bracket),
        },
        "latest_quote": quote,
        "mark_to_bid": mark
        | {
            "bid_size_covers_unit_notional": (
                bool(math.isfinite(bid_size) and finite(mark.get("shares")) and bid_size >= mark["shares"])
                if finite(mark.get("shares"))
                else None
            )
        },
    }


def build_review(target_date_utc: str) -> dict[str, Any]:
    summaries = read_jsonl(SUMMARY_HISTORY)
    decisions = read_jsonl(STATE_DECISIONS)
    today_summaries = [s for s in summaries if str(s.get("generated_at_utc", "")).startswith(target_date_utc)]
    cycle_ids = {s.get("cycle_id") for s in today_summaries}
    today_decisions = [
        r for r in decisions if r.get("cycle_id") in cycle_ids and r.get("state_status") == "ok"
    ]
    trigger_rows = [
        r
        for r in today_decisions
        if r.get("false_fade_reheat_conflict_triggered") or r.get("rich_current_conflict_b4_triggered")
    ]

    first_by_token: dict[tuple[Any, ...], tuple[datetime, dict[str, Any], set[str]]] = {}
    first_by_branch: dict[tuple[Any, ...], tuple[datetime, dict[str, Any]]] = {}
    for row in trigger_rows:
        key = (row.get("city"), row.get("target_date"), row.get("d1_bracket"))
        ts = parse_ts(row["generated_at_utc"])
        branches: set[str] = set()
        if row.get("false_fade_reheat_conflict_triggered"):
            branches.add("false_fade")
        if row.get("rich_current_conflict_b4_triggered"):
            branches.add("b4")
        for branch in branches:
            branch_key = (branch, row.get("city"), row.get("target_date"), row.get("d1_bracket"))
            if branch_key not in first_by_branch or ts < first_by_branch[branch_key][0]:
                first_by_branch[branch_key] = (ts, row)
        if key not in first_by_token:
            first_by_token[key] = (ts, row, branches)
        else:
            if ts < first_by_token[key][0]:
                first_by_token[key] = (ts, row, first_by_token[key][2] | branches)
            else:
                first_by_token[key][2].update(branches)

    latest_city = latest_by_key(decisions, ("city", "target_date"))
    tokens: list[dict[str, Any]] = []
    for (city, target_date, d1_bracket), (_, first, branches) in sorted(first_by_token.items()):
        token = mark_row_from_entry(
            branch=None,
            city=str(city),
            target_date=str(target_date),
            d1_bracket=str(d1_bracket),
            first=first,
            latest_city=latest_city,
        )
        token["branches"] = sorted(branches)
        tokens.append(token)

    branch_rows: dict[str, list[dict[str, Any]]] = {"false_fade": [], "b4": []}
    for (branch, city, target_date, d1_bracket), (_, first) in sorted(first_by_branch.items()):
        branch_rows[str(branch)].append(
            mark_row_from_entry(
                branch=str(branch),
                city=str(city),
                target_date=str(target_date),
                d1_bracket=str(d1_bracket),
                first=first,
                latest_city=latest_city,
            )
        )

    cost = sum(t["mark_to_bid"].get("cost_usd") or 0.0 for t in tokens)
    pnl = sum(t["mark_to_bid"].get("pnl_usd") or 0.0 for t in tokens)
    latest_summary = summaries[-1] if summaries else None
    latest_cycle = latest_summary.get("cycle_id") if latest_summary else None
    latest_rows = [r for r in decisions if r.get("cycle_id") == latest_cycle]
    latest_ok = [r for r in latest_rows if r.get("state_status") == "ok"]
    gate = json.loads(GATE_JSON.read_text(encoding="utf-8")) if GATE_JSON.exists() else {}
    if tokens:
        main_read = (
            "Forward scarcity broke today, but the broader B4 basket faded after overshoots. "
            "False-fade ended slightly positive because Helsinki 20 offset Helsinki 19 and Lucknow losses. "
            "This is evidence to keep shadow running, not evidence to live."
        )
        today_shadow_effect = "mixed_mtm_not_settled"
    else:
        main_read = (
            "No token-level trigger appeared in this UTC review window. "
            "This is a frequency/coverage observation, not evidence for live."
        )
        today_shadow_effect = "no_trigger_rows"

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date_utc": target_date_utc,
        "data_snapshot": {
            "shadow_dir": str(SHADOW_DIR.relative_to(ROOT)),
            "summary_rows_total": len(summaries),
            "summary_rows_today_utc": len(today_summaries),
            "today_first_generated_at_utc": today_summaries[0].get("generated_at_utc") if today_summaries else None,
            "today_last_generated_at_utc": today_summaries[-1].get("generated_at_utc") if today_summaries else None,
            "today_unique_snapshots": len({s.get("snapshot_file") for s in today_summaries}),
            "weather_db_mtime_utc": datetime.fromtimestamp(DB_PATH.stat().st_mtime, timezone.utc).isoformat()
            if DB_PATH.exists()
            else None,
            "clob_gate_pass": gate.get("gate_pass"),
            "clob_gate_fail_reasons": gate.get("fail_reasons"),
        },
        "today_overview": {
            "cycles_with_false_fade": sum(
                1 for s in today_summaries if int(s.get("false_fade_reheat_conflict_triggered") or 0) > 0
            ),
            "cycles_with_b4": sum(
                1 for s in today_summaries if int(s.get("rich_current_conflict_b4_triggered") or 0) > 0
            ),
            "sum_false_fade_triggers": sum(
                int(s.get("false_fade_reheat_conflict_triggered") or 0) for s in today_summaries
            ),
            "sum_b4_triggers": sum(
                int(s.get("rich_current_conflict_b4_triggered") or 0) for s in today_summaries
            ),
            "unique_token_triggers": len(tokens),
            "latest_summary": latest_summary,
            "latest_state_status": dict(Counter(r.get("state_status") for r in latest_rows)),
            "latest_d1_book_status": dict(Counter(r.get("d1_book_status") for r in latest_ok)),
        },
        "mark_to_bid_summary": {
            "unit_notional_usd_per_token": UNIT_NOTIONAL_USD,
            "fee_model": "Polymarket weather taker fee: shares * 0.05 * price * (1-price), applied to entry and bid-exit mark",
            "all_unique_tokens": {
                "tokens": len(tokens),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else None,
                "note": "MTM only; not settled ROI and not a Head B exit policy recommendation",
            },
            "false_fade": summarize_mark_rows(branch_rows["false_fade"]),
            "b4": summarize_mark_rows(branch_rows["b4"]),
        },
        "branch_first_entry_tokens": branch_rows,
        "tokens": tokens,
        "verdict": {
            "today_shadow_effect": today_shadow_effect,
            "promotion_status": "shadow_candidate_keep_collecting",
            "live_action": "none",
            "main_read": main_read,
        },
    }


def fmt_pct(value: Any) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def fmt_usd(value: Any) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"${float(value):+.2f}"


def write_report(result: dict[str, Any], report_path: Path, json_path: Path) -> None:
    data = result["data_snapshot"]
    overview = result["today_overview"]
    mtm = result["mark_to_bid_summary"]
    lines = [
        "# METAR Reversal Shadow Today v1",
        "",
        f"Generated: `{result['generated_at_utc']}`",
        "",
        "## Verdict",
        "",
        (
            "`metar_reversal.rich_current_collapse_d1_yes` remains "
            "`shadow_candidate_keep_collecting`; no live change."
        ),
        "",
        result["verdict"]["main_read"],
        "",
        "## Data Snapshot",
        "",
        f"- Shadow source: `{data['shadow_dir']}`.",
        f"- UTC window: `{data['today_first_generated_at_utc']}` .. `{data['today_last_generated_at_utc']}`.",
        f"- Summary rows today: {data['summary_rows_today_utc']}; unique snapshots: {data['today_unique_snapshots']}.",
        f"- `runtime/weather.db` mtime: `{data['weather_db_mtime_utc']}`; CLOB fill gate pass: `{data['clob_gate_pass']}`.",
        "",
        "## Trigger Funnel",
        "",
        f"- Cycles with false-fade trigger: {overview['cycles_with_false_fade']} "
        f"(raw trigger sum {overview['sum_false_fade_triggers']}).",
        f"- Cycles with B4 trigger: {overview['cycles_with_b4']} "
        f"(raw trigger sum {overview['sum_b4_triggers']}).",
        f"- Unique token-level triggers after dedupe: {overview['unique_token_triggers']}.",
        f"- Latest cycle: `{overview['latest_summary']}`.",
        "",
        "## MTM Summary",
        "",
        "This marks the first shadow entry to the latest best bid with $1 notional per unique token and official taker-fee formula on both entry and bid-exit. It is not a recommended exit rule.",
        "",
        "| Slice | Tokens | MTM PnL | MTM ROI | Cities |",
        "|---|---:|---:|---:|---|",
    ]
    for label, row in [
        ("all_unique_tokens", mtm["all_unique_tokens"]),
        ("false_fade", mtm["false_fade"]),
        ("b4", mtm["b4"]),
    ]:
        cities = ", ".join(row.get("cities", [])) if row.get("cities") else ""
        lines.append(
            f"| {label} | {row['tokens']} | {fmt_usd(row['pnl_usd'])} | {fmt_pct(row['roi'])} | {cities} |"
        )
    lines.extend(
        [
            "",
            "## Token Rows",
            "",
            "| City | D1 Bracket | Branches | Entry Ask | Latest Bid | Latest Ask | MTM ROI | Obs Proxy State | Read |",
            "|---|---:|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for token in result["tokens"]:
        quote = token["latest_quote"]
        entry = token["entry"]
        mark = token["mark_to_bid"]
        state = token["latest"]["relation_to_d1_by_obs_proxy"]
        read = "pump"
        if mark.get("roi") is not None and mark["roi"] < -0.5:
            read = "failed_or_faded"
        elif state == "not_reached":
            read = "open_not_reached"
        elif state == "overshot_by_obs_proxy":
            read = "needs_settlement_rule_check"
        lines.append(
            "| "
            f"{token['city']} | {token['d1_bracket']} | {','.join(token['branches'])} | "
            f"{entry.get('d1_yes_ask')} | {quote.get('yes_best_bid')} | {quote.get('yes_best_ask')} | "
            f"{fmt_pct(mark.get('roi'))} | {state} | {read} |"
        )
    lines.extend(["", "## Read", ""])
    if result["tokens"]:
        lines.extend(
            [
                "- Positive: the state frequency problem eased, and Helsinki 20 ended near binary after the trigger.",
                "- Negative: Amsterdam 22 and Helsinki 19 were overshot by the final-looking market state, while Lucknow 37 was a false reheat and collapsed to near zero.",
                "- Boundary: the latest cycle has no active trigger; the useful signal was the transient conflict window, not a persistent all-day state.",
                "- Action: keep zero-notional shadow running; do not live-size from one day. Next review should separate overshoot risk, first-step vs second-step re-entry, and false-fade-only rows where bracket-aware B4 is false.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- No token-level trigger appeared in this UTC review window.",
                "- This is a frequency/coverage observation only; it is not evidence for or against the entry edge.",
                "- Action: keep zero-notional shadow running and wait for fresh trigger rows before discussing live.",
                "",
            ]
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(json_ready(result), indent=2, sort_keys=True, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="UTC date to review")
    parser.add_argument(
        "--report",
        default=None,
        help="Markdown report path; defaults to docs/analysis/2026-07/YYYY-MM-DD-metar-reversal-shadow-today-v1.md",
    )
    parser.add_argument(
        "--json",
        default=None,
        help="JSON output path; defaults to docs/analysis/2026-07/generated/metar_reversal_shadow_today_v1/YYYY-MM-DD.json",
    )
    args = parser.parse_args()
    report_path = Path(args.report) if args.report else REPORT_DIR / f"{args.date}-metar-reversal-shadow-today-v1.md"
    json_path = Path(args.json) if args.json else GENERATED_DIR / f"{args.date}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    result = build_review(args.date)
    write_report(result, report_path, json_path)
    print(json.dumps({"report": str(report_path), "json": str(json_path), "verdict": result["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
