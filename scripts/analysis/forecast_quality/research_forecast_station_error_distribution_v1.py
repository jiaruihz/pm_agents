#!/usr/bin/env python3
"""Forecast max vs observed station max error distribution.

The grain is city + target_date + forecast snapshot.  Actual max is built from
data-feed source_events, not from market settlement, so this is a station-basis
diagnostic rather than a PnL backtest.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_EVENTS = Path("/home/jiarui/projects/weather_data_feed_service_runtime/output/source_events/sources.jsonl")
DEFAULT_SNAPSHOT_DIR = Path("/home/jiarui/projects/weather_data_feed_service_runtime/output/paper_snapshots")
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/forecast_station_error_distribution_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-06/2026-06-30-forecast-station-error-distribution-v1.md"
SOURCE_PROFILES = ROOT / "weather_data_feed/source_profiles.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-events", default=str(DEFAULT_SOURCE_EVENTS))
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--start-date", default="2026-06-20")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--report", default=str(REPORT_PATH))
    return parser.parse_args()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def load_profiles(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["city"]): row for row in payload.get("source_profiles", [])}


def native_from_c(temp_c: float, unit: str) -> float:
    return temp_c * 9.0 / 5.0 + 32.0 if str(unit).upper() == "F" else temp_c


def percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - pos) + values[hi] * (pos - lo)


def summary(rows: list[dict[str, Any]], group_cols: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(col, "") for col in group_cols)].append(row)
    out = []
    for key, group in groups.items():
        errors = [float(row["error_native"]) for row in group if math.isfinite(float(row["error_native"]))]
        under = [err for err in errors if err <= -1.0]
        severe_under = [err for err in errors if err <= -2.0]
        over = [err for err in errors if err >= 1.0]
        item = {col: key[i] for i, col in enumerate(group_cols)}
        item.update(
            {
                "rows": len(errors),
                "city_dates": len({(row["city"], row["target_date"]) for row in group}),
                "mean_error": round(statistics.fmean(errors), 4) if errors else None,
                "median_error": round(percentile(errors, 0.50), 4) if errors else None,
                "p10_error": round(percentile(errors, 0.10), 4) if errors else None,
                "p90_error": round(percentile(errors, 0.90), 4) if errors else None,
                "under_by_1_rate": round(len(under) / len(errors), 4) if errors else None,
                "under_by_2_rate": round(len(severe_under) / len(errors), 4) if errors else None,
                "over_by_1_rate": round(len(over) / len(errors), 4) if errors else None,
                "min_error": round(min(errors), 4) if errors else None,
                "max_error": round(max(errors), 4) if errors else None,
            }
        )
        out.append(item)
    return sorted(out, key=lambda row: (-int(row["rows"]), str(tuple(row.get(col, "") for col in group_cols))))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                keys.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def load_actual_station_max(source_events: Path, profiles: dict[str, dict[str, Any]], start: str, end: str) -> dict[tuple[str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    with source_events.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            if not city or not (start <= target_date <= end):
                continue
            if str(row.get("status") or "") != "ok":
                continue
            temp_c = safe_float(row.get("temp_c"))
            if not math.isfinite(temp_c):
                continue
            profile = profiles.get(city, {})
            unit = str(row.get("unit") or profile.get("unit") or "C")
            native = native_from_c(temp_c, unit)
            key = (city, target_date)
            source_counts[str(row.get("source") or "")] += 1
            old = by_key.get(key)
            if old is None or native > float(old["actual_station_max_native"]):
                by_key[key] = {
                    "city": city,
                    "target_date": target_date,
                    "unit": unit,
                    "actual_station_max_native": native,
                    "actual_station_max_c": temp_c,
                    "actual_max_report_ts_utc": row.get("source_report_ts_utc"),
                    "actual_max_detect_ts_utc": row.get("local_detect_ts_utc"),
                    "station": row.get("station") or profile.get("official_station_or_feed") or "",
                    "actual_source": row.get("source") or "",
                    "mapping_rule": row.get("mapping_rule") or profile.get("mapping_rule") or "",
                    "settlement_source_class": row.get("settlement_source_class")
                    or profile.get("settlement_source_class")
                    or "",
                    "raw_metar": row.get("raw_metar") or "",
                }
    return by_key


def snapshot_rows(snapshot_dir: Path, profiles: dict[str, dict[str, Any]], actual: dict[tuple[str, str], dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot_ts = payload.get("ts_utc") or payload.get("generated_at_utc") or ""
        snapshot_dt = parse_dt(snapshot_ts)
        for rec in payload.get("records", []):
            city = str(rec.get("city") or "")
            target_date = str(rec.get("target_date") or "")
            if not city or not (start <= target_date <= end):
                continue
            actual_row = actual.get((city, target_date))
            if not actual_row:
                continue
            forecast = safe_float(rec.get("forecast_max_native"))
            if not math.isfinite(forecast):
                continue
            forecast_source = str(rec.get("forecast_source") or "")
            model_version = str(rec.get("model_version") or rec.get("forecast_peak_source") or "")
            key = (city, target_date, snapshot_ts, forecast_source, model_version, forecast)
            if key in seen:
                continue
            seen.add(key)
            profile = profiles.get(city, {})
            tz_name = str(profile.get("timezone_name") or "UTC")
            local_hour = None
            if snapshot_dt is not None:
                local_dt = snapshot_dt.astimezone(ZoneInfo(tz_name))
                local_hour = local_dt.hour + local_dt.minute / 60.0 + local_dt.second / 3600.0
            actual_max = float(actual_row["actual_station_max_native"])
            error = forecast - actual_max
            peak_hour = safe_float(rec.get("forecast_peak_hour_local"))
            rows.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "snapshot": path.name,
                    "snapshot_ts_utc": snapshot_ts,
                    "snapshot_local_hour": round(local_hour, 4) if local_hour is not None else None,
                    "decision_bucket": decision_bucket(local_hour),
                    "unit": actual_row["unit"],
                    "forecast_source": forecast_source,
                    "model_version": model_version,
                    "forecast_max_native": forecast,
                    "forecast_peak_hour_local": peak_hour if math.isfinite(peak_hour) else None,
                    "actual_station_max_native": actual_max,
                    "actual_station_max_c": actual_row["actual_station_max_c"],
                    "error_native": error,
                    "abs_error_native": abs(error),
                    "under_actual_by_1": error <= -1.0,
                    "under_actual_by_2": error <= -2.0,
                    "over_actual_by_1": error >= 1.0,
                    "station": actual_row["station"],
                    "actual_source": actual_row["actual_source"],
                    "actual_max_report_ts_utc": actual_row["actual_max_report_ts_utc"],
                    "settlement_source_class": actual_row["settlement_source_class"],
                    "mapping_rule": actual_row["mapping_rule"],
                    "city_geo_context": (profile.get("source_profile_note") or ""),
                }
            )
    return rows


def decision_bucket(local_hour: float | None) -> str:
    if local_hour is None:
        return "unknown"
    if local_hour < 8:
        return "pre_8"
    if local_hour < 10:
        return "h08_10"
    if local_hour < 12:
        return "h10_12"
    if local_hour < 14:
        return "h12_14"
    if local_hour < 16:
        return "h14_16"
    return "h16_plus"


def markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 20) -> str:
    rows = rows[:limit]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, float):
                val = round(val, 4)
            vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(rows: list[dict[str, Any]], by_city: list[dict[str, Any]], by_source: list[dict[str, Any]], by_bucket: list[dict[str, Any]], out_dir: Path) -> str:
    errors = [float(row["error_native"]) for row in rows]
    worst_under = sorted(rows, key=lambda row: float(row["error_native"]))[:20]
    chengdu_tokyo = [
        row
        for row in sorted(rows, key=lambda r: (r["city"], r["target_date"], r["snapshot_ts_utc"]))
        if row["city"] in {"Chengdu", "Tokyo"} and row["target_date"] == "2026-06-30"
    ]
    latest_cases = {}
    for row in chengdu_tokyo:
        latest_cases[(row["city"], row["target_date"])] = row
    text = f"""# Forecast Station Error Distribution v1

