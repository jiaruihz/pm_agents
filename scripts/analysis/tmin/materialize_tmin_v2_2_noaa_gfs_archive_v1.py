#!/usr/bin/env python3
"""Research-only NOAA GFS 0.25° PIT archive for Tmin V2.2.

This is deliberately an adapter, not a canonical-data materializer.  It
reads an immutable IEM observation export for realized paths and obtains only
``TMP:2 m above ground`` messages from NOAA's public GFS object archive.  A
candidate cycle is eligible only when *every* object used by the remaining
local-day path has an S3 ``Last-Modified`` timestamp no later than the
checkpoint.  That timestamp is an auditable, conservative provider-object
publication upper bound; it is never replaced by retrieval time.

The resulting rows are research evidence only.  In particular IEM report
times are not silently promoted to a production available-at clock.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import ssl
import sys
import tempfile
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd


MODEL_ID = "NOAA_GFS_0P25_EXACT_RUN_PIT_ARCHIVE_V1"
BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
CITY = {
    "Seoul": {"station": "RKSI", "latitude": 37.4691, "longitude": 126.4505, "timezone": "Asia/Seoul"},
    "Tokyo": {"station": "RJTT", "latitude": 35.5494, "longitude": 139.7798, "timezone": "Asia/Tokyo"},
}
CHECKPOINT_HOURS = (6, 9)
IDX_TMP_2M = re.compile(r":TMP:2 m above ground:")


def utc(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if not isinstance(value, pd.Timestamp) else value.tz_convert("UTC") if value.tzinfo else value.tz_localize("UTC")


def native_rung(value: float) -> int:
    """Integer-lattice rounding, halves away from zero."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def next_colder_boundary(rung: int) -> float:
    return float(rung) - 0.5


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def gfs_prefix(issue: pd.Timestamp) -> str:
    return f"gfs.{issue:%Y%m%d}/{issue:%H}/atmos/"


def gfs_key(issue: pd.Timestamp, lead_hour: int) -> str:
    return f"{gfs_prefix(issue)}gfs.t{issue:%H}z.pgrb2.0p25.f{lead_hour:03d}"


def object_url(key: str) -> str:
    return f"{BUCKET}/{key}"


class ObjectMeta:
    """Small dependency-free S3 object record (also importlib-fixture friendly)."""

    __slots__ = ("key", "last_modified", "size", "etag")

    def __init__(
        self, key: str, last_modified: pd.Timestamp, size: int | None = None, etag: str | None = None
    ) -> None:
        self.key = key
        self.last_modified = last_modified
        self.size = size
        self.etag = etag


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 6,
) -> bytes:
    """Fetch an immutable public object with bounded transient-error retries."""
    request_headers = {"User-Agent": "pm-agents-tmin-research-audit/1.0", **(headers or {})}
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if "Range" in request_headers and getattr(response, "status", None) != 206:
                    raise IOError(
                        f"range request returned HTTP {getattr(response, 'status', None)} for {url}"
                    )
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time_module.sleep(min(0.5 * (2**attempt), 8.0))
    assert last_error is not None
    raise last_error


def list_cycle_objects(issue: pd.Timestamp, fetch: Callable[..., bytes] = http_get) -> dict[str, ObjectMeta]:
    """Read one ListObjectsV2 page, avoiding a HEAD per GFS lead.

    GFS 0p25 atmospheric cycles are currently below 1000 keys.  We still
    honour a continuation token if NOAA grows that listing in the future.
    """
    prefix = gfs_prefix(issue)
    token: str | None = None
    result: dict[str, ObjectMeta] = {}
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        payload = fetch(f"{BUCKET}/?{urllib.parse.urlencode(params)}")
        root = ET.fromstring(payload)
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag.split("}", 1)[0] + "}"
        for content in root.findall(f"{namespace}Contents"):
            key = content.findtext(f"{namespace}Key")
            modified = content.findtext(f"{namespace}LastModified")
            if not key or not modified:
                continue
            result[key] = ObjectMeta(
                key=key,
                last_modified=utc(modified),
                size=int(content.findtext(f"{namespace}Size") or 0),
                etag=(content.findtext(f"{namespace}ETag") or "").strip('"') or None,
            )
        truncated = (root.findtext(f"{namespace}IsTruncated") or "false").lower() == "true"
        token = root.findtext(f"{namespace}NextContinuationToken")
        if not truncated:
            return result
        if not token:
            raise RuntimeError("S3 list response was truncated without continuation token")


