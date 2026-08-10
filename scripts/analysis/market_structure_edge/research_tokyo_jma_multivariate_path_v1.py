#!/usr/bin/env python3
"""Train Tokyo JMA→next-routine-METAR multivariate probability heads.

Historical JMA and NOAA/NCEI Global Hourly archives expose observation clocks,
not exact first-seen clocks.  Equal-timestamp METAR rows are excluded from the
state.  The resulting models are weather-path pretraining artifacts only.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_tokyo_jma_metar_history_alignment_v1 import (  # noqa: E402
    fetch_jma_history,
    parse_utc,
    round_native_c,
)
from scripts.analysis.market_structure_edge.build_three_city_official_path_history_v1 import (  # noqa: E402
    write_csv as write_jma_csv,
)
from weather_data_feed.physical_features import metar_physical_features  # noqa: E402


UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")
STATION_ID = "47671099999"
NCEI_BASE = "https://www.ncei.noaa.gov/data/global-hourly/access"
IEM_ASOS_API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HORIZONS = (30, 60, 120)
EPS = 1e-8
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_multivariate_path_v1"
)
PRIOR_JMA_CACHE = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "tokyo_jma_metar_history_alignment_v1"
    / "jma_haneda_10m_2024-04-30_2026-06-10.csv"
)

TEMP_FEATURES = (
    "jma_temp_c",
    "jma_temp_delta_10m",
    "jma_temp_slope_30m_cph",
    "jma_temp_slope_60m_cph",
    "jma_running_max_c",
    "distance_to_next_jma_lattice_c",
    "minutes_since_jma_strict_high",
    "jma_warming_run_count",
    "prior_metar_temp_c",
    "prior_metar_running_max_c",
    "jma_minus_prior_metar_c",
    "jma_lattice_minus_prior_metar_c",
    "prior_metar_age_min",
    "local_hour_sin",
    "local_hour_cos",
    "doy_sin",
    "doy_cos",
    "solar_elevation_deg",
)
JMA_WEATHER_FEATURES = TEMP_FEATURES + (
    "jma_wind_speed_kt",
    "jma_wind_gust_kt",
    "jma_gust_factor_kt",
    "jma_wind_u_kt",
    "jma_wind_v_kt",
    "jma_wind_speed_delta_30m_kt",
    "jma_wind_dir_change_30m_deg",
    "jma_precipitation_10m_mm",
    "jma_precipitation_30m_mm",
)
FULL_FEATURES = JMA_WEATHER_FEATURES + (
    "metar_dewpoint_c",
    "metar_relative_humidity_pct",
    "metar_dewpoint_depression_c",
    "metar_wind_speed_kt",
    "metar_wind_u_kt",
    "metar_wind_v_kt",
    "metar_pressure_hpa",
    "metar_pressure_delta",
    "metar_cloud_cover_fraction",
    "metar_ceiling_ft_agl",
    "metar_precipitating",
    "metar_visibility_m",
)


def finite(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def signed_tenths(value: Any) -> float | None:
    text = str(value or "").split(",", 1)[0].strip()
    if not text or text in {"+9999", "-9999", "9999"}:
        return None
    try:
        return int(text) / 10.0
    except ValueError:
        return None


def ncei_wind(value: Any) -> tuple[float | None, float | None]:
    parts = str(value or "").split(",")
    if len(parts) < 4:
        return None, None
    direction = finite(parts[0])
    speed_tenths_ms = finite(parts[3])
    speed_kt = (
        speed_tenths_ms / 10.0 * 1.94384
        if speed_tenths_ms is not None and speed_tenths_ms < 9999
        else None
    )
    if direction is not None and direction >= 999:
        direction = None
    return direction, speed_kt


def relative_humidity(temp_c: float | None, dewpoint_c: float | None) -> float | None:
    if temp_c is None or dewpoint_c is None:
        return None
    numerator = math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = math.exp((17.625 * temp_c) / (243.04 + temp_c))
    return max(0.0, min(100.0, 100.0 * numerator / denominator))


def raw_metar_from_rem(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(?:METAR|SPECI)\s+RJTT\s+.*?=", text)
    return match.group(0).rstrip("=") if match else text


def cloud_fraction(raw_metar: str) -> float | None:
    mapping = {"SKC": 0.0, "CLR": 0.0, "NSC": 0.0, "FEW": 0.25, "SCT": 0.5, "BKN": 0.875, "OVC": 1.0, "VV": 1.0}
    values = [
        mapping[token]
        for token in re.findall(r"\b(SKC|CLR|NSC|FEW|SCT|BKN|OVC|VV)", raw_metar)
    ]
    if "CAVOK" in raw_metar:
        values.append(0.0)
    return max(values) if values else None


def visibility_m(value: Any) -> float | None:
    text = str(value or "").split(",", 1)[0]
    parsed = finite(text)
    return parsed if parsed is not None and parsed < 999999 else None


def download_iem_year(year: int, start: date, end: date, path: Path) -> None:
    fields = (
        "tmpc",
        "dwpc",
        "relh",
        "drct",
        "sknt",
        "gust",
        "alti",
        "mslp",
        "p01m",
        "vsby",
        "skyc1",
        "skyl1",
        "wxcodes",
        "metar",
    )
    first = max(start, date(year, 1, 1))
    last = min(end, date(year, 12, 31))
    chunk_end = last + timedelta(days=1)
    params: list[tuple[str, str]] = [("station", "RJTT")]
    params.extend(("data", field) for field in fields)
    params.extend(
        [
            ("year1", str(first.year)),
            ("month1", str(first.month)),
            ("day1", str(first.day)),
            ("year2", str(chunk_end.year)),
            ("month2", str(chunk_end.month)),
            ("day2", str(chunk_end.day)),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("elev", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
            ("report_type", "1"),
            ("report_type", "2"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
    )
    response = None
    last_error: httpx.RequestError | None = None
    for attempt in range(4):
        try:
            response = httpx.get(
                IEM_ASOS_API,
                params=params,
                timeout=240,
                follow_redirects=True,
                headers={"User-Agent": "pm-agents-tokyo-research/1"},
            )
        except httpx.RequestError as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code != 429:
            break
        time.sleep(5 * (attempt + 1))
    if response is None:
        assert last_error is not None
        raise last_error
    response.raise_for_status()
    rows = [
        line
        for line in response.text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(rows) < 100:
        raise RuntimeError(f"IEM returned too few RJTT rows for {first}..{last}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def download_metar_archives(start: date, end: date, cache_dir: Path) -> list[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for year in range(start.year, end.year + 1):
        path = cache_dir / f"{STATION_ID}_{year}.csv"
        iem_path = cache_dir / f"iem_RJTT_{year}.csv"
        # A current-year IEM cache is an explicit source, not a fallback that
        # must be discarded merely because NCEI later starts publishing a
        # partial annual file.  Reuse it and fetch only its uncovered tail;
        # otherwise every research refresh downloads a multi-megabyte file
        # and can fail before the already valid cached rows are considered.
        if not path.exists() and iem_path.exists():
            path = iem_path
        if not path.exists():
            url = f"{NCEI_BASE}/{year}/{STATION_ID}.csv"
            response = httpx.get(
                url,
                timeout=120,
                follow_redirects=True,
                headers={"User-Agent": "pm-agents-tokyo-research/1"},
            )
            if response.status_code == 404:
                # NCEI Global Hourly annual files lag the current year.  IEM's
                # archived RJTT METAR rows fill that explicit coverage gap;
                # the distinct filename keeps provenance auditable.
                path = iem_path
                if not path.exists():
                    download_iem_year(year, start, end, path)
            else:
                response.raise_for_status()
                path.write_bytes(response.content)
        paths.append(path)
        if path.name.startswith(STATION_ID):
            expected_last = min(end, date(year, 12, 31))
            latest = None
            with path.open(encoding="utf-8", newline="") as handle:
                for raw in csv.DictReader(handle):
                    try:
                        observed = parse_utc(str(raw["DATE"])).astimezone(TOKYO).date()
                    except (KeyError, ValueError):
                        continue
                    latest = observed if latest is None else max(latest, observed)
            if latest is None or latest < expected_last:
                gap_start = max(start, (latest + timedelta(days=1)) if latest else date(year, 1, 1))
                gap_path = cache_dir / (
                    f"iem_RJTT_{year}_gap_{gap_start.isoformat()}_{expected_last.isoformat()}.csv"
                )
                if not gap_path.exists():
                    download_iem_year(year, gap_start, expected_last, gap_path)
                paths.append(gap_path)
        elif path.name.startswith("iem_RJTT_"):
            expected_last = min(end, date(year, 12, 31))
            latest = None
            with path.open(encoding="utf-8", newline="") as handle:
                for raw in csv.DictReader(handle):
                    try:
                        observed = datetime.strptime(
                            str(raw["valid"]), "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=UTC).astimezone(TOKYO).date()
                    except (KeyError, ValueError):
                        continue
                    latest = observed if latest is None else max(latest, observed)
            if latest is None or latest < expected_last:
                gap_start = max(
                    start,
                    (latest + timedelta(days=1)) if latest else date(year, 1, 1),
                )
                gap_path = cache_dir / (
                    f"iem_RJTT_{year}_gap_{gap_start.isoformat()}_"
                    f"{expected_last.isoformat()}.csv"
                )
                if not gap_path.exists():
                    download_iem_year(year, gap_start, expected_last, gap_path)
                paths.append(gap_path)
    return paths


def iem_visibility_m(value: Any) -> float | None:
    miles = finite(value)
    return miles * 1609.344 if miles is not None else None


def parse_iem_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        observed = datetime.strptime(
            str(raw["valid"]), "%Y-%m-%d %H:%M"
        ).replace(tzinfo=UTC)
    except (KeyError, ValueError):
        return None
    temp_c = finite(raw.get("tmpc"))
    if temp_c is None:
        return None
    dewpoint_c = finite(raw.get("dwpc"))
    raw_metar = str(raw.get("metar") or "")
    physical = metar_physical_features(raw_metar)
    pressure_hpa = finite(raw.get("mslp"))
    if pressure_hpa is None:
        pressure_hpa = physical.get("pressure_hpa")
    return {
        "observation_time_utc": observed,
        "local_date": observed.astimezone(TOKYO).date().isoformat(),
        "report_type": "IEM_ROUTINE" if observed.minute in {0, 30} else "IEM_SPECIAL",
        "routine": observed.minute in {0, 30},
        "temp_c": temp_c,
        "dewpoint_c": dewpoint_c,
        "relative_humidity_pct": finite(raw.get("relh"))
        or relative_humidity(temp_c, dewpoint_c),
        "wind_dir_deg": finite(raw.get("drct")),
        "wind_speed_kt": finite(raw.get("sknt")),
        "pressure_hpa": pressure_hpa,
        "cloud_cover_fraction": cloud_fraction(raw_metar),
        "ceiling_ft_agl": physical.get("ceiling_ft_agl"),
        "precipitating": int(
            bool(str(raw.get("wxcodes") or "").strip() not in {"", "M"})
            or physical.get("precip_state") == "rain_or_drizzle"
        ),
        "visibility_m": iem_visibility_m(raw.get("vsby")),
        "raw_metar": raw_metar,
    }


def parse_metar_archives(paths: Iterable[Path], start: date, end: date) -> list[dict[str, Any]]:
    candidates: dict[tuple[datetime, str], dict[str, Any]] = {}
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                if "valid" in raw:
                    row = parse_iem_row(raw)
                    if row is None:
                        continue
                    observed = row["observation_time_utc"]
                    local_date = observed.astimezone(TOKYO).date()
                    if not start <= local_date <= end:
                        continue
                    key = (observed, str(row["report_type"]))
                    candidates[key] = row
                    continue
                try:
                    observed = parse_utc(str(raw["DATE"]))
                except (KeyError, ValueError):
                    continue
                local_date = observed.astimezone(TOKYO).date()
                if not start <= local_date <= end:
                    continue
                temp_c = signed_tenths(raw.get("TMP"))
                if temp_c is None:
                    continue
                dewpoint_c = signed_tenths(raw.get("DEW"))
                wind_dir, wind_speed = ncei_wind(raw.get("WND"))
                raw_metar = raw_metar_from_rem(raw.get("REM"))
                physical = metar_physical_features(raw_metar)
                report_type = str(raw.get("REPORT_TYPE") or "")
                row = {
                    "observation_time_utc": observed,
                    "local_date": local_date.isoformat(),
                    "report_type": report_type,
                    "routine": report_type == "FM-15" and observed.minute in {0, 30},
                    "temp_c": temp_c,
                    "dewpoint_c": dewpoint_c,
                    "relative_humidity_pct": relative_humidity(temp_c, dewpoint_c),
                    "wind_dir_deg": wind_dir,
                    "wind_speed_kt": wind_speed,
                    "pressure_hpa": physical.get("pressure_hpa"),
                    "cloud_cover_fraction": cloud_fraction(raw_metar),
                    "ceiling_ft_agl": physical.get("ceiling_ft_agl"),
                    "precipitating": int(physical.get("precip_state") == "rain_or_drizzle"),
                    "visibility_m": visibility_m(raw.get("VIS")),
                    "raw_metar": raw_metar,
                }
                key = (observed, report_type)
                current = candidates.get(key)
                if current is None or len(raw_metar) > len(str(current.get("raw_metar") or "")):
                    candidates[key] = row
    # A timestamp can contain multiple NCEI report classes. Prefer FM-15 routine,
    # then the row with the most complete raw report.
    by_time: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates.values():
        by_time[row["observation_time_utc"]].append(row)
    output = []
    for observed, rows in by_time.items():
        rows.sort(
            key=lambda row: (
                int(row["report_type"] in {"FM-15", "IEM_ROUTINE"}),
                len(str(row.get("raw_metar") or "")),
            ),
            reverse=True,
        )
        output.append(rows[0])
    return sorted(output, key=lambda row: row["observation_time_utc"])


def load_jma_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ensure_jma_history(
    start: date,
    end: date,
    out_path: Path,
    *,
    workers: int,
) -> list[dict[str, Any]]:
    if out_path.exists():
        rows = load_jma_csv(out_path)
    elif PRIOR_JMA_CACHE.exists():
        rows = load_jma_csv(PRIOR_JMA_CACHE)
    else:
        rows = []
    # JMA encodes 24:00 as the following local midnight.  A cache ending at
    # 24:00 would otherwise make the next date look "covered" by one row and
    # silently leave almost the entire day absent.  Ten-minute AMeDAS days
    # normally contain 144 rows; require a deliberately loose completeness
    # floor so isolated upstream gaps do not trigger a redownload.
    rows_per_local_date = Counter(
        parse_utc(str(row["observation_time_utc"])).astimezone(TOKYO).date()
        for row in rows
    )
    covered_dates = {
        local_date
        for local_date, count in rows_per_local_date.items()
        if count >= 120
    }
    missing_dates = [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if start + timedelta(days=offset) not in covered_dates
    ]
    if missing_dates:
        # The normal gap is one contiguous tail.  Fetch contiguous runs so a
        # partial cache does not force a full historical redownload.
        run_start = missing_dates[0]
        prior = missing_dates[0]
        runs: list[tuple[date, date]] = []
        for current in missing_dates[1:]:
            if current != prior + timedelta(days=1):
                runs.append((run_start, prior))
                run_start = current
            prior = current
        runs.append((run_start, prior))
        for fetch_start, fetch_end in runs:
            rows.extend(
                fetch_jma_history(fetch_start, fetch_end, workers=workers)
            )
    filtered = [
        row
        for row in rows
        if start
        <= parse_utc(str(row["observation_time_utc"])).astimezone(TOKYO).date()
        <= end
    ]
    dedup = {
        str(row["observation_time_utc"]): row
        for row in filtered
    }
    output = sorted(dedup.values(), key=lambda row: row["observation_time_utc"])
    write_jma_csv(out_path, output)
    return output


def circular_delta(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None:
        return None
    return (current - prior + 180.0) % 360.0 - 180.0


def solar_elevation_approx(timestamp: datetime) -> float:
    local = timestamp.astimezone(TOKYO)
    day = local.timetuple().tm_yday
    decl = math.radians(23.44 * math.sin(2 * math.pi * (284 + day) / 365.0))
    lat = math.radians(35.55)
    hour_angle = math.radians(15.0 * ((local.hour + local.minute / 60.0) - 11.75))
    sin_alt = (
        math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def lagged(
    history: list[dict[str, Any]],
    timestamp: datetime,
    field: str,
    minutes: int,
) -> float | None:
    cutoff = timestamp - timedelta(minutes=minutes)
    values = [
        finite(row.get(field))
        for row in history
        if cutoff <= row["timestamp"] < timestamp and finite(row.get(field)) is not None
    ]
    return values[0] if values else None


def trailing_sum(
    history: list[dict[str, Any]],
    timestamp: datetime,
    field: str,
    minutes: int,
) -> float | None:
    cutoff = timestamp - timedelta(minutes=minutes)
    values = [
        finite(row.get(field))
        for row in history
        if cutoff < row["timestamp"] <= timestamp and finite(row.get(field)) is not None
    ]
    return sum(float(value) for value in values if value is not None) if values else None


def build_feature_rows(
    jma_rows: list[dict[str, Any]],
    metar_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    metar_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metar_rows:
        metar_by_date[str(row["local_date"])].append(row)
    jma_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in jma_rows:
        observed = parse_utc(str(raw["observation_time_utc"]))
        local_date = observed.astimezone(TOKYO).date().isoformat()
        parsed = dict(raw)
        parsed["timestamp"] = observed
        jma_by_date[local_date].append(parsed)

    output: list[dict[str, Any]] = []
    for target_date, observations in sorted(jma_by_date.items()):
        observations.sort(key=lambda row: row["timestamp"])
        metars = sorted(
            metar_by_date.get(target_date, []),
            key=lambda row: row["observation_time_utc"],
        )
        if not metars:
            continue
        metar_times = [row["observation_time_utc"] for row in metars]
        routine = [row for row in metars if row["routine"]]
        if not routine:
            continue
        # Settlement-facing daily maximum uses every RJTT report, including
        # SPECI/special observations.  Restricting the terminal label to
        # routine rows can put a previously observed special-report maximum
        # above the alleged "final" maximum and create an impossible negative
        # remaining-rise label.
        final_metar_max_c = max(float(row["temp_c"]) for row in metars)
        final_metar_rounded_c = round_native_c(final_metar_max_c)
        routine_times = [row["observation_time_utc"] for row in routine]
        metar_running: list[float] = []
        running = -math.inf
        for row in metars:
            running = max(running, float(row["temp_c"]))
            metar_running.append(running)

        history: list[dict[str, Any]] = []
        jma_running = -math.inf
        last_high: datetime | None = None
        warming_run = 0
        for raw in observations:
            timestamp = raw["timestamp"]
            # Strictly earlier source state: equal observation clocks have
            # unknown publication ordering in historical archives.
            metar_index = bisect_left(metar_times, timestamp) - 1
            if metar_index < 0:
                continue
            prior_metar = metars[metar_index]
            age_min = (
                timestamp - prior_metar["observation_time_utc"]
            ).total_seconds() / 60.0
            if age_min > 120:
                continue
            temp = float(raw["temp_c"])
            previous_temp = finite(history[-1]["jma_temp_c"]) if history else None
            delta = None if previous_temp is None else temp - previous_temp
            warming_run = warming_run + 1 if delta is not None and delta > 0 else 0
            if temp > jma_running + EPS:
                jma_running = temp
                last_high = timestamp
            rounded = round_native_c(temp)
            wind_speed = finite(raw.get("wind_speed_kt"))
            wind_gust = finite(raw.get("wind_gust_kt"))
            wind_dir = finite(raw.get("wind_dir_deg"))
            lag30_temp = lagged(history, timestamp, "jma_temp_c", 30)
            lag60_temp = lagged(history, timestamp, "jma_temp_c", 60)
            lag30_wind = lagged(history, timestamp, "jma_wind_speed_kt", 30)
            lag30_dir = lagged(history, timestamp, "jma_wind_dir_deg", 30)
            prior_prior_metar = metars[metar_index - 1] if metar_index > 0 else None
            local = timestamp.astimezone(TOKYO)
            hour = local.hour + local.minute / 60.0
            day_of_year = local.timetuple().tm_yday
            row = {
                "timestamp": timestamp,
                "city": "Tokyo",
                "target_date": target_date,
                "decision_ts_utc": timestamp.isoformat(),
                "pit_provenance": "historical_non_pit_observation_clock",
                "final_metar_max_c": final_metar_max_c,
                "final_metar_rounded_c": final_metar_rounded_c,
                "jma_temp_c": temp,
                "jma_rounded_c": rounded,
                "jma_temp_delta_10m": delta,
                "jma_temp_slope_30m_cph": (
                    None if lag30_temp is None else (temp - lag30_temp) * 2.0
                ),
                "jma_temp_slope_60m_cph": (
                    None if lag60_temp is None else temp - lag60_temp
                ),
                "jma_running_max_c": jma_running,
                "distance_to_next_jma_lattice_c": round_native_c(jma_running) + 0.5 - jma_running,
                "minutes_since_jma_strict_high": (
                    0.0
                    if last_high is None
                    else (timestamp - last_high).total_seconds() / 60.0
                ),
                "jma_warming_run_count": warming_run,
                "prior_metar_time_utc": prior_metar["observation_time_utc"].isoformat(),
                "prior_metar_temp_c": prior_metar["temp_c"],
                "prior_metar_running_max_c": metar_running[metar_index],
                "jma_minus_prior_metar_c": temp - float(prior_metar["temp_c"]),
                "jma_lattice_minus_prior_metar_c": rounded - float(prior_metar["temp_c"]),
                "prior_metar_age_min": age_min,
                "local_hour": hour,
                "local_hour_sin": math.sin(2 * math.pi * hour / 24),
                "local_hour_cos": math.cos(2 * math.pi * hour / 24),
                "doy_sin": math.sin(2 * math.pi * day_of_year / 365.25),
                "doy_cos": math.cos(2 * math.pi * day_of_year / 365.25),
                "solar_elevation_deg": solar_elevation_approx(timestamp),
                "jma_wind_speed_kt": wind_speed,
                "jma_wind_gust_kt": wind_gust,
                "jma_gust_factor_kt": (
                    None if wind_speed is None or wind_gust is None else wind_gust - wind_speed
                ),
                "jma_wind_dir_deg": wind_dir,
                "jma_wind_u_kt": (
                    None if wind_speed is None or wind_dir is None else -wind_speed * math.sin(math.radians(wind_dir))
                ),
                "jma_wind_v_kt": (
                    None if wind_speed is None or wind_dir is None else -wind_speed * math.cos(math.radians(wind_dir))
                ),
                "jma_wind_speed_delta_30m_kt": (
                    None if wind_speed is None or lag30_wind is None else wind_speed - lag30_wind
                ),
                "jma_wind_dir_change_30m_deg": circular_delta(wind_dir, lag30_dir),
                "jma_precipitation_10m_mm": finite(raw.get("precipitation_10m_mm")),
                "jma_precipitation_30m_mm": trailing_sum(
                    history
                    + [
                        {
                            "timestamp": timestamp,
                            "jma_precipitation_10m_mm": finite(raw.get("precipitation_10m_mm")),
                        }
                    ],
                    timestamp,
                    "jma_precipitation_10m_mm",
                    30,
                ),
                "metar_dewpoint_c": prior_metar["dewpoint_c"],
                "metar_relative_humidity_pct": prior_metar["relative_humidity_pct"],
                "metar_dewpoint_depression_c": (
                    None
                    if prior_metar["dewpoint_c"] is None
                    else float(prior_metar["temp_c"]) - float(prior_metar["dewpoint_c"])
                ),
                "metar_wind_speed_kt": prior_metar["wind_speed_kt"],
                "metar_wind_u_kt": (
                    None
                    if prior_metar["wind_speed_kt"] is None or prior_metar["wind_dir_deg"] is None
                    else -float(prior_metar["wind_speed_kt"]) * math.sin(math.radians(float(prior_metar["wind_dir_deg"])))
                ),
                "metar_wind_v_kt": (
                    None
                    if prior_metar["wind_speed_kt"] is None or prior_metar["wind_dir_deg"] is None
                    else -float(prior_metar["wind_speed_kt"]) * math.cos(math.radians(float(prior_metar["wind_dir_deg"])))
                ),
                "metar_pressure_hpa": prior_metar["pressure_hpa"],
                "metar_pressure_delta": (
                    None
                    if prior_prior_metar is None
                    or prior_metar["pressure_hpa"] is None
                    or prior_prior_metar["pressure_hpa"] is None
                    else float(prior_metar["pressure_hpa"]) - float(prior_prior_metar["pressure_hpa"])
                ),
                "metar_cloud_cover_fraction": prior_metar["cloud_cover_fraction"],
                "metar_ceiling_ft_agl": prior_metar["ceiling_ft_agl"],
                "metar_precipitating": prior_metar["precipitating"],
                "metar_visibility_m": prior_metar["visibility_m"],
            }
            for horizon in HORIZONS:
                start_index = bisect_right(routine_times, timestamp)
                end_index = bisect_right(
                    routine_times, timestamp + timedelta(minutes=horizon)
                )
                future = routine[start_index:end_index]
                complete = bool(
                    future
                    and future[-1]["observation_time_utc"]
                    >= timestamp + timedelta(minutes=horizon - 35)
                )
                row[f"confirm_jma_lattice_within_{horizon}m"] = (
                    int(any(float(candidate["temp_c"]) >= rounded for candidate in future))
                    if complete
                    else None
                )
            # Final routine METAR bridge, still only a source proxy.
            later_routine = [
                candidate
                for candidate in routine
                if candidate["observation_time_utc"] > timestamp
            ]
            row["final_metar_breaks_prior_running_max"] = (
                int(
                    bool(later_routine)
                    and max(float(candidate["temp_c"]) for candidate in later_routine)
                    > float(row["prior_metar_running_max_c"])
                )
                if later_routine
                else None
            )
            output.append(row)
            history.append(row)
    return output


def matrix(rows: list[dict[str, Any]], features: tuple[str, ...]) -> np.ndarray:
    return np.asarray(
        [
            [
                np.nan if finite(row.get(feature)) is None else float(row[feature])
                for feature in features
            ]
            for row in rows
        ],
        dtype=float,
    )


def date_weights(rows: list[dict[str, Any]], *, normalize: bool = True) -> np.ndarray:
    counts = Counter(str(row["target_date"]) for row in rows)
    weights = np.asarray([1.0 / counts[str(row["target_date"])] for row in rows])
    return weights / np.mean(weights) if normalize else weights / np.sum(weights)


def fit_models(
    rows: list[dict[str, Any]],
    label: str,
) -> dict[str, tuple[Any, tuple[str, ...]]]:
    y = np.asarray([int(row[label]) for row in rows])
    weights = date_weights(rows)
    output: dict[str, tuple[Any, tuple[str, ...]]] = {}
    for name, features in (
        ("temp_path_logit_v1", TEMP_FEATURES),
        ("jma_weather_logit_v1", JMA_WEATHER_FEATURES),
        ("jma_metar_logit_v1", FULL_FEATURES),
    ):
        model = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.3,
                        max_iter=3000,
                        solver="lbfgs",
                        random_state=20260731,
                    ),
                ),
            ]
        )
        model.fit(matrix(rows, features), y, model__sample_weight=weights)
        output[name] = (model, features)
    hgb = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    learning_rate=0.04,
                    max_iter=180,
                    max_leaf_nodes=15,
                    min_samples_leaf=100,
                    l2_regularization=3.0,
                    random_state=20260731,
                ),
            ),
        ]
    )
    hgb.fit(matrix(rows, FULL_FEATURES), y, model__sample_weight=weights)
    output["jma_metar_hgb_v1"] = (hgb, FULL_FEATURES)
    return output


def metric_row(
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    label: str,
    *,
    horizon: int,
    model: str,
    split: str,
    sample_slice: str,
) -> dict[str, Any]:
    weights = date_weights(rows, normalize=False)
    y = np.asarray([int(row[label]) for row in rows])
    p = np.clip(probabilities, EPS, 1 - EPS)
    ece = 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        mask = (p >= lower) & (p < lower + 0.1)
        if not np.any(mask):
            continue
        ece += float(np.sum(weights[mask])) * abs(
            float(np.average(p[mask], weights=weights[mask]))
            - float(np.average(y[mask], weights=weights[mask]))
        )
    return {
        "horizon_min": horizon,
        "split": split,
        "slice": sample_slice,
        "model": model,
        "events": len(rows),
        "target_dates": len({row["target_date"] for row in rows}),
        "positive_rate": float(np.average(y, weights=weights)),
        "mean_probability": float(np.average(p, weights=weights)),
        "brier": float(np.average((p - y) ** 2, weights=weights)),
        "logloss": float(log_loss(y, p, sample_weight=weights, labels=[0, 1])),
        "ece_10bin": ece,
        "auc": (
            float(roc_auc_score(y, p, sample_weight=weights))
            if len(set(y.tolist())) > 1
            else None
        ),
    }


def bootstrap_brier_delta(
    rows: list[dict[str, Any]],
    challenger: np.ndarray,
    baseline: np.ndarray,
    label: str,
) -> tuple[float, float, float]:
    daily: dict[str, list[float]] = defaultdict(list)
    for row, p1, p0 in zip(rows, challenger, baseline):
        y = int(row[label])
        daily[str(row["target_date"])].append(
            (float(p1) - y) ** 2 - (float(p0) - y) ** 2
        )
    values = np.asarray([np.mean(value) for _, value in sorted(daily.items())])
    rng = np.random.default_rng(20260731)
    draws = np.asarray(
        [
            float(np.mean(rng.choice(values, len(values), replace=True)))
            for _ in range(3000)
        ]
    )
    return (
        float(np.mean(values)),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def evaluate(
    rows: list[dict[str, Any]],
    train_end: date,
    validation_start: date,
    validation_end: date,
    forward_start: date,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    scores: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    predictions_out: list[dict[str, Any]] = []
    coefficients: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        label = f"confirm_jma_lattice_within_{horizon}m"
        train = [
            row
            for row in rows
            if row.get(label) is not None
            and str(row["target_date"]) <= train_end.isoformat()
        ]
        models = fit_models(train, label)
        prior = float(
            np.average(
                [int(row[label]) for row in train],
                weights=date_weights(train),
            )
        )
        for name, (model, features) in models.items():
            if name.endswith("logit_v1"):
                for feature, coefficient in zip(
                    features, model.named_steps["model"].coef_[0]
                ):
                    coefficients.append(
                        {
                            "horizon_min": horizon,
                            "model": name,
                            "feature": feature,
                            "standardized_coefficient": float(coefficient),
                        }
                    )
        for split, selected in (
            (
                "validation",
                [
                    row
                    for row in rows
                    if row.get(label) is not None
                    and validation_start.isoformat()
                    <= str(row["target_date"])
                    <= validation_end.isoformat()
                ],
            ),
            (
                "frozen_forward",
                [
                    row
                    for row in rows
                    if row.get(label) is not None
                    and str(row["target_date"]) >= forward_start.isoformat()
                ],
            ),
        ):
            predictions = {"train_date_prior": np.repeat(prior, len(selected))}
            for name, (model, features) in models.items():
                predictions[name] = model.predict_proba(matrix(selected, features))[:, 1]
            slices = {
                "all": np.ones(len(selected), dtype=bool),
                "heating_05_18": np.asarray(
                    [5 <= float(row["local_hour"]) < 18 for row in selected]
                ),
                "lead_lattice_ge_1": np.asarray(
                    [
                        5 <= float(row["local_hour"]) < 18
                        and float(row["jma_lattice_minus_prior_metar_c"]) >= 1
                        for row in selected
                    ]
                ),
            }
            for slice_name, mask in slices.items():
                selected_rows = [
                    row for row, keep in zip(selected, mask) if keep
                ]
                if not selected_rows:
                    continue
                for name, probability in predictions.items():
                    scores.append(
                        metric_row(
                            selected_rows,
                            probability[mask],
                            label,
                            horizon=horizon,
                            model=name,
                            split=split,
                            sample_slice=slice_name,
                        )
                    )
                baseline = predictions["temp_path_logit_v1"][mask]
                for name in (
                    "jma_weather_logit_v1",
                    "jma_metar_logit_v1",
                    "jma_metar_hgb_v1",
                ):
                    delta, low, high = bootstrap_brier_delta(
                        selected_rows,
                        predictions[name][mask],
                        baseline,
                        label,
                    )
                    deltas.append(
                        {
                            "horizon_min": horizon,
                            "split": split,
                            "slice": slice_name,
                            "challenger": name,
                            "baseline": "temp_path_logit_v1",
                            "brier_delta": delta,
                            "date_bootstrap_ci_low": low,
                            "date_bootstrap_ci_high": high,
                            "events": len(selected_rows),
                            "target_dates": len(
                                {row["target_date"] for row in selected_rows}
                            ),
                        }
                    )
            for index, row in enumerate(selected):
                for name, probability in predictions.items():
                    predictions_out.append(
                        {
                            "city": "Tokyo",
                            "target_date": row["target_date"],
                            "decision_ts_utc": row["decision_ts_utc"],
                            "target_id": f"next_routine_metar_confirms_jma_lattice_{horizon}m",
                            "p_model": float(probability[index]),
                            "label": int(row[label]),
                            "split": split,
                            "model_id": name,
                            "feature_set_id": (
                                "date_prior"
                                if name == "train_date_prior"
                                else "+".join(dict(models).get(name, (None, ()))[1])
                            ),
                            "pit_provenance": row["pit_provenance"],
                            "jma_temp_c": row["jma_temp_c"],
                            "jma_rounded_c": row["jma_rounded_c"],
                            "prior_metar_temp_c": row["prior_metar_temp_c"],
                            "jma_lattice_minus_prior_metar_c": row[
                                "jma_lattice_minus_prior_metar_c"
                            ],
                            "local_hour": row["local_hour"],
                        }
                    )
    return scores, deltas, predictions_out, coefficients


def coverage(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    output = []
    for field in fields:
        count = sum(finite(row.get(field)) is not None for row in rows)
        output.append(
            {
                "feature": field,
                "non_null_rows": count,
                "rows": len(rows),
                "coverage": count / len(rows) if rows else None,
            }
        )
    return output


def write_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {
                key: value
                for key, value in row.items()
                if key != "timestamp"
            }
            for row in rows
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2024-04-30")
    parser.add_argument("--end-date", default="2026-07-30")
    parser.add_argument("--train-end", default="2025-06-30")
    parser.add_argument("--validation-start", default="2025-07-01")
    parser.add_argument("--validation-end", default="2025-12-31")
    parser.add_argument("--forward-start", default="2026-01-01")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    metar_paths = download_metar_archives(start, end, args.out / "metar_cache")
    metar_rows = parse_metar_archives(metar_paths, start, end)
    jma_cache = args.out / f"jma_haneda_10m_{args.start_date}_{args.end_date}.csv"
    jma_rows = ensure_jma_history(
        start, end, jma_cache, workers=args.workers
    )
    feature_rows = build_feature_rows(jma_rows, metar_rows)
    scores, deltas, predictions, coefficients = evaluate(
        feature_rows,
        date.fromisoformat(args.train_end),
        date.fromisoformat(args.validation_start),
        date.fromisoformat(args.validation_end),
        date.fromisoformat(args.forward_start),
    )
    write_rows(args.out / "feature_rows.csv.gz", feature_rows)
    write_rows(args.out / "scores.csv", scores)
    write_rows(args.out / "brier_deltas.csv", deltas)
    write_rows(args.out / "predictions.csv.gz", predictions)
    write_rows(args.out / "logit_coefficients.csv", coefficients)
    write_rows(args.out / "feature_coverage.csv", coverage(feature_rows, FULL_FEATURES))
    summary = {
        "schema_version": "tokyo_jma_multivariate_path_v1",
        "clock_class": "historical_observation_clock_not_first_seen",
        "window": {"start": args.start_date, "end": args.end_date},
        "splits": {
            "train_end": args.train_end,
            "validation_start": args.validation_start,
            "validation_end": args.validation_end,
            "forward_start": args.forward_start,
        },
        "jma_rows": len(jma_rows),
        "metar_rows": len(metar_rows),
        "feature_rows": len(feature_rows),
        "feature_dates": len({row["target_date"] for row in feature_rows}),
        "metar_inputs": [
            {"path": str(path), "sha256": sha256_file(path)}
            for path in metar_paths
        ],
        "jma_input": {"path": str(jma_cache), "sha256": sha256_file(jma_cache)},
        "features": {
            "temp": TEMP_FEATURES,
            "jma_weather": JMA_WEATHER_FEATURES,
            "full": FULL_FEATURES,
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
