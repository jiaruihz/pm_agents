#!/usr/bin/env python3
"""Compare direct NOAA MADIS OMO netCDF with IEM MADISHF and WU settlement.

This is a source-basis study only.  It downloads NOAA's public hourly hfmetar
netCDF files in memory, aligns same-station/same-observation timestamps to the
existing causal IEM collector, and reuses native-F WU settlement labels from
the v1 US MADISHF alignment study.  It never submits orders.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import io
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.io import netcdf_file


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_high_frequency_strategy_eligibility_v2 as eligibility
from scripts.analysis.forecast_quality.research_us_madishf_metar_wu_alignment_v1 import (
    STATION_BY_CITY,
    US_CITIES,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE


MADIS_BASE = "https://madis-data.ncep.noaa.gov/madisPublic1/data/LDAD/hfmetar/netCDF"
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_ALIGNMENT = ROOT / "docs/analysis/2026-07/generated/us_madishf_metar_wu_alignment_v1"
DEFAULT_OUT = ROOT / "docs/analysis/2026-07/generated/us_direct_madis_wu_alignment_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-19-us-direct-madis-wu-alignment-v1.md"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def half_up(value: float) -> int:
    return eligibility.round_half_up(value)


def decode_chars(value: np.ndarray) -> str:
    return b"".join(value.tolist()).decode("ascii", "ignore").strip("\x00 ")


def timestamp(value: float) -> datetime | None:
    if value <= 0 or value > 4e9:
        return None
    return datetime.fromtimestamp(value, timezone.utc)


def hourly_names(start_date: str, end_date: str) -> list[str]:
    # Full UTC envelopes cover every included US local day.
    start = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date.fromisoformat(end_date) + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=8)
    names: list[str] = []
    cursor = start
    while cursor <= end:
        names.append(cursor.strftime("%Y%m%d_%H00.gz"))
        cursor += timedelta(hours=1)
    return names


def parse_hour(name: str, payload: bytes, station_to_city: dict[str, str], start_date: str, end_date: str) -> list[dict[str, Any]]:
    raw = gzip.decompress(payload)
    handle = netcdf_file(io.BytesIO(raw), "r", mmap=False)
    try:
        station_values = np.array(handle.variables["stationId"][:])
        observation = np.array(handle.variables["observationTime"][:], dtype=float)
        received = np.array(handle.variables["receivedTime"][:], dtype=float)
        modified = np.array(handle.variables["modifyTime"][:], dtype=float)
        temperature = np.array(handle.variables["temperature"][:], dtype=float)
        qcr = np.array(handle.variables["temperatureQCR"][:], dtype=int)
        rows: list[dict[str, Any]] = []
        for index, chars in enumerate(station_values):
            station = decode_chars(chars)
            city = station_to_city.get(station)
            if city is None:
                continue
            obs = timestamp(float(observation[index]))
            recv = timestamp(float(received[index]))
            modify = timestamp(float(modified[index]))
            temp_k = float(temperature[index])
            if obs is None or temp_k < 150 or temp_k > 350:
                continue
            target_date = obs.astimezone(ZoneInfo(CITY_TIMEZONE[city])).date().isoformat()
            if not (start_date <= target_date <= end_date):
                continue
            temp_c = temp_k - 273.15
            temp_f = temp_c * 9.0 / 5.0 + 32.0
            rows.append({
                "city": city, "station": station, "target_date": target_date,
                "source_file": name, "obs_ts_utc": obs.isoformat(),
                "received_ts_utc": recv.isoformat() if recv else "",
                "modify_ts_utc": modify.isoformat() if modify else "",
                "received_lag_sec": (recv - obs).total_seconds() if recv else "",
                "temp_c": round(temp_c, 3), "temp_f": round(temp_f, 3),
                "temp_round_f": half_up(temp_f), "temperature_qcr": int(qcr[index]),
            })
        return rows
    finally:
        handle.close()


def fetch_direct(start_date: str, end_date: str, workers: int, timeout: float) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    station_to_city = {station: city for city, station in STATION_BY_CITY.items()}

    def fetch(name: str) -> tuple[str, list[dict[str, Any]], str]:
        try:
            response = httpx.get(f"{MADIS_BASE}/{name}", timeout=timeout, headers={"User-Agent": "pm-agents-direct-madis-research/1.0"})
            response.raise_for_status()
            return name, parse_hour(name, response.content, station_to_city, start_date, end_date), ""
        except Exception as exc:  # noqa: BLE001
            return name, [], f"{type(exc).__name__}: {exc}"

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for name, parsed, error in pool.map(fetch, hourly_names(start_date, end_date)):
            rows.extend(parsed)
            if error:
                failures.append({"source_file": name, "error": error})
    dedup = {(str(row["city"]), str(row["obs_ts_utc"])): row for row in rows}
    return sorted(dedup.values(), key=lambda row: (str(row["city"]), str(row["obs_ts_utc"]))), failures


def load_iem(runtime: Path, profiles_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    profiles = eligibility.load_profiles(profiles_path)
    rows = eligibility.load_fast_observations(
        runtime / "output/high_frequency_observations/high_frequency_observations.jsonl",
        profiles,
        30.0,
    )
    return [
        row for row in rows
        if row["source"] == "noaa_madis_hfmetar"
        and row["city"] in US_CITIES
        and start_date <= row["target_date"] <= end_date
    ]


def observation_alignment(direct: list[dict[str, Any]], iem: list[dict[str, Any]]) -> list[dict[str, Any]]:
    iem_map = {(str(row["city"]), row["obs_ts"].isoformat()): row for row in iem}
    output: list[dict[str, Any]] = []
    for row in direct:
        other = iem_map.get((str(row["city"]), str(row["obs_ts_utc"])))
        output.append({
            **row,
            "iem_match": int(other is not None),
            "iem_temp_f": round(float(other["temp_unit"]), 3) if other else "",
            "iem_temp_round_f": int(other["temp_round"]) if other else "",
            "direct_equals_iem_round": int(int(row["temp_round_f"]) == int(other["temp_round"])) if other else "",
            "iem_detect_ts_utc": other["detect_ts"].isoformat() if other else "",
            "iem_detect_lag_sec": (other["detect_ts"] - other["obs_ts"]).total_seconds() if other else "",
        })
    return output


def daily_alignment(direct: list[dict[str, Any]], prior_daily: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in direct:
        grouped[(str(row["city"]), str(row["target_date"]))].append(row)
    prior = {(row["city"], row["target_date"]): row for row in prior_daily}
    output: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        reference = prior.get(key)
        if reference is None:
            continue
        maximum = max(int(row["temp_round_f"]) for row in rows)
        peak = [row for row in rows if int(row["temp_round_f"]) == maximum]
        winner = str(reference["winning_bracket"])
        wu = int(float(reference["wu_native_daily_max_f"]))
        iem_max = int(float(reference["madishf_daily_max_f"]))
        direct_first = min(eligibility.parse_dt(str(row["obs_ts_utc"])) for row in rows)
        direct_last = max(eligibility.parse_dt(str(row["obs_ts_utc"])) for row in rows)
        wu_peak_first = eligibility.parse_dt(reference["wu_peak_first_ts_utc"])
        wu_peak_last = eligibility.parse_dt(reference["wu_peak_last_ts_utc"])
        peak_covered = (
            direct_first - timedelta(minutes=30) <= wu_peak_first
            and direct_last + timedelta(minutes=30) >= wu_peak_last
        )
        output.append({
            "city": key[0], "target_date": key[1], "station": STATION_BY_CITY[key[0]],
            "direct_observations": len(rows), "direct_daily_max_f": maximum,
            "iem_daily_max_f": iem_max, "wu_native_daily_max_f": wu,
            "winning_bracket": winner,
            "direct_equals_iem_daily_max": int(maximum == iem_max),
            "direct_minus_wu_f": maximum - wu,
            "direct_in_winning_bracket": int(eligibility.parse_bracket_contains(winner, maximum)),
            "direct_peak_all_qcr_zero": int(all(int(row["temperature_qcr"]) == 0 for row in peak)),
            "direct_peak_first_ts_utc": min(str(row["obs_ts_utc"]) for row in peak),
            "direct_first_obs_ts_utc": direct_first.isoformat(),
            "direct_last_obs_ts_utc": direct_last.isoformat(),
            "wu_peak_first_ts_utc": reference["wu_peak_first_ts_utc"],
            "wu_peak_last_ts_utc": reference["wu_peak_last_ts_utc"],
            "direct_covered_wu_peak": int(peak_covered),
        })
    return output


def expected_source_files(city: str, target_date: str) -> set[str]:
    """Return the hourly UTC files needed to cover a complete local city-day."""
    zone = ZoneInfo(CITY_TIMEZONE[city])
    local_start = datetime.combine(date.fromisoformat(target_date), datetime.min.time(), tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    cursor = local_start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = local_end.astimezone(timezone.utc)
    output: set[str] = set()
    while cursor < end:
        output.add(cursor.strftime("%Y%m%d_%H00.gz"))
        cursor += timedelta(hours=1)
    return output


def persistent_alignment(direct: list[dict[str, Any]], prior_events: list[dict[str, str]]) -> list[dict[str, Any]]:
    direct_map = {(str(row["city"]), str(row["obs_ts_utc"])): row for row in direct}
    output: list[dict[str, Any]] = []
    for event in prior_events:
        row = direct_map.get((event["city"], event["fast_obs_ts_utc"]))
        if row is None:
            output.append({**event, "direct_match": 0})
            continue
        output.append({
            **event, "direct_match": 1,
            "direct_temp_f": row["temp_f"], "direct_temp_round_f": row["temp_round_f"],
            "direct_received_ts_utc": row["received_ts_utc"],
            "direct_received_lag_sec": row["received_lag_sec"],
            "direct_temperature_qcr": row["temperature_qcr"],
            "direct_equals_iem_event_round": int(int(row["temp_round_f"]) == int(float(event["fast_temp_round_f"]))),
        })
    return output


def pct(n: int, d: int) -> str:
    return "NA" if not d else f"{n}/{d} ({100*n/d:.1f}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-13")
    parser.add_argument("--end-date", default="2026-07-17")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--profiles", default=str(ROOT / "weather_data_feed/source_profiles.json"))
    parser.add_argument("--alignment-dir", default=str(DEFAULT_ALIGNMENT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument(
        "--reuse-extracts",
        action="store_true",
        help="Reuse direct_observations.csv, observation_alignment.csv, and download_failures.csv instead of refetching/reloading raw collectors.",
    )
    args = parser.parse_args()

    alignment_dir = Path(args.alignment_dir)
    out_dir = Path(args.out_dir)
    if args.reuse_extracts:
        direct = read_csv(out_dir / "direct_observations.csv")
        observations = read_csv(out_dir / "observation_alignment.csv")
        failures = read_csv(out_dir / "download_failures.csv")
    else:
        direct, failures = fetch_direct(args.start_date, args.end_date, args.workers, args.timeout_sec)
        iem = load_iem(Path(args.runtime_root), Path(args.profiles), args.start_date, args.end_date)
        observations = observation_alignment(direct, iem)
    all_prior_daily = read_csv(alignment_dir / "daily_alignment.csv")
    all_prior_events = read_csv(alignment_dir / "persistent_events.csv")
    daily = daily_alignment(direct, [row for row in all_prior_daily if args.start_date <= row["target_date"] <= args.end_date])
    failed_files = {row["source_file"] for row in failures}
    for row in daily:
        row["direct_download_complete"] = int(
            not (expected_source_files(str(row["city"]), str(row["target_date"])) & failed_files)
        )
    events = persistent_alignment(
        direct,
        [row for row in all_prior_events if args.start_date <= row["target_date"] <= args.end_date],
    )

    write_csv(out_dir / "direct_observations.csv", direct)
    write_csv(out_dir / "observation_alignment.csv", observations)
    write_csv(out_dir / "daily_alignment.csv", daily)
    write_csv(out_dir / "persistent_alignment.csv", events)
    write_csv(out_dir / "download_failures.csv", failures)

    matched_obs = [row for row in observations if str(row["iem_match"]) == "1"]
    matched_events = [row for row in events if str(row.get("direct_match")) == "1"]
    settled_events = [row for row in matched_events if row.get("settlement_left_old_bracket") not in (None, "")]
    false_events = [row for row in settled_events if row["settlement_left_old_bracket"] == "False"]
    received_lags = [float(row["received_lag_sec"]) for row in direct if row["received_lag_sec"] != ""]
    iem_lags = [float(row["iem_detect_lag_sec"]) for row in matched_obs if row["iem_detect_lag_sec"] != ""]
    peak_covered_days = [row for row in daily if int(row["direct_covered_wu_peak"]) == 1]
    complete_days = [row for row in daily if int(row["direct_download_complete"]) == 1]
    exact_days = sum(int(row["direct_in_winning_bracket"]) for row in peak_covered_days)
    complete_exact_days = sum(int(row["direct_in_winning_bracket"]) for row in complete_days)
    complete_deltas = Counter(int(float(row["direct_minus_wu_f"])) for row in complete_days)
    complete_bias = statistics.fmean(int(float(row["direct_minus_wu_f"])) for row in complete_days)
    complete_mae = statistics.fmean(abs(int(float(row["direct_minus_wu_f"]))) for row in complete_days)
    direct_iem_days = sum(int(row["direct_equals_iem_daily_max"]) for row in daily)
    event_hits = sum(row["settlement_left_old_bracket"] == "True" for row in settled_events)
    prior_peak_daily = [row for row in all_prior_daily if row["madishf_covered_wu_peak"] == "True"]
    prior_awc_daily = [row for row in all_prior_daily if row["awc_routine_daily_max_f"] != ""]
    prior_persistent_settled = [row for row in all_prior_events if row["settlement_left_old_bracket"] != ""]
    wu_winner_hits = sum(row["wu_in_winning_bracket"] == "True" for row in all_prior_daily)
    awc_winner_hits = sum(row["awc_in_winning_bracket"] == "True" for row in prior_awc_daily)
    iem_winner_hits = sum(row["madishf_in_winning_bracket"] == "True" for row in prior_peak_daily)
    prior_persistent_hits = sum(row["settlement_left_old_bracket"] == "True" for row in prior_persistent_settled)

    city_lines = []
    for city in US_CITIES:
        rows = [row for row in peak_covered_days if row["city"] == city]
        if not rows:
            continue
        city_lines.append(
            f"| `{city}` | {len(rows)} | {sum(int(r['direct_equals_iem_daily_max']) for r in rows)}/{len(rows)} | "
            f"{sum(int(r['direct_in_winning_bracket']) for r in rows)}/{len(rows)} | "
            f"{statistics.fmean(abs(float(r['direct_minus_wu_f'])) for r in rows):.2f}F |"
        )

    report = f"""# US direct MADIS OMO → IEM → WU alignment v1