def local_checkpoint(city: str, target_date: str, hour: int) -> pd.Timestamp:
    return pd.Timestamp(f"{target_date} {hour:02d}:00", tz=CITY[city]["timezone"]).tz_convert("UTC")


def remaining_local_hours(city: str, target_date: str, checkpoint_hour: int) -> list[pd.Timestamp]:
    start = pd.Timestamp(f"{target_date} {checkpoint_hour:02d}:00", tz=CITY[city]["timezone"])
    return [start + pd.Timedelta(hours=i) for i in range(24 - checkpoint_hour)]


def candidate_cycles(checkpoint: pd.Timestamp, *, max_lookback_hours: int = 36) -> list[pd.Timestamp]:
    checkpoint = utc(checkpoint)
    first = checkpoint.floor("h")
    cycles: list[pd.Timestamp] = []
    for offset in range(0, max_lookback_hours + 1):
        candidate = first - pd.Timedelta(hours=offset)
        if candidate.hour in {0, 6, 12, 18} and candidate < checkpoint:
            cycles.append(candidate)
    return cycles


def cycle_required_keys(issue: pd.Timestamp, valid_hours: Iterable[pd.Timestamp]) -> dict[pd.Timestamp, str]:
    keys: dict[pd.Timestamp, str] = {}
    for valid in valid_hours:
        valid_utc = utc(valid)
        delta = (valid_utc - issue).total_seconds() / 3600
        if delta < 0 or not float(delta).is_integer():
            raise ValueError(f"GFS target hour cannot be represented by cycle: {valid_utc} from {issue}")
        keys[valid_utc] = gfs_key(issue, int(delta))
    return keys


def select_latest_cycle(
    checkpoint: pd.Timestamp,
    valid_hours: Iterable[pd.Timestamp],
    cycle_inventory: dict[pd.Timestamp, dict[str, ObjectMeta]],
) -> tuple[pd.Timestamp, dict[pd.Timestamp, ObjectMeta]] | None:
    """Choose newest cycle with complete conservative object availability."""
    for issue in candidate_cycles(checkpoint):
        required = cycle_required_keys(issue, valid_hours)
        inventory = cycle_inventory.get(issue, {})
        metas = {valid: inventory.get(key) for valid, key in required.items()}
        if any(meta is None for meta in metas.values()):
            continue
        assert all(meta is not None for meta in metas.values())
        typed = {valid: meta for valid, meta in metas.items() if meta is not None}
        if all(meta.last_modified <= checkpoint for meta in typed.values()):
            return issue, typed
    return None


def parse_idx_tmp_message(payload: bytes, object_size: int | None) -> tuple[int, int | None]:
    """Return byte range for the unique TMP 2m message in a GFS index."""
    entries: list[tuple[int, str]] = []
    for raw_line in payload.decode("utf-8", errors="replace").splitlines():
        fields = raw_line.split(":")
        if len(fields) < 5 or not fields[1].isdigit():
            continue
        if IDX_TMP_2M.search(raw_line):
            entries.append((int(fields[1]), raw_line))
    if len(entries) != 1:
        raise LookupError(f"expected exactly one TMP 2m index entry, got {len(entries)}")
    start, line = entries[0]
    all_offsets = [int(line.split(":", 2)[1]) for line in payload.decode("utf-8", errors="replace").splitlines() if len(line.split(":", 2)) >= 2 and line.split(":", 2)[1].isdigit()]
    later = sorted(offset for offset in all_offsets if offset > start)
    end = (later[0] - 1) if later else (object_size - 1 if object_size else None)
    return start, end


