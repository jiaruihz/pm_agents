#!/usr/bin/env python3
"""Evaluate fast airport sources against next METAR and settlement-aligned references."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
FAST_SOURCES = {"noaa_madis_hfmetar", "fmi", "ims_lod", "mgm"}
FAST_CITIES = {
    "Ankara", "Atlanta", "Austin", "Chicago", "Dallas", "Denver", "Houston", "LA", "Miami", "NYC",
    "SanFrancisco", "Seattle", "Helsinki", "Istanbul", "TelAviv",
}
ALIASES = {
    "New York": "NYC",
    "Los Angeles": "LA",
    "San Francisco": "SanFrancisco",
    "Tel Aviv": "TelAviv",
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def safe_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def temp_in_unit(temp_c: float, unit: str) -> float:
    return temp_c * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else temp_c


def canonical_city(value: Any) -> str:
    city = str(value or "").strip()
    return ALIASES.get(city, city)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    snapshot_size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < snapshot_size:
            raw_line = handle.readline()
            if not raw_line:
                break
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def load_profiles(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["city"]): row for row in payload["source_profiles"]}


def local_date(ts: datetime, profile: dict[str, Any]) -> str:
    return ts.astimezone(ZoneInfo(str(profile.get("timezone_name") or "UTC"))).date().isoformat()


@dataclass
class ReferenceEvent:
    city: str
    source: str
    report_ts: datetime
    detect_ts: datetime
    temp_c: float
    temp_round: int
    target_date: str
    running_max_round: int = 0


def load_reference_events(
    path: Path, profiles: dict[str, dict[str, Any]]
) -> tuple[dict[str, list[ReferenceEvent]], dict[str, list[ReferenceEvent]]]:
    earliest: dict[tuple[str, str, str], ReferenceEvent] = {}
    for row in iter_jsonl(path):
        source = str(row.get("source") or "")
        if source not in {"aviationweather_metar", "synopticdata_timeseries"}:
            continue
        city = canonical_city(row.get("city"))
        if city not in profiles:
            continue
        report_ts = parse_dt(row.get("source_report_ts_utc"))
        detect_ts = parse_dt(row.get("local_detect_ts_utc") or row.get("ts_utc"))
        temp_c = safe_float(row.get("temp_c"))
        if report_ts is None or detect_ts is None or temp_c is None:
            continue
        unit = str(profiles[city].get("unit") or "C")
        event = ReferenceEvent(
            city=city,
            source=source,
            report_ts=report_ts,
            detect_ts=detect_ts,
            temp_c=temp_c,
            temp_round=round_half_up(temp_in_unit(temp_c, unit)),
            target_date=local_date(report_ts, profiles[city]),
        )
        key = (source, city, report_ts.isoformat())
        if key not in earliest or detect_ts < earliest[key].detect_ts:
            earliest[key] = event

    by_source: dict[str, dict[str, list[ReferenceEvent]]] = {
        "aviationweather_metar": defaultdict(list),
        "synopticdata_timeseries": defaultdict(list),
    }
    for event in earliest.values():
        by_source[event.source][event.city].append(event)
    for source_groups in by_source.values():
        for city, events in source_groups.items():
            events.sort(key=lambda event: (event.detect_ts, event.report_ts))
            running: dict[str, int] = {}
            for event in events:
                event.running_max_round = max(running.get(event.target_date, event.temp_round), event.temp_round)
                running[event.target_date] = event.running_max_round
    return by_source["aviationweather_metar"], by_source["synopticdata_timeseries"]


def load_fast_observations(
    path: Path, profiles: dict[str, dict[str, Any]], max_age_min: float
) -> list[dict[str, Any]]:
    earliest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in iter_jsonl(path):
        source = str(row.get("source") or "")
        city = canonical_city(row.get("city"))
        if source not in FAST_SOURCES or city not in FAST_CITIES or city not in profiles:
            continue
        obs_ts = parse_dt(row.get("observation_time_utc"))
        detect_ts = parse_dt(row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        temp_c = safe_float(row.get("temp_c") or row.get("point_temp_c"))
        if obs_ts is None or detect_ts is None or temp_c is None:
            continue
        age_min = (detect_ts - obs_ts).total_seconds() / 60.0
        if age_min < -2 or age_min > max_age_min:
            continue
        unit = str(profiles[city].get("unit") or "C")
        normalized = {
            "city": city,
            "source": source,
            "obs_ts": obs_ts,
            "detect_ts": detect_ts,
            "target_date": local_date(obs_ts, profiles[city]),
            "temp_c": temp_c,
            "market_unit": unit.upper(),
            "temp_unit": temp_in_unit(temp_c, unit),
            "temp_round": round_half_up(temp_in_unit(temp_c, unit)),
            "age_min": age_min,
        }
        key = (source, city, obs_ts.isoformat())
        if key not in earliest or detect_ts < earliest[key]["detect_ts"]:
            earliest[key] = normalized
    return sorted(earliest.values(), key=lambda row: (row["detect_ts"], row["city"], row["source"]))


def build_event_comparison(
    fast_rows: list[dict[str, Any]], awc_events: dict[str, list[ReferenceEvent]], max_next_min: float
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for fast in fast_rows:
        events = awc_events.get(fast["city"], [])
        detects = [event.detect_ts for event in events]
        previous_index = bisect.bisect_right(detects, fast["detect_ts"]) - 1
        if previous_index < 0:
            continue
        previous = events[previous_index]
        following = next(
            (
                event
                for event in events[previous_index + 1 :]
                if event.detect_ts > fast["detect_ts"] and event.report_ts > previous.report_ts
            ),
            None,
        )
        if following is None or following.target_date != fast["target_date"] or previous.target_date != fast["target_date"]:
            continue
        lead_min = (following.detect_ts - fast["detect_ts"]).total_seconds() / 60.0
        if lead_min < 0 or lead_min > max_next_min:
            continue
        output.append(
            {
                "city": fast["city"],
                "target_date": fast["target_date"],
                "fast_source": fast["source"],
                "fast_obs_ts_utc": fast["obs_ts"].isoformat(),
                "fast_detect_ts_utc": fast["detect_ts"].isoformat(),
                "fast_age_min": round(fast["age_min"], 3),
                "fast_market_unit": fast["market_unit"],
                "fast_temp_unit": round(float(fast["temp_unit"]), 3),
                "fast_temp_round": fast["temp_round"],
                "previous_metar_report_ts_utc": previous.report_ts.isoformat(),
                "previous_metar_detect_ts_utc": previous.detect_ts.isoformat(),
                "previous_metar_temp_round": previous.temp_round,
                "previous_metar_running_max_round": previous.running_max_round,
                "next_metar_report_ts_utc": following.report_ts.isoformat(),
                "next_metar_detect_ts_utc": following.detect_ts.isoformat(),
                "next_metar_temp_round": following.temp_round,
                "next_metar_report_clock_distance_min": round(
                    abs((following.report_ts - fast["detect_ts"]).total_seconds()) / 60.0, 3
                ),
                "lead_to_next_metar_min": round(lead_min, 3),
                "fast_implies_cross": fast["temp_round"] > previous.running_max_round,
                "next_metar_crossed": following.temp_round > previous.running_max_round,
            }
        )
    return output


def first_cross_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if not row.get("fast_implies_market_cross", False):
            continue
        key = (
            row["city"], row["target_date"], row["fast_source"],
            row["previous_market_bracket"],
        )
        if key not in first or row["fast_detect_ts_utc"] < first[key]["fast_detect_ts_utc"]:
            first[key] = row
    return sorted(first.values(), key=lambda row: row["fast_detect_ts_utc"])


def persistent_cross_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Confirm a cross with two distinct observations and a stronger latest margin."""
    states: dict[tuple[Any, ...], dict[str, Any]] = {}
    confirmed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: item["fast_detect_ts_utc"]):
        key = (
            row["city"], row["target_date"], row["fast_source"],
            row.get("previous_market_bracket", ""),
        )
        upper = safe_float(row.get("previous_market_bracket_upper"))
        if upper is None:
            continue
        margin = float(row["fast_temp_unit"]) - upper
        qualifies = margin >= 0.5 - 1e-9
        prior = states.get(key)
        if not qualifies:
            states.pop(key, None)
            continue
        distinct = prior is not None and prior["fast_obs_ts_utc"] != row["fast_obs_ts_utc"]
        inside_execution_window = float(row.get("next_metar_report_clock_distance_min") or math.inf) <= 20.0 + 1e-9
        if key not in confirmed and distinct and margin >= 0.7 - 1e-9 and inside_execution_window:
            confirmed[key] = {
                **row,
                "confirmation_first_obs_ts_utc": prior["fast_obs_ts_utc"],
                "confirmation_first_margin": prior["fast_margin_over_running_max"],
                "confirmation_latest_margin": round(margin, 3),
                "confirmation_rule": "two_distinct_ge_0p5_latest_ge_0p7_market_unit",
                "confirmation_execution_window_min": 20.0,
            }
        states[key] = {**row, "fast_margin_over_running_max": round(margin, 3)}
    return sorted(confirmed.values(), key=lambda row: row["fast_detect_ts_utc"])


