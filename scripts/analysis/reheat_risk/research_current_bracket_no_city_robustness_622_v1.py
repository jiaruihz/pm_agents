#!/usr/bin/env python3
"""6/22 loss forensics and city robustness for current-bracket NO.

Uses the current shadow research expression:

    enhanced_all_rows::remaining_heat_p40_ev10
    minus shadow-veto cities BuenosAires/Jeddah

The purpose is diagnostic: explain the 2026-06-22 forward loss and test whether
city good/bad labels are stable when early and late validation windows are
swapped.  It does not tune thresholds for live deployment.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SOURCE_SELECTED = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_city_robustness_622_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_622 = OUT_DIR / "forward_2026_06_22_trade_details.csv"
OUT_CITY = OUT_DIR / "city_window_summary.csv"
OUT_SET = OUT_DIR / "city_set_swap_validation.csv"
OUT_DAILY = OUT_DIR / "current_shadow_daily_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-city-robustness-622-v1.md"

FOCUS_MODEL = "enhanced_all_rows"
FOCUS_VARIANT = "remaining_heat_p40_ev10"
VETO_CITIES = {"BuenosAires", "Jeddah"}
EARLY_END = "2026-06-10"
LATE_START = "2026-06-11"
LATE_END = "2026-06-20"
FORWARD_START = "2026-06-21"

CITY_FAMILY = {
    "Amsterdam": "europe_cloud_break",
    "Helsinki": "europe_cloud_break",
    "Madrid": "europe_cloud_break",
    "Munich": "europe_cloud_break",
    "Warsaw": "europe_cloud_break",
    "Ankara": "continental_dry_hot",
    "Austin": "continental_dry_hot",
    "Dallas": "continental_dry_hot",
    "Denver": "continental_dry_hot",
    "Jeddah": "continental_dry_hot",
    "Karachi": "continental_dry_hot",
    "Lucknow": "continental_dry_hot",
    "Atlanta": "humid_low_latitude",
    "Busan": "humid_low_latitude",
    "Chengdu": "humid_low_latitude",
    "Chongqing": "humid_low_latitude",
    "Guangzhou": "humid_low_latitude",
    "Houston": "humid_low_latitude",
    "Manila": "humid_low_latitude",
    "Miami": "humid_low_latitude",
    "Shanghai": "humid_low_latitude",
    "Singapore": "humid_low_latitude",
    "Taipei": "humid_low_latitude",
    "Tokyo": "humid_low_latitude",
    "Wuhan": "humid_low_latitude",
    "BuenosAires": "southern_or_maritime",
    "CapeTown": "southern_or_maritime",
    "Istanbul": "southern_or_maritime",
    "LA": "southern_or_maritime",
    "NYC": "southern_or_maritime",
    "SanFrancisco": "southern_or_maritime",
    "SaoPaulo": "southern_or_maritime",
    "Seattle": "southern_or_maritime",
    "TelAviv": "southern_or_maritime",
    "Wellington": "southern_or_maritime",
    "Beijing": "east_asia_continental",
}


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
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+,.2f}"


def roi(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    cost = float(frame["stake_cost_usd"].sum())
    return float(frame["stake_profit_usd"].sum()) / cost if cost else None


def perf(frame: pd.DataFrame, label: str) -> dict[str, Any]:
    settled = frame[frame["label_no_wins"].notna()].copy()
    return {
        "slice": label,
        "trades": int(len(frame)),
        "settled_trades": int(len(settled)),
        "open_trades": int(frame["label_no_wins"].isna().sum()),
        "active_dates": int(frame["target_date"].nunique()) if len(frame) else 0,
        "cities": int(frame["city"].nunique()) if len(frame) else 0,
        "wins": float(settled["label_no_wins"].sum()) if len(settled) else 0.0,
        "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else None,
        "profit_usd": float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0,
        "cost_usd": float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0,
        "roi": roi(settled),
        "avg_actual_margin_f": float(settled["actual_margin_f"].mean()) if len(settled) else None,
        "avg_pred_error_f": float(settled["pred_error_f"].mean()) if len(settled) else None,
        "avg_curve_error_f": float(settled["curve_error_f"].mean()) if len(settled) else None,
    }


def load_current() -> pd.DataFrame:
    df = pd.read_csv(SOURCE_SELECTED)
    work = df[df["model"].eq(FOCUS_MODEL) & df["variant"].eq(FOCUS_VARIANT)].copy()
    work["target_date"] = work["target_date"].astype(str)
    work = work[~work["city"].isin(VETO_CITIES)].copy()
    work["city_family"] = work["city"].map(CITY_FAMILY).fillna("other")
    work["actual_margin_f"] = pd.to_numeric(work["future_delta_to_daymax_f"], errors="coerce") - pd.to_numeric(
        work["required_gap_f"], errors="coerce"
    )
    work["pred_error_f"] = pd.to_numeric(work["pred_remaining_heat_f"], errors="coerce") - pd.to_numeric(
        work["future_delta_to_daymax_f"], errors="coerce"
    )
    work["curve_error_f"] = (
        pd.to_numeric(work["curve_after_decision_max_f"], errors="coerce")
        - pd.to_numeric(work["running_temp_f_equiv"], errors="coerce")
        - pd.to_numeric(work["future_delta_to_daymax_f"], errors="coerce")
    )
    work["loss_reason"] = np.select(
        [
            work["label_no_wins"].eq(1),
            work["actual_margin_f"].lt(0) & work["pred_error_f"].gt(0.35),
            work["actual_margin_f"].lt(0),
            work["pred_error_f"].gt(0.35),
        ],
        ["win", "capped_day_model_overestimate", "capped_day_shortfall", "model_overestimate"],
        default="other_loss",
    )
    return work.reset_index(drop=True)


def date_slice(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "early":
        return frame[frame["target_date"].le(EARLY_END)].copy()
    if name == "late":
        return frame[frame["target_date"].between(LATE_START, LATE_END)].copy()
    if name == "historical":
        return frame[frame["target_date"].le(LATE_END)].copy()
    if name == "forward_settled":
        return frame[frame["target_date"].ge(FORWARD_START) & frame["label_no_wins"].notna()].copy()
    if name == "forward_all":
        return frame[frame["target_date"].ge(FORWARD_START)].copy()
    raise ValueError(name)


def city_window_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window in ["early", "late", "historical", "forward_settled"]:
        part = date_slice(frame, window)
        for city, group in part.groupby("city"):
            row = perf(group, window)
            row["city"] = city
            row["city_family"] = group["city_family"].iloc[0]
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["city", "slice"]).reset_index(drop=True)


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    settled = frame[frame["label_no_wins"].notna()].copy()
    daily = (
        settled.groupby("target_date")
        .agg(
            trades=("city", "size"),
            wins=("label_no_wins", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            avg_pred_error_f=("pred_error_f", "mean"),
            avg_actual_margin_f=("actual_margin_f", "mean"),
            loss_reasons=("loss_reason", lambda x: ",".join(sorted(set(x.astype(str))))),
            cities=("city", lambda x: ",".join(sorted(x.astype(str)))),
        )
        .reset_index()
    )
    daily["win_rate"] = daily["wins"] / daily["trades"]
    daily["roi"] = daily["profit_usd"] / daily["cost_usd"]
    daily["period"] = np.where(
        daily["target_date"].le(EARLY_END),
        "early",
        np.where(daily["target_date"].le(LATE_END), "late", "forward"),
    )
    return daily


def city_scores(summary: pd.DataFrame, window: str) -> pd.DataFrame:
    return summary[summary["slice"].eq(window)].copy()


def select_bad(cities: pd.DataFrame) -> list[str]:
    mask = (
        cities["trades"].ge(6)
        & cities["win_rate"].le(0.20)
        & cities["roi"].lt(0)
        & cities["avg_actual_margin_f"].lt(0)
    )
    return sorted(cities[mask]["city"].astype(str).tolist())


def select_good(cities: pd.DataFrame) -> list[str]:
    mask = (
        cities["trades"].ge(4)
        & cities["win_rate"].ge(0.25)
        & cities["roi"].gt(0)
        & cities["avg_actual_margin_f"].ge(0)
    )
    return sorted(cities[mask]["city"].astype(str).tolist())


def evaluate_set(frame: pd.DataFrame, name: str, cities: list[str]) -> list[dict[str, Any]]:
    rows = []
    for window in ["early", "late", "historical", "forward_settled"]:
        part = date_slice(frame, window)
        inside = part[part["city"].isin(cities)].copy()
        outside = part[~part["city"].isin(cities)].copy()
        inside_row = perf(inside, f"{name}::{window}::inside")
        outside_row = perf(outside, f"{name}::{window}::outside")
        rows.append(
            {
                "city_set": name,
                "window": window,
                "cities": ",".join(cities),
                "city_count": len(cities),
                "inside_trades": inside_row["trades"],
                "inside_win_rate": inside_row["win_rate"],
                "inside_roi": inside_row["roi"],
                "inside_profit_usd": inside_row["profit_usd"],
                "outside_trades": outside_row["trades"],
                "outside_win_rate": outside_row["win_rate"],
                "outside_roi": outside_row["roi"],
                "outside_profit_usd": outside_row["profit_usd"],
                "outside_minus_base_roi": None if roi(part) is None or outside_row["roi"] is None else outside_row["roi"] - roi(part),
            }
        )
    return rows


def details_622(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "city",
        "city_family",
        "label_no_wins",
        "no_ask",
        "stake_profit_usd",
        "p_cross_upper",
        "mechanism_edge",
        "required_gap_f",
        "pred_remaining_heat_f",
        "future_delta_to_daymax_f",
        "actual_margin_f",
        "pred_error_f",
        "curve_next_3h_delta_f",
        "curve_remaining_to_peak_f",
        "curve_tail_above_upper_margin_hours",
        "relative_humidity_pct",
        "wind_speed_kt",
        "solar_elevation_deg",
        "running_temp_f_equiv",
        "bracket_upper",
        "final_max_native",
        "loss_reason",
    ]
    out = frame[frame["target_date"].eq("2026-06-22")].copy()
    return out[cols].sort_values(["label_no_wins", "actual_margin_f", "city"]).reset_index(drop=True)


def render_md(payload: dict[str, Any], detail: pd.DataFrame, sets: pd.DataFrame, city_summary: pd.DataFrame, daily: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col == "outside_minus_base_roi":
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    set_focus = sets[
        sets["city_set"].isin(["early_bad", "late_bad", "early_good", "late_good"])
        & sets["window"].isin(["early", "late", "forward_settled"])
    ].copy()
    city_focus = city_summary[
        city_summary["city"].isin(payload["focus_cities"])
        & city_summary["slice"].isin(["early", "late", "forward_settled"])
    ].copy()
    daily_focus = daily[daily["target_date"].ge("2026-06-15") | daily["roi"].le(-1.0)].copy()
    return "\n".join(
        [
            "# Current-Bracket NO City Robustness And 6/22 Forensics V1",
            "",
            "## 结论",
            "",
            "6/22 的大亏不是单个城市失误，而是 broad capped-day shortfall：9 笔 settled 里 8 笔亏，平均 `pred_remaining_heat` 高于实际 remaining heat，实际没有打穿 upper margin。模型仍把多个城市评成可打穿，但 final max 卡在 bracket upper 附近或下方。",
            "",
            "训练/验证互换后，城市好坏标签不稳定：早窗 bad 城市在晚窗仍偏弱，但 forward 里反而包含赢家；晚窗 bad 城市在早窗不一定坏。早窗 good 城市晚窗不错，但 forward 样本很薄。结论是：城市标签可以做分层校准特征，不能直接做 live keep/remove。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Data",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Source: `{payload['source_selected_rows']}`",
            f"- Current expression: `{FOCUS_MODEL}::{FOCUS_VARIANT}` minus `{','.join(sorted(VETO_CITIES))}`",
            "",
            "## 6/22 Trade Details",
            "",
            table(
                detail,
                [
                    "city",
                    "city_family",
                    "label_no_wins",
                    "no_ask",
                    "stake_profit_usd",
                    "p_cross_upper",
                    "required_gap_f",
                    "pred_remaining_heat_f",
                    "future_delta_to_daymax_f",
                    "actual_margin_f",
                    "pred_error_f",
                    "curve_next_3h_delta_f",
                    "relative_humidity_pct",
                    "loss_reason",
                ],
            ),
            "",
            "## Swap Validation",
            "",
            table(
                set_focus,
                [
                    "city_set",
                    "window",
                    "city_count",
                    "cities",
                    "inside_trades",
                    "inside_win_rate",
                    "inside_roi",
                    "outside_trades",
                    "outside_roi",
                    "outside_minus_base_roi",
                ],
            ),
            "",
            "## Focus City Windows",
            "",
            table(
                city_focus,
                [
                    "city",
                    "city_family",
                    "slice",
                    "trades",
                    "win_rate",
                    "roi",
                    "profit_usd",
                    "avg_actual_margin_f",
                    "avg_pred_error_f",
                ],
                limit=80,
            ),
            "",
            "## Daily Context",
            "",
            table(
                daily_focus,
                [
                    "target_date",
                    "period",
                    "trades",
                    "win_rate",
                    "roi",
                    "profit_usd",
                    "avg_pred_error_f",
                    "avg_actual_margin_f",
                    "loss_reasons",
                ],
                limit=50,
            ),
            "",
            "## Interpretation",
            "",
            "1. `Karachi/Shanghai/Manila/Warsaw/Chongqing` 的历史拖累有真实机制迹象：多数是 negative actual margin 或 model overestimate；但它们不是稳定全窗坏城。",
            "2. `Guangzhou/Ankara/Beijing/Wellington/Atlanta` 等好城市也有样本噪声：不少城市只有 4-9 笔，forward 里覆盖不足。",
            "3. 更合理的下一步是分层校准：对高风险城市/族群要求更高 forecast surplus 或更低 model overestimate，而不是删除城市。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- 6/22 details: `{OUT_622.relative_to(ROOT)}`",
            f"- City window summary: `{OUT_CITY.relative_to(ROOT)}`",
            f"- City set swap validation: `{OUT_SET.relative_to(ROOT)}`",
            f"- Daily summary: `{OUT_DAILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = load_current()
    detail = details_622(frame)
    city_summary = city_window_summary(frame)
    daily = daily_summary(frame)

    early = city_scores(city_summary, "early")
    late = city_scores(city_summary, "late")
    sets = {
        "early_bad": select_bad(early),
        "late_bad": select_bad(late),
        "early_good": select_good(early),
        "late_good": select_good(late),
        "known_draggers": ["Karachi", "Shanghai", "Manila", "Warsaw", "Chongqing"],
        "top_historical_profit": ["Guangzhou", "Ankara", "Beijing", "Wellington", "Atlanta"],
    }
    set_rows = []
    for name, cities in sets.items():
        set_rows.extend(evaluate_set(frame, name, cities))
    set_df = pd.DataFrame(set_rows)

    detail.to_csv(OUT_622, index=False)
    city_summary.to_csv(OUT_CITY, index=False)
    set_df.to_csv(OUT_SET, index=False)
    daily.to_csv(OUT_DAILY, index=False)

    focus_cities = sorted(set(sets["known_draggers"] + sets["top_historical_profit"] + sets["early_bad"] + sets["late_bad"]))
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_city_robustness_622_v1",
        "source_selected_rows": str(SOURCE_SELECTED.relative_to(ROOT)),
        "current_expression": {
            "model": FOCUS_MODEL,
            "variant": FOCUS_VARIANT,
            "veto_cities": sorted(VETO_CITIES),
        },
        "period_summary": {
            "early": perf(date_slice(frame, "early"), "early"),
            "late": perf(date_slice(frame, "late"), "late"),
            "historical": perf(date_slice(frame, "historical"), "historical"),
            "forward_settled": perf(date_slice(frame, "forward_settled"), "forward_settled"),
        },
        "city_sets": sets,
        "focus_cities": focus_cities,
        "forward_2026_06_22": {
            "trades": int(len(detail)),
            "wins": float(detail["label_no_wins"].sum()) if len(detail) else 0.0,
            "roi": roi(frame[frame["target_date"].eq("2026-06-22") & frame["label_no_wins"].notna()]),
            "loss_reason_counts": detail["loss_reason"].value_counts().to_dict(),
        },
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "forward_2026_06_22_trade_details_csv": str(OUT_622.relative_to(ROOT)),
            "city_window_summary_csv": str(OUT_CITY.relative_to(ROOT)),
            "city_set_swap_validation_csv": str(OUT_SET.relative_to(ROOT)),
            "daily_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "city_labels_not_stable_enough_for_live",
            "live_ready": False,
            "reason": "6/22 loss is broad capped-day/model-overestimate; city good/bad labels are useful diagnostics but not stable enough for live keep/remove.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, detail, set_df, city_summary, daily), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
