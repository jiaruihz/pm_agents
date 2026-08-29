"""Adapters into the WCIR Stage 1 canonical next-print contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from weather_data_feed.information_events import canonical_json_hash

from .next_print_contracts import (
    CITY_CONTRACTS,
    CanonicalOfficialPrint,
    CanonicalSourceObservation,
    CausalExclusionReason,
)


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def _runway_rows(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = row.get("runways") or row.get("runway_rows") or []
    return [item for item in rows if isinstance(item, Mapping)]


def adapt_source_observation(city: str, row: Mapping[str, Any]) -> CanonicalSourceObservation:
    """Normalize one frozen source row without inferring an unavailable clock/value."""
    contract = CITY_CONTRACTS[city]
    observed = _value(row, "observation_time_utc", "source_event_ts_utc", "source_observation_ts_utc")
    if not observed:
        raise ValueError("source observation requires native observation timestamp")
    first_seen = _value(row, "source_first_seen_at_utc", "first_seen_at_utc")
    issued = _value(row, "issued_at_utc", "source_published_at_utc")
    payload_kind = str(row.get("payload_kind") or "temperature_point")
    measurement_kind = contract.source_measurement_semantics.get(payload_kind, payload_kind)
    observed_start = _value(row, "observed_start_utc", "valid_from_utc", "interval_start_utc", "observation_time_utc", "source_event_ts_utc")
    observed_end = _value(row, "observed_end_utc", "valid_to_utc", "interval_end_utc", "observation_time_utc", "source_event_ts_utc")
    runway_rows = _runway_rows(row)
    runway_ids = tuple(sorted(str(item.get("runway") or "") for item in runway_rows if item.get("runway")))
    runway_values = [float(item["temp_c"]) for item in runway_rows if item.get("temp_c") is not None]
    native_value = _value(row, "native_value", "temp_c", "ta_c", "tx_c", "source_temp_c")
    if native_value is None and runway_values:
        native_value = max(runway_values)
    native_value = None if native_value is None else float(native_value)
    payload_hash = str(row.get("payload_hash") or canonical_json_hash(dict(row)))
    station = _value(row, "station_id", "station")
    native_key = str(row.get("native_event_key") or "|").strip()
    if native_key == "|":
        native_key = "|".join(filter(None, (str(station or ""), str(observed), payload_kind, ",".join(runway_ids))))
    exclusion = None
    if native_value is None:
        exclusion = CausalExclusionReason.LEGACY_NATIVE_PAYLOAD_NOT_FROZEN.value
    return CanonicalSourceObservation.create(
        city=city,
        target_date=str(row["target_date"]),
        source=str(row.get("source") or contract.fast_source),
        native_event_key=native_key,
        payload_kind=payload_kind,
        measurement_kind=measurement_kind,
        native_value=native_value,
        native_unit=str(row.get("native_unit") or contract.native_unit),
        observed_start_utc=str(observed_start),
        observed_end_utc=str(observed_end),
        issued_at_utc=issued,
        first_seen_at_utc=first_seen,
        ingested_at_utc=str(_value(row, "ingested_at_utc", "available_at_utc", "detected_at_utc", "first_seen_at_utc")),
        pit_lineage_class=str(row.get("pit_lineage_class") or ("collector_exact" if first_seen else "late_backfill_first_seen_unknown")),
        station_id=None if station is None else str(station),
        runway_group_id=(None if not runway_ids else f"{station}|{observed}"),
        runway_ids=runway_ids,
        preferred_runway_id=(None if row.get("preferred_runway_id") is None else str(row["preferred_runway_id"])),
        group_consensus_value=(max(runway_values) if runway_values else None),
        group_min_value=(min(runway_values) if runway_values else None),
        group_mean_value=(sum(runway_values) / len(runway_values) if runway_values else None),
        group_max_value=(max(runway_values) if runway_values else None),
        revision_of_observation_id=(None if row.get("revision_of_observation_id") is None else str(row["revision_of_observation_id"])),
        payload_hash=payload_hash,
        raw_ref=dict(row.get("raw_ref") or {"physical_path": row.get("raw_source_path"), "raw_row_hash": row.get("raw_row_hash")}),
        exclusion_reason=exclusion,
    )


def adapt_legacy_information_event(
    event: Mapping[str, Any], *, target_date: str
) -> CanonicalSourceObservation:
    """Link the immutable legacy event header without manufacturing its omitted payload."""
    city = str(event["city"])
    return adapt_source_observation(city, {
        **dict(event),
        "target_date": target_date,
        "payload_kind": "legacy_observation_header",
        "native_event_key": str(event.get("information_event_id") or event.get("content_key")),
        "observation_time_utc": event.get("source_event_ts_utc"),
        "ingested_at_utc": event.get("available_at_utc") or event.get("detected_at_utc"),
    })


def adapt_official_print(city: str, row: Mapping[str, Any]) -> CanonicalOfficialPrint:
    contract = CITY_CONTRACTS[city]
    observed = _value(row, "observation_time_utc", "report_time_utc", "source_event_ts_utc")
    first_seen = _value(row, "first_seen_at_utc", "source_first_seen_at_utc")
    native_value = _value(row, "native_value", "temp_c", "official_temp_c")
    if not observed or not first_seen or native_value is None:
        raise ValueError("official print requires observation timestamp, first_seen and native value")
    station = _value(row, "station_id", "station")
    if not station:
        raise ValueError("official print requires station identity")
    payload_hash = str(row.get("payload_hash") or canonical_json_hash(dict(row)))
    return CanonicalOfficialPrint.create(
        city=city,
        target_date=str(row["target_date"]),
        official_source=str(row.get("official_source") or contract.official_source),
        native_print_key=str(row.get("native_print_key") or f"{station}|{observed}"),
        payload_kind=str(row.get("payload_kind") or "official_temperature_point"),
        measurement_kind=str(row.get("measurement_kind") or contract.official_measurement_kind),
        native_value=float(native_value),
        native_unit=str(row.get("native_unit") or contract.native_unit),
        observed_start_utc=str(_value(row, "observed_start_utc", "interval_start_utc", "observation_time_utc", "report_time_utc")),
        observed_end_utc=str(_value(row, "observed_end_utc", "interval_end_utc", "observation_time_utc", "report_time_utc")),
        issued_at_utc=_value(row, "issued_at_utc", "published_at_utc"),
        first_seen_at_utc=str(first_seen),
        ingested_at_utc=str(_value(row, "ingested_at_utc", "available_at_utc", "first_seen_at_utc")),
        station_id=str(station),
        report_group_id=None if row.get("report_group_id") is None else str(row["report_group_id"]),
        revision_of_print_id=None if row.get("revision_of_print_id") is None else str(row["revision_of_print_id"]),
        payload_hash=payload_hash,
        settlement_basis=dict(row.get("settlement_basis") or {"contract": contract.settlement_basis}),
        raw_ref=dict(row.get("raw_ref") or {"physical_path": row.get("raw_source_path"), "raw_row_hash": row.get("raw_row_hash")}),
    )


def adapt_amos_group(city: str, rows: Sequence[Mapping[str, Any]]) -> CanonicalSourceObservation:
    """Normalize a same-city/station/timestamp AMOS runway group."""
    if city not in {"Busan", "Seoul"} or not rows:
        raise ValueError("AMOS group requires Busan/Seoul rows")
    keys = {(row.get("city"), row.get("target_date"), row.get("station"), row.get("observation_time_utc")) for row in rows}
    if len(keys) != 1 or next(iter(keys))[0] != city:
        raise ValueError("AMOS group rows must share city, target_date, station and timestamp")
    first = dict(rows[0])
    first["payload_kind"] = "runway_group_point"
    first["runways"] = [dict(row) for row in rows]
    first["payload_hash"] = canonical_json_hash(first["runways"])
    preferred = next((row for row in rows if row.get("is_preferred_temperature_runway")), None)
    first["preferred_runway_id"] = None if preferred is None else preferred.get("runway")
    return adapt_source_observation(city, first)