def annotate_settlement_labels(
    rows: list[dict[str, Any]], winners: dict[tuple[str, str], str]
) -> None:
    for row in rows:
        winner = winners.get((str(row["city"]), str(row["target_date"])), "")
        row["winning_bracket"] = winner
        row["settlement_crossed_previous_max"] = (
            not parse_bracket_contains(winner, int(row["previous_metar_running_max_round"])) if winner else ""
        )
        row["settlement_left_previous_bracket"] = (
            winner != row.get("previous_market_bracket") if winner and row.get("previous_market_bracket") else ""
        )


def load_wu_daily_max(path: Path, profiles: dict[str, dict[str, Any]]) -> dict[tuple[str, str], int]:
    output: dict[tuple[str, str], int] = {}
    for row in iter_jsonl(path):
        city = canonical_city(row.get("city"))
        temp_c = safe_float(row.get("temp_c"))
        target_date = str(row.get("target_date") or "")
        if city not in profiles or temp_c is None or not target_date:
            continue
        value = round_half_up(temp_in_unit(temp_c, str(profiles[city].get("unit") or "C")))
        key = (city, target_date)
        output[key] = max(output.get(key, value), value)
    return output


def official_daily_max(
    synoptic: dict[str, list[ReferenceEvent]], cities: set[str]
) -> dict[tuple[str, str], int]:
    output: dict[tuple[str, str], int] = {}
    for city in cities:
        for event in synoptic.get(city, []):
            key = (city, event.target_date)
            output[key] = max(output.get(key, event.temp_round), event.temp_round)
    return output


