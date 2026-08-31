#!/usr/bin/env python3
"""WCIR next-official-print Helsinki/Tokyo research closure.

The runner is zero-notional and research-only.  Runtime and market inputs are
read-only; all writes are confined to a newly named review directory.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_rev2 import (  # noqa: E402
    CORE_FEATURES,
    PROBABILITY_COLUMNS,
    SUPPORT,
    MODEL_IDS,
    _evaluate_prediction_bundle,
    _prediction_bundle,
    _sweep,
    dependency_weights,
    expanding_folds,
)
from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import (  # noqa: E402
    MonotonicAdditiveModel,
    OrdinalThresholdModel,
    baseline_pmf,
    fit_temperature,
    half_up,
    temperature_scale,
)
from weather_modeling.next_print_feature_builder_v1 import (  # noqa: E402
    NextPrintFeatureBuilderV1,
    stable_hash,
)


UTC = timezone.utc
GENERATED_SCHEMA = "wcir_next_print_helsinki_tokyo_rev1"
DEFAULT_OUTPUT = ROOT / "reviews/wcir_next_official_print_helsinki_tokyo_rev1"
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
TOKYO_ARTIFACT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
    "tokyo_jma_multivariate_path_refresh_20260810_v2"
)
START_HISTORICAL = "2024-04-30"
END_HISTORICAL = "2026-08-09"
CAPTURE_START = "2026-08-09"
CAPTURE_END = "2026-08-29"
FMI_WFS = "https://opendata.fmi.fi/wfs"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
RESEARCH_NAMESPACE = "wcir_next_print_helsinki_tokyo_rev1"
DEFAULT_CANONICAL_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")


@dataclass(frozen=True)
class CityContract:
    city: str
    fast_source: str
    fast_station: str
    official_station: str
    timezone_name: str
    routine_minutes: tuple[int, ...]
    settlement_source: str
    source_basis: str
    path_start_local_minutes: int


CONTRACTS = {
    "Helsinki": CityContract(
        city="Helsinki",
        fast_source="fmi",
        fast_station="100968",
        official_station="EFHK",
        timezone_name="Europe/Helsinki",
        routine_minutes=(20, 50),
        settlement_source="WU EFHK daily history under market rules",
        source_basis="FMI Helsinki-Vantaa station 100968 10-minute t2m point observation",
        path_start_local_minutes=350,
    ),
    "Tokyo": CityContract(
        city="Tokyo",
        fast_source="jma_amedas",
        fast_station="44166",
        official_station="RJTT",
        timezone_name="Asia/Tokyo",
        routine_minutes=(0, 30),
        settlement_source="WU RJTT daily history under market rules",
        source_basis="JMA AMeDAS Haneda station 44166 10-minute point observation",
        path_start_local_minutes=350,
    ),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, rows: int | None = None, sealed_root: Path | None = None) -> dict[str, Any]:
    output = {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if rows is not None:
        output["row_count"] = int(rows)
    if sealed_root is not None:
        output["sealed_relative_path"] = str(path.resolve().relative_to(sealed_root.resolve()))
    return output


def parse_ts(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            for row in rows:
                handle.write(
                    (json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()
                )
                count += 1
    return count


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def _fmi_pairs(xml: str) -> dict[pd.Timestamp, float]:
    output: dict[pd.Timestamp, float] = {}
    for block in re.split(r"<om:observedProperty\s", xml)[1:]:
        if not re.search(r"param=t2m(?:&|\")", block[:1400]):
            continue
        for time_text, value_text in re.findall(
            r"<wml2:MeasurementTVP>.*?<wml2:time>(.*?)</wml2:time>\s*"
            r"<wml2:value>(.*?)</wml2:value>",
            block,
            flags=re.DOTALL,
        ):
            try:
                output[pd.Timestamp(time_text).tz_convert("UTC")] = float(value_text)
            except (TypeError, ValueError):
                continue
    return output


def _fmi_chunk(start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, Any]]:
    query = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": "fmi::observations::weather::timevaluepair",
        "fmisid": "100968",
        "parameters": "t2m",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestep": "10",
    }
    response = httpx.get(
        f"{FMI_WFS}?{urlencode(query)}",
        timeout=90,
        follow_redirects=True,
        headers={"User-Agent": "pm-agents-wcir-research/1"},
    )
    response.raise_for_status()
    return [
        {
            "city": "Helsinki",
            "source": "fmi",
            "station": "100968",
            "observation_time_utc": stamp.isoformat(),
            "temp_c": value,
            "collection_mode": "historical_observation_clock_not_first_seen",
        }
        for stamp, value in _fmi_pairs(response.text).items()
    ]


def fetch_helsinki_fast(path: Path, start: str, end: str, workers: int = 8) -> None:
    first = pd.Timestamp(start, tz="UTC")
    stop = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    chunks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = first
    while cursor <= stop:
        chunk_end = min(cursor + pd.Timedelta(days=6, hours=23, minutes=59), stop)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + pd.Timedelta(minutes=1)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fmi_chunk, a, b): (a, b) for a, b in chunks}
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(key=lambda row: row["observation_time_utc"])
    frame = pd.DataFrame(rows).drop_duplicates(["observation_time_utc"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})


def fetch_iem_station(path: Path, station: str, start: str, end: str) -> None:
    first = date.fromisoformat(start) - timedelta(days=1)
    stop = date.fromisoformat(end) + timedelta(days=1)
    params: list[tuple[str, str]] = [
        ("station", station), ("data", "tmpc"), ("data", "metar"),
        ("year1", str(first.year)), ("month1", str(first.month)), ("day1", str(first.day)),
        ("year2", str(stop.year)), ("month2", str(stop.month)), ("day2", str(stop.day)),
        ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
        ("elev", "no"), ("missing", "M"), ("trace", "T"), ("direct", "no"),
        ("report_type", "3"), ("report_type", "4"),
    ]
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": "pm-agents-wcir-research/1"},
    ) as client:
        response = client.get(IEM_URL, params=params)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(response.text)


def load_fast_csv(path: Path, contract: CityContract) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="infer")
    observed_name = "observation_time_utc"
    if observed_name not in frame:
        raise RuntimeError(f"historical fast input lacks {observed_name}: {path}")
    frame = frame.loc[frame.get("city", contract.city).astype(str).eq(contract.city)].copy()
    frame["observed_at"] = pd.to_datetime(frame[observed_name], utc=True, errors="coerce")
    frame["latest_fast_native_value"] = pd.to_numeric(frame["temp_c"], errors="coerce")
    frame = frame.dropna(subset=["observed_at", "latest_fast_native_value"])
    local = frame["observed_at"].dt.tz_convert(contract.timezone_name)
    frame["target_date"] = local.dt.date.astype(str)
    frame = frame.loc[frame["target_date"].between(START_HISTORICAL, END_HISTORICAL)].copy()
    frame["available_at"] = pd.NaT
    frame["source_observation_id"] = [
        stable_hash({"city": contract.city, "station": contract.fast_station, "observed_at": stamp.isoformat(), "temp_c": temp})
        for stamp, temp in zip(frame["observed_at"], frame["latest_fast_native_value"])
    ]
    return frame.sort_values(["target_date", "observed_at", "source_observation_id"]).drop_duplicates(
        ["target_date", "observed_at"], keep="last"
    )


def load_iem(path: Path, contract: CityContract) -> pd.DataFrame:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    raw = pd.read_csv(io.StringIO("".join(lines)))
    if "station" in raw:
        raw = raw.loc[raw["station"].astype(str).eq(contract.official_station)]
    raw["official_report_at"] = pd.to_datetime(raw["valid"], utc=True, errors="coerce")
    raw["official_native_value"] = pd.to_numeric(raw["tmpc"], errors="coerce")
    return _clean_official(raw, contract)


def load_tokyo_official(paths: list[Path], contract: CityContract) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            for raw in csv.DictReader(handle):
                if "valid" in raw:
                    try:
                        rows.append({"official_report_at": pd.Timestamp(raw["valid"], tz="UTC"), "official_native_value": float(raw["tmpc"])})
                    except (TypeError, ValueError):
                        continue
                elif "DATE" in raw:
                    try:
                        value = int(str(raw["TMP"]).split(",", 1)[0]) / 10.0
                        if abs(value) > 80:
                            continue
                        rows.append({"official_report_at": pd.Timestamp(raw["DATE"], tz="UTC"), "official_native_value": value})
                    except (TypeError, ValueError):
                        continue
    return _clean_official(pd.DataFrame(rows), contract)


def _clean_official(frame: pd.DataFrame, contract: CityContract) -> pd.DataFrame:
    frame = frame.dropna(subset=["official_report_at", "official_native_value"]).copy()
    frame["official_report_at"] = pd.to_datetime(frame["official_report_at"], utc=True)
    frame = frame.loc[frame["official_report_at"].dt.minute.isin(contract.routine_minutes)].copy()
    local = frame["official_report_at"].dt.tz_convert(contract.timezone_name)
    frame["target_date"] = local.dt.date.astype(str)
    frame["official_native_value"] = np.floor(frame["official_native_value"].to_numpy(float) + 0.5)
    frame = frame.sort_values(["official_report_at"]).drop_duplicates("official_report_at", keep="last")
    frame["official_print_id"] = [
        stable_hash({"city": contract.city, "station": contract.official_station, "report_at": stamp.isoformat(), "value": value})
        for stamp, value in zip(frame["official_report_at"], frame["official_native_value"])
    ]
    return frame[["target_date", "official_report_at", "official_native_value", "official_print_id"]]


def build_historical_panel(
    fast: pd.DataFrame,
    official: pd.DataFrame,
    contract: CityContract,
    *,
    complete_path_before_label_filter: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    parts: list[pd.DataFrame] = []
    for target_date, day_fast in fast.groupby("target_date", sort=True):
        local_minute = (
            day_fast["observed_at"].dt.tz_convert(contract.timezone_name).dt.hour * 60
            + day_fast["observed_at"].dt.tz_convert(contract.timezone_name).dt.minute
        )
        day_fast = day_fast.loc[local_minute.ge(contract.path_start_local_minutes)].copy()
        if day_fast.empty:
            continue
        day_official = official.loc[official["target_date"].eq(target_date)].sort_values("official_report_at")
        if len(day_official) < 2:
            continue
        left = day_fast.sort_values("observed_at").copy()
        if complete_path_before_label_filter:
            left = NextPrintFeatureBuilderV1.add_path_features(
                left, group_column="target_date", require_available_at=False
            )
        official_join = day_official[
            ["official_report_at", "official_native_value", "official_print_id"]
        ]
        current = pd.merge_asof(
            left,
            official_join.rename(columns={"official_report_at": "last_official_at", "official_native_value": "last_official_native_value"}),
            left_on="observed_at", right_on="last_official_at", direction="backward", allow_exact_matches=False,
        )
        following = pd.merge_asof(
            left[["observed_at"]],
            official_join.rename(columns={"official_report_at": "next_official_observed_at", "official_native_value": "next_official_native_value", "official_print_id": "next_official_print_id"}),
            left_on="observed_at", right_on="next_official_observed_at", direction="forward", allow_exact_matches=False,
        )
        current[["next_official_observed_at", "next_official_native_value", "next_official_print_id"]] = following[
            ["next_official_observed_at", "next_official_native_value", "next_official_print_id"]
        ].to_numpy()
        current["official_running_max"] = current["last_official_native_value"].cummax()
        parts.append(current)
    if not parts:
        raise RuntimeError(f"no linked historical rows for {contract.city}")
    frame = pd.concat(parts, ignore_index=True)
    frame = frame.dropna(subset=["last_official_native_value", "next_official_native_value"]).copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame["next_official_observed_at"] = pd.to_datetime(frame["next_official_observed_at"], utc=True)
    frame["next_official_report_gap_seconds"] = (
        frame["next_official_observed_at"] - frame["observed_at"]
    ).dt.total_seconds()
    frame = frame.loc[frame["next_official_report_gap_seconds"].between(0, 1200, inclusive="right")].copy()
    if not complete_path_before_label_filter:
        frame = NextPrintFeatureBuilderV1.add_path_features(
            frame, group_column="target_date", require_available_at=False
        )
    local = frame["observed_at"].dt.tz_convert(contract.timezone_name)
    minute = local.dt.hour * 60 + local.dt.minute
    frame["local_time_sin"] = np.sin(2 * np.pi * minute / 1440)
    frame["local_time_cos"] = np.cos(2 * np.pi * minute / 1440)
    frame["fast_minus_last_official"] = frame["latest_fast_native_value"] - frame["last_official_native_value"]
    frame["fast_minus_running_max"] = frame["latest_fast_native_value"] - frame["official_running_max"]
    frame["distance_to_up_native_boundary"] = np.ceil(frame["latest_fast_native_value"]) - frame["latest_fast_native_value"]
    frame["distance_to_down_native_boundary"] = frame["latest_fast_native_value"] - np.floor(frame["latest_fast_native_value"])
    frame["next_official_delta_native_tick"] = (
        frame["next_official_native_value"] - frame["last_official_native_value"]
    )
    frame["official_print_group_id"] = frame["next_official_print_id"]
    frame["decision_vintage_id"] = [
        stable_hash({"city": contract.city, "target_date": day, "observed_at": observed.isoformat(), "last_official_at": str(last)})
        for day, observed, last in zip(frame["target_date"], frame["observed_at"], frame["last_official_at"])
    ]
    frame["opportunity_matched"] = (
        half_up(frame["latest_fast_native_value"]).to_numpy(float)
        == frame["official_running_max"].to_numpy(float) + 1
    )
    frame["availability_class"] = "HISTORICAL_FINAL_ARCHIVE"
    frame["strict_pit_eligible"] = False
    frame = frame.loc[frame["next_official_delta_native_tick"].between(SUPPORT[0], SUPPORT[-1])].copy()
    frame["next_official_delta_native_tick"] = frame["next_official_delta_native_tick"].astype(int)
    audit = {
        "city": contract.city,
        "clock_class": "historical_observation_clock_not_first_seen",
        "fast_rows": len(fast),
        "fast_dates": int(fast["target_date"].nunique()),
        "official_rows": len(official),
        "official_dates": int(official["target_date"].nunique()),
        "linked_rows": len(frame),
        "linked_dates": int(frame["target_date"].nunique()),
        "opportunity_matched_rows": int(frame["opportunity_matched"].sum()),
        "opportunity_matched_dates": int(frame.loc[frame["opportunity_matched"], "target_date"].nunique()),
        "date_range": [frame["target_date"].min(), frame["target_date"].max()],
        "strict_pit_training_rows": 0,
        "feature_path_start_local": "05:50",
    }
    return frame, audit


def load_captured_raw(runtime: Path, contract: CityContract) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    root = runtime / "output/live_cross_observations"
    for path in sorted(root.glob("2026-*/high_frequency_observations.jsonl")):
        if not CAPTURE_START <= path.parent.name <= CAPTURE_END:
            continue
        for raw in read_jsonl(path):
            if raw.get("city") != contract.city or raw.get("source") != contract.fast_source:
                continue
            if raw.get("temp_c") is None or raw.get("observation_time_utc") in (None, ""):
                continue
            rows.append(
                {
                    "target_date": str(raw.get("target_date") or ""),
                    "observed_at": raw.get("observation_time_utc"),
                    "available_at": raw.get("available_at_utc") or raw.get("first_seen_at_utc"),
                    "latest_fast_native_value": raw.get("temp_c"),
                    "source_observation_id": raw.get("information_event_id") or raw.get("raw_row_hash"),
                    "event_role": raw.get("event_role"),
                    "raw_row_hash": raw.get("raw_row_hash"),
                }
            )
    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    frame["latest_fast_native_value"] = pd.to_numeric(frame["latest_fast_native_value"], errors="coerce")
    return frame.dropna(subset=["observed_at", "available_at", "latest_fast_native_value", "source_observation_id"])


def load_captured_official(runtime: Path, contract: CityContract) -> pd.DataFrame:
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    root = runtime / "output/source_events"
    for path in sorted(root.glob("2026-*/sources.jsonl")):
        if not "2026-08-08" <= path.parent.name <= "2026-08-30":
            continue
        for raw in read_jsonl(path):
            station = str(raw.get("station_id") or raw.get("station") or "")
            if (
                raw.get("city") != contract.city
                or raw.get("source") != "aviationweather_metar"
                or station != contract.official_station
            ):
                continue
            report = parse_ts(raw.get("source_report_ts_utc"))
            available = parse_ts(raw.get("first_seen_at_utc") or raw.get("local_detect_ts_utc"))
            if report is None or available is None or raw.get("temp_c") is None:
                continue
            if report.minute not in contract.routine_minutes:
                continue
            key = (contract.city, report.isoformat())
            row = {
                "target_date": str(raw.get("target_date") or ""),
                "official_report_at": report,
                "official_available_at": available,
                "official_native_value": math.floor(float(raw["temp_c"]) + 0.5),
                "official_print_id": raw.get("information_event_id") or stable_hash(key),
                "official_station": station,
                "official_source": str(raw.get("source")),
                "official_raw_row_hash": raw.get("raw_row_hash"),
                "official_raw_payload_hash": raw.get("raw_payload_hash"),
                "official_raw_source_path": raw.get("raw_source_path"),
            }
            if key not in earliest or available < earliest[key]["official_available_at"]:
                earliest[key] = row
    return pd.DataFrame(earliest.values()).sort_values(["official_report_at", "official_available_at"])


def load_opportunities(runtime: Path, contract: CityContract) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    path = runtime / "output/fast_source_prev_no_trial/events.jsonl"
    for raw in read_jsonl(path):
        if raw.get("city") != contract.city or raw.get("source") != contract.fast_source:
            continue
        target_date = str(raw.get("target_date") or "")
        event_key = str(raw.get("event_key") or "")
        if not CAPTURE_START <= target_date <= CAPTURE_END or not event_key or event_key in seen:
            continue
        seen.add(event_key)
        rows.append(dict(raw))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["source_obs_ts_utc"] = pd.to_datetime(frame["source_obs_ts_utc"], utc=True)
    frame["source_detect_ts_utc"] = pd.to_datetime(frame["source_detect_ts_utc"], utc=True)
    frame["event_id"] = [
        stable_hash({"event_key": key, "source_detect_ts_utc": stamp.isoformat(), "token_id": str(token)})
        for key, stamp, token in zip(frame["event_key"], frame["source_detect_ts_utc"], frame["token_id"])
    ]
    return frame.sort_values(["source_detect_ts_utc", "event_id"])


def build_captured_panel(
    opportunities: pd.DataFrame,
    raw_path: pd.DataFrame,
    official: pd.DataFrame,
    contract: CityContract,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lineage: list[pd.DataFrame] = []
    audit_rows: list[dict[str, Any]] = []
    for event in opportunities.to_dict("records"):
        detect = pd.Timestamp(event["source_detect_ts_utc"])
        observed = pd.Timestamp(event["source_obs_ts_utc"])
        decision_ready = parse_ts(event.get("ts_utc")) or detect
        prior_report = parse_ts(event.get("latest_metar_report_ts_utc"))
        candidates = official.loc[
            official["target_date"].eq(str(event["target_date"]))
            & official["official_report_at"].gt(prior_report)
            & official["official_report_at"].gt(observed)
        ].sort_values(["official_report_at", "official_available_at"])
        following = candidates.iloc[0] if len(candidates) else None
        lead_seconds = None if following is None else (following["official_available_at"] - detect).total_seconds()
        report_gap_seconds = None if following is None else (following["official_report_at"] - observed).total_seconds()
        link_status = "OK"
        if decision_ready < detect:
            link_status = "DATA_FAIL_CLOSED_NONCAUSAL_DECISION_CLOCK"
        elif following is None:
            link_status = "DATA_FAIL_CLOSED_NO_NEXT_ROUTINE_PRINT"
        elif following["official_available_at"] <= decision_ready:
            link_status = "DATA_FAIL_CLOSED_TARGET_ALREADY_AVAILABLE_AT_DECISION"
        elif report_gap_seconds is None or report_gap_seconds > 1200:
            link_status = "RESEARCH_INELIGIBLE_NEXT_PRINT_OUTSIDE_1200S"
        legacy_clock_link_status = "OK"
        if following is None:
            legacy_clock_link_status = "DATA_FAIL_CLOSED_NO_NEXT_ROUTINE_PRINT"
        elif lead_seconds is None or lead_seconds > 1200:
            legacy_clock_link_status = "RESEARCH_INELIGIBLE_FIRST_SEEN_LATENCY_OUTSIDE_1200S"
        day_path = raw_path.loc[
            raw_path["target_date"].eq(str(event["target_date"]))
            & raw_path["observed_at"].le(observed)
        ].copy()
        eligible_archive_prefix = day_path.loc[day_path["available_at"].le(decision_ready)].copy()
        start_hour, start_minute = divmod(contract.path_start_local_minutes, 60)
        expected_path_start = pd.Timestamp(
            f"{event['target_date']} {start_hour:02d}:{start_minute:02d}:00",
            tz=ZoneInfo(contract.timezone_name),
        ).tz_convert("UTC")
        archive_prefix_complete = bool(
            len(eligible_archive_prefix)
            and eligible_archive_prefix["observed_at"].min() == expected_path_start
            and eligible_archive_prefix["observed_at"].max() == observed
            and eligible_archive_prefix["observed_at"].eq(observed).any()
        )
        local = observed.tz_convert(contract.timezone_name)
        minute = local.hour * 60 + local.minute
        source_value = float(event["source_temp_c"])
        last_official = float(event["latest_metar_round_c"])
        official_max = float(event["metar_running_max_round_c"])
        context_source_ids = [
            str(event.get("event_key") or event["event_id"]),
            f"official_report:{event.get('latest_metar_report_ts_utc')}",
        ]
        feature_context = {
            "last_official_native_value": {"value": last_official, "available_at": detect, "source_observation_ids": context_source_ids},
            "official_running_max": {"value": official_max, "available_at": detect, "source_observation_ids": context_source_ids},
            "fast_minus_last_official": {"value": source_value - last_official, "available_at": detect, "source_observation_ids": context_source_ids},
            "fast_minus_running_max": {"value": source_value - official_max, "available_at": detect, "source_observation_ids": context_source_ids},
            "distance_to_up_native_boundary": {"value": math.ceil(source_value) - source_value, "available_at": detect, "source_observation_ids": context_source_ids[:1]},
            "distance_to_down_native_boundary": {"value": source_value - math.floor(source_value), "available_at": detect, "source_observation_ids": context_source_ids[:1]},
            "local_time_sin": {"value": math.sin(2 * math.pi * minute / 1440), "available_at": detect, "source_observation_ids": context_source_ids[:1]},
            "local_time_cos": {"value": math.cos(2 * math.pi * minute / 1440), "available_at": detect, "source_observation_ids": context_source_ids[:1]},
        }
        build = NextPrintFeatureBuilderV1.build_vintage(
            day_path,
            decision_vintage_id=str(event["event_id"]),
            feature_cutoff_at=decision_ready,
            decision_ready_at=decision_ready,
            observation_cutoff_at=observed,
            availability_class="CAPTURED_PIT_ARCHIVE",
            feature_names=CORE_FEATURES,
            source_path_complete=archive_prefix_complete,
            expected_cadence_minutes=10,
            feature_context=feature_context,
        )
        endpoint_matches_event = (
            build.feature_vector.get("latest_fast_native_value") is not None
            and math.isclose(float(build.feature_vector["latest_fast_native_value"]), source_value, abs_tol=1e-9)
        )
        status = link_status if link_status != "OK" else (
            "OK" if build.status == "OK" and endpoint_matches_event
            else "DATA_FAIL_CLOSED_ENDPOINT_VALUE_MISMATCH" if build.status == "OK"
            else "DATA_FAIL_CLOSED_INCOMPLETE_SOURCE_PATH"
        )
        legacy_clock_status = legacy_clock_link_status if legacy_clock_link_status != "OK" else (
            "OK" if build.status == "OK" and endpoint_matches_event
            else "DATA_FAIL_CLOSED_ENDPOINT_VALUE_MISMATCH" if build.status == "OK"
            else "DATA_FAIL_CLOSED_INCOMPLETE_SOURCE_PATH"
        )
        audit_rows.append(
            {
                "event_id": event["event_id"], "city": contract.city,
                "target_date": event["target_date"], "source_obs_ts_utc": observed,
                "source_detect_ts_utc": detect, "next_official_lead_seconds": lead_seconds,
                "decision_ready_at_utc": decision_ready,
                "official_first_seen_at_utc": None if following is None else following["official_available_at"],
                "next_official_report_gap_seconds": report_gap_seconds,
                "feature_status": build.status, "eligibility_status": status,
                "legacy_first_seen_clock_eligibility_status": legacy_clock_status,
                "clock_contract_repair_changed_status": legacy_clock_status != status,
                "source_path_row_count": build.source_path_row_count,
                "frozen_archive_prefix_complete": archive_prefix_complete,
                "expected_source_path_start": expected_path_start,
                "source_path_start": build.source_path_start, "source_path_end": build.source_path_end,
                "max_feature_available_at": build.max_feature_available_at,
                "observed_gap_count": build.observed_gap_count,
                "feature_vector_hash": build.feature_vector_hash,
                "feature_vector_json": json.dumps(
                    build.feature_vector, sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
                "endpoint_matches_event": endpoint_matches_event,
            }
        )
        lineage.append(build.lineage)
        if status != "OK" or following is None:
            continue
        row = dict(event)
        row.update(build.feature_vector)
        row["target_date"] = str(event["target_date"])
        row["observed_at"] = observed
        row["next_official_native_value"] = float(following["official_native_value"])
        row["next_official_observed_at"] = following["official_report_at"]
        row["official_first_seen_at_utc"] = following["official_available_at"]
        row["official_station"] = following["official_station"]
        row["official_source"] = following["official_source"]
        row["official_raw_row_hash"] = following["official_raw_row_hash"]
        row["official_raw_payload_hash"] = following["official_raw_payload_hash"]
        row["official_raw_source_path"] = following["official_raw_source_path"]
        row["next_official_delta_native_tick"] = int(
            row["next_official_native_value"] - row["last_official_native_value"]
        )
        row["official_print_group_id"] = str(following["official_print_id"])
        row["decision_vintage_id"] = str(event["event_id"])
        row["opportunity_matched"] = True
        row["availability_class"] = "CAPTURED_PIT_ARCHIVE"
        row["strict_pit_eligible"] = True
        rows.append(row)
    captured = pd.DataFrame(rows)
    audit = pd.DataFrame(audit_rows)
    lineage_frame = pd.concat(lineage, ignore_index=True) if lineage else pd.DataFrame()
    summary = {
        "city": contract.city,
        "raw_opportunities": len(opportunities),
        "linked_and_feature_eligible": len(captured),
        "eligible_dates": int(captured["target_date"].nunique()) if len(captured) else 0,
        "status_histogram": audit["eligibility_status"].value_counts().to_dict(),
        "feature_status_histogram": audit["feature_status"].value_counts().to_dict(),
        "source_path_rows": len(raw_path),
        "source_path_dates": int(raw_path["target_date"].nunique()),
        "official_rows": len(official),
        "official_dates": int(official["target_date"].nunique()),
        "causal_gate": "source_detect <= feature cutoff < official first-seen; every path row available_at <= cutoff",
        "feature_path_start_local": "05:50",
    }
    return captured, audit, lineage_frame, summary


def fit_dependency_weights(frame: pd.DataFrame) -> np.ndarray:
    groups_per_date = frame.groupby("target_date")["official_print_group_id"].transform("nunique").to_numpy(float)
    rows_per_group = frame.groupby(["target_date", "official_print_group_id"])["decision_vintage_id"].transform("size").to_numpy(float)
    raw = (1.0 / groups_per_date) / rows_per_group
    unique_groups = frame[["target_date", "official_print_group_id"]].drop_duplicates().shape[0]
    return raw * (unique_groups / raw.sum())


def fit_temperature_dependency_weighted(pmf: np.ndarray, labels: np.ndarray, weights: np.ndarray) -> float:
    indices = np.searchsorted(SUPPORT, labels)
    normalized = weights / weights.sum()

    def objective(value: float) -> float:
        scaled = temperature_scale(pmf, value)
        losses = -np.log(np.clip(scaled[np.arange(len(labels)), indices], 1e-7, 1.0))
        return float(np.sum(losses * normalized))

    return float(minimize_scalar(objective, bounds=(0.5, 3.0), method="bounded").x)


def _b2_pmf(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    train_point = half_up(train["latest_fast_native_value"]).to_numpy(float) - train["last_official_native_value"].to_numpy(float)
    test_point = half_up(test["latest_fast_native_value"]).to_numpy(float) - test["last_official_native_value"].to_numpy(float)
    residual = np.clip(
        train["next_official_delta_native_tick"].to_numpy(int) - np.rint(train_point),
        SUPPORT[0], SUPPORT[-1],
    ).astype(int)
    weights = fit_dependency_weights(train)
    counts = np.bincount(residual - int(SUPPORT[0]), weights=weights, minlength=len(SUPPORT))
    residual_probability = (counts + 1.0) / (counts.sum() + len(SUPPORT))
    output = np.zeros((len(test_point), len(SUPPORT)), dtype=float)
    for row_index, prediction in enumerate(np.rint(test_point).astype(int)):
        for residual_value, probability in zip(SUPPORT, residual_probability):
            value = int(np.clip(prediction + residual_value, SUPPORT[0], SUPPORT[-1]))
            output[row_index, value - SUPPORT[0]] += probability
    return output / output.sum(axis=1, keepdims=True)


def train_city_models(
    historical: pd.DataFrame, captured: pd.DataFrame, city: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    outer_dates = sorted(historical["target_date"].unique())[-20:]
    development = historical.loc[~historical["target_date"].isin(outer_dates)].copy()
    outer = historical.loc[historical["target_date"].isin(outer_dates)].copy()
    folds = expanding_folds(sorted(development["target_date"].unique()))
    oof_parts: list[pd.DataFrame] = []
    fold_manifest: list[dict[str, Any]] = []
    prior_raw: list[np.ndarray] = []
    prior_labels: list[np.ndarray] = []
    prior_frames: list[pd.DataFrame] = []
    gam_features = [("fast_minus_last_official", True), ("recent_slope", True), ("reheat_strength", True), ("pullback_depth", False)]
    for fold_id, (train_dates, test_dates) in enumerate(folds):
        train = development.loc[development["target_date"].isin(train_dates)].copy()
        test = development.loc[development["target_date"].isin(test_dates)].copy()
        weights = fit_dependency_weights(train)
        m1 = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(train, weights)
        m2 = MonotonicAdditiveModel(gam_features, SUPPORT).fit(train, weights)
        m1_raw = m1.predict_pmf(test)
        temperature = (
            fit_temperature_dependency_weighted(
                np.vstack(prior_raw), np.concatenate(prior_labels),
                fit_dependency_weights(pd.concat(prior_frames, ignore_index=True)),
            ) if prior_raw else 1.0
        )
        probabilities = {"B2": _b2_pmf(train, test), "M1": temperature_scale(m1_raw, temperature), "M2": m2.predict_pmf(test)}
        oof_parts.append(_prediction_bundle(test, probabilities, fold_id))
        prior_raw.append(m1_raw)
        prior_labels.append(test["next_official_delta_native_tick"].to_numpy(int))
        prior_frames.append(test[["target_date", "official_print_group_id", "decision_vintage_id"]].copy())
        fold_manifest.append({
            "fold": fold_id, "train_start": train_dates[0], "train_end": train_dates[-1],
            "test_start": test_dates[0], "test_end": test_dates[-1],
            "train_dates": len(train_dates), "test_dates": len(test_dates),
            "train_rows": len(train), "test_rows": len(test), "m1_temperature": temperature,
        })
    oof = pd.concat(oof_parts, ignore_index=True)
    temperature = fit_temperature_dependency_weighted(
        np.vstack(prior_raw), np.concatenate(prior_labels),
        fit_dependency_weights(pd.concat(prior_frames, ignore_index=True)),
    )
    weights = fit_dependency_weights(development)
    m1 = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(development, weights)
    m2 = MonotonicAdditiveModel(gam_features, SUPPORT).fit(development, weights)
    outer_predictions = _prediction_bundle(
        outer,
        {"B2": _b2_pmf(development, outer), "M1": temperature_scale(m1.predict_pmf(outer), temperature), "M2": m2.predict_pmf(outer)},
        "historical_outer",
    )
    captured_predictions = _prediction_bundle(
        captured,
        {"B2": _b2_pmf(development, captured), "M1": temperature_scale(m1.predict_pmf(captured), temperature), "M2": m2.predict_pmf(captured)},
        "captured_pit",
    )
    oof_frame = development.loc[development["decision_vintage_id"].isin(oof["decision_vintage_id"].unique())].copy()
    oof_frame = oof_frame.sort_values(["target_date", "observed_at", "decision_vintage_id"])
    oof = oof.sort_values(["target_date", "observed_at", "decision_vintage_id", "model_id"])
    full_summary, _ = _evaluate_prediction_bundle(oof_frame, oof)
    matched = oof_frame.loc[oof_frame["opportunity_matched"]].copy()
    matched_predictions = oof.loc[oof["decision_vintage_id"].isin(matched["decision_vintage_id"])].copy()
    matched_summary, _ = _evaluate_prediction_bundle(matched, matched_predictions)
    outer_summary, _ = _evaluate_prediction_bundle(outer, outer_predictions)
    captured_summary, _ = _evaluate_prediction_bundle(captured, captured_predictions)

    def disposition(summary: dict[str, Any]) -> str:
        comparisons = (summary["M1_minus_B2"], summary["M2_minus_B2"])
        if any(row["candidate_minus_baseline"] < 0 and row["ci95"][1] < 0 for row in comparisons):
            return "CHALLENGER_OUTPERFORMS_B2_ON_CAPTURED_DENOMINATOR"
        if all(row["candidate_minus_baseline"] > 0 and row["ci95"][0] > 0 for row in comparisons):
            return "B2_OUTPERFORMS_BOTH_CHALLENGERS_ON_CAPTURED_DENOMINATOR"
        return "INCONCLUSIVE_NO_PROMOTION"

    results = {
        "schema_version": f"wcir_{city.lower()}_model_comparison_rev1",
        "city": city,
        "feature_parity": "SHARED_FULL_PATH_BUILDER_CLOSED",
        "primary_metric": "target_date_equal_then_official_print_group_equal_RPS",
        "fit_weighting": "target_date_equal_then_official_print_group_equal; effective total weight equals unique official-print groups",
        "denominators": {
            "historical_expanding_oof_all_checkpoints": full_summary,
            "historical_expanding_oof_opportunity_matched": matched_summary,
            "historical_untouched_outer_20_dates": outer_summary,
            "captured_pit": captured_summary,
        },
        "captured_disposition": disposition(captured_summary),
        "fold_manifest": fold_manifest,
        "outer_dates": outer_dates,
        "models": {
            "B2": "city-native rounded-fast physical measurement residual baseline",
            "M1": "Amsterdam-rev2 transferable ordinal shared-path challenger",
            "M2": "preregistered monotonic additive shared-path challenger",
        },
        "live_or_shadow_authorized": False,
    }
    return oof, pd.concat([outer_predictions, captured_predictions], ignore_index=True), results


def load_stage2_ws() -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    aligned_path = ROOT / "reviews/wcir_next_print/stage_02/evidence/EVENT_ALIGNED_BOOK_ROWS_2026-08-26.jsonl.gz"
    truths_path = ROOT / "reviews/wcir_next_print/stage_02/evidence/FROZEN_EXECUTABLE_BOOK_TRUTHS_2026-08-26.jsonl.gz"
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(aligned_path):
        by_event[str(row.get("event_id"))].append(row)
    truths = {str(row.get("truth_id")): row for row in read_jsonl(truths_path)}
    return by_event, truths


def scan_rest_books(runtime: Path, events: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    tokens = set(events["token_id"].astype(str))
    by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dates = sorted(events["target_date"].astype(str).unique())
    for target_date in dates:
        for path in sorted((runtime / "market_books/batches" / target_date).glob("*.jsonl.gz")):
            for raw in read_jsonl(path):
                token = str(raw.get("token_id") or "")
                if token not in tokens or raw.get("status") != "ok":
                    continue
                row = dict(raw)
                row["_path"] = str(path)
                by_token[token].append(row)
    for rows in by_token.values():
        rows.sort(key=lambda row: str(row.get("available_at_utc") or row.get("response_received_at_utc") or ""))
    return by_token


def scan_fixed_full_ladder(runtime: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    event_map: dict[str, dict[str, Any]] = {}
    root = runtime / "output/source_event_full_ladder_v1"
    for row in read_jsonl(root / "events.jsonl"):
        event_map[str(row.get("information_event_id"))] = row
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for capture in read_jsonl(root / "captures.jsonl"):
        source = event_map.get(str(capture.get("source_event_id")))
        path = Path(str(capture.get("snapshot_path") or ""))
        if source is None or not path.is_file():
            continue
        key = (str(source.get("city")), str(source.get("source_event_ts_utc")))
        row = dict(capture)
        row["source_event"] = source
        row["snapshot"] = json.loads(path.read_text(encoding="utf-8"))
        output[key].append(row)
    return output


def scan_legacy_quotes(runtime: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    path = runtime / "output/source_event_ladder_repricing_shadow/quote_snapshots.jsonl"
    for row in read_jsonl(path):
        city = str(row.get("city") or "")
        if city not in CONTRACTS:
            continue
        output[(city, str(row.get("source_obs_ts_utc")))].append(row)
    return output


def _rest_time(row: Mapping[str, Any]) -> pd.Timestamp | None:
    return parse_ts(row.get("available_at_utc") or row.get("response_received_at_utc") or row.get("snapshot_ts_utc"))


def _exact_fixed_record(snapshot: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = [
        row for row in snapshot.get("records", [])
        if str(row.get("market_id")) == str(event.get("market_id"))
        and str(row.get("condition_id")) == str(event.get("condition_id"))
        and str(event.get("token_id")) in {str(row.get("yes_token_id")), str(row.get("no_token_id"))}
    ]
    return rows[0] if len(rows) == 1 else None


def _fixed_time(row: Mapping[str, Any]) -> pd.Timestamp | None:
    return parse_ts(row.get("ts_utc") or row.get("capture_started_at_utc"))


def _legacy_time(row: Mapping[str, Any]) -> pd.Timestamp | None:
    return parse_ts(
        row.get("available_at_utc")
        or row.get("captured_at_utc")
        or row.get("snapshot_ts_utc")
        or row.get("ts_utc")
    )


def reconcile_market(runtime: Path, events: pd.DataFrame, city: str) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    ws_by_event, truths = load_stage2_ws()
    rest = scan_rest_books(runtime, events)
    fixed = scan_fixed_full_ladder(runtime)
    legacy = scan_legacy_quotes(runtime)
    evidence_rows: list[dict[str, Any]] = []
    primary_raw_rows: list[dict[str, Any]] = []
    repricing_rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    for event in events.to_dict("records"):
        detect = pd.Timestamp(event["source_detect_ts_utc"])
        official = pd.Timestamp(event["official_first_seen_at_utc"])
        token = str(event["token_id"])
        ws_valid_rows: list[dict[str, Any]] = []
        for aligned in ws_by_event.get(str(event["event_id"]), []):
            truth = truths.get(str(aligned.get("book_truth_id")))
            checkpoint = parse_ts(aligned.get("checkpoint_at_utc"))
            if (
                aligned.get("book_valid") and truth
                and str(aligned.get("event_id")) == str(event["event_id"])
                and str(aligned.get("token_id")) == token
                and str(truth.get("token_id")) == token
                and checkpoint is not None
            ):
                ws_valid_rows.append({"aligned": aligned, "truth": truth, "checkpoint": checkpoint})
        rest_candidates = [
            row for row in rest.get(token, [])
            if str(row.get("market_id")) == str(event.get("market_id"))
            and str(row.get("condition_id")) == str(event.get("condition_id"))
            and (stamp := _rest_time(row)) is not None and detect <= stamp < official
        ]
        rest_t0 = min(rest_candidates, key=lambda row: abs((_rest_time(row) - detect).total_seconds())) if rest_candidates else None
        fixed_rows = fixed.get((city, pd.Timestamp(event["source_obs_ts_utc"]).isoformat()), [])
        exact_fixed = []
        for row in fixed_rows:
            source = row["source_event"]
            checkpoint = _fixed_time(row)
            source_first_seen = parse_ts(source.get("first_seen_at_utc") or source.get("available_at_utc"))
            if (
                _exact_fixed_record(row["snapshot"], event) is not None
                and str(source.get("city")) == city
                and parse_ts(source.get("source_event_ts_utc")) == pd.Timestamp(event["source_obs_ts_utc"])
                and source_first_seen == detect
                and checkpoint is not None and detect <= checkpoint < official
            ):
                exact_fixed.append(row)
        legacy_rows = []
        for row in legacy.get((city, pd.Timestamp(event["source_obs_ts_utc"]).isoformat()), []):
            quote = ((row.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {}
            checkpoint = _legacy_time(row)
            if (
                str(quote.get("market_id")) == str(event.get("market_id"))
                and str(quote.get("condition_id")) == str(event.get("condition_id"))
                and str(quote.get("token_id")) == token
                and checkpoint is not None and detect <= checkpoint < official
            ):
                legacy_rows.append(row)
        if ws_valid_rows:
            tier = "TIER_A_STRICT_RECONSTRUCTED_WS"
            reason = "strict_ws_event_token_checkpoint_market_fields_not_native"
            primary_checkpoint = min(row["checkpoint"] for row in ws_valid_rows)
            event_id_method = "archive_native_event_id_exact"
            market_condition_method = "not_archive_native_in_stage2_ws_rows"
            event_id_exact = True
            market_id_exact = any(
                str(row["aligned"].get("market_id") or row["truth"].get("market_id") or "")
                == str(event.get("market_id")) for row in ws_valid_rows
            )
            condition_id_exact = any(
                str(row["aligned"].get("condition_id") or row["truth"].get("condition_id") or "")
                == str(event.get("condition_id")) for row in ws_valid_rows
            )
        elif exact_fixed:
            tier = "TIER_B_FIXED_HORIZON_FULL_LADDER"
            reason = "fixed_horizon_market_token_checkpoint_exact_event_join_derived"
            primary_checkpoint = min(_fixed_time(row) for row in exact_fixed)
            event_id_method = "source_obs_and_first_seen_transport_join_exact"
            market_condition_method = "archive_native_market_condition_token_exact"
            event_id_exact = False
            market_id_exact = True
            condition_id_exact = True
        elif rest_t0 is not None:
            tier = "TIER_B_TIMESTAMPED_REST_FULL_LADDER"
            reason = "rest_market_token_checkpoint_exact_event_join_derived"
            primary_checkpoint = _rest_time(rest_t0)
            event_id_method = "derived_from_exact_market_token_and_causal_event_window"
            market_condition_method = "archive_native_market_condition_token_exact"
            event_id_exact = False
            market_id_exact = True
            condition_id_exact = True
        elif legacy_rows:
            tier = "TIER_C_LEGACY_SELECTED_TOP_OF_BOOK"
            reason = "legacy_market_token_checkpoint_exact_event_join_derived_quote_only"
            primary_checkpoint = min(_legacy_time(row) for row in legacy_rows)
            event_id_method = "derived_from_exact_market_token_and_causal_event_window"
            market_condition_method = "archive_native_market_condition_token_exact"
            event_id_exact = False
            market_id_exact = True
            condition_id_exact = True
        else:
            tier = "TIER_D_UNUSABLE"
            reason = "no_exact_identity_causal_book"
            primary_checkpoint = None
            event_id_method = "unmatched"
            market_condition_method = "unmatched"
            event_id_exact = False
            market_id_exact = False
            condition_id_exact = False
        identity = {
            "event_id_archive_exact": event_id_exact,
            "market_id_archive_exact": market_id_exact,
            "condition_id_archive_exact": condition_id_exact,
            "token_id_archive_exact": tier != "TIER_D_UNUSABLE" and bool(token),
            "checkpoint_timestamp_archive_exact": primary_checkpoint is not None,
        }
        tiers[tier] += 1
        reasons[reason] += 1
        primary_raw: dict[str, Any] = {
            "event_id": str(event["event_id"]), "city": city, "evidence_tier": tier,
            "primary_reason": reason, "event_identity": {
                "market_id": str(event.get("market_id")),
                "condition_id": str(event.get("condition_id")), "token_id": token,
            },
        }
        if ws_valid_rows:
            chosen = min(ws_valid_rows, key=lambda row: row["checkpoint"])
            primary_raw["archive_record"] = {"aligned": chosen["aligned"], "truth": chosen["truth"]}
        elif exact_fixed:
            chosen = min(exact_fixed, key=lambda row: _fixed_time(row))
            primary_raw["archive_record"] = {
                "capture": {key: value for key, value in chosen.items() if key not in {"snapshot", "source_event"}},
                "source_event": chosen["source_event"],
                "matching_ladder_record": _exact_fixed_record(chosen["snapshot"], event),
            }
        elif rest_t0 is not None:
            primary_raw["archive_record"] = rest_t0
        elif legacy_rows:
            primary_raw["archive_record"] = legacy_rows[0]
        else:
            primary_raw["archive_record"] = None
        primary_raw_rows.append(primary_raw)
        evidence_rows.append({
            "event_id": event["event_id"], "city": city, "target_date": event["target_date"],
            "source_obs_ts_utc": event["source_obs_ts_utc"], "source_detect_ts_utc": detect,
            "official_first_seen_at_utc": official, "market_id": str(event.get("market_id")),
            "condition_id": str(event.get("condition_id")), "token_id": token,
            "checkpoint_timestamp": primary_checkpoint,
            "checkpoint_temporal_relation": (
                "MISSING" if primary_checkpoint is None
                else "PRE_DETECTION" if primary_checkpoint < detect
                else "CAUSAL_PRE_OFFICIAL" if primary_checkpoint < official
                else "POST_OFFICIAL_NAMED_CHECKPOINT"
            ),
            "event_id_alignment_method": event_id_method,
            "market_condition_alignment_method": market_condition_method,
            **identity,
            "full_archive_native_identity_reconciled": all(identity.values()),
            "evidence_tier": tier, "primary_reason": reason, "strict_ws_valid_rows": len(ws_valid_rows),
            "fixed_horizon_exact_rows": len(exact_fixed), "rest_causal_exact_rows": len(rest_candidates),
            "legacy_exact_rows": len(legacy_rows),
            "eligibility_class": "RESEARCH_INELIGIBLE" if tier == "TIER_D_UNUSABLE" else "WEATHER_SCORE_ELIGIBLE_MARKET_DIAGNOSTIC_ONLY",
        })
        for horizon in (15, 30, 60, 120, 300):
            target = detect + pd.Timedelta(seconds=horizon)
            later = [
                row for row in rest.get(token, [])
                if str(row.get("market_id")) == str(event.get("market_id"))
                and str(row.get("condition_id")) == str(event.get("condition_id"))
                and (stamp := _rest_time(row)) is not None and stamp >= target
                and abs((stamp - target).total_seconds()) <= 120
            ]
            later_row = min(later, key=lambda row: abs((_rest_time(row) - target).total_seconds())) if later else None
            entry_sweep = _sweep((rest_t0 or {}).get("raw", {}).get("asks", []), 1.0, "buy") if rest_t0 else {"fully_executable": False}
            exit_sweep = _sweep((later_row or {}).get("raw", {}).get("bids", []), 1.0, "sell") if later_row else {"fully_executable": False}
            eligible = bool(entry_sweep.get("fully_executable") and exit_sweep.get("fully_executable"))
            repricing_rows.append({
                "event_id": event["event_id"], "city": city, "target_date": event["target_date"],
                "market_id": str(event.get("market_id")), "condition_id": str(event.get("condition_id")), "token_id": token,
                "horizon_seconds": horizon, "entry_checkpoint_timestamp": None if rest_t0 is None else _rest_time(rest_t0),
                "exit_checkpoint_timestamp": None if later_row is None else _rest_time(later_row),
                "entry_1share_executable": bool(entry_sweep.get("fully_executable")),
                "exit_1share_executable": bool(exit_sweep.get("fully_executable")),
                "repricing_diagnostic_eligible": eligible,
                "counterfactual_net_markout_usd": (
                    float(exit_sweep["effective_value_usd"] - entry_sweep["effective_value_usd"]) if eligible else None
                ),
                "action_mapping_status": "NO_NEXT_PRINT_TO_EXACT_TOKEN_ACTION_MAPPING",
                "is_exact_token_fair_probability": False, "is_model_selector": False,
                "orders": 0, "fills": 0, "notional": 0,
            })
    evidence = pd.DataFrame(evidence_rows)
    repricing = pd.DataFrame(repricing_rows)
    summary = {
        "city": city, "events": len(evidence), "reason_histogram": dict(reasons),
        "evidence_tier_histogram": dict(tiers),
        "exact_identity_fields": ["event_id", "market_id", "condition_id", "token_id", "checkpoint_timestamp"],
        "identity_contract": {
            "event_id": "archive-native where present; otherwise exact source-event or market-token causal join with method recorded per row",
            "market_id_condition_id_token_id": "exact equality against the frozen opportunity identity",
            "checkpoint_timestamp": "archive-native timestamp retained and causally bounded for REST/fixed/legacy; named Stage-2 checkpoint retained for WS",
            "full_identity_reconciled": "all five archive-native component flags true; derived event joins are intentionally false",
        },
        "full_archive_native_identity_reconciled_events": int(evidence["full_archive_native_identity_reconciled"].sum()),
        "strict_ws_events": int(evidence["strict_ws_valid_rows"].gt(0).sum()),
        "fixed_horizon_full_ladder_events": int(evidence["fixed_horizon_exact_rows"].gt(0).sum()),
        "timestamped_rest_events": int(evidence["rest_causal_exact_rows"].gt(0).sum()),
        "legacy_quote_events": int(evidence["legacy_exact_rows"].gt(0).sum()),
        "repricing_eligible_by_horizon": [
            {"horizon_seconds": int(h), "rows": int(group["repricing_diagnostic_eligible"].sum()),
             "net_markout_usd": None if not group["repricing_diagnostic_eligible"].any() else float(group["counterfactual_net_markout_usd"].sum())}
            for h, group in repricing.groupby("horizon_seconds")
        ],
        "proper_score_only": True,
        "reason": "No preregistered next-print outcome to exact settlement-token action mapping; market rows are repricing diagnostics only.",
        "orders": 0, "fills": 0, "notional": 0,
    }
    return evidence, repricing, primary_raw_rows, summary


def seal_manifest(output: Path) -> None:
    entries = [
        {"path": str(path.relative_to(output)), "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json" and not path.name.endswith((".zip", ".zip.sha256"))
    ]
    write_json(output / "EVIDENCE_MANIFEST.json", {
        "schema_version": f"{GENERATED_SCHEMA}_evidence_manifest", "strict_entry_set": True,
        "generated_at_utc": utc_now(), "entries": entries,
    })


def verify_manifest(output: Path) -> None:
    manifest = json.loads((output / "EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = {
        str(path.relative_to(output)): path for path in output.rglob("*")
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json" and not path.name.endswith((".zip", ".zip.sha256"))
    }
    if set(expected) != set(actual):
        raise RuntimeError(f"manifest entry-set drift missing={sorted(set(expected)-set(actual))} extra={sorted(set(actual)-set(expected))}")
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"manifest hash drift: {relative}")


def compact_member_paths(output: Path) -> list[Path]:
    excluded_prefixes = ("inputs/", "evidence/ROW_LEVEL_")
    return [
        item for item in sorted(output.rglob("*"))
        if item.is_file()
        and item.name not in {"EVIDENCE_MANIFEST.json", "COMPACT_EVIDENCE_MANIFEST.json"}
        and not item.name.endswith((".zip", ".zip.sha256"))
        and not str(item.relative_to(output)).startswith(excluded_prefixes)
    ]


def seal_compact_manifest(output: Path) -> None:
    entries = [
        {"path": str(path.relative_to(output)), "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in compact_member_paths(output)
    ]
    write_json(output / "COMPACT_EVIDENCE_MANIFEST.json", {
        "schema_version": f"{GENERATED_SCHEMA}_compact_evidence_manifest",
        "strict_entry_set": True,
        "package_role": "review packet; strict identities and summaries are offline-verifiable, row-level score recomputation belongs to the full evidence seal",
        "excluded_classes": ["frozen raw inputs", "row-level historical/captured/prediction panels"],
        "entries": entries,
    })


def package(output: Path, compact: bool) -> tuple[Path, str]:
    import zipfile
    suffix = "compact" if compact else "full-evidence-seal"
    path = output / f"wcir-helsinki-tokyo-rev1-{suffix}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        members = compact_member_paths(output) + [output / "COMPACT_EVIDENCE_MANIFEST.json"] if compact else sorted(output.rglob("*"))
        for item in members:
            relative = str(item.relative_to(output))
            if not item.is_file() or item == path or item.name.endswith((".zip", ".zip.sha256")):
                continue
            archive.write(item, relative)
    digest = sha256(path)
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return path, digest


def write_contracts(output: Path, summaries: dict[str, Any], generated: str) -> None:
    contracts = {
        city: {
            "contract_id": f"wcir_{city.lower()}_next_print_rev1",
            "fast_source": contract.source_basis,
            "prediction_target_source": f"canonical {contract.official_station} routine METAR at minutes {list(contract.routine_minutes)}",
            "settlement_source": contract.settlement_source,
            "station_identity": {"fast": contract.fast_station, "official": contract.official_station, "settlement": contract.official_station},
            "raw_unit": "degC point observation",
            "settlement_native_lattice": "1 degree Celsius; half-up only at declared mapping boundary",
            "pit_contract": "source observed_at and available_at retained; decision feature rows require available_at <= decision_ready and source_detect <= decision_ready < official first_seen",
            "feature_path_start_local": "05:50 inclusive; historical and captured use the same preregistered city-local path domain",
            "matching_contract": "first routine official report after prior official state, first-seen after source detection, maximum 1200 seconds",
            "fast_source_is_settlement_truth": False,
        }
        for city, contract in CONTRACTS.items()
    }
    write_json(output / "CITY_SOURCE_TARGET_SETTLEMENT_CONTRACTS.json", contracts)
    lines = ["# WCIR Helsinki + Tokyo GPT Pro review packet", "", f"Generated: `{generated}`.", "", "Research-only; canonical exact-namespace signals/plans/orders/fills/posted-notional reconcile to `0/0/0/0/0`.", ""]
    for city in ("Helsinki", "Tokyo"):
        row = summaries[city]
        lines += [
            f"## {city}", "",
            f"- Data: historical `{row['historical']['linked_rows']}` rows / `{row['historical']['linked_dates']}` dates; captured eligible `{row['captured']['linked_and_feature_eligible']}` / raw `{row['captured']['raw_opportunities']}` events.",
            f"- Feature parity: `{row['models']['feature_parity']}`.",
            f"- Captured verdict: `{row['models']['captured_disposition']}`.",
            f"- Executable replay: proper-score primary; book evidence tiers `{row['market']['evidence_tier_histogram']}` with full archive-native five-field identity `{row['market']['full_archive_native_identity_reconciled_events']}`; no action mapping, so repricing is diagnostic only.",
            f"- Frozen collection status: `{row['frozen_collection_status']}`.", "",
        ]
    lines += [
        "## Review focus", "",
        "Verify city contracts, fixed denominators, row-level PMFs, target-date/official-print dependency weighting, feature parity audit, exact identity reasons, and package manifest. No Amsterdam fact or conclusion is reused as Helsinki/Tokyo truth.", "",
    ]
    (output / "GPT_PRO_REVIEW_PACKET_HELSINKI_TOKYO_REV1.md").write_text("\n".join(lines), encoding="utf-8")


def reconcile_execution_ledger(db_path: Path, captured_panels: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    resolved = db_path.resolve()
    stat = resolved.stat()
    sql = """
        WITH scoped_signals AS (
            SELECT signal_id FROM signals WHERE producer_system = ? OR model_version = ?
        ), scoped_plans AS (
            SELECT p.* FROM plans p JOIN scoped_signals s USING(signal_id)
        ), scoped_orders AS (
            SELECT o.* FROM orders o JOIN scoped_plans p USING(plan_id)
        ), scoped_fills AS (
            SELECT f.* FROM fills f JOIN scoped_orders o USING(execution_id)
        )
        SELECT
            (SELECT COUNT(*) FROM scoped_signals),
            (SELECT COUNT(*) FROM scoped_plans),
            (SELECT COUNT(*) FROM scoped_orders),
            (SELECT COUNT(*) FROM scoped_fills),
            (SELECT COALESCE(SUM(COALESCE(posted_notional, notional, cost_usd, 0)), 0) FROM scoped_orders),
            (SELECT COALESCE(SUM(filled_shares * filled_price + COALESCE(fees_usd, 0)), 0) FROM scoped_fills)
    """
    connection = sqlite3.connect(f"file:{resolved}?mode=ro&immutable=1", uri=True, timeout=5.0)
    try:
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        row = connection.execute(sql, (RESEARCH_NAMESPACE, RESEARCH_NAMESPACE)).fetchone()
        max_order = connection.execute("SELECT MAX(created_at_utc) FROM orders").fetchone()[0]
        max_fill = connection.execute("SELECT MAX(created_at_utc) FROM fills").fetchone()[0]
    finally:
        connection.close()
    planned = {
        city: {
            "eligible_rows_with_planned_notional_field": int(panel["planned_notional_usd"].notna().sum()) if "planned_notional_usd" in panel else 0,
            "opportunity_planned_notional_usd": float(panel["planned_notional_usd"].fillna(0).sum()) if "planned_notional_usd" in panel else 0.0,
            "classification": "upstream opportunity metadata, not an order or fill from this research runner",
        }
        for city, panel in captured_panels.items()
    }
    return {
        "scope": "exact research namespace in canonical signals→plans→orders→fills ledger",
        "research_namespace": RESEARCH_NAMESPACE,
        "query_sql_sha256": hashlib.sha256(" ".join(sql.split()).encode()).hexdigest(),
        "canonical_db_identity": {
            "resolved_path": str(resolved), "device": stat.st_dev, "inode": stat.st_ino,
            "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sqlite_schema_version": schema_version,
            "max_order_created_at_utc": max_order, "max_fill_created_at_utc": max_fill,
        },
        "counts": {
            "signals": int(row[0]), "plans": int(row[1]), "orders": int(row[2]), "fills": int(row[3]),
            "posted_notional_usd": float(row[4]), "fill_cost_usd": float(row[5]),
        },
        "opportunity_metadata_not_execution": planned,
        "reconciled_at_utc": utc_now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--canonical-db", type=Path, default=DEFAULT_CANONICAL_DB)
    parser.add_argument("--helsinki-fast", type=Path)
    parser.add_argument("--helsinki-official", type=Path)
    parser.add_argument("--prepare-inputs-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"refuse to overwrite non-empty review directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / "inputs"
    evidence = output / "evidence"
    inputs.mkdir()
    evidence.mkdir()

    helsinki_fast = inputs / f"helsinki_fmi_10m_{START_HISTORICAL}_{END_HISTORICAL}.csv.gz"
    helsinki_official = inputs / f"helsinki_efhk_routine_{START_HISTORICAL}_{END_HISTORICAL}.csv.gz"
    if args.helsinki_fast:
        shutil.copy2(args.helsinki_fast, helsinki_fast)
    else:
        fetch_helsinki_fast(helsinki_fast, START_HISTORICAL, END_HISTORICAL)
    if args.helsinki_official:
        shutil.copy2(args.helsinki_official, helsinki_official)
    else:
        fetch_iem_station(helsinki_official, "EFHK", START_HISTORICAL, END_HISTORICAL)
    if args.prepare_inputs_only:
        write_json(output / "PREPARED_INPUT_IDENTITIES.json", {
            "helsinki_fast": identity(helsinki_fast), "helsinki_official": identity(helsinki_official)
        })
        return 0

    tokyo_fast_source = TOKYO_ARTIFACT / f"jma_haneda_10m_{START_HISTORICAL}_{END_HISTORICAL}.csv"
    tokyo_fast = inputs / tokyo_fast_source.name
    shutil.copy2(tokyo_fast_source, tokyo_fast)
    tokyo_official_sources = sorted((TOKYO_ARTIFACT / "metar_cache").glob("*.csv"))
    tokyo_official_dir = inputs / "tokyo_metar_cache"
    tokyo_official_dir.mkdir()
    for path in tokyo_official_sources:
        shutil.copy2(path, tokyo_official_dir / path.name)

    historical_inputs = {
        "Helsinki": (helsinki_fast, [helsinki_official]),
        "Tokyo": (tokyo_fast, sorted(tokyo_official_dir.glob("*.csv"))),
    }
    generated = utc_now()
    summaries: dict[str, Any] = {}
    external_identities: dict[str, Any] = {}
    repair_summaries: dict[str, Any] = {}
    captured_panels: dict[str, pd.DataFrame] = {}
    for city, contract in CONTRACTS.items():
        fast_path, official_paths = historical_inputs[city]
        fast = load_fast_csv(fast_path, contract)
        official = load_iem(official_paths[0], contract) if city == "Helsinki" else load_tokyo_official(official_paths, contract)
        historical, historical_audit = build_historical_panel(fast, official, contract)
        legacy_historical, _ = build_historical_panel(
            fast, official, contract, complete_path_before_label_filter=False
        )
        path_features = list(NextPrintFeatureBuilderV1.path_features)
        feature_diff = historical[["decision_vintage_id", "target_date", "observed_at", *path_features]].merge(
            legacy_historical[["decision_vintage_id", *path_features]],
            on="decision_vintage_id", how="inner", suffixes=("_fixed", "_legacy_filtered"), validate="one_to_one",
        )
        changed_columns: list[str] = []
        feature_changed_counts: dict[str, int] = {}
        for feature_name in path_features:
            fixed_values = feature_diff[f"{feature_name}_fixed"]
            legacy_values = feature_diff[f"{feature_name}_legacy_filtered"]
            changed = ~(fixed_values.eq(legacy_values) | (fixed_values.isna() & legacy_values.isna()))
            column = f"{feature_name}_changed"
            feature_diff[column] = changed
            changed_columns.append(column)
            feature_changed_counts[feature_name] = int(changed.sum())
        feature_diff["any_path_feature_changed"] = feature_diff[changed_columns].any(axis=1)
        feature_diff = feature_diff.loc[feature_diff["any_path_feature_changed"]].copy()
        raw_path = load_captured_raw(args.runtime_root, contract)
        captured_official = load_captured_official(args.runtime_root, contract)
        opportunities = load_opportunities(args.runtime_root, contract)
        captured_fast_path = inputs / f"captured_fast_path_{city.lower()}.jsonl.gz"
        captured_official_path = inputs / f"captured_official_prints_{city.lower()}.jsonl.gz"
        captured_opportunity_path = inputs / f"captured_opportunities_{city.lower()}.jsonl.gz"
        write_jsonl_gz(captured_fast_path, raw_path.to_dict("records"))
        write_jsonl_gz(captured_official_path, captured_official.to_dict("records"))
        write_jsonl_gz(captured_opportunity_path, opportunities.to_dict("records"))
        captured, parity, lineage, captured_audit = build_captured_panel(
            opportunities, raw_path, captured_official, contract
        )
        if len(captured) == 0:
            raise RuntimeError(f"{city} has zero captured feature-eligible rows")
        captured_panels[city] = captured.copy()
        oof, heldout_predictions, model_results = train_city_models(historical, captured, city)
        eligible_events = captured.copy()
        market_rows, repricing, market_raw, market_summary = reconcile_market(args.runtime_root, eligible_events, city)

        historical.to_csv(evidence / f"ROW_LEVEL_HISTORICAL_PANEL_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        captured.to_csv(evidence / f"ROW_LEVEL_CAPTURED_PANEL_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        parity.to_csv(evidence / f"FEATURE_PARITY_ROW_AUDIT_{city.upper()}.csv", index=False)
        parity.loc[parity["clock_contract_repair_changed_status"]].to_csv(
            evidence / f"CAPTURED_CLOCK_REPAIR_ROW_DIFF_{city.upper()}.csv", index=False
        )
        feature_diff.to_csv(
            evidence / f"HISTORICAL_FEATURE_REPAIR_ROW_DIFF_{city.upper()}.csv.gz",
            index=False, compression={"method": "gzip", "mtime": 0},
        )
        lineage.to_csv(evidence / f"FEATURE_LINEAGE_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        oof.to_csv(evidence / f"ROW_LEVEL_OOF_PREDICTIONS_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        heldout_predictions.to_csv(evidence / f"ROW_LEVEL_HELDOUT_AND_CAPTURED_PREDICTIONS_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        market_rows.to_csv(evidence / f"MARKET_IDENTITY_RECONCILIATION_{city.upper()}.csv", index=False)
        repricing.to_csv(evidence / f"MARKET_REPRICING_DIAGNOSTIC_{city.upper()}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        write_jsonl_gz(evidence / f"MARKET_PRIMARY_RAW_EVIDENCE_{city.upper()}.jsonl.gz", market_raw)
        write_json(output / f"MODEL_COMPARISON_{city.upper()}.json", model_results)
        write_json(output / f"MARKET_ARCHIVE_RECONCILIATION_{city.upper()}.json", market_summary)
        write_json(output / f"FEATURE_PARITY_AUDIT_{city.upper()}.json", captured_audit)
        write_json(output / f"DATA_COVERAGE_AND_LINEAGE_{city.upper()}.json", historical_audit)
        summaries[city] = {
            "historical": historical_audit, "captured": captured_audit,
            "models": model_results, "market": market_summary,
            "data_sufficient": historical_audit["linked_dates"] > 365 and captured_audit["linked_and_feature_eligible"] > 0,
            "frozen_collection_status": (
                "WORTH_CONTINUING_ZERO_NOTIONAL_FROZEN_COLLECTION"
                if captured_audit["eligible_dates"] < 20 or model_results["captured_disposition"] == "INCONCLUSIVE_NO_PROMOTION"
                else "ENOUGH_DATES_FOR_NEXT_INDEPENDENT_FROZEN_REVIEW_NOT_DEPLOYMENT"
            ),
        }
        external_identities[city] = {
            "historical_fast": identity(fast_path, rows=len(fast), sealed_root=output),
            "historical_official": [identity(path, sealed_root=output) for path in official_paths],
            "captured_runtime_sources": {
                "raw_path": identity(captured_fast_path, rows=len(raw_path), sealed_root=output),
                "official_prints": identity(captured_official_path, rows=len(captured_official), sealed_root=output),
                "opportunities": identity(captured_opportunity_path, rows=len(opportunities), sealed_root=output),
            },
        }
        repair_summaries[city] = {
            "historical_feature_path_repair": {
                "window": [historical["target_date"].min(), historical["target_date"].max()],
                "eligible_rows": len(historical),
                "affected_rows": len(feature_diff),
                "feature_changed_counts": feature_changed_counts,
                "row_evidence": f"evidence/HISTORICAL_FEATURE_REPAIR_ROW_DIFF_{city.upper()}.csv.gz",
                "counterfactual": "legacy label-window-filtered path versus fixed complete-day path before label filtering",
            },
            "captured_1200_second_clock_repair": {
                "window": [CAPTURE_START, CAPTURE_END],
                "affected_rows": int(parity["clock_contract_repair_changed_status"].sum()),
                "restored_eligible_rows": int(
                    (
                        parity["eligibility_status"].eq("OK")
                        & parity["legacy_first_seen_clock_eligibility_status"].ne("OK")
                    ).sum()
                ),
                "row_evidence": f"evidence/CAPTURED_CLOCK_REPAIR_ROW_DIFF_{city.upper()}.csv",
                "fixed_contract": "official report timestamp minus fast observation timestamp in (0, 1200] seconds",
                "legacy_counterfactual": "official first-seen timestamp minus source detection timestamp <= 1200 seconds",
            },
        }

    write_json(output / "DUAL_CITY_STATUS.json", summaries)
    write_json(output / "FROZEN_INPUT_MANIFEST.json", external_identities)
    write_json(output / "DATA_FEATURE_REPAIR_IMPACT.json", {
        "repairs": repair_summaries,
        "helsinki_historical_reconstruction": "FMI WFS + EFHK IEM mirror reconstructed because prior row artifacts were absent",
        "impact_window": [START_HISTORICAL, CAPTURE_END],
        "counterfactual_scope": "row-level diffs cover the complete-path feature fix and the captured 1200-second clock fix; newly reconstructed Helsinki historical rows remain listed in the full panel",
        "decision_effect": "research evidence only; no production decisions/config/orders changed",
        "classification_contract": {
            "POLICY_ABSTAIN": "complete evidence and policy chooses no action",
            "DATA_FAIL_CLOSED": "required PIT path/clock missing or incomplete",
            "RESEARCH_INELIGIBLE": "row may be physically valid but violates matching/market evidence research contract",
        },
    })
    execution_reconciliation = reconcile_execution_ledger(args.canonical_db, captured_panels)
    write_json(output / "EXECUTION_LEDGER_RECONCILIATION.json", execution_reconciliation)
    write_json(output / "ZERO_NOTIONAL_AUDIT.json", {
        "scope": execution_reconciliation["scope"],
        **execution_reconciliation["counts"],
        "production_config_changed": False, "collector_changed": False,
        "selector_or_threshold_changed": False, "position_policy_changed": False,
        "order_path_changed": False, "live_or_shadow_behavior_changed": False,
        "frozen_forward_started": False,
        "evidence": "EXECUTION_LEDGER_RECONCILIATION.json",
        "runner_execution_surface": "none; research runner imports no order submission path and writes only its review directory",
        "output_boundary": str(output),
    })
    write_contracts(output, summaries, generated)
    source_snapshot = evidence / "code_snapshot"
    source_snapshot.mkdir()
    for path in (
        Path(__file__),
        ROOT / "weather_modeling/next_print_feature_builder_v1.py",
        ROOT / "scripts/analysis/forecast_quality/wcir_amsterdam_pilot_rev2.py",
        ROOT / "scripts/analysis/forecast_quality/wcir_unified_amsterdam_pilot.py",
        ROOT / "scripts/analysis/forecast_quality/verify_wcir_next_print_helsinki_tokyo_rev1.py",
        ROOT / "tests/research_tests/test_wcir_next_print_helsinki_tokyo_rev1.py",
    ):
        shutil.copy2(path, source_snapshot / path.name)
    write_json(output / "PACKAGE_IDENTITIES.json", {
        "full": {"path": "wcir-helsinki-tokyo-rev1-full-evidence-seal.zip", "sha256_sidecar": "wcir-helsinki-tokyo-rev1-full-evidence-seal.zip.sha256"},
        "compact": {"path": "wcir-helsinki-tokyo-rev1-compact.zip", "sha256_sidecar": "wcir-helsinki-tokyo-rev1-compact.zip.sha256"},
    })
    seal_compact_manifest(output)
    seal_manifest(output)
    verify_manifest(output)
    full_zip, full_sha = package(output, compact=False)
    compact_zip, compact_sha = package(output, compact=True)
    print(json.dumps({
        "output": str(output),
        "cities": {
            city: {
                "historical_rows": row["historical"]["linked_rows"],
                "captured_rows": row["captured"]["linked_and_feature_eligible"],
                "verdict": row["models"]["captured_disposition"],
                "market_tiers": row["market"]["evidence_tier_histogram"],
            }
            for city, row in summaries.items()
        },
        "full_sha256": full_sha, "compact_sha256": compact_sha,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