def decode_nearest_tmp_celsius(grib: bytes, latitude: float, longitude: float) -> float:
    """Decode a single GRIB2 message through eccodes, returning Celsius."""
    try:
        from eccodes import codes_get, codes_grib_find_nearest, codes_new_from_message, codes_release
    except ImportError as exc:  # pragma: no cover - depends on runtime wheel
        raise RuntimeError("eccodes is required to decode GFS GRIB2 messages") from exc
    message = codes_new_from_message(grib)
    if message is None:
        raise ValueError("empty/non-GRIB TMP message")
    try:
        nearest = codes_grib_find_nearest(message, latitude, longitude, is_lsm=False)
        kelvin = float(nearest[0]["value"])
        units = str(codes_get(message, "units"))
        if units != "K":
            raise ValueError(f"unexpected GFS TMP unit {units!r}")
        return kelvin - 273.15
    finally:
        codes_release(message)


def parse_iem_truth(path: Path) -> pd.DataFrame:
    """Normalize common IEM ASOS CSV columns into city/event/temperature-C."""
    frame = pd.read_csv(path, comment="#", low_memory=False)
    normalized = {str(column).lower(): column for column in frame.columns}
    station_col = next((normalized.get(name) for name in ("station", "icao", "station_id")), None)
    time_col = next((normalized.get(name) for name in ("valid", "event_time", "observation_event_time", "time")), None)
    temp_c_col = next((normalized.get(name) for name in ("tmpc", "temperature_c", "temp_c")), None)
    temp_f_col = next((normalized.get(name) for name in ("tmpf", "temperature_f", "temp_f")), None)
    if not time_col or (not temp_c_col and not temp_f_col):
        raise ValueError("IEM CSV requires valid/event time and tmpc or tmpf")
    output = pd.DataFrame()
    if station_col:
        station = frame[station_col].astype(str).str.upper()
        output["city"] = station.map({value["station"]: name for name, value in CITY.items()})
    elif "city" in normalized:
        output["city"] = frame[normalized["city"]]
    else:
        raise ValueError("IEM CSV requires station/icao or city")
    output["observation_event_time"] = pd.to_datetime(frame[time_col], utc=True, errors="coerce")
    values = pd.to_numeric(frame[temp_c_col], errors="coerce") if temp_c_col else (pd.to_numeric(frame[temp_f_col], errors="coerce") - 32.0) * 5.0 / 9.0
    output["temperature_c"] = values
    output = output.loc[output["city"].isin(CITY) & output["observation_event_time"].notna() & output["temperature_c"].notna()].copy()
    output["target_date"] = [ts.tz_convert(CITY[city]["timezone"]).date().isoformat() for city, ts in zip(output["city"], output["observation_event_time"], strict=True)]
    return output.sort_values(["city", "observation_event_time", "temperature_c"], kind="mergesort").reset_index(drop=True)


def load_frozen_pit_observations(root: Path | None) -> pd.DataFrame:
    """Load optional frozen collector records without treating them as IEM truth.

    The V2.2 package writes per-city JSONL records with ``last_obs_utc``,
    ``available_at_utc`` and ``running_min_c``.  We only use a record if both
    clocks predate the checkpoint; ties are broken by canonical JSON so future
    appends and duplicate order cannot alter a historical row.
    """
    if root is None:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            rows.append({
                "city": raw.get("city"), "target_date": str(raw.get("target_date")),
                "available_at": pd.to_datetime(raw.get("available_at_utc"), utc=True, errors="coerce"),
                "event_time": pd.to_datetime(raw.get("last_obs_utc"), utc=True, errors="coerce"),
                "running_min_c": pd.to_numeric(raw.get("running_min_c"), errors="coerce"),
                "source_message_id": raw.get("observation_history_id"), "revision_id": raw.get("revision_id"),
                "canonical_json": canonical_json(raw), "source_path": str(path), "source_line": number,
            })
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    return frame.loc[frame["city"].isin(CITY)].copy()


