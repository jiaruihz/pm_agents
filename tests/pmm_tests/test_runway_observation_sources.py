from __future__ import annotations

import sys
import types

from weather_data_feed import (
    RunwayFetchResult,
    parse_amos_runway_html,
    parse_amsc_wind_plate_payload,
    supported_runway_cities,
)
from weather_data_feed.runway_sources import fetch_amsc_awos_runway


def test_amsc_parser_keeps_runway_point_fields_and_configured_target() -> None:
    payload = {
        "code": 200,
        "data": {
            "16/34": {
                "RNO": "16/34",
                "OTIME": "2026-07-07 05:21:00",
                "TDZ_TEMP": "30.1",
                "MID_TEMP": "31.0",
                "END_TEMP": "32.2",
                "TDZ_WIND_D10": "190",
                "TDZ_WIND_F10": "4.5",
                "TDZ_RVR_1A": "P2000",
                "TDZ_MOR_1A": "10000",
                "TDZ_HUMID": "61",
                "METAR": "ZSQD 070500Z 19005MPS 9999 30/24 Q1004",
            }
        },
    }

    rows = parse_amsc_wind_plate_payload(payload, city="Qingdao", station="ZSQD", target_date="2026-07-07")

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "amsc_awos"
    assert row["runway"] == "16/34"
    assert row["tdz_temp_c"] == 30.1
    assert row["mid_temp_c"] == 31.0
    assert row["end_temp_c"] == 32.2
    assert row["configured_runway_target"] == "34"
    assert row["is_configured_runway_target"] is True
    assert row["configured_runway_position"] == "end"
    assert row["point_temp_c"] == 32.2
    assert row["rvr"] == 2000


def test_amos_parser_pairs_runway_rows_and_preserves_metar_anchor() -> None:
    html = """
    <div>(RKSI) 2026년 7월 7일 14:20 KST</div>
    <div>METAR RKSI 070520Z 22008KT 9999 FEW030 29/22 Q1007=</div>
    <table>
      <tr><td>15L</td><td>AVG</td><td>MIN</td><td>MAX</td></tr>
      <tr><td>TEMP(℃)</td><td>28.8</td></tr>
      <tr><td>DEW (℃)</td><td>22.1</td></tr>
      <tr><td>WD</td><td>220</td><td>210</td><td>230</td></tr>
      <tr><td>WS</td><td>8.0</td><td>6.0</td><td>10.0</td></tr>
      <tr><td>33R</td><td>AVG</td><td>MIN</td><td>MAX</td></tr>
      <tr><td>TEMP(℃)</td><td>29.4</td></tr>
      <tr><td>DEW (℃)</td><td>22.2</td></tr>
    </table>
    """

    rows = parse_amos_runway_html(html, city="Seoul", station="RKSI", target_date="2026-07-07")

    assert len(rows) == 1
    assert rows[0]["source"] == "amos"
    assert rows[0]["runway"] == "15L/33R"
    assert rows[0]["point_temp_c"] == 29.1
    assert rows[0]["metar_temp_c"] == 29.0
    assert rows[0]["observation_time_utc"] == "2026-07-07T05:20:00+00:00"


def test_runway_observations_cli_dispatches_runner_args(monkeypatch) -> None:
    from weather_data_feed_service import cli

    calls = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setitem(sys.modules, "weather_data_feed_service.runway_observations", fake_module)

    rc = cli.main(["runway-observations", "--", "--cities", "Shanghai", "--sources", "amsc_awos"])

    assert rc == 0
    assert calls == [["--cities", "Shanghai", "--sources", "amsc_awos"]]


def test_supported_runway_cities_cover_polyweather_runway_sources() -> None:
    cities = supported_runway_cities()

    assert cities["Shanghai"]["station"] == "ZSPD"
    assert cities["Seoul"]["station"] == "RKSI"


def test_amsc_fetch_marks_login_expired_as_auth_failed(monkeypatch) -> None:
    class FakeResponse:
        def json(self) -> dict[str, object]:
            return {"errCode": -12013, "errMsg": "登录信息失效"}

    def fake_http_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("weather_data_feed.runway_sources._http_get", fake_http_get)

    result = fetch_amsc_awos_runway("Qingdao")

    assert isinstance(result, RunwayFetchResult)
    assert result.status == "auth_failed"
    assert "登录信息失效" in result.error
    assert result.metadata["city"] == "Qingdao"
