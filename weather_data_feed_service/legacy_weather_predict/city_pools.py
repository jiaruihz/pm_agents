"""City universe and collection/trading pools for weather paper data.

T1 cities are eligible for paper orders. T2 cities are collected for research
snapshots but must not create paper orders until promoted. There is intentionally
no T3 pool: if a city is known and technically configured, collect it as T2.
"""

from __future__ import annotations


BUY_BOTH = ("BUY_NO", "BUY_YES")
BUY_NO_ONLY = ("BUY_NO",)

# City/side live policy, kept as a JSON-shaped object for easy audit.
# 2026-05-29 change rationale:
# - Default both sides: medium-confidence city x side slices should not cut legs.
# - Madrid re-enters as BUY_NO only: cf +10.0 / live +2.38 on NO; YES is -9.9 live.
# - Shanghai remains BUY_NO only: YES cf -7.5 with win 0; NO side is positive.
# - Paris moves to T2: paper + cf both structurally negative; live positive is one-fill fragile.
# 2026-06-06 change rationale:
# - Amsterdam moves back to T2: three live instances all negative, all-history live PnL -30.43 / ROI -82.0%.
# - BuenosAires moves back to T2: all-history live PnL -28.11 / ROI -49.6%, V2 YES full-loss cluster.
CITY_TRADING_CONFIG = {
    "TRADING_T1_CITIES": [
        "Ankara",
        "Boston",
        "Chengdu",
        "Guangzhou",
        "Istanbul",
        "Jeddah",
        "Karachi",
        "LA",
        "London",
        "Lucknow",
        "Madrid",
        "Manila",
        "Miami",
        "Moscow",
        "Munich",
        "NYC",
        "Phoenix",
        "Seattle",
        "Shanghai",
        "Singapore",
        "Tokyo",
        "Warsaw",
    ],
    "RESEARCH_T2_CITIES": [
        "Atlanta",
        "Austin",
        "Beijing",
        "Busan",
        "Amsterdam",
        "BuenosAires",
        "CapeTown",
        "Chicago",
        "Chongqing",
        "Dallas",
        "Denver",
        "Helsinki",
        "HongKong",
        "Houston",
        "Jakarta",
        "KualaLumpur",
        "Lagos",
        "MexicoCity",
        "Milan",
        "Minneapolis",
        "PanamaCity",
        "Paris",
        "SanFrancisco",
        "SaoPaulo",
        "Seoul",
        "Shenzhen",
        "Taipei",
        "TelAviv",
        "Wellington",
        "Wuhan",
    ],
    "CITY_ALLOWED_SIDES": {
        "Madrid": ["BUY_NO"],
        "Shanghai": ["BUY_NO"],
    },
}

TRADING_T1_CITIES = set(CITY_TRADING_CONFIG["TRADING_T1_CITIES"])