def observation_state(
    truth: pd.DataFrame, city: str, target_date: str, checkpoint: pd.Timestamp,
    pit_observations: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    day = truth.loc[(truth["city"] == city) & (truth["target_date"] == target_date)].copy()
    before = day.loc[day["observation_event_time"] <= checkpoint]
    after = day.loc[day["observation_event_time"] >= checkpoint]
    if before.empty or after.empty:
        return None
    raw_min = float(before["temperature_c"].min())
    pit = pd.DataFrame() if pit_observations is None else pit_observations
    eligible = pit.loc[
        pit.get("city", pd.Series(dtype=object)).eq(city)
        & pit.get("target_date", pd.Series(dtype=object)).eq(str(target_date))
        & pit.get("available_at", pd.Series(dtype="datetime64[ns, UTC]")).notna()
        & pit.get("event_time", pd.Series(dtype="datetime64[ns, UTC]")).notna()
        & pit.get("running_min_c", pd.Series(dtype=float)).notna()
    ].copy() if not pit.empty else pit
    if not eligible.empty:
        eligible = eligible.loc[
            eligible["available_at"].le(checkpoint) & eligible["event_time"].le(checkpoint)
        ].sort_values(["available_at", "event_time", "canonical_json"], kind="mergesort")
    chosen = eligible.iloc[-1] if not eligible.empty else None
    if chosen is not None:
        raw_min = float(chosen["running_min_c"])
    realized_remaining = float(after["temperature_c"].min())
    rung = native_rung(raw_min)
    return {
        "raw_running_min_native": raw_min,
        "current_native_rung": rung,
        "next_colder_boundary_native": next_colder_boundary(rung),
        "distance_to_next_colder_boundary": raw_min - next_colder_boundary(rung),
        "realized_official_remaining_min": realized_remaining,
        "observation_count_before_checkpoint": int(len(before)),
        "observation_count_remaining": int(len(after)),
        "observation_event_time_max": before["observation_event_time"].max(),
        "observation_available_at": chosen["available_at"] if chosen is not None else pd.NaT,
        "observation_event_time": chosen["event_time"] if chosen is not None else before["observation_event_time"].max(),
        "source_message_id": chosen["source_message_id"] if chosen is not None else None,
        "revision_id": chosen["revision_id"] if chosen is not None else None,
        "observation_clock_provenance": "FROZEN_COLLECTOR_PIT" if chosen is not None else "IEM_HISTORICAL_EVENT_TIME_ONLY_NOT_PIT_AVAILABLE_AT",
    }


def cache_path(cache_dir: Path, key: str, suffix: str) -> Path:
    return cache_dir / hashlib.sha256(key.encode()).hexdigest()[:32] / suffix


def cached_bytes(cache_dir: Path, url: str, suffix: str, fetch: Callable[..., bytes], *, headers: dict[str, str] | None = None) -> bytes:
    path = cache_path(cache_dir, url + canonical_json(headers or {}), suffix)
    if path.exists():
        return path.read_bytes()
    payload = fetch(url, headers=headers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def fetch_point_values(
    metas: dict[pd.Timestamp, ObjectMeta], city: str, cache_dir: Path, fetch: Callable[..., bytes] = http_get,
    decoder: Callable[[bytes, float, float], float] = decode_nearest_tmp_celsius,
    workers: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def fetch_one(valid: pd.Timestamp, meta: ObjectMeta) -> tuple[dict[str, Any], dict[str, Any]]:
        message, item = cache_object_message(meta, cache_dir, fetch)
        value = decoder(message, CITY[city]["latitude"], CITY[city]["longitude"])
        point = {"target_valid_time": valid, "forecast_temperature_c": value, "source_key": meta.key, "source_last_modified": meta.last_modified}
        return point, item

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(fetch_one, valid, meta): valid
            for valid, meta in sorted(metas.items())
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0]["target_valid_time"])
    points = [item[0] for item in results]
    evidence = [item[1] for item in results]
    return points, evidence


def cache_object_message(
    meta: ObjectMeta, cache_dir: Path, fetch: Callable[..., bytes] = http_get
) -> tuple[bytes, dict[str, Any]]:
    """Ensure one indexed TMP message is cached and return its audit evidence."""
    url = object_url(meta.key)
    idx = cached_bytes(cache_dir, url + ".idx", ".idx", fetch)
    start, end = parse_idx_tmp_message(idx, meta.size)
    headers = {"Range": f"bytes={start}-{end}"} if end is not None else {"Range": f"bytes={start}-"}
    message = cached_bytes(
        cache_dir, url + "#" + headers["Range"], ".grib2", fetch, headers=headers
    )
    evidence = {
        "key": meta.key,
        "url": url,
        "last_modified": meta.last_modified,
        "etag": meta.etag,
        "size": meta.size,
        "range": headers["Range"],
        "idx_sha256": hashlib.sha256(idx).hexdigest(),
        "message_sha256": hashlib.sha256(message).hexdigest(),
    }
    return message, evidence


def materialize(
    truth: pd.DataFrame, output_dir: Path, *, start_date: str | None = None, end_date: str | None = None,
    date_limit: int | None = None, dry_run: bool = False, workers: int = 4,
    fetch: Callable[..., bytes] = http_get, decoder: Callable[[bytes, float, float], float] = decode_nearest_tmp_celsius,
    pit_observations: pd.DataFrame | None = None,
    materialized_at: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"
    dates = sorted(truth["target_date"].unique())
    if start_date:
        dates = [value for value in dates if value >= start_date]
    if end_date:
        dates = [value for value in dates if value <= end_date]
    if date_limit is not None:
        dates = dates[:date_limit]
    jobs = [(city, target_date, hour) for target_date in dates for city in CITY for hour in CHECKPOINT_HOURS]
    cycles = {issue for city, target_date, hour in jobs for issue in candidate_cycles(local_checkpoint(city, target_date, hour))}
    inventory: dict[pd.Timestamp, dict[str, ObjectMeta]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(list_cycle_objects, issue, fetch): issue for issue in cycles}
        for future in as_completed(futures):
            issue = futures[future]
            try:
                inventory[issue] = future.result()
            except Exception:
                inventory[issue] = {}
    plan: list[dict[str, Any]] = []
    selected: list[tuple[str, str, int, pd.Timestamp, dict[pd.Timestamp, ObjectMeta], dict[str, Any]]] = []
    for city, target_date, hour in jobs:
        checkpoint = local_checkpoint(city, target_date, hour)
        state = observation_state(truth, city, target_date, checkpoint, pit_observations)
        valid = [stamp.tz_convert("UTC") for stamp in remaining_local_hours(city, target_date, hour)]
        choice = select_latest_cycle(checkpoint, valid, inventory)
        plan.append({"city": city, "target_date": target_date, "checkpoint_hour": hour, "checkpoint_time": checkpoint, "eligible": choice is not None, "selected_issue_time": choice[0] if choice else pd.NaT, "state_available": state is not None})
        if choice and state:
            selected.append((city, target_date, hour, choice[0], choice[1], state))
    if dry_run:
        return pd.DataFrame(plan), pd.DataFrame(), {"dry_run": True, "planned_rows": len(plan), "selected_rows": len(selected)}, []
    materialized_at = utc(materialized_at or pd.Timestamp.now(tz="UTC"))
    unique_metas = {
        meta.key: meta
        for _, _, _, _, metas, _ in selected
        for meta in metas.values()
    }
    completed = 0
    print(f"prefetch_unique_objects={len(unique_metas)}", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(cache_object_message, meta, cache_dir, fetch): key
            for key, meta in sorted(unique_metas.items())
        }
        for future in as_completed(futures):
            future.result()
            completed += 1
            if completed % 250 == 0 or completed == len(futures):
                print(
                    f"prefetch_complete={completed}/{len(futures)}",
                    file=sys.stderr,
                    flush=True,
                )
    rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for city, target_date, hour, issue, metas, state in selected:
        points, point_evidence = fetch_point_values(metas, city, cache_dir, fetch, decoder, workers)
        full_path = [float(point["forecast_temperature_c"]) for point in points]
        available_at = max(meta.last_modified for meta in metas.values())
        checkpoint = local_checkpoint(city, target_date, hour)
        row = {
            "city": city, "target_date": target_date, "checkpoint_hour": hour, "checkpoint_time": checkpoint,
            "forecast_source_id": "NOAA_NODD_GFS_BDP_PDS", "model_id": "GFS", "model_run": issue.strftime("%Y%m%d%H"),
            "issue_time": issue, "available_at": available_at, "available_at_provenance": "NOAA_NODD_S3_OBJECT_LAST_MODIFIED_CONSERVATIVE_PUBLICATION_UPPER_BOUND",
            "ingested_at": materialized_at, "ingested_at_provenance": "RESEARCH_MATERIALIZER_RETRIEVAL_TIME_NOT_NATIVE_AVAILABILITY",
            "target_valid_time": [point["target_valid_time"].isoformat() for point in points], "full_remaining_path": full_path,
            "forecast_remaining_min": min(full_path), "forecast_at_checkpoint": full_path[0], "member_quantile_identity": "deterministic",
            "source_unit": "K_GRIB2_NORMALIZED_TO_C", "normalization_version": "gfs_kelvin_to_celsius_v1",
            "status": "PIT_NATIVE_VINTAGE_ELIGIBLE", "provider_object_count": len(metas), **state,
        }
        row["forecast_error"] = row["realized_official_remaining_min"] - row["forecast_remaining_min"]
        rows.append(row)
        for point in points:
            point_rows.append({"city": city, "target_date": target_date, "checkpoint_hour": hour, "checkpoint_time": checkpoint, "issue_time": issue, "available_at": available_at, **point})
        evidence.extend([{**item, "city": city, "target_date": target_date, "checkpoint_hour": hour, "issue_time": issue} for item in point_evidence])
    archive = pd.DataFrame(rows).sort_values(["target_date", "city", "checkpoint_hour"], kind="mergesort").reset_index(drop=True) if rows else pd.DataFrame()
    points = pd.DataFrame(point_rows).sort_values(["target_date", "city", "checkpoint_hour", "target_valid_time"], kind="mergesort").reset_index(drop=True) if point_rows else pd.DataFrame()
    inv = {
        "schema_version": "tmin_v2_2_noaa_gfs_archive_inventory_v1", "model_id": MODEL_ID,
        "provider": "NOAA NODD noaa-gfs-bdp-pds", "available_at_contract": "max consumed S3 Last-Modified; conservative object-publication upper bound; retrieval time never used",
        "planned_rows": len(plan), "eligible_rows": len(archive), "cycles_listed": len(inventory),
        "unique_provider_objects": len(unique_metas), "materialized_at": materialized_at,
        "coverage_by_city": {city: int(archive.loc[archive["city"].eq(city), ["target_date"]].drop_duplicates().shape[0]) if not archive.empty else 0 for city in CITY},
        "warnings": ["IEM event-time truth is research label/path evidence, not production PIT availability."],
    }
    return archive, points, inv, evidence


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iem-truth-csv", required=True, type=Path, action="append")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--date-limit", type=int)
    parser.add_argument("--observation-root", type=Path, help="optional frozen PIT collector JSONL root")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--materialized-at-utc",
        help="fixed retrieval timestamp for deterministic evidence; never used as native available_at",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    truth = pd.concat([parse_iem_truth(path) for path in args.iem_truth_csv], ignore_index=True)
    truth = truth.drop_duplicates(
        ["city", "target_date", "observation_event_time", "temperature_c"], keep="last"
    ).sort_values(["city", "observation_event_time", "temperature_c"], kind="mergesort").reset_index(drop=True)
    pit_observations = load_frozen_pit_observations(args.observation_root)
    archive, points, inventory, evidence = materialize(
        truth,
        args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        date_limit=args.date_limit,
        dry_run=args.dry_run,
        workers=args.workers,
        pit_observations=pit_observations,
        materialized_at=(
            utc(args.materialized_at_utc) if args.materialized_at_utc else None
        ),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        archive.to_csv(args.output_dir / "DRY_RUN_PLAN.csv", index=False)
    else:
        archive.to_parquet(args.output_dir / "FORECAST_ERROR_ARCHIVE_SAMPLE.parquet", index=False)
        points.to_parquet(args.output_dir / "NOAA_GFS_FORECAST_POINT_AUDIT.parquet", index=False)
        (args.output_dir / "DOWNLOAD_EVIDENCE_MANIFEST.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n")
    (args.output_dir / "FORECAST_ARCHIVE_INVENTORY.json").write_text(json.dumps(inventory, indent=2, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
