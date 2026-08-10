#!/usr/bin/env python3
"""Calibrate KNMI station 240 ta/tx to later EHAM METAR and WU daily max."""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import csv
import json
import math
import os
import re
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

from scripts.analysis.forecast_quality import (  # noqa: E402
    research_high_frequency_strategy_eligibility_v2 as eligibility,
)
from weather_data_feed.high_frequency_observation_sources import (  # noqa: E402
    KNMI_API_BASE,
    KNMI_DATASET,
    KNMI_VERSION,
)
from weather_data_feed.knmi_open_data import parse_knmi_netcdf  # noqa: E402
from weather_data_feed.observation_sources.fetchers import (  # noqa: E402
    WEATHER_COM_API_KEY,
    WEATHER_COM_HISTORICAL_OBS,
)


RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
OUT = ROOT / "docs/analysis/2026-07/generated/knmi_eham_wu_alignment_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-28-knmi-eham-wu-alignment-v1.md"
DB = ROOT / "runtime/weather.db"
FILENAME_RE = re.compile(r"_(\d{12})\.nc$")
LOCAL_ZONE = ZoneInfo("Europe/Amsterdam")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def half_up(value: float) -> int:
    return math.floor(float(value) + 0.5)


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def date_range(start_date: str, end_date: str) -> Iterable[date]:
    cursor = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def list_knmi_files(
    client: httpx.Client,
    token: str,
    *,
    begin: str = "",
) -> list[dict[str, Any]]:
    url = f"{KNMI_API_BASE}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    params: dict[str, Any] = {"maxKeys": 1000, "sorting": "asc"}
    if begin:
        params.update({"orderBy": "filename", "begin": begin})
    response = client.get(
        url,
        params=params,
        headers={"Authorization": token},
    )
    response.raise_for_status()
    return list(response.json().get("files") or [])


def file_local_date(filename: str) -> str:
    match = FILENAME_RE.search(filename)
    if not match:
        return ""
    timestamp = datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return timestamp.astimezone(LOCAL_ZONE).date().isoformat()


def file_local_hour(filename: str) -> int | None:
    match = FILENAME_RE.search(filename)
    if not match:
        return None
    timestamp = datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return timestamp.astimezone(LOCAL_ZONE).hour


