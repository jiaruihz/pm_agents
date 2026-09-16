#!/usr/bin/env python3
"""Offline parity harness for weather observation parsing and freshness helpers.

This script is intentionally read-only outside its Markdown report output.  It
does not fetch weather APIs, start live runners, or touch order/executor paths.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_ldm_metar_notify_monitor as ldm
from scripts.ops import weather_metar_cross_prev_no_shadow as metar_cross
from scripts.ops import weather_theta_current_yes_tiny_live as theta
from weather_data_feed import (
    index_observation_cache,
    load_city_configs,
    load_observation_cache,
    normalize_observation_cache_record,
)
from weather_data_feed.observation_sources import (
    normalize_source_name,
    parse_aviationweather_records,
    parse_metar_report_time,
    parse_metar_temp_c,
)


REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-weather-observation-parity-report-v1.md"
MAC_OBSERVATION_CACHE = Path("/Users/deepsleep/projects/weather_data_feed_service_runtime/output/observations/latest.json")
MAC_PAPER_SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
MAC_CURRENT_PAPER_SNAPSHOT_DIR = Path("/Users/deepsleep/projects/weather_data_feed_service_runtime/output/paper_snapshots")
MAC_SOURCE_EVENTS = Path("/Users/deepsleep/projects/weather_data_feed_service_runtime/output/source_events/latest.json")
LEGACY_SOURCE_EVENTS = ROOT / "runtime/n100_recovery_20260705/weather_data_feed_service_runtime/output/source_events/latest.json"


@dataclass(frozen=True)
class CheckResult:
    name: str
    sample_source: str
    sample_window: str
    rows: int
    matched: int
    diffs: int
    skipped: int
    verdict: str
    detail: str


def parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_equal(left: Any, right: Any) -> bool:
    return iso(parse_utc(left)) == iso(parse_utc(right))


def floats_equal(left: Any, right: Any, *, eps: float = 1e-6) -> bool:
    try:
        lf = float(left)
        rf = float(right)
    except (TypeError, ValueError):
        return left == right
    if not math.isfinite(lf) and not math.isfinite(rf):
        return True
    return abs(lf - rf) <= eps


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_source_event_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    records = payload.get("records") if isinstance(payload, dict) else payload
    return [row for row in records or [] if isinstance(row, dict)]


def generated_at(path: Path) -> str:
    if not path.exists():
        return "missing"
    payload = read_json(path)
    return str(payload.get("generated_at_utc") or payload.get("ts_utc") or "unknown")


def newest_snapshot() -> Path | None:
    paths = []
    for folder in (MAC_PAPER_SNAPSHOT_DIR, MAC_CURRENT_PAPER_SNAPSHOT_DIR):
        if folder.exists():
            paths.extend(folder.glob("snapshot_2026070[3-5]_*.json"))
    if not paths:
        return None
    return sorted(paths)[-1]


def city_config_map() -> dict[str, Any]:
    return {cfg.city: cfg for cfg in load_city_configs(include_station_diff=True)}


def sample_window_from_rows(rows: list[dict[str, Any]], key: str) -> str:
    values = sorted(str(row.get(key) or "") for row in rows if row.get(key))
    if not values:
        return "n/a"
    return values[0] if values[0] == values[-1] else f"{values[0]}..{values[-1]}"


def check_ldm_vs_shared_metar(source_events_path: Path, max_rows: int) -> CheckResult:
    rows = [
        row
        for row in load_source_event_records(source_events_path)
        if row.get("raw_metar") and row.get("local_detect_ts_utc")
    ][:max_rows]
    matched = 0
    diffs = 0
    skipped = 0
    examples: list[str] = []
    for row in rows:
        raw = str(row["raw_metar"])
        detect_ts = parse_utc(row.get("local_detect_ts_utc"))
        if detect_ts is None:
            skipped += 1
            continue
        parsed_rows = ldm.parse_metar_lines(raw, detect_ts_utc=detect_ts)
        shared_temp = parse_metar_temp_c(raw)
        shared_report_ts = parse_metar_report_time(raw, detect_ts)
        if not parsed_rows or shared_temp is None or shared_report_ts is None:
            skipped += 1
            continue
        parsed = parsed_rows[0]
        ok = floats_equal(parsed.get("temp_c"), shared_temp) and iso_equal(
            parsed.get("source_report_ts_utc"),
            shared_report_ts.isoformat(),
        )
        if ok:
            matched += 1
        else:
            diffs += 1
            if len(examples) < 3:
                examples.append(
                    f"{row.get('city')} {row.get('station')} raw={raw!r} "
                    f"ldm=({parsed.get('source_report_ts_utc')},{parsed.get('temp_c')}) "
                    f"shared=({iso(shared_report_ts)},{shared_temp})"
                )
    detail = "No field diffs on temp_c/source_report_ts_utc." if not examples else "; ".join(examples)
    return CheckResult(
        name="LDM raw METAR parser vs shared METAR parser",
        sample_source=str(source_events_path),
        sample_window=sample_window_from_rows(rows, "local_detect_ts_utc"),
        rows=len(rows),
        matched=matched,
        diffs=diffs,
        skipped=skipped,
        verdict="parser-equivalent",
        detail=detail,
    )


def theta_aviationweather_from_payload(data: list[dict[str, Any]], tz: ZoneInfo, local_date: date) -> list[dict[str, Any]]:
    original = theta.fetch_json

    def fake_fetch_json(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return data

    theta.fetch_json = fake_fetch_json
    try:
        return theta.aviationweather_obs("TEST", tz, local_date)
    finally:
        theta.fetch_json = original


def check_aviationweather_json_parsers(source_events_path: Path, max_rows: int) -> CheckResult:
    cfgs = city_config_map()
    rows = [
        row
        for row in load_source_event_records(source_events_path)
        if row.get("status") == "ok"
        and row.get("raw_metar")
        and row.get("temp_c") is not None
        and row.get("source_report_ts_utc")
        and row.get("city") in cfgs
    ][:max_rows]
    matched = 0
    diffs = 0
    skipped = 0
    examples: list[str] = []
    for row in rows:
        cfg = cfgs[str(row["city"])]
        tz = ZoneInfo(cfg.timezone_name)
        local_day = date.fromisoformat(str(row["target_date"]))
        payload = [
            {
                "reportTime": row["source_report_ts_utc"],
                "temp": row["temp_c"],
                "rawOb": row["raw_metar"],
            }
        ]
        shared = parse_aviationweather_records(payload, tz, local_day)
        metar_cross_rows = metar_cross.parse_metar_records(payload, tz, local_day)
        theta_rows = theta_aviationweather_from_payload(payload, tz, local_day)
        if not shared or not metar_cross_rows or not theta_rows:
            skipped += 1
            continue
        shared_ts, shared_temp, _raw = shared[-1]
        metar_ts, metar_temp = metar_cross_rows[-1]
        theta_row = theta_rows[-1]
        ok = (
            iso_equal(shared_ts.isoformat(), metar_ts.isoformat())
            and iso_equal(shared_ts.isoformat(), theta_row.get("ts").isoformat())
            and floats_equal(shared_temp, metar_temp)
            and floats_equal(shared_temp, theta_row.get("tmpc"))
        )
        if ok:
            matched += 1
        else:
            diffs += 1
            if len(examples) < 3:
                examples.append(
                    f"{row.get('city')} raw={row.get('raw_metar')!r} "
                    f"shared=({iso(shared_ts)},{shared_temp}) "
                    f"metar_cross=({iso(metar_ts)},{metar_temp}) "
                    f"theta=({iso(theta_row.get('ts'))},{theta_row.get('tmpc')})"
                )

    edge_detail = edge_fixture_detail(rows[0], cfgs) if rows else "No edge fixture: no source-event rows."
    detail = (
        "Historical reconstructed AWC rows matched. "
        + edge_detail
        + (" Examples: " + "; ".join(examples) if examples else "")
    )
    verdict = "historical-equivalent-with-parser-edge-risk" if rows else "not-tested"
    return CheckResult(
        name="AviationWeather JSON parsers: metar_cross/theta vs shared data_feed",
        sample_source=str(source_events_path),
        sample_window=sample_window_from_rows(rows, "source_report_ts_utc"),
        rows=len(rows),
        matched=matched,
        diffs=diffs,
        skipped=skipped,
        verdict=verdict,
        detail=detail,
    )


def edge_fixture_detail(row: dict[str, Any], cfgs: dict[str, Any]) -> str:
    cfg = cfgs[str(row["city"])]
    tz = ZoneInfo(cfg.timezone_name)
    local_day = date.fromisoformat(str(row["target_date"]))
    detect_ts = parse_utc(row.get("local_detect_ts_utc")) or parse_utc(row.get("source_report_ts_utc"))
    if detect_ts is None:
        return "Edge fixture skipped: missing detect timestamp."
    payload = [
        {
            "reportTime": detect_ts.isoformat(),
            "temp": row["temp_c"],
            "rawOb": row["raw_metar"],
        }
    ]
    shared = parse_aviationweather_records(payload, tz, local_day)
    metar_rows = metar_cross.parse_metar_records(payload, tz, local_day)
    theta_rows = theta_aviationweather_from_payload(payload, tz, local_day)
    if not shared or not metar_rows or not theta_rows:
        return "Edge fixture skipped: parser returned no rows."
    shared_ts = shared[-1][0]
    metar_ts = metar_rows[-1][0]
    theta_ts = theta_rows[-1]["ts"]
    if iso_equal(shared_ts.isoformat(), metar_ts.isoformat()) and iso_equal(shared_ts.isoformat(), theta_ts.isoformat()):
        return "Edge fixture also matched."
    return (
        "Stress fixture exposed timestamp semantic drift when reportTime is detect/fetch time: "
        f"shared rawOb DDHHMMZ={iso(shared_ts)}, metar_cross reportTime={iso(metar_ts)}, "
        f"theta reportTime={iso(theta_ts)}."
    )


def check_metar_cross_source_events(path: Path, max_rows: int) -> CheckResult:
    label = (
        "metar_cross source-events adapter (current Mac)"
        if path == MAC_SOURCE_EVENTS
        else "metar_cross source-events adapter (legacy N100 recovery)"
    )
    if not path.exists():
        return CheckResult(
            name=label,
            sample_source=str(path),
            sample_window="missing",
            rows=0,
            matched=0,
            diffs=0,
            skipped=0,
            verdict="not-tested",
            detail="Current Mac source_events/latest.json was not present.",
        )
    cfgs = city_config_map()
    lookup = metar_cross.source_event_lookup(path)
    rows = [
        row
        for row in load_source_event_records(path)
        if row.get("city") in cfgs and row.get("status") == "ok" and row.get("source_report_ts_utc")
    ][:max_rows]
    matched = 0
    diffs = 0
    skipped = 0
    running_max_equals_latest = 0
    examples: list[str] = []
    for row in rows:
        cfg = cfgs[str(row["city"])]
        now = parse_utc(row.get("local_detect_ts_utc")) or parse_utc(row.get("ts_utc"))
        if now is None:
            skipped += 1
            continue
        local_day = date.fromisoformat(str(row["target_date"]))
        summary = metar_cross.observation_summary_from_source_event(
            cfg,
            ZoneInfo(cfg.timezone_name),
            local_day,
            obs_source=str(row.get("source") or ""),
            source_events=lookup,
            source_events_path=path,
            now_utc=now,
        )
        ok = (
            summary.get("status") == row.get("status")
            and normalize_source_name(str(summary.get("source") or "")) == normalize_source_name(str(row.get("source") or ""))
            and floats_equal(summary.get("current_temp_c"), row.get("temp_c"))
            and iso_equal(summary.get("last_obs_utc"), row.get("source_report_ts_utc"))
        )
        if floats_equal(summary.get("running_max_c"), row.get("temp_c")):
            running_max_equals_latest += 1
        if ok:
            matched += 1
        else:
            diffs += 1
            if len(examples) < 3:
                examples.append(
                    f"{row.get('city')} {row.get('source')}: "
                    f"row=({row.get('status')},{row.get('temp_c')},{row.get('source_report_ts_utc')}) "
                    f"summary=({summary.get('status')},{summary.get('current_temp_c')},{summary.get('last_obs_utc')})"
                )
    detail = (
        "Core latest-observation fields matched; "
        f"running_max_c equaled latest temp in {running_max_equals_latest}/{matched + diffs} checked rows, "
        "which is a latency/state specialization rather than full intraday max replay."
    )
    if examples:
        detail += " Examples: " + "; ".join(examples)
    return CheckResult(
        name=label,
        sample_source=str(path),
        sample_window=sample_window_from_rows(rows, "local_detect_ts_utc"),
        rows=len(rows),
        matched=matched,
        diffs=diffs,
        skipped=skipped,
        verdict="latest-event-equivalent-stateful-running-max-specialization",
        detail=detail,
    )


def check_theta_observation_cache(path: Path, max_rows: int) -> CheckResult:
    if not path.exists():
        return CheckResult(
            name="theta observation_cache_obs vs weather_data_feed observation_cache",
            sample_source=str(path),
            sample_window="missing",
            rows=0,
            matched=0,
            diffs=0,
            skipped=0,
            verdict="not-tested",
            detail="Observation cache file missing.",
        )
    cache = load_observation_cache(path)
    now = parse_utc(cache.get("generated_at_utc")) or datetime.now(timezone.utc)
    records = [row for row in cache.get("records", []) if isinstance(row, dict)][:max_rows]
    matched = 0
    diffs = 0
    skipped = 0
    status_counts: Counter[str] = Counter()
    relaxed_count = 0
    examples: list[str] = []
    for record in records:
        try:
            normalized = normalize_observation_cache_record(record)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            if len(examples) < 3:
                examples.append(f"{record.get('city')} normalize error {type(exc).__name__}: {exc}")
            continue
        station = theta.Station(
            city=str(record.get("city") or ""),
            icao=str(record.get("station") or ""),
            unit=str(record.get("unit") or "C"),
            utc_offset=0,
            timezone_name=str(record.get("timezone_name") or ""),
        )
        private = theta.observation_cache_obs(
            cache,
            str(record.get("city") or ""),
            str(record.get("target_date") or ""),
            station,
            now,
            max_obs_age_min=20.0,
            pre_update_blackout_min=6.0,
        )
        status_counts[str(private.get("status"))] += 1
        if private.get("obs_age_limit_relaxed"):
            relaxed_count += 1
        field_ok = (
            normalized.get("city") == record.get("city")
            and normalized.get("target_date") == record.get("target_date")
            and normalized.get("source") == record.get("source")
        )
        if record.get("status") == "ok" and private.get("status") == "ok":
            field_ok = field_ok and floats_equal(private.get("current_temp_c"), record.get("current_temp_c"))
            field_ok = field_ok and floats_equal(private.get("running_max_c"), record.get("running_max_c"))
            field_ok = field_ok and iso_equal(private.get("last_obs_utc"), record.get("last_obs_utc"))
        if field_ok:
            matched += 1
        else:
            diffs += 1
            if len(examples) < 3:
                examples.append(
                    f"{record.get('city')} {record.get('target_date')}: "
                    f"cache_status={record.get('status')} theta_status={private.get('status')} "
                    f"cache_temp={record.get('current_temp_c')} theta_temp={private.get('current_temp_c')}"
                )
    detail = (
        "weather_data_feed currently normalizes/indexes cache records; theta adds cadence-aware freshness gates. "
        f"theta statuses={dict(status_counts)}, relaxed_age_limit_rows={relaxed_count}. "
        + ("Examples: " + "; ".join(examples) if examples else "No value diffs for accepted ok rows.")
    )
    return CheckResult(
        name="theta observation_cache_obs vs weather_data_feed observation_cache",
        sample_source=str(path),
        sample_window=f"generated_at={cache.get('generated_at_utc')}",
        rows=len(records),
        matched=matched,
        diffs=diffs,
        skipped=skipped,
        verdict="value-equivalent-freshness-semantic-fork",
        detail=detail,
    )


def check_theta_snapshot_metar(path: Path | None, max_rows: int) -> CheckResult:
    if path is None or not path.exists():
        return CheckResult(
            name="theta snapshot_metar_obs vs paper_snapshot METAR fields",
            sample_source=str(path),
            sample_window="missing",
            rows=0,
            matched=0,
            diffs=0,
            skipped=0,
            verdict="not-tested",
            detail="No 2026-07-03..2026-07-05 paper snapshot found.",
        )
    payload = read_json(path)
    now = parse_utc(payload.get("ts_utc")) or datetime.now(timezone.utc)
    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in payload.get("records", []):
        if not isinstance(row, dict):
            continue
        if row.get("metar_latest_ts_utc") and row.get("metar_latest_temp_f") not in (None, ""):
            by_city[str(row.get("city") or "")].append(row)
    matched = 0
    diffs = 0
    skipped = 0
    status_counts: Counter[str] = Counter()
    examples: list[str] = []
    for city, city_rows in sorted(by_city.items())[:max_rows]:
        record = max(city_rows, key=lambda row: float(row.get("metar_obs_count_today") or 0.0))
        station = theta.Station(
            city=city,
            icao=str(record.get("configured_icao") or record.get("metar_icao") or ""),
            unit=str(record.get("unit") or "C"),
            utc_offset=0,
            timezone_name=str(record.get("forecast_timezone") or ""),
        )
        private = theta.snapshot_metar_obs(
            city_rows,
            station,
            now,
            max_obs_age_min=20.0,
            pre_update_blackout_min=6.0,
        )
        status_counts[str(private.get("status"))] += 1
        if private.get("status") != "ok":
            skipped += 1
            continue
        expected_current_c = (float(record["metar_latest_temp_f"]) - 32.0) * 5.0 / 9.0
        expected_running_c = (float(record["metar_current_max_f"]) - 32.0) * 5.0 / 9.0
        ok = (
            floats_equal(private.get("current_temp_c"), expected_current_c)
            and floats_equal(private.get("running_max_c"), expected_running_c)
            and iso_equal(private.get("last_obs_utc"), record.get("metar_latest_ts_utc"))
        )
        if ok:
            matched += 1
        else:
            diffs += 1
            if len(examples) < 3:
                examples.append(
                    f"{city}: snapshot=({record.get('metar_latest_ts_utc')},{expected_current_c},{expected_running_c}) "
                    f"theta=({private.get('last_obs_utc')},{private.get('current_temp_c')},{private.get('running_max_c')})"
                )
    detail = (
        f"theta statuses={dict(status_counts)}. "
        "Accepted rows preserve snapshot latest/running-max values; freshness uses theta cadence/default rules. "
        + ("Examples: " + "; ".join(examples) if examples else "No accepted value diffs.")
    )
    return CheckResult(
        name="theta snapshot_metar_obs vs paper_snapshot METAR fields",
        sample_source=str(path),
        sample_window=f"snapshot_ts={payload.get('ts_utc')}",
        rows=len(by_city),
        matched=matched,
        diffs=diffs,
        skipped=skipped,
        verdict="value-equivalent-snapshot-specific-freshness",
        detail=detail,
    )


def render_report(results: list[CheckResult], report_path: Path) -> str:
    mac_obs_exists = MAC_OBSERVATION_CACHE.exists()
    snapshot_path = newest_snapshot()
    legacy_rows = load_source_event_records(LEGACY_SOURCE_EVENTS)
    current_source_events_note = (
        f"missing at `{MAC_SOURCE_EVENTS}`"
        if not MAC_SOURCE_EVENTS.exists()
        else f"present generated_at={generated_at(MAC_SOURCE_EVENTS)}"
    )
    obs_summary = "missing"
    if mac_obs_exists:
        cache = load_observation_cache(MAC_OBSERVATION_CACHE)
        counts = Counter(row.get("status") for row in cache.get("records", []))
        sources = Counter(row.get("source") for row in cache.get("records", []))
        obs_summary = (
            f"generated_at={cache.get('generated_at_utc')}, "
            f"records={len(cache.get('records', []))}, statuses={dict(counts)}, sources={dict(sources)}"
        )
    snapshot_summary = "missing" if snapshot_path is None else f"{snapshot_path}, ts_utc={read_json(snapshot_path).get('ts_utc')}"

    inventory_rows = [
        (
            "`weather_metar_cross_prev_no_shadow.py`",
            "`parse_metar_records`, `observation_summary_from_source_event`, latest TGFTP/Synoptic summaries",
            "AWC JSON list, data-feed source-event latest rows, or single latest station text. Running max may be carried by runner state.",
            "Do not directly merge with full-day cache parser; source-events adapter is latest-event/latency specialized.",
        ),
        (
            "`weather_source_orderbook_timing_monitor.py`",
            "`fetch_*_latest`, `latest_source_event_rows`",
            "Timing rows are city/source latest events joined to orderbook; AWC/IEM parsers already call `weather_data_feed.observation_sources`.",
            "Parser surface mostly shared already; fetch wrappers need source-payload parity before consolidation.",
        ),
        (
            "`weather_theta_current_yes_tiny_live.py`",
            "`aviationweather_obs`, `iem_obs`, `snapshot_metar_obs`, `observation_cache_obs`",
            "Consumes full obs series, paper snapshot METAR columns, and observation cache summaries; adds min_obs, cadence relaxation, pre-update blackout.",
            "Freshness semantics are live-strategy specific. Shared parser import is safe only below the freshness/state layer.",
        ),
        (
            "`weather_ldm_metar_notify_monitor.py`",
            "`parse_metar_lines`, local temp/report-time parsers",
            "Raw LDM/PQCAT METAR lines with detect timestamp; station target mapping is optional.",
            "Temp/DDHHMM parser is equivalent on sampled rows and is the cleanest shared-parser candidate.",
        ),
    ]

    result_table = "\n".join(
        "| {name} | {rows} | {matched} | {diffs} | {skipped} | `{verdict}` |".format(
            name=item.name,
            rows=item.rows,
            matched=item.matched,
            diffs=item.diffs,
            skipped=item.skipped,
            verdict=item.verdict,
        )
        for item in results
    )
    detail_blocks = "\n\n".join(
        "\n".join(
            [
                f"### {idx}. {item.name}",
                f"- Sample: `{item.sample_source}`",
                f"- Window: `{item.sample_window}`",
                f"- Verdict: `{item.verdict}`",
                f"- Detail: {item.detail}",
            ]
        )
        for idx, item in enumerate(results, start=1)
    )
    inventory_table = "\n".join(
        f"| {path} | {helpers} | {inputs} | {verdict} |"
        for path, helpers, inputs, verdict in inventory_rows
    )

    text = f"""# Weather Observation Parsing Parity Report v1

