from weather_data_feed.sky_cover import SKY_COVER_CODE


def test_sky_cover_code_preserves_legacy_mapping() -> None:
    assert SKY_COVER_CODE == {
        "CLR": 0,
        "SKC": 0,
        "NSC": 0,
        "NCD": 0,
        "CAVOK": 0,
        "FEW": 1,
        "SCT": 2,
        "BKN": 3,
        "OVC": 4,
        "VV": 4,
    }