def backfill_knmi(
    start_date: str,
    end_date: str,
    timeout: float,
    workers: int,
    cache_dir: Path | None = None,
    max_files: int | None = None,
    local_hour_start: int | None = None,
    local_hour_end: int | None = None,
) -> list[dict[str, Any]]:
    token = os.environ.get("KNMI_OPEN_DATA_API_KEY", "").strip()
    if not token:
        raise RuntimeError("KNMI_OPEN_DATA_API_KEY is not configured")
    files_url = f"{KNMI_API_BASE}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    headers = {"Authorization": token}
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        begin_date = date.fromisoformat(start_date) - timedelta(days=1)
        begin_filename = (
            "KMDS__OPER_P___10M_OBS_L2_"
            f"{begin_date.strftime('%Y%m%d')}2100.nc"
        )
        selected = [
            item
            for item in list_knmi_files(client, token, begin=begin_filename)
            if start_date <= file_local_date(str(item.get("filename") or "")) <= end_date
            and (
                local_hour_start is None
                or (
                    (hour := file_local_hour(str(item.get("filename") or ""))) is not None
                    and local_hour_start <= hour < (local_hour_end or 24)
                )
            )
        ]
        if max_files is not None:
            selected = selected[: max(0, max_files)]
        if len(selected) > 850:
            raise RuntimeError(f"refusing {len(selected)} KNMI files; 850-file quota headroom limit")
        print(f"knmi_files={len(selected)} authenticated_request_budget={len(selected) + 1}", flush=True)

    raw_cache = cache_dir
    if raw_cache is not None:
        raw_cache.mkdir(parents=True, exist_ok=True)

    def download(file_meta: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        filename = str(file_meta["filename"])
        cache_path = None if raw_cache is None else raw_cache / filename
        if cache_path is not None and cache_path.exists() and cache_path.stat().st_size > 0:
            return file_meta, cache_path.read_bytes()
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                with httpx.Client(timeout=timeout, trust_env=False) as client:
                    url_response = client.get(
                        f"{files_url}/{filename}/url",
                        headers=headers,
                    )
                    url_response.raise_for_status()
                    download_url = str(
                        url_response.json().get("temporaryDownloadUrl") or ""
                    )
                    content_response = client.get(download_url)
                    content_response.raise_for_status()
                    content = content_response.content
                    if cache_path is not None:
                        cache_path.write_bytes(content)
                    return file_meta, content
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
        raise RuntimeError(f"failed to download {filename} after 3 attempts") from last_error

    downloaded: list[tuple[dict[str, Any], bytes]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(download, item) for item in selected]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            downloaded.append(future.result())
            if index % 50 == 0 or index == len(selected):
                print(f"knmi_downloaded={index}/{len(selected)}", flush=True)

    for file_meta, content in sorted(downloaded, key=lambda item: str(item[0]["filename"])):
        parsed = parse_knmi_netcdf(
            content,
            fetched_at=datetime.now(timezone.utc),
            file_metadata=file_meta,
        )
        for row in parsed:
            row["collection_mode"] = "historical_backfill_not_pit"
            row["historical_retrieved_at_utc"] = row.pop("knmi_first_seen_at_utc", "")
            rows.append(row)
    return rows


def load_metar(start_date: str, end_date: str) -> list[dict[str, Any]]:
    earliest: dict[str, dict[str, Any]] = {}
    for day in date_range(
        (date.fromisoformat(start_date) - timedelta(days=1)).isoformat(),
        (date.fromisoformat(end_date) + timedelta(days=1)).isoformat(),
    ):
        path = RUNTIME / "output/source_events" / day.isoformat() / "sources.jsonl"
        for raw in iter_jsonl(path):
            if raw.get("source") != "aviationweather_metar" or raw.get("city") != "Amsterdam" or raw.get("status") != "ok":
                continue
            report = parse_dt(raw.get("source_report_ts_utc"))
            detect = parse_dt(raw.get("local_detect_ts_utc"))
            temp = raw.get("temp_c")
            if report is None or detect is None or temp is None:
                continue
            key = report.isoformat()
            row = {
                "report_ts": report,
                "detect_ts": detect,
                "temp_c": float(temp),
                "temp_round_c": half_up(float(temp)),
                "target_date": report.astimezone(LOCAL_ZONE).date().isoformat(),
                "raw_metar": str(raw.get("raw_metar") or ""),
            }
            if key not in earliest or detect < earliest[key]["detect_ts"]:
                earliest[key] = row
    return sorted(earliest.values(), key=lambda row: row["report_ts"])


def align_next_metar(knmi: list[dict[str, Any]], metar: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports = [row["report_ts"] for row in metar]
    output: list[dict[str, Any]] = []
    for row in knmi:
        obs = parse_dt(row.get("observation_time_utc"))
        if obs is None:
            continue
        index = bisect.bisect_right(reports, obs)
        if index >= len(metar):
            continue
        following = metar[index]
        if following["target_date"] != row["target_date"]:
            continue
        lead = (following["report_ts"] - obs).total_seconds() / 60
        if not (0 < lead <= 45):
            continue
        ta = float(row["temp_c"])
        tx_raw = row.get("max_temp_c_past_10m")
        tx = float(tx_raw) if tx_raw not in (None, "") else ta
        output.append(
            {
                "target_date": row["target_date"],
                "knmi_obs_ts_utc": obs.isoformat(),
                "knmi_ta_c": ta,
                "knmi_tx_c": tx,
                "knmi_ta_round_c": half_up(ta),
                "knmi_tx_round_c": half_up(tx),
                "next_metar_report_ts_utc": following["report_ts"].isoformat(),
                "next_metar_detect_ts_utc": following["detect_ts"].isoformat(),
                "next_metar_temp_c": following["temp_c"],
                "lead_to_next_metar_min": lead,
                "ta_minus_next_metar_c": half_up(ta) - following["temp_round_c"],
                "tx_minus_next_metar_c": half_up(tx) - following["temp_round_c"],
                "ta_exact_next_metar": int(half_up(ta) == following["temp_round_c"]),
                "tx_exact_next_metar": int(half_up(tx) == following["temp_round_c"]),
                "ta_within1_next_metar": int(abs(half_up(ta) - following["temp_round_c"]) <= 1),
                "tx_within1_next_metar": int(abs(half_up(tx) - following["temp_round_c"]) <= 1),
                "raw_metar": following["raw_metar"],
            }
        )
    return output


def fetch_wu_day(target_date: str, timeout: float) -> dict[str, Any]:
    response = httpx.get(
        WEATHER_COM_HISTORICAL_OBS.format(location="EHAM:9:NL"),
        params={
            "apiKey": WEATHER_COM_API_KEY,
            "units": "m",
            "startDate": target_date.replace("-", ""),
            "endDate": target_date.replace("-", ""),
        },
        headers={
            "Origin": "https://www.wunderground.com",
            "Referer": "https://www.wunderground.com/history/daily/EHAM",
            "User-Agent": "Mozilla/5.0 pm-agents-knmi-calibration/1.0",
        },
        timeout=timeout,
        trust_env=False,
    )
    response.raise_for_status()
    observations = []
    for raw in response.json().get("observations") or []:
        timestamp = raw.get("valid_time_gmt")
        temp = raw.get("temp")
        if timestamp is None or temp is None:
            continue
        dt = datetime.fromtimestamp(int(timestamp), timezone.utc)
        if dt.astimezone(LOCAL_ZONE).date().isoformat() == target_date:
            observations.append((dt, float(temp)))
    maximum = max((temp for _, temp in observations), default=None)
    return {
        "target_date": target_date,
        "wu_rows": len(observations),
        "wu_native_daily_max_c": half_up(maximum) if maximum is not None else "",
        "wu_status": "ok" if observations else "empty",
    }


def load_winners(start_date: str, end_date: str) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    rows = conn.execute(
        """SELECT target_date,bracket FROM settlement_outcomes
           WHERE city='Amsterdam' AND settlement_status='settled'
             AND final_price>=.999 AND target_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return {str(target_date): str(bracket) for target_date, bracket in rows}


def build_daily(
    knmi: list[dict[str, Any]],
    metar: list[dict[str, Any]],
    wu: list[dict[str, Any]],
    winners: dict[str, str],
) -> list[dict[str, Any]]:
    knmi_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metar_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in knmi:
        knmi_by_day[str(row["target_date"])].append(row)
    for row in metar:
        metar_by_day[row["target_date"]].append(row)
    wu_by_day = {row["target_date"]: row for row in wu}
    output = []
    for target_date, rows in sorted(knmi_by_day.items()):
        wu_row = wu_by_day.get(target_date, {})
        ta_max = max(float(row["temp_c"]) for row in rows)
        tx_max = max(float(row.get("max_temp_c_past_10m") or row["temp_c"]) for row in rows)
        metar_max = max((row["temp_round_c"] for row in metar_by_day.get(target_date, [])), default=None)
        wu_max_raw = wu_row.get("wu_native_daily_max_c")
        wu_max = int(wu_max_raw) if wu_max_raw not in (None, "") else None
        winner = winners.get(target_date, "")
        output.append(
            {
                "target_date": target_date,
                "knmi_rows": len(rows),
                "knmi_ta_daily_max_raw_c": ta_max,
                "knmi_ta_daily_max_round_c": half_up(ta_max),
                "knmi_tx_daily_max_raw_c": tx_max,
                "knmi_tx_daily_max_round_c": half_up(tx_max),
                "eham_metar_daily_max_c": metar_max if metar_max is not None else "",
                "wu_native_daily_max_c": wu_max if wu_max is not None else "",
                "winning_bracket": winner,
                "ta_minus_wu_c": "" if wu_max is None else half_up(ta_max) - wu_max,
                "tx_minus_wu_c": "" if wu_max is None else half_up(tx_max) - wu_max,
                "metar_minus_wu_c": "" if wu_max is None or metar_max is None else metar_max - wu_max,
                "ta_in_winning_bracket": "" if not winner else int(eligibility.parse_bracket_contains(winner, half_up(ta_max))),
                "tx_in_winning_bracket": "" if not winner else int(eligibility.parse_bracket_contains(winner, half_up(tx_max))),
                "wu_in_winning_bracket": "" if not winner or wu_max is None else int(eligibility.parse_bracket_contains(winner, wu_max)),
                "tx_terminal_false_cross": "" if wu_max is None else int(half_up(tx_max) > wu_max),
                "wu_status": wu_row.get("wu_status", "missing"),
            }
        )
    return output


def build_publication_timing(knmi: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    rows: list[dict[str, Any]] = []
    for row in knmi:
        interval_end = parse_dt(row.get("measurement_interval_end_utc") or row.get("observation_time_utc"))
        created = parse_dt(row.get("knmi_file_created_at_utc"))
        modified = parse_dt(row.get("knmi_file_last_modified_at_utc"))
        if interval_end is None or created is None:
            continue
        created_delay = (created - interval_end).total_seconds()
        modified_delay = (
            (modified - interval_end).total_seconds()
            if modified is not None
            else math.nan
        )
        rows.append(
            {
                "target_date": row.get("target_date"),
                "knmi_filename": row.get("knmi_filename"),
                "interval_end_utc": interval_end.isoformat(),
                "created_at_utc": created.isoformat(),
                "last_modified_at_utc": modified.isoformat() if modified else "",
                "created_delay_sec": created_delay,
                "last_modified_delay_sec": "" if math.isnan(modified_delay) else modified_delay,
                "revision_lag_after_created_sec": (
                    ""
                    if math.isnan(modified_delay)
                    else modified_delay - created_delay
                ),
            }
        )
    delays = [float(row["created_delay_sec"]) for row in rows]
    modified_delays = [
        float(row["last_modified_delay_sec"])
        for row in rows
        if row["last_modified_delay_sec"] != ""
    ]
    revision_lags = [
        float(row["revision_lag_after_created_sec"])
        for row in rows
        if row["revision_lag_after_created_sec"] != ""
    ]
    summary = {
        "files": float(len(rows)),
        "created_min_sec": min(delays),
        "created_p50_sec": percentile(delays, 0.50),
        "created_p90_sec": percentile(delays, 0.90),
        "created_p95_sec": percentile(delays, 0.95),
        "created_p99_sec": percentile(delays, 0.99),
        "created_max_sec": max(delays),
        "created_after_5m_count": float(sum(value > 300 for value in delays)),
        "modified_p50_sec": percentile(modified_delays, 0.50),
        "modified_p95_sec": percentile(modified_delays, 0.95),
        "modified_max_sec": max(modified_delays),
        "revision_lag_p50_sec": percentile(revision_lags, 0.50),
        "revision_lag_max_sec": max(revision_lags),
    }
    return rows, summary


def build_cross_events(
    knmi: list[dict[str, Any]],
    metar: list[dict[str, Any]],
    wu: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    knmi_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metar_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in knmi:
        if parse_dt(row.get("observation_time_utc")) is not None:
            knmi_by_day[str(row["target_date"])].append(row)
    for row in metar:
        metar_by_day[str(row["target_date"])].append(row)
    wu_by_day = {
        str(row["target_date"]): (
            None
            if row.get("wu_native_daily_max_c") in (None, "")
            else int(row["wu_native_daily_max_c"])
        )
        for row in wu
    }

    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for target_date, source_rows in sorted(knmi_by_day.items()):
        source_rows.sort(key=lambda row: parse_dt(row.get("observation_time_utc")) or datetime.min.replace(tzinfo=timezone.utc))
        reports = sorted(metar_by_day.get(target_date, []), key=lambda row: row["report_ts"])
        for source_index, row in enumerate(source_rows):
            obs = parse_dt(row.get("observation_time_utc"))
            if obs is None:
                continue
            prior_reports = [item for item in reports if item["report_ts"] <= obs]
            later_reports = [item for item in reports if item["report_ts"] > obs]
            if not prior_reports or not later_reports:
                continue
            prior_max = max(int(item["temp_round_c"]) for item in prior_reports)
            following = later_reports[0]
            if (following["report_ts"] - obs).total_seconds() > 45 * 60:
                continue
            next_source = source_rows[source_index + 1] if source_index + 1 < len(source_rows) else None
            for field, value_key in (("ta", "temp_c"), ("tx", "max_temp_c_past_10m")):
                raw_value = row.get(value_key)
                if raw_value in (None, ""):
                    continue
                source_round = half_up(float(raw_value))
                if source_round <= prior_max:
                    continue
                event_key = (target_date, field, prior_max)
                if event_key in seen:
                    continue
                seen.add(event_key)
                next_source_value = (
                    None
                    if next_source is None or next_source.get(value_key) in (None, "")
                    else half_up(float(next_source[value_key]))
                )
                wu_max = wu_by_day.get(target_date)
                output.append(
                    {
                        "target_date": target_date,
                        "source_field": field,
                        "prior_metar_running_max_c": prior_max,
                        "knmi_obs_ts_utc": obs.isoformat(),
                        "knmi_value_c": float(raw_value),
                        "knmi_round_c": source_round,
                        "cross_increment_c": source_round - prior_max,
                        "next_knmi_round_c": "" if next_source_value is None else next_source_value,
                        "persistent_next_knmi": int(
                            next_source_value is not None and next_source_value > prior_max
                        ),
                        "next_metar_report_ts_utc": following["report_ts"].isoformat(),
                        "next_metar_temp_round_c": int(following["temp_round_c"]),
                        "next_metar_confirmed_cross": int(int(following["temp_round_c"]) > prior_max),
                        "wu_native_daily_max_c": "" if wu_max is None else wu_max,
                        "wu_final_confirmed_cross": (
                            "" if wu_max is None else int(wu_max > prior_max)
                        ),
                        "terminal_false_cross": (
                            "" if wu_max is None else int(wu_max <= prior_max)
                        ),
                        "file_created_at_utc": row.get("knmi_file_created_at_utc", ""),
                    }
                )
    return output


def ratio(rows: list[dict[str, Any]], key: str) -> str:
    return "NA" if not rows else f"{sum(int(row[key]) for row in rows)}/{len(rows)} ({100*sum(int(row[key]) for row in rows)/len(rows):.1f}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-22")
    parser.add_argument("--end-date", default="2026-07-26")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--reuse-knmi-csv", action="store_true")
    parser.add_argument("--knmi-cache-dir", type=Path)
    parser.add_argument("--knmi-max-files", type=int)
    parser.add_argument("--knmi-local-hour-start", type=int)
    parser.add_argument("--knmi-local-hour-end", type=int)
    args = parser.parse_args()

    if (args.knmi_local_hour_start is None) != (
        args.knmi_local_hour_end is None
    ):
        parser.error(
            "--knmi-local-hour-start and --knmi-local-hour-end must be set together"
        )
    if args.knmi_local_hour_start is not None and not (
        0 <= args.knmi_local_hour_start < args.knmi_local_hour_end <= 24
    ):
        parser.error("KNMI local-hour window must satisfy 0 <= start < end <= 24")
    if args.knmi_max_files is not None and args.knmi_max_files < 0:
        parser.error("--knmi-max-files must be non-negative")

    raw_path = OUT / "knmi_observations_backfill.csv"
    if args.reuse_knmi_csv:
        knmi = read_csv(raw_path)
    else:
        knmi = backfill_knmi(
            args.start_date,
            args.end_date,
            args.timeout_sec,
            args.workers,
            cache_dir=args.knmi_cache_dir,
            max_files=args.knmi_max_files,
            local_hour_start=args.knmi_local_hour_start,
            local_hour_end=args.knmi_local_hour_end,
        )
        write_csv(raw_path, knmi)
    metar = load_metar(args.start_date, args.end_date)
    next_rows = align_next_metar(knmi, metar)
    wu = [fetch_wu_day(day.isoformat(), args.timeout_sec) for day in date_range(args.start_date, args.end_date)]
    winners = load_winners(args.start_date, args.end_date)
    daily = build_daily(knmi, metar, wu, winners)
    timing_rows, timing = build_publication_timing(knmi)
    cross_events = build_cross_events(knmi, metar, wu)

    write_csv(OUT / "next_eham_metar_alignment.csv", next_rows)
    write_csv(OUT / "daily_knmi_eham_wu.csv", daily)
    write_csv(OUT / "wu_fetch_status.csv", wu)
    write_csv(OUT / "publication_timing.csv", timing_rows)
    write_csv(OUT / "cross_events.csv", cross_events)

    ta_bias = [int(row["ta_minus_wu_c"]) for row in daily if row["ta_minus_wu_c"] != ""]
    tx_bias = [int(row["tx_minus_wu_c"]) for row in daily if row["tx_minus_wu_c"] != ""]
    ta_next_bias = [int(row["ta_minus_next_metar_c"]) for row in next_rows]
    tx_next_bias = [int(row["tx_minus_next_metar_c"]) for row in next_rows]
    canonical_days = [row for row in daily if row["winning_bracket"]]
    ta_cross = [row for row in cross_events if row["source_field"] == "ta"]
    tx_cross = [row for row in cross_events if row["source_field"] == "tx"]
    daily_table = "\n".join(
        f"| `{row['target_date']}` | {row['knmi_ta_daily_max_raw_c']}→{row['knmi_ta_daily_max_round_c']} | "
        f"{row['knmi_tx_daily_max_raw_c']}→{row['knmi_tx_daily_max_round_c']} | "
        f"{row['eham_metar_daily_max_c']} | {row['wu_native_daily_max_c']} | "
        f"`{row['winning_bracket'] or 'coverage_gap'}` | {row['tx_terminal_false_cross']} |"
        for row in daily
    )
    report = f"""# KNMI EHAM → METAR → WU alignment v1

## 数据快照

- 数据源：KNMI Open Data 10-minute station 240 historical files；Mac JRS `source_events` EHAM METAR；Weather.com/WU EHAM history；`runtime/weather.db settlement_outcomes`
- 窗口：`{args.start_date}..{args.end_date}`；KNMI `{len(knmi)}` rows；next-METAR `{len(next_rows)}` rows；WU `{sum(row['wu_status']=='ok' for row in wu)}/{len(wu)}` days；canonical settlement `{len(canonical_days)}/{len(daily)}` days
- grain：observation→next routine METAR；city-day→WU/settlement。历史 KNMI retrieval 不是 PIT first-seen，不能用于延迟/盘口研究。
- unsettled：`{len(daily)-len(canonical_days)}/{len(daily)}`；missing_bracket：`0`（无 winner 的日期按 coverage gap，不伪装为策略筛除）

## 结论

- `ta` arithmetic-round → 下一份 EHAM METAR exact：`{ratio(next_rows, 'ta_exact_next_metar')}`；±1°C：`{ratio(next_rows, 'ta_within1_next_metar')}`。
- `tx` arithmetic-round → 下一份 EHAM METAR exact：`{ratio(next_rows, 'tx_exact_next_metar')}`；±1°C：`{ratio(next_rows, 'tx_within1_next_metar')}`。
- 下一份 METAR bias/MAE：`ta {statistics.mean(ta_next_bias):+.3f}/{statistics.mean(abs(value) for value in ta_next_bias):.3f}°C`；`tx {statistics.mean(tx_next_bias):+.3f}/{statistics.mean(abs(value) for value in tx_next_bias):.3f}°C`。因此逐报文映射优先 `ta`。
- 日最高对 WU：`ta-WU` median `{statistics.median(ta_bias) if ta_bias else 'NA'}°C`；`tx-WU` median `{statistics.median(tx_bias) if tx_bias else 'NA'}°C`。
- 日最高 exact WU：`ta {sum(value == 0 for value in ta_bias)}/{len(ta_bias)}`；`tx {sum(value == 0 for value in tx_bias)}/{len(tx_bias)}`。因此日最高候选优先 `tx`，但仍不能当 settlement latch。
- `tx > WU` terminal-false-cross days：`{sum(int(row['tx_terminal_false_cross']) for row in daily if row['tx_terminal_false_cross'] != '')}/{sum(row['tx_terminal_false_cross'] != '' for row in daily)}`。
- 结论等级：`inconclusive`。该窗口只校准 source basis；forward collector 从 2026-07-28 起才具备真实 first-seen clock，不授权 live。

## 发布节奏与采集策略

- 历史文件 metadata `{int(timing['files'])}` 个：`created - interval_end` min/p50/p95/p99/max = `{timing['created_min_sec']:.0f}/{timing['created_p50_sec']:.0f}/{timing['created_p95_sec']:.0f}/{timing['created_p99_sec']:.0f}/{timing['created_max_sec']:.0f}s`；超过 5 分钟 `{int(timing['created_after_5m_count'])}` 个。
- 这批样本的初次创建窗口为约 `+03:37..+04:11`。生产采集采用保守 hot window `+03:25..+04:20` 每 `10s` list；窗口外每 `300s`，并会提前唤醒到下一个 hot window。约 `48` 次 list/hour，低于 Open Data registered key 的 `1000/hour`。
- `lastModified - interval_end` p50/p95/max = `{timing['modified_p50_sec']:.0f}/{timing['modified_p95_sec']:.0f}/{timing['modified_max_sec']:.0f}s`；`lastModified-created` p50/max = `{timing['revision_lag_p50_sec']:.0f}/{timing['revision_lag_max_sec']:.0f}s`。因此同一 filename 必须按 revision 重采，不能 filename-only dedupe。
- KNMI 官方只承诺 10 分钟文件在几分钟后可用，不把上述 5 日经验窗口当 SLA；cold polling 用来捕捉异常延迟，forward first-seen 会继续校准窗口。

## Cross-NO 事件检验

- 事件定义：在下一份 EHAM METAR 之前，KNMI arithmetic-round 首次高于当日已见 METAR running max；每个 `date × source_field × prior_max` 只保留首个事件。
- `ta`：events `{len(ta_cross)}` / independent dates `{len(set(row['target_date'] for row in ta_cross))}`；下一 KNMI 仍 cross `{ratio(ta_cross, 'persistent_next_knmi')}`；下一 METAR confirm `{ratio(ta_cross, 'next_metar_confirmed_cross')}`；WU final confirm `{ratio(ta_cross, 'wu_final_confirmed_cross')}`；terminal false `{ratio(ta_cross, 'terminal_false_cross')}`。
- `tx`：events `{len(tx_cross)}` / independent dates `{len(set(row['target_date'] for row in tx_cross))}`；下一 KNMI 仍 cross `{ratio(tx_cross, 'persistent_next_knmi')}`；下一 METAR confirm `{ratio(tx_cross, 'next_metar_confirmed_cross')}`；WU final confirm `{ratio(tx_cross, 'wu_final_confirmed_cross')}`；terminal false `{ratio(tx_cross, 'terminal_false_cross')}`。
- action：只进入 `collector + zero-notional shadow`。5 个 independent city-days、无 PIT book/成交分母，且已有 terminal false cross，不能升 live。

## 每日对照

| Date | KNMI ta max raw→round | KNMI tx max raw→round | EHAM METAR max | WU max | canonical winner | tx false cross |
|---|---:|---:|---:|---:|---|---:|
{daily_table}

## 双漏斗

- signal funnel（observation grain）：KNMI files `{len(knmi)}` → next EHAM METAR `{len(next_rows)}`。
- evidence funnel（city-day grain）：KNMI `{len(daily)}` → WU `{sum(row['wu_status']=='ok' for row in wu)}` → canonical winner `{len(canonical_days)}` → PIT book `0` → fill `0`。

## 8 环

覆盖 source/reference/settlement basis；缺 PIT book、执行、容量、PnL、显著性 forward。`significance=NA baseline=NA forward=FAIL conclusion=inconclusive`。
"""
    REPORT.write_text(report, encoding="utf-8")
    print(json.dumps({"knmi_rows": len(knmi), "next_metar_rows": len(next_rows), "daily_rows": len(daily), "cross_events": len(cross_events), "report": str(REPORT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