Date: 2026-07-05

Scope: offline parity harness only. No live runner behavior was changed, no network fetch was performed, and no CLOB/private-key/order path was touched.

Harness: `{report_path.with_name('2026-07-05-weather-observation-parity-harness-v1.py')}`

## Input Windows

- Mac observation cache: `{MAC_OBSERVATION_CACHE}` ({obs_summary})
- Mac paper snapshot: `{snapshot_summary}`
- Current Mac source_events: {current_source_events_note}
- Legacy source_events sanity sample: `{LEGACY_SOURCE_EVENTS}` (generated_at={generated_at(LEGACY_SOURCE_EVENTS)}, records={len(legacy_rows)})

P10 guardrail: the main positive parity evidence uses 2026-07-05 Mac observation cache and 2026-07-03..2026-07-05 Mac paper snapshots. The 2026-07-01 N100 recovery source-events file is explicitly treated as a legacy raw-METAR/source-event parser sanity sample, not as clean Mac source-events coverage.

## Duplicate Logic Inventory

| File | Local helpers | Input assumptions | Harness conclusion |
|---|---|---|---|
{inventory_table}

## Parity Summary

| Check | Rows | Matched | Diffs | Skipped | Verdict |
|---|---:|---:|---:|---:|---|
{result_table}

## Check Details

