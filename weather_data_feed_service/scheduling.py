"""Shared scheduling helpers for data-feed service producers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from weather_data_feed import city_in_local_hour_window, city_local_hour
from weather_clock_contract import parse_utc_or_none


@dataclass(frozen=True)
class ActiveLocalWindow:
    start_hour: float | None = None
    end_hour: float | None = None

    @property
    def enabled(self) -> bool:
        return self.start_hour is not None and self.end_hour is not None

    def as_payload(self) -> dict[str, float] | None:
        if not self.enabled:
            return None
        return {"start_hour": float(self.start_hour), "end_hour": float(self.end_hour)}


def filter_jobs_by_local_window(
    jobs: Iterable[tuple[str, str, dict[str, Any]]],
    *,
    now_utc: datetime,
    window: ActiveLocalWindow,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    active: list[tuple[str, str]] = []
    skipped: list[dict[str, Any]] = []
    for source, city, meta in jobs:
        timezone_name = str((meta or {}).get("timezone_name") or "")
        if window.enabled and not city_in_local_hour_window(
            city,
            now_utc,
            start_hour=float(window.start_hour),
            end_hour=float(window.end_hour),
            timezone_name=timezone_name or None,
        ):
            skipped.append(
                {
                    "source": source,
                    "city": city,
                    "timezone_name": timezone_name,
                    "local_hour": round(city_local_hour(city, now_utc, timezone_name or None), 3),
                    "reason": "outside_active_local_window",
                }
            )
            continue
        active.append((source, city))
    return active, skipped


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value)


def filter_jobs_by_min_interval(
    jobs: Iterable[tuple[str, str]],
    *,
    now_utc: datetime,
    min_interval_by_source: dict[str, float],
    last_attempt_by_job: dict[str, str],
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    due: list[tuple[str, str]] = []
    skipped: list[dict[str, Any]] = []
    for source, city in jobs:
        interval = float(min_interval_by_source.get(source, 0.0) or 0.0)
        key = f"{source}:{city}"
        last = parse_utc(last_attempt_by_job.get(key))
        age = (now_utc - last).total_seconds() if last else None
        if interval > 0 and age is not None and age < interval:
            skipped.append(
                {
                    "source": source,
                    "city": city,
                    "last_attempt_utc": last.isoformat(),
                    "age_sec": round(age, 3),
                    "min_interval_sec": interval,
                    "reason": "inside_source_min_interval",
                }
            )
            continue
        due.append((source, city))
    return due, skipped
