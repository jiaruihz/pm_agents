"""Read helpers for forecast enrichment capture artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FORECAST_ENRICHMENT_SCHEMA_VERSION = "weather_forecast_enrichment_v1"


def load_forecast_enrichment(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"forecast enrichment payload must be an object: {path}")
    return payload


def normalize_forecast_enrichment_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    out.setdefault("schema_version", FORECAST_ENRICHMENT_SCHEMA_VERSION)
    out.setdefault("city", "")
    out.setdefault("target_date", "")
    out.setdefault("snapshot_ts_utc", "")
    out.setdefault("status", "")
    out.setdefault("source_statuses", {})
    out.setdefault("open_meteo_multi_model", {})
    out.setdefault("open_meteo_weather_context", {})
    out.setdefault("vertical_profile_signal", {})
    out.setdefault("taf", {})
    return out


def forecast_enrichment_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records") or []
    if not isinstance(records, list):
        raise ValueError("forecast enrichment payload records must be a list")
    return [
        normalize_forecast_enrichment_record(row)
        for row in records
        if isinstance(row, dict)
    ]


def index_forecast_enrichment(
    payload: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in forecast_enrichment_records(payload):
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if key == ("", ""):
            continue
        previous = indexed.get(key)
        if previous is None or str(row.get("snapshot_ts_utc") or "") >= str(previous.get("snapshot_ts_utc") or ""):
            indexed[key] = row
    return indexed


def load_indexed_forecast_enrichment(path: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    return index_forecast_enrichment(load_forecast_enrichment(path))
