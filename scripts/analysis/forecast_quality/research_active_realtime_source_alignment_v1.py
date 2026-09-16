#!/usr/bin/env python3
"""Audit already-running non-US fast sources against routine METAR, native-unit WU, settlement and book.

Research only.  This script reads existing collectors and canonical facts, fetches
historical WU observations in the market's native unit, and never submits orders.
"""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import csv
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_high_frequency_strategy_eligibility_v2 as eligibility  # noqa: E402
from weather_data_feed.observation_sources.fetchers import WEATHER_COM_API_KEY, WEATHER_COM_HISTORICAL_OBS  # noqa: E402


RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
OUT = ROOT / "docs/analysis/2026-07/generated/active_realtime_source_alignment_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-19-active-realtime-source-alignment-v1.md"
DB = ROOT / "runtime/weather.db"

# Tokyo is retained as an already-studied benchmark.  The four research picks
# are Helsinki, Seoul, Busan and Singapore.
CANDIDATES: dict[str, dict[str, str]] = {
    "Helsinki": {"source": "fmi", "source_city": "Helsinki", "station": "EFHK", "country": "FI", "timezone": "Europe/Helsinki", "basis": "same_airport"},
    "Seoul": {"source": "amos_runway", "source_city": "Seoul", "station": "RKSI", "country": "KR", "timezone": "Asia/Seoul", "basis": "same_airport_runway"},
    "Busan": {"source": "amos_runway", "source_city": "Busan", "station": "RKPK", "country": "KR", "timezone": "Asia/Seoul", "basis": "same_airport_runway"},
    "Singapore": {"source": "singapore_mss", "source_city": "Singapore", "station": "WSSS", "country": "SG", "timezone": "Asia/Singapore", "basis": "near_airport_reference_S24_to_WSSS"},
    "Tokyo": {"source": "jma_amedas", "source_city": "Tokyo", "station": "RJTT", "country": "JP", "timezone": "Asia/Tokyo", "basis": "same_airport_benchmark"},
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def half_up(value: float) -> int:
    return math.floor(value + 0.5)


def percentile(values: Iterable[float], q: float) -> float | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    return clean[round((len(clean) - 1) * q)]


def med(values: Iterable[float]) -> float | None:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def fmt(value: float | None, digits: int = 1) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def pct(n: int, d: int) -> str:
    return "NA" if not d else f"{n}/{d} ({100*n/d:.1f}%)"


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


def partition_paths(root: Path, filename: str, start_date: str, end_date: str) -> list[Path]:
    start = date.fromisoformat(start_date) - timedelta(days=1)
    end = date.fromisoformat(end_date) + timedelta(days=1)
    paths: list[Path] = []
    cursor = start
    while cursor <= end:
        path = root / cursor.isoformat() / filename
        if path.exists():
            paths.append(path)
        cursor += timedelta(days=1)
    return paths


def load_fast(start_date: str, end_date: str) -> list[dict[str, Any]]:
    by_source_city = {(meta["source"], meta["source_city"]): city for city, meta in CANDIDATES.items()}
    earliest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    root = RUNTIME / "output/high_frequency_observations"
    for path in partition_paths(root, "high_frequency_observations.jsonl", start_date, end_date):
        for raw in iter_jsonl(path):
            source_city = str(raw.get("city") or "")
            source = str(raw.get("source") or "")
            city = by_source_city.get((source, source_city))
            if city is None:
                continue
            if city == "Seoul" and raw.get("preferred_temperature_runway") and raw.get("runway") != raw.get("preferred_temperature_runway"):
                continue
            obs = parse_dt(raw.get("observation_time_utc"))
            detect = parse_dt(raw.get("local_detect_ts_utc") or raw.get("fetched_at_utc"))
            temp_c = number(raw.get("temp_c") if raw.get("temp_c") is not None else raw.get("point_temp_c"))
            target_date = str(raw.get("target_date") or "")
            if obs is None or detect is None or temp_c is None or not (start_date <= target_date <= end_date):
                continue
            key = (source, city, obs.isoformat(), str(raw.get("runway") or ""))
            normalized = {
                "city": city, "source": source, "target_date": target_date,
                "obs_ts": obs, "detect_ts": detect, "temp_c": temp_c,
                "temp_round_c": half_up(temp_c), "runway": raw.get("runway") or "",
                "first_seen_lag_min": (detect - obs).total_seconds() / 60.0,
            }
            if key not in earliest or detect < earliest[key]["detect_ts"]:
                earliest[key] = normalized
    return sorted(earliest.values(), key=lambda row: (row["detect_ts"], row["city"]))


def load_awc(start_date: str, end_date: str) -> dict[str, list[dict[str, Any]]]:
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    root = RUNTIME / "output/source_events"
    wanted = set(CANDIDATES)
    for path in partition_paths(root, "sources.jsonl", start_date, end_date):
        for raw in iter_jsonl(path):
            if raw.get("source") != "aviationweather_metar" or str(raw.get("city") or "") not in wanted:
                continue
            city = str(raw["city"])
            report = parse_dt(raw.get("source_report_ts_utc"))
            detect = parse_dt(raw.get("local_detect_ts_utc") or raw.get("ts_utc"))
            temp_c = number(raw.get("temp_c"))
            target_date = str(raw.get("target_date") or "")
            if report is None or detect is None or temp_c is None or not (start_date <= target_date <= end_date):
                continue
            key = (city, report.isoformat())
            row = {
                "city": city, "target_date": target_date, "report_ts": report,
                "detect_ts": detect, "temp_c": temp_c, "temp_round_c": half_up(temp_c),
            }
            if key not in earliest or detect < earliest[key]["detect_ts"]:
                earliest[key] = row
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in earliest.values():
        output[row["city"]].append(row)
    for city, rows in output.items():
        rows.sort(key=lambda row: (row["detect_ts"], row["report_ts"]))
        running: dict[str, int] = {}
        for row in rows:
            row["running_max_c"] = max(running.get(row["target_date"], row["temp_round_c"]), row["temp_round_c"])
            running[row["target_date"]] = row["running_max_c"]
    return output


def next_metar_alignment(fast: list[dict[str, Any]], awc: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in fast:
        refs = awc.get(row["city"], [])
        detects = [ref["detect_ts"] for ref in refs]
        prior_idx = bisect.bisect_right(detects, row["detect_ts"]) - 1
        if prior_idx < 0:
            continue
        prior = refs[prior_idx]
        following = next(
            (ref for ref in refs[prior_idx + 1:] if ref["detect_ts"] > row["detect_ts"] and ref["report_ts"] > prior["report_ts"]),
            None,
        )
        if following is None or prior["target_date"] != row["target_date"] or following["target_date"] != row["target_date"]:
            continue
        lead = (following["detect_ts"] - row["detect_ts"]).total_seconds() / 60.0
        if not (0 <= lead <= 90):
            continue
        output.append({
            "city": row["city"], "source": row["source"], "target_date": row["target_date"],
            "fast_obs_ts_utc": row["obs_ts"].isoformat(), "fast_detect_ts_utc": row["detect_ts"].isoformat(),
            "fast_temp_c": row["temp_c"], "fast_temp_round_c": row["temp_round_c"],
            "first_seen_lag_min": round(row["first_seen_lag_min"], 3),
            "prior_metar_running_max_c": prior["running_max_c"],
            "next_metar_report_ts_utc": following["report_ts"].isoformat(),
            "next_metar_detect_ts_utc": following["detect_ts"].isoformat(),
            "next_metar_temp_round_c": following["temp_round_c"],
            "lead_to_next_metar_min": round(lead, 3),
            "fast_minus_next_metar_c": row["temp_round_c"] - following["temp_round_c"],
            "fast_exact_next_metar": int(row["temp_round_c"] == following["temp_round_c"]),
            "fast_within1_next_metar": int(abs(row["temp_round_c"] - following["temp_round_c"]) <= 1),
            "fast_implies_cross": int(row["temp_round_c"] > prior["running_max_c"]),
            "next_metar_crossed": int(following["temp_round_c"] > prior["running_max_c"]),
        })
    return output


def fetch_wu(city: str, target_date: str, proxy: str | None, timeout: float) -> dict[str, Any]:
    meta = CANDIDATES[city]
    station = meta["station"]
    location = f"{station}:9:{meta['country']}"
    headers = {
        "Accept": "application/json", "Origin": "https://www.wunderground.com",
        "Referer": f"https://www.wunderground.com/history/daily/{station}",
        "User-Agent": "Mozilla/5.0 pm-agents-active-source-alignment/1.0",
    }
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False, headers=headers) as client:
                response = client.get(
                    WEATHER_COM_HISTORICAL_OBS.format(location=location),
                    params={"apiKey": WEATHER_COM_API_KEY, "units": "m", "startDate": target_date.replace("-", ""), "endDate": target_date.replace("-", "")},
                )
                response.raise_for_status()
                payload = response.json()
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if payload is None:
        assert last_error is not None
        raise last_error
    zone = ZoneInfo(meta["timezone"])
    observations = []
    for raw in payload.get("observations") or []:
        ts_raw = raw.get("valid_time_gmt")
        temp = number(raw.get("temp"))
        if ts_raw is None or temp is None:
            continue
        ts = datetime.fromtimestamp(int(ts_raw), timezone.utc)
        if ts.astimezone(zone).date().isoformat() == target_date:
            observations.append((ts, temp))
    maximum = max((temp for _, temp in observations), default=None)
    peaks = sorted(ts for ts, temp in observations if maximum is not None and temp == maximum)
    return {
        "city": city, "target_date": target_date, "wu_station": station,
        "wu_native_unit": "C", "wu_rows": len(observations),
        "wu_native_daily_max_c": half_up(maximum) if maximum is not None else "",
        "wu_peak_first_ts_utc": peaks[0].isoformat() if peaks else "",
        "wu_peak_last_ts_utc": peaks[-1].isoformat() if peaks else "",
        "wu_status": "ok" if observations else "empty",
    }


def load_wu(start_date: str, end_date: str, proxy: str | None, timeout: float, workers: int) -> list[dict[str, Any]]:
    keys = [(city, day.isoformat()) for city in CANDIDATES for day in date_range(start_date, end_date)]
    output: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_wu, city, target_date, proxy, timeout): (city, target_date) for city, target_date in keys}
        for future in concurrent.futures.as_completed(futures):
            city, target_date = futures[future]
            try:
                output.append(future.result())
            except Exception as exc:  # noqa: BLE001
                output.append({"city": city, "target_date": target_date, "wu_status": f"{type(exc).__name__}: {exc}", "wu_rows": 0})
    return sorted(output, key=lambda row: (row["city"], row["target_date"]))