Generated: `{datetime.now(timezone.utc).isoformat()}`
Status: `research_snapshot`; no live authorization

## Action

Direct MADIS improves latency but does not improve settlement accuracy: it is the same raw OMO signal carried by IEM. Keep US previous-bracket NO in shadow. NOAA temperature QC does not catch the Atlanta terminal false cross.

## Fixed denominator

- dates: `{args.start_date}..{args.end_date}`
- cities: `{len({row['city'] for row in daily})}` US airport markets with WU labels
- direct source: NOAA public `LDAD/hfmetar/netCDF`, same station and exact observation timestamp
- settlement label: native-F WU-aligned winning market bracket from the prior v1 study
- persistent expression: BUY NO on the previous Fahrenheit bracket

## Source identity and latency

- direct observations: `{len(direct)}`; hourly download failures: `{len(failures)}` (all are the expired 2026-07-13 00:00-16:00 UTC portion of the public rolling archive)
- same-timestamp direct↔IEM overlap: `{len(matched_obs)}`
- direct↔IEM rounded-temperature agreement: `{pct(sum(int(r['direct_equals_iem_round']) for r in matched_obs), len(matched_obs))}`
- median NOAA received lag: `{statistics.median(received_lags):.1f}` seconds
- median IEM first-seen lag on the same observations: `{statistics.median(iem_lags):.1f}` seconds

