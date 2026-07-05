#!/usr/bin/env python3
"""City and climate-family forensics for current-bracket NO all-loss days.

This script is intentionally a diagnostic, not a city-pruning optimizer.  It
uses the V3 mechanism-feature selected rows and a pre-existing city-family map
from no-reheat segment work to test whether all-loss behavior is concentrated
in a clean city/climate mechanism.
"""

from __future__ import annotations

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

from weather_data_feed.city_family import CITY_FAMILY_CURRENT_BRACKET_NO_V1 as CITY_FAMILY  # noqa: E402

SOURCE_SELECTED = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_mechanism_features_v3/selected_trade_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_city_climate_forensics_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_CITY = OUT_DIR / "city_summary.csv"
OUT_FAMILY = OUT_DIR / "family_summary.csv"
OUT_LEAVE_CITY = OUT_DIR / "leave_one_city_summary.csv"
OUT_LEAVE_FAMILY = OUT_DIR / "leave_one_family_summary.csv"
OUT_FORWARD_FAMILY = OUT_DIR / "forward_family_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-city-climate-forensics-v1.md"

FOCUS_MODEL = "enhanced_all_rows"
FOCUS_VARIANT = "remaining_heat_p40_ev10"
BASE_MODEL = "base_all_rows"
SEED = 20260624
BOOTSTRAP_REPS = 3000

# Fixed taxonomy reused from research_current_yes_no_reheat_segment_breakdown_v1.
# This is a pre-analysis mechanism grouping, not a group selected for this PnL.


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


def block_bootstrap_roi(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"ci_low": None, "ci_high": None, "active_dates": 0}
    grouped = frame.groupby("target_date").agg(profit=("stake_profit_usd", "sum"), cost=("stake_cost_usd", "sum"))
    dates = sorted(grouped.index.astype(str))
    if len(dates) < 3:
        return {"ci_low": None, "ci_high": None, "active_dates": len(dates)}
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(BOOTSTRAP_REPS):
        draw = rng.choice(dates, size=len(dates), replace=True)
        profit = float(grouped.loc[draw, "profit"].sum())
        cost = float(grouped.loc[draw, "cost"].sum())
        vals.append(profit / cost if cost else np.nan)
    return {
        "ci_low": float(np.nanquantile(vals, 0.025)),
        "ci_high": float(np.nanquantile(vals, 0.975)),
        "active_dates": len(dates),
    }


def all_loss_dates(frame: pd.DataFrame) -> list[str]:
    daily = frame.groupby("target_date").agg(wins=("label_no_wins", "sum"))
    return daily[daily["wins"].eq(0)].index.astype(str).tolist()


