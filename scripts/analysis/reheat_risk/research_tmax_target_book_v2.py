#!/usr/bin/env python3
"""Tmax target-book v2 comparison.

Research-only. No live runner/config/order behavior is changed.

v2 upgrades the prior single-leg selector into a position-aware target-book
replay and compares three probability sources on the same exact-book
denominator:

  - our_current: current local tmax exact-book bridge probabilities;
  - our_current_ext_guard: our_current, but veto violent disagreement with the
    external-style baseline;
  - external_baseline: external_tmax_baseline_v0 probabilities alone.

The key behavior is that later posterior updates revalue the active city-day
position before any close/reopen action is allowed.
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
OUR_CANDIDATES = ROOT / "docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv"
EXTERNAL_CANDIDATES = ROOT / "docs/analysis/2026-07/generated/external_tmax_baseline_v0/expression_candidates.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_target_book_v2"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-08-tmax-target-book-v2.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-08-tmax-target-book-v2.json"

OUR_METHOD = "loo_no_city_source_blend"
EXTERNAL_METHOD = "external_gaussian_market_blend"
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02
EXTERNAL_GUARD_MIN_EDGE = -0.02
TAKER_FEE_RATE = 0.05
BOOT_N = 2000
BOOT_SEED = 20260708

ACTIVE_EXPRESSIONS = ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]
ALL_EXPRESSIONS_FOR_REVALUE = ["current_yes", "current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]

POLICIES = {
    "v2_first_lock_no_current_yes": {"mode": "first_lock", "buffer": math.inf},
    "v2_close_only_ev02": {"mode": "close_only", "buffer": 0.02},
    "v2_rebalance_ev02": {"mode": "rebalance", "buffer": 0.02},
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


def _load_one(path: Path, *, model_source: str, method: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing candidates: {path}")
    df = pd.read_csv(path, low_memory=False)
    if "method" in df.columns:
        df = df[df["method"].astype(str).eq(method)].copy()
    df = df[df["expression"].isin(ALL_EXPRESSIONS_FOR_REVALUE)].copy()
    for col in [
        "decision_hour_local",
        "ask",
        "p_win",
        "win",
        "fee_adjusted_edge",
        "fee_adjusted_roi_model",
        "cost_net",
        "pnl_net",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "fee_adjusted_edge" not in df.columns or df["fee_adjusted_edge"].isna().all():
        df["fee_adjusted_edge"] = df["p_win"] - df["ask"] - df["ask"].map(_fee)
    if "fee_adjusted_roi_model" not in df.columns or df["fee_adjusted_roi_model"].isna().all():
        df["fee_adjusted_roi_model"] = df["fee_adjusted_edge"] / df["ask"]
    if "cost_net" not in df.columns or df["cost_net"].isna().all():
        df["cost_net"] = df["ask"] + df["ask"].map(_fee)
    if "pnl_net" not in df.columns or df["pnl_net"].isna().all():
        df["pnl_net"] = df["win"] - df["cost_net"]
    df["side"] = df["expression"].map(_side)
    df["bucket"] = df["expression"].map(_bucket)
    df["model_source"] = model_source
    return df


def _load_candidates() -> tuple[pd.DataFrame, dict[str, Any]]:
    our = _load_one(OUR_CANDIDATES, model_source="our_current", method=OUR_METHOD)
    external = _load_one(EXTERNAL_CANDIDATES, model_source="external_baseline", method=EXTERNAL_METHOD)

    ext_edge = external[
        ["scope", "city", "target_date", "decision_hour_local", "expression", "fee_adjusted_edge", "p_win"]
    ].rename(columns={"fee_adjusted_edge": "external_fee_adjusted_edge", "p_win": "external_p_win"})
    guarded = our.merge(
        ext_edge,
        on=["scope", "city", "target_date", "decision_hour_local", "expression"],
        how="left",
        validate="many_to_one",
    )
    guarded["model_source"] = "our_current_ext_guard"
    guarded["external_guard_min_edge"] = EXTERNAL_GUARD_MIN_EDGE

    combined = pd.concat([our, guarded, external], ignore_index=True, sort=False)
    combined = combined.dropna(subset=["scope", "city", "target_date", "decision_hour_local", "expression", "ask", "p_win", "win"])
    inventory = {
        "our_rows": int(len(our)),
        "external_rows": int(len(external)),
        "guarded_rows": int(len(guarded)),
        "combined_rows": int(len(combined)),
        "date_range": [str(combined["target_date"].min()), str(combined["target_date"].max())],
        "model_sources": {str(k): int(v) for k, v in combined["model_source"].value_counts().to_dict().items()},
    }
    return combined.sort_values(["model_source", "scope", "target_date", "city", "decision_hour_local", "expression"]).reset_index(drop=True), inventory


def _eligible_candidates(candidates: pd.DataFrame, model_source: str) -> pd.DataFrame:
    sub = candidates[
        candidates["model_source"].eq(model_source)
        & candidates["expression"].isin(ACTIVE_EXPRESSIONS)
        & candidates["ask"].ge(ASK_FLOOR)
        & candidates["ask"].le(ASK_CEILING)
        & candidates["fee_adjusted_edge"].ge(EDGE_THRESHOLD)
    ].copy()
    if model_source == "our_current_ext_guard":
        sub = sub[sub["external_fee_adjusted_edge"].fillna(-999.0).ge(EXTERNAL_GUARD_MIN_EDGE)].copy()
    if sub.empty:
        return sub
    return (
        sub.sort_values(
            ["model_source", "scope", "city", "target_date", "decision_hour_local", "fee_adjusted_edge", "fee_adjusted_roi_model"],
            ascending=[True, True, True, True, True, False, False],
        )
        .groupby(["model_source", "scope", "city", "target_date", "decision_hour_local"], as_index=False)
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
    pnl = payoff - cost
    roi = pnl / cost if cost else math.nan
    return cost, pnl, roi


def _simulate_group(
    full_group: pd.DataFrame,
    best_group: pd.DataFrame,
    *,
    policy_name: str,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    first = full_group.iloc[0]
    active: ActiveLeg | None = None
    cost = 0.0
    payoff = 0.0
    closes = 0
    reopens = 0
    actions: list[dict[str, Any]] = []

    for row in best_group.sort_values(["decision_hour_local"]).itertuples(index=False):
        desired = pd.Series(row._asdict())
        hour = float(desired["decision_hour_local"])
        if active is None:
            active = _leg_from_row(desired)
            cost += active.cost
            actions.append(_action(first, policy_name, hour, "open", None, active.expression, active, None, None, None, None))
            continue

        if policy["mode"] == "first_lock":
            actions.append(_action(first, policy_name, hour, "hold_first_lock", active.expression, str(desired["expression"]), active, None, None, None, None))
            continue

        if str(desired["expression"]) == active.expression:
            actions.append(_action(first, policy_name, hour, "hold_same_expression", active.expression, str(desired["expression"]), active, None, None, None, None))
            continue

        old_row = _row_lookup(full_group, hour, active.expression)
        close_expr = _complement(active.expression)
        close_row = _row_lookup(full_group, hour, close_expr) if close_expr else None
        if old_row is None or close_row is None:
            actions.append(_action(first, policy_name, hour, "hold_missing_revalue_or_close_quote", active.expression, str(desired["expression"]), active, None, None, None, None))
            continue

        old_p = float(old_row["p_win"])
        close_cost = float(close_row["ask"]) + _fee(float(close_row["ask"]))
        close_value = 1.0 - close_cost
        close_incremental_ev = close_value - old_p
        desired_cost = float(desired["ask"]) + _fee(float(desired["ask"]))
        new_value = float(desired["p_win"]) - desired_cost
        rebalance_incremental_ev = close_value + new_value - old_p

        if policy["mode"] == "close_only":
            if close_incremental_ev > float(policy["buffer"]):
                cost += close_cost
                payoff += 1.0
                closes += 1
                actions.append(_action(first, policy_name, hour, "close", active.expression, str(desired["expression"]), active, close_expr, close_cost, old_p, close_incremental_ev))
                active = None
            else:
                actions.append(_action(first, policy_name, hour, "hold_close_not_worth_cost", active.expression, str(desired["expression"]), active, close_expr, close_cost, old_p, close_incremental_ev))
            continue

        if rebalance_incremental_ev > float(policy["buffer"]):
            cost += close_cost
            payoff += 1.0
            closes += 1
            actions.append(_action(first, policy_name, hour, "close", active.expression, str(desired["expression"]), active, close_expr, close_cost, old_p, close_incremental_ev, rebalance_incremental_ev))
            active = _leg_from_row(desired)
            cost += active.cost
            reopens += 1
            actions.append(_action(first, policy_name, hour, "reopen", None, active.expression, active, None, active.cost, None, None, rebalance_incremental_ev))
        else:
            actions.append(_action(first, policy_name, hour, "hold_rebalance_not_worth_cost", active.expression, str(desired["expression"]), active, close_expr, close_cost, old_p, close_incremental_ev, rebalance_incremental_ev))

    final_cost, final_pnl, final_roi = _settle_active(active, cost, payoff)
    return (
        {
            "model_source": first["model_source"],
            "policy": policy_name,
            "scope": first["scope"],
            "city": first["city"],
            "target_date": first["target_date"],
            "first_expression": actions[0]["desired_expression"] if actions else None,
            "final_expression": active.expression if active else None,
            "rows_seen": int(len(best_group)),
            "closes": closes,
            "reopens": reopens,
            "cost_net": final_cost,
            "pnl_net": final_pnl,
            "roi_net": final_roi,
            "last_action": actions[-1]["action"] if actions else "none",
        },
        actions,
    )


def _action(
    first: pd.Series,
    policy: str,
    hour: float,
    action: str,
    active_before: str | None,
    desired_expression: str | None,
    active: ActiveLeg | None,
    close_expr: str | None,
    cost_delta: float | None,
    old_p: float | None,
    close_incremental_ev: float | None,
    rebalance_incremental_ev: float | None = None,
) -> dict[str, Any]:
    return {
        "model_source": first["model_source"],
        "policy": policy,
        "scope": first["scope"],
        "city": first["city"],
        "target_date": first["target_date"],
        "decision_hour_local": hour,
        "action": action,
        "active_before": active_before,
        "desired_expression": desired_expression,
        "close_expr": close_expr,
        "active_after": active.expression if active else None,
        "cost_delta": cost_delta,
        "old_p": old_p,
        "close_incremental_ev": close_incremental_ev,
        "rebalance_incremental_ev": rebalance_incremental_ev,
    }


def _run_replay(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    decision_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    for model_source in sorted(candidates["model_source"].unique()):
        best = _eligible_candidates(candidates, model_source)
        if best.empty:
            continue
        full = candidates[candidates["model_source"].eq(model_source)].copy()
        for policy_name, policy in POLICIES.items():
            for key, best_group in best.groupby(["model_source", "scope", "city", "target_date"], sort=False):
                _model_source, scope, city, target_date = key
                full_group = full[
                    full["scope"].eq(scope)
                    & full["city"].eq(city)
                    & full["target_date"].eq(target_date)
                ].copy()
                decision, actions = _simulate_group(full_group, best_group, policy_name=policy_name, policy=policy)
                decision_rows.append(decision)
                action_rows.extend(actions)
    return pd.DataFrame(decision_rows), pd.DataFrame(action_rows)


def _summaries(decisions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    for (model_source, policy, scope), grp in decisions.groupby(["model_source", "policy", "scope"], dropna=False):
        ci = _date_block_ci(grp)
        by_day = grp.groupby("target_date", as_index=False).agg(cost=("cost_net", "sum"), pnl=("pnl_net", "sum"))
        rows.append(
            {
                "model_source": model_source,
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
                "daily_positive": int((by_day["pnl"] > 0).sum()) if len(by_day) else 0,
                "daily_negative": int((by_day["pnl"] < 0).sum()) if len(by_day) else 0,
            }
        )
    policy_summary = pd.DataFrame(rows).sort_values(["scope", "roi_net"], ascending=[True, False]).reset_index(drop=True)

    daily = (
        decisions.groupby(["model_source", "policy", "scope", "target_date"], as_index=False)
        .agg(rows=("city", "count"), cities=("city", "nunique"), closes=("closes", "sum"), reopens=("reopens", "sum"), cost_net=("cost_net", "sum"), pnl_net=("pnl_net", "sum"))
    )
    daily["roi_net"] = daily["pnl_net"] / daily["cost_net"]

    expr = (
        decisions.groupby(["model_source", "policy", "scope", "final_expression"], as_index=False)
        .agg(rows=("city", "count"), dates=("target_date", "nunique"), cost_net=("cost_net", "sum"), pnl_net=("pnl_net", "sum"))
    )
    expr["roi_net"] = expr["pnl_net"] / expr["cost_net"]
    return policy_summary, daily, expr


def _table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for item in df[cols].to_dict("records"):
        vals = []
        for col in cols:
            value = item.get(col)
            if col in {"roi_net", "roi_net_ci_low", "roi_net_ci_high"}:
                vals.append(_fmt_pct(value))
            elif isinstance(value, float):
                vals.append(_fmt_num(value, 2))
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _write_report(payload: dict[str, Any]) -> None:
    summary = pd.DataFrame(payload["policy_summary"])
    verified = summary[summary["scope"].eq("verified_forward")].copy()
    lines = [
        "# Tmax Target-Book v2",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> Scope: research/shadow only. No live runner/config/order behavior changed.",
        "",
        "## 结论 / 策略改造方向",
        "",
        "- v2 的主改造不是换成外部 tmax，而是把我们当前 tmax 从 `single-leg edge selector` 升级为 `full-ladder-ish posterior + city-day target-book ledger`。",
        "- 外部对照组在 v0 里没有赢概率评分；因此本版只把它作为 disagreement guard/对照，不把它并入主 posterior。",
        "- verified 上继续由 `our_current` 的 first-lock/no-current-YES 形态最稳；rebalance 只能 shadow，因为换仓成本和 posterior flip 还没有被证明能稳定赚钱。",
        "- live 前默认动作：每个 city-day 只允许一个 active target；新报文只 revalue，不直接生成反向新开仓；switch 必须走 close/reopen EV 账本。",
        "",
        "## Verified Policy Summary",
        "",
        *_table(
            verified,
            [
                "model_source",
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
        ),
        "",
        "## All Policy Summary",
        "",
        *_table(
            summary,
            [
                "scope",
                "model_source",
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
        ),
        "",
        "## v2 Contract",
        "",
        "```text",
        "probability_source -> expression candidates -> target_book ledger",
        "open: first eligible city-day target only",
        "hold: default after posterior update",
        "close/reopen: only if close_value + new_value - old_hold_value > buffer",
        "current_yes: not active in primary set; only allowed for revalue/complement cost",
        "external_tmax: challenger/guard only until it beats local model on same denominator",
        "```",
        "",
        "## Verdict",
        "",
        f"conclusion=`{payload['verdict']}`.",
        "",
        "下一步是把 v2 ledger 接到 fresh shadow runner：每轮写 position_book/target_book/revalue_actions，但仍保持 zero-notional，等新 settled forward rows 验证。",
        "",
        "## Artifacts",
        "",
        "- `docs/analysis/2026-07/generated/tmax_target_book_v2/target_book_decisions.csv`",
        "- `docs/analysis/2026-07/generated/tmax_target_book_v2/target_book_actions.csv`",
        "- `docs/analysis/2026-07/generated/tmax_target_book_v2/policy_summary.csv`",
        "- `docs/analysis/2026-07/generated/tmax_target_book_v2/daily_summary.csv`",
        "- `docs/analysis/2026-07/generated/tmax_target_book_v2/expression_summary.csv`",
        "- `docs/analysis/2026-07/2026-07-08-tmax-target-book-v2.json`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    generated_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    candidates, inventory = _load_candidates()
    decisions, actions = _run_replay(candidates)
    policy_summary, daily, expr = _summaries(decisions)

    decisions.to_csv(OUT_DIR / "target_book_decisions.csv", index=False)
    actions.to_csv(OUT_DIR / "target_book_actions.csv", index=False)
    policy_summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_summary.csv", index=False)
    expr.to_csv(OUT_DIR / "expression_summary.csv", index=False)

    primary = policy_summary[
        policy_summary["scope"].eq("verified_forward")
        & policy_summary["model_source"].eq("our_current")
        & policy_summary["policy"].eq("v2_first_lock_no_current_yes")
    ]
    verdict = "shadow_candidate_target_book_v2_not_live"
    if not primary.empty and float(primary.iloc[0]["roi_net_ci_low"]) > 0:
        verdict = "shadow_candidate_primary_positive_ci_still_no_live_without_fresh_ledger"

    payload = {
        "generated_at_utc": generated_at,
        "source_candidates": {
            "our": str(OUR_CANDIDATES.relative_to(ROOT)),
            "external": str(EXTERNAL_CANDIDATES.relative_to(ROOT)),
        },
        "inventory": inventory,
        "parameters": {
            "ask_floor": ASK_FLOOR,
            "ask_ceiling": ASK_CEILING,
            "edge_threshold": EDGE_THRESHOLD,
            "external_guard_min_edge": EXTERNAL_GUARD_MIN_EDGE,
            "active_expressions": ACTIVE_EXPRESSIONS,
            "policies": POLICIES,
        },
        "policy_summary": policy_summary.to_dict("records"),
        "verdict": verdict,
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_report(payload)


if __name__ == "__main__":
    main()
