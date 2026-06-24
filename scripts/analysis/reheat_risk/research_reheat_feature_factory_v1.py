#!/usr/bin/env python3
"""Build the shared reheat-risk intraday feature layer.

This is a feature factory, not a strategy optimizer.  It materializes one
city/date/hour/bracket/outcome fact layer from canonical weather.db tables plus
time-aligned raw orderbook snapshots.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
OBSERVED_DETAIL = (
    ROOT
    / "docs/analysis/2026-06/generated/m3_observed_max_v6_h10_21_iem_patch_20260617"
    / "m3_observed_max_residual_detail.csv"
)
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
EXT_CACHE_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v7_20260617"
FORECAST_PEAK_BACKFILL = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-reheat-feature-factory-v1.md"

DEFAULT_START = "2026-05-19"
DEFAULT_END = "2026-06-17"
DEFAULT_HOURS = tuple(range(10, 22))
SKY_CODE = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}


@dataclass(frozen=True)
class Bracket:
    raw: str
    low: float | None
    high: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--orderbook-dir", default=str(ORDERBOOK_DIR))
    parser.add_argument("--observed-detail", default=str(OBSERVED_DETAIL))
    parser.add_argument("--station-summary", default=str(STATION_SUMMARY))
    parser.add_argument("--ext-cache-dir", default=str(EXT_CACHE_DIR))
    parser.add_argument("--forecast-peak-backfill", default=str(FORECAST_PEAK_BACKFILL))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    parser.add_argument("--decision-hours", default=",".join(str(h) for h in DEFAULT_HOURS))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
        "fact_trades_by_class": query_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "fact_trades_by_settlement_status": query_rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "fact_signal_candidate_coverage": query_rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "clob_order_fill_join": query_rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def parse_bracket(value: Any) -> Bracket | None:
    if value is None:
        return None
    raw = str(value).replace("°", "").strip()
    if not raw:
        return None
    if raw.endswith("+"):
        try:
            return Bracket(raw=raw, low=float(raw[:-1]), high=None)
        except ValueError:
            return None
    if "-" in raw:
        left, right = raw.split("-", 1)
        try:
            return Bracket(raw=raw, low=float(left), high=float(right))
        except ValueError:
            return None
    try:
        val = float(raw)
    except ValueError:
        return None
    return Bracket(raw=raw, low=val, high=val)


def bracket_contains(bracket: Bracket, value: float) -> bool:
    if bracket.low is not None and value < bracket.low:
        return False
    if bracket.high is not None and value > bracket.high:
        return False
    return True


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def best_level(raw: object, side: str) -> tuple[float | None, float | None]:
    if not isinstance(raw, dict):
        return None, None
    levels = raw.get(side)
    if not isinstance(levels, list) or not levels:
        return None, None
    parsed: list[tuple[float, float | None]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level.get("price"))
        except (TypeError, ValueError):
            continue
        try:
            size = float(level.get("size")) if level.get("size") is not None else None
        except (TypeError, ValueError):
            size = None
        parsed.append((price, size))
    if not parsed:
        return None, None
    if side == "asks":
        return min(parsed, key=lambda item: item[0])
    return max(parsed, key=lambda item: item[0])


def summary_num(record: dict[str, Any], key: str) -> float | None:
    summary = record.get("summary")
    if not isinstance(summary, dict):
        return None
    value = summary.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_observed(path: Path, start: str, end: str, hours: set[int]) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["target_date"] = df["target_date"].astype(str)
    df = df[df["target_date"].between(start, end) & df["decision_hour_local"].isin(hours)].copy()
    df["decline_from_max_c"] = df["running_max_c"] - df["current_temp_c"]
    df["decline_from_max_f"] = df["running_max_f"] - df["current_temp_f"]
    keep = [
        "city",
        "icao",
        "timezone",
        "target_date",
        "decision_hour_local",
        "obs_count_day",
        "obs_count_to_decision",
        "decision_last_obs_utc",
        "current_temp_c",
        "current_temp_f",
        "running_max_c",
        "running_max_f",
        "decline_from_max_c",
        "decline_from_max_f",
        "final_max_c",
        "final_max_f",
    ]
    return df[keep].drop_duplicates(["city", "target_date", "decision_hour_local"]).copy()


def load_settlements(conn: sqlite3.Connection, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = query_rows(
        conn,
        "SELECT city, target_date, bracket, unit, final_price, settlement_status, source_system "
        "FROM settlement_outcomes WHERE target_date BETWEEN ? AND ?",
        (start, end),
    )
    outcomes = pd.DataFrame(rows)
    if outcomes.empty:
        return outcomes, pd.DataFrame()
    outcomes["target_date"] = outcomes["target_date"].astype(str)
    outcomes["final_price"] = pd.to_numeric(outcomes["final_price"], errors="coerce")
    winners = outcomes[outcomes["final_price"].ge(0.99)].copy()
    winner_counts = winners.groupby(["city", "target_date"]).size().rename("winner_count").reset_index()
    winners = winners.merge(winner_counts, on=["city", "target_date"], how="left")
    winners = winners[winners["winner_count"].eq(1)].copy()
    winners = winners.rename(columns={"bracket": "final_winning_bracket", "final_price": "winner_final_price"})
    return outcomes, winners[["city", "target_date", "final_winning_bracket", "winner_final_price", "unit", "settlement_status", "source_system"]]


def candidate_outcome(side: Any) -> str | None:
    text = str(side or "").lower()
    if "yes" in text:
        return "yes"
    if "no" in text:
        return "no"
    return None


def load_forecasts(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    rows = query_rows(
        conn,
        "SELECT city, event_date AS target_date, bracket, side, decision_snapshot_ts_utc, "
        "forecast_source, forecast_max_f, forecast_max_native, forecast_peak_hour_local, "
        "forecast_peak_time_local, forecast_peak_hour_utc, forecast_peak_time_utc, "
        "forecast_hourly_count, forecast_values_hash, forecast_peak_source, forecast_timezone, "
        "forecast_utc_offset_seconds, forecast_peak_delta_hours_local "
        "FROM fact_signal_candidates WHERE event_date BETWEEN ? AND ?",
        (start, end),
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["target_date"] = df["target_date"].astype(str)
    df["outcome"] = df["side"].apply(candidate_outcome)
    df["forecast_snapshot_ts"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    return df.dropna(subset=["outcome"]).copy()


def load_forecast_peak_backfill(path: Path, start: str, end: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df["target_date"] = df["target_date"].astype(str)
    return df[df["target_date"].between(start, end)].copy()


def load_station_summary(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["city"]): item for item in data.get("stations", []) if "city" in item}


def ext_cache_path(ext_dir: Path, station: dict[str, Any], start: str, end: str) -> Path:
    return ext_dir / f"iem_ext_{str(station.get('icao')).upper()}_{start}_{end}.csv"


def ext_cache_paths(ext_dir: Path, station: dict[str, Any], start: str, end: str) -> list[Path]:
    exact = ext_cache_path(ext_dir, station, start, end)
    if exact.exists():
        return [exact]
    icao = str(station.get("icao") or "").upper()
    if not icao:
        return []
    paths: list[Path] = []
    prefix = f"iem_ext_{icao}_"
    for path in sorted(ext_dir.glob(f"{prefix}*.csv")):
        stem = path.stem
        try:
            raw_range = stem.removeprefix(prefix)
            file_start, file_end = raw_range.rsplit("_", 1)
        except ValueError:
            continue
        if file_end >= start and file_start <= end:
            paths.append(path)
    return paths


def load_ext_cache(ext_dir: Path, station_summary: dict[str, dict[str, Any]], start: str, end: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for city, station in station_summary.items():
        paths = ext_cache_paths(ext_dir, station, start, end)
        if not paths:
            continue
        frames = [pd.read_csv(path, na_values=["M"], low_memory=False) for path in paths]
        df = pd.concat(frames, ignore_index=True)
        if "valid" not in df:
            continue
        df["ts"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values("ts").copy()
        start_ts = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=1)
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        df = df[df["ts"].between(start_ts, end_ts)].drop_duplicates("ts").copy()
        for col in ["tmpf", "dwpf", "relh", "sknt"]:
            df[col] = pd.to_numeric(df.get(col), errors="coerce")
        df["sky"] = df.get("skyc1").map(SKY_CODE) if "skyc1" in df else np.nan
        out[city] = {
            "ts": df["ts"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]"),
            "tmpf": df["tmpf"].to_numpy(dtype=float),
            "dwpf": df["dwpf"].to_numpy(dtype=float),
            "relh": df["relh"].to_numpy(dtype=float),
            "sknt": df["sknt"].to_numpy(dtype=float),
            "sky": df["sky"].to_numpy(dtype=float),
        }
    return out


def asof_value(ts: np.ndarray, vals: np.ndarray, target: np.datetime64, tol_min: float) -> float:
    idx = np.searchsorted(ts, target, side="right")
    if idx == 0:
        return float("nan")
    age = (target - ts[idx - 1]) / np.timedelta64(1, "m")
    if age > tol_min:
        return float("nan")
    return float(vals[idx - 1])


def minutes_since_running_max(city_ext: dict[str, Any], target: np.datetime64, running_max_f: float) -> float:
    ts = city_ext["ts"]
    tmpf = city_ext["tmpf"]
    idx = np.searchsorted(ts, target, side="right")
    if idx == 0 or not math.isfinite(running_max_f):
        return float("nan")
    prior_ts = ts[:idx]
    prior_tmpf = tmpf[:idx]
    mask = np.isfinite(prior_tmpf) & (prior_tmpf >= running_max_f - 0.05)
    if not mask.any():
        return float("nan")
    last_ts = prior_ts[np.where(mask)[0][-1]]
    return float((target - last_ts) / np.timedelta64(1, "m"))


def iter_orderbook(orderbook_dir: Path, observed: pd.DataFrame, start: str, end: str, hours: set[int]) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tz_by_city = observed.dropna(subset=["timezone"]).drop_duplicates("city").set_index("city")["timezone"].to_dict()
    state_keys = set(zip(observed["city"], observed["target_date"], observed["decision_hour_local"]))
    files_seen = records_seen = records_ok = records_kept = 0
    records_missing_tz = records_wrong_hour = records_no_observed = records_no_quote = 0
    for date_dir in sorted(orderbook_dir.iterdir()):
        if not date_dir.is_dir() or not (start <= date_dir.name <= end):
            continue
        for path in sorted(date_dir.glob("*.jsonl.gz")):
            files_seen += 1
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    records_seen += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("status") != "ok":
                        continue
                    records_ok += 1
                    city = str(record.get("city") or "")
                    target_date = str(record.get("event_date") or "")
                    tz_name = tz_by_city.get(city)
                    if not tz_name:
                        records_missing_tz += 1
                        continue
                    ts_raw = str(record.get("snapshot_ts_utc") or "")
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    local_ts = ts.astimezone(ZoneInfo(tz_name))
                    decision_hour = int(local_ts.hour)
                    local_date = local_ts.date().isoformat()
                    if local_date != target_date or decision_hour not in hours:
                        records_wrong_hour += 1
                        continue
                    if (city, target_date, decision_hour) not in state_keys:
                        records_no_observed += 1
                        continue
                    bracket = parse_bracket(record.get("bracket"))
                    outcome = str(record.get("outcome") or "").lower()
                    if bracket is None or outcome not in {"yes", "no"}:
                        continue
                    ask, ask_size = best_level(record.get("raw"), "asks")
                    bid, bid_size = best_level(record.get("raw"), "bids")
                    if ask is None and bid is None:
                        records_no_quote += 1
                        continue
                    rows.append(
                        {
                            "orderbook_file": str(path.relative_to(ROOT)),
                            "decision_snapshot_ts_utc": ts.astimezone(timezone.utc).isoformat(),
                            "decision_hour_local": decision_hour,
                            "city": city,
                            "target_date": target_date,
                            "bracket": bracket.raw,
                            "bracket_low": bracket.low,
                            "bracket_high": bracket.high,
                            "outcome": outcome,
                            "quote_best_ask": ask,
                            "quote_best_ask_size": ask_size,
                            "quote_best_bid": bid,
                            "quote_best_bid_size": bid_size,
                            "quote_spread": summary_num(record, "spread"),
                            "quote_depth_ask_5c": summary_num(record, "depth_ask_5c"),
                            "quote_depth_bid_5c": summary_num(record, "depth_bid_5c"),
                            "condition_id": record.get("condition_id"),
                            "market_id": record.get("market_id"),
                            "token_id": record.get("token_id"),
                        }
                    )
                    records_kept += 1
    raw = pd.DataFrame(rows)
    meta = {
        "orderbook_files_seen": files_seen,
        "orderbook_records_seen": records_seen,
        "orderbook_records_ok": records_ok,
        "orderbook_records_kept_before_dedupe": records_kept,
        "records_missing_timezone": records_missing_tz,
        "records_wrong_local_date_or_hour": records_wrong_hour,
        "records_without_observed_state": records_no_observed,
        "records_without_quote": records_no_quote,
    }
    if raw.empty:
        meta["quote_rows_after_hourly_dedupe"] = 0
        return raw, meta
    raw["snapshot_sort"] = pd.to_datetime(raw["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    raw = raw.sort_values("snapshot_sort")
    raw = raw.groupby(["city", "target_date", "decision_hour_local", "bracket", "outcome"], as_index=False).tail(1)
    raw = raw.drop(columns=["snapshot_sort"]).reset_index(drop=True)
    meta["quote_rows_after_hourly_dedupe"] = int(len(raw))
    return raw, meta


def tail_distance(row: pd.Series) -> int | None:
    low = row.get("bracket_low")
    if pd.isna(low):
        return None
    running = float(row["running_native"])
    if float(low) <= running:
        return None
    if str(row["unit"]).upper() == "F":
        return int(math.ceil((float(low) - running) / 2.0))
    return int(round(float(low) - running))


def add_state_siblings(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["is_f"] = out["unit"].str.upper().eq("F")
    out["current_native"] = np.where(out["is_f"], out["current_temp_f"], out["current_temp_c"])
    out["running_native"] = np.where(out["is_f"], out["running_max_f"], out["running_max_c"])
    out["decline_native"] = out["running_native"] - out["current_native"]
    out["running_value"] = out["running_native"].apply(lambda x: round_half_up(x) if pd.notna(x) else np.nan)

    yes = out[out["outcome"].eq("yes")].copy()
    yes["contains_running"] = yes.apply(
        lambda r: bracket_contains(Bracket(str(r["bracket"]), r["bracket_low"], r["bracket_high"]), float(r["running_value"]))
        if pd.notna(r["running_value"])
        else False,
        axis=1,
    )
    current = yes[yes["contains_running"]].copy()
    current["specificity"] = current["bracket_high"].notna().astype(int)
    state_cols = ["city", "target_date", "decision_hour_local"]
    current = (
        current.sort_values(state_cols + ["specificity", "quote_best_ask"], ascending=[True, True, True, False, True])
        .drop_duplicates(state_cols)
        [state_cols + ["bracket", "quote_best_ask", "quote_best_ask_size", "quote_best_bid", "quote_spread"]]
        .rename(
            columns={
                "bracket": "current_bracket",
                "quote_best_ask": "current_yes_ask",
                "quote_best_ask_size": "current_yes_ask_size",
                "quote_best_bid": "current_yes_bid",
                "quote_spread": "current_yes_spread",
            }
        )
    )

    no = out[out["outcome"].eq("no")].copy()
    no["distance"] = no.apply(tail_distance, axis=1)
    sibling_frames = []
    for dist, prefix in [(1, "d1_no"), (2, "d2_no")]:
        sub = (
            no[no["distance"].eq(dist)]
            .sort_values(state_cols + ["quote_best_ask"], ascending=[True, True, True, False])
            .drop_duplicates(state_cols)
            [state_cols + ["bracket", "quote_best_ask", "quote_best_ask_size", "quote_best_bid", "quote_spread"]]
            .rename(
                columns={
                    "bracket": f"{prefix}_bracket",
                    "quote_best_ask": f"{prefix}_ask",
                    "quote_best_ask_size": f"{prefix}_ask_size",
                    "quote_best_bid": f"{prefix}_bid",
                    "quote_spread": f"{prefix}_spread",
                }
            )
        )
        sibling_frames.append(sub)

    target_yes = (
        yes[state_cols + ["bracket", "quote_best_ask", "quote_best_ask_size", "quote_best_bid", "quote_spread"]]
        .rename(
            columns={
                "quote_best_ask": "target_yes_ask",
                "quote_best_ask_size": "target_yes_ask_size",
                "quote_best_bid": "target_yes_bid",
                "quote_spread": "target_yes_spread",
            }
        )
    )

    out = out.merge(current, on=state_cols, how="left")
    for frame in sibling_frames:
        out = out.merge(frame, on=state_cols, how="left")
    out = out.merge(target_yes, on=state_cols + ["bracket"], how="left")
    return out


def add_truth(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    has_winner = out["final_winning_bracket"].notna()
    out["current_bracket_held"] = np.where(has_winner, out["final_winning_bracket"].astype(str).eq(out["current_bracket"].astype(str)), np.nan)
    out["d1_hit"] = np.where(has_winner, out["final_winning_bracket"].astype(str).eq(out["d1_no_bracket"].astype(str)), np.nan)
    out["d2_hit"] = np.where(has_winner, out["final_winning_bracket"].astype(str).eq(out["d2_no_bracket"].astype(str)), np.nan)
    out["target_hit"] = np.where(has_winner, out["final_winning_bracket"].astype(str).eq(out["bracket"].astype(str)), np.nan)
    out["skip_over_d1"] = np.where(
        has_winner,
        (~pd.Series(out["current_bracket_held"]).fillna(False).astype(bool))
        & (~pd.Series(out["d1_hit"]).fillna(False).astype(bool))
        & out["d1_no_bracket"].notna(),
        np.nan,
    )
    return out


def add_weather_features(rows: pd.DataFrame, ext: dict[str, dict[str, Any]]) -> pd.DataFrame:
    weather_feature_cols = [
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
    ]
    empty_features = {col: np.nan for col in weather_feature_cols}
    feature_rows: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        city_ext = ext.get(row.city)
        ts_raw = pd.to_datetime(row.decision_snapshot_ts_utc, utc=True, errors="coerce")
        if city_ext is None or pd.isna(ts_raw):
            feature_rows.append(empty_features.copy())
            continue
        target = np.datetime64(ts_raw.tz_convert("UTC").tz_localize(None).to_datetime64(), "ns")
        ts = city_ext["ts"]
        tmpf_now = asof_value(ts, city_ext["tmpf"], target, 90)
        dwpf_now = asof_value(ts, city_ext["dwpf"], target, 90)
        relh_now = asof_value(ts, city_ext["relh"], target, 90)
        sknt_now = asof_value(ts, city_ext["sknt"], target, 90)
        sky_now = asof_value(ts, city_ext["sky"], target, 90)
        tmpf_1h = asof_value(ts, city_ext["tmpf"], target - np.timedelta64(1, "h"), 90)
        tmpf_3h = asof_value(ts, city_ext["tmpf"], target - np.timedelta64(3, "h"), 90)
        feature = empty_features.copy()
        feature.update(
            {
                "tmpf_now": tmpf_now,
                "dwpf_now": dwpf_now,
                "dewpoint_depression_f": tmpf_now - dwpf_now if math.isfinite(tmpf_now) and math.isfinite(dwpf_now) else np.nan,
                "relative_humidity_pct": relh_now,
                "wind_speed_kt": sknt_now,
                "sky_cover_code": sky_now,
                "temp_trend_1h_f": tmpf_now - tmpf_1h if math.isfinite(tmpf_now) and math.isfinite(tmpf_1h) else np.nan,
                "temp_trend_3h_f": tmpf_now - tmpf_3h if math.isfinite(tmpf_now) and math.isfinite(tmpf_3h) else np.nan,
                "minutes_since_running_max": minutes_since_running_max(city_ext, target, float(row.running_max_f)),
            }
        )
        feature_rows.append(feature)
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(feature_rows).reset_index(drop=True)], axis=1)


def add_forecasts(rows: pd.DataFrame, forecasts: pd.DataFrame) -> pd.DataFrame:
    forecast_cols = [
        "forecast_source",
        "forecast_max_f",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_time_local",
        "forecast_peak_hour_utc",
        "forecast_peak_time_utc",
        "forecast_hourly_count",
        "forecast_values_hash",
        "forecast_peak_source",
        "forecast_timezone",
        "forecast_utc_offset_seconds",
        "forecast_peak_delta_hours_local",
    ]
    if forecasts.empty:
        out = rows.copy()
        for col in forecast_cols:
            out[col] = np.nan
        out["forecast_join_status"] = "no_fact_signal_candidates"
        return out

    f = forecasts.copy()
    f["forecast_sort_ts"] = pd.to_datetime(f["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    f = f.sort_values("forecast_sort_ts")
    grouped = {
        key: frame.reset_index(drop=True)
        for key, frame in f.groupby(["city", "target_date", "bracket", "outcome"], dropna=False)
    }
    filled: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        key = (row.city, row.target_date, row.bracket, row.outcome)
        frame = grouped.get(key)
        if frame is None or frame.empty:
            filled.append({"forecast_join_status": "no_fact_candidate"})
            continue
        row_ts = pd.to_datetime(row.decision_snapshot_ts_utc, utc=True, errors="coerce")
        usable = frame
        if pd.notna(row_ts) and frame["forecast_sort_ts"].notna().any():
            asof = frame[frame["forecast_sort_ts"].le(row_ts)]
            if not asof.empty:
                usable = asof
        selected = usable.iloc[-1]
        item = {col: selected.get(col) for col in forecast_cols}
        item["forecast_join_status"] = "matched_asof" if pd.notna(selected.get("forecast_sort_ts")) else "matched_no_snapshot_ts"
        filled.append(item)
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(filled).reset_index(drop=True)], axis=1)


def add_forecast_peak_backfill(rows: pd.DataFrame, peak: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    peak_cols = [
        "gfs_forecast_model_name",
        "gfs_forecast_max_f",
        "gfs_forecast_max_native",
        "gfs_forecast_peak_hour_local",
        "gfs_forecast_peak_time_local",
        "gfs_forecast_peak_hour_utc",
        "gfs_forecast_peak_time_utc",
        "gfs_forecast_hourly_count",
        "gfs_forecast_values_hash",
        "gfs_forecast_timezone",
        "gfs_forecast_utc_offset_seconds",
        "gfs_forecast_cache_status",
        "ecmwf_forecast_model_name",
        "ecmwf_forecast_max_f",
        "ecmwf_forecast_max_native",
        "ecmwf_forecast_peak_hour_local",
        "ecmwf_forecast_peak_time_local",
        "ecmwf_forecast_peak_hour_utc",
        "ecmwf_forecast_peak_time_utc",
        "ecmwf_forecast_hourly_count",
        "ecmwf_forecast_values_hash",
        "ecmwf_forecast_timezone",
        "ecmwf_forecast_utc_offset_seconds",
        "ecmwf_forecast_cache_status",
        "gfs_forecast_peak_present",
        "ecmwf_forecast_peak_present",
        "forecast_peak_models_agree_le_1h",
        "forecast_peak_hour_spread",
    ]
    if peak.empty:
        for col in peak_cols:
            out[col] = np.nan
        out["forecast_peak_backfill_join_status"] = "missing_backfill_file"
        out["forecast_clock_source"] = np.where(out["forecast_peak_hour_local"].notna(), "native_fact", "missing")
        return out

    keep_cols = ["city", "target_date"] + [col for col in peak_cols if col in peak.columns]
    deduped = peak[keep_cols].drop_duplicates(["city", "target_date"]).copy()
    out = out.merge(deduped, on=["city", "target_date"], how="left")
    out["forecast_peak_backfill_join_status"] = np.where(
        out["gfs_forecast_peak_hour_local"].notna() | out["ecmwf_forecast_peak_hour_local"].notna(),
        "matched_city_date",
        "no_backfill_city_date",
    )

    out["gfs_forecast_peak_delta_hours_local"] = out["decision_hour_local"] - pd.to_numeric(
        out["gfs_forecast_peak_hour_local"], errors="coerce"
    )
    out["ecmwf_forecast_peak_delta_hours_local"] = out["decision_hour_local"] - pd.to_numeric(
        out["ecmwf_forecast_peak_hour_local"], errors="coerce"
    )
    out["gfs_forecast_gap_to_running_native"] = pd.to_numeric(out["gfs_forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )
    out["ecmwf_forecast_gap_to_running_native"] = pd.to_numeric(out["ecmwf_forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )

    native_present = out["forecast_peak_hour_local"].notna()
    gfs_present = out["gfs_forecast_peak_hour_local"].notna()
    out["forecast_clock_source"] = np.select(
        [native_present, gfs_present],
        ["native_fact", "backfill_gfs_primary"],
        default="missing",
    )
    for col in [
        "forecast_source",
        "forecast_peak_time_local",
        "forecast_peak_time_utc",
        "forecast_values_hash",
        "forecast_peak_source",
        "forecast_timezone",
    ]:
        out[col] = out[col].astype("object")
    fill_mask = ~native_present & gfs_present
    out.loc[fill_mask, "forecast_source"] = out.loc[fill_mask, "gfs_forecast_model_name"]
    out.loc[fill_mask, "forecast_max_f"] = out.loc[fill_mask, "gfs_forecast_max_f"]
    out.loc[fill_mask, "forecast_max_native"] = out.loc[fill_mask, "gfs_forecast_max_native"]
    out.loc[fill_mask, "forecast_peak_hour_local"] = out.loc[fill_mask, "gfs_forecast_peak_hour_local"]
    out.loc[fill_mask, "forecast_peak_time_local"] = out.loc[fill_mask, "gfs_forecast_peak_time_local"]
    out.loc[fill_mask, "forecast_peak_hour_utc"] = out.loc[fill_mask, "gfs_forecast_peak_hour_utc"]
    out.loc[fill_mask, "forecast_peak_time_utc"] = out.loc[fill_mask, "gfs_forecast_peak_time_utc"]
    out.loc[fill_mask, "forecast_hourly_count"] = out.loc[fill_mask, "gfs_forecast_hourly_count"]
    out.loc[fill_mask, "forecast_values_hash"] = out.loc[fill_mask, "gfs_forecast_values_hash"]
    out.loc[fill_mask, "forecast_peak_source"] = "backfill_gfs_primary"
    out.loc[fill_mask, "forecast_timezone"] = out.loc[fill_mask, "gfs_forecast_timezone"]
    out.loc[fill_mask, "forecast_utc_offset_seconds"] = out.loc[fill_mask, "gfs_forecast_utc_offset_seconds"]
    out.loc[fill_mask, "forecast_peak_delta_hours_local"] = out.loc[fill_mask, "gfs_forecast_peak_delta_hours_local"]
    return out


def coverage_by_state(rows: pd.DataFrame) -> pd.DataFrame:
    checks = {
        "has_current_temp": "current_temp_c",
        "has_running_max": "running_max_c",
        "has_minutes_since_max": "minutes_since_running_max",
        "has_forecast_peak_hour": "forecast_peak_hour_local",
        "has_forecast_hash": "forecast_values_hash",
        "has_dewpoint": "dwpf_now",
        "has_rh": "relative_humidity_pct",
        "has_wind": "wind_speed_kt",
        "has_sky": "sky_cover_code",
        "has_temp_trend": "temp_trend_1h_f",
        "has_current_yes_quote": "current_yes_ask",
        "has_d1_no_quote": "d1_no_ask",
        "has_d2_no_quote": "d2_no_ask",
        "has_any_target_yes_quote": "target_yes_ask",
        "has_final_winner": "final_winning_bracket",
    }
    state_cols = ["target_date", "city", "decision_hour_local"]
    state = rows.groupby(state_cols, as_index=False).agg(
        quote_rows=("bracket", "size"),
        bracket_count=("bracket", "nunique"),
        outcome_count=("outcome", "nunique"),
    )
    for out_col, source_col in checks.items():
        flag = rows.groupby(state_cols)[source_col].apply(lambda s: bool(s.notna().any())).reset_index(name=out_col)
        state = state.merge(flag, on=state_cols, how="left")
    missing_cols = [col for col in checks if col.startswith("has_")]
    state["complete_core"] = state[
        [
            "has_current_temp",
            "has_running_max",
            "has_minutes_since_max",
            "has_current_yes_quote",
            "has_d1_no_quote",
            "has_final_winner",
        ]
    ].all(axis=1)
    state["missing_fields"] = state[missing_cols].apply(
        lambda r: ",".join(col.removeprefix("has_") for col, ok in r.items() if not bool(ok)),
        axis=1,
    )
    return state.sort_values(state_cols).reset_index(drop=True)


def summarize_coverage(rows: pd.DataFrame, state: pd.DataFrame, orderbook_meta: dict[str, Any], self_check: dict[str, Any]) -> dict[str, Any]:
    field_cols = [
        "current_temp_c",
        "running_max_c",
        "decline_from_max_c",
        "minutes_since_running_max",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "forecast_values_hash",
        "gfs_forecast_peak_hour_local",
        "gfs_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_peak_hour_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_models_agree_le_1h",
        "forecast_peak_hour_spread",
        "dwpf_now",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "current_yes_ask",
        "d1_no_ask",
        "d2_no_ask",
        "target_yes_ask",
        "final_winning_bracket",
        "current_bracket_held",
        "d1_hit",
        "skip_over_d1",
        "target_hit",
    ]
    rates = {col: float(rows[col].notna().mean()) if col in rows and len(rows) else None for col in field_cols}
    state_rates = {col: float(state[col].mean()) for col in state.columns if col.startswith("has_")}
    missing_counts = (
        state["missing_fields"].fillna("").str.get_dummies(sep=",").sum().sort_values(ascending=False)
    )
    return {
        "generated_at_utc": now_utc(),
        "target_metric": "reheat_risk_shared_feature_factory",
        "row_grain": "city + target_date + decision_snapshot_ts_utc + decision_hour_local + bracket + outcome",
        "evidence_layer": "time-aligned orderbook replay / opportunity feature layer, not live fills",
        "data_self_check": self_check,
        "funnel": {
            **orderbook_meta,
            "feature_rows": int(len(rows)),
            "state_rows_date_city_hour": int(len(state)),
            "min_target_date": str(rows["target_date"].min()) if len(rows) else None,
            "max_target_date": str(rows["target_date"].max()) if len(rows) else None,
            "active_dates": int(rows["target_date"].nunique()) if len(rows) else 0,
            "cities": int(rows["city"].nunique()) if len(rows) else 0,
            "complete_core_state_rows": int(state["complete_core"].sum()) if len(state) else 0,
        },
        "field_non_null_rates_by_feature_row": rates,
        "field_coverage_by_state_rate": state_rates,
        "missing_field_state_counts": {str(k): int(v) for k, v in missing_counts.items() if str(k) and int(v) > 0},
        "forecast_gap": {
            "fact_signal_candidates_rows": self_check["fact_signal_candidate_coverage"]["rows"],
            "forecast_peak_hour_feature_row_rate": rates.get("forecast_peak_hour_local"),
            "forecast_values_hash_feature_row_rate": rates.get("forecast_values_hash"),
            "gfs_forecast_peak_hour_feature_row_rate": rates.get("gfs_forecast_peak_hour_local"),
            "ecmwf_forecast_peak_hour_feature_row_rate": rates.get("ecmwf_forecast_peak_hour_local"),
            "forecast_peak_sources": {
                str(k): int(v)
                for k, v in rows.get("forecast_clock_source", pd.Series(dtype=object)).fillna("missing").value_counts().items()
            },
        },
    }


def write_report(payload: dict[str, Any], out_md: Path, feature_csv: Path, state_csv: Path) -> None:
    f = payload["funnel"]
    rates = payload["field_non_null_rates_by_feature_row"]
    state_rates = payload["field_coverage_by_state_rate"]
    missing_counts = payload.get("missing_field_state_counts", {})

    def pct(value: Any) -> str:
        if value is None:
            return "NA"
        return f"{float(value) * 100:.1f}%"

    lines = [
        "# Reheat Feature Factory v1",
        "",
        "## Data Snapshot",
        "",
        f"- Data source: `runtime/weather.db` (`fact_signal_candidates`, `fact_trades`, `settlement_outcomes`) plus time-aligned raw orderbook snapshots under `runtime/weather_edge_v1/market_data/orderbook_snapshots`.",
        f"- Generated at UTC: `{payload['generated_at_utc']}`.",
        f"- DB fact built at UTC: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`.",
        f"- Actual feature target-date range: `{f.get('min_target_date')}`..`{f.get('max_target_date')}` ({f.get('active_dates')} active dates).",
        f"- Row grain: `{payload['row_grain']}`.",
        f"- Evidence layer: {payload['evidence_layer']}.",
        "",
        "## Verdict",
        "",
        "This first shared factory is usable for downstream reheat-risk research on observed path, current YES, d1/d2 NO, target YES quotes, and settlement labels. It should replace strategy-private materializers for `current_yes_peak_forming`, `current_yes_fade_confirmed`, `higher_no_carry`, and `low_price_yes_reheat_reversal`.",
        "",
        "The former largest gap was forecast peak context. This factory now consumes the documented `forecast_peak_clock_backfill_v1.csv` city-date layer when native `fact_signal_candidates` peak fields are missing, and exposes both GFS and ECMWF peak-clock features. This makes forecast peak clock usable for shared research tables; it is still a backfilled research feature, not proof of production native point-in-time coverage.",
        "",
        "No live action is implied. This is an opportunity/replay feature layer, not fill PnL.",
        "",
        "## Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["data_self_check"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Data Funnel",
        "",
        "| Stage | Rows/count |",
        "|---|---:|",
        f"| orderbook files seen | {f['orderbook_files_seen']} |",
        f"| orderbook records seen | {f['orderbook_records_seen']} |",
        f"| ok orderbook records | {f['orderbook_records_ok']} |",
        f"| kept before hourly dedupe | {f['orderbook_records_kept_before_dedupe']} |",
        f"| feature rows after hourly dedupe/enrichment | {f['feature_rows']} |",
        f"| date/city/hour state rows | {f['state_rows_date_city_hour']} |",
        f"| complete core state rows | {f['complete_core_state_rows']} |",
        f"| active dates | {f['active_dates']} |",
        f"| cities | {f['cities']} |",
        "",
        "Core state means current temp, running max, minutes since max, current YES quote, d1 NO quote, and final winner are all present.",
        "",
        "## Field Coverage",
        "",
        "| Field | Feature-row non-null | State coverage |",
        "|---|---:|---:|",
    ]
    field_pairs = [
        ("current_temp_c", "has_current_temp"),
        ("running_max_c", "has_running_max"),
        ("decline_from_max_c", "has_running_max"),
        ("minutes_since_running_max", "has_minutes_since_max"),
        ("forecast_peak_hour_local", "has_forecast_peak_hour"),
        ("forecast_peak_delta_hours_local", "has_forecast_peak_hour"),
        ("forecast_values_hash", "has_forecast_hash"),
        ("gfs_forecast_peak_hour_local", "has_forecast_peak_hour"),
        ("gfs_forecast_peak_delta_hours_local", "has_forecast_peak_hour"),
        ("ecmwf_forecast_peak_hour_local", "has_forecast_peak_hour"),
        ("ecmwf_forecast_peak_delta_hours_local", "has_forecast_peak_hour"),
        ("dwpf_now", "has_dewpoint"),
        ("relative_humidity_pct", "has_rh"),
        ("wind_speed_kt", "has_wind"),
        ("sky_cover_code", "has_sky"),
        ("temp_trend_1h_f", "has_temp_trend"),
        ("current_yes_ask", "has_current_yes_quote"),
        ("d1_no_ask", "has_d1_no_quote"),
        ("d2_no_ask", "has_d2_no_quote"),
        ("target_yes_ask", "has_any_target_yes_quote"),
        ("final_winning_bracket", "has_final_winner"),
    ]
    for field, state_field in field_pairs:
        lines.append(f"| `{field}` | {pct(rates.get(field))} | {pct(state_rates.get(state_field))} |")
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- Feature rows CSV: `{feature_csv.relative_to(ROOT)}`",
            f"- Date/city/hour coverage CSV: `{state_csv.relative_to(ROOT)}`",
            f"- JSON manifest: `{payload['outputs']['json']}`",
            "",
            "## Date/City/Hour Missing-Field Summary",
            "",
            "The coverage CSV has one row per `target_date + city + decision_hour_local` with booleans and a `missing_fields` list. The largest state-level gaps are:",
            "",
            "| Missing field | State rows |",
            "|---|---:|",
        ]
    )
    for field, count in list(missing_counts.items())[:12]:
        lines.append(f"| `{field}` | {count} |")
    lines.extend(
        [
            "",
            "Use the coverage CSV to inspect the exact date/city/hour rows before running any strategy-head experiment.",
            "",
            "## Schema Notes",
            "",
            "- `decision_snapshot_ts_utc` is the raw orderbook snapshot timestamp; quotes are not forward-filled from later books.",
            "- `current_bracket` is the YES bracket containing the rounded running max in the market's unit.",
            "- `d1_no_bracket` and `d2_no_bracket` are the first and second higher NO siblings above the running max.",
            "- `target_yes_*` is the YES sibling quote for the row's own bracket, so low-price YES reheat reversal can use the same table.",
            "- `current_bracket_held`, `d1_hit`, `skip_over_d1`, and `target_hit` come from `settlement_outcomes` source-grain truth.",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    orderbook_dir = Path(args.orderbook_dir)
    observed_path = Path(args.observed_detail)
    station_summary_path = Path(args.station_summary)
    ext_dir = Path(args.ext_cache_dir)
    forecast_peak_backfill_path = Path(args.forecast_peak_backfill)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    hours = {int(part) for part in str(args.decision_hours).split(",") if part.strip()}

    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_ro(db_path)
    try:
        self_check = data_self_check(conn)
        _, winners = load_settlements(conn, args.start_date, args.end_date)
        forecasts = load_forecasts(conn, args.start_date, args.end_date)
    finally:
        conn.close()

    station_summary = load_station_summary(station_summary_path)
    observed = load_observed(observed_path, args.start_date, args.end_date, hours)
    quotes, orderbook_meta = iter_orderbook(orderbook_dir, observed, args.start_date, args.end_date, hours)
    if quotes.empty:
        raise SystemExit("No orderbook quote rows materialized; check date/hour inputs.")
    rows = quotes.merge(observed, on=["city", "target_date", "decision_hour_local"], how="left")
    rows = rows.merge(winners, on=["city", "target_date"], how="left", suffixes=("", "_settlement"))
    if "unit" not in rows:
        rows["unit"] = np.nan
    if "unit_settlement" in rows:
        rows["unit"] = rows["unit"].fillna(rows["unit_settlement"])
    unit_by_city = {city: str(item.get("unit") or "").upper() for city, item in station_summary.items()}
    rows["unit"] = rows["unit"].fillna(rows["city"].map(unit_by_city))
    rows = add_state_siblings(rows)
    rows = add_truth(rows)
    ext = load_ext_cache(ext_dir, station_summary, args.start_date, args.end_date)
    rows = add_weather_features(rows, ext)
    rows = add_forecasts(rows, forecasts)
    peak_backfill = load_forecast_peak_backfill(forecast_peak_backfill_path, args.start_date, args.end_date)
    rows = add_forecast_peak_backfill(rows, peak_backfill)

    state = coverage_by_state(rows)
    feature_csv = out_dir / "reheat_feature_rows.csv"
    state_csv = out_dir / "coverage_by_date_city_hour.csv"
    rows.to_csv(feature_csv, index=False)
    state.to_csv(state_csv, index=False)

    payload = summarize_coverage(rows, state, orderbook_meta, self_check)
    payload["inputs"] = {
        "db": str(db_path.relative_to(ROOT)) if db_path.is_absolute() else str(db_path),
        "orderbook_dir": str(orderbook_dir.relative_to(ROOT)) if orderbook_dir.is_absolute() else str(orderbook_dir),
        "observed_detail": str(observed_path.relative_to(ROOT)) if observed_path.is_absolute() else str(observed_path),
        "station_summary": str(station_summary_path.relative_to(ROOT)) if station_summary_path.is_absolute() else str(station_summary_path),
        "ext_cache_dir": str(ext_dir.relative_to(ROOT)) if ext_dir.is_absolute() else str(ext_dir),
        "forecast_peak_backfill": str(forecast_peak_backfill_path.relative_to(ROOT))
        if forecast_peak_backfill_path.is_absolute()
        else str(forecast_peak_backfill_path),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "decision_hours": sorted(hours),
    }
    payload["outputs"] = {
        "feature_rows_csv": str(feature_csv.relative_to(ROOT)),
        "coverage_by_date_city_hour_csv": str(state_csv.relative_to(ROOT)),
        "json": str(out_json.relative_to(ROOT)),
        "markdown": str(out_md.relative_to(ROOT)),
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, out_md, feature_csv, state_csv)
    print(json.dumps({"feature_rows": len(rows), "state_rows": len(state), "out_json": str(out_json), "out_md": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
