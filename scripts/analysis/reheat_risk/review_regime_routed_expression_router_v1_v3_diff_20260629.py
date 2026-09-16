#!/usr/bin/env python3
"""Review trade-level differences between original v1 and expression-router v3."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
V2_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv"
V3_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v1_v3_diff_20260629"
OUT_JSON = OUT_DIR / "summary.json"
OUT_AFFECTED = OUT_DIR / "affected_v1_rows.csv"
OUT_REMOVED = OUT_DIR / "removed_from_v3.csv"
OUT_FLIPPED = OUT_DIR / "flipped_to_current_high_yes.csv"
OUT_STATE = OUT_DIR / "affected_state_summary.csv"
OUT_DAILY = OUT_DIR / "affected_daily_summary.csv"
OUT_CITY = OUT_DIR / "affected_city_summary.csv"
OUT_RECENT = OUT_DIR / "recent_daily_comparison.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-expression-router-v1-v3-diff-review.md"

STAKE_USD = 5.0
FORWARD_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
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
    return f"${val:+.2f}"


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def trade_key_cols(df: pd.DataFrame) -> list[str]:
    cols = ["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc", "current_bracket"]
    return [c for c in cols if c in df.columns]


def add_original_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["v1_side"] = "BUY_NO"
    out["v1_expression"] = out["expression"].astype(str)
    out["v1_ask"] = pd.to_numeric(out["ask"], errors="coerce")
    out["v1_payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out["v1_cost_usd"] = STAKE_USD
    out["v1_pnl_usd"] = pd.to_numeric(out["stake_profit_usd"], errors="coerce")
    out["weight"] = pd.to_numeric(out.get("soft_balanced"), errors="coerce").fillna(1.0)
    out["v1_weighted_cost_usd"] = out["v1_cost_usd"] * out["weight"]
    out["v1_weighted_pnl_usd"] = out["v1_pnl_usd"] * out["weight"]
    return out


def add_router_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["v3_side"] = out["router_side"].astype(str)
    out["v3_expression"] = out["router_expression"].astype(str)
    out["v3_route"] = out["router_route"].astype(str)
    out["v3_ask"] = pd.to_numeric(out["router_ask"], errors="coerce")
    out["v3_payoff"] = pd.to_numeric(out["router_payoff"], errors="coerce")
    out["v3_cost_usd"] = pd.to_numeric(out["router_cost_usd"], errors="coerce")
    out["v3_pnl_usd"] = pd.to_numeric(out["router_pnl_usd"], errors="coerce")
    out["v3_weighted_cost_usd"] = pd.to_numeric(out["router_weighted_cost_usd"], errors="coerce")
    out["v3_weighted_pnl_usd"] = pd.to_numeric(out["router_weighted_pnl_usd"], errors="coerce")
    return out


def summarize(frame: pd.DataFrame, name: str, *, prefix: str = "v1") -> dict[str, Any]:
    cost_col = f"{prefix}_cost_usd"
    pnl_col = f"{prefix}_pnl_usd"
    ask_col = f"{prefix}_ask"
    payoff_col = f"{prefix}_payoff"
    wcost_col = f"{prefix}_weighted_cost_usd"
    wpnl_col = f"{prefix}_weighted_pnl_usd"
    if frame.empty:
        return {
            "slice": name,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "avg_ask": None,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "weighted_cost_usd": 0.0,
            "weighted_pnl_usd": 0.0,
            "weighted_roi": None,
        }
    cost = float(pd.to_numeric(frame[cost_col], errors="coerce").sum())
    pnl = float(pd.to_numeric(frame[pnl_col], errors="coerce").sum())
    wcost = float(pd.to_numeric(frame[wcost_col], errors="coerce").sum()) if wcost_col in frame else cost
    wpnl = float(pd.to_numeric(frame[wpnl_col], errors="coerce").sum()) if wpnl_col in frame else pnl
    return {
        "slice": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(pd.to_numeric(frame[payoff_col], errors="coerce").fillna(0).sum()),
        "win_rate": float(pd.to_numeric(frame[payoff_col], errors="coerce").mean()),
        "avg_ask": float(pd.to_numeric(frame[ask_col], errors="coerce").mean()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": wcost,
        "weighted_pnl_usd": wpnl,
        "weighted_roi": wpnl / wcost if wcost else None,
    }


def grouped_summary(frame: pd.DataFrame, by: list[str], name: str, *, prefix: str = "v1") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(by, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = summarize(group, name, prefix=prefix)
        for col, val in zip(by, keys):
            row[col] = val
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["pnl_usd", "rows"], ascending=[True, False]).reset_index(drop=True)


def daily_for(frame: pd.DataFrame, strategy: str, *, prefix: str) -> pd.DataFrame:
    out = grouped_summary(frame, ["target_date"], strategy, prefix=prefix)
    if out.empty:
        return out
    out["strategy"] = strategy
    return out.sort_values("target_date")


def md_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, [c for c in cols if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if col in {"win_rate", "roi", "weighted_roi", "delta_roi"}:
            view[col] = view[col].map(pct)
        elif col.endswith("_usd") or col in {"avg_ask", "v1_ask", "v3_ask"}:
            if col in {"avg_ask", "v1_ask", "v3_ask"}:
                view[col] = view[col].map(lambda x: "NA" if pd.isna(x) else f"{float(x):.3f}")
            else:
                view[col] = view[col].map(money)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in view.astype(str).to_numpy()]
    return "\n".join([header, sep, *rows])


def build_diff() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    source = pd.read_csv(V2_DETAILS, low_memory=False)
    router = pd.read_csv(V3_DETAILS, low_memory=False)
    v1 = add_original_metrics(source[boolish(source["strategy_original_mixed_v1"])].copy())
    v3 = add_router_metrics(router.copy())

    key_cols = trade_key_cols(v1)
    v1_key = v1[key_cols].astype(str).agg("|".join, axis=1)
    v3_key = v3[key_cols].astype(str).agg("|".join, axis=1)
    v1 = v1.assign(_key=v1_key)
    v3 = v3.assign(_key=v3_key)

    merged = v1.merge(
        v3[
            [
                "_key",
                "v3_route",
                "v3_side",
                "v3_expression",
                "v3_ask",
                "v3_payoff",
                "v3_cost_usd",
                "v3_pnl_usd",
                "v3_weighted_cost_usd",
                "v3_weighted_pnl_usd",
            ]
        ],
        on="_key",
        how="left",
    )
    merged["diff_group"] = np.where(
        merged["v3_side"].eq("BUY_YES"),
        "flipped_to_current_high_yes",
        np.where(merged["v3_side"].isna(), "removed_from_v3", "unchanged_in_v3"),
    )
    merged["pnl_delta_v3_minus_v1"] = merged["v3_pnl_usd"].fillna(0.0) - merged["v1_pnl_usd"].fillna(0.0)
    merged["weighted_pnl_delta_v3_minus_v1"] = (
        merged["v3_weighted_pnl_usd"].fillna(0.0) - merged["v1_weighted_pnl_usd"].fillna(0.0)
    )
    affected = merged[merged["diff_group"].ne("unchanged_in_v3")].copy()
    removed = affected[affected["diff_group"].eq("removed_from_v3")].copy()
    flipped = affected[affected["diff_group"].eq("flipped_to_current_high_yes")].copy()

    summary_rows = [
        summarize(v1, "original_mixed_v1", prefix="v1"),
        summarize(v3, "router_v3", prefix="v3"),
        summarize(affected, "v1_rows_changed_by_v3_old_v1_result", prefix="v1"),
        summarize(removed, "removed_from_v3_old_v1_result", prefix="v1"),
        summarize(flipped, "flipped_rows_old_v1_no_result", prefix="v1"),
        summarize(flipped, "flipped_rows_new_v3_yes_result", prefix="v3"),
    ]
    summary_rows.append(
        {
            "slice": "net_router_v3_minus_v1",
            "rows": int(len(v3) - len(v1)),
            "dates": None,
            "cities": None,
            "wins": None,
            "win_rate": None,
            "avg_ask": None,
            "cost_usd": float(v3["v3_cost_usd"].sum() - v1["v1_cost_usd"].sum()),
            "pnl_usd": float(v3["v3_pnl_usd"].sum() - v1["v1_pnl_usd"].sum()),
            "roi": None,
            "weighted_cost_usd": float(v3["v3_weighted_cost_usd"].sum() - v1["v1_weighted_cost_usd"].sum()),
            "weighted_pnl_usd": float(v3["v3_weighted_pnl_usd"].sum() - v1["v1_weighted_pnl_usd"].sum()),
            "weighted_roi": None,
        }
    )
    return v1, v3, affected, removed, flipped, summary_rows


def write_report(payload: dict[str, Any], state: pd.DataFrame, daily: pd.DataFrame, city: pd.DataFrame, recent: pd.DataFrame, affected: pd.DataFrame) -> None:
    rows = {row["slice"]: row for row in payload["summary_rows"]}
    affected_rows = int(payload["affected_v1_rows"])
    removed_rows = int(payload["removed_from_v3_rows"])
    flipped_rows = int(payload["flipped_to_current_high_yes_rows"])
    net_delta = int(payload["net_row_delta_v3_minus_v1"])
    lines = [
        "# Regime-Routed Expression Router V1 vs V3 Diff Review",
        "",
        "## 结论",
        "",
        f"在当前可结算分母 `{payload['source_date_min']}`..`{payload['source_date_max']}` 上，v1 是 {rows['original_mixed_v1']['rows']} 笔，v3 是 {rows['router_v3']['rows']} 笔，净少 {abs(net_delta)} 笔。用户说的“差 30 笔”按当前文件精确拆出来是：{affected_rows} 笔 v1 stale-current-NO 被影响，其中 {removed_rows} 笔被移除，{flipped_rows} 笔被改成 current-high YES。",
        "",
        f"这 {affected_rows} 笔如果按旧 v1 的 NO 买法，历史 PnL 是 {money(rows['v1_rows_changed_by_v3_old_v1_result']['pnl_usd'])}，ROI {pct(rows['v1_rows_changed_by_v3_old_v1_result']['roi'])}。所以 v3 不是简单“去掉亏损单”：它移除了/改写了一组语义不干净的 stale/pullback 单，其中有些历史是赚钱的。",
        "",
        f"最关键的机制修复是 {flipped_rows} 笔 `pullback_uncertain`：旧 v1 买 NO 的 PnL {money(rows['flipped_rows_old_v1_no_result']['pnl_usd'])}、ROI {pct(rows['flipped_rows_old_v1_no_result']['roi'])}；v3 改 current-high YES 的 PnL {money(rows['flipped_rows_new_v3_yes_result']['pnl_usd'])}、ROI {pct(rows['flipped_rows_new_v3_yes_result']['roi'])}。这一块方向合理，但样本太小且 YES size 缺，仍只能 shadow。",
        "",
        "## Summary",
        "",
        md_table(
            pd.DataFrame(payload["summary_rows"]),
            ["slice", "rows", "dates", "cities", "wins", "win_rate", "avg_ask", "cost_usd", "pnl_usd", "roi", "weighted_cost_usd", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Affected State Breakdown",
        "",
        md_table(
            state,
            ["diff_group", "running_max_state", "intraday_state", "rows", "dates", "cities", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Affected Daily Breakdown",
        "",
        md_table(
            daily,
            ["diff_group", "target_date", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
            limit=60,
        ),
        "",
        "## Affected City Breakdown",
        "",
        md_table(
            city,
            ["diff_group", "city", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
            limit=80,
        ),
        "",
        "## Recent Daily V1 vs V3",
        "",
        md_table(
            recent,
            ["strategy", "target_date", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Changed Rows",
        "",
        md_table(
            affected.sort_values("pnl_delta_v3_minus_v1"),
            [
                "diff_group",
                "target_date",
                "city",
                "v1_expression",
                "v1_ask",
                "v1_payoff",
                "v1_pnl_usd",
                "v3_expression",
                "v3_ask",
                "v3_payoff",
                "v3_pnl_usd",
                "pnl_delta_v3_minus_v1",
                "running_max_state",
                "intraday_state",
                "minutes_since_running_max",
            ],
            limit=80,
        ),
        "",
        "## 机制判断",
        "",
        "- `pullback_uncertain` 不该继续归到 current-bracket NO；更自然的表达是 current-high YES，但必须 shadow 补 capacity。",
        "- `mature_fade/mature_fade` 已经不再被移除；新 v3 把它保留为 `cheap_stale_tail_current_no`，和 runway NO 分账。",
        f"- 被 v3 移除的 {removed_rows} 笔现在只剩 `plateau_near_high` 与 `running_max_clock_unknown`。前者历史表现偏弱，后者更像 clock/data freshness 诊断，不应塞回 runway 策略。",
        "- v3 的收益变化来自 route 语义重排：pullback 改 YES、mature fade 拆成 tail NO、plateau/clock unknown 留作诊断，而不是简单收益筛选。",
        "",
        f"Verdict: `inconclusive_shadow_candidate`。v3 是更干净的表达路由，但不是 live-ready；后续应单独评估 `cheap_stale_tail_current_no`，并继续审计 removed {removed_rows} 笔里的 clock/freshness 问题。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    v1, v3, affected, removed, flipped, summary_rows = build_diff()

    affected.to_csv(OUT_AFFECTED, index=False)
    removed.to_csv(OUT_REMOVED, index=False)
    flipped.to_csv(OUT_FLIPPED, index=False)

    state = pd.concat(
        [
            grouped_summary(g, ["running_max_state", "intraday_state"], name, prefix="v1").assign(diff_group=name)
            for name, g in affected.groupby("diff_group")
        ],
        ignore_index=True,
    ).sort_values(["diff_group", "pnl_usd", "rows"], ascending=[True, True, False])
    daily = pd.concat(
        [grouped_summary(g, ["target_date"], name, prefix="v1").assign(diff_group=name) for name, g in affected.groupby("diff_group")],
        ignore_index=True,
    ).sort_values(["target_date", "diff_group"])
    city = pd.concat(
        [grouped_summary(g, ["city"], name, prefix="v1").assign(diff_group=name) for name, g in affected.groupby("diff_group")],
        ignore_index=True,
    ).sort_values(["diff_group", "pnl_usd", "city"], ascending=[True, True, True])
    recent = pd.concat(
        [
            daily_for(v1[v1["target_date"].ge(FORWARD_START)], "original_mixed_v1", prefix="v1"),
            daily_for(v3[v3["target_date"].ge(FORWARD_START)], "router_v3", prefix="v3"),
        ],
        ignore_index=True,
    ).sort_values(["target_date", "strategy"])

    state.to_csv(OUT_STATE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    city.to_csv(OUT_CITY, index=False)
    recent.to_csv(OUT_RECENT, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "source_files": {
            "v1_source": str(V2_DETAILS.relative_to(ROOT)),
            "v3_source": str(V3_DETAILS.relative_to(ROOT)),
        },
        "source_date_min": str(v1["target_date"].min()),
        "source_date_max": str(v1["target_date"].max()),
        "source_rows_v1": int(len(v1)),
        "source_rows_v3": int(len(v3)),
        "affected_v1_rows": int(len(affected)),
        "removed_from_v3_rows": int(len(removed)),
        "flipped_to_current_high_yes_rows": int(len(flipped)),
        "net_row_delta_v3_minus_v1": int(len(v3) - len(v1)),
        "forward_start": FORWARD_START,
        "summary_rows": summary_rows,
        "outputs": {
            "summary": str(OUT_JSON.relative_to(ROOT)),
            "affected_v1_rows": str(OUT_AFFECTED.relative_to(ROOT)),
            "removed_from_v3": str(OUT_REMOVED.relative_to(ROOT)),
            "flipped_to_current_high_yes": str(OUT_FLIPPED.relative_to(ROOT)),
            "affected_state_summary": str(OUT_STATE.relative_to(ROOT)),
            "affected_daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "affected_city_summary": str(OUT_CITY.relative_to(ROOT)),
            "recent_daily_comparison": str(OUT_RECENT.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "NA_DIFF_REVIEW",
            "baseline": "NA_DIFF_REVIEW",
            "forward": "FAIL_THIN_AND_RECENT_STILL_NEGATIVE",
            "conclusion": "inconclusive_shadow_candidate",
            "live_ready": False,
        },
    }
    payload = finite(payload)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload, state, daily, city, recent, affected)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
