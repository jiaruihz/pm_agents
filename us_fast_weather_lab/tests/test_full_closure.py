from __future__ import annotations

import csv
from pathlib import Path

import yaml


LAB_ROOT = Path(__file__).resolve().parents[1]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_direct_sensor_inventory_covers_primary_airports() -> None:
    airport_config = yaml.safe_load((LAB_ROOT / "config" / "airports.yaml").read_text(encoding="utf-8"))
    expected = {row["icao"] for row in airport_config["primary_20"]}
    inventory = _csv_rows(LAB_ROOT / "config" / "airport_direct_sensor_inventory.csv")
    assert len(inventory) == 20
    assert {row["icao"] for row in inventory} == expected
    assert all(row["asos_awos_phone"] for row in inventory)
    assert len([row for row in inventory if row["datis_phone"]]) >= 18


def test_full_closure_source_universe_and_contact_floor() -> None:
    sources = _csv_rows(LAB_ROOT / "closure" / "SOURCE_LEDGER.csv")
    source_ids = [row["source_id"] for row in sources]
    assert len(source_ids) == len(set(source_ids))
    commercial_or_provider = {
        row["source_id"]
        for row in sources
        if row["family"]
        in {
            "commercial_trial",
            "commercial_relay",
            "commercial_weather",
            "commercial_aviation",
            "manufacturer_cloud",
            "wmscr_intermediary",
            "official_push",
        }
    }
    assert len(commercial_or_provider) >= 12

    attempts = _csv_rows(LAB_ROOT / "closure" / "ACCESS_ATTEMPT_LEDGER.csv")
    direct_contacts = [row for row in attempts if row["action"] in {"RFI email", "budgetary RFQ"}]
    assert len(direct_contacts) >= 7
    assert {row["source_id"] for row in direct_contacts}.issuperset(
        {"WMSCR_AWI", "WMSCR_DBT", "WMSCR_ANYAWOS", "WMSCR_RSI", "WMSCR_URF"}
    )


def test_terminal_dispositions_are_narrow() -> None:
    contract = yaml.safe_load((LAB_ROOT / "config" / "full_closure_acceptance.yaml").read_text(encoding="utf-8"))
    assert contract["terminal_dispositions"] == [
        "CLOSE_WITH_PROVEN_FAST_SOURCE_STACK",
        "CLOSE_COMPETITOR_CLAIM_NOT_REPRODUCIBLE_OR_MISLEADING",
        "CLOSE_EXHAUSTED_NO_VIABLE_SOURCE_ABANDON",
    ]


def test_commercial_stream_config_contains_only_environment_secret_names() -> None:
    raw = (LAB_ROOT / "config" / "commercial_streams.yaml").read_text(encoding="utf-8")
    config = yaml.safe_load(raw)
    assert config["metar_ws"]["api_key_env"] == "METAR_WS_API_KEY"
    assert config["synoptic_push"]["api_token_env"] == "SYNOPTIC_API_TOKEN"
    assert set(config["metar_ws"]["benchmark_stations"]) == {
        "KMIA", "KLAX", "KLGA", "KHOU", "KDAL", "KSEA", "KSFO", "KORD"
    }
    assert "mts_live_" not in raw
