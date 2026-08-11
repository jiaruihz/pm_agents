"""Operational fixed-24h forecast curves for intraday model parity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from weather_data_feed.forecast_hourly_curves import (
    build_curve_row,
    write_forecast_hourly_curve_capture,
)


API = "https://previous-runs-api.open-meteo.com/v1/forecast"


def collect_amsterdam_ecmwf_day1(
    *, output_root: Path, target_dates: list[str], now_utc: datetime
) -> dict:
    dates = sorted(set(target_dates))
    if not dates:
        return {"status": "not_requested", "rows": 0, "capture_path": None}
    parameters = {
        "latitude": 52.3105,
        "longitude": 4.7683,
        "start_date": dates[0],
        "end_date": dates[-1],
        "timezone": "Europe/Amsterdam",
        "models": "ecmwf_ifs025",
        "hourly": "temperature_2m_previous_day1",
    }
    request = Request(
        f"{API}?{urlencode(parameters)}",
        headers={"User-Agent": "pm-agents-weather-data-feed/1"},
    )
    detected = datetime.now(timezone.utc)
    with urlopen(request, timeout=90) as response:
        payload_bytes = response.read()
    detected = datetime.now(timezone.utc)
    payload = json.loads(payload_bytes)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    values = hourly.get("temperature_2m_previous_day1") or []
    grouped: dict[str, list[tuple[str, float]]] = {date: [] for date in dates}
    for time_local, temperature in zip(times, values):
        target_date = str(time_local)[:10]
        if target_date in grouped and temperature is not None:
            grouped[target_date].append((str(time_local), float(temperature)))
    rows = []
    for target_date in dates:
        curve = grouped[target_date]
        if len(curve) != 24:
            continue
        temperatures_c = [value for _, value in curve]
        peak_index = max(range(len(curve)), key=lambda index: temperatures_c[index])
        hourly_curve = [
            {"time_local": time_local, "temperature_f": round(value * 9 / 5 + 32, 3)}
            for time_local, value in curve
        ]
        values_hash = hashlib.sha256(
            json.dumps(curve, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        peak_time = curve[peak_index][0]
        rows.append({
            **build_curve_row(
                snapshot_ts_utc=now_utc.astimezone(timezone.utc).isoformat(),
                city="Amsterdam", target_date=target_date,
                forecast_source="open_meteo_previous_runs_ecmwf_day1",
                forecast_model="ecmwf_ifs025",
                forecast_assigned_model="ecmwf_ifs025",
                forecast_values_hash=values_hash,
                hourly_curve=hourly_curve,
                forecast_max_f=max(temperatures_c) * 9 / 5 + 32,
                forecast_peak_hour_local=int(peak_time[11:13]),
                forecast_peak_time_local=peak_time,
                forecast_peak_hour_utc=None, forecast_peak_time_utc=None,
                forecast_timezone="Europe/Amsterdam",
                forecast_timezone_abbreviation=str(payload.get("timezone_abbreviation") or ""),
                forecast_utc_offset_seconds=payload.get("utc_offset_seconds"),
                forecast_generationtime_ms=payload.get("generationtime_ms"),
                forecast_model_fallback_reason=None,
                forecast_detected_at_utc=detected.isoformat(),
                latitude=52.3105, longitude=4.7683,
            ),
            "forecast_lead_days": 1,
            "forecast_lead_semantics": "fixed_24_hours_before_valid_time",
        })
    capture = write_forecast_hourly_curve_capture(output_root, rows) if rows else None
    return {
        "status": "ok" if len(rows) == len(dates) else "degraded",
        "requested_target_dates": dates,
        "rows": len(rows),
        "capture_path": str(capture) if capture else None,
        "source": "open_meteo_previous_runs_ecmwf_day1",
    }
