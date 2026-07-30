from __future__ import annotations

from datetime import datetime, timezone

from scripts.analysis.market_structure_edge.research_tokyo_jma_multivariate_path_v1 import (
    build_feature_rows,
    cloud_fraction,
    parse_iem_row,
    raw_metar_from_rem,
    relative_humidity,
    signed_tenths,
)


UTC = timezone.utc


def test_ncei_parsers_keep_native_metar_semantics() -> None:
    assert signed_tenths("+0295,1") == 29.5
    assert signed_tenths("+9999,9") is None
    raw = raw_metar_from_rem(
        "MET075METAR RJTT 010000Z 18005KT 9999 FEW020 BKN040 30/22 Q1004="
    )
    assert raw.startswith("METAR RJTT")
    assert cloud_fraction(raw) == 0.875
    assert 61 < relative_humidity(30, 22) < 63


def test_iem_current_year_fallback_preserves_weather_fields() -> None:
    row = parse_iem_row(
        {
            "valid": "2026-07-01 01:30",
            "tmpc": "25.00",
            "dwpc": "22.00",
            "relh": "83.47",
            "drct": "120.00",
            "sknt": "4.00",
            "mslp": "M",
            "vsby": "3.73",
            "wxcodes": "M",
            "metar": "RJTT 010130Z 12004KT 6000 FEW012 BKN/// 25/22 Q1012 NOSIG",
        }
    )
    assert row is not None
    assert row["routine"] is True
    assert row["pressure_hpa"] == 1012.0
    assert row["cloud_cover_fraction"] == 0.875
    assert 5990 < row["visibility_m"] < 6010


def test_equal_timestamp_metar_is_not_a_feature_or_label() -> None:
    jma = [
        {
            "observation_time_utc": "2026-07-01T01:10:00+00:00",
            "temp_c": "29.6",
            "wind_speed_kt": "5",
            "wind_dir_deg": "180",
            "wind_gust_kt": "8",
            "precipitation_10m_mm": "0",
        }
    ]
    metar = [
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            "local_date": "2026-07-01",
            "report_type": "FM-15",
            "routine": True,
            "temp_c": 29.0,
            "dewpoint_c": 20.0,
            "relative_humidity_pct": 60.0,
            "wind_dir_deg": 180.0,
            "wind_speed_kt": 5.0,
            "pressure_hpa": 1004.0,
            "cloud_cover_fraction": 0.25,
            "ceiling_ft_agl": None,
            "precipitating": 0,
            "visibility_m": 9999.0,
        },
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 10, tzinfo=UTC),
            "local_date": "2026-07-01",
            "report_type": "FM-16",
            "routine": False,
            "temp_c": 99.0,
            "dewpoint_c": 20.0,
            "relative_humidity_pct": 1.0,
            "wind_dir_deg": 180.0,
            "wind_speed_kt": 5.0,
            "pressure_hpa": 1004.0,
            "cloud_cover_fraction": 0.0,
            "ceiling_ft_agl": None,
            "precipitating": 0,
            "visibility_m": 9999.0,
        },
        {
            "observation_time_utc": datetime(2026, 7, 1, 1, 30, tzinfo=UTC),
            "local_date": "2026-07-01",
            "report_type": "FM-15",
            "routine": True,
            "temp_c": 30.0,
            "dewpoint_c": 20.0,
            "relative_humidity_pct": 55.0,
            "wind_dir_deg": 180.0,
            "wind_speed_kt": 5.0,
            "pressure_hpa": 1004.0,
            "cloud_cover_fraction": 0.0,
            "ceiling_ft_agl": None,
            "precipitating": 0,
            "visibility_m": 9999.0,
        },
        {
            "observation_time_utc": datetime(2026, 7, 1, 2, 0, tzinfo=UTC),
            "local_date": "2026-07-01",
            "report_type": "FM-15",
            "routine": True,
            "temp_c": 30.0,
            "dewpoint_c": 20.0,
            "relative_humidity_pct": 55.0,
            "wind_dir_deg": 180.0,
            "wind_speed_kt": 5.0,
            "pressure_hpa": 1004.0,
            "cloud_cover_fraction": 0.0,
            "ceiling_ft_agl": None,
            "precipitating": 0,
            "visibility_m": 9999.0,
        },
    ]

    rows = build_feature_rows(jma, metar)

    assert len(rows) == 1
    assert rows[0]["prior_metar_temp_c"] == 29.0
    assert rows[0]["confirm_jma_lattice_within_30m"] == 1
