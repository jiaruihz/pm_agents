"""Dedicated owner for operational Open-Meteo hourly forecast curves.

The market snapshot collector is intentionally a cache-only consumer.  This
one-shot producer is scheduled independently by the production controller.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_data_feed.forecast_hourly_curves import build_curve_row, write_forecast_hourly_curve_capture
from weather_data_feed_service.legacy_weather_predict import paper_snapshot as snapshot


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _selected_models() -> dict[str, str]:
    """Match the model/error pairing used by the snapshot consumer."""
    selected: dict[str, str] = {}
    for city, cfg in snapshot.CITIES.items():
        assigned = snapshot.CITY_MODEL.get(city, "gfs")
        errors = (
            snapshot.compute_ecmwf_error_distribution(city, cfg)
            if assigned == "ecmwf"
            else snapshot.compute_error_distribution(city, cfg)
        )
        if errors is not None:
            selected[city] = assigned
            continue
        fallback = "gfs" if assigned == "ecmwf" else "ecmwf"
        fallback_errors = (
            snapshot.compute_error_distribution(city, cfg)
            if fallback == "gfs"
            else snapshot.compute_ecmwf_error_distribution(city, cfg)
        )
        if fallback_errors is not None:
            selected[city] = fallback
    return selected


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect(*, output_root: Path, target_date: str | None = None, now_utc: datetime | None = None) -> dict[str, Any]:
    captured_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Each one-shot must discover captures written by previous runs.
    snapshot._FORECAST_CURVE_CACHE = None
    snapshot._FORECAST_LIVE_DISABLED_REASON = None
    models = _selected_models()
    rows: list[dict[str, Any]] = []
    reused = 0
    failed: list[dict[str, str]] = []
    expected = 0

    for city, cfg in snapshot.CITIES.items():
        model = models.get(city)
        assigned = snapshot.CITY_MODEL.get(city, "gfs")
        for date_text in snapshot.city_scan_dates(captured_at, city, target_date):
            settle = snapshot.local_settle_utc(city, date_text)
            hours_to_settle = (settle - captured_at).total_seconds() / 3600
            if hours_to_settle < 0 or hours_to_settle > 50:
                continue
            expected += 1
            if model is None:
                failed.append(
                    {
                        "city": city,
                        "target_date": date_text,
                        "reason": "model_error_distribution_unavailable",
                    }
                )
                continue
            info = snapshot._refresh_live_forecast(None, model, city, cfg, date_text)
            if info is None:
                failed.append({"city": city, "target_date": date_text, "reason": "forecast_unavailable"})
                continue
            if info.get("cache_fallback"):
                reused += 1
                continue
            rows.append(
                build_curve_row(
                    snapshot_ts_utc=_utc(captured_at),
                    city=city,
                    target_date=date_text,
                    forecast_source=info["source_api"],
                    forecast_model=str(info.get("source_model") or model),
                    forecast_assigned_model=assigned,
                    forecast_values_hash=info["values_hash"],
                    hourly_curve=info["hourly_curve"],
                    forecast_max_f=info["max_f"],
                    forecast_peak_hour_local=info["peak_hour_local"],
                    forecast_peak_time_local=info["peak_time_local"],
                    forecast_peak_hour_utc=info["peak_hour_utc"],
                    forecast_peak_time_utc=info["peak_time_utc"],
                    forecast_timezone=info["timezone"],
                    forecast_timezone_abbreviation=info.get("timezone_abbreviation"),
                    forecast_utc_offset_seconds=info["utc_offset_seconds"],
                    forecast_generationtime_ms=info.get("generationtime_ms"),
                    forecast_model_fallback_reason=(
                        "assigned_error_distribution_unavailable" if model != assigned else None
                    ),
                    forecast_detected_at_utc=info.get("detected_at_utc"),
                    latitude=cfg.get("lat"),
                    longitude=cfg.get("lon"),
                )
            )

    archive = write_forecast_hourly_curve_capture(output_root, rows) if rows else None
    available = len(rows) + reused
    coverage_ratio = available / expected if expected else 0.0
    # The operational snapshot contract tolerates a small number of cities
    # without usable model/error pairing. Keep the exact misses visible while
    # avoiding a global dependency cascade when at least 95% remains usable.
    status = "ok" if expected > 0 and coverage_ratio >= 0.95 else "degraded"
    return {
        "schema_version": "weather_forecast_curve_collector_status_v1",
        "status": status,
        "captured_at_utc": _utc(captured_at),
        "owner": "weather_forecast_curve_collector_v1",
        "expected_city_targets": expected,
        "fresh_city_targets": len(rows),
        "reused_city_targets": reused,
        "available_city_targets": available,
        "coverage_ratio": round(coverage_ratio, 6),
        "failed_count": len(failed),
        "failed_examples": failed[:20],
        "open_meteo_disabled_reason": snapshot._FORECAST_LIVE_DISABLED_REASON,
        "refresh_status": (
            "provider_rate_limited"
            if snapshot._FORECAST_LIVE_DISABLED_REASON == "open_meteo_http_429"
            else ("fresh_capture" if rows else "cache_reused")
        ),
        "capture_path": str(archive) if archive else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default=os.environ.get(
            "WEATHER_DATA_FEED_TARGETED_OUTPUT_ROOT",
            "/Volumes/jrs/weather_data_feed_service_runtime/targeted_output",
        ),
    )
    parser.add_argument(
        "--status-path",
        default="/Volumes/jrs/weather_data_feed_service_runtime/output/forecast_curve_collector/latest.json",
    )
    parser.add_argument("--target-date", default=None)
    args = parser.parse_args(argv)
    summary = collect(output_root=Path(args.output_root), target_date=args.target_date)
    _write_summary(Path(args.status_path), summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
