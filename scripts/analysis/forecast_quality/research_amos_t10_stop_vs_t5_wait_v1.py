#!/usr/bin/env python3
"""Compare T-10 entry/T-5 stop with waiting until T-5 for AMOS previous-NO.

The fixed opportunity universe is a report-cycle / previous-bracket row where
the source-only persistent cross is present and ten shares are executable at
the first qualified fresh quote in T-12..8.  Both policies then use the same
first quote in T-6..4 and the same PIT AMOS state.  This is research replay only.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_amos_entry_timing_tradeoff_v1 as timing  # noqa: E402


OUT = ROOT / "docs/analysis/2026-07/generated/amos_t10_stop_vs_t5_wait_v1"
SHARES = Decimal("10")
FEE_RATE = Decimal("0.05")
FEE_QUANT = Decimal("0.00001")
SOURCE_MAX_AGE_SEC = 180
BOOTSTRAP_DRAWS = 10_000
T10_WINDOW_START_MIN = 12
T10_WINDOW_END_MIN = 8
T5_WINDOW_START_MIN = 6
T5_WINDOW_END_MIN = 4


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def taker_fee(shares: Decimal, price: Decimal) -> Decimal:
    return (shares * FEE_RATE * price * (Decimal("1") - price)).quantize(
        FEE_QUANT, rounding=ROUND_HALF_UP
    )


def fmt_dec(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_cycles(aligned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in aligned:
        key = (
            row["city"], row["target_date"], int(row["prior_max_c"]),
            row["report_ts"].isoformat(), row["report_detect_ts"].isoformat(),
        )
        grouped[key].append(row)
    output: list[dict[str, Any]] = []
    for index, (key, rows) in enumerate(sorted(grouped.items()), start=1):
        rows.sort(key=lambda row: (row["detect_ts"], row["obs_ts"]))
        city, target_date, prior_max_c, report_ts, report_detect_ts = key
        output.append({
            "cycle_id": index,
            "city": city,
            "target_date": target_date,
            "prior_max_c": prior_max_c,
            "threshold_c": prior_max_c + 0.5,
            "report_ts": timing.parse_dt(report_ts),
            "report_detect_ts": timing.parse_dt(report_detect_ts),
            "next_metar_crossed": int(rows[0]["next_crossed"]),
            "source_rows": rows,
        })
    return output


def source_state(cycle: dict[str, Any], decision_ts: datetime) -> dict[str, Any]:
    seen = [row for row in cycle["source_rows"] if row["detect_ts"] <= decision_ts]
    if not seen:
        return {
            "source_covered": 0, "source_fresh": 0, "source_qualified": 0,
            "latest_above_threshold": 0, "active_distinct_obs": 0,
        }
    latest = seen[-1]
    threshold = float(cycle["threshold_c"])
    active: list[dict[str, Any]] = []
    for row in reversed(seen):
        if float(row["temp_c"]) < threshold - 1e-12:
            break
        active.append(row)
    active.reverse()
    distinct_obs = len({row["obs_ts"] for row in active})
    source_age_sec = (decision_ts - latest["detect_ts"]).total_seconds()
    fresh = 0 <= source_age_sec <= SOURCE_MAX_AGE_SEC
    latest_above = float(latest["temp_c"]) >= threshold - 1e-12
    latest_strong = float(latest["temp_c"]) >= float(cycle["prior_max_c"]) + 0.7 - 1e-12
    peak = max((float(row["temp_c"]) for row in active), default=float(latest["temp_c"]))
    return {
        "source_covered": 1,
        "source_fresh": int(fresh),
        "source_age_sec": round(source_age_sec, 3),
        "latest_detect_ts_utc": latest["detect_ts"].isoformat(),
        "latest_obs_ts_utc": latest["obs_ts"].isoformat(),
        "latest_temp_c": float(latest["temp_c"]),
        "threshold_c": threshold,
        "active_distinct_obs": distinct_obs,
        "active_peak_c": peak,
        "active_drawdown_c": round(peak - float(latest["temp_c"]), 3),
        "latest_above_threshold": int(fresh and latest_above),
        "latest_strong": int(fresh and latest_strong),
        "source_qualified": int(fresh and distinct_obs >= 2 and latest_strong),
    }


def first_quote_in_window(
    quotes: list[dict[str, Any]], cycle: dict[str, Any],
    start_ts: datetime, end_ts: datetime, require_source_qualified: bool,
) -> dict[str, Any] | None:
    for quote in quotes:
        if quote["city"] != cycle["city"] or quote["target_date"] != cycle["target_date"]:
            continue
        if int(quote["prior_max_c"]) != int(cycle["prior_max_c"]):
            continue
        if quote["ts"] < start_ts:
            continue
        if quote["ts"] > end_ts:
            break
        if require_source_qualified and not source_state(cycle, quote["ts"])["source_qualified"]:
            continue
        return quote
    return None


def first_invalidation_quote(
    quotes: list[dict[str, Any]], cycle: dict[str, Any], start_ts: datetime, end_ts: datetime,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for quote in quotes:
        if quote["city"] != cycle["city"] or quote["target_date"] != cycle["target_date"]:
            continue
        if int(quote["prior_max_c"]) != int(cycle["prior_max_c"]):
            continue
        if quote["ts"] <= start_ts:
            continue
        if quote["ts"] > end_ts:
            break
        state = source_state(cycle, quote["ts"])
        if state.get("source_fresh") and not state.get("latest_above_threshold"):
            return quote, state
    return None


def quote_fields(prefix: str, quote: dict[str, Any] | None, report_ts: datetime) -> dict[str, Any]:
    if quote is None:
        return {
            f"{prefix}_quote_covered": 0, f"{prefix}_quote_ts_utc": "",
            f"{prefix}_minutes_to_report": "", f"{prefix}_bid": "",
            f"{prefix}_bid_size": "", f"{prefix}_ask": "", f"{prefix}_ask_size": "",
        }
    return {
        f"{prefix}_quote_covered": 1,
        f"{prefix}_quote_ts_utc": quote["ts"].isoformat(),
        f"{prefix}_minutes_to_report": round((report_ts - quote["ts"]).total_seconds() / 60, 3),
        f"{prefix}_bid": quote.get("bid"), f"{prefix}_bid_size": quote.get("bid_size"),
        f"{prefix}_ask": quote.get("ask"), f"{prefix}_ask_size": quote.get("ask_size"),
    }


def entry_economics(ask_raw: Any, ask_size_raw: Any) -> dict[str, Any]:
    if ask_raw in (None, ""):
        return {"entry_executable10": 0, "entry_notional": "", "entry_fee": "", "entry_cash_cost": ""}
    ask = dec(ask_raw)
    ask_size = None if ask_size_raw is None else dec(ask_size_raw)
    executable = ask <= Decimal("0.97") and ask_size is not None and ask_size >= SHARES
    fee = taker_fee(SHARES, ask)
    return {
        "entry_executable10": int(executable),
        "entry_notional": fmt_dec(SHARES * ask),
        "entry_fee": fmt_dec(fee),
        "entry_cash_cost": fmt_dec(SHARES * ask + fee),
    }


def hold_pnl(ask_raw: Any, final_no_win: int | None) -> Decimal | None:
    if ask_raw in (None, "") or final_no_win is None:
        return None
    ask = dec(ask_raw)
    return SHARES * dec(final_no_win) - (SHARES * ask + taker_fee(SHARES, ask))


def stop_pnl(ask_raw: Any, bid_raw: Any) -> Decimal | None:
    if ask_raw in (None, "") or bid_raw in (None, ""):
        return None
    ask = dec(ask_raw)
    bid = dec(bid_raw)
    return SHARES * bid - taker_fee(SHARES, bid) - (SHARES * ask + taker_fee(SHARES, ask))


def replay_rows(
    cycles: list[dict[str, Any]], quotes: list[dict[str, Any]], final_labels: dict[tuple[str, str, int], int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for cycle in cycles:
        t10_window_start = cycle["report_ts"] - timedelta(minutes=T10_WINDOW_START_MIN)
        t10_window_end = cycle["report_ts"] - timedelta(minutes=T10_WINDOW_END_MIN)
        t5_window_start = cycle["report_ts"] - timedelta(minutes=T5_WINDOW_START_MIN)
        t5_window_end = cycle["report_ts"] - timedelta(minutes=T5_WINDOW_END_MIN)
        t10_quote = first_quote_in_window(
            quotes, cycle, t10_window_start, t10_window_end, require_source_qualified=True
        )
        t5_quote = first_quote_in_window(
            quotes, cycle, t5_window_start, t5_window_end, require_source_qualified=False
        )
        t10_state = source_state(cycle, t10_quote["ts"] if t10_quote else t10_window_end)
        t5_state = source_state(cycle, t5_quote["ts"] if t5_quote else t5_window_end)
        final_no_win = final_labels.get((cycle["city"], cycle["target_date"], int(cycle["prior_max_c"])))
        row: dict[str, Any] = {
            "cycle_id": cycle["cycle_id"], "city": cycle["city"],
            "target_date": cycle["target_date"], "prior_max_c": cycle["prior_max_c"],
            "threshold_c": cycle["threshold_c"],
            "next_report_ts_utc": cycle["report_ts"].isoformat(),
            "next_metar_crossed": cycle["next_metar_crossed"],
            "final_no_win": "" if final_no_win is None else final_no_win,
            **quote_fields("t10", t10_quote, cycle["report_ts"]),
            **{f"t10_{key}": value for key, value in t10_state.items()},
            **quote_fields("t5", t5_quote, cycle["report_ts"]),
            **{f"t5_{key}": value for key, value in t5_state.items()},
        }
        row.update(entry_economics(row.get("t10_ask"), row.get("t10_ask_size")))
        cohort = bool(t10_quote and t10_state["source_qualified"] and row["entry_executable10"])
        row["t10_executable_signal_cohort"] = int(cohort)

        dynamic_stop_result = None
        if cohort and t10_quote:
            dynamic_end = t5_quote["ts"] if t5_quote else t5_window_end
            dynamic_stop_result = first_invalidation_quote(quotes, cycle, t10_quote["ts"], dynamic_end)
        dynamic_quote = None if dynamic_stop_result is None else dynamic_stop_result[0]
        row.update(quote_fields("dynamic_stop", dynamic_quote, cycle["report_ts"]))

        if not cohort:
            row.update({
                "wait_t5_action": "outside_t10_cohort", "wait_t5_pnl": "",
                "early_hold_action": "outside_t10_cohort", "early_hold_pnl": "",
                "early_stop_action": "outside_t10_cohort", "early_stop_pnl_full_top": "",
                "early_stop_full_exit_executable": 0,
                "dynamic_stop_action": "outside_t10_cohort", "dynamic_stop_pnl_full_top": "",
                "dynamic_stop_full_exit_executable": 0,
            })
            output.append(row)
            continue

        early_hold = hold_pnl(row["t10_ask"], final_no_win)
        row["early_hold_action"] = "hold_to_final" if final_no_win is not None else "hold_unsettled"
        row["early_hold_pnl"] = fmt_dec(early_hold)

        if dynamic_quote is not None:
            dynamic_mark = stop_pnl(row["t10_ask"], dynamic_quote.get("bid"))
            dynamic_full = (
                dynamic_quote.get("bid") is not None
                and dynamic_quote.get("bid_size") is not None
                and dec(dynamic_quote["bid_size"]) >= SHARES
            )
            row["dynamic_stop_action"] = "stop_full_top" if dynamic_full else "stop_top_depth_insufficient"
            row["dynamic_stop_pnl_full_top"] = fmt_dec(dynamic_mark)
            row["dynamic_stop_full_exit_executable"] = int(dynamic_full)
            row["dynamic_stop_exit_fee"] = (
                "" if dynamic_quote.get("bid") is None
                else fmt_dec(taker_fee(SHARES, dec(dynamic_quote["bid"])))
            )
        else:
            row["dynamic_stop_action"] = "hold_no_invalidation" if final_no_win is not None else "hold_unsettled"
            row["dynamic_stop_pnl_full_top"] = fmt_dec(early_hold)
            row["dynamic_stop_full_exit_executable"] = 0

        if not t5_quote or not t5_state["source_covered"] or not t5_state["source_fresh"]:
            row.update({
                "wait_t5_action": "t5_coverage_gap", "wait_t5_pnl": "",
                "early_stop_action": "t5_coverage_gap", "early_stop_pnl_full_top": "",
                "early_stop_full_exit_executable": 0,
            })
            output.append(row)
            continue

        survived = bool(t5_state["latest_above_threshold"])
        row["t5_signal_survived"] = int(survived)

        if survived:
            t5_entry = entry_economics(row.get("t5_ask"), row.get("t5_ask_size"))
            row.update({f"wait_t5_{key}": value for key, value in t5_entry.items()})
            if t5_entry["entry_executable10"]:
                wait_pnl = hold_pnl(row["t5_ask"], final_no_win)
                row["wait_t5_action"] = "enter_hold_final" if final_no_win is not None else "enter_hold_unsettled"
                row["wait_t5_pnl"] = fmt_dec(wait_pnl)
            else:
                row["wait_t5_action"] = "no_trade_t5_ask_unexecutable"
                row["wait_t5_pnl"] = "0"
            row["early_stop_action"] = "hold_signal_survived" if final_no_win is not None else "hold_unsettled"
            row["early_stop_pnl_full_top"] = fmt_dec(early_hold)
            row["early_stop_full_exit_executable"] = 0
        else:
            row["wait_t5_action"] = "no_trade_signal_invalidated"
            row["wait_t5_pnl"] = "0"
            bid = row.get("t5_bid")
            bid_size = row.get("t5_bid_size")
            marked_stop = stop_pnl(row["t10_ask"], bid)
            full_exit = bid not in (None, "") and bid_size not in (None, "") and dec(bid_size) >= SHARES
            row["early_stop_action"] = "stop_full_top" if full_exit else "stop_top_depth_insufficient"
            row["early_stop_pnl_full_top"] = fmt_dec(marked_stop)
            row["early_stop_full_exit_executable"] = int(full_exit)
            row["early_stop_exit_fee"] = "" if bid in (None, "") else fmt_dec(taker_fee(SHARES, dec(bid)))
        output.append(row)
    return output


def aggregate_policy(rows: list[dict[str, Any]], policy: str, optimistic_stop: bool) -> dict[str, Any]:
    cohort = [row for row in rows if row.get("t10_executable_signal_cohort") == 1]
    covered = [row for row in cohort if row.get("wait_t5_action") != "t5_coverage_gap"]
    pnl_rows: list[tuple[dict[str, Any], Decimal, Decimal]] = []
    unknown = 0
    trades = 0
    stops = 0
    for row in covered:
        if policy == "wait_t5":
            action = str(row["wait_t5_action"])
            if action.startswith("enter_hold"):
                trades += 1
                cost = dec(row["wait_t5_entry_cash_cost"])
            else:
                cost = Decimal("0")
            raw_pnl = row["wait_t5_pnl"]
        elif policy == "early_hold":
            trades += 1
            cost = dec(row["entry_cash_cost"])
            raw_pnl = row["early_hold_pnl"]
        elif policy == "early_stop":
            trades += 1
            cost = dec(row["entry_cash_cost"])
            action = str(row["early_stop_action"])
            if action.startswith("stop_"):
                stops += 1
                if not optimistic_stop and not row.get("early_stop_full_exit_executable"):
                    raw_pnl = ""
                else:
                    raw_pnl = row["early_stop_pnl_full_top"]
            else:
                raw_pnl = row["early_stop_pnl_full_top"]
        else:
            trades += 1
            cost = dec(row["entry_cash_cost"])
            action = str(row["dynamic_stop_action"])
            if action.startswith("stop_"):
                stops += 1
                if not optimistic_stop and not row.get("dynamic_stop_full_exit_executable"):
                    raw_pnl = ""
                else:
                    raw_pnl = row["dynamic_stop_pnl_full_top"]
            else:
                raw_pnl = row["dynamic_stop_pnl_full_top"]
        if raw_pnl in (None, ""):
            unknown += 1
            continue
        pnl_rows.append((row, dec(raw_pnl), cost))
    total_pnl = sum((pnl for _, pnl, _ in pnl_rows), Decimal("0"))
    total_cost = sum((cost for _, _, cost in pnl_rows), Decimal("0"))
    return {
        "policy": policy + ("_optimistic_top_mark" if policy in {"early_stop", "dynamic_stop"} and optimistic_stop else ""),
        "cohort_rows": len(cohort), "t5_covered_rows": len(covered),
        "known_pnl_rows": len(pnl_rows), "unknown_pnl_rows": unknown,
        "trades": trades, "stops": stops,
        "independent_dates": len({row["target_date"] for row, _, _ in pnl_rows}),
        "cash_cost": fmt_dec(total_cost), "pnl": fmt_dec(total_pnl),
        "roi": "" if not total_cost else str(round(float(total_pnl / total_cost), 6)),
        "pnl_per_cohort": "" if not pnl_rows else str(round(float(total_pnl / len(pnl_rows)), 6)),
        "wins": sum(int(pnl > 0) for _, pnl, _ in pnl_rows),
    }


def paired_bootstrap(rows: list[dict[str, Any]], pnl_field: str) -> dict[str, Any]:
    paired: list[dict[str, Any]] = []
    for row in rows:
        if row.get("t10_executable_signal_cohort") != 1 or row.get("wait_t5_action") == "t5_coverage_gap":
            continue
        if row.get("wait_t5_pnl") in (None, "") or row.get(pnl_field) in (None, ""):
            continue
        paired.append(row)
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in paired:
        by_date[row["target_date"]].append(row)
    dates = sorted(by_date)
    if not dates:
        return {"paired_rows": 0, "dates": 0}
    observed = sum(
        dec(row[pnl_field]) - dec(row["wait_t5_pnl"])
        for row in paired
    ) / dec(len(paired))
    rng = random.Random(20260720)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled_dates = [rng.choice(dates) for _ in dates]
        sampled_rows = [row for target_date in sampled_dates for row in by_date[target_date]]
        delta = sum(
            dec(row[pnl_field]) - dec(row["wait_t5_pnl"])
            for row in sampled_rows
        ) / dec(len(sampled_rows))
        samples.append(float(delta))
    samples.sort()
    return {
        "paired_rows": len(paired), "dates": len(dates),
        "pnl_field": pnl_field,
        "mean_pnl_delta_stop_minus_wait": str(round(float(observed), 6)),
        "date_block_bootstrap_ci95": [
            round(samples[int(0.025 * (len(samples) - 1))], 6),
            round(samples[int(0.975 * (len(samples) - 1))], 6),
        ],
        "bootstrap_draws": BOOTSTRAP_DRAWS,
    }


def diagnostic_summary(rows: list[dict[str, Any]], city: str) -> dict[str, Any]:
    scoped = rows if city == "ALL" else [row for row in rows if row["city"] == city]
    quoted_signal = [
        row for row in scoped
        if row.get("t10_quote_covered") == 1 and row.get("t10_source_qualified") == 1
    ]
    checkpoint = [
        row for row in quoted_signal
        if row.get("t5_quote_covered") == 1 and row.get("t5_source_fresh") == 1
    ]
    survived = [row for row in checkpoint if row.get("t5_latest_above_threshold") == 1]
    invalidated = [row for row in checkpoint if row.get("t5_latest_above_threshold") == 0]
    paired_asks = [
        row for row in survived
        if row.get("t10_ask") not in (None, "") and row.get("t5_ask") not in (None, "")
    ]
    ask_deltas = [float(row["t5_ask"]) - float(row["t10_ask"]) for row in paired_asks]
    top_removed = list(ask_deltas)
    if top_removed:
        top_removed.remove(max(top_removed))
    return {
        "city": city,
        "t10_quoted_source_signal_rows": len(quoted_signal),
        "t10_quoted_source_signal_dates": len({row["target_date"] for row in quoted_signal}),
        "t10_executable10_rows": sum(int(row.get("entry_executable10", 0)) for row in quoted_signal),
        "t10_ask_ge_0p97_rows": sum(
            row.get("t10_ask") not in (None, "") and dec(row["t10_ask"]) >= Decimal("0.97")
            for row in quoted_signal
        ),
        "t5_fresh_checkpoint_rows": len(checkpoint),
        "survived_rows": len(survived), "invalidated_rows": len(invalidated),
        "paired_ask_rows": len(paired_asks),
        "paired_ask_dates": len({row["target_date"] for row in paired_asks}),
        "median_t5_minus_t10_ask": None if not ask_deltas else round(statistics.median(ask_deltas), 6),
        "mean_t5_minus_t10_ask": None if not ask_deltas else round(statistics.mean(ask_deltas), 6),
        "top_increase_removed_mean_t5_minus_t10_ask": (
            None if not top_removed else round(statistics.mean(top_removed), 6)
        ),
        "full_top_exit_invalidations": sum(
            int(row.get("dynamic_stop_full_exit_executable", 0)) for row in invalidated
        ),
    }


def main() -> int:
    aligned = timing.load_alignment()
    quotes = timing.load_quotes()
    labels = timing.load_final_labels()
    cycles = build_cycles(aligned)
    rows = replay_rows(cycles, quotes, labels)
    cohort = [row for row in rows if row.get("t10_executable_signal_cohort") == 1]
    summaries: list[dict[str, Any]] = []
    for city in ["ALL", "Busan", "Seoul"]:
        city_rows = rows if city == "ALL" else [row for row in rows if row["city"] == city]
        for policy, optimistic in [
            ("wait_t5", False), ("early_hold", False),
            ("early_stop", False), ("early_stop", True),
            ("dynamic_stop", False), ("dynamic_stop", True),
        ]:
            summaries.append({"city": city, **aggregate_policy(city_rows, policy, optimistic)})
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": f"{timing.START_DATE}..{timing.END_DATE}",
        "cities": sorted(timing.CITIES), "shares": str(SHARES),
        "fee_rate": str(FEE_RATE),
        "t10_window_minutes": [T10_WINDOW_START_MIN, T10_WINDOW_END_MIN],
        "t5_window_minutes": [T5_WINDOW_START_MIN, T5_WINDOW_END_MIN],
        "source_max_age_sec": SOURCE_MAX_AGE_SEC,
        "aligned_rows": len(aligned), "report_cycles": len(cycles),
        "t10_executable_signal_cohort": len(cohort),
        "t10_cohort_dates": len({row["target_date"] for row in cohort}),
        "policy_summary": summaries,
        "diagnostic_summary": [diagnostic_summary(rows, city) for city in ["ALL", "Busan", "Seoul"]],
        "paired_optimistic_t5_stop_vs_wait": paired_bootstrap(rows, "early_stop_pnl_full_top"),
        "paired_optimistic_dynamic_stop_vs_wait": paired_bootstrap(rows, "dynamic_stop_pnl_full_top"),
        "warnings": [
            "Same-sample exploratory replay; not a live gate.",
            "Optimistic stop assumes all 10 shares fill at displayed best bid even when top depth is below 10; executable-stop summary does not.",
            "Final WU labels are incomplete after 2026-07-17; Busan 2026-07-20 is provisional weather-confirmed.",
            "Quote capture is observer-driven and is an evidence gap, not a strategy filter.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "policy_rows.csv", rows)
    write_csv(OUT / "policy_summary.csv", summaries)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
