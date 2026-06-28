from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


FAST_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "iem_asos_madishf_latest": ("temp_round_f", "main_round_f", "rmk_round_f"),
    "iem_asos_latest_raw": ("temp_round_f", "main_round_f", "rmk_round_f"),
    "aviationweather_cache_csv": ("main_round_f", "temp_round_f"),
    "aviationweather_metar": ("main_round_f", "temp_round_f"),
    "noaa_tgftp_station_txt": ("main_round_f", "temp_round_f"),
    "checkwx_html": ("main_round_f", "temp_round_f"),
}


@dataclass(frozen=True)
class RmkSourceBasisState:
    city: str
    target_date: str
    status: str
    proxy_source: str = "iem_asos_routine_latest"
    proxy_round_f: int | None = None
    proxy_report_ts_utc: str = ""
    proxy_detect_ts_utc: str = ""
    proxy_detected_after_report_sec: float | None = None
    routine_main_round_f: int | None = None
    wu_current_round_f: int | None = None
    wu_max_f_since_7am: int | None = None
    wu_current_contradicts_proxy: bool = False
    wu_report_ts_utc: str = ""
    wu_detect_ts_utc: str = ""
    wu_detected_after_report_sec: float | None = None
    wu_report_lag_vs_proxy_sec: float | None = None
    wu_temporal_relation_to_proxy: str = "missing"
    fast_round_f: int | None = None
    false_cross_sources: dict[str, int] = field(default_factory=dict)

    @property
    def has_false_cross(self) -> bool:
        return bool(self.false_cross_sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "target_date": self.target_date,
            "status": self.status,
            "proxy_source": self.proxy_source,
            "proxy_round_f": self.proxy_round_f,
            "proxy_report_ts_utc": self.proxy_report_ts_utc,
            "proxy_detect_ts_utc": self.proxy_detect_ts_utc,
            "proxy_detected_after_report_sec": self.proxy_detected_after_report_sec,
            "routine_main_round_f": self.routine_main_round_f,
            "wu_current_round_f": self.wu_current_round_f,
            "wu_max_f_since_7am": self.wu_max_f_since_7am,
            "wu_current_contradicts_proxy": self.wu_current_contradicts_proxy,
            "wu_report_ts_utc": self.wu_report_ts_utc,
            "wu_detect_ts_utc": self.wu_detect_ts_utc,
            "wu_detected_after_report_sec": self.wu_detected_after_report_sec,
            "wu_report_lag_vs_proxy_sec": self.wu_report_lag_vs_proxy_sec,
            "wu_temporal_relation_to_proxy": self.wu_temporal_relation_to_proxy,
            "fast_round_f": self.fast_round_f,
            "false_cross_sources": dict(self.false_cross_sources),
            "has_false_cross": self.has_false_cross,
        }


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def lag_sec(left: Any, right: Any) -> float | None:
    left_dt = parse_dt(left)
    right_dt = parse_dt(right)
    if left_dt is None or right_dt is None:
        return None
    return round((left_dt - right_dt).total_seconds(), 3)


def latest_sources(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        city = row.get("city")
        source = row.get("source")
        if city and source:
            latest[(str(city), str(source))] = row
    return latest


def _first_int(row: dict[str, Any], fields: tuple[str, ...]) -> int | None:
    for field in fields:
        value = int_or_none(row.get(field))
        if value is not None:
            return value
    return None


def rmk_source_basis_state(city: str, latest_by_source: dict[tuple[str, str], dict[str, Any]]) -> RmkSourceBasisState:
    routine = latest_by_source.get((city, "iem_asos_routine_latest"), {})
    target_date = str(routine.get("target_date") or "")
    proxy_round_f = int_or_none(routine.get("rmk_round_f"))
    if proxy_round_f is None:
        return RmkSourceBasisState(
            city=city,
            target_date=target_date,
            status="missing_routine_rmk",
            routine_main_round_f=int_or_none(routine.get("main_round_f")),
        )

    false_cross_sources: dict[str, int] = {}
    for source_name, fields in FAST_SOURCE_FIELDS.items():
        row = latest_by_source.get((city, source_name), {})
        if row.get("status") not in {None, "", "ok"}:
            continue
        value = _first_int(row, fields)
        if value is not None and value > proxy_round_f:
            false_cross_sources[source_name] = value

    wu = latest_by_source.get((city, "weather_com_current"), {})
    wu_current_round_f = int_or_none(wu.get("temp_round_f"))
    wu_max_f_since_7am = int_or_none(wu.get("max_temp_f_since_7am"))
    wu_report_ts = str(wu.get("source_report_ts_utc") or "")
    proxy_report_ts = str(routine.get("source_report_ts_utc") or "")
    wu_report_lag = lag_sec(wu_report_ts, proxy_report_ts)
    if not wu_report_ts:
        wu_relation = "missing"
    elif wu_report_lag is None:
        wu_relation = "unparseable"
    elif wu_report_lag >= 0:
        wu_relation = "newer_or_equal_proxy"
    else:
        wu_relation = "older_than_proxy"
    wu_current_contradicts_proxy = any(
        value is not None and value > proxy_round_f
        for value in (wu_current_round_f, wu_max_f_since_7am)
    )
    fast_round_f = max(false_cross_sources.values()) if false_cross_sources else None
    return RmkSourceBasisState(
        city=city,
        target_date=target_date or str(wu.get("target_date") or ""),
        status="false_cross" if false_cross_sources else "aligned_or_no_fast_cross",
        proxy_round_f=proxy_round_f,
        proxy_report_ts_utc=proxy_report_ts,
        proxy_detect_ts_utc=str(routine.get("local_detect_ts_utc") or ""),
        proxy_detected_after_report_sec=None
        if routine.get("detected_after_report_sec") is None
        else float(routine["detected_after_report_sec"]),
        routine_main_round_f=int_or_none(routine.get("main_round_f")),
        wu_current_round_f=wu_current_round_f,
        wu_max_f_since_7am=wu_max_f_since_7am,
        wu_current_contradicts_proxy=wu_current_contradicts_proxy,
        wu_report_ts_utc=wu_report_ts,
        wu_detect_ts_utc=str(wu.get("local_detect_ts_utc") or ""),
        wu_detected_after_report_sec=float_or_none(wu.get("detected_after_report_sec")),
        wu_report_lag_vs_proxy_sec=wu_report_lag,
        wu_temporal_relation_to_proxy=wu_relation,
        fast_round_f=fast_round_f,
        false_cross_sources=false_cross_sources,
    )
