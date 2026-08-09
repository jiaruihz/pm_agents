#!/usr/bin/env python3
"""Busan CrossNO exit-overlay replay on live-captured PIT checkpoints."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import glob
import gzip
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRADES = ROOT / (
    "docs/analysis/2026-08/generated/busan_cross_event_remaining_heat_v7/"
    "busan_expression_trades.csv"
)
DEFAULT_GLOB = (
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "korea_first_seen_state_v1/checkpoints/*.jsonl"
)
DEFAULT_SNAPSHOT_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/targeted_output/"
    "orderbook_snapshots"
)
DEFAULT_OUT = ROOT / (
    "docs/analysis/2026-08/generated/busan_crossno_exit_overlay_v10"
)
DEFAULT_REPORT = DEFAULT_OUT / "report.md"
START_DATE = "2026-07-22"
END_DATE = "2026-08-03"
POLICIES = (
    "hold_to_settlement",
    "two_pullbacks",
    "first_routine_nonconfirmation",
    "physical_nonconfirmation",
)


def fee_per_share(price: float) -> float:
    return round(0.05 * float(price) * (1.0 - float(price)), 5)


def report_ts(raw_metar: str | None, target_date: str) -> pd.Timestamp | None:
    match = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", str(raw_metar or ""))
    if not match:
        return None
    day, hour, minute = map(int, match.groups())
    base = pd.Timestamp(target_date, tz="UTC")
    candidates = []
    # METAR day-of-month can belong to the prior UTC month at a local-date edge.
    for month_shift in (-1, 0, 1):
        shifted = base + pd.DateOffset(months=month_shift)
        try:
            candidates.append(
                pd.Timestamp(
                    year=shifted.year,
                    month=shifted.month,
                    day=day,
                    hour=hour,
                    minute=minute,
                    tz="UTC",
                )
            )
        except ValueError:
            pass
    return min(candidates, key=lambda value: abs(value - base))


def exact_no_book(row: dict[str, Any], rung: int) -> dict[str, Any] | None:
    for book in (row.get("market_capture") or {}).get("books", []):
        if str(book.get("outcome") or "").lower() != "no":
            continue
        question = str(book.get("question") or "").lower()
        if "or below" in question or "or higher" in question:
            continue
        match = re.search(r"-?\d+", str(book.get("bracket") or ""))
        if match and int(match.group()) == int(rung):
            return book
    return None


def sell_vwap(book: dict[str, Any] | None, shares: float = 5.0) -> dict[str, float] | None:
    bids = ((book or {}).get("summary") or {}).get("bids") or []
    remaining = shares
    gross = 0.0
    fees = 0.0
    for level in sorted(bids, key=lambda item: float(item["price"]), reverse=True):
        quantity = min(remaining, float(level["size"]))
        price = float(level["price"])
        gross += quantity * price
        fees += quantity * fee_per_share(price)
        remaining -= quantity
        if remaining <= 1e-9:
            return {
                "sell_vwap": gross / shares,
                "sell_gross_usd": gross,
                "sell_fees_usd": fees,
                "sell_net_usd": gross - fees,
            }
    return None


def load_states(checkpoint_glob: str) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for path in sorted(glob.glob(checkpoint_glob)):
        date = Path(path).stem
        if not START_DATE <= date <= END_DATE:
            continue
        with Path(path).open() as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("city") != "Busan":
                    continue
                decision_ts = row.get("source_available_at_utc") or row.get(
                    "source_first_seen_ts_utc"
                )
                if not decision_ts:
                    continue
                local = pd.Timestamp(decision_ts).tz_convert("Asia/Seoul")
                peak_hour = (row.get("forecast_context") or {}).get(
                    "forecast_peak_hour_local"
                )
                path15 = (row.get("path_windows") or {}).get("15m") or {}
                rung = row.get("routine_running_max_market_value")
                output.append(
                    {
                        "target_date": str(row.get("target_date")),
                        "decision_ts_utc": pd.Timestamp(decision_ts),
                        "source_observation_ts_utc": row.get("source_observation_ts_utc"),
                        "source_temp_c": row.get("source_temp_c"),
                        "source_running_max_c": row.get("source_running_max_c"),
                        "routine_rung": rung,
                        "routine_report_ts_utc": report_ts(
                            row.get("raw_metar"), str(row.get("target_date"))
                        ),
                        "forecast_peak_hour_local": peak_hour,
                        "forecast_peak_passed": bool(
                            peak_hour is not None
                            and (local.hour + local.minute / 60.0) >= float(peak_hour)
                        ),
                        "path_15m_slope_c_per_hour": path15.get(
                            "temp_slope_c_per_hour"
                        ),
                        "distance_below_source_running_max_c": row.get(
                            "distance_below_source_running_max_c"
                        ),
                        "books_json": json.dumps(
                            (row.get("market_capture") or {}).get("books", [])
                        ),
                    }
                )
    return pd.DataFrame(output).sort_values(
        ["target_date", "decision_ts_utc"]
    ).reset_index(drop=True)


def load_entries(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame[
        frame.probability_source.eq("p_cross_rule")
        & frame.target_date.between(START_DATE, END_DATE)
    ].copy()
    frame["entry_ts_utc"] = pd.to_datetime(frame.trigger_detect_ts_utc, utc=True)
    frame["rung"] = frame.prior_max_c.astype(int)
    return frame.sort_values(["target_date", "entry_ts_utc"]).reset_index(drop=True)


def load_external_books(root: Path) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir() if root.exists() else []):
        if not directory.is_dir() or not START_DATE <= directory.name <= END_DATE:
            continue
        for path in directory.glob("*.jsonl.gz"):
            with gzip.open(path, "rt") as handle:
                for line in handle:
                    row = json.loads(line)
                    if row.get("city") != "Busan" or str(row.get("outcome")).lower() != "no":
                        continue
                    target_date = str(row.get("event_date") or row.get("market_local_date") or "")
                    if target_date != directory.name:
                        continue
                    match = re.search(r"-?\d+", str(row.get("bracket") or ""))
                    if not match:
                        continue
                    output.append(
                        {
                            "target_date": target_date,
                            "rung": int(match.group()),
                            "snapshot_ts_utc": pd.Timestamp(
                                row.get("fetched_at_utc") or row.get("snapshot_ts_utc")
                            ),
                            "book_json": json.dumps(row),
                            "source_path": str(path),
                        }
                    )
    if not output:
        return pd.DataFrame(
            columns=["target_date", "rung", "snapshot_ts_utc", "book_json", "source_path"]
        )
    return pd.DataFrame(output).sort_values(
        ["target_date", "rung", "snapshot_ts_utc"]
    ).reset_index(drop=True)


def book_from_state(state: pd.Series, rung: int) -> dict[str, Any] | None:
    return exact_no_book({"market_capture": {"books": json.loads(state.books_json)}}, rung)


def first_executable_after(
    states: pd.DataFrame,
    external_books: pd.DataFrame,
    target_date: str,
    trigger_ts: pd.Timestamp,
    rung: int,
) -> tuple[pd.Timestamp, dict[str, float], str] | None:
    candidates: list[tuple[pd.Timestamp, dict[str, float], str]] = []
    for _, state in states[states.decision_ts_utc.ge(trigger_ts)].iterrows():
        execution = sell_vwap(book_from_state(state, rung), 5.0)
        if execution is not None:
            candidates.append((state.decision_ts_utc, execution, "wcir_checkpoint_book"))
            break
    books = external_books[
        external_books.target_date.eq(target_date)
        & external_books.rung.eq(rung)
        & external_books.snapshot_ts_utc.ge(trigger_ts)
    ]
    for _, book_row in books.iterrows():
        execution = sell_vwap(json.loads(book_row.book_json), 5.0)
        if execution is not None:
            candidates.append(
                (book_row.snapshot_ts_utc, execution, "targeted_orderbook_snapshot")
            )
            break
    return min(candidates, key=lambda item: item[0]) if candidates else None


def replay_entry(
    entry: pd.Series, states: pd.DataFrame, external_books: pd.DataFrame
) -> list[dict[str, Any]]:
    relevant = states[
        states.target_date.eq(entry.target_date)
        & states.decision_ts_utc.ge(entry.entry_ts_utc)
    ].copy()
    before = states[
        states.target_date.eq(entry.target_date)
        & states.decision_ts_utc.le(entry.entry_ts_utc)
    ]
    entry_routine_ts = (
        before.routine_report_ts_utc.dropna().max()
        if not before.empty
        else pd.NaT
    )
    threshold = float(entry.rung) + 0.5
    pullbacks = 0
    routine_nonconfirmation_ts: pd.Timestamp | None = None
    confirmed = False
    triggers: dict[str, pd.Timestamp] = {}
    for _, state in relevant.iterrows():
        temp = state.source_temp_c
        pullbacks = pullbacks + 1 if pd.notna(temp) and float(temp) < threshold else 0
        if pullbacks >= 2 and "two_pullbacks" not in triggers:
            triggers["two_pullbacks"] = state.decision_ts_utc
        routine_ts = state.routine_report_ts_utc
        if (
            pd.notna(routine_ts)
            and (pd.isna(entry_routine_ts) or routine_ts > entry_routine_ts)
        ):
            if pd.notna(state.routine_rung) and int(state.routine_rung) > int(entry.rung):
                confirmed = True
                break
            if routine_nonconfirmation_ts is None:
                routine_nonconfirmation_ts = state.decision_ts_utc
                triggers["first_routine_nonconfirmation"] = state.decision_ts_utc
        if routine_nonconfirmation_ts is not None and pullbacks >= 2:
            slope = state.path_15m_slope_c_per_hour
            drawdown = state.distance_below_source_running_max_c
            physical_failure = bool(
                state.forecast_peak_passed
                or (
                    pd.notna(slope)
                    and float(slope) <= 0
                    and pd.notna(drawdown)
                    and float(drawdown) >= 1.0
                )
            )
            if physical_failure and "physical_nonconfirmation" not in triggers:
                triggers["physical_nonconfirmation"] = state.decision_ts_utc

    hold_pnl = 5.0 * float(entry.final_no_win) - float(entry.cost_usd)
    output: list[dict[str, Any]] = []
    for policy in POLICIES:
        result: dict[str, Any] = {
            "target_date": entry.target_date,
            "rung": int(entry.rung),
            "entry_ts_utc": entry.entry_ts_utc.isoformat(),
            "entry_ask": float(entry.ask),
            "entry_cost_usd": float(entry.cost_usd),
            "final_no_win": int(entry.final_no_win),
            "policy": policy,
            "routine_confirmed_before_stop": confirmed,
            "action": "hold",
            "exit_trigger_ts_utc": None,
            "exit_execution_ts_utc": None,
            "exit_vwap": None,
            "pnl_usd": hold_pnl,
            "pnl_delta_vs_hold": 0.0,
            "coverage_status": "not_needed_hold",
        }
        if policy != "hold_to_settlement" and policy in triggers:
            execution = first_executable_after(
                relevant,
                external_books,
                entry.target_date,
                triggers[policy],
                int(entry.rung),
            )
            if execution is None:
                result["coverage_status"] = "coverage_gap_no_5share_exit_bid"
            else:
                execution_ts, values, quote_source = execution
                exit_pnl = float(values["sell_net_usd"]) - float(entry.cost_usd)
                result.update(
                    {
                        "action": "exit",
                        "exit_trigger_ts_utc": triggers[policy].isoformat(),
                        "exit_execution_ts_utc": execution_ts.isoformat(),
                        "exit_quote_source": quote_source,
                        "exit_vwap": values["sell_vwap"],
                        "exit_net_usd": values["sell_net_usd"],
                        "pnl_usd": exit_pnl,
                        "pnl_delta_vs_hold": exit_pnl - hold_pnl,
                        "coverage_status": "executable_5share_bid",
                    }
                )
        output.append(result)
    return output


def summarize(rows: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for policy, group in rows.groupby("policy", sort=False):
        cost = float(group.entry_cost_usd.sum())
        daily_delta = (
            group.groupby("target_date").pnl_delta_vs_hold.sum().to_numpy(float)
        )
        rng = np.random.default_rng(10501 + len(output))
        boot_delta = rng.choice(
            daily_delta, size=(10000, len(daily_delta)), replace=True
        ).mean(axis=1) * len(daily_delta)
        output.append(
            {
                "policy": policy,
                "entries": int(len(group)),
                "dates": int(group.target_date.nunique()),
                "exits": int(group.action.eq("exit").sum()),
                "exit_coverage_gaps": int(
                    group.coverage_status.eq("coverage_gap_no_5share_exit_bid").sum()
                ),
                "pnl_usd": float(group.pnl_usd.sum()),
                "roi": float(group.pnl_usd.sum() / cost),
                "pnl_delta_vs_hold": float(group.pnl_delta_vs_hold.sum()),
                "pnl_delta_vs_hold_ci95": [
                    float(x) for x in np.quantile(boot_delta, [0.025, 0.975])
                ],
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--checkpoint-glob", default=DEFAULT_GLOB)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    entries = load_entries(args.trades)
    states = load_states(args.checkpoint_glob)
    external_books = load_external_books(args.snapshot_root)
    replay = pd.DataFrame(
        [
            row
            for _, entry in entries.iterrows()
            for row in replay_entry(entry, states, external_books)
        ]
    )
    summary_rows = summarize(replay)
    summary = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": [START_DATE, END_DATE],
        "entry_universe": "Busan p_cross_rule executable first date-rung",
        "entries": int(len(entries)),
        "dates": int(entries.target_date.nunique()),
        "state_rows": int(len(states)),
        "external_book_rows": int(len(external_books)),
        "policies": summary_rows,
        "coverage_note": "Earlier CrossNO dates lack WCIR full-book checkpoints and are evidence gaps, not exclusions.",
        "status": "inconclusive_research_only",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    replay.to_csv(args.output_dir / "busan_crossno_exit_replay.csv", index=False)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    table = "\n".join(
        f"| {x['policy']} | {x['entries']} | {x['dates']} | {x['exits']} | "
        f"${x['pnl_usd']:+.4f} | {100*x['roi']:+.2f}% | ${x['pnl_delta_vs_hold']:+.4f} |"
        for x in summary_rows
    )
    report = f"""# Busan CrossNO exit overlay v10

