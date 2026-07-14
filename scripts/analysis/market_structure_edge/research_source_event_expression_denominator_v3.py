#!/usr/bin/env python3
"""Expression-specific denominator for generic running-high bracket advances.

Unlike the full-ladder v3 state builder, this replay does not require current
+ d1 + d2 to exist before evaluating current YES/NO.  Each expression requires
only its own token, executable quote/depth, a PIT running high, and a canonical
settlement winner.  Research-only; never places orders.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import audit_source_event_denominator_v2 as audit_v2  # noqa: E402
from scripts.analysis.reheat_risk import research_tmax_single_snapshot_lineage_replay_v2 as replay  # noqa: E402
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402


RAW_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2"
DEFAULT_GROUPS = RAW_DIR / "raw_decision_groups.jsonl"
DEFAULT_EVENTS = RAW_DIR / "embedded_observation_events.csv"
DEFAULT_SOURCE_TIMING = ROOT / "runtime/weather_edge_v1/source_orderbook_timing/sources.jsonl"
DEFAULT_CANDIDATES = RAW_DIR / "snapshot_candidates.csv"
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT_DIR = ROOT / "docs/analysis/2026-07/generated/source_event_expression_denominator_v3"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-14-source-event-expression-denominator-v3.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-source-event-expression-denominator-v3.json"


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def load_winners(db_path: Path) -> tuple[dict[tuple[str, str], str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, bracket
            FROM settlement_outcomes
            WHERE settlement_status='settled' AND final_price > 0.5
            """
        ).fetchall()
        snapshot = conn.execute(
            """
            SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date),
                   COUNT(DISTINCT city)
            FROM settlement_outcomes WHERE settlement_status='settled'
            """
        ).fetchone()
    finally:
        conn.close()
    winners = {(str(city), str(date)): replay.bracket_key({"bracket": bracket}) for city, date, bracket in rows}
    return winners, {
        "min_date": snapshot[0],
        "max_date": snapshot[1],
        "dates": int(snapshot[2] or 0),
        "cities": int(snapshot[3] or 0),
    }


def load_history(
    path: Path, source_timing_path: Path
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, int]]:
    events = pd.read_csv(path, parse_dates=["obs_ts_utc", "first_seen_snapshot_ts_utc"])
    rows = events.to_dict("records")
    timing_ok = 0
    if source_timing_path.exists():
        with source_timing_path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(item.get("status") or "") != "ok":
                    continue
                city = str(item.get("city") or "")
                target_date = str(item.get("target_date") or "")[:10]
                obs_ts = pd.to_datetime(item.get("source_report_ts_utc"), errors="coerce", utc=True)
                first_seen = pd.to_datetime(
                    item.get("local_detect_ts_utc") or item.get("ts_utc"), errors="coerce", utc=True
                )
                temp_f = finite(item.get("temp_f"))
                temp_c = finite(item.get("temp_c"))
                if temp_f is None and temp_c is not None:
                    temp_f = temp_c * 9.0 / 5.0 + 32.0
                if not city or not target_date or pd.isna(obs_ts) or pd.isna(first_seen) or temp_f is None:
                    continue
                rows.append(
                    {
                        "city": city,
                        "target_date": target_date,
                        "obs_ts_utc": obs_ts,
                        "temp_f": temp_f,
                        "first_seen_snapshot_ts_utc": first_seen,
                    }
                )
                timing_ok += 1
    combined = pd.DataFrame(rows)
    combined.sort_values("first_seen_snapshot_ts_utc", inplace=True)
    combined.drop_duplicates(["city", "target_date", "obs_ts_utc"], keep="first", inplace=True)
    history = {key: group.to_dict("records") for key, group in combined.groupby(["city", "target_date"])}
    return history, {
        "embedded_events": len(events),
        "source_timing_ok_repolls": timing_ok,
        "combined_unique_pit_events": len(combined),
    }


def quote_fields(record: dict[str, Any] | None, prefix: str) -> dict[str, float]:
    bid, ask, _ = replay.yes_quote(record or {})
    return {
        f"{prefix}_yes_ask": math.nan if ask is None else float(ask),
        f"{prefix}_yes_bid": math.nan if bid is None else float(bid),
        f"{prefix}_yes_ask_size": finite((record or {}).get("yes_ask_size")) or math.nan,
        f"{prefix}_no_ask": finite((record or {}).get("no_best_ask")) or math.nan,
        f"{prefix}_no_bid": finite((record or {}).get("no_best_bid")) or math.nan,
        f"{prefix}_no_ask_size": finite((record or {}).get("no_ask_size")) or math.nan,
    }


def saved_running_native(records: list[dict[str, Any]], unit: str) -> float | None:
    if not records:
        return None
    running_f = finite(records[0].get("metar_current_max_f"))
    if running_f is None:
        return None
    return running_f if unit == "F" else (running_f - 32.0) * 5.0 / 9.0


