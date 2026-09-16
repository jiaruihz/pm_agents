#!/usr/bin/env python3
"""Build observation-clock history for the three-city pre-cross path model.

This is research history, not first-seen evidence.  JMA and FMI retain native
10-minute paths; KNMI's bulk climate endpoint supplies hourly history while
the separately collected 10-minute files remain the higher-cadence layer.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated"
    / "three_city_pre_cross_path_pretrain_v2"
    / "official_path_history.csv"
)
JMA_URL = "https://www.data.jma.go.jp/stats/etrn/view/10min_a1.php"
FMI_URL = "https://opendata.fmi.fi/wfs"
KNMI_URL = "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens"
UTC = timezone.utc
JMA_DIRECTION_DEG = {
    "北": 0.0,
    "北北東": 22.5,
    "北東": 45.0,
    "東北東": 67.5,
    "東": 90.0,
    "東南東": 112.5,
    "南東": 135.0,
    "南南東": 157.5,
    "南": 180.0,
    "南南西": 202.5,
    "南西": 225.0,
    "西南西": 247.5,
    "西": 270.0,
    "西北西": 292.5,
    "北西": 315.0,
    "北北西": 337.5,
}


def days(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def finite(value: str) -> float | None:
    cleaned = value.strip().replace("−", "-")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "city",
        "source",
        "station",
        "observation_time_utc",
        "temp_c",
        "wind_speed_kt",
        "wind_dir_deg",
        "wind_gust_kt",
        "wind_gust_dir_deg",
        "pressure_hpa",
        "relative_humidity_pct",
        "precipitation_10m_mm",
        "sunshine_duration_min",
        "cadence_minutes",
        "archive_source",
        "collection_mode",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fetch_jma(start: date, end: date, client: httpx.Client) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    local_zone = ZoneInfo("Asia/Tokyo")
    for day in days(start, end):
        response = client.get(
            JMA_URL,
            params={
                "prec_no": "44",
                "block_no": "0371",
                "year": day.year,
                "month": day.month,
                "day": day.day,
                "view": "",
            },
        )
        response.raise_for_status()
        table = re.search(
            r'<table id="tablefix1".*?</table>', response.text, flags=re.DOTALL
        )
        if not table:
            raise RuntimeError(f"JMA history table missing for {day}")
        for row_html in re.findall(
            r'<tr class="mtx".*?</tr>', table.group(), flags=re.DOTALL
        ):
            cells = [
                unescape(re.sub(r"<.*?>", "", cell)).strip()
                for cell in re.findall(r"<td.*?>(.*?)</td>", row_html, flags=re.DOTALL)
            ]
            if len(cells) < 5 or not re.fullmatch(r"\d{2}:\d{2}", cells[0]):
                continue
            temp_c = finite(cells[2])
            if temp_c is None:
                continue
            hour, minute = (int(part) for part in cells[0].split(":"))
            obs_day = day + timedelta(days=1) if hour == 24 else day
            hour = 0 if hour == 24 else hour
            obs_ts = datetime(
                obs_day.year, obs_day.month, obs_day.day, hour, minute, tzinfo=local_zone
            ).astimezone(UTC)
            wind_ms = finite(cells[4])
            gust_ms = finite(cells[6])
            humidity = finite(cells[3])
            sunshine = finite(cells[8])
            output.append(
                {
                    "city": "Tokyo",
                    "source": "jma_amedas",
                    "station": "44166",
                    "observation_time_utc": obs_ts.isoformat(),
                    "temp_c": temp_c,
                    "wind_speed_kt": (
                        round(wind_ms * 1.94384, 3) if wind_ms is not None else ""
                    ),
                    "wind_dir_deg": JMA_DIRECTION_DEG.get(cells[5], ""),
                    "wind_gust_kt": (
                        round(gust_ms * 1.94384, 3) if gust_ms is not None else ""
                    ),
                    "wind_gust_dir_deg": JMA_DIRECTION_DEG.get(cells[7], ""),
                    "pressure_hpa": "",
                    "relative_humidity_pct": (
                        humidity if humidity is not None else ""
                    ),
                    "precipitation_10m_mm": finite(cells[1]),
                    "sunshine_duration_min": (
                        sunshine if sunshine is not None else ""
                    ),
                    "cadence_minutes": 10,
                    "archive_source": "jma_etrn_10min_block_0371",
                    "collection_mode": "historical_observation_clock_not_first_seen",
                }
            )
    return output


def _fmi_parameter_pairs(xml: str) -> dict[str, dict[datetime, float]]:
    output: dict[str, dict[datetime, float]] = {}
    for block in re.split(r"<om:observedProperty\s", xml):
        parameter = re.search(r"param=(\w+)", block)
        if not parameter:
            continue
        values = output.setdefault(parameter.group(1), {})
        for time_text, value_text in re.findall(
            r"<wml2:MeasurementTVP>.*?<wml2:time>(.*?)</wml2:time>\s*"
            r"<wml2:value>(.*?)</wml2:value>",
            block,
            flags=re.DOTALL,
        ):
            value = finite(value_text)
            if value is None:
                continue
            values[datetime.fromisoformat(time_text.replace("Z", "+00:00"))] = value
    return output


def fetch_fmi(start: date, end: date, client: httpx.Client) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cursor = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    stop = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    while cursor < stop:
        chunk_end = min(cursor + timedelta(days=6, hours=23, minutes=59), stop)
        response = client.get(
            FMI_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "getFeature",
                "storedquery_id": "fmi::observations::weather::timevaluepair",
                "place": "helsinki-vantaa_airport",
                "parameters": "t2m,ws_10min,p_sea",
                "starttime": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endtime": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        response.raise_for_status()
        values = _fmi_parameter_pairs(response.text)
        for obs_ts, temp_c in values.get("t2m", {}).items():
            wind_ms = values.get("ws_10min", {}).get(obs_ts)
            output.append(
                {
                    "city": "Helsinki",
                    "source": "fmi",
                    "station": "Helsinki-Vantaa",
                    "observation_time_utc": obs_ts.astimezone(UTC).isoformat(),
                    "temp_c": temp_c,
                    "wind_speed_kt": (
                        round(wind_ms * 1.94384, 3) if wind_ms is not None else ""
                    ),
                    "wind_dir_deg": "",
                    "wind_gust_kt": "",
                    "wind_gust_dir_deg": "",
                    "pressure_hpa": values.get("p_sea", {}).get(obs_ts, ""),
                    "relative_humidity_pct": "",
                    "precipitation_10m_mm": "",
                    "sunshine_duration_min": "",
                    "cadence_minutes": 10,
                    "archive_source": "fmi_wfs_timevaluepair",
                    "collection_mode": "historical_observation_clock_not_first_seen",
                }
            )
        cursor = chunk_end + timedelta(seconds=1)
    return output


def fetch_knmi(start: date, end: date, client: httpx.Client) -> list[dict[str, Any]]:
    response = client.post(
        KNMI_URL,
        data={
            "stns": "240",
            "vars": "ALL",
            "start": start.strftime("%Y%m%d") + "01",
            "end": end.strftime("%Y%m%d") + "24",
        },
    )
    response.raise_for_status()
    output: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) < 16 or cells[0] != "240":
            continue
        day = date.fromisoformat(
            f"{cells[1][0:4]}-{cells[1][4:6]}-{cells[1][6:8]}"
        )
        hour = int(cells[2])
        obs_ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
            hours=hour
        )
        temp_tenths = finite(cells[7])
        if temp_tenths is None:
            continue
        wind_tenths_ms = finite(cells[4])
        pressure_tenths = finite(cells[14])
        output.append(
            {
                "city": "Amsterdam",
                "source": "knmi",
                "station": "240",
                "observation_time_utc": obs_ts.isoformat(),
                "temp_c": temp_tenths / 10.0,
                "wind_speed_kt": (
                    round(wind_tenths_ms / 10.0 * 1.94384, 3)
                    if wind_tenths_ms is not None
                    else ""
                ),
                "wind_dir_deg": "",
                "wind_gust_kt": "",
                "wind_gust_dir_deg": "",
                "pressure_hpa": (
                    pressure_tenths / 10.0 if pressure_tenths is not None else ""
                ),
                "relative_humidity_pct": "",
                "precipitation_10m_mm": "",
                "sunshine_duration_min": "",
                "cadence_minutes": 60,
                "archive_source": "knmi_hourly_climate_station_240",
                "collection_mode": "historical_observation_clock_not_first_seen",
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-05-19")
    parser.add_argument("--helsinki-end", default="2026-07-20")
    parser.add_argument("--tokyo-end", default="2026-07-20")
    parser.add_argument("--amsterdam-end", default="2026-07-26")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    start = date.fromisoformat(args.start_date)
    with httpx.Client(
        timeout=60, follow_redirects=True, headers={"User-Agent": "pm-agents-research/1"}
    ) as client:
        rows = [
            *fetch_fmi(start, date.fromisoformat(args.helsinki_end), client),
            *fetch_jma(start, date.fromisoformat(args.tokyo_end), client),
            *fetch_knmi(start, date.fromisoformat(args.amsterdam_end), client),
        ]
    rows.sort(key=lambda row: (row["city"], row["observation_time_utc"]))
    write_csv(Path(args.out), rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["city"]] = counts.get(row["city"], 0) + 1
    print(f"wrote={Path(args.out)} rows={len(rows)} by_city={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
