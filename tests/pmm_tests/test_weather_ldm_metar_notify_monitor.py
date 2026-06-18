from datetime import datetime, timezone

from scripts.ops.weather_ldm_metar_notify_monitor import (
    classify_probe_output,
    parse_metar_lines,
    parse_metar_temp_c,
    parse_report_time_utc,
)


def test_parse_metar_temp_c_handles_signed_tokens():
    assert parse_metar_temp_c("KORD 181251Z 24007KT 10SM FEW250 28/18 A2992") == 28.0
    assert parse_metar_temp_c("KDEN 181251Z 01005KT 10SM FEW020 M03/M08 A2992") == -3.0


def test_parse_report_time_utc_handles_month_boundary():
    ref = datetime(2026, 7, 1, 0, 2, tzinfo=timezone.utc)
    parsed = parse_report_time_utc("302359", ref)

    assert parsed is not None
    assert parsed.isoformat() == "2026-06-30T23:59:00+00:00"


def test_parse_metar_lines_extracts_station_temp_and_latency():
    rows = parse_metar_lines(
        "\n".join(
            [
                "SAUS70 KWBC 181300",
                "METAR KORD 181251Z 24007KT 10SM FEW250 28/18 A2992",
                "SPECI RJTT 181255Z 18006KT 9999 FEW020 31/23 Q1008=",
            ]
        ),
        detect_ts_utc=datetime(2026, 6, 18, 13, 0, tzinfo=timezone.utc),
    )

    assert [row["station"] for row in rows] == ["KORD", "RJTT"]
    assert rows[0]["temp_c"] == 28.0
    assert rows[0]["source_report_ts_utc"] == "2026-06-18T12:51:00Z"
    assert rows[0]["detected_after_report_sec"] == 540.0
    assert rows[1]["raw_payload_hash"]


def test_parse_metar_lines_can_filter_source_profile_stations():
    rows = parse_metar_lines(
        "METAR KORD 181251Z 24007KT 10SM FEW250 28/18 A2992\n",
        station_targets={},
        detect_ts_utc=datetime(2026, 6, 18, 13, 0, tzinfo=timezone.utc),
    )

    assert len(rows) == 1


def test_classify_probe_output_identifies_access_and_reset():
    assert classify_probe_output("ERROR FEEDME(host): 7: Access denied by remote server") == "access_denied"
    assert classify_probe_output("RPC: Unable to receive; errno = Connection reset by peer") == "connection_reset"
