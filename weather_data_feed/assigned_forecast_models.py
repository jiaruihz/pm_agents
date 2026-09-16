"""Single authority for the historical fixed city forecast-model assignment."""

from __future__ import annotations


CITY_MODEL: dict[str, str] = {
    "Amsterdam": "ecmwf", "Ankara": "ecmwf", "Atlanta": "gfs",
    "BuenosAires": "ecmwf", "Busan": "ecmwf", "CapeTown": "ecmwf",
    "Chengdu": "ecmwf", "Chicago": "gfs", "Miami": "gfs", "Austin": "gfs",
    "NYC": "gfs", "Chongqing": "ecmwf", "Dallas": "ecmwf", "Denver": "gfs",
    "Guangzhou": "gfs", "Helsinki": "ecmwf", "HongKong": "ecmwf",
    "Houston": "gfs", "Istanbul": "ecmwf", "Jakarta": "ecmwf",
    "Jeddah": "ecmwf", "Karachi": "ecmwf", "KualaLumpur": "ecmwf",
    "LA": "gfs", "Boston": "gfs", "Phoenix": "gfs", "Lagos": "ecmwf",
    "London": "ecmwf", "Madrid": "ecmwf", "Warsaw": "ecmwf",
    "Lucknow": "ecmwf", "Manila": "gfs", "MexicoCity": "ecmwf",
    "Milan": "ecmwf", "Minneapolis": "gfs", "Moscow": "ecmwf",
    "Munich": "ecmwf", "PanamaCity": "gfs", "Beijing": "ecmwf",
    "Seoul": "ecmwf", "Paris": "gfs", "Tokyo": "gfs", "Shanghai": "gfs",
    "SanFrancisco": "ecmwf", "SaoPaulo": "ecmwf", "Seattle": "gfs",
    "Shenzhen": "ecmwf", "Singapore": "gfs", "Taipei": "gfs",
    "TelAviv": "gfs", "Wellington": "gfs", "Wuhan": "ecmwf",
}


def assigned_model_family(city: str) -> str:
    return CITY_MODEL.get(city, "gfs")


def assigned_single_run_model_key(city: str) -> str:
    return "ecmwf_ifs025" if assigned_model_family(city) == "ecmwf" else "gfs_global"