def date_range(start_date: str, end_date: str) -> Iterable[date]:
    cursor, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def load_winners(start_date: str, end_date: str) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    placeholders = ",".join("?" for _ in CANDIDATES)
    rows = conn.execute(
        f"SELECT city,target_date,bracket FROM settlement_outcomes WHERE settlement_status='settled' AND final_price>=.999 AND target_date BETWEEN ? AND ? AND city IN ({placeholders})",
        (start_date, end_date, *CANDIDATES.keys()),
    ).fetchall()
    conn.close()
    return {(str(city), str(target_date)): str(bracket) for city, target_date, bracket in rows}


def build_daily(fast: list[dict[str, Any]], awc: dict[str, list[dict[str, Any]]], wu: list[dict[str, Any]], winners: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    fast_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in fast:
        fast_groups[(row["city"], row["target_date"])].append(row)
    awc_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for city, rows in awc.items():
        for row in rows:
            awc_groups[(city, row["target_date"])].append(row)
    wu_map = {(row["city"], row["target_date"]): row for row in wu if row.get("wu_status") == "ok"}
    output: list[dict[str, Any]] = []
    for key in sorted(set(fast_groups) & set(winners)):
        if key not in wu_map:
            continue
        rows = fast_groups[key]
        wu_row = wu_map[key]
        winner = winners[key]
        source_max_raw = max(float(row["temp_c"]) for row in rows)
        source_max = half_up(source_max_raw)
        routine_max = max((int(row["temp_round_c"]) for row in awc_groups.get(key, [])), default=None)
        wu_max = int(wu_row["wu_native_daily_max_c"])
        first_obs = min(row["obs_ts"] for row in rows)
        last_obs = max(row["obs_ts"] for row in rows)
        peak_first = parse_dt(wu_row.get("wu_peak_first_ts_utc"))
        peak_last = parse_dt(wu_row.get("wu_peak_last_ts_utc"))
        peak_covered = bool(peak_first and peak_last and first_obs <= peak_first and last_obs >= peak_last)
        source_in_winner = eligibility.parse_bracket_contains(winner, source_max)
        wu_in_winner = eligibility.parse_bracket_contains(winner, wu_max)
        output.append({
            "city": key[0], "source": CANDIDATES[key[0]]["source"], "source_basis": CANDIDATES[key[0]]["basis"],
            "target_date": key[1], "winning_bracket": winner, "source_rows": len(rows),
            "source_first_obs_ts_utc": first_obs.isoformat(), "source_last_obs_ts_utc": last_obs.isoformat(),
            "source_daily_max_raw_c": round(source_max_raw, 3), "source_daily_max_round_c": source_max,
            "routine_metar_daily_max_c": routine_max if routine_max is not None else "",
            "wu_native_daily_max_c": wu_max, "source_minus_wu_round_c": source_max - wu_max,
            "source_peak_covered_wu_peak": int(peak_covered),
            "source_in_winning_bracket": int(source_in_winner),
            "routine_in_winning_bracket": "" if routine_max is None else int(eligibility.parse_bracket_contains(winner, routine_max)),
            "wu_in_winning_bracket": int(wu_in_winner),
            "terminal_false_cross_daily": int(peak_covered and wu_in_winner and source_max > wu_max and not source_in_winner),
            "wu_peak_first_ts_utc": wu_row["wu_peak_first_ts_utc"], "wu_peak_last_ts_utc": wu_row["wu_peak_last_ts_utc"],
        })
    return output


def book_class(ask: float | None, size: float | None) -> str:
    if ask is None:
        return "no_ask"
    if ask > 0.97 + 1e-12:
        return "ask_above_0.97"
    if size is None or size < 10 - 1e-12:
        return "top_size_below_10"
    return "executable"


def load_canonical_fill_map(order_ids: set[str]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "fees": 0.0})
    if not order_ids:
        return output
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    for start in range(0, len(order_ids), 500):
        batch = sorted(order_ids)[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for order_id, shares, price, fees in conn.execute(
            f"SELECT order_id,filled_shares,filled_price,COALESCE(fees_usd,0) FROM fills WHERE order_id IN ({placeholders})",
            batch,
        ):
            output[str(order_id)]["shares"] += float(shares)
            output[str(order_id)]["cost"] += float(shares) * float(price)
            output[str(order_id)]["fees"] += float(fees)
    conn.close()
    return output


def load_runner_events(start_date: str, end_date: str, winners: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    wanted = {(meta["source"], city) for city, meta in CANDIDATES.items()}
    first: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    event_keys_by_group: dict[tuple[str, str, str, int], set[str]] = defaultdict(set)
    for raw in iter_jsonl(RUNTIME / "output/fast_source_prev_no_trial/events.jsonl"):
        source, city = str(raw.get("source") or ""), str(raw.get("city") or "")
        target_date = str(raw.get("target_date") or "")
        previous = number(raw.get("t_minus_1_no_bracket_c"))
        if (source, city) not in wanted or previous is None or raw.get("source_cross_confirmed") is not True or not (start_date <= target_date <= end_date):
            continue
        winner = winners.get((city, target_date))
        detect = parse_dt(raw.get("source_detect_ts_utc") or raw.get("ts_utc"))
        if winner is None or detect is None:
            continue
        key = (city, target_date, source, int(previous))
        event_keys_by_group[key].add(str(raw.get("event_key") or ""))
        row = {
            "city": city, "source": source, "target_date": target_date,
            "event_key": str(raw.get("event_key") or ""), "token_id": str(raw.get("token_id") or ""),
            "source_detect_ts_utc": detect.isoformat(), "previous_bracket_c": int(previous),
            "winning_bracket": winner,
            "settlement_left_previous_bracket": int(not eligibility.parse_bracket_contains(winner, int(previous))),
            "best_ask": number(raw.get("best_ask")), "ask_size": number(raw.get("ask_size")),
        }
        row["max97_taker10_book_class"] = book_class(row["best_ask"], row["ask_size"])
        if key not in first or detect.isoformat() < first[key]["source_detect_ts_utc"]:
            first[key] = row

    order_children: list[tuple[str, str, str]] = []
    for raw in iter_jsonl(RUNTIME / "output/fast_source_prev_no_trial/orders.jsonl"):
        event_key, order_id = str(raw.get("event_key") or ""), str(raw.get("order_id") or "")
        if event_key and order_id:
            order_children.append((event_key, order_id, str(raw.get("child_order_role") or "unknown")))
    fills = load_canonical_fill_map({order_id for _, order_id, _ in order_children})
    by_event: dict[str, dict[str, float]] = defaultdict(lambda: {"canonical_fill_shares": 0.0, "canonical_fill_cost": 0.0, "canonical_fill_fees": 0.0, "canonical_maker_fill_shares": 0.0})
    for event_key, order_id, role in order_children:
        fill = fills.get(order_id)
        if not fill:
            continue
        by_event[event_key]["canonical_fill_shares"] += fill["shares"]
        by_event[event_key]["canonical_fill_cost"] += fill["cost"]
        by_event[event_key]["canonical_fill_fees"] += fill["fees"]
        if role == "maker":
            by_event[event_key]["canonical_maker_fill_shares"] += fill["shares"]
    output = sorted(first.values(), key=lambda row: row["source_detect_ts_utc"])
    for row in output:
        group = (row["city"], row["target_date"], row["source"], row["previous_bracket_c"])
        event_keys = event_keys_by_group[group]
        row.update({
            metric: sum(by_event[event_key][metric] for event_key in event_keys)
            for metric in ("canonical_fill_shares", "canonical_fill_cost", "canonical_fill_fees", "canonical_maker_fill_shares")
        })
        row["grouped_event_keys"] = len(event_keys)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-08")
    parser.add_argument("--end-date", default="2026-07-17")
    parser.add_argument("--wu-proxy", default=os.environ.get("WEATHER_DATA_FEED_WEATHER_PROXY", ""))
    parser.add_argument("--wu-timeout-sec", type=float, default=25.0)
    parser.add_argument("--wu-workers", type=int, default=8)
    args = parser.parse_args()

    fast = load_fast(args.start_date, args.end_date)
    awc = load_awc(args.start_date, args.end_date)
    next_rows = next_metar_alignment(fast, awc)
    winners = load_winners(args.start_date, args.end_date)
    wu = load_wu(args.start_date, args.end_date, args.wu_proxy or None, args.wu_timeout_sec, args.wu_workers)
    daily = build_daily(fast, awc, wu, winners)
    runner = load_runner_events(args.start_date, args.end_date, winners)

    summary_rows: list[dict[str, Any]] = []
    table: list[str] = []
    for city, meta in CANDIDATES.items():
        f = [row for row in fast if row["city"] == city]
        n = [row for row in next_rows if row["city"] == city]
        d = [row for row in daily if row["city"] == city and row["source_peak_covered_wu_peak"] == 1]
        e = [row for row in runner if row["city"] == city]
        correct = [row for row in e if row["settlement_left_previous_bracket"] == 1]
        false = [row for row in e if row["settlement_left_previous_bracket"] == 0]
        source_hits = sum(row["source_in_winning_bracket"] == 1 for row in d)
        false_daily = sum(row["terminal_false_cross_daily"] == 1 for row in d)
        rate = source_hits / len(d) if d else 0.0
        if city == "Tokyo":
            action = "benchmark_shadow"
        elif city == "Helsinki":
            action = "P1_research_feature_shadow"
        elif rate >= 0.9 and false_daily <= 1:
            action = "P1_forward_shadow"
        elif rate >= 0.8:
            action = "P2_feature_shadow"
        else:
            action = "feature_only_not_cross_trigger"
        row = {
            "city": city, "source": meta["source"], "basis": meta["basis"], "action": action,
            "distinct_observations": len(f), "observation_dates": len({x["target_date"] for x in f}),
            "first_seen_lag_p50_min": round(med(x["first_seen_lag_min"] for x in f) or 0, 3),
            "first_seen_lag_p90_min": round(percentile((x["first_seen_lag_min"] for x in f), .9) or 0, 3),
            "next_metar_rows": len(n), "next_metar_exact": sum(x["fast_exact_next_metar"] for x in n),
            "next_metar_within1": sum(x["fast_within1_next_metar"] for x in n),
            "lead_to_next_metar_p50_min": round(med(x["lead_to_next_metar_min"] for x in n) or 0, 3),
            "peak_covered_days": len(d), "source_winner_hits": source_hits,
            "wu_winner_hits": sum(x["wu_in_winning_bracket"] == 1 for x in d),
            "routine_winner_hits": sum(x["routine_in_winning_bracket"] == 1 for x in d),
            "terminal_false_cross_days": false_daily,
            "persistent_first_bracket_events": len(e), "persistent_correct": len(correct), "persistent_false": len(false),
            "correct_executable_first_read": sum(x["max97_taker10_book_class"] == "executable" for x in correct),
            "false_executable_first_read": sum(x["max97_taker10_book_class"] == "executable" for x in false),
            "canonical_fill_shares": sum(float(x["canonical_fill_shares"]) for x in e),
            "canonical_maker_fill_shares": sum(float(x["canonical_maker_fill_shares"]) for x in e),
            "canonical_fill_cost": sum(float(x["canonical_fill_cost"]) for x in e),
            "canonical_fill_fees": sum(float(x["canonical_fill_fees"]) for x in e),
        }
        summary_rows.append(row)
        table.append(
            f"| `{city}/{meta['source']}` | {fmt(row['first_seen_lag_p50_min'])}/{fmt(row['first_seen_lag_p90_min'])}m | "
            f"{pct(row['next_metar_exact'], row['next_metar_rows'])} / {pct(row['next_metar_within1'], row['next_metar_rows'])} | "
            f"{pct(source_hits, len(d))} | {false_daily} | {pct(len(correct), len(e))} | "
            f"{pct(row['correct_executable_first_read'], len(correct))} | {pct(row['false_executable_first_read'], len(false))} | `{action}` |"
        )

    bad_day_lines = [
        f"| `{row['city']}` | `{row['target_date']}` | {row['source_daily_max_raw_c']}→{row['source_daily_max_round_c']}°C | "
        f"{row['wu_native_daily_max_c']}°C | `{row['winning_bracket']}` |"
        for row in daily if row["terminal_false_cross_daily"] == 1
    ]

    write_csv(OUT / "daily_source_wu_settlement.csv", daily)
    write_csv(OUT / "next_routine_metar_alignment.csv", next_rows)
    write_csv(OUT / "first_persistent_bracket_events.csv", runner)
    write_csv(OUT / "summary_by_city_source.csv", summary_rows)
    write_csv(OUT / "wu_native_fetch_status.csv", wu)

    generated = datetime.now(timezone.utc).isoformat()
    REPORT.write_text(f"""# Active realtime source alignment v1

Generated: `{generated}`
Window: `{args.start_date}..{args.end_date}` settled city-days
Status: `research/shadow_only`; no live authorization

## 结论

现有无新 key 的实时源里，**Helsinki/FMI 是下一轮最值得继续 forward shadow 的城市**。Seoul/Busan AMOS 和 Singapore/MSS 虽然 first-seen 很快、对下一份 routine METAR 也有信息，但不能把小数快源的日内最高直接当 WU 整数结算事实；它们出现了与 Atlanta 同结构的 terminal overshoot，应只作为概率特征。Tokyo/JMA 保留作已知 benchmark，不因本轮重复样本升级 live。

## 同口径结果

| city/source | first-seen p50/p90 | next METAR exact / within1 | source daily max in WU winner | terminal false-cross days | persistent event correct | correct executable | false executable | action |
|---|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(table)}

这里的 daily denominator 只保留快源时间覆盖 WU peak 的 city-day。WU 用 market native `units=m` 直接取摄氏度日高，**没有 C→F→C 或 double rounding**；canonical winning bracket 是 label。routine METAR、WU native 和快源日高分开列，不能相互替代。

错误 persistent event 更容易成交的形态在非美国源也出现：Seoul `2/2`、Busan `1/2`、Singapore `1/1` 错误事件首读可成交；对应正确事件只有 `7/26`、`2/23`、`0/12`。因此不能拿 90%+ persistent correctness 当可交易胜率。

## Terminal false city-days

| city | date | fast raw→round max | WU native max | winner |
|---|---|---:|---:|---|
{chr(10).join(bad_day_lines)}

## Atlanta negative control

- `terminal_false_cross_daily=1` 定义为：快源完整覆盖 WU peak、快源取整日高高于 WU native 日高、且快源落在错误 bracket，而 WU native 落在最终 winning bracket。
- persistent event 固定为 runner 的首个 `city-day + previous bracket` 事件，不能用重复 polling 扩大命中率。
- execution 固定为 source first-seen 后 direct best ask，`max_no_ask=0.97`、top size >=10。正确与错误事件分开报，盘口缺失是 coverage gap，不是策略过滤。
- fill 只从 canonical `fills` 回连 exchange order id；maker 在 submission journal 中尚未成交时也不会再漏记。

## 研究动作

1. Helsinki/FMI：继续 zero-notional forward shadow，累计至少 30 个 settled persistent events；重点看 terminal false cross 和可成交正确事件，而不是只看 next-METAR exact。
2. Seoul/Busan AMOS：保留 1-minute collector，但把 runway decimal max 作为 `P(WU leaves bracket)` 特征；禁止 raw cross 直接触发 previous-NO。
3. Singapore/MSS：S24→WSSS 本身是 cross-station basis，再叠加 terminal overshoot，只做 feature shadow。
4. Tokyo/JMA：继续现有 shadow/小样本观察；本报告不改变它的生产授权。
5. 新 key 到位后，再把 Paris/Amsterdam/Madrid 接入同一份 admission test；不要另造一套只看 source accuracy 的口径。

## Files

- `daily_source_wu_settlement.csv`: source daily max → WU native-unit max → winning bracket
- `next_routine_metar_alignment.csv`: distinct source observation → next routine AWC METAR
- `first_persistent_bracket_events.csv`: first bracket event → settlement → direct book → canonical fills
- `summary_by_city_source.csv`: city/source scoreboard
- `wu_native_fetch_status.csv`: WU request coverage/status without credentials

## Contract

signal funnel = distinct observations → first persistent city-day/previous-bracket event; evidence funnel = next routine METAR → native-unit WU → canonical settlement → direct book → canonical fill; baseline = same-time market; forward = collecting; conclusion = Helsinki P1 shadow, AMOS/MSS feature-only, no new live city
""", encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "fast_rows": len(fast), "next_rows": len(next_rows), "daily_rows": len(daily), "runner_events": len(runner), "summary": summary_rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
