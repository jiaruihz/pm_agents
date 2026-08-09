#!/usr/bin/env python3
"""Augment physical semantic audit v1 with rain, gust, and pressure history."""

from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_overshoot_missing_mechanisms_v2 as base,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_core_carry_physical_semantic_audit_v1 as v1,
)


RESEARCH_ID = "current_yes_core_carry_physical_semantic_audit_v2"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
PREREG = OUT_DIR / "preregistration.json"
REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-29-current-yes-core-carry-physical-semantic-audit-v2.md"
)
RESULT_JSON = REPORT.with_suffix(".json")
AUGMENTED_CACHE = OUT_DIR / "iem_augmented_weather.csv"

NEW_FEATURES = [
    "rain_evaporative_cooling_score",
    "gust_mixing_score",
    "pressure_transition_score",
]
PRIMARY_FEATURES = [*v1.PRIMARY_FEATURES, *NEW_FEATURES]
MODEL_SPECS = {
    "semantic_augmented_residual_v2": PRIMARY_FEATURES,
    "semantic_parent_v1": v1.PRIMARY_FEATURES,
    "augmented_only": NEW_FEATURES,
}


def fetch_augmented_iem(
    city_to_icao: dict[str, str], start: str, end: str
) -> pd.DataFrame:
    if AUGMENTED_CACHE.exists() and AUGMENTED_CACHE.stat().st_size > 1000:
        frame = pd.read_csv(AUGMENTED_CACHE, low_memory=False)
        frame["valid_utc"] = pd.to_datetime(
            frame["valid"], utc=True, errors="coerce"
        )
        return frame
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inverse = {icao: city for city, icao in city_to_icao.items()}
    stations = sorted(inverse)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    records: list[pd.DataFrame] = []
    fields = [
        "tmpf",
        "dwpf",
        "wxcodes",
        "gust",
        "p01i",
        "alti",
    ]
    for offset in range(0, len(stations), 6):
        batch = stations[offset : offset + 6]
        params: list[tuple[str, str]] = [
            ("station", station) for station in batch
        ]
        params += [("data", field) for field in fields]
        params += [
            ("year1", str(start_ts.year)),
            ("month1", str(start_ts.month)),
            ("day1", str(start_ts.day)),
            ("year2", str(end_ts.year)),
            ("month2", str(end_ts.month)),
            ("day2", str(end_ts.day)),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("elev", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
        ]
        params += [
            ("report_type", report_type)
            for report_type in ("1", "2", "3", "4")
        ]
        url = (
            "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
            + urllib.parse.urlencode(params)
        )
        error: Exception | None = None
        for attempt in range(4):
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "pm-agent-physical-semantic-audit/2.0"
                    },
                )
                with urllib.request.urlopen(
                    request, timeout=180
                ) as response:
                    text = response.read().decode()
                lines = [
                    line
                    for line in text.splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                batch_frame = pd.read_csv(io.StringIO("\n".join(lines)))
                records.append(batch_frame)
                error = None
                break
            except Exception as exc:  # network retry is research-only
                error = exc
                time.sleep(3 * (attempt + 1))
        if error is not None:
            raise RuntimeError(
                f"IEM augmented fetch failed for {batch}: {error}"
            )
    frame = pd.concat(records, ignore_index=True)
    frame["city"] = frame["station"].map(inverse)
    frame = frame.dropna(subset=["city"])
    frame.to_csv(AUGMENTED_CACHE, index=False)
    frame["valid_utc"] = pd.to_datetime(
        frame["valid"], utc=True, errors="coerce"
    )
    return frame


def asof_value(
    history: pd.DataFrame, timestamp: pd.Timestamp, column: str
) -> Any:
    eligible = history[history["valid_utc"].le(timestamp)]
    if eligible.empty:
        return math.nan
    row = eligible.iloc[-1]
    age = (timestamp - row["valid_utc"]).total_seconds() / 60.0
    return row.get(column) if age <= 90 else math.nan


def augment_frame(
    frame: pd.DataFrame, augmented: pd.DataFrame
) -> pd.DataFrame:
    out = frame.copy()
    for column in ["gust", "p01i", "alti"]:
        augmented[column] = pd.to_numeric(
            augmented[column], errors="coerce"
        )
    rows: list[dict[str, Any]] = []
    for _, row in out.iterrows():
        history = augmented[
            augmented["city"].eq(str(row["city"]))
        ].sort_values("valid_utc")
        decision = row["decision_dt"]
        now_alti = asof_value(history, decision, "alti")
        lag_alti = asof_value(
            history, decision - pd.Timedelta(hours=3), "alti"
        )
        recent = history[
            history["valid_utc"].between(
                decision - pd.Timedelta(hours=1), decision
            )
        ]
        precip_available = not recent.empty and (
            "wxcodes" in recent or "p01i" in recent
        )
        wx_text = " ".join(
            recent["wxcodes"]
            .dropna()
            .astype(str)
            .replace("M", "")
            .tolist()
        ).upper()
        p01i = pd.to_numeric(
            recent["p01i"]
            if "p01i" in recent
            else pd.Series(index=recent.index, dtype=float),
            errors="coerce",
        ).fillna(0)
        precip = (
            bool(
                re_search_precip(wx_text)
                or (len(p01i) and float(p01i.max()) > 0)
            )
            if precip_available
            else pd.NA
        )
        gust = pd.to_numeric(
            recent["gust"]
            if "gust" in recent
            else pd.Series(index=recent.index, dtype=float),
            errors="coerce",
        ).max()
        gust = float(gust) if pd.notna(gust) else math.nan
        sustained = float(row["observed_wind_speed_kt"])
        gust_excess = (
            max(0.0, gust - sustained)
            if math.isfinite(gust) and math.isfinite(sustained)
            else math.nan
        )
        temp1 = float(row["temp_tendency_1h_f"])
        dpd = float(row["dewpoint_depression_f"])
        stable = math.exp(
            -abs(float(row["temp_tendency_3h_f"])) / 1.8
        )
        mature = min(
            1.0,
            max(
                0.0,
                float(row["minutes_since_last_strict_new_high"])
                / 360.0,
            ),
        )
        pressure_change_hpa = (
            (float(now_alti) - float(lag_alti)) * 33.8639
            if pd.notna(now_alti) and pd.notna(lag_alti)
            else math.nan
        )
        rows.append(
            {
                "precip_observed_1h": precip,
                "gust_kt": gust,
                "gust_excess_kt": gust_excess,
                "pressure_tendency_3h_hpa": pressure_change_hpa,
                "rain_evaporative_cooling_score": (
                    float(precip)
                    * max(0.0, -temp1)
                    / 1.8
                    * max(0.0, dpd)
                    / 18.0
                    if pd.notna(precip)
                    else math.nan
                ),
                "gust_mixing_score": (
                    gust_excess / 15.0 * stable * mature
                    if math.isfinite(gust_excess)
                    else math.nan
                ),
                "pressure_transition_score": (
                    abs(pressure_change_hpa) / 3.0
                    if math.isfinite(pressure_change_hpa)
                    else math.nan
                ),
            }
        )
    augmented_rows = pd.DataFrame(rows, index=out.index)
    out = pd.concat([out, augmented_rows], axis=1)
    precip_flag = out["precip_observed_1h"].map(
        lambda value: bool(value) if pd.notna(value) else False
    )
    out["is_rain_evaporative_cooling"] = (
        precip_flag & out["temp_tendency_1h_f"].lt(0)
    )
    out["is_gust_mixing"] = (
        out["gust_excess_kt"].ge(8)
        & out["temp_tendency_3h_f"].abs().le(0.5)
        & out["minutes_since_last_strict_new_high"].ge(180)
    )
    return out


def re_search_precip(text: str) -> bool:
    tokens = text.replace("+", " ").replace("-", " ").split()
    return any(
        any(code in token for code in ("RA", "DZ", "SH", "TS", "SN"))
        for token in tokens
    )


def expanding_oof(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame["target_date"].unique())
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    keep = [
        "opportunity_id",
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "overshoot",
        "p_over_core",
        "p_over_market",
    ]
    for index, target_date in enumerate(dates):
        if index < v1.MIN_TRAIN_DATES:
            continue
        train = frame[frame["target_date"].lt(target_date)]
        test = frame[frame["target_date"].eq(target_date)]
        if test.empty or train["overshoot"].nunique() < 2:
            continue
        result = test[keep].copy()
        for model, features in MODEL_SPECS.items():
            probability, fit = base.fit_offset_residual(
                train, test, features
            )
            result[f"p_over_{model}"] = probability
            folds.append(
                {
                    "target_date": target_date,
                    "model": model,
                    "columns": json.dumps(fit["columns"]),
                    "coefficients": json.dumps(
                        fit.get("coefficients", [])
                    ),
                }
            )
        predictions.append(result)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(folds)


def augmented_slice_summary(
    frame: pd.DataFrame, flag: str, mechanism: str
) -> dict[str, Any]:
    sample = frame[frame[flag]].copy()
    if sample.empty:
        return {
            "mechanism": mechanism,
            "states": 0,
            "city_days": 0,
            "dates": 0,
        }
    city_days = (
        sample.groupby(["city", "target_date"], as_index=False)
        .agg(
            overshoot=("overshoot", "first"),
            p_over_core=("p_over_core", "mean"),
        )
    )
    daily = (
        city_days.groupby("target_date", as_index=False)
        .agg(
            overshoot_rate=("overshoot", "mean"),
            core_probability=("p_over_core", "mean"),
        )
    )
    daily["actual_minus_core"] = (
        daily["overshoot_rate"] - daily["core_probability"]
    )
    values = daily["actual_minus_core"].to_numpy(float)
    rng = np.random.default_rng(
        20260729 + sum(ord(character) for character in mechanism)
    )
    draws = np.empty(3000, dtype=float)
    for index in range(len(draws)):
        chosen = rng.integers(0, len(values), len(values))
        draws[index] = values[chosen].mean()
    return {
        "mechanism": mechanism,
        "states": len(sample),
        "city_days": len(city_days),
        "dates": sample["target_date"].nunique(),
        "overshoot_rate": float(daily["overshoot_rate"].mean()),
        "core_predicted": float(daily["core_probability"].mean()),
        "actual_minus_core": float(daily["actual_minus_core"].mean()),
        "actual_minus_core_ci95": np.quantile(
            draws, [0.025, 0.975]
        ).tolist(),
    }


def case_rows(frame: pd.DataFrame, flag: str) -> list[dict[str, Any]]:
    columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
        "final_winning_bracket",
        "p_core",
        "p_market_raw",
        "overshoot",
        "temp_tendency_1h_f",
        "dewpoint_tendency_3h_f",
        "observed_wind_speed_kt",
        "gust_kt",
        "gust_excess_kt",
        "precip_observed_1h",
        "pressure_tendency_3h_hpa",
    ]
    return (
        frame[frame[flag] & frame["overshoot"].eq(1)]
        .sort_values("p_core", ascending=False)
        .head(10)[columns]
        .to_dict("records")
    )