def parse_bracket_contains(bracket: str, value: int) -> bool:
    text = str(bracket or "").replace("°", "").strip()
    if not text:
        return False
    if text.endswith("+"):
        try:
            return value >= int(float(text[:-1]))
        except ValueError:
            return False
    if text.startswith("<"):
        try:
            return value < int(float(text.lstrip("<="))) + (1 if text.startswith("<=") else 0)
        except ValueError:
            return False
    if "-" in text:
        left, right = text.split("-", 1)
        try:
            return int(float(left)) <= value <= int(float(right))
        except ValueError:
            return False
    try:
        return value == int(float(text))
    except ValueError:
        return False


def bracket_bounds(bracket: str) -> tuple[int | None, int | None]:
    text = str(bracket or "").replace("°", "").strip()
    if not text:
        return None, None
    if text.endswith("+"):
        try:
            return int(float(text[:-1])), None
        except ValueError:
            return None, None
    if "-" in text:
        left, right = text.split("-", 1)
        try:
            return int(float(left)), int(float(right))
        except ValueError:
            return None, None
    try:
        value = int(float(text))
    except ValueError:
        return None, None
    return value, value


def annotate_market_brackets(
    rows: list[dict[str, Any]], ladders: dict[tuple[str, str], list[str]]
) -> None:
    for row in rows:
        brackets = ladders.get((str(row["city"]), str(row["target_date"])), [])
        value = int(row["previous_metar_running_max_round"])
        selected = next((bracket for bracket in brackets if parse_bracket_contains(bracket, value)), "")
        if not selected:
            finite = [
                (bracket, bounds)
                for bracket in brackets
                if (bounds := bracket_bounds(bracket))[1] is not None
            ]
            if finite:
                lowest = min(finite, key=lambda item: int(item[1][1]))
                if value <= int(lowest[1][1]):
                    selected = lowest[0]
        lower, upper = bracket_bounds(selected)
        row["previous_market_bracket"] = selected
        row["previous_market_bracket_lower"] = lower if lower is not None else ""
        row["previous_market_bracket_upper"] = upper if upper is not None else ""
        row["fast_implies_market_cross"] = upper is not None and row["fast_temp_round"] > upper
        row["next_metar_market_crossed"] = upper is not None and row["next_metar_temp_round"] > upper


