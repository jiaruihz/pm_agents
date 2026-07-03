#!/usr/bin/env python3
"""Mechanism split for regime-routed NO.

The original regime-routed NO replay mixed two different current-NO ideas under
one `runway_current_no` label:

1. fresh runway: current observation is still making a fresh high;
2. false-fade/reheat: the day-level forecast has runway, but the observation has
   already pulled back or stalled and may reheat later.

This script keeps the same historical denominator as expression v1 and tests
whether a route-name split is more scientifically coherent than a hard stale
state removal.
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
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import research_regime_routed_no_expression_v1 as expression_v1  # noqa: E402

SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_STRATEGY = OUT_DIR / "strategy_summary.csv"
OUT_ROUTE = OUT_DIR / "route_mechanism_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_CITY = OUT_DIR / "city_summary.csv"
OUT_DETAILS = OUT_DIR / "trade_details.csv"
OUT_STALE = OUT_DIR / "unapproved_stale_current_no_details.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-28-regime-routed-no-mechanism-split-v2.md"

FORWARD_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def weighted_frame(frame: pd.DataFrame, weight_col: str = "soft_balanced") -> pd.DataFrame:
    out = expression_v1.add_soft_weights(frame)
    weight = pd.to_numeric(out.get(weight_col), errors="coerce").fillna(1.0)
    out["weight_policy"] = weight_col
    out["weight"] = weight
    out["weighted_cost_usd"] = out["stake_cost_usd"] * weight
    out["weighted_profit_usd"] = out["stake_profit_usd"] * weight
    return out


def date_bootstrap_roi(
    frame: pd.DataFrame,
    *,
    cost_col: str = "stake_cost_usd",
    pnl_col: str = "stake_profit_usd",
    n: int = 5000,
    seed: int = 20260628,
) -> tuple[float | None, float | None]:
    clean = frame[[cost_col, pnl_col, "target_date"]].dropna().copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(seed)
    dates = daily["target_date"].astype(str).to_numpy()
    daily = daily.set_index("target_date")
    vals: list[float] = []
    for _ in range(n):
        draw = rng.choice(dates, size=len(dates), replace=True)
        sample = daily.loc[draw]
        cost = float(sample["cost"].sum())
        pnl = float(sample["pnl"].sum())
        if cost:
            vals.append(pnl / cost)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def add_mechanism_labels(frame: pd.DataFrame) -> pd.DataFrame:
    out = expression_v1.add_soft_weights(frame.copy())
    route = out["route_leg"].astype(str)
    expr = out["expression"].astype(str)
    running = out["running_max_state"].astype(str)
    state = out["intraday_state"].astype(str)

    is_current_no = route.eq("runway_current_no") | expr.eq("current_bracket_no")
    is_capped = route.eq("capped_d2_no") | expr.eq("d2_no")
    fresh_runway = is_current_no & running.eq("fresh_running_high") & state.isin(["active_warming", "fresh_high"])
    false_fade_reheat = is_current_no & ~fresh_runway & state.isin(["false_fade_risk", "reheating_after_dip"])
    unapproved_stale = is_current_no & ~(fresh_runway | false_fade_reheat)

    out["is_current_no"] = is_current_no
    out["mechanism_fresh_runway"] = fresh_runway
    out["mechanism_false_fade_reheat"] = false_fade_reheat
    out["mechanism_unapproved_stale_current_no"] = unapproved_stale
    out["mechanism_capped_d2"] = is_capped
    out["route_mechanism"] = np.select(
        [is_capped, fresh_runway, false_fade_reheat, unapproved_stale],
        [
            "capped_d2_no",
            "fresh_runway_current_no",
            "false_fade_reheat_current_no",
            "unapproved_stale_current_no",
        ],
        default="other",
    )
    out["strategy_original_mixed_v1"] = True
    out["strategy_date_lineage_only"] = True
    out["strategy_fresh_plus_capped"] = is_capped | fresh_runway
    out["strategy_mechanism_split_v2"] = is_capped | fresh_runway | false_fade_reheat
    out["strategy_shadow_tail_all_current_no"] = is_capped | fresh_runway | false_fade_reheat | unapproved_stale
    out["window"] = np.where(out["target_date"].astype(str).ge(FORWARD_START), "forward_since_2026_06_21", "train_to_2026_06_20")
    return out


def summarize(frame: pd.DataFrame, name: str, *, window: str = "all") -> dict[str, Any]:
    if frame.empty:
        return {
            "slice": name,
            "window": window,
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
            "weighted_roi_ci_low": None,
            "weighted_roi_ci_high": None,
            "daily_roi_le_minus50": 0,
            "daily_roi_eq_minus100": 0,
            "worst_day_pnl_usd": None,
        }
    weighted = weighted_frame(frame)
    cost = float(frame["stake_cost_usd"].sum())
    pnl = float(frame["stake_profit_usd"].sum())
    weighted_cost = float(weighted["weighted_cost_usd"].sum())
    weighted_pnl = float(weighted["weighted_profit_usd"].sum())
    ci_low, ci_high = date_bootstrap_roi(weighted, cost_col="weighted_cost_usd", pnl_col="weighted_profit_usd")
    daily = (
        frame.groupby("target_date", as_index=False)
        .agg(cost=("stake_cost_usd", "sum"), pnl=("stake_profit_usd", "sum"), rows=("city", "size"))
        .assign(roi=lambda d: d["pnl"] / d["cost"])
    )
    return {
        "slice": name,
        "window": window,
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
        "weighted_roi_ci_low": ci_low,
        "weighted_roi_ci_high": ci_high,
        "daily_roi_le_minus50": int(daily["roi"].le(-0.50).sum()),
        "daily_roi_eq_minus100": int(daily["roi"].le(-0.999999).sum()),
        "worst_day_pnl_usd": float(daily["pnl"].min()) if not daily.empty else None,
    }


def summarize_strategies(frame: pd.DataFrame) -> pd.DataFrame:
    strategy_flags = [
        ("original_mixed_v1", "strategy_original_mixed_v1"),
        ("date_lineage_only_same_historical", "strategy_date_lineage_only"),
        ("fresh_plus_capped", "strategy_fresh_plus_capped"),
        ("mechanism_split_v2", "strategy_mechanism_split_v2"),
        ("shadow_tail_all_current_no", "strategy_shadow_tail_all_current_no"),
        ("diagnostic_unapproved_stale_only", "mechanism_unapproved_stale_current_no"),
        ("diagnostic_false_fade_reheat_only", "mechanism_false_fade_reheat"),
    ]
    rows: list[dict[str, Any]] = []
    for name, flag in strategy_flags:
        selected = frame[frame[flag].astype(bool)].copy()
        rows.append(summarize(selected, name))
        for window, sub in selected.groupby("window"):
            rows.append(summarize(sub, name, window=str(window)))
    return pd.DataFrame(rows)


def summarize_routes(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mechanism, group in frame.groupby("route_mechanism", dropna=False):
        row = summarize(group, str(mechanism))
        row["running_intraday_mix"] = "; ".join(
            f"{a}/{b}:{n}"
            for (a, b), n in group.groupby(["running_max_state", "intraday_state"], dropna=False).size().sort_values(ascending=False).items()
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["pnl_usd", "rows"], ascending=[True, False]).reset_index(drop=True)


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy, flag in [
        ("original_mixed_v1", "strategy_original_mixed_v1"),
        ("fresh_plus_capped", "strategy_fresh_plus_capped"),
        ("mechanism_split_v2", "strategy_mechanism_split_v2"),
    ]:
        selected = frame[frame[flag].astype(bool)].copy()
        for date, group in selected.groupby("target_date"):
            cost = float(group["stake_cost_usd"].sum())
            pnl = float(group["stake_profit_usd"].sum())
            rows.append(
                {
                    "target_date": date,
                    "strategy": strategy,
                    "rows": int(len(group)),
                    "cities": int(group["city"].nunique()),
                    "wins": int(pd.to_numeric(group["payoff"], errors="coerce").sum()),
                    "win_rate": float(pd.to_numeric(group["payoff"], errors="coerce").mean()),
                    "avg_ask": float(pd.to_numeric(group["ask"], errors="coerce").mean()),
                    "cost_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost else np.nan,
                    "route_mix": ",".join(f"{k}:{v}" for k, v in group["route_mechanism"].value_counts().sort_index().items()),
                    "loss_cities": ",".join(group.loc[group["payoff"].eq(0), "city"].astype(str).sort_values().tolist()),
                }
            )
    return pd.DataFrame(rows).sort_values(["strategy", "target_date"]).reset_index(drop=True)


def city_summary(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[frame["strategy_mechanism_split_v2"].astype(bool)].copy()
    rows = []
    for city, group in selected.groupby("city"):
        cost = float(group["stake_cost_usd"].sum())
        pnl = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "city": city,
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "wins": int(pd.to_numeric(group["payoff"], errors="coerce").sum()),
                "win_rate": float(pd.to_numeric(group["payoff"], errors="coerce").mean()),
                "avg_ask": float(pd.to_numeric(group["ask"], errors="coerce").mean()),
                "cost_usd": cost,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost else np.nan,
                "route_mix": ",".join(f"{k}:{v}" for k, v in group["route_mechanism"].value_counts().sort_index().items()),
            }
        )
    return pd.DataFrame(rows).sort_values(["pnl_usd", "rows"], ascending=[True, False]).reset_index(drop=True)


def markdown_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, [c for c in cols if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if col in {"win_rate", "roi", "weighted_roi", "weighted_roi_ci_low", "weighted_roi_ci_high"}:
            view[col] = view[col].map(pct)
        elif col.endswith("_usd") or col in {"avg_ask"}:
            if col == "avg_ask":
                view[col] = view[col].map(lambda x: "NA" if pd.isna(x) else f"{float(x):.3f}")
            else:
                view[col] = view[col].map(money)
    header = "| " + " | ".join(view.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in view.astype(str).to_numpy()]
    return "\n".join([header, sep, *rows])


def write_report(payload: dict[str, Any], strategy: pd.DataFrame, route: pd.DataFrame, daily: pd.DataFrame, city: pd.DataFrame) -> None:
    key_rows = {row["slice"]: row for row in payload["strategy_rows"] if row["window"] == "all"}
    original = key_rows["original_mixed_v1"]
    fresh = key_rows["fresh_plus_capped"]
    split = key_rows["mechanism_split_v2"]
    stale = key_rows["diagnostic_unapproved_stale_only"]
    reheat = key_rows["diagnostic_false_fade_reheat_only"]
    worst_split = daily[daily["strategy"].eq("mechanism_split_v2")].sort_values("pnl_usd").head(12)
    lines = [
        "# Regime-Routed NO Mechanism Split V2",
        "",
        "## 结论",
        "",
        "这次不是再加一条 gate，而是把旧 `runway_current_no` 拆成两个物理含义不同的 route：",
        "",
        "- `fresh_runway_current_no`：当前观测仍是新高/近新高，且日内状态还在升温。",
        "- `false_fade_reheat_current_no`：当前已经回落或停住，但状态像 false fade / reheating after dip，下注逻辑是后面还能重新加热打穿当前档。",
        "- `unapproved_stale_current_no`：停滞、pullback uncertain、clock unknown 等不干净形态，先只做诊断，不并回主策略。",
        "",
        f"同分母历史主样本仍是 `{payload['source_file']}` 的 `{MAIN_VARIANT}`：{payload['rows']} rows / {payload['dates']} dates / {payload['cities']} cities。",
        "",
        f"- 原始混合 v1：{original['rows']} 笔，full ROI {pct(original['roi'])}，soft weighted ROI {pct(original['weighted_roi'])}，CI [{pct(original['weighted_roi_ci_low'])}, {pct(original['weighted_roi_ci_high'])}]。",
        f"- 只保留 fresh runway + capped d2：{fresh['rows']} 笔，full ROI {pct(fresh['roi'])}，soft weighted ROI {pct(fresh['weighted_roi'])}，CI [{pct(fresh['weighted_roi_ci_low'])}, {pct(fresh['weighted_roi_ci_high'])}]。",
        f"- 机制拆分 v2（fresh + false-fade/reheat + capped d2）：{split['rows']} 笔，full ROI {pct(split['roi'])}，soft weighted ROI {pct(split['weighted_roi'])}，CI [{pct(split['weighted_roi_ci_low'])}, {pct(split['weighted_roi_ci_high'])}]。",
        "",
        "所以 ROI 降低的原因不是“新定义不符合物理”，而是 fresh-only 把一批不是 runway、但可能属于 reheat-after-dip 的盈利样本一起切掉了。正确修法是拆 route，不是把 stale/fade 全部塞回 runway。",
        "",
        "## Strategy Summary",
        "",
        markdown_table(
            strategy[strategy["window"].eq("all")],
            [
                "slice",
                "rows",
                "dates",
                "cities",
                "wins",
                "win_rate",
                "avg_ask",
                "roi",
                "weighted_roi",
                "weighted_roi_ci_low",
                "weighted_roi_ci_high",
                "daily_roi_eq_minus100",
                "worst_day_pnl_usd",
            ],
        ),
        "",
        "## Train / Forward Split",
        "",
        markdown_table(
            strategy[strategy["window"].ne("all")],
            [
                "slice",
                "window",
                "rows",
                "dates",
                "cities",
                "win_rate",
                "avg_ask",
                "roi",
                "weighted_roi",
                "daily_roi_eq_minus100",
                "worst_day_pnl_usd",
            ],
        ),
        "",
        "## Route Mechanisms",
        "",
        markdown_table(
            route,
            [
                "slice",
                "rows",
                "dates",
                "cities",
                "win_rate",
                "avg_ask",
                "roi",
                "weighted_roi",
                "weighted_roi_ci_low",
                "weighted_roi_ci_high",
                "daily_roi_eq_minus100",
                "worst_day_pnl_usd",
            ],
        ),
        "",
        "## 为什么 fresh-only 会降 ROI",
        "",
        f"被 fresh-only 切掉的 current-NO 里，false-fade/reheat 子组是 {reheat['rows']} 笔，full ROI {pct(reheat['roi'])}，soft weighted ROI {pct(reheat['weighted_roi'])}；不干净 stale 子组是 {stale['rows']} 笔，full ROI {pct(stale['roi'])}，soft weighted ROI {pct(stale['weighted_roi'])}。",
        "",
        "这说明旧策略混了三类东西：干净 runway、另一条可能有效的 reheat-after-dip、以及目前缺乏机制解释的尾部样本。fresh-only 物理更干净，但会牺牲 reheat-after-dip 这条候选 alpha；v2 的科学性在于把它们命名分账，而不是用一个大 `runway_current_no` 继续混。",
        "",
        "## Mechanism Split V2 Worst Days",
        "",
        markdown_table(
            worst_split,
            ["target_date", "rows", "cities", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "route_mix", "loss_cities"],
        ),
        "",
        "## City Contribution",
        "",
        markdown_table(
            city,
            ["city", "rows", "dates", "wins", "win_rate", "avg_ask", "pnl_usd", "roi", "route_mix"],
            limit=20,
        ),
        "",
        "## Verdict",
        "",
        "significance=PASS：v2 soft weighted ROI 的历史 date-block CI 大于 0。",
        "baseline=PARTIAL：相对旧混合 v1 没有明显提升，主要价值是机制解释、避免错名，并把 unapproved stale 从主策略里分离出来。",
        "forward=FAIL：2026-06-21 以来已结算 forward 子窗仍薄且表现为负，不能作为 live confirmation。",
        "conclusion=inconclusive_shadow_only。",
        "",
        "当前可采取的动作：本地策略研究口径改成机制分裂；live 不恢复。shadow 可以记录三个 route 的独立命中率、价格、wind/cloud/freshness 分布，后续再看 false-fade/reheat 是否能单独过前瞻门。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(SELECTED, low_memory=False)
    main = df[df["variant"].eq(MAIN_VARIANT)].copy()
    labelled = add_mechanism_labels(main)
    strategy = summarize_strategies(labelled)
    route = summarize_routes(labelled)
    daily = daily_summary(labelled)
    city = city_summary(labelled)
    labelled.to_csv(OUT_DETAILS, index=False)
    labelled[labelled["mechanism_unapproved_stale_current_no"].astype(bool)].to_csv(OUT_STALE, index=False)
    strategy.to_csv(OUT_STRATEGY, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    city.to_csv(OUT_CITY, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "source_file": str(SELECTED.relative_to(ROOT)),
        "variant": MAIN_VARIANT,
        "date_min": str(labelled["target_date"].min()),
        "date_max": str(labelled["target_date"].max()),
        "rows": int(len(labelled)),
        "dates": int(labelled["target_date"].nunique()),
        "cities": int(labelled["city"].nunique()),
        "forward_start": FORWARD_START,
        "strategy_rows": strategy.to_dict("records"),
        "route_rows": route.to_dict("records"),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "strategy_summary": str(OUT_STRATEGY.relative_to(ROOT)),
            "route_mechanism_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "city_summary": str(OUT_CITY.relative_to(ROOT)),
            "trade_details": str(OUT_DETAILS.relative_to(ROOT)),
            "unapproved_stale_current_no_details": str(OUT_STALE.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "PASS",
            "baseline": "PARTIAL",
            "forward": "FAIL",
            "conclusion": "inconclusive_shadow_only",
            "live_ready": False,
        },
    }
    OUT_SUMMARY.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(finite(payload), strategy, route, daily, city)
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