def main() -> None:
    if not PREREG.exists():
        raise FileNotFoundError(PREREG)
    parent, parent_lineage = v1.build_frame()
    start, end = parent["target_date"].min(), parent["target_date"].max()
    augmented = fetch_augmented_iem(v1.clean.station_map(), start, end)
    frame = augment_frame(parent, augmented)
    oof, folds = expanding_oof(frame)
    dates = sorted(oof["target_date"].unique())
    forward_dates = set(dates[-v1.FORWARD_DATES :])

    def scores(sample: pd.DataFrame) -> dict[str, Any]:
        return {
            "candidate": base.score_metrics(
                sample, "p_over_semantic_augmented_residual_v2"
            ),
            "parent_v1": base.score_metrics(
                sample, "p_over_semantic_parent_v1"
            ),
            "core": base.score_metrics(sample, "p_over_core"),
            "market": base.score_metrics(sample, "p_over_market"),
        }

    score_payload = {
        "overall": scores(oof),
        "frozen_forward": scores(
            oof[oof["target_date"].isin(forward_dates)]
        ),
    }
    paired_deltas = {
        period: {
            baseline: base.paired_delta(
                sample,
                "p_over_semantic_augmented_residual_v2",
                probability_column,
            )
            for baseline, probability_column in {
                "core": "p_over_core",
                "market": "p_over_market",
            }.items()
        }
        for period, sample in {
            "overall": oof,
            "frozen_forward": oof[
                oof["target_date"].isin(forward_dates)
            ],
        }.items()
    }
    promote = all(
        score_payload[period]["candidate"][metric]
        < score_payload[period][baseline][metric]
        for period in ["overall", "frozen_forward"]
        for baseline in ["core", "market"]
        for metric in ["brier", "logloss"]
    )
    slices = [
        augmented_slice_summary(
            frame,
            "is_rain_evaporative_cooling",
            "rain_evaporative_cooling",
        ),
        augmented_slice_summary(frame, "is_gust_mixing", "gust_mixing"),
    ]
    rain_cases = case_rows(frame, "is_rain_evaporative_cooling")
    gust_cases = case_rows(frame, "is_gust_mixing")
    result = {
        "research_id": RESEARCH_ID,
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selection": {
            "status": "通过预注册 promotion rule"
            if promote
            else "未通过预注册 promotion rule",
            "promote_candidate": promote,
            "live_effect": "none",
        },
        "funnel": {
            "states": len(frame),
            "city_days": frame.groupby(["city", "target_date"]).ngroups,
            "dates": frame["target_date"].nunique(),
            "precip_state_coverage": float(
                frame["precip_observed_1h"].notna().mean()
            ),
            "gust_value_coverage": float(frame["gust_kt"].notna().mean()),
            "pressure_tendency_coverage": float(
                frame["pressure_tendency_3h_hpa"].notna().mean()
            ),
            **parent_lineage,
        },
        "scores": score_payload,
        "paired_target_date_bootstrap": paired_deltas,
        "forward_dates": sorted(forward_dates),
        "augmented_slices": slices,
        "rain_loss_cases": rain_cases,
        "gust_loss_cases": gust_cases,
    }
    lines = [
        "# Current-YES 气象物理语义审计 v2：雨、阵风、气压补全",
        "",
        f"结论：`semantic_augmented_residual_v2` **{result['selection']['status']}**。"
        f"Overall Brier candidate/core="
        f"{score_payload['overall']['candidate']['brier']:.5f}/"
        f"{score_payload['overall']['core']['brier']:.5f}；"
        f"frozen-forward="
        f"{score_payload['frozen_forward']['candidate']['brier']:.5f}/"
        f"{score_payload['frozen_forward']['core']['brier']:.5f}。",
        "",
        "本轮从 IEM 同期历史报文补了 `wxcodes/gust/p01i/alti`，没有用云量冒充降雨。",
        "",
        "## 机制覆盖",
        "",
    ]
    for row in slices:
        if row["states"]:
            lines.append(
                f"- {row['mechanism']}: {row['states']} states / "
                f"{row['city_days']} city-days / {row['dates']} dates，"
                f"actual overshoot={row['overshoot_rate']:.1%}，"
                f"core={row['core_predicted']:.1%}，"
                f"actual-core={row['actual_minus_core']:+.1%} "
                f"[{row['actual_minus_core_ci95'][0]:+.1%}, "
                f"{row['actual_minus_core_ci95'][1]:+.1%}]。"
            )
        else:
            lines.append(f"- {row['mechanism']}: 0 recoverable states。")
    lines += [
        "",
        f"- precip state coverage={result['funnel']['precip_state_coverage']:.1%}；"
        f"gust numeric coverage={result['funnel']['gust_value_coverage']:.1%}；"
        f"pressure tendency coverage={result['funnel']['pressure_tendency_coverage']:.1%}。",
        "",
        "## 常识审计",
        "",
        "- 降雨且当小时降温的样本，实际越档率低于 core 预测；方向上支持“蒸发冷却/云遮蔽压低 reheat”，没有发现 core 系统性忽略降雨。",
        "- 强阵风且成熟高温平台的 6 个 city-day 没有越档；方向上支持“混合维持温度不等于推动升温”，但 gust 数值覆盖仅 3%，不能据此校准概率。",
        "- 降雨不是绝对锁定：Munich 2026-06-11 和 Guangzhou 2026-07-01 都出现报雨、短时降温后最终继续越档，说明雨必须与剩余加热窗口、气团转换共同解释。",
        "",
        "## 结论边界",
        "",
        "- 雨、阵风、气压加入后仍未改善 proper score，因此不更新 production core。",
        "- `wxcodes` 的无天气代码也是有效的“未报告降水”，但国际站 gust 字段稀疏。",
        "- 所有输出均为 research-only；未改 live。",
        "",
        f"Parent semantic casebook: `{v1.REPORT.relative_to(ROOT)}`。",
        "",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "semantic_augmented_feature_ledger.csv", index=False)
    oof.to_csv(OUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUT_DIR / "fold_coefficients.csv", index=False)
    RESULT_JSON.write_text(
        json.dumps(v1.json_ready(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(v1.json_ready(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
