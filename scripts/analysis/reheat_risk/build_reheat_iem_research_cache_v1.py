#!/usr/bin/env python3
"""Build repeatable IEM research caches for reheat/current-YES studies.

Outputs two legacy-compatible cache layouts:
- WU-like `wu_obs_ICAO.csv` for observed running-max builders.
- `iem_ext_ICAO_START_END.csv` for reheat feature factories.

The fetch window is computed from each city's local target_date range so
Americas/Asia dates are not truncated by UTC calendar boundaries.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OBSERVED_MAX_DIR = ROOT / "scripts/analysis/observed_max"
sys.path.insert(0, str(OBSERVED_MAX_DIR))

from research_m3_observed_max_residual import CITY_TIMEZONE  # noqa: E402


IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DEFAULT_STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
DEFAULT_OUT_ROOT = ROOT / "docs/analysis/2026-06/generated"
IEM_COLS = ("tmpc", "tmpf", "dwpc", "dwpf", "relh", "drct", "sknt", "skyc1")


@dataclass(frozen=True)
class Station:
    city: str
    icao: str
    unit: str
    timezone_name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--station-summary", default=str(DEFAULT_STATION_SUMMARY))
    parser.add_argument("--start-date", default="2026-05-19")
    parser.add_argument("--end-date", default="2026-06-17")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--wu-cache-name", default="theta_no_wu_obs_patch_v2_20260617")
    parser.add_argument("--iem-ext-name", default="theta_no_iem_ext_patch_v7_20260617")
    parser.add_argument("--sleep-sec", type=float, default=1.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def load_stations(path: Path) -> list[Station]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Station] = []
    for item in data.get("stations", []):
        city = str(item["city"])
        tz = CITY_TIMEZONE.get(city)
        if not tz:
            continue
        out.append(
            Station(
                city=city,
                icao=str(item["icao"]).upper(),
                unit=str(item.get("unit") or "").upper(),
                timezone_name=tz,
            )
        )
    return out


def local_window_utc(start: str, end: str, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(date.fromisoformat(start), datetime.min.time(), tzinfo=tz)
    end_local = datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def build_params(station: Station, start: str, end: str) -> list[tuple[str, str]]:
    start_utc, end_utc = local_window_utc(start, end, station.timezone_name)
    # IEM ASOS date parameters are day-granular; overfetch by one day and filter
    # back to local target dates after parsing.
    fetch_start = start_utc.date()
    fetch_end = (end_utc + timedelta(days=1)).date()
    params: list[tuple[str, str]] = [("station", station.icao)]
    for col in IEM_COLS:
        params.append(("data", col))
    params.extend(
        [
            ("year1", str(fetch_start.year)),
            ("month1", str(fetch_start.month)),
            ("day1", str(fetch_start.day)),
            ("year2", str(fetch_end.year)),
            ("month2", str(fetch_end.month)),
            ("day2", str(fetch_end.day)),
            ("tz", "UTC"),
            ("format", "comma"),
            ("latlon", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
            ("report_type", "1"),
            ("report_type", "2"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
    )
    return params


def fetch_text(params: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    url = IEM_URL + "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(1, 6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pm-agent-reheat-research-cache/1.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read().decode("utf-8"), None
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code not in {429, 500, 502, 503, 504}:
                break
        except (TimeoutError, URLError) as exc:
            last_error = str(exc)
        time.sleep(min(45.0, 3.0 * attempt * attempt))
    return None, last_error


def csv_rows(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def f_to_c(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def num(value: Any) -> float | None:
    if value in (None, "", "M"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def iso_minute(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def normalized_rows(raw_rows: list[dict[str, str]], station: Station, start: str, end: str) -> list[dict[str, Any]]:
    tz = ZoneInfo(station.timezone_name)
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        valid = row.get("valid")
        if not valid:
            continue
        try:
            ts = datetime.fromisoformat(valid.replace(" ", "T")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        local_date = ts.astimezone(tz).date().isoformat()
        if local_date < start or local_date > end:
            continue
        tmpf = num(row.get("tmpf"))
        tmpc = num(row.get("tmpc"))
        if tmpf is None and tmpc is not None:
            tmpf = c_to_f(tmpc)
        if tmpc is None and tmpf is not None:
            tmpc = f_to_c(tmpf)
        if tmpf is None or tmpc is None:
            continue
        dwpf = num(row.get("dwpf"))
        dwpc = num(row.get("dwpc"))
        if dwpf is None and dwpc is not None:
            dwpf = c_to_f(dwpc)
        out.append(
            {
                "station": station.icao,
                "valid": iso_minute(ts),
                "tmpf": round(tmpf, 3),
                "tmpc": round(tmpc, 3),
                "dwpf": round(dwpf, 3) if dwpf is not None else "",
                "relh": row.get("relh") if row.get("relh") not in (None, "M") else "",
                "drct": row.get("drct") if row.get("drct") not in (None, "M") else "",
                "sknt": row.get("sknt") if row.get("sknt") not in (None, "M") else "",
                "skyc1": row.get("skyc1") if row.get("skyc1") not in (None, "M") else "",
                "date_local": local_date,
            }
        )
    return sorted(out, key=lambda item: item["valid"])


def write_iem_ext(path: Path, rows: list[dict[str, Any]]) -> None:
    cols = ["station", "valid", "tmpf", "dwpf", "relh", "drct", "sknt", "skyc1"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in cols})


def write_wu_like(path: Path, rows: list[dict[str, Any]]) -> None:
    cols = ["date_local", "valid_utc", "temp", "dewpt", "wspd", "wdir", "wx_phrase"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "date_local": row["date_local"],
                    "valid_utc": row["valid"],
                    "temp": round(float(row["tmpf"])),
                    "dewpt": round(float(row["dwpf"])) if row.get("dwpf") not in ("", None) else "",
                    "wspd": row.get("sknt", ""),
                    "wdir": row.get("drct", ""),
                    "wx_phrase": "IEM_ASOS_METAR_FALLBACK",
                }
            )


def main() -> int:
    args = parse_args()
    start = str(args.start_date)
    end = str(args.end_date)
    out_root = Path(args.out_root)
    wu_dir = out_root / str(args.wu_cache_name) / "wu_obs"
    iem_raw_dir = out_root / str(args.wu_cache_name) / "iem"
    ext_dir = out_root / str(args.iem_ext_name)
    for path in (wu_dir, iem_raw_dir, ext_dir):
        path.mkdir(parents=True, exist_ok=True)

    station_summary = Path(args.station_summary)
    stations = load_stations(station_summary)
    summary_rows = []
    for station in stations:
        iem_path = iem_raw_dir / f"iem_v2_{station.icao}_{start}_{end}.csv"
        wu_path = wu_dir / f"wu_obs_{station.icao}.csv"
        ext_path = ext_dir / f"iem_ext_{station.icao}_{start}_{end}.csv"
        raw_text = None
        status = "cached"
        error = ""
        if iem_path.exists() and iem_path.stat().st_size > 1000 and not args.force:
            raw_text = iem_path.read_text(encoding="utf-8")
        else:
            raw_text, error = fetch_text(build_params(station, start, end))
            if raw_text is None:
                status = "fetch_error"
            else:
                lines = [line for line in raw_text.splitlines() if line.strip() and not line.startswith("#")]
                iem_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                status = "iem_fetched"
        rows: list[dict[str, Any]] = []
        if raw_text is not None:
            rows = normalized_rows(csv_rows(raw_text), station, start, end)
            write_wu_like(wu_path, rows)
            write_iem_ext(ext_path, rows)
        day_counts: dict[str, int] = {}
        for row in rows:
            day_counts[row["date_local"]] = day_counts.get(row["date_local"], 0) + 1
        summary_rows.append(
            {
                "city": station.city,
                "icao": station.icao,
                "unit": station.unit,
                "timezone": station.timezone_name,
                "status": status,
                "error": error,
                "iem_file": str(iem_path),
                "wu_file": str(wu_path),
                "ext_file": str(ext_path),
                "rows": len(rows),
                "local_days": len(day_counts),
                "first_date": min(day_counts) if day_counts else None,
                "last_date": max(day_counts) if day_counts else None,
                "min_obs_per_day": min(day_counts.values()) if day_counts else 0,
                "max_obs_per_day": max(day_counts.values()) if day_counts else 0,
            }
        )
        time.sleep(max(0.0, float(args.sleep_sec)))

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "IEM ASOS/METAR report_type=1+2+3+4",
        "range": {"start": start, "end": end},
        "station_summary": str(station_summary),
        "wu_dir": str(wu_dir),
        "iem_ext_dir": str(ext_dir),
        "stations_requested": len(stations),
        "stations_ok": sum(1 for row in summary_rows if row["rows"] > 0),
        "stations": summary_rows,
    }
    summary_path = out_root / str(args.wu_cache_name) / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["stations_ok"] == len(stations) else 2


if __name__ == "__main__":
    raise SystemExit(main())
