#!/usr/bin/env python3
"""
build_weather_signal_candidates.py

机会粒度(opportunity-grain)候选事实表 fact_signal_candidates。
grain = 一个机会 (condition_id, side, event_date)。

把三层数据对齐到同一行:
  全机会宇宙 (paper_snapshots/*.json)
    → intended (paper_orders.jsonl)
    → actual (fact_trades WHERE trade_class='live_real')
  + 结算 (settlements 表)

输出:
  - runtime/weather.db 的 fact_signal_candidates 表(幂等重建)
  - runtime/weather_edge_v1/market_data/research/fact_signal_candidates.parquet

设计文档: docs/WEATHER_SIGNAL_CANDIDATES_DESIGN.md
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.official_observation_clock import city_timezone_name
DB_PATH = ROOT / "runtime" / "weather.db"
PARQUET_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "market_data" / "research"
    / "fact_signal_candidates.parquet"
)
SNAPSHOT_DIR = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "paper_snapshots"
PAPER_ORDERS_PATH = (
    ROOT / "runtime" / "weather_edge_v1" / "market_data" / "paper_trades" / "paper_orders.jsonl"
)
FORECAST_CACHE_ROOT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "cache"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _binary_final_yes(v: Any) -> float | None:
    fp = _safe_float(v)
    if fp is None:
        return None
    if fp >= 0.99:
        return 1.0
    if fp <= 0.01:
        return 0.0
    return None


def _hours_to_settle(rec: dict) -> float | None:
    return _safe_float(rec.get("hours_to_settle"))


def _safe_int(v: Any) -> int | None:
    fv = _safe_float(v)
    return int(fv) if fv is not None else None


def _parse_utc_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _forecast_source_model(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return None


def _forecast_values_hash(rows: list[tuple[str, float]]) -> str:
    payload = [[str(ts), round(float(temp), 3)] for ts, temp in rows]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class _ForecastPeakIndex:
    """Derive forecast peak-clock fields from mirrored hourly forecast cache.

    Snapshot records are the source of truth. This index only fills missing
    forecast_peak_* fields when a matching city/model/date hourly cache exists.
    """

    SOURCE_DIRS = {
        "gfs": "gfs_v4",
        "ecmwf": "ecmwf_v4",
    }

    def __init__(self, cache_root: Path):
        self.cache_root = cache_root
        self._by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.files_seen = 0
        self.files_loaded = 0
        self.derived_city_dates = 0
        self.min_date: str | None = None
        self.max_date: str | None = None
        self._load()

    @staticmethod
    def _cache_city_from_path(path: Path, source_model: str) -> str:
        stem = path.stem
        prefix = f"{source_model}_v4_"
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
        return stem.rsplit("_", 2)[0]

    @staticmethod
    def _aliases(city: str) -> set[str]:
        aliases = {city}
        if city == "LA":
            aliases.add("LosAngeles")
        if city == "LosAngeles":
            aliases.add("LA")
        return aliases

    def _load(self) -> None:
        for source_model, dirname in self.SOURCE_DIRS.items():
            folder = self.cache_root / dirname
            if not folder.exists():
                continue
            for path in sorted(folder.glob(f"{source_model}_v4_*.json")):
                self.files_seen += 1
                try:
                    payload = json.loads(path.read_text())
                except Exception:
                    continue
                derived = self._derive_file(path, payload, source_model)
                if not derived:
                    continue
                self.files_loaded += 1
                for key, row in derived.items():
                    self._by_key[key] = row
                    date = key[2]
                    self.min_date = date if self.min_date is None else min(self.min_date, date)
                    self.max_date = date if self.max_date is None else max(self.max_date, date)
                self.derived_city_dates += len(derived)

    def _derive_file(
        self,
        path: Path,
        payload: dict[str, Any],
        source_model: str,
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        if not times or not temps or len(times) != len(temps):
            return {}

        city = self._cache_city_from_path(path, source_model)
        tz_name = city_timezone_name(city) or "UTC"
        try:
            city_tz = ZoneInfo(tz_name)
        except Exception:
            city_tz = ZoneInfo("UTC")
            tz_name = "UTC"

        payload_tz = str(payload.get("timezone") or "").strip()
        payload_offset = _safe_int(payload.get("utc_offset_seconds")) or 0
        payload_is_utc = payload_tz.upper() in {"GMT", "UTC", ""} and payload_offset == 0

        by_local_date: dict[str, list[tuple[datetime, float]]] = {}
        for raw_ts, raw_temp in zip(times, temps, strict=False):
            try:
                temp = float(raw_temp)
            except Exception:
                continue
            try:
                naive = datetime.fromisoformat(str(raw_ts))
            except ValueError:
                continue
            if naive.tzinfo is not None:
                local_dt = naive.astimezone(city_tz)
            elif payload_is_utc:
                local_dt = naive.replace(tzinfo=timezone.utc).astimezone(city_tz)
            else:
                local_dt = naive.replace(tzinfo=city_tz)
            by_local_date.setdefault(local_dt.date().isoformat(), []).append((local_dt, temp))

        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        for date, rows in by_local_date.items():
            if not rows:
                continue
            max_f = max(temp for _, temp in rows)
            peak_dt = min(dt for dt, temp in rows if abs(temp - max_f) < 1e-9)
            peak_utc = peak_dt.astimezone(timezone.utc)
            hash_rows = [
                (dt.replace(tzinfo=None).isoformat(timespec="minutes"), temp)
                for dt, temp in sorted(rows, key=lambda x: x[0])
            ]
            utc_offset = int(peak_dt.utcoffset().total_seconds()) if peak_dt.utcoffset() else 0
            try:
                source_file = str(path.relative_to(ROOT))
            except ValueError:
                source_file = str(path)
            row = {
                "forecast_max_f": max_f,
                "forecast_peak_hour_local": peak_dt.hour,
                "forecast_peak_time_local": peak_dt.replace(tzinfo=None).isoformat(timespec="minutes"),
                "forecast_peak_hour_utc": peak_utc.hour,
                "forecast_peak_time_utc": peak_utc.isoformat().replace("+00:00", "Z"),
                "forecast_hourly_count": len(rows),
                "forecast_values_hash": _forecast_values_hash(hash_rows),
                "forecast_peak_source": f"open_meteo_live_{source_model}",
                "forecast_timezone": tz_name,
                "forecast_utc_offset_seconds": utc_offset,
                "forecast_peak_source_file": source_file,
            }
            for alias in self._aliases(city):
                out[(alias, source_model, date)] = row
        return out

    def enrich(self, rec: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if rec.get("forecast_peak_hour_local") is not None and rec.get("forecast_values_hash"):
            return rec, False
        city = str(rec.get("city") or "").strip()
        event_date = str(rec.get("event_date") or "").strip()
        source_model = _forecast_source_model(rec.get("forecast_source") or rec.get("model"))
        if not city or not event_date or not source_model:
            return rec, False
        derived = self._by_key.get((city, source_model, event_date))
        if not derived:
            return rec, False

        out = dict(rec)
        for key, value in derived.items():
            if key == "forecast_peak_source_file":
                continue
            if out.get(key) in (None, ""):
                out[key] = value
        unit = str(out.get("unit") or "").upper()
        max_f = _safe_float(out.get("forecast_max_f"))
        if max_f is not None and out.get("forecast_max_native") in (None, ""):
            out["forecast_max_native"] = (max_f - 32.0) * 5.0 / 9.0 if unit == "C" else max_f
        peak_hour = _safe_float(out.get("forecast_peak_hour_local"))
        ts = _parse_utc_ts(out.get("ts_utc"))
        tz_name = out.get("forecast_timezone") or city_timezone_name(city)
        if peak_hour is not None and ts is not None and out.get("forecast_peak_delta_hours_local") in (None, "") and tz_name:
            try:
                local_ts = ts.astimezone(ZoneInfo(str(tz_name)))
                out["forecast_peak_delta_hours_local"] = (
                    local_ts.hour + local_ts.minute / 60.0 - float(peak_hour)
                )
            except Exception:
                pass
        return out, True


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CANDIDATE_DDL = """
CREATE TABLE IF NOT EXISTS fact_signal_candidates (
  -- grain / 关联键
  candidate_id        TEXT PRIMARY KEY,
  condition_id        TEXT,
  market_id           TEXT,
  side                TEXT,
  event_date          TEXT,
  bracket             TEXT,

  -- 维度
  city                TEXT,
  city_pool           TEXT,
  icao                TEXT,
  unit                TEXT,
  forecast_source     TEXT,
  forecast_max_f      REAL,
  forecast_max_native REAL,
  forecast_peak_hour_local INTEGER,
  forecast_peak_time_local TEXT,
  forecast_peak_hour_utc INTEGER,
  forecast_peak_time_utc TEXT,
  forecast_hourly_count INTEGER,
  forecast_values_hash TEXT,
  forecast_peak_source TEXT,
  forecast_timezone TEXT,
  forecast_utc_offset_seconds INTEGER,
  forecast_peak_delta_hours_local REAL,
  forecast_max_in_bracket INTEGER,
  forecast_max_above_bracket_f REAL,
  forecast_max_below_bracket_f REAL,
  forecast_max_above_metar_max_f REAL,
  model_version       TEXT,
  time_bucket         TEXT,
  window              TEXT,

  -- 决策窗（builder 参数选出的代表 snapshot，自描述）
  decision_window_label    TEXT,
  decision_hours_to_settle REAL,
  decision_snapshot_ts_utc TEXT,
  decision_window_missing  INTEGER,

  -- 信号（决策窗代表值）
  model_p_yes         REAL,
  market_yes_price    REAL,
  edge                REAL,
  abs_edge            REAL,

  -- 盘口可成交性（决策窗代表值）
  decision_entry_price REAL,
  yes_spread          REAL,
  no_spread           REAL,
  yes_depth_ask_5c    REAL,
  no_depth_ask_5c     REAL,

  -- 全天聚合（诊断用，非主口径）
  first_seen_ts_utc   TEXT,
  last_seen_ts_utc    TEXT,
  n_snapshots         INTEGER,
  edge_max            REAL,
  edge_mean           REAL,
  best_entry_price    REAL,

  -- 链路标志
  seen                INTEGER,
  eligible            INTEGER,
  paper_ordered       INTEGER,
  live_filled         INTEGER,

  -- intended（paper 决定）
  paper_order_id      TEXT,
  paper_entry_price   REAL,
  paper_shares        REAL,
  paper_snapshot_ts_utc TEXT,

  -- actual（live 成交，引用 fact_trades）
  fill_id             TEXT,
  live_fill_price     REAL,
  live_fill_qty       REAL,
  live_pnl_usd        REAL,

  -- intended vs actual
  slippage_vs_paper   REAL,

  -- 结算 / 中没中
  settlement_status   TEXT,
  final_yes           REAL,
  bracket_hit         INTEGER,
  win_by_count        INTEGER,

  -- 反事实绩效
  counterfactual_pnl        REAL,
  counterfactual_pnl_best   REAL,

  -- build 元数据
  fact_built_at_utc   TEXT
)
"""


# ---------------------------------------------------------------------------
# Counterfactual PnL (沿用 fact_trades 已验证公式)
# ---------------------------------------------------------------------------

def _counterfactual_pnl(
    side: str, entry: float | None, final_yes: float | None, shares: float | None
) -> float | None:
    if entry is None or final_yes is None or shares is None:
        return None
    if side == "BUY_YES":
        return (final_yes - entry) * shares
    if side == "BUY_NO":
        return ((1.0 - final_yes) - entry) * shares
    return None


# ---------------------------------------------------------------------------
# Universe accumulator
# ---------------------------------------------------------------------------

class _Opportunity:
    """Streaming accumulator for one (condition_id, side, event_date) opportunity."""

    __slots__ = (
        "condition_id", "side", "event_date",
        "market_id", "bracket", "city", "city_pool", "icao", "unit",
        "forecast_source", "model_version", "time_bucket", "window",
        "dec_forecast_max_f", "dec_forecast_max_native",
        "dec_forecast_peak_hour_local", "dec_forecast_peak_time_local",
        "dec_forecast_peak_hour_utc", "dec_forecast_peak_time_utc",
        "dec_forecast_hourly_count", "dec_forecast_values_hash",
        "dec_forecast_peak_source",
        "dec_forecast_timezone", "dec_forecast_utc_offset_seconds",
        "dec_forecast_peak_delta_hours_local",
        "dec_forecast_max_in_bracket",
        "dec_forecast_max_above_bracket_f", "dec_forecast_max_below_bracket_f",
        "dec_forecast_max_above_metar_max_f",
        "first_seen_ts_utc", "last_seen_ts_utc", "n_snapshots",
        "edge_max", "_edge_sum", "_edge_count", "best_entry_price",
        "eligible",
        # decision-window representative
        "_dec_dist", "dec_hts", "dec_ts", "dec_model_p_yes", "dec_market_yes_price",
        "dec_edge", "dec_abs_edge", "dec_entry_price",
        "dec_yes_spread", "dec_no_spread", "dec_yes_depth_ask_5c", "dec_no_depth_ask_5c",
        "dec_shares",
    )

    def __init__(self, condition_id: str, side: str, event_date: str):
        self.condition_id = condition_id
        self.side = side
        self.event_date = event_date
        self.market_id = None
        self.bracket = None
        self.city = None
        self.city_pool = None
        self.icao = None
        self.unit = None
        self.forecast_source = None
        self.model_version = None
        self.time_bucket = None
        self.window = None
        self.dec_forecast_max_f = None
        self.dec_forecast_max_native = None
        self.dec_forecast_peak_hour_local = None
        self.dec_forecast_peak_time_local = None
        self.dec_forecast_peak_hour_utc = None
        self.dec_forecast_peak_time_utc = None
        self.dec_forecast_hourly_count = None
        self.dec_forecast_values_hash = None
        self.dec_forecast_peak_source = None
        self.dec_forecast_timezone = None
        self.dec_forecast_utc_offset_seconds = None
        self.dec_forecast_peak_delta_hours_local = None
        self.dec_forecast_max_in_bracket = None
        self.dec_forecast_max_above_bracket_f = None
        self.dec_forecast_max_below_bracket_f = None
        self.dec_forecast_max_above_metar_max_f = None
        self.first_seen_ts_utc = None
        self.last_seen_ts_utc = None
        self.n_snapshots = 0
        self.edge_max = None
        self._edge_sum = 0.0
        self._edge_count = 0
        self.best_entry_price = None
        self.eligible = None
        self._dec_dist = None
        self.dec_hts = None
        self.dec_ts = None
        self.dec_model_p_yes = None
        self.dec_market_yes_price = None
        self.dec_edge = None
        self.dec_abs_edge = None
        self.dec_entry_price = None
        self.dec_yes_spread = None
        self.dec_no_spread = None
        self.dec_yes_depth_ask_5c = None
        self.dec_no_depth_ask_5c = None
        self.dec_shares = None

    def observe(self, rec: dict, target_hts: float, hts_min: float, hts_max: float) -> None:
        ts = rec.get("ts_utc")
        self.n_snapshots += 1
        if self.first_seen_ts_utc is None or (ts and ts < self.first_seen_ts_utc):
            self.first_seen_ts_utc = ts
        if self.last_seen_ts_utc is None or (ts and ts > self.last_seen_ts_utc):
            self.last_seen_ts_utc = ts

        # constant dims (last non-null wins)
        for attr, key in (
            ("market_id", "market_id"), ("bracket", "bracket"), ("city", "city"),
            ("city_pool", "city_pool"), ("icao", "icao"), ("unit", "unit"),
            ("forecast_source", "forecast_source"), ("model_version", "model"),
            ("time_bucket", "time_bucket"), ("window", "window"),
        ):
            v = rec.get(key)
            if v is not None:
                setattr(self, attr, str(v) if not isinstance(v, str) else v)

        if "eligible_for_paper_order" in rec and rec["eligible_for_paper_order"] is not None:
            self.eligible = int(bool(rec["eligible_for_paper_order"]))

        edge = _safe_float(rec.get("edge"))
        if edge is not None:
            self._edge_sum += edge
            self._edge_count += 1
            if self.edge_max is None or edge > self.edge_max:
                self.edge_max = edge

        entry = _safe_float(rec.get("entry_price"))
        if entry is not None:
            if self.best_entry_price is None or entry < self.best_entry_price:
                self.best_entry_price = entry

        # decision-window candidate: in band, closest to target hours_to_settle
        hts = _hours_to_settle(rec)
        if hts is not None and hts_min <= hts <= hts_max:
            dist = abs(hts - target_hts)
            if self._dec_dist is None or dist < self._dec_dist:
                self._dec_dist = dist
                self.dec_hts = hts
                self.dec_ts = ts
                self.dec_model_p_yes = _safe_float(rec.get("model_prob"))
                self.dec_market_yes_price = _safe_float(rec.get("market_yes_price"))
                self.dec_edge = edge
                self.dec_abs_edge = _safe_float(rec.get("abs_edge"))
                self.dec_entry_price = entry
                self.dec_yes_spread = _safe_float(rec.get("yes_spread"))
                self.dec_no_spread = _safe_float(rec.get("no_spread"))
                self.dec_yes_depth_ask_5c = _safe_float(rec.get("yes_depth_ask_5c"))
                self.dec_no_depth_ask_5c = _safe_float(rec.get("no_depth_ask_5c"))
                self.dec_shares = _safe_float(rec.get("shares"))
                self.dec_forecast_max_f = _safe_float(rec.get("forecast_max_f"))
                self.dec_forecast_max_native = _safe_float(rec.get("forecast_max_native"))
                self.dec_forecast_peak_hour_local = _safe_float(rec.get("forecast_peak_hour_local"))
                self.dec_forecast_peak_time_local = rec.get("forecast_peak_time_local")
                self.dec_forecast_peak_hour_utc = _safe_float(rec.get("forecast_peak_hour_utc"))
                self.dec_forecast_peak_time_utc = rec.get("forecast_peak_time_utc")
                self.dec_forecast_hourly_count = _safe_float(rec.get("forecast_hourly_count"))
                self.dec_forecast_values_hash = rec.get("forecast_values_hash")
                self.dec_forecast_peak_source = rec.get("forecast_peak_source")
                self.dec_forecast_timezone = rec.get("forecast_timezone")
                self.dec_forecast_utc_offset_seconds = _safe_float(rec.get("forecast_utc_offset_seconds"))
                self.dec_forecast_peak_delta_hours_local = _safe_float(rec.get("forecast_peak_delta_hours_local"))
                self.dec_forecast_max_in_bracket = _safe_float(rec.get("forecast_max_in_bracket"))
                self.dec_forecast_max_above_bracket_f = _safe_float(rec.get("forecast_max_above_bracket_f"))
                self.dec_forecast_max_below_bracket_f = _safe_float(rec.get("forecast_max_below_bracket_f"))
                self.dec_forecast_max_above_metar_max_f = _safe_float(rec.get("forecast_max_above_metar_max_f"))

    @property
    def edge_mean(self) -> float | None:
        return self._edge_sum / self._edge_count if self._edge_count else None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_universe(
    snapshot_dir: Path,
    target_hts: float,
    hts_min: float,
    hts_max: float,
    forecast_index: _ForecastPeakIndex | None = None,
) -> tuple[dict[tuple, _Opportunity], int, int, int]:
    """Stream all snapshots into opportunity accumulators keyed by
    (condition_id, side, event_date). Records missing condition_id are dropped.
    Returns (opportunities, n_files, n_dropped_no_cid).
    """
    files = sorted(glob.glob(str(snapshot_dir / "*.json")))
    opps: dict[tuple, _Opportunity] = {}
    n_dropped = 0
    n_forecast_enriched = 0
    for f in files:
        try:
            data = json.loads(Path(f).read_text())
        except Exception:
            continue
        for rec in data.get("records", []):
            if not isinstance(rec, dict):
                continue
            if forecast_index is not None:
                rec, enriched = forecast_index.enrich(rec)
                n_forecast_enriched += int(enriched)
            cid = rec.get("condition_id")
            side = rec.get("side")
            event_date = rec.get("event_date")
            if not cid:
                n_dropped += 1
                continue
            if not side or not event_date:
                continue
            key = (cid, side, str(event_date))
            opp = opps.get(key)
            if opp is None:
                opp = _Opportunity(cid, side, str(event_date))
                opps[key] = opp
            opp.observe(rec, target_hts, hts_min, hts_max)
    return opps, len(files), n_dropped, n_forecast_enriched


def _load_paper_orders(path: Path) -> tuple[dict[tuple, dict], dict]:
    """paper_orders.jsonl aggregated by (condition_id, side, event_date)."""
    out: dict[tuple, dict] = {}
    stats = {"raw_rows": 0, "dropped_no_key": 0, "duplicate_extra_rows": 0}
    if not path.exists():
        return out, stats
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            cid = r.get("condition_id")
            side = r.get("side")
            event_date = r.get("event_date") or r.get("target_date")
            if not cid or not side or not event_date:
                stats["dropped_no_key"] += 1
                continue
            stats["raw_rows"] += 1
            key = (cid, side, str(event_date))
            entry = _safe_float(r.get("entry_price"))
            shares = _safe_float(r.get("shares"))
            snapshot_ts = r.get("snapshot_ts_utc")

            agg = out.get(key)
            if agg is None:
                agg = {
                    "condition_id": cid,
                    "side": side,
                    "event_date": str(event_date),
                    "order_ids": [],
                    "shares": 0.0,
                    "_notional": 0.0,
                    "_priced_shares": 0.0,
                    "entry_price": None,
                    "snapshot_ts_utc": None,
                    "order_count": 0,
                }
                out[key] = agg
            else:
                stats["duplicate_extra_rows"] += 1

            order_id = r.get("order_id")
            if order_id:
                agg["order_ids"].append(str(order_id))
            agg["order_count"] += 1
            if shares is not None:
                agg["shares"] += shares
                if entry is not None:
                    agg["_notional"] += entry * shares
                    agg["_priced_shares"] += shares
            if snapshot_ts and (
                agg["snapshot_ts_utc"] is None or str(snapshot_ts) > agg["snapshot_ts_utc"]
            ):
                agg["snapshot_ts_utc"] = str(snapshot_ts)

    for agg in out.values():
        priced_shares = agg.pop("_priced_shares")
        notional = agg.pop("_notional")
        agg["entry_price"] = notional / priced_shares if priced_shares else None
        agg["order_id"] = ",".join(agg.pop("order_ids")) or None
        if agg["shares"] == 0.0:
            agg["shares"] = None
    return out, stats


def _load_live_fills(conn: sqlite3.Connection) -> dict[tuple, dict]:
    """fact_trades live_real aggregated by (condition_id, side, target_date)."""
    rows = conn.execute(
        """
        SELECT
          condition_id,
          side,
          target_date,
          GROUP_CONCAT(fill_id) AS fill_id,
          CASE
            WHEN SUM(CASE WHEN fill_price IS NOT NULL AND fill_qty IS NOT NULL THEN fill_qty ELSE 0 END) > 0
            THEN
              SUM(CASE WHEN fill_price IS NOT NULL AND fill_qty IS NOT NULL THEN fill_price * fill_qty ELSE 0 END)
              / SUM(CASE WHEN fill_price IS NOT NULL AND fill_qty IS NOT NULL THEN fill_qty ELSE 0 END)
            ELSE AVG(fill_price)
          END AS fill_price,
          SUM(fill_qty) AS fill_qty,
          SUM(pnl_usd_at_fill) AS pnl_usd_at_fill,
          COUNT(*) AS fill_count
        FROM fact_trades
        WHERE trade_class='live_real'
        GROUP BY condition_id, side, target_date
        """
    ).fetchall()
    cols = ["condition_id", "side", "target_date", "fill_id", "fill_price",
            "fill_qty", "pnl_usd_at_fill", "fill_count"]
    out: dict[tuple, dict] = {}
    for r in rows:
        d = dict(zip(cols, r))
        cid = d.get("condition_id")
        side = d.get("side")
        td = d.get("target_date")
        if not cid or not side or not td:
            continue
        out[(cid, side, td)] = d
    return out


def _load_settlements(conn: sqlite3.Connection) -> dict[tuple, dict]:
    """settlements keyed by (target_date, condition_id, bracket). First wins."""
    rows = conn.execute(
        "SELECT target_date, condition_id, bracket, final_price, settlement_status "
        "FROM settlements"
    ).fetchall()
    cols = ["target_date", "condition_id", "bracket", "final_price", "settlement_status"]
    out: dict[tuple, dict] = {}
    for r in rows:
        d = dict(zip(cols, r))
        key = (d["target_date"], d["condition_id"], str(d["bracket"]) if d["bracket"] is not None else None)
        out.setdefault(key, d)
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(
    conn: sqlite3.Connection,
    snapshot_dir: Path = SNAPSHOT_DIR,
    paper_orders_path: Path = PAPER_ORDERS_PATH,
    hts_min: float = 22.0,
    hts_max: float = 24.0,
    forecast_cache_root: Path = FORECAST_CACHE_ROOT,
) -> tuple[list[dict], list[str], dict]:
    """Build candidate rows. Returns (rows, alerts, stats)."""
    target_hts = (hts_min + hts_max) / 2.0
    window_label = f"hts_{int(hts_min)}_{int(hts_max)}" if hts_min == int(hts_min) and hts_max == int(hts_max) else f"hts_{hts_min}_{hts_max}"

    forecast_index = _ForecastPeakIndex(forecast_cache_root)
    opps, n_files, n_dropped, n_forecast_enriched = _load_universe(
        snapshot_dir,
        target_hts,
        hts_min,
        hts_max,
        forecast_index=forecast_index,
    )
    paper_orders, paper_stats = _load_paper_orders(paper_orders_path)
    live_fills = _load_live_fills(conn)
    settlements = _load_settlements(conn)

    now_utc = datetime.now(timezone.utc).isoformat()
    alerts: list[str] = []
    rows: list[dict] = []

    matched_paper: set[tuple] = set()
    matched_fills: set[tuple] = set()

    for (cid, side, event_date), opp in opps.items():
        candidate_id = f"{cid}|{side}|{event_date}"
        decision_missing = int(opp.dec_hts is None)

        # intended (paper)
        po = paper_orders.get((cid, side, event_date))
        paper_ordered = int(po is not None)
        paper_entry_price = _safe_float(po.get("entry_price")) if po else None
        paper_shares = _safe_float(po.get("shares")) if po else None
        if po:
            matched_paper.add((cid, side, event_date))

        # actual (live)
        lf = live_fills.get((cid, side, event_date))
        live_filled = int(lf is not None)
        live_fill_price = _safe_float(lf.get("fill_price")) if lf else None
        live_fill_qty = _safe_float(lf.get("fill_qty")) if lf else None
        live_pnl_usd = _safe_float(lf.get("pnl_usd_at_fill")) if lf else None
        if lf:
            matched_fills.add((cid, side, event_date))

        slippage = (
            live_fill_price - paper_entry_price
            if live_fill_price is not None and paper_entry_price is not None
            else None
        )

        # settlement
        sett = settlements.get((event_date, cid, opp.bracket))
        settlement_status = sett.get("settlement_status") if sett else None
        final_yes: float | None = None
        if settlement_status == "settled" and sett:
            final_yes = _binary_final_yes(sett.get("final_price"))
            if final_yes is None:
                alerts.append(
                    f"FINAL_YES_UNEXPECTED candidate={candidate_id} final_price={sett.get('final_price')}"
                )

        bracket_hit = int(final_yes == 1.0) if final_yes is not None else None
        win_by_count = None
        if final_yes is not None:
            if side == "BUY_YES":
                win_by_count = int(final_yes == 1.0)
            elif side == "BUY_NO":
                win_by_count = int(final_yes == 0.0)

        counterfactual_pnl = _counterfactual_pnl(
            side, opp.dec_entry_price, final_yes, opp.dec_shares
        )
        counterfactual_pnl_best = _counterfactual_pnl(
            side, opp.best_entry_price, final_yes, opp.dec_shares
        )

        rows.append({
            "candidate_id": candidate_id,
            "condition_id": cid,
            "market_id": opp.market_id,
            "side": side,
            "event_date": event_date,
            "bracket": opp.bracket,
            "city": opp.city,
            "city_pool": opp.city_pool,
            "icao": opp.icao,
            "unit": opp.unit,
            "forecast_source": opp.forecast_source,
            "forecast_max_f": opp.dec_forecast_max_f,
            "forecast_max_native": opp.dec_forecast_max_native,
            "forecast_peak_hour_local": (
                int(opp.dec_forecast_peak_hour_local)
                if opp.dec_forecast_peak_hour_local is not None
                else None
            ),
            "forecast_peak_time_local": opp.dec_forecast_peak_time_local,
            "forecast_peak_hour_utc": (
                int(opp.dec_forecast_peak_hour_utc)
                if opp.dec_forecast_peak_hour_utc is not None
                else None
            ),
            "forecast_peak_time_utc": opp.dec_forecast_peak_time_utc,
            "forecast_hourly_count": (
                int(opp.dec_forecast_hourly_count)
                if opp.dec_forecast_hourly_count is not None
                else None
            ),
            "forecast_values_hash": opp.dec_forecast_values_hash,
            "forecast_peak_source": opp.dec_forecast_peak_source,
            "forecast_timezone": opp.dec_forecast_timezone,
            "forecast_utc_offset_seconds": (
                int(opp.dec_forecast_utc_offset_seconds)
                if opp.dec_forecast_utc_offset_seconds is not None
                else None
            ),
            "forecast_peak_delta_hours_local": opp.dec_forecast_peak_delta_hours_local,
            "forecast_max_in_bracket": (
                int(opp.dec_forecast_max_in_bracket)
                if opp.dec_forecast_max_in_bracket is not None
                else None
            ),
            "forecast_max_above_bracket_f": opp.dec_forecast_max_above_bracket_f,
            "forecast_max_below_bracket_f": opp.dec_forecast_max_below_bracket_f,
            "forecast_max_above_metar_max_f": opp.dec_forecast_max_above_metar_max_f,
            "model_version": opp.model_version,
            "time_bucket": opp.time_bucket,
            "window": opp.window,
            "decision_window_label": window_label,
            "decision_hours_to_settle": opp.dec_hts,
            "decision_snapshot_ts_utc": opp.dec_ts,
            "decision_window_missing": decision_missing,
            "model_p_yes": opp.dec_model_p_yes,
            "market_yes_price": opp.dec_market_yes_price,
            "edge": opp.dec_edge,
            "abs_edge": opp.dec_abs_edge,
            "decision_entry_price": opp.dec_entry_price,
            "yes_spread": opp.dec_yes_spread,
            "no_spread": opp.dec_no_spread,
            "yes_depth_ask_5c": opp.dec_yes_depth_ask_5c,
            "no_depth_ask_5c": opp.dec_no_depth_ask_5c,
            "first_seen_ts_utc": opp.first_seen_ts_utc,
            "last_seen_ts_utc": opp.last_seen_ts_utc,
            "n_snapshots": opp.n_snapshots,
            "edge_max": opp.edge_max,
            "edge_mean": opp.edge_mean,
            "best_entry_price": opp.best_entry_price,
            "seen": 1,
            "eligible": opp.eligible,
            "paper_ordered": paper_ordered,
            "live_filled": live_filled,
            "paper_order_id": po.get("order_id") if po else None,
            "paper_entry_price": paper_entry_price,
            "paper_shares": paper_shares,
            "paper_snapshot_ts_utc": po.get("snapshot_ts_utc") if po else None,
            "fill_id": lf.get("fill_id") if lf else None,
            "live_fill_price": live_fill_price,
            "live_fill_qty": live_fill_qty,
            "live_pnl_usd": live_pnl_usd,
            "slippage_vs_paper": slippage,
            "settlement_status": settlement_status,
            "final_yes": final_yes,
            "bracket_hit": bracket_hit,
            "win_by_count": win_by_count,
            "counterfactual_pnl": counterfactual_pnl,
            "counterfactual_pnl_best": counterfactual_pnl_best,
            "fact_built_at_utc": now_utc,
        })

    # orphan detection: paper orders / live fills with no matching opportunity
    orphan_paper = [k for k in paper_orders if k not in matched_paper]
    orphan_fills = [k for k in live_fills if k not in matched_fills]
    for cid, side, event_date in orphan_paper[:20]:
        alerts.append(
            f"ORPHAN_PAPER_ORDER no snapshot universe match: condition_id={cid} side={side} date={event_date}"
        )
    for cid, side, td in orphan_fills[:20]:
        alerts.append(f"ORPHAN_LIVE_FILL no snapshot universe match: condition_id={cid} side={side} date={td}")

    stats = {
        "n_files": n_files,
        "n_dropped_no_cid": n_dropped,
        "forecast_cache_files_seen": forecast_index.files_seen,
        "forecast_cache_files_loaded": forecast_index.files_loaded,
        "forecast_cache_city_dates": forecast_index.derived_city_dates,
        "forecast_cache_min_date": forecast_index.min_date,
        "forecast_cache_max_date": forecast_index.max_date,
        "n_records_forecast_enriched": n_forecast_enriched,
        "n_opportunities": len(rows),
        "n_paper_order_rows": paper_stats["raw_rows"],
        "n_paper_orders": len(paper_orders),
        "n_paper_order_duplicate_rows": paper_stats["duplicate_extra_rows"],
        "n_paper_orders_dropped_no_key": paper_stats["dropped_no_key"],
        "n_live_fill_rows": sum(int(v.get("fill_count") or 0) for v in live_fills.values()),
        "n_live_fills": len(live_fills),
        "n_live_fill_duplicate_rows": sum(
            max(int(v.get("fill_count") or 0) - 1, 0) for v in live_fills.values()
        ),
        "orphan_paper": len(orphan_paper),
        "orphan_fills": len(orphan_fills),
    }
    return rows, alerts, stats


def write_db(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DROP TABLE IF EXISTS fact_signal_candidates")
    conn.execute(CANDIDATE_DDL)
    if not rows:
        conn.commit()
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join(cols)
    conn.executemany(
        f"INSERT INTO fact_signal_candidates ({col_list}) VALUES ({placeholders})",
        [[r[c] for c in cols] for r in rows],
    )
    conn.commit()


def write_parquet(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)


def print_summary(rows: list[dict], alerts: list[str], stats: dict) -> None:
    total = len(rows)
    print("\n=== fact_signal_candidates build summary ===")
    print(f"snapshot files       : {stats['n_files']}")
    print(f"dropped (no cid)     : {stats['n_dropped_no_cid']}")
    print(f"opportunities (rows) : {total}")
    if total == 0:
        return
    paper = sum(r["paper_ordered"] for r in rows)
    live = sum(r["live_filled"] for r in rows)
    missing = sum(r["decision_window_missing"] for r in rows)
    eligible = sum(1 for r in rows if r["eligible"] == 1)
    settled = sum(1 for r in rows if r["final_yes"] is not None)
    missed_fill = sum(1 for r in rows if r["paper_ordered"] == 1 and r["live_filled"] == 0)
    forecast_peak = sum(1 for r in rows if r.get("forecast_peak_hour_local") is not None)
    forecast_hash = sum(1 for r in rows if r.get("forecast_values_hash"))
    print(f"paper_ordered        : {paper}")
    print(f"live_filled          : {live}")
    print(f"missed_fill          : {missed_fill}")
    print(f"eligible=1           : {eligible}")
    print(f"decision_window_miss : {missing} ({missing/total*100:.1f}%)")
    print(f"settled              : {settled} ({settled/total*100:.1f}%)")
    print(
        f"forecast peak fields : {forecast_peak} peak_hour / {forecast_hash} hash "
        f"({forecast_peak/total*100:.1f}% / {forecast_hash/total*100:.1f}%)"
    )
    print(
        f"forecast cache       : {stats['forecast_cache_files_loaded']}/"
        f"{stats['forecast_cache_files_seen']} files loaded, "
        f"{stats['forecast_cache_city_dates']} city-dates "
        f"({stats['forecast_cache_min_date']}..{stats['forecast_cache_max_date']}), "
        f"{stats['n_records_forecast_enriched']} snapshot records enriched"
    )
    print(
        f"paper orders total   : {stats['n_paper_order_rows']} rows / "
        f"{stats['n_paper_orders']} keys "
        f"(collapsed {stats['n_paper_order_duplicate_rows']}, orphan {stats['orphan_paper']})"
    )
    if stats["n_paper_orders_dropped_no_key"]:
        print(f"paper orders dropped : {stats['n_paper_orders_dropped_no_key']} (missing key)")
    print(
        f"live fills total     : {stats['n_live_fill_rows']} fills / "
        f"{stats['n_live_fills']} keys "
        f"(collapsed {stats['n_live_fill_duplicate_rows']}, orphan {stats['orphan_fills']})"
    )
    if alerts:
        print(f"\nalerts ({len(alerts)}):")
        for a in alerts[:20]:
            print(f"  [ALERT] {a}")
        if len(alerts) > 20:
            print(f"  ... {len(alerts)-20} more")
    else:
        print("\nalerts: none")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build weather fact_signal_candidates table")
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--parquet-path", default=str(PARQUET_PATH))
    ap.add_argument("--no-parquet", action="store_true",
                    help="Write the DB table only; skip parquet export")
    ap.add_argument("--snapshot-dir", default=str(SNAPSHOT_DIR))
    ap.add_argument("--paper-orders", default=str(PAPER_ORDERS_PATH))
    ap.add_argument("--forecast-cache-root", default=str(FORECAST_CACHE_ROOT))
    ap.add_argument("--decision-hts-min", type=float, default=22.0)
    ap.add_argument("--decision-hts-max", type=float, default=24.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute rows but do not write to DB or parquet")
    args = ap.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        rows, alerts, stats = build(
            conn,
            snapshot_dir=Path(args.snapshot_dir),
            paper_orders_path=Path(args.paper_orders),
            hts_min=args.decision_hts_min,
            hts_max=args.decision_hts_max,
            forecast_cache_root=Path(args.forecast_cache_root),
        )
        print_summary(rows, alerts, stats)
        if args.dry_run:
            print("\n[dry-run] skipping write")
            return
        write_db(conn, rows)
        print(f"\nfact_signal_candidates written to DB: {db_path}")
        if args.no_parquet:
            print("fact_signal_candidates parquet export skipped (--no-parquet)")
        else:
            write_parquet(rows, Path(args.parquet_path))
            print(f"fact_signal_candidates written to parquet: {args.parquet_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
