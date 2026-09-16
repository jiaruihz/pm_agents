"""Shared adapter for immutable legacy weather strategy snapshots.

The current canonical ladder tables begin in July 2026.  Earlier PIT weather
and direct two-sided books live in the archived strategy snapshot product.
This module projects that historical product into the same snapshot/rung
columns consumed by the ladder-mass-transport evaluator.  It does not infer
fills or use a future quote as an execution event.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from weather_data_feed import city_timezone_name
from weather_data_feed.market_brackets import bracket_center
from weather_clock_contract import parse_utc_or_none


DIRECT_FIELDS = tuple(
    f"{side}_{field}"
    for side in ("yes", "no")
    for field in (
        "best_bid",
        "best_ask",
        "bid_size",
        "ask_size",
        "depth_bid_5c",
        "depth_ask_5c",
    )
)
SNAPSHOT_COLUMNS = (
    "ladder_snapshot_id", "city", "target_date", "event_slug",
    "event_identity", "source_snapshot_ts_utc", "available_at_utc",
    "market_unit", "market_timezone", "market_utc_offset_seconds",
    "rung_count", "absolute_ladder_signature", "snapshot_source",
    "source_path", "forecast_capture_id", "forecast_model",
    "forecast_values_hash", "forecast_peak_f", "legacy_observed_max_f",
    "legacy_latest_obs_ts_utc", "legacy_metar_source",
)
RUNG_COLUMNS = (
    "ladder_snapshot_id", "bracket", "condition_id", "yes_token_id",
    "no_token_id", "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size",
    "yes_depth_bid_5c", "yes_depth_ask_5c", "no_bid", "no_ask",
    "no_bid_size", "no_ask_size", "no_depth_bid_5c",
    "no_depth_ask_5c", "yes_book_fetched_at_utc", "no_book_fetched_at_utc",
)
CACHE_SCHEMA_VERSION = "ladder_snapshot_history_cache_v2"


def _utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value)


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _first(records: list[dict[str, Any]], *fields: str) -> Any:
    for field in fields:
        for record in records:
            value = record.get(field)
            if value is not None and str(value).strip():
                return value
    return None


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_snapshot(path_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    path = Path(path_text)
    counts: Counter[str] = Counter(files_read=1)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], [], {"files_read": 1, "invalid_files": 1}
    snapshot_dt = _utc(payload.get("ts_utc"))
    records = payload.get("records")
    if snapshot_dt is None or not isinstance(records, list):
        return [], [], {"files_read": 1, "invalid_files": 1}
    counts["raw_record_rows"] += len(records)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        city = str(record.get("city") or "").strip()
        target_date = str(record.get("target_date") or record.get("event_date") or "").strip()
        bracket = str(record.get("bracket") or "").strip()
        if not city or not target_date or not bracket:
            counts["missing_identity_rows"] += 1
            continue
        event = str(record.get("event_slug") or f"{city}|{target_date}")
        grouped[(city, target_date, event)].append(record)

    snapshots: list[dict[str, Any]] = []
    rungs: list[dict[str, Any]] = []
    for (city, target_date, event), group in grouped.items():
        counts["raw_ladder_groups"] += 1
        timezone_name = city_timezone_name(city)
        if not timezone_name:
            counts["missing_timezone_groups"] += 1
            continue
        by_bracket: dict[str, dict[str, Any]] = {}
        duplicate = False
        for record in group:
            bracket = str(record.get("bracket") or "").strip()
            if bracket in by_bracket:
                duplicate = True
                break
            by_bracket[bracket] = record
        if duplicate or len(by_bracket) < 3:
            counts["invalid_ladder_groups"] += 1
            continue
        ordered = sorted(
            by_bracket.values(),
            key=lambda record: (bracket_center(record.get("bracket")), str(record.get("bracket"))),
        )
        if not all(math.isfinite(bracket_center(record.get("bracket"))) for record in ordered):
            counts["invalid_ladder_groups"] += 1
            continue
        if not all(record.get("condition_id") and all(field in record for field in DIRECT_FIELDS) for record in ordered):
            counts["incomplete_direct_groups"] += 1
            continue
        local_dt = snapshot_dt.astimezone(ZoneInfo(timezone_name))
        offset_seconds = int((local_dt.utcoffset() or datetime.resolution).total_seconds())
        signature = _stable_hash(
            [(str(record.get("bracket")), str(record.get("condition_id"))) for record in ordered]
        )
        snapshot_id = "paper:" + _stable_hash(
            [path.name, snapshot_dt.isoformat(), city, target_date, event, signature]
        )
        forecast_peak = _finite(_first(group, "forecast_max_f", "gfs_forecast_f"))
        forecast_hash = _first(group, "forecast_values_hash")
        if not forecast_hash and math.isfinite(forecast_peak):
            forecast_hash = "legacy:" + _stable_hash(
                [
                    forecast_peak,
                    _first(group, "forecast_source", "model"),
                    _first(group, "model_init_utc_estimated"),
                    _first(group, "forecast_peak_time_utc", "forecast_peak_time_local"),
                ]
            )
        observed_max = _finite(_first(group, "metar_current_max_f"))
        latest_obs_ts = _first(group, "metar_latest_ts_utc")
        snapshots.append(
            {
                "ladder_snapshot_id": snapshot_id,
                "city": city,
                "target_date": target_date,
                "event_slug": event,
                "event_identity": event,
                "source_snapshot_ts_utc": snapshot_dt.isoformat().replace("+00:00", "Z"),
                "available_at_utc": snapshot_dt.isoformat().replace("+00:00", "Z"),
                "market_unit": str(_first(group, "unit") or "F").upper(),
                "market_timezone": timezone_name,
                "market_utc_offset_seconds": offset_seconds,
                "rung_count": len(ordered),
                "absolute_ladder_signature": signature,
                "snapshot_source": "immutable_paper_snapshot",
                "source_path": str(path),
                "forecast_capture_id": f"{snapshot_id}:forecast",
                "forecast_model": str(_first(group, "model", "forecast_model") or "legacy_unknown"),
                "forecast_values_hash": forecast_hash,
                "forecast_peak_f": forecast_peak,
                "legacy_observed_max_f": observed_max,
                "legacy_latest_obs_ts_utc": latest_obs_ts,
                "legacy_metar_source": _first(group, "metar_source"),
            }
        )
        for record in ordered:
            rungs.append(
                {
                    "ladder_snapshot_id": snapshot_id,
                    "bracket": str(record.get("bracket")),
                    "condition_id": str(record.get("condition_id")),
                    "yes_token_id": record.get("yes_token_id"),
                    "no_token_id": record.get("no_token_id"),
                    "yes_bid": _finite(record.get("yes_best_bid")),
                    "yes_ask": _finite(record.get("yes_best_ask")),
                    "yes_bid_size": _finite(record.get("yes_bid_size")),
                    "yes_ask_size": _finite(record.get("yes_ask_size")),
                    "yes_depth_bid_5c": _finite(record.get("yes_depth_bid_5c")),
                    "yes_depth_ask_5c": _finite(record.get("yes_depth_ask_5c")),
                    "no_bid": _finite(record.get("no_best_bid")),
                    "no_ask": _finite(record.get("no_best_ask")),
                    "no_bid_size": _finite(record.get("no_bid_size")),
                    "no_ask_size": _finite(record.get("no_ask_size")),
                    "no_depth_bid_5c": _finite(record.get("no_depth_bid_5c")),
                    "no_depth_ask_5c": _finite(record.get("no_depth_ask_5c")),
                    "yes_book_fetched_at_utc": record.get("yes_book_fetched_at_utc") or snapshot_dt.isoformat(),
                    "no_book_fetched_at_utc": record.get("no_book_fetched_at_utc") or snapshot_dt.isoformat(),
                }
            )
        counts["complete_direct_groups"] += 1
        counts["complete_direct_rungs"] += len(ordered)
    return snapshots, rungs, dict(counts)


def _paths(
    snapshot_dir: Path, start: str, end: str, *, capture_lookback_days: int = 3
) -> list[Path]:
    start_key = (datetime.fromisoformat(start) - timedelta(days=capture_lookback_days)).strftime("%Y%m%d")
    end_key = end.replace("-", "")
    # Filename dates are Beijing capture dates.  Include the adjacent files and
    # apply the authoritative target-date filter after parsing.
    return [
        path
        for path in sorted(snapshot_dir.glob("snapshot_*.json"))
        if start_key <= path.name[9:17] <= end_key
    ]


def _first_event_time(frame: pd.DataFrame, key_column: str) -> pd.Series:
    streams = [frame["city"], frame["target_date"], frame["event_identity"]]
    changed = frame[key_column].fillna("__missing__").ne(
        frame.groupby(["city", "target_date", "event_identity"], sort=False)[key_column].shift(1).fillna("__missing__")
    )
    segment = changed.groupby(streams).cumsum()
    return frame.groupby([*streams, segment], sort=False)["snapshot_ts"].transform("first")


def _attach_weather_lineage(snapshots: pd.DataFrame) -> pd.DataFrame:
    frame = snapshots.sort_values(
        ["city", "target_date", "event_identity", "snapshot_ts", "ladder_snapshot_id"]
    ).copy()
    stream = ["city", "target_date", "event_identity"]
    frame["forecast_event_ts"] = _first_event_time(frame, "forecast_values_hash")
    frame["forecast_age_min"] = (
        frame["snapshot_ts"] - frame["forecast_event_ts"]
    ).dt.total_seconds() / 60.0
    forecast_changed = frame["forecast_values_hash"].fillna("__missing__").ne(
        frame.groupby(stream, sort=False)["forecast_values_hash"].shift(1).fillna("__missing__")
    )
    prior_forecast = frame.groupby(stream, sort=False)["forecast_peak_f"].shift(1)
    frame["forecast_peak_shock_f"] = np.where(
        forecast_changed, frame["forecast_peak_f"] - prior_forecast, 0.0
    )

    observation_key = frame["legacy_latest_obs_ts_utc"].fillna("__missing__").astype(str)
    frame["legacy_observation_key"] = observation_key
    frame["observation_event_ts"] = _first_event_time(frame, "legacy_observation_key")
    missing_observation = observation_key.eq("__missing__")
    frame.loc[missing_observation, "observation_event_ts"] = pd.NaT
    frame["observation_age_min"] = (
        frame["snapshot_ts"] - frame["observation_event_ts"]
    ).dt.total_seconds() / 60.0
    frame["observed_max_f"] = pd.to_numeric(frame["legacy_observed_max_f"], errors="coerce")
    obs_changed = observation_key.ne(
        frame.groupby(stream, sort=False)["legacy_observation_key"].shift(1).fillna("__missing__")
    )
    prior_observed = frame.groupby(stream, sort=False)["observed_max_f"].shift(1)
    frame["observed_max_shock_f"] = np.where(
        obs_changed, (frame["observed_max_f"] - prior_observed).clip(lower=0), 0.0
    )
    frame["tmax_v2_observation_id"] = np.where(
        observation_key.ne("__missing__"), "legacy_obs:" + observation_key, None
    )
    return frame


def load_history(
    snapshot_dir: Path,
    start: str,
    end: str,
    *,
    sample_seconds: int,
    workers: int = 8,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    paths = _paths(snapshot_dir, start, end)
    snapshot_rows: list[dict[str, Any]] = []
    rung_rows: list[dict[str, Any]] = []
    totals: Counter[str] = Counter(input_files=len(paths))
    parsed: Iterable[tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]]
    if workers == 1:
        parsed = map(_parse_snapshot, map(str, paths))
    else:
        # This is predominantly archive I/O.  Threads avoid macOS spawn
        # failures when the shared loader is invoked from an Agent/notebook
        # instead of a standalone __main__ file.
        executor = ThreadPoolExecutor(max_workers=workers)
        parsed = executor.map(_parse_snapshot, map(str, paths), chunksize=8)
    try:
        for snapshots, rungs, counts in parsed:
            snapshot_rows.extend(snapshots)
            rung_rows.extend(rungs)
            totals.update(counts)
    finally:
        if workers != 1:
            executor.shutdown()
    snapshots = pd.DataFrame(snapshot_rows)
    rungs = pd.DataFrame(rung_rows)
    if snapshots.empty:
        return snapshots, rungs, dict(totals)
    snapshots["snapshot_ts"] = pd.to_datetime(snapshots["source_snapshot_ts_utc"], utc=True)
    snapshots = snapshots[snapshots["target_date"].between(start, end)].copy()
    snapshots["time_bin"] = (snapshots["snapshot_ts"].astype("int64") // (sample_seconds * 1_000_000_000)).astype("int64")
    snapshots = snapshots.sort_values(["city", "target_date", "event_identity", "snapshot_ts", "ladder_snapshot_id"])
    snapshots["sample_rank"] = snapshots.groupby(
        ["city", "target_date", "event_identity", "time_bin"], sort=False
    ).cumcount()
    selected = snapshots[snapshots["sample_rank"].eq(0)].drop(columns=["time_bin", "sample_rank"])
    selected_ids = set(selected["ladder_snapshot_id"])
    rungs = rungs[rungs["ladder_snapshot_id"].isin(selected_ids)].copy()
    selected = _attach_weather_lineage(selected)
    totals["target_date_filtered_groups"] = len(snapshots)
    totals["sampled_groups"] = len(selected)
    totals["sampled_rungs"] = len(rungs)
    totals["sampled_dates"] = selected["target_date"].nunique()
    totals["sampled_cities"] = selected["city"].nunique()
    totals["forecast_hash_proxy_groups"] = int(
        selected["forecast_values_hash"].astype(str).str.startswith("legacy:").sum()
    )
    totals["missing_forecast_groups"] = int(selected["forecast_peak_f"].isna().sum())
    totals["missing_observation_groups"] = int(selected["observed_max_f"].isna().sum())
    return selected.reset_index(drop=True), rungs.reset_index(drop=True), dict(totals)


def _cache_inventory(paths: list[Path]) -> dict[str, Any]:
    stats = [path.stat() for path in paths]
    return {
        "file_count": len(paths),
        "first_file": paths[0].name if paths else None,
        "last_file": paths[-1].name if paths else None,
        "total_bytes": sum(stat.st_size for stat in stats),
        "max_mtime_ns": max((stat.st_mtime_ns for stat in stats), default=0),
    }


def _cache_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def materialize_history_cache(
    snapshot_dir: Path,
    start: str,
    end: str,
    cache_path: Path,
    *,
    sample_seconds: int = 1800,
    workers: int = 8,
) -> dict[str, Any]:
    """Stream the legacy JSON corpus into a run-scoped SQLite artifact."""
    paths = _paths(snapshot_dir, start, end)
    inventory = _cache_inventory(paths)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _cache_connect(cache_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        snapshot_ddl = ",".join(f'"{column}"' for column in SNAPSHOT_COLUMNS)
        rung_ddl = ",".join(f'"{column}"' for column in RUNG_COLUMNS)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS snapshots({snapshot_ddl}, PRIMARY KEY(ladder_snapshot_id))"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS rungs({rung_ddl}, PRIMARY KEY(ladder_snapshot_id,condition_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sample_keys("
            "city TEXT NOT NULL,target_date TEXT NOT NULL,event_identity TEXT NOT NULL,"
            "time_bin INTEGER NOT NULL,PRIMARY KEY(city,target_date,event_identity,time_bin))"
        )
        if conn.execute("SELECT COUNT(*) FROM sample_keys").fetchone()[0] == 0:
            conn.execute(
                "INSERT OR IGNORE INTO sample_keys(city,target_date,event_identity,time_bin) "
                "SELECT city,target_date,event_identity,"
                "CAST(strftime('%s',source_snapshot_ts_utc) AS INTEGER)/? FROM snapshots",
                (sample_seconds,),
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_snapshots_date ON snapshots(target_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_rungs_snapshot ON rungs(ladder_snapshot_id)")
        meta = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT key,value FROM metadata")}
        expected = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "start": start,
            "end": end,
            "snapshot_dir": str(snapshot_dir.resolve()),
            "sample_seconds": sample_seconds,
            "inventory": inventory,
        }
        if meta.get("complete") is True:
            for key, value in expected.items():
                if meta.get(key) != value:
                    raise ValueError(f"historical cache identity mismatch for {key}")
            return dict(meta["coverage"])
        mismatched = [
            key for key, value in expected.items()
            if key in meta and meta.get(key) != value
        ]
        if mismatched:
            # Reset only this recoverable derived cache. Resuming a partial
            # cache from another raw root/range would contaminate the model
            # denominator while looking superficially complete.
            conn.execute("DELETE FROM rungs")
            conn.execute("DELETE FROM snapshots")
            conn.execute("DELETE FROM metadata")
            meta = {}
        for key, value in expected.items():
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                (key, json.dumps(value, sort_keys=True)),
            )
        resume_after = meta.get("last_processed_file")
        if resume_after is None:
            row = conn.execute("SELECT MAX(source_path) FROM snapshots").fetchone()
            resume_after = Path(row[0]).name if row and row[0] else None
        remaining = [path for path in paths if resume_after is None or path.name > resume_after]
        totals: Counter[str] = Counter(
            input_files=len(paths),
            resumed_after_files=len(paths) - len(remaining),
        )
        snapshot_sql = (
            f"INSERT OR IGNORE INTO snapshots({','.join(SNAPSHOT_COLUMNS)}) "
            f"VALUES({','.join('?' for _ in SNAPSHOT_COLUMNS)})"
        )
        rung_sql = (
            f"INSERT OR IGNORE INTO rungs({','.join(RUNG_COLUMNS)}) "
            f"VALUES({','.join('?' for _ in RUNG_COLUMNS)})"
        )
        seen_keys = set(conn.execute(
            "SELECT city,target_date,event_identity,time_bin FROM sample_keys"
        ))
        batch_size = max(1, workers * 2)
        executor = ThreadPoolExecutor(max_workers=max(1, workers)) if workers > 1 else None
        try:
            for offset in range(0, len(remaining), batch_size):
                batch = remaining[offset:offset + batch_size]
                parsed = (
                    executor.map(_parse_snapshot, map(str, batch))
                    if executor is not None
                    else map(_parse_snapshot, map(str, batch))
                )
                for path, (snapshots, rungs, counts) in zip(batch, parsed):
                    snapshots = [row for row in snapshots if start <= row["target_date"] <= end]
                    selected_snapshots=[]
                    selected_keys=[]
                    for row in snapshots:
                        snapshot_dt=_utc(row["source_snapshot_ts_utc"])
                        if snapshot_dt is None:
                            continue
                        key=(
                            row["city"],row["target_date"],row["event_identity"],
                            int(snapshot_dt.timestamp())//sample_seconds,
                        )
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        selected_keys.append(key)
                        selected_snapshots.append(row)
                    snapshots=selected_snapshots
                    selected_ids = {row["ladder_snapshot_id"] for row in snapshots}
                    rungs = [row for row in rungs if row["ladder_snapshot_id"] in selected_ids]
                    if selected_keys:
                        conn.executemany(
                            "INSERT OR IGNORE INTO sample_keys(city,target_date,event_identity,time_bin) VALUES(?,?,?,?)",
                            selected_keys,
                        )
                    if snapshots:
                        conn.executemany(snapshot_sql, [tuple(row.get(column) for column in SNAPSHOT_COLUMNS) for row in snapshots])
                    if rungs:
                        conn.executemany(rung_sql, [tuple(row.get(column) for column in RUNG_COLUMNS) for row in rungs])
                    totals.update(counts)
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                    ("last_processed_file", json.dumps(batch[-1].name)),
                )
                conn.commit()
        finally:
            if executor is not None:
                executor.shutdown()
        totals["cached_groups"] = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        totals["cached_rungs"] = conn.execute("SELECT COUNT(*) FROM rungs").fetchone()[0]
        totals["cached_dates"] = conn.execute("SELECT COUNT(DISTINCT target_date) FROM snapshots").fetchone()[0]
        totals["cached_cities"] = conn.execute("SELECT COUNT(DISTINCT city) FROM snapshots").fetchone()[0]
        for key, value in (("coverage", dict(totals)), ("complete", True)):
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                (key, json.dumps(value, sort_keys=True)),
            )
        conn.commit()
        return dict(totals)
    finally:
        conn.close()


def history_cache_dates(cache_path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True, timeout=30.0)
    try:
        return [row[0] for row in conn.execute("SELECT DISTINCT target_date FROM snapshots ORDER BY target_date")]
    finally:
        conn.close()


def load_history_cache_date(
    cache_path: Path,
    target_date: str,
    *,
    sample_seconds: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True, timeout=30.0)
    try:
        snapshots = pd.read_sql_query(
            "SELECT * FROM snapshots WHERE target_date=? ORDER BY source_snapshot_ts_utc,city,event_identity",
            conn,
            params=(target_date,),
        )
        if snapshots.empty:
            return snapshots, pd.DataFrame(columns=RUNG_COLUMNS)
        snapshots["snapshot_ts"] = pd.to_datetime(snapshots["source_snapshot_ts_utc"], utc=True)
        snapshots["time_bin"] = (
            snapshots["snapshot_ts"].astype("int64") // (sample_seconds * 1_000_000_000)
        ).astype("int64")
        snapshots = snapshots.sort_values(
            ["city", "target_date", "event_identity", "snapshot_ts", "ladder_snapshot_id"]
        )
        snapshots["sample_rank"] = snapshots.groupby(
            ["city", "target_date", "event_identity", "time_bin"], sort=False
        ).cumcount()
        snapshots = snapshots[snapshots["sample_rank"].eq(0)].drop(columns=["time_bin", "sample_rank"])
        ids = snapshots["ladder_snapshot_id"].tolist()
        rung_frames: list[pd.DataFrame] = []
        for offset in range(0, len(ids), 800):
            chunk = ids[offset:offset + 800]
            marks = ",".join("?" for _ in chunk)
            rung_frames.append(
                pd.read_sql_query(
                    f"SELECT * FROM rungs WHERE ladder_snapshot_id IN ({marks})",
                    conn,
                    params=chunk,
                )
            )
        rungs = pd.concat(rung_frames, ignore_index=True) if rung_frames else pd.DataFrame(columns=RUNG_COLUMNS)
    finally:
        conn.close()
    return _attach_weather_lineage(snapshots).reset_index(drop=True), rungs.reset_index(drop=True)
