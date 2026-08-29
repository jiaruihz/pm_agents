from __future__ import annotations

import pytest

from weather_city_runtime import (
    CITY_CONTRACTS,
    CanonicalMarketIdentity,
    ExperimentEpoch,
    NextPrintResearchJournal,
    adapt_amos_group,
    adapt_official_print,
    adapt_source_observation,
    audit_causal_clocks,
    contract_schema_fingerprints,
    link_next_official_print,
)


def _source(city: str, *, seen: str = "2026-08-20T10:00:02Z"):
    contract = CITY_CONTRACTS[city]
    source = {
        "city": city,
        "target_date": "2026-08-20",
        "source": contract.fast_source,
        "station": {"Amsterdam": "EHAM", "Helsinki": "EFHK", "Tokyo": "RJTT", "Busan": "RKPK", "Seoul": "RKSI"}[city],
        "observation_time_utc": "2026-08-20T10:00:00Z",
        "source_first_seen_at_utc": seen,
        "available_at_utc": seen,
        "ingested_at_utc": seen,
        "temp_c": 24.1,
        "native_unit": "C",
        "payload_hash": f"{city.lower()}-source",
        "pit_lineage_class": "collector_exact",
    }
    if city == "Amsterdam":
        source.update(payload_kind="ta")
    return adapt_source_observation(city, source)


def _official(city: str, *, observed: str = "2026-08-20T10:10:00Z", seen: str = "2026-08-20T10:10:15Z", payload_hash: str = "official"):
    contract = CITY_CONTRACTS[city]
    return adapt_official_print(city, {
        "target_date": "2026-08-20",
        "official_source": contract.official_source,
        "station": {"Amsterdam": "EHAM", "Helsinki": "EFHK", "Tokyo": "RJTT", "Busan": "RKPK", "Seoul": "RKSI"}[city],
        "observation_time_utc": observed,
        "first_seen_at_utc": seen,
        "available_at_utc": seen,
        "temp_c": 24.0,
        "payload_hash": payload_hash,
    })


@pytest.mark.parametrize("city", sorted(CITY_CONTRACTS))
def test_each_city_contract_links_reproducible_next_print(city: str) -> None:
    source = _source(city)
    official = _official(city)
    link = link_next_official_print(source, [official], CITY_CONTRACTS[city])
    assert link.status == "linked"
    assert link.official_print_id == official.official_print_id
    assert link.source_first_seen_at_utc < link.official_first_seen_at_utc


def test_amsterdam_ta_tx_and_revision_semantics_are_distinct() -> None:
    contract = CITY_CONTRACTS["Amsterdam"]
    assert contract.source_measurement_semantics == {
        "ta": "preceding_10m_average_ambient_temperature",
        "tx": "preceding_10m_maximum_ambient_temperature",
        "tn": "preceding_10m_minimum_ambient_temperature_UNAVAILABLE_IN_FROZEN_INPUT",
        "revision": "new_payload_version_preserving_prior",
    }
    ta = _source("Amsterdam")
    tx = adapt_source_observation("Amsterdam", {
        "target_date": "2026-08-20", "source": contract.fast_source,
        "station": "EHAM", "observation_time_utc": "2026-08-20T10:00:00Z",
        "interval_start_utc": "2026-08-20T09:50:00Z", "interval_end_utc": "2026-08-20T10:00:00Z",
        "first_seen_at_utc": "2026-08-20T10:00:02Z", "available_at_utc": "2026-08-20T10:00:02Z",
        "payload_kind": "tx", "temp_c": 24.4, "payload_hash": "ams-tx",
    })
    assert ta.measurement_kind == "preceding_10m_average_ambient_temperature"
    assert tx.measurement_kind == "preceding_10m_maximum_ambient_temperature"
    assert tx.observed_start_utc < tx.observed_end_utc
    assert ta.observation_id != tx.observation_id