FULL_CITY_CONFIGS = {
    "Amsterdam": {"lat": 52.31, "lon": 4.76, "icao": "EHAM", "tz_offset": 1, "unit": "C", "slug": "amsterdam", "region": "EU"},
    "Ankara": {"lat": 40.13, "lon": 32.99, "icao": "LTAC", "tz_offset": 3, "unit": "C", "slug": "ankara", "region": "EU"},
    "Atlanta": {"lat": 33.64, "lon": -84.43, "icao": "KATL", "tz_offset": -5, "unit": "F", "slug": "atlanta", "region": "US"},
    "Austin": {"lat": 30.19, "lon": -97.67, "icao": "KAUS", "tz_offset": -6, "unit": "F", "slug": "austin", "region": "US"},
    "Beijing": {"lat": 40.08, "lon": 116.58, "icao": "ZBAA", "tz_offset": 8, "unit": "C", "slug": "beijing", "region": "AS"},
    "Boston": {"lat": 42.36, "lon": -71.01, "icao": "KBOS", "tz_offset": -5, "unit": "F", "slug": "boston", "region": "US"},
    "BuenosAires": {"lat": -34.82, "lon": -58.53, "icao": "SAEZ", "tz_offset": -3, "unit": "C", "slug": "buenos-aires", "region": "SA"},
    "Busan": {"lat": 35.18, "lon": 128.94, "icao": "RKPK", "tz_offset": 9, "unit": "C", "slug": "busan", "region": "AS"},
    "CapeTown": {"lat": -33.96, "lon": 18.6, "icao": "FACT", "tz_offset": 2, "unit": "C", "slug": "cape-town", "region": "AF"},
    "Chengdu": {"lat": 30.58, "lon": 103.95, "icao": "ZUUU", "tz_offset": 8, "unit": "C", "slug": "chengdu", "region": "AS"},
    "Chicago": {"lat": 41.79, "lon": -87.75, "icao": "KMDW", "tz_offset": -6, "unit": "F", "slug": "chicago", "region": "US"},
    "Chongqing": {"lat": 29.72, "lon": 106.64, "icao": "ZUCK", "tz_offset": 8, "unit": "C", "slug": "chongqing", "region": "AS"},
    "Dallas": {"lat": 32.85, "lon": -96.85, "icao": "KDAL", "tz_offset": -6, "unit": "F", "slug": "dallas", "region": "US"},
    "Denver": {"lat": 39.72, "lon": -104.75, "icao": "KBKF", "tz_offset": -7, "unit": "F", "slug": "denver", "region": "US"},
    "Guangzhou": {"lat": 23.39, "lon": 113.3, "icao": "ZGGG", "tz_offset": 8, "unit": "C", "slug": "guangzhou", "region": "AS"},
    "Helsinki": {"lat": 60.32, "lon": 24.97, "icao": "EFHK", "tz_offset": 2, "unit": "C", "slug": "helsinki", "region": "EU"},
    "HongKong": {"lat": 22.3, "lon": 114.17, "icao": "VHHH", "tz_offset": 8, "unit": "C", "slug": "hong-kong", "region": "AS"},
    "Houston": {"lat": 29.65, "lon": -95.28, "icao": "KHOU", "tz_offset": -6, "unit": "F", "slug": "houston", "region": "US"},
    "Istanbul": {"lat": 41.26, "lon": 28.74, "icao": "LTFM", "tz_offset": 3, "unit": "C", "slug": "istanbul", "region": "EU"},
    "Jakarta": {"lat": -6.13, "lon": 106.66, "icao": "WIII", "tz_offset": 7, "unit": "C", "slug": "jakarta", "region": "AS"},
    "Jeddah": {"lat": 21.68, "lon": 39.16, "icao": "OEJN", "tz_offset": 3, "unit": "C", "slug": "jeddah", "region": "ME"},
    "Karachi": {"lat": 24.91, "lon": 67.16, "icao": "OPKC", "tz_offset": 5, "unit": "C", "slug": "karachi", "region": "ME"},
    "KualaLumpur": {"lat": 3.13, "lon": 101.55, "icao": "WMSA", "tz_offset": 8, "unit": "C", "slug": "kuala-lumpur", "region": "AS"},
    "LA": {"lat": 33.94, "lon": -118.41, "icao": "KLAX", "tz_offset": -8, "unit": "F", "slug": "los-angeles", "region": "US"},
    "Lagos": {"lat": 6.58, "lon": 3.32, "icao": "DNMM", "tz_offset": 1, "unit": "C", "slug": "lagos", "region": "AF"},
    "London": {"lat": 51.48, "lon": -0.46, "icao": "EGLL", "tz_offset": 0, "unit": "C", "slug": "london", "region": "EU"},
    "Lucknow": {"lat": 26.76, "lon": 80.89, "icao": "VILK", "tz_offset": 5, "unit": "C", "slug": "lucknow", "region": "AS"},
    "Madrid": {"lat": 40.47, "lon": -3.56, "icao": "LEMD", "tz_offset": 1, "unit": "C", "slug": "madrid", "region": "EU"},
    "Manila": {"lat": 14.51, "lon": 121.02, "icao": "RPLL", "tz_offset": 8, "unit": "C", "slug": "manila", "region": "AS"},
    "MexicoCity": {"lat": 19.44, "lon": -99.07, "icao": "MMMX", "tz_offset": -6, "unit": "C", "slug": "mexico-city", "region": "SA"},
    "Miami": {"lat": 25.8, "lon": -80.29, "icao": "KMIA", "tz_offset": -5, "unit": "F", "slug": "miami", "region": "US"},
    "Milan": {"lat": 45.45, "lon": 9.28, "icao": "LIML", "tz_offset": 1, "unit": "C", "slug": "milan", "region": "EU"},
    "Minneapolis": {"lat": 44.88, "lon": -93.22, "icao": "KMSP", "tz_offset": -6, "unit": "F", "slug": "minneapolis", "region": "US"},
    "Moscow": {"lat": 55.6, "lon": 37.26, "icao": "UUWW", "tz_offset": 3, "unit": "C", "slug": "moscow", "region": "EU"},
    "Munich": {"lat": 48.35, "lon": 11.79, "icao": "EDDM", "tz_offset": 1, "unit": "C", "slug": "munich", "region": "EU"},
    "NYC": {"lat": 40.78, "lon": -73.87, "icao": "KLGA", "tz_offset": -5, "unit": "F", "slug": "nyc", "region": "US"},
    "PanamaCity": {"lat": 9.07, "lon": -79.38, "icao": "MPTO", "tz_offset": -5, "unit": "C", "slug": "panama-city", "region": "SA"},
    "Paris": {"lat": 49.01, "lon": 2.55, "icao": "LFPG", "tz_offset": 1, "unit": "C", "slug": "paris", "region": "EU"},
    "Phoenix": {"lat": 33.44, "lon": -112.01, "icao": "KPHX", "tz_offset": -7, "unit": "F", "slug": "phoenix", "region": "US"},
    "SanFrancisco": {"lat": 37.62, "lon": -122.38, "icao": "KSFO", "tz_offset": -8, "unit": "F", "slug": "san-francisco", "region": "US"},
    "SaoPaulo": {"lat": -23.43, "lon": -46.47, "icao": "SBGR", "tz_offset": -3, "unit": "C", "slug": "sao-paulo", "region": "SA"},
    "Seattle": {"lat": 47.45, "lon": -122.31, "icao": "KSEA", "tz_offset": -8, "unit": "F", "slug": "seattle", "region": "US"},
    "Seoul": {"lat": 37.47, "lon": 126.45, "icao": "RKSI", "tz_offset": 9, "unit": "C", "slug": "seoul", "region": "AS"},
    "Shanghai": {"lat": 31.14, "lon": 121.81, "icao": "ZSPD", "tz_offset": 8, "unit": "C", "slug": "shanghai", "region": "AS"},
    "Shenzhen": {"lat": 22.64, "lon": 113.81, "icao": "ZGSZ", "tz_offset": 8, "unit": "C", "slug": "shenzhen", "region": "AS"},
    "Singapore": {"lat": 1.36, "lon": 103.99, "icao": "WSSS", "tz_offset": 8, "unit": "C", "slug": "singapore", "region": "AS"},
    "Taipei": {"lat": 25.07, "lon": 121.55, "icao": "RCSS", "tz_offset": 8, "unit": "C", "slug": "taipei", "region": "AS"},
    "TelAviv": {"lat": 32.01, "lon": 34.89, "icao": "LLBG", "tz_offset": 2, "unit": "C", "slug": "tel-aviv", "region": "ME"},
    "Tokyo": {"lat": 35.55, "lon": 139.78, "icao": "RJTT", "tz_offset": 9, "unit": "C", "slug": "tokyo", "region": "AS"},
    "Warsaw": {"lat": 52.17, "lon": 20.97, "icao": "EPWA", "tz_offset": 1, "unit": "C", "slug": "warsaw", "region": "EU"},
    "Wellington": {"lat": -41.33, "lon": 174.81, "icao": "NZWN", "tz_offset": 12, "unit": "C", "slug": "wellington", "region": "OC"},
    "Wuhan": {"lat": 30.78, "lon": 114.21, "icao": "ZHHH", "tz_offset": 8, "unit": "C", "slug": "wuhan", "region": "AS"},
}