Status: snapshot
Generated: {datetime.now(timezone.utc).isoformat(timespec="seconds")}

## Data Snapshot

- Actual max source: data-feed `source_events/sources.jsonl`, station-grain observed max.
- Forecast source: data-feed `paper_snapshots/snapshot_*.json`, deduped to `city + target_date + snapshot_ts + forecast_source`.
- Coverage rows: `{len(rows)}` forecast snapshots.
- City-days with actual station max: `{len({(r['city'], r['target_date']) for r in rows})}`.
- Date range in joined rows: `{min(r['target_date'] for r in rows) if rows else ''}`..`{max(r['target_date'] for r in rows) if rows else ''}`.

`error_native = forecast_max_native - actual_station_max_native`.

- Negative error: forecast under actual station max. Dangerous for higher/tail NO.
- Positive error: forecast over actual station max. Dangerous for current-bracket NO pass-through.

## Overall

- mean error: `{statistics.fmean(errors):.3f}` native degrees.
- median error: `{percentile(errors, 0.5):.3f}`.
- p10/p90: `{percentile(errors, 0.1):.3f}` / `{percentile(errors, 0.9):.3f}`.
- under actual by >=1 degree: `{sum(e <= -1 for e in errors) / len(errors):.1%}`.
- under actual by >=2 degrees: `{sum(e <= -2 for e in errors) / len(errors):.1%}`.
- over actual by >=1 degree: `{sum(e >= 1 for e in errors) / len(errors):.1%}`.