def test_helsinki_poll_time_cannot_replace_observation_timestamp() -> None:
    with pytest.raises(ValueError, match="native observation timestamp"):
        adapt_source_observation("Helsinki", {
            "target_date": "2026-08-20", "source": "fmi", "poll_time_utc": "2026-08-20T10:01:00Z",
            "first_seen_at_utc": "2026-08-20T10:01:00Z", "temp_c": 20.0,
        })


def test_tokyo_identity_is_native_checkpoint_not_poll_tick() -> None:
    base = {
        "target_date": "2026-08-20", "source": "jma_amedas", "station": "RJTT",
        "observation_time_utc": "2026-08-20T10:00:00Z", "first_seen_at_utc": "2026-08-20T10:07:00Z",
        "available_at_utc": "2026-08-20T10:07:00Z", "temp_c": 30.0, "payload_hash": "same-payload",
    }
    first = adapt_source_observation("Tokyo", {**base, "poll_time_utc": "2026-08-20T10:07:00Z"})
    second = adapt_source_observation("Tokyo", {**base, "poll_time_utc": "2026-08-20T10:09:00Z"})
    assert first.observation_id == second.observation_id
    assert "10:00:00" in first.native_event_key


@pytest.mark.parametrize("city,station", [("Busan", "RKPK"), ("Seoul", "RKSI")])
def test_korea_group_retains_runways_consensus_and_preferred(city: str, station: str) -> None:
    rows = [
        {"city": city, "target_date": "2026-08-20", "source": "amos_runway", "station": station,
         "observation_time_utc": "2026-08-20T10:00:00Z", "first_seen_at_utc": "2026-08-20T10:00:03Z",
         "available_at_utc": "2026-08-20T10:00:03Z", "runway": "15L", "temp_c": 27.0},
        {"city": city, "target_date": "2026-08-20", "source": "amos_runway", "station": station,
         "observation_time_utc": "2026-08-20T10:00:00Z", "first_seen_at_utc": "2026-08-20T10:00:04Z",
         "available_at_utc": "2026-08-20T10:00:04Z", "runway": "15R/33L", "temp_c": 27.5,
         "is_preferred_temperature_runway": city == "Seoul"},
    ]
    group = adapt_amos_group(city, rows)
    assert group.runway_ids == ("15L", "15R/33L")
    assert group.group_consensus_value == 27.5
    assert group.group_min_value == 27.0
    assert group.group_mean_value == 27.25
    assert group.group_max_value == 27.5
    assert group.preferred_runway_id == ("15R/33L" if city == "Seoul" else None)


def test_ambiguous_equal_ranked_official_payloads_fail_closed() -> None:
    source = _source("Tokyo")
    one = _official("Tokyo", payload_hash="one")
    two = _official("Tokyo", payload_hash="two")
    link = link_next_official_print(source, [one, two], CITY_CONTRACTS["Tokyo"])
    assert link.status == "excluded"
    assert link.exclusion_reason == "ambiguous_official_print"


def test_conflicting_same_report_revisions_fail_closed_across_first_seen_times() -> None:
    source = _source("Amsterdam")
    early = _official(
        "Amsterdam",
        seen="2026-08-20T10:10:15Z",
        payload_hash="early-conflict",
    )
    late = _official(
        "Amsterdam",
        seen="2026-08-20T10:10:45Z",
        payload_hash="late-conflict",
    )
    link = link_next_official_print(source, [early, late], CITY_CONTRACTS["Amsterdam"])
    assert link.status == "excluded"
    assert link.exclusion_reason == "ambiguous_official_print"


