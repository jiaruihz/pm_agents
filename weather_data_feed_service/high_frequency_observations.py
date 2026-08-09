"""High-frequency airport/reference observation producer for research capture."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_data_feed import parse_now_utc
from weather_data_feed.information_events import (
    build_information_event,
    normalized_observation_payload,
)
from weather_data_feed.high_frequency_observation_sources import (
    HighFrequencyFetchResult,
    HighFrequencyFetchSettings,
    fetch_high_frequency_observation,
    stable_hash,
    supported_high_frequency_sources,
)
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import write_latest_and_daily_jsonl
from weather_data_feed_service.scheduling import ActiveLocalWindow, filter_jobs_by_local_window, filter_jobs_by_min_interval


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "high_frequency_observations"
DEFAULT_DIRECT_SOURCES = [
    "amos_runway",
    "noaa_madis_hfmetar",
    "singapore_mss",
    "jma_amedas",
    "hko_obs",
    "cowin_obs",
    "fmi",
    "knmi",
    "mgm",
    "ims_lod",
    "bom_aws",
]
DEFAULT_SOURCE_MIN_INTERVAL_SEC = {
    "jma_amedas": 300.0,
    "noaa_madis_hfmetar": 300.0,
    "fmi": 300.0,
    "knmi": 300.0,
    "mgm": 300.0,
    "ims_lod": 300.0,
    "bom_aws": 300.0,
}
INFORMATION_EVENT_STATE_KEY = "__information_event_state_v1"
PRODUCER_SCHEMA_VERSION = "weather_high_frequency_observations_payload_v2"
PRODUCER_SCHEMA = {
    "schema_version": PRODUCER_SCHEMA_VERSION,
    "schema_fingerprint": "sha256",
    "producer_identity": {
        "runtime_instance_id": "sha256",
        "repo_head": "git_sha",
        "repo_dirty_tracked": "bool",
        "config_sha256": "sha256",
        "loaded_module_sha256": "mapping[path,sha256]",
    },
    "records": "observation_rows_with_producer_contract",
}
PRODUCER_SCHEMA_FINGERPRINT = hashlib.sha256(
    json.dumps(PRODUCER_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _producer_config(args: argparse.Namespace) -> dict[str, Any]:
    override = getattr(args, "_producer_config_payload", None)
    if override is not None:
        return dict(override)
    ignored = {"now_utc", "_producer_entrypoint_path", "_producer_config_payload"}
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
        if key not in ignored
    }


def build_producer_identity(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    module_names = {
        __name__,
        "weather_data_feed.high_frequency_observation_sources",
        "weather_data_feed.information_events",
    }
    module_hashes: dict[str, str] = {}
    for module_name in sorted(module_names):
        spec = importlib.util.find_spec(module_name)
        if spec is None or not spec.origin:
            raise RuntimeError(f"cannot resolve producer module: {module_name}")
        path = Path(spec.origin).resolve()
        module_hashes[str(path)] = _sha256_file(path)
    entrypoint = Path(
        getattr(args, "_producer_entrypoint_path", None) or __file__
    ).resolve()
    module_hashes[str(entrypoint)] = _sha256_file(entrypoint)
    config_hash = hashlib.sha256(
        json.dumps(_producer_config(args), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    git = lambda *parts: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo_root), *parts],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    identity = {
        "repo_root": str(repo_root),
        "repo_head": git("rev-parse", "HEAD"),
        "repo_dirty_tracked": bool(git("status", "--short", "--untracked-files=no")),
        "config_sha256": config_hash,
        "loaded_module_sha256": module_hashes,
        "output_schema_version": PRODUCER_SCHEMA_VERSION,
        "output_schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
    }
    return {
        **identity,
        "runtime_instance_id": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _parse_minute_window(raw: str) -> tuple[float, float] | None:
    if "-" not in raw:
        return None
    start_raw, end_raw = raw.split("-", 1)
    try:
        start = float(start_raw)
        end = float(end_raw)
    except ValueError:
        return None
    if start < 0 or end < 0 or start >= 60 or end >= 60 or end < start:
        return None
    return start, end


def _failed_result(source: str, city: str, exc: BaseException) -> HighFrequencyFetchResult:
    now = datetime.now(timezone.utc).isoformat()
    return HighFrequencyFetchResult(
        source_key=source,
        city=city,
        status="fetch_failed",
        fetched_at_utc=now,
        latency_ms=0.0,
        error=f"{type(exc).__name__}: {exc}",
        metadata={"city": city},
    )


def _jobs(source_names: list[str], city_names: list[str] | None, now_utc: datetime, args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    registry = supported_high_frequency_sources()
    selected_sources = source_names or list(DEFAULT_DIRECT_SOURCES)
    selected_cities = set(city_names or [])
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for source in selected_sources:
        for city, meta in registry.get(source, {}).items():
            if selected_cities and city not in selected_cities:
                continue
            candidates.append((source, city, meta))
    always_active_cities = set(args.always_active_cities or [])
    always_active = [(source, city) for source, city, _meta in candidates if city in always_active_cities]
    window_candidates = [row for row in candidates if row[1] not in always_active_cities]
    window_active, skipped = filter_jobs_by_local_window(
        window_candidates,
        now_utc=now_utc,
        window=ActiveLocalWindow(args.active_local_start_hour, args.active_local_end_hour),
    )
    return sorted(set(always_active + window_active)), skipped


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_source_min_intervals(raw_items: list[str] | None) -> dict[str, float]:
    out = dict(DEFAULT_SOURCE_MIN_INTERVAL_SEC)
    for raw in raw_items or []:
        if "=" not in str(raw):
            continue
        source, value = str(raw).split("=", 1)
        try:
            out[source.strip()] = max(0.0, float(value))
        except ValueError:
            continue
    return out


def _parse_source_minute_window_intervals(raw_items: list[str] | None) -> dict[str, list[dict[str, Any]]]:
    """Parse source=minute_start-minute_end,...:interval_sec rules."""
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_items or []:
        item = str(raw).strip()
        if not item or "=" not in item or ":" not in item:
            continue
        source, rest = item.split("=", 1)
        windows_raw, interval_raw = rest.rsplit(":", 1)
        source = source.strip()
        try:
            interval_sec = max(0.0, float(interval_raw))
        except ValueError:
            continue
        windows = []
        for window_raw in windows_raw.split(","):
            parsed = _parse_minute_window(window_raw.strip())
            if parsed is None:
                continue
            windows.append(parsed)
        if not source or not windows:
            continue
        out.setdefault(source, []).append({"windows": windows, "interval_sec": interval_sec})
    return out


def _apply_active_minute_window_intervals(
    base_intervals: dict[str, float],
    rules: dict[str, list[dict[str, Any]]],
    *,
    now_utc: datetime,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    intervals = dict(base_intervals)
    minute_value = now_utc.minute + now_utc.second / 60.0
    active: list[dict[str, Any]] = []
    for source, source_rules in rules.items():
        for rule in source_rules:
            interval_sec = float(rule.get("interval_sec") or 0.0)
            for start, end in rule.get("windows") or []:
                if start <= minute_value <= end:
                    previous = float(intervals.get(source, 0.0) or 0.0)
                    intervals[source] = interval_sec if previous <= 0 else min(previous, interval_sec)
                    active.append(
                        {
                            "source": source,
                            "minute_utc": round(minute_value, 3),
                            "window_start_minute": start,
                            "window_end_minute": end,
                            "base_interval_sec": previous,
                            "active_interval_sec": intervals[source],
                        }
                    )
                    break
    return intervals, active


def _previous_records_for_jobs(output_dir: Path, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = {(str(row.get("source") or ""), str(row.get("city") or "")) for row in jobs}
    if not wanted:
        return []
    payload = _read_json(output_dir / "latest.json")
    records = []
    for row in payload.get("records") or []:
        key = (str(row.get("source") or ""), str(row.get("city") or ""))
        if key in wanted:
            records.append({**row, "source_status": row.get("source_status") or "cadence_preserved"})
    return records


def _fetch_job(source: str, city: str, now_utc: datetime, settings: HighFrequencyFetchSettings) -> HighFrequencyFetchResult:
    try:
        return fetch_high_frequency_observation(source, city, settings=settings, now_utc=now_utc)
    except Exception as exc:  # noqa: BLE001
        return _failed_result(source, city, exc)


def _row_with_hash(row: dict[str, Any], result: HighFrequencyFetchResult) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("source", result.source_key)
    out.setdefault("source_status", result.status)
    out.setdefault("fetched_at_utc", result.fetched_at_utc)
    out.setdefault("source_fetch_latency_sec", round(result.latency_ms / 1000.0, 3))
    out["payload_hash"] = out.get("payload_hash") or stable_hash(
        {
            "source": out.get("source"),
            "city": out.get("city"),
            "station": out.get("station"),
            "observation_time_utc": out.get("observation_time_utc"),
            "temp_c": out.get("temp_c"),
            "raw_payload_hash": out.get("raw_payload_hash"),
        }
    )
    return out


def annotate_information_events(
    rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    raw_source_path: str,
    available_at_utc: str,
) -> list[dict[str, Any]]:
    """Attach immutable first-seen headers to high-frequency observations."""

    event_state = state.setdefault(INFORMATION_EVENT_STATE_KEY, {})
    first_seen_by_id = dict(event_state.get("first_seen_by_id") or {})
    latest_by_content = dict(event_state.get("latest_by_content") or {})
    annotated: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        if row.get("source_status") not in {"ok", "cadence_preserved"}:
            row["information_event_status"] = "not_material_fetch_failure"
            annotated.append(row)
            continue
        city = str(row.get("city") or "")
        station = str(row.get("station") or "")
        observation_time = str(row.get("observation_time_utc") or "")
        if not city or not station or not observation_time:
            row["information_event_status"] = "not_material_missing_identity"
            annotated.append(row)
            continue
        content_key = "|".join((city, station, observation_time))
        exact_first_seen = str(row.get("source_first_seen_at_utc") or "")
        is_late = not bool(exact_first_seen)
        detected_at = exact_first_seen or str(
            row.get("local_detect_ts_utc")
            or row.get("fetched_at_utc")
            or available_at_utc
        )
        normalized_payload = normalized_observation_payload(row)
        provisional = build_information_event(
            event_kind="observation",
            event_role="new_content",
            source=str(row.get("source") or ""),
            city=city,
            station_id=station,
            provider_item_id=observation_time,
            content_key=content_key,
            normalized_payload=normalized_payload,
            source_event_ts_utc=observation_time,
            detected_at_utc=detected_at,
            first_seen_at_utc=None if is_late else exact_first_seen,
            available_at_utc=available_at_utc,
            pit_lineage_class=(
                "late_backfill_first_seen_unknown" if is_late else "collector_exact"
            ),
            original_first_seen_unknown=is_late,
            raw_source_path=raw_source_path,
            raw_row_hash=str(row.get("payload_hash") or "") or None,
        )
        event_id = str(provisional["information_event_id"])
        first_seen = (
            None
            if is_late
            else str(first_seen_by_id.get(event_id) or exact_first_seen)
        )
        previous_event_id = latest_by_content.get(content_key)
        event_role = (
            "revision"
            if previous_event_id and previous_event_id != event_id
            else "new_content"
        )
        material = previous_event_id != event_id
        event = build_information_event(
            event_kind="observation",
            event_role=event_role,
            source=str(row.get("source") or ""),
            city=city,
            station_id=station,
            provider_item_id=observation_time,
            content_key=content_key,
            normalized_payload=normalized_payload,
            revision_of_event_id=(
                previous_event_id if event_role == "revision" else None
            ),
            source_event_ts_utc=observation_time,
            detected_at_utc=detected_at,
            first_seen_at_utc=first_seen,
            available_at_utc=available_at_utc,
            pit_lineage_class=(
                "late_backfill_first_seen_unknown" if is_late else "collector_exact"
            ),
            original_first_seen_unknown=is_late,
            material_state_change=material,
            raw_source_path=raw_source_path,
            raw_row_hash=str(row.get("payload_hash") or "") or None,
        )
        if not is_late:
            first_seen_by_id.setdefault(event_id, first_seen)
        latest_by_content[content_key] = event_id
        annotated.append(
            {
                **row,
                **event,
                "information_event_status": "material" if material else "duplicate",
            }
        )
    event_state["first_seen_by_id"] = first_seen_by_id
    event_state["latest_by_content"] = latest_by_content
    return annotated


def append_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("source") or ""),
            str(row.get("city") or ""),
            str(row.get("station") or ""),
            str(row.get("icao") or ""),
            str(row.get("runway") or ""),
        )
        old = latest_by_key.get(key)
        if old is None or str(row.get("observation_time_utc") or "") >= str(old.get("observation_time_utc") or ""):
            latest_by_key[key] = row
    return sorted(
        latest_by_key.values(),
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("source") or ""),
            str(row.get("station") or ""),
            str(row.get("observation_time_utc") or ""),
        ),
    )


def observation_identity(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("source") or ""),
        str(row.get("city") or ""),
        str(row.get("station") or ""),
        str(row.get("icao") or ""),
        str(row.get("runway") or ""),
        str(row.get("observation_time_utc") or row.get("source_obs_ts_utc") or ""),
        str(row.get("temp_c") or ""),
    )


def new_observation_rows(
    previous_rows: list[dict[str, Any]], fetched_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    previous = {observation_identity(row) for row in previous_rows}
    return append_history_rows(
        [row for row in fetched_rows if observation_identity(row) not in previous]
    )


def preserve_observation_first_seen(
    previous_rows: list[dict[str, Any]],
    fetched_rows: list[dict[str, Any]],
    *,
    published_at_utc: str,
) -> list[dict[str, Any]]:
    previous = {observation_identity(row): row for row in previous_rows}
    enriched = []
    for row in fetched_rows:
        prior = previous.get(observation_identity(row)) or {}
        first_seen = (
            prior.get("source_first_seen_at_utc")
            or prior.get("local_detect_ts_utc")
            or prior.get("fetched_at_utc")
            or row.get("local_detect_ts_utc")
            or row.get("fetched_at_utc")
            or published_at_utc
        )
        enriched.append({
            **row,
            "local_detect_ts_utc": first_seen,
            "source_first_seen_at_utc": first_seen,
            "source_published_at_utc": prior.get("source_published_at_utc") or published_at_utc,
        })
    return enriched


def write_new_observation_notification(path: Path, payload: dict[str, Any]) -> bool:
    rows = list(payload.get("new_observation_records") or [])
    if not rows:
        return False
    _write_json(path, {
        "schema_version": "weather_high_frequency_observation_notification_v1",
        "generated_at_utc": payload.get("generated_at_utc"),
        "producer_contract": {
            "schema_version": payload.get("schema_version"),
            "schema_fingerprint": payload.get("schema_fingerprint"),
            "runtime_identity": payload.get("producer_identity"),
        },
        "records": rows,
    })
    return True


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    now_utc = parse_now_utc(args.now_utc) if args.now_utc else datetime.now(timezone.utc)
    settings = HighFrequencyFetchSettings(timeout_sec=args.timeout_sec)
    active_jobs, skipped_jobs = _jobs(args.sources or [], args.cities, now_utc, args)
    state_path = Path(args.state_path) if args.state_path else Path(args.output_dir) / "state.json"
    state = _read_json(state_path)
    source_min_intervals = _parse_source_min_intervals(args.source_min_interval_sec)
    source_minute_window_intervals = _parse_source_minute_window_intervals(args.source_minute_window_min_interval_sec)
    effective_source_min_intervals, active_minute_window_overrides = _apply_active_minute_window_intervals(
        source_min_intervals,
        source_minute_window_intervals,
        now_utc=now_utc,
    )
    jobs, cadence_skipped_jobs = filter_jobs_by_min_interval(
        active_jobs,
        now_utc=now_utc,
        min_interval_by_source=effective_source_min_intervals,
        last_attempt_by_job=dict(state.get("last_attempt_by_job") or {}),
    )
    results: list[HighFrequencyFetchResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(_fetch_job, source, city, now_utc, settings): (source, city)
            for source, city in jobs
        }
        for future in as_completed(futures):
            results.append(future.result())

    published_at_utc = datetime.now(timezone.utc).isoformat()
    fetched_rows: list[dict[str, Any]] = []
    for result in results:
        fetched_rows.extend(
            {**_row_with_hash(row, result), "source_published_at_utc": published_at_utc}
            for row in result.records
        )
    previous_rows = list(
        (_read_json(Path(args.output_dir) / "latest.json").get("records") or [])
    )
    fetched_rows = preserve_observation_first_seen(
        previous_rows, fetched_rows, published_at_utc=published_at_utc
    )
    new_rows = new_observation_rows(previous_rows, fetched_rows)
    rows = list(fetched_rows)
    preserved_rows = _previous_records_for_jobs(Path(args.output_dir), cadence_skipped_jobs)
    rows.extend(preserved_rows)
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("city") or ""),
            str(row.get("source") or ""),
            str(row.get("station") or ""),
            str(row.get("observation_time_utc") or ""),
        ),
    )
    generated_at = published_at_utc
    partition_only = bool(getattr(args, "partition_only", False))
    raw_journal = Path(args.output_dir) / "high_frequency_observations.jsonl"
    if partition_only:
        raw_journal = (
            Path(args.output_dir)
            / generated_at[:10]
            / "high_frequency_observations.jsonl"
        )
    rows = annotate_information_events(
        rows,
        state,
        raw_source_path=str(raw_journal),
        available_at_utc=generated_at,
    )
    source_statuses = {f"{result.source_key}:{result.city}": result.status for result in results}
    source_errors = {f"{result.source_key}:{result.city}": result.error for result in results if result.error}
    producer_identity = build_producer_identity(args)
    producer_contract = {
        "schema_version": PRODUCER_SCHEMA_VERSION,
        "schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
        "runtime_identity": producer_identity,
    }
    rows = [{**row, "producer_contract": producer_contract} for row in rows]
    new_ids = {observation_identity(row) for row in new_rows}
    new_rows = [row for row in rows if observation_identity(row) in new_ids]
    summary = {
        "status": "ok",
        "schema_version": PRODUCER_SCHEMA_VERSION,
        "schema_fingerprint": PRODUCER_SCHEMA_FINGERPRINT,
        "producer_identity": producer_identity,
        "generated_at_utc": generated_at,
        "producer": "weather_data_feed_service.high_frequency_observations",
        "rows": len(rows),
        "ok_sources": sum(1 for result in results if result.status == "ok"),
        "empty_sources": sum(1 for result in results if result.status == "empty"),
        "non_ok_sources": sum(1 for result in results if result.status not in {"ok", "empty"}),
        "sources": args.sources or list(DEFAULT_DIRECT_SOURCES),
        "cities": sorted({str(row.get("city") or "") for row in rows if row.get("city")}),
        "active_job_cities": sorted({city for _source, city in active_jobs}),
        "fetch_attempt_cities": sorted({city for _source, city in jobs}),
        "active_local_window": ActiveLocalWindow(args.active_local_start_hour, args.active_local_end_hour).as_payload(),
        "always_active_cities": sorted(set(args.always_active_cities or [])),
        "skipped_inactive_jobs": skipped_jobs,
        "skipped_inactive_count": len(skipped_jobs),
        "source_min_interval_sec": source_min_intervals,
        "effective_source_min_interval_sec": effective_source_min_intervals,
        "source_minute_window_min_interval_sec": source_minute_window_intervals,
        "active_minute_window_overrides": active_minute_window_overrides,
        "skipped_cadence_jobs": cadence_skipped_jobs,
        "skipped_cadence_count": len(cadence_skipped_jobs),
        "cadence_preserved_rows": len(preserved_rows),
        "state_path": str(state_path),
        "source_statuses": source_statuses,
        "source_errors": source_errors,
        "new_observation_count": len(new_rows),
        "new_observation_records": new_rows,
    }
    return {
        **summary,
        "records": rows,
        "_information_event_state": state.get(INFORMATION_EVENT_STATE_KEY, {}),
    }


def update_state(payload: dict[str, Any], state_path: Path) -> None:
    now = str(payload.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
    state = _read_json(state_path)
    last = dict(state.get("last_attempt_by_job") or {})
    for key in payload.get("source_statuses") or {}:
        last[str(key)] = now
    _write_json(
        state_path,
        {
            "schema_version": "weather_high_frequency_observations_state_v2",
            "updated_at_utc": now,
            "last_attempt_by_job": last,
            "producer_contract": {
                "schema_version": payload.get("schema_version"),
                "schema_fingerprint": payload.get("schema_fingerprint"),
                "runtime_identity": payload.get("producer_identity"),
            },
            INFORMATION_EVENT_STATE_KEY: dict(
                payload.get("_information_event_state") or {}
            ),
        },
    )


def write_outputs(
    payload: dict[str, Any],
    output_dir: Path,
    *,
    write_aggregate: bool = True,
) -> None:
    append_rows = append_history_rows(list(payload.get("new_observation_records") or []))
    latest_payload = {
        key: value
        for key, value in payload.items()
        if not str(key).startswith("_")
    }
    latest_payload["append_rows"] = len(append_rows)
    write_latest_and_daily_jsonl(
        output_dir=output_dir,
        latest_payload=latest_payload,
        rows=append_rows,
        jsonl_name="high_frequency_observations.jsonl",
        day=str(payload.get("generated_at_utc") or "")[:10] or None,
        write_aggregate=write_aggregate,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build airport/reference high-frequency observation rows.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--sources", nargs="*", default=None)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--active-local-start-hour", type=float, default=None)
    parser.add_argument("--active-local-end-hour", type=float, default=None)
    parser.add_argument("--always-active-cities", nargs="*", default=[])
    parser.add_argument("--source-min-interval-sec", action="append", default=[])
    parser.add_argument("--source-minute-window-min-interval-sec", action="append", default=[])
    parser.add_argument("--state-path", default="")
    parser.add_argument("--notify-path", default="")
    parser.add_argument(
        "--partition-only",
        action="store_true",
        help="Write the dated shard and latest.json without a root aggregate journal.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(args)
    write_outputs(
        payload,
        Path(args.output_dir),
        write_aggregate=not bool(args.partition_only),
    )
    if args.notify_path:
        write_new_observation_notification(Path(args.notify_path), payload)
    update_state(payload, Path(args.state_path) if args.state_path else Path(args.output_dir) / "state.json")
    print(json.dumps({k: v for k, v in payload.items() if k != "records"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