## By City

{markdown_table(by_city, ['city', 'rows', 'city_dates', 'mean_error', 'median_error', 'under_by_1_rate', 'under_by_2_rate', 'over_by_1_rate', 'min_error', 'max_error'], 40)}

## By Forecast Source

{markdown_table(by_source, ['forecast_source', 'rows', 'city_dates', 'mean_error', 'median_error', 'under_by_1_rate', 'under_by_2_rate', 'over_by_1_rate', 'min_error', 'max_error'], 20)}

## By Local Decision Bucket

{markdown_table(by_bucket, ['decision_bucket', 'rows', 'city_dates', 'mean_error', 'median_error', 'under_by_1_rate', 'under_by_2_rate', 'over_by_1_rate', 'min_error', 'max_error'], 20)}

## Worst Under-Forecast Cases

{markdown_table(worst_under, ['city', 'target_date', 'snapshot_ts_utc', 'decision_bucket', 'forecast_source', 'forecast_max_native', 'actual_station_max_native', 'error_native', 'station', 'actual_max_report_ts_utc'], 20)}

## Chengdu / Tokyo 2026-06-30

{markdown_table(list(latest_cases.values()), ['city', 'target_date', 'snapshot_ts_utc', 'decision_bucket', 'forecast_source', 'forecast_max_native', 'actual_station_max_native', 'error_native', 'station', 'actual_max_report_ts_utc'], 10)}

## Interpretation

This report is not a trading rule and does not approve live changes.  It shows
that forecast ceiling reliability is a separate mechanism layer from regime
routing.  Tail/higher NO needs a calibrated station-error buffer; current NO
needs a different buffer for over-forecast pass-through risk.

Generated files:

- `{out_dir / 'forecast_station_error_rows.csv'}`
- `{out_dir / 'summary_by_city.csv'}`
- `{out_dir / 'summary_by_forecast_source.csv'}`
- `{out_dir / 'summary_by_decision_bucket.csv'}`
"""
    return text


def main() -> int:
    args = parse_args()
    profiles = load_profiles(SOURCE_PROFILES)
    source_events = Path(args.source_events).expanduser()
    snapshot_dir = Path(args.snapshot_dir).expanduser()
    out_dir = Path(args.out_dir)
    report_path = Path(args.report)
    actual = load_actual_station_max(source_events, profiles, args.start_date, args.end_date)
    rows = snapshot_rows(snapshot_dir, profiles, actual, args.start_date, args.end_date)
    by_city = summary(rows, ["city"])
    by_source = summary(rows, ["forecast_source"])
    by_bucket = summary(rows, ["decision_bucket"])
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "forecast_station_error_rows.csv", rows)
    write_csv(out_dir / "summary_by_city.csv", by_city)
    write_csv(out_dir / "summary_by_forecast_source.csv", by_source)
    write_csv(out_dir / "summary_by_decision_bucket.csv", by_bucket)
    report = build_report(rows, by_city, by_source, by_bucket, out_dir)
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"rows": len(rows), "city_dates": len({(r["city"], r["target_date"]) for r in rows}), "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
