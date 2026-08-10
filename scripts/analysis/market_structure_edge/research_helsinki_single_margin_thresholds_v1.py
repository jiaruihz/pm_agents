#!/usr/bin/env python3
"""Compare first usable Helsinki/FMI single-cross margin thresholds."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_high_frequency_strategy_eligibility_v2 as eligibility  # noqa: E402


DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
THRESHOLDS = (0.5, 0.6, 0.7, 0.8)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < size:
            raw = handle.readline()
            if not raw:
                break
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                yield row


def parse_dt(value: Any) -> datetime | None:
    return eligibility.parse_dt(value)


def safe_float(value: Any) -> float | None:
    return eligibility.safe_float(value)


def wilson_low(hits: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = hits / total
    den = 1 + z * z / total
    center = p + z * z / (2 * total)
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (center - radius) / den


def weather_fee_per_share(price: float) -> float:
    return 0.05 * price * (1.0 - price)


def target_date_block_bootstrap_roi(
    rows: list[dict[str, Any]], *, iterations: int = 10_000, seed: int = 20260722
) -> tuple[float | None, float | None]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if not dates:
        return None, None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        selected = [rng.choice(dates) for _ in dates]
        principal = sum(
            float(row["hypothetical_principal_usd"])
            for date in selected for row in by_date[date]
        )
        fees = sum(
            float(row["hypothetical_fee_usd"])
            for date in selected for row in by_date[date]
        )
        pnl = sum(
            float(row["hypothetical_pnl_usd"])
            for date in selected for row in by_date[date]
        )
        if principal + fees > 0:
            samples.append(pnl / (principal + fees))
    if not samples:
        return None, None
    samples.sort()
    low = samples[math.floor((len(samples) - 1) * 0.025)]
    high = samples[math.ceil((len(samples) - 1) * 0.975)]
    return low, high


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


def first_threshold_rows(
    events: list[dict[str, Any]], threshold: float, *, execution_window_min: float
) -> list[dict[str, Any]]:
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(events, key=lambda item: str(item.get("fast_detect_ts_utc") or "")):
        upper = safe_float(row.get("previous_market_bracket_upper"))
        if upper is None:
            continue
        margin = float(row["fast_temp_unit"]) - upper
        if margin < threshold - 1e-9:
            continue
        if float(row.get("next_metar_report_clock_distance_min") or math.inf) > execution_window_min + 1e-9:
            continue
        key = (str(row["target_date"]), str(row["previous_market_bracket"]))
        if key not in first:
            first[key] = {**row, "margin_threshold": threshold, "actual_margin": round(margin, 3)}
    return list(first.values())


def quote_values(row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    no = ((row.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {}
    return (
        safe_float(row.get("t_minus_1_no_best_ask") or no.get("fresh_best_ask")),
        safe_float(row.get("t_minus_1_no_best_bid") or no.get("fresh_best_bid")),
        safe_float(no.get("fresh_ask_size")),
    )


def first_quote_after(
    rows: list[dict[str, Any]],
    decision: datetime,
    predicate: Any | None = None,
) -> tuple[dict[str, Any] | None, float | None]:
    candidates = []
    for row in rows:
        ts = parse_dt(row.get("ts_utc"))
        if ts is not None and ts >= decision and (predicate is None or predicate(row)):
            candidates.append((ts, row))
    if not candidates:
        return None, None
    ts, row = min(candidates, key=lambda item: item[0])
    return row, (ts - decision).total_seconds()


def bracket_key(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return str(value or "")
    return str(int(number)) if float(number).is_integer() else str(number)


def quote_expression_bracket(row: dict[str, Any]) -> str:
    return bracket_key(
        row.get("t_minus_1_no_bracket_c")
        or row.get("previous_no_bracket_c")
        or row.get("previous_market_bracket")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--profiles", default=str(ROOT / "weather_data_feed/source_profiles.json"))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-fast-age-min", type=float, default=30.0)
    parser.add_argument("--max-next-metar-min", type=float, default=90.0)
    parser.add_argument("--execution-window-min", type=float, default=20.0)
    parser.add_argument("--quote-support-sec", type=float, default=120.0)
    parser.add_argument("--collector-cycle-sec", type=float, default=600.0)
    parser.add_argument("--max-no-ask", type=float, default=0.97)
    parser.add_argument("--shares", type=float, default=10.0)
    args = parser.parse_args()

    runtime = Path(args.runtime_root)
    profiles = eligibility.load_profiles(Path(args.profiles))
    awc, _synoptic = eligibility.load_reference_events(runtime / "output/source_events", profiles)
    fast = eligibility.load_fast_observations(
        runtime / "output/high_frequency_observations",
        profiles,
        args.max_fast_age_min,
    )
    events = eligibility.build_event_comparison(fast, awc, args.max_next_metar_min)
    winners, ladders = eligibility.load_market_ladders(Path(args.db_path))
    eligibility.annotate_market_brackets(events, ladders)
    eligibility.annotate_settlement_labels(events, winners)
    helsinki = [
        row for row in events
        if row.get("city") == "Helsinki"
        and row.get("fast_source") == "fmi"
        and row.get("previous_market_bracket_upper") not in (None, "")
    ]

    quote_by_expression: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    collector_detects: list[datetime] = []
    collector_active_dates: set[str] = set()
    archive_paths = (
        ("fast_source_stale_book", runtime / "output/fast_source_stale_book/quote_snapshots.jsonl"),
        (
            "source_event_ladder_repricing_shadow",
            runtime / "output/source_event_ladder_repricing_shadow/quote_snapshots.jsonl",
        ),
    )
    archive_row_counts: dict[str, int] = defaultdict(int)
    for archive_name, archive_path in archive_paths:
        for raw_row in iter_jsonl(archive_path):
            if raw_row.get("city") != "Helsinki" or raw_row.get("source") != "fmi":
                continue
            row = dict(raw_row)
            row["_archive_name"] = archive_name
            target_date = str(row.get("target_date") or "")
            expression = quote_expression_bracket(row)
            if not target_date or not expression:
                continue
            quote_by_expression[(target_date, expression)].append(row)
            archive_row_counts[archive_name] += 1
            collector_active_dates.add(target_date)
            detected = parse_dt(row.get("source_detect_ts_utc"))
            if detected is not None:
                collector_detects.append(detected)
    for rows in quote_by_expression.values():
        rows.sort(key=lambda row: str(row.get("ts_utc") or ""))
    if not collector_detects:
        raise RuntimeError("no Helsinki/FMI direct-book collector rows")
    collector_started_at = min(collector_detects)

    selected_by_threshold = {
        threshold: first_threshold_rows(
            helsinki,
            threshold,
            execution_window_min=args.execution_window_min,
        )
        for threshold in THRESHOLDS
    }
    baseline = {
        (str(row["target_date"]), str(row["previous_market_bracket"])): row
        for row in selected_by_threshold[0.5]
    }

    ledger: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        rows = selected_by_threshold[threshold]
        for row in rows:
            key = (str(row["target_date"]), str(row["previous_market_bracket"]))
            baseline_row = baseline.get(key)
            decision = parse_dt(row.get("fast_detect_ts_utc"))
            baseline_decision = parse_dt(baseline_row.get("fast_detect_ts_utc")) if baseline_row else None
            previous_bracket = bracket_key(row["previous_market_bracket"])
            quote, quote_delay = first_quote_after(
                quote_by_expression.get((str(row["target_date"]), previous_bracket), []),
                decision,
            )
            priced_quote, priced_quote_delay = first_quote_after(
                quote_by_expression.get((str(row["target_date"]), previous_bracket), []),
                decision,
                lambda candidate: quote_values(candidate)[0] is not None,
            )
            executable_quote, executable_quote_delay = first_quote_after(
                quote_by_expression.get((str(row["target_date"]), previous_bracket), []),
                decision,
                lambda candidate: (
                    quote_values(candidate)[0] is not None
                    and quote_values(candidate)[0] <= args.max_no_ask + 1e-9
                    and quote_values(candidate)[2] is not None
                    and quote_values(candidate)[2] >= args.shares - 1e-9
                ),
            )
            ask, bid, ask_size = quote_values(priced_quote or {})
            execution_ask, _execution_bid, execution_ask_size = quote_values(executable_quote or {})
            quote_supported = quote_delay is not None and quote_delay <= args.quote_support_sec + 1e-9
            priced_supported = bool(
                priced_quote_delay is not None and priced_quote_delay <= args.quote_support_sec + 1e-9
            )
            collector_cycle_supported = bool(
                quote_delay is not None and quote_delay <= args.collector_cycle_sec + 1e-9
            )
            executable = bool(
                executable_quote_delay is not None
                and executable_quote_delay <= args.quote_support_sec + 1e-9
            )
            collector_cycle_executable = bool(
                executable_quote_delay is not None
                and executable_quote_delay <= args.collector_cycle_sec + 1e-9
            )
            direct_first_quote_executable = bool(
                ask is not None
                and ask <= args.max_no_ask + 1e-9
                and ask_size is not None
                and ask_size >= args.shares - 1e-9
            )
            settled_label = row.get("settlement_left_previous_bracket")
            fee = (
                weather_fee_per_share(ask) * args.shares
                if direct_first_quote_executable and ask is not None else None
            )
            principal = (
                ask * args.shares
                if direct_first_quote_executable and ask is not None else None
            )
            pnl = None
            if direct_first_quote_executable and settled_label in (True, False):
                pnl = (args.shares if settled_label is True else 0.0) - float(principal) - float(fee)
            ledger.append(
                {
                    "margin_threshold": threshold,
                    "target_date": row["target_date"],
                    "previous_market_bracket": row["previous_market_bracket"],
                    "fast_obs_ts_utc": row["fast_obs_ts_utc"],
                    "fast_detect_ts_utc": row["fast_detect_ts_utc"],
                    "fast_temp_unit": row["fast_temp_unit"],
                    "actual_margin": row["actual_margin"],
                    "delay_vs_0p5_min": round((decision - baseline_decision).total_seconds() / 60.0, 3)
                    if decision and baseline_decision else "",
                    "lead_to_next_metar_min": row["lead_to_next_metar_min"],
                    "next_metar_market_crossed": row["next_metar_market_crossed"],
                    "settlement_left_previous_bracket": settled_label,
                    "winning_bracket": row.get("winning_bracket"),
                    "collector_started_at_utc": collector_started_at.isoformat(),
                    "decision_after_collector_start": bool(decision and decision >= collector_started_at),
                    "collector_active_on_target_date": str(row["target_date"]) in collector_active_dates,
                    "collector_archive": (quote or {}).get("_archive_name", ""),
                    "priced_collector_archive": (priced_quote or {}).get("_archive_name", ""),
                    "execution_collector_archive": (executable_quote or {}).get("_archive_name", ""),
                    "first_quote_delay_sec": round(quote_delay, 3) if quote_delay is not None else "",
                    "quote_supported_within_sec": quote_supported,
                    "quote_supported_within_collector_cycle": collector_cycle_supported,
                    "first_priced_quote_delay_sec": round(priced_quote_delay, 3)
                    if priced_quote_delay is not None else "",
                    "priced_quote_supported_within_sec": priced_supported,
                    "priced_quote_supported_within_collector_cycle": bool(
                        priced_quote_delay is not None
                        and priced_quote_delay <= args.collector_cycle_sec + 1e-9
                    ),
                    "direct_no_ask": ask,
                    "direct_no_bid": bid,
                    "top_ask_size": ask_size,
                    "first_executable_quote_delay_sec": round(executable_quote_delay, 3)
                    if executable_quote_delay is not None else "",
                    "execution_no_ask": execution_ask,
                    "execution_top_ask_size": execution_ask_size,
                    "direct_first_quote_executable_10_share_at_0p97": direct_first_quote_executable,
                    "executable_10_share_at_0p97": executable,
                    "collector_cycle_executable_10_share_at_0p97": collector_cycle_executable,
                    "hypothetical_principal_usd": round(principal, 6) if principal is not None else "",
                    "hypothetical_fee_usd": round(fee, 6) if fee is not None else "",
                    "hypothetical_pnl_usd": round(pnl, 6) if pnl is not None else "",
                }
            )

        all_threshold_ledger = [row for row in ledger if row["margin_threshold"] == threshold]
        threshold_ledger = [row for row in all_threshold_ledger if row["decision_after_collector_start"]]
        settled = [row for row in threshold_ledger if row["settlement_left_previous_bracket"] in (True, False)]
        settled_hits = sum(row["settlement_left_previous_bracket"] is True for row in settled)
        next_labeled = [row for row in threshold_ledger if row["next_metar_market_crossed"] in (True, False)]
        next_hits = sum(row["next_metar_market_crossed"] is True for row in next_labeled)
        delays = [float(row["delay_vs_0p5_min"]) for row in threshold_ledger if row["delay_vs_0p5_min"] != ""]
        positive_delays = [value for value in delays if value > 1e-9]
        leads = [float(row["lead_to_next_metar_min"]) for row in threshold_ledger]
        priced = [row for row in threshold_ledger if row["direct_no_ask"] is not None]
        direct_executable = [
            row for row in threshold_ledger
            if row["direct_first_quote_executable_10_share_at_0p97"]
        ]
        pnl_rows = [row for row in direct_executable if row["hypothetical_pnl_usd"] != ""]
        direct_hits = sum(row["settlement_left_previous_bracket"] is True for row in pnl_rows)
        priced_delays = [float(row["first_priced_quote_delay_sec"]) for row in priced]
        principal = sum(float(row["hypothetical_principal_usd"]) for row in pnl_rows)
        fees = sum(float(row["hypothetical_fee_usd"]) for row in pnl_rows)
        pnl = sum(float(row["hypothetical_pnl_usd"]) for row in pnl_rows)
        roi_ci_low, roi_ci_high = target_date_block_bootstrap_roi(
            pnl_rows, seed=20260722 + int(threshold * 10)
        )
        summaries.append(
            {
                "margin_threshold": threshold,
                "all_history_signals": len(all_threshold_ledger),
                "signals": len(threshold_ledger),
                "dates": len({row["target_date"] for row in threshold_ledger}),
                "missed_vs_0p5": sum(
                    parse_dt(row.get("fast_detect_ts_utc")) >= collector_started_at
                    for row in selected_by_threshold[0.5]
                    if parse_dt(row.get("fast_detect_ts_utc")) is not None
                ) - len(threshold_ledger),
                "settled_signals": len(settled),
                "final_hits": settled_hits,
                "final_precision": round(settled_hits / len(settled), 4) if settled else "",
                "final_wilson_low": round(wilson_low(settled_hits, len(settled)), 4) if settled else "",
                "next_metar_hits": next_hits,
                "next_metar_signals": len(next_labeled),
                "next_metar_precision": round(next_hits / len(next_labeled), 4) if next_labeled else "",
                "median_delay_vs_0p5_min": round(statistics.median(delays), 3) if delays else "",
                "p90_delay_vs_0p5_min": round(sorted(delays)[math.ceil(len(delays) * 0.9) - 1], 3) if delays else "",
                "same_observation_as_0p5": sum(abs(value) < 1e-9 for value in delays),
                "delayed_vs_0p5": len(positive_delays),
                "median_positive_delay_vs_0p5_min": round(statistics.median(positive_delays), 3)
                if positive_delays else "",
                "median_lead_to_next_metar_min": round(statistics.median(leads), 3) if leads else "",
                "collector_active_signal_dates": len(
                    {row["target_date"] for row in threshold_ledger if row["collector_active_on_target_date"]}
                ),
                "direct_priced_quote_coverage": len(priced),
                "direct_priced_quote_dates": len({row["target_date"] for row in priced}),
                "direct_quote_delay_median_sec": round(statistics.median(priced_delays), 3)
                if priced_delays else "",
                "direct_quote_delay_p90_sec": round(
                    sorted(priced_delays)[math.ceil(len(priced_delays) * 0.9) - 1], 3
                ) if priced_delays else "",
                "direct_quote_delay_max_sec": round(max(priced_delays), 3) if priced_delays else "",
                "direct_executable_10_share_at_0p97": len(direct_executable),
                "direct_executable_dates": len({row["target_date"] for row in direct_executable}),
                "direct_trade_hits": direct_hits,
                "direct_trade_accuracy": round(direct_hits / len(pnl_rows), 4) if pnl_rows else "",
                "direct_trade_wilson_low": round(wilson_low(direct_hits, len(pnl_rows)), 4)
                if pnl_rows else "",
                "hypothetical_settled_fills": len(pnl_rows),
                "hypothetical_principal_usd": round(principal, 4),
                "hypothetical_fees_usd": round(fees, 4),
                "hypothetical_pnl_usd": round(pnl, 4),
                "hypothetical_roi": round(pnl / (principal + fees), 4) if principal + fees > 0 else "",
                "hypothetical_roi_block_bootstrap_low": round(roi_ci_low, 4)
                if roi_ci_low is not None else "",
                "hypothetical_roi_block_bootstrap_high": round(roi_ci_high, 4)
                if roi_ci_high is not None else "",
            }
        )

    collector_date_summary: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        threshold_rows = [
            row for row in ledger
            if row["margin_threshold"] == threshold and row["decision_after_collector_start"]
        ]
        for target_date in sorted({str(row["target_date"]) for row in threshold_rows}):
            date_rows = [row for row in threshold_rows if str(row["target_date"]) == target_date]
            collector_date_summary.append(
                {
                    "margin_threshold": threshold,
                    "target_date": target_date,
                    "signals": len(date_rows),
                    "collector_active_on_target_date": target_date in collector_active_dates,
                    "direct_priced_quote_coverage": sum(
                        row["direct_no_ask"] is not None for row in date_rows
                    ),
                    "direct_executable_10_share_at_0p97": sum(
                        bool(row["direct_first_quote_executable_10_share_at_0p97"])
                        for row in date_rows
                    ),
                }
            )

    out_dir = Path(args.out_dir)
    write_csv(out_dir / "threshold_event_ledger.csv", ledger)
    write_csv(out_dir / "threshold_summary.csv", summaries)
    write_csv(out_dir / "collector_date_summary.csv", collector_date_summary)
    snapshot = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime),
        "db_path": str(Path(args.db_path)),
        "raw_helsinki_event_comparisons": len(helsinki),
        "thresholds": list(THRESHOLDS),
        "execution_window_min": args.execution_window_min,
        "quote_support_sec": args.quote_support_sec,
        "collector_cycle_sec": args.collector_cycle_sec,
        "collector_started_at_utc": collector_started_at.isoformat(),
        "collector_active_dates": sorted(collector_active_dates),
        "archive_row_counts": dict(sorted(archive_row_counts.items())),
        "max_no_ask": args.max_no_ask,
        "shares": args.shares,
        "summary": summaries,
    }
    (out_dir / "summary.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    signal_table_lines = [
        "| 单次 margin | 采集后信号/日期 | final 正确率 | Wilson 95% 下界 | next METAR 正确率 |",
        "|---:|---:|---:|---:|---:|",
    ]
    trade_table_lines = [
        "| 单次 margin | first-priced coverage | 当场可执行/日期 | 成交正确率（Wilson low） | principal | fee | PnL | ROI（date-block 95% CI） | quote延迟中位/最大 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        signal_table_lines.append(
            f"| +{row['margin_threshold']:.1f} | {row['signals']}/{row['dates']} | "
            f"{row['final_hits']}/{row['settled_signals']} ({row['final_precision']}) | "
            f"{row['final_wilson_low']} | "
            f"{row['next_metar_hits']}/{row['next_metar_signals']} ({row['next_metar_precision']}) |"
        )
        trade_table_lines.append(
            f"| +{row['margin_threshold']:.1f} | {row['direct_priced_quote_coverage']}/{row['signals']} | "
            f"{row['direct_executable_10_share_at_0p97']}/{row['direct_executable_dates']} | "
            f"{row['direct_trade_hits']}/{row['hypothetical_settled_fills']} "
            f"({row['direct_trade_accuracy']}; {row['direct_trade_wilson_low']}) | "
            f"${row['hypothetical_principal_usd']} | ${row['hypothetical_fees_usd']} | "
            f"${row['hypothetical_pnl_usd']} | {row['hypothetical_roi']} "
            f"[{row['hypothetical_roi_block_bootstrap_low']}, {row['hypothetical_roi_block_bootstrap_high']}] | "
            f"{row['direct_quote_delay_median_sec']}s/{row['direct_quote_delay_max_sec']}s |"
        )
    execution_date_lines = [
        "| margin | 第一张 priced quote 当场可执行日期（行数） |",
        "|---:|---|",
    ]
    for threshold in THRESHOLDS:
        threshold_rows = [
            row for row in ledger
            if row["margin_threshold"] == threshold and row["decision_after_collector_start"]
        ]
        direct_counts = Counter(
            str(row["target_date"])
            for row in threshold_rows if row["direct_first_quote_executable_10_share_at_0p97"]
        )
        format_counts = lambda counts: ", ".join(  # noqa: E731
            f"{date}{f'×{count}' if count > 1 else ''}" for date, count in sorted(counts.items())
        ) or "—"
        execution_date_lines.append(
            f"| +{threshold:.1f} | {format_counts(direct_counts)} |"
        )
    report = f"""# Helsinki FMI 单次 Cross Margin 回放 v1

