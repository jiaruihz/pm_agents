from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1, CITY_FAMILY_CURRENT_BRACKET_NO_V1


def test_city_family_maps_preserve_known_taxonomy_fork() -> None:
    assert len(CITY_FAMILY_CURRENT_BRACKET_NO_V1) == 36
    assert len(CITY_FAMILY_ATLAS_V1) == 36
    assert CITY_FAMILY_CURRENT_BRACKET_NO_V1["Beijing"] == "east_asia_continental"
    assert CITY_FAMILY_ATLAS_V1["Beijing"] == "continental_dry_hot"


def test_city_family_maps_are_identical_except_beijing() -> None:
    differences = {
        city
        for city in set(CITY_FAMILY_CURRENT_BRACKET_NO_V1) | set(CITY_FAMILY_ATLAS_V1)
        if CITY_FAMILY_CURRENT_BRACKET_NO_V1.get(city) != CITY_FAMILY_ATLAS_V1.get(city)
    }
    assert differences == {"Beijing"}
