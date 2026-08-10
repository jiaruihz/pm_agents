#!/usr/bin/env python3
"""Score current-bracket NO with true previous-day forecast snapshots.

This is a point-in-time consumption test for the afternoon-peak classifier.
The classifier is still trained on the broader research/backfill feature set,
but every scored PIT row replaces forecast peak/max features with the last
forecast snapshot available on the city-local day before the target date.

No live config is touched and no orders are placed.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_current_bracket_no_pass_through_v1 as pass_through  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


SNAPSHOT_DIR = historical_strategy_snapshots()
GFS_DAILY_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/gfs_daily"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_prevday_pit_shadow_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_PIT_ROWS = OUT_DIR / "prevday_pit_forecast_rows.csv"
OUT_SCORED = OUT_DIR / "scored_pit_rows.csv"
OUT_SHADOW = OUT_DIR / "shadow_candidates.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-23-current-bracket-no-prevday-pit-shadow-v1.md"
FORWARD_FEATURE_ROWS = OUT_DIR / "forward_feature_factory/reheat_feature_rows.csv"

SEED = 20260623
STAKE_USD = pass_through.STAKE_USD

NUM_FEATURES = [
    "decision_hour_local",
    "forecast_peak_hour_local",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
    "forecast_gap_to_bracket_upper_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "decline_native",
    "distance_into_bracket_native",
    "current_native",
    "running_native",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "sky_cover_code",
]
CAT_FEATURES = ["city", "unit", "forecast_clock_source"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def pct(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"{100.0 * fval:+.1f}%"


def parse_dt_utc(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def parse_date(raw: Any) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except Exception:
        return None


def model_key(source: str) -> str:
    text = source.lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return text.replace("open_meteo_live_", "") or "unknown"


def load_prevday_pit_forecasts(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    keys = set(map(tuple, universe[["city", "target_date"]].astype(str).drop_duplicates().values.tolist()))
    unit_by_city = (
        universe[["city", "unit"]]
        .dropna(subset=["city"])
        .drop_duplicates("city")
        .set_index("city")["unit"]
        .astype(str)
        .str.upper()
        .to_dict()
        if "unit" in universe.columns
        else {}
    )
    date_min = parse_date(universe["target_date"].astype(str).min())
    date_max = parse_date(universe["target_date"].astype(str).max())
    rows: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_records = 0
    records_with_peak = 0
    gfs_daily_files = 0
    gfs_daily_prevday_files = 0

    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot_ts = parse_dt_utc(data.get("ts_utc") or data.get("snapshot_ts_utc"))
        if snapshot_ts is None:
            continue
        scanned_files += 1
        for rec in data.get("records", []):
            scanned_records += 1
            if rec.get("forecast_peak_hour_local") is None or rec.get("forecast_max_native") is None:
                continue
            records_with_peak += 1
            city = rec.get("city")
            target = rec.get("target_date") or rec.get("event_date") or rec.get("market_local_date")
            if not city or not target or (str(city), str(target)) not in keys:
                continue
            target_dt = parse_date(target)
            if target_dt is None:
                continue
            if date_min and target_dt < date_min:
                continue
            if date_max and target_dt > date_max:
                continue
            tz_name = rec.get("forecast_timezone") or rec.get("timezone_name")
            if not tz_name:
                continue
            try:
                snapshot_local = snapshot_ts.astimezone(ZoneInfo(str(tz_name)))
            except Exception:
                continue
            if snapshot_local.date() != target_dt - timedelta(days=1):
                continue
            source = str(rec.get("forecast_peak_source") or rec.get("forecast_source") or "")
            key = model_key(source)
            if key not in {"gfs", "ecmwf"}:
                continue
            rows.append(
                {
                    "city": str(city),
                    "target_date": str(target)[:10],
                    "model": key,
                    "forecast_source": source,
                    "source_layer": "paper_snapshot",
                    "source_priority": 1,
                    "snapshot_ts_utc": snapshot_ts.isoformat().replace("+00:00", "Z"),
                    "snapshot_ts_local": snapshot_local.isoformat(),
                    "snapshot_file": str(path.relative_to(ROOT)),
                    "forecast_peak_hour_local": float(rec.get("forecast_peak_hour_local")),
                    "forecast_peak_time_local": rec.get("forecast_peak_time_local"),
                    "forecast_max_native": float(rec.get("forecast_max_native")),
                    "forecast_max_f": float(rec.get("forecast_max_f")) if rec.get("forecast_max_f") is not None else np.nan,
                    "forecast_values_hash": rec.get("forecast_values_hash"),
                }
            )

    for path in sorted(GFS_DAILY_DIR.glob("*.json")):
        gfs_daily_files += 1
        data = json.loads(path.read_text(encoding="utf-8"))
        city = str(data.get("city") or "").strip()
        target = str(data.get("date") or "").strip()
        fetched_at = str(data.get("fetched_at") or "").strip()
        if not city or not target or (city, target) not in keys:
            continue
        target_dt = parse_date(target)
        fetched_dt = parse_date(fetched_at)
        if target_dt is None or fetched_dt is None:
            continue
        if target_dt - fetched_dt != timedelta(days=1):
            continue
        if date_min and target_dt < date_min:
            continue
        if date_max and target_dt > date_max:
            continue
        temps = data.get("hourly_temps") or []
        if len(temps) != 24:
            continue
        try:
            values = [float(x) for x in temps]
        except Exception:
            continue
        max_f = float(max(values))
        peak_hour = int(next(i for i, value in enumerate(values) if abs(value - max_f) < 1e-9))
        max_c = data.get("max_c")
        if max_c is None:
            max_c = (max_f - 32.0) * 5.0 / 9.0
        max_native = max_f if unit_by_city.get(city) == "F" else float(max_c)
        gfs_daily_prevday_files += 1
        rows.append(
            {
                "city": city,
                "target_date": target,
                "model": "gfs",
                "forecast_source": "weather_predict_gfs_daily_prevday",
                "source_layer": "weather_predict_gfs_daily",
                "source_priority": 0,
                "snapshot_ts_utc": None,
                "snapshot_ts_local": fetched_at,
                "snapshot_file": str(path.relative_to(ROOT)),
                "forecast_peak_hour_local": float(peak_hour),
                "forecast_peak_time_local": f"{target}T{peak_hour:02d}:00",
                "forecast_max_native": float(max_native),
                "forecast_max_f": max_f,
                "forecast_values_hash": None,
            }
        )

    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw, {
            "scanned_files": scanned_files,
            "scanned_records": scanned_records,
            "records_with_peak": records_with_peak,
            "gfs_daily_files": gfs_daily_files,
            "gfs_daily_prevday_files": gfs_daily_prevday_files,
            "prevday_records": 0,
        }
    raw = raw.sort_values(["city", "target_date", "model", "source_priority", "snapshot_ts_utc"])
    last = raw.drop_duplicates(["city", "target_date", "model"], keep="last").reset_index(drop=True)
    stats = {
        "scanned_files": scanned_files,
        "scanned_records": scanned_records,
        "records_with_peak": records_with_peak,
        "gfs_daily_files": gfs_daily_files,
        "gfs_daily_prevday_files": gfs_daily_prevday_files,
        "prevday_records_raw": int(len(raw)),
        "prevday_records_last": int(len(last)),
        "prevday_city_dates": int(last[["city", "target_date"]].drop_duplicates().shape[0]),
        "prevday_date_min": str(last["target_date"].min()),
        "prevday_date_max": str(last["target_date"].max()),
        "models": last["model"].value_counts().to_dict(),
        "source_layers": last["source_layer"].value_counts().to_dict(),
    }
    return last, stats


def discover_snapshot_peak_universe(after_date: str) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    floor = parse_date(after_date)
    for path in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for rec in data.get("records", []):
            if rec.get("forecast_peak_hour_local") is None:
                continue
            city = rec.get("city")
            target = rec.get("target_date") or rec.get("event_date") or rec.get("market_local_date")
            target_dt = parse_date(target)
            if not city or target_dt is None:
                continue
            if floor is not None and target_dt <= floor:
                continue
            rows.append({"city": str(city), "target_date": str(target_dt)})
    if not rows:
        return pd.DataFrame(columns=["city", "target_date"])
    return pd.DataFrame(rows).drop_duplicates(["city", "target_date"]).reset_index(drop=True)


def pivot_pit(pit: pd.DataFrame) -> pd.DataFrame:
    if pit.empty:
        return pd.DataFrame(columns=["city", "target_date"])
    base = pit[["city", "target_date"]].drop_duplicates().copy()
    for model in ["gfs", "ecmwf"]:
        part = pit[pit["model"].eq(model)].copy()
        rename = {
            "forecast_source": f"pit_{model}_forecast_source",
            "source_layer": f"pit_{model}_source_layer",
            "snapshot_ts_utc": f"pit_{model}_snapshot_ts_utc",
            "snapshot_ts_local": f"pit_{model}_snapshot_ts_local",
            "snapshot_file": f"pit_{model}_snapshot_file",
            "forecast_peak_hour_local": f"pit_{model}_forecast_peak_hour_local",
            "forecast_peak_time_local": f"pit_{model}_forecast_peak_time_local",
            "forecast_max_native": f"pit_{model}_forecast_max_native",
            "forecast_max_f": f"pit_{model}_forecast_max_f",
            "forecast_values_hash": f"pit_{model}_forecast_values_hash",
        }
        keep = ["city", "target_date"] + list(rename)
        if part.empty:
            for col in keep:
                if col not in base:
                    base[col] = np.nan
            continue
        part = part[keep].rename(columns=rename)
        base = base.merge(part, on=["city", "target_date"], how="left")
    gfs = pd.to_numeric(base.get("pit_gfs_forecast_peak_hour_local"), errors="coerce")
    ecmwf = pd.to_numeric(base.get("pit_ecmwf_forecast_peak_hour_local"), errors="coerce")
    base["pit_forecast_peak_hour_spread"] = (gfs - ecmwf).abs()
    base["pit_both_models_present"] = gfs.notna() & ecmwf.notna()
    return base


def load_training_dataset() -> pd.DataFrame:
    raw = pass_through.load_feature_rows()
    df = pass_through.enrich_current_no(raw)
    df["depth5_notional"] = df["no_ask"] * df["quote_depth_ask_5c"]
    df = df[df["midday_h10_14"] & df["actual_peak_afternoon"].notna()].copy()
    df["label_afternoon_peak"] = df["actual_peak_afternoon"].astype(int)
    df["trade_base"] = df["no_ask"].between(0.10, 0.35) & df["depth5_notional"].ge(STAKE_USD)
    for col in NUM_FEATURES:
        if col not in df.columns:
            df[col] = np.nan
    for col in CAT_FEATURES:
        if col not in df.columns:
            df[col] = ""
    return df


def split_dates(df: pd.DataFrame) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    split_date = dates[split_idx - 1]
    train = df[df["target_date"].astype(str) <= split_date].copy()
    holdout = df[df["target_date"].astype(str) > split_date].copy()
    return split_date, train, holdout


def build_model(c: float) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]),
                NUM_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                CAT_FEATURES,
            ),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(C=c, max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )


def model_metrics(pipe: Pipeline, frame: pd.DataFrame) -> dict[str, Any]:
    prob = pipe.predict_proba(frame[NUM_FEATURES + CAT_FEATURES])[:, 1]
    label = frame["label_afternoon_peak"].astype(int)
    return {
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "label_rate": float(label.mean()),
        "auc": float(roc_auc_score(label, prob)),
        "brier": float(brier_score_loss(label, prob)),
        "prob_mean": float(prob.mean()),
    }


def apply_pit_features(df: pd.DataFrame, pit_wide: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(pit_wide, on=["city", "target_date"], how="left")
    gfs_hour = pd.to_numeric(out.get("pit_gfs_forecast_peak_hour_local"), errors="coerce")
    ecmwf_hour = pd.to_numeric(out.get("pit_ecmwf_forecast_peak_hour_local"), errors="coerce")
    gfs_max = pd.to_numeric(out.get("pit_gfs_forecast_max_native"), errors="coerce")
    ecmwf_max = pd.to_numeric(out.get("pit_ecmwf_forecast_max_native"), errors="coerce")
    decision_hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    running = pd.to_numeric(out["running_native"], errors="coerce")
    bracket_upper = pd.to_numeric(out["bracket_upper"], errors="coerce")

    primary_hour = gfs_hour.where(gfs_hour.notna(), ecmwf_hour)
    primary_max = gfs_max.where(gfs_max.notna(), ecmwf_max)
    out["pit_prevday_available"] = primary_hour.notna() & primary_max.notna()
    out["forecast_peak_hour_local"] = primary_hour
    out["forecast_max_native"] = primary_max
    out["forecast_peak_delta_hours_local"] = decision_hour - primary_hour
    out["gfs_forecast_peak_delta_hours_local"] = decision_hour - gfs_hour
    out["ecmwf_forecast_peak_delta_hours_local"] = decision_hour - ecmwf_hour
    out["forecast_peak_hour_spread"] = pd.to_numeric(out.get("pit_forecast_peak_hour_spread"), errors="coerce")
    out["gfs_forecast_gap_to_running_native"] = gfs_max - running
    out["ecmwf_forecast_gap_to_running_native"] = ecmwf_max - running
    out["forecast_gap_to_bracket_upper_native"] = primary_max - bracket_upper
    out["forecast_clock_source"] = np.where(out["pit_prevday_available"], "prevday_pit_snapshot", "missing_prevday_pit")
    return out


def summarize_selected(name: str, raw: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    selected = pass_through.select_first_per_city_day(raw.copy())
    return pass_through.summarize(name, raw, selected, baseline)


def load_forward_current_no() -> pd.DataFrame:
    if not FORWARD_FEATURE_ROWS.exists():
        return pd.DataFrame()
    old_feature_rows = pass_through.FEATURE_ROWS
    pass_through.FEATURE_ROWS = FORWARD_FEATURE_ROWS
    try:
        raw = pass_through.load_feature_rows()
    finally:
        pass_through.FEATURE_ROWS = old_feature_rows

    cur = raw[
        raw["outcome"].astype(str).str.lower().eq("no")
        & raw["bracket"].astype(str).eq(raw["current_bracket"].astype(str))
        & raw["quote_best_ask"].notna()
        & raw["current_bracket"].notna()
    ].copy()
    if cur.empty:
        return cur
    cur["no_ask"] = cur["quote_best_ask"]
    cur["no_ask_size"] = cur["quote_best_ask_size"]
    cur["no_top_ask_notional"] = cur["no_ask"] * cur["no_ask_size"]
    cur["depth5_notional"] = cur["no_ask"] * cur["quote_depth_ask_5c"]
    cur["bracket_upper"] = cur["bracket_high"].fillna(cur["bracket_low"])
    cur["distance_into_bracket_native"] = cur["running_native"] - cur["bracket_low"]
    cur["forecast_gap_to_bracket_upper_native"] = cur["forecast_max_native"] - cur["bracket_upper"]
    cur["midday_h10_14"] = cur["decision_hour_local"].between(10, 14)
    cur["trade_base"] = cur["midday_h10_14"] & cur["no_ask"].between(0.10, 0.35) & cur["depth5_notional"].ge(STAKE_USD)
    for col in NUM_FEATURES:
        if col not in cur.columns:
            cur[col] = np.nan
    for col in CAT_FEATURES:
        if col not in cur.columns:
            cur[col] = ""
    return cur.reset_index(drop=True)


def score_forward_shadow(pipe: Pipeline, pit_wide: pd.DataFrame, training_dates_max: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    latest_dates = sorted(d for d in pit_wide["target_date"].dropna().astype(str).unique() if d > training_dates_max)
    if not latest_dates:
        return pd.DataFrame(), {
            "status": "blocked_by_no_future_pit_dates",
            "shadow_candidates": 0,
            "reason": "no PIT forecast dates beyond settled feature table",
        }
    forward = load_forward_current_no()
    if forward.empty:
        return pd.DataFrame(), {
            "status": "blocked_by_missing_intraday_feature_rows",
            "target_dates": latest_dates,
            "pit_city_dates": int(pit_wide[pit_wide["target_date"].isin(latest_dates)][["city", "target_date"]].drop_duplicates().shape[0]),
            "shadow_candidates": 0,
            "reason": f"forward feature rows missing or empty at {FORWARD_FEATURE_ROWS.relative_to(ROOT)}",
        }
    forward_pit = apply_pit_features(forward, pit_wide)
    forward_pit = forward_pit[forward_pit["pit_prevday_available"]].copy()
    if forward_pit.empty:
        return forward_pit, {
            "status": "blocked_by_no_joined_forward_pit",
            "target_dates": latest_dates,
            "forward_rows": int(len(forward)),
            "shadow_candidates": 0,
            "reason": "forward feature rows did not join to previous-day PIT forecast rows",
        }
    forward_pit["pit_logit_c0p2_p"] = pipe.predict_proba(forward_pit[NUM_FEATURES + CAT_FEATURES])[:, 1]
    raw = forward_pit[forward_pit["trade_base"] & forward_pit["pit_logit_c0p2_p"].ge(0.50)].copy()
    selected = pass_through.select_first_per_city_day(raw)
    return selected, {
        "status": "shadow_candidates_generated",
        "target_dates": latest_dates,
        "pit_city_dates": int(pit_wide[pit_wide["target_date"].isin(latest_dates)][["city", "target_date"]].drop_duplicates().shape[0]),
        "forward_rows": int(len(forward)),
        "forward_pit_rows": int(len(forward_pit)),
        "trade_base_rows": int(forward_pit["trade_base"].sum()),
        "raw_shadow_signals": int(len(raw)),
        "shadow_candidates": int(len(selected)),
        "reason": "zero-notional forward shadow candidates only; no settlement labels and no orders placed",
    }


def table_lines(rows: pd.DataFrame, cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows.to_dict("records"):
        vals = []
        for col in cols:
            val = row.get(col)
            if col in {
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "train_roi",
                "holdout_roi",
                "baseline_roi",
                "excess_roi_vs_baseline",
                "excess_roi_ci_low",
                "excess_roi_ci_high",
            }:
                vals.append(pct(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append(str(val))
        out.append("| " + " | ".join(vals) + " |")
    return out


def write_report(payload: dict[str, Any], variants: pd.DataFrame) -> None:
    main = variants[variants["variant"].eq("pit_prevday_logit_c0p2_p_ge_0p50")]
    main_row = main.iloc[0].to_dict() if not main.empty else {}
    base = variants[variants["variant"].eq("pit_prevday_baseline_ask10_35_depth5_ge5")]
    base_row = base.iloc[0].to_dict() if not base.empty else {}
    lines = [
        "# Current-Bracket NO Prevday PIT Shadow v1",
        "",
        "## 数据快照",
        "",
        f"- 事实表已重建；CLOB fill coverage gate：`{payload['clob_gate']['gate_pass']}`。",
        "- PIT forecast 来源：`paper_snapshots` + 远端同步的 `weather-predict/cache/gfs_daily`；每个 city-date-model 取目标日前一天可见的 forecast，6/20+ paper snapshot 优先。",
        f"- 历史 feature rows：`{pass_through.FEATURE_ROWS.relative_to(ROOT)}`，范围 `{payload['training']['date_min']}`..`{payload['training']['date_max']}`。",
        f"- PIT 可检验覆盖：`{payload['pit_coverage']['prevday_city_dates']}` city-days，日期 `{payload['pit_coverage'].get('prevday_date_min')}`..`{payload['pit_coverage'].get('prevday_date_max')}`。",
        f"- 生成时间：`{payload['generated_at_utc']}`。",
        "",
        "## 结论",
        "",
        "这次补上了旧 `weather-predict` 的前一日 GFS daily forecast cache；因此历史 PIT 可检验分母不再只有 `2026-06-20` 一天。",
        "",
        (
            f"把 backfill-trained classifier 改为消费 PIT prevday forecast 后，在可检验 PIT 窗口上 "
            f"`pit_prevday_logit_c0p2_p_ge_0p50` 选出 {main_row.get('selected_trades', 0)} 笔；"
            f"ROI {pct(main_row.get('roi'))}，CI [{pct(main_row.get('roi_ci_low'))}, {pct(main_row.get('roi_ci_high'))}]。"
        ),
        "",
        (
            f"同一 NO ask/cap baseline 选 {base_row.get('selected_trades', 0)} 笔，ROI {pct(base_row.get('roi'))}；"
            f"classifier excess ROI {pct(main_row.get('excess_roi_vs_baseline'))}，"
            f"CI [{pct(main_row.get('excess_roi_ci_low'))}, {pct(main_row.get('excess_roi_ci_high'))}]。"
        ),
        "",
        (
            "注意：历史 PIT 主要来自 GFS daily cache，6/20 后才有 paper snapshot 双模型字段；"
            "历史 significance/baseline 两门已过，但 forward 还只有 zero-notional candidates、没有结算结果。"
        ),
        "",
        (
        f"Forward/shadow：`{payload['forward_shadow']['status']}`，"
            f"zero-notional candidates `{payload['forward_shadow'].get('shadow_candidates', 0)}`。"
            f"原因：{payload['forward_shadow']['reason']}。"
        ),
        "",
        "交易动作：`shadow_candidate_keep_collecting`，继续收这些候选的 settlement；不 live。",
        "",
        "## Variant Table",
        "",
        *table_lines(
            variants[
                [
                    "variant",
                    "raw_signals",
                    "selected_trades",
                    "active_dates",
                    "avg_no_ask",
                    "no_win_rate",
                    "actual_peak_afternoon_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "holdout_roi",
                ]
            ],
            [
                "variant",
                "raw_signals",
                "selected_trades",
                "active_dates",
                "avg_no_ask",
                "no_win_rate",
                "actual_peak_afternoon_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
            ],
        ),
        "",
        "## 口径",
        "",
        "- `decision_minus_peak = decision_hour_local - forecast_peak_hour_local`，沿用 feature factory 的实际字段符号。",
        "- 交易价格仍使用同窗真实 `NO ask` 和 `no_depth_ask_5c`，没有用 `1 - YES ask` 推断 NO。",
        "- 6/21+ forward rows 来自本轮新补的 IEM observed cache + reheat feature factory；没有 settlement label，只能做 shadow candidate。",
        "",
        "## 输出",
        "",
        f"- JSON：`{OUT_JSON.relative_to(ROOT)}`",
        f"- PIT forecast rows：`{OUT_PIT_ROWS.relative_to(ROOT)}`",
        f"- Scored PIT rows：`{OUT_SCORED.relative_to(ROOT)}`",
        f"- Shadow candidates：`{OUT_SHADOW.relative_to(ROOT)}`",
        f"- Forward feature factory：`{FORWARD_FEATURE_ROWS.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    train_df = load_training_dataset()
    split_date, train, holdout = split_dates(train_df)

    pipe = build_model(0.2)
    pipe.fit(train[NUM_FEATURES + CAT_FEATURES], train["label_afternoon_peak"].astype(int))
    train_df["backfill_logit_c0p2_p"] = pipe.predict_proba(train_df[NUM_FEATURES + CAT_FEATURES])[:, 1]

    future_universe = discover_snapshot_peak_universe(str(train_df["target_date"].max()))
    forecast_universe = (
        pd.concat([train_df[["city", "target_date", "unit"]].drop_duplicates(), future_universe], ignore_index=True)
        .drop_duplicates(["city", "target_date"])
        .reset_index(drop=True)
    )
    pit_rows, pit_stats = load_prevday_pit_forecasts(forecast_universe)
    pit_rows.to_csv(OUT_PIT_ROWS, index=False)
    pit_wide = pivot_pit(pit_rows)
    scored = apply_pit_features(train_df.copy(), pit_wide)
    pit_scored = scored[scored["pit_prevday_available"]].copy()
    if not pit_scored.empty:
        pit_scored["pit_logit_c0p2_p"] = pipe.predict_proba(pit_scored[NUM_FEATURES + CAT_FEATURES])[:, 1]
    else:
        pit_scored["pit_logit_c0p2_p"] = pd.Series(dtype=float)

    baseline_raw = pit_scored[pit_scored["trade_base"]].copy()
    baseline_selected = pass_through.select_first_per_city_day(baseline_raw)
    chosen_raw = pit_scored[pit_scored["trade_base"] & pit_scored["pit_logit_c0p2_p"].ge(0.50)].copy()
    variants = pd.DataFrame(
        [
            summarize_selected("pit_prevday_baseline_ask10_35_depth5_ge5", baseline_raw, None),
            summarize_selected("pit_prevday_logit_c0p2_p_ge_0p50", chosen_raw, baseline_selected),
        ]
    )
    variants.to_csv(OUT_VARIANTS, index=False)

    scored_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "depth5_notional",
        "label_afternoon_peak",
        "label_no_wins",
        "actual_peak_first_hour_local",
        "forecast_peak_hour_local",
        "forecast_gap_to_bracket_upper_native",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "pit_gfs_snapshot_ts_local",
        "pit_gfs_source_layer",
        "pit_ecmwf_snapshot_ts_local",
        "pit_ecmwf_source_layer",
        "pit_logit_c0p2_p",
        "trade_base",
    ]
    for col in scored_cols:
        if col not in pit_scored.columns:
            pit_scored[col] = np.nan
    pit_scored[scored_cols].to_csv(OUT_SCORED, index=False)

    shadow, forward_shadow = score_forward_shadow(pipe, pit_wide, str(train_df["target_date"].max()))
    shadow_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "depth5_notional",
        "current_native",
        "running_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_hour_local",
        "forecast_gap_to_bracket_upper_native",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "pit_gfs_snapshot_ts_local",
        "pit_gfs_source_layer",
        "pit_ecmwf_snapshot_ts_local",
        "pit_ecmwf_source_layer",
        "pit_logit_c0p2_p",
        "trade_base",
    ]
    for col in shadow_cols:
        if col not in shadow.columns:
            shadow[col] = np.nan
    shadow[shadow_cols].to_csv(OUT_SHADOW, index=False)

    gate = pass_through.load_gate()
    payload = finite_or_none(
        {
            "generated_at_utc": now_utc(),
            "strategy": "current_bracket_no_prevday_pit_shadow_v1",
            "clob_gate": gate,
            "training": {
                "source": "backfill-trained classifier, PIT forecast consumption test with weather-predict gfs_daily prevday cache",
                "rows": int(len(train_df)),
                "date_min": str(train_df["target_date"].min()),
                "date_max": str(train_df["target_date"].max()),
                "cities": int(train_df["city"].nunique()),
                "split_date": split_date,
                "train_rows": int(len(train)),
                "holdout_rows": int(len(holdout)),
                "holdout_auc": float(model_metrics(pipe, holdout)["auc"]),
                "holdout_brier": float(model_metrics(pipe, holdout)["brier"]),
            },
            "pit_coverage": {
                **pit_stats,
                "forecast_universe_city_dates": int(forecast_universe[["city", "target_date"]].drop_duplicates().shape[0]),
                "future_universe_city_dates": int(future_universe[["city", "target_date"]].drop_duplicates().shape[0]),
                "scored_rows": int(len(pit_scored)),
                "scored_dates": int(pit_scored["target_date"].nunique()) if not pit_scored.empty else 0,
                "trade_base_rows": int(pit_scored["trade_base"].sum()) if not pit_scored.empty else 0,
            },
            "variants": variants.to_dict("records"),
            "forward_shadow": forward_shadow,
            "verdict": {
                "level": "shadow_candidate_keep_collecting",
                "live": "NO",
                "reason": "historical PIT significance and baseline gates pass after syncing weather-predict gfs_daily; live is blocked until zero-notional forward candidates settle",
            },
            "outputs": {
                "json": str(OUT_JSON.relative_to(ROOT)),
                "markdown": str(OUT_MD.relative_to(ROOT)),
                "variant_summary": str(OUT_VARIANTS.relative_to(ROOT)),
                "pit_rows": str(OUT_PIT_ROWS.relative_to(ROOT)),
                "scored_rows": str(OUT_SCORED.relative_to(ROOT)),
                "shadow_candidates": str(OUT_SHADOW.relative_to(ROOT)),
            },
        }
    )
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, variants)
    print(json.dumps({"out_json": str(OUT_JSON.relative_to(ROOT)), "out_md": str(OUT_MD.relative_to(ROOT)), "verdict": payload["verdict"], "pit_coverage": payload["pit_coverage"], "variants": payload["variants"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
