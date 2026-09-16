#!/usr/bin/env python3
"""Tmax target-book rebalance replay.

Research-only. No live runner/config/order behavior is changed.

This script upgrades the P5/P6 "one row = one trade opportunity" replay into a
position-aware city-day target book:

  - one active net leg per city-day;
  - later posterior updates revalue the existing leg first;
  - closing is modeled by buying the same-bracket complementary token at ask;
  - reversing is allowed only when incremental EV covers close + new-entry cash
    costs under the current posterior.

The primary target set excludes current_yes because current_yes remains
settlement-basis/below-bucket shadow only.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
EXPR_CANDIDATES = ROOT / "docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_target_book_rebalance_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-08-tmax-target-book-rebalance-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-08-tmax-target-book-rebalance-v1.json"

METHOD = "loo_no_city_source_blend"
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02
TAKER_FEE_RATE = 0.05
BOOT_N = 2000
BOOT_SEED = 20260708

ACTIVE_EXPRESSIONS = ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]
ALL_EXPRESSIONS_FOR_REVALUE = ["current_yes", "current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]

POLICIES = {
    "first_lock_no_current_yes": {"mode": "first_lock", "buffer": math.inf},
    "target_book_close_only_ev0": {"mode": "close_only", "buffer": 0.0},
    "target_book_rebalance_ev0": {"mode": "rebalance", "buffer": 0.0},
    "target_book_rebalance_ev02": {"mode": "rebalance", "buffer": 0.02},
}


@dataclass
class ActiveLeg:
    expression: str
    bucket: str
    side: str
    entry_hour: float
    ask: float
    fee: float
    cost: float
    win: float


def _fee(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)


def _side(expr: str) -> str:
    return "YES" if str(expr).endswith("_yes") else "NO"


def _bucket(expr: str) -> str:
    if str(expr).startswith("current"):
        return "current"
    if str(expr).startswith("d1"):
        return "d1"
    if str(expr).startswith("d2"):
        return "d2"
    return "unknown"


def _complement(expr: str) -> str | None:
    return {
        "current_yes": "current_no",
        "current_no": "current_yes",
        "d1_yes": "d1_no",
        "d1_no": "d1_yes",
        "d2_yes": "d2_no",
        "d2_no": "d2_yes",
    }.get(str(expr))


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _fmt_num(value: object, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{digits}f}"


def _date_block_ci(rows: pd.DataFrame, cost_col: str = "cost_net", pnl_col: str = "pnl_net") -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    cost = float(rows[cost_col].sum())
    pnl = float(rows[pnl_col].sum())
    roi = pnl / cost if cost else math.nan
    by_day = rows.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(by_day) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(BOOT_SEED)
    arr = by_day[["cost", "pnl"]].to_numpy(dtype=float)
    boot: list[float] = []
    for _ in range(BOOT_N):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
    }


def _load_candidates() -> pd.DataFrame:
    if not EXPR_CANDIDATES.exists():
        raise FileNotFoundError(f"missing expression candidates: {EXPR_CANDIDATES}")
    df = pd.read_csv(EXPR_CANDIDATES, low_memory=False)
    required = {
        "scope",
        "method",
        "expression",
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "ask",
        "p_win",
        "win",
        "unit_pnl",
        "fee_adjusted_edge",
        "fee_adjusted_roi_model",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"expression candidates missing columns: {missing}")
    df = df[df["method"].eq(METHOD) & df["expression"].isin(ALL_EXPRESSIONS_FOR_REVALUE)].copy()
    for col in [
        "decision_hour_local",
        "ask",
        "p_win",
        "win",
        "unit_pnl",
        "fee_adjusted_edge",
        "fee_adjusted_roi_model",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["ask"].notna() & df["p_win"].notna() & df["win"].notna()].copy()
    df["side"] = df["expression"].map(_side)
    df["bucket"] = df["expression"].map(_bucket)
    df["fee"] = df["ask"].map(_fee)
    df["cost_net_one"] = df["ask"] + df["fee"]
    return df.sort_values(["scope", "target_date", "city", "decision_hour_local", "expression"]).reset_index(drop=True)


def _best_by_hour(candidates: pd.DataFrame) -> pd.DataFrame:
    eligible = candidates[
        candidates["expression"].isin(ACTIVE_EXPRESSIONS)
        & candidates["ask"].ge(ASK_FLOOR)
        & candidates["ask"].le(ASK_CEILING)
        & candidates["fee_adjusted_edge"].ge(EDGE_THRESHOLD)
    ].copy()
    if eligible.empty:
        return eligible
    return (
        eligible.sort_values(
            ["scope", "city", "target_date", "decision_hour_local", "fee_adjusted_edge", "fee_adjusted_roi_model"],
            ascending=[True, True, True, True, False, False],
        )
        .groupby(["scope", "city", "target_date", "decision_hour_local"], as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def _leg_from_row(row: pd.Series) -> ActiveLeg:
    ask = float(row["ask"])
    fee = _fee(ask)
    return ActiveLeg(
        expression=str(row["expression"]),
        bucket=str(row["bucket"]),
        side=str(row["side"]),
        entry_hour=float(row["decision_hour_local"]),
        ask=ask,
        fee=fee,
        cost=ask + fee,
        win=float(row["win"]),
    )


def _row_lookup(grp: pd.DataFrame, hour: float, expr: str) -> pd.Series | None:
    sub = grp[(grp["decision_hour_local"].eq(hour)) & (grp["expression"].eq(expr))]
    if sub.empty:
        return None
    return sub.iloc[0]


def _settle_active(active: ActiveLeg | None, cost: float, payoff: float) -> tuple[float, float, float]:
    if active is not None:
        payoff += active.win
    return cost, payoff, payoff - cost


def _maybe_rebalance(
    *,
    grp: pd.DataFrame,
    active: ActiveLeg,
    desired_row: pd.Series,
    mode: str,
    buffer: float,
) -> dict[str, Any]:
    hour = float(desired_row["decision_hour_local"])
    old_row = _row_lookup(grp, hour, active.expression)
    close_expr = _complement(active.expression)
    close_row = _row_lookup(grp, hour, close_expr or "")
    if old_row is None or close_row is None:
        return {"action": "hold_missing_revalue_or_close", "do": False}

    old_p = float(old_row["p_win"])
    close_cost = float(close_row["ask"]) + _fee(float(close_row["ask"]))
    close_value = 1.0 - close_cost
    close_incremental_ev = close_value - old_p

    if mode == "close_only":
        return {
            "action": "close" if close_incremental_ev > buffer else "hold_close_not_worth_cost",
            "do": close_incremental_ev > buffer,
            "close_expr": str(close_row["expression"]),
            "close_cost": close_cost,
            "new_cost": 0.0,
            "old_p": old_p,
            "new_p": math.nan,
            "close_incremental_ev": close_incremental_ev,
            "rebalance_incremental_ev": math.nan,
        }

    new_cost = float(desired_row["ask"]) + _fee(float(desired_row["ask"]))
    new_p = float(desired_row["p_win"])
    rebalance_incremental_ev = close_value + (new_p - new_cost) - old_p
    return {
        "action": "close_and_reopen" if rebalance_incremental_ev > buffer else "hold_rebalance_not_worth_cost",
        "do": rebalance_incremental_ev > buffer,
        "close_expr": str(close_row["expression"]),
        "close_cost": close_cost,
        "new_cost": new_cost,
        "old_p": old_p,
        "new_p": new_p,
        "close_incremental_ev": close_incremental_ev,
        "rebalance_incremental_ev": rebalance_incremental_ev,
    }


def simulate_policy(candidates: pd.DataFrame, best: pd.DataFrame, policy_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = POLICIES[policy_name]
    mode = str(spec["mode"])
    buffer = float(spec["buffer"])
    rows: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for (scope, city, target_date), grp_all in candidates.groupby(["scope", "city", "target_date"], dropna=False):
        state_best = best[
            best["scope"].eq(scope) & best["city"].eq(city) & best["target_date"].eq(target_date)
        ].copy()
        if state_best.empty:
            continue
        grp = grp_all.sort_values(["decision_hour_local", "expression"]).copy()
        state_best = state_best.sort_values(["decision_hour_local", "fee_adjusted_edge"], ascending=[True, False])

        cost = 0.0
        payoff = 0.0
        active: ActiveLeg | None = None
        first_expr = ""
        last_action = ""
        closes = 0
        reopens = 0
        holds = 0

        for _, desired in state_best.iterrows():
            hour = float(desired["decision_hour_local"])
            desired_leg = _leg_from_row(desired)
            if active is None:
                active = desired_leg
                cost += desired_leg.cost
                first_expr = first_expr or desired_leg.expression
                actions.append(
                    {
                        "policy": policy_name,
                        "scope": scope,
                        "city": city,
                        "target_date": target_date,
                        "hour": hour,
                        "action": "open",
                        "active_before": "",
                        "desired_expression": desired_leg.expression,
                        "close_expr": "",
                        "cost_delta": desired_leg.cost,
                        "payoff_locked_delta": 0.0,
                        "old_p": math.nan,
                        "new_p": float(desired["p_win"]),
                        "rebalance_incremental_ev": math.nan,
                    }
                )
                last_action = "open"
                if mode == "first_lock":
                    break
                continue

            if desired_leg.expression == active.expression:
                holds += 1
                last_action = "hold_same_expression"
                continue

            decision = _maybe_rebalance(grp=grp, active=active, desired_row=desired, mode=mode, buffer=buffer)
            if not decision.get("do"):
                holds += 1
                actions.append(
                    {
                        "policy": policy_name,
                        "scope": scope,
                        "city": city,
                        "target_date": target_date,
                        "hour": hour,
                        "action": decision.get("action"),
                        "active_before": active.expression,
                        "desired_expression": desired_leg.expression,
                        "close_expr": decision.get("close_expr", ""),
                        "cost_delta": 0.0,
                        "payoff_locked_delta": 0.0,
                        "old_p": decision.get("old_p", math.nan),
                        "new_p": decision.get("new_p", math.nan),
                        "close_incremental_ev": decision.get("close_incremental_ev", math.nan),
                        "rebalance_incremental_ev": decision.get("rebalance_incremental_ev", math.nan),
                    }
                )
                last_action = str(decision.get("action"))
                continue

            close_cost = float(decision["close_cost"])
            cost += close_cost
            payoff += 1.0
            closes += 1
            actions.append(
                {
                    "policy": policy_name,
                    "scope": scope,
                    "city": city,
                    "target_date": target_date,
                    "hour": hour,
                    "action": "close",
                    "active_before": active.expression,
                    "desired_expression": desired_leg.expression,
                    "close_expr": decision.get("close_expr", ""),
                    "cost_delta": close_cost,
                    "payoff_locked_delta": 1.0,
                    "old_p": decision.get("old_p", math.nan),
                    "new_p": decision.get("new_p", math.nan),
                    "close_incremental_ev": decision.get("close_incremental_ev", math.nan),
                    "rebalance_incremental_ev": decision.get("rebalance_incremental_ev", math.nan),
                }
            )
            active = None
            last_action = "close"

            if mode == "rebalance":
                active = desired_leg
                cost += desired_leg.cost
                reopens += 1
                actions.append(
                    {
                        "policy": policy_name,
                        "scope": scope,
                        "city": city,
                        "target_date": target_date,
                        "hour": hour,
                        "action": "reopen",
                        "active_before": "",
                        "desired_expression": desired_leg.expression,
                        "close_expr": "",
                        "cost_delta": desired_leg.cost,
                        "payoff_locked_delta": 0.0,
                        "old_p": math.nan,
                        "new_p": float(desired["p_win"]),
                        "close_incremental_ev": math.nan,
                        "rebalance_incremental_ev": decision.get("rebalance_incremental_ev", math.nan),
                    }
                )
                last_action = "close_and_reopen"

        final_expr = "" if active is None else active.expression
        final_win = 0.0 if active is None else active.win
        cost_net, payoff_total, pnl_net = _settle_active(active, cost, payoff)
        rows.append(
            {
                "policy": policy_name,
                "scope": scope,
                "city": city,
                "target_date": target_date,
                "first_expression": first_expr,
                "final_expression": final_expr,
                "final_win": final_win,
                "actions": len([a for a in actions if a["policy"] == policy_name and a["scope"] == scope and a["city"] == city and a["target_date"] == target_date]),
                "closes": closes,
                "reopens": reopens,
                "holds": holds,
                "last_action": last_action,
                "cost_net": cost_net,
                "payoff": payoff_total,
                "pnl_net": pnl_net,
                "roi_net": pnl_net / cost_net if cost_net else math.nan,
            }
        )

    return pd.DataFrame(rows), pd.DataFrame(actions)


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    if rows.empty:
        return pd.DataFrame()
    for (policy, scope), grp in rows.groupby(["policy", "scope"], dropna=False):
        ci = _date_block_ci(grp)
        daily = grp.groupby("target_date", as_index=False).agg(cost_net=("cost_net", "sum"), pnl_net=("pnl_net", "sum"))
        daily["roi_net"] = daily["pnl_net"] / daily["cost_net"]
        out.append(
            {
                "policy": policy,
                "scope": scope,
                "rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "closes": int(grp["closes"].sum()),
                "reopens": int(grp["reopens"].sum()),
                "cost_net": float(grp["cost_net"].sum()),
                "pnl_net": float(grp["pnl_net"].sum()),
                "roi_net": ci["roi"],
                "roi_net_ci_low": ci["ci_low"],
                "roi_net_ci_high": ci["ci_high"],
                "daily_positive": int((daily["pnl_net"] > 0).sum()),
                "daily_negative": int((daily["pnl_net"] < 0).sum()),
                "worst_day_roi_net": float(daily["roi_net"].min()),
                "best_day_roi_net": float(daily["roi_net"].max()),
            }
        )
    return pd.DataFrame(out).sort_values(["scope", "policy"]).reset_index(drop=True)


def daily_summary(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    daily = (
        rows.groupby(["policy", "scope", "target_date"], dropna=False)
        .agg(
            rows=("city", "size"),
            cities=("city", "nunique"),
            closes=("closes", "sum"),
            reopens=("reopens", "sum"),
            cost_net=("cost_net", "sum"),
            pnl_net=("pnl_net", "sum"),
        )
        .reset_index()
    )
    daily["roi_net"] = daily["pnl_net"] / daily["cost_net"]
    return daily.sort_values(["scope", "target_date", "policy"]).reset_index(drop=True)


def delta_vs_first(rows: pd.DataFrame) -> pd.DataFrame:
    base = rows[rows["policy"].eq("first_lock_no_current_yes")].copy()
    others = rows[~rows["policy"].eq("first_lock_no_current_yes")].copy()
    keys = ["scope", "city", "target_date"]
    base = base.set_index(keys)
    out: list[dict[str, Any]] = []
    for _, row in others.iterrows():
        key = (row["scope"], row["city"], row["target_date"])
        if key not in base.index:
            continue
        b = base.loc[key]
        out.append(
            {
                "policy": row["policy"],
                "scope": row["scope"],
                "city": row["city"],
                "target_date": row["target_date"],
                "first_expression": b["final_expression"],
                "target_book_final_expression": row["final_expression"],
                "first_pnl_net": float(b["pnl_net"]),
                "target_book_pnl_net": float(row["pnl_net"]),
                "pnl_net_delta": float(row["pnl_net"] - b["pnl_net"]),
                "closes": int(row["closes"]),
                "reopens": int(row["reopens"]),
                "last_action": row["last_action"],
            }
        )
    return pd.DataFrame(out).sort_values(["scope", "policy", "pnl_net_delta", "target_date", "city"]).reset_index(drop=True)


def expression_contribution(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    settled = rows[rows["final_expression"].ne("")].copy()
    if settled.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for (policy, scope, expr), grp in settled.groupby(["policy", "scope", "final_expression"], dropna=False):
        ci = _date_block_ci(grp)
        out.append(
            {
                "policy": policy,
                "scope": scope,
                "final_expression": expr,
                "rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "win_rate": float(grp["final_win"].mean()),
                "cost_net": float(grp["cost_net"].sum()),
                "pnl_net": float(grp["pnl_net"].sum()),
                "roi_net": ci["roi"],
            }
        )
    return pd.DataFrame(out).sort_values(["scope", "policy", "pnl_net"]).reset_index(drop=True)


def _table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    use = df if max_rows is None else df.head(max_rows)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    pct_cols = {"roi_net", "roi_net_ci_low", "roi_net_ci_high", "worst_day_roi_net", "best_day_roi_net", "win_rate"}
    money_cols = {"cost_net", "pnl_net", "first_pnl_net", "target_book_pnl_net", "pnl_net_delta"}
    for row in use.to_dict("records"):
        vals: list[str] = []
        for col in columns:
            val = row.get(col)
            if isinstance(val, float):
                if col in pct_cols:
                    vals.append(_fmt_pct(val))
                elif col in money_cols:
                    vals.append(_fmt_num(val, 2))
                else:
                    vals.append(_fmt_num(val, 3))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def write_report(
    *,
    candidates: pd.DataFrame,
    best: pd.DataFrame,
    summary: pd.DataFrame,
    daily: pd.DataFrame,
    expr: pd.DataFrame,
    delta: pd.DataFrame,
    actions: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    verified = summary[summary["scope"].eq("verified_forward")].copy()
    dev = summary[summary["scope"].eq("dev_cv")].copy()
    primary_daily = daily[
        daily["scope"].eq("verified_forward") & daily["policy"].isin(["first_lock_no_current_yes", "target_book_rebalance_ev0"])
    ].copy()
    primary_expr = expr[
        expr["scope"].eq("verified_forward") & expr["policy"].isin(["first_lock_no_current_yes", "target_book_rebalance_ev0"])
    ].copy()
    primary_delta = delta[
        delta["scope"].eq("verified_forward") & delta["policy"].eq("target_book_rebalance_ev0")
    ].copy()
    action_focus = actions[
        actions["scope"].eq("verified_forward") & actions["policy"].eq("target_book_rebalance_ev0") & actions["action"].isin(["close", "reopen"])
    ].copy()
    lines = [
        "# Tmax Target-Book Rebalance v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: research/shadow only. No live runner/config/order behavior changed.",
        "",
        "## 结论 / 交易动作",
        "",
        "- 这版把策略从“每小时 posterior 生成新单”改成“city-day 级目标持仓账本”。核心机制是先重估已有仓位，再决定是否用同 bracket 反向 token 锁平，最后才考虑重新开新目标。",
        "- 中间成本不是拍参数：开仓成本=`ask + 0.05*ask*(1-ask)`；锁平成本=买同 bracket complement 的 `ask + fee`；换仓只在 `1 - close_cost + (p_new - new_cost) - p_old > buffer` 时发生。",
        "- Primary 建议不是 follow-latest，而是 `first_lock_no_current_yes` 作为 live 前默认；`target_book_rebalance_ev0` 只做 shadow。原因是 verified 点估略好/接近，但 CI 跨 0，且 current data layer 仍没有真实 position ids / fresh bid path。",
        "- Lucknow 7/05 在这个体系下：主动表达集不含 `current_yes`，所以 12:17 的 36 YES 不会作为新主动目标；已有 36 NO 只会被重估。即使允许 current_yes，成本账本也会先走“平/翻仓成本”计算，而不是直接叠加反向单。",
        "",
        "## 数据快照",
        "",
        f"- Source candidates: `{EXPR_CANDIDATES.relative_to(ROOT)}`",
        f"- Candidate rows: `{len(candidates)}`; best state rows after ask/fee edge: `{len(best)}`.",
        f"- Date range: `{candidates['target_date'].min()}`..`{candidates['target_date'].max()}`; scopes: `{candidates['scope'].value_counts(dropna=False).to_dict()}`.",
        "- This is P5/P6 materialized evidence, not fresh current-day live replay. It cannot replace executor-level fill sync.",
        "",
        "## 成本机制",
        "",
        "For one active old leg at a later hour:",
        "",
        "```text",
        "old_hold_value = p_old",
        "close_value = 1 - close_ask - close_fee",
        "new_value = p_new - new_ask - new_fee",
        "",
        "close_only_incremental_EV = close_value - old_hold_value",
        "rebalance_incremental_EV = close_value + new_value - old_hold_value",
        "execute iff incremental_EV > buffer",
        "```",
        "",
        "`buffer=0` is the clean theoretical cost gate because ask/spread/fee are already paid. `buffer=0.02` is only a robustness sensitivity for stale-book/adverse-selection; it is not used as primary proof.",
        "",
        "## Verified Forward Summary",
        "",
        *_table(
            verified,
            [
                "policy",
                "rows",
                "dates",
                "cities",
                "closes",
                "reopens",
                "cost_net",
                "pnl_net",
                "roi_net",
                "roi_net_ci_low",
                "roi_net_ci_high",
                "daily_positive",
                "daily_negative",
            ],
            max_rows=20,
        ),
        "",
        "## Lucknow 2026-07-05 Counterfactual",
        "",
        "This date is not in the P5 materialized denominator, so it is a case-level replay from the live review facts, not part of the aggregate ROI table.",
        "",
        "- Actual bug path: three repeated `36 NO` buys, then one opposite `36 YES` buy.",
        "- New primary policy path: buy the first eligible `36 NO` once; do not open active `current_yes`; later signals only revalue the existing `36 NO`.",
        "- Per-share clean cost for the first `36 NO @0.65`: `0.65 + 0.05*0.65*(1-0.65) = 0.6614`; final reached 37, so 36 NO wins, net `+0.3386/share` before share multiplier.",
        "- Important stress check: if `current_yes` were allowed, the 12:17 posterior could still justify flipping under the cost formula because `p_yes≈0.586`, `yes_ask≈0.48`, and the model overvalued stop-at-36. That is why cost-aware ledger is necessary but not sufficient; `current_yes` stays shadow until E2/five-bucket target is fixed.",
        "",
        "## Dev CV Summary",
        "",
        *_table(
            dev,
            [
                "policy",
                "rows",
                "dates",
                "cities",
                "closes",
                "reopens",
                "cost_net",
                "pnl_net",
                "roi_net",
                "roi_net_ci_low",
                "roi_net_ci_high",
            ],
            max_rows=20,
        ),
        "",
        "## Verified Daily: First Lock vs Target Book",
        "",
        *_table(
            primary_daily,
            ["policy", "target_date", "rows", "cities", "closes", "reopens", "cost_net", "pnl_net", "roi_net"],
            max_rows=80,
        ),
        "",
        "## Verified Expression Contribution",
        "",
        *_table(
            primary_expr,
            ["policy", "final_expression", "rows", "dates", "win_rate", "cost_net", "pnl_net", "roi_net"],
            max_rows=80,
        ),
        "",
        "## Target-Book Delta vs First Lock",
        "",
        "Worst and best deltas are city-day level. Positive means target-book improved over first-lock.",
        "",
        "### Worst Deltas",
        "",
        *_table(
            primary_delta,
            [
                "city",
                "target_date",
                "first_expression",
                "target_book_final_expression",
                "first_pnl_net",
                "target_book_pnl_net",
                "pnl_net_delta",
                "closes",
                "reopens",
                "last_action",
            ],
            max_rows=12,
        ),
        "",
        "### Best Deltas",
        "",
        *_table(
            primary_delta.sort_values("pnl_net_delta", ascending=False),
            [
                "city",
                "target_date",
                "first_expression",
                "target_book_final_expression",
                "first_pnl_net",
                "target_book_pnl_net",
                "pnl_net_delta",
                "closes",
                "reopens",
                "last_action",
            ],
            max_rows=12,
        ),
        "",
        "## Rebalance Action Samples",
        "",
        *_table(
            action_focus,
            [
                "city",
                "target_date",
                "hour",
                "action",
                "active_before",
                "desired_expression",
                "close_expr",
                "cost_delta",
                "old_p",
                "new_p",
                "close_incremental_ev",
                "rebalance_incremental_ev",
            ],
            max_rows=30,
        ),
        "",
        "## 三道门",
        "",
        "significance=FAIL/PARTIAL because verified point estimates are positive but policy deltas are not yet proven with executor-grade bid/fill data; baseline=PARTIAL because first-lock remains competitive and simpler; forward=FAIL because this is materialized P5/P6, not fresh live-shadow target-book ledger.",
        "",
        "conclusion=`shadow_candidate_target_book_not_live`.",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'target_book_decisions.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'target_book_actions.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'policy_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'daily_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'expression_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'delta_vs_first_lock.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = _load_candidates()
    best = _best_by_hour(candidates)
    decision_frames: list[pd.DataFrame] = []
    action_frames: list[pd.DataFrame] = []
    for policy in POLICIES:
        decisions, actions = simulate_policy(candidates, best, policy)
        decision_frames.append(decisions)
        action_frames.append(actions)
    decisions_all = pd.concat(decision_frames, ignore_index=True) if decision_frames else pd.DataFrame()
    actions_all = pd.concat(action_frames, ignore_index=True) if action_frames else pd.DataFrame()
    summary = summarize(decisions_all)
    daily = daily_summary(decisions_all)
    expr = expression_contribution(decisions_all)
    delta = delta_vs_first(decisions_all)

    decisions_all.to_csv(OUT_DIR / "target_book_decisions.csv", index=False)
    actions_all.to_csv(OUT_DIR / "target_book_actions.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_summary.csv", index=False)
    expr.to_csv(OUT_DIR / "expression_summary.csv", index=False)
    delta.to_csv(OUT_DIR / "delta_vs_first_lock.csv", index=False)

    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": str(EXPR_CANDIDATES.relative_to(ROOT)),
        "method": METHOD,
        "ask_floor": ASK_FLOOR,
        "edge_threshold": EDGE_THRESHOLD,
        "active_expressions": ACTIVE_EXPRESSIONS,
        "policies": POLICIES,
        "candidate_rows": int(len(candidates)),
        "best_state_rows": int(len(best)),
        "date_range": [str(candidates["target_date"].min()), str(candidates["target_date"].max())],
        "summary": summary.to_dict("records"),
        "verdict": "shadow_candidate_target_book_not_live",
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True), encoding="utf-8")
    write_report(
        candidates=candidates,
        best=best,
        summary=summary,
        daily=daily,
        expr=expr,
        delta=delta,
        actions=actions_all,
        report=report,
    )
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH.relative_to(ROOT)),
                "candidate_rows": int(len(candidates)),
                "best_state_rows": int(len(best)),
                "date_range": report["date_range"],
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
