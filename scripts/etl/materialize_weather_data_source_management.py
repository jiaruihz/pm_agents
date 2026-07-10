#!/usr/bin/env python3
"""Materialize weather_data_source_profile and weather_data_monitor_instance tables.

Reads from:
  - weather_data_feed/source_profiles.json
  - weather_data_feed/high_frequency_observation_sources.supported_high_frequency_sources()
  - weather_data_feed/runway_sources.supported_runway_cities()
  - output directories under the data-feed runtime root

Idempotent: uses INSERT OR REPLACE on natural keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.connection import get_conn

DEFAULT_DB = str(PROJECT_ROOT / "runtime" / "weather.db")
DEFAULT_RUNTIME_ROOT = os.environ.get(
    "WEATHER_DATA_FEED_RUNTIME_ROOT",
    "/Volumes/jrs/weather_data_feed_service_runtime",
)

AUTH_REQUIRED_SOURCES = {"cwa", "knmi", "ncm_jeddah", "aeroweb"}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _profile_id(city: str, feed_kind: str, source_key: str,
                station_or_feed: str | None, runway: str | None) -> str:
    raw = f"{city}|{feed_kind}|{source_key}|{station_or_feed or ''}|{runway or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Profile builders ────────────────────────────────────────────────────────

def build_official_observation_profiles() -> list[dict]:
    profiles_path = PROJECT_ROOT / "weather_data_feed" / "source_profiles.json"
    with open(profiles_path, encoding="utf-8") as f:
        data = json.load(f)
    source_profiles = data.get("source_profiles", data)
    if isinstance(source_profiles, dict):
        source_profiles = list(source_profiles.values())

    rows = []
    for sp in source_profiles:
        city = sp.get("city", "")
        station = sp.get("official_station_or_feed") or sp.get("configured_icao") or ""
        icao = sp.get("configured_icao") or ""
        tz = sp.get("timezone_name", "")
        live = 1 if sp.get("live_eligible") else 0
        blocked = sp.get("blocked_reason", "")

        primary = sp.get("primary_source", "aviationweather_metar")
        rows.append({
            "profile_id": _profile_id(city, "official_observation", primary, station, None),
            "feed_kind": "official_observation",
            "city": city,
            "source_key": primary,
            "source_kind": "metar_api",
            "station_or_feed": station,
            "icao": icao,
            "runway": None,
            "source_role": "primary",
            "timezone_name": tz,
            "expected_cadence_sec": 1800,
            "staleness_max_age_sec": 3600,
            "active_window_json": "{}",
            "requires_auth": 0,
            "auth_ref": None,
            "strategy_eligible": live,
            "live_eligible": live,
            "notes": blocked or sp.get("source_profile_note", ""),
        })

        for fb in sp.get("fallback_sources", []):
            rows.append({
                "profile_id": _profile_id(city, "official_observation", fb, station, None),
                "feed_kind": "official_observation",
                "city": city,
                "source_key": fb,
                "source_kind": "metar_api",
                "station_or_feed": station,
                "icao": icao,
                "runway": None,
                "source_role": "fallback",
                "timezone_name": tz,
                "expected_cadence_sec": 1800,
                "staleness_max_age_sec": 3600,
                "active_window_json": "{}",
                "requires_auth": 0,
                "auth_ref": None,
                "strategy_eligible": 0,
                "live_eligible": 0,
                "notes": f"fallback for {city}",
            })
    return rows


def build_high_frequency_profiles() -> list[dict]:
    from weather_data_feed.high_frequency_observation_sources import supported_high_frequency_sources
    srcs = supported_high_frequency_sources()
    rows = []
    for source_key, cities in srcs.items():
        requires_auth = 1 if source_key in AUTH_REQUIRED_SOURCES else 0
        auth_ref = f"WEATHER_DATA_FEED_{source_key.upper()}_KEY" if requires_auth else None
        for city, info in cities.items():
            station = info.get("station", "")
            icao = info.get("icao", "")
            tz = info.get("timezone_name", "")
            rows.append({
                "profile_id": _profile_id(city, "high_frequency_observation", source_key, station, None),
                "feed_kind": "high_frequency_observation",
                "city": city,
                "source_key": source_key,
                "source_kind": info.get("source_kind", "official_airport_station"),
                "station_or_feed": station,
                "icao": icao,
                "runway": None,
                "source_role": "reference",
                "timezone_name": tz,
                "expected_cadence_sec": 60,
                "staleness_max_age_sec": 600,
                "active_window_json": json.dumps({"start_hour": 6, "end_hour": 22}),
                "requires_auth": requires_auth,
                "auth_ref": auth_ref,
                "strategy_eligible": 0 if requires_auth else 1,
                "live_eligible": 0,
                "notes": info.get("label", ""),
            })
    return rows


def build_runway_profiles() -> list[dict]:
    from weather_data_feed.runway_sources import supported_runway_cities
    cities = supported_runway_cities()
    rows = []
    for city, info in cities.items():
        station = info.get("station", "")
        runway = info.get("configured_runway_target", "")
        source_key = "amsc_awos" if info.get("configured_runway_target") else "amos_runway"
        rows.append({
            "profile_id": _profile_id(city, "runway_observation", source_key, station, runway),
            "feed_kind": "runway_observation",
            "city": city,
            "source_key": source_key,
            "source_kind": "runway_air_temperature",
            "station_or_feed": station,
            "icao": station,
            "runway": runway or None,
            "source_role": "runway",
            "timezone_name": info.get("timezone_name", ""),
            "expected_cadence_sec": 60,
            "staleness_max_age_sec": 300,
            "active_window_json": json.dumps({"start_hour": 6, "end_hour": 22}),
            "requires_auth": 1 if info.get("configured_runway_target") else 0,
            "auth_ref": "WEATHER_DATA_FEED_AMSC_SESSION_ID" if info.get("configured_runway_target") else None,
            "strategy_eligible": 0,
            "live_eligible": 0,
            "notes": info.get("label", ""),
        })
    return rows


# ── Monitor instance builders ───────────────────────────────────────────────

MONITOR_DEFS: list[dict] = [
    {
        "monitor_instance_id": "source_events",
        "display_name": "Source Events (METAR/WU)",
        "feed_kind": "official_observation",
        "sources": ["aviationweather_metar", "aviationweather_cache_csv"],
        "scan_interval_sec": 120,
        "subdir": "source_events",
        "journals": ["sources.jsonl"],
        "start_command": "weather_data_feed_service source-events",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "high_frequency_observations",
        "display_name": "High-Frequency Airport Observations",
        "feed_kind": "high_frequency_observation",
        "sources": ["amos_runway", "noaa_madis_hfmetar", "singapore_mss", "jma_amedas",
                     "hko_obs", "cowin_obs", "fmi", "mgm", "ims_lod"],
        "scan_interval_sec": 60,
        "subdir": "high_frequency_observations",
        "journals": ["high_frequency_observations.jsonl"],
        "start_command": "weather_data_feed_service high-frequency-observations",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "runway_observations",
        "display_name": "Runway Point-Position Observations",
        "feed_kind": "runway_observation",
        "sources": ["amsc_awos", "amos_runway"],
        "scan_interval_sec": 60,
        "subdir": "runway_observations",
        "journals": ["runway_observations.jsonl"],
        "start_command": "weather_data_feed_service runway-observations",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "forecast_enrichment",
        "display_name": "Forecast Enrichment (Open-Meteo/TAF)",
        "feed_kind": "forecast",
        "sources": ["open_meteo_multi_model", "aviationweather_taf"],
        "scan_interval_sec": 300,
        "subdir": "forecast_enrichment",
        "journals": ["forecast_enrichment.jsonl"],
        "start_command": "weather_data_feed_service forecast-enrichment",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "fast_source_stale_book",
        "display_name": "Fast-Source Stale-Book Observer",
        "feed_kind": "high_frequency_observation",
        "sources": ["amos_runway", "noaa_madis_hfmetar", "jma_amedas", "singapore_mss", "fmi"],
        "scan_interval_sec": 60,
        "subdir": "fast_source_stale_book",
        "journals": ["events.jsonl", "quote_snapshots.jsonl"],
        "start_command": "scripts/ops/start_weather_fast_source_stale_book_observer.sh",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "hko_running_max_stale_book_shadow",
        "display_name": "HKO Running-Max Stale-Book Shadow",
        "feed_kind": "high_frequency_observation",
        "sources": ["hko_obs"],
        "scan_interval_sec": 30,
        "subdir": "hko_running_max_stale_book_shadow",
        "journals": ["events.jsonl", "quote_snapshots.jsonl"],
        "start_command": "scripts/ops/start_weather_hko_running_max_stale_book_shadow.sh",
        "tmux_session": "weather_hko_running_max_stale_book_shadow",
    },
    {
        "monitor_instance_id": "hko_running_min_stale_book_shadow",
        "display_name": "HKO Running-Min Stale-Book Shadow",
        "feed_kind": "high_frequency_observation",
        "sources": ["hko_obs"],
        "scan_interval_sec": 30,
        "subdir": "hko_running_min_stale_book_shadow",
        "journals": ["events.jsonl", "quote_snapshots.jsonl"],
        "start_command": "scripts/ops/start_weather_hko_running_min_stale_book_shadow.sh",
        "tmux_session": "weather_hko_running_min_stale_book_shadow",
    },
    {
        "monitor_instance_id": "wu_running_min_stale_book_shadow",
        "display_name": "WU Airport Running-Min Stale-Book Shadow",
        "feed_kind": "high_frequency_observation",
        "sources": ["amos_runway", "jma_amedas"],
        "scan_interval_sec": 30,
        "subdir": "wu_running_min_stale_book_shadow",
        "journals": ["events.jsonl", "quote_snapshots.jsonl"],
        "start_command": "scripts/ops/start_weather_wu_running_min_stale_book_shadow.sh",
        "tmux_session": "weather_wu_running_min_stale_book_shadow",
    },
    {
        "monitor_instance_id": "fast_source_prev_no_trial",
        "display_name": "Fast-Source Prev-NO Trial (live)",
        "feed_kind": "high_frequency_observation",
        "sources": ["amos_runway", "noaa_madis_hfmetar", "jma_amedas", "singapore_mss", "fmi"],
        "scan_interval_sec": 60,
        "subdir": "fast_source_prev_no_trial",
        "journals": ["events.jsonl", "opportunities.jsonl", "orders.jsonl"],
        "start_command": "scripts/ops/start_weather_fast_source_prev_no_trial.sh",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "orderbook_snapshots",
        "display_name": "Orderbook Snapshots",
        "feed_kind": "orderbook",
        "sources": ["polymarket_clob"],
        "scan_interval_sec": 60,
        "subdir": "orderbook_snapshots",
        "journals": [],
        "start_command": "weather_data_feed_service orderbook-snapshots",
        "tmux_session": "weather_data_feed_jrs",
    },
    {
        "monitor_instance_id": "paper_snapshots",
        "display_name": "Paper Snapshots (Market Data)",
        "feed_kind": "orderbook",
        "sources": ["polymarket_clob"],
        "scan_interval_sec": 300,
        "subdir": "paper_snapshots",
        "journals": [],
        "start_command": "weather_data_feed_service paper-snapshots",
        "tmux_session": "weather_data_feed_jrs",
    },
]


def build_monitor_instances(runtime_root: str) -> list[dict]:
    root = Path(runtime_root) / "output"
    now = _now_utc()
    rows = []
    for mdef in MONITOR_DEFS:
        subdir = root / mdef["subdir"]
        output_dir = str(subdir) if subdir.is_dir() else str(subdir)
        latest_path = str(subdir / "latest.json") if (subdir / "latest.json").is_file() else None
        state_path = str(subdir / "state.json") if (subdir / "state.json").is_file() else None

        journal_paths = []
        for j in mdef.get("journals", []):
            jp = subdir / j
            if jp.is_file():
                journal_paths.append(str(jp))

        cities: list[str] = []
        summary: dict = {}
        if latest_path and Path(latest_path).is_file():
            try:
                with open(latest_path, encoding="utf-8") as f:
                    latest_data = json.load(f)
                if isinstance(latest_data.get("cities"), list):
                    cities = latest_data["cities"]
                elif isinstance(latest_data.get("active_job_cities"), list):
                    cities = latest_data["active_job_cities"]
                summary = {
                    k: latest_data.get(k)
                    for k in ("generated_at_utc", "producer", "ok_sources", "non_ok_sources",
                              "rows", "status", "schema_version")
                    if latest_data.get(k) is not None
                }
            except Exception:
                pass

        rows.append({
            "monitor_instance_id": mdef["monitor_instance_id"],
            "display_name": mdef["display_name"],
            "feed_kind": mdef["feed_kind"],
            "sources_json": json.dumps(mdef["sources"]),
            "cities_json": json.dumps(cities),
            "scan_interval_sec": mdef.get("scan_interval_sec"),
            "active_window_json": json.dumps({"start_hour": 6, "end_hour": 22}),
            "output_dir": output_dir,
            "latest_path": latest_path,
            "journal_paths_json": json.dumps(journal_paths),
            "state_path": state_path,
            "proxy_policy": "direct" if mdef["feed_kind"] != "orderbook" else "market_proxy",
            "auth_refs_json": "[]",
            "desired_status": "enabled",
            "host": "mac",
            "tmux_session": mdef.get("tmux_session"),
            "start_command": mdef.get("start_command"),
            "summary_json": json.dumps(summary),
            "updated_at_utc": now,
        })
    return rows


# ── DB write ────────────────────────────────────────────────────────────────

def upsert_profiles(conn: sqlite3.Connection, profiles: list[dict]) -> int:
    now = _now_utc()
    count = 0
    for p in profiles:
        p.setdefault("updated_at_utc", now)
        conn.execute(
            """INSERT OR REPLACE INTO weather_data_source_profile
            (profile_id, feed_kind, city, source_key, source_kind, station_or_feed,
             icao, runway, source_role, timezone_name, expected_cadence_sec,
             staleness_max_age_sec, active_window_json, requires_auth, auth_ref,
             strategy_eligible, live_eligible, observed_median_lag_sec,
             observed_p95_lag_sec, notes, updated_at_utc)
            VALUES (:profile_id, :feed_kind, :city, :source_key, :source_kind,
             :station_or_feed, :icao, :runway, :source_role, :timezone_name,
             :expected_cadence_sec, :staleness_max_age_sec, :active_window_json,
             :requires_auth, :auth_ref, :strategy_eligible, :live_eligible,
             :observed_median_lag_sec, :observed_p95_lag_sec, :notes, :updated_at_utc)""",
            {
                **p,
                "observed_median_lag_sec": p.get("observed_median_lag_sec"),
                "observed_p95_lag_sec": p.get("observed_p95_lag_sec"),
            },
        )
        count += 1
    return count


def upsert_monitor_instances(conn: sqlite3.Connection, instances: list[dict]) -> int:
    count = 0
    for mi in instances:
        conn.execute(
            """INSERT OR REPLACE INTO weather_data_monitor_instance
            (monitor_instance_id, display_name, feed_kind, sources_json, cities_json,
             scan_interval_sec, active_window_json, output_dir, latest_path,
             journal_paths_json, state_path, proxy_policy, auth_refs_json,
             desired_status, host, tmux_session, start_command, summary_json,
             updated_at_utc)
            VALUES (:monitor_instance_id, :display_name, :feed_kind, :sources_json,
             :cities_json, :scan_interval_sec, :active_window_json, :output_dir,
             :latest_path, :journal_paths_json, :state_path, :proxy_policy,
             :auth_refs_json, :desired_status, :host, :tmux_session, :start_command,
             :summary_json, :updated_at_utc)""",
            mi,
        )
        count += 1
    return count


def materialize(db_path: str = DEFAULT_DB, runtime_root: str = DEFAULT_RUNTIME_ROOT) -> dict:
    conn = get_conn(db_path)
    try:
        apply_schema_canonical(conn)

        profiles = (
            build_official_observation_profiles()
            + build_high_frequency_profiles()
            + build_runway_profiles()
        )
        p_count = upsert_profiles(conn, profiles)

        instances = build_monitor_instances(runtime_root)
        m_count = upsert_monitor_instances(conn, instances)

        conn.commit()
        return {"profiles": p_count, "monitor_instances": m_count}
    finally:
        conn.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Materialize weather data-source management tables")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to weather.db")
    parser.add_argument("--runtime-root", default=DEFAULT_RUNTIME_ROOT,
                        help="weather_data_feed_service_runtime root")
    args = parser.parse_args()
    result = materialize(args.db, args.runtime_root)
    print(f"Materialized {result['profiles']} profiles, {result['monitor_instances']} monitor instances.")


if __name__ == "__main__":
    main()