RESEARCH_T2_CITIES = set(CITY_TRADING_CONFIG["RESEARCH_T2_CITIES"])
CITY_POOL_BY_CITY = {
    **{city: "t1_trading" for city in TRADING_T1_CITIES},
    **{city: "t2_research" for city in RESEARCH_T2_CITIES},
}

CITY_ALLOWED_SIDES = {
    city: BUY_BOTH for city in TRADING_T1_CITIES
}
CITY_ALLOWED_SIDES.update(
    {city: tuple(sides) for city, sides in CITY_TRADING_CONFIG["CITY_ALLOWED_SIDES"].items()}
)


def city_pool(city: str) -> str:
    return CITY_POOL_BY_CITY.get(city, "t2_research")


def eligible_for_paper_order(city: str) -> bool:
    return city in TRADING_T1_CITIES


def allowed_sides_for_city(city: str) -> tuple[str, ...]:
    return CITY_ALLOWED_SIDES.get(city, BUY_BOTH)


def side_allowed_for_city(city: str, side: str) -> bool:
    return side in allowed_sides_for_city(city)


_configured = TRADING_T1_CITIES | RESEARCH_T2_CITIES
_missing = set(FULL_CITY_CONFIGS) - _configured
_unknown = _configured - set(FULL_CITY_CONFIGS)
_overlap = TRADING_T1_CITIES & RESEARCH_T2_CITIES
if _missing or _unknown or _overlap:
    raise ValueError(
        "Invalid CITY_TRADING_CONFIG: "
        f"missing={sorted(_missing)} unknown={sorted(_unknown)} overlap={sorted(_overlap)}"
    )
