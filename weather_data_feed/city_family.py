"""Shared city climate-family reference maps.

These labels are descriptive mechanism groupings used by weather research
features. They are not city allowlists or live trading gates.
"""

from __future__ import annotations

CITY_FAMILY_CURRENT_BRACKET_NO_V1 = {
    "Amsterdam": "europe_cloud_break",
    "Helsinki": "europe_cloud_break",
    "Madrid": "europe_cloud_break",
    "Munich": "europe_cloud_break",
    "Warsaw": "europe_cloud_break",
    "Ankara": "continental_dry_hot",
    "Austin": "continental_dry_hot",
    "Dallas": "continental_dry_hot",
    "Denver": "continental_dry_hot",
    "Jeddah": "continental_dry_hot",
    "Karachi": "continental_dry_hot",
    "Lucknow": "continental_dry_hot",
    "Atlanta": "humid_low_latitude",
    "Busan": "humid_low_latitude",
    "Chengdu": "humid_low_latitude",
    "Chongqing": "humid_low_latitude",
    "Guangzhou": "humid_low_latitude",
    "Houston": "humid_low_latitude",
    "Manila": "humid_low_latitude",
    "Miami": "humid_low_latitude",
    "Shanghai": "humid_low_latitude",
    "Singapore": "humid_low_latitude",
    "Taipei": "humid_low_latitude",
    "Tokyo": "humid_low_latitude",
    "Wuhan": "humid_low_latitude",
    "BuenosAires": "southern_or_maritime",
    "CapeTown": "southern_or_maritime",
    "Istanbul": "southern_or_maritime",
    "LA": "southern_or_maritime",
    "NYC": "southern_or_maritime",
    "SanFrancisco": "southern_or_maritime",
    "SaoPaulo": "southern_or_maritime",
    "Seattle": "southern_or_maritime",
    "TelAviv": "southern_or_maritime",
    "Wellington": "southern_or_maritime",
    "Beijing": "east_asia_continental",
}

CITY_FAMILY_ATLAS_V1 = {
    **CITY_FAMILY_CURRENT_BRACKET_NO_V1,
    "Beijing": "continental_dry_hot",
}