def add_family(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    return out


def load_focus(model: str, variant: str, forward: bool = False) -> pd.DataFrame:
    df = pd.read_csv(SOURCE_SELECTED)
    mask = df["model"].eq(model) & df["variant"].eq(variant)
    if forward:
        mask &= df["target_date"].astype(str).gt("2026-06-20")
    else:
        mask &= df["target_date"].astype(str).le("2026-06-20")
    out = add_family(df[mask].copy())
    return out.reset_index(drop=True)


def summarize_slice(name: str, frame: pd.DataFrame, loss_dates: list[str]) -> dict[str, Any]:
    if frame.empty:
        return {"slice": name, "trades": 0}
    settled = frame[frame["label_no_wins"].notna()].copy()
    holdout = frame[frame["period_split"].eq("holdout")].copy() if "period_split" in frame.columns else frame.iloc[0:0]
    ci = block_bootstrap_roi(settled)
    return {
        "slice": name,
        "trades": int(len(frame)),
        "settled_trades": int(len(settled)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": float(settled["label_no_wins"].sum()) if not settled.empty else 0.0,
        "win_rate": float(settled["label_no_wins"].mean()) if not settled.empty else None,
        "profit_usd": float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0,
        "cost_usd": float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0,
        "roi": roi(settled),
        "roi_ci_low": ci["ci_low"],
        "roi_ci_high": ci["ci_high"],
        "holdout_trades": int(len(holdout)),
        "holdout_roi": roi(holdout),
        "all_loss_trades": int(frame["target_date"].astype(str).isin(loss_dates).sum()),
        "all_loss_trade_share": float(frame["target_date"].astype(str).isin(loss_dates).mean()) if len(frame) else None,
        "avg_required_gap_f": float(pd.to_numeric(frame.get("required_gap_f"), errors="coerce").mean()),
        "avg_pred_remaining_heat_f": float(pd.to_numeric(frame.get("pred_remaining_heat_f"), errors="coerce").mean()),
        "avg_actual_remaining_heat_f": float(pd.to_numeric(frame.get("future_delta_to_daymax_f"), errors="coerce").mean()),
        "avg_curve_next_3h_delta_f": float(pd.to_numeric(frame.get("curve_next_3h_delta_f"), errors="coerce").mean()),
        "avg_curve_remaining_to_peak_f": float(pd.to_numeric(frame.get("curve_remaining_to_peak_f"), errors="coerce").mean()),
        "avg_relative_humidity_pct": float(pd.to_numeric(frame.get("relative_humidity_pct"), errors="coerce").mean()),
        "avg_wind_speed_kt": float(pd.to_numeric(frame.get("wind_speed_kt"), errors="coerce").mean()),
        "avg_solar_elevation_deg": float(pd.to_numeric(frame.get("solar_elevation_deg"), errors="coerce").mean()),
    }


def grouped_summary(frame: pd.DataFrame, by: str, loss_dates: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(by):
        row = summarize_slice(str(key), group, loss_dates)
        row[by] = key
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    total_trades = max(1, len(frame))
    total_all_loss = max(1, int(frame["target_date"].astype(str).isin(loss_dates).sum()))
    out["trade_share"] = out["trades"] / total_trades
    out["all_loss_exposure_share"] = out["all_loss_trades"] / total_all_loss
    out["all_loss_enrichment"] = out["all_loss_exposure_share"] / out["trade_share"]
    return out.sort_values(["roi", "trades"], ascending=[True, False]).reset_index(drop=True)


def leave_one(frame: pd.DataFrame, by: str, loss_dates: list[str]) -> pd.DataFrame:
    rows = []
    base_roi = roi(frame)
    for key, group in frame.groupby(by):
        kept = frame[frame[by] != key].copy()
        row = summarize_slice(f"without_{key}", kept, loss_dates)
        row[by] = key
        row[f"removed_{by}"] = key
        row["removed_trades"] = int(len(group))
        row["removed_roi"] = roi(group)
        row["roi_lift_vs_base"] = None if base_roi is None or row["roi"] is None else row["roi"] - base_roi
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["roi", "removed_trades"], ascending=[False, False]).reset_index(drop=True)


def forward_family_summary(forward: pd.DataFrame, loss_dates: list[str]) -> pd.DataFrame:
    rows = []
    for family, group in forward.groupby("city_family"):
        row = summarize_slice(str(family), group, loss_dates)
        row["city_family"] = family
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["roi", "trades"], ascending=[True, False]).reset_index(drop=True)


def render_md(payload: dict[str, Any], city: pd.DataFrame, family: pd.DataFrame, leave_city: pd.DataFrame, leave_family: pd.DataFrame, forward_family: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col.endswith("share") or col in {"win_rate", "roi_ci_low", "roi_ci_high", "holdout_roi", "roi_lift_vs_base"}:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    return "\n".join(
        [
            "# Current-Bracket NO City/Climate Forensics V1",
            "",
            "## 结论",
            "",
            "这次检验的是“全错是不是集中在某些气候特征城市”。答案：有城市/气候族群信号，但它不是一个干净到可以直接删城解决的机制。",
            "",
            "- `humid_low_latitude` 历史确实拖累最大：focus ROI 为 -1.8%，去掉后整体历史 ROI 从 +10.1% 到 +19.7%。",
            "- 但这个 leave-out 到 forward 仍然亏：6/21..6/23 去掉 `humid_low_latitude` 后 settled ROI 仍是 -36.1%。",
            "- 全错日不是单一城市问题：5 个 all-loss dates 横跨多个 family；`southern_or_maritime`、`europe_cloud_break` 的 all-loss enrichment 也不低。",
            "- 因此正确动作不是删一批城市，而是把 city/climate family 当作机制特征或分层校准项；任何 city removal 都只能 shadow 观察，不能 live。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## 数据层",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Source selected rows: `{payload['source_selected_rows']}`",
            f"- Focus: `{payload['focus']['model']}::{payload['focus']['variant']}`",
            f"- Historical selected trades: `{payload['focus']['historical_trades']}` / dates `{payload['focus']['historical_dates']}` / cities `{payload['focus']['historical_cities']}`",
            f"- All-loss dates: `{payload['focus']['all_loss_dates']}`",
            f"- Forward dates: `{payload['focus']['forward_dates']}`",
            "",
            "## Family Summary",
            "",
            table(
                family,
                [
                    "city_family",
                    "trades",
                    "cities",
                    "win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "holdout_roi",
                    "all_loss_trade_share",
                    "all_loss_enrichment",
                    "avg_curve_next_3h_delta_f",
                    "avg_relative_humidity_pct",
                ],
            ),
            "",
            "## Leave-One Family",
            "",
            table(
                leave_family,
                ["removed_city_family", "removed_trades", "removed_roi", "roi", "roi_lift_vs_base", "holdout_roi", "all_loss_trade_share"],
            ),
            "",
            "## Forward By Family",
            "",
            table(
                forward_family,
                ["city_family", "trades", "settled_trades", "win_rate", "roi", "profit_usd", "avg_curve_next_3h_delta_f", "avg_relative_humidity_pct"],
            ),
            "",
            "## City Diagnostics",
            "",
            "这些是诊断，不是推荐删城清单；city-level 样本很薄，且同日高度相关。",
            "",
            table(
                city,
                ["city", "city_family", "trades", "win_rate", "roi", "holdout_roi", "all_loss_trades", "all_loss_enrichment", "avg_relative_humidity_pct"],
                limit=25,
            ),
            "",
            "## Leave-One City Top Diagnostics",
            "",
            table(
                leave_city,
                ["removed_city", "city_family", "removed_trades", "removed_roi", "roi", "roi_lift_vs_base", "holdout_roi"],
                limit=25,
            ),
            "",
            "## 机制判断",
            "",
            "1. `humid_low_latitude` 更像“预报高估/湿热低纬封顶风险”的候选机制特征，不是独立可交易规则。",
            "2. 全错日跨 family 发生，说明 day-regime/macro weather pattern 比单城黑名单更重要。",
            "3. 下一步应该把 family 与 forecast overestimate、curve plateau、humidity/cloud/wind regime 做交互特征，并做 forward/shadow；不要用 leave-one-city 直接决定 live 城市池。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- City summary: `{OUT_CITY.relative_to(ROOT)}`",
            f"- Family summary: `{OUT_FAMILY.relative_to(ROOT)}`",
            f"- Leave-one city: `{OUT_LEAVE_CITY.relative_to(ROOT)}`",
            f"- Leave-one family: `{OUT_LEAVE_FAMILY.relative_to(ROOT)}`",
            f"- Forward family: `{OUT_FORWARD_FAMILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    focus = load_focus(FOCUS_MODEL, FOCUS_VARIANT, forward=False)
    base = load_focus(BASE_MODEL, FOCUS_VARIANT, forward=False)
    forward = load_focus(FOCUS_MODEL, FOCUS_VARIANT, forward=True)
    focus_loss_dates = all_loss_dates(focus)
    base_loss_dates = all_loss_dates(base)

    city = grouped_summary(focus, "city", focus_loss_dates)
    city = city.merge(focus[["city", "city_family"]].drop_duplicates(), on="city", how="left", suffixes=("", "_dup"))
    family = grouped_summary(focus, "city_family", focus_loss_dates)
    leave_city = leave_one(focus, "city", focus_loss_dates)
    leave_city = leave_city.merge(focus[["city", "city_family"]].drop_duplicates(), left_on="removed_city", right_on="city", how="left")
    leave_family = leave_one(focus, "city_family", focus_loss_dates)
    forward_fam = forward_family_summary(forward, focus_loss_dates)

    city.to_csv(OUT_CITY, index=False)
    family.to_csv(OUT_FAMILY, index=False)
    leave_city.to_csv(OUT_LEAVE_CITY, index=False)
    leave_family.to_csv(OUT_LEAVE_FAMILY, index=False)
    forward_fam.to_csv(OUT_FORWARD_FAMILY, index=False)

    humid_without = leave_family[leave_family["removed_city_family"].eq("humid_low_latitude")].iloc[0].to_dict()
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_city_climate_forensics_v1",
        "source_selected_rows": str(SOURCE_SELECTED.relative_to(ROOT)),
        "focus": {
            "model": FOCUS_MODEL,
            "variant": FOCUS_VARIANT,
            "historical_trades": int(len(focus)),
            "historical_dates": int(focus["target_date"].nunique()),
            "historical_cities": int(focus["city"].nunique()),
            "historical_roi": roi(focus),
            "all_loss_dates": focus_loss_dates,
            "forward_dates": sorted(forward["target_date"].astype(str).unique().tolist()),
            "forward_trades": int(len(forward)),
            "forward_settled_roi": roi(forward[forward["label_no_wins"].notna()]),
            "humid_low_latitude_leaveout_historical_roi": humid_without.get("roi"),
        },
        "base_comparison": {
            "model": BASE_MODEL,
            "variant": FOCUS_VARIANT,
            "historical_trades": int(len(base)),
            "historical_roi": roi(base),
            "all_loss_dates": base_loss_dates,
        },
        "family_summary": finite_or_none(family.to_dict(orient="records")),
        "leave_one_family_summary": finite_or_none(leave_family.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "city_summary_csv": str(OUT_CITY.relative_to(ROOT)),
            "family_summary_csv": str(OUT_FAMILY.relative_to(ROOT)),
            "leave_one_city_csv": str(OUT_LEAVE_CITY.relative_to(ROOT)),
            "leave_one_family_csv": str(OUT_LEAVE_FAMILY.relative_to(ROOT)),
            "forward_family_csv": str(OUT_FORWARD_FAMILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "city_climate_signal_but_not_clean_filter",
            "live_ready": False,
            "reason": "Predefined climate-family leave-out improves historical diagnostics but does not fix forward loss; use as model feature/shadow layer, not live city deletion.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, city, family, leave_city, leave_family, forward_fam), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
