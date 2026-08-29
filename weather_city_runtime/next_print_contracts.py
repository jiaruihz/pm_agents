"""Canonical contracts for WCIR earlier-observation -> next-official-print evidence.

This module is additive to the existing information-event and decision facts.
It has no network, database, collector, order, or production side effects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from enum import Enum
from typing import Any, Mapping, Sequence

from weather_data_feed.information_events import canonical_json_hash
from weather_model_evaluation.contracts import parse_utc, utc_text


SOURCE_SCHEMA_VERSION = "wcir_canonical_source_observation_v1"
OFFICIAL_SCHEMA_VERSION = "wcir_canonical_official_print_v1"
LINK_SCHEMA_VERSION = "wcir_next_print_link_v1"
MARKET_SCHEMA_VERSION = "wcir_canonical_market_identity_v1"
EPOCH_SCHEMA_VERSION = "wcir_experiment_epoch_v1"
LEGACY_REGISTRY_SCHEMA_VERSION = "wcir_legacy_model_registry_v1"


class CausalExclusionReason(str, Enum):
    SOURCE_FIRST_SEEN_MISSING = "source_first_seen_missing"
    SOURCE_AFTER_DECISION = "source_first_seen_after_decision"
    FEATURE_BOOK_NOT_PRE_EVENT = "feature_book_not_strictly_pre_source_first_seen"
    EXECUTION_BOOK_BEFORE_DECISION = "execution_book_before_decision"
    EXECUTION_BOOK_MISSING = "execution_book_missing"
    MARKOUT_NOT_AFTER_EXECUTION = "markout_not_after_execution_book"
    MARKOUT_MISSING = "markout_missing"
    OFFICIAL_NOT_AFTER_SOURCE = "next_official_not_after_fast_source"
    OFFICIAL_FIRST_SEEN_MISSING = "next_official_first_seen_missing"
    CITY_MISMATCH = "city_mismatch"
    TARGET_DATE_MISMATCH = "target_date_mismatch"
    STATION_MISMATCH = "station_mismatch"
    OFFICIAL_SOURCE_MISMATCH = "official_source_mismatch"
    OUTSIDE_MATCH_WINDOW = "outside_matching_window"
    AMBIGUOUS_OFFICIAL_PRINT = "ambiguous_official_print"
    MISSING_OFFICIAL_PRINT = "missing_official_print"
    REVISION_SUPERSEDED = "revision_superseded"
    LEGACY_NATIVE_PAYLOAD_NOT_FROZEN = "legacy_native_payload_not_frozen"


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_utc(value: Any) -> str | None:
    return None if value in (None, "") else utc_text(str(value))


@dataclass(frozen=True)
class CanonicalSourceObservation:
    observation_id: str
    city: str
    target_date: str
    source: str
    native_event_key: str
    payload_kind: str
    measurement_kind: str
    native_value: float | None
    native_unit: str
    observed_start_utc: str
    observed_end_utc: str
    issued_at_utc: str | None
    first_seen_at_utc: str | None
    ingested_at_utc: str
    pit_lineage_class: str
    station_id: str | None
    runway_group_id: str | None
    runway_ids: tuple[str, ...]
    preferred_runway_id: str | None
    group_consensus_value: float | None
    group_min_value: float | None
    group_mean_value: float | None
    group_max_value: float | None
    revision_of_observation_id: str | None
    payload_hash: str
    raw_ref: Mapping[str, Any]
    exclusion_reason: str | None = None
    schema_version: str = SOURCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("city", "target_date", "source", "native_event_key", "payload_kind", "measurement_kind", "native_unit", "payload_hash"):
            _required_text(getattr(self, name), name)
        start, end = parse_utc(self.observed_start_utc), parse_utc(self.observed_end_utc)
        if end < start:
            raise ValueError("observed_end_utc must be >= observed_start_utc")
        parse_utc(self.ingested_at_utc)
        if self.first_seen_at_utc is not None:
            parse_utc(self.first_seen_at_utc)
        if self.issued_at_utc is not None:
            parse_utc(self.issued_at_utc)
        expected = canonical_json_hash({
            "schema_version": SOURCE_SCHEMA_VERSION,
            "city": self.city,
            "target_date": self.target_date,
            "source": self.source,
            "native_event_key": self.native_event_key,
            "payload_kind": self.payload_kind,
            "payload_hash": self.payload_hash,
        })
        if self.observation_id != expected:
            raise ValueError("observation_id does not match canonical identity")
        if self.pit_lineage_class == "collector_exact" and not self.first_seen_at_utc:
            raise ValueError("collector_exact source observation requires first_seen_at_utc")

    @classmethod
    def create(cls, **values: Any) -> "CanonicalSourceObservation":
        values = dict(values)
        values["runway_ids"] = tuple(values.get("runway_ids") or ())
        values["observed_start_utc"] = utc_text(values["observed_start_utc"])
        values["observed_end_utc"] = utc_text(values["observed_end_utc"])
        values["issued_at_utc"] = _optional_utc(values.get("issued_at_utc"))
        values["first_seen_at_utc"] = _optional_utc(values.get("first_seen_at_utc"))
        values["ingested_at_utc"] = utc_text(values["ingested_at_utc"])
        values["observation_id"] = canonical_json_hash({
            "schema_version": SOURCE_SCHEMA_VERSION,
            "city": values["city"],
            "target_date": values["target_date"],
            "source": values["source"],
            "native_event_key": values["native_event_key"],
            "payload_kind": values["payload_kind"],
            "payload_hash": values["payload_hash"],
        })
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalOfficialPrint:
    official_print_id: str
    city: str
    target_date: str
    official_source: str
    native_print_key: str
    payload_kind: str
    measurement_kind: str
    native_value: float
    native_unit: str
    observed_start_utc: str
    observed_end_utc: str
    issued_at_utc: str | None
    first_seen_at_utc: str
    ingested_at_utc: str
    station_id: str
    report_group_id: str | None
    revision_of_print_id: str | None
    payload_hash: str
    settlement_basis: Mapping[str, Any]
    raw_ref: Mapping[str, Any]
    schema_version: str = OFFICIAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("city", "target_date", "official_source", "native_print_key", "payload_kind", "measurement_kind", "native_unit", "station_id", "payload_hash"):
            _required_text(getattr(self, name), name)
        start, end = parse_utc(self.observed_start_utc), parse_utc(self.observed_end_utc)
        if end < start:
            raise ValueError("official observed_end_utc must be >= observed_start_utc")
        parse_utc(self.first_seen_at_utc)
        parse_utc(self.ingested_at_utc)
        if self.issued_at_utc is not None:
            parse_utc(self.issued_at_utc)
        expected = canonical_json_hash({
            "schema_version": OFFICIAL_SCHEMA_VERSION,
            "city": self.city,
            "target_date": self.target_date,
            "official_source": self.official_source,
            "native_print_key": self.native_print_key,
            "payload_hash": self.payload_hash,
        })
        if self.official_print_id != expected:
            raise ValueError("official_print_id does not match canonical identity")

    @classmethod
    def create(cls, **values: Any) -> "CanonicalOfficialPrint":
        values = dict(values)
        for name in ("observed_start_utc", "observed_end_utc", "first_seen_at_utc", "ingested_at_utc"):
            values[name] = utc_text(values[name])
        values["issued_at_utc"] = _optional_utc(values.get("issued_at_utc"))
        values["official_print_id"] = canonical_json_hash({
            "schema_version": OFFICIAL_SCHEMA_VERSION,
            "city": values["city"],
            "target_date": values["target_date"],
            "official_source": values["official_source"],
            "native_print_key": values["native_print_key"],
            "payload_hash": values["payload_hash"],
        })
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NextPrintLink:
    link_id: str
    source_observation_id: str
    official_print_id: str | None
    city: str
    target_date: str
    matching_rule_id: str
    matching_window_seconds: int
    tie_break_rule: str
    status: str
    exclusion_reason: str | None
    candidate_official_print_ids: tuple[str, ...]
    source_first_seen_at_utc: str | None
    official_first_seen_at_utc: str | None
    schema_version: str = LINK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"linked", "excluded"}:
            raise ValueError("NextPrintLink status must be linked/excluded")
        if self.status == "linked" and (not self.official_print_id or self.exclusion_reason):
            raise ValueError("linked NextPrintLink requires official_print_id and no exclusion")
        if self.status == "excluded" and not self.exclusion_reason:
            raise ValueError("excluded NextPrintLink requires exclusion_reason")
        expected = canonical_json_hash({
            "schema_version": LINK_SCHEMA_VERSION,
            "source_observation_id": self.source_observation_id,
            "official_print_id": self.official_print_id,
            "matching_rule_id": self.matching_rule_id,
            "status": self.status,
            "exclusion_reason": self.exclusion_reason,
        })
        if self.link_id != expected:
            raise ValueError("link_id does not match canonical identity")

    @classmethod
    def create(cls, **values: Any) -> "NextPrintLink":
        values = dict(values)
        values["candidate_official_print_ids"] = tuple(values.get("candidate_official_print_ids") or ())
        values["source_first_seen_at_utc"] = _optional_utc(values.get("source_first_seen_at_utc"))
        values["official_first_seen_at_utc"] = _optional_utc(values.get("official_first_seen_at_utc"))
        values["link_id"] = canonical_json_hash({
            "schema_version": LINK_SCHEMA_VERSION,
            "source_observation_id": values["source_observation_id"],
            "official_print_id": values.get("official_print_id"),
            "matching_rule_id": values["matching_rule_id"],
            "status": values["status"],
            "exclusion_reason": values.get("exclusion_reason"),
        })
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalMarketIdentity:
    city: str
    target_date: str
    market_id: str
    condition_id: str
    token_id: str
    side: str
    native_bracket: str
    native_unit: str
    lower_bound: float | None
    upper_bound: float | None
    lower_inclusive: bool
    upper_inclusive: bool
    schema_version: str = MARKET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("city", "target_date", "market_id", "condition_id", "token_id", "native_bracket", "native_unit"):
            _required_text(getattr(self, name), name)
        if self.side not in {"YES", "NO"}:
            raise ValueError("market side must be YES/NO")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentEpoch:
    epoch_id: str
    name: str
    start_utc: str
    end_utc: str | None
    boundary_kind: str
    immutable: bool
    input_manifest_sha256: str
    schema_version: str = EPOCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        parse_utc(self.start_utc)
        if self.end_utc is not None and parse_utc(self.end_utc) < parse_utc(self.start_utc):
            raise ValueError("epoch end must not precede start")
        if not self.immutable:
            raise ValueError("Stage 1 ExperimentEpoch must be immutable")
        expected = canonical_json_hash({
            "schema_version": EPOCH_SCHEMA_VERSION,
            "name": self.name,
            "start_utc": utc_text(self.start_utc),
            "end_utc": _optional_utc(self.end_utc),
            "input_manifest_sha256": self.input_manifest_sha256,
        })
        if self.epoch_id != expected:
            raise ValueError("epoch_id does not match canonical identity")

    @classmethod
    def create(cls, **values: Any) -> "ExperimentEpoch":
        values = dict(values)
        values["start_utc"] = utc_text(values["start_utc"])
        values["end_utc"] = _optional_utc(values.get("end_utc"))
        values["epoch_id"] = canonical_json_hash({
            "schema_version": EPOCH_SCHEMA_VERSION,
            "name": values["name"],
            "start_utc": values["start_utc"],
            "end_utc": values["end_utc"],
            "input_manifest_sha256": values["input_manifest_sha256"],
        })
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyModelRegistryEntry:
    asset_id: str
    city: str
    kind: str
    model_id: str | None
    profile_id: str | None
    artifact_sha256: str | None
    training_start_date: str | None
    training_end_date: str | None
    forward_start_utc: str | None
    feature_set_id: str | None
    selector_id: str | None
    disposition: str
    legacy_rows_immutable: bool
    missing_declarations: tuple[str, ...] = ()
    schema_version: str = LEGACY_REGISTRY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CityObservationContract:
    city: str
    contract_id: str
    fast_source: str
    official_source: str
    source_payload_kinds: tuple[str, ...]
    source_measurement_semantics: Mapping[str, str]
    official_measurement_kind: str
    native_unit: str
    native_tick: float
    source_native_key: str
    official_native_key: str
    matching_window_seconds: int
    tie_break_rule: str
    station_rule: str
    runway_group_rule: str | None
    revision_rule: str
    settlement_basis: str
    ambiguity_rule: str

    @property
    def fast_source_id(self) -> str:
        return self.fast_source

    @property
    def prediction_target_source_id(self) -> str:
        return self.official_source

    @property
    def settlement_source_id(self) -> str:
        return self.settlement_basis

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CITY_CONTRACTS: dict[str, CityObservationContract] = {
    "Amsterdam": CityObservationContract(
        city="Amsterdam", contract_id="wcir_amsterdam_next_print_v2",
        fast_source="knmi_schiphol_10m_observation_notification_archive",
        official_source="eham_next_distinct_eligible_routine_report",
        source_payload_kinds=("ta", "tx", "tn", "revision"),
        source_measurement_semantics={
            "ta": "preceding_10m_average_ambient_temperature",
            "tx": "preceding_10m_maximum_ambient_temperature",
            "tn": "preceding_10m_minimum_ambient_temperature_UNAVAILABLE_IN_FROZEN_INPUT",
            "revision": "new_payload_version_preserving_prior",
        },
        official_measurement_kind="routine_report_point_temperature",
        native_unit="C", native_tick=1.0,
        source_native_key="station+observation_time+field+payload_hash",
        official_native_key="EHAM+routine_report_time+first_seen+payload_hash",
        matching_window_seconds=3600,
        tie_break_rule="minimum eligible EHAM routine report time strictly after source observation; exclude SPECI; collapse byte-identical duplicate; earliest first_seen; distinct conflicting correction/revision fails closed",
        station_rule="KNMI Schiphol WMO station 0-20000-0-06240 maps to EHAM only through the frozen station map; no station fallback",
        runway_group_rule=None,
        revision_rule="KNMI and EHAM revisions append new payload identities; late/conflicting same-rank official revisions fail closed and never overwrite",
        settlement_basis="polymarket_condition_specific_designated_weather_underground_eham_daily_record",
        ambiguity_rule="exclude SPECI, station/date mismatch, missing first_seen, multiple equal-ranked distinct payloads, or cross-day ambiguity",
    ),
    "Helsinki": CityObservationContract(
        city="Helsinki", contract_id="wcir_helsinki_next_print_v1",
        fast_source="fmi_helsinki_vantaa_10m_poll", official_source="canonical_helsinki_vantaa_report",
        source_payload_kinds=("temperature_point", "revision"), source_measurement_semantics={"temperature_point": "point_temperature_at_provider_observation_timestamp", "revision": "new_payload_version_preserving_prior"},
        official_measurement_kind="routine_report_point_temperature", native_unit="C", native_tick=1.0,
        source_native_key="station+provider_observation_timestamp+payload_hash_not_poll_time",
        official_native_key="station+canonical_report_timestamp+payload_hash",
        matching_window_seconds=1200, tie_break_rule="first canonical report timestamp strictly after source observation; earliest first_seen; print id",
        station_rule="Helsinki-Vantaa station identity required", runway_group_rule=None,
        revision_rule="poll rediscovery is duplicate; changed payload is explicit revision",
        settlement_basis="canonical market settlement source registry; poll timestamp is never settlement evidence",
        ambiguity_rule="exclude missing canonical observation timestamp or multiple equal-ranked revisions",
    ),
    "Tokyo": CityObservationContract(
        city="Tokyo", contract_id="wcir_tokyo_next_print_v1",
        fast_source="jma_amedas_haneda_10m", official_source="canonical_tokyo_routine_report",
        source_payload_kinds=("temperature_point", "revision"), source_measurement_semantics={"temperature_point": "native_10m_checkpoint_point_temperature", "revision": "new_payload_version_preserving_prior"},
        official_measurement_kind="routine_report_point_temperature", native_unit="C", native_tick=1.0,
        source_native_key="station+native_10m_checkpoint_timestamp+payload_hash",
        official_native_key="station+canonical_report_timestamp+payload_hash",
        matching_window_seconds=1200, tie_break_rule="first official report observed after native 10m checkpoint; earliest first_seen; print id",
        station_rule="Haneda/JMA source identity and settlement station are preserved as separate anchors",
        runway_group_rule=None, revision_rule="same checkpoint changed payload is revision, not a new poll tick",
        settlement_basis="canonical settlement registry; JMA cross is not settlement truth",
        ambiguity_rule="exclude poll-only timestamps, station ambiguity, or equal-ranked official revisions",
    ),
    "Busan": CityObservationContract(
        city="Busan", contract_id="wcir_busan_next_print_v1",
        fast_source="amos_runway_rkpk", official_source="canonical_rkpk_routine_report",
        source_payload_kinds=("runway_group_point", "revision"), source_measurement_semantics={"runway_group_point": "same-station-minute multi-runway air-temperature group", "revision": "new group payload preserving prior group"},
        official_measurement_kind="routine_report_point_temperature", native_unit="C", native_tick=1.0,
        source_native_key="station+observation_minute+sorted_runway_ids+group_payload_hash",
        official_native_key="station+canonical_report_timestamp+payload_hash",
        matching_window_seconds=1200, tie_break_rule="first RKPK routine report after group timestamp; earliest first_seen; print id",
        station_rule="RKPK required", runway_group_rule="group all runway rows at exact observation timestamp; consensus=max for cross evidence; retain min/mean/max and each runway; no implicit preferred runway",
        revision_rule="changed runway set/value is revision of the same station-minute group",
        settlement_basis="settlement registry/WU or official RKPK basis remains separate from AMOS runway air temperature",
        ambiguity_rule="exclude mixed station/timestamp group, missing runway identity, or equal-ranked official payloads",
    ),
    "Seoul": CityObservationContract(
        city="Seoul", contract_id="wcir_seoul_next_print_v1",
        fast_source="amos_runway_rksi", official_source="canonical_rksi_routine_report",
        source_payload_kinds=("runway_group_point", "revision"), source_measurement_semantics={"runway_group_point": "same-station-minute multi-runway air-temperature group", "revision": "new group payload preserving prior group"},
        official_measurement_kind="routine_report_point_temperature", native_unit="C", native_tick=1.0,
        source_native_key="station+observation_minute+sorted_runway_ids+group_payload_hash",
        official_native_key="station+canonical_report_timestamp+payload_hash",
        matching_window_seconds=1200, tie_break_rule="first RKSI routine report after group timestamp; earliest first_seen; print id",
        station_rule="RKSI required", runway_group_rule="retain all runway rows; group consensus=max; preferred 15R/33L retained separately and never substituted for group consensus",
        revision_rule="changed runway set/value is revision of the same station-minute group",
        settlement_basis="settlement registry/WU RKSI basis remains separate from AMOS runway air temperature",
        ambiguity_rule="exclude mixed station/timestamp group, missing preferred metadata when required, or equal-ranked official payloads",
    ),
}

CITY_STATION_IDS: dict[str, str] = {
    "Amsterdam": "EHAM",
    "Helsinki": "EFHK",
    "Tokyo": "RJTT",
    "Busan": "RKPK",
    "Seoul": "RKSI",
}


def contract_schema_fingerprints() -> dict[str, str]:
    """Freeze field-order-independent fingerprints for review and future parity checks."""
    classes = (
        CanonicalSourceObservation, CanonicalOfficialPrint, NextPrintLink,
        CanonicalMarketIdentity, ExperimentEpoch, LegacyModelRegistryEntry,
    )
    return {
        cls.__name__: canonical_json_hash({
            "schema_version": next(
                item.default for item in fields(cls)
                if item.name == "schema_version"
            ),
            "fields": sorted(item.name for item in fields(cls)),
        })
        for cls in classes
    }


def audit_causal_clocks(
    *, source_first_seen_at_utc: str | None, decision_at_utc: str,
    feature_book_at_utc: str | None, execution_book_at_utc: str | None,
    markout_at_utc: str | None, next_official_first_seen_at_utc: str | None,
    preregistered_t0_without_feature_book: bool = False,
) -> dict[str, Any]:
    """Fail closed on any required ordering; never repair or reorder clocks."""
    failures: list[str] = []
    source = parse_utc(source_first_seen_at_utc) if source_first_seen_at_utc else None
    decision = parse_utc(decision_at_utc)
    if source is None:
        failures.append(CausalExclusionReason.SOURCE_FIRST_SEEN_MISSING.value)
    elif source > decision:
        failures.append(CausalExclusionReason.SOURCE_AFTER_DECISION.value)
    if feature_book_at_utc:
        if source is None or parse_utc(feature_book_at_utc) >= source:
            failures.append(CausalExclusionReason.FEATURE_BOOK_NOT_PRE_EVENT.value)
    elif not preregistered_t0_without_feature_book:
        failures.append(CausalExclusionReason.FEATURE_BOOK_NOT_PRE_EVENT.value)
    if not execution_book_at_utc:
        failures.append(CausalExclusionReason.EXECUTION_BOOK_MISSING.value)
    elif parse_utc(execution_book_at_utc) < decision:
        failures.append(CausalExclusionReason.EXECUTION_BOOK_BEFORE_DECISION.value)
    if not markout_at_utc:
        failures.append(CausalExclusionReason.MARKOUT_MISSING.value)
    elif not execution_book_at_utc or parse_utc(markout_at_utc) <= parse_utc(execution_book_at_utc):
        failures.append(CausalExclusionReason.MARKOUT_NOT_AFTER_EXECUTION.value)
    if not next_official_first_seen_at_utc:
        failures.append(CausalExclusionReason.OFFICIAL_FIRST_SEEN_MISSING.value)
    elif source is None or parse_utc(next_official_first_seen_at_utc) <= source:
        failures.append(CausalExclusionReason.OFFICIAL_NOT_AFTER_SOURCE.value)
    return {
        "status": "pass" if not failures else "excluded",
        "exclusion_reasons": failures,
        "clocks": {
            "source_first_seen_at_utc": source_first_seen_at_utc,
            "decision_at_utc": utc_text(decision_at_utc),
            "feature_book_at_utc": _optional_utc(feature_book_at_utc),
            "execution_book_at_utc": _optional_utc(execution_book_at_utc),
            "markout_at_utc": _optional_utc(markout_at_utc),
            "next_official_first_seen_at_utc": _optional_utc(next_official_first_seen_at_utc),
        },
    }


def link_next_official_print(
    source: CanonicalSourceObservation,
    official_prints: Sequence[CanonicalOfficialPrint],
    contract: CityObservationContract,
) -> NextPrintLink:
    """Apply the frozen city window and deterministic tie-break fail closed."""
    common = {
        "source_observation_id": source.observation_id,
        "city": source.city,
        "target_date": source.target_date,
        "matching_rule_id": contract.contract_id,
        "matching_window_seconds": contract.matching_window_seconds,
        "tie_break_rule": contract.tie_break_rule,
        "source_first_seen_at_utc": source.first_seen_at_utc,
    }
    if not source.first_seen_at_utc:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.SOURCE_FIRST_SEEN_MISSING.value, candidate_official_print_ids=(), official_first_seen_at_utc=None)
    if source.exclusion_reason:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=source.exclusion_reason, candidate_official_print_ids=(), official_first_seen_at_utc=None)
    expected_station = CITY_STATION_IDS[source.city]
    if source.station_id != expected_station:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.STATION_MISMATCH.value, candidate_official_print_ids=(), official_first_seen_at_utc=None)
    source_matched = [item for item in official_prints if item.official_source == contract.official_source]
    if not source_matched and official_prints:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.OFFICIAL_SOURCE_MISMATCH.value, candidate_official_print_ids=tuple(item.official_print_id for item in official_prints), official_first_seen_at_utc=None)
    station_matched = [item for item in source_matched if item.station_id == expected_station]
    if not station_matched and source_matched:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.STATION_MISMATCH.value, candidate_official_print_ids=tuple(item.official_print_id for item in source_matched), official_first_seen_at_utc=None)
    source_end = parse_utc(source.observed_end_utc)
    limit = source_end + timedelta(seconds=contract.matching_window_seconds)
    candidates = [
        item for item in station_matched
        if item.city == source.city and item.target_date == source.target_date
        and source_end < parse_utc(item.observed_end_utc) <= limit
        and parse_utc(item.first_seen_at_utc) > parse_utc(source.first_seen_at_utc)
    ]
    candidates.sort(key=lambda item: (parse_utc(item.observed_end_utc), parse_utc(item.first_seen_at_utc), item.official_print_id))
    ids = tuple(item.official_print_id for item in candidates)
    if not candidates:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.MISSING_OFFICIAL_PRINT.value, candidate_official_print_ids=ids, official_first_seen_at_utc=None)
    winner = candidates[0]
    # A report-time key denotes one official print. Identical retransmissions are
    # benign revisions and use the earliest first-seen payload. Conflicting
    # payloads for that same print are ambiguous regardless of arrival order.
    same_print = [
        item for item in candidates
        if item.observed_end_utc == winner.observed_end_utc
    ]
    if len({item.payload_hash for item in same_print}) > 1:
        return NextPrintLink.create(**common, official_print_id=None, status="excluded", exclusion_reason=CausalExclusionReason.AMBIGUOUS_OFFICIAL_PRINT.value, candidate_official_print_ids=ids, official_first_seen_at_utc=None)
    return NextPrintLink.create(**common, official_print_id=winner.official_print_id, status="linked", exclusion_reason=None, candidate_official_print_ids=ids, official_first_seen_at_utc=winner.first_seen_at_utc)