## 结论

在有完整WCIR分钟盘口的同分母 `{START_DATE}..{END_DATE}` 上回放 `{len(entries)}` 个CrossNO entry / `{entries.target_date.nunique()}` dates。止损只是execution overlay，不改变原始CrossNO candidate分母。

| policy | entries | dates | exits | fee-adjusted PnL | ROI | delta vs hold |
|---|---:|---:|---:|---:|---:|---:|
{table}

Paired target-date bootstrap delta CI见 `summary.json`；仅5个日期，区间用于显示日期集中度，不代表跨季稳定性。

## 规则

- `two_pullbacks`：cross后连续两个source observations低于 `rung+0.5°C`。
- `first_routine_nonconfirmation`：cross后的第一份新routine报告仍未把running max抬过旧rung。
- `physical_nonconfirmation`：routine未确认 + 两次回落 +（forecast peak已过，或15m slope≤0且距source峰值≥1°C）。
- 已被routine确认跨档后不再触发止损。
- exit使用首次可见的真实NO bids吃满5 shares并扣Weather fee；无5-share深度保留coverage gap。

## 分母边界

CrossNO完整参考为38 entries / 13 dates。7/22以后有WCIR source checkpoints，并接入独立PIT targeted orderbook snapshots；7/20–21缺同级source sequence，保留coverage gap。本报告分母不是价格/结果筛选。

结论保持 `inconclusive_research_only`；规则结果用于选择后续frozen exit shadow，不直接修改live。
"""
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
