#!/usr/bin/env python3
"""Review the trade-level difference between regime-routed NO v1 and v2."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_v1_v2_diff_20260629"
OUT_JSON = OUT_DIR / "summary.json"
OUT_EXCLUDED = OUT_DIR / "v1_only_excluded_from_v2.csv"
OUT_REHEAT = OUT_DIR / "fresh_only_to_v2_added_false_fade_reheat.csv"
OUT_STATE = OUT_DIR / "diff_state_summary.csv"
OUT_DAILY = OUT_DIR / "diff_daily_summary.csv"
OUT_CITY = OUT_DIR / "diff_city_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-no-v1-v2-diff-review.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
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


def add_weighted(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["weight"] = pd.to_numeric(out["soft_balanced"], errors="coerce").fillna(1.0)
    out["weighted_cost_usd"] = out["stake_cost_usd"] * out["weight"]
    out["weighted_profit_usd"] = out["stake_profit_usd"] * out["weight"]
    out["window"] = np.where(out["target_date"].astype(str).ge("2026-06-21"), "forward_2026_06_21_23", "train_to_2026_06_20")
    out["ask_bucket"] = pd.cut(
        pd.to_numeric(out["ask"], errors="coerce"),
        bins=[0.0, 0.20, 0.35, 0.50, 0.70, 1.0],
        labels=["<=0.20", "0.20-0.35", "0.35-0.50", "0.50-0.70", ">0.70"],
        include_lowest=True,
    ).astype(str)
    return out


def summarize(frame: pd.DataFrame, name: str) -> dict[str, Any]:
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
    cost = float(frame["stake_cost_usd"].sum())
    pnl = float(frame["stake_profit_usd"].sum())
    weighted_cost = float(frame["weighted_cost_usd"].sum())
    weighted_pnl = float(frame["weighted_profit_usd"].sum())
    return {
        "slice": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(pd.to_numeric(frame["payoff"], errors="coerce").sum()),
        "win_rate": float(pd.to_numeric(frame["payoff"], errors="coerce").mean()),
        "avg_ask": float(pd.to_numeric(frame["ask"], errors="coerce").mean()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": weighted_cost,
        "weighted_pnl_usd": weighted_pnl,
        "weighted_roi": weighted_pnl / weighted_cost if weighted_cost else None,
    }


def grouped_summary(frame: pd.DataFrame, by: list[str], prefix: str) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(by, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = summarize(group, prefix)
        for col, val in zip(by, keys):
            row[col] = val
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["pnl_usd", "rows"], ascending=[True, False]).reset_index(drop=True)


def markdown_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, [c for c in cols if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if col in {"win_rate", "roi", "weighted_roi"}:
            view[col] = view[col].map(pct)
        elif col.endswith("_usd") or col == "avg_ask":
            if col == "avg_ask":
                view[col] = view[col].map(lambda x: "NA" if pd.isna(x) else f"{float(x):.3f}")
            else:
                view[col] = view[col].map(money)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in view.astype(str).to_numpy()]
    return "\n".join([header, sep, *rows])


def write_report(payload: dict[str, Any], state: pd.DataFrame, daily: pd.DataFrame, city: pd.DataFrame, excluded: pd.DataFrame, reheat: pd.DataFrame) -> None:
    rows = {row["slice"]: row for row in payload["summary_rows"]}
    original = rows["original_mixed_v1"]
    v2 = rows["mechanism_split_v2"]
    excluded_row = rows["v1_only_excluded_from_v2"]
    reheat_row = rows["fresh_only_to_v2_added_false_fade_reheat"]

    worst_excluded = excluded.sort_values("stake_profit_usd").head(15)
    best_excluded = excluded.sort_values("stake_profit_usd", ascending=False).head(15)
    lines = [
        "# Regime-Routed NO V1 vs V2 Diff Review",
        "",
        "## 结论",
        "",
        "v2 比最早的原始混合 v1 低，主要不是因为 fresh/reheat 机制本身差，而是因为 v2 把 `unapproved_stale_current_no` 从主策略里拿掉了。这个尾部组只有 35 笔、胜率 37.1%，但平均 ask 只有 0.385，靠少数便宜 NO 打出高赔率，给 v1 贡献了正 PnL。",
        "",
        f"- 原始混合 v1：{original['rows']} 笔，full ROI {pct(original['roi'])}，weighted ROI {pct(original['weighted_roi'])}。",
        f"- 机制拆分 v2：{v2['rows']} 笔，full ROI {pct(v2['roi'])}，weighted ROI {pct(v2['weighted_roi'])}。",
        f"- v1 有、v2 没有的 35 笔：full PnL {money(excluded_row['pnl_usd'])}，weighted PnL {money(excluded_row['weighted_pnl_usd'])}，weighted ROI {pct(excluded_row['weighted_roi'])}。",
        f"- fresh-only 到 v2 加回的 false-fade/reheat 43 笔：full PnL {money(reheat_row['pnl_usd'])}，weighted PnL {money(reheat_row['weighted_pnl_usd'])}，weighted ROI {pct(reheat_row['weighted_roi'])}。",
        "",
        "所以这里不是简单“v2 优化后变差”。更准确是：v2 把旧 v1 的便宜尾部票剥离了，牺牲了一点历史点估和 forward 3 天点估，但减少了把 stale/pullback 误叫 runway 的语义错误。",
        "",
        "## Diff Summary",
        "",
        markdown_table(
            pd.DataFrame(payload["summary_rows"]),
            ["slice", "rows", "dates", "cities", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Removed From V2 By State",
        "",
        markdown_table(
            state[state["diff_group"].eq("v1_only_excluded_from_v2")],
            ["running_max_state", "intraday_state", "rows", "dates", "cities", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Removed From V2 By Ask Bucket",
        "",
        markdown_table(
            grouped_summary(excluded, ["ask_bucket"], "v1_only_excluded_from_v2"),
            ["ask_bucket", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
        ),
        "",
        "## Daily Delta",
        "",
        markdown_table(
            daily,
            ["target_date", "diff_group", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
            limit=30,
        ),
        "",
        "## City Delta",
        "",
        markdown_table(
            city,
            ["city", "diff_group", "rows", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
            limit=40,
        ),
        "",
        "## Worst Removed Cases",
        "",
        markdown_table(
            worst_excluded,
            [
                "target_date",
                "city",
                "ask",
                "payoff",
                "stake_profit_usd",
                "weighted_profit_usd",
                "running_max_state",
                "intraday_state",
                "minutes_since_running_max",
                "forecast_peak_delta_hours_local",
                "forecast_gap_to_running_native",
                "temp_trend_1h_f",
                "temp_trend_3h_f",
                "wind_regime",
                "moisture_cloud_regime",
            ],
        ),
        "",
        "## Best Removed Cases",
        "",
        markdown_table(
            best_excluded,
            [
                "target_date",
                "city",
                "ask",
                "payoff",
                "stake_profit_usd",
                "weighted_profit_usd",
                "running_max_state",
                "intraday_state",
                "minutes_since_running_max",
                "forecast_peak_delta_hours_local",
                "forecast_gap_to_running_native",
                "temp_trend_1h_f",
                "temp_trend_3h_f",
                "wind_regime",
                "moisture_cloud_regime",
            ],
        ),
        "",
        "## 机制判断",
        "",
        "- `pullback_from_high / pullback_uncertain` 是明确坏形态：7 笔全输，应继续排除。",
        "- `stalled_high / plateau_near_high` 也不支持主策略：13 笔 ROI 为负，像是真的封顶/停滞。",
        "- `mature_fade / mature_fade` 和 `running_max_clock_unknown` 历史点估好，但样本小、胜率低或 clock 缺失，不应该直接并回 live；它更像低价 tail NO 或数据 freshness/clock 语义问题。",
        "- `false_fade/reheat` 是更像机制的一组，但 6/21..6/23 forward 两笔全错，因此只能 shadow，不进 live。",
        "",
        "## 需要调整的地方",
        "",
        "1. 策略层：保留 v2 的 route 分账，不把 removed 35 笔并回 `runway_current_no`。",
        "2. 数据层：`running_max_clock_unknown` 不能作为 live 交易态，应单独记录 clock/freshness 缺失原因；如果 live 里出现 unknown 但仍能下单，这是数据链路问题。",
        "3. 机制层：单独研究 `cheap stale current-NO tail`，但它的目标不是午后 runway，而是低价 NO tail 的市场结构/赔率问题。",
        "4. shadow 层：继续记录 `false_fade_reheat_current_no`，但必须看更多 forward 日期后再决定是否进入真钱 sizing。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = add_weighted(pd.read_csv(DETAILS, low_memory=False))
    original = df.copy()
    v2 = df[df["strategy_mechanism_split_v2"].astype(bool)].copy()
    excluded = df[df["mechanism_unapproved_stale_current_no"].astype(bool)].copy()
    reheat = df[df["mechanism_false_fade_reheat"].astype(bool)].copy()
    excluded = excluded.sort_values(["target_date", "city"]).reset_index(drop=True)
    reheat = reheat.sort_values(["target_date", "city"]).reset_index(drop=True)
    excluded.to_csv(OUT_EXCLUDED, index=False)
    reheat.to_csv(OUT_REHEAT, index=False)

    state = pd.concat(
        [
            grouped_summary(excluded, ["running_max_state", "intraday_state"], "v1_only_excluded_from_v2").assign(
                diff_group="v1_only_excluded_from_v2"
            ),
            grouped_summary(reheat, ["running_max_state", "intraday_state"], "fresh_only_to_v2_added_false_fade_reheat").assign(
                diff_group="fresh_only_to_v2_added_false_fade_reheat"
            ),
        ],
        ignore_index=True,
    )
    daily = pd.concat(
        [
            grouped_summary(excluded, ["target_date"], "v1_only_excluded_from_v2").assign(diff_group="v1_only_excluded_from_v2"),
            grouped_summary(reheat, ["target_date"], "fresh_only_to_v2_added_false_fade_reheat").assign(
                diff_group="fresh_only_to_v2_added_false_fade_reheat"
            ),
        ],
        ignore_index=True,
    ).sort_values(["pnl_usd", "target_date"], ascending=[True, True])
    city = pd.concat(
        [
            grouped_summary(excluded, ["city"], "v1_only_excluded_from_v2").assign(diff_group="v1_only_excluded_from_v2"),
            grouped_summary(reheat, ["city"], "fresh_only_to_v2_added_false_fade_reheat").assign(
                diff_group="fresh_only_to_v2_added_false_fade_reheat"
            ),
        ],
        ignore_index=True,
    ).sort_values(["pnl_usd", "city"], ascending=[True, True])
    state.to_csv(OUT_STATE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    city.to_csv(OUT_CITY, index=False)

    rows = [
        summarize(original, "original_mixed_v1"),
        summarize(v2, "mechanism_split_v2"),
        summarize(excluded, "v1_only_excluded_from_v2"),
        summarize(reheat, "fresh_only_to_v2_added_false_fade_reheat"),
    ]
    payload = {
        "generated_at_utc": now_utc(),
        "source_file": str(DETAILS.relative_to(ROOT)),
        "summary_rows": rows,
        "outputs": {
            "summary": str(OUT_JSON.relative_to(ROOT)),
            "v1_only_excluded_from_v2": str(OUT_EXCLUDED.relative_to(ROOT)),
            "fresh_only_to_v2_added_false_fade_reheat": str(OUT_REHEAT.relative_to(ROOT)),
            "diff_state_summary": str(OUT_STATE.relative_to(ROOT)),
            "diff_daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "diff_city_summary": str(OUT_CITY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(finite(payload), state, daily, city, excluded, reheat)
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
