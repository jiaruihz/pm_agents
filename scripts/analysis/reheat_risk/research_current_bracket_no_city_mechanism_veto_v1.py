#!/usr/bin/env python3
"""Train-only city mechanism veto diagnostics for current-bracket NO.

This is not a search for the best city blacklist.  Candidate city vetoes are
identified from the train window only, using interpretable failure mechanics,
then checked on holdout and 2026-06-21..23 forward.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_family import CITY_FAMILY_CURRENT_BRACKET_NO_V1 as CITY_FAMILY  # noqa: E402

SOURCE_SELECTED = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_city_mechanism_veto_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_CITY_TRAIN = OUT_DIR / "train_city_mechanism_summary.csv"
OUT_RULES = OUT_DIR / "rule_summary.csv"
OUT_RULE_CITIES = OUT_DIR / "rule_city_membership.csv"
OUT_CURRENT_SUMMARY = OUT_DIR / "current_shadow_strategy_summary.csv"
OUT_CURRENT_DAILY = OUT_DIR / "current_shadow_daily_summary.csv"
OUT_CURRENT_CITY = OUT_DIR / "current_shadow_city_distribution.csv"
OUT_CURRENT_FAMILY = OUT_DIR / "current_shadow_family_distribution.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-city-mechanism-veto-v1.md"

FOCUS_MODEL = "enhanced_all_rows"
FOCUS_VARIANT = "remaining_heat_p40_ev10"
TRAIN_END = "2026-06-10"
HOLDOUT_END = "2026-06-20"


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


def load_focus() -> pd.DataFrame:
    df = pd.read_csv(SOURCE_SELECTED)
    out = df[df["model"].eq(FOCUS_MODEL) & df["variant"].eq(FOCUS_VARIANT)].copy()
    out["target_date"] = out["target_date"].astype(str)
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    out["actual_margin_f"] = pd.to_numeric(out["future_delta_to_daymax_f"], errors="coerce") - pd.to_numeric(out["required_gap_f"], errors="coerce")
    out["pred_error_f"] = pd.to_numeric(out["pred_remaining_heat_f"], errors="coerce") - pd.to_numeric(out["future_delta_to_daymax_f"], errors="coerce")
    out["curve_error_f"] = (
        pd.to_numeric(out["curve_after_decision_max_f"], errors="coerce")
        - pd.to_numeric(out["running_temp_f_equiv"], errors="coerce")
        - pd.to_numeric(out["future_delta_to_daymax_f"], errors="coerce")
    )
    return out.reset_index(drop=True)


def split_frames(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "train": frame[frame["target_date"].le(TRAIN_END)].copy(),
        "holdout": frame[frame["target_date"].gt(TRAIN_END) & frame["target_date"].le(HOLDOUT_END)].copy(),
        "forward_settled": frame[frame["target_date"].gt(HOLDOUT_END) & frame["label_no_wins"].notna()].copy(),
        "forward_all": frame[frame["target_date"].gt(HOLDOUT_END)].copy(),
    }


def train_city_summary(train: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for city, group in train.groupby("city"):
        rows.append(
            {
                "city": city,
                "city_family": group["city_family"].iloc[0],
                "trades": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "wins": float(group["label_no_wins"].sum()),
                "win_rate": float(group["label_no_wins"].mean()),
                "profit_usd": float(group["stake_profit_usd"].sum()),
                "cost_usd": float(group["stake_cost_usd"].sum()),
                "roi": roi(group),
                "avg_actual_margin_f": float(group["actual_margin_f"].mean()),
                "avg_pred_error_f": float(group["pred_error_f"].mean()),
                "avg_curve_error_f": float(group["curve_error_f"].mean()),
                "avg_required_gap_f": float(pd.to_numeric(group["required_gap_f"], errors="coerce").mean()),
                "avg_actual_remaining_heat_f": float(pd.to_numeric(group["future_delta_to_daymax_f"], errors="coerce").mean()),
                "avg_curve_next_3h_delta_f": float(pd.to_numeric(group["curve_next_3h_delta_f"], errors="coerce").mean()),
                "avg_relative_humidity_pct": float(pd.to_numeric(group["relative_humidity_pct"], errors="coerce").mean()),
                "avg_wind_speed_kt": float(pd.to_numeric(group["wind_speed_kt"], errors="coerce").mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["win_rate", "trades"], ascending=[True, False]).reset_index(drop=True)


def summarize_eval(rule: str, cities: list[str], frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for period, frame in frames.items():
        if period == "forward_all":
            continue
        removed = frame[frame["city"].isin(cities)].copy()
        kept = frame[~frame["city"].isin(cities)].copy()
        rows.append(
            {
                "rule": rule,
                "period": period,
                "removed_cities": ",".join(cities),
                "removed_city_count": len(cities),
                "base_trades": int(len(frame)),
                "base_roi": roi(frame),
                "kept_trades": int(len(kept)),
                "kept_roi": roi(kept),
                "removed_trades": int(len(removed)),
                "removed_roi": roi(removed),
                "roi_lift": None if roi(frame) is None or roi(kept) is None else roi(kept) - roi(frame),
                "base_win_rate": float(frame["label_no_wins"].mean()) if len(frame) else None,
                "kept_win_rate": float(kept["label_no_wins"].mean()) if len(kept) else None,
                "removed_win_rate": float(removed["label_no_wins"].mean()) if len(removed) else None,
            }
        )
    return rows


def current_shadow_strategy_outputs(frame: pd.DataFrame, veto_cities: list[str]) -> dict[str, Any]:
    work = frame[~frame["city"].isin(veto_cities)].copy()
    work["current_shadow_strategy"] = f"{FOCUS_MODEL}::{FOCUS_VARIANT}__veto_{'_'.join(veto_cities)}"
    periods = [
        ("train", "2026-05-20", TRAIN_END),
        ("holdout", "2026-06-11", HOLDOUT_END),
        ("historical_all", "2026-05-20", HOLDOUT_END),
        ("forward_all", "2026-06-21", "2026-06-23"),
    ]
    summary_rows = []
    for name, start, end in periods:
        d = work[work["target_date"].between(start, end)].copy()
        settled = d[d["label_no_wins"].notna()].copy()
        summary_rows.append(
            {
                "period": name,
                "selected_trades": int(len(d)),
                "settled_trades": int(len(settled)),
                "open_trades": int(d["label_no_wins"].isna().sum()),
                "active_dates": int(d["target_date"].nunique()),
                "cities": int(d["city"].nunique()),
                "wins": float(settled["label_no_wins"].sum()) if len(settled) else 0.0,
                "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else None,
                "profit_usd": float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0,
                "cost_usd": float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0,
                "roi": roi(settled),
                "avg_trades_per_day": float(len(d) / d["target_date"].nunique()) if d["target_date"].nunique() else None,
            }
        )
    current_summary = pd.DataFrame(summary_rows)
    settled_all = work[work["label_no_wins"].notna()].copy()
    daily = (
        settled_all.groupby("target_date")
        .agg(
            trades=("city", "size"),
            wins=("label_no_wins", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            cities=("city", lambda x: ",".join(sorted(x.astype(str)))),
        )
        .reset_index()
    )
    daily["win_rate"] = daily["wins"] / daily["trades"]
    daily["roi"] = daily["profit_usd"] / daily["cost_usd"]
    daily["period"] = np.where(
        daily["target_date"].le(TRAIN_END),
        "train",
        np.where(daily["target_date"].le(HOLDOUT_END), "holdout", "forward"),
    )
    hist = work[work["target_date"].le(HOLDOUT_END)].copy()
    city = (
        hist.groupby("city")
        .agg(
            trades=("city", "size"),
            active_dates=("target_date", "nunique"),
            wins=("label_no_wins", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            avg_no_ask=("no_ask", "mean"),
            avg_required_gap_f=("required_gap_f", "mean"),
            avg_pred_remaining_heat_f=("pred_remaining_heat_f", "mean"),
            avg_actual_remaining_heat_f=("future_delta_to_daymax_f", "mean"),
        )
        .reset_index()
    )
    city["win_rate"] = city["wins"] / city["trades"]
    city["roi"] = city["profit_usd"] / city["cost_usd"]
    city = city.sort_values(["profit_usd", "trades"], ascending=[False, False]).reset_index(drop=True)
    family = (
        hist.groupby("city_family")
        .agg(
            trades=("city", "size"),
            cities=("city", "nunique"),
            active_dates=("target_date", "nunique"),
            wins=("label_no_wins", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
        )
        .reset_index()
    )
    family["win_rate"] = family["wins"] / family["trades"]
    family["roi"] = family["profit_usd"] / family["cost_usd"]
    family = family.sort_values("profit_usd", ascending=False).reset_index(drop=True)

    current_summary.to_csv(OUT_CURRENT_SUMMARY, index=False)
    daily.to_csv(OUT_CURRENT_DAILY, index=False)
    city.to_csv(OUT_CURRENT_CITY, index=False)
    family.to_csv(OUT_CURRENT_FAMILY, index=False)
    return {
        "summary": current_summary,
        "daily": daily,
        "city": city,
        "family": family,
        "veto_cities": veto_cities,
    }


def render_md(
    payload: dict[str, Any],
    city: pd.DataFrame,
    rules: pd.DataFrame,
    members: pd.DataFrame,
    current: dict[str, Any],
) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col == "roi_lift":
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus_rules = rules[rules["rule"].isin(["train_zero_win_min4", "mechanism_low15_min6_neg_margin_overpred", "mechanism_low20_min8_neg_margin"])]
    current_summary = current["summary"]
    current_daily = current["daily"]
    current_city = current["city"]
    current_family = current["family"]
    daily_focus = current_daily[
        current_daily["period"].eq("forward")
        | current_daily["wins"].eq(0)
        | current_daily["target_date"].ge("2026-06-15")
    ].copy()
    return "\n".join(
        [
            "# Current-Bracket NO City Mechanism Veto V1",
            "",
            "## 结论",
            "",
            "可以继续研究 city veto，但现在还不能把它当 live 删除规则。唯一在 forward 也同向改善的是 `train_zero_win_min4`，它只抓出 `BuenosAires,Jeddah`：train 去掉后 ROI +5.4pct，holdout +0.8pct，forward settled 从 -35.3% 到 -28.5%。这是候选 shadow veto，不是 confirmed。",
            "",
            "更“机制化”的低胜率+负 margin+模型高估规则能改善 train/holdout，但 forward 反而变差，说明它会删掉 forward 里的赢家，不能上线。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## 数据层",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Source: `{payload['source_selected_rows']}`",
            f"- Focus: `{FOCUS_MODEL}::{FOCUS_VARIANT}`",
            f"- Train end: `{TRAIN_END}`; holdout end: `{HOLDOUT_END}`",
            "",
            "## Rule Evaluation",
            "",
            table(
                focus_rules,
                ["rule", "period", "removed_cities", "base_trades", "base_roi", "kept_trades", "kept_roi", "removed_trades", "removed_roi", "roi_lift", "kept_win_rate"],
            ),
            "",
            "## Current Shadow Version",
            "",
            "当前研究版定义：`enhanced_all_rows::remaining_heat_p40_ev10` + shadow veto `BuenosAires,Jeddah`。这不是 live 规则。",
            "",
            table(
                current_summary,
                ["period", "selected_trades", "settled_trades", "open_trades", "active_dates", "cities", "win_rate", "roi", "profit_usd", "avg_trades_per_day"],
            ),
            "",
            "## Current Daily Snapshot",
            "",
            table(
                daily_focus,
                ["target_date", "period", "trades", "wins", "win_rate", "roi", "profit_usd", "cities"],
                limit=40,
            ),
            "",
            "## Current City Distribution",
            "",
            table(
                current_city,
                ["city", "trades", "active_dates", "win_rate", "roi", "profit_usd", "avg_no_ask", "avg_required_gap_f", "avg_pred_remaining_heat_f", "avg_actual_remaining_heat_f"],
                limit=40,
            ),
            "",
            "## Current Family Distribution",
            "",
            table(
                current_family,
                ["city_family", "trades", "cities", "active_dates", "win_rate", "roi", "profit_usd"],
            ),
            "",
            "## Rule City Membership",
            "",
            table(
                members,
                ["rule", "city", "city_family", "trades", "win_rate", "roi", "avg_actual_margin_f", "avg_pred_error_f", "avg_curve_error_f", "avg_relative_humidity_pct"],
                limit=40,
            ),
            "",
            "## Train City Diagnostics",
            "",
            table(
                city,
                ["city", "city_family", "trades", "win_rate", "roi", "avg_actual_margin_f", "avg_pred_error_f", "avg_curve_error_f", "avg_relative_humidity_pct"],
                limit=30,
            ),
            "",
            "## 机制判断",
            "",
            "1. `BuenosAires,Jeddah` 是目前最像 candidate veto 的组合，但共同机制不够干净：一个高湿南半球/海洋，一个干热低纬。",
            "2. 更像机制的规则在 forward 删除了赢家，说明单靠城市历史失败和平均 margin 还不够。",
            "3. 下一步不是直接删城，而是把 city/family 作为分层校准项：对这些城市要求更高的 `p_cross` 或更大的 forecast surplus，并继续 shadow 验证。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Train city summary: `{OUT_CITY_TRAIN.relative_to(ROOT)}`",
            f"- Rule summary: `{OUT_RULES.relative_to(ROOT)}`",
            f"- Rule city membership: `{OUT_RULE_CITIES.relative_to(ROOT)}`",
            f"- Current strategy summary: `{OUT_CURRENT_SUMMARY.relative_to(ROOT)}`",
            f"- Current daily summary: `{OUT_CURRENT_DAILY.relative_to(ROOT)}`",
            f"- Current city distribution: `{OUT_CURRENT_CITY.relative_to(ROOT)}`",
            f"- Current family distribution: `{OUT_CURRENT_FAMILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = load_focus()
    frames = split_frames(frame)
    city = train_city_summary(frames["train"])

    rules: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
        "train_zero_win_min4": lambda c: c["trades"].ge(4) & c["win_rate"].eq(0),
        "mechanism_low15_min6_neg_margin_overpred": lambda c: c["trades"].ge(6)
        & c["win_rate"].le(0.15)
        & c["avg_actual_margin_f"].lt(0)
        & c["avg_pred_error_f"].gt(0.20),
        "mechanism_low20_min8_neg_margin": lambda c: c["trades"].ge(8)
        & c["win_rate"].le(0.20)
        & c["avg_actual_margin_f"].lt(0),
        "humid_low_latitude_family": lambda c: c["city_family"].eq("humid_low_latitude"),
    }

    rule_rows = []
    member_rows = []
    for rule, fn in rules.items():
        selected = city[fn(city)].copy()
        cities = selected["city"].astype(str).tolist()
        rule_rows.extend(summarize_eval(rule, cities, frames))
        if selected.empty:
            member_rows.append({"rule": rule, "city": "", "city_family": "", "trades": 0})
        else:
            for _, row in selected.iterrows():
                item = row.to_dict()
                item["rule"] = rule
                member_rows.append(item)

    rules_df = pd.DataFrame(rule_rows)
    members_df = pd.DataFrame(member_rows)
    zero_veto_cities = members_df[members_df["rule"].eq("train_zero_win_min4")]["city"].dropna().astype(str).tolist()
    current = current_shadow_strategy_outputs(frame, zero_veto_cities)
    city.to_csv(OUT_CITY_TRAIN, index=False)
    rules_df.to_csv(OUT_RULES, index=False)
    members_df.to_csv(OUT_RULE_CITIES, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_city_mechanism_veto_v1",
        "source_selected_rows": str(SOURCE_SELECTED.relative_to(ROOT)),
        "focus": {
            "model": FOCUS_MODEL,
            "variant": FOCUS_VARIANT,
            "train_rows": int(len(frames["train"])),
            "holdout_rows": int(len(frames["holdout"])),
            "forward_settled_rows": int(len(frames["forward_settled"])),
        },
        "rule_summary": finite_or_none(rules_df.to_dict(orient="records")),
        "rule_city_membership": finite_or_none(members_df.to_dict(orient="records")),
        "current_shadow_strategy": {
            "veto_cities": zero_veto_cities,
            "summary": finite_or_none(current["summary"].to_dict(orient="records")),
        },
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "train_city_summary_csv": str(OUT_CITY_TRAIN.relative_to(ROOT)),
            "rule_summary_csv": str(OUT_RULES.relative_to(ROOT)),
            "rule_city_membership_csv": str(OUT_RULE_CITIES.relative_to(ROOT)),
            "current_shadow_strategy_summary_csv": str(OUT_CURRENT_SUMMARY.relative_to(ROOT)),
            "current_shadow_daily_summary_csv": str(OUT_CURRENT_DAILY.relative_to(ROOT)),
            "current_shadow_city_distribution_csv": str(OUT_CURRENT_CITY.relative_to(ROOT)),
            "current_shadow_family_distribution_csv": str(OUT_CURRENT_FAMILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "candidate_city_veto_shadow_only",
            "live_ready": False,
            "reason": "Train-only city veto has one small forward-improving candidate but lacks a clean shared mechanism and sample support.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, city, rules_df, members_df, current), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
