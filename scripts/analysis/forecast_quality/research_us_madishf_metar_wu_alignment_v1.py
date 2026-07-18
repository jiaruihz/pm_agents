#!/usr/bin/env python3
"""Audit US MADISHF -> routine METAR -> native-F WU settlement alignment.

The daily layer measures source-basis bias on every settled city-day.  The
event layer reuses the frozen persistent-cross definition from the existing
eligibility study and asks whether the next routine METAR and final settlement
actually left the prior market bracket.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_high_frequency_strategy_eligibility_v2 as eligibility
from weather_data_feed.high_frequency_observation_sources import US_HFMETAR_CITIES
from weather_data_feed.observation_sources.fetchers import WEATHER_COM_API_KEY, WEATHER_COM_HISTORICAL_OBS


DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
ALIASES = {"New York": "NYC", "Los Angeles": "LA", "San Francisco": "SanFrancisco"}
US_CITIES = tuple(sorted(ALIASES.get(city, city) for city in US_HFMETAR_CITIES))
STATION_BY_CITY = {
    ALIASES.get(city, city): str(meta["station"]).upper()
    for city, meta in US_HFMETAR_CITIES.items()
}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def median(values: list[float]) -> float | str:
    return round(statistics.median(values), 3) if values else ""


def mean_abs(values: list[float]) -> float | str:
    return round(statistics.fmean(abs(value) for value in values), 3) if values else ""


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return round(center - half, 4), round(center + half, 4)


def load_native_wu_daily(
    keys: list[tuple[str, str]], *, proxy: str | None, timeout_sec: float, workers: int
) -> list[dict[str, Any]]:
    api_key = os.environ.get("WEATHER_COM_API_KEY", "").strip() or WEATHER_COM_API_KEY

    def fetch(key: tuple[str, str]) -> dict[str, Any]:
        city, target_date = key
        station = STATION_BY_CITY[city]
        location = f"{station}:9:US"
        url = WEATHER_COM_HISTORICAL_OBS.format(location=location)
        params = {
            "apiKey": api_key,
            "units": "e",
            "startDate": target_date.replace("-", ""),
            "endDate": target_date.replace("-", ""),
        }
        headers = {
            "Accept": "application/json",
            "Origin": "https://www.wunderground.com",
            "Referer": f"https://www.wunderground.com/history/daily/{station}",
            "User-Agent": "Mozilla/5.0 pm-agents-native-wu-alignment/1.0",
        }
        last_error: Exception | None = None
        payload: dict[str, Any] | None = None
        for _ in range(3):
            try:
                with httpx.Client(proxy=proxy, timeout=timeout_sec, trust_env=False, headers=headers) as client:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if payload is None:
            assert last_error is not None
            raise last_error
        observations = payload.get("observations") or []
        temps = [eligibility.safe_float(row.get("temp")) for row in observations]
        temps = [value for value in temps if value is not None]
        daily_max = max(temps) if temps else None
        peak_times = sorted(
            datetime.fromtimestamp(int(row["valid_time_gmt"]), tz=timezone.utc).isoformat()
            for row in observations
            if daily_max is not None
            and eligibility.safe_float(row.get("temp")) == daily_max
            and row.get("valid_time_gmt") is not None
        )
        return {
            "city": city,
            "target_date": target_date,
            "station": station,
            "wu_native_unit": "F",
            "wu_observation_rows": len(temps),
            "wu_native_daily_max_f": eligibility.round_half_up(daily_max) if daily_max is not None else "",
            "wu_peak_first_ts_utc": peak_times[0] if peak_times else "",
            "wu_peak_last_ts_utc": peak_times[-1] if peak_times else "",
            "wu_payload_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:16],
            "wu_fetch_status": "ok" if temps else "empty",
        }

    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch, key): key for key in keys}
        for future in concurrent.futures.as_completed(futures):
            city, target_date = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "city": city,
                        "target_date": target_date,
                        "station": STATION_BY_CITY[city],
                        "wu_native_unit": "F",
                        "wu_observation_rows": 0,
                        "wu_native_daily_max_f": "",
                        "wu_peak_first_ts_utc": "",
                        "wu_peak_last_ts_utc": "",
                        "wu_payload_hash": "",
                        "wu_fetch_status": f"{type(exc).__name__}: {exc}",
                    }
                )
    return sorted(rows, key=lambda row: (row["city"], row["target_date"]))


def load_settled_winners(
    db_path: Path, *, start_date: str, end_date: str
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], list[str]]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    placeholders = ",".join("?" for _ in US_CITIES)
    rows = conn.execute(
        f"""
        SELECT city, target_date, bracket, final_price
        FROM settlement_outcomes
        WHERE settlement_status='settled'
          AND target_date BETWEEN ? AND ?
          AND city IN ({placeholders})
        """,
        (start_date, end_date, *US_CITIES),
    ).fetchall()
    conn.close()
    winners: dict[tuple[str, str], str] = {}
    ladders: dict[tuple[str, str], list[str]] = {}
    for city, target_date, bracket, final_price in rows:
        key = (str(city), str(target_date))
        ladders.setdefault(key, [])
        if str(bracket) not in ladders[key]:
            ladders[key].append(str(bracket))
        if float(final_price) >= 0.999:
            winners[key] = str(bracket)
    return winners, ladders


def daily_maxima(
    fast_rows: list[dict[str, Any]],
    awc: dict[str, list[eligibility.ReferenceEvent]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], int]]:
    grouped_fast: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in fast_rows:
        if row["source"] != "noaa_madis_hfmetar" or row["city"] not in US_CITIES:
            continue
        key = (str(row["city"]), str(row["target_date"]))
        grouped_fast.setdefault(key, []).append(row)
    fast_max: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped_fast.items():
        ordered = sorted(rows, key=lambda row: row["obs_ts"])
        fast_max[key] = {
            "daily_max_f": max(int(row["temp_round"]) for row in ordered),
            "observation_rows": len(ordered),
            "first_obs_ts_utc": ordered[0]["obs_ts"],
            "last_obs_ts_utc": ordered[-1]["obs_ts"],
        }
    awc_max: dict[tuple[str, str], int] = {}
    for city, events in awc.items():
        if city not in US_CITIES:
            continue
        for event in events:
            key = (city, event.target_date)
            awc_max[key] = max(awc_max.get(key, event.temp_round), event.temp_round)
    return fast_max, awc_max


def build_daily_rows(
    winners: dict[tuple[str, str], str],
    fast_max: dict[tuple[str, str], dict[str, Any]],
    awc_max: dict[tuple[str, str], int],
    wu_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    wu = {(row["city"], row["target_date"]): row for row in wu_rows}
    output: list[dict[str, Any]] = []
    for key in sorted(set(winners) & set(fast_max)):
        city, target_date = key
        wu_row = wu.get(key, {})
        wu_max = eligibility.safe_float(wu_row.get("wu_native_daily_max_f"))
        fast_meta = fast_max[key]
        fast = int(fast_meta["daily_max_f"])
        routine = awc_max.get(key)
        winner = winners[key]
        peak_first = eligibility.parse_dt(wu_row.get("wu_peak_first_ts_utc"))
        peak_last = eligibility.parse_dt(wu_row.get("wu_peak_last_ts_utc"))
        fast_first = fast_meta["first_obs_ts_utc"]
        fast_last = fast_meta["last_obs_ts_utc"]
        peak_covered = bool(
            peak_first is not None
            and peak_last is not None
            and fast_first - timedelta(minutes=30) <= peak_last
            and fast_last + timedelta(minutes=30) >= peak_first
        )
        output.append(
            {
                "city": city,
                "target_date": target_date,
                "station": STATION_BY_CITY[city],
                "winning_bracket": winner,
                "madishf_daily_max_f": fast,
                "madishf_observation_rows": fast_meta["observation_rows"],
                "madishf_first_obs_ts_utc": fast_first.isoformat(),
                "madishf_last_obs_ts_utc": fast_last.isoformat(),
                "awc_routine_daily_max_f": routine if routine is not None else "",
                "wu_native_daily_max_f": int(wu_max) if wu_max is not None else "",
                "wu_peak_first_ts_utc": wu_row.get("wu_peak_first_ts_utc", ""),
                "wu_peak_last_ts_utc": wu_row.get("wu_peak_last_ts_utc", ""),
                "madishf_covered_wu_peak": peak_covered,
                "wu_observation_rows": wu_row.get("wu_observation_rows", 0),
                "wu_fetch_status": wu_row.get("wu_fetch_status", "missing"),
                "madishf_minus_wu_f": fast - wu_max if wu_max is not None else "",
                "awc_minus_wu_f": routine - wu_max if routine is not None and wu_max is not None else "",
                "madishf_in_winning_bracket": eligibility.parse_bracket_contains(winner, fast),
                "awc_in_winning_bracket": (
                    eligibility.parse_bracket_contains(winner, routine) if routine is not None else ""
                ),
                "wu_in_winning_bracket": (
                    eligibility.parse_bracket_contains(winner, int(wu_max)) if wu_max is not None else ""
                ),
            }
        )
    return output


def build_event_rows(
    fast_rows: list[dict[str, Any]],
    awc: dict[str, list[eligibility.ReferenceEvent]],
    winners: dict[tuple[str, str], str],
    ladders: dict[tuple[str, str], list[str]],
    wu_daily: dict[tuple[str, str], int],
    *,
    max_next_mins: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    events = eligibility.build_event_comparison(fast_rows, awc, max_next_mins)
    eligibility.annotate_market_brackets(events, ladders)
    eligibility.annotate_settlement_labels(events, winners)
    first = eligibility.first_cross_rows(events)
    persistent = eligibility.persistent_cross_rows(events)
    output: list[dict[str, Any]] = []
    for row in persistent:
        if row["city"] not in US_CITIES:
            continue
        key = (str(row["city"]), str(row["target_date"]))
        wu_max = wu_daily.get(key)
        output.append(
            {
                "city": row["city"],
                "target_date": row["target_date"],
                "station": STATION_BY_CITY[str(row["city"])],
                "fast_obs_ts_utc": row["fast_obs_ts_utc"],
                "fast_detect_ts_utc": row["fast_detect_ts_utc"],
                "previous_market_bracket": row["previous_market_bracket"],
                "fast_temp_f": row["fast_temp_unit"],
                "fast_temp_round_f": row["fast_temp_round"],
                "next_metar_temp_round_f": row["next_metar_temp_round"],
                "wu_native_daily_max_f": wu_max if wu_max is not None else "",
                "winning_bracket": row["winning_bracket"],
                "next_metar_left_old_bracket": row["next_metar_market_crossed"],
                "settlement_left_old_bracket": row["settlement_left_previous_bracket"],
                "fast_minus_wu_final_f": (
                    round(float(row["fast_temp_unit"]) - wu_max, 3) if wu_max is not None else ""
                ),
                "atlanta_like_false_cross": row["settlement_left_previous_bracket"] is False,
                "confirmation_first_obs_ts_utc": row["confirmation_first_obs_ts_utc"],
                "confirmation_first_margin": row["confirmation_first_margin"],
                "confirmation_latest_margin": row["confirmation_latest_margin"],
            }
        )
    return events, first, output


def summarize_city(daily: list[dict[str, Any]], persistent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for city in US_CITIES:
        days = [row for row in daily if row["city"] == city]
        if not days and not any(row["city"] == city for row in persistent):
            continue
        wu_days = [row for row in days if row["wu_native_daily_max_f"] != ""]
        comparable_days = [row for row in wu_days if row["madishf_covered_wu_peak"] is True]
        awc_days = [row for row in wu_days if row["awc_routine_daily_max_f"] != ""]
        events = [row for row in persistent if row["city"] == city]
        settled_events = [row for row in events if row["settlement_left_old_bracket"] != ""]
        next_labeled = [row for row in events if row["next_metar_left_old_bracket"] != ""]
        fast_bias = [float(row["madishf_minus_wu_f"]) for row in comparable_days]
        awc_bias = [float(row["awc_minus_wu_f"]) for row in awc_days]
        output.append(
            {
                "city": city,
                "station": STATION_BY_CITY[city],
                "daily_city_days": len(days),
                "wu_native_labeled_days": len(wu_days),
                "madishf_peak_covered_days": len(comparable_days),
                "awc_three_way_days": len(awc_days),
                "madishf_daily_in_winner_days": sum(
                    row["madishf_in_winning_bracket"] is True for row in comparable_days
                ),
                "madishf_daily_in_winner_rate": ratio(
                    sum(row["madishf_in_winning_bracket"] is True for row in comparable_days),
                    len(comparable_days),
                ),
                "awc_daily_in_winner_days": sum(row["awc_in_winning_bracket"] is True for row in awc_days),
                "awc_daily_in_winner_rate": ratio(
                    sum(row["awc_in_winning_bracket"] is True for row in awc_days), len(awc_days)
                ),
                "median_madishf_minus_wu_f": median(fast_bias),
                "mean_abs_madishf_minus_wu_f": mean_abs(fast_bias),
                "madishf_overshoot_wu_days": sum(value > 0 for value in fast_bias),
                "madishf_overshoot_wu_ge2f_days": sum(value >= 2 for value in fast_bias),
                "median_awc_minus_wu_f": median(awc_bias),
                "mean_abs_awc_minus_wu_f": mean_abs(awc_bias),
                "persistent_events": len(events),
                "persistent_next_metar_hits": sum(
                    row["next_metar_left_old_bracket"] is True for row in next_labeled
                ),
                "persistent_next_metar_rate": ratio(
                    sum(row["next_metar_left_old_bracket"] is True for row in next_labeled), len(next_labeled)
                ),
                "persistent_settlement_hits": sum(
                    row["settlement_left_old_bracket"] is True for row in settled_events
                ),
                "persistent_settlement_rate": ratio(
                    sum(row["settlement_left_old_bracket"] is True for row in settled_events), len(settled_events)
                ),
                "atlanta_like_false_crosses": sum(row["atlanta_like_false_cross"] is True for row in events),
            }
        )
    return output


def write_report(
    path: Path,
    *,
    generated_at: str,
    start_date: str,
    end_date: str,
    raw_fast_rows: int,
    event_rows: int,
    first_rows: int,
    daily: list[dict[str, Any]],
    persistent: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    wu_failures: int,
) -> None:
    false_events = [row for row in persistent if row["atlanta_like_false_cross"] is True]
    peak_covered = [row for row in daily if row["madishf_covered_wu_peak"] is True]
    awc_days = [row for row in daily if row["awc_routine_daily_max_f"] != ""]
    madishf_exact = sum(row["madishf_in_winning_bracket"] is True for row in peak_covered)
    awc_exact = sum(row["awc_in_winning_bracket"] is True for row in awc_days)
    wu_exact = sum(row["wu_in_winning_bracket"] is True for row in daily)
    fast_deltas = [float(row["madishf_minus_wu_f"]) for row in peak_covered]
    persistent_next_hits = sum(row["next_metar_left_old_bracket"] is True for row in persistent)
    persistent_settlement_hits = sum(row["settlement_left_old_bracket"] is True for row in persistent)
    false_ci = wilson_interval(len(false_events), len(persistent))
    lines = [
        "# US MADISHF → METAR → WU Settlement Alignment v1",
        "",
        f"Generated: `{generated_at}`",
        "Status: `research_snapshot`",
        "",
        "## Action",
        "",
        "Keep every US MADISHF expression in shadow. Native-F WU labels are clean, but MADISHF is a proxy feature rather than settlement truth; no live expansion is authorized.",
        "",
        "## Target",
        "",
        "Estimate city-level MADISHF-to-next-routine-METAR and MADISHF-to-native-F-WU settlement basis, with Atlanta-type persistent false crosses as the primary failure metric.",
        "",
        "## Data Integrity",
        "",
        f"- target dates: `{start_date}..{end_date}`",
        f"- deduplicated causal MADISHF observations: `{raw_fast_rows}`",
        f"- causal next-METAR comparison rows: `{event_rows}`",
        f"- first market-cross events: `{first_rows}`",
        f"- persistent market-cross events: `{len(persistent)}`",
        f"- MADISHF + settled winner city-days: `{len(daily)}`",
        f"- native-F WU fetch failures: `{wu_failures}`",
        f"- three-way AWC-covered city-days: `{sum(int(row['awc_three_way_days']) for row in summary)}`",
        "- Austin/Dallas/Houston lose AWC routine-METAR coverage after July 7; their daily MADISHF↔WU rows remain, but event-level next-METAR precision is a coverage gap.",
        "",
        "## Aggregate Result",
        "",
        f"- native-F WU max inside market winner: `{wu_exact}/{len(daily)}`.",
        f"- routine AWC METAR daily max inside market winner: `{awc_exact}/{len(awc_days)}`.",
        f"- MADISHF daily max inside market winner on peak-covered days: `{madishf_exact}/{len(peak_covered)}`; mismatch `{len(peak_covered) - madishf_exact}/{len(peak_covered)}`.",
        f"- MADISHF minus WU daily max: equal `{sum(value == 0 for value in fast_deltas)}`, hotter `{sum(value > 0 for value in fast_deltas)}`, cooler `{sum(value < 0 for value in fast_deltas)}`; absolute error >=2F `{sum(abs(value) >= 2 for value in fast_deltas)}`.",
        f"- persistent crosses: next-METAR confirmed `{persistent_next_hits}/{len(persistent)}`; final settlement confirmed `{persistent_settlement_hits}/{len(persistent)}`; Atlanta-type terminal false `{len(false_events)}/{len(persistent)}` (Wilson 95% `{false_ci}`).",
        "- the median city bias is generally 0F, so a single global offset cannot repair the basis mismatch.",
        "",
        "## City Summary",
        "",
        "| City | WU days / peak-covered / AWC days | MADISHF daily winner | median bias / MAE vs WU | overshoot / >=2F | persistent next METAR | persistent settlement | Atlanta-like false |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| `{row['city']}` | {row['wu_native_labeled_days']} / {row['madishf_peak_covered_days']} / {row['awc_three_way_days']} | "
            f"{row['madishf_daily_in_winner_days']}/{row['madishf_peak_covered_days']} ({row['madishf_daily_in_winner_rate']}) | "
            f"{row['median_madishf_minus_wu_f']}F / {row['mean_abs_madishf_minus_wu_f']}F | "
            f"{row['madishf_overshoot_wu_days']} / {row['madishf_overshoot_wu_ge2f_days']} | "
            f"{row['persistent_next_metar_hits']}/{row['persistent_events']} ({row['persistent_next_metar_rate']}) | "
            f"{row['persistent_settlement_hits']}/{row['persistent_events']} ({row['persistent_settlement_rate']}) | "
            f"{row['atlanta_like_false_crosses']} |"
        )
    lines.extend(
        [
            "",
            "## Atlanta-Type Persistent False Crosses",
            "",
        ]
    )
    if not false_events:
        lines.append("None in the covered window.")
    else:
        lines.extend(
            [
                "| City/date | prior bracket | MADISHF | next METAR | WU final | winner | fast-WU |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in false_events:
            lines.append(
                f"| `{row['city']} {row['target_date']}` | {row['previous_market_bracket']} | "
                f"{row['fast_temp_f']}F | {row['next_metar_temp_round_f']}F | "
                f"{row['wu_native_daily_max_f']}F | {row['winning_bracket']} | {row['fast_minus_wu_final_f']}F |"
            )
    lines.extend(
        [
            "",
            "## Funnels",
            "",
            f"- signal funnel (observation/event): `{raw_fast_rows}` causal fast observations → `{event_rows}` comparable rows → `{first_rows}` first crosses → `{len(persistent)}` persistent crosses.",
            f"- evidence funnel (city-day): `{len(daily)}` fast+settled days → `{sum(int(row['wu_native_labeled_days']) for row in summary)}` native-F WU labels → `{sum(int(row['madishf_peak_covered_days']) for row in summary)}` MADISHF peak-covered days → `{sum(int(row['awc_three_way_days']) for row in summary)}` three-way AWC days.",
            "- book/fill evidence is outside this source-basis study; no opportunity, quote, or fill denominator is inferred here.",
            "",
            "## Contract",
            "",
            "significance=NA; baseline=NA; forward=FAIL; conclusion=inconclusive",
            "",
            "The window is short and starts after collector activation. Results diagnose source basis and do not establish a fee-adjusted market residual.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--profiles", default=str(ROOT / "weather_data_feed/source_profiles.json"))
    parser.add_argument("--start-date", default="2026-07-07")
    parser.add_argument("--end-date", default="2026-07-17")
    parser.add_argument("--max-fast-age-min", type=float, default=30.0)
    parser.add_argument("--max-next-metar-min", type=float, default=90.0)
    parser.add_argument("--wu-proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--wu-timeout-sec", type=float, default=20.0)
    parser.add_argument("--wu-workers", type=int, default=6)
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "docs/analysis/2026-07/generated/us_madishf_metar_wu_alignment_v1"),
    )
    parser.add_argument(
        "--report",
        default=str(ROOT / "docs/analysis/2026-07/2026-07-18-us-madishf-metar-wu-alignment-v1.md"),
    )
    args = parser.parse_args()

    runtime = Path(args.runtime_root)
    profiles = eligibility.load_profiles(Path(args.profiles))
    awc, _ = eligibility.load_reference_events(runtime / "output/source_events/sources.jsonl", profiles)
    fast = eligibility.load_fast_observations(
        runtime / "output/high_frequency_observations/high_frequency_observations.jsonl",
        profiles,
        args.max_fast_age_min,
    )
    fast = [
        row
        for row in fast
        if row["source"] == "noaa_madis_hfmetar"
        and row["city"] in US_CITIES
        and args.start_date <= row["target_date"] <= args.end_date
    ]
    winners, ladders = load_settled_winners(
        Path(args.db_path), start_date=args.start_date, end_date=args.end_date
    )
    fast_max, awc_max = daily_maxima(fast, awc)
    keys = sorted(set(winners) & set(fast_max))
    wu_rows = load_native_wu_daily(
        keys,
        proxy=args.wu_proxy or None,
        timeout_sec=args.wu_timeout_sec,
        workers=args.wu_workers,
    )
    wu_daily = {
        (row["city"], row["target_date"]): int(row["wu_native_daily_max_f"])
        for row in wu_rows
        if row["wu_native_daily_max_f"] != ""
    }
    daily = build_daily_rows(winners, fast_max, awc_max, wu_rows)
    events, first, persistent = build_event_rows(
        fast,
        awc,
        winners,
        ladders,
        wu_daily,
        max_next_mins=args.max_next_metar_min,
    )
    summary = summarize_city(daily, persistent)

    out_dir = Path(args.out_dir)
    write_csv(out_dir / "wu_native_daily.csv", wu_rows)
    write_csv(out_dir / "daily_alignment.csv", daily)
    write_csv(out_dir / "persistent_events.csv", persistent)
    write_csv(out_dir / "summary_by_city.csv", summary)
    write_report(
        Path(args.report),
        generated_at=datetime.now(timezone.utc).isoformat(),
        start_date=args.start_date,
        end_date=args.end_date,
        raw_fast_rows=len(fast),
        event_rows=len(events),
        first_rows=len([row for row in first if row["city"] in US_CITIES]),
        daily=daily,
        persistent=persistent,
        summary=summary,
        wu_failures=sum(row["wu_fetch_status"] != "ok" for row in wu_rows),
    )
    print(
        json.dumps(
            {
                "fast_rows": len(fast),
                "event_rows": len(events),
                "first_us_rows": len([row for row in first if row["city"] in US_CITIES]),
                "persistent_us_rows": len(persistent),
                "daily_rows": len(daily),
                "wu_failures": sum(row["wu_fetch_status"] != "ok" for row in wu_rows),
                "atlanta_like_false_crosses": sum(row["atlanta_like_false_cross"] is True for row in persistent),
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
