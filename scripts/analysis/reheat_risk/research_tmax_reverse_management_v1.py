#!/usr/bin/env python3
"""Reverse-signal management replay for Tmax distribution edge.

The P6 shadow ledger selects only the first edge-passing row per city-day.  This
script asks what happens when the later best expression flips side.  The ledger
does not contain live bid paths or market ids, so true mark-to-market close PnL
is only available as a same-bracket proxy for expression transitions that likely
refer to the same bracket.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
P5_OPPS = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_reverse_management_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-reverse-management-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-reverse-management-v1.json"

METHOD = "loo_no_city_source_blend"
CONFIG_ID = "tmax_dist_clean_edge02"
EDGE_THRESHOLD = 0.02
ASK_CEILING = 0.99
ASK_FLOORS = [0.0, 0.20, 0.40]
TAKER_FEE_RATE = 0.05
BOOT_N = 2000
BOOT_SEED = 20260705


def _side(expr: str) -> str:
    return "YES" if str(expr) == "current_yes" else "NO"


def _fee(price: float, shares: float = 1.0) -> float:
    if not math.isfinite(price):
        return math.nan
    return shares * TAKER_FEE_RATE * price * (1.0 - price)


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


def _date_block_ci(rows: pd.DataFrame, cost_col: str, pnl_col: str) -> dict[str, float]:
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
    boot = []
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


def _load() -> pd.DataFrame:
    if not P5_OPPS.exists():
        raise FileNotFoundError(f"missing source: {P5_OPPS}")
    df = pd.read_csv(P5_OPPS)
    required = {
        "scope",
        "method",
        "expression",
        "city",
        "target_date",
        "decision_hour_local",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
        "actual_bucket",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"source missing columns: {missing}")
    for col in [
        "decision_hour_local",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["method"].eq(METHOD)].copy()
    df["side"] = df["expression"].map(_side)
    return df


def _state_best_rows(df: pd.DataFrame, ask_floor: float) -> pd.DataFrame:
    eligible = df[
        df["model_edge"].ge(EDGE_THRESHOLD)
        & df["ask"].ge(ask_floor)
        & df["ask"].le(ASK_CEILING)
    ].copy()
    if eligible.empty:
        return eligible
    best = (
        eligible.sort_values(
            ["scope", "city", "target_date", "decision_hour_local", "model_edge", "model_roi"],
            ascending=[True, True, True, True, False, False],
        )
        .groupby(["scope", "city", "target_date", "decision_hour_local"], as_index=False)
        .head(1)
        .copy()
    )
    return best


def _same_bracket_proxy(first_expr: str, reverse_expr: str) -> bool:
    # Strong proxy: before the next integer printed, d1 NO is the same bracket
    # that later becomes current YES.  The current/current pair may also be same
    # bracket if the running max did not move; P5 lacks bracket ids, so keep it
    # as a weaker proxy but flag the exact transition in outputs.
    pair = (str(first_expr), str(reverse_expr))
    return pair in {
        ("d1_no", "current_yes"),
        ("current_no", "current_yes"),
        ("current_yes", "current_no"),
    }


def _build_decisions(df: pd.DataFrame, ask_floor: float) -> pd.DataFrame:
    best = _state_best_rows(df, ask_floor)
    if best.empty:
        return best

    rows: list[dict[str, Any]] = []
    sort_cols = ["decision_hour_local", "model_edge", "model_roi"]
    for (scope, city, target_date), grp in best.groupby(["scope", "city", "target_date"], dropna=False):
        grp = grp.sort_values(sort_cols, ascending=[True, False, False]).copy()
        first = grp.iloc[0]
        later = grp[grp["decision_hour_local"].gt(first["decision_hour_local"])]
        opposite = later[later["side"].ne(first["side"])].head(1)
        rev = opposite.iloc[0] if not opposite.empty else None

        first_fee = _fee(float(first["ask"]))
        first_net_cost = float(first["ask"]) + first_fee
        first_net_pnl = float(first["unit_pnl"]) - first_fee

        row = {
            "ask_floor": ask_floor,
            "scope": scope,
            "city": city,
            "target_date": target_date,
            "first_hour": float(first["decision_hour_local"]),
            "first_expression": first["expression"],
            "first_side": first["side"],
            "first_ask": float(first["ask"]),
            "first_p_win": float(first["p_win"]),
            "first_model_edge": float(first["model_edge"]),
            "first_win": float(first["win"]),
            "first_cost_gross": float(first["ask"]),
            "first_pnl_gross": float(first["unit_pnl"]),
            "first_cost_taker": first_net_cost,
            "first_pnl_taker": first_net_pnl,
            "has_opposite": bool(rev is not None),
        }
        if rev is None:
            row.update(
                {
                    "reverse_hour": math.nan,
                    "reverse_expression": "",
                    "reverse_side": "",
                    "reverse_ask": math.nan,
                    "reverse_p_win": math.nan,
                    "reverse_model_edge": math.nan,
                    "reverse_win": math.nan,
                    "same_bracket_proxy": False,
                    "transition": "",
                    "replace_cost_gross": float(first["ask"]),
                    "replace_pnl_gross": float(first["unit_pnl"]),
                    "replace_cost_taker": first_net_cost,
                    "replace_pnl_taker": first_net_pnl,
                    "add_cost_gross": float(first["ask"]),
                    "add_pnl_gross": float(first["unit_pnl"]),
                    "add_cost_taker": first_net_cost,
                    "add_pnl_taker": first_net_pnl,
                    "close_only_cost_gross": float(first["ask"]),
                    "close_only_pnl_gross": float(first["unit_pnl"]),
                    "close_only_cost_taker": first_net_cost,
                    "close_only_pnl_taker": first_net_pnl,
                    "close_plus_reverse_cost_gross": float(first["ask"]),
                    "close_plus_reverse_pnl_gross": float(first["unit_pnl"]),
                    "close_plus_reverse_cost_taker": first_net_cost,
                    "close_plus_reverse_pnl_taker": first_net_pnl,
                }
            )
            rows.append(row)
            continue

        rev_fee = _fee(float(rev["ask"]))
        rev_net_cost = float(rev["ask"]) + rev_fee
        rev_net_pnl = float(rev["unit_pnl"]) - rev_fee
        same_proxy = _same_bracket_proxy(str(first["expression"]), str(rev["expression"]))
        transition = f"{first['expression']}->{rev['expression']}"

        # Replace is a clean policy test: ignore the first signal and hold the
        # first later opposite signal. It is not a real close price replay.
        row.update(
            {
                "reverse_hour": float(rev["decision_hour_local"]),
                "reverse_expression": rev["expression"],
                "reverse_side": rev["side"],
                "reverse_ask": float(rev["ask"]),
                "reverse_p_win": float(rev["p_win"]),
                "reverse_model_edge": float(rev["model_edge"]),
                "reverse_win": float(rev["win"]),
                "same_bracket_proxy": same_proxy,
                "transition": transition,
                "replace_cost_gross": float(rev["ask"]),
                "replace_pnl_gross": float(rev["unit_pnl"]),
                "replace_cost_taker": rev_net_cost,
                "replace_pnl_taker": rev_net_pnl,
                "add_cost_gross": float(first["ask"]) + float(rev["ask"]),
                "add_pnl_gross": float(first["unit_pnl"]) + float(rev["unit_pnl"]),
                "add_cost_taker": first_net_cost + rev_net_cost,
                "add_pnl_taker": first_net_pnl + rev_net_pnl,
            }
        )

        if same_proxy:
            locked_pnl_gross = 1.0 - float(first["ask"]) - float(rev["ask"])
            locked_pnl_taker = locked_pnl_gross - first_fee - rev_fee
            row.update(
                {
                    "close_only_cost_gross": float(first["ask"]) + float(rev["ask"]),
                    "close_only_pnl_gross": locked_pnl_gross,
                    "close_only_cost_taker": first_net_cost + rev_net_cost,
                    "close_only_pnl_taker": locked_pnl_taker,
                    "close_plus_reverse_cost_gross": float(first["ask"]) + 2.0 * float(rev["ask"]),
                    "close_plus_reverse_pnl_gross": locked_pnl_gross + float(rev["unit_pnl"]),
                    "close_plus_reverse_cost_taker": first_net_cost + 2.0 * rev_net_cost,
                    "close_plus_reverse_pnl_taker": locked_pnl_taker + rev_net_pnl,
                }
            )
        else:
            # If it is not likely the same bracket, calling it a close would be
            # false. Leave the baseline leg unchanged for close policies.
            row.update(
                {
                    "close_only_cost_gross": float(first["ask"]),
                    "close_only_pnl_gross": float(first["unit_pnl"]),
                    "close_only_cost_taker": first_net_cost,
                    "close_only_pnl_taker": first_net_pnl,
                    "close_plus_reverse_cost_gross": float(first["ask"]),
                    "close_plus_reverse_pnl_gross": float(first["unit_pnl"]),
                    "close_plus_reverse_cost_taker": first_net_cost,
                    "close_plus_reverse_pnl_taker": first_net_pnl,
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _summarize_policy(decisions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    policies = [
        ("first_only_hold", "first_cost", "first_pnl"),
        ("replace_with_first_opposite_hold", "replace_cost", "replace_pnl"),
        ("add_first_opposite_hold_both", "add_cost", "add_pnl"),
        ("same_bracket_close_only_proxy", "close_only_cost", "close_only_pnl"),
        ("same_bracket_close_plus_reverse_proxy", "close_plus_reverse_cost", "close_plus_reverse_pnl"),
    ]
    for (ask_floor, scope), grp in decisions.groupby(["ask_floor", "scope"], dropna=False):
        for price_mode in ["gross", "taker"]:
            for policy, cost_prefix, pnl_prefix in policies:
                cost_col = f"{cost_prefix}_{price_mode}"
                pnl_col = f"{pnl_prefix}_{price_mode}"
                ci = _date_block_ci(grp, cost_col, pnl_col)
                by_day = grp.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
                rows.append(
                    {
                        "ask_floor": ask_floor,
                        "scope": scope,
                        "price_mode": price_mode,
                        "policy": policy,
                        "rows": int(len(grp)),
                        "dates": int(grp["target_date"].nunique()),
                        "cities": int(grp["city"].nunique()),
                        "opposite_rows": int(grp["has_opposite"].sum()),
                        "same_bracket_proxy_rows": int(grp["same_bracket_proxy"].sum()),
                        "avg_first_ask": float(grp["first_ask"].mean()),
                        "avg_reverse_ask": float(grp.loc[grp["has_opposite"], "reverse_ask"].mean()) if grp["has_opposite"].any() else math.nan,
                        "cost": float(grp[cost_col].sum()),
                        "pnl": float(grp[pnl_col].sum()),
                        "roi": ci["roi"],
                        "roi_ci_low": ci["ci_low"],
                        "roi_ci_high": ci["ci_high"],
                        "daily_win_rate": float((by_day["pnl"] > 0).mean()) if len(by_day) else math.nan,
                    }
                )
    return pd.DataFrame(rows).sort_values(["ask_floor", "scope", "price_mode", "policy"]).reset_index(drop=True)


def _transition_summary(decisions: pd.DataFrame) -> pd.DataFrame:
    rev = decisions[decisions["has_opposite"]].copy()
    if rev.empty:
        return pd.DataFrame()
    return (
        rev.groupby(["ask_floor", "scope", "transition", "same_bracket_proxy"], dropna=False)
        .agg(
            rows=("city", "size"),
            dates=("target_date", "nunique"),
            first_pnl=("first_pnl_gross", "sum"),
            reverse_pnl=("replace_pnl_gross", "sum"),
            add_pnl=("add_pnl_gross", "sum"),
            avg_first_ask=("first_ask", "mean"),
            avg_reverse_ask=("reverse_ask", "mean"),
        )
        .reset_index()
        .sort_values(["ask_floor", "scope", "rows"], ascending=[True, True, False])
    )


def _table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    use = df if max_rows is None else df.head(max_rows)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in use.to_dict("records"):
        vals = []
        for col in columns:
            val = row.get(col)
            if col in {"roi", "roi_ci_low", "roi_ci_high", "daily_win_rate"}:
                vals.append(_fmt_pct(val, signed=col != "daily_win_rate"))
            elif col in {"avg_first_ask", "avg_reverse_ask", "cost", "pnl", "first_pnl", "reverse_pnl", "add_pnl"}:
                vals.append(_fmt_num(val, 3 if col.startswith("avg_") else 2))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _write_report(decisions: pd.DataFrame, summary: pd.DataFrame, transitions: pd.DataFrame, payload: dict[str, Any]) -> None:
    focus = summary[
        summary["scope"].eq("verified_forward")
        & summary["ask_floor"].isin([0.20, 0.40])
        & summary["price_mode"].eq("taker")
    ].copy()
    lines = [
        "# Tmax Reverse Management v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> source: `{P5_OPPS.relative_to(ROOT)}`",
        "",
        "## 结论",
        "",
        "- P6 主回测只选 city-day 第一笔；今天 live 的多笔 Lucknow 说明执行层没有按这个主口径做持仓血缘/去重。",
        "- 在 P6/P5 可验证账本里，真正盘口平仓缺 bid path 和 market id，不能硬算；本报告只做同分母近似：第一笔、后续反向替换、反向腿叠加，以及 likely same-bracket 的平仓 proxy。",
        "- 结论方向很清楚：后续反向信号不是免费增强。直接叠加双边通常恶化；same-bracket close 只是锁定点差损耗；是否反向开仓必须先解决持仓血缘和 same-market 判定，不能让 live runner 自己翻来翻去。",
        "",
        "## Verified Forward Taker Summary",
        "",
        *_table(
            focus,
            [
                "ask_floor",
                "policy",
                "rows",
                "dates",
                "opposite_rows",
                "same_bracket_proxy_rows",
                "avg_first_ask",
                "avg_reverse_ask",
                "cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "daily_win_rate",
            ],
        ),
        "",
        "## Transition Summary",
        "",
        *_table(
            transitions[
                transitions["scope"].eq("verified_forward")
                & transitions["ask_floor"].isin([0.20, 0.40])
            ],
            [
                "ask_floor",
                "transition",
                "same_bracket_proxy",
                "rows",
                "dates",
                "avg_first_ask",
                "avg_reverse_ask",
                "first_pnl",
                "reverse_pnl",
                "add_pnl",
            ],
            max_rows=30,
        ),
        "",
        "## Definitions",
        "",
        "- `first_only_hold`: 每个 city-day 第一条 clean_edge02 edge-pass，持有到结算。",
        "- `replace_with_first_opposite_hold`: 如果后续同城同日 best expression 反向，则用后续反向腿替换第一腿，持有到结算；这是“相信新模型判断”的反事实，不是实盘平仓价格。",
        "- `add_first_opposite_hold_both`: 第一腿不动，再加第一条反向腿，双边都持有到结算；这近似今天这种矛盾持仓的风险形态。",
        "- `same_bracket_close_only_proxy`: 仅对 likely same-bracket 反向，按 `1 - old_ask - new_ask` 锁平；其他行沿用第一腿。",
        "- `same_bracket_close_plus_reverse_proxy`: likely same-bracket 时买两份反向 token：一份锁平旧仓，一份作为新反向仓。",
        "- `taker`: 买入腿按 Polymarket weather fee `0.05 * price * (1-price)` 扣费。",
        "",
        "## Caveat",
        "",
        "P5/P6 没有 `market_id/token_id/bid path/position id`。所以本报告不能替代真实 executor replay；它回答的是“后续反向信号在历史上是否值得相信”，不是精确账户平仓收益。",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'reverse_decisions.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'reverse_policy_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'reverse_transition_summary.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = _load()
    decisions = pd.concat([_build_decisions(df, floor) for floor in ASK_FLOORS], ignore_index=True)
    if decisions.empty:
        raise RuntimeError("no decisions built")
    summary = _summarize_policy(decisions)
    transitions = _transition_summary(decisions)
    decisions.to_csv(OUT_DIR / "reverse_decisions.csv", index=False)
    summary.to_csv(OUT_DIR / "reverse_policy_summary.csv", index=False)
    transitions.to_csv(OUT_DIR / "reverse_transition_summary.csv", index=False)
    payload = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": str(P5_OPPS.relative_to(ROOT)),
        "config_id": CONFIG_ID,
        "method": METHOD,
        "edge_threshold": EDGE_THRESHOLD,
        "ask_floors": ASK_FLOORS,
        "rows": int(len(decisions)),
        "summary": summary.to_dict("records"),
        "transition_summary": transitions.to_dict("records"),
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(payload), indent=2, sort_keys=True), encoding="utf-8")
    _write_report(decisions, summary, transitions, payload)
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH.relative_to(ROOT)),
                "decisions_rows": int(len(decisions)),
                "summary_rows": int(len(summary)),
                "transition_rows": int(len(transitions)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
