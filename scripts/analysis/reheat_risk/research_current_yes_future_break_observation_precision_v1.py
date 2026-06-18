#!/usr/bin/env python3
"""Observation precision audit for current-YES future-break prediction.

This is a research-only companion to the current-YES fade studies.  It avoids
the narrow "reheat" name and audits whether the observed weather feed has enough
resolution/cadence to support predicting if the current running max will be
broken later in the day.
"""

from __future__ import annotations

import glob
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
IEM_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/iem"
REHEAT_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
FUTURE_BREAK_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_future_break_observation_precision_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-future-break-observation-precision-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-19-current-yes-future-break-observation-precision-v1.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:.{digits}f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
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
    finally:
        conn.close()


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin(["true", "1", "1.0", "yes"])
    return out


def load_decision_rows() -> pd.DataFrame:
    cols = [
        "decision_snapshot_ts_utc",
        "decision_last_obs_utc",
        "decision_hour_local",
        "city",
        "target_date",
        "icao",
        "unit",
        "current_temp_c",
        "current_temp_f",
        "current_native",
        "running_native",
        "decline_native",
        "decline_from_max_c",
        "minutes_since_running_max",
        "current_bracket",
        "current_yes_ask",
        "d1_no_ask",
        "current_bracket_held",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "sky_cover_code",
    ]
    df = pd.read_csv(REHEAT_ROWS, usecols=cols)
    df = df.drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"])
    df["current_bracket_held_bool"] = boolish(df["current_bracket_held"]).astype(int)
    df["future_break"] = 1 - df["current_bracket_held_bool"]
    df["decision_snapshot_dt"] = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    df["decision_last_obs_dt"] = pd.to_datetime(df["decision_last_obs_utc"], utc=True, errors="coerce")
    df["decision_obs_age_min"] = (df["decision_snapshot_dt"] - df["decision_last_obs_dt"]).dt.total_seconds() / 60.0
    return df


def current_yes_like(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        df["current_yes_ask"].notna()
        & df["current_bracket"].notna()
        & df["current_bracket_held"].notna()
    ].copy()


def fade_like(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["decline_from_max_c"] >= 0.5)
        & df["current_yes_ask"].notna()
        & df["d1_no_ask"].notna()
        & df["current_bracket_held"].notna()
    ].copy()


def summarize_slice(df: pd.DataFrame, name: str) -> dict[str, Any]:
    if df.empty:
        return {
            "slice": name,
            "rows": 0,
            "city_days": 0,
            "dates": 0,
            "future_break_rate": None,
            "median_obs_age_min": None,
            "p90_obs_age_min": None,
            "median_minutes_since_running_max": None,
        }
    return {
        "slice": name,
        "rows": int(len(df)),
        "city_days": int(df[["city", "target_date"]].drop_duplicates().shape[0]),
        "dates": int(df["target_date"].nunique()),
        "future_break_rate": float(df["future_break"].mean()),
        "median_obs_age_min": float(df["decision_obs_age_min"].median()),
        "p90_obs_age_min": float(df["decision_obs_age_min"].quantile(0.9)),
        "median_minutes_since_running_max": float(df["minutes_since_running_max"].median()),
    }


