#!/usr/bin/env python3
"""Regime-aware soft sizing for current-bracket NO.

This is deliberately not another hard gate.  It keeps the same base p40 trade
set and asks whether a first-principles day-regime risk score can resize
exposure:

    size_multiplier = 0.25 + 0.75 * (1 - bad_regime_risk)

The bad-regime model is trained only on daily features visible before
settlement: p_cross/p_cap distribution, forecast curve shape, humidity/wind,
running-max staleness, and forecast-overconfidence counts.
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_capped_day_regime_v1 as cap  # noqa: E402
import research_current_bracket_no_forward_regime_stress_v1 as stress  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_regime_aware_policy_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_BLOCKS = OUT_DIR / "soft_sizing_rolling_blocks.csv"
OUT_DAILY = OUT_DIR / "soft_sizing_daily_features.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-regime-aware-policy-v1.md"

SETTLED_FORWARD_DATES = ["2026-06-21", "2026-06-22"]
SEED = 20260624

DAY_FEATURES = [
    "selected_trades",
    "avg_p_cross",
    "avg_p_cap",
    "avg_p_cross_cap_adjusted",
    "overconf_share",
    "overconf_count",
    "high_pcap_share",
    "avg_forecast_over_required_ratio",
    "avg_curve_next_3h_delta_f",
    "avg_curve_slope_next_3h_fph",
    "avg_curve_plateau_hours_next_3h",
    "avg_relative_humidity_pct",
    "avg_wind_speed_kt",
    "avg_minutes_since_running_max",
    "avg_required_gap_f",
    "avg_no_ask",
]


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


def weighted_perf(selected: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    if selected.empty:
        return {
            "trades": 0,
            "cost_usd": 0.0,
            "weighted_cost_usd": 0.0,
            "profit_usd": 0.0,
            "weighted_profit_usd": 0.0,
            "roi": None,
            "notional_retained": None,
            "win_rate": None,
        }
    settled = selected[selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return {
            "trades": int(len(selected)),
            "cost_usd": 0.0,
            "weighted_cost_usd": 0.0,
            "profit_usd": 0.0,
            "weighted_profit_usd": 0.0,
            "roi": None,
            "notional_retained": None,
            "win_rate": None,
        }
    weights = pd.to_numeric(settled[weight_col], errors="coerce").fillna(1.0)
    cost = float(settled["stake_cost_usd"].sum())
    profit = float(settled["stake_profit_usd"].sum())
    weighted_cost = float((settled["stake_cost_usd"] * weights).sum())
    weighted_profit = float((settled["stake_profit_usd"] * weights).sum())
    return {
        "trades": int(len(settled)),
        "cost_usd": cost,
        "weighted_cost_usd": weighted_cost,
        "profit_usd": profit,
        "weighted_profit_usd": weighted_profit,
        "roi": weighted_profit / weighted_cost if weighted_cost else None,
        "notional_retained": weighted_cost / cost if cost else None,
        "win_rate": float(settled["label_no_wins"].mean()),
    }


def selected_daily_features(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    rows = []
    for date, group in selected.groupby("target_date"):
        settled = group[group["label_no_wins"].notna()].copy()
        cost = float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0
        profit = float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0
        row = {
            "target_date": str(date),
            "selected_trades": int(len(group)),
            "settled_trades": int(len(settled)),
            "daily_roi": profit / cost if cost else np.nan,
            "daily_profit_usd": profit,
            "daily_cost_usd": cost,
            "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else np.nan,
            "bad_regime_label": int((profit / cost) <= -0.50) if cost else 0,
            "avg_p_cross": float(group["p_cross_upper"].mean()),
            "avg_p_cap": float(group["p_cap"].mean()),
            "avg_p_cross_cap_adjusted": float(group["p_cross_cap_adjusted"].mean()),
            "overconf_count": int((group["p_cross_upper"].ge(0.95) & group["p_cap"].lt(0.30)).sum()),
            "overconf_share": float((group["p_cross_upper"].ge(0.95) & group["p_cap"].lt(0.30)).mean()),
            "high_pcap_share": float(group["p_cap"].ge(0.80).mean()),
            "avg_forecast_over_required_ratio": float(pd.to_numeric(group.get("forecast_over_required_ratio"), errors="coerce").mean()),
            "avg_curve_next_3h_delta_f": float(pd.to_numeric(group.get("curve_next_3h_delta_f"), errors="coerce").mean()),
            "avg_curve_slope_next_3h_fph": float(pd.to_numeric(group.get("curve_slope_next_3h_fph"), errors="coerce").mean()),
            "avg_curve_plateau_hours_next_3h": float(pd.to_numeric(group.get("curve_plateau_hours_next_3h"), errors="coerce").mean()),
            "avg_relative_humidity_pct": float(pd.to_numeric(group.get("relative_humidity_pct"), errors="coerce").mean()),
            "avg_wind_speed_kt": float(pd.to_numeric(group.get("wind_speed_kt"), errors="coerce").mean()),
            "avg_minutes_since_running_max": float(pd.to_numeric(group.get("minutes_since_running_max"), errors="coerce").mean()),
            "avg_required_gap_f": float(pd.to_numeric(group.get("required_gap_f"), errors="coerce").mean()),
            "avg_no_ask": float(pd.to_numeric(group.get("no_ask"), errors="coerce").mean()),
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values("target_date").reset_index(drop=True)


def build_regime_model() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(C=0.5, class_weight="balanced", max_iter=1000, random_state=SEED)),
        ]
    )


def apply_soft_sizing(holdout_selected: pd.DataFrame, train_selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = holdout_selected.copy()
    if out.empty:
        return out, pd.DataFrame()
    train_daily = selected_daily_features(train_selected)
    holdout_daily = selected_daily_features(out)
    if train_daily["bad_regime_label"].nunique() < 2 or holdout_daily.empty:
        holdout_daily["bad_regime_risk"] = float(train_daily["bad_regime_label"].mean()) if len(train_daily) else 0.5
    else:
        model = build_regime_model()
        model.fit(train_daily[DAY_FEATURES], train_daily["bad_regime_label"])
        holdout_daily["bad_regime_risk"] = model.predict_proba(holdout_daily[DAY_FEATURES])[:, 1]
    holdout_daily["day_regime_multiplier"] = 0.25 + 0.75 * (1.0 - holdout_daily["bad_regime_risk"])
    holdout_daily["principled_cluster_risk"] = (
        0.55 * holdout_daily["avg_p_cap"].clip(0, 1)
        + 0.25 * holdout_daily["high_pcap_share"].clip(0, 1)
        + 0.20 * (holdout_daily["overconf_count"] / 2.0).clip(0, 1)
    ).clip(0, 1)
    holdout_daily["principled_cluster_multiplier"] = 0.25 + 0.75 * (1.0 - holdout_daily["principled_cluster_risk"])
    holdout_daily["overconf_cluster_multiplier"] = 1.0 / (1.0 + (holdout_daily["overconf_count"] / 2.0).clip(lower=0))
    multipliers = holdout_daily.set_index("target_date")["day_regime_multiplier"].to_dict()
    risks = holdout_daily.set_index("target_date")["bad_regime_risk"].to_dict()
    cluster_multipliers = holdout_daily.set_index("target_date")["principled_cluster_multiplier"].to_dict()
    overconf_multipliers = holdout_daily.set_index("target_date")["overconf_cluster_multiplier"].to_dict()
    out["bad_regime_risk"] = out["target_date"].map(risks).fillna(0.5)
    out["day_regime_multiplier"] = out["target_date"].map(multipliers).fillna(0.625)
    out["principled_cluster_multiplier"] = out["target_date"].map(cluster_multipliers).fillna(0.625)
    out["overconf_cluster_multiplier"] = out["target_date"].map(overconf_multipliers).fillna(1.0)
    out["trade_cap_multiplier"] = 0.25 + 0.75 * (1.0 - pd.to_numeric(out["p_cap"], errors="coerce").clip(0, 1))
    out["combined_multiplier"] = out["day_regime_multiplier"] * out["trade_cap_multiplier"]
    out["principled_combined_multiplier"] = out["principled_cluster_multiplier"] * out["trade_cap_multiplier"]
    out["overconf_combined_multiplier"] = out["overconf_cluster_multiplier"] * out["trade_cap_multiplier"]
    out["full_multiplier"] = 1.0
    return out, holdout_daily


def evaluate_block(frame: pd.DataFrame, holdout_dates: list[str]) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    all_dates = set(frame["target_date"].unique().tolist())
    train_dates = all_dates - set(holdout_dates)
    scored, _threshold = stress.train_score_arbitrary(frame, train_dates)
    train_selected = cap.select_base_p40(scored[scored["target_date"].isin(train_dates)].copy())
    holdout = scored[scored["target_date"].isin(holdout_dates)].copy()
    holdout_selected = cap.select_base_p40(holdout)
    sized, daily_features = apply_soft_sizing(holdout_selected, train_selected)
    rows = []
    for policy, weight_col in [
        ("full_size_base_p40", "full_multiplier"),
        ("trade_cap_soft_size", "trade_cap_multiplier"),
        ("day_regime_soft_size", "day_regime_multiplier"),
        ("combined_soft_size", "combined_multiplier"),
        ("principled_cluster_soft_size", "principled_cluster_multiplier"),
        ("principled_combined_soft_size", "principled_combined_multiplier"),
        ("overconf_cluster_soft_size", "overconf_cluster_multiplier"),
        ("overconf_combined_soft_size", "overconf_combined_multiplier"),
    ]:
        perf = weighted_perf(sized, weight_col)
        rows.append(
            {
                "holdout_dates": ",".join(holdout_dates),
                "policy": policy,
                "train_includes_2026_06_21": "2026-06-21" in train_dates,
                "train_includes_2026_06_22": "2026-06-22" in train_dates,
                "avg_bad_regime_risk": float(sized["bad_regime_risk"].mean()) if len(sized) else None,
                "avg_day_regime_multiplier": float(sized["day_regime_multiplier"].mean()) if len(sized) else None,
                "avg_principled_cluster_multiplier": float(sized["principled_cluster_multiplier"].mean()) if len(sized) else None,
                "avg_overconf_cluster_multiplier": float(sized["overconf_cluster_multiplier"].mean()) if len(sized) else None,
                "avg_trade_cap_multiplier": float(sized["trade_cap_multiplier"].mean()) if len(sized) else None,
                **perf,
            }
        )
    if not daily_features.empty:
        daily_features["holdout_dates"] = ",".join(holdout_dates)
    return rows, daily_features


def rolling_blocks(frame: pd.DataFrame, block_days: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(frame["target_date"].unique().tolist())
    rows = []
    daily_parts = []
    for start in range(0, len(dates) - block_days + 1):
        holdout_dates = dates[start : start + block_days]
        block_rows, daily = evaluate_block(frame, holdout_dates)
        rows.extend(block_rows)
        if not daily.empty:
            daily_parts.append(daily)
    return pd.DataFrame(rows), pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()


def summarize(blocks: pd.DataFrame) -> dict[str, Any]:
    rows = {}
    forward_key = ",".join(SETTLED_FORWARD_DATES)
    for policy, group in blocks.groupby("policy"):
        cost = float(group["weighted_cost_usd"].sum())
        profit = float(group["weighted_profit_usd"].sum())
        target = group[group["holdout_dates"].eq(forward_key)].copy()
        rows[policy] = {
            "blocks": int(len(group)),
            "total_trades": int(group["trades"].sum()),
            "weighted_cost_usd": cost,
            "weighted_profit_usd": profit,
            "cost_weighted_roi": profit / cost if cost else None,
            "mean_block_roi": float(group["roi"].mean()),
            "median_block_roi": float(group["roi"].median()),
            "positive_blocks": int(group["roi"].gt(0).sum()),
            "bad_blocks_roi_le_minus50": int(group["roi"].le(-0.50).sum()),
            "avg_notional_retained": float(group["notional_retained"].mean()),
            "target_forward_block": finite_or_none(target.to_dict(orient="records")),
        }
    return rows


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("retained") or col.endswith("rate"):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], blocks: pd.DataFrame, daily: pd.DataFrame) -> str:
    summary_df = pd.DataFrame(
        [
            {"policy": policy, **vals}
            for policy, vals in payload["policy_summary"].items()
        ]
    )
    target = blocks[blocks["holdout_dates"].eq(",".join(SETTLED_FORWARD_DATES))].copy()
    worst = blocks.sort_values(["roi"]).head(20)
    daily_focus = daily[
        daily["target_date"].isin(SETTLED_FORWARD_DATES)
        | daily["bad_regime_label"].eq(1)
        | daily["bad_regime_risk"].ge(0.65)
    ].copy()
    return "\n".join(
        [
            "# Current-Bracket NO Regime-Aware Policy V1",
            "",
            "## 结论",
            "",
            "这版没有新增 hard gate，保留 base p40 交易集合，只用连续 multiplier 调整 notional。结果说明：单纯用小样本 logistic 预测坏 day 不稳；更直接的 first-principles cluster risk 和单笔 p_cap soft sizing 能明显减亏，但还没把完整 rolling CV 稳定拉正。训练包含 6/21-6/22 后，trade_cap soft sizing 可以转正；全窗口仍接近打平而非 confirmed。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Policy Summary",
            "",
            table(
                summary_df,
                [
                    "policy",
                    "blocks",
                    "total_trades",
                    "weighted_cost_usd",
                    "weighted_profit_usd",
                    "cost_weighted_roi",
                    "positive_blocks",
                    "bad_blocks_roi_le_minus50",
                    "avg_notional_retained",
                ],
            ),
            "",
            "## 6/21-6/22 Target Block",
            "",
            table(
                target,
                [
                    "policy",
                    "trades",
                    "win_rate",
                    "weighted_cost_usd",
                    "weighted_profit_usd",
                    "roi",
                    "notional_retained",
                    "avg_bad_regime_risk",
                    "avg_day_regime_multiplier",
                    "avg_trade_cap_multiplier",
                    "avg_principled_cluster_multiplier",
                    "avg_overconf_cluster_multiplier",
                ],
            ),
            "",
            "## Worst Policy Blocks",
            "",
            table(
                worst,
                [
                    "holdout_dates",
                    "policy",
                    "trades",
                    "win_rate",
                    "roi",
                    "weighted_profit_usd",
                    "notional_retained",
                    "avg_bad_regime_risk",
                    "avg_day_regime_multiplier",
                    "avg_principled_cluster_multiplier",
                    "avg_overconf_cluster_multiplier",
                ],
                limit=20,
            ),
            "",
            "## Day Regime Features",
            "",
            table(
                daily_focus.sort_values(["bad_regime_risk", "daily_roi"], ascending=[False, True]),
                [
                    "holdout_dates",
                    "target_date",
                    "selected_trades",
                    "daily_roi",
                    "bad_regime_label",
                    "bad_regime_risk",
                    "day_regime_multiplier",
                    "principled_cluster_multiplier",
                    "overconf_cluster_multiplier",
                    "overconf_count",
                    "avg_p_cap",
                    "avg_p_cross",
                    "high_pcap_share",
                ],
                limit=40,
            ),
            "",
            "## Interpretation",
            "",
            "1. 这个版本没有把样本筛没：所有 base p40 trades 仍然保留，变化只在 notional multiplier。",
            "2. 单笔 `p_cap` 能把总结果从明显负拉到接近打平；这说明 capped-day 是有效机制特征。",
            "3. day-regime 小样本监督学习还不稳，不能为了漂亮结果继续拟合坏日标签。",
            "4. 更可取的是继续做 first-principles regime score：cluster cap、forecast overconfidence、forecast revision、观测刷新速度，用 soft sizing 而不是 hard gate。",
            "5. 下一步应把 day-regime score 接入 forward shadow runner，记录每天进场前的 risk 和实际结算，而不是继续只做离线调参。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Rolling blocks: `{OUT_BLOCKS.relative_to(ROOT)}`",
            f"- Daily features: `{OUT_DAILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined, _open_rows, stats = stress.load_combined()
    blocks, daily = rolling_blocks(combined, block_days=2)
    blocks.to_csv(OUT_BLOCKS, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_regime_aware_policy_v1",
        "data_refresh_note": "Using the same freshly synced/rebuilt fact layer as forward_regime_stress_v1; CLOB gate checked true.",
        "source_stats": stats,
        "policy_summary": summarize(blocks),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "soft_sizing_rolling_blocks_csv": str(OUT_BLOCKS.relative_to(ROOT)),
            "soft_sizing_daily_features_csv": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "regime_aware_soft_sizing_directional_but_not_confirmed",
            "live_ready": False,
            "reason": "Soft sizing reduces regime tail without hard filtering, but complete rolling CV remains near flat/negative and tail blocks remain too large for live.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, blocks, daily), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