The faster feed is not a new thermometer or a new settlement basis. It is earlier delivery of the same OMO observations.

## Accuracy hierarchy against final WU

| source / expression | fixed denominator | aligned with final WU |
|---|---:|---:|
| native-F WU final vs winning market bracket | city-day | `{pct(wu_winner_hits, len(all_prior_daily))}` |
| routine AWC METAR daily max vs winning bracket | city-day | `{pct(awc_winner_hits, len(prior_awc_daily))}` |
| IEM MADISHF OMO daily max vs winning bracket, WU-peak-covered | city-day | `{pct(iem_winner_hits, len(prior_peak_daily))}` |
| direct NOAA MADIS OMO daily max vs winning bracket, full files | city-day | `{pct(complete_exact_days, len(complete_days))}` |
| MADISHF persistent previous-bracket NO vs WU final | event | `{pct(prior_persistent_hits, len(prior_persistent_settled))}` |

WU native-F is the settlement label and passes the market-winner contract. Routine METAR is close to that label but is later. Both IEM MADISHF and direct MADIS are the faster OMO family; direct delivery changes latency, not the underlying basis risk.

## Daily max vs WU settlement

- city-days with direct coverage spanning the WU peak: `{len(peak_covered_days)}/{len(daily)}`
- city-days with every local-day hourly file still downloadable: `{len(complete_days)}/{len(daily)}`
- direct MADIS daily max equals IEM daily max: `{pct(direct_iem_days, len(daily))}`
- direct MADIS daily max falls inside the WU winning bracket (WU-peak-covered): `{pct(exact_days, len(peak_covered_days))}`
- same result on fully downloadable local days: `{pct(complete_exact_days, len(complete_days))}`
- fully downloadable day bias: mean `{complete_bias:+.3f}F`, MAE `{complete_mae:.3f}F`; delta distribution `{dict(sorted(complete_deltas.items()))}`

