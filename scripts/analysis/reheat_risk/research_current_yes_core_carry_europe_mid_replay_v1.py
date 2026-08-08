#!/usr/bin/env python3
"""Replay current-YES Core Carry across European market-mid bands.

This study keeps the current v3 no-peak-clock probability model, five-share
full-ladder cost, exact-bracket semantics, and first-positive-EV city-day
selection fixed.  It varies only the market-mid floor and reports both signal
frequency and realized win/ROI so that "more hits" is not confused with better
alpha.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RESEARCH_ID = "current_yes_core_carry_europe_mid_replay_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-30-current-yes-core-carry-europe-mid-replay-v1.md"
)
RESULT_JSON = REPORT.with_suffix(".json")

LOCAL_V3_MODULE = (
    ROOT
    / "scripts/analysis/reheat_risk/"
    "research_current_yes_core_carry_peak_clock_fix_v3.py"
)
PRODUCTION_V3_MODULE = Path(
    "/Users/deepsleep/projects/pm_agents_prod/scripts/analysis/reheat_risk/"
    "research_current_yes_core_carry_peak_clock_fix_v3.py"
)
DEFAULT_V3_MODULE = (
    LOCAL_V3_MODULE if LOCAL_V3_MODULE.exists() else PRODUCTION_V3_MODULE
)
DEFAULT_COSTED = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/"
    "oof_states_five_share_cost.csv"
)
DEFAULT_LIVE_SCORES = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl"
)
DEFAULT_SOURCE_PROFILES = (
    Path("/Users/deepsleep/projects/pm_agents_prod")
    / "weather_data_feed/source_profiles.json"
)
DEFAULT_OBSERVATIONS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations/latest.json"
)

EUROPE_CITIES = (
    "Amsterdam",
    "Ankara",
    "Helsinki",
    "Istanbul",
    "London",
    "Madrid",
    "Milan",
    "Moscow",
    "Munich",
    "Paris",
    "Warsaw",
)
MID_FLOORS = (0.50, 0.60, 0.70, 0.75, 0.80)
MID_CEILING = 0.9895
BOOTSTRAP_REPS = 5000
SEED = 20260730


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def load_v3_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("core_carry_peak_clock_fix_v3", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v3 research module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_costed_v3(v3_module: Any, costed_path: Path) -> pd.DataFrame:
    universe = v3_module.load_universe()
    if len(universe) != 4061:
        raise RuntimeError(f"v3 historical denominator drift: expected 4061, got {len(universe)}")
    oof = v3_module.expanding_oof(universe)
    costed = pd.read_csv(costed_path, low_memory=False)
    keys = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
    ]
    cost_columns = [
        *keys,
        "five_share_executable",
        "five_share_cost_per_share",
        "five_share_cost_source",
    ]
    merged = oof.merge(
        costed[cost_columns].drop_duplicates(keys),
        on=keys,
        how="left",
        validate="one_to_one",
    )
    missing = int(merged["five_share_executable"].isna().sum())
    if missing:
        raise RuntimeError(f"five-share cost merge missing {missing} OOF rows")
    merged["five_share_executable"] = merged["five_share_executable"].astype(bool)
    return merged


def exact_bounded(frame: pd.DataFrame) -> pd.Series:
    bracket = frame["current_bracket"].astype(str).str.lower()
    return ~bracket.str.contains(r"\+|below|under|higher|above", regex=True)


def first_entries(frame: pd.DataFrame, floor: float) -> pd.DataFrame:
    eligible = frame[
        frame["five_share_executable"]
        & exact_bounded(frame)
        & frame["market_mid"].between(floor, MID_CEILING, inclusive="both")
        & frame["p_v3_no_peak_clock"].gt(frame["five_share_cost_per_share"])
    ].copy()
    eligible["model_probability"] = eligible["p_v3_no_peak_clock"]
    eligible["model_edge_after_fee_and_depth"] = (
        eligible["model_probability"] - eligible["five_share_cost_per_share"]
    )
    return (
        eligible.sort_values(["target_date", "city", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .reset_index(drop=True)
    )


def roi_draws(entries: pd.DataFrame) -> list[float]:
    if entries.empty:
        return []
    work = entries.assign(
        _cost=entries["five_share_cost_per_share"].astype(float),
        _pnl=entries["label"].astype(float)
        - entries["five_share_cost_per_share"].astype(float),
    )
    daily = work.groupby("target_date")[["_pnl", "_cost"]].sum()
    values = daily.to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = float(sample[:, 1].sum())
        if cost > 0:
            draws.append(float(sample[:, 0].sum() / cost))
    return draws


def entry_metrics(entries: pd.DataFrame, label: str) -> dict[str, Any]:
    if entries.empty:
        return {
            "slice": label,
            "city_days": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "fee_adjusted_roi": None,
            "target_date_block_ci95": None,
            "avg_effective_cost": None,
            "avg_model_edge": None,
        }
    cost = entries["five_share_cost_per_share"].astype(float)
    pnl = entries["label"].astype(float) - cost
    draws = roi_draws(entries)
    return {
        "slice": label,
        "city_days": int(len(entries)),
        "dates": int(entries["target_date"].nunique()),
        "cities": int(entries["city"].nunique()),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "win_rate": float(entries["label"].mean()),
        "fee_adjusted_roi": float(pnl.sum() / cost.sum()),
        "target_date_block_ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "avg_effective_cost": float(cost.mean()),
        "avg_model_edge": float(entries["model_edge_after_fee_and_depth"].mean()),
    }


def probability_metrics(frame: pd.DataFrame, label: str) -> dict[str, Any]:
    if frame.empty:
        return {"slice": label, "rows": 0}
    counts = frame.groupby(["city", "target_date"])["label"].transform("size")
    weights = 1.0 / counts.clip(lower=1).to_numpy(float)
    y = frame["label"].to_numpy(float)
    model = frame["p_v3_no_peak_clock"].clip(1e-6, 1 - 1e-6).to_numpy(float)
    market = frame["market_mid"].clip(1e-6, 1 - 1e-6).to_numpy(float)
    model_brier = float(np.average((model - y) ** 2, weights=weights))
    market_brier = float(np.average((market - y) ** 2, weights=weights))
    model_ll = float(
        np.average(-(y * np.log(model) + (1 - y) * np.log(1 - model)), weights=weights)
    )
    market_ll = float(
        np.average(
            -(y * np.log(market) + (1 - y) * np.log(1 - market)),
            weights=weights,
        )
    )
    return {
        "slice": label,
        "rows": int(len(frame)),
        "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
        "dates": int(frame["target_date"].nunique()),
        "actual_rate": float(np.average(y, weights=weights)),
        "mean_model_probability": float(np.average(model, weights=weights)),
        "mean_market_mid": float(np.average(market, weights=weights)),
        "model_brier": model_brier,
        "market_brier": market_brier,
        "model_minus_market_brier": model_brier - market_brier,
        "model_logloss": model_ll,
        "market_logloss": market_ll,
        "model_minus_market_logloss": model_ll - market_ll,
    }


def load_live_scores(path: Path) -> pd.DataFrame:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pd.DataFrame(rows)


def nested_values(value: Any, key: str) -> set[str]:
    output: set[str] = set()
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if child_key == key and child_value is not None:
                output.add(str(child_value))
            output.update(nested_values(child_value, key))
    elif isinstance(value, list):
        for child in value:
            output.update(nested_values(child, key))
    return output


def source_coverage(source_profiles_path: Path, observations_path: Path) -> list[dict[str, Any]]:
    profiles_payload = json.loads(source_profiles_path.read_text(encoding="utf-8"))
    profiles = {
        str(row["city"]): row for row in profiles_payload["source_profiles"]
    }
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    stations = nested_values(observations, "station")
    output: list[dict[str, Any]] = []
    for city in ("Istanbul", "Moscow"):
        profile = profiles[city]
        station = str(profile.get("configured_icao") or "")
        output.append(
            {
                "city": city,
                "configured_station": station,
                "station_in_current_observation_cache": station in stations,
                "live_eligible": bool(profile.get("live_eligible", False)),
                "primary_source": profile.get("primary_source"),
                "alignment_days": profile.get("alignment_days"),
                "alignment_rate": profile.get("alignment_rate"),
                "source_profile_note": profile.get("source_profile_note"),
            }
        )
    return output


def fmt_pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value):+.2%}"


def write_report(payload: dict[str, Any]) -> None:
    rows = payload["floor_replay"]
    table = [
        "| mid floor | city-days | dates | win rate | fee ROI | date-block 95% CI | forward ROI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        total = row["all"]
        forward = row["forward"]
        ci = total["target_date_block_ci95"]
        table.append(
            f"| {row['market_mid_floor']:.2f} | {total['city_days']} | {total['dates']} | "
            f"{fmt_pct(total['win_rate'])} | {fmt_pct(total['fee_adjusted_roi'])} | "
            f"{'NA' if ci is None else f'[{fmt_pct(ci[0])}, {fmt_pct(ci[1])}]'} | "
            f"{fmt_pct(forward['fee_adjusted_roi'])} |"
        )
    probability = payload["probability_by_mid_band"]
    probability_table = [
        "| mid band | rows | actual hold | model−market Brier | model−market logloss |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in probability:
        probability_table.append(
            f"| {row['slice']} | {row['rows']} | {fmt_pct(row.get('actual_rate'))} | "
            f"{row.get('model_minus_market_brier', float('nan')):+.5f} | "
            f"{row.get('model_minus_market_logloss', float('nan')):+.5f} |"
        )
    coverage_lines = [
        f"- {row['city']}: historical OOF rows `{row['historical_oof_rows']}`；"
        f"current cache station `{row['configured_station']}` present="
        f"`{str(row['station_in_current_observation_cache']).lower()}`；"
        f"source profile live_eligible=`{str(row['live_eligible']).lower()}`。"
        for row in payload["source_coverage"]
    ]
    incremental = payload["floor_075_incremental"]
    incremental_metrics = incremental["only_city_days"]
    istanbul = payload["istanbul_replay"]
    lines = [
        "# Current-YES Core Carry：欧洲 mid-price 回放 v1",
        "",
        "Status: `inconclusive / research replay / no live change`",
        "",
        "## 结论",
        "",
        payload["conclusion"],
        "",
        "## 欧洲 floor 回放",
        "",
        *table,
        "",
        f"Train/forward 切点固定为 `{payload['forward_split']['forward_start_date']}`；"
        "最后 10 个 target dates 只作 frozen direction check。",
        "",
        "### 0.75 相对 0.80 的实际增量",
        "",
        f"- 触发数增加 `{incremental['frequency_increase_vs_080']:.1%}`，"
        f"win rate 变化 `{incremental['win_rate_change_vs_080']:+.1%}`，"
        f"整体 ROI 变化 `{incremental['roi_change_vs_080']:+.2%}`。",
        f"- 0.75 独有的 `{incremental_metrics['city_days']}` 个 city-day 为 "
        f"`{incremental_metrics['wins']}` 胜 `{incremental_metrics['losses']}` 负，"
        f"单独 fee ROI `{incremental_metrics['fee_adjusted_roi']:+.2%}`；"
        "因此增加触发不等于增加 alpha。",
        f"- Istanbul：floor 0.80 为 `{istanbul['floor_080']['city_days']}` 笔、"
        f"`{istanbul['floor_080']['wins']}` 胜 `{istanbul['floor_080']['losses']}` 负、"
        f"ROI `{istanbul['floor_080']['fee_adjusted_roi']:+.2%}`；floor 0.75 为 "
        f"`{istanbul['floor_075']['city_days']}` 笔、"
        f"`{istanbul['floor_075']['wins']}` 胜 `{istanbul['floor_075']['losses']}` 负、"
        f"ROI `{istanbul['floor_075']['fee_adjusted_roi']:+.2%}`。样本仍低于城市级门槛。",
        "",
        "## 概率层：同一 mid band 与市场比较",
        "",
        *probability_table,
        "",
        "负数表示 v3 优于 raw market mid；正数表示更差。该表固定同 rows/labels，"
        "不使用 selected trades 代替概率分母。",
        "",
        "## 当前 live signal funnel",
        "",
        f"- raw Europe score checkpoints：`{payload['current_live_funnel']['score_rows']}`，"
        f"覆盖 `{payload['current_live_funnel']['cities']}` 城 / "
        f"`{payload['current_live_funnel']['dates']}` 个 target dates。",
        f"- mid 0.80–0.9895：`{payload['current_live_funnel']['within_frozen_mid_domain']}`；"
        f"positive taker EV：`{payload['current_live_funnel']['positive_taker_ev']}`。",
        "- 当前 raw 是触发覆盖，不含完整 settlement，因此不与历史 realized ROI 混算。",
        "",
        "## Istanbul / Moscow source coverage",
        "",
        *coverage_lines,
        "",
        "Istanbul 有历史 PIT/settlement 分母，可加入 collector 和 zero-notional Core Carry "
        "shadow；Moscow 当前历史 OOF 与 live observation cache 都不足，先补 source collector，"
        "不能直接加入真实执行。",
        "",
        "## 双漏斗与验证边界",
        "",
        f"- signal funnel：欧洲 historical OOF `{payload['historical_funnel']['oof_rows']}` rows "
        f"→ 5-share executable `{payload['historical_funnel']['executable_rows']}` "
        f"→ exact bounded `{payload['historical_funnel']['exact_rows']}` "
        f"→ floor-specific first positive-EV city-days（见表）。",
        f"- evidence funnel：`{payload['historical_funnel']['dates']}` target dates，"
        "PIT book、settlement、5-share ladder 与官方 fee 已覆盖；Istanbul/Moscow 当前 source "
        "coverage 另列，maker queue/fill 不属于本次 taker-expression 回放。",
        "- 本轮同时查看 5 个 floor，未做多重检验校正；lower-floor 只可作为 shadow hypothesis。",
        "",
        "## 三门",
        "",
        "- significance：按各 floor 的 target-date block CI。",
        "- baseline：看同 band 的 model−market proper score；不能只看交易 ROI。",
        "- forward：最后 10 个日期只复核方向，样本不足则 FAIL/NA。",
        "- conclusion：`inconclusive`；不修改现有 0.80 live floor，不扩大城市 live allowlist。",
        "",
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python scripts/analysis/reheat_risk/"
        "research_current_yes_core_carry_europe_mid_replay_v1.py",
        "```",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--study",
        choices=("europe-historical", "global-live-forward"),
        default="europe-historical",
    )
    parser.add_argument("--v3-module", type=Path, default=DEFAULT_V3_MODULE)
    parser.add_argument("--costed-oof", type=Path, default=DEFAULT_COSTED)
    parser.add_argument("--live-scores", type=Path, default=DEFAULT_LIVE_SCORES)
    parser.add_argument("--source-profiles", type=Path, default=DEFAULT_SOURCE_PROFILES)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--db-path", type=Path, default=ROOT / "runtime/weather.db")
    parser.add_argument(
        "--deployed-artifact",
        type=Path,
        default=Path(
            "/Users/deepsleep/projects/pm_agents_market_books_prod/"
            "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json"
        ),
    )
    parser.add_argument("--start-date", default="2026-07-31")
    parser.add_argument("--end-date", default="2026-08-07")
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=Path(
            "/Volumes/jrs-archive/pm_agents/research/artifact_store/"
            "current_yes_core_carry_mid_floor_forward_v1"
        ),
    )
    args = parser.parse_args()

    if args.study == "global-live-forward":
        from src.strategies.weather_edge_v1.research import (
            core_carry_mid_floor_forward,
        )

        return core_carry_mid_floor_forward.main(
            [
                "--scores",
                str(args.live_scores),
                "--db-path",
                str(args.db_path),
                "--artifact",
                str(args.deployed_artifact),
                "--start-date",
                args.start_date,
                "--end-date",
                args.end_date,
                "--ledger-dir",
                str(args.ledger_dir),
            ]
        )

    v3 = load_v3_module(args.v3_module)
    all_oof = load_costed_v3(v3, args.costed_oof)
    europe = all_oof[all_oof["city"].isin(EUROPE_CITIES)].copy()
    dates = sorted(europe["target_date"].astype(str).unique())
    if len(dates) < 20:
        raise RuntimeError(f"insufficient Europe OOF dates: {len(dates)}")
    forward_dates = set(dates[-10:])
    forward_start = min(forward_dates)

    floor_rows: list[dict[str, Any]] = []
    entry_frames: list[pd.DataFrame] = []
    for floor in MID_FLOORS:
        entries = first_entries(europe, floor)
        entries["market_mid_floor"] = floor
        entry_frames.append(entries)
        floor_rows.append(
            {
                "market_mid_floor": floor,
                "all": entry_metrics(entries, "all"),
                "train": entry_metrics(
                    entries[~entries["target_date"].isin(forward_dates)], "train"
                ),
                "forward": entry_metrics(
                    entries[entries["target_date"].isin(forward_dates)], "forward"
                ),
            }
        )

    bands = (
        (0.00, 0.50),
        (0.50, 0.60),
        (0.60, 0.70),
        (0.70, 0.80),
        (0.75, 0.80),
        (0.80, 0.90),
        (0.90, MID_CEILING),
    )
    probability_rows = [
        probability_metrics(
            europe[
                europe["market_mid"].ge(lower)
                & (
                    europe["market_mid"].le(upper)
                    if upper == MID_CEILING
                    else europe["market_mid"].lt(upper)
                )
            ],
            f"[{lower:.2f},{upper:.4g}{']' if upper == MID_CEILING else ')'}",
        )
        for lower, upper in bands
    ]

    live = load_live_scores(args.live_scores)
    live_europe = live[live["city"].isin(EUROPE_CITIES)].copy()
    within = live_europe["market_mid"].between(0.80, MID_CEILING, inclusive="both")
    coverage = source_coverage(args.source_profiles, args.observations)
    historical_counts = europe.groupby("city").size().to_dict()
    for row in coverage:
        row["historical_oof_rows"] = int(historical_counts.get(row["city"], 0))

    floor_080 = next(row for row in floor_rows if row["market_mid_floor"] == 0.80)
    floor_070 = next(row for row in floor_rows if row["market_mid_floor"] == 0.70)
    floor_075 = next(row for row in floor_rows if row["market_mid_floor"] == 0.75)
    entries_075 = next(
        frame for frame in entry_frames if float(frame["market_mid_floor"].iloc[0]) == 0.75
    )
    entries_080 = next(
        frame for frame in entry_frames if float(frame["market_mid_floor"].iloc[0]) == 0.80
    )
    keys = ["city", "target_date"]
    only_075_keys = (
        entries_075[keys]
        .merge(entries_080[keys], on=keys, how="left", indicator=True)
        .loc[lambda frame: frame["_merge"].eq("left_only"), keys]
    )
    incremental_075 = entries_075.merge(only_075_keys, on=keys, how="inner")
    incremental_075_metrics = entry_metrics(incremental_075, "floor_0.75_only_city_days")
    istanbul_075 = entry_metrics(
        entries_075[entries_075["city"].eq("Istanbul")], "Istanbul_floor_0.75"
    )
    istanbul_080 = entry_metrics(
        entries_080[entries_080["city"].eq("Istanbul")], "Istanbul_floor_0.80"
    )
    extra = floor_070["all"]["city_days"] - floor_080["all"]["city_days"]
    conclusion = (
        f"欧洲并非没有 carry。把 floor 从 0.80 降到 0.70，历史 first-positive-EV "
        f"city-days 增加 `{extra}`；但是否值得取决于 lower-floor 的 fee ROI、"
        "model-vs-market proper score 与最后 10 日 forward 是否同时成立。"
        "Istanbul 有历史分母但当前 live source 未放行；Moscow 连当前 observation cache "
        "和历史 OOF 分母都不足，二者不能一起直接加进 live。"
    )

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_id": RESEARCH_ID,
        "source": {
            "v3_module": str(args.v3_module),
            "costed_oof": str(args.costed_oof),
            "live_scores": str(args.live_scores),
        },
        "universe": {
            "cities_requested": list(EUROPE_CITIES),
            "cities_covered": sorted(europe["city"].unique()),
            "date_min": min(dates),
            "date_max": max(dates),
        },
        "forward_split": {
            "method": "last_10_target_dates",
            "forward_start_date": forward_start,
            "forward_dates": sorted(forward_dates),
        },
        "historical_funnel": {
            "oof_rows": int(len(europe)),
            "executable_rows": int(europe["five_share_executable"].sum()),
            "exact_rows": int((europe["five_share_executable"] & exact_bounded(europe)).sum()),
            "dates": int(europe["target_date"].nunique()),
        },
        "floor_replay": floor_rows,
        "floor_075_incremental": {
            "frequency_increase_vs_080": (
                floor_075["all"]["city_days"] / floor_080["all"]["city_days"] - 1.0
            ),
            "win_rate_change_vs_080": (
                floor_075["all"]["win_rate"] - floor_080["all"]["win_rate"]
            ),
            "roi_change_vs_080": (
                floor_075["all"]["fee_adjusted_roi"]
                - floor_080["all"]["fee_adjusted_roi"]
            ),
            "only_city_days": incremental_075_metrics,
            "rows": incremental_075[
                [
                    "city",
                    "target_date",
                    "decision_snapshot_ts_utc",
                    "market_mid",
                    "p_v3_no_peak_clock",
                    "five_share_cost_per_share",
                    "label",
                    "model_edge_after_fee_and_depth",
                ]
            ].to_dict(orient="records"),
        },
        "istanbul_replay": {
            "floor_075": istanbul_075,
            "floor_080": istanbul_080,
        },
        "probability_by_mid_band": probability_rows,
        "current_live_funnel": {
            "score_rows": int(len(live_europe)),
            "cities": int(live_europe["city"].nunique()),
            "dates": int(live_europe["target_date"].nunique()),
            "within_frozen_mid_domain": int(within.sum()),
            "positive_taker_ev": int(
                live_europe["decision_status"].eq("positive_taker_ev").sum()
            ),
        },
        "source_coverage": coverage,
        "conclusion": conclusion,
        "gates": {
            "significance": "per_floor",
            "baseline": "per_mid_band",
            "forward": "last_10_dates_direction_check",
            "conclusion": "inconclusive_no_live_change",
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.concat(entry_frames, ignore_index=True).to_csv(
        OUT_DIR / "floor_entries.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "market_mid_floor": row["market_mid_floor"],
                "period": period,
                **metrics,
            }
            for row in floor_rows
            for period, metrics in (
                ("all", row["all"]),
                ("train", row["train"]),
                ("forward", row["forward"]),
            )
        ]
    ).to_csv(OUT_DIR / "floor_metrics.csv", index=False)
    pd.DataFrame(probability_rows).to_csv(
        OUT_DIR / "probability_by_mid_band.csv", index=False
    )
    RESULT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(payload)
    print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
