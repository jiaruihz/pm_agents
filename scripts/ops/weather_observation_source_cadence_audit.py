#!/usr/bin/env python3
"""Audit official observation source cadence and first-seen latency.

This is data-layer telemetry. It records, per city/source:
- source observation report time
- when our fetcher first saw that observation
- inferred cadence from recent records exposed by the source

Run `loop` for useful first-seen latency; one `cycle` can only report the
current source age at fetch time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_sources import (  # noqa: E402
    FetchSettings,
    ObservationSourceRequest,
    expand_source_names,
    fetch_observation_source,
    infer_cadence_min,
    normalize_source_name,
)
from weather_data_feed.observation_sources.fetchers import source_age_sec, source_station_id  # noqa: E402
from weather_data_feed.source_policy import CityConfig, city_slug, load_city_configs  # noqa: E402
from weather_data_feed.source_registry import load_source_profiles  # noqa: E402


DATA_ROOT = Path(os.environ.get("OBS_CADENCE_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/observation_source_cadence"
DEFAULT_TIMEOUT_SEC = float(os.environ.get("OBS_CADENCE_HTTP_TIMEOUT_SEC", "3.0"))
SYNOPTIC_CLASSES = {"non_wu_source_by_rules"}


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


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def station_id_from_profile(profile: Any) -> str:
    for value in (profile.official_station_or_feed, profile.official_source, profile.configured_icao):
        station = source_station_id(value)
        if station:
            return station
    return ""


def research_live_source(profile: Any) -> str:
    if profile.primary_source:
        return profile.primary_source
    raw = " ".join([str(profile.official_source or ""), str(profile.official_station_or_feed or "")]).lower()
    if "weather.gov/wrh" in raw or profile.settlement_source_class in SYNOPTIC_CLASSES:
        return "synopticdata_timeseries"
    return ""


def load_audit_city_configs(*, include_station_diff: bool, include_research_cities: bool, only_cities: set[str] | None) -> list[CityConfig]:
    configs = {
        cfg.city: cfg
        for cfg in load_city_configs(include_station_diff=include_station_diff, only_cities=only_cities)
    }
    if include_research_cities:
        for city, profile in sorted(load_source_profiles().items()):
            if only_cities and city not in only_cities:
                continue
            if city in configs:
                continue
            station = station_id_from_profile(profile)
            live_source = research_live_source(profile)
            if not station or not live_source:
                continue
            configs[city] = CityConfig(
                city=city,
                slug=city_slug(city),
                unit=profile.unit,
                timezone_name=profile.timezone_name,
                official_icao=station,
                settlement_source_class=profile.settlement_source_class,
                settlement_source=profile.official_source,
                live_observation_source=live_source,
                fallback_sources=profile.fallback_sources,
                mapping_rule=profile.mapping_rule,
                registry_class="research_source_profile",
                alignment_days=profile.alignment_days or 0,
                alignment_rate=profile.alignment_rate or 0.0,
                rules_recheck_required=profile.rules_recheck_required,
                source_profile_note=profile.source_profile_note,
            )
    return sorted(configs.values(), key=lambda row: row.city)


def expanded_sources(cfg: CityConfig, requested: list[str]) -> list[str]:
    return expand_source_names(
        requested,
        primary=cfg.live_observation_source,
        fallback_sources=cfg.fallback_sources,
    )


def observation_row(cfg: CityConfig, source_name: str, now_utc: datetime, settings: FetchSettings, state: dict[str, Any]) -> dict[str, Any]:
    source_key = normalize_source_name(source_name)
    target_date = now_utc.astimezone(ZoneInfo(cfg.timezone_name)).date().isoformat()
    result = fetch_observation_source(
        ObservationSourceRequest(
            city=cfg.city,
            station_or_feed=cfg.official_icao,
            target_date=target_date,
            timezone_name=cfg.timezone_name,
            source_key=source_key,
            metadata={"recent_minutes": 360},
        ),
        settings=settings,
    )
    latest = result.records[-1] if result.records else None
    latest_obs_ts = latest.obs_ts_utc if latest else ""
    state_key = f"{cfg.city}|{result.source_key}|{latest_obs_ts}"
    first_seen_utc = state.setdefault(state_key, result.fetched_at_utc) if latest_obs_ts else ""
    latest_dt = parse_dt(latest_obs_ts)
    first_seen_dt = parse_dt(first_seen_utc)
    obs_minute_utc = latest_dt.strftime("%H:%M") if latest_dt else ""
    first_seen_minute_utc = first_seen_dt.strftime("%H:%M") if first_seen_dt else ""
    return {
        "ts_utc": result.fetched_at_utc,
        "city": cfg.city,
        "target_date": target_date,
        "timezone_name": cfg.timezone_name,
        "source": result.source_key,
        "station": cfg.official_icao,
        "status": result.status,
        "n_recent_records": len(result.records),
        "latest_obs_ts_utc": latest_obs_ts,
        "latest_obs_minute_utc": obs_minute_utc,
        "latest_temp_c": latest.temp_c if latest else None,
        "first_seen_utc": first_seen_utc,
        "first_seen_minute_utc": first_seen_minute_utc,
        "first_seen_lag_sec": None
        if not latest_dt or not first_seen_dt
        else round((first_seen_dt - latest_dt).total_seconds(), 3),
        "source_age_sec_at_fetch": source_age_sec(latest_obs_ts, parse_dt(result.fetched_at_utc) or now_utc),
        "fetch_latency_ms": result.latency_ms,
        "estimated_cadence_min": infer_cadence_min(list(result.records)),
        "report_minutes_utc": sorted({(parse_dt(row.obs_ts_utc) or now_utc).strftime("%H:%M") for row in result.records}),
        "error": result.error,
    }


def cycle(configs: list[CityConfig], *, sources: list[str], max_workers: int, settings: FetchSettings) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = OUT_DIR / "state.json"
    state = read_json(state_path, {})
    now_utc = datetime.now(timezone.utc)
    jobs = [(cfg, source_name) for cfg in configs for source_name in expanded_sources(cfg, sources)]
    counts = {"cities": len(configs), "jobs": len(jobs), "ok": 0, "empty": 0, "errors": 0}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(observation_row, cfg, source_name, now_utc, settings, state): (cfg, source_name)
            for cfg, source_name in jobs
        }
        for future in as_completed(futures):
            cfg, source_name = futures[future]
            try:
                row = future.result()
                if row["status"] == "ok":
                    counts["ok"] += 1
                else:
                    counts["empty"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                row = {
                    "ts_utc": datetime.now(timezone.utc).isoformat(),
                    "city": cfg.city,
                    "source": normalize_source_name(source_name),
                    "station": cfg.official_icao,
                    "status": "fetch_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            append_jsonl(OUT_DIR / "sources.jsonl", row)
    write_json(state_path, state)
    return {"out_dir": str(OUT_DIR), **counts}


def report() -> int:
    path = OUT_DIR / "sources.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        latest[(row.get("city", ""), row.get("source", ""))] = row
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in latest.values():
        by_source.setdefault(str(row.get("source")), []).append(row)
    summary = {
        "rows": len(rows),
        "latest_pairs": len(latest),
        "out_dir": str(OUT_DIR),
        "sources": {
            source: {
                "pairs": len(items),
                "ok": sum(1 for row in items if row.get("status") == "ok"),
                "cadence_min_counts": _counts(row.get("estimated_cadence_min") for row in items),
                "latest_obs_minute_utc_counts": _counts(row.get("latest_obs_minute_utc") for row in items if row.get("latest_obs_minute_utc")),
                "first_seen_lag_sec_avg": _avg(row.get("first_seen_lag_sec") for row in items),
            }
            for source, items in sorted(by_source.items())
        },
        "latest": sorted(latest.values(), key=lambda row: (str(row.get("city")), str(row.get("source")))),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda item: item[0]))


def _avg(values: Any) -> float | None:
    vals = [float(value) for value in values if value is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit city/source observation cadence and first-seen lag.")
    parser.add_argument("command", choices=["cycle", "loop", "report"], nargs="?", default="cycle")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-research-cities", action="store_true")
    parser.add_argument("--sources", nargs="*", default=["source_profiles"])
    parser.add_argument("--interval-sec", type=float, default=60.0)
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC)
    args = parser.parse_args()

    if args.command == "report":
        return report()

    configs = load_audit_city_configs(
        include_station_diff=args.include_station_diff,
        include_research_cities=args.include_research_cities,
        only_cities=set(args.cities or []) or None,
    )
    if not configs:
        raise SystemExit("no eligible city configs")
    settings = FetchSettings(timeout_sec=args.timeout_sec, proxy_candidates=(None,))
    print(
        json.dumps(
            {
                "command": args.command,
                "cities": [cfg.city for cfg in configs],
                "sources": args.sources,
                "out_dir": str(OUT_DIR),
                "interval_sec": args.interval_sec,
                "max_workers": args.max_workers,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    while True:
        started = datetime.now(timezone.utc)
        counts = cycle(configs, sources=args.sources, max_workers=args.max_workers, settings=settings)
        ended = datetime.now(timezone.utc)
        print(json.dumps({"ts_utc": ended.isoformat(), "runtime_sec": round((ended - started).total_seconds(), 3), **counts}, sort_keys=True))
        if args.command == "cycle":
            return 0
        time.sleep(max(1.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
