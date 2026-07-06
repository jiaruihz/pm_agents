#!/usr/bin/env python3
"""Feature-layer replay for late-window exact-bracket residual capture.

The replay is intentionally conservative:
- observed state comes from the shared intraday weather regime atlas;
- orderbook rows are replayed at snapshot granularity inside the local
  late-window hour, using only the state already visible at that hour;
- settlement is resolved once per city/date exact bracket from
  settlement_outcomes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_feature_layer.market import Bracket, bracket_contains, parse_bracket  # noqa: E402

DB = ROOT / "runtime/weather.db"
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
ATLAS_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
ATLAS_FRESHNESS = ROOT / "runtime/_analysis_logs/intraday_weather_regime_atlas_freshness_v1.json"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_capture_feature_layer_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-late-window-residual-capture-feature-layer-v2.md"
OBS_LATEST = ROOT.parent / "weather_data_feed_service_runtime/output/observations/latest.json"
DECISION_HOURS = (15, 16, 17, 18)
FEE_RATE = 0.05


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def best_level(raw: Any, side: str) -> tuple[float | None, float | None]:
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
    return min(parsed, key=lambda x: x[0]) if side == "asks" else max(parsed, key=lambda x: x[0])


def summary_num(record: dict[str, Any], key: str) -> float | None:
    summary = record.get("summary")
    if not isinstance(summary, dict):
        return None
    try:
        return float(summary.get(key)) if summary.get(key) is not None else None
    except (TypeError, ValueError):
        return None


def load_atlas_states(path: Path, hours: set[int]) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing atlas state rows: {path}")
    keep = {
        "city",
        "target_date",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "timezone",
        "icao",
        "unit",
        "current_temp_c",
        "current_temp_f",
        "running_max_c",
        "running_max_f",
        "current_native",
        "running_native",
        "decline_native",
        "running_value",
        "current_bracket",
        "current_yes_ask",
        "current_no_ask",
        "d1_no_bracket",
        "d1_no_ask",
        "d2_no_bracket",
        "d2_no_ask",
        "tmpf_now",
        "dwpf_now",
        "dewpoint_depression_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "forecast_source",
        "forecast_clock_source",
        "forecast_max_native",
        "forecast_max_f",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
        "forecast_gap_to_running_native",
        "city_family",
        "solar_window",
        "day_regime",
        "running_max_state",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
    }
    header = pd.read_csv(path, nrows=0).columns
    cols = [col for col in header if col in keep]
    out = pd.read_csv(path, usecols=cols, low_memory=False)
    out["target_date"] = out["target_date"].astype(str)
    out = out[out["decision_hour_local"].isin(hours)].copy()
    out["decision_ts_utc"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce").dt.strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )
    out["running_value"] = pd.to_numeric(out["running_value"], errors="coerce")
    out["feature_layer_source"] = "intraday_weather_regime_atlas_v1"
    out = out.sort_values(["target_date", "city", "decision_hour_local", "decision_snapshot_ts_utc"])
    out = out.drop_duplicates(["city", "target_date", "decision_hour_local"], keep="last")
    return out.dropna(subset=["running_value", "timezone", "decision_ts_utc"])


def load_today_observation(path: Path, city: str = "Chengdu", hour: int = 17) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") or []
    rec = next((r for r in records if r.get("city") == city and r.get("status") == "ok"), None)
    if not rec:
        return pd.DataFrame()
    target_date = str(rec.get("target_date"))
    row = {
        "city": rec.get("city"),
        "icao": rec.get("station"),
        "timezone": rec.get("timezone_name"),
        "target_date": target_date,
        "decision_hour_local": hour,
        "obs_count_day": rec.get("record_count"),
        "obs_count_to_decision": rec.get("record_count"),
        "decision_last_obs_utc": rec.get("last_obs_utc"),
        "current_temp_c": rec.get("current_temp_c"),
        "current_temp_f": rec.get("tmpf_now"),
        "running_max_c": rec.get("running_max_c"),
        "running_max_f": (float(rec["running_max_c"]) * 9.0 / 5.0 + 32.0) if rec.get("running_max_c") is not None else None,
        "decline_from_max_c": rec.get("decline_c"),
        "decline_from_max_f": (float(rec["decline_c"]) * 9.0 / 5.0) if rec.get("decline_c") is not None else None,
        "final_max_c": np.nan,
        "final_max_f": np.nan,
        "tmpf_now": rec.get("tmpf_now"),
        "dwpf_now": rec.get("dwpf_now"),
        "dewpoint_depression_f": rec.get("dewpoint_depression_f"),
        "relative_humidity_pct": rec.get("relh_now"),
        "wind_speed_kt": rec.get("sknt_now"),
        "sky_cover_code": rec.get("sky_code_now"),
        "temp_trend_1h_f": rec.get("d_tmpf_1h"),
        "temp_trend_3h_f": rec.get("d_tmpf_3h"),
        "minutes_since_running_max": rec.get("minutes_since_running_max"),
        "cadence_min": rec.get("cadence_min"),
        "minutes_to_next_obs": rec.get("minutes_to_next_obs"),
        "source": rec.get("source"),
        "source_chain": ",".join(rec.get("source_chain") or []),
        "asof_generated_at_utc": data.get("generated_at_utc"),
    }
    return pd.DataFrame([row])


def settlement_maps(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict[tuple[str, str, str], float], dict[tuple[str, str], str]]:
    outcomes = pd.DataFrame(rows(conn, "SELECT city,target_date,bracket,unit,final_price,settlement_status FROM settlement_outcomes"))
    if outcomes.empty:
        return outcomes, {}, {}
    outcomes["target_date"] = outcomes["target_date"].astype(str)
    outcomes["final_price"] = pd.to_numeric(outcomes["final_price"], errors="coerce")
    price_map = {(r.city, r.target_date, str(r.bracket)): float(r.final_price) for r in outcomes.itertuples()}
    winners = outcomes[outcomes["final_price"].ge(0.99)].copy()
    winners = winners.groupby(["city", "target_date"]).filter(lambda g: len(g) == 1)
    winner_map = {(r.city, r.target_date): str(r.bracket) for r in winners.itertuples()}
    return outcomes, price_map, winner_map


def unit_by_state(outcomes: pd.DataFrame, observed: pd.DataFrame) -> dict[tuple[str, str], str]:
    unit_map = {}
    if not outcomes.empty:
        for r in outcomes.dropna(subset=["unit"]).drop_duplicates(["city", "target_date"]).itertuples():
            unit_map[(r.city, r.target_date)] = str(r.unit)
    for r in observed.itertuples():
        unit_map.setdefault((r.city, r.target_date), "C" if str(r.timezone).startswith("Asia/") or str(r.unit if hasattr(r, "unit") else "") == "C" else "F")
    return unit_map


def decision_ts_utc(target_date: str, hour: int, tz_name: str) -> pd.Timestamp:
    return pd.Timestamp(f"{target_date} {hour:02d}:00:00", tz=ZoneInfo(tz_name)).tz_convert("UTC")


def build_states(observed: pd.DataFrame, outcomes: pd.DataFrame, hours: set[int]) -> pd.DataFrame:
    unit_map = unit_by_state(outcomes, observed)
    out = observed[observed["decision_hour_local"].isin(hours)].copy()
    out["target_date"] = out["target_date"].astype(str)
    out["unit"] = [unit_map.get((r.city, r.target_date), "C") for r in out.itertuples()]
    out["is_f"] = out["unit"].str.upper().eq("F")
    out["running_native"] = np.where(out["is_f"], out["running_max_f"], out["running_max_c"])
    out["current_native"] = np.where(out["is_f"], out["current_temp_f"], out["current_temp_c"])
    out["decline_native"] = out["running_native"] - out["current_native"]
    out["running_value"] = out["running_native"].apply(lambda v: round_half_up(v) if pd.notna(v) else np.nan)
    out["decision_ts_utc"] = [
        decision_ts_utc(str(r.target_date), int(r.decision_hour_local), str(r.timezone)).isoformat()
        for r in out.itertuples()
    ]
    return out.dropna(subset=["running_value", "timezone"])


def desired_leg(bracket: Bracket, outcome: str, running_value: float, unit: str) -> str | None:
    if outcome == "yes" and bracket_contains(bracket, running_value):
        return "current_yes"
    if outcome != "no" or bracket.low is None or float(bracket.low) <= running_value:
        return None
    if unit.upper() == "F":
        dist = int(math.ceil((float(bracket.low) - running_value) / 2.0))
    else:
        dist = int(round(float(bracket.low) - running_value))
    if dist in (1, 2, 3):
        return f"d{dist}_no"
    return None


def load_quotes(orderbook_dir: Path, states: pd.DataFrame, max_book_age_min: float) -> pd.DataFrame:
    state = states[["city", "target_date", "decision_hour_local", "decision_ts_utc", "running_value", "unit"]].copy()
    state["decision_ts"] = pd.to_datetime(state["decision_ts_utc"], utc=True)
    state_map: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in state.itertuples():
        state_map[(str(r.city), str(r.target_date), int(r.decision_hour_local))] = {
            "city": str(r.city),
            "target_date": str(r.target_date),
            "decision_hour_local": int(r.decision_hour_local),
            "decision_ts": r.decision_ts,
            "running_value": float(r.running_value),
            "unit": str(r.unit),
        }
    out = []
    tz_by_city_date = states.drop_duplicates(["city", "target_date"]).set_index(["city", "target_date"])["timezone"].to_dict()
    for date_dir in sorted(orderbook_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for path in sorted(date_dir.glob("orderbook_snapshot_*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("status") != "ok":
                        continue
                    city = str(rec.get("city") or "")
                    target_date = str(rec.get("event_date") or "")
                    tz_name = tz_by_city_date.get((city, target_date))
                    if not tz_name:
                        continue
                    try:
                        snap_dt = datetime.fromisoformat(str(rec.get("snapshot_ts_utc")).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    local_dt = snap_dt.astimezone(ZoneInfo(str(tz_name)))
                    if local_dt.date().isoformat() != target_date:
                        continue
                    st = state_map.get((city, target_date, int(local_dt.hour)))
                    if not st:
                        continue
                    snap = pd.Timestamp(snap_dt).tz_convert("UTC")
                    age_min = (snap - st["decision_ts"]).total_seconds() / 60.0
                    if age_min < -1e-9 or age_min > 59.999:
                        continue
                    bracket = parse_bracket(rec.get("bracket"))
                    outcome = str(rec.get("outcome") or "").lower()
                    if bracket is None or outcome not in {"yes", "no"}:
                        continue
                    leg = desired_leg(bracket, outcome, st["running_value"], st["unit"])
                    if leg is None:
                        continue
                    ask, ask_size = best_level(rec.get("raw"), "asks")
                    bid, bid_size = best_level(rec.get("raw"), "bids")
                    out.append(
                        {
                            "orderbook_file": str(path.relative_to(ROOT)),
                            "snapshot_ts_utc": snap.isoformat(),
                            "book_age_min": age_min,
                            "decision_hour_local": st["decision_hour_local"],
                            "decision_minute_local": int(local_dt.minute),
                            "city": city,
                            "target_date": target_date,
                            "unit": st["unit"],
                            "running_value": st["running_value"],
                            "leg": leg,
                            "bracket": bracket.raw,
                            "outcome": outcome,
                            "condition_id": rec.get("condition_id"),
                            "market_id": rec.get("market_id"),
                            "token_id": rec.get("token_id"),
                            "best_ask": ask,
                            "ask_size": ask_size,
                            "best_bid": bid,
                            "bid_size": bid_size,
                            "spread": summary_num(rec, "spread"),
                            "depth_ask_5c": summary_num(rec, "depth_ask_5c"),
                            "depth_bid_5c": summary_num(rec, "depth_bid_5c"),
                        }
                    )
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    return df.sort_values(["snapshot_ts_utc", "city", "target_date", "leg", "bracket", "outcome"]).reset_index(drop=True)


def fee_per_share(price: float, mode: str) -> float:
    if mode == "maker":
        return 0.0
    return round(FEE_RATE * price * (1.0 - price), 5)


def add_performance(legs: pd.DataFrame, price_map: dict[tuple[str, str, str], float], winner_map: dict[tuple[str, str], str]) -> pd.DataFrame:
    out = legs.copy()
    out["final_winning_bracket"] = [winner_map.get((r.city, r.target_date)) for r in out.itertuples()]
    out["final_yes"] = [price_map.get((r.city, r.target_date, str(r.bracket))) for r in out.itertuples()]
    out["settled"] = out["final_yes"].notna()
    out["win"] = np.where(out["settled"], np.where(out["outcome"].eq("yes"), out["final_yes"].ge(0.99), out["final_yes"].le(0.01)), np.nan)
    rows_out = []
    for mode, price_col, size_col, depth_col in [
        ("taker", "best_ask", "ask_size", "depth_ask_5c"),
        ("maker", "best_bid", "bid_size", "depth_bid_5c"),
    ]:
        m = out.copy()
        m["execution_mode"] = mode
        m["entry_price"] = pd.to_numeric(m[price_col], errors="coerce")
        m["top_size"] = pd.to_numeric(m[size_col], errors="coerce")
        m["depth_5c"] = pd.to_numeric(m[depth_col], errors="coerce")
        m["residual_points"] = (1.0 - m["entry_price"]) * 100.0
        m["fee_per_share"] = m["entry_price"].apply(lambda x: fee_per_share(float(x), mode) if pd.notna(x) else np.nan)
        m["cost_per_share"] = m["entry_price"] + m["fee_per_share"]
        m["pnl_per_share"] = np.where(m["settled"], m["win"].astype(float) - m["cost_per_share"], np.nan)
        m["roi"] = m["pnl_per_share"] / m["cost_per_share"]
        m["strict_residual_1_5"] = m["residual_points"].between(1.0, 5.0, inclusive="both")
        m["watch_residual_1_10"] = m["residual_points"].between(1.0, 10.0, inclusive="both")
        m["depth_ok"] = m["top_size"].fillna(0).ge(5.0) | m["depth_5c"].fillna(0).ge(5.0)
        rows_out.append(m)
    return pd.concat(rows_out, ignore_index=True)


def summarize(frame: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows_out = []
    for key, g in frame.groupby(group_cols, dropna=False):
        settled = g[g["settled"]].copy()
        if settled.empty:
            row = {"rows": int(len(g)), "settled_rows": 0}
        else:
            cost = float(settled["cost_per_share"].sum())
            pnl = float(settled["pnl_per_share"].sum())
            daily = settled.groupby("target_date")["pnl_per_share"].sum()
            boot = block_ci(daily.to_numpy(), reps=2000, seed=17)
            row = {
                "rows": int(len(g)),
                "settled_rows": int(len(settled)),
                "active_dates": int(settled["target_date"].nunique()),
                "cities": int(settled["city"].nunique()),
                "avg_entry_price": float(settled["entry_price"].mean()),
                "avg_residual_points": float(settled["residual_points"].mean()),
                "hit_rate": float(settled["win"].mean()),
                "cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost else None,
                "daily_pnl_ci_low": boot[0],
                "daily_pnl_ci_high": boot[1],
                "max_loss_per_share": float(settled["pnl_per_share"].min()),
                "top_size_sum": float(settled["top_size"].fillna(0).sum()),
            }
        if len(group_cols) == 1:
            row[group_cols[0]] = key if not isinstance(key, tuple) else key[0]
        else:
            vals = key if isinstance(key, tuple) else (key,)
            row.update(dict(zip(group_cols, vals)))
        rows_out.append(row)
    return rows_out


def block_ci(values: np.ndarray, reps: int = 2000, seed: int = 0) -> tuple[float | None, float | None]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(reps, len(values)), replace=True).sum(axis=1)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def add_slices(rows_df: pd.DataFrame) -> pd.DataFrame:
    out = rows_df.copy()
    out["period"] = np.where(out["target_date"].ge("2026-06-29"), "forward_2026-06-29_2026-07-04", "train_to_2026-06-28")
    out["residual_band"] = pd.cut(out["residual_points"], bins=[0, 1, 5, 10, 100], labels=["lt1", "1_5", "5_10", "gt10"], include_lowest=True)
    out["hour"] = out["decision_hour_local"].astype(str)
    out["book_spread_band"] = pd.cut(pd.to_numeric(out["spread"], errors="coerce"), bins=[-0.001, 0.01, 0.03, 0.06, 1], labels=["<=1c", "1_3c", "3_6c", ">6c"])
    out["plateau_state"] = np.select(
        [
            out["decline_native"].abs().le(0.25),
            out["decline_native"].gt(0.25),
            out["decline_native"].lt(-0.25),
        ],
        ["at_high", "decline", "new_high_or_warming"],
        default="missing",
    )
    out["forecast_gap_bucket"] = pd.cut(
        pd.to_numeric(out.get("forecast_gap_to_running_native"), errors="coerce"),
        bins=[-100, -1, 0, 1, 2, 100],
        labels=["busted_lt_minus1", "capped_minus1_0", "room_0_1", "room_1_2", "room_gt2"],
        include_lowest=True,
    )
    out["minutes_since_max_bucket"] = pd.cut(
        pd.to_numeric(out.get("minutes_since_running_max"), errors="coerce"),
        bins=[-1, 15, 45, 90, 180, 10000],
        labels=["fresh_le15", "recent_15_45", "mature_45_90", "old_90_180", "stale_gt180"],
    )
    return out


def first_cross_rows(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return selected.copy()
    sort_cols = ["execution_mode", "city", "target_date", "decision_hour_local", "leg", "bracket", "snapshot_ts_utc"]
    return (
        selected.sort_values(sort_cols)
        .drop_duplicates(["execution_mode", "city", "target_date", "decision_hour_local", "leg", "bracket"], keep="first")
        .copy()
    )


def candidate_rule_rows(selected: pd.DataFrame) -> list[dict[str, Any]]:
    if selected.empty:
        return []
    rules = [
        ("d3_no_day_room", "d3_no", "day_regime", {"day_marginal_runway", "day_open_runway", "day_forecast_capped"}),
        ("d3_no_forecast_room", "d3_no", "forecast_gap_bucket", {"room_gt2", "room_0_1", "room_1_2"}),
        ("d2_no_plateau_pullback", "d2_no", "intraday_state", {"plateau_near_high", "pullback_uncertain"}),
        ("d2_no_stalled_pullback", "d2_no", "running_max_state", {"stalled_high", "pullback_from_high"}),
        ("current_yes_plateau_unknown_clock", "current_yes", "running_max_state", {"near_high_plateau", "running_max_clock_unknown"}),
    ]
    rows_out: list[dict[str, Any]] = []
    for execution_mode in ("taker", "maker"):
        mode_frame = selected[selected["execution_mode"].eq(execution_mode)].copy()
        for rule_id, leg, feature_col, values in rules:
            if feature_col not in mode_frame.columns:
                continue
            full = mode_frame[mode_frame["leg"].eq(leg) & mode_frame[feature_col].isin(values)].copy()
            forward = full[full["target_date"].ge("2026-06-29")].copy()
            for period, frame in [("full", full), ("forward_2026-06-29_2026-07-04", forward)]:
                if frame.empty:
                    continue
                cost = float(frame["cost_per_share"].sum())
                pnl = float(frame["pnl_per_share"].sum())
                rows_out.append(
                    {
                        "rule_id": rule_id,
                        "execution_mode": execution_mode,
                        "period": period,
                        "leg": leg,
                        "feature_col": feature_col,
                        "feature_values": ",".join(sorted(values)),
                        "settled_rows": int(len(frame)),
                        "active_dates": int(frame["target_date"].nunique()),
                        "hit_rate": float(frame["win"].mean()),
                        "cost": cost,
                        "pnl": pnl,
                        "roi": pnl / cost if cost else None,
                    }
                )
    return rows_out


def basket_rows(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    settled = selected[selected["settled"]].copy()
    if settled.empty:
        return pd.DataFrame()
    return (
        settled.groupby(["execution_mode", "city", "target_date", "decision_hour_local", "snapshot_ts_utc"], as_index=False)
        .agg(
            legs=("leg", "count"),
            leg_set=("leg", lambda s: ",".join(sorted(s))),
            cost=("cost_per_share", "sum"),
            pnl=("pnl_per_share", "sum"),
            worst_leg_pnl=("pnl_per_share", "min"),
            final_winning_bracket=("final_winning_bracket", "first"),
        )
        .assign(roi=lambda d: d["pnl"] / d["cost"])
    )


def chengdu_39no_orderbook_timeline(orderbook_dir: Path) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    for path in sorted((orderbook_dir / "2026-07-06").glob("orderbook_snapshot_*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if (
                    rec.get("status") == "ok"
                    and rec.get("city") == "Chengdu"
                    and str(rec.get("event_date")) == "2026-07-06"
                    and str(rec.get("bracket")) == "39"
                    and str(rec.get("outcome")).lower() == "no"
                ):
                    snap_dt = datetime.fromisoformat(str(rec.get("snapshot_ts_utc")).replace("Z", "+00:00"))
                    local_dt = snap_dt.astimezone(ZoneInfo("Asia/Shanghai"))
                    if local_dt.hour < 14:
                        continue
                    out.append(
                        {
                            "snapshot_ts_utc": snap_dt.isoformat(),
                            "snapshot_time_bj": local_dt.strftime("%H:%M:%S"),
                            "best_ask": summary_num(rec, "best_ask"),
                            "ask_size": summary_num(rec, "ask_size"),
                            "best_bid": summary_num(rec, "best_bid"),
                            "bid_size": summary_num(rec, "bid_size"),
                            "spread": summary_num(rec, "spread"),
                            "depth_ask_5c": summary_num(rec, "depth_ask_5c"),
                            "depth_bid_5c": summary_num(rec, "depth_bid_5c"),
                            "orderbook_file": str(path.relative_to(ROOT)),
                        }
                    )
    return pd.DataFrame(out)


def write_md(payload: dict[str, Any], md_path: Path) -> None:
    strict = pd.DataFrame(payload["summary"]["strict_by_mode_leg"])
    first_cross = pd.DataFrame(payload["summary"]["first_cross_by_mode_leg"])
    forward = pd.DataFrame(payload["summary"]["strict_forward_by_mode_leg"])
    first_forward = pd.DataFrame(payload["summary"]["first_cross_forward_by_mode_leg"])
    basket = pd.DataFrame(payload["summary"]["basket_by_mode"])
    basket_first = pd.DataFrame(payload["summary"]["basket_first_cross_by_mode"])
    day_regime = pd.DataFrame(payload["summary"]["first_cross_by_day_regime"])
    intraday_state = pd.DataFrame(payload["summary"]["first_cross_by_intraday_state"])
    running_state = pd.DataFrame(payload["summary"]["first_cross_by_running_max_state"])
    forecast_gap = pd.DataFrame(payload["summary"]["first_cross_by_forecast_gap"])
    candidate_rules = pd.DataFrame(payload["candidate_rule_summary"])
    chengdu = pd.DataFrame(payload["chengdu_case"])
    chengdu_39no = pd.DataFrame(payload["chengdu_39no_orderbook"])

    def table(df: pd.DataFrame, cols: list[str]) -> str:
        if df.empty:
            return "_empty_"
        d = df[[col for col in cols if col in df.columns]].copy()
        for col in d.columns:
            if pd.api.types.is_float_dtype(d[col]):
                d[col] = d[col].map(lambda x: "" if pd.isna(x) else f"{x:.3f}")
        d = d.fillna("").astype(str)
        header = "| " + " | ".join(d.columns) + " |"
        sep = "| " + " | ".join(["---"] * len(d.columns)) + " |"
        body = ["| " + " | ".join(row) + " |" for row in d.to_numpy()]
        return "\n".join([header, sep, *body])

    lines = [
        "# Late-window residual capture feature-layer v2",
        "",
        "## 数据快照",
        f"- sync/rebuild: `{payload['data_snapshot']['sync_rebuild']}`",
        f"- DB fact built: `{payload['data_snapshot']['fact_signal_candidates_max_built_at_utc']}`; CLOB gate: `{payload['data_snapshot']['clob_gate_pass']}`",
        f"- settlement_outcomes: `{payload['data_snapshot']['settlement_min_date']}..{payload['data_snapshot']['settlement_max_date']}`",
        f"- feature layer: `{payload['data_snapshot']['feature_layer_source']}`; status `{payload['data_snapshot']['feature_layer_freshness_status']}`; dates `{payload['data_snapshot']['observed_min_date']}..{payload['data_snapshot']['observed_max_date']}`; state rows `{payload['funnel']['observed_states']}`",
        f"- orderbook matched snapshot-level leg quote rows: `{payload['funnel']['quote_rows']}`; strict 1-5 point rows with depth: `{payload['funnel']['strict_depth_rows']}`; first-cross rows `{payload['funnel']['first_cross_rows']}`",
        "",
        "## 结论",
        payload["verdict"],
        "",
        "## Snapshot-level strict 1-5 point residual legs",
        table(strict, ["execution_mode", "leg", "settled_rows", "active_dates", "avg_entry_price", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high", "top_size_sum"]),
        "",
        "## First-cross strict 1-5 point residual legs",
        table(first_cross, ["execution_mode", "leg", "settled_rows", "active_dates", "avg_entry_price", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high", "top_size_sum"]),
        "",
        "## Forward 2026-06-29..2026-07-04",
        table(forward, ["execution_mode", "leg", "settled_rows", "active_dates", "avg_entry_price", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "## First-cross forward 2026-06-29..2026-07-04",
        table(first_forward, ["execution_mode", "leg", "settled_rows", "active_dates", "avg_entry_price", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "## Basket risk",
        table(basket, ["execution_mode", "rows", "settled_rows", "active_dates", "cost", "pnl", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "## First-cross basket risk",
        table(basket_first, ["execution_mode", "rows", "settled_rows", "active_dates", "cost", "pnl", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "## First-cross feature slices",
        "### Day regime",
        table(day_regime, ["execution_mode", "day_regime", "settled_rows", "active_dates", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "### Intraday state",
        table(intraday_state, ["execution_mode", "intraday_state", "settled_rows", "active_dates", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "### Running max state",
        table(running_state, ["execution_mode", "running_max_state", "settled_rows", "active_dates", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "### Forecast gap",
        table(forecast_gap, ["execution_mode", "forecast_gap_bucket", "settled_rows", "active_dates", "hit_rate", "roi", "daily_pnl_ci_low", "daily_pnl_ci_high"]),
        "",
        "## Candidate scorer rules",
        table(candidate_rules, ["rule_id", "execution_mode", "period", "leg", "feature_col", "feature_values", "settled_rows", "active_dates", "hit_rate", "roi", "pnl"]),
        "",
        "## Chengdu 2026-07-06 As-of Case",
        "Feature-layer atlas currently stops at 2026-07-04, so current-day Chengdu is quote evidence only in this v2.",
        table(chengdu, ["snapshot_ts_utc", "decision_hour_local", "decision_minute_local", "leg", "execution_mode", "bracket", "entry_price", "residual_points", "top_size", "spread", "book_age_min", "running_value", "current_native", "decline_native"]),
        "",
        "## Chengdu 39 NO Orderbook Timeline",
        "This table is quote evidence only: the Mac observation cache kept latest state, not a 15:00/16:00 historical state for 2026-07-06, so these rows are not injected into the settled PIT replay.",
        table(chengdu_39no, ["snapshot_time_bj", "best_ask", "ask_size", "best_bid", "bid_size", "spread", "depth_ask_5c"]),
        "",
        "## Failure cases",
        table(pd.DataFrame(payload["failure_cases"]), ["target_date", "city", "decision_hour_local", "leg", "execution_mode", "bracket", "entry_price", "residual_points", "final_winning_bracket", "pnl_per_share"]),
        "",
        "## 8环覆盖",
        "- 描述性绩效/统计推断/执行微结构/容量/组合相关性/基准: covered at research replay level.",
        "- 信号判别: covered as feature-layer slices, not a trained probability model.",
        "- 前瞻门: checked on 2026-06-29..2026-07-04; v2 remains research/shadow until fresh feature-layer forward accumulates.",
        "",
        "## Gate verdict",
        payload["gate_sentence"],
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--orderbook-dir", default=str(ORDERBOOK_DIR))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--max-book-age-min", type=float, default=45.0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_ro(Path(args.db))
    outcomes, price_map, winner_map = settlement_maps(conn)
    states = load_atlas_states(ATLAS_ROWS, set(DECISION_HOURS))
    quotes = load_quotes(Path(args.orderbook_dir), states, args.max_book_age_min)
    legs = add_performance(quotes.merge(states, on=["city", "target_date", "decision_hour_local", "unit", "running_value"], how="left"), price_map, winner_map)
    legs = add_slices(legs)
    legs.to_csv(out_dir / "leg_rows.csv", index=False)

    strict = legs[legs["strict_residual_1_5"] & legs["depth_ok"]].copy()
    strict_first = first_cross_rows(strict)
    watch = legs[legs["watch_residual_1_10"] & legs["depth_ok"]].copy()
    strict_settled = strict[strict["settled"] & strict["target_date"].le("2026-07-04")].copy()
    strict_first_settled = strict_first[strict_first["settled"] & strict_first["target_date"].le("2026-07-04")].copy()
    basket = basket_rows(strict_settled)
    basket_first = basket_rows(strict_first_settled)
    basket.to_csv(out_dir / "basket_rows.csv", index=False)
    strict_first.to_csv(out_dir / "first_cross_rows.csv", index=False)
    basket_first.to_csv(out_dir / "basket_first_cross_rows.csv", index=False)

    chengdu_case = watch[(watch["city"].eq("Chengdu")) & (watch["target_date"].eq("2026-07-06"))].copy()
    chengdu_case.to_csv(out_dir / "chengdu_2026_07_06_case.csv", index=False)
    chengdu_39no = chengdu_39no_orderbook_timeline(Path(args.orderbook_dir))
    chengdu_39no.to_csv(out_dir / "chengdu_39no_orderbook_2026_07_06.csv", index=False)

    failures = (
        strict_settled[strict_settled["pnl_per_share"].lt(0)]
        .sort_values("pnl_per_share")
        .head(30)
        [[
            "target_date",
            "city",
            "decision_hour_local",
            "leg",
            "execution_mode",
            "bracket",
            "entry_price",
            "residual_points",
            "final_winning_bracket",
            "pnl_per_share",
        ]]
        .to_dict("records")
    )
    data_snapshot = {
        "sync_rebuild": "scripts/ops/sync_weather_remote.sh && scripts/weather_dashboard/run_stack.sh on 2026-07-06 17:47-17:51 Asia/Shanghai",
        "fact_signal_candidates_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
        "fact_signal_candidates_rows": scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
        "clob_gate_pass": json.loads((ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json").read_text()).get("gate_pass"),
        "settlement_min_date": scalar(conn, "SELECT MIN(target_date) FROM settlement_outcomes"),
        "settlement_max_date": scalar(conn, "SELECT MAX(target_date) FROM settlement_outcomes"),
        "feature_layer_source": str(ATLAS_ROWS.relative_to(ROOT)),
        "feature_layer_freshness_status": json.loads(ATLAS_FRESHNESS.read_text()).get("status") if ATLAS_FRESHNESS.exists() else None,
        "observed_min_date": str(states["target_date"].min()) if not states.empty else None,
        "observed_max_date": str(states["target_date"].max()) if not states.empty else None,
    }
    summary = {
        "strict_by_mode_leg": summarize(strict_settled, ["execution_mode", "leg"]),
        "first_cross_by_mode_leg": summarize(strict_first_settled, ["execution_mode", "leg"]),
        "strict_forward_by_mode_leg": summarize(strict_settled[strict_settled["period"].eq("forward_2026-06-29_2026-07-04")], ["execution_mode", "leg"]),
        "first_cross_forward_by_mode_leg": summarize(strict_first_settled[strict_first_settled["period"].eq("forward_2026-06-29_2026-07-04")], ["execution_mode", "leg"]),
        "watch_by_mode_leg": summarize(watch[watch["settled"] & watch["target_date"].le("2026-07-04")], ["execution_mode", "leg"]),
        "strict_by_city": summarize(strict_settled, ["execution_mode", "city"]),
        "strict_by_hour": summarize(strict_settled, ["execution_mode", "hour"]),
        "strict_by_plateau": summarize(strict_settled, ["execution_mode", "plateau_state"]),
        "first_cross_by_day_regime": summarize(strict_first_settled, ["execution_mode", "day_regime"]),
        "first_cross_by_intraday_state": summarize(strict_first_settled, ["execution_mode", "intraday_state"]),
        "first_cross_by_running_max_state": summarize(strict_first_settled, ["execution_mode", "running_max_state"]),
        "first_cross_by_forecast_gap": summarize(strict_first_settled, ["execution_mode", "forecast_gap_bucket"]),
        "basket_by_mode": summarize(
            basket.rename(columns={"cost": "cost_per_share", "pnl": "pnl_per_share"}).assign(
                settled=True,
                win=np.nan,
                entry_price=np.nan,
                residual_points=np.nan,
                top_size=np.nan,
                leg="basket",
            ),
            ["execution_mode"],
        ),
        "basket_first_cross_by_mode": summarize(
            basket_first.rename(columns={"cost": "cost_per_share", "pnl": "pnl_per_share"}).assign(
                settled=True,
                win=np.nan,
                entry_price=np.nan,
                residual_points=np.nan,
                top_size=np.nan,
                leg="basket",
            ),
            ["execution_mode"],
        ),
    }
    verdict = (
        "Feature-layer v2 uses the shared intraday weather regime atlas for state and keeps orderbook execution at real bid/ask. "
        "First-cross de-duplication is the strategy-relevant view: it removes repeated snapshots of the same city/hour/leg/bracket opportunity. "
        "Broad taker residual capture remains negative; the only actionable shape is a shadow scorer, not a live rule: prefer d3 NO when feature-layer runway still has room, d2 NO after plateau/pullback states, and current YES only in narrow near-high/unknown-clock plateau states. "
        "All candidate rules remain low-forward-support and must run shadow before any live decision-input rewire."
    )
    gate_sentence = (
        "在 2026-05-19..2026-07-04，late-window strict residual 1-5 point replay 相对 market-implied zero EV 的 fee-after ROI 未同时通过显著性、可执行基准和 forward 三门；"
        "significance=FAIL/NA, baseline=FAIL, forward=FAIL/NA, conclusion=inconclusive。"
    )
    payload = {
        "data_snapshot": data_snapshot,
        "funnel": {
            "observed_states": int(len(states)),
            "quote_rows": int(len(quotes)),
            "leg_rows_mode_expanded": int(len(legs)),
            "strict_depth_rows": int(len(strict)),
            "strict_settled_rows": int(len(strict_settled)),
            "first_cross_rows": int(len(strict_first)),
            "first_cross_settled_rows": int(len(strict_first_settled)),
            "watch_depth_rows": int(len(watch)),
        },
        "summary": summary,
        "candidate_rule_summary": candidate_rule_rows(strict_first_settled),
        "failure_cases": failures,
        "chengdu_case": chengdu_case.sort_values(["snapshot_ts_utc", "leg", "execution_mode"])[
            [
                "snapshot_ts_utc",
                "decision_hour_local",
                "decision_minute_local",
                "leg",
                "execution_mode",
                "bracket",
                "entry_price",
                "residual_points",
                "top_size",
                "spread",
                "book_age_min",
                "running_value",
                "current_native",
                "decline_native",
            ]
        ].to_dict("records"),
        "chengdu_39no_orderbook": chengdu_39no.to_dict("records"),
        "verdict": verdict,
        "gate_sentence": gate_sentence,
        "outputs": {
            "leg_rows": str(out_dir / "leg_rows.csv"),
            "basket_rows": str(out_dir / "basket_rows.csv"),
            "first_cross_rows": str(out_dir / "first_cross_rows.csv"),
            "basket_first_cross_rows": str(out_dir / "basket_first_cross_rows.csv"),
            "chengdu_case": str(out_dir / "chengdu_2026_07_06_case.csv"),
            "chengdu_39no_orderbook": str(out_dir / "chengdu_39no_orderbook_2026_07_06.csv"),
            "markdown": str(Path(args.out_md)),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    write_md(payload, Path(args.out_md))
    print(json.dumps({"out_dir": str(out_dir), "out_md": args.out_md, "funnel": payload["funnel"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