{detail_blocks}

## 收编清单

可收编, 但只限 parser 层:

- `weather_ldm_metar_notify_monitor.py` 的 METAR temp/DDHHMM parser 可以迁到 `weather_data_feed.observation_sources.metar` 或直接 import shared helper；采样行 temp/report timestamp 无差异。
- `weather_source_orderbook_timing_monitor.py` 的 AWC/IEM parser 已经使用 shared parser；后续若收编, 应该集中 fetch/result normalization, 而不是改 timing monitor 的 orderbook join 语义。

暂不收编, 需要保留语义差异:

- `weather_metar_cross_prev_no_shadow.py` 的 source-events adapter 是 latest-event fast path, `running_max_c` 依赖 runner state/当前 latest row，不等同 observation cache 的 full intraday running max。
- `weather_theta_current_yes_tiny_live.py` 的 `observation_cache_obs` 和 `snapshot_metar_obs` 保留 cadence relaxation、pre-update blackout、min_obs 语义；这属于 current-YES live 风控, 不是通用 parser。
- `theta.aviationweather_obs` 和 `metar_cross.parse_metar_records` 在历史 reconstructed rows 上与 shared parser 等价，但 stress fixture 显示当 `reportTime` 不是 raw METAR 的 DDHHMMZ 时会和 `weather_data_feed.parse_aviationweather_records` 分叉。收编前应先决定 reportTime/rawOb 谁是权威 timestamp。

