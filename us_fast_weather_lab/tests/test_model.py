from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

from us_fast_weather_lab.model import events_from_json, events_from_tac, metar_event, sniff_payload


REFERENCE_NS = int(datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc).timestamp() * 1e9)


def test_metar_identity_is_stable_and_correction_is_new_version() -> None:
    raw = "METAR KJFK 281051Z 05003KT 4SM BR BKN017 22/21 A2998="
    first = metar_event(raw, reference_ns=REFERENCE_NS)
    repeated = metar_event(raw.replace("  ", " "), reference_ns=REFERENCE_NS)
    correction = metar_event(raw.replace("4SM", "COR 4SM"), reference_ns=REFERENCE_NS)
    assert first is not None and repeated is not None and correction is not None
    assert first["observation_version_id"] == repeated["observation_version_id"]
    assert first["event_family_id"] == correction["event_family_id"]
    assert first["observation_version_id"] != correction["observation_version_id"]
    assert correction["is_correction"] is True


def test_collective_tac_retains_only_target_stations() -> None:
    payload = (
        "SAUS01 TEST 281100\n"
        "METAR KJFK 281051Z 05003KT 10SM FEW020 22/21 A2998=\n"
        "METAR KORD 281051Z 00000KT 10SM FEW040 19/17 A3011=\n"
        "METAR KMCI 281053Z 00000KT 10SM CLR 20/10 A3000="
    ).encode()
    events = events_from_tac(payload, reference_ns=REFERENCE_NS, stations={"KJFK", "KORD"})
    assert {event["station_id"] for event in events} == {"KJFK", "KORD"}


def test_awc_json_cross_format_event_matches_tac() -> None:
    raw = "METAR KORD 281051Z 00000KT 10SM FEW040 19/17 A3011"
    payload = (
        '[{"icaoId":"KORD","obsTime":1787914260,"temp":19.4,"dewp":16.7,'
        '"metarType":"METAR","rawOb":"' + raw + '"}]'
    ).encode()
    events = events_from_json(payload, reference_ns=REFERENCE_NS, stations={"KORD"})
    direct = metar_event(raw, reference_ns=REFERENCE_NS)
    assert len(events) == 1 and direct is not None
    assert events[0]["observation_version_id"] == direct["observation_version_id"]


def test_sniff_gzip_json_and_bufr() -> None:
    kind, decoded = sniff_payload(gzip.compress(b'{"ok":true}'), "application/gzip", "")
    assert kind == "json"
    assert decoded == b'{"ok":true}'
    assert sniff_payload(b"BUFR\x00\x00")[0] == "bufr"


def test_metar_ws_publication_wrapper_is_decoded() -> None:
    payload = b'''{
      "type":"publication",
      "channel":"metar.obs.katl",
      "data":{
        "station":"KATL",
        "report_time":"2026-08-28T11:00:00Z",
        "temp_c":"24",
        "raw":"METAR KATL 281100Z 00000KT 10SM CLR 24/20 A3000"
      }
    }'''
    events = events_from_json(payload, reference_ns=REFERENCE_NS, stations={"KATL"})
    assert len(events) == 1
    assert events[0]["station_id"] == "KATL"
    assert events[0]["air_temperature_c"] == 24.0


def test_metar_ws_report_type_preserves_speci_semantics_without_tac_prefix() -> None:
    payload = b'''{
      "type":"publication",
      "channel":"metar.obs.kphx",
      "data":{
        "station":"KPHX",
        "report_time":"2026-08-30T06:44:00Z",
        "report_type":"SPECI",
        "temp_c":"32",
        "raw":"KPHX 300644Z 12012G20KT 10SM FEW130 32/18 A2984"
      }
    }'''
    events = events_from_json(payload, reference_ns=REFERENCE_NS, stations={"KPHX"})
    assert len(events) == 1
    assert events[0]["report_kind"] == "SPECI"
    assert events[0]["station_id"] == "KPHX"


def test_optional_metar_prefix_and_source_visibility_override_share_semantic_identity() -> None:
    body = "KATL 300852Z 11004KT 10SM FEW110 23/21 A3015"
    vendor = metar_event(body, reference_ns=REFERENCE_NS, override={"visibility_m": 9999})
    public = metar_event(f"METAR {body}", reference_ns=REFERENCE_NS)
    assert vendor is not None and public is not None
    assert vendor["semantic_version_id"] == public["semantic_version_id"]
    assert vendor["normalized_fields_json"] == public["normalized_fields_json"]
    assert vendor["raw_report_id"] != public["raw_report_id"]
    assert vendor["observation_version_id"] != public["observation_version_id"]


def test_report_kind_and_correction_remain_distinct_semantics() -> None:
    body = "KATL 300852Z 11004KT 10SM FEW110 23/21 A3015"
    routine = metar_event(f"METAR {body}", reference_ns=REFERENCE_NS)
    special = metar_event(f"SPECI {body}", reference_ns=REFERENCE_NS)
    correction = metar_event(f"METAR KATL 300852Z COR 11004KT 10SM FEW110 23/21 A3015", reference_ns=REFERENCE_NS)
    assert routine is not None and special is not None and correction is not None
    assert len({routine["semantic_version_id"], special["semantic_version_id"], correction["semantic_version_id"]}) == 3


def test_tac_visibility_forms_are_normalized_with_units_and_qualifier() -> None:
    cases = {
        "10SM": (16_093.44, "exact"),
        "1 1/2SM": (2_414.016, "exact"),
        "P6SM": (9_656.064, "greater_than"),
        "M1/4SM": (402.336, "less_than"),
        "CAVOK": (10_000.0, "at_least"),
    }
    for token, expected in cases.items():
        event = metar_event(
            f"METAR KATL 300852Z 11004KT {token} FEW110 23/21 A3015",
            reference_ns=REFERENCE_NS,
        )
        assert event is not None
        fields = json.loads(event["normalized_fields_json"])
        assert (fields["visibility_m"], fields["visibility_qualifier"]) == expected
