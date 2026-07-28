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
from weather_data_feed.information_events import build_information_event
from weather_data_feed.observation_sources import (
    FetchSettings,
    expand_source_names,
    normalize_source_name,
    snapshot_observation_source,
    stable_hash,
)

from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json, write_latest_and_daily_jsonl


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "source_events"
AWC_RECONCILE_SOURCES = {"aviationweather_metar"}
AWC_INDEX_STATE_KEY = "__awc_report_index_v1"
INFORMATION_EVENT_STATE_KEY = "__information_event_state_v1"

_DELIVERY_METADATA_FIELDS = {
    "producer",
    "status",
    "error",
    "ts_utc",
    "local_detect_ts_utc",
    "fetched_at_utc",
    "payload_hash",
    "changed_since_last",
    "first_seen_type",
    "original_first_seen_unknown",
    "recovered_from_multi_record_payload",
    "information_event_id",
    "event_kind",
    "event_role",
    "content_key",
    "revision_of_event_id",
    "detected_at_utc",
    "first_seen_at_utc",
    "available_at_utc",
    "pit_lineage_class",
    "raw_source_path",
    "raw_row_hash",
    "information_event_status",
}


def requested_sources(
    cfg: CityConfig,
    source_names: list[str],
    *,
    include_fallback_sources: bool,
    include_awc_cache_first_arrival: bool = False,
) -> list[str]:
    effective_source_names = list(source_names)
    if (
        include_fallback_sources
        and cfg.registry_class == "research_source_profile"
        and effective_source_names == ["profile_primary"]
    ):
        effective_source_names = ["source_profiles"]
    expanded = expand_source_names(
        effective_source_names,
        primary=cfg.live_observation_source,
        fallback_sources=cfg.fallback_sources if include_fallback_sources else (),
    )
    out: list[str] = []
    for source in expanded:
        normalized = normalize_source_name(source)
        if normalized and normalized not in out:
            out.append(normalized)
    if (
        include_awc_cache_first_arrival
        and (
            normalize_source_name(cfg.live_observation_source) == "aviationweather_metar"
            or "aviationweather_metar" in {normalize_source_name(source) for source in cfg.fallback_sources}
        )
        and "aviationweather_cache_csv" not in out
    ):
        out.append("aviationweather_cache_csv")
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
            include_record_rows=normalize_source_name(source_name) in AWC_RECONCILE_SOURCES,
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


def awc_report_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(part) or "")
        for part in ("city", "target_date", "source", "station", "source_report_ts_utc")
    )


