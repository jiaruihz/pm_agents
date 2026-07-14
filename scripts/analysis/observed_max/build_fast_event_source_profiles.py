#!/usr/bin/env python3
"""Build the city x fast-source profile used by source-event telemetry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.fast_event_source_policy import DEFAULT_FAST_EVENT_SOURCE_PROFILES_JSON
from weather_data_feed.high_frequency_observation_sources import HIGH_FREQUENCY_CITY_SOURCES
from weather_data_feed.source_registry import load_source_profiles


DEFAULT_SOURCES = {
    "amos_runway",
    "noaa_madis_hfmetar",
    "singapore_mss",
    "jma_amedas",
    "hko_obs",
    "cowin_obs",
    "fmi",
    "mgm",
    "ims_lod",
}
CITY_ALIASES = {
    "Hong Kong": "HongKong",
    "Los Angeles": "LA",
    "New York": "NYC",
    "San Francisco": "SanFrancisco",
    "Tel Aviv": "TelAviv",
}
SOURCE_BASIS_OVERRIDES = {
    ("Seoul", "amos_runway"): "same_airport_alternate_sensor",
    ("Busan", "amos_runway"): "same_airport_alternate_sensor",
    ("Singapore", "singapore_mss"): "cross_station_reference",
    ("Tokyo", "jma_amedas"): "same_airport_alternate_sensor",
    ("HongKong", "hko_obs"): "official_settlement_feed",
    ("Shenzhen", "hko_obs"): "cross_station_proxy",
    ("HongKong", "cowin_obs"): "same_city_secondary_sensor",
    ("Ankara", "mgm"): "same_station_alternate_feed",
    ("Istanbul", "mgm"): "same_station_alternate_feed",
    ("TelAviv", "ims_lod"): "same_station_alternate_feed",
    ("Helsinki", "fmi"): "same_station_alternate_feed",
}


def canonical_city(city: str) -> str:
    return CITY_ALIASES.get(city, city.replace(" ", ""))


def station_from_official(value: str) -> str:
    raw = str(value or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]{4}", raw):
        return raw
    match = re.search(r"[?&]site=([A-Z0-9]{4})\b", str(value or ""), flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def calibration_fields(source_basis_class: str) -> tuple[str, str]:
    if source_basis_class == "official_settlement_feed":
        return "basis_confirmed_repricing_pending", "requires_side_neutral_forward_repricing"
    if source_basis_class == "same_station_mirror":
        return "same_station_forward_pending", "requires_forward_repricing_calibration"
    if source_basis_class in {"same_station_alternate_feed", "same_airport_alternate_sensor"}:
        return "alignment_and_repricing_pending", "requires_source_to_settlement_alignment"
    if source_basis_class in {"cross_station_reference", "cross_station_proxy"}:
        return "blocked_cross_station_basis", "cross_station_basis_unverified"
    return "secondary_sensor_alignment_pending", "non_settlement_sensor_alignment_required"


def build_payload() -> dict[str, Any]:
    official = load_source_profiles()
    rows: list[dict[str, Any]] = []
    for source, cities in sorted(HIGH_FREQUENCY_CITY_SOURCES.items()):
        if source not in DEFAULT_SOURCES:
            continue
        for raw_city, meta in sorted(cities.items()):
            city = canonical_city(raw_city)
            settlement = official.get(city)
            if settlement is None:
                raise ValueError(f"missing official source profile for {city}")
            source_icao = str(meta.get("icao") or meta.get("station") or "")
            official_station = station_from_official(settlement.official_station_or_feed)
            basis = SOURCE_BASIS_OVERRIDES.get((city, source))
            if basis is None:
                basis = "same_station_mirror" if source_icao == official_station else "unclassified"
            calibration_status, blocked_reason = calibration_fields(basis)
            rows.append(
                {
                    "city": city,
                    "source": source,
                    "source_station_or_feed": str(meta.get("station") or ""),
                    "source_icao": source_icao,
                    "timezone_name": settlement.timezone_name,
                    "market_unit": settlement.unit,
                    "bracket_rounding": "floor" if city == "HongKong" else "arithmetic_round",
                    "settlement_source_class": settlement.settlement_source_class,
                    "official_station_or_feed": settlement.official_station_or_feed,
                    "source_basis_class": basis,
                    "collector_enabled": True,
                    "live_eligible": False,
                    "calibration_status": calibration_status,
                    "blocked_reason": blocked_reason,
                    "note": "zero-notional source-event calibration profile; never grants live eligibility",
                }
            )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["source_basis_class"]] = counts.get(row["source_basis_class"], 0) + 1
    return {
        "schema_version": "weather_fast_event_source_profiles_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_from": [
            "weather_data_feed/high_frequency_observation_sources.py",
            "weather_data_feed/source_profiles.json",
        ],
        "profiles": sorted(rows, key=lambda row: (row["city"], row["source"])),
        "summary": {
            "profiles": len(rows),
            "cities": len({row["city"] for row in rows}),
            "live_eligible": sum(bool(row["live_eligible"]) for row in rows),
            "source_basis_counts": dict(sorted(counts.items())),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_FAST_EVENT_SOURCE_PROFILES_JSON))
    args = parser.parse_args()
    payload = build_payload()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
