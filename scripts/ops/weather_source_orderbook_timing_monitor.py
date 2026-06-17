#!/usr/bin/env python3
"""Monitor weather-source update timing against Polymarket orderbook changes.

This is a measurement process, not a trading script. It records:
- latest observation timestamp and temperature from each source
- whether the source payload changed since the prior poll
- nearby bracket YES/NO top-of-book snapshots

Use it to identify whether the bottleneck is our polling cadence, the public
weather source, or other traders receiving faster data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import weather_station_basis_shadow as source  # noqa: E402
from src.strategies.weather_edge_v1.official_observation_feed.source_policy import (  # noqa: E402
    CityConfig,
    build_city_policy,
    load_city_configs,
)
from weather_metar_cross_prev_no_shadow import (  # noqa: E402
    market_value,
)


DATA_ROOT = Path(os.environ.get("TIMING_MONITOR_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing"
CHECKWX_URL = "https://www.checkwx.com/weather/{icao}/metar"
METAR_TEMP_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)")
CHECKWX_OBS_RE = re.compile(r"Observed.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.IGNORECASE | re.DOTALL)
SOURCE_ALIASES = {
    "aviationweather": "aviationweather_metar",
    "aviationweather_metar": "aviationweather_metar",
    "checkwx": "checkwx_html",
    "checkwx_html": "checkwx_html",
    "iem": "iem_asos",
    "iem_asos": "iem_asos",
}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_metar_temp_c(raw: str) -> float | None:
    match = METAR_TEMP_RE.search(f" {raw}")
    if not match:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def source_age_sec(report_ts_utc: str | None, now_utc: datetime) -> float | None:
    dt = parse_dt(report_ts_utc)
    if not dt:
        return None
    return round((now_utc - dt).total_seconds(), 3)


def canonical_source_name(source_name: str) -> str:
    return SOURCE_ALIASES.get(source_name, source_name)


def expand_source_names(cfg: CityConfig, requested_sources: list[str]) -> list[str]:
    expanded: list[str] = []
    for raw_name in requested_sources:
        if raw_name == "source_profiles":
            expanded.extend([cfg.live_observation_source, *cfg.fallback_sources])
        elif raw_name == "profile_primary":
            expanded.append(cfg.live_observation_source)
        elif raw_name == "profile_fallbacks":
            expanded.extend(cfg.fallback_sources)
        else:
            expanded.append(raw_name)
    deduped: list[str] = []
    for raw_name in expanded:
        source_name = canonical_source_name(str(raw_name))
        if source_name and source_name not in deduped:
            deduped.append(source_name)
    return deduped


def parse_aviationweather_records(data: list[dict[str, Any]], tz: ZoneInfo, local_date: Any) -> list[tuple[datetime, float, dict[str, Any]]]:
    records = []
    for rec in data:
        temp = rec.get("temp")
        ts = rec.get("reportTime")
        if temp is None or not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            temp_c = float(temp)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp_c, rec))
    return sorted(records, key=lambda item: item[0])


def fetch_aviationweather_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    data = source.fetch_json(source.METAR_API, {"ids": cfg.official_icao, "format": "json", "hours": "2"})
    records = parse_aviationweather_records(data, tz, local_date) if isinstance(data, list) else []
    if not records:
        return {"status": "empty", "source": "aviationweather_metar", "station": cfg.official_icao}
    latest_dt, latest_temp, raw = records[-1]
    return {
        "status": "ok",
        "source": "aviationweather_metar",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": raw.get("rawOb"),
        "raw_payload_hash": stable_hash(raw),
    }


def fetch_checkwx_latest(cfg: CityConfig) -> dict[str, Any]:
    text = source.fetch_text(CHECKWX_URL.format(icao=cfg.official_icao))
    raw_match = re.search(rf"{cfg.official_icao}\s+\d{{6}}Z[^<]+", text)
    raw_metar = raw_match.group(0).strip() if raw_match else ""
    observed_match = CHECKWX_OBS_RE.search(text)
    return {
        "status": "ok" if raw_metar else "missing_metar",
        "source": "checkwx_html",
        "station": cfg.official_icao,
        "source_report_ts_utc": observed_match.group(1).replace("Z", "+00:00") if observed_match else "",
        "temp_c": parse_metar_temp_c(raw_metar) if raw_metar else None,
        "raw_metar": raw_metar,
        "raw_payload_hash": stable_hash(text),
    }


def fetch_iem_asos_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    start_utc = local_start.astimezone(timezone.utc) - timedelta(hours=2)
    end_utc = datetime.now(timezone.utc) + timedelta(hours=1)
    params = [
        ("station", cfg.official_icao),
        ("data", "tmpc"),
        ("year1", str(start_utc.year)),
        ("month1", str(start_utc.month)),
        ("day1", str(start_utc.day)),
        ("year2", str(end_utc.year)),
        ("month2", str(end_utc.month)),
        ("day2", str(end_utc.day)),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("elev", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", "1"),
        ("report_type", "2"),
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    text = source.fetch_text(source.IEM_ASOS_API, params)
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    records: list[tuple[datetime, float, dict[str, str]]] = []
    for row in csv.DictReader(io.StringIO("\n".join(rows))):
        raw_temp = row.get("tmpc")
        raw_ts = row.get("valid")
        if not raw_temp or raw_temp == "M" or not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(raw_ts.replace(" ", "T")).replace(tzinfo=timezone.utc)
            temp = float(raw_temp)
        except ValueError:
            continue
        if dt.astimezone(tz).date() == local_date:
            records.append((dt, temp, row))
    if not records:
        return {
            "status": "empty",
            "source": "iem_asos",
            "station": cfg.official_icao,
            "raw_payload_hash": stable_hash(text),
        }
    latest_dt, latest_temp, latest_row = sorted(records)[-1]
    return {
        "status": "ok",
        "source": "iem_asos",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": "",
        "raw_payload_hash": stable_hash({"latest_row": latest_row, "payload": text}),
    }


def source_snapshot(cfg: CityConfig, source_name: str, now_utc: datetime) -> dict[str, Any]:
    source_name = canonical_source_name(source_name)
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    if source_name == "aviationweather_metar":
        row = fetch_aviationweather_latest(cfg, tz, local_date)
    elif source_name == "checkwx_html":
        row = fetch_checkwx_latest(cfg)
    elif source_name == "iem_asos":
        row = fetch_iem_asos_latest(cfg, tz, local_date)
    else:
        raise ValueError(f"unknown source {source_name}")
    detected_after_report_sec = source_age_sec(row.get("source_report_ts_utc"), now_utc)
    row.update(
        {
            "ts_utc": now_utc.isoformat(),
            "local_detect_ts_utc": now_utc.isoformat(),
            "city": cfg.city,
            "target_date": local_date.isoformat(),
            "unit": cfg.unit,
            "settlement_source_class": cfg.settlement_source_class,
            "settlement_source": cfg.settlement_source,
            "live_observation_source": cfg.live_observation_source,
            "mapping_rule": cfg.mapping_rule,
            "registry_class": cfg.registry_class,
            "source_age_sec": detected_after_report_sec,
            "detected_after_report_sec": detected_after_report_sec,
        }
    )
    row["payload_hash"] = stable_hash(
        {
            "source_report_ts_utc": row.get("source_report_ts_utc"),
            "temp_c": row.get("temp_c"),
            "raw_metar": row.get("raw_metar"),
            "raw_payload_hash": row.get("raw_payload_hash"),
        }
    )
    return row


def in_update_window(now_utc: datetime, *, window_min: float) -> bool:
    minute = now_utc.minute + now_utc.second / 60.0
    distance_to_half_hour = min(abs(minute - 0.0), abs(minute - 30.0), abs(minute - 60.0))
    return distance_to_half_hour <= window_min


def target_brackets_from_temp(temp_c: float | None, unit: str, radius: int) -> list[int]:
    if temp_c is None:
        return []
    value = market_value(float(temp_c), unit)
    return list(range(value - radius, value + radius + 1))


def find_markets_for_brackets(markets: list[dict[str, Any]], brackets: list[int]) -> list[tuple[int, dict[str, Any]]]:
    out = []
    for market in markets:
        parsed = source.parse_label(str(market.get("groupItemTitle") or ""), str(market.get("question") or ""))
        if parsed is None or parsed.get("top"):
            continue
        low = parsed.get("low")
        high = parsed.get("high")
        for bracket in brackets:
            if low is None and high is not None and bracket <= int(float(high)):
                out.append((bracket, market))
                break
            if low is not None and high is not None and float(low) <= bracket <= float(high):
                out.append((bracket, market))
                break
    return out


def fetch_orderbook_rows(cfg: CityConfig, now_utc: datetime, temp_c: float | None, *, radius: int) -> list[dict[str, Any]]:
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    event_slug = source.event_slug(cfg.slug, local_date)
    events = source.fetch_json(f"{source.GAMMA}/events", {"slug": event_slug})
    markets = events[0].get("markets") if events else []
    if not markets:
        return [{"ts_utc": now_utc.isoformat(), "city": cfg.city, "event_slug": event_slug, "status": "no_event"}]
    rows = []
    for bracket, market in find_markets_for_brackets(markets, target_brackets_from_temp(temp_c, cfg.unit, radius)):
        token_ids = json.loads(market["clobTokenIds"])
        for side, token_id in (("YES", token_ids[0]), ("NO", token_ids[1])):
            try:
                book = source.book_summary(source.fetch_json(f"{source.CLOB}/book", {"token_id": token_id}))
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                book = {"error": f"{type(exc).__name__}: {exc}"}
                status = "book_fetch_failed"
            row = {
                "ts_utc": now_utc.isoformat(),
                "local_detect_ts_utc": now_utc.isoformat(),
                "city": cfg.city,
                "target_date": local_date.isoformat(),
                "event_slug": event_slug,
                "bracket": bracket,
                "market_label": market.get("groupItemTitle"),
                "side": side,
                "token_id": token_id,
                "status": status,
                **book,
            }
            row["payload_hash"] = stable_hash({k: row.get(k) for k in ("best_bid", "best_bid_size", "best_ask", "best_ask_size", "bid_levels", "ask_levels")})
            rows.append(row)
    return rows


def mark_changed(state: dict[str, Any], key: str, payload_hash: str) -> bool:
    old = state.get(key)
    state[key] = payload_hash
    return old != payload_hash


def cycle_once(configs: list[CityConfig], *, sources: list[str], bracket_radius: int) -> dict[str, int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = read_json(OUT_DIR / "state.json", {})
    now_utc = datetime.now(timezone.utc)
    counts = {"cities": 0, "source_rows": 0, "source_updates": 0, "book_rows": 0, "book_updates": 0, "errors": 0}
    for cfg in configs:
        counts["cities"] += 1
        temp_for_books = None
        source_names = expand_source_names(cfg, sources)
        for source_name in source_names:
            try:
                row = source_snapshot(cfg, source_name, now_utc)
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                row = {
                    "ts_utc": now_utc.isoformat(),
                    "local_detect_ts_utc": now_utc.isoformat(),
                    "city": cfg.city,
                    "source": canonical_source_name(source_name),
                    "station": cfg.official_icao,
                    "status": "fetch_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            key = f"source|{cfg.city}|{source_name}"
            row["changed_since_last"] = mark_changed(state, key, row.get("payload_hash", ""))
            counts["source_rows"] += 1
            counts["source_updates"] += int(bool(row["changed_since_last"]))
            append_jsonl(OUT_DIR / "sources.jsonl", row)
            if row.get("source") == canonical_source_name(cfg.live_observation_source) and row.get("temp_c") is not None:
                temp_for_books = float(row["temp_c"])
        try:
            for book_row in fetch_orderbook_rows(cfg, now_utc, temp_for_books, radius=bracket_radius):
                key = f"book|{book_row.get('city')}|{book_row.get('token_id')}"
                book_row["changed_since_last"] = mark_changed(state, key, book_row.get("payload_hash", ""))
                counts["book_rows"] += 1
                counts["book_updates"] += int(bool(book_row["changed_since_last"]))
                append_jsonl(OUT_DIR / "books.jsonl", book_row)
        except Exception as exc:  # noqa: BLE001
            counts["errors"] += 1
            append_jsonl(OUT_DIR / "books.jsonl", {"ts_utc": now_utc.isoformat(), "city": cfg.city, "status": "event_or_book_fetch_failed", "error": f"{type(exc).__name__}: {exc}"})
    write_json(OUT_DIR / "state.json", state)
    return counts


def report() -> int:
    sources_path = OUT_DIR / "sources.jsonl"
    books_path = OUT_DIR / "books.jsonl"
    source_rows = [json.loads(line) for line in sources_path.read_text(encoding="utf-8").splitlines() if line.strip()] if sources_path.exists() else []
    book_rows = [json.loads(line) for line in books_path.read_text(encoding="utf-8").splitlines() if line.strip()] if books_path.exists() else []
    latest_sources = {}
    for row in source_rows:
        latest_sources[(row.get("city"), row.get("source"))] = row
    source_latencies: dict[str, dict[str, float | int | None]] = {}
    for source_name in sorted({str(row.get("source")) for row in source_rows if row.get("source")}):
        vals = [
            float(row["detected_after_report_sec"])
            for row in source_rows
            if row.get("source") == source_name and row.get("detected_after_report_sec") is not None
        ]
        source_latencies[source_name] = {
            "rows": len(vals),
            "min_sec": min(vals) if vals else None,
            "max_sec": max(vals) if vals else None,
            "avg_sec": round(sum(vals) / len(vals), 3) if vals else None,
        }
    print(
        json.dumps(
            {
                "source_rows": len(source_rows),
                "book_rows": len(book_rows),
                "latest_sources": list(latest_sources.values()),
                "source_latencies": source_latencies,
                "source_updates": sum(1 for row in source_rows if row.get("changed_since_last")),
                "book_updates": sum(1 for row in book_rows if row.get("changed_since_last")),
                "out_dir": str(OUT_DIR),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def source_policy(*, include_station_diff: bool, only_cities: set[str] | None = None) -> int:
    print(json.dumps(build_city_policy(include_station_diff=include_station_diff, only_cities=only_cities), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure source update timing vs weather market orderbook changes.")
    parser.add_argument("command", choices=["cycle", "loop", "report", "source-policy"], nargs="?", default="cycle")
    parser.add_argument("--cities", nargs="*", default=["Shanghai", "Tokyo"])
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--sources", nargs="*", default=["source_profiles", "checkwx_html"])
    parser.add_argument("--bracket-radius", type=int, default=2)
    parser.add_argument("--base-interval-sec", type=float, default=60.0)
    parser.add_argument("--burst-interval-sec", type=float, default=3.0)
    parser.add_argument("--burst-window-min", type=float, default=8.0)
    args = parser.parse_args()

    if args.command == "report":
        return report()
    if args.command == "source-policy":
        return source_policy(include_station_diff=args.include_station_diff, only_cities=set(args.cities or []) or None)
    configs = load_city_configs(include_station_diff=args.include_station_diff, only_cities=set(args.cities or []) or None)
    if not configs:
        raise SystemExit("no eligible city configs")
    print(json.dumps({"command": args.command, "cities": [cfg.city for cfg in configs], "sources": args.sources, "out_dir": str(OUT_DIR)}, ensure_ascii=False, sort_keys=True))
    while True:
        counts = cycle_once(configs, sources=args.sources, bracket_radius=args.bracket_radius)
        print(json.dumps({"ts_utc": datetime.now(timezone.utc).isoformat(), **counts}, sort_keys=True))
        if args.command == "cycle":
            return 0
        sleep_sec = args.burst_interval_sec if in_update_window(datetime.now(timezone.utc), window_min=args.burst_window_min) else args.base_interval_sec
        time.sleep(sleep_sec)


if __name__ == "__main__":
    raise SystemExit(main())
