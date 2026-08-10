#!/usr/bin/env python3
"""Collect unified Seoul/Busan AMOS first-seen states and active ladder books.

This process is zero-notional telemetry. It never creates orders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.weather_fast_source_stale_book_observer import (  # noqa: E402
    augment_market_index_from_gamma,
    build_market_index,
    fetch_fresh_book,
    latest_paper_snapshot,
    market_date_has_tokens,
    relative_market_token,
    temperature_event_slug,
)
from weather_data_feed.korea_amos_features import (  # noqa: E402
    aggregate_amos_observation,
    parse_utc,
    rolling_path_features,
)
from weather_data_feed.physical_features import forecast_window_features  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


PRODUCTION_SPEC = load_production_spec()
RUNTIME_ROOT = PRODUCTION_SPEC.data_feed_runtime_root
DEFAULT_SOURCE_JSONL = PRODUCTION_SPEC.live_cross_observations_root()
DEFAULT_FORECAST_ROOT = PRODUCTION_SPEC.forecast_hourly_curve_dir()
DEFAULT_OUTPUT_DIR = RUNTIME_ROOT / "output/korea_first_seen_state_v1"
DEFAULT_CONFIG = ROOT / "configs/weather/korea_first_seen_collector_v1.json"
SEOUL_TZ = ZoneInfo("Asia/Seoul")
STATE_SCHEMA_VERSION = "korea_first_seen_collector_state_v1"
OUTPUT_SCHEMA_VERSION = "korea_first_seen_research_checkpoint_v1"
_FORECAST_INDEX_CACHE: dict[
    tuple[str, str, str], list[tuple[datetime, dict[str, Any]]]
] = {}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def append_jsonl_batch(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def load_config(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    required = {
        "cities",
        "source",
        "path_window_minutes",
        "market_capture",
        "mode",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"Korea collector config missing fields: {', '.join(missing)}")
    if payload.get("source") != "amos_runway":
        raise ValueError("Korea collector v1 only supports source=amos_runway")
    return payload


def _empty_cursor(path: Path) -> dict[str, Any]:
    return {
        "source_path": str(path),
        "file_identity": None,
        "byte_offset": 0,
    }


def read_appended_rows(
    path: Path,
    cursor: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    configured_path = path
    if path.is_dir():
        candidates = sorted(
            path.glob("????-??-??/high_frequency_observations.jsonl")
        )
        if not candidates:
            return [], _empty_cursor(path), {
                "status": "source_missing",
                "path": str(path),
                "lines_read": 0,
            }
        path = candidates[-1]
    try:
        stat = path.stat()
    except FileNotFoundError:
        return [], _empty_cursor(path), {
            "status": "source_missing",
            "path": str(path),
            "lines_read": 0,
        }
    identity = [int(stat.st_dev), int(stat.st_ino)]
    current = dict(cursor or {})
    reset_reason = ""
    offset = int(current.get("byte_offset") or 0)
    if str(current.get("source_path") or "") != str(path):
        reset_reason = "source_path_changed"
    elif current.get("file_identity") != identity:
        reset_reason = "file_rotated"
    elif stat.st_size < offset:
        reset_reason = "file_truncated"
    if reset_reason:
        current = _empty_cursor(path)
        offset = 0

    rows: list[dict[str, Any]] = []
    start_offset = offset
    lines_read = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                offset = line_start
                break
            offset = handle.tell()
            lines_read += 1
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    cursor_out = {
        "source_path": str(path),
        "file_identity": identity,
        "byte_offset": offset,
    }
    return rows, cursor_out, {
        "status": "ok",
        "configured_path": str(configured_path),
        "physical_path": str(path),
        "reset_reason": reset_reason,
        "lines_read": lines_read,
        "bytes_read": max(0, offset - start_offset),
        "file_size": int(stat.st_size),
    }


def distinct_amos_groups(
    rows: list[dict[str, Any]],
    *,
    cities: set[str],
    source: str,
    seen_event_keys: set[str],
) -> list[list[dict[str, Any]]]:
    first_by_runway: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        city = str(row.get("city") or "")
        if city not in cities or str(row.get("source") or "") != source:
            continue
        target_date = str(row.get("target_date") or "")
        observation_ts = str(row.get("observation_time_utc") or "")
        runway = str(row.get("runway") or "")
        if not target_date or not observation_ts:
            continue
        key = (city, target_date, source, observation_ts, runway)
        current = first_by_runway.get(key)
        current_seen = str(
            current.get("source_first_seen_at_utc")
            or current.get("local_detect_ts_utc")
            or current.get("fetched_at_utc")
            or ""
        ) if current else ""
        candidate_seen = str(
            row.get("source_first_seen_at_utc")
            or row.get("local_detect_ts_utc")
            or row.get("fetched_at_utc")
            or ""
        )
        if current is None or candidate_seen < current_seen:
            first_by_runway[key] = row

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for (city, target_date, row_source, observation_ts, _runway), row in first_by_runway.items():
        event_key = "|".join((city, target_date, row_source, observation_ts))
        if event_key not in seen_event_keys:
            grouped[(city, target_date, row_source, observation_ts)].append(row)
    return sorted(
        grouped.values(),
        key=lambda group: (
            str(group[0].get("source_first_seen_at_utc") or group[0].get("local_detect_ts_utc") or ""),
            str(group[0].get("city") or ""),
        ),
    )


def latest_forecast_asof(
    root: Path,
    *,
    city: str,
    target_date: str,
    as_of: datetime,
) -> dict[str, Any] | None:
    cache_key = (str(root), city, target_date)
    indexed = _FORECAST_INDEX_CACHE.get(cache_key)
    if indexed is None:
        local_date = as_of.astimezone(SEOUL_TZ).date()
        capture_dates = {
            (local_date - timedelta(days=offset)).isoformat()
            for offset in range(0, 2)
        }
        paths: list[Path] = []
        for capture_date in capture_dates:
            paths.extend(
                (root / capture_date).glob("forecast_hourly_curves_*.jsonl")
            )
        by_identity: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
        newest_paths = sorted(
            paths,
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )[:12]
        for path in newest_paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("city") != city or row.get("target_date") != target_date:
                    continue
                available = parse_utc(
                    row.get("available_at_utc")
                    or row.get("forecast_first_seen_utc")
                    or row.get("snapshot_ts_utc")
                )
                if available is None:
                    continue
                identity = (
                    str(row.get("forecast_values_hash") or ""),
                    available.isoformat(),
                )
                by_identity[identity] = (available, row)
        indexed = sorted(by_identity.values(), key=lambda item: item[0])
        _FORECAST_INDEX_CACHE[cache_key] = indexed
    eligible = [item for item in indexed if item[0] <= as_of]
    return eligible[-1][1] if eligible else None


def forecast_context(
    root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    as_of = parse_utc(state.get("source_first_seen_ts_utc"))
    if as_of is None:
        return {"status": "missing_source_first_seen"}
    age_hours = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600.0
    if age_hours > 12.0:
        return {
            "status": "historical_forecast_asof_deferred",
            "reason": "immutable forecast curves remain joinable by first_seen in offline materialization",
        }
    row = latest_forecast_asof(
        root,
        city=str(state["city"]),
        target_date=str(state["target_date"]),
        as_of=as_of,
    )
    if row is None:
        return {"status": "missing_pit_forecast"}
    local = as_of.astimezone(SEOUL_TZ)
    feature_input = {
        **row,
        "decision_hour_local": local.hour + local.minute / 60.0,
        "target_date": state["target_date"],
        "current_temp_c": state.get("source_temp_c"),
        "running_max_c": state.get("source_running_max_c"),
    }
    derived = forecast_window_features(feature_input)
    return {
        "status": "ok",
        "forecast_source": row.get("forecast_source"),
        "forecast_model": row.get("forecast_model"),
        "forecast_values_hash": row.get("forecast_values_hash"),
        "forecast_first_seen_utc": row.get("forecast_first_seen_utc"),
        "forecast_available_at_utc": row.get("available_at_utc"),
        "forecast_peak_hour_local": row.get("forecast_peak_hour_local"),
        "forecast_peak_time_local": row.get("forecast_peak_time_local"),
        "forecast_max_f": row.get("forecast_max_f"),
        **derived,
    }


def _book_row(
    token: Any,
    outcome: str,
    *,
    proxy: str,
    top_n: int,
) -> dict[str, Any]:
    token_id = token.yes_token_id if outcome == "yes" else token.no_token_id
    fetched = fetch_fresh_book(token_id, proxy=proxy, top_n=top_n)
    return {
        "relative_offset": None,
        "bracket": token.bracket,
        "question": token.question,
        "market_id": token.market_id,
        "condition_id": token.condition_id,
        "outcome": outcome,
        "token_id": token_id,
        "status": fetched.get("status"),
        "fetched_at_utc": fetched.get("fetched_at_utc"),
        "http_status": fetched.get("http_status"),
        "error": fetched.get("error", ""),
        "proxy_used": fetched.get("proxy_used", ""),
        "summary": fetched.get("summary") or {},
        "raw": fetched.get("raw") or {},
    }


def capture_market(
    state: dict[str, Any],
    *,
    config: dict[str, Any],
    market_proxy: str,
    last_capture_by_city: dict[str, str],
) -> dict[str, Any]:
    market_cfg = dict(config.get("market_capture") or {})
    if not market_cfg.get("enabled"):
        return {"status": "disabled"}
    first_seen = parse_utc(state.get("source_first_seen_ts_utc"))
    if first_seen is None:
        return {"status": "missing_source_first_seen"}
    now = datetime.now(timezone.utc)
    age_sec = (now - first_seen).total_seconds()
    if age_sec > float(market_cfg.get("fresh_event_max_age_seconds") or 180):
        return {
            "status": "historical_event_no_live_quote",
            "event_age_seconds": round(age_sec, 3),
        }
    local = first_seen.astimezone(SEOUL_TZ)
    start_hour = int(market_cfg.get("local_window_start_hour") or 0)
    end_hour = int(market_cfg.get("local_window_end_hour") or 24)
    if not start_hour <= local.hour < end_hour:
        return {"status": "outside_market_capture_window", "local_hour": local.hour}
    last_capture = parse_utc(last_capture_by_city.get(str(state["city"])))
    min_interval = float(
        market_cfg.get("minimum_interval_seconds_per_city") or 0
    )
    if last_capture and (now - last_capture).total_seconds() < min_interval:
        return {"status": "rate_limited", "last_capture_at_utc": last_capture.isoformat()}

    target_date = str(state["target_date"])
    city = str(state["city"])
    paper_path = latest_paper_snapshot()
    index = build_market_index(
        paper_path,
        target_dates={target_date},
        extreme_kind="max",
    )
    if not market_date_has_tokens(index, city, target_date):
        index = augment_market_index_from_gamma(
            index,
            target_dates={target_date},
            cities={city},
            event_slugs={
                city: temperature_event_slug(city, target_date, "max")
            },
            market_proxy=market_proxy,
        )
    reference = state.get("routine_running_max_market_value")
    if reference is None:
        reference = round(float(state["source_running_max_c"]))
    offsets = [int(value) for value in market_cfg.get("relative_bracket_offsets") or []]
    outcomes = [
        str(value).lower() for value in market_cfg.get("outcomes") or []
        if str(value).lower() in {"yes", "no"}
    ]
    jobs: list[tuple[int, Any, str]] = []
    seen_token_outcomes: set[tuple[str, str]] = set()
    for offset in offsets:
        token = relative_market_token(index, city, target_date, int(reference), offset)
        if token is None:
            continue
        for outcome in outcomes:
            token_id = token.yes_token_id if outcome == "yes" else token.no_token_id
            key = (token_id, outcome)
            if not token_id or key in seen_token_outcomes:
                continue
            seen_token_outcomes.add(key)
            jobs.append((offset, token, outcome))

    books: list[dict[str, Any]] = []
    top_n = int(market_cfg.get("book_depth_levels") or 20)
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as executor:
        futures = {
            executor.submit(
                _book_row,
                token,
                outcome,
                proxy=market_proxy,
                top_n=top_n,
            ): (offset, token, outcome)
            for offset, token, outcome in jobs
        }
        for future in as_completed(futures):
            offset, _token, _outcome = futures[future]
            row = future.result()
            row["relative_offset"] = offset
            books.append(row)
    captured_at = iso_now()
    last_capture_by_city[city] = captured_at
    return {
        "status": "ok" if books else "missing_market_tokens",
        "captured_at_utc": captured_at,
        "reference_market_value": int(reference),
        "relative_bracket_offsets": offsets,
        "outcomes": outcomes,
        "markout_horizon_seconds": market_cfg.get("markout_horizon_seconds") or [],
        "paper_snapshot_path": str(paper_path) if paper_path else "",
        "books": sorted(
            books,
            key=lambda row: (int(row["relative_offset"]), str(row["outcome"])),
        ),
    }


def compact_history_row(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": state.get("city"),
        "target_date": state.get("target_date"),
        "source_event_key": state.get("source_event_key"),
        "source_observation_ts_utc": state.get("source_observation_ts_utc"),
        "source_temp_c": state.get("source_temp_c"),
        "routine_metar_temp_c": state.get("routine_metar_temp_c"),
    }


def recover_checkpoint_state(
    output_dir: Path,
) -> tuple[set[str], dict[str, list[dict[str, Any]]]]:
    """Recover a partially completed initial materialization without deletion."""

    seen: set[str] = set()
    history_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((output_dir / "checkpoints").glob("*.jsonl")):
        try:
            handle = path.open(encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_key = str(row.get("source_event_key") or "")
                city = str(row.get("city") or "")
                target_date = str(row.get("target_date") or "")
                if event_key:
                    seen.add(event_key)
                if city and target_date:
                    history_by_key[f"{city}|{target_date}"].append(
                        compact_history_row(row)
                    )
    for key, rows in history_by_key.items():
        dedup = {
            str(row.get("source_event_key") or ""): row
            for row in rows
            if row.get("source_event_key")
        }
        history_by_key[key] = sorted(
            dedup.values(),
            key=lambda row: str(row.get("source_observation_ts_utc") or ""),
        )
    return seen, dict(history_by_key)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(Path(args.config))
    output_dir = Path(args.output_dir)
    state_path = output_dir / "state.json"
    persisted = read_json(state_path)
    if persisted.get("schema_version") != STATE_SCHEMA_VERSION:
        persisted = {}
    recovered_seen: set[str] = set()
    recovered_history: dict[str, list[dict[str, Any]]] = {}
    if not persisted:
        recovered_seen, recovered_history = recover_checkpoint_state(output_dir)
    rows, cursor, cursor_audit = read_appended_rows(
        Path(args.source_jsonl),
        dict(persisted.get("cursor") or {}),
    )
    seen = {
        str(value) for value in persisted.get("seen_event_keys") or []
    } | recovered_seen
    groups = distinct_amos_groups(
        rows,
        cities={str(value) for value in config["cities"]},
        source=str(config["source"]),
        seen_event_keys=seen,
    )
    history_by_key = {
        str(key): list(value)
        for key, value in dict(persisted.get("history_by_city_date") or {}).items()
    }
    for key, recovered_rows in recovered_history.items():
        existing = {
            str(row.get("source_event_key") or ""): row
            for row in history_by_key.get(key) or []
        }
        for row in recovered_rows:
            existing.setdefault(str(row.get("source_event_key") or ""), row)
        history_by_key[key] = sorted(
            existing.values(),
            key=lambda row: str(row.get("source_observation_ts_utc") or ""),
        )
    last_capture_by_city = {
        str(key): str(value)
        for key, value in dict(persisted.get("last_market_capture_by_city") or {}).items()
    }
    emitted: list[dict[str, Any]] = []
    for group in groups:
        aggregate = aggregate_amos_observation(group)
        event_key = str(aggregate["source_event_key"])
        if event_key in seen:
            continue
        history_key = f"{aggregate['city']}|{aggregate['target_date']}"
        history = list(history_by_key.get(history_key) or [])
        path = rolling_path_features(
            aggregate,
            history,
            windows_minutes=tuple(int(value) for value in config["path_window_minutes"]),
        )
        state = {**aggregate, **path}
        routine_values = [
            float(value)
            for row in [*history, compact_history_row(state)]
            if (value := row.get("routine_metar_temp_c")) is not None
        ]
        state["routine_running_max_c"] = (
            round(max(routine_values), 3) if routine_values else None
        )
        state["routine_running_max_market_value"] = (
            round(max(routine_values)) if routine_values else None
        )
        state["forecast_context"] = (
            forecast_context(Path(args.forecast_root), state)
            if (config.get("forecast_context") or {}).get("enabled")
            else {"status": "disabled"}
        )
        state["market_capture"] = capture_market(
            state,
            config=config,
            market_proxy=args.market_proxy,
            last_capture_by_city=last_capture_by_city,
        )
        checkpoint = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "mode": str(config["mode"]),
            "collector_config_schema_version": config.get("schema_version"),
            "collector_emitted_at_utc": iso_now(),
            **state,
        }
        emitted.append(checkpoint)
        seen.add(event_key)
        history.append(compact_history_row(state))
        history_by_key[history_key] = history[-1800:]

    emitted_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for checkpoint in emitted:
        emitted_by_date[str(checkpoint["target_date"])].append(checkpoint)
    for target_date, date_rows in sorted(emitted_by_date.items()):
        append_jsonl_batch(
            output_dir / "checkpoints" / f"{target_date}.jsonl",
            date_rows,
        )

    retained_dates = sorted(
        {
            datetime.now(SEOUL_TZ).date().isoformat(),
            (datetime.now(SEOUL_TZ).date() - timedelta(days=1)).isoformat(),
        }
    )
    history_by_key = {
        key: value
        for key, value in history_by_key.items()
        if key.rsplit("|", 1)[-1] in retained_dates
    }
    persisted_out = {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at_utc": iso_now(),
        "cursor": cursor,
        "seen_event_keys": sorted(seen)[-8000:],
        "history_by_city_date": history_by_key,
        "last_market_capture_by_city": last_capture_by_city,
    }
    write_json_atomic(state_path, persisted_out)
    latest = {
        "schema_version": "korea_first_seen_collector_latest_v1",
        "status": "ok",
        "generated_at_utc": iso_now(),
        "mode": config["mode"],
        "cities": config["cities"],
        "source": config["source"],
        "path_window_minutes": config["path_window_minutes"],
        "market_capture_policy": config["market_capture"],
        "source_cursor_audit": cursor_audit,
        "new_distinct_observations": len(emitted),
        "recovered_checkpoint_observations": len(recovered_seen),
        "market_capture_ok": sum(
            1
            for row in emitted
            if (row.get("market_capture") or {}).get("status") == "ok"
        ),
        "latest_checkpoints": emitted[-4:],
        "checkpoint_root": str(output_dir / "checkpoints"),
        "state_path": str(state_path),
    }
    write_json_atomic(output_dir / "latest.json", latest)
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--source-jsonl", default=str(DEFAULT_SOURCE_JSONL))
    parser.add_argument("--forecast-root", default=str(DEFAULT_FORECAST_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--market-proxy",
        default=os.environ.get("WEATHER_DATA_FEED_MARKET_PROXY", "http://127.0.0.1:7890"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.loop:
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    while True:
        started = time.monotonic()
        try:
            latest = run_once(args)
            print(
                json.dumps(
                    {
                        "status": latest["status"],
                        "generated_at_utc": latest["generated_at_utc"],
                        "new_distinct_observations": latest["new_distinct_observations"],
                        "market_capture_ok": latest["market_capture_ok"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "status": "error",
                        "generated_at_utc": iso_now(),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, float(args.interval_seconds) - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
