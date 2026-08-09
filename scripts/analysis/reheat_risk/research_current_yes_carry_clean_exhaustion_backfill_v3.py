#!/usr/bin/env python3
"""Backfill frozen clean heating-exhaustion features and replay current-YES carry.

Recoverable historical facts are rebuilt at each archived decision timestamp:
strict-new-high age from report-time IEM/METAR history, solar geometry from the
frozen forecast-cache coordinates, and remaining forecast runway from the
per-city fixed CITY_MODEL Single Runs curve.  Historical TAF transitions are
deliberately excluded because first-seen lineage was not retained.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_mechanism_timing_audit_v1 as audit,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_residual_entry_v2 as v2,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.physical_features import solar_geometry_features  # noqa: E402
from weather_data_feed.weather_context import heating_done_features_v2  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_yes_carry_clean_exhaustion_backfill_v3"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-clean-exhaustion-backfill-v3.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-22-current-yes-carry-clean-exhaustion-backfill-v3.md"
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
EXT_ROOT = ROOT / "docs/analysis/2026-06/generated"


CORE = list(v2.FEATURE_SETS["core"])
CLEAN_COMPONENTS = [
    "heat_exhaustion_forecast_runway_v2",
    "heat_exhaustion_observed_path_v2",
    "heat_exhaustion_strict_high_age_v2",
    "heat_exhaustion_solar_v2",
    "heat_exhaustion_clear_no_new_high_v2",
]
FEATURE_SETS = {
    "market_cal": ["market_logit"],
    "core": CORE,
    "core_plus_strict_high": CORE
    + ["strict_high_age_log", "strict_high_left_censored_num", "same_running_max_obs_count_log"],
    "core_plus_solar": CORE
    + ["solar_elevation_deg", "solar_elevation_delta_2h_deg", "daylight_remaining_minutes"],
    "core_plus_runway": CORE
    + ["forecast_remaining_gap_to_running_native", "forecast_reheat_after_now_f"],
    "market_plus_clean_exhaustion": ["market_logit", *CLEAN_COMPONENTS],
    "core_plus_clean_exhaustion": [*CORE, *CLEAN_COMPONENTS],
    "core_plus_exhaustion_index": [
        *CORE,
        "heating_exhaustion_index_v2",
        "heating_exhaustion_component_count_v2",
    ],
}


def json_ready(value: Any) -> Any:
    return v2.json_ready(value)


def station_map() -> dict[str, str]:
    payload = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))
    return {str(row["city"]): str(row["icao"]).upper() for row in payload["stations"]}


def observation_file_range(path: Path, icao: str) -> tuple[str, str] | None:
    match = re.match(rf"iem_ext_{re.escape(icao)}_(\d{{4}}-\d{{2}}-\d{{2}})_(\d{{4}}-\d{{2}}-\d{{2}})\.csv$", path.name)
    return None if match is None else (match.group(1), match.group(2))


def load_observation_histories(
    cities: list[str], start: str, end: str
) -> tuple[dict[tuple[str, str], pd.DataFrame], dict[str, Any]]:
    stations = station_map()
    histories: dict[tuple[str, str], pd.DataFrame] = {}
    files_used: set[str] = set()
    city_coverage: dict[str, dict[str, Any]] = {}
    for city in cities:
        icao = stations[city]
        paths = []
        for path in EXT_ROOT.glob(f"theta_no_iem_ext_patch*/iem_ext_{icao}_*.csv"):
            bounds = observation_file_range(path, icao)
            if bounds and bounds[1] >= start and bounds[0] <= end:
                paths.append(path)
        frames = []
        for path in sorted(paths):
            header = set(pd.read_csv(path, nrows=0).columns)
            use = [column for column in ["valid", "tmpf", "skyc1"] if column in header]
            if "valid" not in use or "tmpf" not in use:
                continue
            frame = pd.read_csv(path, usecols=use, na_values=["M"], low_memory=False)
            frame["source_file"] = str(path.relative_to(ROOT))
            frames.append(frame)
            files_used.add(str(path.relative_to(ROOT)))
        if not frames:
            city_coverage[city] = {"status": "missing", "days": 0, "rows": 0}
            continue
        merged = pd.concat(frames, ignore_index=True)
        merged["ts"] = pd.to_datetime(merged["valid"], utc=True, errors="coerce")
        merged["tmpf"] = pd.to_numeric(merged["tmpf"], errors="coerce")
        merged = merged.dropna(subset=["ts", "tmpf"]).sort_values("ts").drop_duplicates("ts", keep="last")
        local = merged["ts"].dt.tz_convert(ZoneInfo(CITY_TIMEZONE[city]))
        merged["target_date"] = local.dt.strftime("%Y-%m-%d")
        merged = merged[merged["target_date"].between(start, end)].copy()
        for target_date, day in merged.groupby("target_date"):
            histories[(city, str(target_date))] = day.sort_values("ts").reset_index(drop=True)
        city_coverage[city] = {
            "status": "ok",
            "days": int(merged["target_date"].nunique()),
            "rows": int(len(merged)),
        }
    return histories, {
        "source": "historically cached IEM/METAR report-time rows",
        "files_used": len(files_used),
        "cities_ok": sum(row["status"] == "ok" for row in city_coverage.values()),
        "city_coverage": city_coverage,
        "pit_limit": "report timestamp <= decision timestamp; original first-seen/ingest latency unavailable",
    }


def observation_path_asof(day: pd.DataFrame | None, decision: pd.Timestamp) -> dict[str, Any]:
    empty = {
        "minutes_since_last_strict_new_high": np.nan,
        "same_running_max_obs_count": np.nan,
        "running_max_clock_left_censored": np.nan,
        "clear_sky_regime_minutes": np.nan,
        "observation_path_source": "missing",
    }
    if day is None or pd.isna(decision):
        return empty
    prefix = day[day["ts"].le(decision)].copy()
    if prefix.empty:
        return empty
    high = -math.inf
    strict_index = None
    strict_ts = None
    for index, row in prefix.iterrows():
        value = float(row["tmpf"])
        if value > high + 1e-9:
            high = value
            strict_index = index
            strict_ts = row["ts"]
    hit_count = int(np.isclose(prefix["tmpf"].to_numpy(float), high, atol=0.11).sum())
    clear_codes = {"CLR", "SKC", "NSC", "CAVOK", "FEW"}
    clear_minutes = np.nan
    if "skyc1" in prefix and str(prefix.iloc[-1].get("skyc1") or "").upper() in clear_codes:
        start_position = len(prefix) - 1
        while start_position > 0:
            prior = str(prefix.iloc[start_position - 1].get("skyc1") or "").upper()
            if prior not in clear_codes:
                break
            start_position -= 1
        clear_minutes = max(
            0.0, (decision - prefix.iloc[start_position]["ts"]).total_seconds() / 60.0
        )
    return {
        "minutes_since_last_strict_new_high": max(
            0.0, (decision - strict_ts).total_seconds() / 60.0
        )
        if strict_ts is not None
        else np.nan,
        "same_running_max_obs_count": hit_count,
        "running_max_clock_left_censored": bool(strict_index == prefix.index[0])
        if strict_index is not None
        else np.nan,
        "clear_sky_regime_minutes": clear_minutes,
        "observation_path_source": "iem_metar_report_time_proxy",
    }


def interpolate(points: list[tuple[float, float]], hour: float) -> float | None:
    if not points:
        return None
    ordered = sorted(points)
    for item_hour, value in ordered:
        if abs(item_hour - hour) < 1e-9:
            return value
    for (left_hour, left), (right_hour, right) in zip(ordered, ordered[1:]):
        if left_hour < hour < right_hour:
            weight = (hour - left_hour) / (right_hour - left_hour)
            return left + weight * (right - left)
    return None


def forecast_curve_features(row: pd.Series, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    model = str(row["forecast_assigned_model"])
    path_value = row.get(f"{model}_forecast_cache_path")
    path = Path(str(path_value)) if path_value and str(path_value) != "nan" else None
    if path is None or not path.exists():
        return {
            "forecast_remaining_gap_to_running_native": np.nan,
            "forecast_reheat_after_now_f": np.nan,
            "forecast_remaining_temp_delta_f": np.nan,
            "latitude": np.nan,
            "longitude": np.nan,
            "forecast_curve_backfill_status": "missing_cache",
        }
    key = str(path)
    if key not in cache:
        cache[key] = json.loads(path.read_text(encoding="utf-8"))
    payload = cache[key]
    hourly = payload.get("hourly") or {}
    unit = str((payload.get("hourly_units") or {}).get("temperature_2m") or "°F").lower()
    points = []
    for raw_time, raw_temp in zip(hourly.get("time") or [], hourly.get("temperature_2m") or []):
        if raw_temp is None or not str(raw_time).startswith(str(row["target_date"])):
            continue
        try:
            hour = float(str(raw_time)[11:13]) + float(str(raw_time)[14:16]) / 60.0
            value = float(raw_temp)
        except (TypeError, ValueError, IndexError):
            continue
        value_f = value if "f" in unit else value * 9.0 / 5.0 + 32.0
        points.append((hour, value_f))
    decision_hour = float(row["decision_hour_local"])
    at_decision = interpolate(points, decision_hour)
    future = [value for hour, value in points if hour >= decision_hour]
    if at_decision is not None:
        future.append(at_decision)
    remaining_max_f = max(future) if future else None
    if remaining_max_f is None or at_decision is None:
        gap_native = reheat = delta = np.nan
        status = "missing_target_day_curve"
    else:
        delta = remaining_max_f - at_decision
        reheat = max(0.0, delta)
        remaining_native = (
            remaining_max_f
            if str(row["unit"]).upper() == "F"
            else (remaining_max_f - 32.0) * 5.0 / 9.0
        )
        gap_native = remaining_native - float(row["running_native"])
        status = "ok_fixed_city_model_single_run"
    return {
        "forecast_remaining_gap_to_running_native": gap_native,
        "forecast_reheat_after_now_f": reheat,
        "forecast_remaining_temp_delta_f": delta,
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "forecast_curve_backfill_status": status,
    }


def add_clean_features(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    histories, observation_lineage = load_observation_histories(
        sorted(universe["city"].unique()),
        str(universe["target_date"].min()),
        str(universe["target_date"].max()),
    )
    cache: dict[str, dict[str, Any]] = {}
    feature_rows = []
    for _, row in universe.iterrows():
        decision = row["decision_snapshot_dt"]
        observed = observation_path_asof(histories.get((row["city"], row["target_date"])), decision)
        forecast = forecast_curve_features(row, cache)
        solar = solar_geometry_features(
            {
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "latitude": forecast["latitude"],
                "longitude": forecast["longitude"],
            }
        )
        record = {
            **row.to_dict(),
            **observed,
            **forecast,
            **solar,
            "obs_age_minutes": row["obs_age_min"],
        }
        exhaustion = heating_done_features_v2(record)
        feature_rows.append({**observed, **forecast, **solar, **exhaustion})
    features = pd.DataFrame(feature_rows, index=universe.index)
    out = pd.concat([universe.copy(), features], axis=1)
    out["strict_high_age_log"] = np.log1p(
        pd.to_numeric(out["minutes_since_last_strict_new_high"], errors="coerce").clip(0, 720)
    )
    out["same_running_max_obs_count_log"] = np.log1p(
        pd.to_numeric(out["same_running_max_obs_count"], errors="coerce").clip(0, 24)
    )
    out["strict_high_left_censored_num"] = (
        out["running_max_clock_left_censored"].map({True: 1.0, False: 0.0})
    )
    coverage_fields = [
        "minutes_since_last_strict_new_high",
        "solar_elevation_deg",
        "solar_elevation_delta_2h_deg",
        "daylight_remaining_minutes",
        "forecast_remaining_gap_to_running_native",
        "forecast_reheat_after_now_f",
        "heating_exhaustion_index_v2",
    ]
    lineage = {
        "observation": observation_lineage,
        "forecast": {
            "source": "per-row fixed CITY_MODEL Single Runs cache path already audited by v2 lineage repair",
            "cache_files_used": len(cache),
            "status_counts": out["forecast_curve_backfill_status"].value_counts(dropna=False).to_dict(),
        },
        "solar": "NOAA-style deterministic geometry from decision UTC and frozen cache lat/lon",
        "taf": "excluded: historical issue first-seen lineage unavailable",
        "coverage": {
            field: {
                "rows": int(pd.to_numeric(out[field], errors="coerce").notna().sum()),
                "rate": float(pd.to_numeric(out[field], errors="coerce").notna().mean()),
            }
            for field in coverage_fields
        },
    }
    return out, lineage


def expanding_predictions(universe: pd.DataFrame) -> pd.DataFrame:
    base = audit.expanding_oof(universe, FEATURE_SETS)
    keep = [
        "city",
        "target_date",
        "decision_snapshot_dt",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "current_yes_bid",
        "current_yes_ask",
        "current_yes_ask_size",
        "taker_cost",
        "orderbook_file",
        "front",
    ]
    return base.merge(
        universe[keep].drop_duplicates(["city", "target_date", "decision_snapshot_dt"]),
        on=["city", "target_date", "decision_snapshot_dt"],
        how="left",
        validate="one_to_one",
    ).sort_values(["target_date", "city", "decision_snapshot_dt"]).reset_index(drop=True)


def score_rows(carry: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for name in FEATURE_SETS:
        row = {"model": name, **audit.score_loss(carry, f"p_{name}")}
        if name != "market_cal":
            row["vs_market_cal"] = audit.loss_delta_ci(carry, f"p_{name}", "p_market_cal")
        if name not in {"market_cal", "core"}:
            row["vs_core"] = audit.loss_delta_ci(carry, f"p_{name}", "p_core")
        output.append(row)
    return output


def entry_rows(carry: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    results, frames = [], {}
    for name in FEATURE_SETS:
        entries = v2.choose_first_positive_ev(carry, f"p_{name}")
        results.append(v2.entry_result(entries, name))
        frames[name] = entries
    return results, frames


def fmt_pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value) * 100:+.2f}%"


def fmt_num(value: Any, digits: int = 5) -> str:
    return "NA" if value is None else f"{float(value):.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    scores = {row["model"]: row for row in payload["proper_scores"]}
    entries = {row["selector"]: row for row in payload["entry_results"]}
    coverage = payload["historical_feature_lineage"]["coverage"]
    core = entries["core"]
    clean = entries["core_plus_clean_exhaustion"]
    clean_score = scores["core_plus_clean_exhaustion"]["vs_core"]
    lines = [
        "# Current-YES Carry clean heating-exhaustion 历史冻结回填 v3",
        "",
        "Status: `historical_report_time_PIT_proxy / frozen_feature_ablation / no-live-change`",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "## 直接回答",
        "",
        "可以回测，而且已经回填完成。之前的“只有 7月22日”仅指新版 shadow ledger 的 live first-seen telemetry；"
        "不是历史 observation/forecast 不存在。36 城的逐报 IEM/METAR 与固定 CITY_MODEL Single Runs 小时曲线"
        "足以重建 strict-new-high、太阳和 remaining runway。TAF transition 因没有历史 first-seen 留档，本轮排除。",
        "",
        "## 历史覆盖",
        "",
        f"- Universe：{payload['data_snapshot']['rows']} states，{payload['data_snapshot']['dates']} target dates，"
        f"{payload['data_snapshot']['date_min']}..{payload['data_snapshot']['date_max']}。",
        f"- strict-new-high：{coverage['minutes_since_last_strict_new_high']['rows']}/"
        f"{payload['data_snapshot']['rows']} ({coverage['minutes_since_last_strict_new_high']['rate']:.1%})。",
        f"- solar：{coverage['solar_elevation_deg']['rows']}/{payload['data_snapshot']['rows']} "
        f"({coverage['solar_elevation_deg']['rate']:.1%})。",
        f"- forecast remaining runway：{coverage['forecast_remaining_gap_to_running_native']['rows']}/"
        f"{payload['data_snapshot']['rows']} ({coverage['forecast_remaining_gap_to_running_native']['rate']:.1%})。",
        f"- exhaustion index：{coverage['heating_exhaustion_index_v2']['rows']}/"
        f"{payload['data_snapshot']['rows']} ({coverage['heating_exhaustion_index_v2']['rate']:.1%})。",
        "",
        "## 同分母 expanding OOF 结果",
        "",
        "所有模型仍以 market 为基准，测试日只用严格更早日期训练；carry domain 固定为 market midpoint≥0.80，"
        "交易为每 city-day 首个 fee 后正 taker EV。",
        "",
        "| model | first EV n | win | avg ask | taker ROI [95%CI] | Brier Δ vs core [95%CI] | logloss Δ vs core [95%CI] | front/back ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in FEATURE_SETS:
        entry = entries[name]
        score = scores[name]
        delta = score.get("vs_core")
        ci = entry["target_date_block_ci95"]
        if delta:
            brier = (
                f"{fmt_num(delta['candidate_minus_baseline_brier'])} "
                f"[{fmt_num(delta['brier_delta_ci95'][0])},{fmt_num(delta['brier_delta_ci95'][1])}]"
            )
            logloss = (
                f"{fmt_num(delta['candidate_minus_baseline_logloss'])} "
                f"[{fmt_num(delta['logloss_delta_ci95'][0])},{fmt_num(delta['logloss_delta_ci95'][1])}]"
            )
        else:
            brier = logloss = "—"
        lines.append(
            f"| {name} | {entry['city_days']} | {fmt_pct(entry['win_rate'])} | {fmt_num(entry['avg_ask'],3)} | "
            f"{fmt_pct(entry['fee_adjusted_taker_roi'])} [{fmt_pct(ci[0])},{fmt_pct(ci[1])}] | "
            f"{brier} | {logloss} | {fmt_pct(entry['robustness']['front_roi'])}/"
            f"{fmt_pct(entry['robustness']['back_roi'])} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        f"旧 core：{core['city_days']} city-days，ROI {fmt_pct(core['fee_adjusted_taker_roi'])}。"
        f"加入完整 clean exhaustion components 后：{clean['city_days']} city-days，ROI "
        f"{fmt_pct(clean['fee_adjusted_taker_roi'])}。",
        f"相对 core 的 proper-score delta：Brier {fmt_num(clean_score['candidate_minus_baseline_brier'])} "
        f"CI[{fmt_num(clean_score['brier_delta_ci95'][0])},{fmt_num(clean_score['brier_delta_ci95'][1])}]；"
        f"logloss {fmt_num(clean_score['candidate_minus_baseline_logloss'])} "
        f"CI[{fmt_num(clean_score['logloss_delta_ci95'][0])},{fmt_num(clean_score['logloss_delta_ci95'][1])}]。"
        "负值才表示 clean exhaustion 有增量。",
        "",
        payload["decision"],
        "",
        "这次历史 observation 的可见性只能按 report timestamp 近似，不能恢复真正 ingest first-seen latency；"
        "所以它比事后天气切片严格，但仍需用 7月22日起的 forward ledger 做最终 production-parity 验证。",
        "",
        "## 产物",
        "",
        f"- Script: `{payload['outputs']['script']}`",
        f"- JSON: `{payload['outputs']['json']}`",
        f"- Historical clean states: `{payload['outputs']['clean_states']}`",
        f"- OOF predictions: `{payload['outputs']['oof_predictions']}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    universe = v2.prepare_universe()
    # v2's loader needs these cache-path columns for the frozen curve replay.
    # Pull them from the expression-clean source rows on the same state key.
    paths = []
    for shard in sorted(
        (ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1").glob(
            "feature_factory_*/reheat_feature_rows.csv"
        )
    ):
        header = set(pd.read_csv(shard, nrows=0).columns)
        use = [
            column
            for column in [
                "city",
                "target_date",
                "decision_hour_local",
                "bracket",
                "current_bracket",
                "outcome",
                "gfs_forecast_cache_path",
                "ecmwf_forecast_cache_path",
            ]
            if column in header
        ]
        frame = pd.read_csv(shard, usecols=use, low_memory=False)
        frame["target_date"] = frame["target_date"].astype(str)
        frame = frame[
            frame["bracket"].astype(str).eq(frame["current_bracket"].astype(str))
            & frame["outcome"].astype(str).str.lower().eq("yes")
        ]
        paths.append(frame)
    cache_paths = pd.concat(paths, ignore_index=True).drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )
    universe = universe.merge(
        cache_paths[
            [
                "city",
                "target_date",
                "decision_hour_local",
                "gfs_forecast_cache_path",
                "ecmwf_forecast_cache_path",
            ]
        ],
        on=["city", "target_date", "decision_hour_local"],
        how="left",
        validate="many_to_one",
    )
    clean_universe, lineage = add_clean_features(universe)
    oof = expanding_predictions(clean_universe)
    carry = oof[oof["market_mid"].ge(v2.CARRY_MARKET_MID_FLOOR)].copy()
    proper_scores = score_rows(carry)
    entry_results, entries = entry_rows(carry)

    clean_universe.to_csv(OUT_DIR / "historical_clean_exhaustion_states.csv", index=False)
    oof.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    for name, frame in entries.items():
        frame.to_csv(OUT_DIR / f"entries_{name}.csv", index=False)
    full_vwap = {}
    for name in ["core", "core_plus_clean_exhaustion", "core_plus_exhaustion_index"]:
        summary, detail = audit.taker_vwap_audit(entries[name])
        full_vwap[name] = summary
        detail.to_csv(OUT_DIR / f"taker_ladder_{name}.csv", index=False)

    by_name = {row["selector"]: row for row in entry_results}
    score_by_name = {row["model"]: row for row in proper_scores}
    clean_delta = score_by_name["core_plus_clean_exhaustion"]["vs_core"]
    clean_proper_improves = (
        clean_delta["candidate_minus_baseline_brier"] < 0
        and clean_delta["candidate_minus_baseline_logloss"] < 0
    )
    clean_ci_pass = (
        clean_delta["brier_delta_ci95"][1] < 0
        and clean_delta["logloss_delta_ci95"][1] < 0
    )
    core_roi = by_name["core"]["fee_adjusted_taker_roi"]
    clean_roi = by_name["core_plus_clean_exhaustion"]["fee_adjusted_taker_roi"]
    if clean_ci_pass and clean_roi is not None and core_roi is not None and clean_roi > core_roi:
        decision = (
            "Clean exhaustion 在 proper score 与交易 ROI 上均通过本次历史门；冻结为下一版 forward challenger，"
            "但 report-time PIT proxy 仍不授权 live。"
        )
    elif clean_proper_improves:
        decision = (
            "Clean exhaustion 的 proper-score 点估有增量，但 CI 或交易 ROI 尚未同时超过 core；保留为 forward "
            "challenger，不替换 core，也不设新的 hard gate。"
        )
    else:
        decision = (
            "Clean exhaustion 在本次同分母历史检验没有改善 core 的两项 proper score；正确物理语义已回填，"
            "但不因语义合理就强行入模。继续 forward 记录用于验证，不替换 core。"
        )

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "historical_report_time_pit_proxy_frozen_ablation_no_live_change",
        "data_snapshot": {
            "rows": int(len(clean_universe)),
            "dates": int(clean_universe["target_date"].nunique()),
            "cities": int(clean_universe["city"].nunique()),
            "date_min": str(clean_universe["target_date"].min()),
            "date_max": str(clean_universe["target_date"].max()),
            "oof_rows": int(len(oof)),
            "oof_dates": int(oof["target_date"].nunique()),
            "carry_rows": int(len(carry)),
        },
        "frozen_feature_sets": FEATURE_SETS,
        "historical_feature_lineage": lineage,
        "proper_scores": proper_scores,
        "entry_results": entry_results,
        "full_ladder_taker": full_vwap,
        "decision": decision,
        "limitations": [
            "historical IEM/METAR visibility is report-time PIT proxy; ingest first-seen latency was not retained",
            "TAF transition history excluded because issue first-seen lineage was not retained",
            "candidate variants are frozen in this script before reading their results, but v3 follows exploratory v2 and is not an independent external sample",
        ],
        "live_action": "none",
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
            "clean_states": str((OUT_DIR / "historical_clean_exhaustion_states.csv").relative_to(ROOT)),
            "oof_predictions": str((OUT_DIR / "oof_predictions.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(payload)
    print(
        json.dumps(
            json_ready(
                {
                    "data_snapshot": payload["data_snapshot"],
                    "coverage": lineage["coverage"],
                    "entry_results": entry_results,
                    "proper_scores": proper_scores,
                    "decision": decision,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