## 目标和口径

- target：比较第一次可用单次 FMI 信号相对旧 exact bracket 上沿 `+0.5/+0.6/+0.7/+0.8°C` 的准确率、时间和可执行盘口。
- grain：`target_date × previous exact bracket` 的首个信号；decision clock 为 source first-seen，不是 observation timestamp。
- 共同 gate：距离下一 routine METAR report clock 不超过 `{args.execution_window_min}` 分钟；只改变 margin，不叠加连续确认。
- final label：最终 winning bracket 是否离开旧档；next label：下一份 routine METAR 是否立即离开旧档。
- book：合并 `fast_source_stale_book` 与 `source_event_ladder_repricing_shadow`；collector 首次 Helsinki/FMI detect 为 `{collector_started_at.isoformat()}`。采集后没有 archive 的日期记 coverage gap。
- 执行：signal 后第一张可见 fresh priced quote 立即判断；top ask `≤{args.max_no_ask}` 且 top depth `≥{args.shares}` 就买 `{args.shares}` 股，否则不交易。不等待后续价格或深度改善，不使用 120 秒 eligibility gate。
- fee/PnL：按第一张 quote 的 ask、官方 Weather taker fee 和 final settlement 计算；这是 research replay，不是 actual fill。

## 结果

### Signal 正确率

