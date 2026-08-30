"""Versioned target contract shared by METAR.ws capture and reaction analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_COHORT_PATH = Path(__file__).resolve().parent / "config" / "market_reaction_cohort.yaml"
EUROPE_ASIA_COHORT_PATH = Path(__file__).resolve().parent / "config" / "market_reaction_cohort_europe_asia.yaml"
COHORT_PATHS = {
    "us_temperature_markets_wide_v1": DEFAULT_COHORT_PATH,
    "europe_asia_core_v1": EUROPE_ASIA_COHORT_PATH,
}

EXPECTED_PRIMARY_TARGETS = frozenset(
    {
        ("Atlanta", "KATL", "KATL", "primary", "station_aligned"),
        ("Austin", "KAUS", "KAUS", "primary", "station_aligned"),
        ("Dallas", "KDAL", "KDAL", "primary", "station_aligned"),
        ("Denver", "KBKF", "KBKF", "primary", "station_aligned"),
        ("Houston", "KHOU", "KHOU", "primary", "station_aligned"),
        ("LA", "KLAX", "KLAX", "primary", "station_aligned"),
        ("Miami", "KMIA", "KMIA", "primary", "station_aligned"),
        ("NYC", "KLGA", "KLGA", "primary", "station_aligned"),
        ("SanFrancisco", "KSFO", "KSFO", "primary", "station_aligned"),
        ("Seattle", "KSEA", "KSEA", "primary", "station_aligned"),
    }
)
EXPECTED_OBSERVATION_ONLY = frozenset(
    {
        ("Boston", "KBOS", None, "market_unavailable_observation_only", "market_unavailable"),
        ("Minneapolis", "KMSP", None, "market_unavailable_observation_only", "market_unavailable"),
    }
)
EXPECTED_GLOBAL_REACTION_TARGETS = frozenset({
    ("Helsinki", "EFHK", "EFHK", "primary", "station_aligned", "Europe", "station_aligned", "C"),
    ("Tokyo", "RJTT", "RJTT", "primary", "station_aligned", "Asia", "station_aligned", "C"),
    ("Amsterdam", "EHAM", "EHAM", "knmi_semantics_control", "settlement_semantics_control", "Europe", "knmi_semantics", "C"),
    ("Busan", "RKPK", "RKPK", "amos_basis_control", "amos_basis_control", "Asia", "amos_basis", "C"),
    ("Seoul", "RKSI", "RKSI", "amos_basis_control", "amos_basis_control", "Asia", "amos_basis", "C"),
    ("Singapore", "WSSS", "WSSS", "cross_station_control", "mss_s24_cross_station_comparator", "Asia", "mss_s24_cross_station", "C"),
})
EXPECTED_GLOBAL_OBSERVATION_ONLY = frozenset({
    ("Ankara", "LTAC", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Istanbul", "LTFM", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("London", "EGLC", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Madrid", "LEMD", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Milan", "LIMC", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Moscow", "UUWW", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Munich", "EDDM", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Paris", "LFPB", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("Warsaw", "EPWA", None, "observation_only", "source_availability_only", "Europe", "observation_only", "C"),
    ("HongKong", "VHHH", None, "observation_only", "source_market_station_mismatch", "Asia", "observation_only", "C"),
    ("Shenzhen", "ZGSZ", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("TelAviv", "LLBG", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Beijing", "ZBAA", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Chengdu", "ZUUU", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Chongqing", "ZUCK", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Guangzhou", "ZGGG", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Jeddah", "OEJN", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Karachi", "OPKC", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("KualaLumpur", "WMKK", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Lucknow", "VILK", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Manila", "RPLL", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Shanghai", "ZSPD", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Taipei", "RCSS", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
    ("Wuhan", "ZHHH", None, "observation_only", "source_availability_only", "Asia", "observation_only", "C"),
})


@dataclass(frozen=True)
class CohortTarget:
    city: str
    source_station: str
    market_station: str | None
    role: str
    basis_status: str
    region: str = "US"
    comparison_class: str = "station_aligned"
    market_unit: str = "F"

    def metadata(self, *, cohort_id: str) -> dict[str, str | None]:
        return {
            "cohort_id": cohort_id,
            "cohort_role": self.role,
            "basis_status": self.basis_status,
            "cohort_source_station": self.source_station,
            "cohort_market_station": self.market_station,
            "region": self.region,
            "comparison_class": self.comparison_class,
            "market_unit": self.market_unit,
        }


@dataclass(frozen=True)
class MarketReactionCohort:
    contract_version: str
    cohort_id: str
    reaction_targets: tuple[CohortTarget, ...]
    observation_only: tuple[CohortTarget, ...]

    @property
    def primary_targets(self) -> tuple[CohortTarget, ...]:
        return tuple(target for target in self.reaction_targets if target.role == "primary")

    @property
    def reaction_cities(self) -> frozenset[str]:
        return frozenset(target.city for target in self.reaction_targets)

    @property
    def official_source_stations(self) -> frozenset[str]:
        return frozenset(target.source_station for target in self.reaction_targets)

    @property
    def all_source_stations(self) -> frozenset[str]:
        return frozenset(target.source_station for target in (*self.reaction_targets, *self.observation_only))

    def target_for_event(self, event: Mapping[str, Any]) -> CohortTarget | None:
        city = str(event.get("city") or "")
        station = str(event.get("station_id") or event.get("station") or "").upper()
        for target in self.reaction_targets:
            if target.city != city:
                continue
            # Old source-event rows did not always carry station_id. Keep the
            # API compatible, while rejecting a positive contradictory station.
            if station and station != target.source_station:
                return None
            return target
        return None


def _targets(
    raw: Any,
    *,
    require_market_station: bool,
    require_semantic_metadata: bool = False,
) -> tuple[CohortTarget, ...]:
    if not isinstance(raw, list):
        raise ValueError("cohort targets must be a list")
    result: list[CohortTarget] = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("cohort target must be a mapping")
        city = str(row.get("city") or "").strip()
        source_station = str(row.get("source_station") or "").strip().upper()
        market_station = str(row.get("market_station") or "").strip().upper() or None
        role = str(row.get("role") or "").strip()
        basis_status = str(row.get("basis_status") or "").strip()
        region = str(row.get("region") or "").strip()
        comparison_class = str(row.get("comparison_class") or "").strip()
        raw_market_unit = row.get("market_unit")
        market_unit = str(raw_market_unit or "F").strip().upper()
        if not all((city, source_station, role, basis_status)) or (require_market_station and not market_station):
            raise ValueError(f"invalid cohort target: {row}")
        if require_semantic_metadata and not all((region, comparison_class, raw_market_unit)):
            raise ValueError(f"global cohort semantic metadata is required: {row}")
        if market_unit not in {"C", "F"}:
            raise ValueError(f"invalid market_unit: {market_unit}")
        result.append(CohortTarget(city, source_station, market_station, role, basis_status, region, comparison_class, market_unit))
    return tuple(result)


def load_market_reaction_cohort(path: Path = DEFAULT_COHORT_PATH) -> MarketReactionCohort:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("market reaction cohort must be a mapping")
    contract_version = str(raw.get("contract_version") or "").strip()
    cohort_id = str(raw.get("cohort_id") or "").strip()
    if not contract_version or not cohort_id:
        raise ValueError("market reaction cohort identity is required")
    require_semantic_metadata = cohort_id == "europe_asia_core_v1"
    reaction_targets = _targets(
        raw.get("reaction_targets"),
        require_market_station=True,
        require_semantic_metadata=require_semantic_metadata,
    )
    observation_only = _targets(
        raw.get("observation_only", []),
        require_market_station=False,
        require_semantic_metadata=require_semantic_metadata,
    )
    cities = [target.city for target in reaction_targets]
    if len(cities) != len(set(cities)):
        raise ValueError("reaction cohort cities must be unique")
    identity = frozenset(
        (
            target.city, target.source_station, target.market_station, target.role,
            target.basis_status, target.region, target.comparison_class, target.market_unit,
        )
        for target in reaction_targets
    )
    observation_identity = frozenset(
        (
            target.city, target.source_station, target.market_station, target.role,
            target.basis_status, target.region, target.comparison_class, target.market_unit,
        )
        for target in observation_only
    )
    if cohort_id == "us_temperature_markets_wide_v1":
        primary_identity = frozenset(value[:5] for value in identity if value[3] == "primary")
        if primary_identity != EXPECTED_PRIMARY_TARGETS:
            raise ValueError("reaction cohort primary targets differ from the frozen ten-city contract")
        chicago = next((target for target in reaction_targets if target.city == "Chicago"), None)
        if chicago is None or chicago.role != "basis_mismatch_control" or chicago.source_station != "KORD" or chicago.market_station != "KMDW":
            raise ValueError("Chicago must remain KORD -> KMDW basis-mismatch control")
        if len(reaction_targets) != 11:
            raise ValueError("US reaction target count differs from frozen contract")
        if frozenset(value[:5] for value in observation_identity) != EXPECTED_OBSERVATION_ONLY:
            raise ValueError("observation-only targets differ from the frozen Boston/Minneapolis contract")
    elif cohort_id == "europe_asia_core_v1":
        if identity != EXPECTED_GLOBAL_REACTION_TARGETS:
            raise ValueError("global reaction targets differ from frozen Europe/Asia contract")
        if len(observation_only) != len(EXPECTED_GLOBAL_OBSERVATION_ONLY) or observation_identity != EXPECTED_GLOBAL_OBSERVATION_ONLY:
            raise ValueError("global observation-only targets differ from frozen Europe/Asia contract")
    else:
        raise ValueError(f"unknown cohort_id: {cohort_id}")
    return MarketReactionCohort(
        contract_version=contract_version,
        cohort_id=cohort_id,
        reaction_targets=reaction_targets,
        observation_only=observation_only,
    )


def load_named_market_reaction_cohort(cohort_id: str) -> MarketReactionCohort:
    try:
        path = COHORT_PATHS[cohort_id]
    except KeyError as exc:
        raise ValueError(f"unknown market reaction cohort: {cohort_id}") from exc
    return load_market_reaction_cohort(path)