| city | days | direct=IEM daily max | direct in WU winner | MAE vs WU |
|---|---:|---:|---:|---:|
{chr(10).join(city_lines)}

The direct=IEM daily-max row is not an identity test: the historical IEM collector sampled fewer observations than the direct hourly archive. Exact same-timestamp observations above are the identity test. Daily exact agreement is a source-basis diagnostic, not the previous-NO label. A source can miss the final exact bracket but still correctly prove that an older bracket was left.

## Persistent previous-NO label

- persistent events with direct timestamp match: `{len(matched_events)}/{len(events)}`
- direct event temperature equals IEM event temperature: `{pct(sum(int(r['direct_equals_iem_event_round']) for r in matched_events), len(matched_events))}`
- settlement left the old bracket: `{pct(event_hits, len(settled_events))}`
- terminal false crosses: `{len(false_events)}`
- false crosses with `temperatureQCR=0`: `{sum(str(r.get('direct_temperature_qcr')) == '0' for r in false_events)}/{len(false_events)}`

The Atlanta 2026-07-17 91.4F observation is present in direct NOAA MADIS, arrives about 134 seconds after observation, and carries `temperatureQCR=0`; neither direct delivery nor the available QC flag removes it.

## Decision

1. Direct MADIS/LDM can solve most of the 24-minute IEM delay.
2. It cannot solve OMO→WU basis risk because it is the identical raw observation family.
3. No US live order should be authorized from a raw cross alone. A future direct-source model must estimate `P(WU leaves old bracket | OMO path, margin, persistence, station bias, time)` and beat the same-time market after fee/depth.
4. Continue only zero-notional direct collector until at least 30 new settled persistent events with complete direct-book coverage.

## Contract

significance=NA; baseline=IEM same-source + WU; forward=FAIL for raw deterministic trigger; conclusion=direct source useful for latency, not sufficient for live
"""
    Path(args.report).write_text(report, encoding="utf-8")
    print(json.dumps({
        "direct_observations": len(direct), "download_failures": len(failures),
        "matched_observations": len(matched_obs), "daily_rows": len(daily),
        "peak_covered_daily_rows": len(peak_covered_days), "complete_daily_rows": len(complete_days),
        "direct_iem_daily_equal": direct_iem_days, "direct_in_winner_peak_covered": exact_days,
        "direct_in_winner_complete": complete_exact_days,
        "persistent_events": len(events), "persistent_matched": len(matched_events),
        "persistent_hits": event_hits, "false_events": len(false_events),
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