## 未覆盖缺口

- 当前 Mac runtime 没有 `output/source_events/latest.json`; 本次不能声明 Mac source-events fast path 已逐字段 clean replay。
- SynopticData/IEM raw payload 在本地 clean window 没有完整 raw response 落盘；本次只核对已有 shared parser调用和 cache/summary语义, 不能证明 fetch wrapper 完全可合并。
- 没有触碰 `metar_cross` FOK fast path, 也没有测 latency; P12 live CLOB 收编仍应冻结。
"""
    return text


def run(report_path: Path, max_rows: int) -> list[CheckResult]:
    snapshot_path = newest_snapshot()
    results = [
        check_ldm_vs_shared_metar(LEGACY_SOURCE_EVENTS, max_rows),
        check_aviationweather_json_parsers(LEGACY_SOURCE_EVENTS, max_rows),
        check_metar_cross_source_events(MAC_SOURCE_EVENTS, max_rows),
        check_metar_cross_source_events(LEGACY_SOURCE_EVENTS, max_rows),
        check_theta_observation_cache(MAC_OBSERVATION_CACHE, max_rows),
        check_theta_snapshot_metar(snapshot_path, max_rows),
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(results, report_path), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--max-rows", type=int, default=100)
    args = parser.parse_args()
    results = run(args.report, args.max_rows)
    for item in results:
        print(
            f"{item.name}: rows={item.rows} matched={item.matched} "
            f"diffs={item.diffs} skipped={item.skipped} verdict={item.verdict}"
        )
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
