from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


OBSERVATION_CACHE_SCHEMA_VERSION = "weather_data_feed_observation_cache_v1"


def parse_utc(value: Any) -> datetime | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_observation_cache(records: list[Mapping[str, Any]], *, generated_at_utc: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": OBSERVATION_CACHE_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or utc_now_iso(),
        "records": [normalize_observation_cache_record(row) for row in records],
    }


def normalize_observation_cache_record(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["schema_version"] = str(normalized.get("schema_version") or OBSERVATION_CACHE_SCHEMA_VERSION)
    for key in ("city", "target_date", "status", "source", "station"):
        if not str(normalized.get(key) or "").strip():
            raise ValueError(f"observation cache record missing required field: {key}")
    return normalized


def write_observation_cache(cache: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_observation_cache(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema_version")
    if schema != OBSERVATION_CACHE_SCHEMA_VERSION:
        raise ValueError(f"unsupported observation cache schema: {schema!r}")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("observation cache records must be a list")
    data["records"] = [normalize_observation_cache_record(row) for row in records if isinstance(row, Mapping)]
    return data


def index_observation_cache(cache: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    records = cache.get("records")
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(records, list):
        return out
    for row in records:
        if not isinstance(row, Mapping):
            continue
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if city and target_date:
            out[(city, target_date)] = dict(row)
    return out
