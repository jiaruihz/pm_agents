"""Observation source-event producer for latency research.

This keeps weather source first-seen/cadence facts in the data-feed service.
Strategy/research scripts can join these rows to market orderbooks without
owning weather-source polling themselves.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_data_feed import load_city_configs, parse_now_utc
from weather_data_feed.models import CityConfig
from weather_data_feed.observation_sources import (
    FetchSettings,
    expand_source_names,
    normalize_source_name,
    snapshot_observation_source,
    stable_hash,
)

from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import read_json, write_json, write_latest_and_daily_jsonl


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "source_events"


def requested_sources(cfg: CityConfig, source_names: list[str], *, include_fallback_sources: bool) -> list[str]:
    expanded = expand_source_names(
        source_names,
        primary=cfg.live_observation_source,
        fallback_sources=cfg.fallback_sources if include_fallback_sources else (),
    )
    out: list[str] = []
    for source in expanded:
        normalized = normalize_source_name(source)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def fetch_source_row(
    cfg: CityConfig,
    source_name: str,
    now_utc: datetime,
    *,
    settings: FetchSettings,
    recent_minutes: int,
) -> dict[str, Any]:
    try:
        row = snapshot_observation_source(
            cfg,
            source_name,
            now_utc,
            settings=settings,
            recent_minutes=recent_minutes,
        )
        row["producer"] = "weather_data_feed_service.source_events"
        return row
    except Exception as exc:  # noqa: BLE001
        local_date = now_utc.astimezone(ZoneInfo(cfg.timezone_name)).date().isoformat()
        row = {
            "producer": "weather_data_feed_service.source_events",
            "status": "fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "source": normalize_source_name(source_name),
            "station": cfg.official_icao,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "local_detect_ts_utc": datetime.now(timezone.utc).isoformat(),
            "city": cfg.city,
            "target_date": local_date,
            "unit": cfg.unit,
            "settlement_source_class": cfg.settlement_source_class,
            "settlement_source": cfg.settlement_source,
            "live_observation_source": cfg.live_observation_source,
            "mapping_rule": cfg.mapping_rule,
            "registry_class": cfg.registry_class,
        }
        row["payload_hash"] = stable_hash({k: row.get(k) for k in ("status", "error", "source", "station")})
        return row


def annotate_changed(rows: list[dict[str, Any]], state: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    changed = 0
    for row in rows:
        key = "|".join(
            str(row.get(part) or "")
            for part in ("city", "target_date", "source", "station")
        )
        payload_hash = str(row.get("payload_hash") or "")
        old_hash = state.get(key)
        is_changed = bool(payload_hash and old_hash != payload_hash)
        row["changed_since_last"] = is_changed
        if is_changed:
            changed += 1
        if payload_hash:
            state[key] = payload_hash
    return rows, changed


def build_events(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    configs = load_city_configs(
        include_station_diff=args.include_station_diff,
        only_cities=set(args.cities or []) or None,
    )
    settings = FetchSettings(
        timeout_sec=args.timeout_sec,
        proxy_candidates=(None,),
        user_agent="pm-agent-weather-data-feed-source-events/1.0",
    )
    jobs: list[tuple[CityConfig, str]] = []
    for cfg in configs:
        for source_name in requested_sources(
            cfg,
            args.sources,
            include_fallback_sources=args.include_fallback_sources,
        ):
            jobs.append((cfg, source_name))

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_source_row,
                cfg,
                source_name,
                now_utc,
                settings=settings,
                recent_minutes=args.recent_minutes,
            ): (cfg, source_name)
            for cfg, source_name in jobs
        }
        for future in as_completed(futures):
            rows.append(future.result())

    output_dir = Path(args.output_dir)
    state_path = Path(args.state_path) if args.state_path else output_dir / "state.json"
    state = read_json(state_path, {})
    rows, changed = annotate_changed(rows, state)
    rows = sorted(rows, key=lambda row: (str(row.get("city")), str(row.get("source")), str(row.get("station"))))
    write_json(state_path, state)

    summary = {
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": "weather_data_feed_service.source_events",
        "rows": len(rows),
        "changed": changed,
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "non_ok": sum(1 for row in rows if row.get("status") != "ok"),
        "cities": len(configs),
        "sources": args.sources,
        "output_dir": str(output_dir),
        "state_path": str(state_path),
    }
    payload = {**summary, "records": rows}
    return payload


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = list(payload.get("records") or [])
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=payload,
        rows=rows,
        jsonl_name="sources.jsonl",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build observation source-event rows for latency research.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--state-path", default="")
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--sources", nargs="*", default=["profile_primary"])
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-fallback-sources", action="store_true")
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--recent-minutes", type=int, default=240)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_events(args)
    write_outputs(payload, Path(args.output_dir))
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
