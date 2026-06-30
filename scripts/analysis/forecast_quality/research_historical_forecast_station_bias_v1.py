#!/usr/bin/env python3
"""Historical forecast-vs-station max-temperature bias study.

This reproduces the old mid-price/probability-model evidence layer:
daily error = station actual daily max - model forecast daily max.

The input data intentionally comes from the adjacent legacy weather-predict
checkout because that is where the long WU/forecast caches live.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_WEATHER_PREDICT_DIR = Path("/Users/deepsleep/projects/weather-predict")
DEFAULT_OUT_DIR = Path(
    "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1"
)
DEFAULT_REPORT = Path(
    "docs/analysis/2026-06/2026-06-30-historical-forecast-station-bias-v1.md"
)

MODEL_PREFIXES = {
    "gfs": ("gfs_v4_", "gfs_365d_"),
    "ecmwf": ("ecmwf_v4_",),
}

FOCUS_CITIES = [
    "Chengdu",
    "Tokyo",
    "NYC",
    "SanFrancisco",
    "Seattle",
    "Miami",
    "Karachi",
    "Jeddah",
    "Lucknow",
    "Chongqing",
    "Manila",
    "Shanghai",
    "Warsaw",
]


def _read_city_configs(weather_predict_dir: Path) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(weather_predict_dir))
    try:
        from city_pools import FULL_CITY_CONFIGS, TRADING_T1_CITIES  # type: ignore
    finally:
        sys.path.pop(0)

    out: dict[str, dict[str, Any]] = {}
    for city, cfg in FULL_CITY_CONFIGS.items():
        row = dict(cfg)
        row["city_pool"] = "TRADING_T1" if city in TRADING_T1_CITIES else "RESEARCH_T2"
        out[city] = row
    return out


def _load_calibration_results(weather_predict_dir: Path) -> dict[str, Any]:
    path = weather_predict_dir / "calibration_results_v5.json"
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def _forecast_cache_score(path: Path) -> tuple[int, float, str]:
    try:
        with path.open() as f:
            raw = json.load(f)
        n = len(raw.get("hourly", {}).get("time", []))
    except Exception:
        n = 0
    return (n, path.stat().st_mtime, str(path))


def _find_forecast_cache(
    weather_predict_dir: Path, model: str, city: str
) -> Path | None:
    aliases = [city]
    if city == "LosAngeles":
        aliases.append("LA")
    elif city == "LA":
        aliases.append("LosAngeles")

    roots = [
        weather_predict_dir / "cache",
        weather_predict_dir / "cache_global",
        weather_predict_dir / "cache_global_full",
    ]
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for alias in aliases:
            for prefix in MODEL_PREFIXES[model]:
                matches.extend(
                    p for p in root.glob(f"{prefix}{alias}_*.json") if p.exists()
                )
    if not matches:
        return None
    return max(matches, key=_forecast_cache_score)


def _load_wu_daily_max_f(weather_predict_dir: Path, icao: str) -> dict[str, float]:
    path = weather_predict_dir / "cache" / "wu_obs" / f"wu_obs_{icao}.csv"
    daily: dict[str, float] = {}
    if not path.exists():
        return daily
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_local = (row.get("date_local") or "").strip()
            temp_raw = (row.get("temp") or "").strip()
            if not date_local or not temp_raw or temp_raw in {"M", "null", "None"}:
                continue
            try:
                temp_f = float(temp_raw)
            except ValueError:
                continue
            if date_local not in daily or temp_f > daily[date_local]:
                daily[date_local] = temp_f
    return daily


def _load_forecast_daily_max_f(path: Path, tz_offset_hours: float) -> dict[str, float]:
    with path.open() as f:
        raw = json.load(f)
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    daily: dict[str, float] = {}
    for ts, temp in zip(times, temps):
        if temp is None:
            continue
        try:
            utc_dt = datetime.fromisoformat(str(ts))
            temp_f = float(temp)
        except (TypeError, ValueError):
            continue
        local_dt = utc_dt + timedelta(hours=tz_offset_hours)
        date_local = local_dt.strftime("%Y-%m-%d")
        if date_local not in daily or temp_f > daily[date_local]:
            daily[date_local] = temp_f
    return daily


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rmse(values: list[float]) -> float | None:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else None


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [float(r["error_market_unit"]) for r in rows]
    abs_errors = [abs(x) for x in errors]
    return {
        "n": len(rows),
        "first_date": min(r["date"] for r in rows) if rows else "",
        "last_date": max(r["date"] for r in rows) if rows else "",
        "bias": _mean(errors),
        "mae": _mean(abs_errors),
        "rmse": _rmse(errors),
        "p05": _quantile(errors, 0.05),
        "p10": _quantile(errors, 0.10),
        "p25": _quantile(errors, 0.25),
        "p50": _quantile(errors, 0.50),
        "p75": _quantile(errors, 0.75),
        "p90": _quantile(errors, 0.90),
        "p95": _quantile(errors, 0.95),
        "pct_actual_ge_forecast_plus_1": (
            sum(1 for x in errors if x >= 1.0) / len(errors) if errors else None
        ),
        "pct_forecast_ge_actual_plus_1": (
            sum(1 for x in errors if x <= -1.0) / len(errors) if errors else None
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    out = []
    out.append("| " + " | ".join(label for _, label in columns) + " |")
    out.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        out.append("| " + " | ".join(str(row.get(key, "")) for key, _ in columns) + " |")
    return "\n".join(out)


def build(args: argparse.Namespace) -> dict[str, Any]:
    weather_predict_dir = Path(args.weather_predict_dir).expanduser().resolve()
    out_dir = Path(args.out_dir)
    report_path = Path(args.report)
    city_configs = _read_city_configs(weather_predict_dir)
    calibration = _load_calibration_results(weather_predict_dir)

    daily_rows: list[dict[str, Any]] = []
    cache_rows: list[dict[str, Any]] = []
    for city, cfg in sorted(city_configs.items()):
        icao = cfg.get("icao")
        if not icao:
            continue
        unit = cfg.get("unit") or "F"
        tz_offset = float(cfg.get("tz_offset") or 0)
        actual_daily_f = _load_wu_daily_max_f(weather_predict_dir, str(icao))
        if not actual_daily_f:
            continue
        best_model = calibration.get(city, {}).get("_best_model", "")

        for model in MODEL_PREFIXES:
            cache_path = _find_forecast_cache(weather_predict_dir, model, city)
            if cache_path is None:
                continue
            forecast_daily_f = _load_forecast_daily_max_f(cache_path, tz_offset)
            common_dates = sorted(set(actual_daily_f) & set(forecast_daily_f))
            if not common_dates:
                continue
            cache_rows.append(
                {
                    "city": city,
                    "model": model,
                    "cache_path": str(cache_path),
                    "cache_file": cache_path.name,
                    "forecast_days": len(forecast_daily_f),
                    "actual_days": len(actual_daily_f),
                    "common_days": len(common_dates),
                    "first_common_date": common_dates[0],
                    "last_common_date": common_dates[-1],
                }
            )
            for date in common_dates:
                actual_f = actual_daily_f[date]
                forecast_f = forecast_daily_f[date]
                error_f = actual_f - forecast_f
                if unit == "C":
                    actual_market = (actual_f - 32.0) * 5.0 / 9.0
                    forecast_market = (forecast_f - 32.0) * 5.0 / 9.0
                    error_market = error_f * 5.0 / 9.0
                else:
                    actual_market = actual_f
                    forecast_market = forecast_f
                    error_market = error_f
                daily_rows.append(
                    {
                        "city": city,
                        "city_pool": cfg.get("city_pool", ""),
                        "region": cfg.get("region", ""),
                        "icao": icao,
                        "unit": unit,
                        "date": date,
                        "month": date[:7],
                        "model": model,
                        "is_best_model": model == best_model,
                        "actual_max_f": round(actual_f, 3),
                        "forecast_max_f": round(forecast_f, 3),
                        "error_f_actual_minus_forecast": round(error_f, 3),
                        "actual_max_market_unit": round(actual_market, 3),
                        "forecast_max_market_unit": round(forecast_market, 3),
                        "error_market_unit": round(error_market, 3),
                        "forecast_cache_file": cache_path.name,
                    }
                )

    summary_rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[(row["city"], row["model"])].append(row)
    for (city, model), rows in sorted(grouped.items()):
        unit = rows[0]["unit"]
        s = _summarize_rows(rows)
        summary_rows.append(
            {
                "city": city,
                "city_pool": rows[0]["city_pool"],
                "region": rows[0]["region"],
                "icao": rows[0]["icao"],
                "unit": unit,
                "model": model,
                "is_best_model": any(r["is_best_model"] for r in rows),
                **{k: round(v, 4) if isinstance(v, float) else v for k, v in s.items()},
            }
        )

    month_rows: list[dict[str, Any]] = []
    month_grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        month_grouped[(row["city"], row["model"], row["month"])].append(row)
    for (city, model, month), rows in sorted(month_grouped.items()):
        s = _summarize_rows(rows)
        month_rows.append(
            {
                "city": city,
                "unit": rows[0]["unit"],
                "model": model,
                "month": month,
                **{k: round(v, 4) if isinstance(v, float) else v for k, v in s.items()},
            }
        )

    best_rows = [r for r in summary_rows if r["is_best_model"]]
    focus_rows = [
        r for r in summary_rows if r["city"] in FOCUS_CITIES and r["is_best_model"]
    ]
    focus_rows.sort(key=lambda r: FOCUS_CITIES.index(r["city"]))

    _write_csv(
        out_dir / "daily_error_rows.csv",
        daily_rows,
        [
            "city",
            "city_pool",
            "region",
            "icao",
            "unit",
            "date",
            "month",
            "model",
            "is_best_model",
            "actual_max_f",
            "forecast_max_f",
            "error_f_actual_minus_forecast",
            "actual_max_market_unit",
            "forecast_max_market_unit",
            "error_market_unit",
            "forecast_cache_file",
        ],
    )
    _write_csv(out_dir / "forecast_cache_inventory.csv", cache_rows, list(cache_rows[0]))
    _write_csv(out_dir / "city_model_error_summary.csv", summary_rows, list(summary_rows[0]))
    _write_csv(out_dir / "city_model_month_error_summary.csv", month_rows, list(month_rows[0]))
    _write_csv(out_dir / "focus_best_model_error_summary.csv", focus_rows, list(focus_rows[0]))

    overall_best_by_unit: list[dict[str, Any]] = []
    for unit in sorted({r["unit"] for r in best_rows}):
        rows = [r for r in daily_rows if r["unit"] == unit and r["is_best_model"]]
        s = _summarize_rows(rows)
        overall_best_by_unit.append(
            {
                "unit": unit,
                "n": s["n"],
                "first_date": s["first_date"],
                "last_date": s["last_date"],
                "bias": _fmt(s["bias"]),
                "mae": _fmt(s["mae"]),
                "rmse": _fmt(s["rmse"]),
                "p10": _fmt(s["p10"]),
                "p50": _fmt(s["p50"]),
                "p90": _fmt(s["p90"]),
                "pct_hot_tail_ge_1": _fmt(
                    (s["pct_actual_ge_forecast_plus_1"] or 0) * 100, 1
                )
                + "%",
                "pct_cold_tail_ge_1": _fmt(
                    (s["pct_forecast_ge_actual_plus_1"] or 0) * 100, 1
                )
                + "%",
            }
        )

    focus_md_rows = []
    for r in focus_rows:
        focus_md_rows.append(
            {
                "city": r["city"],
                "unit": r["unit"],
                "model": r["model"],
                "n": r["n"],
                "range": f"{r['first_date']}..{r['last_date']}",
                "bias": _fmt(r["bias"]),
                "mae": _fmt(r["mae"]),
                "rmse": _fmt(r["rmse"]),
                "p10": _fmt(r["p10"]),
                "p50": _fmt(r["p50"]),
                "p90": _fmt(r["p90"]),
                "hot_tail": _fmt(r["pct_actual_ge_forecast_plus_1"] * 100, 1) + "%",
                "cold_tail": _fmt(r["pct_forecast_ge_actual_plus_1"] * 100, 1) + "%",
            }
        )

    worst_hot = sorted(
        best_rows,
        key=lambda r: (r["p90"] if r["p90"] is not None else -999),
        reverse=True,
    )[:12]
    worst_cold = sorted(best_rows, key=lambda r: r["p10"] if r["p10"] is not None else 999)[:12]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# Historical Forecast Station Bias v1",
                "",
                f"Generated: 2026-06-30",
                "",
                "## Verdict",
                "",
                "用户记忆的方向是对的：早期 mid-price / probability-model 不是看单城市单天，而是用历史 forecast-vs-station 误差分布校准概率。这里复现的核心口径是：",
                "",
                "`error = station actual daily max - model forecast daily max`",
                "",
                "正数表示实际比预报更热；负数表示预报比实际更热。对温度 NO 来说，正尾越厚，越容易被实际高温打穿。",
                "",
                "## Data",
                "",
                f"- Source checkout: `{weather_predict_dir}`",
                "- Actual: `cache/wu_obs/wu_obs_<ICAO>.csv`, station daily max from WU/IEM-style station rows.",
                "- Forecast: `cache`, `cache_global`, `cache_global_full`; for each city/model pick the cache with most hourly rows.",
                "- Forecast cache and WU temperatures are in °F; C-market cities are converted to °C error for market-unit summaries.",
                "- Models included here: GFS and ECMWF, because these are the sources relevant to current weather strategy routing.",
                "",
                "Important boundary: the old review doc mentions about 735 days, and those two-year GFS v4 files exist for a subset of cities. Full ECMWF coverage is mostly about 354 common days. This report reports the actual cache used per city/model in `forecast_cache_inventory.csv`.",
                "",
                "## Overall Best-Model Distribution",
                "",
                _markdown_table(
                    overall_best_by_unit,
                    [
                        ("unit", "unit"),
                        ("n", "rows"),
                        ("first_date", "first"),
                        ("last_date", "last"),
                        ("bias", "bias"),
                        ("mae", "MAE"),
                        ("rmse", "RMSE"),
                        ("p10", "p10"),
                        ("p50", "p50"),
                        ("p90", "p90"),
                        ("pct_hot_tail_ge_1", "actual>=fcst+1"),
                        ("pct_cold_tail_ge_1", "fcst>=actual+1"),
                    ],
                ),
                "",
                "## Focus Cities Best-Model Bias",
                "",
                _markdown_table(
                    focus_md_rows,
                    [
                        ("city", "city"),
                        ("unit", "unit"),
                        ("model", "model"),
                        ("n", "n"),
                        ("range", "range"),
                        ("bias", "bias"),
                        ("mae", "MAE"),
                        ("rmse", "RMSE"),
                        ("p10", "p10"),
                        ("p50", "p50"),
                        ("p90", "p90"),
                        ("hot_tail", "actual>=fcst+1"),
                        ("cold_tail", "fcst>=actual+1"),
                    ],
                ),
                "",
                "## Hottest Positive-Tail Cities",
                "",
                _markdown_table(
                    [
                        {
                            "city": r["city"],
                            "unit": r["unit"],
                            "model": r["model"],
                            "n": r["n"],
                            "bias": _fmt(r["bias"]),
                            "p90": _fmt(r["p90"]),
                            "p95": _fmt(r["p95"]),
                            "hot_tail": _fmt(r["pct_actual_ge_forecast_plus_1"] * 100, 1)
                            + "%",
                        }
                        for r in worst_hot
                    ],
                    [
                        ("city", "city"),
                        ("unit", "unit"),
                        ("model", "model"),
                        ("n", "n"),
                        ("bias", "bias"),
                        ("p90", "p90"),
                        ("p95", "p95"),
                        ("hot_tail", "actual>=fcst+1"),
                    ],
                ),
                "",
                "## Cold/Overforecast-Tail Cities",
                "",
                _markdown_table(
                    [
                        {
                            "city": r["city"],
                            "unit": r["unit"],
                            "model": r["model"],
                            "n": r["n"],
                            "bias": _fmt(r["bias"]),
                            "p10": _fmt(r["p10"]),
                            "p05": _fmt(r["p05"]),
                            "cold_tail": _fmt(r["pct_forecast_ge_actual_plus_1"] * 100, 1)
                            + "%",
                        }
                        for r in worst_cold
                    ],
                    [
                        ("city", "city"),
                        ("unit", "unit"),
                        ("model", "model"),
                        ("n", "n"),
                        ("bias", "bias"),
                        ("p10", "p10"),
                        ("p05", "p05"),
                        ("cold_tail", "fcst>=actual+1"),
                    ],
                ),
                "",
                "## Strategy Implication",
                "",
                "- 单日复盘只能解释事故，不能给出 source/station margin。策略侧应该使用 city + source 的历史误差分布，至少拿 p10/p50/p90 或 tail probability 当 feature。",
                "- current-bracket NO 的危险不是一个固定 0.5°C/1°F margin，而是该城市/模型在相同站点口径下的正尾概率。",
                "- d2/higher NO 和 capped forecast 也一样：要看 `actual - forecast` 的正尾，而不是只看 forecast peak 离 bracket 有多远。",
                "- 这个层应该是 feature/calibration layer，不是新增 hard gate；是否交易仍由盘口价格、route、执行纪律和实时 regime 一起决定。",
                "",
                "## Artifacts",
                "",
                f"- Daily rows: `{out_dir / 'daily_error_rows.csv'}`",
                f"- City/model summary: `{out_dir / 'city_model_error_summary.csv'}`",
                f"- Month summary: `{out_dir / 'city_model_month_error_summary.csv'}`",
                f"- Focus summary: `{out_dir / 'focus_best_model_error_summary.csv'}`",
                f"- Cache inventory: `{out_dir / 'forecast_cache_inventory.csv'}`",
                "- Generated CSVs are reproducible and ignored by git per `.gitignore`; rerun the script to recreate them.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "daily_rows": len(daily_rows),
        "summary_rows": len(summary_rows),
        "month_rows": len(month_rows),
        "report": str(report_path),
        "out_dir": str(out_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weather-predict-dir", default=str(DEFAULT_WEATHER_PREDICT_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    result = build(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
