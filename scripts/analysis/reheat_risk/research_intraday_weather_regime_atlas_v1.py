#!/usr/bin/env python3
"""Intraday weather regime atlas v1.

This is a mechanism atlas, not a strategy optimizer.  Labels describe the
city/date/hour weather structure using point-in-time features; realized max and
quote payoff columns are added only for explanation and calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools import regime_routed_no_stable as _stable  # noqa: E402
from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1 as CITY_FAMILY  # noqa: E402

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-intraday-weather-regime-atlas-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-24-intraday-weather-regime-atlas-v1.json"
DEFAULT_FEATURE_ROWS = [
    OUT_DIR / "feature_factory_20260519_20260620/reheat_feature_rows.csv",
    OUT_DIR / "feature_factory_20260621_20260623/reheat_feature_rows.csv",
]


# Pre-analysis mechanism grouping reused from city-climate forensics.  This is
# descriptive context, not a selected trading rule.

STATE_KEYS = ["city", "target_date", "decision_hour_local"]
STATE_VALUE_COLS = [
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
    "final_max_c",
    "final_max_f",
    "final_winning_bracket",
    "current_bracket",
    "current_yes_ask",
    "d1_no_bracket",
    "d1_no_ask",
    "d1_no_ask_size",
    "d1_no_bid",
    "d1_no_spread",
    "d2_no_bracket",
    "d2_no_ask",
    "d2_no_ask_size",
    "d2_no_bid",
    "d2_no_spread",
    "current_bracket_held",
    "d1_hit",
    "d2_hit",
    "skip_over_d1",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_join_status",
    "forecast_source",
    "forecast_clock_source",
    "forecast_max_native",
    "forecast_max_f",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_max_native",
    "ecmwf_forecast_max_native",
    "forecast_peak_models_agree_le_1h",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--feature-rows",
        nargs="+",
        default=None,
        help="Explicit feature-row shards. Defaults to all feature_factory_*/reheat_feature_rows.csv shards.",
    )
    parser.add_argument("--db", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    return f"{float(value) * 100:.1f}%"


def num(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def query_one(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    row = conn.execute(sql).fetchone()
    return dict(row) if row is not None else {}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
    return row is not None


def optional_table_summary(conn: sqlite3.Connection, table: str, date_col: str) -> dict[str, Any]:
    if not table_exists(conn, table):
        return {"rows": 0, f"min_{date_col}": None, f"max_{date_col}": None, "table_missing": True}
    out = query_one(
        conn,
        f"SELECT COUNT(*) AS rows, MIN({date_col}) AS min_{date_col}, MAX({date_col}) AS max_{date_col} FROM {table}",
    )
    out["table_missing"] = False
    return out


def load_settlement_winners(db_path: Path) -> pd.DataFrame:
    with connect_ro(db_path) as conn:
        if not table_exists(conn, "settlement_outcomes"):
            return pd.DataFrame(columns=["city", "target_date", "settlement_final_winning_bracket"])
        return pd.read_sql_query(
            """
            SELECT
              city,
              target_date,
              bracket AS settlement_final_winning_bracket
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
              AND final_price >= 0.5
            """,
            conn,
        ).drop_duplicates(["city", "target_date"], keep="last")


def add_settlement_winner_context(states: pd.DataFrame, db_path: Path) -> tuple[pd.DataFrame, int]:
    winners = load_settlement_winners(db_path)
    if winners.empty:
        return states, 0
    out = states.merge(winners, on=["city", "target_date"], how="left", validate="many_to_one")
    current = out.get("final_winning_bracket")
    if current is None:
        out["final_winning_bracket"] = np.nan
        missing = pd.Series(True, index=out.index)
    else:
        missing = current.isna() | current.astype(str).str.strip().isin(["", "nan", "None"])
    fillable = missing & out["settlement_final_winning_bracket"].notna()
    out.loc[fillable, "final_winning_bracket"] = out.loc[fillable, "settlement_final_winning_bracket"]
    out = out.drop(columns=["settlement_final_winning_bracket"])
    return out, int(fillable.sum())


def recompute_settlement_labels(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    if "final_winning_bracket" not in out:
        return out
    winner = out["final_winning_bracket"].astype("object")
    has_winner = winner.notna() & ~winner.astype(str).str.strip().isin(["", "nan", "None"])
    label_pairs = [
        ("current_bracket", "current_bracket_held"),
        ("d1_no_bracket", "d1_hit"),
        ("d2_no_bracket", "d2_hit"),
        ("lottery_yes_bracket", "lottery_yes_hit"),
    ]
    for bracket_col, label_col in label_pairs:
        if bracket_col not in out:
            continue
        out[label_col] = np.where(has_winner, winner.astype(str).eq(out[bracket_col].astype(str)), np.nan)
    if {"current_bracket_held", "d1_hit", "d1_no_bracket"}.issubset(out.columns):
        current_held = pd.Series(out["current_bracket_held"]).fillna(False).astype(bool)
        d1_hit = pd.Series(out["d1_hit"]).fillna(False).astype(bool)
        out["skip_over_d1"] = np.where(has_winner, (~current_held) & (~d1_hit) & out["d1_no_bracket"].notna(), np.nan)
    return out


def load_gate() -> dict[str, Any]:
    path = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
    if not path.exists():
        return {"gate_pass": None, "path": str(path.relative_to(ROOT))}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path.relative_to(ROOT)),
        "gate_pass": data.get("gate_pass"),
        "fact_trades_live_real": data.get("fact_trades_live_real"),
        "fail_reasons": data.get("fail_reasons"),
    }


def discover_feature_rows(out_dir: Path) -> list[Path]:
    paths = sorted(out_dir.glob("feature_factory_*/reheat_feature_rows.csv"))
    if paths:
        return paths
    return [path for path in DEFAULT_FEATURE_ROWS if path.exists()]


def load_features(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        df = pd.read_csv(path, low_memory=False)
        df["feature_source_file"] = str(path.relative_to(ROOT) if path.is_absolute() else path)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["target_date"] = out["target_date"].astype(str)
    out["decision_snapshot_sort"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    out = out.sort_values(STATE_KEYS + ["decision_snapshot_sort", "bracket", "outcome"])
    return out.drop_duplicates(STATE_KEYS + ["bracket", "outcome"], keep="last").reset_index(drop=True)


def first_state_rows(rows: pd.DataFrame) -> pd.DataFrame:
    existing_cols = [col for col in STATE_VALUE_COLS if col in rows.columns]
    base = rows.sort_values(STATE_KEYS + ["decision_snapshot_sort"]).groupby(STATE_KEYS, as_index=False).tail(1)
    base = base[STATE_KEYS + existing_cols].drop_duplicates(STATE_KEYS).reset_index(drop=True)

    counts = rows.groupby(STATE_KEYS, as_index=False).agg(
        feature_quote_rows=("bracket", "size"),
        bracket_count=("bracket", "nunique"),
        outcome_count=("outcome", "nunique"),
        source_files=("feature_source_file", lambda s: ",".join(sorted(set(map(str, s))))),
    )
    return base.merge(counts, on=STATE_KEYS, how="left")


def add_expression_quotes(states: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    cur_no = rows[
        rows["outcome"].astype(str).str.lower().eq("no")
        & rows["bracket"].astype(str).eq(rows["current_bracket"].astype(str))
    ].copy()
    cur_no = (
        cur_no.sort_values(STATE_KEYS + ["quote_best_ask"])
        .drop_duplicates(STATE_KEYS)
        [STATE_KEYS + ["quote_best_ask", "quote_best_ask_size", "quote_best_bid", "quote_spread"]]
        .rename(
            columns={
                "quote_best_ask": "current_no_ask",
                "quote_best_ask_size": "current_no_ask_size",
                "quote_best_bid": "current_no_bid",
                "quote_spread": "current_no_spread",
            }
        )
    )
    out = out.merge(cur_no, on=STATE_KEYS, how="left")

    yes = rows[rows["outcome"].astype(str).str.lower().eq("yes")].copy()
    yes["running_value_num"] = pd.to_numeric(yes["running_value"], errors="coerce")
    yes["bracket_low_num"] = pd.to_numeric(yes["bracket_low"], errors="coerce")
    higher_yes = yes[yes["bracket_low_num"].gt(yes["running_value_num"])].copy()
    lottery_cols = [
        *STATE_KEYS,
        "bracket",
        "quote_best_ask",
        "quote_best_ask_size",
        "quote_best_bid",
        "quote_spread",
        "target_hit",
    ]
    higher_yes = (
        higher_yes.sort_values(STATE_KEYS + ["quote_best_ask", "bracket_low_num"])
        .drop_duplicates(STATE_KEYS)
        .loc[:, lottery_cols]
        .rename(
            columns={
                "bracket": "lottery_yes_bracket",
                "quote_best_ask": "lottery_yes_ask",
                "quote_best_ask_size": "lottery_yes_ask_size",
                "quote_best_bid": "lottery_yes_bid",
                "quote_spread": "lottery_yes_spread",
                "target_hit": "lottery_yes_hit",
            }
        )
    )
    return out.merge(higher_yes, on=STATE_KEYS, how="left")


def unit_step(row: pd.Series) -> float:
    return 1.0 if str(row.get("unit", "")).upper() == "F" else 0.5


def final_native(row: pd.Series) -> float:
    return float(row["final_max_f"]) if str(row.get("unit", "")).upper() == "F" else float(row["final_max_c"])


def add_pit_context(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    out["forecast_gap_to_running_native"] = pd.to_numeric(out["forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )
    out["gfs_gap_to_running_native"] = pd.to_numeric(out["gfs_forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )
    out["ecmwf_gap_to_running_native"] = pd.to_numeric(out["ecmwf_forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )
    return out


def add_realized_context(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    out["final_max_native"] = out.apply(lambda r: final_native(r) if pd.notna(r.get("final_max_f")) else np.nan, axis=1)
    out["remaining_heat_native"] = pd.to_numeric(out["final_max_native"], errors="coerce") - pd.to_numeric(
        out["running_native"], errors="coerce"
    )
    step = out.apply(unit_step, axis=1)
    out["future_break_any"] = out["remaining_heat_native"].gt(0)
    out["future_break_step"] = out["remaining_heat_native"].ge(step)
    out["capped_day"] = out["current_bracket_held"].astype("float").eq(1.0)
    out["forecast_error_native"] = pd.to_numeric(out["forecast_max_native"], errors="coerce") - out["final_max_native"]
    return out


def label_solar_window(hour: float) -> str:
    if pd.isna(hour):
        return "hour_missing"
    if hour <= 11:
        return "late_morning"
    if hour <= 14:
        return "solar_peak_window"
    if hour <= 17:
        return "afternoon_decay_window"
    return "evening_tail"


def label_day_space(row: pd.Series) -> str:
    gap = row.get("forecast_gap_to_running_native")
    if pd.isna(gap):
        return "day_space_unknown"
    step = unit_step(row)
    if gap >= 3 * step:
        return "day_open_runway"
    if gap >= step:
        return "day_marginal_runway"
    if gap >= -step:
        return "day_forecast_capped"
    return "day_forecast_busted"


def label_moisture_cloud(row: pd.Series) -> str:
    rh = row.get("relative_humidity_pct")
    sky = row.get("sky_cover_code")
    dew_dep = row.get("dewpoint_depression_f")
    if pd.isna(rh) and pd.isna(sky) and pd.isna(dew_dep):
        return "moisture_cloud_unknown"
    if pd.notna(rh) and rh >= 80 and pd.notna(sky) and sky >= 3:
        return "humid_overcast_suppression"
    if pd.notna(rh) and rh >= 75:
        return "humid_convective_risk"
    if pd.notna(sky) and sky >= 3:
        return "cloud_suppression"
    if pd.notna(dew_dep) and dew_dep >= 25:
        return "dry_heat_inertia"
    return "mixed_moisture"


def label_wind_noise(row: pd.Series) -> str:
    wind = row.get("wind_speed_kt")
    if pd.isna(wind):
        return "wind_unknown"
    if wind >= 18:
        return "windy_mixing_noise"
    if wind >= 10:
        return "moderate_wind"
    return "light_wind"


def label_running_state(row: pd.Series) -> str:
    mins = row.get("minutes_since_running_max")
    decline = row.get("decline_native")
    step = unit_step(row)
    if pd.isna(mins) or pd.isna(decline):
        return "running_max_clock_unknown"
    if decline <= 0.25 * step and mins <= 45:
        return "fresh_running_high"
    if decline <= 0.5 * step and mins <= 120:
        return "near_high_plateau"
    if decline <= 0.5 * step:
        return "stalled_high"
    if mins >= 120:
        return "mature_fade"
    return "pullback_from_high"


def label_intraday_state(row: pd.Series) -> str:
    trend1 = row.get("temp_trend_1h_f")
    trend3 = row.get("temp_trend_3h_f")
    decline = row.get("decline_native")
    mins = row.get("minutes_since_running_max")
    hour = row.get("decision_hour_local")
    step = unit_step(row)
    if pd.isna(trend1) and pd.isna(trend3):
        return "state_unknown"
    if pd.notna(trend1) and trend1 >= 1.0 and (pd.isna(decline) or decline <= step):
        return "active_warming"
    if pd.notna(decline) and decline <= 0.25 * step and pd.notna(mins) and mins <= 60:
        return "fresh_high"
    if pd.notna(decline) and decline <= 0.5 * step and pd.notna(mins) and mins > 60:
        return "plateau_near_high"
    if pd.notna(decline) and decline > 0.5 * step:
        if pd.notna(hour) and hour <= 14 and pd.notna(trend3) and trend3 > 0:
            return "false_fade_risk"
        if pd.notna(trend1) and trend1 > 0:
            return "reheating_after_dip"
        if pd.notna(mins) and mins >= 120:
            return "mature_fade"
        return "pullback_uncertain"
    if pd.notna(trend3) and trend3 <= 0:
        return "flat_or_cooling"
    return "slow_warming"


def add_regime_labels(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    out["solar_window"] = out["decision_hour_local"].apply(label_solar_window)
    out["day_regime"] = out.apply(label_day_space, axis=1)
    out["moisture_cloud_regime"] = out.apply(label_moisture_cloud, axis=1)
    out["wind_regime"] = out.apply(label_wind_noise, axis=1)
    out["running_max_state"] = out.apply(label_running_state, axis=1)
    out["intraday_state"] = out.apply(label_intraday_state, axis=1)
    out["composite_regime"] = (
        out["day_regime"].astype(str)
        + " | "
        + out["intraday_state"].astype(str)
        + " | "
        + out["moisture_cloud_regime"].astype(str)
    )
    return out


unit_step = _stable.unit_step
label_solar_window = _stable.label_solar_window
label_day_space = _stable.label_day_space
label_moisture_cloud = _stable.label_moisture_cloud
label_wind_noise = _stable.label_wind_noise
label_running_state = _stable.label_running_state
label_intraday_state = _stable.label_intraday_state
add_regime_labels = _stable.add_regime_labels


def add_expression_payoffs(states: pd.DataFrame) -> pd.DataFrame:
    out = states.copy()
    definitions = [
        ("current_yes", "current_yes_ask", "current_bracket_held"),
        ("current_bracket_no", "current_no_ask", None),
        ("d1_no", "d1_no_ask", None),
        ("d2_no", "d2_no_ask", None),
        ("lottery_yes", "lottery_yes_ask", "lottery_yes_hit"),
    ]
    for name, ask_col, hit_col in definitions:
        ask = pd.to_numeric(out.get(ask_col), errors="coerce")
        if hit_col is not None:
            payoff = pd.to_numeric(out.get(hit_col), errors="coerce")
        elif name == "current_bracket_no":
            payoff = 1.0 - pd.to_numeric(out.get("current_bracket_held"), errors="coerce")
        elif name == "d1_no":
            payoff = 1.0 - pd.to_numeric(out.get("d1_hit"), errors="coerce")
        elif name == "d2_no":
            payoff = 1.0 - pd.to_numeric(out.get("d2_hit"), errors="coerce")
        else:
            payoff = pd.Series(np.nan, index=out.index)
        out[f"{name}_ask"] = ask
        out[f"{name}_payoff"] = payoff
        out[f"{name}_unit_pnl"] = payoff - ask
        out[f"{name}_roi"] = np.where(ask.gt(0), (payoff - ask) / ask, np.nan)
    return out


def summarize_group(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for key, g in frame.groupby(group_col, dropna=False):
        row = {
            group_col: str(key),
            "state_rows": int(len(g)),
            "cities": int(g["city"].nunique()),
            "active_dates": int(g["target_date"].nunique()),
            "reheat_any_rate": float(g["future_break_any"].mean()),
            "reheat_step_rate": float(g["future_break_step"].mean()),
            "capped_day_rate": float(g["capped_day"].mean()),
            "avg_remaining_heat_native": float(g["remaining_heat_native"].mean()),
            "avg_forecast_error_native": float(g["forecast_error_native"].mean()),
        }
        for expr in ["current_yes", "current_bracket_no", "d1_no", "d2_no", "lottery_yes"]:
            mask = g[f"{expr}_ask"].notna() & g[f"{expr}_payoff"].notna()
            row[f"{expr}_n"] = int(mask.sum())
            row[f"{expr}_hit_rate"] = float(g.loc[mask, f"{expr}_payoff"].mean()) if mask.any() else np.nan
            row[f"{expr}_roi"] = float(g.loc[mask, f"{expr}_unit_pnl"].sum() / g.loc[mask, f"{expr}_ask"].sum()) if mask.any() else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values(["state_rows", group_col], ascending=[False, True]).reset_index(drop=True)


def expression_matrix(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for key, g in frame.groupby(group_col, dropna=False):
        for expr in ["current_yes", "current_bracket_no", "d1_no", "d2_no", "lottery_yes"]:
            mask = g[f"{expr}_ask"].notna() & g[f"{expr}_payoff"].notna()
            sub = g.loc[mask]
            if sub.empty:
                continue
            rows.append(
                {
                    group_col: str(key),
                    "expression": expr,
                    "state_rows": int(len(sub)),
                    "active_dates": int(sub["target_date"].nunique()),
                    "avg_ask": float(sub[f"{expr}_ask"].mean()),
                    "hit_rate": float(sub[f"{expr}_payoff"].mean()),
                    "unit_pnl": float(sub[f"{expr}_unit_pnl"].sum()),
                    "cost": float(sub[f"{expr}_ask"].sum()),
                    "roi": float(sub[f"{expr}_unit_pnl"].sum() / sub[f"{expr}_ask"].sum()),
                }
            )
    return pd.DataFrame(rows).sort_values([group_col, "expression"]).reset_index(drop=True)


def crosstab_rates(frame: pd.DataFrame, rows_col: str, cols_col: str) -> pd.DataFrame:
    table = pd.crosstab(frame[rows_col], frame[cols_col], normalize="index")
    return table.reset_index().sort_values(rows_col)


def to_records(frame: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    view = frame if limit is None else frame.head(limit)
    return json.loads(view.replace({np.nan: None}).to_json(orient="records"))


def write_report(payload: dict[str, Any], out_md: Path) -> None:
    f = payload["funnel"]
    day = payload["top_day_regimes"]
    intra = payload["top_intraday_states"]
    expr = payload["expression_by_day_regime"]
    lines = [
        "# Intraday Weather Regime Atlas v1",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`runtime/weather.db` + atlas feature-factory 分片；CLOB coverage gate=`{payload['clob_gate'].get('gate_pass')}`。",
        f"- 生成时间 UTC：`{payload['generated_at_utc']}`。",
        f"- feature rows：`{f['feature_rows']}`；state rows(city/date/hour)：`{f['state_rows']}`；日期：`{f['min_target_date']}`..`{f['max_target_date']}`。",
        f"- 覆盖城市：`{f['cities']}`；小时：`{f['decision_hours']}`。",
        f"- fact 自检：`fact_signal_candidates` {payload['db_self_check']['fact_signal_candidates_rows']} rows / max event_date `{payload['db_self_check']['fact_signal_candidates_max_event_date']}`；`settlement_outcomes` max target_date `{payload['db_self_check']['settlement_outcomes_max_target_date']}`。",
        "",
        "## 结论",
        "",
        "第一版 atlas 已经把日内天气状态从单策略里抽出来：每个 city/date/hour 有 `day_regime`、`intraday_state`、`moisture_cloud_regime`、`running_max_state` 和 `composite_regime`。这些 label 是天气结构，不等于买/不买。",
        "",
        "最重要的事实是：同一个 regime 对不同表达的含义相反。`day_open_runway` 描述的是物理上更容易继续打穿，不等于 YES 或 lottery 自动有正 EV；`day_forecast_capped` / `day_forecast_busted` 描述的是封顶结构，也不等于 NO 自动便宜。payoff 表只用于校准这些天气结构是否已被市场价格吸收，不是 live gate。",
        "",
        "## Top Day Regimes",
        "",
        "| day_regime | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi | lottery_yes_roi |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in day[:12]:
        lines.append(
            f"| `{row['day_regime']}` | {row['state_rows']} | {pct(row['reheat_step_rate'])} | {pct(row['capped_day_rate'])} | {num(row['avg_remaining_heat_native'])} | {pct(row.get('current_yes_roi'))} | {pct(row.get('current_bracket_no_roi'))} | {pct(row.get('d1_no_roi'))} | {pct(row.get('lottery_yes_roi'))} |"
        )
    lines.extend(
        [
            "",
            "## Top Intraday States",
            "",
            "| intraday_state | states | reheat_step | capped_day | avg_remaining | current_yes_roi | current_bracket_no_roi | d1_no_roi |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in intra[:12]:
        lines.append(
            f"| `{row['intraday_state']}` | {row['state_rows']} | {pct(row['reheat_step_rate'])} | {pct(row['capped_day_rate'])} | {num(row['avg_remaining_heat_native'])} | {pct(row.get('current_yes_roi'))} | {pct(row.get('current_bracket_no_roi'))} | {pct(row.get('d1_no_roi'))} |"
        )
    lines.extend(
        [
            "",
            "## Expression Matrix By Day Regime",
            "",
            "| day_regime | expression | states | active_dates | avg_ask | hit_rate | roi |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in expr[:40]:
        lines.append(
            f"| `{row['day_regime']}` | `{row['expression']}` | {row['state_rows']} | {row['active_dates']} | {num(row['avg_ask'])} | {pct(row['hit_rate'])} | {pct(row['roi'])} |"
        )
    lines.extend(
        [
            "",
            "## Forward Recording Protocol",
            "",
            "后续 live/shadow runner 不需要先做交易决策；每个 city/date/hour 先记录同一套 regime ledger：",
            "",
            "- identity: `generated_at_utc`, `city`, `target_date`, `decision_hour_local`, `decision_snapshot_ts_utc`, `source_profile`, `forecast_source`, `forecast_clock_source`",
            "- PIT mechanism: `current_temp`, `running_max`, `minutes_since_running_max`, `forecast_gap_to_running`, `forecast_peak_delta`, `temp_trend_1h/3h`, `rh`, `dewpoint_depression`, `wind_speed`, `sky_cover`",
            "- labels: `day_regime`, `intraday_state`, `moisture_cloud_regime`, `wind_regime`, `running_max_state`, `composite_regime`",
            "- quotes by expression: current YES ask, real current-bracket NO ask, d1/d2 NO ask, cheapest higher YES ask, ask size/depth when available",
            "- settlement join later only: final max, final winning bracket, expression payoff/ROI",
            "",
            "## 输出文件",
            "",
            f"- State feature table: `{payload['outputs']['state_rows_csv']}`",
            f"- Day-regime summary: `{payload['outputs']['day_regime_summary_csv']}`",
            f"- Intraday-state summary: `{payload['outputs']['intraday_state_summary_csv']}`",
            f"- Expression matrix: `{payload['outputs']['expression_matrix_csv']}`",
            f"- JSON manifest: `{payload['outputs']['json']}`",
            "",
            "## 口径限制",
            "",
            "- 6/21..6/23 的 forecast clock 只有 native fact 覆盖的一部分，缺失 rows 保持 `day_space_unknown`，没有 forward-fill 后验 peak。",
            "- 这里的 ROI 是 quote ask proxy 的表达校准，不是 live fill PnL，也没有做三道门 promotion。",
            "- label 阈值是物理分桶尺度，用来描述 regime；后续若做交易模型，应把这些作为特征/分层，而不是硬 gate 清单。",
            "",
        ]
    )
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    feature_paths = [Path(p) for p in args.feature_rows] if args.feature_rows else discover_feature_rows(out_dir)
    if not feature_paths:
        raise SystemExit(f"No feature-row shards found under {out_dir}")

    rows = load_features(feature_paths)
    states = first_state_rows(rows)
    states = add_expression_quotes(states, rows)
    states, settlement_winner_filled_rows = add_settlement_winner_context(states, db_path)
    states = recompute_settlement_labels(states)
    states = add_pit_context(states)
    states = add_realized_context(states)
    states = add_regime_labels(states)
    states = add_expression_payoffs(states)

    state_csv = out_dir / "intraday_weather_regime_state_rows.csv"
    day_csv = out_dir / "day_regime_summary.csv"
    intra_csv = out_dir / "intraday_state_summary.csv"
    family_day_csv = out_dir / "city_family_day_regime_distribution.csv"
    source_day_csv = out_dir / "forecast_source_day_regime_distribution.csv"
    expression_csv = out_dir / "expression_matrix_by_regime.csv"

    day_summary = summarize_group(states, "day_regime")
    intra_summary = summarize_group(states, "intraday_state")
    family_dist = crosstab_rates(states, "city_family", "day_regime")
    source_dist = crosstab_rates(states.fillna({"forecast_source": "missing"}), "forecast_source", "day_regime")
    expr_matrix = expression_matrix(states, "day_regime")

    states.to_csv(state_csv, index=False)
    day_summary.to_csv(day_csv, index=False)
    intra_summary.to_csv(intra_csv, index=False)
    family_dist.to_csv(family_day_csv, index=False)
    source_dist.to_csv(source_day_csv, index=False)
    expr_matrix.to_csv(expression_csv, index=False)

    with connect_ro(db_path) as conn:
        fsc = optional_table_summary(conn, "fact_signal_candidates", "event_date")
        so = optional_table_summary(conn, "settlement_outcomes", "target_date")
        ft = optional_table_summary(conn, "fact_trades", "target_date")

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "intraday_weather_regime_atlas_v1",
        "evidence_layer": "PIT intraday state feature rows plus settlement/payoff calibration; not live approval",
        "inputs": [str(p.relative_to(ROOT) if p.is_absolute() else p) for p in feature_paths],
        "db_self_check": {
            "fact_signal_candidates_rows": int(fsc.get("rows", 0)),
            "fact_signal_candidates_min_event_date": fsc.get("min_event_date"),
            "fact_signal_candidates_max_event_date": fsc.get("max_event_date"),
            "fact_signal_candidates_table_missing": bool(fsc.get("table_missing")),
            "settlement_outcomes_rows": int(so.get("rows", 0)),
            "settlement_outcomes_min_target_date": so.get("min_target_date"),
            "settlement_outcomes_max_target_date": so.get("max_target_date"),
            "settlement_outcomes_table_missing": bool(so.get("table_missing")),
            "fact_trades_rows": int(ft.get("rows", 0)),
            "fact_trades_min_target_date": ft.get("min_target_date"),
            "fact_trades_max_target_date": ft.get("max_target_date"),
            "fact_trades_table_missing": bool(ft.get("table_missing")),
        },
        "clob_gate": load_gate(),
        "funnel": {
            "feature_rows": int(len(rows)),
            "state_rows": int(len(states)),
            "min_target_date": str(states["target_date"].min()),
            "max_target_date": str(states["target_date"].max()),
            "active_dates": int(states["target_date"].nunique()),
            "cities": int(states["city"].nunique()),
            "decision_hours": [int(x) for x in sorted(states["decision_hour_local"].dropna().unique())],
            "current_no_quote_coverage": float(states["current_no_ask"].notna().mean()),
            "lottery_yes_quote_coverage": float(states["lottery_yes_ask"].notna().mean()),
            "forecast_source_counts": {str(k): int(v) for k, v in states["forecast_source"].fillna("missing").value_counts().items()},
            "settlement_winner_filled_rows": int(settlement_winner_filled_rows),
            "forecast_clock_source_counts": {str(k): int(v) for k, v in states["forecast_clock_source"].fillna("missing").value_counts().items()},
        },
        "top_day_regimes": to_records(day_summary),
        "top_intraday_states": to_records(intra_summary),
        "expression_by_day_regime": to_records(expr_matrix),
        "outputs": {
            "state_rows_csv": str(state_csv.relative_to(ROOT)),
            "day_regime_summary_csv": str(day_csv.relative_to(ROOT)),
            "intraday_state_summary_csv": str(intra_csv.relative_to(ROOT)),
            "city_family_day_regime_distribution_csv": str(family_day_csv.relative_to(ROOT)),
            "forecast_source_day_regime_distribution_csv": str(source_day_csv.relative_to(ROOT)),
            "expression_matrix_csv": str(expression_csv.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, out_md)
    print(
        json.dumps(
            {
                "state_rows": len(states),
                "feature_rows": len(rows),
                "date_range": [str(states["target_date"].min()), str(states["target_date"].max())],
                "out_json": str(out_json),
                "out_md": str(out_md),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
