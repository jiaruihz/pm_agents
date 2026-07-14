from paho.mqtt.reasoncodes import ReasonCode

from scripts.ops.weather_wis2_metar_probe import (
    compact_notification,
    mqtt_reason_code_value,
    notification_is_aviation_candidate,
    notification_station_matches,
)


def test_mqtt_reason_code_value_supports_paho_v2_reason_code() -> None:
    assert mqtt_reason_code_value(ReasonCode(packetType=2, identifier=0)) == 0


def test_wis2_aviation_candidate_accepts_semantic_and_gts_bulletin_topics() -> None:
    assert notification_is_aviation_candidate("origin/a/wis2/x/data/recommended/aviation/metar")
    assert notification_is_aviation_candidate("origin/a/wis2/x/data/core/S/A/K/O/31/RKSL")
    assert not notification_is_aviation_candidate("origin/a/wis2/x/data/core/weather/prediction/forecast")


def test_wis2_notification_station_filter_accepts_properties_and_urls() -> None:
    payload = {
        "id": "notification-1",
        "properties": {"wigos-station-identifier": "0-20000-0-06240"},
        "links": [{"rel": "canonical", "href": "https://example.test/A_SA_EHAM_METAR.txt"}],
    }

    assert notification_station_matches(payload, {"EHAM", "UUWW"}) == ["EHAM"]
    row = compact_notification("origin/a/wis2/nl/data/recommended/aviation/metar", payload, {"EHAM"}, "2026-07-15T04:00:00+00:00")
    assert row["matched_stations"] == ["EHAM"]
    assert row["canonical_urls"] == ["https://example.test/A_SA_EHAM_METAR.txt"]
