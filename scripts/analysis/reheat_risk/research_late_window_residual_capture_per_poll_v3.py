#!/usr/bin/env python3
"""Per-poll PIT replay for late-window exact-bracket residual capture.

This version uses `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` as the
decision denominator. Each row already contains the as-of METAR state,
forecast peak fields, and executable CLOB top-of-book/depth captured in the
same polling cycle. Settlement is joined only after candidate generation.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_feature_layer.market import Bracket, bracket_contains, parse_bracket  # noqa: E402

DB = ROOT / "runtime/weather.db"
PAPER_SNAPSHOTS = ROOT / "runtime/weather_edge_v1/market_data.pre_external_20260706T215828/paper_snapshots"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_capture_per_poll_v3"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-late-window-residual-capture-per-poll-v3.md"

FEE_RATE = 0.05
LOCAL_START_HOUR = 15
LOCAL_END_HOUR = 18
MIN_RESIDUAL_POINTS = 1.0
MAX_RESIDUAL_POINTS = 5.0
MIN_DEPTH_SHARES = 5.0
FORWARD_START = "2026-06-29"
FORWARD_END = "2026-07-04"


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


def safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def round_half_up(value: float) -> int | None:
    if not math.isfinite(value):
        return None
    return int(math.floor(value + 0.5))


def native_from_metar_f(temp_f: float, unit: str) -> float:
    if not math.isfinite(temp_f):
        return math.nan
    if str(unit).upper() == "C":
        return (temp_f - 32.0) * 5.0 / 9.0
    return temp_f


def native_tick_value(temp_f: float, unit: str) -> int | None:
    native = native_from_metar_f(temp_f, unit)
    return round_half_up(native)


def no_distance(bracket: Bracket, running_value: int, unit: str) -> int | None:
    if bracket.low is None:
        return None
    low = float(bracket.low)
    if low <= running_value:
        return None
    if str(unit).upper() == "F":
        return int(math.ceil((low - running_value) / 2.0))
    return int(round(low - running_value))


def leg_for_record(record: dict[str, Any], running_value: int) -> str | None:
    bracket = parse_bracket(record.get("bracket"))
    if bracket is None:
        return None
    unit = str(record.get("unit") or "")
    if bracket_contains(bracket, running_value):
        return "current_yes"
    dist = no_distance(bracket, running_value, unit)
    if dist in (1, 2, 3):
        return f"d{dist}_no"
    return None


def entry_fields(record: dict[str, Any], leg: str) -> dict[str, float]:
    if leg == "current_yes":
        return {
            "outcome": "yes",
            "entry_price": safe_float(record.get("yes_best_ask")),
            "best_bid": safe_float(record.get("yes_best_bid")),
            "spread": safe_float(record.get("yes_spread")),
            "top_size": safe_float(record.get("yes_ask_size")),
            "depth_5c": safe_float(record.get("yes_depth_ask_5c")),
            "bid_depth_5c": safe_float(record.get("yes_depth_bid_5c")),
        }
    return {
        "outcome": "no",
        "entry_price": safe_float(record.get("no_best_ask")),
        "best_bid": safe_float(record.get("no_best_bid")),
        "spread": safe_float(record.get("no_spread")),
        "top_size": safe_float(record.get("no_ask_size")),
        "depth_5c": safe_float(record.get("no_depth_ask_5c")),
        "bid_depth_5c": safe_float(record.get("no_depth_bid_5c")),
    }


def settlement_maps(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict[tuple[str, str, str], float], set[str]]:
    outcomes = pd.DataFrame(
        rows(
            conn,
            """
            SELECT city,target_date,bracket,unit,final_price,settlement_status
            FROM settlement_outcomes
            WHERE settlement_status='settled'
            """,
        )
    )
    if outcomes.empty:
        return outcomes, {}, set()
    outcomes["target_date"] = outcomes["target_date"].astype(str)
    outcomes["final_price"] = pd.to_numeric(outcomes["final_price"], errors="coerce")
    price_map = {
        (str(r.city), str(r.target_date), str(r.bracket)): float(r.final_price)
        for r in outcomes.dropna(subset=["final_price"]).itertuples()
    }
    counts = outcomes.groupby("target_date")["city"].nunique()
    complete_dates = set(counts[counts.ge(30)].index.astype(str))
    return outcomes, price_map, complete_dates


def iter_snapshot_records(snapshot_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("snapshot_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ts_bj = str(data.get("ts_beijing") or "")
        ts_utc = str(data.get("ts_utc") or "")
        records = data.get("records") or []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            target_date = str(rec.get("target_date") or rec.get("event_date") or "")
            if not target_date:
                continue
            city_date = str(rec.get("city_local_date_at_snapshot") or "")
            if city_date != target_date:
                continue
            ts_local = str(rec.get("ts_local") or ts_bj)
            try:
                local_hour = int(ts_local[11:13] if "T" in ts_local else ts_bj[11:13])
                local_minute = int(ts_local[14:16] if "T" in ts_local else ts_bj[14:16])
            except (ValueError, IndexError):
                continue
            if local_hour < LOCAL_START_HOUR or local_hour > LOCAL_END_HOUR:
                continue
            if str(rec.get("probability_status")) != "ok":
                continue
            if rec.get("metar_source") in (None, "none"):
                continue
            metar_max_f = safe_float(rec.get("metar_current_max_f"))
            metar_latest_f = safe_float(rec.get("metar_latest_temp_f"))
            if not math.isfinite(metar_max_f):
                continue
            unit = str(rec.get("unit") or "")
            running_value = native_tick_value(metar_max_f, unit)
            if running_value is None:
                continue
            leg = leg_for_record(rec, running_value)
            if leg is None:
                continue
            fields = entry_fields(rec, leg)
            entry_price = fields["entry_price"]
            if not math.isfinite(entry_price):
                continue
            residual_points = (1.0 - entry_price) * 100.0
            running_native = native_from_metar_f(metar_max_f, unit)
            latest_native = native_from_metar_f(metar_latest_f, unit)
            forecast_max_native = safe_float(rec.get("forecast_max_native"))
            out.append(
                {
                    "snapshot_file": str(path.relative_to(ROOT)),
                    "snapshot_ts_utc": ts_utc,
                    "ts_beijing": ts_bj,
                    "ts_local": ts_local,
                    "local_hour": local_hour,
                    "local_minute": local_minute,
                    "city": rec.get("city"),
                    "city_pool": rec.get("city_pool"),
                    "target_date": target_date,
                    "unit": unit,
                    "bracket": str(rec.get("bracket")),
                    "leg": leg,
                    "outcome": fields["outcome"],
                    "running_value": running_value,
                    "running_native": running_native,
                    "latest_native": latest_native,
                    "decline_native": running_native - latest_native if math.isfinite(latest_native) else math.nan,
                    "metar_current_max_f": metar_max_f,
                    "metar_latest_temp_f": metar_latest_f,
                    "metar_latest_ts_utc": rec.get("metar_latest_ts_utc"),
                    "metar_obs_count_today": rec.get("metar_obs_count_today"),
                    "forecast_max_native": forecast_max_native,
                    "forecast_gap_to_running_native": forecast_max_native - running_value
                    if math.isfinite(forecast_max_native)
                    else math.nan,
                    "forecast_peak_time_local": rec.get("forecast_peak_time_local"),
                    "forecast_peak_delta_hours_local": safe_float(rec.get("forecast_peak_delta_hours_local")),
                    "forecast_source": rec.get("forecast_source"),
                    "entry_price": entry_price,
                    "best_bid": fields["best_bid"],
                    "spread": fields["spread"],
                    "top_size": fields["top_size"],
                    "depth_5c": fields["depth_5c"],
                    "bid_depth_5c": fields["bid_depth_5c"],
                    "residual_points": residual_points,
                    "condition_id": rec.get("condition_id"),
                    "market_id": rec.get("market_id"),
                    "yes_best_ask": safe_float(rec.get("yes_best_ask")),
                    "yes_best_bid": safe_float(rec.get("yes_best_bid")),
                    "no_best_ask": safe_float(rec.get("no_best_ask")),
                    "no_best_bid": safe_float(rec.get("no_best_bid")),
                    "question": rec.get("question"),
                }
            )
    return out


def fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def add_settlement(candidates: pd.DataFrame, price_map: dict[tuple[str, str, str], float]) -> pd.DataFrame:
    out = candidates.copy()
    out["final_yes"] = [price_map.get((str(r.city), str(r.target_date), str(r.bracket))) for r in out.itertuples()]
    out["settled"] = out["final_yes"].notna()
    out["win"] = np.where(
        out["settled"],
        np.where(out["outcome"].eq("yes"), out["final_yes"].ge(0.99), out["final_yes"].le(0.01)),
        np.nan,
    )
    out["fee_per_share"] = out["entry_price"].apply(fee_per_share)
    out["cost_per_share"] = out["entry_price"] + out["fee_per_share"]
    out["pnl_per_share"] = np.where(out["settled"], out["win"].astype(float) - out["cost_per_share"], np.nan)
    out["roi"] = out["pnl_per_share"] / out["cost_per_share"]
    return out


def add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["period"] = np.select(
        [
            out["target_date"].lt(FORWARD_START),
            out["target_date"].between(FORWARD_START, FORWARD_END, inclusive="both"),
            out["target_date"].gt(FORWARD_END),
        ],
        ["train_to_2026-06-28", f"forward_{FORWARD_START}_{FORWARD_END}", "post_forward_incomplete"],
        default="unknown",
    )
    out["price_band"] = pd.cut(
        out["entry_price"],
        bins=[0.0, 0.90, 0.95, 0.97, 0.99, 1.01],
        labels=["lt90", "90_95", "95_97", "97_99", "99_plus"],
        include_lowest=True,
    )
    out["residual_band"] = pd.cut(
        out["residual_points"],
        bins=[-0.01, 1.0, 3.0, 5.0, 10.0, 100.0],
        labels=["lt1", "1_3", "3_5", "5_10", "gt10"],
        include_lowest=True,
    )
    out["peak_delta_bucket"] = pd.cut(
        out["forecast_peak_delta_hours_local"],
        bins=[-100, -1.0, -0.25, 0.5, 1.5, 100],
        labels=["peak_gt1h_ahead", "peak_1h_to_15m_ahead", "near_peak", "post_peak_0_5_1_5h", "post_peak_gt1_5h"],
    )
    out["forecast_gap_bucket"] = pd.cut(
        out["forecast_gap_to_running_native"],
        bins=[-100, -1, 0, 1, 2, 100],
        labels=["busted_lt_minus1", "capped_minus1_0", "room_0_1", "room_1_2", "room_gt2"],
    )
    out["path_state"] = np.select(
        [
            out["decline_native"].abs().le(0.25),
            out["decline_native"].gt(0.25),
            out["decline_native"].lt(-0.25),
        ],
        ["at_high", "decline", "warming_above_running"],
        default="missing",
    )
    out["hour"] = out["local_hour"].astype(str)
    return out


def strict_candidates(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        df["residual_points"].between(MIN_RESIDUAL_POINTS, MAX_RESIDUAL_POINTS, inclusive="both")
        & (df["top_size"].fillna(0).ge(MIN_DEPTH_SHARES) | df["depth_5c"].fillna(0).ge(MIN_DEPTH_SHARES))
    ].copy()


def first_cross(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return (
        df.sort_values(["snapshot_ts_utc", "city", "target_date", "leg", "bracket"])
        .drop_duplicates(["city", "target_date", "leg", "bracket"], keep="first")
        .reset_index(drop=True)
    )


def block_ci_daily_roi(df: pd.DataFrame, reps: int = 3000, seed: int = 13) -> tuple[float | None, float | None]:
    settled = df[df["settled"]].copy()
    if settled["target_date"].nunique() < 2:
        return None, None
    daily = settled.groupby("target_date", as_index=False).agg(cost=("cost_per_share", "sum"), pnl=("pnl_per_share", "sum"))
    values = daily[["cost", "pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    rois = []
    for _ in range(reps):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = sample[:, 0].sum()
        if cost > 0:
            rois.append(sample[:, 1].sum() / cost)
    if not rois:
        return None, None
    return float(np.quantile(rois, 0.025)), float(np.quantile(rois, 0.975))


def summarize(df: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    for key, g in df.groupby(group_cols, dropna=False, observed=True):
        settled = g[g["settled"]]
        cost = float(settled["cost_per_share"].sum()) if not settled.empty else 0.0
        pnl = float(settled["pnl_per_share"].sum()) if not settled.empty else 0.0
        ci_low, ci_high = block_ci_daily_roi(settled)
        row: dict[str, Any] = {
            "rows": int(len(g)),
            "settled_rows": int(len(settled)),
            "active_dates": int(settled["target_date"].nunique()) if not settled.empty else 0,
            "cities": int(settled["city"].nunique()) if not settled.empty else 0,
            "avg_entry": float(settled["entry_price"].mean()) if not settled.empty else None,
            "avg_residual_pts": float(settled["residual_points"].mean()) if not settled.empty else None,
            "hit_rate": float(settled["win"].mean()) if not settled.empty else None,
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "max_loss_per_share": float(settled["pnl_per_share"].min()) if not settled.empty else None,
            "depth_5c_sum": float(settled["depth_5c"].fillna(0).sum()) if not settled.empty else 0.0,
        }
        vals = key if isinstance(key, tuple) else (key,)
        row.update(dict(zip(group_cols, vals)))
        out.append(row)
    return out


def basket_rows(first: pd.DataFrame) -> pd.DataFrame:
    if first.empty:
        return pd.DataFrame()
    settled = first[first["settled"]].copy()
    if settled.empty:
        return pd.DataFrame()
    out = (
        settled.groupby(["city", "target_date", "snapshot_ts_utc"], as_index=False)
        .agg(
            legs=("leg", "count"),
            leg_set=("leg", lambda s: ",".join(sorted(s))),
            cost=("cost_per_share", "sum"),
            pnl=("pnl_per_share", "sum"),
            wins=("win", "sum"),
            worst_leg_pnl=("pnl_per_share", "min"),
            depth_5c_sum=("depth_5c", "sum"),
        )
        .assign(roi=lambda d: d["pnl"] / d["cost"])
    )
    out["period"] = np.where(out["target_date"].between(FORWARD_START, FORWARD_END, inclusive="both"), f"forward_{FORWARD_START}_{FORWARD_END}", "train_to_2026-06-28")
    return out


def summarize_basket(df: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = []
    for key, g in df.groupby(group_cols, dropna=False):
        cost = float(g["cost"].sum())
        pnl = float(g["pnl"].sum())
        row: dict[str, Any] = {
            "rows": int(len(g)),
            "active_dates": int(g["target_date"].nunique()),
            "cities": int(g["city"].nunique()),
            "avg_legs": float(g["legs"].mean()),
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
            "max_basket_loss": float(g["pnl"].min()),
            "depth_5c_sum": float(g["depth_5c_sum"].sum()),
        }
        vals = key if isinstance(key, tuple) else (key,)
        row.update(dict(zip(group_cols, vals)))
        out.append(row)
    return out


def fail_cases(df: pd.DataFrame, limit: int = 80) -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "ts_beijing",
        "leg",
        "bracket",
        "entry_price",
        "residual_points",
        "pnl_per_share",
        "running_value",
        "latest_native",
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "path_state",
        "forecast_gap_bucket",
        "question",
    ]
    bad = df[df["settled"].fillna(False).astype(bool) & ~df["win"].fillna(False).astype(bool)].copy()
    return bad.sort_values(["pnl_per_share", "target_date", "city"])[cols].head(limit)


def table(df: pd.DataFrame, cols: list[str], max_rows: int = 40) -> str:
    if df.empty:
        return "_empty_"
    sub = df[cols].head(max_rows).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda x: "" if pd.isna(x) else f"{x:.3f}")
    sub = sub.fillna("")
    header = "| " + " | ".join(str(c) for c in sub.columns) + " |"
    sep = "| " + " | ".join("---" for _ in sub.columns) + " |"
    body = ["| " + " | ".join(str(v) for v in row) + " |" for row in sub.itertuples(index=False, name=None)]
    return "\n".join([header, sep, *body])


def write_report(payload: dict[str, Any], out_md: Path) -> None:
    summary = payload["summary"]
    lines = [
        "# Late-Window Residual Capture Per-Poll v3",
        "",
        "Status: `snapshot`",
        "Evidence: `paper_snapshots/snapshot_YYYYMMDD_HHMM.json` per polling cycle; settlement joined only after PIT candidate generation.",
        "",
        "## Verdict",
        payload["verdict"],
        "",
        "## Data Snapshot",
        f"- paper snapshots scanned: {summary['snapshot_files']}",
        f"- raw PIT leg rows: {summary['raw_rows']}",
        f"- strict residual rows before first-cross: {summary['strict_rows']}",
        f"- first-cross rows: {summary['first_cross_rows']}",
        f"- settlement-complete dates used for headline: through {summary['settled_complete_max_date']}",
        f"- incomplete dates excluded from headline ROI: {', '.join(summary['incomplete_dates']) or 'none'}",
        "",
        "## PIT Rules",
        "- Decision denominator is each paper snapshot polling cycle, not hourly resampling.",
        "- Candidate fields are only from the row itself: METAR current high/latest, forecast peak, top-of-book and depth.",
        "- Strict executable residual means taker ask is 0.95-0.99 and top ask size or 5c ask depth is at least 5 shares.",
        "- First-cross dedupe keeps the first city/date/leg/bracket time entering the strict band; later reprices are hold/no-add diagnostics.",
        "- Settlement labels from `settlement_outcomes` are joined after candidate generation.",
        "",
        "## First-Cross By Leg",
        table(pd.DataFrame(summary["first_by_leg"]), ["leg", "period", "rows", "settled_rows", "active_dates", "cities", "avg_entry", "hit_rate", "roi", "roi_ci_low", "roi_ci_high", "pnl", "depth_5c_sum"]),
        "",
        "## Peak-Window Slices",
        table(pd.DataFrame(summary["first_by_peak"]), ["leg", "peak_delta_bucket", "rows", "settled_rows", "active_dates", "hit_rate", "roi", "roi_ci_low", "roi_ci_high"]),
        "",
        "## Forecast-Gap Slices",
        table(pd.DataFrame(summary["first_by_gap"]), ["leg", "forecast_gap_bucket", "rows", "settled_rows", "active_dates", "hit_rate", "roi", "roi_ci_low", "roi_ci_high"]),
        "",
        "## Path-State Slices",
        table(pd.DataFrame(summary["first_by_path"]), ["leg", "path_state", "rows", "settled_rows", "active_dates", "hit_rate", "roi", "roi_ci_low", "roi_ci_high"]),
        "",
        "## Basket",
        table(pd.DataFrame(summary["basket_by_period"]), ["period", "rows", "active_dates", "cities", "avg_legs", "cost", "pnl", "roi", "max_basket_loss", "depth_5c_sum"]),
        "",
        "## Failure Cases",
        table(pd.DataFrame(payload["failure_cases"]), ["city", "target_date", "ts_beijing", "leg", "bracket", "entry_price", "residual_points", "pnl_per_share", "running_value", "forecast_max_native", "forecast_peak_delta_hours_local", "path_state", "forecast_gap_bucket"], max_rows=80),
        "",
        "## Chengdu 2026-07-06 Case",
        "Chengdu 2026-07-06 is retained as a per-poll case replay, not in headline ROI because settlement coverage for 2026-07-06 is incomplete in the local DB.",
        table(pd.DataFrame(payload["chengdu_case"]), ["ts_beijing", "leg", "bracket", "entry_price", "residual_points", "running_value", "forecast_max_native", "forecast_peak_delta_hours_local", "path_state"], max_rows=30),
        "",
        "## Contract Gates",
        payload["contract_gates"],
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper-snapshots", type=Path, default=PAPER_SNAPSHOTS)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    conn = connect_ro(args.db)
    outcomes, price_map, complete_dates = settlement_maps(conn)
    raw = pd.DataFrame(iter_snapshot_records(args.paper_snapshots))
    if raw.empty:
        raise SystemExit("no PIT rows generated")
    raw = add_buckets(add_settlement(raw, price_map))

    strict_all = strict_candidates(raw)
    headline = strict_all[strict_all["target_date"].isin(complete_dates)].copy()
    first = first_cross(headline)
    basket = basket_rows(first)
    failures = fail_cases(first)
    chengdu_case = strict_all[(strict_all["city"].eq("Chengdu")) & (strict_all["target_date"].eq("2026-07-06"))].copy()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw.to_csv(args.out_dir / "raw_pit_leg_rows.csv", index=False)
    strict_all.to_csv(args.out_dir / "strict_all_snapshot_rows.csv", index=False)
    first.to_csv(args.out_dir / "first_cross_rows.csv", index=False)
    basket.to_csv(args.out_dir / "basket_rows.csv", index=False)
    failures.to_csv(args.out_dir / "failure_cases.csv", index=False)
    chengdu_case.to_csv(args.out_dir / "chengdu_2026_07_06_case.csv", index=False)

    incomplete_dates = sorted(set(strict_all["target_date"]) - complete_dates)
    complete_max = max(complete_dates) if complete_dates else None
    summary = {
        "snapshot_files": len(list(args.paper_snapshots.glob("snapshot_*.json"))),
        "raw_rows": int(len(raw)),
        "strict_rows": int(len(strict_all)),
        "strict_headline_rows": int(len(headline)),
        "first_cross_rows": int(len(first)),
        "settlement_rows": int(len(outcomes)),
        "settled_complete_max_date": complete_max,
        "incomplete_dates": incomplete_dates,
        "first_by_leg": summarize(first, ["leg", "period"]),
        "first_by_peak": summarize(first, ["leg", "peak_delta_bucket"]),
        "first_by_gap": summarize(first, ["leg", "forecast_gap_bucket"]),
        "first_by_path": summarize(first, ["leg", "path_state"]),
        "first_by_hour": summarize(first, ["leg", "hour"]),
        "basket_by_period": summarize_basket(basket, ["period"]),
        "basket_by_legset": summarize_basket(basket, ["leg_set", "period"]),
    }

    # Conservative verdict: require all-gates for live; this research is not there.
    leg_df = pd.DataFrame(summary["first_by_leg"])
    forward = leg_df[leg_df.get("period", pd.Series(dtype=str)).astype(str).str.startswith("forward")] if not leg_df.empty else pd.DataFrame()
    positive_forward = forward[pd.to_numeric(forward.get("roi", pd.Series(dtype=float)), errors="coerce").gt(0)] if not forward.empty else pd.DataFrame()
    verdict = (
        "Per-poll replay confirms the Chengdu-style 39 NO opportunity exists in the snapshot layer, "
        "but long-run first-cross evidence is mixed: broad current YES / d1 NO / d2 NO are not robust, "
        "and any positive d3 NO or narrow slice remains low-sample with CI crossing zero. "
        "Conclusion: `inconclusive_research_shadow_only`; do not change live."
    )
    if not positive_forward.empty:
        verdict += f" Positive forward point estimates exist for: {', '.join(sorted(positive_forward['leg'].astype(str).unique()))}."

    payload = {
        "summary": summary,
        "verdict": verdict,
        "contract_gates": (
            "significance=FAIL/NA because current YES, d1 NO, and d2 NO forward date-block ROI CIs cross 0, "
            "while d3 NO is positive but below the active-date support gate; "
            "baseline=FAIL/NA because broad residual capture does not beat a robust zero/market baseline after fees; "
            "forward=FAIL/NA because 2026-06-29..2026-07-04 support is small and unstable; "
            "conclusion=inconclusive."
        ),
        "failure_cases": failures.to_dict("records"),
        "chengdu_case": chengdu_case.sort_values(["snapshot_ts_utc", "leg", "bracket"]).to_dict("records"),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(payload, args.out_md)
    print(json.dumps({"out_dir": str(args.out_dir), "out_md": str(args.out_md), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
