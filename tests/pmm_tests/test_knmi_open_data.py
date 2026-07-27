from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from netCDF4 import Dataset, date2num

from weather_data_feed.knmi_open_data import parse_knmi_netcdf
from weather_data_feed_service.knmi_open_data import collect_once


def _fixture_netcdf(path: Path) -> bytes:
    with Dataset(path, "w") as dataset:
        dataset.createDimension("station", 2)
        dataset.createDimension("time", 1)
        station = dataset.createVariable("station", str, ("station",))
        wsi = dataset.createVariable("wsi", str, ("station",))
        stationname = dataset.createVariable("stationname", str, ("station",))
        time_var = dataset.createVariable("time", "f8", ("time",))
        ta = dataset.createVariable("ta", "f8", ("station", "time"))
        tx = dataset.createVariable("tx", "f8", ("station", "time"))
        station[0], station[1] = "06235", "06240"
        wsi[0], wsi[1] = "0-20000-0-06235", "0-20000-0-06240"
        stationname[0], stationname[1] = "De Kooy", "Schiphol Airport"
        time_var.units = "seconds since 1950-01-01"
        time_var.calendar = "proleptic_gregorian"
        time_var[:] = [
            date2num(
                datetime(2026, 7, 27, 15, 30),
                time_var.units,
                time_var.calendar,
            )
        ]
        ta[:, :] = [[18.1], [20.6]]
        tx[:, :] = [[18.4], [20.9]]
    return path.read_bytes()


def test_parse_knmi_netcdf_extracts_schiphol_ta_tx(tmp_path: Path) -> None:
    content = _fixture_netcdf(tmp_path / "fixture.nc")
    rows = parse_knmi_netcdf(
        content,
        fetched_at=datetime(2026, 7, 27, 15, 34, tzinfo=timezone.utc),
        file_metadata={
            "filename": "KMDS__OPER_P___10M_OBS_L2_202607271530.nc",
            "created": "2026-07-27T15:33:41+00:00",
            "lastModified": "2026-07-27T15:34:10+00:00",
        },
    )

    assert len(rows) == 1
    assert rows[0]["station"] == "0-20000-0-06240"
    assert rows[0]["knmi_station_code"] == "06240"
    assert rows[0]["temp_c"] == 20.6
    assert rows[0]["max_temp_c_past_10m"] == 20.9
    assert rows[0]["knmi_first_seen_at_utc"] == "2026-07-27T15:34:00+00:00"


def test_collect_once_does_not_append_when_file_is_unchanged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from weather_data_feed_service import knmi_open_data as service
    from weather_data_feed.high_frequency_observation_sources import (
        HighFrequencyFetchResult,
    )

    state = {
        "last_success_filename": "same.nc",
        "last_success_at_utc": "2026-07-27T15:34:00+00:00",
    }
    (tmp_path / "state.json").write_text(__import__("json").dumps(state))
    previous = {"records": [{"observation_time_utc": "2026-07-27T15:30:00+00:00", "temp_c": 20.6}]}
    (tmp_path / "latest.json").write_text(__import__("json").dumps(previous))

    def fake_fetch(*, settings, last_filename):
        assert last_filename == "same.nc"
        return HighFrequencyFetchResult(
            source_key="knmi",
            city="Amsterdam",
            status="no_new_file",
            fetched_at_utc="2026-07-27T15:39:00+00:00",
            latency_ms=1.0,
            metadata={"filename": "same.nc", "api_requests": 1},
        )

    monkeypatch.setattr(service, "fetch_knmi_open_data", fake_fetch)
    payload = collect_once(output_dir=tmp_path)

    assert payload["status"] == "no_new_file"
    assert payload["rows"] == 1
    assert payload["append_rows"] == 0
    assert payload["records"][0]["temp_c"] == 20.6
    assert not (tmp_path / "knmi_observations.jsonl").exists()