def load_iem_observations() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path_str in glob.glob(str(IEM_DIR / "iem_v2_*.csv")):
        path = Path(path_str)
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if df.empty or "valid" not in df.columns:
            continue
        temp_col = "tmpc" if "tmpc" in df.columns else "tmpf" if "tmpf" in df.columns else None
        if temp_col is None:
            continue
        df = df[["station", "valid", temp_col]].rename(columns={temp_col: "temp_value"})
        df["temp_value"] = pd.to_numeric(df["temp_value"], errors="coerce")
        df["temp_unit_in_cache"] = "C" if temp_col == "tmpc" else "F"
        df["source_file"] = path.name
        df["valid_dt"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
        df = df[df["valid_dt"].notna() & df["temp_value"].notna()].copy()
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    obs = pd.concat(frames, ignore_index=True)
    obs = obs.drop_duplicates(["station", "valid_dt", "temp_unit_in_cache", "temp_value"])
    obs["date"] = obs["valid_dt"].dt.date.astype(str)
    return obs.sort_values(["station", "valid_dt"])


def station_precision(obs: pd.DataFrame, city_station: pd.DataFrame) -> pd.DataFrame:
    if obs.empty:
        return pd.DataFrame()
    obs = obs.merge(city_station, how="left", left_on="station", right_on="iem_station")
    obs["fractional_abs"] = np.abs(obs["temp_value"] - np.round(obs["temp_value"]))
    obs["is_integer_temp"] = obs["fractional_abs"] < 1e-6
    obs["is_tenth_temp"] = np.abs(obs["temp_value"] * 10 - np.round(obs["temp_value"] * 10)) < 1e-6
    rows = []
    for (station, unit, market_unit), d in obs.groupby(["station", "temp_unit_in_cache", "unit"], dropna=False):
        d = d.sort_values("valid_dt")
        deltas = d["valid_dt"].diff().dt.total_seconds().div(60)
        daily_counts = d.groupby("date").size()
        rows.append(
            {
                "station": station,
                "city": ",".join(sorted(set(d["city"].dropna().astype(str)))) or None,
                "cache_temp_unit": unit,
                "market_unit": market_unit,
                "obs_rows": int(len(d)),
                "dates": int(daily_counts.shape[0]),
                "integer_temp_share": float(d["is_integer_temp"].mean()),
                "tenth_temp_share": float(d["is_tenth_temp"].mean()),
                "median_obs_per_day": float(daily_counts.median()),
                "p10_obs_per_day": float(daily_counts.quantile(0.1)),
                "median_gap_min": float(deltas.dropna().median()) if deltas.notna().any() else None,
                "p90_gap_min": float(deltas.dropna().quantile(0.9)) if deltas.notna().any() else None,
                "share_gap_le_35m": float((deltas.dropna() <= 35).mean()) if deltas.notna().any() else None,
                "share_gap_gt_65m": float((deltas.dropna() > 65).mean()) if deltas.notna().any() else None,
            }
        )
    return pd.DataFrame(rows).sort_values(["market_unit", "station"])


def aggregate_precision(precision: pd.DataFrame) -> pd.DataFrame:
    if precision.empty:
        return pd.DataFrame()
    rows = []
    known = precision[precision["market_unit"].notna()].copy()
    for market_unit, d in known.groupby("market_unit", dropna=False):
        weights = d["obs_rows"].to_numpy()
        def wavg(col: str) -> float | None:
            vals = pd.to_numeric(d[col], errors="coerce").to_numpy()
            mask = np.isfinite(vals) & np.isfinite(weights)
            if not mask.any() or weights[mask].sum() == 0:
                return None
            return float(np.average(vals[mask], weights=weights[mask]))

        rows.append(
            {
                "market_unit": market_unit,
                "stations": int(d["station"].nunique()),
                "obs_rows": int(d["obs_rows"].sum()),
                "weighted_integer_temp_share": wavg("integer_temp_share"),
                "weighted_tenth_temp_share": wavg("tenth_temp_share"),
                "median_station_obs_per_day": float(d["median_obs_per_day"].median()),
                "median_station_gap_min": float(d["median_gap_min"].median()),
                "median_station_p90_gap_min": float(d["p90_gap_min"].median()),
                "weighted_share_gap_le_35m": wavg("share_gap_le_35m"),
                "weighted_share_gap_gt_65m": wavg("share_gap_gt_65m"),
            }
        )
    return pd.DataFrame(rows)


def binned_future_break(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    bin_specs = [
        (
            "decision_obs_age_min",
            pd.cut(work["decision_obs_age_min"], [-np.inf, 5, 15, 30, 45, 65, np.inf], labels=["<=5m", "5-15m", "15-30m", "30-45m", "45-65m", ">65m"]),
        ),
        (
            "minutes_since_running_max",
            pd.cut(work["minutes_since_running_max"], [-np.inf, 15, 30, 60, 120, np.inf], labels=["<15m", "15-30m", "30-60m", "60-120m", "120m+"]),
        ),
        (
            "gfs_peak_clock_delta",
            pd.cut(
                work["gfs_forecast_peak_delta_hours_local"],
                [-np.inf, -1, 0, 1, 2, np.inf],
                labels=["forecast_peak_future>1h", "future0-1h", "now_or_past0-1h", "past1-2h", "past2h+"],
            ),
        ),
        (
            "temp_trend_1h_f",
            pd.cut(work["temp_trend_1h_f"], [-np.inf, -2, -0.5, 0.5, 2, np.inf], labels=["fall>2F", "fall0.5-2F", "flat", "rise0.5-2F", "rise>2F"]),
        ),
    ]
    rows = []
    for feature, bucket in bin_specs:
        temp = work.assign(bucket=bucket.astype(str))
        for bucket_name, d in temp.groupby("bucket", dropna=False):
            if bucket_name == "nan":
                continue
            rows.append(
                {
                    "feature": feature,
                    "bucket": bucket_name,
                    "rows": int(len(d)),
                    "city_days": int(d[["city", "target_date"]].drop_duplicates().shape[0]),
                    "future_break_rate": float(d["future_break"].mean()),
                    "avg_current_yes_ask": float(d["current_yes_ask"].mean()),
                    "median_obs_age_min": float(d["decision_obs_age_min"].median()),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    FUTURE_BREAK_DIR.mkdir(parents=True, exist_ok=True)
    decisions = load_decision_rows()
    current_yes = current_yes_like(decisions)
    fade = fade_like(decisions)

    city_station = decisions[["city", "icao", "unit"]].dropna().drop_duplicates()
    aliases = [city_station.assign(iem_station=city_station["icao"])]
    us_mask = city_station["icao"].astype(str).str.startswith("K") & (city_station["icao"].astype(str).str.len() == 4)
    aliases.append(city_station.loc[us_mask].assign(iem_station=city_station.loc[us_mask, "icao"].astype(str).str[1:]))
    city_station = pd.concat(aliases, ignore_index=True).drop_duplicates(["iem_station", "city", "unit"])
    obs = load_iem_observations()
    precision_by_station = station_precision(obs, city_station)
    precision_by_unit = aggregate_precision(precision_by_station)
    future_break_bins = binned_future_break(fade)

    sample_funnel = pd.DataFrame(
        [
            summarize_slice(decisions, "all_reheat_factory_states"),
            summarize_slice(current_yes, "current_yes_like_states"),
            summarize_slice(fade, "fade_like_current_yes_states"),
            summarize_slice(fade[fade["unit"] == "C"], "fade_like_celsius_markets"),
            summarize_slice(fade[fade["unit"] == "F"], "fade_like_fahrenheit_markets"),
        ]
    )

    sample_funnel.to_csv(FUTURE_BREAK_DIR / "sample_funnel.csv", index=False)
    precision_by_station.to_csv(FUTURE_BREAK_DIR / "observation_precision_by_station.csv", index=False)
    precision_by_unit.to_csv(FUTURE_BREAK_DIR / "observation_precision_by_unit.csv", index=False)
    future_break_bins.to_csv(FUTURE_BREAK_DIR / "future_break_feature_bins.csv", index=False)

    headline = {
        "current_yes_rows": int(sample_funnel.loc[sample_funnel["slice"] == "current_yes_like_states", "rows"].iloc[0]),
        "fade_rows": int(sample_funnel.loc[sample_funnel["slice"] == "fade_like_current_yes_states", "rows"].iloc[0]),
        "fade_future_break_rate": float(sample_funnel.loc[sample_funnel["slice"] == "fade_like_current_yes_states", "future_break_rate"].iloc[0]),
        "fade_median_obs_age_min": float(sample_funnel.loc[sample_funnel["slice"] == "fade_like_current_yes_states", "median_obs_age_min"].iloc[0]),
        "fade_p90_obs_age_min": float(sample_funnel.loc[sample_funnel["slice"] == "fade_like_current_yes_states", "p90_obs_age_min"].iloc[0]),
    }
    for _, row in precision_by_unit.iterrows():
        unit = str(row["market_unit"])
        headline[f"{unit}_stations"] = int(row["stations"])
        headline[f"{unit}_weighted_integer_temp_share"] = row["weighted_integer_temp_share"]
        headline[f"{unit}_median_station_gap_min"] = row["median_station_gap_min"]
        headline[f"{unit}_weighted_share_gap_le_35m"] = row["weighted_share_gap_le_35m"]

    report = {
        "created_at_utc": now_utc(),
        "scope": "research_only_current_yes_future_break_observation_precision",
        "data_self_check": data_self_check(),
        "inputs": {
            "reheat_feature_rows": str(REHEAT_ROWS.relative_to(ROOT)),
            "iem_cache_dir": str(IEM_DIR.relative_to(ROOT)),
        },
        "headline": headline,
        "sample_funnel": sample_funnel.to_dict(orient="records"),
        "observation_precision_by_unit": precision_by_unit.to_dict(orient="records"),
        "feature_bins": future_break_bins.to_dict(orient="records"),
        "external_source_notes": [
            "FMH-1 says temperature is determined to nearest tenth C but METAR body reports whole C; remarks can report tenths C at designated stations.",
            "ASOS User Guide says ASOS provides 5-minute average ambient/dew point temperature every minute and stores 1/5-minute values; ordinary METAR dissemination may still be hourly/half-hourly by station/feed.",
            "NCEI notes ASOS stations work continuously and NCEI archives one- and five-minute, hourly, summary, and special observations.",
        ],
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    md = f"""# Current-YES future-break observation precision v1

Created: {report["created_at_utc"]}

## Target

Research-only.  Rename the bad event from narrow `reheat` to `future_break`: after the current running max is visible, does a later official observation print a higher bracket?  This can be ordinary continued warming, a forecast peak that was too low, or a true late re-warm.

## Data self-check

- `fact_trades` max built at: `{report["data_self_check"]["fact_trades_max_built_at_utc"]}`
- `fact_signal_candidates`: `{report["data_self_check"]["fact_signal_candidate_coverage"]}`
- CLOB order/fill join: `{report["data_self_check"]["clob_order_fill_join"]}`

## Sample funnel

| slice | rows | city_days | dates | future_break_rate | median_obs_age_min | p90_obs_age_min |
|---|---:|---:|---:|---:|---:|---:|
"""
    for row in sample_funnel.to_dict(orient="records"):
        md += (
            f"| {row['slice']} | {row['rows']} | {row['city_days']} | {row['dates']} | "
            f"{pct(row['future_break_rate'])} | {num(row['median_obs_age_min'])} | {num(row['p90_obs_age_min'])} |\n"
        )

    md += """
## Observation precision by market unit

| market_unit | stations | obs_rows | integer_temp_share | median_obs_per_day | median_gap_min | gap<=35m_share | gap>65m_share |
|---|---:|---:|---:|---:|---:|---:|---:|
"""
    for row in precision_by_unit.to_dict(orient="records"):
        md += (
            f"| {row['market_unit']} | {row['stations']} | {row['obs_rows']} | "
            f"{pct(row['weighted_integer_temp_share'])} | {num(row['median_station_obs_per_day'])} | "
            f"{num(row['median_station_gap_min'])} | {pct(row['weighted_share_gap_le_35m'])} | {pct(row['weighted_share_gap_gt_65m'])} |\n"
        )

    md += """
## Future-break feature bins

| feature | bucket | rows | future_break_rate | avg_current_yes_ask | median_obs_age_min |
|---|---:|---:|---:|---:|---:|
"""
    focus = {
        "decision_obs_age_min": ["<=5m", "5-15m", "15-30m", "30-45m", "45-65m"],
        "minutes_since_running_max": ["<15m", "15-30m", "30-60m", "60-120m", "120m+"],
        "gfs_peak_clock_delta": ["forecast_peak_future>1h", "future0-1h", "now_or_past0-1h", "past1-2h", "past2h+"],
        "temp_trend_1h_f": ["rise>2F", "rise0.5-2F", "flat", "fall0.5-2F"],
    }
    for feature, buckets in focus.items():
        for bucket in buckets:
            rows = future_break_bins[(future_break_bins["feature"] == feature) & (future_break_bins["bucket"] == bucket)]
            if rows.empty:
                continue
            row = rows.iloc[0].to_dict()
            md += (
                f"| {feature} | {bucket} | {int(row['rows'])} | {pct(row['future_break_rate'])} | "
                f"{num(row['avg_current_yes_ask'], 3)} | {num(row['median_obs_age_min'])} |\n"
            )

    md += """
## Readout

- The right target is not strictly `reheat`; it is `future_break`.  A loss can happen because the day was still climbing, because the forecast peak was low, or because a late secondary warm-up occurred.
- The observation layer is good enough to support a probabilistic model, but not a deterministic one-degree call.  The public METAR body is whole-degree Celsius while the sensor/remarks layer can be tenths; for 1C brackets, the rounding boundary is material.
- Cadence is modelable but not free.  A decision made just after a stale or just-crossed observation has much higher uncertainty than one made after the peak clock has passed and the temperature has been flat/falling for a while.
- Practical implication: live signals should log both `p_hold` and `p_future_break`, plus observation age, station cadence class, and forecast peak clock.  Use these as a risk overlay/veto before trying a standalone specialist model.

## External standard notes

- FMH-1: temperature is observed to nearest tenth Celsius, but METAR body reporting resolution is whole Celsius; remarks can carry tenths at designated stations.
- ASOS User Guide: ASOS computes short averaged temperature values frequently; local dissemination/cache may still expose only METAR cadence.
- NCEI: ASOS stations operate continuously and archives include one-/five-minute, hourly, daily summary and special observations.

## Artifacts

- `sample_funnel.csv`
- `observation_precision_by_station.csv`
- `observation_precision_by_unit.csv`
- `future_break_feature_bins.csv`
"""
    OUT_MD.write_text(md)

    print(json.dumps(headline, ensure_ascii=False, indent=2))
    print(f"Wrote {FUTURE_BREAK_DIR.relative_to(ROOT)}")
    print(f"Wrote {OUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