@pytest.mark.parametrize(
    "override,reason",
    [({"station_id": "WRONG"}, "station_mismatch"), ({"official_source": "wrong-source"}, "official_source_mismatch")],
)
def test_official_station_and_source_mismatch_fail_closed(override: dict, reason: str) -> None:
    source = _source("Tokyo")
    values = _official("Tokyo").to_dict()
    values.pop("official_print_id")
    values.update(override)
    from weather_city_runtime import CanonicalOfficialPrint
    wrong = CanonicalOfficialPrint.create(**values)
    link = link_next_official_print(source, [wrong], CITY_CONTRACTS["Tokyo"])
    assert link.status == "excluded"
    assert link.exclusion_reason == reason


def test_all_causal_clock_constraints_and_failures() -> None:
    passed = audit_causal_clocks(
        source_first_seen_at_utc="2026-08-20T10:00:02Z",
        decision_at_utc="2026-08-20T10:00:03Z",
        feature_book_at_utc="2026-08-20T09:59:59Z",
        execution_book_at_utc="2026-08-20T10:00:04Z",
        markout_at_utc="2026-08-20T10:01:04Z",
        next_official_first_seen_at_utc="2026-08-20T10:10:15Z",
    )
    assert passed["status"] == "pass"
    failed = audit_causal_clocks(
        source_first_seen_at_utc="2026-08-20T10:00:05Z",
        decision_at_utc="2026-08-20T10:00:03Z",
        feature_book_at_utc="2026-08-20T10:00:05Z",
        execution_book_at_utc="2026-08-20T10:00:02Z",
        markout_at_utc="2026-08-20T10:00:02Z",
        next_official_first_seen_at_utc="2026-08-20T10:00:04Z",
    )
    assert set(failed["exclusion_reasons"]) == {
        "source_first_seen_after_decision", "feature_book_not_strictly_pre_source_first_seen",
        "execution_book_before_decision", "markout_not_after_execution_book", "next_official_not_after_fast_source",
    }


def test_exact_market_identity_requires_market_condition_and_token() -> None:
    identity = CanonicalMarketIdentity(
        city="Tokyo", target_date="2026-08-20", market_id="market", condition_id="condition",
        token_id="token", side="NO", native_bracket="30", native_unit="C", lower_bound=29.5,
        upper_bound=30.5, lower_inclusive=True, upper_inclusive=False,
    )
    assert identity.token_id == "token"
    with pytest.raises(ValueError, match="token_id"):
        CanonicalMarketIdentity(**{**identity.to_dict(), "token_id": ""})


def test_research_dual_write_is_idempotent_and_shadow_read_has_no_orphans(tmp_path) -> None:
    source = _source("Tokyo")
    official = _official("Tokyo")
    link = link_next_official_print(source, [official], CITY_CONTRACTS["Tokyo"])
    journal = NextPrintResearchJournal(tmp_path / "stage1")
    assert journal.append(source, official, link) == {"sources": 1, "officials": 1, "links": 1}
    assert journal.append(source, official, link) == {"sources": 0, "officials": 0, "links": 0}
    assert journal.shadow_read() == {
        "status": "pass", "sources": 1, "officials": 1, "links": 1,
        "orphan_source_links": [], "orphan_official_links": [],
        "orders": 0, "fills": 0, "notional": 0,
    }


def test_epoch_identity_and_schema_fingerprints_are_stable() -> None:
    epoch = ExperimentEpoch.create(
        name="fixture", start_utc="2026-08-20T00:00:00Z", end_utc=None,
        boundary_kind="frozen", immutable=True, input_manifest_sha256="abc",
    )
    assert ExperimentEpoch.create(
        name="fixture", start_utc="2026-08-20T00:00:00+00:00", end_utc=None,
        boundary_kind="frozen", immutable=True, input_manifest_sha256="abc",
    ).epoch_id == epoch.epoch_id
    fingerprints = contract_schema_fingerprints()
    assert set(fingerprints) == {
        "CanonicalSourceObservation", "CanonicalOfficialPrint", "NextPrintLink",
        "CanonicalMarketIdentity", "ExperimentEpoch", "LegacyModelRegistryEntry",
    }
    assert all(len(value) == 64 for value in fingerprints.values())