def bootstrap_awc_report_index(path: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    if not path.exists():
        return index
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if normalize_source_name(row.get("source")) not in AWC_RECONCILE_SOURCES:
                continue
            if not row.get("source_report_ts_utc"):
                continue
            index[awc_report_key(row)] = str(row.get("payload_hash") or stable_hash(row))
    return index


def late_awc_backfills(
    latest_rows: list[dict[str, Any]],
    record_rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    journal_path: Path,
) -> list[dict[str, Any]]:
    if AWC_INDEX_STATE_KEY not in state:
        state[AWC_INDEX_STATE_KEY] = bootstrap_awc_report_index(journal_path)
    index = dict(state.get(AWC_INDEX_STATE_KEY) or {})
    latest_keys = {awc_report_key(row) for row in latest_rows if row.get("source_report_ts_utc")}
    backfills: list[dict[str, Any]] = []
    for raw_row in record_rows:
        row = dict(raw_row)
        row.pop("_record_rows", None)
        key = awc_report_key(row)
        payload_hash = str(row.get("payload_hash") or stable_hash(row))
        previous_hash = index.get(key)
        index[key] = payload_hash
        if key in latest_keys or previous_hash is not None:
            continue
        row.update(
            {
                "producer": "weather_data_feed_service.source_events",
                "first_seen_type": "late_backfill",
                "original_first_seen_unknown": True,
                "recovered_from_multi_record_payload": True,
                "changed_since_last": False,
            }
        )
        backfills.append(row)
    dates = sorted({key.split("|")[1] for key in index if len(key.split("|")) >= 2})
    keep_dates = set(dates[-3:])
    state[AWC_INDEX_STATE_KEY] = {
        key: value for key, value in index.items() if len(key.split("|")) >= 2 and key.split("|")[1] in keep_dates
    }
    return sorted(backfills, key=lambda row: (str(row.get("city")), str(row.get("source_report_ts_utc"))))


def _observation_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Keep provider content while excluding poll/publication metadata."""
    return {key: value for key, value in row.items() if key not in _DELIVERY_METADATA_FIELDS and not key.startswith("_")}


def _event_content_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(key) or "")
        for key in ("city", "source", "station", "source_report_ts_utc", "target_date")
    )


def annotate_information_events(
    rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    raw_source_path: str,
    available_at_utc: str,
) -> list[dict[str, Any]]:
    """Attach immutable event headers at the raw publication boundary.

    Fetch failures remain raw coverage records. They do not acquire an event ID
    and therefore cannot create a checkpoint or a candidate downstream.
    """
    event_state = state.setdefault(INFORMATION_EVENT_STATE_KEY, {})
    first_seen_by_id = dict(event_state.get("first_seen_by_id") or {})
    latest_by_content = dict(event_state.get("latest_by_content") or {})
    annotated: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        if row.get("status") != "ok":
            row["information_event_status"] = "not_material_fetch_failure"
            annotated.append(row)
            continue
        content_key = _event_content_key(row)
        detected_at = str(row.get("local_detect_ts_utc") or row.get("ts_utc") or available_at_utc)
        is_late = bool(row.get("original_first_seen_unknown")) or row.get("first_seen_type") == "late_backfill"
        provisional_payload = _observation_payload(row)
        # Build once to obtain the immutable ID. A changed source payload for
        # the same report/content key becomes a linked revision; an identical
        # post-restart poll retains the original first-seen value.
        provisional = build_information_event(
            event_kind="observation",
            event_role="new_content",
            source=str(row.get("source") or ""),
            city=str(row.get("city") or ""),
            station_id=str(row.get("station") or "") or None,
            provider_item_id=str(row.get("provider_item_id") or row.get("source_report_ts_utc") or "") or None,
            content_key=content_key,
            normalized_payload=provisional_payload,
            source_event_ts_utc=row.get("source_report_ts_utc"),
            detected_at_utc=detected_at,
            first_seen_at_utc=None if is_late else first_seen_by_id.get("pending") or detected_at,
            available_at_utc=available_at_utc,
            pit_lineage_class="late_backfill_first_seen_unknown" if is_late else "collector_exact",
            original_first_seen_unknown=is_late,
            raw_source_path=raw_source_path,
            raw_row_hash=str(row.get("payload_hash") or "") or None,
        )
        event_id = str(provisional["information_event_id"])
        first_seen = None if is_late else str(first_seen_by_id.get(event_id) or detected_at)
        previous_event_id = latest_by_content.get(content_key)
        event_role = "revision" if previous_event_id and previous_event_id != event_id else "new_content"
        event = build_information_event(
            event_kind="observation",
            event_role=event_role,
            source=str(row.get("source") or ""),
            city=str(row.get("city") or ""),
            station_id=str(row.get("station") or "") or None,
            provider_item_id=str(row.get("provider_item_id") or row.get("source_report_ts_utc") or "") or None,
            content_key=content_key,
            normalized_payload=provisional_payload,
            revision_of_event_id=previous_event_id if event_role == "revision" else None,
            source_event_ts_utc=row.get("source_report_ts_utc"),
            detected_at_utc=detected_at,
            first_seen_at_utc=first_seen,
            available_at_utc=available_at_utc,
            pit_lineage_class="late_backfill_first_seen_unknown" if is_late else "collector_exact",
            original_first_seen_unknown=is_late,
            raw_source_path=raw_source_path,
            raw_row_hash=str(row.get("payload_hash") or "") or None,
        )
        if not is_late:
            first_seen_by_id.setdefault(event_id, first_seen)
        latest_by_content[content_key] = event_id
        annotated.append({**row, **event, "information_event_status": "material"})
    event_state["first_seen_by_id"] = first_seen_by_id
    event_state["latest_by_content"] = latest_by_content
    return annotated


def build_events(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    configs = load_city_configs(
        include_station_diff=args.include_station_diff,
        only_cities=set(args.cities or []) or None,
        include_research_cities=args.include_research_cities,
        research_cities=set(args.research_cities or []) or None,
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
            include_awc_cache_first_arrival=args.include_awc_cache_first_arrival,
        ):
            jobs.append((cfg, source_name))

    rows: list[dict[str, Any]] = []
    record_rows: list[dict[str, Any]] = []
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
            row = future.result()
            record_rows.extend(row.pop("_record_rows", []) or [])
            rows.append(row)

    output_dir = Path(args.output_dir)
    state_path = Path(args.state_path) if args.state_path else output_dir / "state.json"
    state = read_json(state_path, {})
    backfill_rows = late_awc_backfills(
        rows,
        record_rows,
        state,
        journal_path=output_dir / "sources.jsonl",
    )
    rows, changed = annotate_changed(rows, state)
    rows = sorted(rows, key=lambda row: (str(row.get("city")), str(row.get("source")), str(row.get("station"))))
    summary = {
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "producer": "weather_data_feed_service.source_events",
        "rows": len(rows),
        "changed": changed,
        "late_backfill_rows": len(backfill_rows),
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "non_ok": sum(1 for row in rows if row.get("status") != "ok"),
        "cities": len(configs),
        "sources": args.sources,
        "include_awc_cache_first_arrival": args.include_awc_cache_first_arrival,
        "include_research_cities": args.include_research_cities,
        "research_cities": args.research_cities or [],
        "output_dir": str(output_dir),
        "state_path": str(state_path),
        "history_reconcile_only": bool(args.history_reconcile_only),
    }
    payload = {
        **summary,
        "records": [] if args.history_reconcile_only else rows,
        "append_records": backfill_rows,
        "_state": state,
        "_state_path": str(state_path),
    }
    return payload


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    rows = list(payload.get("records") or [])
    append_rows = list(payload.get("append_records") or [])
    state = dict(payload.get("_state") or {})
    available_at_utc = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    raw_source_path = str(output_dir / "sources.jsonl")
    rows = annotate_information_events(
        rows,
        state,
        raw_source_path=raw_source_path,
        available_at_utc=available_at_utc,
    )
    append_rows = annotate_information_events(
        append_rows,
        state,
        raw_source_path=raw_source_path,
        available_at_utc=available_at_utc,
    )
    latest_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"append_records", "_state", "_state_path"}
    }
    latest_payload["records"] = rows
    if payload.get("history_reconcile_only"):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        append_jsonl(output_dir / "sources.jsonl", append_rows)
        append_jsonl(output_dir / day / "sources.jsonl", append_rows)
        write_json(Path(str(payload["_state_path"])), state)
        return
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=latest_payload,
        rows=rows + append_rows,
        jsonl_name="sources.jsonl",
    )
    write_json(Path(str(payload["_state_path"])), state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build observation source-event rows for latency research.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--state-path", default="")
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--sources", nargs="*", default=["profile_primary"])
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument("--include-fallback-sources", action="store_true")
    parser.add_argument("--include-awc-cache-first-arrival", action="store_true")
    parser.add_argument("--include-research-cities", action="store_true")
    parser.add_argument("--research-cities", nargs="*", default=None)
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--recent-minutes", type=int, default=240)
    parser.add_argument("--history-reconcile-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_events(args)
    write_outputs(payload, Path(args.output_dir))
    print(
        json.dumps(
            {k: v for k, v in payload.items() if k not in {"records", "append_records"}},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
