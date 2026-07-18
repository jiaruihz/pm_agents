from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


CITY_TIMEZONE: dict[str, str] = {
    "Amsterdam": "Europe/Amsterdam",
    "Ankara": "Europe/Istanbul",
    "Atlanta": "America/New_York",
    "Austin": "America/Chicago",
    "Beijing": "Asia/Shanghai",
    "Boston": "America/New_York",
    "BuenosAires": "America/Argentina/Buenos_Aires",
    "Busan": "Asia/Seoul",
    "CapeTown": "Africa/Johannesburg",
    "Chengdu": "Asia/Shanghai",
    "Chicago": "America/Chicago",
    "Chongqing": "Asia/Shanghai",
    "Dallas": "America/Chicago",
    "Denver": "America/Denver",
    "Guangzhou": "Asia/Shanghai",
    "Helsinki": "Europe/Helsinki",
    "HongKong": "Asia/Hong_Kong",
    "Houston": "America/Chicago",
    "Istanbul": "Europe/Istanbul",
    "Jakarta": "Asia/Jakarta",
    "Jeddah": "Asia/Riyadh",
    "Karachi": "Asia/Karachi",
    "KualaLumpur": "Asia/Kuala_Lumpur",
    "LA": "America/Los_Angeles",
    "Lagos": "Africa/Lagos",
    "London": "Europe/London",
    "Lucknow": "Asia/Kolkata",
    "Madrid": "Europe/Madrid",
    "Manila": "Asia/Manila",
    "MexicoCity": "America/Mexico_City",
    "Miami": "America/New_York",
    "Milan": "Europe/Rome",
    "Minneapolis": "America/Chicago",
    "Moscow": "Europe/Moscow",
    "Munich": "Europe/Berlin",
    "NYC": "America/New_York",
    "PanamaCity": "America/Panama",
    "Paris": "Europe/Paris",
    "Phoenix": "America/Phoenix",
    "SanFrancisco": "America/Los_Angeles",
    "SaoPaulo": "America/Sao_Paulo",
    "Seattle": "America/Los_Angeles",
    "Seoul": "Asia/Seoul",
    "Shanghai": "Asia/Shanghai",
    "Shenzhen": "Asia/Shanghai",
    "Singapore": "Asia/Singapore",
    "Taipei": "Asia/Taipei",
    "TelAviv": "Asia/Jerusalem",
    "Tokyo": "Asia/Tokyo",
    "Warsaw": "Europe/Warsaw",
    "Wellington": "Pacific/Auckland",
    "Wuhan": "Asia/Shanghai",
}

_NORMALIZED_CITY_TIMEZONE = {key.lower(): value for key, value in CITY_TIMEZONE.items()}


@dataclass(frozen=True)
class StationLike:
    city: str
    utc_offset: int = 0
    timezone_name: str | None = None
    timezone: str | None = None


def parse_now_utc(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def city_timezone_name(city_key: str | None) -> str | None:
    if not city_key:
        return None
    return CITY_TIMEZONE.get(city_key) or _NORMALIZED_CITY_TIMEZONE.get(str(city_key).lower())


def station_timezone(station: Any) -> ZoneInfo | timezone:
    timezone_name = (
        getattr(station, "timezone_name", None)
        or getattr(station, "timezone", None)
        or city_timezone_name(getattr(station, "city", None))
    )
    if timezone_name:
        try:
            return ZoneInfo(str(timezone_name))
        except Exception:
            pass
    return timezone(timedelta(hours=int(getattr(station, "utc_offset", 0))))


def timezone_label(tz: ZoneInfo | timezone) -> str:
    return getattr(tz, "key", None) or str(tz)


def city_local_date(city: str, now_utc: str | datetime | None = None, timezone_name: str | None = None) -> date:
    tz_name = timezone_name or city_timezone_name(city)
    if not tz_name:
        raise ValueError(f"missing timezone for city={city!r}")
    return parse_now_utc(now_utc).astimezone(ZoneInfo(tz_name)).date()


def city_local_datetime(city: str, now_utc: str | datetime | None = None, timezone_name: str | None = None) -> datetime:
    tz_name = timezone_name or city_timezone_name(city)
    if not tz_name:
        raise ValueError(f"missing timezone for city={city!r}")
    return parse_now_utc(now_utc).astimezone(ZoneInfo(tz_name))


def city_local_hour(city: str, now_utc: str | datetime | None = None, timezone_name: str | None = None) -> float:
    local = city_local_datetime(city, now_utc, timezone_name)
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def city_in_local_hour_window(
    city: str,
    now_utc: str | datetime | None = None,
    *,
    start_hour: float,
    end_hour: float,
    timezone_name: str | None = None,
) -> bool:
    start = float(start_hour) % 24.0
    end = float(end_hour) % 24.0
    if abs(start - end) < 1e-9:
        return True
    hour = city_local_hour(city, now_utc, timezone_name)
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def local_settle_utc(
    city: str,
    target_date: str | date,
    *,
    settle_hour_local: int = 22,
    timezone_name: str | None = None,
) -> datetime:
    tz_name = timezone_name or city_timezone_name(city)
    if not tz_name:
        raise ValueError(f"missing timezone for city={city!r}")
    target = target_date if isinstance(target_date, date) else date.fromisoformat(str(target_date))
    local_dt = datetime(
        target.year,
        target.month,
        target.day,
        settle_hour_local,
        0,
        0,
        tzinfo=ZoneInfo(tz_name),
    )
    return local_dt.astimezone(timezone.utc)


def city_scan_dates(
    city: str,
    now_utc: str | datetime | None = None,
    *,
    timezone_name: str | None = None,
    explicit_target_date: str | date | None = None,
    include_tomorrow: bool = True,
) -> list[str]:
    if explicit_target_date is not None:
        if isinstance(explicit_target_date, date):
            return [explicit_target_date.isoformat()]
        return [str(explicit_target_date)]
    local = city_local_date(city, now_utc, timezone_name)
    out = [local.isoformat()]
    if include_tomorrow:
        out.append((local + timedelta(days=1)).isoformat())
    return out


def unique_city_scan_dates(
    cities: Iterable[str],
    now_utc: str | datetime | None = None,
    *,
    explicit_target_date: str | date | None = None,
) -> list[str]:
    dates: set[str] = set()
    for city in cities:
        dates.update(city_scan_dates(city, now_utc, explicit_target_date=explicit_target_date))
    return sorted(dates)
