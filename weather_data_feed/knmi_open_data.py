"""KNMI Open Data file collector support for Schiphol station 240."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

from weather_data_feed.high_frequency_observation_sources import (
    HIGH_FREQUENCY_CITY_SOURCES,
    KNMI_API_BASE,
    KNMI_DATASET,
    KNMI_VERSION,
    HighFrequencyFetchResult,
    HighFrequencyFetchSettings,
    _base_record,
    _http_get,
    _result,
    stable_hash,
)


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _finite_float(value: Any) -> float | None:
    value = _scalar(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_knmi_netcdf(
    content: bytes,
    *,
    city: str = "Amsterdam",
    fetched_at: datetime | None = None,
    file_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Extract station 240 ta/tx from one KNMI 10-minute NetCDF file."""
    try:
        from netCDF4 import Dataset, num2date
    except ImportError as exc:
        raise RuntimeError("netCDF4 is required for KNMI Open Data files") from exc

    meta = HIGH_FREQUENCY_CITY_SOURCES["knmi"][city]
    fetched = fetched_at or datetime.now(timezone.utc)
    file_meta = dict(file_metadata or {})
    with Dataset("knmi_open_data.nc", mode="r", memory=content) as dataset:
        wsi_values = [str(_scalar(value)) for value in dataset.variables["wsi"][:]]
        try:
            station_index = wsi_values.index(str(meta["station"]))
        except ValueError:
            return []

        time_var = dataset.variables["time"]
        time_value = time_var[0]
        obs_dt = num2date(
            time_value,
            units=time_var.units,
            calendar=getattr(time_var, "calendar", "standard"),
            only_use_cftime_datetimes=False,
        )
        if obs_dt.tzinfo is None:
            obs_dt = obs_dt.replace(tzinfo=timezone.utc)
        else:
            obs_dt = obs_dt.astimezone(timezone.utc)

        ta = _finite_float(dataset.variables["ta"][station_index, 0])
        tx = _finite_float(dataset.variables["tx"][station_index, 0])
        if ta is None:
            return []

        raw = {
            "filename": file_meta.get("filename"),
            "station": str(_scalar(dataset.variables["station"][station_index])),
            "wsi": wsi_values[station_index],
            "ta": ta,
            "tx": tx,
            "measurement_end_utc": obs_dt.isoformat(),
        }
        row = _base_record(
            source="knmi",
            city=city,
            meta=meta,
            target_date=obs_dt.astimezone(
                __import__("zoneinfo").ZoneInfo(str(meta["timezone_name"]))
            ).date().isoformat(),
            obs_dt=obs_dt,
            fetched_at=fetched,
            temp_c=ta,
            raw=raw,
            source_kind="official_airport_station",
            source_note=(
                "KNMI Schiphol station 240 10-minute AWS/synoptic file; "
                "ta is the final 1-minute mean and tx is the 10-minute maximum; "
                "research enrichment, not METAR or WU settlement truth"
            ),
            extra={
                "wigos_station_id": wsi_values[station_index],
                "knmi_station_code": str(_scalar(dataset.variables["station"][station_index])),
                "max_temp_c_past_10m": round(tx, 3) if tx is not None else None,
                "measurement_interval_end_utc": obs_dt.isoformat(),
                "knmi_filename": file_meta.get("filename"),
                "knmi_file_created_at_utc": file_meta.get("created"),
                "knmi_file_last_modified_at_utc": file_meta.get("lastModified"),
                "knmi_first_seen_at_utc": fetched.isoformat(),
            },
        )
        return [row]


def fetch_knmi_open_data(
    city: str = "Amsterdam",
    *,
    settings: HighFrequencyFetchSettings | None = None,
    last_filename: str = "",
) -> HighFrequencyFetchResult:
    """Check the newest KNMI file and download it only when it is new."""
    start = datetime.now(timezone.utc)
    token = os.environ.get("KNMI_OPEN_DATA_API_KEY", "").strip()
    if not token:
        return _result(
            "knmi",
            city,
            "auth_required",
            [],
            start,
            datetime.now(timezone.utc),
            error="KNMI_OPEN_DATA_API_KEY not configured",
        )

    files_url = (
        f"{KNMI_API_BASE}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    )
    headers = {"Authorization": token, "Accept": "application/json"}
    listing = _http_get(
        files_url,
        params={"maxKeys": 1, "sorting": "desc"},
        headers=headers,
        settings=settings,
    ).json()
    files = listing.get("files") if isinstance(listing, dict) else None
    if not isinstance(files, list) or not files:
        end = datetime.now(timezone.utc)
        return _result("knmi", city, "empty", [], start, end)

    file_meta = dict(files[0])
    filename = str(file_meta.get("filename") or "")
    if not filename:
        end = datetime.now(timezone.utc)
        return _result("knmi", city, "empty", [], start, end)
    if filename == last_filename:
        end = datetime.now(timezone.utc)
        return _result(
            "knmi",
            city,
            "no_new_file",
            [],
            start,
            end,
            metadata={"filename": filename, "api_requests": 1},
        )

    url_payload = _http_get(
        f"{files_url}/{filename}/url",
        headers=headers,
        settings=settings,
    ).json()
    download_url = str(url_payload.get("temporaryDownloadUrl") or "")
    if not download_url:
        raise RuntimeError(f"KNMI did not return a download URL for {filename}")
    content = _http_get(download_url, settings=settings).content
    end = datetime.now(timezone.utc)
    records = parse_knmi_netcdf(
        content,
        city=city,
        fetched_at=end,
        file_metadata=file_meta,
    )
    return _result(
        "knmi",
        city,
        "ok" if records else "empty",
        records,
        start,
        end,
        metadata={
            "filename": filename,
            "file_created_at_utc": file_meta.get("created"),
            "file_last_modified_at_utc": file_meta.get("lastModified"),
            "api_requests": 2,
            "download_requests": 1,
            "raw_payload_hash": stable_hash(file_meta),
        },
    )