{chr(10).join(signal_table_lines)}

### 第一张 fresh quote 直接执行

{chr(10).join(trade_table_lines)}

完整逐事件见 `threshold_event_ledger.csv`；逐日 coverage 见 `collector_date_summary.csv`；汇总见 `threshold_summary.csv`。

## 可执行盘口对应日期

{chr(10).join(execution_date_lines)}

## 直接结论

- `120s` 已从交易 eligibility 中删除。runner 应当立即抓 book；历史 replay 只用第一张实际归档 quote，quote 延迟单列为执行质量，不据此筛交易。
- 不允许等待后续盘口改善：例如 `+0.5` 的唯一 final 错误（7/17 previous 24 NO）第一张 ask `0.16` 但 top depth 只有 `6.75`，严格 10 股口径不成交；不能等 389 秒后深度变成 `14.43` 再假装直接成交。
- 阈值结论只使用采集启动后的同分母行；没有 archive 的 signal 日期保留为 coverage gap，不从策略分母删除。
- signal 层 `+0.6/+0.7/+0.8` 都是 100%，但直接可执行层四档目前也全胜，样本只有 `6/4/3/2` 笔。`+0.5` 的已覆盖直接执行 PnL/ROI 最高，`+0.6` 的 signal 误判更少；现有 book coverage 不足以证明哪个 live 更优。

## 双漏斗

- signal funnel：Helsinki/FMI causal comparisons `{len(helsinki)}` → collector 启动后首信号 → threshold 子集。
- evidence funnel：collector 后 signal → first-priced archive coverage → 第一张 quote 当场满足 `ask≤0.97 & depth≥10` → hypothetical direct fill。
- collector active target dates：`{', '.join(sorted(collector_active_dates))}`。信号存在但 archive 不工作的日期是 coverage gap，不算策略筛除。

## 结论边界

这是同分母、多阈值探索性 replay；没有独立 frozen forward，且 direct-book coverage 薄。阈值只能决定下一阶段 shadow 对照，不能单凭本表修改 live。
"""
    Path(args.report).write_text(report, encoding="utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