def load_market_ladders(
    db_path: Path,
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], list[str]]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    try:
        rows = connection.execute(
            """
            SELECT city, target_date, bracket, final_price
            FROM settlement_outcomes
            WHERE settlement_status='settled'
            """
        ).fetchall()
    finally:
        connection.close()
    winners: dict[tuple[str, str], str] = {}
    ladders: dict[tuple[str, str], list[str]] = defaultdict(list)
    for city, target_date, bracket, final_price in rows:
        key = (str(city), str(target_date))
        text = str(bracket)
        if text not in ladders[key]:
            ladders[key].append(text)
        if float(final_price) >= 0.999:
            winners[key] = text
    return winners, dict(ladders)


def build_settlement_comparison(
    fast_rows: list[dict[str, Any]], wu_max: dict[tuple[str, str], int],
    official_max: dict[tuple[str, str], int], winners: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    daily_fast: dict[tuple[str, str, str], int] = {}
    for row in fast_rows:
        key = (row["city"], row["target_date"], row["source"])
        daily_fast[key] = max(daily_fast.get(key, row["temp_round"]), row["temp_round"])
    output = []
    for (city, target_date, source), fast_max in sorted(daily_fast.items()):
        proxy_source = "WRH/Synoptic" if city in {"Istanbul", "TelAviv"} else "WU history"
        proxy = official_max.get((city, target_date)) if city in {"Istanbul", "TelAviv"} else wu_max.get((city, target_date))
        winner = winners.get((city, target_date), "")
        if proxy is None and not winner:
            continue
        output.append(
            {
                "city": city,
                "target_date": target_date,
                "fast_source": source,
                "fast_daily_max_round": fast_max,
                "settlement_proxy_source": proxy_source,
                "settlement_proxy_daily_max_round": proxy if proxy is not None else "",
                "fast_minus_proxy_round": fast_max - proxy if proxy is not None else "",
                "proxy_exact_round": fast_max == proxy if proxy is not None else "",
                "winning_bracket": winner,
                "fast_max_in_winning_bracket": parse_bracket_contains(winner, fast_max) if winner else "",
                "proxy_max_in_winning_bracket": parse_bracket_contains(winner, proxy) if winner and proxy is not None else "",
            }
        )
    return output


def build_route_latency(
    awc: dict[str, list[ReferenceEvent]], synoptic: dict[str, list[ReferenceEvent]]
) -> list[dict[str, Any]]:
    output = []
    for city in sorted(set(awc) & set(synoptic)):
        awc_map = {event.report_ts: event for event in awc[city]}
        synoptic_map = {event.report_ts: event for event in synoptic[city]}
        for report_ts in sorted(set(awc_map) & set(synoptic_map)):
            left, right = awc_map[report_ts], synoptic_map[report_ts]
            output.append(
                {
                    "city": city,
                    "report_ts_utc": report_ts.isoformat(),
                    "awc_detect_ts_utc": left.detect_ts.isoformat(),
                    "synoptic_detect_ts_utc": right.detect_ts.isoformat(),
                    "synoptic_minus_awc_detect_sec": round((right.detect_ts - left.detect_ts).total_seconds(), 3),
                    "same_rounded_temp": left.temp_round == right.temp_round,
                }
            )
    return output


def build_new_us_source_speed(
    synoptic: dict[str, list[ReferenceEvent]], fast_rows: list[dict[str, Any]], cities: set[str]
) -> list[dict[str, Any]]:
    output = []
    for city in sorted(cities):
        events = sorted(synoptic.get(city, []), key=lambda event: event.report_ts)
        gaps = [
            (right.report_ts - left.report_ts).total_seconds() / 60.0
            for left, right in zip(events, events[1:])
            if left.target_date == right.target_date
            and 0 < (right.report_ts - left.report_ts).total_seconds() <= 10_800
        ]
        lags = [(event.detect_ts - event.report_ts).total_seconds() / 60.0 for event in events]
        old_rows = sorted(
            (row for row in fast_rows if row["city"] == city and row["source"] == "noaa_madis_hfmetar"),
            key=lambda row: row["obs_ts"],
        )
        old_gaps = [
            (right["obs_ts"] - left["obs_ts"]).total_seconds() / 60.0
            for left, right in zip(old_rows, old_rows[1:])
            if left["target_date"] == right["target_date"]
            and 0 < (right["obs_ts"] - left["obs_ts"]).total_seconds() <= 10_800
        ]
        output.append(
            {
                "city": city,
                "source": "synopticdata_timeseries",
                "sample_start_date": min((event.target_date for event in events), default=""),
                "sample_end_date": max((event.target_date for event in events), default=""),
                "sample_days": len({event.target_date for event in events}),
                "distinct_reports": len(events),
                "median_observation_cadence_min": round(statistics.median(gaps), 3) if gaps else "",
                "median_detection_lag_min": round(statistics.median(lags), 3) if lags else "",
                "old_iem_observations": len(old_rows),
                "old_iem_median_observation_cadence_min": round(statistics.median(old_gaps), 3) if old_gaps else "",
                "old_iem_median_detection_lag_min": round(statistics.median(row["age_min"] for row in old_rows), 3) if old_rows else "",
                "cross_precision": "",
                "precision_blocker": "missing_concurrent_awc_label_after_primary_source_switch",
            }
        )
    return output


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(
    event_rows: list[dict[str, Any]], cross_rows: list[dict[str, Any]], persistent_rows: list[dict[str, Any]],
    settlement_rows: list[dict[str, Any]], route_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in event_rows:
        groups[(row["city"], row["fast_source"])]["events"].append(row)
    for row in cross_rows:
        groups[(row["city"], row["fast_source"])]["cross"].append(row)
    for row in persistent_rows:
        groups[(row["city"], row["fast_source"])]["persistent"].append(row)
    for row in settlement_rows:
        groups[(row["city"], row["fast_source"])]["settlement"].append(row)
    output = []
    for (city, source), parts in sorted(groups.items()):
        events, crosses, persistent, settlements = parts["events"], parts["cross"], parts["persistent"], parts["settlement"]
        unique_fast = {row["fast_obs_ts_utc"]: row for row in events}
        ordered_fast = sorted(unique_fast.values(), key=lambda row: row["fast_obs_ts_utc"])
        fast_gaps = [
            (parse_dt(right["fast_obs_ts_utc"]) - parse_dt(left["fast_obs_ts_utc"])).total_seconds() / 60.0
            for left, right in zip(ordered_fast, ordered_fast[1:])
            if left["target_date"] == right["target_date"]
            and parse_dt(left["fast_obs_ts_utc"]) is not None
            and parse_dt(right["fast_obs_ts_utc"]) is not None
            and 0 < (parse_dt(right["fast_obs_ts_utc"]) - parse_dt(left["fast_obs_ts_utc"])).total_seconds() <= 10_800
        ]
        metar_points: dict[tuple[str, str], datetime] = {}
        for event in events:
            for field in ("previous_metar_report_ts_utc", "next_metar_report_ts_utc"):
                ts = parse_dt(event[field])
                if ts is not None:
                    metar_points[(event["target_date"], event[field])] = ts
        ordered_metar = sorted(((day, ts) for (day, _), ts in metar_points.items()), key=lambda item: item[1])
        metar_gaps = [
            (right[1] - left[1]).total_seconds() / 60.0
            for left, right in zip(ordered_metar, ordered_metar[1:])
            if left[0] == right[0] and 0 < (right[1] - left[1]).total_seconds() <= 10_800
        ]
        hits = sum(1 for row in crosses if row["next_metar_market_crossed"])
        positives = sum(1 for row in events if row["next_metar_market_crossed"])
        proxy_valid = [row for row in settlements if row["proxy_exact_round"] != ""]
        winner_valid = [row for row in settlements if row["fast_max_in_winning_bracket"] != ""]
        settled_crosses = [row for row in crosses if row.get("settlement_left_previous_bracket") != ""]
        settled_cross_hits = sum(1 for row in settled_crosses if row["settlement_left_previous_bracket"] is True)
        settled_cross_precision = ratio(settled_cross_hits, len(settled_crosses))
        settled_cross_days = len({row["target_date"] for row in settled_crosses})
        persistent_hits = sum(1 for row in persistent if row["next_metar_market_crossed"])
        persistent_precision = ratio(persistent_hits, len(persistent))
        persistent_days = len({row["target_date"] for row in persistent})
        settled_persistent = [row for row in persistent if row.get("settlement_left_previous_bracket") != ""]
        settled_persistent_hits = sum(1 for row in settled_persistent if row["settlement_left_previous_bracket"] is True)
        settled_persistent_precision = ratio(settled_persistent_hits, len(settled_persistent))
        settled_persistent_days = len({row["target_date"] for row in settled_persistent})
        cross_precision = ratio(hits, len(crosses))
        proxy_exact_rate = ratio(sum(1 for row in proxy_valid if row["proxy_exact_round"] is True), len(proxy_valid))
        next_status = "shadow_candidate" if len(crosses) >= 5 and cross_precision is not None and cross_precision >= 0.7 else "accumulate_only"
        settlement_status = (
            "aligned"
            if len(settled_crosses) >= 3 and settled_cross_days >= 3
            and settled_cross_precision is not None and settled_cross_precision >= 0.8
            else "not_yet_aligned"
        )
        if next_status == "shadow_candidate" and settlement_status == "aligned":
            combined_status = "strategy_shadow_candidate"
        elif next_status == "shadow_candidate":
            combined_status = "next_report_signal_only"
        else:
            combined_status = "not_ready"
        persistent_shadow_status = (
            "strict_shadow_candidate"
            if len(persistent) >= 3 and persistent_days >= 3
            and persistent_precision is not None and persistent_precision >= 0.7
            and len(settled_persistent) >= 3 and settled_persistent_days >= 3
            and settled_persistent_precision is not None and settled_persistent_precision >= 0.8
            else "accumulate_only"
        )
        market_shape = "range_2f" if source == "noaa_madis_hfmetar" else "exact_1c"
        tiny_live_status = (
            "blocked_range_handler_and_low_sample"
            if market_shape == "range_2f" and persistent_shadow_status == "strict_shadow_candidate"
            else "blocked_low_sample"
        )
        output.append(
            {
                "city": city,
                "fast_source": source,
                "event_rows": len(events),
                "sample_start_date": min((row["target_date"] for row in events), default=""),
                "sample_end_date": max((row["target_date"] for row in events), default=""),
                "event_sample_days": len({row["target_date"] for row in events}),
                "distinct_fast_observations": len(unique_fast),
                "median_fast_observation_cadence_min": round(statistics.median(fast_gaps), 3) if fast_gaps else "",
                "median_fast_detection_lag_min": round(statistics.median(float(row["fast_age_min"]) for row in ordered_fast), 3) if ordered_fast else "",
                "median_metar_report_cadence_min": round(statistics.median(metar_gaps), 3) if metar_gaps else "",
                "first_cross_signals": len(crosses),
                "first_cross_days": len({row["target_date"] for row in crosses}),
                "first_cross_hits": hits,
                "first_cross_precision": cross_precision,
                "next_metar_cross_base_rate": ratio(positives, len(events)),
                "median_lead_to_next_metar_min": round(statistics.median(row["lead_to_next_metar_min"] for row in persistent), 3) if persistent else "",
                "median_next_metar_clock_distance_min": round(
                    statistics.median(row["next_metar_report_clock_distance_min"] for row in persistent), 3
                ) if persistent else "",
                "settled_cross_signals": len(settled_crosses),
                "settled_cross_days": settled_cross_days,
                "settled_cross_hits": settled_cross_hits,
                "settled_cross_precision": settled_cross_precision,
                "persistent_cross_signals": len(persistent),
                "persistent_cross_days": persistent_days,
                "persistent_cross_hits": persistent_hits,
                "persistent_cross_precision": persistent_precision,
                "settled_persistent_signals": len(settled_persistent),
                "settled_persistent_days": settled_persistent_days,
                "settled_persistent_hits": settled_persistent_hits,
                "settled_persistent_precision": settled_persistent_precision,
                "settlement_proxy_days": len(proxy_valid),
                "proxy_exact_days": sum(1 for row in proxy_valid if row["proxy_exact_round"] is True),
                "proxy_exact_rate": proxy_exact_rate,
                "settled_market_days": len(winner_valid),
                "fast_max_in_winner_days": sum(1 for row in winner_valid if row["fast_max_in_winning_bracket"] is True),
                "fast_max_in_winner_rate": ratio(sum(1 for row in winner_valid if row["fast_max_in_winning_bracket"] is True), len(winner_valid)),
                "next_report_status": next_status,
                "settlement_basis_status": settlement_status,
                "research_status": combined_status,
                "market_shape": market_shape,
                "persistent_shadow_status": persistent_shadow_status,
                "tiny_live_status": tiny_live_status,
            }
        )

    route_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in route_rows:
        route_groups[row["city"]].append(row)
    for row in output:
        paired = route_groups.get(row["city"], [])
        values = [float(item["synoptic_minus_awc_detect_sec"]) for item in paired]
        row["paired_awc_synoptic_reports"] = len(values)
        row["median_synoptic_minus_awc_sec"] = round(statistics.median(values), 3) if values else ""
        row["synoptic_first_rate"] = ratio(sum(1 for value in values if value < 0), len(values))
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path, generated_at: str, summary: list[dict[str, Any]],
    new_us_speed: list[dict[str, Any]], counts: dict[str, Any],
) -> None:
    combined = [row["city"] for row in summary if row["persistent_shadow_status"] == "strict_shadow_candidate"]
    not_ready = [row["city"] for row in summary if row["persistent_shadow_status"] != "strict_shadow_candidate"]
    lines = [
        "# High-Frequency Source Strategy Eligibility v2", "", "Status: `research_snapshot`",
        f"Generated: `{generated_at}`", "", "## Verdict", "",
        "本报告只批准 source/city 进入 shadow feature layer，不直接批准 live。行动状态按双观测确认事件判断，不按重复轮询行或单次跳档判断。",
        "",
        f"- strict shadow candidates: `{', '.join(combined)}`",
        f"- not ready: `{', '.join(not_ready)}`",
        "- US tiny live remains blocked: the market is normally a 2°F range ladder and the current generic exact-bracket executor is not a valid trade-expression handler.",
        "", "## Signal Definition", "",
        "- causal window: fast observation must be first seen no more than 30 minutes after its observation timestamp; the next distinct AWC METAR must first appear within 90 minutes.",
        "- single cross: the rounded fast value leaves the market bracket containing the prior known METAR running max.",
        "- persistent cross: two distinct observation timestamps stay at least 0.5 market units above that bracket's upper edge, the latest is at least 0.7 above it, and the latest detection is within 20 minutes of the next routine METAR report clock.",
        "- US example: when the prior max is inside `90-91`, thresholds are `>=91.5F` then `>=91.7F`; a move from 90F to 91F is not a market-bracket cross.",
        "- next-METAR precision: the next distinct AWC report also leaves the prior bracket. Settled precision: the final winning bracket differs from the prior bracket.",
        "", "## New US Source Speed", "",
        "The rows below are the standard Synoptic 5-minute station feed currently used for Austin/Dallas/Houston. They are faster than the IEM MADISHF rows in the city table, but they are not the true `ICAO1M` one-minute OMO product.",
        "",
        "| City | Sample | New Synoptic cadence / lag | Old IEM cadence / lag | Cross precision |",
        "|---|---:|---:|---:|---|",
    ]
    for row in new_us_speed:
        lines.append(
            f"| `{row['city']}` | {row['sample_start_date']}..{row['sample_end_date']} ({row['sample_days']}d) | "
            f"{row['median_observation_cadence_min']}m / {row['median_detection_lag_min']}m | "
            f"{row['old_iem_median_observation_cadence_min']}m / {row['old_iem_median_detection_lag_min']}m | "
            f"`NA: {row['precision_blocker']}` |"
        )
    lines.extend([
        "", "## Data Integrity", "",
        f"- deduplicated fast observations: `{counts['fast_rows']}`",
        f"- causal event comparisons: `{counts['event_rows']}`",
        f"- first-cross events: `{counts['cross_rows']}`",
        f"- persistent two-observation cross events: `{counts['persistent_rows']}`",
        f"- settlement/proxy city-days: `{counts['settlement_rows']}`",
        f"- paired AWC/Synoptic reports: `{counts['route_rows']}`",
        "- next METAR is deduplicated by city/report timestamp; the same report arriving on another route is not a new label.",
        "- settlement proxy is WU history for default-WU cities and WRH/Synoptic for Istanbul/TelAviv.",
        "- live eligibility is judged against the actual winning bracket when settled; WU-history daily max is only a bias diagnostic because monitoring began on July 10.",
        "- Austin/Dallas/Houston switched source-events primary to Synoptic; without concurrent AWC rows, neither the new Synoptic path nor IEM MADISHF can be independently labelled as lead-to-next-METAR there.",
        "", "## City And Source", "",
        "| City/source | Market | Sample | Fast obs cadence / detect lag | METAR cadence | Single next-METAR | Persistent next-METAR | Persistent settled | Median lead | Action status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in summary:
        lines.append(
            f"| `{row['city']}/{row['fast_source']}` | `{row['market_shape']}` | {row['sample_start_date']}..{row['sample_end_date']} "
            f"({row['event_sample_days']}d) | {row['median_fast_observation_cadence_min']}m / "
            f"{row['median_fast_detection_lag_min']}m | {row['median_metar_report_cadence_min']}m | "
            f"{row['first_cross_hits']}/{row['first_cross_signals']} ({row['first_cross_precision']}) | "
            f"{row['persistent_cross_hits']}/{row['persistent_cross_signals']} ({row['persistent_cross_precision']}) | "
            f"{row['settled_persistent_hits']}/{row['settled_persistent_signals']} ({row['settled_persistent_precision']}) | "
            f"{row['median_lead_to_next_metar_min']}m | `{row['persistent_shadow_status']}` / `{row['tiny_live_status']}` |"
        )
    lines.extend(["", "## First-Arrival Route", ""])
    for row in summary:
        if not row["paired_awc_synoptic_reports"]:
            continue
        lines.append(
            f"- `{row['city']}`: paired reports `{row['paired_awc_synoptic_reports']}`, median Synoptic-AWC "
            f"`{row['median_synoptic_minus_awc_sec']}` sec, Synoptic first rate `{row['synoptic_first_rate']}`."
        )
    lines.extend(
        [
            "", "## Not Yet Evaluable", "",
            "True Synoptic `ICAO1M` OMO, WIS2, Météo-France, KNMI, DWD, AEMET, MetService and ECCC have no production event history yet. They remain probe candidates and are not assigned strategy eligibility from documentation alone.",
        ]
    )
    lines.extend(
        [
            "", "## Contract", "",
            "significance=NA; baseline=NA; forward=FAIL; conclusion=shadow_candidate",
            "",
            "The sample is below the 10-day/30-fill live threshold. No live city-pool, size, or execution-policy change is authorized.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--profiles", default=str(ROOT / "weather_data_feed/source_profiles.json"))
    parser.add_argument("--out-dir", default=str(ROOT / "docs/analysis/2026-07/generated/high_frequency_strategy_eligibility_v2"))
    parser.add_argument("--report", default=str(ROOT / "docs/analysis/2026-07/2026-07-15-high-frequency-strategy-eligibility-v2.md"))
    parser.add_argument("--max-fast-age-min", type=float, default=30.0)
    parser.add_argument("--max-next-metar-min", type=float, default=90.0)
    args = parser.parse_args()

    runtime = Path(args.runtime_root).expanduser()
    profiles = load_profiles(Path(args.profiles))
    awc, synoptic = load_reference_events(runtime / "output/source_events/sources.jsonl", profiles)
    fast = load_fast_observations(
        runtime / "output/high_frequency_observations/high_frequency_observations.jsonl",
        profiles,
        args.max_fast_age_min,
    )
    events = build_event_comparison(fast, awc, args.max_next_metar_min)
    wu_max = load_wu_daily_max(runtime / "output/wu_history_latency/wu_history_latency.jsonl", profiles)
    official_max = official_daily_max(synoptic, {"Istanbul", "TelAviv"})
    winners, ladders = load_market_ladders(Path(args.db_path))
    annotate_market_brackets(events, ladders)
    annotate_settlement_labels(events, winners)
    crosses = first_cross_rows(events)
    persistent = persistent_cross_rows(events)
    settlements = build_settlement_comparison(fast, wu_max, official_max, winners)
    routes = build_route_latency(awc, synoptic)
    new_us_speed = build_new_us_source_speed(synoptic, fast, {"Austin", "Dallas", "Houston"})
    city_summary = summarize(events, crosses, persistent, settlements, routes)

    out_dir = Path(args.out_dir)
    write_csv(out_dir / "event_comparison.csv", events)
    write_csv(out_dir / "first_cross_events.csv", crosses)
    write_csv(out_dir / "persistent_cross_events.csv", persistent)
    write_csv(out_dir / "settlement_comparison.csv", settlements)
    write_csv(out_dir / "route_latency.csv", routes)
    write_csv(out_dir / "new_us_source_speed.csv", new_us_speed)
    write_csv(out_dir / "summary_by_city_source.csv", city_summary)
    generated_at = datetime.now(timezone.utc).isoformat()
    counts = {
        "fast_rows": len(fast), "event_rows": len(events), "cross_rows": len(crosses),
        "persistent_rows": len(persistent),
        "settlement_rows": len(settlements), "route_rows": len(routes),
    }
    write_report(Path(args.report), generated_at, city_summary, new_us_speed, counts)
    print(json.dumps({**counts, "summary_rows": len(city_summary)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
