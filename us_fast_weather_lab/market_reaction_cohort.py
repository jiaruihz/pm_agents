"""Versioned target contract shared by METAR.ws capture and reaction analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_COHORT_PATH = Path(__file__).resolve().parent / "config" / "market_reaction_cohort.yaml"

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


@dataclass(frozen=True)
class CohortTarget:
    city: str
    source_station: str
    market_station: str | None
    role: str
    basis_status: str

    def metadata(self, *, cohort_id: str) -> dict[str, str | None]:
        return {
            "cohort_id": cohort_id,
            "cohort_role": self.role,
            "basis_status": self.basis_status,
            "cohort_source_station": self.source_station,
            "cohort_market_station": self.market_station,
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


def _targets(raw: Any, *, require_market_station: bool) -> tuple[CohortTarget, ...]:
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
        if not all((city, source_station, role, basis_status)) or (require_market_station and not market_station):
            raise ValueError(f"invalid cohort target: {row}")
        result.append(CohortTarget(city, source_station, market_station, role, basis_status))
    return tuple(result)


def load_market_reaction_cohort(path: Path = DEFAULT_COHORT_PATH) -> MarketReactionCohort:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("market reaction cohort must be a mapping")
    reaction_targets = _targets(raw.get("reaction_targets"), require_market_station=True)
    observation_only = _targets(raw.get("observation_only", []), require_market_station=False)
    cities = [target.city for target in reaction_targets]
    if len(cities) != len(set(cities)):
        raise ValueError("reaction cohort cities must be unique")
    primary_identity = frozenset(
        (target.city, target.source_station, target.market_station, target.role, target.basis_status)
        for target in reaction_targets
        if target.role == "primary"
    )
    if primary_identity != EXPECTED_PRIMARY_TARGETS:
        raise ValueError("reaction cohort primary targets differ from the frozen ten-city contract")
    chicago = next((target for target in reaction_targets if target.city == "Chicago"), None)
    if chicago is None or chicago.role != "basis_mismatch_control" or chicago.source_station != "KORD" or chicago.market_station != "KMDW":
        raise ValueError("Chicago must remain KORD -> KMDW basis-mismatch control")
    if len(reaction_targets) != 11:
        raise ValueError("reaction cohort must contain only ten primary targets plus Chicago control")
    observation_identity = frozenset(
        (target.city, target.source_station, target.market_station, target.role, target.basis_status)
        for target in observation_only
    )
    if observation_identity != EXPECTED_OBSERVATION_ONLY:
        raise ValueError("observation-only targets differ from the frozen Boston/Minneapolis contract")
    contract_version = str(raw.get("contract_version") or "").strip()
    cohort_id = str(raw.get("cohort_id") or "").strip()
    if not contract_version or not cohort_id:
        raise ValueError("market reaction cohort identity is required")
    return MarketReactionCohort(
        contract_version=contract_version,
        cohort_id=cohort_id,
        reaction_targets=reaction_targets,
        observation_only=observation_only,
    )