def materialize_expression_states(
    groups_path: Path,
    history: dict[tuple[str, str], list[dict[str, Any]]],
    winners: dict[tuple[str, str], str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    with groups_path.open(encoding="utf-8") as handle:
        for line in handle:
            counts["raw_decision_groups"] += 1
            group = json.loads(line)
            city = str(group.get("city") or "")
            target_date = str(group.get("target_date") or "")
            decision = replay.parse_utc(group.get("snapshot_ts_utc"))
            records = group.get("records") or []
            winner = winners.get((city, target_date))
            if winner is None:
                counts["missing_canonical_settlement"] += 1
                continue
            counts["canonical_settlement_available"] += 1
            if decision is None or not records:
                counts["invalid_snapshot"] += 1
                continue
            unit = str(records[0].get("unit") or "").upper()
            if unit not in {"C", "F"}:
                counts["invalid_unit"] += 1
                continue

            path_state = replay._path_asof(history.get((city, target_date), []), decision, unit)
            if path_state is not None:
                running = float(path_state["running_native"])
                running_source = "embedded_first_seen_observation"
                counts["running_from_embedded_history"] += 1
            else:
                running = saved_running_native(records, unit)
                if running is None:
                    counts["missing_pit_running_max"] += 1
                    continue
                running_source = "saved_snapshot_running_max"
                counts["running_from_saved_snapshot"] += 1

            ladder = sorted(
                {replay.bracket_key(item): item for item in records if replay.bracket_key(item) is not None}.values(),
                key=replay.bracket_sort_key,
            )
            anchor = next(
                (
                    index
                    for index, item in enumerate(ladder)
                    if (
                        parsed := parse_market_bracket(
                            str(item.get("bracket") or ""), str(item.get("question") or "")
                        )
                    )
                    is not None
                    and replay.interval_contains(parsed, running)
                ),
                None,
            )
            if anchor is None:
                counts["running_outside_saved_ladder"] += 1
                continue
            counts["current_anchor_mapped"] += 1

            current = ladder[anchor]
            d1 = ladder[anchor + 1] if anchor + 1 < len(ladder) else None
            previous = ladder[anchor - 1] if anchor > 0 else None
            current_key = replay.bracket_key(current)
            d1_key = replay.bracket_key(d1) if d1 is not None else ""
            previous_key = replay.bracket_key(previous) if previous is not None else ""
            current_quote = quote_fields(current, "current")
            d1_quote = quote_fields(d1, "d1")
            previous_quote = quote_fields(previous, "prev")
            if d1 is None:
                counts["d1_rung_absent_but_current_retained"] += 1
            rows.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "decision_snapshot_ts_utc": decision.isoformat(),
                    "decision_ts": decision,
                    "snapshot_root_priority": int(group.get("root_priority") or 0),
                    "running_source": running_source,
                    "running_native": running,
                    "current_key": current_key,
                    "d1_key": d1_key,
                    "prev_key": previous_key,
                    "y_current_yes": int(winner == current_key),
                    "y_d1_yes": int(bool(d1_key) and winner == d1_key),
                    "y_prev_yes": int(bool(previous_key) and winner == previous_key),
                    **current_quote,
                    **d1_quote,
                    **previous_quote,
                }
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no expression-specific PIT states materialized")
    frame.sort_values(
        ["city", "target_date", "decision_ts", "snapshot_root_priority"], inplace=True
    )
    frame.drop_duplicates(
        ["city", "target_date", "decision_snapshot_ts_utc"], keep="last", inplace=True
    )
    groups = frame.groupby(["city", "target_date"], sort=False)
    frame["previous_current_key"] = groups["current_key"].shift()
    frame["cross_event"] = frame["previous_current_key"].notna() & frame["current_key"].ne(
        frame["previous_current_key"]
    )
    frame["same_anchor_sequence"] = groups["current_key"].transform(lambda values: values.ne(values.shift()).cumsum())
    frame["anchor_episode"] = (
        frame["city"].astype(str)
        + "|"
        + frame["target_date"].astype(str)
        + "|"
        + frame["current_key"].astype(str)
        + "|"
        + frame["same_anchor_sequence"].astype(str)
    )
    frame["current_yes_ask"] = frame["current_yes_ask"]
    frame["current_yes_ask_size"] = frame["current_yes_ask_size"]
    frame["current_no_ask_book"] = frame["current_no_ask"]
    frame["current_no_ask_size_book"] = frame["current_no_ask_size"]
    frame["d1_yes_ask_book"] = frame["d1_yes_ask"]
    frame["d1_yes_ask_size_book"] = frame["d1_yes_ask_size"]
    frame["d1_no_ask_book"] = frame["d1_no_ask"]
    frame["d1_no_ask_size_book"] = frame["d1_no_ask_size"]
    frame["prev_no_ask"] = frame["prev_no_ask"]
    frame["prev_no_ask_size"] = frame["prev_no_ask_size"]
    frame["prev_yes_ask"] = frame["prev_yes_ask"]
    frame["prev_yes_ask_size"] = frame["prev_yes_ask_size"]
    frame["y_prev_no"] = 1 - frame["y_prev_yes"]
    counts["deduplicated_expression_states"] = len(frame)
    counts["generic_cross_events"] = int(frame["cross_event"].sum())
    return frame.reset_index(drop=True), dict(counts)


def settlement_coverage(candidates_path: Path, winners: dict[tuple[str, str], str]) -> dict[str, Any]:
    candidates = pd.read_csv(candidates_path, usecols=["city", "target_date"]).drop_duplicates()
    has = candidates.apply(lambda row: (str(row["city"]), str(row["target_date"])) in winners, axis=1)
    missing = candidates[~has].copy()
    return {
        "candidate_city_days": len(candidates),
        "settled_city_days": int(has.sum()),
        "missing_city_days": int((~has).sum()),
        "missing_by_date": missing.groupby("target_date").size().astype(int).to_dict(),
    }


def markdown_table(frame: pd.DataFrame) -> str:
    return audit_v2.markdown_table(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--source-timing", type=Path, default=DEFAULT_SOURCE_TIMING)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    winners, settlement = load_winners(args.db)
    coverage = settlement_coverage(args.candidates, winners)
    history, history_coverage = load_history(args.events, args.source_timing)
    states, funnel = materialize_expression_states(args.groups, history, winners)
    all_trades = audit_v2.trade_rows(states, cross_only=False)
    cross_trades = audit_v2.trade_rows(states, cross_only=True)
    summary, daily = audit_v2.summarize_cross(cross_trades, all_trades)
    summary = summary[summary["expression"].isin(["current_yes", "current_no", "d1_yes", "d1_no"])].copy()

    current_no = summary[summary["expression"].eq("current_no")].iloc[0]
    payload = {
        "schema_version": "source_event_expression_denominator_v3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_zero_notional",
        "settlement_snapshot": settlement,
        "settlement_coverage": coverage,
        "running_history_coverage": history_coverage,
        "funnel": funnel,
        "summary": summary.where(pd.notna(summary), None).to_dict("records"),
        "verdict": {
            "significance": "PASS" if float(current_no["roi_ci_low"]) > 0 else "FAIL",
            "baseline": "PASS" if float(current_no["excess_ci_low"]) > 0 else "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
        },
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out_dir / "expression_summary.csv", index=False)
    daily.to_csv(args.out_dir / "expression_daily.csv", index=False)
    pd.DataFrame([{"stage": key, "rows": value} for key, value in funnel.items()]).to_csv(
        args.out_dir / "funnel.csv", index=False
    )
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    display = summary[
        ["expression", "rows", "dates", "cities", "avg_ask", "win_rate", "roi", "roi_ci_low", "roi_ci_high",
         "date_equal_excess_roi", "excess_ci_low", "excess_ci_high"]
    ]
    report = "\n".join(
        [
            "# Source-Event Expression-Specific Denominator v3",
            "",
            "> 2026-07-14; research-only; corrects the full-ladder d2 requirement inherited by v1/v2.",
            "",
            "## 数据快照",
            "",
            f"- canonical settlement: `{settlement['min_date']}..{settlement['max_date']}` / `{settlement['dates']}` dates / `{settlement['cities']}` cities.",
            f"- raw cache: `{args.groups}`; PIT observation events: `{args.events}`.",
            f"- source timing PIT log: `{args.source_timing}`; combined unique observations `{history_coverage['combined_unique_pit_events']}`.",
            "- strategy grain: every saved decision snapshot; quote must be from the same snapshot and observation must be first-seen by decision time.",
            "",
            "## 纠错结论",
            "",
            "`current 上方不足两档`不应排除 current YES/NO。那是 full-ladder current+d1+d2 概率模型的结构要求，不是 current token 的交易要求。",
            "本轮改成 expression-specific：current 只要求 current bracket；d1 只要求 current+d1；不存在 d1 时仍保留 current state。",
            "settlement winner 直接从 canonical `settlement_outcomes` 补，不要求 winner 必须出现在当时保存的 forward ladder；它只作为事后 label，不进入信号。",
            "running max 是决策时刻已经 first-seen 的当日最高观测。优先从 embedded observation history PIT 重建；没有历史时才使用快照当时已保存的 `metar_current_max_f`；两者都没有就不能无泄漏补。",
            "",
            "## Expression-specific funnel",
            "",
            markdown_table(pd.DataFrame([{"stage": key, "rows": value} for key, value in funnel.items()])),
            "",
            "## Settlement coverage",
            "",
            f"- raw candidate city-days `{coverage['candidate_city_days']}`; canonical settled `{coverage['settled_city_days']}`; missing `{coverage['missing_city_days']}`.",
            f"- missing by target_date: `{json.dumps(coverage['missing_by_date'], ensure_ascii=False, sort_keys=True)}`.",
            "",
            "## 全部 generic cross 结果",
            "",
            markdown_table(display),
            "",
            "## Three Gates",
            "",
            f"- significance={payload['verdict']['significance']}; baseline={payload['verdict']['baseline']}; forward=FAIL; conclusion=inconclusive.",
            "- 本轮只修正分母，不批准 live；真正 fast-source event 仍需独立 forward。",
            "",
        ]
    )
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps({"funnel": funnel, "settlement_coverage": coverage, "summary": payload["summary"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
