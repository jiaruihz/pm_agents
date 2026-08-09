#!/usr/bin/env python3
"""Explore AMOS previous-NO entry timing for Busan and Seoul.

This is a PIT research replay.  It separates source confirmation timing from
book coverage and from final WU settlement.  It never changes production or
submits an order.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_active_realtime_source_alignment_v1 as alignment  # noqa: E402


DEFAULT_START_DATE = "2026-07-08"
CITIES = {"Busan", "Seoul"}
RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
QUOTE_PATH = RUNTIME / "output/fast_source_stale_book/quote_snapshots.jsonl"
DAILY_PATH = ROOT / "docs/analysis/2026-07/generated/active_realtime_source_alignment_v1/daily_source_wu_settlement.csv"
DEFAULT_DB = ROOT / "runtime/weather.db"
OUT = ROOT / "docs/analysis/2026-07/generated/amos_entry_timing_tradeoff_v1"


def parse_dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


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


def median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def default_end_date() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


START_DATE = DEFAULT_START_DATE  # compatibility for dependent research modules
END_DATE = default_end_date()  # dynamic compatibility alias; no fixed calendar cutoff


def load_alignment(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    start_date = start_date or START_DATE
    end_date = end_date or END_DATE
    fast = alignment.load_fast(start_date, end_date)
    awc = alignment.load_awc(start_date, end_date)
    rows = alignment.next_metar_alignment(fast, awc)
    output: list[dict[str, Any]] = []
    for raw in rows:
        if raw["city"] not in CITIES:
            continue
        output.append({
            **raw,
            "detect_ts": parse_dt(raw["fast_detect_ts_utc"]),
            "obs_ts": parse_dt(raw["fast_obs_ts_utc"]),
            "temp_c": float(raw["fast_temp_c"]),
            "prior_max_c": int(raw["prior_metar_running_max_c"]),
            "report_ts": parse_dt(raw["next_metar_report_ts_utc"]),
            "report_detect_ts": parse_dt(raw["next_metar_detect_ts_utc"]),
            "next_crossed": int(raw["next_metar_crossed"]),
        })
    return output


def build_episodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts: dict[tuple[str, str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            row["city"], row["target_date"], row["prior_max_c"],
            row["next_metar_report_ts_utc"], row["next_metar_detect_ts_utc"],
        )
        contexts[key].append(row)

    output: list[dict[str, Any]] = []
    episode_id = 0
    for context_rows in contexts.values():
        context_rows.sort(key=lambda row: (row["detect_ts"], row["obs_ts"]))
        active = False
        current: dict[str, Any] | None = None
        for row in context_rows:
            crossed = row["temp_c"] >= row["prior_max_c"] + 0.5 - 1e-12
            if crossed and not active:
                episode_id += 1
                current = {
                    "episode_id": episode_id,
                    "city": row["city"], "target_date": row["target_date"],
                    "prior_max_c": row["prior_max_c"], "threshold_c": row["prior_max_c"] + 0.5,
                    "start_detect_ts": row["detect_ts"], "start_obs_ts": row["obs_ts"],
                    "report_ts": row["report_ts"], "report_detect_ts": row["report_detect_ts"],
                    "next_crossed": row["next_crossed"], "context_rows": context_rows,
                }
                output.append(current)
                active = True
            elif not crossed:
                active = False
                current = None
    for episode in output:
        episode_rows: list[dict[str, Any]] = []
        terminal_below_row: dict[str, Any] | None = None
        started = False
        for row in episode["context_rows"]:
            if row["detect_ts"] == episode["start_detect_ts"] and row["obs_ts"] == episode["start_obs_ts"]:
                started = True
            if not started:
                continue
            if row["temp_c"] < episode["threshold_c"] - 1e-12:
                terminal_below_row = row
                break
            episode_rows.append(row)
        episode["episode_rows"] = episode_rows
        # Retain the first below-threshold observation so a dead episode stays
        # dead.  A later re-cross is evaluated as its own independent episode.
        episode["path_rows"] = episode_rows + ([] if terminal_below_row is None else [terminal_below_row])
        qualifying = None
        for index, row in enumerate(episode_rows):
            seen = episode_rows[: index + 1]
            # Source-only component of persistent_candidate_margin_v5: two
            # distinct, continuous >= X+0.5 C observations, with the latest
            # (not merely an earlier peak) at least X+0.7 C.  The live v5
            # report-clock <=20m condition is applied separately below so the
            # value-versus-confirmation timing tradeoff remains measurable.
            distinct_obs = len({item["obs_ts"] for item in seen}) >= 2
            if distinct_obs and row["temp_c"] >= episode["prior_max_c"] + 0.7 - 1e-12:
                qualifying = row
                break
        episode["trigger_detect_ts"] = None if qualifying is None else qualifying["detect_ts"]
        episode["trigger_obs_ts"] = None if qualifying is None else qualifying["obs_ts"]
    return output


def load_quotes(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    start_date = start_date or START_DATE
    end_date = end_date or END_DATE
    dedup: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for raw in iter_jsonl(QUOTE_PATH):
        city = str(raw.get("city") or "")
        target_date = str(raw.get("target_date") or "")
        if city not in CITIES or not (start_date <= target_date <= end_date):
            continue
        quote = ((raw.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {}
        bracket = number(((raw.get("quotes") or {}).get("t_minus_1") or {}).get("bracket_c"))
        if bracket is None:
            bracket = number(quote.get("bracket"))
        ts_raw = raw.get("ts_utc")
        if bracket is None or not ts_raw:
            continue
        bid = number(quote.get("fresh_best_bid"))
        ask = number(quote.get("fresh_best_ask"))
        bid_size = number(quote.get("fresh_bid_size"))
        ask_size = number(quote.get("fresh_ask_size"))
        if bid is None and ask is None:
            continue
        ts = parse_dt(ts_raw)
        key = (city, target_date, ts.isoformat(), int(bracket))
        dedup[key] = {
            "city": city, "target_date": target_date, "ts": ts, "prior_max_c": int(bracket),
            "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size,
        }
    return sorted(dedup.values(), key=lambda row: (row["city"], row["target_date"], row["ts"], row["prior_max_c"]))


def labels_from_winners(
    winners: dict[tuple[str, str], str],
) -> dict[tuple[str, str, int], int]:
    output: dict[tuple[str, str, int], int] = {}
    for (city, target_date), winner in winners.items():
        for prior in range(15, 46):
            output[(city, target_date, prior)] = int(
                not alignment.eligibility.parse_bracket_contains(winner, prior)
            )
    return output


def load_final_labels(daily_path: Path = DAILY_PATH) -> dict[tuple[str, str, int], int]:
    winners: dict[tuple[str, str], str] = {}
    with daily_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            city = row["city"]
            if city not in CITIES:
                continue
            winners[(city, row["target_date"])] = row["winning_bracket"]
    return labels_from_winners(winners)


def load_canonical_final_labels(
    db_path: Path, start_date: str, end_date: str
) -> dict[tuple[str, str, int], int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    rows = conn.execute(
        """
        SELECT city, target_date, bracket
        FROM settlement_outcomes
        WHERE city IN ('Busan', 'Seoul')
          AND target_date BETWEEN ? AND ?
          AND final_price >= 0.999
        """,
        (start_date, end_date),
    ).fetchall()
    conn.close()
    winners: dict[tuple[str, str], str] = {}
    for city, target_date, bracket in rows:
        key = (str(city), str(target_date))
        candidate = str(bracket)
        existing = winners.get(key)
        if existing is not None and existing != candidate:
            raise RuntimeError(
                f"conflicting canonical winners for {key}: {existing!r} vs {candidate!r}"
            )
        winners[key] = candidate
    return labels_from_winners(winners)


def path_state(episode: dict[str, Any], decision_ts: datetime) -> dict[str, Any]:
    rows = [
        row for row in episode["path_rows"]
        if episode["start_detect_ts"] <= row["detect_ts"] <= decision_ts
    ]
    if not rows:
        return {"observations": 0, "latest_above": 0, "retained": 0}
    latest = rows[-1]
    peak = max(row["temp_c"] for row in rows)
    threshold = episode["threshold_c"]
    above = sum(row["temp_c"] >= threshold - 1e-12 for row in rows)
    return {
        "observations": len(rows), "latest_temp_c": latest["temp_c"],
        "above_fraction": above / len(rows), "peak_temp_c": peak,
        "drawdown_c": peak - latest["temp_c"],
        "latest_above": int(latest["temp_c"] >= threshold - 1e-12),
        "latest_strong": int(latest["temp_c"] >= episode["prior_max_c"] + 0.7 - 1e-12),
        "retained": int(
            len(rows) >= 3 and above / len(rows) >= 0.8
            and latest["temp_c"] >= threshold - 1e-12
            and peak - latest["temp_c"] <= 0.2 + 1e-12
        ),
    }


def first_quote(
    quotes: list[dict[str, Any]], episode: dict[str, Any], at_or_after: datetime,
    criterion: str, max_lag_sec: int = 90,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    candidates = [
        quote for quote in quotes
        if quote["city"] == episode["city"]
        and quote["target_date"] == episode["target_date"]
        and quote["prior_max_c"] == episode["prior_max_c"]
        and at_or_after <= quote["ts"] < episode["report_ts"]
        and (quote["ts"] - at_or_after).total_seconds() <= max_lag_sec
    ]
    for quote in candidates:
        state = path_state(episode, quote["ts"])
        if criterion == "none" or bool(state.get(criterion)):
            return quote, state
    return None


def add_economics(row: dict[str, Any], final_win: int | None) -> None:
    ask = row.get("ask")
    ask_size = row.get("ask_size")
    if ask is None:
        row.update({"executable10": 0, "entry_fee_per_share": None, "cost_with_fee": None, "upside_if_win": None})
        return
    fee = 0.05 * ask * (1.0 - ask)
    cost = ask + fee
    row.update({
        "executable10": int(ask <= 0.97 + 1e-12 and ask_size is not None and ask_size >= 10 - 1e-12),
        "entry_fee_per_share": round(fee, 6), "cost_with_fee": round(cost, 6),
        "upside_if_win": round(1.0 - cost, 6),
        "final_no_win": final_win,
        "final_pnl_per_share": None if final_win is None else round(float(final_win) - cost, 6),
    })


def delay_policy_rows(
    episodes: list[dict[str, Any]], quotes: list[dict[str, Any]], final_labels: dict[tuple[str, str, int], int],
) -> list[dict[str, Any]]:
    policies = [
        # clock_max_min=None means source state alone; 20 reproduces the live
        # v5 execution-clock boundary.
        ("source_immediate", 0, "none", None),
        ("current_v5_immediate_le20m", 0, "latest_strong", 20),
        ("current_v5_immediate_le10m", 0, "latest_strong", 10),
        ("wait2_latest_above", 2, "latest_above", None),
        ("wait5_latest_above", 5, "latest_above", None),
        ("wait5_retained", 5, "retained", None),
        ("wait10_retained", 10, "retained", None),
    ]
    output: list[dict[str, Any]] = []
    for episode in episodes:
        if episode.get("trigger_detect_ts") is None:
            continue
        for policy, delay_min, criterion, clock_max_min in policies:
            decision_target = episode["trigger_detect_ts"] + timedelta(minutes=delay_min)
            if clock_max_min is not None:
                decision_target = max(
                    decision_target,
                    episode["report_ts"] - timedelta(minutes=clock_max_min),
                )
            target_state = path_state(episode, decision_target)
            target_minutes_to_report = (episode["report_ts"] - decision_target).total_seconds() / 60
            clock_passed = (
                clock_max_min is None
                or 0 <= target_minutes_to_report <= clock_max_min + 1e-12
            )
            passed = clock_passed and (criterion == "none" or bool(target_state.get(criterion)))
            quote_result = first_quote(quotes, episode, decision_target, criterion) if passed else None
            quote = quote_result[0] if quote_result else None
            state = quote_result[1] if quote_result else target_state
            row = {
                "episode_id": episode["episode_id"], "city": episode["city"],
                "target_date": episode["target_date"], "prior_max_c": episode["prior_max_c"],
                "start_detect_ts_utc": episode["start_detect_ts"].isoformat(),
                "trigger_detect_ts_utc": episode["trigger_detect_ts"].isoformat(),
                "next_report_ts_utc": episode["report_ts"].isoformat(),
                "next_metar_crossed": episode["next_crossed"], "policy": policy,
                "delay_min": delay_min, "criterion": criterion,
                "clock_max_min": clock_max_min, "clock_passed": int(clock_passed),
                "path_passed": int(passed),
                "quote_path_revalidated": int(quote_result is not None),
                **state,
                "quote_covered": int(quote is not None),
                "quote_ts_utc": "" if quote is None else quote["ts"].isoformat(),
                "minutes_to_report": None if quote is None else round((episode["report_ts"] - quote["ts"]).total_seconds() / 60, 3),
                "bid": None if quote is None else quote["bid"], "ask": None if quote is None else quote["ask"],
                "bid_size": None if quote is None else quote["bid_size"], "ask_size": None if quote is None else quote["ask_size"],
            }
            add_economics(row, final_labels.get((episode["city"], episode["target_date"], episode["prior_max_c"])))
            output.append(row)
    # Execution grain: one first executable entry per city-day / previous
    # bracket / policy.  Failed early spikes do not block a later independently
    # retained re-cross, but repeated eligible polls cannot create more trades.
    selected: set[tuple[str, str, int, str]] = set()
    for row in sorted(output, key=lambda item: parse_dt(item["quote_ts_utc"]) if item["quote_ts_utc"] else datetime.max.replace(tzinfo=timezone.utc)):
        key = (row["city"], row["target_date"], int(row["prior_max_c"]), row["policy"])
        choose = bool(row.get("path_passed") and row.get("executable10") and key not in selected)
        row["selected_first_executable"] = int(choose)
        if choose:
            selected.add(key)
    return output


def report_bin(minutes_to_report: float) -> str:
    if minutes_to_report < 5:
        return "00-05m"
    if minutes_to_report < 10:
        return "05-10m"
    if minutes_to_report < 20:
        return "10-20m"
    return "20m+"


def report_window_rows(
    episodes: list[dict[str, Any]], quotes: list[dict[str, Any]], final_labels: dict[tuple[str, str, int], int],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for episode in episodes:
        if episode.get("trigger_detect_ts") is None:
            continue
        eligible: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for quote in quotes:
            if not (
                quote["city"] == episode["city"] and quote["target_date"] == episode["target_date"]
                and quote["prior_max_c"] == episode["prior_max_c"]
                and episode["trigger_detect_ts"] <= quote["ts"] < episode["report_ts"]
            ):
                continue
            state = path_state(episode, quote["ts"])
            if not state.get("retained"):
                continue
            minutes = (episode["report_ts"] - quote["ts"]).total_seconds() / 60
            bucket = report_bin(minutes)
            eligible.setdefault(bucket, (quote, state))
        for bucket, (quote, state) in eligible.items():
            row = {
                "episode_id": episode["episode_id"], "city": episode["city"],
                "target_date": episode["target_date"], "prior_max_c": episode["prior_max_c"],
                "next_metar_crossed": episode["next_crossed"], "report_window": bucket,
                "next_report_ts_utc": episode["report_ts"].isoformat(),
                "quote_ts_utc": quote["ts"].isoformat(),
                "minutes_to_report": round((episode["report_ts"] - quote["ts"]).total_seconds() / 60, 3),
                **state, "bid": quote["bid"], "ask": quote["ask"],
                "bid_size": quote["bid_size"], "ask_size": quote["ask_size"],
            }
            add_economics(row, final_labels.get((episode["city"], episode["target_date"], episode["prior_max_c"])))
            candidates.append(row)
    # A report-window policy may legitimately ignore an early failed spike and
    # act on a later re-cross.  Still allow only the first eligible decision per
    # report-cycle/bracket/window so recurrent oscillations do not multiply the
    # trade denominator.
    dedup: dict[tuple[str, str, int, str, str], dict[str, Any]] = {}
    for row in sorted(candidates, key=lambda item: parse_dt(item["quote_ts_utc"])):
        key = (
            row["city"], row["target_date"], int(row["prior_max_c"]),
            row["next_report_ts_utc"], row["report_window"],
        )
        dedup.setdefault(key, row)
    return list(dedup.values())


def summarize(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row[key]), row["city"])].append(row)
        groups[(str(row[key]), "ALL")].append(row)
    for (value, city), group in sorted(groups.items()):
        path_rows = [row for row in group if row.get("path_passed", 1)]
        covered = [row for row in path_rows if row.get("ask") is not None]
        executable = [row for row in covered if row.get("executable10")]
        selected = [row for row in executable if row.get("selected_first_executable", 1)]
        final = [row for row in selected if row.get("final_no_win") is not None]
        output.append({
            key: value, "city": city, "episodes": len(group), "path_passed": len(path_rows),
            "independent_dates": len({row["target_date"] for row in path_rows}),
            "quote_covered": len(covered), "executable10": len(executable),
            "selected_first_executable": len(selected),
            "next_metar_confirmed_n": sum(int(row["next_metar_crossed"]) for row in path_rows),
            "next_metar_confirmed_rate": None if not path_rows else round(sum(int(row["next_metar_crossed"]) for row in path_rows) / len(path_rows), 4),
            "selected_next_metar_confirmed_n": sum(int(row["next_metar_crossed"]) for row in selected),
            "selected_next_metar_confirmed_rate": None if not selected else round(sum(int(row["next_metar_crossed"]) for row in selected) / len(selected), 4),
            "median_ask": median(row.get("ask") for row in covered),
            "selected_median_ask": median(row.get("ask") for row in selected),
            "median_cost_with_fee": median(row.get("cost_with_fee") for row in selected),
            "median_upside_if_win": median(row.get("upside_if_win") for row in selected),
            "final_label_executable_n": len(final),
            "final_no_wins": sum(int(row["final_no_win"]) for row in final),
            "final_no_win_rate": None if not final else round(sum(int(row["final_no_win"]) for row in final) / len(final), 4),
            "final_pnl_per_share_sum": None if not final else round(sum(float(row["final_pnl_per_share"]) for row in final), 6),
        })
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=default_end_date())
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--daily-label-path",
        type=Path,
        default=None,
        help="Explicit legacy CSV for historical reproduction; current runs use canonical settlement_outcomes.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_date > args.end_date:
        raise ValueError(f"start date after end date: {args.start_date} > {args.end_date}")
    aligned = load_alignment(args.start_date, args.end_date)
    all_episodes = build_episodes(aligned)
    # Strategy grain: one first-cross candidate per report-cycle and previous
    # bracket.  Recurrent threshold oscillations stay in the path, but cannot
    # manufacture additional independent trade candidates.
    first_by_cycle: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for episode in all_episodes:
        key = (
            episode["city"], episode["target_date"], episode["prior_max_c"],
            episode["report_ts"].isoformat(),
        )
        if key not in first_by_cycle or episode["start_detect_ts"] < first_by_cycle[key]["start_detect_ts"]:
            first_by_cycle[key] = episode
    episodes = sorted(first_by_cycle.values(), key=lambda row: (row["start_detect_ts"], row["city"]))
    quotes = load_quotes(args.start_date, args.end_date)
    if args.daily_label_path is not None:
        final_labels = load_final_labels(args.daily_label_path)
        label_source = str(args.daily_label_path)
    else:
        final_labels = load_canonical_final_labels(
            args.db, args.start_date, args.end_date
        )
        label_source = f"{args.db}:settlement_outcomes"
    delay_rows = delay_policy_rows(all_episodes, quotes, final_labels)
    window_rows = report_window_rows(all_episodes, quotes, final_labels)
    delay_summary = summarize(delay_rows, "policy")
    window_summary = summarize(window_rows, "report_window")
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(
        OUT / "episodes.csv",
        [
            {key: value for key, value in row.items() if key not in {"context_rows", "episode_rows", "path_rows"}}
            for row in episodes
        ],
    )
    write_csv(OUT / "delay_policy_rows.csv", delay_rows)
    write_csv(OUT / "delay_policy_summary.csv", delay_summary)
    write_csv(OUT / "report_window_rows.csv", window_rows)
    write_csv(OUT / "report_window_summary.csv", window_summary)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": f"{args.start_date}..{args.end_date}", "cities": sorted(CITIES),
        "aligned_rows": len(aligned), "raw_recurrent_episodes": len(all_episodes),
        "qualified_source_persistence_episodes": sum(
            episode.get("trigger_detect_ts") is not None for episode in all_episodes
        ),
        "first_report_cycle_episodes": len(episodes), "quote_rows": len(quotes),
        "label_source": label_source,
        "label_max_target_date": max(
            (key[1] for key in final_labels), default=None
        ),
        "delay_summary": delay_summary, "report_window_summary": window_summary,
        "warnings": [
            "Exploratory same-sample timing policies; not a live gate.",
            "Source persistence excludes the live v5 <=20m report-clock boundary; current_v5_immediate_le20m restores it explicitly.",
            "Episodes are correlated within city-day; quote coverage is observer-driven and is an evidence gap, not selection.",
            "Next-METAR confirmation is not final WU settlement. Busan 2026-07-20 final label is provisional weather-confirmed.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
