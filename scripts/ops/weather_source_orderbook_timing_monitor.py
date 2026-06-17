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
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
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
from weather_metar_cross_prev_no_shadow import (  # noqa: E402
    OUT_DIR as CROSS_OUT_DIR,
    CityConfig,
    load_city_configs,
    market_value,
    parse_metar_records,
)


DATA_ROOT = Path(os.environ.get("TIMING_MONITOR_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/source_orderbook_timing"
CHECKWX_URL = "https://www.checkwx.com/weather/{icao}/metar"
METAR_TEMP_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)")
CHECKWX_OBS_RE = re.compile(r"Observed.*?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", re.IGNORECASE | re.DOTALL)


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


def fetch_aviationweather_latest(cfg: CityConfig, tz: ZoneInfo, local_date: Any) -> dict[str, Any]:
    data = source.fetch_json(source.METAR_API, {"ids": cfg.official_icao, "format": "json", "hours": "2"})
    obs = parse_metar_records(data, tz, local_date)
    if not obs:
        return {"status": "empty", "source": "aviationweather", "station": cfg.official_icao}
    latest_dt, latest_temp = obs[-1]
    raw = data[-1] if isinstance(data, list) and data else {}
    return {
        "status": "ok",
        "source": "aviationweather",
        "station": cfg.official_icao,
        "source_report_ts_utc": latest_dt.isoformat(),
        "temp_c": latest_temp,
        "raw_metar": raw.get("rawOb"),
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
    }


def source_snapshot(cfg: CityConfig, source_name: str, now_utc: datetime) -> dict[str, Any]:
    tz = ZoneInfo(cfg.timezone_name)
    local_date = now_utc.astimezone(tz).date()
    if source_name == "aviationweather":
        row = fetch_aviationweather_latest(cfg, tz, local_date)
    elif source_name == "checkwx_html":
        row = fetch_checkwx_latest(cfg)
    else:
        raise ValueError(f"unknown source {source_name}")
    row.update(
        {
            "ts_utc": now_utc.isoformat(),
            "city": cfg.city,
            "target_date": local_date.isoformat(),
            "unit": cfg.unit,
            "registry_class": cfg.registry_class,
            "source_age_sec": source_age_sec(row.get("source_report_ts_utc"), now_utc),
        }
    )
    row["payload_hash"] = stable_hash(
        {
            "source_report_ts_utc": row.get("source_report_ts_utc"),
            "temp_c": row.get("temp_c"),
            "raw_metar": row.get("raw_metar"),
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
        for source_name in sources:
            try:
                row = source_snapshot(cfg, source_name, now_utc)
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                row = {"ts_utc": now_utc.isoformat(), "city": cfg.city, "source": source_name, "status": "fetch_failed", "error": f"{type(exc).__name__}: {exc}"}
            key = f"source|{cfg.city}|{source_name}"
            row["changed_since_last"] = mark_changed(state, key, row.get("payload_hash", ""))
            counts["source_rows"] += 1
            counts["source_updates"] += int(bool(row["changed_since_last"]))
            append_jsonl(OUT_DIR / "sources.jsonl", row)
            if row.get("source") == "aviationweather" and row.get("temp_c") is not None:
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
    print(
        json.dumps(
            {
                "source_rows": len(source_rows),
                "book_rows": len(book_rows),
                "latest_sources": list(latest_sources.values()),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure source update timing vs weather market orderbook changes.")
    parser.add_argument("command", choices=["cycle", "loop", "report"], nargs="?", default="cycle")
    parser.add_argument("--cities", nargs="*", default=["Shanghai", "Tokyo"])
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--sources", nargs="*", default=["aviationweather", "checkwx_html"])
    parser.add_argument("--bracket-radius", type=int, default=2)
    parser.add_argument("--base-interval-sec", type=float, default=60.0)
    parser.add_argument("--burst-interval-sec", type=float, default=3.0)
    parser.add_argument("--burst-window-min", type=float, default=8.0)
    args = parser.parse_args()

    if args.command == "report":
        return report()
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
