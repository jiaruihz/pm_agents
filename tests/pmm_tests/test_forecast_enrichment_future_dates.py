from weather_data_feed.forecast_sources import ForecastFetchResult
from weather_data_feed_service.forecast_enrichment import (
    _compact_multi_model_payload,
    multi_model_forecast_versions,
    write_outputs,
)


def test_compact_multi_model_payload_keeps_future_dates() -> None:
    result = ForecastFetchResult(
        source_key="open_meteo_multi_model",
        status="ok",
        fetched_at_utc="2026-07-28T00:00:00+00:00",
        latency_ms=10.0,
        payload={
            "daily": {
                "2026-07-28": {
                    "models": {"ECMWF": 95.0, "GFS": 97.0},
                    "model_count": 2,
                },
                "2026-07-29": {
                    "models": {"ECMWF": 94.0, "GFS": 98.0},
                    "model_count": 2,
                },
            },
            "daily_dates": ["2026-07-28", "2026-07-29"],
            "model_metadata": {
                "ECMWF": {"open_meteo_model": "ecmwf_ifs025"}
            },
            "hourly_values_hash_by_model": {"ECMWF": "hash-a"},
        },
    )

    compact = _compact_multi_model_payload(result, "2026-07-28")

    assert compact["target_date"]["models"]["GFS"] == 97.0
    assert compact["daily"]["2026-07-29"]["models"]["GFS"] == 98.0
    assert compact["daily_dates"] == ["2026-07-28", "2026-07-29"]


def test_forecast_versions_keep_each_target_model_capture(tmp_path) -> None:
    result = ForecastFetchResult(
        source_key="open_meteo_multi_model",
        status="ok",
        fetched_at_utc="2026-07-28T00:00:03+00:00",
        latency_ms=10.0,
        payload={
            "daily": {
                "2026-07-28": {
                    "models": {"ECMWF": 95.0, "GFS": 97.0},
                    "model_count": 2,
                },
                "2026-07-29": {
                    "models": {"ECMWF": 94.0, "GFS": 98.0},
                    "model_count": 2,
                },
            },
            "daily_dates": ["2026-07-28", "2026-07-29"],
            "model_metadata": {
                "ECMWF": {
                    "open_meteo_model": "ecmwf_ifs025",
                    "provider": "ECMWF",
                    "tier": "global",
                },
                "GFS": {
                    "open_meteo_model": "gfs_seamless",
                    "provider": "NOAA",
                    "tier": "global",
                },
            },
        },
        metadata={
            "source_fetch_start_utc": "2026-07-28T00:00:01+00:00",
            "source_fetch_end_utc": "2026-07-28T00:00:03+00:00",
            "raw_payload_hash": "raw-hash",
        },
    )
    row = {
        "city": "Shanghai",
        "station": "ZSPD",
        "target_date": "2026-07-28",
        "snapshot_ts_utc": "2026-07-28T00:00:00+00:00",
        "timezone_name": "Asia/Shanghai",
        "unit": "C",
        "open_meteo_multi_model": _compact_multi_model_payload(
            result, "2026-07-28"
        ),
    }

    versions = multi_model_forecast_versions(row)

    assert len(versions) == 4
    future_gfs = next(
        version
        for version in versions
        if version["forecast_target_date"] == "2026-07-29"
        and version["model_label"] == "GFS"
    )
    assert future_gfs["forecast_max_f"] == 98.0
    assert future_gfs["forecast_horizon_days_local"] == 1
    assert future_gfs["captured_at_utc"] == "2026-07-28T00:00:00+00:00"
    assert future_gfs["available_at_utc"] == "2026-07-28T00:00:03+00:00"
    assert (
        future_gfs["forecast_run_lineage_status"]
        == "provider_run_unavailable_collector_versioned"
    )

    payload = {
        # The capture is on the following UTC day while the provider evidence
        # remains available on 7/28.  Physical storage follows capture time.
        "generated_at_utc": "2026-07-29T00:00:04+00:00",
        "records": [row],
    }
    write_outputs(payload, tmp_path)
    write_outputs(payload, tmp_path)

    version_lines = (
        tmp_path / "2026-07-29" / "forecast_versions.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(version_lines) == 8
    assert not (tmp_path / "2026-07-28" / "forecast_versions.jsonl").exists()
    for aggregate_name in (
        "forecast_versions.jsonl",
        "forecast_run_rows_v2.jsonl",
        "forecast_batches_v2.jsonl",
        "forecast_batch_summaries.jsonl",
    ):
        assert not (tmp_path / aggregate_name).exists()
    latest = (
        tmp_path / "latest_versions.json"
    ).read_text(encoding="utf-8")
    assert '"capture_rows": 4' in latest
    assert '"2026-07-29"' in latest
